# Video Dubber

Video Dubber là ứng dụng web chạy trên máy cá nhân để lồng tiếng và Việt hóa video. Dán link từ Douyin, TikTok, YouTube hoặc Facebook để tải video, nhận dạng lời nói, dịch sang tiếng Việt và xuất bản dựng từ trình duyệt.

## Quy trình

1. **Nhận dạng và dịch.** Faster-whisper nhận dạng lời thoại. Ứng dụng ghép các đoạn nhận dạng thành câu trọn ý trước khi dịch.
2. **Tạo giọng và phụ đề.** Tạo giọng đọc tiếng Việt, căn theo thời gian lời thoại và dựng phụ đề. Có thể tách giọng gốc để giữ lại nhạc nền.
3. **Xem và xuất video.** Xem bản dựng thử, chỉnh nội dung dịch và xuất video bằng FFmpeg. Có thể che phụ đề gốc hoặc dịch chữ xuất hiện trên hình.

Tùy video, bạn có thể chọn lồng tiếng, chỉ làm phụ đề hoặc dịch chữ trên màn hình. Ứng dụng gợi ý chế độ dựa trên lời nói và chữ nhận dạng được. Quá trình xử lý chạy trên máy của bạn. Dịch và tạo giọng có thể dùng dịch vụ bên ngoài tùy cấu hình.

## Chạy trên Windows

Với Python 3.11 và FFmpeg đã có trong PATH, chạy các lệnh sau từ thư mục repo.

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
.\run.bat
```

Mở [Video Dubber](http://127.0.0.1:8765) trong trình duyệt. Xem [hướng dẫn chi tiết](README-legacy.md) để thiết lập GPU NVIDIA, cấu hình dịch và giọng đọc hoặc xử lý lỗi.
