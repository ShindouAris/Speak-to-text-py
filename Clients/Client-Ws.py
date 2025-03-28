#!/usr/bin/env python3

import asyncio
import websockets
import sys
import json
import numpy as np
import threading # Thêm threading cho key listener

# --- VAD Imports ---
try:
    import webrtcvad
except ImportError:
    print("Please install WebRTC VAD: pip install webrtcvad-wheels")
    sys.exit(1)

# --- Keybinding Imports ---
try:
    from pynput import keyboard
except ImportError:
    print("Please install pynput: pip install pynput")
    sys.exit(1)

# --- Sounddevice Imports (giữ nguyên) ---
try:
    import sounddevice as sd
except OSError as e:
    print(f"Error importing sounddevice: {e}")
    print("Please ensure you have a working audio backend (like PortAudio) installed.")
    sys.exit(1)
except ImportError:
    print("Please install sounddevice: pip install sounddevice")
    sys.exit(1)


# --- Configuration ---
LANG_CODE = "vi"
SERVER_URI = f"ws://localhost:8000/ws/stt/{LANG_CODE}"
SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = np.int16 # NumPy dtype tương ứng int16
BYTES_PER_SAMPLE = np.dtype(DTYPE).itemsize # = 2 bytes cho int16

# --- VAD Configuration ---
VAD_AGGRESSIVENESS = 3 # 0 đến 3 (3 là tích cực nhất trong việc lọc không phải giọng nói)
VAD_FRAME_MS = 30 # Thời lượng frame VAD hỗ trợ (10, 20, hoặc 30 ms)
VAD_FRAME_SAMPLES = int(SAMPLE_RATE * (VAD_FRAME_MS / 1000)) # Số sample trong 1 frame VAD
VAD_FRAME_BYTES = VAD_FRAME_SAMPLES * BYTES_PER_SAMPLE * CHANNELS
# Ngưỡng im lặng để dừng gửi (tính bằng số frame VAD)
SILENCE_FRAMES_THRESHOLD = int(500 / VAD_FRAME_MS) # 500 ms im lặng

# --- Audio Buffer & State ---
# BLOCK_SIZE nên là bội số của VAD_FRAME_SAMPLES để xử lý dễ dàng
BLOCK_SIZE_MS = 120 # Ví dụ: đọc 120ms mỗi lần từ sounddevice
BLOCK_SIZE_SAMPLES = int(SAMPLE_RATE * (BLOCK_SIZE_MS / 1000))

audio_queue = asyncio.Queue() # Queue để gửi audio bytes đến send_task
vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
speaking = False
silence_frames_count = 0
temp_audio_buffer = bytearray() # Buffer để chứa dữ liệu chưa đủ 1 VAD frame

# --- Keybinding State ---
send_audio_enabled = True # Bắt đầu ở trạng thái bật
key_listener_thread = None
key_listener = None
TOGGLE_KEY = keyboard.Key.space # Sử dụng phím Space

# --- Functions ---

def process_vad(frame_bytes):
    """Xử lý một frame audio với VAD và quyết định có gửi không."""
    global speaking, silence_frames_count
    is_speech = False
    try:
        # VAD cần đúng số byte và sample rate
        if len(frame_bytes) == VAD_FRAME_BYTES:
            is_speech = vad.is_speech(frame_bytes, SAMPLE_RATE)
        else:
            print(f"Warning: Incorrect frame size for VAD: {len(frame_bytes)} != {VAD_FRAME_BYTES}", file=sys.stderr)
            return # Bỏ qua frame không đúng kích thước

        if is_speech:
            silence_frames_count = 0
            if not speaking:
                print("[VAD] Speech started")
                speaking = True
            # Queue audio nếu VAD nói là speech VÀ keybind đang bật
            if send_audio_enabled:
                try:
                    audio_queue.put_nowait(frame_bytes)
                except asyncio.QueueFull: pass # Bỏ qua nếu queue đầy
        else: # Not speech
            if speaking:
                silence_frames_count += 1
                if silence_frames_count > SILENCE_FRAMES_THRESHOLD:
                    print("[VAD] Speech ended (silence threshold reached)")
                    speaking = False
                else:
                    # Vẫn đang trong ngưỡng im lặng sau khi nói -> gửi để padding
                    if send_audio_enabled:
                        try:
                            audio_queue.put_nowait(frame_bytes)
                        except asyncio.QueueFull: pass # Bỏ qua nếu queue đầy

    except Exception as e:
        print(f"Error during VAD processing: {e}", file=sys.stderr)


def audio_callback(indata, frames, time, status):
    """Callback nhận audio từ sounddevice, chia frame cho VAD."""
    global temp_audio_buffer

    if not recording_active:
        return
    if status:
        print(f"Audio Callback Status: {status}", file=sys.stderr)

    try:
        # Chuyển float32 sang int16 bytes
        int_data = (indata * 32767).astype(DTYPE)
        byte_data = int_data.tobytes()

        # Nối dữ liệu mới vào buffer tạm
        temp_audio_buffer.extend(byte_data)

        # Xử lý các frame VAD hoàn chỉnh trong buffer
        while len(temp_audio_buffer) >= VAD_FRAME_BYTES:
            vad_frame = temp_audio_buffer[:VAD_FRAME_BYTES]
            process_vad(bytes(vad_frame)) # Gọi xử lý VAD
            # Xóa frame đã xử lý khỏi buffer
            del temp_audio_buffer[:VAD_FRAME_BYTES]

    except Exception as e:
        print(f"Error in audio_callback: {e}", file=sys.stderr)


async def receive_task(websocket):
    # (Giữ nguyên như trước)
    print("Receive task started.")
    try:
        async for message in websocket:
            try:
                result = json.loads(message)
                if "partial" in result:
                    # Thêm \r để ghi đè dòng partial trước đó
                    print(f"Partial: {result['partial']}{' ' * 10}\r", end='', flush=True)
                elif "text" in result:
                    # Xóa dòng partial cũ và in dòng final
                    print(f"{' ' * 80}\r", end='', flush=True) # Xóa dòng
                    print(f"Final  : {result['text']}")
                else:
                    print(f"\nServer: {result}") # Xuống dòng cho các message khác
            except json.JSONDecodeError:
                print(f"\nServer (raw): {message}") # Xuống dòng
    except websockets.exceptions.ConnectionClosedOK:
        print("\nReceive task: Connection closed normally.")
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"\nReceive task: Connection closed with error: {e.code} {e.reason}")
    except Exception as e:
        print(f"\nReceive task error: {e}")
    finally:
        print("Receive task finished.")

async def send_task(websocket):
    # (Giữ nguyên như trước)
    global recording_active
    print("Send task started. Press SPACE to toggle sending audio.")
    print(f"Current state: {'SENDING' if send_audio_enabled else 'PAUSED'}")
    try:
        while recording_active or not audio_queue.empty(): # Xử lý hết queue ngay cả khi dừng
            byte_data = await audio_queue.get()
            if byte_data is None: # Tín hiệu dừng hẳn
                break
            if recording_active: # Chỉ gửi nếu vẫn đang trong trạng thái chạy chính
                await websocket.send(byte_data)
            audio_queue.task_done()
    except websockets.exceptions.ConnectionClosed:
        print("Send task: Connection closed while sending.")
        recording_active = False
    except Exception as e:
        print(f"Send task error: {e}")
        recording_active = False
    finally:
        print("Send task finished.")

# --- Keybinding Logic ---
def on_press(key):
    """Callback khi nhấn phím."""
    global send_audio_enabled
    if key == TOGGLE_KEY:
        send_audio_enabled = not send_audio_enabled
        state = "SENDING" if send_audio_enabled else "PAUSED"
        # In trạng thái mới, dùng \r để ghi đè
        print(f"{' ' * 80}\r", end='', flush=True) # Xóa dòng hiện tại
        print(f"Keybind: Audio sending {state}. Press SPACE to toggle.", flush=True)

def start_key_listener():
    """Khởi động key listener trong một thread riêng."""
    global key_listener
    # Non-blocking listener
    key_listener = keyboard.Listener(on_press=on_press)
    key_listener.start()
    print("Key listener started.")

def stop_key_listener():
    """Dừng key listener."""
    global key_listener
    if key_listener:
        print("Stopping key listener...")
        key_listener.stop()
        key_listener.join() # Đợi thread kết thúc
        key_listener = None
        print("Key listener stopped.")

# --- Main Function (Cập nhật) ---
async def run_stt_client():
    global recording_active, key_listener_thread

    # Kiểm tra audio input (giữ nguyên)
    try:
        sd.check_input_settings(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32')
        print(f"Default input device supports {SAMPLE_RATE} Hz, {CHANNELS} channel(s), float32.")
    except Exception as e:
        print(f"Error checking input device settings: {e}")
        return

    # Khởi động key listener trong thread riêng
    key_listener_thread = threading.Thread(target=start_key_listener, daemon=True)
    key_listener_thread.start()

    stream = None # Khởi tạo stream là None
    try:
        stream = sd.InputStream(samplerate=SAMPLE_RATE,
                                blocksize=BLOCK_SIZE_SAMPLES, # Sử dụng block size mới
                                dtype='float32',
                                channels=CHANNELS,
                                callback=audio_callback)

        print(f"Connecting to {SERVER_URI}...")
        async with websockets.connect(SERVER_URI) as websocket:
            print("Connected! Recording active. Press SPACE to pause/resume, Ctrl+C to stop.")
            stream.start() # Bắt đầu luồng audio SAU KHI kết nối WebSocket thành công

            receiver = asyncio.create_task(receive_task(websocket))
            sender = asyncio.create_task(send_task(websocket))

            done, pending = await asyncio.wait(
                [receiver, sender],
                return_when=asyncio.FIRST_COMPLETED,
            )

            print("One task finished, initiating shutdown...")
            recording_active = False

            # Đảm bảo stream audio dừng
            if stream and stream.active:
                print("Stopping audio stream...")
                stream.stop()
                stream.close() # Đóng stream
                print("Audio stream stopped.")
            stream = None # Reset stream

            await audio_queue.put(None) # Báo hiệu send_task dừng
            await sender # Chờ send_task xử lý xong queue

            if not receiver.done():
                receiver.cancel()
                try:
                    await receiver
                except asyncio.CancelledError:
                    print("Receiver task cancelled.")

    except sd.PortAudioError as e:
        print(f"PortAudioError: {e}")
    except websockets.exceptions.InvalidURI:
        print(f"Invalid WebSocket URI: {SERVER_URI}")
    except websockets.exceptions.WebSocketException as e:
        print(f"WebSocket connection failed: {e}")
    except KeyboardInterrupt:
        print("\nCtrl+C pressed, initiating shutdown...")
        recording_active = False
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        recording_active = False
    finally:
        # Dừng và đóng stream nếu nó vẫn còn tồn tại (ví dụ lỗi trước khi vào `async with`)
        if stream and stream.active:
            print("Ensuring audio stream is stopped in finally block...")
            stream.stop()
            stream.close()
            print("Audio stream stopped.")

        # Dừng key listener
        stop_key_listener()
        if key_listener_thread and key_listener_thread.is_alive():
            key_listener_thread.join(timeout=1) # Đợi thread key listener kết thúc

        # Gửi tín hiệu dừng cuối cùng (phòng trường hợp chưa gửi)
        if not audio_queue.empty():
            print("Queue not empty, sending final stop signal.")
        try:
            audio_queue.put_nowait(None)
        except asyncio.QueueFull: pass # Queue có thể đầy nếu lỗi xảy ra nhanh

        print("Client finished.")


if __name__ == "__main__":
    recording_active = True # Reset trạng thái khi chạy lại
    send_audio_enabled = True
    try:
        asyncio.run(run_stt_client())
    except KeyboardInterrupt:
        print("\nExiting program.")