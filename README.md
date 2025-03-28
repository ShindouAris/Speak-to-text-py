# Vosk Speech-to-Text FastAPI Server

Ứng dụng FastAPI này cung cấp API nhận dạng giọng nói (Speech-to-Text - STT) thời gian thực và xử lý file sử dụng engine Vosk offline. Nó hỗ trợ nhiều ngôn ngữ, streaming qua WebSocket và upload file qua HTTP POST.

## Tính năng

*   **Nhận dạng Offline:** Sử dụng Vosk, hoạt động hoàn toàn trên máy chủ của bạn, không cần kết nối internet.
*   **Streaming Real-time:** Endpoint WebSocket (`/ws/stt/{lang_code}`) nhận luồng âm thanh PCM rå và trả về kết quả nhận dạng (tạm thời và cuối cùng) theo thời gian thực.
*   **Xử lý File Upload:** Endpoint HTTP POST (`/stt/{lang_code}`) nhận file âm thanh tải lên, tự động chuyển đổi định dạng (nếu có `pydub` và `ffmpeg`) và trả về kết quả nhận dạng cuối cùng.
*   **Đa ngôn ngữ:** Hỗ trợ nhiều ngôn ngữ bằng cách tải các model Vosk tương ứng.
*   **Giao diện Console:** Sử dụng `rich` để hiển thị thông tin API và log một cách rõ ràng khi chạy server.
*   **Log Vosk được ẩn:** Cấu hình để ẩn các log nội bộ chi tiết từ thư viện Vosk/Kaldi C++.

## Cài đặt môi trường

Trước khi bắt đầu, đảm bảo bạn đã cài đặt:

1.  **Python:** Phiên bản 3.8 trở lên được khuyến nghị.
2.  **pip:** Trình quản lý gói Python (thường đi kèm với Python).
3.  **ffmpeg:** **Rất quan trọng** cho việc chuyển đổi định dạng âm thanh trong endpoint HTTP POST.
    *   **Ubuntu/Debian:** `sudo apt update && sudo apt install ffmpeg`
    *   **macOS (sử dụng Homebrew):** `brew install ffmpeg`
    *   **Windows:** Tải bản build từ [trang chủ ffmpeg](https://ffmpeg.org/download.html) hoặc sử dụng trình quản lý gói như Chocolatey (`choco install ffmpeg`) và đảm bảo `ffmpeg.exe` nằm trong biến môi trường `PATH` của hệ thống.

## Cài đặt

1.  **Clone Repository (hoặc tải code):**
    ```bash
    git clone https://github.com/ShindouAris/Speak-to-text-py.git
    cd Speak-to-text-py
    ```

2.  **Tạo và Kích hoạt Virtual Environment (Khuyến nghị):**
    ```bash
    # Windows
    python -m venv venv
    .\venv\Scripts\activate

    # macOS/Linux
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Cài đặt Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
    (Xem nội dung file `requirements.txt` mẫu bên dưới)

   4.  **Tải Vosk Models:**
       *   Truy cập [Trang Model Vosk](https://alphacephei.com/vosk/models).
       *   Tải các model cho ngôn ngữ bạn muốn hỗ trợ (ví dụ: `vosk-model-vn-0.4` cho tiếng Việt, `vosk-model-en-us-0.22` cho tiếng Anh).
       *   Giải nén các model đã tải.
       *   Tạo thư mục `Models` trong thư mục gốc của dự án.
       *   Đặt các thư mục model đã giải nén vào bên trong thư mục `Models`. Cấu trúc thư mục phải giống như sau:
           ```
           root/
           ├── Models/
           │   ├── Viet/         <-- Thư mục chứa model tiếng Việt đã giải nén
           │   ├── English/      <-- Thư mục chứa model tiếng Anh đã giải nén
           │   └── ...           <-- Các thư mục model ngôn ngữ khác
           ├── core/
           │   ├── loader.py
           │   └── logger.py
           └── main.py
           └── README.md
           └── requirements.txt
           ```

5.  **Cài đặt ngôn ngữ**
    *   Mở file `core/loader.py`.
    *   Đảm bảo dictionary `LANGUAGE_FOLDER_MAP` có chính xác tên các thư mục trong `Models` (ví dụ: `"Viet"`) sang mã ngôn ngữ bạn muốn sử dụng trong API (ví dụ: `"vn"`).
    ```python
    # src/loader.py
    LANGUAGE_FOLDER_MAP = {
        "Viet": "vi",
        "English": "en",
        "Chinese": "zh-CN", # Ví dụ
        # ... chỉnh sửa cho phù hợp ...
    }
    ```

## Chạy Server

Từ thư mục gốc của dự án (nơi có file `main.py`), chạy lệnh sau:

```bash
python main.py