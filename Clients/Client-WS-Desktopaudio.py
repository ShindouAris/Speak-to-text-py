#!/usr/bin/env python3

import asyncio
import websockets
import sys
import json
import numpy as np
import threading
import time

try:
    import soundcard as sc
    default_speaker = sc.default_speaker()
    if not default_speaker:
        raise RuntimeError("No default speaker found by soundcard.")
    print(f"[Info] Default speaker found: {default_speaker.name}")
    loopback_mic = sc.get_microphone(default_speaker.name, include_loopback=True)
    if not loopback_mic:
        raise RuntimeError(f"Could not find loopback device for speaker '{default_speaker.name}'. "
                            "Ensure loopback is enabled in your OS (e.g., Stereo Mix).")
    print(f"[Info] Using loopback microphone: {loopback_mic.name}")

except ImportError as e:
    print(f"Error importing soundcard: {e}")
    print("Please install soundcard: pip install soundcard")
    sys.exit(1)
except RuntimeError as e:
    print(f"Soundcard Runtime Error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"An unexpected error occurred initializing soundcard: {e}")
    sys.exit(1)

try:
    import webrtcvad
except ImportError:
    print("Please install WebRTC VAD: pip install webrtcvad-wheels")
    sys.exit(1)

try:
    from pynput import keyboard
except ImportError:
    print("Please install pynput: pip install pynput")
    sys.exit(1)


# --- Configuration ---
LANG_CODE = "en"
SERVER_URI = f"ws://localhost:8000/ws/stt/{LANG_CODE}"

# --- Code Configuration, don't touch that ---
SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = np.int16
BYTES_PER_SAMPLE = np.dtype(DTYPE).itemsize
VAD_AGGRESSIVENESS = 3
VAD_FRAME_MS = 30
VAD_FRAME_SAMPLES = int(SAMPLE_RATE * (VAD_FRAME_MS / 1000))
VAD_FRAME_BYTES = VAD_FRAME_SAMPLES * BYTES_PER_SAMPLE * CHANNELS
SILENCE_FRAMES_THRESHOLD = int(500 / VAD_FRAME_MS)
BLOCK_SIZE_SAMPLES = VAD_FRAME_SAMPLES * 4 # 120ms
audio_queue = asyncio.Queue()
vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
speaking = False
silence_frames_count = 0
temp_audio_buffer = bytearray()
send_audio_enabled = True
key_listener_thread = None
key_listener = None
TOGGLE_KEY = keyboard.Key.space
recording_active = True
main_loop = None

# --- Functions ---
def process_vad(frame_bytes, loop):
    global speaking, silence_frames_count

    try:
        if len(frame_bytes) == VAD_FRAME_BYTES:
            is_speech = vad.is_speech(frame_bytes, SAMPLE_RATE)
        else:
            return

        should_send = False
        if is_speech:
            silence_frames_count = 0
            if not speaking: speaking = True
            should_send = True
        elif speaking:
            silence_frames_count += 1
            if silence_frames_count > SILENCE_FRAMES_THRESHOLD:
                speaking = False
                should_send = False
            else:
                should_send = True

        if should_send and send_audio_enabled:
            if loop and loop.is_running():
                try:
                    # Chỉ put nếu queue chưa quá đầy để tránh block thread audio quá lâu
                    if audio_queue.qsize() < 50: # Giới hạn queue size (tùy chỉnh)
                        asyncio.run_coroutine_threadsafe(audio_queue.put(frame_bytes), loop)
                    # else: print("Queue full, dropping frame.")
                except Exception as e_put:
                    print(f"Error putting to queue: {e_put}", file=sys.stderr)

    except Exception as e:
        print(f"Error during VAD processing: {e}", file=sys.stderr)


# --- SỬA LẠI audio_capture_thread ---
def audio_capture_thread(loop):
    """Thread ghi âm audio từ loopback microphone."""
    global recording_active, temp_audio_buffer
    print("[Audio Thread] Started.")

    try:
        print(f"[Audio Thread] Attempting to record from: {loopback_mic.name} with {CHANNELS} channel(s), SR={SAMPLE_RATE}")
        with loopback_mic.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS, blocksize=BLOCK_SIZE_SAMPLES) as mic:
            print(f"[Audio Thread] Recorder started (blocksize={BLOCK_SIZE_SAMPLES}). Capturing loopback audio...")
            while recording_active:
                data = mic.record(numframes=BLOCK_SIZE_SAMPLES)

                if not recording_active: break

                if data is None or data.shape[0] == 0:
                    time.sleep(0.01)
                    continue

                if data.ndim > 1 and data.shape[1] >= CHANNELS:
                    if CHANNELS == 1:
                        mono_data = data[:, 0]
                    else:
                        mono_data = data
                else:
                    mono_data = data.flatten()

                if np.isnan(mono_data).any() or np.isinf(mono_data).any():
                    print("[Audio Thread] Warning: NaN or Inf detected in audio data, skipping block.", file=sys.stderr)
                    continue


                int_data = (np.clip(mono_data, -1.0, 1.0) * 32767).astype(DTYPE)
                byte_data = int_data.tobytes()

                # Xử lý VAD
                temp_audio_buffer.extend(byte_data)
                while len(temp_audio_buffer) >= VAD_FRAME_BYTES:
                    vad_frame = temp_audio_buffer[:VAD_FRAME_BYTES]
                    process_vad(bytes(vad_frame), loop)
                    del temp_audio_buffer[:VAD_FRAME_BYTES]

    except RuntimeError as e:
        print(f"[Audio Thread] Soundcard Runtime Error: {e}", file=sys.stderr)
        print("[Audio Thread] Check sample rate/channels support and loopback configuration.", file=sys.stderr)
        recording_active = False
    except Exception as e:
        print(f"[Audio Thread] Unexpected Error: {e}", file=sys.stderr)
        recording_active = False
    finally:
        print("[Audio Thread] Finished.")

async def receive_task(websocket):
    print("[Receive Task] Started.")
    try:
        async for message in websocket:
            try:
                result = json.loads(message)
                if "partial" in result:
                    print(f"Partial: {result['partial']}{' ' * 10}\r", end='', flush=True)
                elif "text" in result:
                    print(f"{' ' * 80}\r", end='', flush=True)
                    print(f"> {result['text']} <")
                else:
                    print(f"\nServer: {result}")
            except json.JSONDecodeError:
                print(f"\nServer (raw): {message}")
    except websockets.exceptions.ConnectionClosedOK:
        print("\n[Receive Task] Connection closed normally.")
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"\n[Receive Task] Connection closed with error: {e.rcvd.code} {e.rcvd.reason}")
    except Exception as e:
        print(f"\n[Receive Task] Error: {e}")
    finally:
        print("[Receive Task] Finished.")


async def send_task(websocket):
    global recording_active
    print("[Send Task] Started. Press SPACE to toggle sending audio.")
    print(f"Current state: {'SENDING' if send_audio_enabled else 'PAUSED'}")
    items_sent = 0
    try:
        while True:
            byte_data = await audio_queue.get()
            if byte_data is None:
                print("[Send Task] Received stop signal.")
                audio_queue.task_done()
                break # Kết thúc vòng lặp

            if recording_active:
                await websocket.send(byte_data)
                items_sent += 1

            else:
                print("[Send Task] recording_active is false, discarding data from queue.")

            audio_queue.task_done()

    except websockets.exceptions.ConnectionClosed:
        print("[Send Task] Connection closed while sending.")
        recording_active = False
    except Exception as e:
        print(f"[Send Task] Error: {e}")
        recording_active = False
    finally:
        print(f"[Send Task] Finished. Total chunks sent: {items_sent}")
        if websocket and not websocket.closed:
            await websocket.close(reason="Client terminated by user.")

def on_press(key):
    global send_audio_enabled
    if key == TOGGLE_KEY:
        send_audio_enabled = not send_audio_enabled
        state = "SENDING" if send_audio_enabled else "PAUSED"
        print(f"{' ' * 80}\r", end='', flush=True)
        print(f"Keybind: Audio sending {state}. Press SPACE to toggle.", flush=True)

def start_key_listener():
    global key_listener
    key_listener = keyboard.Listener(on_press=on_press)
    key_listener.start()
    print("Key listener started.")

def stop_key_listener():
    global key_listener
    if key_listener:
        print("Stopping key listener...")
        key_listener.stop()
        key_listener = None
        print("Key listener stopped.")

async def run_stt_client():
    global recording_active, key_listener_thread, main_loop
    main_loop = asyncio.get_running_loop()
    key_listener_thread = threading.Thread(target=start_key_listener, daemon=True)
    key_listener_thread.start()
    capture_thread = threading.Thread(target=audio_capture_thread, args=(main_loop,), daemon=True)
    capture_thread.start()
    websocket_connection = None
    try:
        print(f"Connecting to {SERVER_URI}...")
        websocket_connection = await websockets.connect(SERVER_URI, ping_interval=20, ping_timeout=20)
        print("Connected! Capturing desktop audio. Press SPACE to pause/resume, Ctrl+C to stop.")

        receiver = asyncio.create_task(receive_task(websocket_connection))
        sender = asyncio.create_task(send_task(websocket_connection))

        done, pending = await asyncio.wait(
            [receiver, sender],
            return_when=asyncio.FIRST_COMPLETED,
        )
        print("One task finished, initiating shutdown...")

    except websockets.exceptions.InvalidURI:
        print(f"Invalid WebSocket URI: {SERVER_URI}")
    except websockets.exceptions.WebSocketException as e:
        print(f"WebSocket connection failed: {e}")
    except KeyboardInterrupt:
        print("\nCtrl+C pressed, initiating shutdown...")
    except Exception as e:
        print(f"An unexpected error occurred in main loop: {e}", file=sys.stderr)
    finally:
        print("Starting cleanup...")
        recording_active = False
        if capture_thread and capture_thread.is_alive():
            print("Waiting for audio capture thread to finish...")
            capture_thread.join(timeout=2)
            if capture_thread.is_alive(): print("Warning: Audio capture thread did not finish promptly.")

        print("Putting stop signal into audio queue...")
        if main_loop and main_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(audio_queue.put(None), main_loop)
            try: future.result(timeout=1)
            except (TimeoutError, asyncio.TimeoutError): print("Warning: Timeout putting stop signal into queue.")
            except Exception as e_put: print(f"Error putting stop signal: {e_put}")
        else:
            try: audio_queue.put_nowait(None)
            except: pass

        tasks = [task for task in asyncio.all_tasks(loop=main_loop) if task is not asyncio.current_task(loop=main_loop)]
        if tasks:
            print(f"Cancelling {len(tasks)} outstanding asyncio tasks...")
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            print("Asyncio tasks cancelled.")

        if websocket_connection and not websocket_connection.closed:
            print("Closing WebSocket connection...")
            await websocket_connection.close()
            print("WebSocket connection closed.")

        stop_key_listener()
        if key_listener_thread and key_listener_thread.is_alive():
            key_listener_thread.join(timeout=1)

        print("Cleanup finished. Client exiting.")


if __name__ == "__main__":
    recording_active = True
    send_audio_enabled = True
    try:
        asyncio.run(run_stt_client())
    except KeyboardInterrupt:
        print("\nExiting program.")
    except Exception as e:
        print(f"Fatal error in main execution: {e}")