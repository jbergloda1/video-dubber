# Video Dubber

App web chạy trên máy của bạn để **Việt hoá video ngắn** từ Douyin, TikTok, YouTube, Facebook, Bilibili. Dán một link, app tự tải video, nghe lời thoại, dịch, đọc lồng tiếng Việt, che phụ đề / watermark gốc, gắn phụ đề Việt và xuất ra bản sẵn đăng TikTok 9:16.

Mọi thứ chạy trên máy bạn (GPU NVIDIA, đang dùng RTX 3060 12 GB). Phần dịch gọi AI qua tài khoản của bạn (Claude, ChatGPT/Codex, Gemini…) hoặc chạy offline.

![Màn hình Xử lý video](docs/demo/workspace.png)

[Video demo 20 giây](docs/demo/video-dubber-demo.mp4)

---

## Chạy app

Nhấp đúp **`run.bat`**. Cửa sổ đen hiện ra, trình duyệt tự mở http://127.0.0.1:8765. **Giữ cửa sổ đen mở**; đóng nó là tắt app.

- **Trên điện thoại:** cài Tailscale trên cả máy tính và điện thoại (cùng tài khoản). `run.bat` in ra địa chỉ `http://100.x.x.x:8765`, mở địa chỉ đó trên điện thoại.
- **Có code mới:** nút **🔄 Cập nhật** hiện ở góc trên, bấm là server tự khởi động lại (chỉ khi không có job đang chạy). Lệnh tương đương:

  ```bash
  curl -X POST http://127.0.0.1:8765/api/admin/restart
  ```

- **Job đang chạy dở** khi tắt server sẽ hiện lỗi "Server đã khởi động lại"; bấm **↻ Chạy lại** để làm tiếp, các bước đã xong được dùng lại.

Cài lần đầu trên máy mới (Python 3.11, ffmpeg, PyTorch CUDA, model tuỳ chọn): xem [Hướng dẫn chi tiết › Cài đặt lần đầu](README-legacy.md#cài-đặt-lần-đầu-windows--gpu-nvidia).

---

## Làm video đầu tiên

1. Tab **🎙 Xử lý video** → dán link (hoặc kéo thả file video).
2. Chọn kiểu kết quả. Không chắc thì để **✨ Tự nhận diện**: app nghe thử và nhìn thử video rồi tự chọn.
   Có sẵn 3 preset bấm nhanh: **⚡ Reup nhanh**, **🎬 Phim + Biên kịch**, **💬 Chỉ sub**.
3. Bấm **🚀 Bắt đầu**. Tiến trình từng bước hiện ở bên phải.
4. Video xong thì xem ở tab **✅ Kết quả** của job, rồi tải về hoặc đăng.

| Kiểu kết quả | Dùng khi | Âm thanh |
|---|---|---|
| 🎙 **Lồng tiếng** | Một người kể / thuyết minh | Giọng Việt thay giọng gốc, giữ nhạc nền |
| 📢 **Thuyết minh** | Phim, vlog, hội thoại nhiều người | Giọng gốc nhỏ lại, giọng Việt đè lên |
| 💬 **Chỉ sub** | Bài hát, biểu diễn, ít lời | Giữ nguyên |
| 🔤 **Chỉ dịch chữ màn hình** | Video toàn chữ, gần như không có lời | Giữ nguyên |

Video không có lời nói nào sẽ tự chuyển sang "Chỉ dịch chữ màn hình".

---

## Các tab

**Thanh trên cùng** (trên điện thoại là thanh dưới đáy):

| Tab | Để làm gì |
|---|---|
| 🎙 **Xử lý video** | Tạo job, lịch sử job (có tên tiếng Việt, tìm được bằng tiếng Việt) và chi tiết từng job |
| 🔎 **Tìm video** | Tìm video đáng làm. **Top · Trending · Từ khoá**: top 10 theo lượt xem từng thể loại TikTok Việt Nam / Douyin, insights đang lên, tìm theo từ khoá. **Thư viện đã lấy**: mọi video đã quét, lọc theo nguồn / lượt thích / thời lượng. Chọn nhiều video để chạy hàng loạt |
| 📦 **Kho video** | Tải sẵn video nguồn về máy để xử lý sau |
| 🤖 **Tự chạy** | Autopilot ban đêm: tự chọn video hot và xử lý trong khung giờ đặt trước (mặc định tắt) |

**Trong mỗi job** có 5 tab, tab nào mở mới tải dữ liệu tab đó:

| Tab | Có gì |
|---|---|
| ✅ **Kết quả** | Video, file tải về, kiểm tra chuẩn TikTok trước khi đăng |
| ✏ **Sửa** | Bản dịch (sửa trực tiếp rồi "Lưu & render lại"), 🎬 Biên kịch, chữ trên màn hình, vùng xoá watermark |
| 📋 **Đánh giá** | Chấm điểm, ghi chú cần sửa, đổi nhạc nền (xem dưới) |
| 📤 **Đăng** | Tiêu đề, mô tả, hashtag cho TikTok / YouTube / Facebook |
| 📜 **Nhật ký** | Log trực tiếp của job |

---

## Tính năng chính

- **Dịch theo câu trọn ý.** Nhận dạng giọng nói hay cắt ngang câu; app gộp lại thành câu hoàn chỉnh rồi mới dịch, mỗi câu được đọc liền một hơi. Phụ đề chỉ chia dòng trên màn hình, ở chỗ ngắt tự nhiên.
- **Nhiều AI dịch, tự dự phòng.** Claude, ChatGPT/Codex, Gemini (qua tài khoản của bạn, không cần API key), API OpenAI-compatible, Ollama, hoặc máy dịch offline. Provider lỗi thì tự chuyển sang provider sau.
- **Giọng đọc.** VieNeu-TTS (giọng Việt tự nhiên, chạy GPU), Edge TTS (miễn phí), ElevenLabs, Google, Piper. Tự nhận diện người nói và giới tính để chọn giọng; câu đọc không kịp thì AI tự viết gọn lại.
- **Che phụ đề và watermark gốc.** Dò phụ đề cứng theo từng dòng (che bám đúng dòng, đúng lúc), xoá logo / tên kênh nền tảng khác bằng làm mờ hoặc vẽ lại (LaMa).
- **Dịch chữ trên màn hình.** OCR tiêu đề, chú thích in trên hình, dịch và vẽ lại đúng chỗ.
- **🎬 Biên kịch.** AI xem phim rồi viết hook mở đầu, lời kể, thanh tiêu đề, cắt các đoạn lặng (không bao giờ cắt vào lời thoại), câu loop cuối video. Bạn duyệt kịch bản trước khi render.
- **📋 Đánh giá & sửa.** Ghi chú lỗi khi xem video:
  - **Kịch bản:** AI viết lại đúng các câu bạn nhắc tới, hiện bản cũ / mới để chọn áp dụng.
  - **Kỹ thuật:** duyệt xong thì lỗi vào hàng chờ cho Claude Code sửa code (`data/feedback/queue.json`, cách xử lý trong [CLAUDE.md](CLAUDE.md)).
  - **Nhạc nền:** AI gợi ý nhạc hợp video và viết sẵn prompt để dán vào Suno / Udio; tải file nhạc lên là app thay nhạc nền gốc.
- **Bản TikTok 9:16.** Render cùng lượt với bản chính; kiểm tra kích thước, âm lượng, watermark sót, chữ nằm dưới nút TikTok. Đăng thẳng lên TikTok qua API chính thức.
- **Chạy lại nhanh.** Mỗi bước nhớ đầu vào của nó; chạy lại chỉ làm những bước có đầu vào thay đổi. Đổi nhạc nền hay âm lượng thì chỉ trộn lại tiếng, giữ nguyên phần hình.
- **Thư mục `final/`.** Video xong được chép ra đây với tên tiếng Việt dễ tìm, ví dụ `Bữa sáng mỡ đuôi cừu (Lồng tiếng).mp4`.

Chi tiết từng tính năng (Biên kịch, VieNeu-TTS, phong cách dịch, kiểm tra TikTok, Autopilot…): [README-legacy.md](README-legacy.md).

---

## Tải video Douyin / TikTok / Bilibili qua DTK (tuỳ chọn)

App có thể lấy thông tin và tải video **không watermark** qua [Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) (DTK) mà không cần mở Chrome. Nhanh hơn, và quét được nhiều link cùng lúc.

1. Tự chạy một server DTK trên máy (mặc định `http://127.0.0.1:8000`, cả bản v5 lẫn v4 đều được).
2. **⚙ Cài đặt → DTK MCP**: bật, điền địa chỉ và API key (nếu server của bạn yêu cầu), bấm **🔌 Kiểm tra kết nối**.

| App dùng DTK cho | Nếu DTK không trả lời |
|---|---|
| Tải video, xem trước, đọc tiêu đề / thời lượng (Douyin, TikTok, Bilibili) | Lùi về Chrome ẩn (Douyin) hoặc yt-dlp |
| Bảng hot Douyin, quét kênh `user:…` | Lùi về Chrome |
| Tìm từ khoá TikTok, quét kênh `@handle` | Lùi về Chrome |

DTK đang **bật sẵn** nhưng không bắt buộc. Không có server DTK thì kết nối bị từ chối ngay và app tự lùi về cách cũ, gần như không tốn thêm thời gian.

---

## Khi có lỗi

| Thấy gì | Làm gì |
|---|---|
| Douyin báo chặn / đòi xác minh | Bấm **Chạy lại**: app mở cửa sổ Chrome để bạn tự kéo captcha. Hoặc **⚙ Cài đặt → Douyin → Đăng nhập** |
| Tìm kiếm TikTok không ra kết quả | TikTok chặn tìm kiếm khi chưa đăng nhập. **⚙ Cài đặt → TikTok Việt Nam → Đăng nhập**, nên dùng mã QR |
| Câu dịch nghe cụt, sai ý | Tab **📋 Đánh giá** → ghi chú loại "Kịch bản" → AI đề xuất sửa |
| Chữ dịch đè, che mờ sai, lỗi giao diện | Tab **📋 Đánh giá** → ghi chú loại "Kỹ thuật" → **Duyệt → gửi Claude** |
| `No module named uvicorn` | Thư viện được cài từ bên trong app Claude (bị ảo hoá). Chạy `powershell -ExecutionPolicy Bypass -File fix_env.ps1` trong PowerShell thường |
| Server không bật được, cổng 8765 bận | Server đã chạy sẵn: mở thẳng http://127.0.0.1:8765 |
| Rất chậm, log ghi "chạy trên CPU" | PyTorch bản CPU đè bản CUDA: cài lại theo bước 4 trong [hướng dẫn cài đặt](README-legacy.md#cài-đặt-lần-đầu-windows--gpu-nvidia) |

Bảng đầy đủ: [README-legacy.md › Không chạy được?](README-legacy.md#không-chạy-được-kiểm-tra-theo-thứ-tự)

---

## Cho người sửa code

| Phần | File |
|---|---|
| API (FastAPI) | [dubber/app.py](dubber/app.py) |
| Hàng đợi job, trạng thái | [dubber/jobs.py](dubber/jobs.py) |
| Các bước xử lý | [dubber/pipeline.py](dubber/pipeline.py), mỗi bước một module: `downloader`, `separate`, `asr`, `translate`, `speakers`, `tts`, `subdetect`, `watermark`, `screentext`, `render`, `content` (Biên kịch), `tiktokcheck` |
| Dùng lại kết quả khi chạy lại | [dubber/checkpoint.py](dubber/checkpoint.py) |
| Tìm video | `trending.py`, `hot.py`, `library.py`, `sources/`, `dtk_client.py` |
| Giao diện | `dubber/static/index.html` (HTML), `app.css`, `js/01-core.js … 10-boot.js` |

- **Giao diện:** các file JS là script thường nạp theo thứ tự, dùng chung biến toàn cục (không phải ES module). Sửa file tĩnh chỉ cần tải lại trang. `index.single.html` là bản gộp cũ, đừng sửa.
- **Test:**

  ```bash
  python -m pytest -q
  ```

- **Đo tốc độ** các job đã chạy:

  ```bash
  python tools/bench.py
  ```

  Kết quả các lần tối ưu: [docs/PLAN_OPTIMIZE.md](docs/PLAN_OPTIMIZE.md).
- **Hàng chờ lỗi** từ tab Đánh giá và quy ước cho Claude: [CLAUDE.md](CLAUDE.md).
