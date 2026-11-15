"""Remove on-screen text from the video so the translation can be drawn in its place.

For every frame where a text event is visible:
  - "inpaint" (default): build a mask of the text strokes (pixels close to the text fill / outline
    colours inside the original box, dilated a little), reconstruct the background there with
    OpenCV inpainting, and blend it back with a feathered mask -> no visible box.
  - "blur": soft-edged blur of the region (feathered, no hard rectangle).
  - "box": dark translucent box.
Frames are piped through ffmpeg (decode -> numpy/OpenCV -> NVENC), video only.
"""
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

from . import util


def _hex_bgr(h):
    h = (h or "#FFFFFF").lstrip("#")
    return np.array([int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)], dtype=np.int16)


def _fps(src):
    for st in util.probe(src)["streams"]:
        if st.get("codec_type") == "video":
            n, d = st.get("r_frame_rate", "30/1").split("/")
            f = float(n) / float(d or 1)
            return f if 1 < f < 121 else 30.0
    return 30.0


def signature(layouts, W, H, style, dur):
    key = [W, H, style, round(dur, 2)] + [[L["start"], L["end"], L["box"], L.get("cover"), L.get("fill"),
                                           L.get("outline"), L["line_h"], L.get("track"), L.get("mask"),
                                           L.get("clean_style")] for L in layouts]
    return hashlib.sha1(json.dumps(key).encode()).hexdigest()[:12]


def at_time(L, t):
    """The event as it is at time t (moving text: box interpolated along its track)."""
    if not L.get("track"):
        return L
    from .render import box_at
    x0, y0, x1, y1 = box_at(L, t)
    # the position between two OCR samples is interpolated: widen by half a sample's travel so fast
    # moving text is still fully covered
    tr = L["track"]
    seg = next(((a, b) for a, b in zip(tr, tr[1:]) if a[0] <= t <= b[0]), (tr[0], tr[-1]))
    dx = abs((seg[1][1] + seg[1][3]) - (seg[0][1] + seg[0][3])) / 4
    dy = abs((seg[1][2] + seg[1][4]) - (seg[0][2] + seg[0][4])) / 4
    return {**L, "box": [max(0.0, x0 - dx), max(0.0, y0 - dy), min(1.0, x1 + dx), min(1.0, y1 + dy)]}


def _region(L, W, H):
    """Area to clean: the original text box (padded) — where the old strokes are.
    OCR boxes are a little tight (and averaged over frames), so pad more vertically."""
    x0, y0, x1, y1 = L["box"]
    lh = L["line_h"] * H
    px, py = max(4, lh * 0.35), max(6, lh * 0.6)
    rx0, ry0 = int(max(0, x0 * W - px)), int(max(0, y0 * H - py))
    rx1, ry1 = int(min(W, x1 * W + px)), int(min(H, y1 * H + py))
    return rx0, ry0, rx1, ry1, lh


def _feather(mask, k):
    """0/255 mask -> soft 0..1 alpha (H, W, 1)."""
    import cv2
    k = int(k) | 1
    a = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (k, k), 0)
    return np.clip(a, 0.0, 1.0)[..., None]


def _hsv_of(hex_color):
    import cv2
    bgr = np.uint8([[_hex_bgr(hex_color).clip(0, 255)]])
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[0, 0].astype(int)


def _text_mask(roi, L):
    """Pixels that belong to the caption: its fill colour and its outline/glow colour (matched by hue,
    so every shade of an orange outline is caught), as a 0/255 mask."""
    import cv2
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV).astype(int)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    masks = []
    for col in (L.get("fill"), L.get("outline")):
        ch, cs, cv = _hsv_of(col)
        if cs < 60:                    # white / grey / black text: match by brightness
            if cv > 160:
                # light tints of the outline colour sit between "white" and "orange": keep them (s < 130)
                masks.append((v > cv - 60) & (s < 130))
            elif cv < 80:
                masks.append(v < cv + 45)
            else:
                masks.append((np.abs(v - cv) < 35) & (s < 70))
        else:                          # coloured: match the hue (OpenCV hue is 0..179, circular)
            dh = np.abs(h - ch)
            dh = np.minimum(dh, 180 - dh)
            # gradient outlines drift in hue (orange -> yellow): wide hue window, modest saturation
            masks.append((dh < 20) & (s > max(45, cs * 0.3)) & (v > 70))
    masks.append(_differs_from_background(roi))
    return (np.logical_or.reduce(masks) * 255).astype(np.uint8)


_MASKS = {}


def _mask(roi, L, rx0, ry0, W, H):
    """Mask of what to remove inside a region: caption strokes for on-screen text; for a watermark its
    stored stroke+logo mask (from persistent edges), or the whole box you drew."""
    if not L.get("wm"):
        return _text_mask(roi, L)
    import cv2
    m = np.zeros(roi.shape[:2], np.uint8)
    x0, y0, x1, y1 = L["box"]
    bx0, by0, bx1, by1 = int(x0 * W), int(y0 * H), int(round(x1 * W)), int(round(y1 * H))
    if bx1 - bx0 < 2 or by1 - by0 < 2:
        return m
    src = None
    if L.get("mask_path"):
        src = _MASKS.get(L["mask_path"])
        if src is None:
            src = cv2.imread(L["mask_path"], cv2.IMREAD_GRAYSCALE)
            _MASKS[L["mask_path"]] = src
    patch = (cv2.resize(src, (bx1 - bx0, by1 - by0), interpolation=cv2.INTER_LINEAR) > 60).astype(np.uint8) * 255         if src is not None else np.full((by1 - by0, bx1 - bx0), 255, np.uint8)
    # place the box patch into the region (clipped)
    ox, oy = bx0 - rx0, by0 - ry0
    sx0, sy0 = max(0, -ox), max(0, -oy)
    dx0, dy0 = max(0, ox), max(0, oy)
    hh = min(patch.shape[0] - sy0, m.shape[0] - dy0)
    ww = min(patch.shape[1] - sx0, m.shape[1] - dx0)
    if hh > 0 and ww > 0:
        m[dy0:dy0 + hh, dx0:dx0 + ww] = patch[sy0:sy0 + hh, sx0:sx0 + ww]
    return m


def _differs_from_background(roi, thresh=70):
    """Colour-independent rule: estimate the background from the region's border (text rarely touches it)
    and flag pixels far from it. Only used when that border is fairly uniform (sky, pitch, wall…)."""
    ring = np.concatenate([roi[:3].reshape(-1, 3), roi[-3:].reshape(-1, 3),
                           roi[:, :3].reshape(-1, 3), roi[:, -3:].reshape(-1, 3)]).astype(np.float32)
    if ring.std(0).max() > 35:
        return np.zeros(roi.shape[:2], bool)
    bg = np.median(ring, 0)
    return np.abs(roi.astype(np.float32) - bg).max(-1) > thresh


def _clean_inpaint(frame, L, W, H):
    import cv2
    rx0, ry0, rx1, ry1, lh = _region(L, W, H)
    roi = frame[ry0:ry1, rx0:rx1]
    if roi.size == 0:
        return
    mask = _mask(roi, L, rx0, ry0, W, H)
    # every heavy operation (dilate, inpaint, blurs) runs on a copy ≤ 360 px on its long side: their cost grows
    # with region size × kernel² — a huge region at full size could take minutes for a single frame
    sc = min(1.0, 360 / max(roi.shape[:2]))
    small = cv2.resize(roi, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA) if sc < 1 else roi
    sm = cv2.resize(mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST) if sc < 1 else mask
    ls = lh * sc
    k = max(3, int(ls * 0.2)) | 1
    sm = cv2.dilate(sm, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    cover = sm.mean() / 255
    if cover > (0.97 if L.get("wm") else 0.7) or cover < 0.01:
        # text colour ≈ background (or nothing found): inpainting would smear everything -> soft blur
        _clean_blur(frame, L, W, H)
        return
    fixed = cv2.inpaint(small, sm, int(min(10, max(3, ls * 0.12))), cv2.INPAINT_TELEA)
    # a light blur hides inpainting texture; feathered mask hides the seam
    fixed = cv2.GaussianBlur(fixed, (0, 0), max(1.0, ls * 0.06))
    a = _feather(sm, max(5, ls * 0.25) if sc == 1 else max(3, ls * 0.25))
    if sc < 1:
        fixed = cv2.resize(fixed, (roi.shape[1], roi.shape[0]), interpolation=cv2.INTER_LINEAR)
        a = cv2.resize(a, (roi.shape[1], roi.shape[0]), interpolation=cv2.INTER_LINEAR)[..., None]
    frame[ry0:ry1, rx0:rx1] = (roi * (1 - a) + fixed * a).astype(np.uint8)


def _clean_blur(frame, L, W, H):
    import cv2
    rx0, ry0, rx1, ry1, lh = _region(L, W, H)
    if rx1 - rx0 < 4 or ry1 - ry0 < 4:
        return
    # blur a slightly larger area so the feathered edge has real pixels to fade into
    m = int(lh * 0.6)
    bx0, by0, bx1, by1 = max(0, rx0 - m), max(0, ry0 - m), min(W, rx1 + m), min(H, ry1 + m)
    roi = frame[by0:by1, bx0:bx1]
    rect = np.zeros(roi.shape[:2], np.uint8)
    rect[ry0 - by0:ry1 - by0, rx0 - bx0:rx1 - bx0] = 255
    # blur + feather on a small copy (≤ 320 px), then scale back: same look, bounded cost for huge regions
    sc = min(1.0, 320 / max(roi.shape[:2]))
    if sc < 1:
        small = cv2.resize(roi, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
        rs = cv2.resize(rect, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
        blurred = cv2.resize(cv2.GaussianBlur(small, (0, 0), max(3, lh * 0.45 * sc)), (roi.shape[1], roi.shape[0]),
                             interpolation=cv2.INTER_LINEAR)
        a = cv2.resize(_feather(rs, max(3, lh * 0.9 * sc)), (roi.shape[1], roi.shape[0]),
                       interpolation=cv2.INTER_LINEAR)[..., None]
    else:
        blurred = cv2.GaussianBlur(roi, (0, 0), max(6, lh * 0.45))
        a = _feather(rect, max(9, lh * 0.9))
    frame[by0:by1, bx0:bx1] = (roi * (1 - a) + blurred * a).astype(np.uint8)


def _clean_box(frame, L, W, H):
    rx0, ry0, rx1, ry1, _ = _region(L, W, H)
    roi = frame[ry0:ry1, rx0:rx1]
    frame[ry0:ry1, rx0:rx1] = (roi * 0.25).astype(np.uint8)


# ------------------------------------------------------------------ LaMa (AI inpainting on the GPU)
LAMA_URL = "https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt"
LAMA_MD5 = "e3aa4aaa15225a33ec84f9f4bc47e500"     # published by IOPaint / lama-cleaner
_lama_model = None


def lama_path():
    from . import config
    return config.DATA / "models" / "lama" / "big-lama.pt"


def ensure_lama(job=None):
    """Download big-lama (~196 MB) once and verify its checksum."""
    import hashlib
    import urllib.request
    p = lama_path()
    if p.exists():
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    if job:
        job.log("Tải model LaMa (~196 MB, chỉ lần đầu)…")
    tmp = p.with_suffix(".part")
    with urllib.request.urlopen(LAMA_URL, timeout=60) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    md5 = hashlib.md5(tmp.read_bytes()).hexdigest()
    if md5 != LAMA_MD5:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"File model LaMa không đúng (md5 {md5})")
    tmp.replace(p)
    return p


def _lama():
    global _lama_model
    if _lama_model is None:
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("LaMa cần GPU (PyTorch không thấy CUDA)")
        torch.backends.cudnn.benchmark = True
        _lama_model = torch.jit.load(str(ensure_lama()), map_location="cuda").eval()
    return _lama_model


def unload_lama():
    global _lama_model
    _lama_model = None


def _boundary_fill(roi, mask):
    """Cheap low-resolution OpenCV inpaint: smooth colours interpolated from the hole's boundary (float32)."""
    import cv2
    k = 0.25
    small = cv2.resize(roi, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    sm = cv2.resize(mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
    sm = cv2.dilate(sm, np.ones((3, 3), np.uint8))
    filled = cv2.inpaint(small, sm, 3, cv2.INPAINT_TELEA)
    return cv2.resize(filled, (roi.shape[1], roi.shape[0]), interpolation=cv2.INTER_LINEAR).astype(np.float32)


def _clean_lama_batch(frames, L, W, H):
    """Inpaint the same text region in several frames with one LaMa call."""
    import cv2
    import torch
    rx0, ry0, rx1, ry1, lh = _region(L, W, H)
    # give LaMa some surrounding context
    m = int(max(16, lh * 0.8))
    cx0, cy0, cx1, cy1 = max(0, rx0 - m), max(0, ry0 - m), min(W, rx1 + m), min(H, ry1 + m)
    cw, ch = cx1 - cx0, cy1 - cy0
    if cw < 8 or ch < 8:
        return
    k = min(1.0, 512 / max(cw, ch), 192 / ch)            # keep crops small: fast, and LaMa likes ≤512
    sw, sh = max(8, int(round(cw * k / 8)) * 8), max(8, int(round(ch * k / 8)) * 8)
    crops, masks, masks_full, alphas, keep = [], [], [], [], []
    kd = max(5, int(lh * 0.3)) | 1          # wider than OpenCV: LaMa keeps (and extends) any stroke left outside
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kd, kd))
    for n, fr in enumerate(frames):
        roi = fr[cy0:cy1, cx0:cx1]
        mask = np.zeros((ch, cw), np.uint8)
        mask[ry0 - cy0:ry1 - cy0, rx0 - cx0:rx1 - cx0] = _mask(fr[ry0:ry1, rx0:rx1], L, rx0, ry0, W, H)
        mask = cv2.dilate(mask, kernel)
        cover = mask[ry0 - cy0:ry1 - cy0, rx0 - cx0:rx1 - cx0].mean() / 255
        if cover < 0.01:
            continue
        if cover > (0.97 if L.get("wm") else 0.9):          # whole region masked (text colour ≈ background): nothing to inpaint from -> soft blur
            _clean_blur(fr, L, W, H)
            continue
        crops.append(cv2.resize(roi, (sw, sh), interpolation=cv2.INTER_AREA))
        masks.append(cv2.resize(mask, (sw, sh), interpolation=cv2.INTER_NEAREST))
        masks_full.append(mask)
        alphas.append(_feather(mask, max(7, lh * 0.4)))
        keep.append(n)
    if not keep:
        return
    model = _lama()
    with torch.inference_mode():
        img = torch.from_numpy(np.stack(crops)[..., ::-1].copy()).cuda().permute(0, 3, 1, 2).float() / 255
        msk = (torch.from_numpy(np.stack(masks)).cuda().float() / 255 > 0.5).float()[:, None]
        res = model(img, msk)
        res = (res.clamp(0, 1) * 255).byte().permute(0, 2, 3, 1).cpu().numpy()[..., ::-1]
    for j, n in enumerate(keep):
        fr = frames[n]
        roi = fr[cy0:cy1, cx0:cx1]
        fixed = cv2.resize(np.ascontiguousarray(res[j]), (cw, ch), interpolation=cv2.INTER_CUBIC).astype(np.float32)
        # LaMa tends to fill big holes a bit lighter than the surroundings (a visible band). Keep LaMa's detail
        # (people, objects) but take the low-frequency colour from a boundary-interpolated fill, which by
        # construction matches the pixels around the hole.
        sig = max(4.0, lh * 0.7)
        base = _boundary_fill(roi, masks_full[j])
        fixed = fixed - cv2.GaussianBlur(fixed, (0, 0), sig) + cv2.GaussianBlur(base, (0, 0), sig)
        fixed = np.clip(fixed, 0, 255)
        a = alphas[j]
        fr[cy0:cy1, cx0:cx1] = (roi * (1 - a) + fixed * a).astype(np.uint8)


CLEANERS = {"inpaint": _clean_inpaint, "blur": _clean_blur, "box": _clean_box}


def make_clean(job, workdir: Path, src: Path, layouts, W, H, style, dur, step, s, t0=0.0, prog=None):
    """Write clean_<sig>.mp4 (video only, W×H) with the original on-screen text removed, for the time window
    [t0, t0+dur] of the source (frame times inside are absolute)."""
    sig = signature(layouts, W, H, style, dur) + (f"_{int(t0 * 1000)}" if t0 else "")
    out = workdir / f"clean_{sig}.mp4"
    if out.exists():
        return out
    fps = _fps(src)
    n_frames = int(dur * fps)
    prog = prog or (lambda i, n: job.step(step, progress=0.5 * min(i / max(n, 1), 1.0),
                                          detail=f"Xoá chữ gốc {i}/{n} khung"))
    style_of = lambda L: L.get("clean_style") or style       # watermarks may use another cleaner
    any_lama = any(style_of(L) == "lama" for L in layouts)
    dec = subprocess.Popen(["ffmpeg", "-v", "error", *(["-ss", f"{t0:.3f}"] if t0 else []), "-i", str(src),
                            "-t", f"{dur:.3f}", "-an",
                            "-vf", f"scale={W}:{H}:flags=lanczos,setsar=1", "-fps_mode", "cfr", "-r", f"{fps:.5f}",
                            "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                           stdout=subprocess.PIPE, creationflags=util.CREATE_NO_WINDOW)
    tmp = out.with_suffix(".part.mp4")
    venc = (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "17"] if s.get("video_encoder") == "h264_nvenc"
            else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16"])
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                            "-r", f"{fps:.5f}", "-i", "-", *venc, "-pix_fmt", "yuv420p", str(tmp)],
                           stdin=subprocess.PIPE, creationflags=util.CREATE_NO_WINDOW)
    job.proc = dec
    size = W * H * 3
    i = 0
    # frames are read in batches; OpenCV cleaners run on several frames at once (they release the GIL)
    batch = 8 if any_lama else 24
    from concurrent.futures import ThreadPoolExecutor
    import os
    pool = ThreadPoolExecutor(max(2, min(8, (os.cpu_count() or 4) - 2)))
    if any_lama:
        _lama()       # load the model once, fail early (caller falls back to OpenCV)
    try:
        done = False
        while not done:
            # read a chunk of frames (LaMa runs them as one GPU batch per text region)
            frames, times = [], []
            while len(frames) < batch:
                buf = dec.stdout.read(size)
                if len(buf) < size:
                    done = True
                    break
                frames.append(buf)
                times.append(t0 + (i + len(frames) - 1) / fps)
            if not frames:
                break
            arrs = [None] * len(frames)
            for L in layouts:
                idx = [k for k, t in enumerate(times) if L["start"] <= t <= L["end"]]
                if not idx:
                    continue
                for k in idx:
                    if arrs[k] is None:
                        arrs[k] = np.frombuffer(frames[k], np.uint8).reshape(H, W, 3).copy()
                st = style_of(L)
                if st == "lama":
                    if L.get("track"):          # moving text: its region changes every frame
                        for k in idx:
                            _clean_lama_batch([arrs[k]], at_time(L, times[k]), W, H)
                    else:
                        _clean_lama_batch([arrs[k] for k in idx], L, W, H)
                else:
                    fn = CLEANERS.get(st, _clean_inpaint)
                    list(pool.map(lambda k: fn(arrs[k], at_time(L, times[k]), W, H), idx))
            frames = [a.tobytes() if a is not None else f for a, f in zip(arrs, frames)]
            for f in frames:
                enc.stdin.write(f)
            prev, i = i, i + len(frames)
            if i // 15 != prev // 15:
                job.check_cancel()
                prog(i, n_frames)
    finally:
        pool.shutdown(wait=False)
        try:
            enc.stdin.close()
        except Exception:
            pass
        enc.wait()
        dec.kill()
        job.proc = None
    if enc.returncode != 0 or not tmp.exists():
        raise RuntimeError("Không tạo được video đã xoá chữ gốc")
    tmp.replace(out)
    return out


def windows(layouts, dur, pad=0.3, merge_gap=2.0):
    """Time ranges that contain any text/watermark to clean (padded, merged when close)."""
    iv = sorted((max(0.0, L["start"] - pad), min(dur, L["end"] + pad)) for L in layouts if L["start"] < dur)
    out = []
    for a, b in iv:
        if out and a - out[-1][1] <= merge_gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out if b - a > 0.05]


def make_clean_segments(job, workdir: Path, src: Path, layouts, W, H, style, dur, step, s):
    """Clean ONLY the time ranges where text is on screen (the rest of the video never goes through Python).
    Returns [(file name, start, end)] to overlay on the source at those times. A video with text almost all
    the time is done in one piece."""
    wins = windows(layouts, dur)
    if not wins:
        return []
    covered = sum(b - a for a, b in wins)
    if covered > 0.7 * dur:
        wins = [(0.0, dur)]
    fps = _fps(src)
    total = int(sum(b - a for a, b in wins) * fps)
    done = [0]
    out = []
    for a, b in wins:
        part = [L for L in layouts if L["start"] < b and L["end"] > a]

        def prog(i, n, base=done[0]):
            job.step(step, progress=0.5 * min((base + i) / max(total, 1), 1.0),
                     detail=f"Xoá chữ gốc {base + i}/{total} khung ({len(wins)} đoạn có chữ)")
        f = make_clean(job, workdir, src, part, W, H, style, b - a, step, s, t0=a, prog=prog)
        done[0] += int((b - a) * fps)
        out.append((f.name, a, b))
    keep = {f for f, _, _ in out}
    for p in workdir.glob("clean_*.mp4"):            # older cleaned pieces (other settings / preview)
        if p.name not in keep:
            p.unlink(missing_ok=True)
    job.log(f"Xoá chữ gốc: {len(wins)} đoạn có chữ, {sum(b - a for a, b in wins):.0f}s / {dur:.0f}s video", "debug")
    return out
