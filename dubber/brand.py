"""Your own moving watermark on the final video: your handle(s) as text (TikTok / YouTube / Facebook), no logos.

Badges are drawn once as transparent PNGs with Pillow (simple generic icons, Vietnamese-safe fonts), then
overlaid by ffmpeg with time-based x/y expressions:
  - "bounce":  drifts slowly across the frame, bouncing off the edges (hard to crop or blur out)
  - "corners": hops to the next corner every few seconds
  - "static":  stays in the top-right corner
With several handles, "cycle" shows one platform at a time (switching every brand_cycle_s), "stack" shows
them all in one badge. The badge stays out of the bottom 20% so it never sits on the subtitles.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PLATFORMS = ("tiktok", "youtube", "facebook")
FONTS = Path("C:/Windows/Fonts")


def items(s):
    out = []
    for p in PLATFORMS:
        name = (s.get(f"brand_{p}") or "").strip()
        if name:
            if p in ("tiktok", "youtube") and not name.startswith("@"):
                name = "@" + name
            out.append((p, name))
    return out


def _font(size, bold=True):
    for f in (("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")):
        if (FONTS / f).exists():
            return ImageFont.truetype(str(FONTS / f), int(size))
    return ImageFont.load_default()


def _icon(platform, size):
    """Generic platform glyph, `size` px square, RGBA (drawn 4× and downscaled for smooth edges)."""
    k = 4
    S = size * k
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if platform == "youtube":
        h = int(S * 0.72)
        y0 = (S - h) // 2
        d.rounded_rectangle((0, y0, S - 1, y0 + h), radius=int(h * 0.28), fill=(255, 0, 0, 255))
        cx, cy, r = S * 0.53, S / 2, h * 0.24
        d.polygon([(cx - r * 0.8, cy - r), (cx - r * 0.8, cy + r), (cx + r, cy)], fill=(255, 255, 255, 255))
    elif platform == "facebook":
        d.ellipse((0, 0, S - 1, S - 1), fill=(24, 119, 242, 255))
        f = _font(S * 0.95)
        d.text((S * 0.56, S * 0.60), "f", font=f, fill=(255, 255, 255, 255), anchor="mm")
    else:   # tiktok: dark rounded square with a two-tone music note
        # light rim so the dark square still reads on dark footage
        d.rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * 0.24), fill=(10, 10, 10, 255),
                            outline=(255, 255, 255, 230), width=max(2, int(S * 0.06)))
        note = None
        for name in ("seguisym.ttf", "segoeui.ttf", "arial.ttf"):
            if (FONTS / name).exists():
                note = ImageFont.truetype(str(FONTS / name), int(S * 0.72))
                break
        c = (S / 2, S / 2)
        off = S * 0.035
        d.text((c[0] - off, c[1] - off), "♪", font=note, fill=(37, 244, 238, 255), anchor="mm")
        d.text((c[0] + off, c[1] + off), "♪", font=note, fill=(254, 44, 85, 255), anchor="mm")
        d.text(c, "♪", font=note, fill=(255, 255, 255, 255), anchor="mm")
    return im.resize((size, size), Image.LANCZOS)


def badge(rows, text_px):
    """rows: [(platform, name)] -> RGBA image: just the handle text (no logos), white with a soft dark outline."""
    f = _font(text_px)
    pad = int(text_px * 0.45)
    row_h = int(text_px * 1.35)
    W = max(int(f.getlength(n)) for _, n in rows) + 2 * pad
    H = row_h * len(rows) + 2 * pad - (row_h - text_px)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ds = ImageDraw.Draw(sh)
    for i, (_, n) in enumerate(rows):
        ds.text((pad, pad + i * row_h + text_px / 2), n, font=f, fill=(0, 0, 0, 200), anchor="lm",
                stroke_width=max(2, text_px // 8), stroke_fill=(0, 0, 0, 200))
    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(max(1, text_px // 10))))
    d = ImageDraw.Draw(im)
    for i, (_, n) in enumerate(rows):
        d.text((pad, pad + i * row_h + text_px / 2), n, font=f, fill=(255, 255, 255, 255), anchor="lm",
               stroke_width=max(1, text_px // 14), stroke_fill=(0, 0, 0, 230))
    return im


def make_badges(workdir: Path, s, W, H):
    """Write brand_<n>.png files; returns their names (one per platform when cycling, else one)."""
    its = items(s)
    if not s.get("brand_enabled") or not its:
        return []
    text_px = max(14, int(min(W, H) * float(s.get("brand_size") or 0.03)))
    groups = [[it] for it in its] if (s.get("brand_show") or "cycle") == "cycle" else [its]
    names = []
    for i, g in enumerate(groups):
        name = f"brand_{i}.png"
        badge(g, text_px).save(workdir / name)
        names.append(name)
    return names


def position(motion, m=0.03):
    """ffmpeg overlay x/y expressions (single-quoted) for the chosen motion."""
    mx, my = f"main_w*{m}", f"main_h*{m}"
    span_x = f"(main_w-overlay_w-2*{mx})"
    span_y = f"(main_h*0.80-overlay_h-{my})"       # keep clear of the subtitle area
    if motion == "static":
        return f"main_w-overlay_w-{mx}", my
    if motion == "corners":
        c = "mod(floor(t/6),4)"                        # TL -> TR -> BR -> BL, 6 s each
        return f"{mx}+{span_x}*between({c},1,2)", f"{my}+{span_y}*gte({c},2)"
    # bounce: two triangle waves with unrelated periods -> slow diagonal drift that covers the frame
    return f"{mx}+{span_x}*abs(mod(t/11,2)-1)", f"{my}+{span_y}*abs(mod(t/17+0.5,2)-1)"


def filters(names, first_input, last, s):
    """filter_complex pieces overlaying badge inputs [first_input…] on [last]; returns (filters, new_last)."""
    if not names:
        return [], last
    op = max(0.1, min(1.0, float(s.get("brand_opacity") or 0.75)))
    x, y = position(s.get("brand_motion") or "bounce")
    cyc = max(2.0, float(s.get("brand_cycle_s") or 5))
    n = len(names)
    out = []
    for i in range(n):
        tag = f"bg{i}"
        out.append(f"[{first_input + i}:v]format=rgba,colorchannelmixer=aa={op:.2f}[{tag}]")
        en = f":enable='between(mod(t,{n * cyc:.2f}),{i * cyc:.2f},{(i + 1) * cyc - 0.001:.3f})'" if n > 1 else ""
        out.append(f"[{last}][{tag}]overlay=x='{x}':y='{y}':shortest=1{en}[bo{i}]")
        last = f"bo{i}"
    return out, last
