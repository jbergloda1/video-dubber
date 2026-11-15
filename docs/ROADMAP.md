# Video Dubber — Roadmap phase 2

Tài liệu sống: cập nhật sau mỗi mốc để có thể làm tiếp an toàn ở session khác.
Trạng thái: ✅ xong · 🟡 đang làm · ⬜ chưa làm · ⛔ bị chặn (cần bạn quyết định)

---

## 1. Hiện trạng (đánh giá ngày 2026-11-13)

| Module | Vai trò hiện tại | Dùng lại cho |
|---|---|---|
| `jobs.py` | Job, danh sách bước (`STEPS`), hàng đợi 1 worker (GPU tuần tự), lưu `job.json`, huỷ, chạy lại từ bước | Mọi chế độ mới (A), job từ nguồn mới (B, D) |
| `pipeline.py` | Chuỗi bước download → … → render, `should()`/`skip()` theo `start_from` | Chế độ "chỉ dịch chữ trên màn hình" (A) |
| `downloader.py` | yt-dlp (TikTok, …), Douyin qua Chrome (profile riêng, có cửa sổ captcha cho người dùng), trang chủ đề 热榜 | Adapter nguồn (B, D) |
| `trending.py` | Crawl 热榜 + 精选 → `data/trending.json`, ảnh bìa, gộp trùng | Thư viện khám phá theo chủ đề (B) |
| `translate.py` | 4 provider viết tay (claude_cli, anthropic, ollama, google) + fallback 1 cấp sang Google; căn thời gian | **Phải tách thành lớp provider** (C) |
| `screentext.py` · `textclean.py` · `render.py` | OCR (GPU) → gom/track khối chữ → dịch → xoá chữ (OpenCV / LaMa thử nghiệm / mờ / hộp) → vẽ lại bằng ASS | Chế độ A đã có ~80% |
| `app.py` + `static/index.html` | FastAPI + SSE, cài đặt (khoá được che), tab Trending, xem nhanh | UI cho A, B, C, D |
| `config.py` | `DEFAULTS` + `settings.json` (merge, khoá bí mật che `••••`) | Cấu hình provider (C), danh mục (B) |

**Đã thiếu:**
- (C) Lớp provider dịch thống nhất: phân loại lỗi, retry có giới hạn, chuyển provider, kiểm tra Ollama trước khi dùng, OpenAI-compatible, Gemini, lưu tiến độ dịch.
- (A) Chế độ đầu ra riêng: không nhận dạng giọng, không phụ đề lời nói.
- (B) Danh mục nhiều cấp, adapter nguồn, phân trang, lọc theo chủ đề.
- (D) Adapter Facebook Reels.
- Bộ test tự động: hiện chỉ có script thử tay (`tests/try_download.py`, `tests/bench_lama.py`).

**Phát hiện khi khảo sát (ảnh hưởng thiết kế):**
- Tìm kiếm Douyin theo từ khoá **bắt captcha khi chưa đăng nhập** (trang 验证码中间页).
- Các tab chủ đề 精选 (体育 `tag_id=300207`, 知识 `300213`, URL `/jingxuan/<slug>`) cho feed **theo chủ đề mà không cần đăng nhập**.
- → Khám phá theo chủ đề trên Douyin = feed tab + lọc từ khoá/hashtag cho danh mục con. Tìm kiếm chỉ là tuỳ chọn phụ, khi người dùng tự giải captcha.
- Repo chưa có git → không có mốc rollback; mỗi mốc phải chạy test hồi quy trước khi sang mốc sau.

## 2. Phụ thuộc

```
C (provider dịch + fallback) ──► A (chữ trên màn hình dùng dịch theo lô)
        │                     └► pipeline dịch lời nói (hiện có)
B (adapter nguồn + danh mục) ──► D (Facebook = 1 adapter nữa)
A ⟂ B  (độc lập)
```
- C là nền tảng chung lớn nhất (cả dịch lời nói lẫn dịch chữ màn hình đều qua nó), và đang có lỗi thực tế: Google 429/302, Ollama không chạy, Claude CLI chưa đăng nhập.
- D chỉ nên làm sau khi B có giao diện adapter, để không phải gắn cứng Facebook vào Douyin.

## 3. Thứ tự triển khai

1. **M1 – C: lớp provider dịch** (nền tảng, nhiều lỗi thật cần sửa).
2. **M2 – A: chế độ "Video & audio gốc + dịch chữ trên màn hình"** (chủ yếu nối lại phần đã có; rủi ro thấp; dùng M1).
3. **M3 – B: adapter nguồn + danh mục chủ đề** (tách `trending.py` thành adapter Douyin; thêm `categories.json`).
4. **M4 – D: adapter Facebook Reels** (dùng giao diện của M3).
5. Song song / sau: hoàn thiện LaMa (đang thử nghiệm, xem §9).

## 4. Thay đổi kiến trúc (chỉ phần cần thiết)

- `dubber/providers/` — lớp provider dịch:
  - `base.py`: `Provider` (`id`, `kind` = `llm`/`mt`, `local`, `available(s)`, `complete(job, system, user, schema)` hoặc `translate_list(...)`), phân loại lỗi `Transient / RateLimited / QuotaExhausted / AuthError / NotConfigured / Unreachable / BadOutput`.
  - một file cho mỗi provider: `claude_cli`, `anthropic`, `openai_compat` (OpenAI, OpenRouter, LM Studio, vLLM…), `gemini`, `ollama`, `google_free`.
  - `orchestrator.py`: chuỗi fallback, retry backoff có trần, khoá provider tạm thời (cooldown) khi quota/auth lỗi, kiểm tra provider local trước khi dùng, che khoá API trong mọi thông báo lỗi.
  - `translate.py` giữ API cũ (`translate()`, `translate_texts()`, `align()`) và gọi orchestrator → pipeline không đổi.
- `dubber/sources/` — adapter nguồn: `base.py` (`discover(target, cursor) → items, next_cursor`), `douyin.py` (hot, featured, tab chủ đề, tìm kiếm tuỳ chọn), `facebook.py`.
  `trending.py` thành lớp lưu trữ thư viện (giữ file `data/trending.json`).
- `dubber/categories_default.json` → sao chép thành `data/categories.json` (người dùng sửa được); mỗi danh mục khai báo cách lấy theo từng nguồn (tab + từ khoá), không gắn cứng trong code.
- `output_mode` thêm giá trị `"text"`.

## 5. Ảnh hưởng

| Thay đổi | Có thể ảnh hưởng | Biện pháp |
|---|---|---|
| Lớp provider | Bước Dịch, dịch chữ màn hình, nút "Sửa bản dịch" | Giữ chữ ký `translate()`/`translate_texts()`; test bằng provider giả |
| `output_mode="text"` | `pipeline.run`, `render.render`, đổi chế độ khi chạy lại (`keep_text`) | Skip có kiểm soát; không đọc `lines.json` khi không có |
| Adapter nguồn | Tab Trending, xem nhanh, chạy job từ trending | Giữ khoá `id` cũ của Douyin; mục mới có `platform` |
| Cài đặt mới | `settings.json` cũ | Merge với `DEFAULTS` như hiện nay; không đổi tên khoá cũ |

## 6. Mốc

| # | Mốc | Kiểm chứng | Trạng thái |
|---|---|---|---|
| M1.1 | `providers/` base + phân loại lỗi + orchestrator | unit test retry/fallback/không lặp vô hạn/che khoá | ✅ |
| M1.2 | Port claude_cli, anthropic, ollama (probe `/api/tags`), google_free (giới hạn tốc độ) | unit + chạy thật Claude CLI | ✅ |
| M1.3 | Thêm openai_compat, gemini | unit (phân loại HTTP); chưa có key để chạy thật | ✅ |
| M1.4 | Lưu tiến độ dịch theo khối (`translate_progress.json`), chạy lại không dịch lại | unit | ✅ |
| M1.5 | UI: chọn provider + model + thứ tự dự phòng, trạng thái từng provider, nút Thử | thủ công: Claude ✅, Ollama → 'không chạy', Google → 'giới hạn tốc độ' | ✅ |
| M2 | Chế độ `text` (bỏ ASR/TTS/phụ đề lời nói, giữ audio gốc — chép nguyên luồng audio) + UI + đổi chế độ dùng lại kết quả | job bóng đá: âm lượng -10,0 vs -10,1 dB, chữ dịch tại chỗ; Ollama không chạy → tự chuyển Claude | ✅ |
| M3.1 | `sources/` + adapter Douyin (hot, featured, 16 tab chủ đề, tìm kiếm tuỳ chọn) | crawl thật tab 体育: 40 video | ✅ |
| M3.2 | `categories.json` nhiều cấp + từ khoá + phân loại lúc đọc + chống trùng (gộp feed) | unit; Bóng đá 14 / Bóng rổ 8 / Bóng bàn 2 đúng | ✅ |
| M3.3 | UI: cây danh mục + số lượng, lấy theo chủ đề / theo nguồn, sửa danh mục | thủ công | ✅ |
| M4 | Adapter Facebook Reels: khám phá Reels công khai của Page bạn nhập, xem nhanh qua yt-dlp, tải qua yt-dlp; đăng nhập tuỳ chọn trong profile riêng | ESPN: 10 reel (lượt xem đúng), xem nhanh 720x900, tải reel 11s qua pipeline | ✅ |

### Việc tiếp theo (đề xuất, chưa làm)

| # | Việc | Vì sao |
|---|---|---|
| N1 ✅ | LaMa hoàn thiện: mặt nạ bắt viền chuyển màu (luật màu nới + luật 'khác nền' theo viền vùng), LaMa nở mặt nạ rộng hơn, màu nền tần số thấp lấy từ inpaint biên (hết dải sáng); job bóng đá render lại bằng LaMa | test_textclean: luật cũ sót 11,9% điểm chữ, mới 1,0% |
| N2 ✅ | Theo dõi chữ chuyển động — làm trong M7 | |
| N3 | Chạy thật OpenAI-compatible và Gemini khi có key | M1.3 mới test bằng giả lập |
| N4 | Hẹn giờ crawl theo danh mục (chỉ lấy danh sách) | Feature B, nếu bạn muốn tự động hoá |
| N5 ✅ | Adapter Codex CLI — làm trong M6 (Codex đã đăng nhập ChatGPT sẵn trên máy) | |


### Rà soát lại yêu cầu phase 2 (2026-11-13) — các phần còn thiếu

| # | Yêu cầu trong prompt | Hiện trạng trước rà soát | Mốc |
|---|---|---|---|
| C | "Fall back to local translation… provided a working local engine exists" | Chỉ có Ollama (chưa cài) → thực tế không có engine local | M5 |
| C | "CLI integrations where officially supported", "Official OAuth / login-based" | Chỉ Claude CLI; Codex CLI (ChatGPT) và Gemini CLI (Google) chưa có | M6 |
| C | "Configurable … rate limiting", "Track provider availability" | Giới hạn tốc độ gắn cứng cho Google; cooldown mất khi khởi động lại | M6 |
| A | "Handle … fonts … animation", "labels, decorative text" | Chữ giả định đứng yên; luôn Arial đậm; ngưỡng cỡ chữ cố định | M7 |
| B | "Support pagination", hủy/thao tác an toàn | Chỉ "số lần cuộn"; không tải tiếp, không huỷ crawl | M8 |
| D | "Handle … rate limits" | Không giãn cách tải Facebook | M8 |

| # | Mốc | Trạng thái |
|---|---|---|
| M5 | Engine dịch offline tích hợp sẵn (NLLB-200 1.3B/600M qua CTranslate2, GPU) làm bước lùi local cuối chuỗi; provider mới tự vào cuối chuỗi đã lưu | ✅ chạy thật 2,4s/2 câu |
| M6 | Provider Codex CLI + Gemini CLI (đăng nhập chính thức, nút đăng nhập/đăng xuất), giãn cách theo provider (`translate_min_interval_s`), lưu cooldown (`data/provider_state.json`) | ✅ Codex chạy thật 9,9s; Gemini CLI báo đúng 'chưa chọn phương thức đăng nhập' |
| M7 | Theo dõi chữ chuyển động (`screentext.track`, `\move` theo đường đi, xoá chữ theo vị trí nội suy + bù tốc độ), khớp độ đậm (Arial Black/đậm/thường) hoặc font tự chọn, ngưỡng cỡ chữ chỉnh được | ✅ clip chữ chạy ngang: 1 đoạn chuyển động, 11 điểm |
| M8 | Crawl "tải thêm" (`data/crawl_state.json`), Dừng giữ lại phần đã lấy + ghi đúng số trang đã cuộn, giãn cách tải Facebook (`facebook_gap_s`) | ✅ Dừng: lưu 60 video; Tải thêm: trang 2, +58 video |

### Phase 3 (2026-11-13): tách giọng + watermark
| # | Việc | Kết quả |
|---|---|---|
| P1 | Tách giọng/nhạc nền: `dubber/separate.py` — BS-RoFormer ep317 (audio-separator, fp16, overlap 2) mặc định; Mel-Band RoFormer / htdemucs_ft / htdemucs tuỳ chọn; lỗi → tự dùng Demucs. Chạy trong tiến trình con. | ✅ Đo trên bản trộn giọng TTS đã biết + nhạc thật: giọng gốc sót lại trong nhạc nền −35,8 dB (htdemucs −29,2 dB); 60 s âm thanh mất ~16 s trên RTX 3060 |
| P2 | Giảm nhạc nền khi có giọng Việt (`bg_duck`, sidechaincompress) | ✅ −2,7 dB nhạc nền trong lúc nói |
| P3 | Xoá watermark gốc: bước mới `watermark` (`dubber/watermark.py`) — OCR mẫu chữ watermark (抖音/TikTok/@/号:/ID…), ghép đoạn OCR đọc thiếu, nối liền khi watermark nhảy chỗ, mặt nạ nét chữ từ cạnh cố định + ô logo bên trái; watermark chạy → theo vết. Chữ trên màn hình trùng watermark thì xoá chứ không dịch. Trình vẽ/bật-tắt vùng trong trang job (`watermarks.json`, `/api/jobs/{id}/watermarks`, `/frame`). | ✅ Clip thử có watermark Douyin nhảy từ trên-trái xuống dưới-phải ở giây 15: tìm đúng 2 vùng, xoá cả logo và chữ, không hở lúc chuyển |
| P4 | Watermark của bạn (`dubber/brand.py`): icon TikTok/YouTube/Facebook + tên, trôi chậm / đổi góc / đứng yên, lần lượt từng nền tảng hoặc cùng lúc, độ đậm, cỡ; tuỳ chọn ghi "Nguồn: tác giả gốc" | ✅ |

Giới hạn: chỉ tự xoá watermark có chữ nhận ra được (logo trơn không chữ → bạn vẽ tay); watermark trong suốt rất mờ có thể sót viền nhẹ; trong ~1 s quanh lúc watermark nhảy chỗ, vùng mới có thể bị vẽ lại nền sớm.

### Phase 4 (2026-11-13): ước tính, nội dung đăng, theo dõi phần cứng
| # | Việc | Kết quả |
|---|---|---|
| Q1 | Ước tính trước khi chạy (`dubber/estimate.py`, `POST /api/estimate`): lấy độ dài video không cần tải (`downloader.probe`, kết quả Douyin được dùng lại cho bước tải), thời gian từng bước học từ các job trước (mỗi bước giờ ghi `secs` vào job.json), chi phí dịch theo bảng giá (CLI gói = 0, trừ hạn mức), ElevenLabs theo credit + hạn mức còn lại của tài khoản, tiền điện, dung lượng ổ. Hộp thoại khi bấm Bắt đầu (tắt được), nút 💰 Ước tính, và ước tính tổng khi đưa nhiều video Trending vào hàng đợi. | ✅ Link Douyin 4 phút 14 giây: ước tính 15 phút 14 giây (thực tế 17,5 phút), lấy thông tin mất 5 giây |
| Q2 | Nội dung đăng (`dubber/postgen.py`, bước mới `summary`, `post.json`): tiêu đề, mô tả, hashtag + caption riêng TikTok / YouTube / Facebook, hashtag gốc dịch; dự phòng bằng dịch máy; thẻ “📝 Nội dung đăng” có nút chép và ↻ Viết lại | ✅ Job thật: viết bằng Claude CLI trong 11 giây |
| Q3 | Popover phần cứng (`GET /api/system`, psutil + nvidia-smi): CPU, RAM, GPU, VRAM kèm biểu đồ 60 giây, điện/quạt/xung/NVENC, RAM của app, ổ trống, job đang chạy; nút trên header hiện GPU % và nhiệt độ | ✅ |

Giới hạn: hashtag là gợi ý của AI, không phải dữ liệu xu hướng trực tiếp từ nền tảng; giá API ghi cứng trong `PRICES` (cập nhật khi giá đổi); ước tính thời gian chính xác dần sau vài job.

## 7. Chiến lược test

- `tests/` với pytest:
  - **unit**: orchestrator (provider giả lỗi 429 / quota / auth / mạng), parse JSON, align, gộp khối chữ OCR, lọc danh mục;
  - **hồi quy**: import tất cả module, `screen_text_layout`, `render.write_ass`, cấu hình cũ vẫn nạp được.
- **end-to-end** thủ công: job bóng đá (`20261113-115256-6404`) và job Trái Đất (`20261113-100618-ca3e`) sau mỗi mốc chạm vào pipeline.
- Không gọi dịch vụ trả phí trong test tự động.

## 8. Tương thích ngược

- `settings.json` cũ: khoá mới có giá trị mặc định; `translate_provider` cũ vẫn hợp lệ (`google` → `google_free`, ánh xạ trong code).
- Job cũ: `STEPS` chỉ thêm, không đổi tên; `Job.load` bỏ qua bước lạ / thêm bước thiếu (đã vậy).
- `data/trending.json`: mục cũ không có `platform` → mặc định `douyin`.

## 9. Rủi ro & đánh đổi

- **Nền tảng**: Douyin/Facebook có thể đổi giao diện hoặc chặn bất kỳ lúc nào; adapter phải lỗi riêng lẻ, không làm hỏng nguồn khác. Không vượt captcha/đăng nhập; chỉ dùng phiên do chính người dùng đăng nhập trong cửa sổ của app.
- **Facebook Reels**: khám phá công khai rất hạn chế khi chưa đăng nhập; khả thi nhất là từ URL trang/reel bạn cung cấp. Không có "trending Facebook" công khai.
- **Chi phí provider**: API trả phí (Anthropic, OpenAI, Gemini) tính theo token; gói Claude Pro/Max chỉ dùng được qua Claude Code CLI (đăng nhập chính thức), **không** thành API key.
- **Codex/ChatGPT**: hỗ trợ qua API key OpenAI-compatible. Codex CLI (đăng nhập ChatGPT) chưa cài trên máy → chưa làm adapter CLI (cần bạn quyết định nếu muốn).
- **Chữ động (bay, xoay, hiệu ứng)**: tracker hiện giả định chữ đứng yên; chữ chuyển động chỉ được xử lý từng đoạn ngắn.
- **LaMa**: đẹp nhất ở chỗ chữ đè lên người/vật và nền có chi tiết; chậm hơn OpenCV ~2–4x (≈6,5 khung/s ở 576x1024 trên RTX 3060). Mặc định vẫn là OpenCV; đổi trong Tuỳ chọn nâng cao.

## Nhật ký
- 2026-11-13: khảo sát xong, viết roadmap; bắt đầu M1.
- 2026-11-13: M1 xong — `dubber/providers/` (6 provider, orchestrator), 18 unit test; `translate()`/`translate_texts()` giữ chữ ký cũ.
- 2026-11-13: M2 xong — `output_mode="text"`, `pipeline.SPEECH_STEPS`, `app._mode_switch_step`; 24 test.
- 2026-11-13: M3 xong — `dubber/sources/` (Douyin), `categories.py` + `categories_default.json`; phân trang = số lần cuộn (feed đề xuất không có con trỏ ổn định); 31 test.
- 2026-11-13: M4 xong — `dubber/sources/facebook.py` (profile + locale en-US riêng, khoá `fb:<id>`, ảnh bìa `fb_<id>.jpg`), `downloader._facebook` / `stream_info`; xem nhanh dùng chung proxy với header từ yt-dlp; 45 test.

## Kiến trúc sau phase 2 (tóm tắt để làm tiếp)

```
dubber/
  providers/   base.py (Provider + lỗi) · llm.py (claude_cli, anthropic, openai, gemini, ollama, google_free)
               orchestrator.py (chuỗi dự phòng, retry, cooldown, che khoá) · __init__.REGISTRY
  sources/     base.py (SourceAdapter) · douyin.py · facebook.py · __init__.REGISTRY
  categories.py + categories_default.json  -> data/categories.json (người dùng sửa)
  trending.py  thư viện video + crawl(targets) chạy từng feed độc lập
  translate.py build_prompt/align + translate()/translate_texts() qua orchestrator (+ translate_progress.json)
  pipeline.py  MODE_NAMES / SPEECH_STEPS / OUTPUTS — thêm chế độ = thêm vào 3 bảng này
```
- Thêm provider dịch: 1 class `Provider` + đăng ký trong `providers/__init__.py`.
- Thêm nền tảng: 1 class `SourceAdapter` + đăng ký trong `sources/__init__.py`; nhánh tải trong `downloader.download` nếu yt-dlp chưa đủ.
- Thêm chủ đề: sửa `data/categories.json` (hoặc nút ✏ trong tab Trending), không cần code.
- Test: `python -m pytest tests -q`.
- 2026-11-13: N1 xong — `textclean._text_mask` (+`_differs_from_background`), `_boundary_fill` (ghép tần số thấp), ngưỡng LaMa 0,9; 49 test.
- 2026-11-13: Rà soát lại prompt phase 2 → bổ sung M5–M8 (offline NLLB, Codex/Gemini CLI, giãn cách + cooldown bền, chữ chuyển động + font, tải thêm/dừng crawl, giãn cách Facebook); 70 test.
  Giới hạn còn lại: chữ xoay/biến dạng/hiện dần từng chữ chưa xử lý riêng; font chỉ khớp độ đậm (không nhận diện kiểu chữ có chân/viết tay); Gemini CLI cần bạn bấm Đăng nhập một lần để chọn 'Login with Google'.
- 2026-11-13: Phase 3 — tách giọng BS-RoFormer, giảm nhạc nền khi nói, xoá watermark gốc (tự tìm + vẽ tay), watermark chuyển động của bạn; 78 test.
- 2026-11-13: Phase 4 — ước tính chi phí/thời gian, nội dung đăng bài, popover phần cứng; 84 test.
- 2026-11-13: Che phụ đề gốc bám theo từng dòng: `subdetect.track_lines` OCR dải phụ đề mỗi 0,25 s (bỏ qua OCR khi nét chữ không đổi → video 4 phút mất 79 giây), ghi `subevents.json` (vị trí, độ rộng, thời gian từng dòng); khi render chỉ che đúng dòng đang hiện (làm mờ / xoá nét / LaMa / hộp) thay cho dải cố định. Job cũ: chạy lại từ bước 'Dò phụ đề gốc'. 86 test.
- 2026-11-13: Chế độ mới 'Thuyết minh' (`voiceover`, `output_vo.mp4`): nhạc/tiếng động gốc giữ nguyên, giọng gốc nhỏ (`vo_original_volume` 0,5) và tự nhỏ thêm khi giọng Việt nói (sidechain ratio 2,5), giọng Việt to. Chuyển lồng tiếng ↔ thuyết minh chỉ render lại phần trộn âm. Sửa lỗi `job.json` bị khoá trên Windows làm hỏng job (lưu thử lại, không bao giờ làm hỏng job). 89 test.
- 2026-11-13: Che phụ đề kỹ hơn + nhanh hơn: làm mờ/hộp tối chạy trong ffmpeg bằng mặt nạ bật/tắt theo từng dòng (`render.sub_cover_filters`, mặt nạ kéo về dải đầy đủ 0–255; trước đó 8% chữ gốc lọt qua). Dò dòng: khung OCR đọc yếu vẫn tính là có chữ, che sớm 0,4 s / muộn 0,5 s, nối khoảng hở < 1,2 s, phép bỏ qua OCR chỉ so nét chữ sáng trên cả dải và vẫn OCR mỗi giây. Đo bằng OCR lại video kết quả (5 khung/giây): lộ chữ gốc 65 → 8 khung (8 khung còn lại là chữ trong cảnh, không phải phụ đề). Render video 4 phút 4K: ~80 s. Watermark của bạn: bỏ icon, chỉ còn chữ. 89 test.
- 2026-11-13: Import video từ máy (`POST /api/jobs/upload`, kéo thả trong form; mp4/mov/mkv/webm…; chuyển sang MP4 không nén lại khi được). Xuất file tên tiếng Việt vào `exports/` (`dubber/export.py`, hard link, kèm .srt; tên lấy từ tiêu đề bài đăng) + nút “📂 Mở thư mục render”, thư mục chỉnh trong Cài đặt. Xoá giọng thuyết minh, giữ giọng nhân vật (`dubber/narrator.py`, tuỳ chọn `narrator_remove`): người nói nhiều nhất = thuyết minh (sửa vai từng câu trong bảng Bản dịch), quét dấu giọng ECAPA theo cửa sổ 1 s trên `vocals.wav` → `vocals_keep.wav`, nhận dạng + dịch lời nhân vật làm phụ đề, chỉ lồng tiếng câu thuyết minh. Thử video Red Bull (thuyết minh tiếng Trung + nhân vật tiếng Anh): tắt 155 s thuyết minh, giữ 36 câu nhân vật; Whisper nghe lại video cuối: 0 đoạn tiếng Trung. Giới hạn: lúc thuyết minh nói đè lên nhân vật thì đoạn đó bị tắt luôn. 93 test.
- 2026-11-13: Watermark: nhận thêm tên kênh/tác giả (vd 极限档案) và chữ nhỏ đứng yên ở góc hiện ≥15% thời lượng; logo cố định được nối liền qua đoạn OCR không đọc được (nền đá, lá cây). Watermark mặc định làm mờ bằng ffmpeg như phụ đề (LaMa vẫn chọn được). Tối ưu render: làm mờ + mặt nạ ở 1/4 độ phân giải rồi phóng lên, viền mềm rộng hơn và không làm tối, vùng xử lý rộng hơn ô che (không còn cạnh sắc), giải mã GPU (`-hwaccel cuda`), NVENC p4. Video Red Bull 284 s 4K: render 286 → 136 s (1080p: 102 → 64 s); watermark đọc được 212/284 → 1/284 khung, phụ đề gốc không còn dòng nào đọc được. 95 test.
- 2026-11-13: Dịch chữ màn hình nhanh hơn + không treo: chỉ xoá chữ trong những đoạn có chữ (`textclean.make_clean_segments`, ghép lại bằng overlay theo thời gian), xử lý nhiều khung song song, bước inpaint/blur chạy trên bản thu nhỏ ≤ 360 px; bỏ "chữ" khổng lồ (con dấu, tranh thư pháp: cao > 18% hoặc > 25% diện tích) — trước đó một con dấu bị OCR nhầm làm treo render hàng chục phút. Job 375 s: render 317 → 126 s. Nhãn bước "Dịch" không còn hiện "Codex đang dịch…" khi chỉ dịch chữ màn hình.
- 2026-11-13: Kho video (`dubber/library.py`, tab 📦): dán nhiều link → tải song song (`library_parallel`, Douyin lấy thông tin lần lượt cách `library_douyin_gap_s` giây, phần tải file song song), ảnh bìa + bản xem, chọn nhiều video → xử lý (có ước tính), job dùng file trong kho (hard link, không tải lại). 101 test.
- 2026-11-13: Chữ trên màn hình đẹp + đủ hơn: nhận chữ dọc (cỡ chữ = bề ngang cột, vẽ chữ Việt ngang gọn cạnh cột), che sớm 0,8 / muộn 1,3 khoảng lấy mẫu, chữ Việt luôn nằm trong khung (đẩy vào trong, thu nhỏ nếu cần), font Be Vietnam Pro đi kèm app (`data/fonts`, libass `fontsdir`) theo độ đậm chữ gốc (thường 400 + giãn chữ / đậm 700 / rất đậm 800), viền mảnh + bóng mềm thay viền đen dày; Noto Serif chọn tay. Job 'Mực dấu Long Tuyền': khung còn chữ Trung 42 → 10/749 (10 khung còn lại là bức thư pháp treo trong cảnh, cố ý không xoá), render 123 s. Xoá chữ: OpenCV đa luồng (CPU) 36 s vs LaMa GPU 90 s cho 140 s video — GPU không nhanh hơn vì thời gian nằm ở chép khung & chuẩn bị mặt nạ. Kho video: bấm ảnh bìa = chọn, nút ▶ Xem để xem. 102 test.
- 2026-11-14: Sửa treo render âm thầm: mỗi watermark một nhánh lọc (29 nhánh trên video 'Metal Slug 3') làm đồ thị lọc ffmpeg đứng yên mà không báo lỗi → gộp mọi vùng che (phụ đề + watermark) vào MỘT nhánh (một bản làm mờ độ phân giải thấp + một mặt nạ bật/tắt theo thời gian). Thêm canh gác `util.ffmpeg(stall_s=180)`: ffmpeg không tiến triển 3 phút → dừng và báo lỗi rõ ràng. Watermark chạy không còn bị dịch thành chữ (so theo nội dung chữ, mọi vị trí trên đường đi, đọc thiếu/đọc sai như '阿史', '汇阿史'). Job 424 s: render 62 s. 105 test.
- 2026-11-14: Watermark chạy che kín: dò mỗi 1 s cho video tới 15 phút (trước 2 s khi > 400 s), ô che theo đường đi được nới thêm lề và bật ngay từ đầu đoạn, nối khoảng hở ≤ 8 s giữa các vị trí của cùng một watermark. Kiểm tra bằng OCR độ phân giải đầy đủ trên video gốc (536/847 khung có watermark) rồi đọc lại đúng chỗ đó trên video kết quả: 17 → 0 khung còn đọc được. 107 test.
- 2026-11-14: Kiểm tra chuẩn TikTok trước khi đăng (`dubber/tiktokcheck.py`, bước `tiktok` sau bước viết tiêu đề): tệp/mã hoá/dung lượng/bitrate, độ phân giải, 9:16, fps, thời lượng, độ to (LUFS) + đỉnh âm, mở đầu đen, hình đứng yên, OCR video kết quả tìm watermark nền tảng khác / chữ Trung còn sót / chữ nằm dưới nút & caption của TikTok, nội dung đã biến đổi, caption & hashtag. Lỗi sửa được → tự tạo `output_tiktok.mp4` (1080×1920, nền mờ, −14 LUFS, < 287 MB) rồi kiểm tra lại, chép vào `final/` "(TikTok)". Red Bull 4K ngang: 72 → 100 điểm (kiểm tra + tạo bản TikTok 197 s cho video 4:44). 116 test.
- 2026-11-14: Server không chạy ngoài Claude ("No module named uvicorn"): app Claude là gói MSIX nên thư viện cài từ trong Claude (`pip --user`, Playwright Chromium) nằm trong AppData ảo của app. Thêm `fix_env.ps1` (chép ra AppData thật, không xoá gì), `run.bat` tự kiểm tra, README có mục cài đặt + bảng xử lý lỗi.
- 2026-11-14: Sửa lỗi `KeyError: 'google'` khi dùng giọng Google Dịch (bảng tên giọng ở bước tạo giọng thiếu "google"; giọng đã tạo xong, chỉ lỗi lúc ghi kết quả).
- 2026-11-14: Phong cách dịch (`dubber/styles.py`): 6 mẫu (Tự nhiên, Bựa, Kịch tính, Kể chuyện, Dễ thương, Nghiêm túc) + Tự viết + **Tự gợi ý theo từng video** (mặc định): 1 lần gọi LLM với tiêu đề + 40 câu đầu → chọn mẫu + ghi chú riêng; ưu tiên Bựa cho video giải trí, cấm bựa với nội dung nhạy cảm. Lưu `style.json`, dùng chung cho lời nói, chữ màn hình, nhân vật, tiêu đề/caption. Trang job: đổi phong cách + "Dịch lại". Thử trên video hành sa mạc Gobi: tự chọn Bựa, biệt danh "chiến thần giải ngấy". 122 test.
- 2026-11-14: Sửa bước tạo giọng bị lỗi `WinError 32` khi cùng một câu ("Xông lên!" ×10) được nhiều luồng tạo song song vào cùng một file cache: mỗi câu khác nhau chỉ tạo 1 lần, các câu lặp dùng lại. Bỏ câu LLM ghi "[Không rõ lời]", "(tiếng hét)", "…" khỏi giọng đọc và phụ đề.
- 2026-11-14: Giọng giống nhân vật (`dubber/vc.py`, tuỳ chọn 🎭): mẫu giọng 3–15 s mỗi người nói cắt từ vocals.wav → đổi giọng từng câu tiếng Việt bằng Seed-VC (vendor/seed-vc, GPL-3.0) trên GPU; mặc định model F0 'giữ dấu' (giữ đường cao độ câu tiếng Việt, dời về cao độ nhân vật). Thư viện riêng ở `vendor/pydeps` (transformers 4.57.6, tokenizers, bản rút gọn `dac.nn` của descript-audio-codec) để tránh AppData ảo của app Claude. Đo trên 5 câu, mẫu giọng là tiếng hét trận đánh: giống mẫu (ECAPA cosine) Edge −0.03 → giữ dấu 0.45 / speech 0.39; Whisper đọc đúng dấu 99% → 93% / 94%. ~3 s/câu trên RTX 3060, lần đầu tải ~1–2 GB model. 126 test.
- 2026-11-14: Đổi giọng chạy thật trên job 8857 (trận Hổ Lao Quan, mẫu giọng là tiếng hét): đổi từng câu 1 giây riêng lẻ làm mất dấu nặng (đúng dấu 52–68%). Sửa: (1) ghép các câu của cùng người nói thành đoạn ≤ 15 s rồi cắt lại, (2) giới hạn cao độ đích trong 90–260 Hz (mẫu hét 350 Hz kéo câu thành giọng the thé), (3) cổng kiểm tra bằng Whisper: câu đổi giọng mất chữ/dấu > 15% so với giọng thường thì giữ giọng thường (`gate.json`). Kết quả: đúng dấu 60–88%, giống mẫu 0.24–0.29, giữ 19/26 câu. Mẫu giọng ≤ 10 s. matplotlib của Seed-VC thay bằng stub trong `vendor/pydeps`. Video có lời thoại rõ sẽ tốt hơn nhiều (thử với mẫu nói thường: đúng dấu 93%, giống mẫu 0.45). 127 test.
- 2026-11-14: Mở một job → form bên trái hiện đúng cài đặt của job đó (mặc định + những gì job đã đổi), kèm ghi chú và nút "↺ Về mặc định để tạo video mới". Sửa lỗi kèm theo: nút "Chạy lại" gửi các lựa chọn trong form, trước đây form luôn là mặc định nên chạy lại âm thầm ghi đè cài đặt của job (vd bản thuyết minh thành lồng tiếng, tắt 🎭 đổi giọng).
- 2026-11-14: **Gỡ bỏ** tính năng giọng giống nhân vật (Seed-VC): nghe thử thấy câu tiếng Việt méo, sai lời, không dùng được. Đã xoá `dubber/vc.py`, tuỳ chọn 🎭 trên form, các khoá `voice_clone*`, `vendor/seed-vc`, `vendor/pydeps` và ~3,2 GB model (`data/models/seedvc`). Giữ lại: tạo mỗi câu lặp một lần (sửa WinError 32) và bỏ câu "[Không rõ lời]". Hướng làm lại sau này (nếu có): TTS tiếng Việt có nhân bản giọng sẵn (viXTTS / F5-TTS bản Việt) hoặc ElevenLabs clone, thay vì đổi giọng sau TTS; mẫu giọng phải là lời nói thường, không phải tiếng hét. 124 test.
- 2026-11-14: Mở đầu chấn động (`hook_intro`, mặc định bật, ô 🎬 dưới Phong cách dịch): khối dịch đầu tiên có thêm quy tắc viết lại 1–2 câu đầu thành lời mở gây tò mò (câu hỏi gây sốc / úp mở kết quả / "xem đến cuối…"), không bịa sự kiện, vẫn đọc kịp. Video có ≥ 1,5 s im lặng trước câu nói đầu: AI viết thêm 1 câu mở riêng đặt vào khoảng lặng đó (dòng `hook`, đọc bằng giọng người nói chính, bỏ qua khi nhận diện người nói). Job 4e84: "Chơi lớn đi, phòng ngủ khỏi mua giường!" → "Không mua giường, phòng ngủ vẫn đỉnh nóc?". 128 test.
- 2026-11-14: Rút gọn cho vừa thời lượng (`fit_to_time`, mặc định bật, ô ✂): sau khi tạo giọng, câu nào phải đọc > 1,15x (đo bằng thời lượng thật của giọng) được gửi lại AI kèm câu gốc, câu trước/sau và số âm tiết tối đa → viết gọn, chỉ nhận bản ngắn hơn, đọc lại riêng câu đó; tối đa 2 lượt; câu mở đầu giữ kiểu gây tò mò; câu bạn sửa tay (`edited`) không bị đụng; bản dài cũ lưu ở `tgt_full`. Job 4e84: câu > 1,25x 13 → 0, > 1,15x 23 → 0, câu khó nhất 2,00x → 1,14x, tổng âm tiết 465 → 426.
- 2026-11-14: Phụ đề cứng của lời nói khi bật 🔤 Dịch chữ trên màn hình ở chế độ lồng tiếng/thuyết minh: trước đây bị coi là chữ thường → dịch lần 2 (khác chữ với giọng đọc), vẽ nhỏ, xoá để lại vệt vàng. Giờ đoạn chữ nằm trong vùng phụ đề đã dò + trùng thời gian có người nói = phụ đề lời nói (`screentext.speech_captions`): không dịch lại, che mờ theo từng dòng như phụ đề, thay bằng phụ đề Việt của lời nói (cùng chữ với giọng, vẽ cả khi tắt "Gắn phụ đề Việt"). Tiêu đề/nhãn khác vẫn dịch tại chỗ. Chế độ "Chỉ dịch chữ" giữ nguyên (không có lời nói để dùng lại). 133 test.
- 2026-11-14: Nhạc nền bị "bóp méo": do ducking quá gắt (5:1, ngưỡng −28 dB, attack 20 ms / release 400 ms) — đo trên job 4e84: nhạc bị giảm TB 9,8 dB (tối đa 16 dB) và nhún lên xuống > 6 dB khoảng 66 lần/phút giữa các câu. Đổi sang 2:1, −30 dB, 100/1000 ms, knee 6 (`render.DUCK`; thuyết minh `DUCK_VO` 2.5:1 cũng chậm lại): giảm TB 6,4 dB, tối đa 9 dB, ~1 lần nhún/phút; giọng Việt vẫn cao hơn nhạc ~10 LU. Không phải do vỡ tiếng: đầu ra đỉnh −3,3 dBFS.
- 2026-11-14: Cuối video bị ngắt lời: câu cuối thường kết thúc sát/qua khung hình cuối (vd job d164 vượt 0,35 s, 4e84 chỉ còn 0,2 s) mà `voice.wav` và bản render đều bị cắt đúng độ dài video. Giờ giọng đọc giữ câu cuối trọn vẹn + 0,6 s nghỉ (`tts.END_TAIL`, tối đa kéo dài 6 s), render giữ nguyên khung hình cuối (`tpad`, trước lớp phụ đề) và trộn tiếng theo nguồn dài nhất (`amix duration=longest`). d164: 94,55 s → 95,50 s cả hình và tiếng.
- 2026-11-14: Kho video / Trending: nút "Xử lý video đã chọn" / "Chạy các video đã chọn" trước đây lấy cài đặt ngầm từ form tab Xử lý video (không thấy được). Giờ mở hộp thoại "Cài đặt xử lý" chứa đúng form đầy đủ (bắt đầu từ mặc định + kiểu đã chọn), chỉnh rồi bấm 🚀; cài đặt chỉ cho lần đó (muốn giữ: 💾 Lưu mặc định).
- 2026-11-14: Link YouTube treo ở "Đang lấy thông tin…": (1) `--print-json` của yt-dlp in JSON thông tin YouTube thành MỘT dòng vài MB, mà `util.run` ghép dòng bằng `bytes += 1 byte` (bậc hai) → đứng hàng phút; sửa đọc theo khối + bytearray, và yt-dlp chỉ in title/uploader/description (`--print after_move:INFO…`); (2) `--print-json` tắt tiến trình tải → thêm `--progress`; (3) YouTube cần JavaScript runtime → dùng Node.js có sẵn (`--js-runtimes node`); (4) giới hạn ≤ 1080p H.264 (trước tải 4K AV1), timeout mạng 30 s + thử lại 5 lần, watchdog dừng yt-dlp nếu 180 s không có dữ liệu. Video thử 3:28: 13 s, 1080p, 77 MB.
- 2026-11-14: ElevenLabs HTTP 409 "Multiple voice additions/deletions for the same voice…": giọng lấy từ Voice Library được ElevenLabs tự thêm vào tài khoản ở lần dùng đầu; 3 câu gửi song song cùng "thêm" một giọng → 409 → các câu đó rơi về Piper. Sửa: câu đầu tiên của mỗi giọng chạy riêng trước, phần còn lại mới song song; 409 được thử lại sau vài giây như 429/5xx. Job e68a: 4 câu Piper → ElevenLabs. 135 test.
- 2026-11-14: Checkpoint theo đầu vào (`dubber/checkpoint.py`): mỗi bước lưu khoá = dấu vân tay file vào (file nhỏ: toàn bộ nội dung; media: kích thước + 3 mẫu 256 KB) + cài đặt bước đó dùng + hash mã nguồn module; khớp và file ra còn nguyên → ♻ dùng lại. Bước được chọn "Chạy lại từ" luôn làm lại (`force_steps`), các bước sau tự kiểm tra. `translate.json` = bản dịch gốc của AI (không còn xoá tiến độ dịch rồi dịch lại), cache dịch chữ màn hình theo từng đoạn (`screentext_tr.json`, giữ cả chỗ sửa tay), OCR màn hình (`screentext_ocr.json`), câu rút gọn (`tts/shorten.json`). Đo trên bản sao job 4e84 (110 s): lần đầu 445 s → chạy lại không đổi 0,2 s; chạy lại "Tạo giọng đọc" 14 s (render 77 s được dùng lại vì voice.wav y hệt); dịch lại bắt buộc 230 s (tách nhạc, nhận dạng, phụ đề, watermark dùng lại).
- 2026-11-14: ✨ Tự nhận diện kiểu video (`dubber/analyze.py`): VAD (tỉ lệ lời nói, độ to lời/nền), Whisper ≤ 2 phút (ngôn ngữ, câu mẫu), ECAPA (số người nói), OCR 12 khung (phụ đề cứng / chữ khác; logo-tên kênh đứng yên kể cả OCR đọc lệch được gom và loại) → quy tắc + AI (thể loại, lý do) → kiểu + tuỳ chọn. Thẻ "✨ Tự nhận diện" ở form (bước mới "Nhận diện kiểu video" sau Tải video), nút "🔎 Nhận diện kiểu video" + huy hiệu gợi ý trong Kho video, kiểu "✨ Theo nhận diện từng video" khi xử lý hàng loạt (dùng lại analysis.json của kho). Thử 6 video kho: phòng ngủ → Lồng tiếng, vlog nấu ăn không lời → Chỉ dịch chữ, Metal Slug → Chỉ Sub, cá heo BBC → Lồng tiếng, cắm trại → Chỉ Sub, đậu phụ lên men → Chỉ dịch chữ; 18–37 s/video. 147 test.
- 2026-11-14: A3 — `run.bat` chạy server trong vòng lặp (thoát mã 3 = khởi động lại), `POST /api/admin/restart` (chỉ khi không có job / tải kho / nhận diện đang chạy; cần biến `DUBBER_SUPERVISED` do run.bat đặt), `GET /api/admin/status` so hash mã nguồn lúc khởi động → nút "🔄 Cập nhật" ở đầu trang khi có code mới + nút cố định trong ⚙ Cài đặt → Server. Thử: lên lại sau 2–3 s.
- 2026-11-14: A2 — video gốc lớn hơn cỡ ra (4K) → bản làm việc 1080p ngay sau khi tải (`render.make_work_copy`: NVENC CQ 16, âm thanh chép nguyên; bản gốc `source_orig.mp4`; tắt bằng `work_proxy`). A1 — `screentext.OcrCache`: OCR toàn khung lưu theo mốc thời gian + dấu vân tay video + cỡ khung (`ocr_cache.json`), dùng chung giữa bước watermark (nay 1280 px như chữ màn hình) và chữ trên màn hình. Đo trên bản sao job 4K 8857 (8:19): watermark 166 → 99 s, chữ màn hình 191 → 63 s, render 301 → 231 s, dò phụ đề 16 → 13 s.
- 2026-11-14: A4 — preview chỉ render khi "Dừng sau preview, chờ duyệt" (tiết kiệm 15–20 s/job). A5 — phong cách "✨ Tự gợi ý" được AI chọn ngay trong lượt dịch đầu (`styles.pick_rule/record`, bớt 1 lượt gọi/job); khoá checkpoint dịch dùng `styles._key` thay vì văn bản phong cách; đếm `job.ai_calls`, ghi "AI: N lượt gọi" cuối nhật ký.
- 2026-11-14: B2 — 🤖 Tự chạy ban đêm (`dubber/autopilot.py`, tab 🤖): khung giờ (có thể qua nửa đêm), tối đa N video/ngày, nguồn Kho video (chưa xử lý) + Trending (≥ like, ≤ tuổi, độ dài, chủ đề), bỏ trùng theo link + tiêu đề + đã từng chọn, tuỳ chọn làm mới danh sách Trending mỗi đêm (cùng crawler, không vượt captcha), kiểu mặc định ✨ tự nhận diện. Mặc định tắt; "👀 Xem trước" (dry-run) và "▶ Chạy ngay". 155 test.
- 2026-11-14: B6 — biến đổi nội dung (mặc định tắt, Tuỳ chọn nâng cao): chỉnh màu nhẹ (`transform_grade`: tương phản 1.06, độ rực 1.12), phóng toàn khung (`transform_zoom` 3–5 %, áp lên cả phụ đề/chữ vẽ lại/watermark của bạn nên không lệch), màn kết (`outro_seconds` 2–3 s: giữ khung cuối, làm mờ + tối, "Theo dõi" + từng tên kênh một dòng + dòng phụ `outro_text`; tiếng kéo dài và mờ dần, chế độ giữ âm gốc thì mã hoá lại để có đuôi). Thử trên bản sao job 4e84: 109,8 s → 112,7 s (hình & tiếng khớp).
- 2026-11-14: B1 (TikTok) — `dubber/tiktok_api.py`: đăng nhập OAuth của chính ứng dụng nhà phát triển của bạn (Login Kit Desktop, PKCE SHA-256 hex, redirect `http://127.0.0.1:8765/api/tiktok/callback/`, kiểm tra `state` một lần), token lưu trên máy (`data/tiktok_token.json`, không gửi ra trình duyệt; Client Secret che như các API key), tự gia hạn (refresh token xoay vòng). Đăng: hộp thư (`video.upload`, `/v2/post/publish/inbox/video/init/`) hoặc thẳng (`video.publish`, `creator_info` → mức riêng tư hợp lệ, `is_aigc`), tải lên theo phần 10 MB với `Content-Range`, hỏi trạng thái mỗi 10 s. UI: ⚙ Cài đặt → Đăng TikTok; thẻ Nội dung đăng → 📤 Đăng lên TikTok (hỏi xác nhận kèm caption). Chưa thử với tài khoản thật (cần Client Key của bạn); 7 test với API giả lập. 163 test.
- 2026-11-14: Video autopilot thiếu phụ đề Việt: job 06b2/8790 được đưa vào hàng đợi lần 2 kèm toàn bộ form (khi đó "Gắn phụ đề Việt" đang tắt). Sửa: autopilot luôn đặt `burn_subtitles` (cài đặt `autopilot_burn_subtitles`, mặc định bật); "✨ Tự nhận diện" bật phụ đề cho lồng tiếng/thuyết minh/chỉ sub; chế độ Chỉ Sub mà tắt phụ đề → vẫn gắn (kèm cảnh báo). Render lại 06b2 + 1729. 166 test.
- 2026-11-14: Lọc câu Whisper "bịa" trên nhạc/mưa/tiếng ồn (`asr.is_hallucination`): mẫu câu ghi công phụ đề / kêu gọi đăng ký quen thuộc ("中文字幕——YK", "字幕志愿者 李宗盛", "请不吝点赞 订阅…", "Thank you for watching", "ご視聴ありがとう…") + đoạn mà bộ dò giọng (VAD) nghe < 20 % có tiếng người. Job 1729 (cắm trại trong mưa, không lời): 11 → 2 câu (bỏ 9 đoạn bịa ở đúng mốc 30 s của Whisper). 168 test.
- 2026-11-15: Tối ưu video dài (> 30 phút), đo trên phim 38 phút (job 3aad, 89 phút xử lý): (C) render ra luôn bản TikTok 9:16 trong cùng lượt (một lần giải mã/lọc, hai luồng NVENC; `tiktokcheck.premade`), video dọc chỉ lệch âm lượng thì chép nguyên hình, kiểm tra file > 10 phút chỉ giải mã keyframe; (B) dò phụ đề bỏ OCR khung không có nét chữ sáng + dùng lại dòng đứng yên tới 2 s, video > 20 phút lấy 3 mẫu/s (đoạn 10 phút: 395 → 217 s, vùng che khớp 99,5 %); (A) dò phụ đề + watermark chạy song song với dịch/nhận người nói/tạo giọng (bắt đầu sau tách nhạc — chạy cùng tách nhạc làm BS-RoFormer chậm 55 %); (D) dịch song song 3 khối (khối 1 riêng để chọn phong cách + hook); (E) gợi ý VieNeu cho video > 20 phút; `tts.mix_voice` trộn theo nhóm 150 câu (lỗi WinError 206 với 832 câu). Đoạn 10 phút: ~23 → 18,2 phút.
- 2026-11-15: Nhận diện người nói mới (`speakers.py`): cao độ từng câu (pYIN) chia nam/nữ trước (rõ khi < 160 Hz / > 215 Hz), câu mơ hồ so giọng với tâm nhóm nam/nữ; dấu giọng CAM++ (3D-Speaker, ~200k người nói tiếng Trung; sherpa-onnx trong `vendor/pydeps`, model `data/models/diar`), gom người nói TRONG từng giới (ngưỡng cosine 0,7). Job 6cf9: 15 → 4 người nói, giới tính từng câu khớp lời thoại. Thiếu model → ECAPA như cũ. 188 test.
- 2026-11-15: Bước **Biên kịch** (`dubber/content.py`, bước `content` sau "Dịch chữ trên màn hình", tắt mặc định `content_enabled`; đặc tả `docs/noi-dung/prompt-ky-thuat-content-pipeline.md`). Không AI: khoảng lặng ≥ 3 s, chuyển cảnh (PySceneDetect, dự phòng ffmpeg), khối chữ nhỏ/dài (nơi giấu twist), phân loại `story_sparse / story_dense / narrated / short_clip` theo ngưỡng trong cài đặt. AI xem phim: keyframe (mỗi cảnh, mỗi khoảng lặng, mỗi khối chữ) ghép lưới 3×3 có mốc giờ, MỘT lần gọi `codex exec -i` (Codex CLI 0.168 nhận ảnh; Claude CLI qua `--input-format stream-json`, Ollama `qwen2.5vl:7b`), cache theo video. AI viết `content.json` (hook, thanh tiêu đề, lời kể, cắt, chữ giữ chân, đọc khối chữ, cái kết + câu loop, ảnh bìa, caption/hashtag/bình luận ghim/nhãn AI/nguồn, clip mồi), `validate()` sửa: cắt chỉ trong khoảng lặng (không bao giờ cắt thoại hay cảnh hook), lời kể không đè thoại, một câu chỉ đọc một lần, bỏ #fyp/#xuhuong/#viral. Render: lời kể là track riêng (`content_vo.wav`, VieNeu "Thiền Tâm Đức", cache từng câu) trộn với tiếng gốc hạ theo `duck_original`; cắt bằng `select/aselect` trong cùng lượt sau khi đã dựng mọi thứ theo thời gian gốc; chữ (hook, phụ đề lời kể, phụ đề lớn khi đọc twist, chữ giữ chân, câu kết, thanh tiêu đề) vẽ SAU khi cắt (`CutMap`), riêng cho bản chính và bản 9:16 (dải trên/dưới của bản TikTok); thẻ kết thay màn "Theo dõi". Đầu ra: `post.json` từ kịch bản, `cover.jpg`, `teaser_N.mp4`. Tab 🎬 Biên kịch: sửa từng mục, xem khung hình, nghe thử giọng kể, ✍ AI viết lại (không xem lại phim), duyệt (job dừng chờ duyệt khi `content_review`; Tự chạy không dừng, ghi "chưa được duyệt"). Job 6cf9: hook "Tôi có xe sang, nhưng mất người từng thương", lấp 0:14–0:31, đọc twist ở 4:37, câu loop về cảnh mở, 7 đoạn cắt (−36,9 s).

## Kiến trúc: bước Biên kịch (2026-11-15)

```
content.py   timeline() → keyframe_times() → make_grids() → watch() [AI, cache content_vision.json]
             → write() [AI] → validate() → content.json
             render_parts(): synth_vo() (giọng kể, cache tts/cache) → content_vo.wav + duck windows,
                             items (overlay theo thời gian gốc) → rows(layout, CutMap) sau khi cắt
             extras(): post.json, cover.jpg, teaser_N.mp4
render.py    cparts = content.render_parts(...) → ending card, mix + duck, select/aselect, content_main.ass /
             content_tt.ass (bản 9:16)
providers/   Provider.vision; complete(..., images=[...]); orchestrator.run(order=[...])
```
- 2026-11-15: Biên kịch — sửa lệch hình/tiếng: cắt bằng `select/aselect` thì `aselect` (ffmpeg 8.0) không bỏ khung tiếng nào → tiếng chỉ bị cụt ở cuối, mỗi chỗ cắt đẩy tiếng trễ thêm (tới ~39 s). Giờ cắt bằng `trim/atrim` từng đoạn giữ lại + `concat` hình và tiếng cùng lúc (`content.cut_graph`); test hồi quy chạy ffmpeg thật (chớp hình + tiếng bíp); đo job 6cf9: giọng Việt đúng chỗ ở mọi đoạn (lệch 0,01 s) cả bản ngang và bản TikTok.
- 2026-11-15: Biên kịch — chất lượng lời kể: luật + ví dụ tốt/xấu trong prompt (xưng hô nhất quán `persona.me/her/him`, lời kể khớp cảnh tại mốc của nó, tone trầm chỉ nói điều hình không nói được, câu loop có cảm xúc nối về cảnh mở, hook úp mở); `quality_check()` kiểm tra sau khi AI trả về (xưng hô lẫn, "ngày ấy" trên cảnh hiện tại / "giờ" trên hồi ức theo `era` của từng khung, câu cảm xúc trừu tượng, loop tả hình, hook tóm tắt) → AI viết lại 1 lượt.
- 2026-11-15: Biên kịch — thứ tự bước: AI xem phim chạy ngay sau khi tải (luồng riêng, chỉ cần video: khung theo chuyển cảnh + lấy mẫu đều, mỗi khung có `era` hiện tại/hồi ức), song song tách nhạc/nhận dạng; bước `content` dời lên SAU nhận dạng giọng nói, TRƯỚC dịch: nhận người nói trên câu nhận dạng (`content_speakers.json`, bước Nhận người nói sau đó chỉ gán lại theo thời gian), OCR chữ màn hình chạy trong luồng hình sau watermark (cùng cache), AI viết kịch bản đọc thẳng thoại/chữ tiếng Trung và tự chọn loại video. Bước dịch nhận giọng văn từ kịch bản (`content.translation_voice`: tone, xưng hô; video có thuyết minh → lời thuyết minh dịch theo ngôi kể/tone) và nằm trong khoá cache dịch. Chờ duyệt → chạy tiếp từ bước Dịch. Tắt Biên kịch: thứ tự và kết quả y như cũ.
