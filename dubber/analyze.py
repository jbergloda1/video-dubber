"""Recognise what kind of video this is and recommend the output mode (+ options).

Features (≈ 20–40 s on the GPU, works on the downloaded file):
  speech     Silero VAD over the whole audio: share of the video with speech, speech vs background loudness
  language   Whisper on up to 2 minutes of speech: language, confidence, a sample of what is said
  speakers   ECAPA voice clustering on those sentences: how many people speak (narrator vs dialogue), genders
  text       OCR on ~12 frames: burned-in subtitles (a text band low in the frame) and other on-screen text
             (titles, captions, labels), Chinese/Japanese/Korean or Latin
  picture    size, aspect, duration

Recommendation: rules on those numbers give a first answer; the translation AI (same provider chain) refines it
with the title and the speech sample (genre: documentary, vlog, drama, game, music…) and writes a short reason.
If no AI is available the rules decide. Saved as analysis.json next to the video.

Modes: dub (Vietnamese voice replaces the speech), voiceover (original kept quiet under the Vietnamese voice — keeps
emotion in dialogue/vlogs), sub (original audio, Vietnamese subtitles — songs, ASMR, performances), text (no useful
speech: translate the on-screen text only).
"""
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

from . import util

FILE = "analysis.json"
VERSION = 1
MODES = ("dub", "voiceover", "sub", "text")
MODE_VI = {"dub": "🎙 Lồng tiếng + Sub Việt", "voiceover": "📢 Thuyết minh", "sub": "💬 Chỉ Sub Việt",
           "text": "🔤 Chỉ dịch chữ trên màn hình"}
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
LANG_VI = {"zh": "tiếng Trung", "en": "tiếng Anh", "ja": "tiếng Nhật", "ko": "tiếng Hàn", "th": "tiếng Thái",
           "vi": "tiếng Việt", "fr": "tiếng Pháp", "es": "tiếng Tây Ban Nha", "de": "tiếng Đức", "ru": "tiếng Nga"}


# ---- features --------------------------------------------------------------------------------------------------
def _audio(video: Path, sr=16000):
    import numpy as np
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True, creationflags=util.CREATE_NO_WINDOW)
    return np.frombuffer(out.stdout, dtype=np.float32)


def speech_features(audio, sr=16000):
    import numpy as np
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    dur = len(audio) / sr
    if dur < 0.5:
        return {"speech_ratio": 0.0, "segments": [], "speech_db": None, "bg_db": None}
    ts = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=300, speech_pad_ms=100))
    segs = [(t["start"] / sr, t["end"] / sr) for t in ts]
    speech = sum(b - a for a, b in segs)
    mask = np.zeros(len(audio), dtype=bool)
    for t in ts:
        mask[t["start"]:t["end"]] = True

    def db(x):
        return round(float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)), 1) if len(x) else None
    return {"speech_ratio": round(speech / dur, 3), "segments": segs, "speech_db": db(audio[mask]),
            "bg_db": db(audio[~mask])}


def asr_features(job, audio, segs, s, sr=16000, max_s=120.0):
    """Whisper on the first ~2 minutes that contain speech → language + sentences (absolute times)."""
    from . import asr
    if not segs:
        return {"language": None, "language_prob": 0.0, "lines": []}
    a = max(0.0, segs[0][0] - 0.5)
    b = min(len(audio) / sr, a + max_s)
    model = asr._get_model(s.get("whisper_model", "large-v3-turbo"), s.get("whisper_device", "cuda"))
    lang = None if s.get("source_lang") in (None, "", "auto") else s["source_lang"]
    out, info = model.transcribe(audio[int(a * sr):int(b * sr)], language=lang, vad_filter=True, beam_size=1,
                                 condition_on_previous_text=False)
    lines = [{"start": round(a + x.start, 2), "end": round(a + x.end, 2), "text": x.text.strip()}
             for x in out if x.text.strip()]
    return {"language": info.language, "language_prob": round(float(info.language_probability), 2), "lines": lines}


def speaker_features(job, audio, lines, s, sr=16000):
    """How many different voices say those sentences (ECAPA clustering) and their genders."""
    import soundfile as sf
    from . import speakers
    good = [dict(l) for l in lines if l["end"] - l["start"] >= 0.8]
    if len(good) < 3:
        return {"speakers": 1 if good else 0, "genders": {}}
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        sf.write(str(wav), audio, sr)
        try:
            lab, _ = speakers.detect(_Quiet(job), wav, good, s.get("whisper_device", "cuda"))
        except Exception:
            return {"speakers": None, "genders": {}}
    by = {}
    for l in lab:
        by.setdefault(l.get("speaker"), []).append(l)
    big = {k: v for k, v in by.items() if len(v) >= max(2, 0.12 * len(lab))}   # ignore stray one-line clusters
    genders = {}
    for v in big.values():
        g = v[0].get("gender") or "?"
        genders[g] = genders.get(g, 0) + 1
    return {"speakers": max(1, len(big)), "genders": genders,
            "main_share": round(max((len(v) for v in by.values()), default=0) / max(len(lab), 1), 2)}


def text_features(video: Path, dur, samples=12):
    """OCR a few frames: burned-in subtitles (low text band, changing) vs other on-screen text."""
    from .subdetect import _engine
    from .watermark import is_wm_text
    o = _engine()
    cjk = latin = 0
    examples = []
    frames = []          # per frame: [(norm text, is_sub_position)]
    with tempfile.TemporaryDirectory() as td:
        for k in range(samples):
            t = dur * (k + 0.5) / samples
            f = Path(td) / f"f{k}.jpg"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                            "-vf", "scale='min(960,iw)':-2", str(f)], creationflags=util.CREATE_NO_WINDOW)
            if not f.exists():
                continue
            fw, fh = util.video_size(f)
            r, _ = o(str(f))
            found = []
            for b, txt, sc in (r or []):
                if float(sc) < 0.6 or len(txt.strip()) < 2:
                    continue
                ys = [p[1] for p in b]
                xs = [p[0] for p in b]
                cy = (min(ys) + max(ys)) / 2 / fh
                h = (max(ys) - min(ys)) / fh
                w = (max(xs) - min(xs)) / fw
                if h < 0.015 or is_wm_text(txt):
                    continue                            # tiny text (signs) and platform watermarks
                found.append((re.sub(r"\W+", "", txt.lower()), 0.55 <= cy <= 0.95 and w >= 0.15, txt))
            frames.append(found)
    # the same text in most frames = a logo / channel name burned into the picture, not content to translate
    # (OCR misreads a moving logo a little differently each time: "AIGC阿史", "AIGC.阿史", "AGC.同史" → one group)
    import difflib
    groups = []          # [representative, frame count, members]
    for found in frames:
        hit = set()
        for norm in {x[0] for x in found if x[0]}:
            for gi, g in enumerate(groups):
                if difflib.SequenceMatcher(None, norm, g[0]).ratio() >= 0.6:
                    g[2].add(norm)
                    hit.add(gi)
                    break
            else:
                groups.append([norm, 0, {norm}])
                hit.add(len(groups) - 1)
        for gi in hit:
            groups[gi][1] += 1
    steady = set()
    for rep, cnt, members in groups:
        if cnt >= max(3, 0.5 * len(frames)):
            steady |= members
    sub_frames = text_frames = 0
    for found in frames:
        real = [x for x in found if x[0] not in steady]
        sub_frames += any(x[1] for x in real)
        text_frames += any(not x[1] for x in real)
        for _, _, txt in real:
            if CJK.search(txt):
                cjk += 1
            else:
                latin += 1
            if len(examples) < 8 and txt not in examples:
                examples.append(txt)
    n = max(len(frames), 1)
    return {"subs_ratio": round(sub_frames / n, 2), "text_ratio": round(text_frames / n, 2),
            "text_script": "cjk" if cjk > latin else ("latin" if latin else None), "text_examples": examples,
            "logos": sorted(steady)[:5]}


class _Quiet:
    """Job stand-in for helper modules: logs at debug level only."""
    def __init__(self, job):
        self._job = job
        self.proc = None

    def log(self, m, level="info"):
        if self._job:
            self._job.log(m, "debug")

    def step(self, *a, **k):
        pass

    def check_cancel(self):
        if self._job:
            self._job.check_cancel()


# ---- recommendation ----------------------------------------------------------------------------------------------
def rules(f, target="vi"):
    """Rule-based recommendation from the measured features (also the fallback when no AI is available)."""
    r = _rules(f, target)
    if r["mode"] != "text":
        r["options"]["burn_subtitles"] = True
    return r


def _rules(f, target="vi"):
    sp, lang = f.get("speech_ratio") or 0.0, f.get("language")
    n, txt, subs = f.get("speakers") or 0, f.get("text_ratio") or 0.0, f.get("subs_ratio") or 0.0
    has_text = txt >= 0.25 or subs >= 0.25
    opts = {"screen_text": txt >= 0.25}
    if sp < 0.08 or not f.get("lines"):
        if has_text:
            return {"mode": "text", "options": {"screen_text": True}, "confidence": 0.8,
                    "reason": "Gần như không có lời nói; nội dung nằm ở chữ trên hình → chỉ dịch chữ trên màn hình, "
                              "giữ nguyên âm thanh gốc."}
        return {"mode": "sub", "options": opts, "confidence": 0.5,
                "reason": "Gần như không có lời nói và ít chữ trên hình — ít thứ cần dịch; giữ nguyên audio gốc."}
    if lang == target:
        return {"mode": "text" if has_text else "sub", "options": {"screen_text": has_text}, "confidence": 0.7,
                "reason": f"Lời nói đã là {LANG_VI.get(target, target)} — không cần lồng tiếng."}
    if n >= 2 and sp >= 0.2:
        return {"mode": "voiceover", "options": opts, "confidence": 0.6,
                "reason": f"{n} người nói (hội thoại) → thuyết minh: giữ giọng gốc nhỏ để còn cảm xúc, giọng Việt to "
                          "hơn."}
    return {"mode": "dub", "options": opts, "confidence": 0.65,
            "reason": "Một người kể/thuyết minh, lời nói rõ → lồng tiếng Việt thay giọng gốc, giữ nhạc nền."}


SCHEMA = {"type": "object", "required": ["mode", "reason"], "properties": {
    "mode": {"type": "string"}, "genre": {"type": "string"}, "reason": {"type": "string"},
    "screen_text": {"type": "boolean"}, "narrator_remove": {"type": "boolean"}, "confidence": {"type": "number"}}}
SYSTEM = """Bạn chọn CÁCH LÀM BẢN TIẾNG VIỆT cho một video ngắn (Douyin/TikTok/YouTube) dựa trên số liệu đo và lời nói.
Các kiểu:
- "dub": lồng tiếng Việt thay giọng gốc (giữ nhạc nền). Hợp: 1 người kể/thuyết minh, tài liệu, review, hướng dẫn,
  tin tức, kể chuyện — nơi LỜI NÓI là nội dung chính.
- "voiceover": thuyết minh — giữ giọng gốc nhỏ, giọng Việt to. Hợp: hội thoại nhiều người, vlog người thật nói trước
  máy, phim/drama, phỏng vấn, thể thao có bình luận — nơi cảm xúc giọng gốc quan trọng.
- "sub": giữ nguyên audio, chỉ thêm phụ đề Việt. Hợp: bài hát/MV, ASMR, biểu diễn, video ít lời, hoặc giọng gốc là
  điểm hấp dẫn chính.
- "text": chỉ dịch chữ trên màn hình (tiêu đề, chú thích), giữ nguyên âm thanh. Hợp: gần như không có lời nói
  (nhạc nền + chữ), nấu ăn/đồng quê không lời, video chữ.
Tuỳ chọn:
- screen_text: true nếu có nhiều chữ trên hình KHÁC phụ đề (tiêu đề, chú thích, nhãn) cần dịch.
- narrator_remove: true CHỈ khi có giọng thuyết minh VÀ nhân vật nói ngôn ngữ khác (vd thuyết minh tiếng Trung +
  nhân vật nói tiếng Anh) — bỏ hẳn giọng thuyết minh gốc, giữ giọng nhân vật.
Gợi ý từ quy tắc đo đạc: {rule} — chỉ đổi nếu nội dung cho thấy kiểu khác hợp hơn.
genre: thể loại ngắn gọn (vd "tài liệu thiên nhiên", "vlog nấu ăn đồng quê", "phim hành động").
reason: 1–2 câu tiếng Việt, nói rõ vì sao (dựa trên số liệu + nội dung). confidence: 0–1.
Chỉ trả về JSON."""


def recommend(job, f, s, title=""):
    base = rules(f, s.get("target_lang", "vi"))
    from .providers import orchestrator
    from .providers.base import BadOutput
    facts = {k: f.get(k) for k in ("duration", "width", "height", "speech_ratio", "speech_db", "bg_db", "language",
                                   "language_prob", "speakers", "genders", "main_share", "subs_ratio", "text_ratio",
                                   "text_script")}
    sample = "\n".join(f"[{l['start']:.0f}s] {l['text']}" for l in (f.get("lines") or [])[:25])
    user = (f"Tiêu đề: {title or '(không có)'}\nSố liệu đo: {json.dumps(facts, ensure_ascii=False)}\n"
            f"Chữ đọc được trên hình (vd): {' | '.join(f.get('text_examples') or [])[:300] or '(không có)'}\n"
            f"Lời nói (mẫu):\n{sample or '(không có lời nói)'}")
    sysmsg = SYSTEM.format(rule=f"{base['mode']} — {base['reason']}")
    qj = _Quiet(job)

    def task(p):
        if p.kind == "mt":
            return None
        raw = p.complete(qj, sysmsg, user, SCHEMA, s)
        try:
            data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        except Exception as e:
            raise BadOutput(f"Gợi ý kiểu video không đúng JSON: {e}")
        if data.get("mode") not in MODES:
            raise BadOutput(f"Kiểu không hợp lệ: {data.get('mode')}")
        return data
    try:
        data, pid = orchestrator.run(qj, s, task, "nhận diện kiểu video")
    except Exception as e:
        from .jobs import Cancelled
        if isinstance(e, Cancelled):
            raise
        data, pid = None, None
    if not data:
        return {**base, "by": "rules"}
    opts = {"screen_text": bool(data.get("screen_text", base["options"].get("screen_text")))}
    if data["mode"] != "text":
        opts["burn_subtitles"] = True          # dub / voice-over / sub: the Vietnamese subtitles are part of it
    if data.get("narrator_remove") and data["mode"] in ("dub", "voiceover"):
        opts["narrator_remove"] = True
    return {"mode": data["mode"], "options": opts, "genre": (data.get("genre") or "").strip()[:60],
            "reason": (data.get("reason") or base["reason"]).strip()[:300],
            "confidence": round(float(data.get("confidence") or 0.6), 2), "rule": base["mode"], "by": pid}


# ---- entry point -----------------------------------------------------------------------------------------------
def analyze(job, video: Path, s, title="", out_dir: Path | None = None, progress=None) -> dict:
    t0 = time.time()
    prog = progress or (lambda f, msg: None)
    dur = util.duration(video)
    W, H = util.video_size(video)
    prog(0.05, "Đọc âm thanh…")
    audio = _audio(video)
    sp = speech_features(audio)
    prog(0.25, "Nghe lời nói (Whisper)…")
    a = asr_features(job, audio, sp["segments"], s)
    prog(0.55, "Đếm số người nói…")
    spk = speaker_features(job, audio, a["lines"], s) if a["lines"] else {"speakers": 0, "genders": {}}
    prog(0.7, "Đọc chữ trên hình (OCR)…")
    tx = text_features(video, dur)
    f = {"duration": round(dur, 1), "width": W, "height": H, **{k: v for k, v in sp.items() if k != "segments"},
         **a, **spk, **tx}
    prog(0.85, "AI chọn kiểu kết quả…")
    rec = recommend(job, f, s, title)
    res = {"version": VERSION, "t": time.time(), "secs": round(time.time() - t0, 1), "features": f, "rec": rec}
    if out_dir:
        (Path(out_dir) / FILE).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    prog(1.0, MODE_VI[rec["mode"]])
    return res


def load(d: Path):
    p = Path(d) / FILE
    try:
        r = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        return r if r and r.get("version") == VERSION else None
    except Exception:
        return None


def summary(res) -> str:
    """One line for the job log / card: what was measured and what is recommended."""
    f, r = res["features"], res["rec"]
    lang = LANG_VI.get(f.get("language") or "", f.get("language") or "—")
    bits = [f"lời nói {f.get('speech_ratio', 0):.0%} thời lượng" + (f" ({lang})" if f.get("lines") else ""),
            f"{f.get('speakers') or 0} người nói" if f.get("lines") else None,
            f"phụ đề cứng {f.get('subs_ratio', 0):.0%} khung" if f.get("subs_ratio") else None,
            f"chữ khác trên hình {f.get('text_ratio', 0):.0%} khung" if f.get("text_ratio") else None]
    return f"{MODE_VI[r['mode']]} — " + ", ".join(b for b in bits if b)
