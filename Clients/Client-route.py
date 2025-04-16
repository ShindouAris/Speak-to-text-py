import requests

# Địa chỉ API của bạn (cập nhật nếu cần)
API_URL = "http://localhost:8000/stt/ja"  # Thay 'vn' bằng mã ngôn ngữ khác nếu cần
AUDIO_FILE = "videoplayback.wav"  # Thay thế bằng đường dẫn file âm thanh của bạn

def send_audio_to_stt(api_url, audio_file):
    with open(audio_file, "rb") as f:
        files = {"file": (audio_file, f, "audio/wav")}  # Đảm bảo định dạng file hợp lệ
        response = requests.post(api_url, files=files)

    if response.status_code == 200:
        print("✅ Nhận dạng thành công:", response.json()["text"])
    else:
        print(f"❌ Lỗi {response.status_code}: {response.text}")

if __name__ == "__main__":
    send_audio_to_stt(API_URL, AUDIO_FILE)
