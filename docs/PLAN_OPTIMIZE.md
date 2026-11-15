# Plan tối ưu pipeline và UI

Ngày lập: 2026-11-15. Số liệu lấy từ 69 job trong `data/jobs`, trong đó 45 job có đo giờ. Video trung vị dài 232 giây, video dài nhất 38 phút. Chế độ: 40 lồng tiếng, 16 chỉ dịch chữ, 7 chỉ sub, 5 thuyết minh.

## 0. Tiến độ và đính chính (cập nhật 2026-11-15)

**Đính chính sau khi đọc kỹ code và đo lại theo đồng hồ (`python tools/bench.py`):**
- Thời gian thật của một job là **326 giây (1,73 giây xử lý / giây video)**, không phải 371 giây. Con số cũ cộng dồn các bước, kể cả các bước vốn đã chạy song song.
- **P1 đã có sẵn từ trước:** dò phụ đề gốc + watermark đã chạy song song với dịch → người nói → tạo giọng. Câu "mọi bước chạy nối tiếp" ở mục 1 là sai.
- **P3 một phần đã có sẵn:** OCR đã được cấu hình chạy GPU, và bước theo dõi phụ đề đã bỏ qua khung trùng/khung trống. Tuy vậy đo thực tế mỗi lần OCR một dải 960×126 vẫn mất **~270 ms**, chậm như chạy CPU; tắt bộ phân loại góc (`use_cls=False`) chỉ còn ~247 ms và cho kết quả y hệt. Đây là điểm nghẽn thật của bước 132 giây, cần điều tra tiếp (ONNX có thực sự dùng CUDA khi torch đã nạp không).

**Đã làm:**
- P9: `tools/bench.py` (đo theo đồng hồ, ghi `data/bench/`).
- P4: tiêu đề + kiểm tra TikTok + xuất file chạy nền; job báo "Xong" ngay khi render xong (phần đuôi này trung vị 64 giây ≈ 20 % thời gian một job). Chạy lại job sẽ tự dừng phần đuôi trước.
- Ghi `job.json` được giãn ra (trước đây ghi lại cả file cho mỗi dòng log / mỗi nhịp tiến độ).
- U5: mở job còn 2 request (trước ~12), tab nào mở mới tải; ngừng polling khi tab trình duyệt ẩn; lưới video chỉ vẽ phần đang thấy.
- U8: một cách hiện số kiểu Việt cho cả app.
- U1: trang job chia tab Kết quả / Sửa / Đánh giá / Đăng / Nhật ký.
- U2: gộp Trending + Link hot thành "🔎 Tìm video"; lưới Top có chọn nhiều để chạy hàng loạt.
- U7: thanh điều hướng dưới đáy trên điện thoại, không cuộn ngang ở 375 px.

**Chưa làm:** P3 (tăng tốc OCR), P6, P5, P2, P7, P8, U3, U4, U6. Chưa đo lại bằng job mới sau P4.

## 1. Hiện trạng

**Một job hiện mất trung vị 371 giây, tức 2,34 giây xử lý cho mỗi giây video.** Theo tỉ lệ đó, video 38 phút mất khoảng 90 phút.

| Bước | Trung vị | p90 | % tổng thời gian | Giây xử lý / giây video |
|---|---|---|---|---|
| Render video cuối | 78 s | 203 s | 18,4 % | 0,33 |
| Dò phụ đề gốc (OCR dải phụ đề) | 133 s | 258 s | 16,4 % | **0,59** |
| Kiểm tra chuẩn TikTok | 54 s | 138 s | 11,6 % | 0,29 |
| Tạo giọng đọc | 71 s | 180 s | 9,9 % | 0,32 |
| Tách nhạc nền (GPU) | 62 s | 160 s | 8,9 % | 0,29 |
| Dịch & chia câu (AI) | 49 s | 138 s | 7,8 % | 0,31 |
| Tìm watermark | 43 s | 103 s | 7,5 % | 0,17 |
| Dịch chữ màn hình | 44 s | 84 s | 4,4 % | 0,18 |
| Viết tiêu đề & hashtag (AI) | 22 s | 35 s | 3,4 % | 0,08 |
| Còn lại (tải, nhận diện, nhận dạng giọng, người nói…) | | | ~11 % | |

**Nhận xét:**

1. **Mọi bước chạy nối tiếp nhau, và mỗi lúc chỉ một job.** `jobs.Manager` có đúng một worker. Các bước gọi AI qua mạng (dịch, viết tiêu đề, Biên kịch) không dùng GPU, nhưng vẫn bắt GPU ngồi chờ, và ngược lại.
2. **Hai bước OCR là nặng nhất nếu tính theo độ dài video.** Dò phụ đề gốc tốn 0,59 s/giây video, tìm watermark 0,17. Cả hai giải mã lại cùng một video rồi OCR riêng, không dùng chung khung hình.
3. **Kiểm tra TikTok chiếm 12 % thời gian nhưng không tạo ra video.** Bản TikTok đã được render cùng lượt render chính (`output_tiktok.pre.json`). Bước kiểm tra chỉ giải mã lại cả file để đo âm lượng (ebur128) và OCR lại các khung. Vậy mà job vẫn phải chờ nó xong mới báo "Xong".
4. **Khi render lại vì đổi nhỏ** (sửa một câu, đổi nhạc), app vẫn encode lại toàn bộ video.
5. **Lỗi tải:** cả 3/3 job lỗi tải đều là tải Douyin.

## 2. Mục tiêu

| Chỉ số | Hiện tại | Mục tiêu |
|---|---|---|
| Thời gian từ bấm Bắt đầu tới có video (video ~4 phút) | ~6 phút | ≤ 3,5 phút |
| Giây xử lý / giây video | 2,34 | ≤ 1,3 |
| Sửa 1 câu → có video mới | ~2–3 phút (tts + render toàn bộ) | ≤ 60 giây |
| 5 video trong hàng đợi | 5 × 6 phút nối tiếp | ≤ 18 phút (gối đầu nhau) |
| Mở 1 job trên iPhone → thấy video | ~12 request, trang rất dài | ≤ 4 request, video ở ngay đầu |

## 3. Pipeline

Mỗi việc ghi kèm: tác động, công sức, rủi ro, cách đo.

### P1. Chạy song song các bước không phụ thuộc nhau trong một job
- **Làm gì:** sau bước nhận dạng giọng nói, chia thành 2 nhánh chạy cùng lúc:
  - Nhánh mạng: Biên kịch → dịch → (đã có) shorten.
  - Nhánh máy: dò phụ đề gốc → watermark → OCR chữ màn hình.

  Hai nhánh gặp nhau ở bước người nói / tạo giọng. `pipeline.run` hiện là một hàm tuần tự dài 824 dòng, nên phải tách mỗi bước thành hàm có khai báo đầu vào/đầu ra, rồi chạy bằng một `ThreadPoolExecutor` nhỏ (2 luồng).
- **Tác động:** nhánh dịch (49 s) chạy chồng lên dò phụ đề + watermark (176 s), tiết kiệm khoảng 45–60 s mỗi job (~15 %).
- **Công sức:** trung bình. **Rủi ro:** `job.step()` và log phải an toàn khi nhiều luồng ghi (đã có `_lock`), checkpoint ghi file không được đè nhau.
- **Đo:** thời gian từ lúc nhận dạng xong tới lúc bắt đầu tạo giọng, trên 5 job mẫu.

### P2. Hàng đợi 2 làn: GPU và AI/mạng
- **Làm gì:** Manager có 2 worker, phân theo loại bước chứ không theo job. Bước GPU (tách nhạc, nhận dạng, OCR, render) đi làn GPU, tối đa 1 việc. Bước AI và mạng (tải, dịch, viết tiêu đề, kiểm tra TikTok) đi làn mạng, tối đa 2 việc. Nhờ vậy job B tải và dịch trong lúc job A render.
- **Tác động:** hàng đợi 5 video giảm khoảng 35–45 %.
- **Công sức:** lớn (cần P1 trước). **Rủi ro:** VRAM 12 GB. Làn GPU vẫn chỉ 1 việc nên không tăng VRAM; giữ nguyên các lệnh `unload()` hiện có.

### P3. Gộp dò phụ đề gốc + watermark + chữ màn hình thành một lượt đọc khung hình
- **Làm gì:**
  - Giải mã video một lần ở 4 khung/giây, ảnh xám thu nhỏ, giữ trong bộ nhớ đệm (hoặc file `frames.npy`).
  - Cả 3 bước OCR dùng chung bộ khung này.
  - So sánh khung liên tiếp (hash dải phụ đề): khung giống khung trước thì không OCR lại. `REREAD_SAME` đã có, giờ áp dụng chung cho cả 3 bước.
  - RapidOCR chạy trên GPU nếu có onnxruntime-gpu; hiện cần kiểm tra lại xem đang chạy CPU hay GPU.
- **Tác động:** 176–220 s xuống còn khoảng 80–100 s cho video 4 phút (~20 % tổng thời gian).
- **Công sức:** trung bình–lớn. **Rủi ro:** độ chính xác dò phụ đề. Cần bộ test so sánh sự kiện phụ đề cũ và mới trên 5 job có phụ đề cứng.

### P4. Kiểm tra TikTok chạy nền, không chặn chữ "Xong"
- **Làm gì:**
  - Job báo **Xong** ngay khi render xong; kiểm tra TikTok và viết tiêu đề/hashtag chạy sau như việc phụ, kết quả tự hiện vào thẻ.
  - Âm lượng: render đã áp `loudnorm` cho bản TikTok, nên đo ngay trong lượt render (`ebur128` cùng filter graph) thay vì giải mã lại cả file.
  - OCR kiểm tra: chỉ quét keyframe (đã làm cho video > 10 phút) cho mọi video, khoảng 1 khung/2 s.
- **Tác động:** người dùng thấy "Xong" sớm hơn khoảng 55–75 s (bỏ cả 54 s kiểm tra + 22 s viết tiêu đề khỏi đường găng).
- **Công sức:** nhỏ–trung bình. **Rủi ro:** thấp.

### P5. Render lại từng phần khi chỉ đổi giọng hoặc nhạc
- **Làm gì:**
  - Tách render làm 2 phần: hình (video đã che mờ, gắn phụ đề, watermark) và tiếng (mix giọng + nhạc).
  - Khi chỉ đổi lời một câu hoặc đổi nhạc nền mà phụ đề trên hình không đổi, chỉ mix lại tiếng rồi ghép với hình cũ (`-c:v copy`), mất vài giây.
  - Khi chữ phụ đề đổi, chỉ render lại các đoạn GOP chứa câu đó (cắt theo keyframe, nối bằng concat demuxer).
- **Tác động:** sửa 1 câu → video mới từ 2–3 phút xuống khoảng 20–40 s (gồm tạo lại giọng 1 câu). Rất quan trọng cho tab 📋 Đánh giá & sửa.
- **Công sức:** lớn. **Rủi ro:** lệch hình/tiếng ở chỗ nối. Bắt buộc có test đồng bộ A/V như `test_crossfade_join_keeps_length_and_sync`.

### P6. Tạo giọng: chỉ tạo lại câu đổi, và tạo song song với lúc dịch
- **Làm gì:**
  - Cache theo nội dung câu đã có; cần đảm bảo bước canh thời gian và mix không phải chạy lại toàn bộ khi chỉ 1 câu đổi (mix theo nhóm 150 câu, chỉ mix lại nhóm có câu đổi).
  - VieNeu: tăng `BATCH` theo VRAM còn trống.
  - Video dài: bắt đầu tạo giọng cho khối dịch đầu tiên trong lúc các khối sau còn đang dịch (stream theo khối 120 câu).
- **Tác động:** tạo giọng ~71 s → ~40 s; video dài lợi nhiều hơn.
- **Công sức:** trung bình.

### P7. Tách nhạc: dùng lại theo nội dung file, chọn model theo độ dài
- **Làm gì:**
  - Checkpoint theo hash `audio.wav`, dùng chung giữa các job trùng video (tải lại cùng link).
  - Video > 20 phút: tự đề xuất model nhanh hơn (htdemucs) kèm thông báo chất lượng; người dùng chọn.
- **Tác động:** nhỏ với video ngắn, lớn với video trùng hoặc video dài.

### P8. Độ ổn định khi tải Douyin
- **Làm gì:**
  - Thử tải bằng yt-dlp trước, rồi mới dùng trình duyệt.
  - Khi Douyin đòi xác minh thì mở cửa sổ cho người dùng xử lý (đã có), và **giữ job ở trạng thái "chờ bạn"** thay vì báo lỗi.
  - Ghi lý do lỗi vào nhật ký `data/feedback`.
- **Tác động:** giảm job lỗi tải (hiện 3/3 lỗi tải đều là Douyin).

### P9. Đo lường cố định (làm đầu tiên)
- **Làm gì:**
  - Script `tools/bench.py` chạy lại 5 job mẫu: ngắn có lời, có phụ đề cứng, chỉ chữ, thuyết minh, dài 38 phút.
  - Ghi thời gian từng bước và VRAM tối đa vào `data/bench/<ngày>.json`, in bảng so sánh với lần trước.
  - Mỗi thay đổi P1–P8 phải đi kèm số đo trước/sau.
- **Công sức:** nhỏ.

## 4. UI

### U1. Trang job: chia tab thay vì 10 thẻ xếp dọc
Trang job hiện xếp dọc: video → Đánh giá → Bản dịch → Biên kịch → Chữ màn hình → Nội dung đăng → Kiểm tra TikTok → Watermark → Nhật ký. Trên iPhone phải cuộn rất dài.

- **Đầu trang luôn hiện:** video + trạng thái + nút tải/đăng.
- **Bên dưới là 4 tab:**
  - **Kết quả:** video, tải file, kiểm tra TikTok.
  - **Sửa:** Bản dịch, Biên kịch, Chữ màn hình, Watermark.
  - **Đánh giá:** chấm điểm, ghi chú, nhạc nền.
  - **Đăng:** nội dung đăng, đăng TikTok.
- **Nhật ký:** thu vào nút "📜 Nhật ký" mở ngăn kéo bên phải (trên điện thoại là trang trượt lên).
- **Nhớ tab cuối cùng** cho mỗi job.

### U2. Gộp "🔥 Trending" và "🚀 Link hot" thành một tab "🔎 Tìm video"
- **Vấn đề:** hai tab trùng nhau (cùng thẻ video, cùng danh mục Douyin, cùng nút xem trước) nhưng lọc khác nhau.
- **Gộp lại:** một thanh nguồn (TikTok VN / Douyin / Facebook), một cây chủ đề, 3 chế độ (Top / Insights / Từ khoá), và chọn nhiều để chạy hàng loạt (đang chỉ có ở Trending).
- **Thanh điều hướng còn 4 mục:** Xử lý video · Tìm video · Kho video · Tự chạy.

### U3. Màn hình bắt đầu gọn hơn
- **Vấn đề:** form tạo video (cột giữa) hiện tất cả tuỳ chọn.
- **Giữ ở màn hình đầu:** link/kéo thả, kiểu kết quả, giọng, phong cách dịch. Còn lại vào "Tuỳ chọn nâng cao" (gập), nhớ trạng thái mở/gập.
- **Có preset theo mục đích:** "TikTok reup nhanh", "Phim có Biên kịch", "Chỉ sub". Mỗi preset là một bộ tuỳ chọn đã lưu.

### U4. Cài đặt: tìm kiếm + nhóm theo việc
- Dialog Cài đặt có 17 nhóm. Thêm ô tìm kiếm cài đặt.
- Gom nhóm theo việc: **Tài khoản & đăng nhập** (Claude, ChatGPT/Gemini CLI, Douyin, TikTok, Facebook, Đăng TikTok), **AI dịch**, **Giọng**, **Xử lý video**, **Server**.
- Mỗi tài khoản hiện 1 dòng trạng thái (🟢/⚪) ngay đầu dialog.

### U5. Tải nhanh hơn, ít request hơn
- **Gom request khi mở job:** hiện mỗi lần mở job hoặc đổi trạng thái là khoảng 12 request (lines, style, screentext, content, watermarks, post, tiktok, exports…). Thêm `GET /api/jobs/{id}/bundle` trả tất cả một lần. Thẻ nào ở tab chưa mở thì tải sau.
- **Bớt polling:** `/api/system` + `/api/admin/status` đang được gọi mỗi vài giây. Gộp vào luồng SSE có sẵn, hoặc giãn ra 15 s khi tab không hiển thị (`document.hidden`).
- **Danh sách dài:** danh sách Lịch sử và lưới video (456 video Trending) chỉ vẽ phần đang nhìn thấy, dùng `content-visibility:auto` hoặc phân trang 60 mục.

### U6. Tách `index.html` (2 691 dòng) thành module
- **Làm gì:** giữ không cần build. Tách CSS ra `static/app.css`, JS ra các ES module `static/js/{api,jobs,review,hot,trending,settings}.js` nạp bằng `<script type="module">`.
- **Thư viện:** có thể thêm Alpine.js (15 KB, CDN có khoá phiên bản) cho phần form/thẻ để bớt code `innerHTML` + gán `onclick` thủ công.
- **Tác động:** sửa UI ít lỗi hơn, Claude và người đọc dễ tìm code hơn.
- **Rủi ro:** lớn nếu làm một lần. Nên tách dần, mỗi lần một tab, có kiểm tra trên trình duyệt.

### U7. Điện thoại (iPhone qua Tailscale)
- Thanh điều hướng thành thanh dưới đáy màn hình.
- Video kết quả hiện đầu trang job, nút to ≥ 44 px.
- Thẻ video trong Tìm video xếp 2 cột.
- Hộp xem trước toàn màn hình.
- Kiểm tra ở độ rộng 375 px, không có cuộn ngang.

### U8. Nhất quán nhỏ
- **Định dạng số:** đang lẫn kiểu Trung (`1.2w`, `亿`) và kiểu Việt (`1,2 Tr`, `N`). Dùng một hàm cho cả app: số Việt, có chú thích nguồn đếm.
- **Không tự phát tiếng khi mở trang:** xem trước mặc định tắt tiếng, bấm mới bật.
- **Thông báo lỗi** luôn có "cách xử lý", ví dụ lỗi TikTok tìm kiếm khi chưa đăng nhập đã làm đúng kiểu này.

## 5. Thứ tự làm

| Giai đoạn | Việc | Lý do |
|---|---|---|
| 1 (nhanh, ít rủi ro) | P9 đo lường, P4 kiểm tra TikTok chạy nền, U5 bớt request + polling, U8 | Có số đo trước; người dùng thấy nhanh hơn ngay ~1 phút/job |
| 2 | U1 chia tab trang job, U2 gộp Trending + Link hot, U7 điện thoại | Phần dùng hằng ngày, nhất là trên iPhone |
| 3 | P1 song song trong job, P3 gộp OCR, P6 tạo giọng | Giảm ~35 % thời gian xử lý |
| 4 | P5 render lại từng phần, P2 hàng đợi 2 làn | Lớn nhất về công sức, lợi nhất khi sửa nhiều và chạy hàng loạt |
| Song song, dần dần | U6 tách module, U3 preset, U4 cài đặt, P7, P8 | Không chặn việc khác |

## 6. Kiểm thử bắt buộc cho mỗi thay đổi

- Toàn bộ `python -m pytest -q` đều qua (hiện 240 test).
- Pipeline: chạy `tools/bench.py` trước/sau, đặt bảng so sánh vào mô tả thay đổi. Kiểm tra đồng bộ hình/tiếng (lệch tối đa ≤ 0,05 s) trên bản TikTok, và không cắt vào lời thoại (`test_cut_safety.py`).
- UI: mở trên trình duyệt ở 1400 px và 375 px, không lỗi console. Thao tác hết các nút của phần đã đổi.
- Không làm hỏng job cũ: mở lại 5 job cũ, mọi thẻ vẫn hiện đúng.
