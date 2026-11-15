"""Speech recognition with faster-whisper on the GPU (model cached between jobs)."""
import json
import re
import threading

_model = None
_model_key = None
_lock = threading.Lock()

PUNCT = re.compile(r"[，。！？、；：,.!?;:…\"“”'‘’（）()\[\]%％�]")


def _get_model(name, device):
    global _model, _model_key
    from faster_whisper import WhisperModel
    with _lock:
        if _model_key != (name, device):
            _model = None
            try:
                _model = WhisperModel(name, device=device, compute_type="float16" if device == "cuda" else "int8")
            except Exception:
                if device != "cuda":
                    raise
                _model = WhisperModel(name, device="cpu", compute_type="int8")
            _model_key = (name, device)
        return _model


def unload():
    global _model, _model_key
    _model, _model_key = None, None


def transcribe(job, wav, total_dur, s) -> dict:
    model = _get_model(s["whisper_model"], s["whisper_device"])
    lang = None if s["source_lang"] in ("", "auto") else s["source_lang"]
    prompt = "以下是普通话的句子，使用简体中文。" if lang == "zh" else None
    segs, info = model.transcribe(str(wav), language=lang, vad_filter=False, beam_size=5,
                                  word_timestamps=True, condition_on_previous_text=False,
                                  initial_prompt=prompt)
    job.log(f"Ngôn ngữ phát hiện: {info.language} ({info.language_probability:.0%})")
    speech = _speech_spans(wav)
    words, dropped = [], []
    for seg in segs:
        job.check_cancel()
        if is_hallucination(seg.text, seg.start, seg.end, speech):
            dropped.append(seg.text.strip())
            continue
        for w in seg.words or []:
            words.append({"w": w.word, "s": round(w.start, 3), "e": round(w.end, 3)})
        job.step("transcribe", progress=min(seg.end / max(total_dur, 1), 1.0),
                 detail=seg.text.strip()[:60])
    if dropped:
        job.log(f"Bỏ {len(dropped)} đoạn Whisper “nghe ra” khi không có ai nói (nhạc/tiếng ồn): "
                + " | ".join(dict.fromkeys(dropped))[:200])
    return {"language": info.language, "words": words, "lines": split_lines(words, info.language)}


# Whisper "hears" these over music, rain or silence (subtitle credits, channel outros), often exactly at its
# 30-second window boundaries. They are not speech and must not be translated / dubbed.
HALLUCINATION = re.compile(
    r"字幕|字幕志愿者|中文字幕|字幕组|请不吝点赞|点赞.{0,6}订阅|订阅.{0,6}转发|打赏支持|明镜与点点|优优独播|"
    r"YoYo Television|ご視聴ありがとう|チャンネル登録|구독과 좋아요|시청해 주셔서|"
    r"thanks? (?:you )?for watching|subtitles? by|subscribe to (?:my|the) channel|amara\.org", re.I)


def _speech_spans(wav):
    """Where the voice-activity detector hears a voice ([(start, end)] in seconds); None if it can't run."""
    try:
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        audio = decode_audio(str(wav), sampling_rate=16000)
        ts = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=300, speech_pad_ms=200))
        return [(t["start"] / 16000, t["end"] / 16000) for t in ts]
    except Exception:
        return None


def is_hallucination(text, start, end, speech=None, min_voiced=0.2):
    """A known credit/outro phrase, or a segment where the voice detector hears (almost) no voice."""
    if HALLUCINATION.search(text or ""):
        return True
    if speech is None:
        return False
    dur = max(end - start, 0.05)
    voiced = sum(max(0.0, min(end, b) - max(start, a)) for a, b in speech)
    return voiced / dur < min_voiced


def split_lines(words, lang, max_chars=None):
    """Group words into short subtitle-sized lines using punctuation and pauses."""
    cjk = lang in ("zh", "ja", "ko", "yue")
    max_chars = max_chars or (16 if cjk else 60)
    lines, cur = [], []

    def flush():
        if cur:
            txt = "".join(w["w"] for w in cur) if cjk else " ".join(w["w"].strip() for w in cur)
            txt = PUNCT.sub("" if cjk else lambda m: m.group(0), txt).strip()
            if txt:
                lines.append({"start": cur[0]["s"], "end": cur[-1]["e"], "text": txt})
        cur.clear()

    prev = None
    for w in words:
        length = sum(len(x["w"].strip()) for x in cur)
        if prev is not None and (w["s"] - prev > 0.35 or length >= max_chars):
            flush()
        cur.append(w)
        prev = w["e"]
        if PUNCT.search(w["w"]):
            flush()
    flush()
    return lines


def save(data, path):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
