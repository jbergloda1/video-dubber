"""Find burned-in subtitles with OCR so they can be blurred/covered."""
import re
import subprocess
import tempfile
from pathlib import Path

from . import util

_ocr = None


def _engine():
    """RapidOCR on the GPU when onnxruntime-gpu (CUDA 12 build) can load, otherwise CPU."""
    global _ocr
    if _ocr is None:
        import logging
        import os
        logging.getLogger("OrtInferSession").setLevel(logging.ERROR)
        from rapidocr_onnxruntime import RapidOCR
        use_gpu = False
        try:
            import onnxruntime as ort
            if "CUDAExecutionProvider" in ort.get_available_providers():
                import torch      # reuse the CUDA 12 / cuDNN 9 DLLs that ship with PyTorch
                lib = os.path.join(os.path.dirname(torch.__file__), "lib")
                os.add_dll_directory(lib)
                if hasattr(ort, "preload_dlls"):
                    ort.preload_dlls(directory=lib)
                use_gpu = True
        except Exception:
            use_gpu = False
        if use_gpu:
            try:
                _ocr = RapidOCR(det_use_cuda=True, cls_use_cuda=True, rec_use_cuda=True)
                if _ocr.text_det.infer.session.get_providers()[0] != "CUDAExecutionProvider":
                    raise RuntimeError("CUDA EP did not load")
                _ocr.device = "GPU"
                return _ocr
            except Exception:
                pass
        _ocr = RapidOCR()
        _ocr.device = "CPU"
    return _ocr


def detect(job, video: Path, dur: float, lang: str, samples: int = 16):
    """Return band {x0,x1,y0,y1} as fractions of the frame, or None if no burned subtitles."""
    W, H = util.video_size(video)
    ocr = _engine()
    hits = []
    with tempfile.TemporaryDirectory() as td:
        for k in range(samples):
            job.check_cancel()
            t = dur * (k + 0.5) / samples
            img = Path(td) / f"f{k}.jpg"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                            "-vf", "scale=-2:720", str(img)], creationflags=util.CREATE_NO_WINDOW, check=True)
            h = 720
            w = round(W * 720 / H)
            res, _ = ocr(str(img))
            for box, text, score in res or []:
                if score < 0.6 or len(text.strip()) < 2:
                    continue
                if lang in ("zh", "ja", "yue") and not re.search(r"[一-鿿぀-ヿ]", text):
                    continue
                ys = [p[1] for p in box]
                xs = [p[0] for p in box]
                y0, y1, x0, x1 = min(ys) / h, max(ys) / h, min(xs) / w, max(xs) / w
                cy, cx = (y0 + y1) / 2, (x0 + x1) / 2
                # subtitles: lower part of frame, roughly centred horizontally
                if cy > 0.55 and abs(cx - 0.5) < 0.15:
                    hits.append((k, x0, x1, y0, y1, text))
            job.step("detect_subs", progress=0.3 * (k + 1) / samples)
    frames = {h[0] for h in hits}
    if len(frames) < max(3, samples // 4):
        job.log("Không thấy phụ đề cứng trong video (hoặc quá ít) → không che.")
        return None

    def pct(vals, p):
        vals = sorted(vals)
        return vals[min(int(p * (len(vals) - 1) + 0.5), len(vals) - 1)]

    # most subtitle lines sit on the same baseline; use robust percentiles
    y0 = pct([h[3] for h in hits], 0.1)
    y1 = pct([h[4] for h in hits], 0.9)
    x0 = min(h[1] for h in hits)
    x1 = max(h[2] for h in hits)
    pad_y = (y1 - y0) * 0.25 + 0.006
    # subtitles are centred and longer lines may not appear in the samples: keep the band wide
    half = max(0.5 - x0, x1 - 0.5, 0.35) + 0.03
    x0, x1 = 0.5 - half, 0.5 + half
    band = {"x0": max(0.0, x0), "x1": min(1.0, x1),
            "y0": max(0.0, y0 - pad_y), "y1": min(1.0, y1 + pad_y)}
    sample = " | ".join(h[5] for h in hits[:4])
    job.log(f"Phát hiện phụ đề cứng ở {len(frames)}/{samples} khung, vùng y={band['y0']:.2f}–{band['y1']:.2f}. "
            f"Ví dụ: {sample}")
    return band


# ------------------------------------------------------------------ per-line tracking (cover follows the text)
EVENTS_FILE = "subevents.json"


def _sim(a, b):
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


def track_lines(job, video: Path, dur: float, band: dict, lang: str, interval: float = 0.25):
    """OCR the subtitle band every `interval` s and return one event per subtitle line on screen:
    [{start, end, box[x0,y0,x1,y1] (0..1 of the frame), line_h, src, fill, outline}].
    The cover/erase at render time then follows each line's real width and timing instead of a fixed band."""
    import numpy as np
    from PIL import Image
    from .screentext import _colors
    W, H = util.video_size(video)
    if dur > LONG_S and interval < 1 / 3:
        interval = 1 / 3            # long video: 3 samples/s (subtitles last ≥ ~1 s; covers are padded 0.4–0.5 s)
    # strip = the band, a little taller (the band is a percentile, a few lines sit higher/lower)
    pad = (band["y1"] - band["y0"]) * 0.6
    sy0, sy1 = max(0.0, band["y0"] - pad), min(1.0, band["y1"] + pad)
    k = min(1.0, 960 / W)
    w = int(W * k) // 2 * 2
    sh = int((sy1 - sy0) * H * k) // 2 * 2
    ocr = _engine()
    cjk = lang in ("zh", "ja", "yue")
    frames = []
    last, last_mask, n_ocr, since_ocr = None, None, 0, 0
    since_empty = 0
    empty_px = None
    with tempfile.TemporaryDirectory() as td:
        util.ffmpeg(job, ["-i", str(video), "-vf",
                          f"fps=1/{interval},crop=iw:ih*{sy1 - sy0:.4f}:0:ih*{sy0:.4f},scale={w}:{sh}",
                          "-q:v", "3", str(Path(td) / "s_%05d.jpg")], duration=dur, step="detect_subs",
                    scale=(0.3, 0.5))
        files = sorted(Path(td).glob("s_*.jpg"))
        for i, f in enumerate(files):
            if i % 20 == 0:
                job.check_cancel()
                job.step("detect_subs", progress=0.5 + 0.45 * i / max(len(files), 1),
                         detail=f"Theo dõi phụ đề {i}/{len(files)} khung")
            img = np.asarray(Image.open(f).convert("RGB"))
            # a subtitle usually stays several frames: if the bright strokes inside the last line's box are
            # unchanged, it is the same line -> reuse the reading instead of running OCR again (≈3-5x faster)
            if last is None and frames and frames[-1] is None and since_empty < REREAD_EMPTY:
                # no subtitle on the last frame and still no bright text strokes in the centre of the strip:
                # nothing to read (a 38-min film spent ~half its OCR on such empty frames)
                if empty_px is None:
                    empty_px = (int(w * 0.15), 0, int(w * 0.85), sh)
                if _no_text(img, empty_px):
                    frames.append(None)
                    since_empty += 1
                    continue
            since_empty = 0
            if last is not None and since_ocr < REREAD_SAME:  # still OCR at least every REREAD_SAME frames
                m = _stroke_mask(img, last["_px"])
                if m is not None and last_mask is not None and _iou_mask(m, last_mask) > 0.85:
                    frames.append({k: v for k, v in last.items()})
                    since_ocr += 1
                    continue
            since_ocr = 0
            res, _ = ocr(str(f))
            n_ocr += 1
            lines, weak = [], []
            for box, text, score in res or []:
                text = text.strip()
                xs, ys = [p[0] for p in box], [p[1] for p in box]
                cx = (min(xs) + max(xs)) / 2 / w
                if abs(cx - 0.5) > 0.2 or not text:
                    continue                  # not centred: a logo / label, not the subtitle
                item = (min(xs), min(ys), max(xs), max(ys), text)
                if float(score) >= 0.6 and (not cjk or re.search(r"[一-鿿぀-ヿ]", text)):
                    lines.append(item)
                elif float(score) >= 0.3:
                    weak.append(item)         # fading in/out, blurred by motion: still visible text
            if not lines and weak:
                x0 = min(l[0] for l in weak); y0 = min(l[1] for l in weak)
                x1 = max(l[2] for l in weak); y1 = max(l[3] for l in weak)
                frames.append({"weak": True, "box": [x0 / w, sy0 + y0 / sh * (sy1 - sy0), x1 / w,
                                                     sy0 + y1 / sh * (sy1 - sy0)]})
                last, last_mask = None, None
                continue
            if not lines:
                frames.append(None)
                last, last_mask = None, None
                continue
            x0 = min(l[0] for l in lines); y0 = min(l[1] for l in lines)
            x1 = max(l[2] for l in lines); y1 = max(l[3] for l in lines)
            lh = float(np.median([l[3] - l[1] for l in lines]))
            text = "".join(l[4] for l in sorted(lines, key=lambda l: (l[1], l[0])))
            nb = (x0 / w, y0 / sh, x1 / w, y1 / sh)
            fill, outline = _colors(img, nb, w, sh)
            # strip coords -> frame coords
            fb = [x0 / w, sy0 + y0 / sh * (sy1 - sy0), x1 / w, sy0 + y1 / sh * (sy1 - sy0)]
            # compare the whole centred part of the strip at that height: a longer next line shows up as new
            # strokes outside the old box
            fr = {"box": fb, "text": text, "lh": lh / sh * (sy1 - sy0), "fill": fill, "outline": outline,
                  "_px": (int(w * 0.1), max(0, int(y0 - lh * 0.3)), int(w * 0.9), int(y1 + lh * 0.3))}
            frames.append(fr)
            last, last_mask = fr, _stroke_mask(img, fr["_px"])
    job.log(f"Phụ đề: OCR {n_ocr}/{len(frames)} khung (các khung còn lại cùng dòng chữ với khung trước)", "debug")
    return _events(frames, interval, dur)


LONG_S = 1200        # videos longer than this are sampled 3×/s instead of 4×/s
REREAD_SAME = 8      # the same line is re-read at least every 8 samples (2 s at 4/s)
REREAD_EMPTY = 8     # an empty strip is re-read at least every 8 samples


def _no_text(img, px):
    """True when the centre of the strip has (almost) no bright, text-like pixels — subtitles are drawn bright
    (white/yellow) with a dark edge. Busy bright pictures return False, so OCR still decides there."""
    x0, y0, x1, y1 = px
    crop = img[max(0, y0):y1, max(0, x0):x1]
    if crop.size == 0:
        return False
    bright = crop.max(-1) > 205
    return float(bright.mean()) < 0.004


def _stroke_mask(img, px):
    """Bright text-fill pixels inside an area, downsampled -> bool array; None when the area is too bright or
    busy for this comparison to mean anything (then OCR runs)."""
    import numpy as np
    x0, y0, x1, y1 = px
    crop = img[max(0, y0):y1, max(0, x0):x1]
    if crop.size == 0:
        return None
    m = crop.max(-1) > 205
    if m.mean() > 0.35:
        return None
    h, w = m.shape
    k = 2
    return m[: h // k * k, : w // k * k].reshape(h // k, k, w // k, k).any(axis=(1, 3))


def _iou_mask(a, b):
    if a.shape != b.shape:
        return 0.0
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 0.0


def _events(frames, interval, dur, max_gap=2, lead=0.4, tail=0.5, bridge=1.2):
    """Per-frame readings -> events. Same text (fuzzy) continues an event; a short OCR miss is bridged;
    "weak" frames (text OCR could not read confidently: fading, motion blur) extend the event they touch.
    Covers start `lead` s early and end `tail` s late, and gaps < `bridge` s between lines are closed, so the
    original never flashes between two samples."""
    import numpy as np
    events, cur, miss, weak_start, weak_boxes = [], None, 0, None, []
    for i, fr in enumerate(frames):
        t = i * interval
        if fr is None:
            weak_start, weak_boxes = None, []
            if cur:
                miss += 1
                if miss > max_gap:
                    events.append(cur)
                    cur, miss = None, 0
            continue
        if fr.get("weak"):
            if cur:
                cur["end"] = t
                cur["boxes"].append(fr["box"])
                miss = 0
            else:
                weak_start = t if weak_start is None else weak_start
                weak_boxes.append(fr["box"])
            continue
        if cur and _sim(cur["texts"][-1], fr["text"]) > 0.6:
            cur["end"] = t
            cur["texts"].append(fr["text"]); cur["boxes"].append(fr["box"]); cur["lhs"].append(fr["lh"])
            cur["fills"].append((fr["fill"], fr["outline"]))
            miss = 0
            continue
        if cur:
            events.append(cur)
        cur, miss = {"start": t if weak_start is None else weak_start, "end": t, "texts": [fr["text"]],
                     "boxes": [fr["box"]] + weak_boxes, "lhs": [fr["lh"]], "fills": [(fr["fill"], fr["outline"])]}, 0
        weak_start, weak_boxes = None, []
    if cur:
        events.append(cur)
    out = []
    for e in events:
        b = np.array(e["boxes"])
        # widest reading wins horizontally (OCR sometimes drops a character at the edge)
        box = [float(b[:, 0].min()), float(np.percentile(b[:, 1], 20)), float(b[:, 2].max()), float(np.percentile(b[:, 3], 80))]
        fill, outline = max(set(e["fills"]), key=e["fills"].count)
        text = max(set(e["texts"]), key=lambda x: (e["texts"].count(x), len(x)))
        out.append({"start": round(max(0.0, e["start"] - lead), 2), "end": round(min(dur, e["end"] + tail), 2),
                    "box": [round(v, 4) for v in box], "line_h": round(float(np.median(e["lhs"])), 4),
                    "src": text, "fill": fill, "outline": outline})
    # consecutive lines: no uncovered gap between them
    for a, b in zip(out, out[1:]):
        if 0 < b["start"] - a["end"] < bridge:
            a["end"] = b["start"]
    return out
