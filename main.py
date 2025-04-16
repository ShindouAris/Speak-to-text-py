import asyncio
import json
import logging
import os
import sys
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Path, UploadFile, File, HTTPException
from websockets.exceptions import ConnectionClosedOK, ConnectionClosedError
import core.logger
from vosk import KaldiRecognizer

import io
from pydub import AudioSegment



# ---- RICH ----
from rich.console import Console
from rich.panel import Panel
from rich.logging import RichHandler
from rich.text import Text
# ---- RICH ----

# --- Add src to Python Path ---
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = current_dir
src_path = os.path.join(project_root, "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# --- Import Loader ---
try:
    from core.loader import load_vosk_models
except ImportError as e:
    print(f"[ERROR] Could not import 'load_vosk_models' from 'src/loader.py'. "
          f"Ensure the file exists and the structure is correct. Error: {e}")
    sys.exit(1)


# --- Configuration ---
SAMPLE_RATE = 16000.0
SERVER_HOST = os.getenv("HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("PORT", 8000))

# --- RICH Console & Logging ---
console = Console(stderr=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(
        console=console,
        rich_tracebacks=True,
        tracebacks_show_locals=False,
        show_path=False,
        markup=True
    )]
)

logging.getLogger("vosk").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# --- Load Models using Loader ---
logger.info("Initializing model loader...")
loaded_models = load_vosk_models()

if not loaded_models:
    logger.error("FATAL: No Vosk models were loaded successfully by the loader. Exiting.")
    sys.exit(1)

# --- FastAPI App ---
app = FastAPI(
    title="Vosk Streaming STT API (Loaded via Module)",
    description="Real-time Speech-to-Text API using Vosk and FastAPI WebSockets. Models loaded from external module.",
    version="1.2.0",
    docs_url=None,
    redoc_url=None
)

def convert_audio_for_vosk(audio_bytes: bytes, target_sr: int = 16000, target_channels: int = 1) -> bytes | None:
    """
    Chuyển đổi audio (từ bytes) sang định dạng PCM 16-bit, mono, 16kHz mà Vosk yêu cầu.
    Trả về bytes của audio đã chuyển đổi, hoặc None nếu lỗi.
    """
    try:
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes))

        if audio.frame_rate != target_sr:
            audio = audio.set_frame_rate(target_sr)
            # logger.debug(f"Converted sample rate to {target_sr}Hz")

        if audio.channels != target_channels:
            audio = audio.set_channels(target_channels)
            # logger.debug(f"Converted channels to {target_channels} (mono)")

        pcm_data = audio.export(format="s16le").read()
        # logger.debug(f"Exported to raw PCM S16LE, size: {len(pcm_data)} bytes")
        return pcm_data

    except Exception as e:
        logger.error(f"Audio conversion failed using pydub: {e}", exc_info=True)
        return None


# --- API Info Function ---
def create_api_info_panel() -> Panel:
    """Tạo Panel hiển thị thông tin API."""
    info_text = Text()
    info_text.append("🚀 Vosk Streaming STT Server is Running!\n\n", style="bold bright_green")
    info_text.append("WebSocket Endpoint:\n", style="bold white")

    display_host = SERVER_HOST
    if display_host == "0.0.0.0":
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            display_host = s.getsockname()[0]
            s.close()
        except Exception:
            display_host = "localhost" # Fallback
            info_text.append(f"(Accessible via localhost and potentially other IPs)\n", style="dim white")

    base_url = f"http://{display_host}:{SERVER_PORT}"
    ws_base_url = f"ws://{display_host}:{SERVER_PORT}/ws/stt/"
    info_text.append("HTTP Endpoint (POST):\n", style="bold white")
    info_text.append(f"  {base_url}/stt/", style="cyan") # <--- HTTP Route
    info_text.append("{lang_code}", style="cyan dim")
    info_text.append(" (Body: multipart/form-data with 'file' field)\n", style="dim white")
    info_text.append(f"  {ws_base_url}", style="cyan")
    info_text.append("{lang_code}\n", style="cyan dim")
    info_text.append("\nSupported Languages (codes):\n", style="bold white")
    lang_list = ", ".join(sorted([f"{lang}" for lang in loaded_models.keys()]))
    info_text.append(f"  {lang_list}\n")

    return Panel(
        info_text,
        title="API Information",
        border_style="blue",
        padding=(1, 2)
    )

# --- WebSocket Endpoint ---
@app.websocket("/ws/stt/{lang_code}")
async def websocket_endpoint(
        websocket: WebSocket,
        lang_code: str = Path(..., title="Language code (e.g., 'en', 'vi')", min_length=1, max_length=10)
):
    client_host = websocket.client.host
    client_port = websocket.client.port
    RECEIVE_TIMEOUT = 0.1

    if lang_code not in loaded_models:
        logger.warning(f"Unsupported language '{lang_code}' requested by {client_host}:{client_port}. Closing connection.")
        await websocket.close(code=1008, reason=f"Unsupported language: {lang_code}")
        return

    model = loaded_models[lang_code]
    logger.info(f"✅ Connection accepted: lang='{lang_code}' from {client_host}:{client_port}")
    await websocket.accept()

    logger.info(f"Initializing KaldiRecognizer for lang='{lang_code}'...")
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    recognizer.SetWords(True)
    logger.info(f"KaldiRecognizer initialized successfully.")

    try:
        logger.info("Entering main processing loop...")
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_bytes(),
                    timeout=RECEIVE_TIMEOUT
                )
                logger.debug(f"Received {len(data)} bytes.")
                processed = recognizer.AcceptWaveform(data)

                partial_result_json = recognizer.PartialResult()
                partial_result_dict = json.loads(partial_result_json)
                partial_text = partial_result_dict.get('partial', '')

                if partial_text:
                    await websocket.send_text(json.dumps({"partial": partial_text}))

                if processed:
                    result_json = recognizer.Result()
                    result_dict = json.loads(result_json)
                    final_text = result_dict.get('text', '')
                    if final_text:
                        await websocket.send_text(json.dumps({"text": final_text}))
                else:
                    partial_result_json = recognizer.PartialResult()
                    partial_result_dict = json.loads(partial_result_json)
                    partial_text = partial_result_dict.get('partial', '')
                    if partial_text:
                        await websocket.send_text(json.dumps({"partial": partial_text}))
            except asyncio.TimeoutError:
                final_result_json = recognizer.FinalResult()
                final_result_dict = json.loads(final_result_json)
                final_text = final_result_dict.get('text', '')
                if final_text:
                    await websocket.send_text(json.dumps({"text": final_text}))


    except WebSocketDisconnect as e:
        logger.warning(f"🛑 WebSocket disconnected: lang='{lang_code}' from {client_host}:{client_port}. Code: {e.code}, Reason: {e.reason or 'N/A'}")
        final_result_json = recognizer.FinalResult()
        final_result_dict = json.loads(final_result_json)
        final_text = final_result_dict.get('text', '')
        if final_text:
            logger.info(f"🔊 ({lang_code}) Final (on disconnect) from {client_host}:{client_port}: \"{final_text}\"")

    except ConnectionClosedOK:
        logger.info(f"🛑 WebSocket closed normally: lang='{lang_code}' from {client_host}:{client_port}.")
    except ConnectionClosedError as e:
        logger.warning(f"⚠️ WebSocket closed with error: lang='{lang_code}' from {client_host}:{client_port}. Code: {e.code}, Reason: {e.reason or 'N/A'}")
    except Exception as e:
        logger.error(f"💥 Unhandled exception during WebSocket comm for lang='{lang_code}' ({client_host}:{client_port}):", exc_info=True)
        try:
            await websocket.close(code=1011, reason=f"Internal server error")
        except Exception:
            pass
    finally:
        logger.debug(f"Cleaned up resources for connection {client_host}:{client_port}")

# --- HTTP POST Route for STT ---
@app.post(
    "/stt/{lang_code}",
    summary="Perform Speech-to-Text on an uploaded audio file",
    response_description="The recognized text",
)
async def http_stt_endpoint(
        lang_code: str = Path(..., title="Language code", min_length=1, max_length=10),
        file: UploadFile = File(..., description="Audio file to be transcribed (e.g., WAV, MP3, OGG)")
):
    """
    Nhận dạng giọng nói từ một file âm thanh được tải lên.

    - **lang_code**: Mã ngôn ngữ (vd: 'vn', 'en').
    - **file**: File âm thanh cần nhận dạng. Hệ thống sẽ cố gắng chuyển đổi định dạng nếu cần (yêu cầu `pydub` và `ffmpeg`).
    """

    logger.info(f"Received HTTP STT request for lang='{lang_code}' from file '{file.filename}' ({file.content_type})")

    # --- 1. Kiểm tra ngôn ngữ ---
    if lang_code not in loaded_models:
        logger.warning(f"Unsupported language '{lang_code}' requested for file '{file.filename}'.")
        raise HTTPException(status_code=400, detail=f"Unsupported language: {lang_code}")

    model = loaded_models[lang_code]

    # --- 2. Đọc và Chuyển đổi Audio ---
    try:
        audio_bytes = await file.read()
        if not audio_bytes:
            logger.warning(f"Received empty file: '{file.filename}'")
            raise HTTPException(status_code=400, detail="Empty audio file received.")

        logger.info(f"Read {len(audio_bytes)} bytes from '{file.filename}'. Attempting conversion if needed...")
        pcm_audio_bytes = convert_audio_for_vosk(audio_bytes, target_sr=int(SAMPLE_RATE))

        if pcm_audio_bytes is None:
            logger.error(f"Failed to convert audio file '{file.filename}' to required PCM format.")
            raise HTTPException(status_code=400, detail="Could not process audio file. Ensure it's a valid audio format.")

        logger.info(f"Audio converted to {len(pcm_audio_bytes)} bytes of raw PCM data.")

    except Exception as e:
        logger.error(f"Error reading or processing uploaded file '{file.filename}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing audio file: {e}")
    finally:

        await file.close()

    # --- 3. Thực hiện Nhận dạng ---
    recognizer = None
    try:
        recognizer = KaldiRecognizer(model, SAMPLE_RATE)
        recognizer.SetWords(True)

        recognizer.AcceptWaveform(pcm_audio_bytes)

        result_json = recognizer.FinalResult()
        result_dict = json.loads(result_json)
        final_text = result_dict.get('text', '')

        logger.info(f"🔊 ({lang_code}) HTTP Final from file '{file.filename}': \"{final_text}\"")

        # --- 4. Trả về Kết quả ---
        return {"text": final_text}

    except Exception as e:
        logger.error(f"💥 Unhandled exception during HTTP STT recognition for lang='[bold cyan]{lang_code}[/]' (file: {file.filename}):", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error during recognition: {type(e).__name__}")
    finally:
        # Recognizer sẽ tự giải phóng khi ra khỏi scope
        logger.debug(f"Cleaned up resources for HTTP STT request (file: {file.filename})")