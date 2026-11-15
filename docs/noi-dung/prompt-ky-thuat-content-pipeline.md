# Prompt cho session kỹ thuật: bước "Biên kịch" (content pipeline)

> Dán toàn bộ phần dưới đây vào session kỹ thuật.

---

## Bối cảnh

Kênh TikTok mới, ngách **phim ngắn reup** (chủ yếu phim hoạt hình 3D/AI từ Douyin). Pipeline hiện tại đã làm tốt phần kỹ thuật: tải → ASR → dịch → lồng tiếng → che phụ đề gốc → render 9:16. Nhưng video ra vẫn là **reup trơn**, nên:

- TikTok hạ đề xuất nội dung "unoriginal". Video cần một lớp nội dung mới đủ rõ (giọng kể, chữ, nhịp dựng).
- 3 giây đầu thường là câu thoại vu vơ, nên người xem lướt qua.
- Có nhiều khoảng lặng dài (10–20 giây không lời), làm giữ chân rơi mạnh.
- Cú twist hay nằm trong **chữ nhỏ trên màn hình** (bình luận, tin nhắn, đoạn văn dài) mà người xem không kịp đọc.

Cần thêm một bước **"Biên kịch"**: AI xem phim, viết lớp nội dung (hook, giọng kể, chữ trên màn hình, chỗ cắt, cái kết, vòng lặp, caption), cho người dùng sửa, rồi render vào video.

**Mẫu chuẩn để đối chiếu:** `docs/noi-dung/20261114-203147-6cf9_ghe-phu.md`. Đây là gói nội dung viết tay cho job `20261114-203147-6cf9` và đã được người dùng duyệt (hook hồi tưởng, anh ấy tự kể xưng "tôi", giọng trầm, ít chữ). Output AI của bước mới chạy trên job này phải đạt **cùng loại quyết định**: lấp khoảng 0:14–0:31, đọc to cái kết đang nằm trong chữ nhỏ ở 4:37, hook trong 3 giây, câu loop về cảnh mở. Câu chữ không cần giống hệt.

## Yêu cầu chức năng

### 1. Bước pipeline mới `content`
Đặt sau `translate`/`speakers` và trước `tts`. Có checkpoint/cache như các bước khác (`ck.key`, `reuse`). Bật bằng option `content_enabled`, mặc định tắt cho tới khi ổn định.

**1a. Dựng "timeline phân tích"**, không cần AI, từ dữ liệu đã có:
- `lines.json`: thoại, người nói, thời gian.
- Các **khoảng lặng ≥ 3 giây** giữa các câu thoại (đã có thời gian ASR).
- **Điểm chuyển cảnh** (PySceneDetect, miễn phí), kèm 1 keyframe mỗi cảnh.
- `screentext.json`/`screentext_tr.json`: đánh dấu riêng các **khối chữ dài hoặc nhỏ** (nhiều chữ, `line_h` nhỏ, hiện dưới ~4 giây). Đây thường là chỗ chứa twist.
- Tiêu đề và hashtag gốc (`meta.json`).

**1b. AI "xem" phim**, chia hai tầng (xem mục Model):
- Tầng hình: mô tả từng cảnh (ai, đang làm gì, cảm xúc), tìm **cảnh mạnh nhất** để làm hook/ảnh bìa và đoạn **cao trào**.
- Tầng biên kịch: từ timeline + mô tả cảnh, viết **content plan** dạng JSON.

**1c. Content plan (JSON, lưu `content.json`)**, có ít nhất:
```json
{
  "persona": {"pov": "toi|nguoi_dan_chuyen|nhan_vat_nu", "tone": "tram_it_chu|ngon_tinh|doi_thuong"},
  "hook": {"vo": "...", "overlay": "...", "start": 0, "end": 3, "alt_cold_open": {"from": 260.0, "to": 267.0, "overlay": "..."}},
  "title_bar": "...",
  "narration": [{"start": 14.2, "end": 17.0, "text": "...", "duck_original": 0.3}],
  "cuts": [{"from": 15.0, "to": 18.5, "reason": "lặng dài"}],
  "retention_texts": [{"at": 60.0, "text": "Đoạn cuối mới thật sự đau…"}],
  "replace_screentext": [{"id": 52, "vo": ["...", "..."], "big_subs": true}],
  "ending": {"overlay": "...", "loop_vo": "..."},
  "cover": {"at": 24.0, "text": "..."},
  "post": {"caption": "...", "hashtags": ["..."], "pinned_comment": "...", "ai_label": true, "credit": "Nguồn: ..."},
  "teasers": [{"from": 177.0, "to": 236.0, "overlay": "...", "end_card": "Bản đầy đủ trên kênh 👆"}]
}
```
Ràng buộc đưa vào prompt cho AI:
- Hook phải nói được **chuyện gì / vì sao đau** trong ≤ 3 giây.
- Giọng kể chỉ nói điều **hình không tự nói được**. Với tone `tram_it_chu`: khoảng 10–15 câu cho phim 4 phút, mỗi câu ≤ 12 chữ.
- Không khoảng lặng nào > 5 giây mà không có lời kể, nhạc lên hoặc bị cắt.
- Twist nằm trong chữ nhỏ **bắt buộc** được đọc thành lời.
- Câu cuối phải nối được về cảnh mở (loop).
- Caption: câu đầu chứa từ khoá tìm kiếm, cuối là **câu hỏi gây tranh cãi**. Không dùng `#fyp #xuhuong #viral`.

### 2. Màn hình sửa content plan (UI)
Trong trang job, thêm tab "Biên kịch":
- Chọn persona và tone (3×3 lựa chọn như trên), bấm **Viết lại**.
- Danh sách sửa trực tiếp: hook, thanh tiêu đề, từng câu kể (giờ bắt đầu, nội dung, nghe thử TTS), từng chỗ cắt (bật/tắt), chữ giữ chân, cái kết, câu loop.
- Xem trước khung hình tại mỗi mốc thời gian.
- Dùng lại cơ chế `wait_for_preview`: pipeline dừng sau bước `content` cho tới khi người dùng duyệt (khi option bật).

### 3. Render
- **Cắt** theo `cuts`, rồi **dời toàn bộ mốc thời gian** (thoại, phụ đề, chữ màn hình, lời kể) cho đúng. Viết hàm remap thời gian dùng chung và có unit test.
- **Lời kể**: thêm vào luồng TTS hiện có với `role: "content_vo"`. Dùng một giọng cố định cho cả kênh (chọn trong cài đặt, mặc định giọng nam trầm). Hạ tiếng gốc theo `duck_original` trong lúc kể. Không áp `fit_to_time` lên câu kể; đặt câu kể vào khoảng lặng.
- **Chữ hook**: chữ to giữa màn hình 0–3 giây.
- **Thanh tiêu đề**: cố định suốt video ở vùng nền mờ phía trên của bản 9:16.
- **Chữ giữ chân**: chữ nhỏ góc trên.
- **Khối chữ nhỏ được thay**: che khối đó (dùng cơ chế che phụ đề hiện có), thay bằng phụ đề lớn từng câu khớp lời kể.
- **Câu kết**: chữ to trên nền tối, giữ ≥ 3 giây.
- Phần nào bị tắt trong plan thì render bỏ qua phần đó.
- Thống nhất với `hook_intro` hiện có: khi `content_enabled` thì hook của bước `content` thay cho `hook_intro`, không đọc hai hook.

### 4. Đầu ra phụ
- **Ảnh bìa** `cover.jpg` (khung tại `cover.at` + chữ).
- **Clip mồi** `teaser_1.mp4`, `teaser_2.mp4` theo `teasers`, có chữ hook và thẻ cuối.
- `post.json`: lấy caption, hashtag, bình luận ghim, nhắc **bật nhãn "Nội dung do AI tạo"** và dòng ghi nguồn từ content plan (thay cho `postgen` khi bước content đã chạy).

### 5. Áp dụng cho mọi video (bổ sung)
- **Công tắc trong ⚙ Cài đặt:** "Bật Biên kịch mặc định cho mọi video mới". Từng job vẫn ghi đè được. Job cũ chạy lại từ bước `content` mà không phải tải/ASR/dịch lại. Autopilot dùng chung công tắc này. Khi chạy autopilot thì không dừng chờ duyệt, nhưng nhật ký job phải ghi rõ "kịch bản chưa được duyệt".
- **AI tự nhận loại video** (một lần gọi rẻ trước khi viết, lưu `content.json → video_type` kèm lý do; người dùng đổi được trong tab Biên kịch). Mức can thiệp theo loại:

| `video_type` | Nhận biết | Mức can thiệp |
|---|---|---|
| `story_sparse` | Có cốt truyện, tổng khoảng lặng ≥ 15% thời lượng hoặc có khối chữ dài | **Đầy đủ:** hook, lời kể lấp khoảng lặng, cắt, cái kết đọc thành lời, loop |
| `story_dense` | Có cốt truyện, thoại gần như liên tục | Hook, thanh tiêu đề, cắt nhẹ, cái kết, caption. **Không chèn lời kể đè lên thoại** |
| `narrated` | Video gốc đã có người thuyết minh (dùng nhận diện người kể của `narrator.py`) | **Không thêm giọng kể thứ hai.** Viết lại chính lời thuyết minh theo persona/tone, giữ thời gian câu gốc, cộng hook + caption |
| `short_clip` | Dưới ~30 giây, hài/meme/khoảnh khắc | Chỉ hook chữ + caption + bình luận ghim. Không lời kể, không cắt |

  Ngưỡng nhận biết đặt trong cài đặt, có unit test cho phần phân loại không cần AI (khoảng lặng, thời lượng, có người kể hay không).

## Model đề xuất (mặc định: ChatGPT qua Codex CLI)

Máy: RTX 3060 12 GB. Đã có sẵn provider `codex_cli` (gói ChatGPT, `dubber/providers/cli.py`) cùng orchestrator có fallback. Hãy **dùng lại orchestrator**, chỉ thêm task mới. **Mặc định cả hai tầng dùng `codex_cli`. Không dùng Gemini trong chuỗi mặc định của bước này.**

| Việc | Mặc định | Fallback (theo thứ tự) |
|---|---|---|
| **Xem phim** (mô tả cảnh, chọn cảnh mạnh) | **`codex_cli`**, gửi **ảnh keyframe** (Codex CLI nhận ảnh qua `codex exec -i <ảnh>`, model GPT đọc được hình). Mỗi cảnh 1 keyframe, thêm 1 khung cho mỗi khoảng lặng dài và mỗi khối chữ nhỏ. Thu nhỏ còn khoảng 512 px, ghép **lưới 3×3 có ghi mốc thời gian** lên từng ô để giảm số lần gọi. Kèm timeline dạng chữ (thoại + chữ màn hình) trong cùng prompt | `claude_cli` (cũng đọc được ảnh), rồi local **Qwen2.5-VL 7B** qua Ollama (`qwen2.5vl:7b`, vừa 12 GB) để không bị kẹt khi hết quota |
| **Biên kịch tiếng Việt** (hook, lời kể, caption) | **`codex_cli`**, dùng model mạnh nhất gói ChatGPT cho phép | `claude_cli` Sonnet, rồi Ollama Qwen 7B (tiếng Việt kém hơn rõ, chỉ dùng để không bị kẹt) |
| **Lời kể (TTS)** | **VieNeu** local đang có, chọn một giọng nam trầm cố định | Edge TTS `vi-VN-NamMinhNeural` (miễn phí) |
| **Chuyển cảnh** | PySceneDetect (`ContentDetector`) | ffmpeg `select='gt(scene,0.3)'` |

Ghi chú:
- **Kiểm tra provider `codex_cli` hiện chỉ gửi text qua stdin.** Cần mở rộng để truyền ảnh (`-i`, lặp được nhiều lần) và test với bản Codex CLI đang cài. Nếu bản đang cài không hỗ trợ ảnh thì báo rõ trong nhật ký job rồi chuyển sang fallback, không im lặng bỏ qua tầng hình.
- Để model chỉnh được trong cài đặt (`content_vision_provider`, `content_writer_provider`, `content_codex_model`), mặc định đều là `codex_cli`. Không hardcode tên model; để trống thì dùng model mặc định của Codex CLI.
- Gói ChatGPT có giới hạn lượt dùng theo giờ. Gom các lần gọi lại (lưới ảnh, một lần gọi cho cả kịch bản), và khi bị giới hạn thì orchestrator chuyển sang fallback như đang làm với bước dịch.
- Cache kết quả "xem phim" theo hash video. Viết lại kịch bản (đổi persona/tone) **không** được gọi lại tầng hình.

## Tiêu chí hoàn thành

1. Chạy bước `content` trên job `20261114-203147-6cf9`. `content.json` phải có: hook ≤ 3 giây, lời kể lấp khoảng 0:14–0:31, đọc to twist "năm thứ sáu cô ấy lấy chồng" (đang nằm trong khối chữ ở ~4:37), câu loop, các chỗ cắt khoảng lặng. So với `docs/noi-dung/20261114-203147-6cf9_ghe-phu.md`.
2. Render ra bản 9:16 có đủ: lời kể, chữ hook, thanh tiêu đề, cái kết đọc thành lời. Không còn khoảng lặng > 5 giây. Tổng thời lượng ngắn hơn bản gốc.
3. Sửa một câu kể trong UI rồi render lại thì chỉ chạy lại TTS câu đó và render, không gọi lại AI xem phim.
4. Tắt `content_enabled` thì pipeline chạy y như cũ (tương thích ngược).
5. Unit test cho: phát hiện khoảng lặng, remap thời gian sau khi cắt, validate schema `content.json`.
6. Cập nhật `docs/ROADMAP.md` (nhật ký + kiến trúc) và README phần hướng dẫn tab "Biên kịch".
