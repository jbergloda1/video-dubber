"""The workflow: download → extract → separate → transcribe → translate → tts → detect subs → preview → render."""
import json
import re
import shutil
from pathlib import Path

from . import asr, downloader, render, screentext, separate, speakers, subdetect, translate, tts, util, watermark
from .jobs import STEP_IDS

MODE_NAMES = {"dub": "Lồng tiếng Việt + phụ đề Việt", "sub": "Phụ đề Việt, giữ audio gốc",
              "voiceover": "Thuyết minh: giữ audio + giọng gốc (nhỏ), giọng Việt to",
              "text": "Giữ video & âm thanh gốc, chỉ dịch chữ trên màn hình",
           "auto": "✨ Tự nhận diện kiểu video rồi chọn cách làm"}
# steps that only exist to understand/replace the SPEECH — not needed when only on-screen text is translated
SPEECH_STEPS = ("extract", "separate", "transcribe", "translate", "speakers", "tts", "detect_subs")
VOICED = ("dub", "voiceover")      # modes that generate a Vietnamese voice track
OUTPUTS = {"dub": ("output.mp4", "output"), "sub": ("output_sub.mp4", "output_sub"),
           "voiceover": ("output_vo.mp4", "output_vo"),
           "text": ("output_text.mp4", "output_text")}


def _rd(p):
    return json.loads(p.read_text(encoding="utf-8"))


def _wr(p, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def run(job) -> str:
    s = job.settings()
    d = job.dir
    job.ai_calls = 0
    mode = s["output_mode"]
    start_idx = STEP_IDS.index(job.start_from)
    job.log("Chế độ: " + MODE_NAMES.get(mode, mode))
    text_only = mode == "text"
    if text_only:
        # original picture + original audio; only the on-screen text is translated and redrawn
        s = dict(s, screen_text=True, burn_subtitles=False)

    keep_text = job.keep_text and (d / "asr.json").exists() and (d / "lines.json").exists()
    job.keep_text = False
    if keep_text:
        for sid in ("transcribe", "translate"):
            job.step(sid, status="done", progress=1.0, detail="Giữ kết quả lần trước")

    def should(sid):
        if keep_text and sid in ("transcribe", "translate"):
            return False
        if text_only and sid in SPEECH_STEPS:
            if STEP_IDS.index(sid) >= start_idx:
                skip(sid, "Không cần (chỉ dịch chữ trên màn hình)")
            return False
        return STEP_IDS.index(sid) >= start_idx

    def begin(sid, detail=""):
        job.check_cancel()
        job.step(sid, status="running", progress=0.0, detail=detail)

    def end(sid, detail=None):
        job.step(sid, status="done", progress=1.0, detail=detail)

    def skip(sid, why):
        job.step(sid, status="skipped", progress=1.0, detail=why)

    # checkpoints: a step whose inputs did not change is reused instead of redone (see checkpoint.py).
    # The step you re-run from is always redone ("force"); everything after it is re-checked.
    from . import checkpoint as ck
    from .jobs import STEPS as _STEPS
    cp = ck.Store(d, force=getattr(job, "force_steps", None) or ())
    job.force_steps = set()
    step_names = dict(_STEPS)

    def reuse(sid, k, outputs=()):
        rec = cp.hit(sid, k, outputs)
        if rec is not None:
            job.step(sid, status="done", progress=1.0, detail="♻ " + (rec.get("detail") or "dùng lại kết quả trước"))
            job.log(f"♻ {step_names[sid]}: đầu vào không đổi → dùng lại kết quả lần trước")
        return rec

    # 1. download -------------------------------------------------------------------
    if should("download"):
        begin("download")
        if job.url.startswith("upload:"):
            info = downloader.from_upload(job, d, s)          # a file you imported from your computer
        elif job.url.startswith("library:"):
            from . import library                              # already downloaded into the library
            info = library.to_job_dir(job.url.split(":", 1)[1], d)
        else:
            info = downloader.download(job, job.url, d / "source.mp4", s)
        info.setdefault("webpage_url", downloader.extract_url(job.url))
        job.title = re.sub(r"\s*#\S+", "", info.get("title") or "").strip() or info.get("title", "")[:80]
        _wr(d / "meta.json", info)
        # 4K source → 1080p working copy. Decoding 4K is the slow part (hardware decoder ≈ 4× real time), so the
        # copy is made IN PARALLEL with the audio steps (they don't need the picture); the picture steps wait for it
        # (video_ready). The original is kept as source_orig.mp4 and is what the audio steps read.
        W0, H0 = util.video_size(d / "source.mp4")
        dur0 = util.duration(d / "source.mp4")
        short = int(s.get("output_short_side") or 1080)
        if s.get("work_proxy", True) and min(W0, H0) > short * 1.15:
            (d / "source.mp4").replace(d / "source_orig.mp4")
            job.log(f"Video gốc {W0}x{H0} → tạo bản làm việc {short}p song song với các bước âm thanh")
        elif not (d / "source_view.mp4").exists():
            job.step("download", detail="Tạo bản xem được trên trình duyệt…")
            render.make_view_copy(d / "source.mp4", d / "source_view.mp4", job, dur0)
        job.add_file("source", "source.mp4")
        end("download", f"{W0}x{H0}, {dur0:.0f}s — {info.get('uploader', '')}")

    orig = d / "source_orig.mp4"
    audio_src = orig if orig.exists() else d / "source.mp4"      # audio steps: always the original file
    proxy = {"t": None, "err": None}
    if orig.exists() and not (d / "source.mp4").exists():       # working copy still to make (new job / interrupted)
        import threading

        class _ProxyJob:          # own ffmpeg process handle: runs beside the job's own ffmpeg calls
            proc = None

            def log(self, m, level="info"):
                job.log(m, "debug")

            def step(self, *a, **k):
                pass

            def check_cancel(self):
                job.check_cancel()

        def make_proxy():
            try:
                short_ = int(s.get("output_short_side") or 1080)
                render.make_work_copy(_ProxyJob(), orig, d / "source_work.mp4", short_, util.duration(orig))
                (d / "source_work.mp4").replace(d / "source.mp4")
                if not (d / "source_view.mp4").exists():
                    render.make_view_copy(d / "source.mp4", d / "source_view.mp4")
            except Exception as e:
                proxy["err"] = e
        proxy["t"] = threading.Thread(target=make_proxy, daemon=True, name="work-copy")
        proxy["t"].start()

    def video_ready():
        """The picture steps start here: wait for the 1080p working copy if it is still being made."""
        if proxy["t"] is not None:
            if proxy["t"].is_alive():
                job.log("Chờ bản làm việc 1080p xong (đang tạo song song)…")
                while proxy["t"].is_alive():
                    job.check_cancel()
                    proxy["t"].join(2)
            proxy["t"] = None
            if proxy["err"]:
                raise RuntimeError(f"Không tạo được bản làm việc 1080p: {str(proxy['err']).splitlines()[0][:200]}")
            W1, H1 = util.video_size(d / "source.mp4")
            job.log(f"Bản làm việc {W1}x{H1} sẵn sàng (bản gốc giữ ở source_orig.mp4)")

    total = util.duration(audio_src)
    job.video_duration = total
    meta = _rd(d / "meta.json") if (d / "meta.json").exists() else {}

    # Biên kịch: the AI watches the film (cloud call, no GPU) while the sound is separated and transcribed
    content_on = bool(s.get("content_enabled")) and should("content")
    watch = {"t": None, "err": None, "notes": None}
    if content_on:
        def _watch():
            try:
                from . import content as _c
                watch["notes"] = _c.watch(job, d, s, total)
            except BaseException as e:          # reported when the content step needs the notes
                watch["err"] = e
        import threading as _thw
        watch["t"] = _thw.Thread(target=_watch, daemon=True, name="content-watch")
        watch["t"].start()

    # 1b. "✨ Tự nhận diện": recognise the kind of video and pick the output mode + options ----------------
    if should("analyze"):
        if mode != "auto":
            skip("analyze", "Đã chọn kiểu: " + MODE_NAMES.get(mode, mode))
        else:
            from . import analyze
            res = None
            if job.url.startswith("library:"):              # already recognised in the video library
                from . import library
                res = analyze.load(library.DIR / job.url.split(":", 1)[1])
                if res:
                    _wr(d / analyze.FILE, res)
            k_an = ck.key("analyze", ck.fp(audio_src), ck.pick(s, "target_lang", "source_lang"),
                          ck.code("analyze"))
            if res is None and reuse("analyze", k_an, [analyze.FILE]):
                res = analyze.load(d)
            if res is None:
                begin("analyze", "Nghe lời nói + đọc chữ trên hình…")
                try:
                    res = analyze.analyze(job, audio_src, s, meta.get("title", ""), out_dir=d,
                                          progress=lambda f, m: job.step("analyze", progress=f, detail=m))
                    cp.save("analyze", k_an, [analyze.FILE], analyze.summary(res))
                except Exception as e:
                    from .jobs import Cancelled
                    if isinstance(e, Cancelled):
                        raise
                    job.log(f"Không nhận diện được kiểu video ({str(e).splitlines()[0][:150]}) → lồng tiếng", "warn")
                    res = {"features": {}, "rec": {"mode": "dub", "options": {}, "reason": "Nhận diện lỗi — mặc định lồng tiếng"}}
            rec = res["rec"]
            job.options.update({"output_mode": rec["mode"], **rec.get("options", {})})
            job.log(f"✨ Nhận diện: {analyze.summary(res)}" + (f" — {rec['genre']}" if rec.get("genre") else ""))
            job.log(f"   Lý do: {rec['reason']}")
            end("analyze", analyze.summary(res))
            s = job.settings()
            mode = s["output_mode"]
            text_only = mode == "text"
            if text_only:
                s = dict(s, screen_text=True, burn_subtitles=False)
            job.log("Chế độ: " + MODE_NAMES.get(mode, mode))
    if mode == "auto":          # re-run that skipped the step: fall back to the saved recommendation or dubbing
        from . import analyze
        prev = analyze.load(d)
        mode = prev["rec"]["mode"] if prev else "dub"
        job.options["output_mode"] = mode
        s = job.settings()
        text_only = mode == "text"
        if text_only:
            s = dict(s, screen_text=True, burn_subtitles=False)

    # A. the picture steps (burned-in subtitles + platform watermarks) need only the video: they run in a thread
    # beside translate → speakers → tts (network / light GPU). They start AFTER separate + transcribe: started
    # earlier, the GPU OCR slowed BS-RoFormer separation down by ~55 % (measured on a 10-min cut).
    import threading as _thp
    pic = {"t": None, "err": None, "ocr": _thp.Event()}

    def screen_ocr():
        """OCR of the on-screen text (same cache/key as the screen_text step) — the Biên kịch writer reads it."""
        k_ocr = ck.key("screen_ocr", ck.fp(d / "source.mp4"),
                       ck.pick(s, "screen_text_interval", "screen_text_min_height"), ck.code("screentext"))
        if not cp.hit("screen_ocr", k_ocr, ["screentext_ocr.json"]):
            ev = screentext.detect(job, d / "source.mp4", total, s)
            _wr(d / "screentext_ocr.json", ev)
            cp.save("screen_ocr", k_ocr, ["screentext_ocr.json"])

    def picture_steps(lang):
        video_ready()
        # 7. detect burned-in subs ----------------------------------------------------------
        if should("detect_subs"):
            if s["hide_original_subs"] != "auto":
                skip("detect_subs", "Chế độ che: " + s["hide_original_subs"])
                _wr(d / "subband.json", None)
            else:
                k_ds = ck.key("detect_subs", ck.fp(d / "source.mp4"), lang, ck.code("subdetect"))
                hit = (d / "subband.json").exists() and reuse("detect_subs", k_ds, [subdetect.EVENTS_FILE])
            if should("detect_subs") and s["hide_original_subs"] == "auto" and not hit:
                begin("detect_subs", "OCR")
                band = subdetect.detect(job, d / "source.mp4", total, lang)
                _wr(d / "subband.json", band)
                ev = []
                if band:
                    # follow every subtitle line (its width and timing) instead of covering a fixed band
                    ev = subdetect.track_lines(job, d / "source.mp4", total, band, lang)
                    job.log(f"Theo dõi được {len(ev)} dòng phụ đề gốc (che bám theo từng dòng)")
                _wr(d / subdetect.EVENTS_FILE, ev)
                det = "Không có phụ đề cứng" if not band else f"{len(ev)} dòng, y {band['y0']:.0%}–{band['y1']:.0%}"
                end("detect_subs", det)
                cp.save("detect_subs", k_ds, [subdetect.EVENTS_FILE], det)

        # 8a. original platform watermarks (抖音号 / TikTok @user + logo) -> removed at render ------------
        if should("watermark"):
            if (s.get("wm_remove") or "auto") == "off":
                skip("watermark", "Đang tắt")
            else:
                # (watermarks.json is not fingerprinted: your edits in the watermark editor are kept on reuse)
                k_wm = ck.key("watermark", ck.fp(d / "source.mp4"), ck.code("watermark"))
                if (d / watermark.FILE).exists() and reuse("watermark", k_wm):
                    job.add_file("watermarks", watermark.FILE)
                else:
                    begin("watermark", "OCR + cạnh cố định")
                    manual = [it for it in watermark.load(d) if not it.get("auto")]     # boxes you drew stay
                    found = watermark.detect(job, d / "source.mp4", total, s, d)
                    watermark.save(d, manual + found)
                    job.add_file("watermarks", watermark.FILE)
                    det = ((f"{len(found)} watermark: " + ", ".join(it["src"] for it in found[:3])) if found
                           else "Không thấy watermark" + (f" (giữ {len(manual)} vùng bạn vẽ)" if manual else ""))
                    end("watermark", det)
                    cp.save("watermark", k_wm, [], det)
        # The on-screen text is OCRed here too, beside translate → tts (it used to run after the voice was made,
        # ~35 s on the critical path). Biên kịch reads it before the translation; the screen_text step later finds
        # it in the cache and only translates.
        if s.get("screen_text") and should("screen_text"):
            try:
                job.step("screen_text", detail="OCR (song song với phần âm thanh)…")
                screen_ocr()
            finally:
                pic["ocr"].set()
        pic["ocr"].set()

    def start_picture(lang):
        if text_only or pic["t"] is not None or not lang or not (should("detect_subs") or should("watermark")):
            return

        def run():
            try:
                picture_steps(lang)
            except BaseException as e:      # re-raised in the main flow when it needs the results
                pic["err"] = e
        import threading as _th
        pic["t"] = _th.Thread(target=run, daemon=True, name="picture-steps")
        pic["t"].start()
        job.log("Dò phụ đề gốc + watermark chạy song song với phần âm thanh")

    # 2. extract audio -----------------------------------------------------------------
    if should("extract"):
        k_ex = ck.key("extract", ck.fp(audio_src))
        if not reuse("extract", k_ex, ["audio.wav", "audio16k.wav"]):
            begin("extract")
            util.ffmpeg(job, ["-i", audio_src.name, "-vn", "-ac", "2", "-ar", "44100", "audio.wav"],
                        duration=total, step="extract", cwd=d, scale=(0, 0.6))
            util.ffmpeg(job, ["-i", "audio.wav", "-ac", "1", "-ar", "16000", "audio16k.wav"],
                        duration=total, step="extract", cwd=d, scale=(0.6, 1))
            end("extract")
            cp.save("extract", k_ex, ["audio.wav", "audio16k.wav"])

    # 3. separate voice / background (BS-RoFormer or Demucs, GPU) ---------------------------------------------------
    if should("separate"):
        if mode not in VOICED:
            skip("separate", "Không cần (giữ audio gốc)")
        elif not s["separate_music"]:
            skip("separate", "Đã tắt trong cài đặt")
        else:
            k_sep = ck.key("separate", ck.fp(audio_src), ck.pick(s, "separate_model"), ck.code("separate"))
            if reuse("separate", k_sep, ["vocals.wav", "no_vocals.wav"]):
                job.add_file("music", "no_vocals.wav")
            else:
                begin("separate")
                asr.unload()  # free VRAM for the separator on a 12GB card
                used = separate.separate(job, d, s)
                job.add_file("music", "no_vocals.wav")
                end("separate", used)
                cp.save("separate", k_sep, ["vocals.wav", "no_vocals.wav"], used)

    # 4. transcribe -------------------------------------------------------------------
    if should("transcribe"):
        k_asr = ck.key("transcribe", ck.fp(d / "audio16k.wav"), ck.pick(s, "whisper_model", "source_lang"),
                       ck.code("asr"))
        if not reuse("transcribe", k_asr, ["asr.json"]):
            begin("transcribe", f"Whisper {s['whisper_model']}")
            data = asr.transcribe(job, d / "audio16k.wav", total, s)
            _wr(d / "asr.json", data)
            if not data["lines"]:
                # nothing is said in this video: dubbing / subtitling it makes no sense, but its on-screen text can
                # still be translated — carry on in that mode instead of failing the job
                job.log("Video không có lời nói → chuyển sang “Chỉ dịch chữ trên màn hình” (giữ hình và âm thanh gốc)", "warn")
                end("transcribe", "Không có lời nói → chỉ dịch chữ trên màn hình")
                job.options["output_mode"] = mode = "text"
                text_only = True
                s = dict(job.settings(), screen_text=True, burn_subtitles=False)
                for sid in SPEECH_STEPS:
                    if STEP_IDS.index(sid) > STEP_IDS.index("transcribe"):
                        skip(sid, "Không cần (video không có lời nói)")
            else:
                det = f"{len(data['lines'])} câu, ngôn ngữ: {data['language']}"
                end("transcribe", det)
                cp.save("transcribe", k_asr, ["asr.json"], det)
    asr_data = _rd(d / "asr.json") if (d / "asr.json").exists() else None

    start_picture(asr_data["language"] if asr_data else None)

    # 4b. "Biên kịch": the content layer (hook, narration, cuts, ending, post) — content.py ---------------------
    # before the translation: the writer reads the Chinese dialogue + on-screen text, the translation then follows
    # the plan's persona / tone / forms of address
    if should("content"):
        from . import content
        if not s.get("content_enabled"):
            skip("content", "Đang tắt (⚙ bật “Biên kịch” trong form)")
        else:
            k_ct = ck.key("content", ck.fp(d / "asr.json"), ck.fp(d / "screentext_ocr.json"), ck.fp(d / "source.mp4"),
                          ck.pick(s, "content_persona", "content_tone", "content_vision_provider",
                                  "content_writer_provider", "content_codex_model", "screen_text"), ck.code("content"))
            plan = content.load(d)
            forced = "content" in (cp.force or ())
            if plan and plan.get("edited") and not forced:
                job.step("content", status="done", progress=1.0, detail="✏ Kịch bản bạn đã sửa (giữ nguyên)")
                job.log("Biên kịch: giữ kịch bản bạn đã sửa — bấm “Viết lại” trong tab Biên kịch để AI viết mới")
            elif not (plan and reuse("content", k_ct, [content.FILE])):
                begin("content", "Chờ AI xem phim + đọc chữ trên màn hình…")
                if not s.get("screen_text"):
                    job.log("Biên kịch: “Dịch chữ trên màn hình” đang tắt — AI không thấy được chữ nhỏ (thường chứa twist)", "warn")
                # who speaks each transcript line (the speakers step later only maps these onto the translated lines)
                asr_lines = [dict(l) for l in (asr_data or {}).get("lines", [])]
                if asr_lines and mode in VOICED and s["voice_gender_mode"] == "auto":
                    src_v = d / "vocals.wav" if (d / "vocals.wav").exists() else d / "audio16k.wav"
                    asr.unload()
                    asr_lines, _sum = speakers.detect(job, src_v, asr_lines, s["whisper_device"])
                    _wr(d / "content_speakers.json", [[l["start"], l["end"], l["speaker"], l["gender"]] for l in asr_lines])
                for th, what in ((watch.get("t"), "AI xem phim"),):
                    while th is not None and th.is_alive():
                        job.check_cancel()
                        th.join(2)
                if watch.get("err"):
                    raise watch["err"]
                if content_on and pic["t"] is not None and s.get("screen_text"):
                    while not pic["ocr"].wait(2):
                        job.check_cancel()
                        if not pic["t"].is_alive():
                            break
                    if pic["err"]:
                        raise pic["err"]
                elif s.get("screen_text"):
                    screen_ocr()
                notes = watch.get("notes") or content.watch(job, d, s, total)
                ev = _rd(d / "screentext_ocr.json") if (d / "screentext_ocr.json").exists() else []
                ev = [e for e in ev if not watermark.covers(e, watermark.load(d))]
                tl = content.timeline(job, d, s, total, lines=asr_lines, events=ev)
                job.log(f"Biên kịch: {len(tl['lines'])} câu thoại, {len(tl['silences'])} khoảng lặng ≥3s, "
                        f"{len(tl['scenes'])} cảnh, {len(tl['small_text'])} khối chữ nhỏ/dài")
                (d / "content_timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
                job.step("content", progress=0.6, detail="AI đang viết kịch bản…")
                plan = content.write(job, d, s, tl, notes, s.get("content_persona"), s.get("content_tone"))
                content.save(d, plan)
                det = (f"{plan['video_type']}: hook “{plan['hook']['overlay'][:40]}”, {len(plan['narration'])} câu kể, "
                       f"{len([c for c in plan['cuts'] if c['enabled']])} chỗ cắt")
                end("content", det)
                cp.save("content", k_ct, [content.FILE], det)
            job.add_file("content", content.FILE)
            plan = content.load(d)
            if plan and not plan.get("approved") and not s.get("content_review", True):
                job.log("Biên kịch: kịch bản CHƯA ĐƯỢC DUYỆT (tự chạy) — xem lại trong tab 🎬 Biên kịch trước khi đăng", "warn")
            if s.get("content_review", True) and plan and not plan.get("approved") \
                    and STEP_IDS.index(job.start_from) <= STEP_IDS.index("content"):
                job.step("translate", status="waiting", detail="Chờ bạn duyệt kịch bản (tab 🎬 Biên kịch)")
                job.resume_step = "translate"
                return "awaiting_approval"

    # 5. translate -------------------------------------------------------------------
    if should("translate"):
        same_lang = asr_data["language"] == s["target_lang"]
        from . import styles
        pick_dir = None
        if not same_lang:
            if (s.get("translate_style_preset") or "auto") == "auto" and not styles.cached(d, s):
                pick_dir = d          # the first translation call picks the style itself (one AI call, not two)
            else:
                s = styles.resolve(job, d, s, meta.get("title", ""), [l["text"] for l in asr_data["lines"][:40]])
        # translate.json = the translation as the AI wrote it; lines.json = the working copy (speakers, voice
        # timing, your edits). Same transcript + same style/settings/prompt → the translation is reused.
        voice_extra = ""
        if s.get("content_enabled"):
            from . import content as _cv
            voice_extra = _cv.translation_voice(_cv.load(d))
            if voice_extra and not same_lang:
                # the dialogue in the same voice as the Biên kịch narration (persona / tone / forms of address)
                s = dict(s, translate_style=((s.get("translate_style") or "") + "\n" + voice_extra).strip())
        k_tr = ck.key("translate", ck.fp(d / "asr.json"), ck.pick(s, "target_lang", "hook_intro"),
                      "" if same_lang else styles._key(s), meta.get("title", ""), voice_extra,
                      ck.code("translate", "styles"))
        if reuse("translate", k_tr, ["translate.json"]):
            if not (d / "lines.json").exists():
                shutil.copy(d / "translate.json", d / "lines.json")
            job.add_file("script", "lines.json")
        else:
            begin("translate")
            if "translate" in cp.force:            # you asked for a NEW translation: don't resume old chunks
                (d / "translate_progress.json").unlink(missing_ok=True)
            if same_lang:
                job.log("Video đã là ngôn ngữ đích → giữ nguyên lời gốc")
                lines = [{"src": l["text"], "tgt": l["text"], "start": l["start"], "end": l["end"]}
                         for l in asr_data["lines"]]
                matched = 1.0
            else:
                lines, matched = translate.translate(job, asr_data, s, meta.get("title", ""),
                                                     progress_path=d / "translate_progress.json", style_dir=pick_dir)
            lines = translate.drop_placeholders(job, lines)
            _wr(d / "lines.json", lines)
            _wr(d / "translate.json", lines)
            job.add_file("script", "lines.json")
            det = f"{len(lines)} câu, khớp thời gian {matched:.0%}"
            end("translate", det)
            cp.save("translate", k_tr, ["translate.json"], det)
    lines = _rd(d / "lines.json") if (d / "lines.json").exists() and not text_only else []
    if (d / "style.json").exists():         # later steps (character lines, screen text) use the same style
        from . import styles
        s = styles.resolve(job, d, s, meta.get("title", ""), [l.get("src", "") for l in lines[:40]])

    # 6. speakers + gender ------------------------------------------------------------
    if should("speakers"):
        if mode not in VOICED:
            skip("speakers", "Không cần (giữ audio gốc)")
        elif s["voice_gender_mode"] != "auto" and not s.get("narrator_remove"):
            for l in lines:
                l["gender"] = s["voice_gender_mode"]
                l.setdefault("speaker", "S1")
            _wr(d / "lines.json", lines)
            skip("speakers", "Dùng 1 giọng " + ("nam" if s["voice_gender_mode"] == "male" else "nữ"))
        else:
            src = d / "vocals.wav" if (d / "vocals.wav").exists() else d / "audio16k.wav"
            k_sp = ck.key("speakers", ck.fp(src), [(l.get("src"), l.get("start"), l.get("end")) for l in lines],
                          ck.pick(s, "voice_gender_mode"), ck.code("speakers"))
            rec = None if s.get("narrator_remove") else reuse("speakers", k_sp)
            if rec:
                # re-apply the labels (lines.json may have been rebuilt by a re-translation of the same speech)
                lab = {(a, b): (spk, g) for a, b, spk, g in rec.get("data") or []}
                for l in lines:
                    if (l.get("start"), l.get("end")) in lab:
                        l["speaker"], l["gender"] = lab[(l["start"], l["end"])]
                _wr(d / "lines.json", lines)
        if should("speakers") and mode in VOICED and not (s["voice_gender_mode"] != "auto" and not s.get("narrator_remove")) \
                and not rec:
            begin("speakers", "ECAPA + cao độ")
            asr.unload()
            src = d / "vocals.wav" if (d / "vocals.wav").exists() else d / "audio16k.wav"
            hooks = [l for l in lines if l.get("hook")]       # the opening hook sits on silence: no voice to analyse
            pre = _rd(d / "content_speakers.json") if (s.get("content_enabled") and (d / "content_speakers.json").exists()) else None
            if pre:
                lines = [l for l in lines if not l.get("hook")]
                for l in lines:        # the transcript line this translated line overlaps most
                    best = max(pre, key=lambda x: min(l["end"], x[1]) - max(l["start"], x[0]))
                    l["speaker"], l["gender"] = best[2], best[3]
                from collections import Counter as _C
                summary = ", ".join(f"{k}: {n} câu" for k, n in _C(l["speaker"] for l in lines).most_common())
                job.log("Người nói: dùng lại kết quả của bước Biên kịch (" + summary + ")")
            else:
                lines, summary = speakers.detect(job, src, [l for l in lines if not l.get("hook")], s["whisper_device"])
            if hooks and lines:                               # read by the main speaker (usually the narrator)
                from collections import Counter
                top = Counter(l.get("speaker") for l in lines).most_common(1)[0][0]
                ref = next(l for l in lines if l.get("speaker") == top)
                for h in hooks:
                    h.update(speaker=top, gender=ref.get("gender"))
                lines = hooks + lines
            if s["voice_gender_mode"] != "auto":          # speakers were only needed to find the narrator
                for l in lines:
                    l["gender"] = s["voice_gender_mode"]
            if s.get("narrator_remove"):
                from . import narrator
                lines = [l for l in lines if not l.get("extra")]          # rerun: drop the last character lines
                lines = narrator.assign_roles(lines)
                nar = sorted({l["speaker"] for l in lines if l["role"] == "narrator"})
                summary += f" — thuyết minh: {', '.join(nar)}"
                narrator.make_keep(job, d, lines, s["whisper_device"], step="speakers")
                speakers.unload()
                extra = narrator.character_lines(job, d, s, total, meta.get("title", ""))
                if extra:
                    lines = sorted(lines + extra, key=lambda l: l["start"])
                    summary += f", +{len(extra)} câu nhân vật (phụ đề)"
            speakers.unload()
            _wr(d / "lines.json", lines)
            end("speakers", summary)
            if not s.get("narrator_remove"):
                cp.save("speakers", k_sp, [], summary,
                        data=[[l.get("start"), l.get("end"), l.get("speaker"), l.get("gender")] for l in lines])

    # 7. tts -------------------------------------------------------------------------
    def voice(ls):
        """Make the voice; lines too long to read in time are rewritten shorter by the LLM and voiced again."""
        ls = tts.generate(job, ls, total, s, d)
        if not s.get("fit_to_time", True):
            return ls
        sc_file = d / "tts" / "shorten.json"
        try:
            sc = _rd(sc_file) if sc_file.exists() else {}
        except Exception:
            sc = {}
        for rnd in range(2):
            long = translate.overlong(ls)
            if not long:
                break
            job.step("tts", detail=f"Rút gọn {len(long)} câu đọc không kịp…")
            try:
                from .narrator import _As            # the AI reports on this step, not on "Dịch"
                n = translate.shorten(_As(job, "tts"), ls, long, s, cache=sc)
                sc_file.parent.mkdir(parents=True, exist_ok=True)
                _wr(sc_file, sc)
            except Exception as e:
                from .jobs import Cancelled
                if isinstance(e, Cancelled):
                    raise
                job.log(f"Không rút gọn được ({str(e).splitlines()[0][:150]}) → giữ câu cũ", "warn")
                break
            left = len(long) - n
            job.log(f"Rút gọn cho vừa thời lượng (lượt {rnd + 1}): {n}/{len(long)} câu ngắn lại"
                    + (f", {left} câu giữ nguyên" if left else ""))
            if not n:
                break
            ls = tts.generate(job, ls, total, s, d)
        return ls

    def tts_key(ls):
        rows = [{k: l.get(k) for k in ("tgt", "gender", "speaker", "role", "start", "end", "hook")} for l in ls]
        conf = {k: v for k, v in s.items() if k.startswith(("tts_", "edge_", "elevenlabs_", "piper_", "google_", "vieneu_"))}
        return ck.key("tts", rows, conf, ck.pick(s, "max_speedup", "fit_to_time", "narrator_remove",
                                               "voice_gender_mode", "translate_style"), ck.code("tts"))

    if should("tts") and mode in VOICED and reuse("tts", tts_key(translate.drop_placeholders(job, lines)), ["voice.wav"]):
        job.add_file("voice", "voice.wav")
    elif should("tts"):
        if mode not in VOICED:
            skip("tts", "Không cần (giữ audio gốc)")
        else:
            begin("tts", f"{s['tts_provider']}")
            if total > 1200 and s["tts_provider"] in ("google", "edge", "elevenlabs"):
                from . import vieneu_tts
                if vieneu_tts.installed():
                    # online voices go a few lines at a time (Google: 1252 lines ≈ 13 min on a 38-min film);
                    # VieNeu reads ~24 lines per GPU batch in ~2.5 s
                    job.log(f"Mẹo: video dài {total / 60:.0f} phút — giọng VieNeu (local GPU) tạo giọng nhanh hơn "
                            f"nhiều lần so với {s['tts_provider']} (chọn ở Giọng đọc)", "warn")
            lines = translate.drop_placeholders(job, lines)
            if s.get("content_enabled"):
                lines = [l for l in lines if not l.get("hook")]      # the “Biên kịch” hook replaces it
            if s.get("narrator_remove"):
                # narrator voice-over removed, characters keep their own voice: dub only the narration
                from . import narrator
                if not any("role" in l for l in lines):
                    lines = narrator.assign_roles(lines)
                narrator.make_keep(job, d, lines, s["whisper_device"])
                speakers.unload()
                narr = [l for l in lines if l.get("role") == "narrator"]
                if not narr:
                    raise RuntimeError("Không có câu nào là thuyết minh — chọn vai trong bảng Bản dịch")
                voice(narr)
                for l in lines:
                    if l.get("role") != "narrator":
                        l["vstart"], l["vend"] = l["start"], l["end"]      # subtitles at the original time
            else:
                lines = voice(lines)
            _wr(d / "lines.json", lines)
            job.add_file("voice", "voice.wav")
            name = {"edge": "Edge", "elevenlabs": f"ElevenLabs {s['elevenlabs_model']}", "piper": "Piper local",
                    "google": "Google Dịch", "vieneu": "VieNeu (GPU)"}
            det = f"{len(lines)} câu — {name.get(s['tts_provider'], s['tts_provider'])}"
            end("tts", det)
            cp.save("tts", tts_key(lines), ["voice.wav"], det)      # key of the FINAL lines (after shortening)
    if lines:
        render.write_srt(lines, d / "subs.srt", mode)
        job.add_file("srt", "subs.srt")

    video_ready()

    if pic["t"] is not None:
        if pic["t"].is_alive():
            job.log("Chờ dò phụ đề gốc / watermark xong…")
        while pic["t"].is_alive():
            job.check_cancel()
            pic["t"].join(2)
        if pic["err"]:
            raise pic["err"]
    else:
        picture_steps(asr_data["language"] if asr_data else (s.get("source_lang") or "zh"))
    band = _rd(d / "subband.json") if (d / "subband.json").exists() and not text_only else None
    wm_items = watermark.load(d) if (s.get("wm_remove") or "auto") != "off" else []

    # 8b. on-screen text: OCR over time -> translate -> redrawn in place at render ---------------
    if should("screen_text"):
        if not s.get("screen_text"):
            skip("screen_text", "Đang tắt")
        else:
            begin("screen_text", "OCR")
            k_ocr = ck.key("screen_ocr", ck.fp(d / "source.mp4"),
                           ck.pick(s, "screen_text_interval", "screen_text_min_height"), ck.code("screentext"))
            ocr_reused = bool(cp.hit("screen_ocr", k_ocr, ["screentext_ocr.json"]))
            todo = []
            if ocr_reused:
                events = _rd(d / "screentext_ocr.json")
                job.log(f"♻ OCR chữ trên màn hình: video và cài đặt không đổi → dùng lại {len(events)} đoạn chữ")
            else:
                events = screentext.detect(job, d / "source.mp4", total, s)
                _wr(d / "screentext_ocr.json", events)
                cp.save("screen_ocr", k_ocr, ["screentext_ocr.json"])
            n0 = len(events)
            events = [e for e in events if not watermark.covers(e, wm_items)]     # watermarks: removed, not translated
            if len(events) < n0:
                job.log(f"Bỏ {n0 - len(events)} đoạn chữ là watermark (sẽ xoá thay vì dịch)", "debug")
            if not text_only and band and lines:
                # the burned-in captions ARE the speech: reuse the speech translation instead of translating twice
                cap = screentext.speech_captions(events, band, lines)
                if cap:
                    events = [e for e in events if e["id"] not in cap]
                    _wr(d / "subband.json", dict(band, speech=True))
                    band = dict(band, speech=True)
                    job.log(f"{len(cap)} đoạn chữ là phụ đề của lời nói → dùng lại bản dịch lời nói (cùng chữ với giọng "
                            f"đọc), che như phụ đề; chỉ dịch {len(events)} đoạn chữ còn lại")
            if events:
                job.step("screen_text", progress=0.85, detail=f"Dịch {len(events)} đoạn chữ…")
                from .narrator import _As      # report on this step, not on the (skipped) translate step
                from . import styles
                s = styles.resolve(_As(job, "screen_text"), d, s, meta.get("title", ""), [e["src"] for e in events[:40]])
                # translations cached per text (same style/title/prompt): only NEW texts go to the AI
                tr_file = d / "screentext_tr.json"
                tr_all = _rd(tr_file) if tr_file.exists() else {}
                ctx = ck.key(s.get("target_lang"), s.get("translate_style"), meta.get("title", ""), ck.code("translate"))
                known = tr_all.get(ctx, {})
                todo = [e for e in events if e["src"] not in known]
                for e in events:
                    if e["src"] in known:
                        e["tgt"] = known[e["src"]]
                if todo:
                    todo = translate.translate_texts(_As(job, "screen_text"), todo, s, meta.get("title", ""))
                    for e in todo:
                        known[e["src"]] = e["tgt"]
                    tr_all[ctx] = known
                    _wr(tr_file, tr_all)
                if len(todo) < len(events):
                    job.log(f"♻ Dịch chữ trên màn hình: dùng lại {len(events) - len(todo)} đoạn đã dịch, "
                            f"dịch mới {len(todo)} đoạn")
            _wr(d / "screentext.json", events)
            job.add_file("screentext", "screentext.json")
            end("screen_text", ("♻ " if ocr_reused and not todo else "")
                + (f"{len(events)} đoạn chữ" if events else "Không thấy chữ cần dịch"))
            if text_only and not events:
                job.log("Không tìm thấy chữ trên màn hình để dịch — video kết quả sẽ giống video gốc.", "warn")

    RENDER_INPUTS = ("source.mp4", "lines.json", "voice.wav", "no_vocals.wav", "vocals.wav", "vocals_keep.wav",
                     "screentext.json", "subband.json", subdetect.EVENTS_FILE, watermark.FILE, "content.json", "bgm.wav")

    def render_key(name, seconds=None):
        return ck.key("render", name, seconds, {f: ck.fp(d / f) for f in RENDER_INPUTS}, band,
                      {k: v for k, v in s.items() if not k.startswith(("tiktok_", "library_", "crawl_"))},
                      ck.code("render", "textclean", "brand", "watermark", "tts"))

    def picture_key(name):
        """Everything that decides the PICTURE of the final video (None = no audio-only shortcut for this job)."""
        if mode not in VOICED or s.get("content_enabled"):
            return None
        cues = render.sub_cues(lines, mode) if lines else []
        hold = 0.0
        if (d / "voice.wav").exists():
            hold = round(max(0.0, util.duration(d / "voice.wav") - util.duration(d / "source.mp4")), 2)
        vis = {k: v for k, v in s.items() if k not in render.AUDIO_KEYS
               and not k.startswith(("tiktok_", "library_", "crawl_", "tts_", "edge_", "elevenlabs_", "piper_", "google_",
                                     "vieneu_", "translate_", "content_", "whisper_"))}
        return ck.key("picture", name, cues, hold, band, vis,
                      {f: ck.fp(d / f) for f in ("source.mp4", "screentext.json", "subband.json", subdetect.EVENTS_FILE,
                                                 watermark.FILE)},
                      ck.code("render", "textclean", "brand", "watermark", "subsplit"))

    if mode == "sub" and not s.get("burn_subtitles"):
        # "Chỉ Sub Việt" without subtitles would just copy the original video
        job.log("Chế độ Chỉ Sub Việt nhưng đang tắt “Gắn phụ đề Việt” → vẫn gắn phụ đề (không thì video giống hệt bản gốc)", "warn")
        s = dict(s, burn_subtitles=True)

    # 8. preview ---------------------------------------------------------------------
    if should("preview") and not s.get("wait_for_preview"):
        # nobody waits to approve it: render the final video straight away (the preview cost 15–20 s per job)
        skip("preview", "Bỏ qua — không chờ duyệt, render thẳng bản cuối")
    elif should("preview"):
        sec = float(s["preview_seconds"])
        k_pv = render_key("preview.mp4", sec)
        if reuse("preview", k_pv, ["preview.mp4"]):
            job.add_file("preview", "preview.mp4")
        else:
            begin("preview", f"{sec:.0f} giây đầu")
            render.render(job, d, s, band, "preview.mp4", seconds=sec, step="preview")
            job.add_file("preview", "preview.mp4")
            end("preview", f"{sec:.0f} giây đầu")
            cp.save("preview", k_pv, ["preview.mp4"], f"{sec:.0f} giây đầu")
        if s["wait_for_preview"] and job.start_from != "render":
            job.step("render", status="waiting", detail="Chờ bạn duyệt preview")
            return "awaiting_approval"

    # 9. final render ---------------------------------------------------------------
    if should("render"):
        name, key = OUTPUTS.get(mode, OUTPUTS["sub"])
        k_rd = render_key(name)
        if reuse("render", k_rd, [name]):
            job.add_file(key, name)
        else:
            begin("render")
            # same PICTURE as the last render (only music / volumes / voice sound changed)? then keep it and mix
            # the sound again instead of encoding the whole video
            vk = picture_key(name)
            last = cp.data.get("render") or {}
            remixed = False
            if (vk and "render" not in cp.force and (last.get("data") or {}).get("vkey") == vk
                    and ck.fp(d / name) == (last.get("out") or {}).get(name)):
                try:
                    remixed = render.remix_audio(job, d, s, name)
                except Exception as e:
                    from .jobs import Cancelled
                    if isinstance(e, Cancelled):
                        raise
                    job.log(f"Không trộn lại tiếng được ({str(e).splitlines()[0][:150]}) → render đầy đủ", "warn")
            if not remixed:
                render.render(job, d, s, band, name, step="render")
            job.add_file(key, name)
            det = f"{(d / name).stat().st_size / 1e6:.0f} MB" + (" (chỉ trộn lại tiếng)" if remixed else "")
            end("render", det)
            cp.save("render", k_rd, [name], det, data={"vkey": vk})

    def finish():
        """Post-render steps: title & hashtags, TikTok check, readable copies. The video is already there, so the
        job worker runs these in the background (job.defer_tail) and the job shows “Xong” ~1 minute earlier."""
        nonlocal s
        # 10. post kit: title, description, hashtags (never fails the job) -------------------------
        _cplan = None
        if s.get("content_enabled"):
            from . import content as _content
            _cplan = _content.load(d)
        if should("summary") and _cplan:
            # Biên kịch: caption / hashtags / pinned comment / credit come from the plan (no second AI call);
            # cover.jpg and the teaser clips are cut from the finished video
            begin("summary", "Ảnh bìa, clip mồi, nội dung đăng từ kịch bản")
            name_out = OUTPUTS.get(mode, OUTPUTS["sub"])[0]
            made = _content.extras(job, d, s, name_out, _cplan, total)
            for k, f in made.items():
                job.add_file(k, f)
            end("summary", ", ".join(made))
        elif should("summary"):
            if not s.get("post_summary", True):
                skip("summary", "Đang tắt")
            else:
                from . import postgen
                stj = _rd(d / "screentext.json") if (d / "screentext.json").exists() else []
                k_sum = ck.key("summary", [l.get("tgt") for l in lines], [e.get("tgt") for e in stj or []],
                               s.get("translate_style"), meta.get("title", ""), ck.pick(s, "brand_credit"), ck.code("postgen"))
                hit = reuse("summary", k_sum, [postgen.FILE])
                if hit:
                    job.add_file("post", postgen.FILE)
            if should("summary") and not _cplan and s.get("post_summary", True) and not hit:
                begin("summary")
                try:
                    post = postgen.generate(job, d, s)
                    job.add_file("post", postgen.FILE)
                    end("summary", post["title"][:80])
                    cp.save("summary", k_sum, [postgen.FILE], post["title"][:80])
                except Exception as e:
                    from .jobs import Cancelled
                    if isinstance(e, Cancelled):
                        raise
                    job.log(f"Không viết được tiêu đề/hashtag: {str(e).splitlines()[0][:200]}", "warn")
                    job.step("summary", status="error", progress=1.0, detail="Lỗi — bấm ↻ Viết lại trong thẻ Nội dung đăng")
        # 11. TikTok pre-publish check (+ auto-fixed TikTok version); never fails the job ----------------
        if should("tiktok"):
            if not s.get("tiktok_check", True):
                skip("tiktok", "Đang tắt")
            else:
                name = OUTPUTS.get(mode, OUTPUTS["sub"])[0]
                if not (d / name).exists():
                    skip("tiktok", "Chưa có video cuối")
                else:
                    from . import tiktokcheck
                    k_tt = ck.key("tiktok", ck.fp(d / name), ck.fp(d / "post.json"),
                                  ck.pick(s, "tiktok_autofix", "output_short_side", "video_encoder", "brand_tiktok",
                                          "brand_youtube", "brand_facebook"), ck.code("tiktokcheck"))
                    if reuse("tiktok", k_tt, [tiktokcheck.FILE]):
                        job.add_file("tiktok_check", tiktokcheck.FILE)
                        if (d / tiktokcheck.OUT).exists():
                            job.add_file("output_tiktok", tiktokcheck.OUT)
            if should("tiktok") and s.get("tiktok_check", True) and (d / OUTPUTS.get(mode, OUTPUTS["sub"])[0]).exists() \
                    and job.steps["tiktok"]["status"] != "done":
                name = OUTPUTS.get(mode, OUTPUTS["sub"])[0]
                if True:
                    begin("tiktok", "Đo file, âm lượng, quét chữ…")
                    try:
                        from . import tiktokcheck
                        rep = tiktokcheck.run(job, d, s, name, mode)
                        job.add_file("tiktok_check", tiktokcheck.FILE)
                        end("tiktok", tiktokcheck.summary(rep))
                        cp.save("tiktok", k_tt, [tiktokcheck.FILE], tiktokcheck.summary(rep))
                        r = tiktokcheck.best(rep)
                        for x in r["items"]:
                            if x["status"] == "fail":
                                job.log(f"TikTok: {x['label']} — {x['detail']}", "warn")
                    except Exception as e:
                        from .jobs import Cancelled
                        if isinstance(e, Cancelled):
                            raise
                        job.log(f"Không kiểm tra được chuẩn TikTok: {str(e).splitlines()[0][:200]}", "warn")
                        job.step("tiktok", status="error", progress=1.0, detail="Lỗi — bấm ↻ Kiểm tra lại")
        # readable copies in the export folder: "<Vietnamese title> (Lồng tiếng).mp4"
        try:
            from . import export
            done = export.run(job, d, s)
            if done:
                job.log("Đã xuất: " + " | ".join(Path(p).name for k, p in done.items() if not k.endswith("_srt")))
        except Exception as e:
            job.log(f"Không xuất được file tên tiếng Việt: {e}", "warn")
        job.log(f"AI: {job.ai_calls} lượt gọi trong lần chạy này"
                + (" (các bước dịch/viết dùng lại kết quả cũ)" if not job.ai_calls else ""))

    if getattr(job, "defer_tail", False) and (should("summary") or should("tiktok")):
        job.tail_fn = finish
    else:
        finish()
    return "done"
