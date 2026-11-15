"""Videos that mix a NARRATOR voice-over with the voices of the people in the footage (interviews, riders,
commentators…): remove the narrator completely, keep the characters' own voices, and dub only the narration.

How:
1. The AI separator gives vocals.wav (all voices) and no_vocals.wav (music + effects).
2. speakers.detect labels every transcribed line with a speaker (ECAPA embeddings + clustering).
3. The narrator = the speaker with the most speech time (Douyin/TikTok explainers: the creator's voice-over),
   unless you mark the roles yourself in the script editor (role per line / per speaker).
4. vocals.wav is scanned in short overlapping windows; each window's voice embedding is compared with the
   narrator's and the characters' voice prints. Narrator windows (and the narrator's transcribed lines) are
   muted with short fades -> vocals_keep.wav = only the characters.
5. Only narrator lines get a Vietnamese voice; character lines keep their original voice and get subtitles.
Limit: where the narrator talks OVER a character at the same instant, that moment is muted (removing the
narrator completely wins over keeping the character).
"""
import numpy as np

KEEP = "vocals_keep.wav"
SR = 16000


def total_by_speaker(lines):
    out = {}
    for l in lines:
        out[l.get("speaker", "S1")] = out.get(l.get("speaker", "S1"), 0.0) + max(0.0, l["end"] - l["start"])
    return out


def assign_roles(lines, narrators=None):
    """Mark l["role"] = narrator | character. Lines already marked by you keep their role (role_set)."""
    if narrators is None:
        tot = total_by_speaker(lines)
        narrators = {max(tot, key=tot.get)} if tot else set()
    for l in lines:
        if not l.get("role_set"):
            l["role"] = "narrator" if l.get("speaker", "S1") in narrators else "character"
    return lines


def _embed_windows(enc, y, win=1.0, hop=0.25, batch=64):
    import torch
    n, w, h = len(y), int(win * SR), int(hop * SR)
    starts = list(range(0, max(1, n - w + 1), h))
    rms = np.array([np.sqrt(np.mean(y[s:s + w] ** 2)) for s in starts])
    active = rms > max(0.004, np.percentile(rms, 30) * 0.5)
    embs = np.zeros((len(starts), 192), np.float32)
    idx = [i for i, a in enumerate(active) if a]
    for k in range(0, len(idx), batch):
        part = idx[k:k + batch]
        x = torch.from_numpy(np.stack([y[starts[i]:starts[i] + w] for i in part])).float()
        with torch.no_grad():
            e = enc.encode_batch(x).squeeze(1).cpu().numpy()
        e /= np.linalg.norm(e, axis=1, keepdims=True) + 1e-9
        embs[part] = e
    return starts, active, embs, w


def make_keep(job, d, lines, device="cuda", step="tts"):
    """Write vocals_keep.wav (characters only). Returns seconds of narrator speech removed."""
    import librosa
    import soundfile as sf
    import torch
    from . import speakers
    voc = d / "vocals.wav"
    if not voc.exists():
        raise RuntimeError("Cần bước tách giọng (vocals.wav) để xoá giọng thuyết minh")
    full, sr_full = sf.read(str(voc), always_2d=True)
    y = librosa.resample(full.mean(1).astype(np.float32), orig_sr=sr_full, target_sr=SR)
    enc = speakers._encoder(device if torch.cuda.is_available() else "cpu")

    def line_emb(ls):
        segs = [y[int(l["start"] * SR):int(l["end"] * SR)] for l in ls if l["end"] - l["start"] >= 0.6]
        if not segs:
            return None
        es = []
        for s_ in segs[:40]:
            with torch.no_grad():
                e = enc.encode_batch(torch.from_numpy(s_).float().unsqueeze(0)).squeeze().cpu().numpy()
            es.append(e / (np.linalg.norm(e) + 1e-9))
        c = np.mean(es, 0)
        return c / (np.linalg.norm(c) + 1e-9)

    narr = [l for l in lines if l.get("role") == "narrator"]
    chars = [l for l in lines if l.get("role") != "narrator"]
    job.step(step, detail="Lấy dấu giọng thuyết minh / nhân vật…")
    c_n = line_emb(narr)
    c_c = [c for c in (line_emb([l for l in chars if l.get("speaker") == spk])
                       for spk in sorted({l.get("speaker") for l in chars})) if c is not None]
    mask = np.zeros(len(y), np.float32)
    # 1) the narrator's own transcribed lines (a little wider: ASR timestamps are approximate)
    for l in narr:
        mask[max(0, int((l["start"] - 0.15) * SR)):int((l["end"] + 0.2) * SR)] = 1.0
    # 2) any other voiced window that sounds like the narrator (untranscribed bits, breaths, short words)
    if c_n is not None:
        job.step(step, detail="Quét giọng thuyết minh trên toàn bộ âm thanh…")
        starts, active, embs, w = _embed_windows(enc, y)
        s_n = embs @ c_n
        s_c = np.max(np.stack([embs @ c for c in c_c]), 0) if c_c else np.full(len(starts), 0.2)
        for i, st in enumerate(starts):
            if active[i] and s_n[i] > 0.35 and s_n[i] > s_c[i] + 0.05:
                mask[st:st + w] = 1.0
    # character lines that are clearly NOT the narrator stay audible unless a narrator window overlaps
    # smooth: close tiny holes (<0.25 s) then 40 ms fades
    k = int(0.25 * SR)
    m = np.convolve(mask, np.ones(k) / k, mode="same") > 0.5
    m = np.maximum(m, mask > 0)
    f = int(0.04 * SR)
    g = 1.0 - np.convolve(m.astype(np.float32), np.ones(f) / f, mode="same")
    g = np.clip(g, 0, 1)
    gain = np.interp(np.arange(len(full)) / sr_full, np.arange(len(g)) / SR, g).astype(np.float32)
    sf.write(str(d / KEEP), (full * gain[:, None]).astype(np.float32), sr_full)
    removed = float(m.sum() / SR)
    job.log(f"Xoá giọng thuyết minh: {removed:.0f}s đã tắt tiếng, giữ giọng nhân vật "
            f"({len(chars)} câu của {len({l.get('speaker') for l in chars})} người)")
    return removed


class _As:
    """Report another step's progress (transcribe / translate) on the step that is running."""
    def __init__(self, job, step):
        object.__setattr__(self, "_job", job)
        object.__setattr__(self, "_step", step)

    def __getattr__(self, k):
        return getattr(self._job, k)

    def __setattr__(self, k, v):
        setattr(self._job, k, v)

    def step(self, sid, *a, **k):
        k.pop("status", None)
        return self._job.step(self._step, *a, **k)


def character_lines(job, d, s, total, title="", step="speakers"):
    """Transcribe what is left in vocals_keep.wav (the characters, often another language than the
    narrator), translate it, and return subtitle lines with role=character (not dubbed)."""
    import soundfile as sf
    from . import asr, translate
    keep = d / KEEP
    j = _As(job, step)
    job.step(step, detail="Nhận dạng lời nhân vật…")
    data = asr.transcribe(j, keep, total, {**s, "source_lang": "auto"})
    asr.unload()
    y, sr = sf.read(str(keep), always_2d=True)
    y = y.mean(1)
    seen, good = set(), []
    for l in data["lines"]:
        seg = y[int(l["start"] * sr):int(l["end"] * sr)]
        loud = float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0
        key = l["text"].strip().lower()
        if loud < 0.01 or key in seen or len(key) < 2:
            continue                  # Whisper "hears" sentences in silence; a repeated line is the classic sign
        seen.add(key)
        good.append(l)
    if not good:
        job.log("Không có lời nhân vật nào đủ rõ để làm phụ đề")
        return []
    job.log(f"Lời nhân vật: {len(good)} câu ({data['language']})")
    if data["language"] == s.get("target_lang", "vi"):
        out = [{"src": l["text"], "tgt": l["text"], "start": l["start"], "end": l["end"]} for l in good]
    else:
        job.step(step, detail=f"Dịch {len(good)} câu của nhân vật…")
        out, _ = translate.translate(j, {"language": data["language"], "lines": good,
                                         "words": [w for w in data["words"]
                                                   if any(g["start"] - 0.05 <= w["s"] <= g["end"] + 0.05 for g in good)]},
                                     s, title)
    for l in out:
        l.update(role="character", speaker="NV", extra=True, gender=l.get("gender", "male"))
    return out
