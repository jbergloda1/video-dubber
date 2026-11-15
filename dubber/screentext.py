"""On-screen text (titles, captions, labels burned into the picture): find it over time, so it can be
translated and redrawn in place.

1. OCR a frame every `interval` seconds (RapidOCR).
2. Per frame, merge OCR lines into blocks (a multi-line caption = one block).
3. Track blocks across frames (same place + similar text) -> events with start/end.
4. Estimate font size and colours so the Vietnamese can look like the original.
"""
import difflib
import json
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from . import util

CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")


OCR_FILE = "ocr_cache.json"
OCR_SIDE = 1280         # frame size shared by every full-frame OCR pass (watermarks, on-screen text)


class OcrCache:
    """Full-frame OCR results by timestamp, shared by the watermark and on-screen-text steps (they read the same
    frames: the 1 s grid is part of the 0.5 s grid). Tied to the video's fingerprint and the frame size, saved next
    to the video, so re-runs and the other step reuse every frame already read."""

    def __init__(self, video: Path, max_side: int):
        from . import checkpoint
        self.path = Path(video).parent / OCR_FILE
        self.tag = f"{checkpoint.fp(video)}:{max_side}"
        self.hits = self.misses = 0
        self.data = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            if raw.get("tag") == self.tag:
                self.data = raw.get("frames", {})
        except Exception:
            self.data = {}

    def get(self, ocr, t, frame_file):
        k = str(int(round(t * 1000)))
        if k in self.data:
            self.hits += 1
            return self.data[k]
        self.misses += 1
        res, _ = ocr(str(frame_file))
        out = [[[[float(x), float(y)] for x, y in box], str(text), float(score)] for box, text, score in (res or [])]
        self.data[k] = out
        if self.misses % 25 == 0:
            self.save()
        return out

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"tag": self.tag, "frames": self.data}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def _frames(job, video, dur, interval, td, max_side=OCR_SIDE, step="screen_text"):
    """Extract frames at a fixed rate in one ffmpeg pass."""
    W, H = util.video_size(video)
    k = min(1.0, max_side / max(W, H))
    w, h = int(W * k) // 2 * 2, int(H * k) // 2 * 2
    util.ffmpeg(job, ["-i", str(video), "-vf", f"fps=1/{interval},scale={w}:{h}", "-q:v", "3",
                      str(Path(td) / "f_%05d.jpg")], duration=dur, step=step, scale=(0, 0.15))
    files = sorted(Path(td).glob("f_*.jpg"))
    # frame i is (about) the picture at t = i * interval
    return files, w, h


def _blocks(lines, w, h):
    """Merge OCR lines of one frame into blocks (same paragraph)."""
    lines = sorted(lines, key=lambda l: (l["y0"], l["x0"]))
    blocks = []
    for l in lines:
        lh = l["y1"] - l["y0"]
        best, best_d = None, 1e9
        for b in blocks:
            bh = b["lh"]
            last = b["last"]
            same_row = abs(l["y0"] - last["y0"]) < 0.5 * bh and -0.5 * bh < l["x0"] - last["x1"] < 1.5 * bh
            gap = l["y0"] - last["y1"]        # rows of a caption often overlap slightly
            next_row = -0.5 * bh <= gap < 0.8 * max(bh, lh) and l["y0"] - last["y0"] > 0.5 * bh and \
                abs(lh - bh) < 0.45 * bh and \
                (min(l["x1"], b["x1"]) - max(l["x0"], b["x0"]) > -0.5 * bh or
                 abs((l["x0"] + l["x1"]) - (b["x0"] + b["x1"])) < 0.2 * w)
            if same_row or next_row:
                d = abs(l["y0"] - last["y0"]) if same_row else gap + bh
                if d < best_d:
                    best, best_d = (b, same_row), d
        if best:
            b, same_row = best
            if same_row:
                b["rows"][-1] += l["text"]
            else:
                b["rows"].append(l["text"])
            b["x0"], b["x1"] = min(b["x0"], l["x0"]), max(b["x1"], l["x1"])
            b["y0"], b["y1"] = min(b["y0"], l["y0"]), max(b["y1"], l["y1"])
            b["lh"] = (b["lh"] * b["n"] + lh) / (b["n"] + 1)
            b["n"] += 1
            b["last"] = l
        else:
            blocks.append({"rows": [l["text"]], "x0": l["x0"], "x1": l["x1"], "y0": l["y0"], "y1": l["y1"],
                           "lh": lh, "n": 1, "last": l})
    for b in blocks:
        b["text"] = "".join(b["rows"]) if CJK.search("".join(b["rows"])) else " ".join(b["rows"])
        b["box"] = (b["x0"] / w, b["y0"] / h, b["x1"] / w, b["y1"] / h)
        b["lh"] /= h
    return blocks


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0


def _sim(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def _colors(img, box, w, h):
    """Guess fill / outline colours of text inside box (RGB hex)."""
    x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)
    crop = img[max(y0, 0):max(y1, y0 + 1), max(x0, 0):max(x1, x0 + 1)].reshape(-1, 3).astype(float)
    if len(crop) < 20:
        return "#FFFFFF", "#000000"
    mx, mn = crop.max(1), crop.min(1)
    sat = (mx - mn) / (mx + 1e-6)
    val = mx / 255
    bright = crop[(val > 0.85) & (sat < 0.25)]
    colored = crop[(sat > 0.55) & (val > 0.45)]
    fill = "#FFFFFF" if len(bright) > 0.04 * len(crop) else None
    outline = None
    if len(colored) > 0.04 * len(crop):
        c = np.median(colored, 0)
        outline = "#%02X%02X%02X" % tuple(int(v) for v in c)
    if fill is None:   # coloured text: the saturated pixels are the fill
        fill = outline or "#FFFFFF"
        outline = "#000000"
    return fill, outline or "#000000"


def detect(job, video: Path, dur: float, s: dict):
    from PIL import Image
    from .subdetect import _engine
    interval = float(s.get("screen_text_interval") or (0.5 if dur <= 120 else 1.0))
    min_h = float(s.get("screen_text_min_height") or 0.022)
    ocr = _engine()
    with tempfile.TemporaryDirectory() as td:
        files, w, h = _frames(job, video, dur, interval, td)
        cache = OcrCache(video, OCR_SIDE)
        per_frame = []
        n_cjk = n_all = 0
        for i, f in enumerate(files):
            job.check_cancel()
            res = cache.get(ocr, i * interval, f)
            lines = []
            for box, text, score in res or []:
                text = text.strip()
                if float(score) < 0.75 or len(text) < 2:
                    continue
                ys = [p[1] for p in box]
                xs = [p[0] for p in box]
                l = {"text": text, "x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys)}
                bw, bh = l["x1"] - l["x0"], l["y1"] - l["y0"]
                # vertical CJK (a column of characters): the character size is the column WIDTH
                l["vertical"] = bool(CJK.search(text)) and len(text) >= 2 and bh > 1.6 * bw
                char = bw if l["vertical"] else bh
                if char / h < min_h:
                    continue     # logos, ad boards, watermarks
                if char / h > 0.18 or bw * bh / (w * h) > 0.25:
                    continue     # huge "text": a seal stamp, artwork or calligraphy filling the frame, not a caption
                lines.append(l)
                n_all += 1
                n_cjk += bool(CJK.search(text))
            per_frame.append(lines)
            job.step("screen_text", progress=0.15 + 0.65 * (i + 1) / len(files),
                     detail=f"OCR {i + 1}/{len(files)} khung")

        # which script is the video's text in? keep only that (drops sponsor boards, jersey names…)
        cjk_video = n_cjk >= max(1, 0.4 * n_all)
        blocks_per_frame = []
        for lines in per_frame:
            if cjk_video:
                lines = [l for l in lines if CJK.search(l["text"])]
            vert = [l for l in lines if l.get("vertical")]
            blocks_per_frame.append(_blocks([l for l in lines if not l.get("vertical")], w, h) +
                                    [{"rows": [l["text"]], "text": l["text"], "vertical": True,
                                      "box": (l["x0"] / w, l["y0"] / h, l["x1"] / w, l["y1"] / h),
                                      "lh": (l["x1"] - l["x0"]) / h} for l in vert])
        cache.save()
        events = track(blocks_per_frame, interval)

        out = []
        for ev in events:
            if len(ev["frames"]) < 2 and interval < 1:
                continue          # flicker / OCR noise
            boxes = np.array(ev["boxes"])
            moving = is_moving(ev["boxes"])
            if moving:
                # moving text: keep its path; the drawn box is the median size centred on each sample
                bw, bh = float(np.median(boxes[:, 2] - boxes[:, 0])), float(np.median(boxes[:, 3] - boxes[:, 1]))
                mid_box = boxes[len(boxes) // 2]
                box = (float(mid_box[0]), float(mid_box[1]), float(mid_box[0] + bw), float(mid_box[1] + bh))
            else:
                box = (float(np.percentile(boxes[:, 0], 20)), float(np.percentile(boxes[:, 1], 20)),
                       float(np.percentile(boxes[:, 2], 80)), float(np.percentile(boxes[:, 3], 80)))
            # most common OCR reading wins
            text = max(set(ev["texts"]), key=lambda x: (ev["texts"].count(x), len(x)))
            mid = ev["frames"][len(ev["frames"]) // 2]
            img = np.asarray(Image.open(files[mid]).convert("RGB"))
            mbox = ev["boxes"][len(ev["boxes"]) // 2]
            fill, outline = _colors(img, mbox, w, h)
            # captions linger and fade: cover from a little before the first sighting to well after the last
            e = {"id": len(out), "start": round(max(ev["start"] - 0.8 * interval, 0), 2),
                 "end": round(min(ev["end"] + 1.3 * interval, dur), 2), "box": [round(v, 4) for v in box],
                 "line_h": round(float(np.median(ev["lhs"])), 4), "rows": ev["rows"],
                 "src": text, "tgt": "", "fill": fill, "outline": outline,
                 "weight": _weight(img, mbox, w, h, fill),
                 "vertical": bool(ev.get("vertical"))}
            if moving:
                e["track"] = [[round(fr * interval, 2)] + [round(v, 4) for v in b]
                              for fr, b in zip(ev["frames"], ev["boxes"])]
            out.append(e)
    # merge events of the same text split by a missed frame
    out.sort(key=lambda e: e["start"])
    merged = []
    for e in out:
        p = next((m for m in reversed(merged) if m["src"] == e["src"] and _iou(m["box"], e["box"]) > 0.5
                  and e["start"] - m["end"] <= 2 * interval and "track" not in m and "track" not in e), None)
        if p:
            p["end"] = max(p["end"], e["end"])
        else:
            merged.append(e)
    for i, e in enumerate(merged):
        e["id"] = i
    n_move = sum(1 for e in merged if e.get("track"))
    job.log(f"Chữ trên màn hình: {len(merged)} đoạn ({'chữ Trung/Nhật/Hàn' if cjk_video else 'chữ Latin'})"
            + (f", {n_move} đoạn chữ chuyển động" if n_move else "")
            + f", quét {len(per_frame)} khung, mỗi {interval}s, OCR trên {getattr(ocr, 'device', '?')}"
            + (f" (dùng lại OCR {cache.hits} khung)" if cache.hits else ""))
    return merged


def _center(b):
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _size_close(a, b, tol=0.3):
    wa, ha, wb, hb = a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1]
    return abs(wa - wb) <= tol * max(wa, wb, 1e-6) and abs(ha - hb) <= tol * max(ha, hb, 1e-6)


def track(blocks_per_frame, interval):
    """Link text blocks across sampled frames into events.
    Static text: same place (IoU) + similar reading. Moving text (scrolling, flying titles): near-identical
    reading + same size, displaced by less than 35% of the frame between two samples."""
    events, active = [], []
    for i, blocks in enumerate(blocks_per_frame):
        t = i * interval
        still = []
        for b in blocks:
            match = None
            for ev in active:                     # 1) static: overlapping box, similar text
                if _iou(ev["boxes"][-1], b["box"]) > 0.35 and _sim(ev["texts"][-1], b["text"]) > 0.55:
                    match = ev
                    break
            if match is None:                     # 2) moving: same text & size, nearby
                for ev in active:
                    (ax, ay), (bx, by) = _center(ev["boxes"][-1]), _center(b["box"])
                    if _sim(ev["texts"][-1], b["text"]) > 0.75 and _size_close(ev["boxes"][-1], b["box"]) \
                            and abs(ax - bx) < 0.35 and abs(ay - by) < 0.35:
                        match = ev
                        break
            if match:
                match["end"] = t
                match["texts"].append(b["text"])
                match["boxes"].append(tuple(b["box"]))
                match["lhs"].append(b["lh"])
                match["frames"].append(i)
                still.append(match)
                active.remove(match)
            else:
                still.append({"start": t, "end": t, "texts": [b["text"]], "boxes": [tuple(b["box"])],
                              "lhs": [b["lh"]], "rows": len(b.get("rows") or [b["text"]]), "frames": [i],
                              "vertical": bool(b.get("vertical"))})
        events += active      # not seen this frame: closed
        active = still
    return events + active


def is_moving(boxes, thresh=0.03):
    """Did the block's centre travel more than `thresh` (fraction of the frame)?"""
    cs = [_center(b) for b in boxes]
    return max(abs(x - cs[0][0]) + abs(y - cs[0][1]) for x, y in cs) > thresh


def _serif(img, box, w, h, fill):
    """Does the original look like a serif / Song-style face (thin and thick strokes) rather than a
    uniform-stroke sans (Hei)? Coefficient of variation of the stroke width along the stroke centres."""
    import cv2
    x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)
    crop = img[max(y0, 0):max(y1, y0 + 1), max(x0, 0):max(x1, x0 + 1)].astype(int)
    if crop.size == 0 or min(crop.shape[:2]) < 8:
        return False
    hx = (fill or "#FFFFFF").lstrip("#")
    rgb = np.array([int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)])
    m = (np.abs(crop - rgb).sum(-1) < 90).astype(np.uint8)
    if m.mean() < 0.03:
        return False
    dist = cv2.distanceTransform(m, cv2.DIST_L2, 3)
    ridge = (dist >= cv2.dilate(dist, np.ones((3, 3), np.uint8)) - 1e-6) & (dist >= 1.0)
    v = dist[ridge]
    if len(v) < 30:
        return False
    return bool(v.std() / (v.mean() + 1e-6) > SERIF_COV)


SERIF_COV = 0.42


def _weight(img, box, w, h, fill):
    """Rough font weight from how much of the text box the fill colour covers: heavy | bold | regular."""
    x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)
    crop = img[max(y0, 0):max(y1, y0 + 1), max(x0, 0):max(x1, x0 + 1)].astype(int)
    if crop.size == 0:
        return "bold"
    hx = (fill or "#FFFFFF").lstrip("#")
    rgb = np.array([int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)])
    density = float((np.abs(crop - rgb).sum(-1) < 90).mean())
    return "heavy" if density > 0.34 else "bold" if density > 0.16 else "regular"


def speech_captions(events, band, lines, pad=0.03):
    """Ids of on-screen text events that are the burned-in SUBTITLES of the speech: inside the detected subtitle
    band and on screen while someone speaks. In dub / voice-over mode those must not be translated a second time:
    they are covered like subtitles and replaced by the Vietnamese subtitle of the speech (same words as the voice)."""
    if not band or not lines:
        return set()
    spans = [(float(l["start"]), float(l["end"])) for l in lines if l.get("start") is not None]
    out = set()
    for e in events:
        box = e.get("box")
        if not box:
            continue
        cy = (box[1] + box[3]) / 2
        if not (band["y0"] - pad <= cy <= band["y1"] + pad):
            continue
        if any(min(e["end"], b) - max(e["start"], a) > 0.2 for a, b in spans):
            out.add(e["id"])
    return out
