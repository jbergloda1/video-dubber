# Video Dubber — Douyin/TikTok → Tiếng Việt

Server chạy trên máy bạn (RTX 3060). Dán link Douyin/TikTok/YouTube/Facebook vào trình duyệt → tự tải, nhận dạng giọng, dịch, lồng tiếng, che phụ đề gốc, gắn phụ đề Việt, render.

## Chạy server (máy đã cài đủ)

**Cách 1:** nhấp đúp `run.bat`. Cửa sổ đen hiện ra và trình duyệt tự mở http://127.0.0.1:8765.
**Giữ cửa sổ đen đó mở.** Đóng nó là server tắt.

**Cách 2:** mở PowerShell rồi chạy:

```bash
cd D:\Claude\video-dubber
```

```bash
python -m uvicorn dubber.app:app --host 127.0.0.1 --port 8765
```

Khi thấy dòng `Uvicorn running on http://127.0.0.1:8765` thì mở http://127.0.0.1:8765.
Muốn tắt: bấm `Ctrl+C` trong cửa sổ đó.

Job đang chạy dở hoặc video đang tải trong Kho video sẽ tự chạy tiếp khi bật lại server.

**Cập nhật code không cần đóng run.bat:** khi có code mới trên ổ đĩa, nút **🔄 Cập nhật** hiện ở góc trên. Bấm vào thì server tự khởi động lại trong vài giây (run.bat bật lại ngay) rồi trang tự tải lại. Nút chỉ chạy khi không có job hoặc video trong kho đang chạy. Lần đầu sau bản cập nhật này vẫn phải đóng và mở lại run.bat một lần để dùng run.bat mới.

## 📱 Dùng trên điện thoại (Tailscale)

1. Cài **Tailscale** trên máy tính và trên điện thoại, đăng nhập cùng một tài khoản.
2. Chạy `run.bat`. Cửa sổ đen sẽ in ra dòng `Tren dien thoai (Tailscale): http://100.x.x.x:8765`.
3. Mở địa chỉ đó trong Safari hoặc Chrome trên điện thoại. Có thể bấm "Thêm vào MH chính" để mở như một app.

Chỉ máy tính này và các thiết bị trong Tailscale của bạn mới vào được; người khác dùng chung Wi-Fi sẽ bị chặn (403). Muốn mở cho cả mạng Wi-Fi nhà thì vào **⚙ Cài đặt → Server**. App không có mật khẩu, nên chỉ bật khi bạn tin mọi người dùng chung Wi-Fi.

## Không chạy được? Kiểm tra theo thứ tự

| Thấy gì | Nguyên nhân | Cách sửa |
|---|---|---|
| `No module named uvicorn` dù Claude chạy được | Thư viện được cài **từ bên trong app Claude**. App Claude là gói MSIX nên mọi thứ nó cài vào `%APPDATA%` thật ra nằm trong thư mục ảo riêng của nó; cửa sổ PowerShell / `run.bat` bình thường không thấy | Chạy `powershell -ExecutionPolicy Bypass -File fix_env.ps1` trong **cửa sổ PowerShell bình thường** (không phải trong Claude). Script chép thư viện Python và Chromium của Playwright ra chỗ thật, không xoá gì. Chạy lại mỗi khi Claude vừa cài thêm thư viện |
| `[WinError 10048] ... only one usage of each socket address` | Server **đã chạy rồi** (một cửa sổ khác, hoặc Claude bật sẵn) | Mở thẳng http://127.0.0.1:8765. Nếu trang không lên: chạy `netstat -ano \| findstr 8765` để lấy PID ở cột cuối, rồi `taskkill /PID <số đó> /F`, rồi bật lại |
| `'python' is not recognized` | Python chưa có trong PATH | Cài lại Python 3.11 và tick **Add python.exe to PATH**, hoặc gọi thẳng `"C:\Program Files\Python311\python.exe" -m uvicorn ...` |
| `No module named uvicorn` / `No module named fastapi` | Chưa cài thư viện, hoặc đang dùng Python khác | Làm phần **Cài đặt lần đầu** bên dưới bằng đúng Python đó (`python -m pip ...`) |
| `ModuleNotFoundError: No module named 'dubber'` | Chạy lệnh ở sai thư mục | `cd D:\Claude\video-dubber` trước khi chạy |
| Trang mở được nhưng job báo lỗi `ffmpeg` | Chưa có ffmpeg trong PATH | `choco install ffmpeg-full` (hoặc tải bản full ở gyan.dev, thêm thư mục `bin` vào PATH). Kiểm tra: `ffmpeg -version` |
| Nhật ký ghi "chạy trên CPU", rất chậm | PyTorch bản CPU đè lên bản CUDA | Chạy lại bước 4 ở phần cài đặt. Kiểm tra: `python -c "import torch;print(torch.cuda.is_available())"` phải ra `True` |
| Cửa sổ đen tắt ngay | Lỗi lúc khởi động | Chạy Cách 2 trong PowerShell để đọc dòng lỗi cuối cùng |

Server bị tắt khi đóng cửa sổ, tắt máy hoặc khi phiên Claude chạy hộ kết thúc. Lúc đó chỉ cần bật lại bằng `run.bat`.

## Cài đặt lần đầu (Windows + GPU NVIDIA)

Máy đang dùng: Python 3.11, ffmpeg 8 (bản full), PyTorch 2.11 + CUDA 12.8, driver NVIDIA 617.

1. Cài **Python 3.11** (tick *Add to PATH*), **ffmpeg** bản full (`choco install ffmpeg-full`), driver NVIDIA mới.
2. Cài thư viện:

   ```bash
   cd D:\Claude\video-dubber
   ```

   ```bash
   python -m pip install -r requirements.txt
   ```

3. Cài trình duyệt cho Playwright (dùng để tải Douyin):

   ```bash
   python -m playwright install chromium
   ```

4. Cài PyTorch bản GPU (**làm sau cùng**, vì các gói khác hay kéo bản CPU về):

   ```bash
   python -m pip install --force-reinstall --no-deps torch torchaudio --index-url https://download.pytorch.org/whl/cu128
   ```

5. Tuỳ chọn, cho chất lượng tốt hơn (xem ghi chú trong `requirements.txt`):
   - Tách giọng BS-RoFormer: `python -m pip install --no-deps audio-separator==0.48.0 rotary-embedding-torch==0.6.5 diffq-fixed==0.2.4 onnx2torch-py313==1.6.0`, rồi `python -m pip install beartype==0.18.5 ml_collections pydub resampy samplerate==0.1.0`.
   - OCR trên GPU: `python -m pip uninstall -y onnxruntime`, rồi `python -m pip install onnxruntime-gpu==1.22.0`.
   - Giọng Google Dịch: `python -m pip install gTTS`, rồi `python -m pip install "click>=8.4.2"` (gTTS hay hạ cấp click).
6. Bật server (phần trên) → ⚙ Cài đặt: chọn provider dịch, giọng đọc.

Lần chạy đầu sẽ tự tải model (Whisper, BS-RoFormer, ECAPA… vài GB), nên job đầu chậm hơn.
Kiểm tra mọi thứ ổn: `python -m pytest -q` (phải ra toàn bộ `passed`).

Mở http://127.0.0.1:8765

## Quy trình (tự chạy, tiến trình hiển thị trực tiếp)

| Bước | Công cụ | Chạy ở đâu |
|---|---|---|
| Tải video | yt-dlp (TikTok), Playwright Chromium (Douyin) | máy bạn |
| Tách nhạc nền | BS-RoFormer (audio-separator, fp16), dự phòng Demucs | **GPU** |
| Watermark gốc | OCR + cạnh cố định → LaMa/inpaint; watermark của bạn: PNG + ffmpeg overlay chuyển động | **GPU** |
| Nhận dạng giọng nói | faster-whisper large-v3-turbo | **GPU** |
| Dịch + chia câu + sửa lỗi nghe nhầm | Claude (CLI subscription / API) hoặc Ollama | Claude / **GPU** |
| Giọng đọc | Edge TTS (miễn phí) hoặc ElevenLabs | cloud |
| Dò phụ đề cứng | RapidOCR | máy bạn |
| Render preview 30s + bản cuối | ffmpeg NVENC | **GPU** |

**Dịch theo câu trọn ý.** Whisper cắt lời nói thành mẩu theo độ dài, hay cắt ngang câu.
- **Dịch:** AI gộp các mẩu đó thành câu hoàn chỉnh rồi mới dịch, nên mỗi câu tiếng Việt đứng một mình vẫn hiểu và đúng trật tự từ tiếng Việt.
- **Lưới an toàn:** nếu một dòng vẫn chỉ là nửa câu (không có dấu kết câu và dòng sau viết thường), app ghép hai dòng lại, với điều kiện hai dòng cách nhau ≤ 0,8 giây, cùng người nói và câu ghép dài tối đa 12 giây.
- **Giọng đọc:** mỗi câu được đọc thành **một clip liền hơi**, không còn khoảng lặng giữa câu.
- **Phụ đề:** chỉ trên màn hình, câu dài mới được chia thành vài dòng (`dubber/subsplit.py`). Chỗ chia là dấu câu, hoặc ngay trước từ nối như "thì", "mà", "nhưng"; thời gian mỗi dòng tính theo số âm tiết trong lúc giọng đọc.

## ✨ Tự nhận diện kiểu video

App tự nghe lời nói và đọc chữ trên hình rồi gợi ý kiểu kết quả, mất khoảng 20–40 giây mỗi video trên GPU.

- **Đo:** tỉ lệ thời lượng có lời nói (VAD), ngôn ngữ (Whisper), số người nói (ECAPA), phụ đề cứng và chữ khác trên hình (OCR 12 khung; logo, tên kênh, watermark đứng yên không tính).
- **Chọn:** quy tắc từ số đo, rồi AI tinh chỉnh theo tiêu đề và vài câu nói (thể loại: tài liệu, vlog, phim, game…) kèm lý do. Không có AI thì dùng quy tắc.

| Kiểu | Khi nào |
|---|---|
| 🎙 Lồng tiếng | 1 người kể/thuyết minh, lời nói là nội dung chính |
| 📢 Thuyết minh | Hội thoại nhiều người, vlog, phim: cần giữ cảm xúc giọng gốc |
| 💬 Chỉ Sub | Bài hát, biểu diễn, rất ít lời |
| 🔤 Chỉ dịch chữ | Gần như không có lời nói, nội dung nằm ở chữ trên hình |

Cách dùng:
- **Xử lý video:** chọn thẻ **✨ Tự nhận diện kiểu video** rồi bấm Bắt đầu. Tải xong, bước "Nhận diện kiểu video" chọn kiểu, ghi lý do vào nhật ký rồi chạy tiếp.
- **Kho video:** chọn video, bấm **🔎 Nhận diện kiểu video**. Mỗi thẻ hiện gợi ý (rê chuột để xem lý do). Khi xử lý, kiểu **✨ Theo nhận diện từng video** cho mỗi video chạy đúng kiểu được gợi ý.

## 📤 Đăng lên TikTok (API chính thức)

App đăng bằng **ứng dụng nhà phát triển của chính bạn** trên TikTok. Bạn đăng nhập trên trang của TikTok, app chỉ nhận một mã truy cập (lưu trên máy ở `data/tiktok_token.json`, không bao giờ gửi ra trình duyệt) và **không bao giờ thấy mật khẩu**.

**Tạo ứng dụng (một lần):**
1. Vào **developers.tiktok.com**, đăng nhập, chọn **Manage apps → Connect an app**. Điền tên, icon, mô tả, mục đích dùng (đăng video của chính bạn).
2. **Add products:** thêm **Login Kit** và **Content Posting API**.
3. Ở **Basic information → Platforms** tick **Desktop**. Trong **Login Kit**, mục Redirect URI của **Desktop** (không phải Web, vì Web bắt buộc https và tên miền) dán `http://localhost:8765/api/tiktok/callback/` (nút 📋 trong ⚙ Cài đặt). Theo tài liệu Login Kit Desktop của TikTok, app desktop được dùng `localhost` hoặc `127.0.0.1` kèm cổng. Nếu trang vẫn không nhận, dùng một tunnel https (Cloudflare Tunnel, ngrok) trỏ về `http://localhost:8765`, rồi dán địa chỉ `https://…/api/tiktok/callback/` của tunnel vào cả TikTok lẫn ô "Redirect URI riêng" trong ⚙ Cài đặt.
4. **Scopes:** `user.info.basic` + `video.upload` (đăng vào hộp thư) và/hoặc `video.publish` (đăng thẳng).
5. Chép **Client Key** và **Client Secret** vào ⚙ Cài đặt → Đăng TikTok, bấm Lưu, rồi bấm **🔑 Đăng nhập TikTok** và cho phép ứng dụng trên trang TikTok.

**Hai cách đăng:**

| Cách | Quyền | Kết quả |
|---|---|---|
| Vào hộp thư TikTok (mặc định) | `video.upload` | Video vào hộp thư trong app TikTok, bạn mở thông báo rồi bấm đăng (sửa caption, nhạc… được). Tối đa 5 video chờ trong 24 giờ. |
| Đăng thẳng | `video.publish` | Đăng luôn kèm caption và nhãn "nội dung AI". **Ứng dụng chưa được TikTok duyệt chỉ đăng ở chế độ riêng tư** cho tới khi gửi duyệt (Submit for review) và được chấp nhận. |

**Cách đăng:** trong trang job, thẻ **📝 Nội dung đăng → TikTok**, bấm **📤 Đăng lên TikTok**. App dùng bản TikTok 9:16 nếu có, caption đã viết, rồi tải lên theo từng phần 10 MB. Tiến trình và kết quả ghi trong nhật ký job.

Giới hạn của TikTok: mã truy cập sống 2 giờ (app tự gia hạn), phải đăng nhập lại sau 180 ngày, tối đa 6 yêu cầu mỗi phút.

## 🤖 Tự chạy ban đêm (Autopilot)

Tab **🤖 Tự chạy**. Mặc định **tắt**. Bật lên thì trong khung giờ đã chọn (mặc định 1h–7h), mỗi phút app kiểm tra một lần rồi:
1. Lấy video từ **Kho video** (đã tải nhưng chưa xử lý), rồi từ **Trending** theo bộ lọc: ít nhất N like, đăng trong N ngày, dài từ… đến…, chủ đề. Video nhiều like được ưu tiên.
2. Bỏ qua video đã có job, kể cả khi cùng video nhưng khác link (so cả tiêu đề), và không bao giờ chọn lại video đã từng chọn.
3. Đưa vào hàng đợi, tối đa N video mỗi ngày, với kiểu đã chọn (mặc định **✨ Tự nhận diện từng video**). Sáng ra video đã nằm trong `final/`.

- **👀 Xem trước:** cho thấy nó sẽ chọn video nào nếu chạy bây giờ, không chạy gì.
- **▶ Chạy ngay:** chạy luôn, trong hạn mức hôm nay.
- **Làm mới danh sách Trending mỗi đêm** (tuỳ chọn): chỉ lấy danh sách như nút 🔄 ở tab Trending, không vượt captcha hay đăng nhập.
- Trạng thái và nhật ký nằm trong `data/autopilot.json`.

## Chạy lại nhanh (checkpoint)

Mỗi bước nhớ "dấu vân tay" của đầu vào: file vào, các cài đặt nó dùng, và phiên bản code của nó.
- Chạy lại mà không đổi gì thì bước đó dùng lại kết quả ngay (hiện ♻), không tốn thời gian hay lượt AI.
- **Bước bạn chọn "Chạy lại từ" luôn làm lại.** Các bước sau tự kiểm tra và chỉ làm lại phần bị ảnh hưởng.
- Bản dịch của AI lưu riêng ở `translate.json`. Chỗ bạn sửa tay trong bảng vẫn được giữ.
- Chữ trên màn hình được nhớ theo từng đoạn, câu rút gọn theo từng câu.
- Đo trên video 110 giây: lần đầu 445 s, chạy lại không đổi gì 0,2 s, chạy lại "Tạo giọng đọc" 14 s (render được dùng lại).
- Preview chỉ được render khi bật "Dừng sau preview, chờ duyệt". Nếu không, app render thẳng bản cuối.
- Video 4K: tải xong app tạo bản làm việc 1080p (`source.mp4`, bản gốc giữ ở `source_orig.mp4`). Mọi bước sau đều chạy trên bản này.
- OCR toàn khung được lưu theo mốc thời gian (`ocr_cache.json`). Bước watermark và bước chữ trên màn hình dùng chung, nên một khung không bị đọc hai lần.
- Phong cách "✨ Tự gợi ý" được AI chọn ngay trong lượt dịch đầu tiên, không mất thêm lượt gọi. Cuối nhật ký job có dòng "AI: N lượt gọi".

## Phong cách dịch

Chọn ở form bên trái, mục **Phong cách dịch**:

| Phong cách | Dùng cho |
|---|---|
| ✨ **Tự gợi ý theo từng video** (mặc định) | AI đọc tiêu đề + ~40 câu đầu rồi chọn một phong cách bên dưới, kèm 1 câu ghi chú riêng (vd biệt danh vui cho nhân vật). Khi "Ưu tiên bựa": video giải trí → Bựa |
| 😜 Bựa, hài lầy | Giọng cà khịa, từ lóng Gen Z có chừng mực, khoảng nửa số câu có "muối"; không chửi thề, không xúc phạm, không bịa sự kiện |
| 💬 Tự nhiên · 🔥 Kịch tính · 📖 Kể chuyện · 🐱 Dễ thương · 📰 Nghiêm túc | Như tên |
| ✏ Tự viết | Mô tả giọng văn bạn muốn |

Video nghiêm túc/nhạy cảm (tai nạn, thiên tai, chiến tranh, bệnh tật, tin buồn) không bao giờ được tự chọn kiểu bựa.
Phong cách áp dụng cho lời nói, chữ trên màn hình và cả tiêu đề/caption đăng bài. Phong cách đã chọn hiện ở đầu bảng **Bản dịch**; đổi ở đó rồi bấm **🔁 Dịch lại theo phong cách này** để dịch lại, tạo lại giọng đọc và render.

## Kiểm tra chuẩn TikTok trước khi đăng

Bước cuối của mỗi job (bật/tắt ở Tuỳ chọn nâng cao). Kết quả hiện ở thẻ **✅ Kiểm tra chuẩn TikTok**, gồm điểm 0–100 và từng mục ✅ / ⚠️ / ❌ kèm cách sửa:

| Nhóm | Kiểm tra |
|---|---|
| Tệp | MP4, H.264/HEVC + AAC, yuv420p, dung lượng (app điện thoại ~287 MB, web 4 GB), bitrate |
| Hình | 1080×1920 (dưới 720p = chất lượng thấp), tỉ lệ 9:16, 24–60 fps |
| Thời lượng | 3 giây – 10 phút; trên 1 phút đủ độ dài cho Creator Rewards |
| Âm thanh | Có tiếng, độ to ≈ −14 LUFS, đỉnh ≤ −1 dBTP, không im lặng quá nửa video |
| Chất lượng | Không mở đầu bằng màn hình đen, hình không đứng yên quá 40% |
| Đề xuất (For You) | OCR video kết quả: **không còn watermark nền tảng khác** (抖音, TikTok @…, 快手…), không sót chữ Trung, chữ không nằm dưới nút/caption của TikTok, nội dung đã biến đổi (lồng tiếng/thuyết minh; chỉ thêm sub thì bị cảnh báo) |
| Caption | Có caption TikTok, 3–6 hashtag, không còn chữ Trung, ≤ 4000 ký tự |

Nếu có lỗi tự sửa được (video ngang/vuông, độ phân giải, âm lượng, dung lượng, bitrate, mã hoá), app tạo thêm **📱 Bản TikTok** `output_tiktok.mp4`: 1080×1920, nền mờ thay viền đen, −14 LUFS, < 287 MB, rồi kiểm tra lại bản đó. Bản này cũng được chép vào `final/` với đuôi “(TikTok)”.
Watermark còn sót, chữ Trung, chữ bị che, nội dung chưa biến đổi thì app không tự sửa: làm theo dòng “→” ở mục đó.
Tiêu chuẩn lấy theo khuyến nghị đăng video và tiêu chuẩn For You của TikTok; TikTok có thể đổi bất cứ lúc nào, và đạt hết vẫn không bảo đảm được đề xuất.

## Ba kiểu kết quả

1. **Lồng tiếng + Sub Việt**: giọng Việt, giữ nhạc nền gốc (đã tách bỏ giọng gốc), phụ đề Việt.
2. **Chỉ Sub Việt**: giữ nguyên audio gốc, chỉ thêm phụ đề Việt. Bỏ qua bước tách nhạc và tạo giọng, nên nhanh hơn.
3. **Chỉ dịch chữ trên màn hình**: giữ nguyên video và **âm thanh gốc** (chép nguyên luồng, không mã hoá lại), không nhận dạng giọng, không phụ đề lời nói; chỉ dịch tiêu đề, chú thích, nhãn in trên hình và vẽ lại đúng chỗ.

Làm xong một kiểu, chọn kiểu khác ở ô "Làm thêm bản này": app tự tính bước cần chạy lại và dùng lại bản ghi lời, bản dịch, chữ màn hình đã có.

## Dịch bằng gì? (tự chuyển provider khi lỗi)

| Provider | Cần gì |
|---|---|
| **Claude (gói Pro/Max)** | Claude Code CLI + ⚙ Cài đặt → 🔑 Đăng nhập Claude |
| **ChatGPT (gói Plus/Pro)** | Codex CLI (`npm i -g @openai/codex`) + ⚙ Cài đặt → Đăng nhập ChatGPT |
| **Gemini (tài khoản Google)** | Gemini CLI (`npm i -g @google/gemini-cli`) + ⚙ Cài đặt → Đăng nhập Gemini (chọn “Login with Google”) |
| **Claude API** | Anthropic API key |
| **OpenAI / tương thích OpenAI** | Base URL + model (+ API key). Dùng cho OpenAI, OpenRouter, hoặc server local như LM Studio/vLLM |
| **Google Gemini** | API key từ aistudio.google.com |
| **Ollama** (local, GPU) | Ollama đang chạy + `ollama pull qwen2.5:7b` |
| **Google Dịch miễn phí** | Không cần gì; chất lượng kém và hay bị chặn |
| **Dịch offline tích hợp** | NLLB-200 chạy GPU, không cần mạng; bấm “⬇ Tải model” một lần (0,6–1,4 GB). Là bước lùi cuối khi mọi dịch vụ online đều lỗi |

Gói ChatGPT/Gemini trên web **không** kèm API key, vì API tính phí riêng.

Cách hoạt động khi lỗi (⚙ Cài đặt → "Dịch: provider & thứ tự dự phòng"):
- Provider chọn ở form được thử trước, rồi lần lượt các provider đã tick. Provider chưa cấu hình được bỏ qua; Ollama hoặc server local được **kiểm tra có đang chạy không** trước khi dùng.
- Lỗi tạm thời (giới hạn tốc độ, hết thời gian chờ, 5xx, trả sai JSON): thử lại có giới hạn, chờ tăng dần.
- Hết hạn mức, sai key, chưa đăng nhập, không kết nối được: chuyển provider ngay và tạm bỏ qua provider đó một thời gian.
- Không bao giờ lặp vô hạn. Nếu không còn provider nào dùng được, app báo rõ lý do của từng provider.
- Bản dịch được lưu theo từng khối: chạy lại sau khi lỗi sẽ không dịch lại (và không trả tiền lại) phần đã xong.
- API key không bao giờ xuất hiện trong nhật ký hay phản hồi gửi về trình duyệt.
- Nút **Thử** cạnh mỗi provider dịch thử một câu, để kiểm tra cấu hình.
- Mỗi provider có ô **giãn cách giữa 2 lần gọi** (giới hạn tốc độ). Trạng thái "tạm nghỉ" (hết hạn mức, sai key…) được lưu lại, nên khởi động lại server cũng không gọi lại provider đang hết hạn mức.

## Giọng đọc

- **Edge TTS** (miễn phí), **ElevenLabs** (trả phí), **VieNeu** (local GPU, giọng Việt tự nhiên, miễn phí — xem mục dưới), **Piper** (local, offline, chạy được không cần mạng).
- Mỗi dịch vụ có 1 giọng nam và 1 giọng nữ. Chế độ "Tự nhận diện" tách người nói bằng SpeechBrain ECAPA (GPU), đoán giới tính theo cao độ, rồi đọc mỗi câu bằng giọng tương ứng. Sửa tay được ở cột Nam/Nữ trong bảng bản dịch.
- Khi Edge/ElevenLabs lỗi, câu đó tự được tạo bằng Piper để video vẫn ra; chạy lại bước "Tạo giọng đọc" sẽ thử lại dịch vụ online.
- Key ElevenLabs cần quyền **Text to Speech**; thêm **Voices: Read** nếu muốn thấy thư viện giọng của bạn. Không có quyền Voices thì app dùng danh sách giọng mặc định của ElevenLabs.

## 🎬 Biên kịch (AI): biến reup thành video có nội dung riêng

TikTok hạ đề xuất video "reup trơn". Bước **Biên kịch** cho AI xem phim rồi viết thêm một lớp nội dung:
- **Hook 3 giây đầu:** chữ to giữa màn hình, kèm một câu giọng kể nói ngay chuyện gì xảy ra và vì sao đau.
- **Thanh tiêu đề** cố định suốt video, đặt ở dải trên của bản 9:16.
- **Giọng kể** lấp các khoảng lặng: một giọng cố định cho cả kênh (VieNeu "Thiền Tâm Đức"); tiếng gốc tự nhỏ lại trong lúc kể.
- **Cắt đoạn lặng dài.** Chỉ cắt trong khoảng không có thoại, không bao giờ cắt ngang câu nói.
- **Đọc thành lời cú twist** đang nằm trong chữ nhỏ trên màn hình, kèm phụ đề lớn từng câu.
- **Chữ giữ chân** ở góc trên, ví dụ "Đoạn cuối mới thật sự đau…".
- **Cái kết:** câu chốt trên nền tối, rồi một câu nối về cảnh mở để người xem dễ xem lại lần hai.
- **Nội dung đăng bài:** caption có từ khoá và kết bằng câu hỏi gây tranh cãi, hashtag đúng chủ đề (không #fyp), bình luận ghim, nhắc bật nhãn "Nội dung do AI tạo", ghi nguồn. Kèm ảnh bìa `cover.jpg` và 2 clip mồi `teaser_1.mp4`, `teaser_2.mp4`.

**Bật:**
- Cho từng video: form → tab **📱 TikTok** → ô **🎬 Biên kịch**.
- Mặc định cho mọi video mới: **⚙ Cài đặt → Biên kịch (AI)**.
- **Tự chạy** dùng chung công tắc này nhưng không dừng chờ duyệt; nhật ký ghi rõ "kịch bản chưa được duyệt".
- Bước Biên kịch cần **🔤 Dịch chữ trên màn hình** để thấy được chữ nhỏ (nơi thường giấu twist).

**Duyệt và sửa:** khi ô "Dừng chờ tôi duyệt" đang bật, job dừng sau khi viết xong kịch bản. Mở trang job → thẻ **🎬 Biên kịch**:
- Đổi loại video, ngôi kể, tone, rồi bấm **✍ AI viết lại**. Lần viết lại không gọi lại bước AI xem phim.
- Sửa hook, thanh tiêu đề, từng câu kể (giờ bắt đầu, nội dung, ▶ nghe thử), bật/tắt từng chỗ cắt, chữ giữ chân, phần đọc khối chữ, cái kết, câu loop, ảnh bìa, caption.
- Bấm 🖼 cạnh một mốc để xem khung hình tại đó.
- **✅ Duyệt & render** để render. **💾 Lưu & render lại** khi đã duyệt: chỉ đọc lại câu kể vừa đổi rồi render, không gọi AI.

**Loại video:** app tự đoán loại video, AI xác nhận, và bạn đổi được.

| Loại | Mức can thiệp |
|---|---|
| Phim nhiều khoảng lặng (`story_sparse`) | Đầy đủ |
| Phim thoại dày (`story_dense`) | Hook, thanh tiêu đề, cắt nhẹ, cái kết; không có giọng kể đè thoại |
| Video đã có người thuyết minh (`narrated`) | Không thêm giọng kể thứ hai |
| Clip ngắn (`short_clip`) | Chỉ hook chữ, caption và bình luận ghim |

**Thứ tự khi bật Biên kịch:**
- AI xem phim ngay sau khi tải, song song với tách nhạc và nhận dạng giọng.
- Bước Biên kịch chạy sau nhận dạng giọng nói và **trước khi dịch**: AI đọc thẳng thoại và chữ trên màn hình tiếng Trung để viết kịch bản.
- Bước dịch theo đúng giọng văn và cách xưng hô của kịch bản, để thoại và lời kể nghe như cùng một người viết.
- Khi job dừng chờ duyệt, bấm Duyệt thì app chạy tiếp từ bước Dịch.

**Kiểm tra chất lượng tự động:** app tự kiểm tra kịch bản AI viết theo các luật sau:
- Xưng hô thống nhất suốt kịch bản.
- Lời kể khớp cảnh đang chiếu: cảnh hiện tại không nói "ngày ấy", cảnh hồi ức không nói "giờ".
- Không có câu giải thích cảm xúc trừu tượng.
- Câu loop có cảm xúc, không chỉ tả lại hình.
- Hook phải úp mở, không chỉ tóm tắt. Chữ hook tối đa 10 chữ. AI viết 3 phương án hook; chọn trong thẻ Biên kịch (mặc định phương án 1).
- Lời kể không đọc lại chữ đang hiện trên màn hình (ngày tháng, tin nhắn).
- Không có 2 câu kể mở đầu cùng một cụm từ.
- Tối đa khoảng 2 câu kể mỗi phút; câu chỉ thuật lại hình bị loại.
- Thanh tiêu đề hiện suốt video nên không được lộ twist.

Nếu vi phạm, app gửi lại cho AI kèm danh sách lỗi để viết lại, tối đa 2 lượt, và giữ bản ít lỗi nhất. Lỗi còn sót hiện ở đầu thẻ Biên kịch.

**Cắt và lời kể không chạm vào thoại:** lúc render, app đo năng lượng tiếng thật của giọng gốc (`vocals.wav`) và giọng lồng tiếng Việt (`voice.wav`), không chỉ dựa vào mốc nhận dạng giọng nói.
- Mép mỗi chỗ cắt cách mọi tiếng nói ít nhất 0,5 giây: giọng gốc, giọng lồng và lời kể. Chỗ cắt quá sát thoại bị thu hẹp hoặc bỏ.
- Chỗ cắt được dời vào đúng ranh giới chuyển cảnh khi có. Nếu hai bên chỗ cắt vẫn cùng một cảnh, app chuyển mờ hình và tiếng khoảng 0,13 giây. Độ dài video không đổi.
- Mỗi câu kể được đặt vào chỗ không có thoại: đúng mốc AI chọn, hoặc dời tối đa 3 giây. Không vừa chỗ nào thì câu đó bị bỏ (có ghi trong log).
- Cảnh mở 0–3 giây có thoại thì hook chỉ là chữ, không có giọng kể.
- Kết quả từng chỗ cắt được ghi vào `content_cuts.json`, vị trí từng câu kể vào `content_reads.json`.

**AI dùng:**
- Mặc định là ChatGPT qua Codex CLI cho cả việc xem ảnh lẫn viết kịch bản. Hết lượt thì tự chuyển sang Claude CLI, rồi Ollama (`qwen2.5vl:7b` để xem, `qwen2.5:7b` để viết). Không dùng Gemini.
- Mỗi video tốn 2–4 lần gọi AI: một lần xem phim (khoảng 1 phút), một lần viết kịch bản (khoảng 1 phút), cộng tối đa 2 lượt viết lại nếu kịch bản chưa đạt.
- Tắt **🎬 Biên kịch** thì app chạy y như trước.

## 🗣 VieNeu-TTS — giọng Việt tự nhiên, chạy local trên GPU

[VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS) (giấy phép Apache-2.0) có 25 giọng Việt: nam, nữ, ba miền.
- Chạy ngay trên RTX 3060, không cần mạng sau lần tải model đầu, miễn phí.
- Nhanh khoảng 50 lần thời gian thực khi đọc theo lô.
- Dùng khoảng 1–2 GB VRAM.

### Cài một lần (đã cài sẵn ở `F:\VieNeu-TTS`)

VieNeu cần đúng bản torch 2.8 và transformers 4.57.6. Vì vậy nó được cài vào **môi trường Python riêng**, không đụng tới Python của app (cài chung sẽ làm hỏng Whisper).

Mở PowerShell rồi chạy:

```bash
cd D:\Claude\video-dubber
```

```bash
powershell -ExecutionPolicy Bypass -File install_vieneu.ps1
```

- Tải khoảng 4 GB, mất vài phút. Mọi thứ nằm trong `F:\VieNeu-TTS`:
  - `env\`: môi trường Python.
  - `hf-cache\`: model, tải tự động ở lần đọc đầu tiên.
  - `pip-cache\`: file cài đặt tải về.
- Muốn cài ổ khác thì thêm `-Dir E:\VieNeu-TTS`, rồi sửa đường dẫn trong app: **⚙ Cài đặt → VieNeu-TTS**.
- Cuối script in ra `torch 2.8.0+cu128 GPU: True` là xong.

### Bật / chạy

**Không cần bật riêng.** VieNeu chạy nền tự động:

1. Mở app như bình thường (`run.bat`).
2. Trong form chọn **Giọng đọc → VieNeu – giọng Việt tự nhiên**, chọn giọng nam và giọng nữ, rồi bấm **▶ Nam / ▶ Nữ** để nghe thử.
3. Lần đầu nghe thử hoặc lần đầu chạy job, app khởi động VieNeu: nạp model lên GPU, mất khoảng 20–60 giây (lần đầu tiên còn phải tải model khoảng 1 GB). Từ lần sau nhanh ngay.
4. VieNeu ở lại trên GPU để các job sau khỏi phải nạp lại. Sau **10 phút** không dùng, nó tự tắt và trả VRAM.
5. Đóng `run.bat` (hoặc bấm 🔄 Cập nhật) thì VieNeu cũng tự tắt theo.

Khi VieNeu lỗi, những câu bị lỗi tự chuyển sang giọng Piper (nếu đang tick "tự dùng giọng local"), nên video vẫn ra được. Chạy lại bước "Tạo giọng đọc" sẽ thử lại VieNeu.

### Kiểm tra nhanh nếu có lỗi

Câu bị lỗi trong nhật ký job có ghi lý do. Chạy thử VieNeu ngoài app:

```bash
F:\VieNeu-TTS\env\Scripts\python.exe -c "from vieneu import Vieneu; t = Vieneu(); t.save(t.infer('Xin chào các bạn!', voice='Hải Đăng'), 'F:/VieNeu-TTS/test.wav')"
```

Nghe file `F:\VieNeu-TTS\test.wav`. Nếu lệnh này chạy được mà app vẫn lỗi, kiểm tra đường dẫn trong **⚙ Cài đặt → VieNeu-TTS**.

## Tính năng khác

- Nghe thử giọng trước khi chạy, chỉnh tốc độ và cao độ (Edge) hoặc stability/style (ElevenLabs).
- Sửa bản dịch từng câu ngay trên web, bấm "Lưu & render lại" để chỉ làm lại giọng và render.
- "Dừng sau preview, chờ duyệt" để kiểm tra 30 giây đầu trước khi render hết.
- Chạy lại từ bất kỳ bước nào, huỷ job, lịch sử job.
- Tải file `.srt`, giọng đọc `.wav`, nhạc nền `.wav` để dựng lại trong CapCut nếu muốn.

## Dịch chữ trên màn hình (tiêu đề, chú thích in trên hình)

Bật **🔤 Dịch chữ trên màn hình** ở form bên trái:

1. OCR một khung hình mỗi 0,5 giây (video dài hơn 2 phút thì mỗi 1 giây) bằng RapidOCR.
2. Gộp các dòng thành từng khối chữ (chú thích nhiều dòng tính là 1 khối) và theo dõi theo thời gian: lúc xuất hiện, lúc biến mất, vị trí, cỡ chữ, màu chữ và màu viền.
3. Bỏ chữ quá nhỏ (logo, bảng quảng cáo). Nếu chữ chủ đạo trong video là chữ Trung thì bỏ chữ Latin.
4. Dịch tất cả trong 1 lần gọi Claude (tự sửa lỗi OCR, Việt hoá tên riêng, giữ số và tỉ số).
5. Lúc render, xoá chữ gốc **chỉ trong khoảng thời gian chữ đó hiện**, rồi vẽ chữ Việt cùng vị trí, cùng màu chữ và viền, tự chọn cỡ và ngắt dòng cho vừa. Có 3 cách xoá (Tuỳ chọn nâng cao → "Xoá chữ trên màn hình"):
   - **Xoá nét chữ, vẽ lại nền** (mặc định): mặt nạ lấy đúng nét chữ theo màu chữ và màu viền (so theo sắc độ), inpaint bằng OpenCV từng khung hình, hoà viền mềm. Không lộ khung.
   - **Làm mờ viền mềm**: làm mờ có viền chuyển dần, không có cạnh cứng.
   - **AI LaMa trên GPU**: vẽ lại nền bằng mô hình big-lama (tự tải 196 MB lần đầu, kiểm tra MD5). Tái tạo được người, vật, bảng quảng cáo phía sau chữ; màu nền được ghép từ viền vùng nên không lộ khung. Chậm hơn OpenCV ~2–4x.
   - **Hộp tối**.

OCR chạy trên GPU (onnxruntime-gpu bản CUDA 12, dùng chung DLL CUDA/cuDNN của PyTorch): khoảng 180 ms/khung, so với 570 ms trên CPU. Nếu GPU không dùng được, app tự lùi về CPU; nhật ký ghi rõ "OCR trên GPU/CPU".

- **Chữ chuyển động** (chữ chạy, bay vào…): app theo dõi đường đi của chữ; chữ Việt chạy theo cùng đường đi, và vùng xoá chữ gốc bám theo vị trí ở từng khung.
- **Font**: mặc định tự khớp độ đậm của chữ gốc (Arial Black / Arial đậm / Arial thường), hoặc chọn font trong Tuỳ chọn nâng cao.
- **Nhãn chữ nhỏ**: chỉnh "Bỏ qua chữ nhỏ hơn (% chiều cao)" xuống thấp (vd 1%) để dịch cả nhãn nhỏ.

Sửa bản dịch hoặc bỏ dịch từng đoạn ở bảng "Chữ trên màn hình" trong trang job, rồi bấm "Lưu & render lại".
Với video mà lời nói không quan trọng (chỉ có nhạc hoặc lời bình nền), bỏ tick "Gắn phụ đề Việt của lời nói".

## 📋 Đánh giá & sửa (trong mỗi job)

Video xong thì thẻ **📋 Đánh giá & sửa** hiện ngay dưới khung video. Mọi thứ bạn ghi đều được lưu ở 2 nơi: `review.json` trong thư mục job, và nhật ký chung `data/feedback/log.jsonl`.

- **Tóm tắt & chấm điểm.** Phần tóm tắt cho biết từng bước đã làm gì và các cảnh báo trong nhật ký cần soi kỹ. Bạn chấm 1–5 sao cho tổng thể, bản dịch, giọng, phụ đề, hình và nhạc, kèm nhận xét chung.
- **Ghi chú cần sửa.** Có thể gắn mốc thời gian đang xem (⏱). Có 3 loại:
  - **✍ Kịch bản:** AI viết lại đúng các câu được nhắc tới (cả lời kể, hook, câu loop của Biên kịch). App hiện bản cũ và bản mới để bạn tick chọn rồi bấm áp dụng; sau đó chỉ tạo lại giọng và render.
  - **🛠 Kỹ thuật** (dịch chữ màn hình, che mờ, UI, pipeline…): bấm **Duyệt → gửi Claude** để đưa lỗi vào `data/feedback/queue.json` (và `QUEUE.md`). Mỗi lỗi kèm khung hình tại mốc, cảnh báo và chi tiết từng bước. Sau đó bảo Claude Code "sửa các lỗi trong hàng chờ" (cách làm ghi trong `CLAUDE.md`). Khi Claude ghi "đã sửa", app hiện lại trạng thái đó trong ghi chú.
  - **📝 Khác:** chỉ lưu.
- **🎵 Nhạc nền.** AI gợi ý nhạc hợp video: cảm xúc, thể loại, BPM, nhạc cụ, cấu trúc theo từng đoạn, đúng thời lượng video. Kèm **prompt tiếng Anh/Việt để copy** sang Suno, Udio… Tải nhạc về rồi chọn file trong thẻ này, chỉnh âm lượng, mức giữ tiếng nền gốc và giây bắt đầu. App lặp hoặc cắt nhạc cho vừa video, cân âm lượng, fade cuối, thay nhạc nền gốc rồi render lại. Bấm "↩ Về nhạc gốc" để quay lại nhạc ban đầu.

## 🚀 Tab Link hot

Tab này tìm link video đáng làm. Mặc định là **top 10 theo lượt xem**, mỗi video có nút ▶ Xem trước (trình phát chính thức của TikTok), 📋 Copy link và 🎙 Lồng tiếng (điền link vào tab Xử lý video).

- **🏆 Top theo thể loại.** Nguồn TikTok Việt Nam là trang Explore với 21 thể loại (Công nghệ, Ẩm thực…), không cần đăng nhập. Nguồn Douyin là các chủ đề của tab Trending. Douyin không công khai lượt xem nên xếp theo lượt thích. Có thể sắp xếp theo lượt xem, **tăng nhanh** (lượt xem mỗi giờ kể từ lúc đăng: bắt video đang lên), lượt thích hoặc mới đăng.
- **📈 Insights trending.** Gồm Douyin 热榜, các video TikTok đang tăng nhanh nhất và các từ khoá TikTok gợi ý gần đây. TikTok Creative Center (bảng xếp hạng chính thức tại Việt Nam) không cho app đọc tự động, nên chỉ có nút mở trang này trong trình duyệt của bạn.
- **🔎 Từ khoá.** Lọc ngay trong các video đã lấy. Nút "Tìm trên TikTok" cần **đăng nhập TikTok** (⚙ Cài đặt → TikTok Việt Nam, nên quét QR): khi chưa đăng nhập TikTok chặn tìm kiếm, nhưng vẫn trả về các từ khoá gợi ý để bấm nhanh.

App chỉ đọc những gì trang tải cho người xem bình thường. App không vượt đăng nhập, captcha hay chặn bot.

## Tab Trending

- **Cây chủ đề** bên trái: Thể thao (Bóng đá, Quần vợt, Bóng rổ, Bóng bàn, Đua xe), Lịch sử (thế giới, Trung Quốc, Việt Nam), Kinh tế & Tài chính (Giá vàng, Chứng khoán, Tin kinh tế), Phim AI, Phim tài liệu, Công nghệ, Ẩm thực, Du lịch, Phim & giải trí. Mỗi mục có số video.
- **🔄 Lấy video cho chủ đề này**: lấy từ các nguồn khai báo cho mục đó (vd Thể thao → tab 体育 của Douyin), rồi tự xếp video vào mục con theo từ khoá (vd Bóng đá: 足球, 欧冠, 英超…).
- **Lấy từ nguồn này**: chọn một nguồn cụ thể:
  - Douyin: 热榜, 精选, 16 tab chủ đề (không cần đăng nhập), hoặc tìm theo từ khoá (Douyin bắt captcha khi chưa đăng nhập).
  - Facebook: Reels của một Page (nhập URL).
- **✏ Sửa danh mục**: thêm hoặc sửa chủ đề, nguồn, từ khoá (`data/categories.json`). Video được phân loại lại ngay, không cần crawl lại.
- Mỗi nguồn chạy riêng: một nguồn lỗi không làm hỏng các nguồn khác, lỗi hiện dưới thanh trạng thái.
- **➕ Tải thêm**: lấy sâu hơn lần trước từ đúng nguồn đó (app nhớ mỗi nguồn đã cuộn tới trang nào). **■ Dừng**: dừng sau trang đang cuộn và vẫn lưu những video đã lấy được.
- Crawl **chỉ lấy danh sách** và ảnh bìa (lưu trong `data/trending.json`, `data/trending_covers/`), không tải hay xử lý video nào. Video trùng được gộp lại.

### Facebook Reels
- Reels công khai của Page xem, xem nhanh và tải được khi chưa đăng nhập (dùng yt-dlp).
- Không có danh sách "trending" công khai trên Facebook, nên bạn khai báo các Page muốn theo dõi: `"facebook": {"feeds": ["page:https://www.facebook.com/<trang>"]}` trong danh mục, hoặc nhập URL khi lấy.
- Đăng nhập (tuỳ chọn) trong ⚙ Cài đặt → Đăng nhập Facebook: bạn tự đăng nhập trong cửa sổ riêng của app; app chỉ dùng phiên đó để xem những gì tài khoản của bạn được xem. App không vượt qua giới hạn của Facebook; reel riêng tư hoặc bị giới hạn sẽ báo lỗi rõ ràng.
- Bạn lọc, sắp xếp, tick chọn video, chọn kiểu (lồng tiếng / chỉ Sub), rồi bấm **▶ Chạy**. Các video vào hàng đợi, chạy lần lượt trên GPU, dùng giọng và cách dịch đang chọn ở tab "Xử lý video".
- Giữa 2 lần tải Douyin có giãn cách tối thiểu `douyin_gap_s` (mặc định 30 giây) để tránh bị chặn.

## Khi Douyin chặn

⚙ Cài đặt → **Đăng nhập Douyin**: mở một cửa sổ trình duyệt riêng của app (profile lưu ở `data/douyin_profile`). Bạn quét QR đăng nhập hoặc giải captcha trong đó rồi đóng cửa sổ. App dùng lại phiên này cho các lần tải sau. Nếu lúc đang tải mà Douyin đòi xác minh, app tự mở cửa sổ đó và chờ bạn tối đa 3 phút.

(Cách cũ vẫn dùng được: khai báo file `cookies.txt` trong Cài đặt.)

Dữ liệu job nằm trong `data/jobs/<id>/`.
