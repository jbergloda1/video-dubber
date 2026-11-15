"""Find platform watermarks burned into the original video (抖音号 / TikTok @user / 快手 … plus the logo next
to them), so they can be removed at render time with the same cleaners as on-screen text (LaMa / inpaint).

Detection (conservative — only things that are clearly a watermark are removed automatically):
  1. OCR a frame every 0.5–2 s and keep only readings that look like a watermark (platform names, @handles,
     "号: 12345", "ID: …").
  2. Track them over time (Douyin's watermark jumps from top-left to bottom-right: two items; a bouncing
     watermark becomes a moving item with a track).
  3. For a static one, edges that persist through the whole period (a percentile of the Sobel magnitude over
     frames) give the exact stroke shape of the text AND the logo beside it -> a pixel mask (wm_<n>.png).
Boxes you draw yourself in the job view (manual items) are kept across re-detection.
Everything lives in watermarks.json: {"items": [{id, start, end, box[x0,y0,x1,y1] (0..1), line_h, src,
enabled, auto|manual, mask?, track?}]}.
"""
import json
import re
import tempfile
from pathlib import Path

import numpy as np

WM_TEXT = re.compile(
    r"抖音|dou\s*yin|tik\s*tok|快手|kuaishou|小红书|西瓜视频|微博|weibo|bilibili|哔哩|b站|视频号|youtube|youtu\.be|"
    r"@[\w一-鿿.\-]{2,}|(?:号|ID|id)\s*[:：]\s*\w{3,}", re.I)
FILE = "watermarks.json"


def is_wm_text(t: str) -> bool:
    return bool(WM_TEXT.search(t or ""))


def _is_channel(text, uploader):
    """The video's own channel/author name burned in (e.g. 极限档案 on 极限档案's videos)."""
    import difflib
    a, b = re.sub(r"\W", "", text or "").lower(), re.sub(r"\W", "", uploader or "").lower()
    if len(a) < 2 or len(b) < 2:
        return False
    return a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


def _in_corner(l, w, h):
    """Small text near a corner of the frame (where channel logos live)."""
    cx, cy = (l["x0"] + l["x1"]) / 2 / w, (l["y0"] + l["y1"]) / 2 / h
    small = (l["y1"] - l["y0"]) / h < 0.08 and (l["x1"] - l["x0"]) / w < 0.45
    return small and (cx < 0.3 or cx > 0.7) and (cy < 0.2 or cy > 0.8)


def load(d: Path) -> list:
    p = d / FILE
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("items") or []
    except Exception:
        return []


def save(d: Path, items):
    for i, it in enumerate(items):
        it["id"] = i
    (d / FILE).write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1), encoding="utf-8")


def _overlap(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    area = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return inter / area if area > 0 else 0.0


def covers(event, items):
    """Is this on-screen text event really (part of) a watermark? Then it is removed, not translated."""
    import difflib
    src = event.get("src", "")
    if is_wm_text(src):
        return True
    for it in items:
        if not it.get("enabled", True):
            continue
        # the same text as a watermark (a moving one can be anywhere on its path) is that watermark
        a, b = re.sub(r"\W", "", src), re.sub(r"\W", "", it.get("src", ""))
        sm = difflib.SequenceMatcher(None, a, b)
        common = sm.find_longest_match(0, len(a), 0, len(b)).size if a and b else 0
        # same text, a partial reading ("阿史"), or a misread one ("汇阿史") sharing most of it
        if len(a) >= 2 and b and (a in b or b in a or sm.ratio() >= 0.6 or (common >= 2 and common >= 0.6 * len(a))):
            return True
        boxes = [it["box"]] + [t[1:] for t in it.get("track") or []]
        if any(_overlap(event["box"], bx) > 0.5 for bx in boxes) and \
                event["start"] < it["end"] and it["start"] < event["end"]:
            return True
    return False


def _persistent_edges(grays, idx, region):
    """Edges present in ≥70% of the given frames (30th percentile of the Sobel magnitude) inside region."""
    import cv2
    x0, y0, x1, y1 = region
    mags = []
    for i in idx:
        g = grays[i][y0:y1, x0:x1].astype(np.float32)
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        mags.append(cv2.magnitude(gx, gy))
    return np.percentile(np.stack(mags), 30, axis=0)


def _logo_left(E, box_px, thr):
    """Platform watermarks (Douyin, TikTok, Kuaishou…) put their logo LEFT of the text block, about as tall as
    the block. Width of that logo square if persistent edges are found there, else 0."""
    bx0, by0, bx1, by1 = box_px
    side = int((by1 - by0) * 1.15)
    x0 = max(0, bx0 - side)
    sq = E[by0:by1, x0:bx0]
    if sq.size == 0 or bx0 - x0 < side * 0.5:
        return 0
    return bx0 - x0 if (sq > thr).mean() > 0.04 else 0


def refine_static(grays, idx, box_px, lh_px, w, h, thr=60.0):
    """Extend the text box to the logo on its left, and build a removal mask: the text strokes (edges that
    persist through the whole period, thickened) + the whole logo square (semi-transparent logo plates
    have no inner edges). Returns (box_px, mask uint8 for that box or None)."""
    import cv2
    bx0, by0, bx1, by1 = box_px
    pad = max(2, int(lh_px * 0.15))
    side = int((by1 - by0) * 1.15)
    region = (max(0, bx0 - side - pad), max(0, by0 - pad), min(w, bx1 + pad), min(h, by1 + pad))
    rx0, ry0 = region[0], region[1]
    E = _persistent_edges(grays, idx, region)
    tb = (bx0 - rx0, by0 - ry0, bx1 - rx0, by1 - ry0)
    logo = _logo_left(E, tb, thr)
    ux0, uy0, ux1, uy1 = max(0, tb[0] - logo - pad), 0, E.shape[1], E.shape[0]
    binm = (E[uy0:uy1, ux0:ux1] > thr).astype(np.uint8) * 255
    mask = cv2.dilate(binm, np.ones((3, 3), np.uint8), iterations=max(2, int(lh_px * 0.08)))
    if logo:
        mask[:, : tb[0] - ux0] = 255
    box = (ux0 + rx0, uy0 + ry0, ux1 + rx0, uy1 + ry0)
    cover = mask.mean() / 255 if mask.size else 0
    return box, (mask if 0.03 < cover < 0.95 else None), logo


def _smooth_boxes(boxes, k=5):
    """Median filter over time: OCR sometimes reads only part of the text (box suddenly narrower)."""
    b = np.asarray(boxes, float)
    if len(b) < 3:
        return b
    r = k // 2
    return np.array([np.median(b[max(0, i - r):i + r + 1], axis=0) for i in range(len(b))])


def detect(job, video: Path, dur: float, s: dict, d: Path, step="watermark"):
    import cv2
    from . import screentext
    from .subdetect import _engine
    # moving watermarks need dense samples: between two samples their position is only interpolated
    interval = 0.5 if dur <= 90 else 1.0 if dur <= 900 else 2.0
    ocr = _engine()
    items = []
    try:
        uploader = (json.loads((d / "meta.json").read_text(encoding="utf-8")).get("uploader") or "").strip()
    except Exception:
        uploader = ""
    with tempfile.TemporaryDirectory() as td:
        files, w, h = screentext._frames(job, video, dur, interval, td, max_side=screentext.OCR_SIDE, step=step)
        cache = screentext.OcrCache(video, screentext.OCR_SIDE)      # shared with the on-screen text step
        per_frame, grays = [], []
        for i, f in enumerate(files):
            job.check_cancel()
            grays.append(cv2.imread(str(f), cv2.IMREAD_GRAYSCALE))
            res = cache.get(ocr, i * interval, f)
            lines = []
            for box, text, score in res or []:
                text = text.strip()
                if float(score) < 0.6 or not text:
                    continue
                xs, ys = [p[0] for p in box], [p[1] for p in box]
                l = {"text": text, "x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys)}
                # candidates: platform-style text, the channel/author name, or small text in a corner
                # (a channel logo like "极限档案"); centred captions and subtitles are not candidates
                if is_wm_text(text) or _is_channel(text, uploader) or _in_corner(l, w, h):
                    lines.append(l)
            per_frame.append(screentext._blocks(lines, w, h))
            if i % 5 == 0:
                job.step(step, progress=0.15 + 0.7 * (i + 1) / len(files), detail=f"Tìm watermark {i + 1}/{len(files)}")
        cache.save()
        events = _join(screentext.track(per_frame, interval), interval, max_gap=max(4, int(8 / interval)))
        min_frames = max(2, int(round(1.5 / interval)))
        for ev in events:
            if len(ev["frames"]) < min_frames:
                continue                       # a watermark stays on screen; a passing "@name" caption does not
            boxes = np.array(ev["boxes"])
            text = max(set(ev["texts"]), key=lambda x: (ev["texts"].count(x), len(x)))
            mb = np.median(boxes, axis=0)
            corner = _in_corner({"x0": mb[0] * w, "x1": mb[2] * w, "y0": mb[1] * h, "y1": mb[3] * h}, w, h)
            seen_s = len(ev["frames"]) * interval
            persistent = corner and seen_s >= max(8.0, 0.15 * dur) and not screentext.is_moving(ev["boxes"], 0.05)
            if not (is_wm_text(text) or _is_channel(text, uploader) or persistent):
                continue                       # ordinary on-screen text, not a watermark
            lh = float(np.median(ev["lhs"]))
            start = max(0.0, ev["start"] - 2 * interval)        # OCR misses a frame now and then
            end = min(dur, ev["end"] + 2 * interval)
            start = 0.0 if start <= 1.5 * interval else round(start, 2)
            end = round(dur, 2) if dur - end <= 1.5 * interval else round(end, 2)
            it = {"start": start, "end": end, "line_h": round(lh, 4), "src": text, "enabled": True, "auto": True}
            sm = _smooth_boxes(ev["boxes"])
            if screentext.is_moving([tuple(b) for b in sm], 0.03):
                # moving / bouncing watermark: remove the whole box (+ the logo square on its left) along its path
                bh = float(np.median(sm[:, 3] - sm[:, 1]))
                ext = bh * 1.15 * h / w
                tr = [[fr * interval, b[0] - ext, b[1], b[2], b[3]] for fr, b in zip(ev["frames"], sm)]
                it["track"] = [[round(v, 4) for v in row] for row in tr]
                it["box"] = [round(float(v), 4) for v in tr[0][1:]]
            else:
                b = (np.percentile(boxes[:, 0], 10) * w, np.percentile(boxes[:, 1], 10) * h,
                     np.percentile(boxes[:, 2], 90) * w, np.percentile(boxes[:, 3], 90) * h)
                idx = ev["frames"][:: max(1, len(ev["frames"]) // 20)]
                (x0, y0, x1, y1), mask, _ = refine_static(grays, idx, tuple(int(v) for v in b), lh * h, w, h)
                it["box"] = [round(x0 / w, 4), round(y0 / h, 4), round(x1 / w, 4), round(y1 / h, 4)]
                if mask is not None:
                    name = f"wm_{len(items)}.png"
                    cv2.imwrite(str(d / name), mask)
                    it["mask"] = name
            items.append(it)
    return _handover(_merge(items, interval, long_gap=0.3 * dur))


def _join(events, interval, max_gap=4):
    """Join track events at the same place a few samples apart: OCR sometimes reads only part of a watermark
    ("抖音" without the 抖音号 line), which the tracker sees as different text."""
    events = sorted(events, key=lambda e: e["start"])
    out = []
    for ev in events:
        mb = tuple(np.median(np.array(ev["boxes"]), axis=0))
        p = next((o for o in reversed(out) if _overlap(o["_mb"], mb) > 0.6 and
                  ev["start"] - o["end"] <= max_gap * interval), None)
        if p:
            for k in ("texts", "boxes", "lhs", "frames"):
                p[k] = p[k] + ev[k]
            p["end"] = max(p["end"], ev["end"])
            # the fuller reading defines the box
            p["_mb"] = tuple(np.median(np.array(p["boxes"]), axis=0))
        else:
            out.append({**ev, "_mb": mb})
    return out


def _handover(items, max_gap=3.0):
    """A watermark that jumps (Douyin: top-left -> bottom-right) leaves no gap: the first one stays until the
    second appears. Moving watermarks (one item per stretch of their path) get up to 8 s bridged."""
    items.sort(key=lambda i: i["start"])
    for a, b in zip(items, items[1:]):
        import difflib
        same = difflib.SequenceMatcher(None, a.get("src", ""), b.get("src", "")).ratio() >= 0.6
        gap = 8.0 if (same or a.get("track") or b.get("track")) else max_gap   # one watermark hopping around
        if 0 < b["start"] - a["end"] <= gap and a.get("src") and b.get("src"):
            mid = a["end"]
            a["end"], b["start"] = b["start"], mid
    return items


def _merge(items, interval, long_gap=0.0):
    """Same watermark split where OCR missed it -> one item. A static logo at the same place with the same text
    (a channel name) is joined across long gaps too: it was there all along, just unreadable on a busy
    background (rock, foliage)."""
    import difflib
    items.sort(key=lambda i: i["start"])
    out = []
    for it in items:
        def same(o):
            if "track" in o or "track" in it or _overlap(o["box"], it["box"]) <= 0.6:
                return False
            gap = it["start"] - o["end"]
            if gap <= 4 * interval:
                return True
            similar = difflib.SequenceMatcher(None, o.get("src", ""), it.get("src", "")).ratio() >= 0.6
            return similar and gap <= long_gap
        p = next((o for o in reversed(out) if same(o)), None)
        if p:
            p["end"] = max(p["end"], it["end"])
            p["box"] = [min(p["box"][0], it["box"][0]), min(p["box"][1], it["box"][1]),
                        max(p["box"][2], it["box"][2]), max(p["box"][3], it["box"][3])]
        else:
            out.append(it)
    return out


def for_render(d: Path, s: dict):
    """Enabled items as clean-layouts for textclean.make_clean."""
    if (s.get("wm_remove") or "auto") == "off":
        return []
    style = s.get("wm_clean") or "lama"
    out = []
    for it in load(d):
        if not it.get("enabled", True):
            continue
        x0, y0, x1, y1 = it["box"]
        L = {"start": float(it["start"]), "end": float(it["end"]), "box": [x0, y0, x1, y1],
             "line_h": float(it.get("line_h") or min(y1 - y0, 0.04)), "wm": True, "clean_style": style,
             "mask": it.get("mask"), "mask_path": str(d / it["mask"]) if it.get("mask") else None}
        if it.get("track"):
            L["track"] = it["track"]
        out.append(L)
    return out
