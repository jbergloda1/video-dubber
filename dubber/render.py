"""Build subtitles (ASS) and render preview / final video with ffmpeg."""
import os
from pathlib import Path

from . import tts, util


def _ass_color(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def out_size(W, H, short_side):
    s = min(W, H)
    if s <= short_side:
        w, h = W, H
    else:
        k = short_side / s
        w, h = W * k, H * k
    return int(round(w / 2) * 2), int(round(h / 2) * 2)


def sub_times(lines, mode):
    if mode in ("dub", "voiceover"):
        return tts.sub_times(lines)
    res = []
    for i, l in enumerate(lines):
        end = l["end"] + 0.4
        if i + 1 < len(lines):
            end = min(end, lines[i + 1]["start"])
        res.append((l["start"], max(end, l["start"] + 0.6)))
    return res


def sub_cues(lines, mode):
    """(start, end, text) of every subtitle cue: a long sentence is shown in several cues (see subsplit)."""
    from . import subsplit
    out = []
    for l, (a, b) in zip(lines, sub_times(lines, mode)):
        text = l["tgt"].replace("\n", " ").strip()
        if not text:
            continue
        read_end = l.get("vend") if mode in ("dub", "voiceover") else min(l["end"], b)
        out += subsplit.cues(text, a, b, read_end)
    return out


def write_ass(lines, path: Path, W, H, band, s, mode, extra_rows=()):
    short = min(W, H)
    fs = max(18, int(short * float(s["sub_font_scale"])))
    outline = max(1.5, fs * 0.07)
    if band:
        # centre the text vertically in the band we blurred
        band_mid = (band["y0"] + band["y1"]) / 2 * H
        margin_v = int(H - band_mid - fs * 0.55)
    else:
        margin_v = int(H * 0.07)
    margin_lr = int(W * 0.06)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{s['sub_font']},{fs},{_ass_color(s['sub_color'])},&H000000FF,{_ass_color(s['sub_outline_color'])},&H64000000,-1,0,0,0,100,100,0,0,1,{outline:.1f},{outline*0.4:.1f},2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = []
    box = (s.get("sub_style") or "box") == "box"
    for a, b, text in sub_cues(lines, mode):
        text = text.replace("{", "(").replace("}", ")")
        if box:
            rows += box_rows(text, _ass(a), _ass(b), W, H, fs, margin_v, margin_lr, s)
        else:
            rows.append(f"Dialogue: 0,{_ass(a)},{_ass(b)},Default,,0,0,0,,{text}")
    rows += list(extra_rows)
    path.write_text(head + "\n".join(rows) + "\n", encoding="utf-8-sig")


def rounded_rect(x0, y0, x1, y1, r):
    """ASS vector drawing (\\p1) of a rounded rectangle; corners are cubic béziers."""
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    k = r * 0.4477                       # 1 - 0.5523: control point offset for a quarter circle
    f = lambda v: f"{v:.0f}"
    return (f"m {f(x0 + r)} {f(y0)} l {f(x1 - r)} {f(y0)} b {f(x1 - k)} {f(y0)} {f(x1)} {f(y0 + k)} {f(x1)} {f(y0 + r)} "
            f"l {f(x1)} {f(y1 - r)} b {f(x1)} {f(y1 - k)} {f(x1 - k)} {f(y1)} {f(x1 - r)} {f(y1)} "
            f"l {f(x0 + r)} {f(y1)} b {f(x0 + k)} {f(y1)} {f(x0)} {f(y1 - k)} {f(x0)} {f(y1 - r)} "
            f"l {f(x0)} {f(y0 + r)} b {f(x0)} {f(y0 + k)} {f(x0 + k)} {f(y0)} {f(x0 + r)} {f(y0)}")


def box_rows(text, a, b, W, H, fs, margin_v, margin_lr, s):
    """One subtitle as a white rounded card (drawn over the blurred original subtitles) with dark text on it.
    Lines are wrapped here (not by libass) so the card fits the text exactly."""
    font = _measure_font(s["sub_font"], fs, True)
    padx, pady = fs * 0.6, fs * 0.32
    lines = _wrap(text, font, W - 2 * margin_lr - 2 * padx) or [text]
    if len(lines) > 1:                   # balance: "long long long / x" -> two similar lines
        words = text.split()
        best = min(range(1, len(words)), key=lambda i: abs(font.getlength(" ".join(words[:i])) - font.getlength(" ".join(words[i:]))))
        two = [" ".join(words[:best]), " ".join(words[best:])]
        if len(lines) == 2 and max(map(font.getlength, two)) <= W - 2 * margin_lr - 2 * padx:
            lines = two
    lh = fs * 1.2
    bw = max(font.getlength(x) for x in lines) + 2 * padx
    bh = len(lines) * lh + 2 * pady
    cx = W / 2
    y1 = H - margin_v + pady             # the bottom of the text block sits at margin_v, as with the outline style
    y0 = y1 - bh
    x0, x1 = cx - bw / 2, cx + bw / 2
    col = lambda h: "&H" + _ass_color(h)[4:] + "&"          # "&H00BBGGRR" -> "&HBBGGRR&" for \1c
    bg, fg = col(s.get("sub_box_color") or "#FFFFFF"), col(s.get("sub_box_text_color") or "#111111")
    alpha = f"&H{int(round((1 - float(s.get('sub_box_opacity', 0.92))) * 255)):02X}&"
    shade = rounded_rect(x0, y0 + fs * 0.08, x1, y1 + fs * 0.08, fs * 0.5)
    card = rounded_rect(x0, y0, x1, y1, fs * 0.5)
    body = "\\N".join(lines)
    return [f"Dialogue: 0,{a},{b},Default,,0,0,0,,{{\\an7\\pos(0,0)\\bord0\\shad0\\blur3\\1c&H000000&\\1a&HB0&\\p1}}{shade}",
            f"Dialogue: 1,{a},{b},Default,,0,0,0,,{{\\an7\\pos(0,0)\\bord0\\shad0\\1c{bg}\\1a{alpha}\\p1}}{card}",
            f"Dialogue: 2,{a},{b},Default,,0,0,0,,{{\\an5\\pos({cx:.0f},{(y0 + y1) / 2:.0f})\\bord0\\shad0\\1c{fg}\\q2}}{body}"]


# ------------------------------------------------------------------ on-screen text (redraw in place)
_FONT_FILES = {   # (font name, bold) -> file used to MEASURE text width (rendering is done by libass)
    ("arial", True): "arialbd.ttf", ("arial", False): "arial.ttf", ("arial black", True): "ariblk.ttf",
    ("arial black", False): "ariblk.ttf", ("segoe ui", True): "segoeuib.ttf", ("segoe ui", False): "segoeui.ttf",
    ("segoe ui black", True): "seguibl.ttf", ("tahoma", True): "tahomabd.ttf", ("tahoma", False): "tahoma.ttf",
    ("verdana", True): "verdanab.ttf", ("verdana", False): "verdana.ttf", ("times new roman", True): "timesbd.ttf",
    ("times new roman", False): "times.ttf", ("impact", True): "impact.ttf", ("impact", False): "impact.ttf"}
FONTS_DIR = Path("C:/Windows/Fonts")


APP_FONTS = Path(__file__).resolve().parent.parent / "data" / "fonts"
# family -> {css weight: file}; libass picks the face by family name + \\b<weight> (fontsdir=data/fonts)
BUNDLED = {
    "Be Vietnam Pro": {300: "BeVietnamPro-Light.ttf", 400: "BeVietnamPro-Regular.ttf",
                       500: "BeVietnamPro-Medium.ttf", 600: "BeVietnamPro-SemiBold.ttf",
                       700: "BeVietnamPro-Bold.ttf", 800: "BeVietnamPro-ExtraBold.ttf"},
    "Noto Serif": {500: "NotoSerif-Medium.ttf", 600: "NotoSerif-SemiBold.ttf", 700: "NotoSerif-Bold.ttf"},
}


def _measure_font(name, size, bold=True):
    """PIL font used to MEASURE the text (libass draws it). `bold` may be True/False or a css weight."""
    from PIL import ImageFont
    fam = BUNDLED.get(name)
    if fam:
        wt = bold if isinstance(bold, int) and not isinstance(bold, bool) else (700 if bold else 500)
        f = fam[min(fam, key=lambda k: abs(k - wt))]
        if (APP_FONTS / f).exists():
            return ImageFont.truetype(str(APP_FONTS / f), int(size))
    f = _FONT_FILES.get((name.lower(), bool(bold)), "arialbd.ttf" if bold else "arial.ttf")
    for p in (FONTS_DIR / f, FONTS_DIR / "arialbd.ttf"):
        if p.exists():
            return ImageFont.truetype(str(p), int(size))
    return ImageFont.load_default()


def pick_font(e, s):
    """Font for redrawn on-screen text: your choice, or matched to the original's weight with Be Vietnam Pro
    (designed for Vietnamese): thin elegant captions -> Regular, bold -> Bold, heavy titles -> ExtraBold.
    Returns (family, css weight)."""
    choice = (s.get("screen_text_font") or "auto").strip()
    weight = e.get("weight") or "bold"
    if choice.lower() != "auto":
        if choice == "Be Vietnam Pro":
            return choice, {"heavy": 800, "bold": 700, "regular": 400}[weight]
        if choice in BUNDLED:
            return choice, {"heavy": 700, "bold": 700, "regular": 500}[weight]
        return choice, 700
    if (APP_FONTS / BUNDLED["Be Vietnam Pro"][700]).exists():
        return "Be Vietnam Pro", {"heavy": 800, "bold": 700, "regular": 400}[weight]
    return ("Arial Black", 900) if weight == "heavy" and (FONTS_DIR / "ariblk.ttf").exists() else ("Arial", 700)


def box_at(L, t):
    """Box of a (possibly moving) text event at time t: linear interpolation along its sampled track."""
    tr = L.get("track")
    if not tr:
        return L["box"]
    if t <= tr[0][0]:
        return tr[0][1:]
    for a, b in zip(tr, tr[1:]):
        if a[0] <= t <= b[0]:
            k = (t - a[0]) / max(b[0] - a[0], 1e-6)
            return [a[i] + (b[i] - a[i]) * k for i in range(1, 5)]
    return tr[-1][1:]


def _wrap(text, font, maxw):
    words, lines, cur = text.split(), [], ""
    for wd in words:
        t = (cur + " " + wd).strip()
        if font.getlength(t) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


def screen_text_layout(events, W, H, s):
    """Pick font size + line breaks so the translation fits where the original text was."""
    out = []
    for e in events:
        if not e.get("tgt"):
            continue
        x0, y0, x1, y1 = e["box"]
        bw, bh = (x1 - x0) * W, (y1 - y0) * H
        lh = max(e["line_h"] * H, 10)
        rows = max(int(e.get("rows") or 1), 1)
        cx, cy = (x0 + x1) / 2 * W, (y0 + y1) / 2 * H
        margin = min(W, H) * 0.04
        if e.get("vertical"):
            # a vertical column of characters: Vietnamese can't be written vertically -> a short horizontal
            # block centred on the column, sized like one character of it
            maxw = min(max(bw * 3.2, W * 0.28), W * 0.5)
            bw, bh = maxw, max(bh * 0.6, lh * 2)
            rows = 1
        else:
            maxw = min(max(bw * 1.06, W * 0.5), W - 2 * margin)
        # a Latin font at the same size looks bigger than CJK (taller ascenders, wider words): scale down
        fs0 = lh * (0.85 if e.get("vertical") else 0.82)
        fname, bold = pick_font(e, s)
        best = None
        # pass 1: at most one extra line, font ≥ 70% of the original; pass 2: two extra lines, ≥ 55%
        for max_lines, min_k in ((rows + 1, 0.7), (rows + 2, 0.55)):
            fs = fs0
            while fs >= fs0 * min_k and not best:
                font = _measure_font(fname, fs, bold)
                lines = _wrap(e["tgt"], font, maxw)
                height = len(lines) * fs * 1.22
                if len(lines) <= max_lines and height <= max(bh * 1.7, lh * rows * 1.8):
                    best = (fs, lines, font)
                fs *= 0.94
            if best:
                break
        if not best:
            font = _measure_font(fname, fs0 * 0.5, bold)
            best = (fs0 * 0.5, _wrap(e["tgt"], font, maxw), font)
        fs, lines, font = best
        # balance: narrowest width that keeps the same number of lines (no lonely last word)
        if len(lines) > 1:
            lo, hi = font.getlength(e["tgt"]) / len(lines), maxw
            for _ in range(12):
                mid = (lo + hi) / 2
                if len(_wrap(e["tgt"], font, mid)) <= len(lines):
                    hi = mid
                else:
                    lo = mid
            lines = _wrap(e["tgt"], font, hi + 1)
        tw = max(font.getlength(l) for l in lines)
        th = len(lines) * fs * 1.22
        # keep the whole block inside the frame (with a margin): shift its centre, shrink only if still too big
        if tw > W - 2 * margin:
            fs, tw = fs * (W - 2 * margin) / tw, W - 2 * margin
            th = len(lines) * fs * 1.22
        cx = min(max(cx, margin + tw / 2), W - margin - tw / 2)
        cy = min(max(cy, margin + th / 2), H - margin - th / 2)
        pad = lh * 0.3
        rx0 = max(0, min(x0 * W, cx - tw / 2) - pad)
        rx1 = min(W, max(x1 * W, cx + tw / 2) + pad)
        ry0 = max(0, min(y0 * H, cy - th / 2) - pad)
        ry1 = min(H, max(y1 * H, cy + th / 2) + pad)
        out.append({**e, "fs": round(fs, 1), "lines": lines, "cx": cx, "cy": cy, "font": fname, "bold": bold,
                    "cover": [int(rx0) // 2 * 2, int(ry0) // 2 * 2, int(rx1 - rx0) // 2 * 2, int(ry1 - ry0) // 2 * 2]})
    return out


def screen_text_covers(layouts, last, style="blur"):
    """ffmpeg filters that hide the original text only while it is on screen."""
    filters = []
    for i, L in enumerate(layouts):
        x, y, w, h = L["cover"]
        if w < 4 or h < 4:
            continue
        en = f"enable='between(t,{L['start']:.2f},{L['end']:.2f})'"
        nxt = f"st{i}"
        if style == "box":
            filters.append(f"[{last}]drawbox=x={x}:y={y}:w={w}:h={h}:color=black@0.8:t=fill:{en}[{nxt}]")
        else:
            sig = max(6, min(h * 0.45, 40))
            filters.append(f"[{last}]split[sa{i}][sb{i}];[sb{i}]crop={w}:{h}:{x}:{y},gblur=sigma={sig:.0f}:steps=3,"
                           f"eq=brightness=-0.02[sc{i}];[sa{i}][sc{i}]overlay={x}:{y}:{en}[{nxt}]")
        last = nxt
    return filters, last


def _overlap_small(a, b):
    """Intersection area / the smaller box's area (0..1)."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    sa, sb = (a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])
    return ix * iy / max(1e-9, min(sa, sb))


def show_ends(layouts):
    """When the Vietnamese of a caption must stop being DRAWN: as soon as the next caption at the same place appears.
    Screen-text events are padded (they start a little before the first OCR sighting and end ~1.3 s after the last
    one, so the original text stays covered while it fades), so two captions shown one after the other at the same
    spot overlapped by ~1 s and the new translation was drawn on top of the old one. The covering of the original
    keeps the padded times; only the drawn text hands over."""
    ends = [L["end"] for L in layouts]
    for i, L in enumerate(layouts):
        if L.get("track"):
            continue
        for M in layouts:
            if M is L or M.get("track") or not (L["start"] < M["start"] < ends[i]):
                continue
            if _overlap_small(L["box"], M["box"]) > 0.2:
                ends[i] = max(L["start"] + 0.3, M["start"])
    return ends


def screen_text_rows(layouts, s, W=None, H=None):
    rows = []
    ends = show_ends(layouts)
    for L, show_end in zip(layouts, ends):
        L = dict(L, end=show_end)
        text = "\\N".join(l.replace("{", "(").replace("}", ")") for l in L["lines"])
        b = L.get("bold", True)
        weight = b if isinstance(b, int) and not isinstance(b, bool) else (700 if b else 400)
        outline = (L.get("outline") or "#000000").upper()
        if outline not in ("#000000", "#111111"):
            # the original has a coloured outline (e.g. white text with an orange edge): keep a real border
            edge = f"\\3c{_ass_color(outline)}\\bord{max(1.5, L['fs'] * 0.08):.1f}\\shad{L['fs'] * 0.03:.1f}"
        else:
            # plain captions: thin dark edge, softened, plus a soft drop shadow — readable without a cheap thick border
            edge = (f"\\3c&H000000&\\3a&H50&\\bord{max(1.0, L['fs'] * 0.045):.1f}\\blur{max(0.8, L['fs'] * 0.05):.1f}"
                    f"\\4c&H000000&\\4a&H70&\\shad{max(1.0, L['fs'] * 0.05):.1f}")
        sp = f"\\fsp{L['fs'] * 0.04:.1f}" if weight <= 500 else ""      # light captions: airy tracking
        style = (f"\\fn{L.get('font') or s['sub_font']}\\fs{L['fs']:.0f}\\b{weight}{sp}\\q2"
                 f"\\c{_ass_color(L.get('fill') or '#FFFFFF')}{edge}")
        if L.get("track") and W and H:
            # moving text: one segment per pair of samples, \\move from one sampled position to the next
            pts = [(max(L["start"], tr[0]), ((tr[1] + tr[3]) / 2 * W, (tr[2] + tr[4]) / 2 * H)) for tr in L["track"]]
            pts = [(L["start"], pts[0][1])] + pts + [(L["end"], pts[-1][1])]
            for (t0, (x0, y0)), (t1, (x1, y1)) in zip(pts, pts[1:]):
                if t1 - t0 < 0.02:
                    continue
                dur_ms = int((t1 - t0) * 1000)
                rows.append(f"Dialogue: 1,{_ass(t0)},{_ass(t1)},Default,,0,0,0,,"
                            f"{{\\an5\\move({x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f},0,{dur_ms}){style}}}{text}")
            continue
        rows.append(f"Dialogue: 1,{_ass(L['start'])},{_ass(L['end'])},Default,,0,0,0,,"
                    f"{{\\an5\\pos({L['cx']:.0f},{L['cy']:.0f}){style}\\fad(80,80)}}{text}")
    return rows


def _ass(t):
    t = max(t, 0)
    h, r = divmod(t, 3600)
    m, sec = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{sec:05.2f}"


# Ducking: the music is turned down under the Vietnamese voice. Slow attack/release + soft knee so the music sinks
# smoothly and stays down through a sentence. The old 5:1 / 20 ms / 400 ms setting pulled the music ~10 dB down on
# average (16 dB peak) and pumped it up and down > 6 dB about 66 times a minute between sentences — heard as squashed,
# distorted music. Now (measured on the same video): −6.4 dB average, −9 dB at most, ~1 swing a minute.
DUCK = "sidechaincompress=threshold=0.03:ratio=2:attack=100:release=1000:knee=6:makeup=1"
DUCK_VO = "sidechaincompress=threshold=0.05:ratio=2.5:attack=100:release=1000:knee=6:makeup=1"


def voiceover_mix(workdir, s, inputs, o, bgm=None):
    """Voice-over (thuyết minh): keep the original soundtrack — music at its level, the ORIGINAL voice turned
    down (and pushed further down while the Vietnamese narrator speaks), the Vietnamese voice on top.
    Without separated stems the whole original audio is turned down instead."""
    base = len([a for a in inputs if a == "-i"])
    ov = float(s.get("vo_original_volume", 0.5))
    vv = float(s.get("voice_volume", 1.35))
    duck = DUCK_VO
    stems = s.get("separate_music", True) and (workdir / "no_vocals.wav").exists() and (workdir / "vocals.wav").exists()
    vocals = "vocals_keep.wav" if s.get("narrator_remove") and (workdir / "vocals_keep.wav").exists() else "vocals.wav"
    if stems:
        inputs = inputs + ["-i", bgm or "no_vocals.wav", "-i", vocals, "-i", "voice.wav"]
        m, v, n = base, base + 1, base + 2
        af = [f"[{m}:a]volume={float(s.get('vo_music_volume', 1.0))}[vm]",
              f"[{v}:a]volume={ov}[ov0]", f"[{n}:a]volume={vv}[nv0]",
              "[nv0]asplit=2[nv][nsc]", f"[ov0][nsc]{duck}[ov]",
              "[vm][ov][nv]amix=inputs=3:normalize=0:duration=longest,alimiter=limit=0.95[a]"]
    else:
        inputs = inputs + ["-i", "voice.wav"]
        n = base
        af = [f"[{o}:a]volume={ov}[or0]", f"[{n}:a]volume={vv}[nv0]",
              "[nv0]asplit=2[nv][nsc]", f"[or0][nsc]{duck}[or]",
              "[or][nv]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.95[a]"]
    return inputs, af


SUB_STYLE = {"blur": "blur", "box": "box", "erase": "inpaint", "lama": "lama"}


def sub_cover_filters(subs, W, H, fps, dur, last, style="blur", tag="s"):
    """Cover each original subtitle line only while it is on screen, entirely in ffmpeg:
    one blurred (or darkened) copy of the subtitle area + a grey mask where every line is a box switched on
    by enable='between(t,start,end)' (filters with a timeline are skipped when off, so 150 lines cost nothing),
    feathered, alpha-merged and overlaid. Much faster than piping every frame through Python."""
    boxes = []
    for L in subs:
        x0, y0, x1, y1 = L["box"]
        lhp = max(8.0, float(L.get("line_h") or (y1 - y0)) * H)
        px, py = max(8, lhp * 0.75), max(8, lhp * 0.65)        # room for a soft edge outside the text
        boxes.append((max(0, x0 * W - px), max(0, y0 * H - py), min(W, x1 * W + px), min(H, y1 * H + py),
                      L["start"], L["end"], lhp))
    lh = sorted(b[6] for b in boxes)[len(boxes) // 2]
    # the area worked on is larger than the boxes, so the soft edge fades out inside it (no cut-off line)
    mg = int(lh * 1.1)
    rx = max(0, int(min(b[0] for b in boxes)) - mg) // 2 * 2
    ry = max(0, int(min(b[1] for b in boxes)) - mg) // 2 * 2
    rw = (min(W, int(max(b[2] for b in boxes)) + mg) - rx) // 2 * 2
    rh = (min(H, int(max(b[3] for b in boxes)) + mg) - ry) // 2 * 2
    # blur and mask are computed at 1/k resolution and scaled back up: the result is blurred anyway, and a
    # 4K subtitle strip costs ~16x less this way
    k = max(4 if min(rw, rh) >= 64 else 2 if min(rw, rh) >= 16 else 1, -(-max(rw, rh) // 480))
    sw, sh = max(2, rw // k // 2 * 2), max(2, rh // k // 2 * 2)
    fx, fy = sw / rw, sh / rh
    feather = max(1.5, lh * 0.32 * fx)                            # wide soft edge: no visible rectangle
    draws = [f"drawbox=x={int((b[0] - rx) * fx)}:y={int((b[1] - ry) * fy)}:w={max(1, int((b[2] - b[0]) * fx + 1))}:"
             f"h={max(1, int((b[3] - b[1]) * fy + 1))}:color=white:t=fill:enable='between(t,{b[4]:.2f},{b[5]:.2f})'"
             for b in boxes]
    look = (f"scale={sw}:{sh}:flags=area,gblur=sigma={max(3.0, lh * 0.6 * fx):.1f}:steps=2,"
            f"scale={rw}:{rh}:flags=bilinear" if style == "blur" else "lutyuv=y=val*0.22")
    t = tag
    f = [f"[{last}]split[{t}m0][{t}b0]",
         f"[{t}b0]crop={rw}:{rh}:{rx}:{ry},{look}[{t}bl]",
         f"color=c=black:s={sw}x{sh}:r={fps:.5f}:d={dur + 1:.3f},format=gray," + ",".join(draws) +
         # drawbox writes video-range white (235): stretch to full range or 8% of the original text shows through
         f",gblur=sigma={feather:.1f},lut=c0='clip((val-16)*255/219,0,255)',scale={rw}:{rh}:flags=bilinear[{t}mk]",
         f"[{t}bl][{t}mk]alphamerge[{t}bm]",
         f"[{t}m0][{t}bm]overlay={rx}:{ry}:shortest=1[{t}out]"]
    return f, f"{t}out"


def wm_cover_items(wms):
    """Watermark items as timed boxes for sub_cover_filters; a moving one becomes one box per tracked step."""
    out = []
    for L in wms:
        tr = L.get("track")
        if not tr:
            out.append(L)
            continue
        pts = [[L["start"]] + tr[0][1:]] + tr + [[L["end"]] + tr[-1][1:]]   # covered from the item's start to end
        for a, b in zip(pts, pts[1:]):
            if b[0] - a[0] <= 0:
                continue
            # cover where it is now and where it goes next (union), plus a margin: the real path between two
            # samples is not a straight line, and OCR boxes are tight
            mx, my = (a[3] - a[1]) * 0.25, (a[4] - a[2]) * 0.4
            box = [max(0.0, min(a[1], b[1]) - mx), max(0.0, min(a[2], b[2]) - my),
                   min(1.0, max(a[3], b[3]) + mx), min(1.0, max(a[4], b[4]) + my)]
            out.append({**L, "box": box, "start": a[0], "end": b[0] + 0.05})
    return [L for L in out if L["end"] > L["start"]]


def sub_layouts(workdir, s):
    """Per-line subtitle events (subdetect.track_lines) as clean-layouts."""
    import json
    p = workdir / "subevents.json"
    if not p.exists():
        return []
    ev = json.loads(p.read_text(encoding="utf-8")) or []
    st = SUB_STYLE.get(s.get("hide_style") or "blur", "blur")
    return [{**e, "clean_style": st, "sub": True} for e in ev]


def job_cancelled():
    from .jobs import Cancelled
    return Cancelled


def credit_rows(workdir, s, W, H):
    """Optional small 'Nguồn: <author> (<platform>)' credit in the top-left corner for the whole video."""
    if not s.get("brand_credit"):
        return []
    import json
    meta = workdir / "meta.json"
    info = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
    who = (info.get("uploader") or "").strip()
    if not who:
        return []
    url = info.get("webpage_url") or info.get("url") or ""
    plat = "Douyin" if "douyin" in url else "TikTok" if "tiktok" in url else "Facebook" if "facebook" in url else ""
    text = f"Nguồn: {who}" + (f" ({plat})" if plat else "")
    fs = max(12, int(min(W, H) * 0.022))
    m = int(min(W, H) * 0.03)
    text = text.replace("{", "(").replace("}", ")")
    return [f"Dialogue: 2,{_ass(0)},{_ass(36000)},Default,,0,0,0,,"
            f"{{\\an7\\pos({m},{m})\\fs{fs}\\b0\\bord{max(1, fs * 0.08):.1f}\\shad0\\alpha&H40&}}{text}"]


def write_srt(lines, path: Path, mode):
    with open(path, "w", encoding="utf-8") as f:
        for i, (a, b, text) in enumerate(sub_cues(lines, mode)):
            f.write(f"{i+1}\n{tts._ts(a)} --> {tts._ts(b)}\n{text}\n\n")


def voice_audio(job, workdir: Path, s, mode, inputs, o):
    """The sound of the video: background (the separated original, or your own music) + the Vietnamese voice, or
    the original audio untouched. `o` = index of the input that carries the source audio; the inputs this needs are
    appended. Returns (inputs, audio filters, map label). Used by the full render and by remix_audio()."""
    from . import review
    inputs = list(inputs)
    bgm = review.bgm_track(workdir, s)       # your own background music (📋 Đánh giá → Nhạc nền)
    if bgm:
        job.log("Nhạc nền: dùng nhạc bạn chọn thay nhạc gốc")
    if mode == "dub":
        music = bgm or ("no_vocals.wav" if (s["separate_music"] and (workdir / "no_vocals.wav").exists()) else "source.mp4")
        inputs += ["-i", music, "-i", "voice.wav"]
        af = [f"[{o + 1}:a]volume={float(s['bg_volume'])}[m0]", f"[{o + 2}:a]volume={float(s['voice_volume'])}[vo0]"]
        if s.get("bg_duck", True):
            # duck the background under the Vietnamese voice: clearer speech, hides leftover original voice
            af += ["[vo0]asplit=2[vo][vsc]",
                   f"[m0][vsc]{DUCK}[m]"]
        else:
            af += ["[m0]anull[m]", "[vo0]anull[vo]"]
        mixins = "[m][vo]"
        n = 2
        keep = s.get("narrator_remove") and music != "source.mp4" and (workdir / "vocals_keep.wav").exists()
        if keep:
            # the characters' own voices (narrator removed) at their natural level
            inputs += ["-i", "vocals_keep.wav"]
            af.append(f"[{o + 3}:a]volume={float(s.get('character_volume', 1.0))}[ov]")
            mixins += "[ov]"
            n = 3
        elif float(s.get("original_voice_volume") or 0) > 0 and music != "source.mp4":
            inputs += ["-i", "vocals.wav"]
            af.append(f"[{o + 3}:a]volume={float(s['original_voice_volume'])}[ov]")
            mixins += "[ov]"
            n = 3
        af.append(f"{mixins}amix=inputs={n}:normalize=0:duration=longest,alimiter=limit=0.95[a]")
        return inputs, af, "[a]"
    if mode == "voiceover":
        inputs, af = voiceover_mix(workdir, s, inputs, o, bgm)
        return inputs, af, "[a]"
    return inputs, [], f"{o}:a?"


# Settings that only change the SOUND. When nothing else changed since the last render, the picture is kept as it
# is and only the sound is mixed again (remix_audio): seconds instead of a full encode.
AUDIO_KEYS = ("bg_volume", "voice_volume", "original_voice_volume", "bg_duck", "vo_original_volume", "vo_music_volume",
              "character_volume", "bgm_enabled")


def remix_audio(job, workdir: Path, s, out_name, step="render") -> bool:
    """New sound on the already-rendered picture (stream copy) — for a changed music track / volumes / voice timing
    that left every subtitle cue where it was. Also refreshes the TikTok copy made in the same render.
    False = cannot (the caller renders in full)."""
    from . import tiktokcheck as tk
    mode = s["output_mode"]
    old = workdir / out_name
    if mode not in ("dub", "voiceover") or not old.exists() or s.get("content_enabled"):
        return False
    dur = util.duration(old)
    inputs, af, amap = voice_audio(job, workdir, s, mode, ["-i", "source.mp4"], 0)
    outro = float(s.get("outro_seconds") or 0)
    if outro > 0:
        fade_at = max(0.0, dur - outro - 0.6)
        af.append(f"{amap}apad=whole_dur={dur:.3f},afade=t=out:st={fade_at:.3f}:d=0.6[aout]")
        amap = "[aout]"
    tt = workdir / tk.OUT
    has_tt = tt.exists() and (workdir / tk.PRE).exists()
    vi = inputs.count("-i")
    tmp_main, tmp_tt = "remix_" + out_name, "remix_" + tk.OUT
    inputs += ["-i", out_name]
    args_tt = []
    if has_tt:
        inputs += ["-i", tk.OUT]
        af.append(f"{amap}asplit=2[amain][atk0];[atk0]{tk.TIKTOK_AF}[atk]")
        amap = "[amain]"
        args_tt = ["-map", f"{vi + 1}:v:0", "-c:v", "copy", "-map", "[atk]", *tk.TIKTOK_ACODEC, "-movflags", "+faststart",
                   "-t", f"{dur:.3f}", tmp_tt]
    (workdir / "filter_remix.txt").write_text(";".join(af), encoding="utf-8")
    job.step(step, detail="Hình không đổi → chỉ trộn lại tiếng")
    args = [*inputs, "-filter_complex_script", "filter_remix.txt", "-map", f"{vi}:v:0", "-c:v", "copy", "-map", amap,
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{dur:.3f}", tmp_main, *args_tt]
    try:
        util.ffmpeg(job, args, duration=dur, step=step, cwd=workdir)
        os.replace(workdir / tmp_main, old)
        if has_tt:
            os.replace(workdir / tmp_tt, tt)
            tk.mark_premade(workdir, out_name)
    finally:
        for f in (tmp_main, tmp_tt):
            (workdir / f).unlink(missing_ok=True)
    job.log("Hình không đổi so với lần render trước → chỉ trộn lại tiếng (giữ nguyên phần hình)")
    return True


def render(job, workdir: Path, s, band, out_name, seconds=None, step="render"):
    src = workdir / "source.mp4"
    W0, H0 = util.video_size(src)
    W, H = out_size(W0, H0, int(s["output_short_side"]))
    total = util.duration(src)
    dur = min(seconds, total) if seconds else total
    if total - dur < 1.0:
        dur = total       # preview ≈ whole video: render it all (and reuse the same cleaned video)
    mode = s["output_mode"]
    # the Vietnamese voice may finish after the picture (last line not cut): hold the last frame that long
    ext = 0.0
    if dur == total and mode in ("dub", "voiceover") and (workdir / "voice.wav").exists():
        ext = max(0.0, util.duration(workdir / "voice.wav") - total)
        ext = ext if ext > 0.05 else 0.0
        dur = total + ext
    # ending card (B6): the last frame held, blurred and darkened, with "Theo dõi @…" — full render only
    outro = float(s.get("outro_seconds") or 0) if dur >= total else 0.0
    end_t = dur                       # where the picture/voice end and the card begins
    from . import content as _ct
    cparts = _ct.render_parts(job, workdir, s, W, H, end_t, abs(W / H - 9 / 16) < 0.02)
    if cparts and cparts.ending_s and dur >= total:
        outro = max(outro, cparts.ending_s)          # “Biên kịch” ending card (+ the loop line) over the held frame
    if outro > 0:
        dur = end_t + outro

    hide = s["hide_original_subs"]

    # on-screen text translated in place (titles, captions…): remove the original text frame by frame
    # (inpaint / soft blur in Python), then the Vietnamese is drawn by the subtitle renderer below
    import json
    from . import brand, textclean, watermark
    layouts = []
    video_in = "source.mp4"
    st_file = workdir / "screentext.json"
    if s.get("screen_text") and st_file.exists():
        layouts = screen_text_layout(json.loads(st_file.read_text(encoding="utf-8")) or [], W, H, s)
        if layouts and hide == "auto" and not (band or {}).get("speech"):
            band = None     # the text events already cover burned captions, precisely in time
    # original platform watermarks (抖音号, TikTok @user…) are removed in the same frame-by-frame pass
    wms = watermark.for_render(workdir, s)
    # burned-in subtitles: cover each line where and while it is on screen (not a fixed band all video long)
    subs = sub_layouts(workdir, s) if hide == "auto" and band else []
    # blur / box covers run inside ffmpeg (time-switched mask, GPU encode) — only erase/LaMa need Python
    subs_ff = [L for L in subs if L["clean_style"] in ("blur", "box")]
    wms_ff = [L for L in wms if L.get("clean_style") in ("blur", "box")]       # same fast path for watermarks
    to_clean = layouts + [L for L in wms if L not in wms_ff] + [L for L in subs if L not in subs_ff]
    segs = []           # cleaned pieces [(file, start, end)] laid over the source only where text was
    if to_clean:
        style = s.get("screen_text_clean", "inpaint")
        try:
            segs = textclean.make_clean_segments(job, workdir, src, to_clean, W, H, style, dur, step, s)
        except Exception as e:
            if "lama" not in (style, s.get("wm_clean"), SUB_STYLE.get(s.get("hide_style"))) or isinstance(e, job_cancelled()):
                raise
            job.log(f"LaMa không chạy được ({str(e).splitlines()[0]}) → dùng OpenCV inpaint", "warn")
            to_clean = [dict(L, clean_style="inpaint") if L.get("clean_style") == "lama" else L for L in to_clean]
            segs = textclean.make_clean_segments(job, workdir, src, to_clean, W, H,
                                                 "inpaint" if style == "lama" else style, dur, step, s)
    vf = [f"[0:v]scale={W}:{H}:flags=lanczos,setsar=1[{'v00' if segs else 'v0'}]"]
    last = "v0"
    if subs_ff or wms_ff:
        from .textclean import _fps
        fps = _fps(src)
        # ONE chain for every cover (subtitle lines + all watermarks): one low-resolution blurred copy and one
        # time-switched mask. A chain per watermark (29 on one video) made ffmpeg's filter graph stall silently.
        items = [L for L in subs_ff + wm_cover_items(wms_ff) if L["start"] < dur]
        if items:
            style = "blur" if any(L["clean_style"] == "blur" for L in items) else "box"
            f, last = sub_cover_filters(items, W, H, fps, dur, last, style, tag="c")
            vf += f
    if hide == "manual":
        y0, y1 = s["manual_sub_band"]
        band = {"x0": 0.05, "x1": 0.95, "y0": float(y0), "y1": float(y1)}
    if hide != "off" and band and not subs:
        bx, by = int(band["x0"] * W) // 2 * 2, int(band["y0"] * H) // 2 * 2
        bw = int((band["x1"] - band["x0"]) * W) // 2 * 2
        bh = int((band["y1"] - band["y0"]) * H) // 2 * 2
        if s["hide_style"] == "box":
            vf.append(f"[{last}]drawbox=x={bx}:y={by}:w={bw}:h={bh}:color=black@0.85:t=fill[v1]")
        else:
            # chroma planes are half size, so the radius must stay < h/4
            r = max(2, min(bh // 4 - 1, bw // 4 - 1, 30))
            vf.append(f"[{last}]split[b0][b1];[b1]crop={bw}:{bh}:{bx}:{by},"
                      f"boxblur=luma_radius={r}:luma_power=3:chroma_radius={r}:chroma_power=3[bl];"
                      f"[b0][bl]overlay={bx}:{by}[v1]")
        last = "v1"
    elif not subs:
        band = None      # (with per-line covers the band is kept only to place the Vietnamese subtitles)
    if ext or outro:     # before the subtitles, so the last subtitle line still shows over the held frame
        vf.append(f"[{last}]tpad=stop_mode=clone:stop_duration={ext + outro:.3f}" +
                  (f",gblur=sigma=22:enable='gte(t,{end_t:.3f})',eq=brightness=-0.12:enable='gte(t,{end_t:.3f})'"
                   if outro else "") + "[vx]")
        last = "vx"
    credit = credit_rows(workdir, s, W, H)
    # captions that were the speech get replaced by the speech subtitles, even with "burn subtitles" off
    speech_caps = bool(s.get("screen_text") and (band or {}).get("speech"))
    credit = credit + (outro_rows(s, W, H, end_t, dur) if outro and not (cparts and cparts.ending_s) else [])
    if s["burn_subtitles"] or layouts or credit or speech_caps:
        lines = job_lines(workdir) if (s["burn_subtitles"] or speech_caps) and (workdir / "lines.json").exists() else []
        if cparts:
            lines = [l for l in lines if not l.get("hook")]           # the Biên kịch hook replaces hook_intro
        drawn = [L for L in layouts if L.get("id") not in cparts.replaced] if cparts else layouts
        write_ass(lines, workdir / "subs.ass", W, H, band, s, mode, screen_text_rows(drawn, s, W, H) + credit)
        fdir = os.path.relpath(APP_FONTS, workdir).replace("\\", "/")     # relative: no drive colon to escape
        vf.append(f"[{last}]subtitles=subs.ass:fontsdir='{fdir}'[v2]")
        last = "v2"

    hw = ["-hwaccel", "cuda"] if s.get("video_encoder") == "h264_nvenc" and not s.get("_no_hwdec") else []
    inputs = [*hw, "-i", video_in]
    o = 0                       # index of the input carrying the source audio
    if video_in != "source.mp4":
        inputs += ["-i", "source.mp4"]
        o = 1
    inputs, af, amap = voice_audio(job, workdir, s, mode, inputs, o)
    # original audio is kept as is (bit-exact) when the container allows it — no needless re-encode
    acodec = ["-c:a", "aac", "-b:a", "192k"]
    if mode not in ("dub", "voiceover"):
        codecs = [st.get("codec_name") for st in util.probe(src)["streams"] if st.get("codec_type") == "audio"]
        if codecs and codecs[0] in ("aac", "mp3", "alac", "ac3"):
            acodec = ["-c:a", "copy"]

    if outro and (af or util.has_audio(src)):
        if not af:                     # original audio kept as is: it has to be re-encoded to get the tail
            af, amap = [f"[{o}:a]anull[a]"], "[a]"
            acodec = ["-c:a", "aac", "-b:a", "192k"]
        fade_at = max(0.0, end_t - 0.6)
        af.append(f"{amap}apad=whole_dur={dur:.3f},afade=t=out:st={fade_at:.3f}:d=0.6[aout]")
        amap = "[aout]"

    def label_audio():
        """the audio as a filter label (copied original audio becomes a filter input)"""
        nonlocal af, amap, acodec
        if not amap.startswith("["):
            if util.has_audio(src):
                af.append(f"[{o}:a]anull[abase]")
            else:
                af.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f}[abase]")
            amap, acodec = "[abase]", ["-c:a", "aac", "-b:a", "192k"]
    if cparts and cparts.vo:
        label_audio()
        ci = inputs.count("-i")
        inputs += ["-i", str(cparts.vo)]
        af.append(f"{amap}volume='{_ct.duck_expr(cparts.duck)}':eval=frame[aduck];"
                  f"[{ci}:a]volume={float(s.get('content_vo_volume', 1.5)):.2f}[cvo];"
                  f"[aduck][cvo]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95[acnt]")
        amap = "[acnt]"

    # your own moving watermark (TikTok / YouTube / Facebook handle) on top of everything
    badges = brand.make_badges(workdir, s, W, H)
    if badges:
        first = inputs.count("-i")
        for b in badges:
            inputs += ["-loop", "1", "-i", b]
        bf, last = brand.filters(badges, first, last, s)
        vf += bf
    tf = []
    if s.get("transform_grade"):
        tf.append("eq=contrast=1.06:saturation=1.12:gamma=0.98")
    z = float(s.get("transform_zoom") or 1.0)
    if z > 1.001:
        tf.append(f"scale=trunc(iw*{z:.3f}/2)*2:trunc(ih*{z:.3f}/2)*2,crop={W}:{H}")
    if tf:
        vf.append(f"[{last}]{','.join(tf)}[vt]")
        last = "vt"
    if segs:
        # each cleaned piece starts at its own time (-itsoffset) and is shown only inside its window
        prev = "v00"
        for k, (name, a, b) in enumerate(segs):
            idx = inputs.count("-i")
            inputs += ["-itsoffset", f"{a:.3f}", "-i", name]
            lab = "v0" if k == len(segs) - 1 else f"cs{k}"
            vf.append(f"[{prev}][{idx}:v]overlay=0:0:eof_action=pass:enable='between(t,{a:.3f},{b:.3f})'[{lab}]")
            prev = lab
    out_dur = dur
    cmap = None
    if cparts and cparts.cuts:
        # cut points on the FRAME grid: the picture can only be cut between frames, the sound anywhere — off-grid
        # cuts let the sound drift up to one frame per cut (measured: 0.01 → 0.08 s after 12 cuts)
        from .textclean import _fps
        fr = _fps(src) or 30.0
        xfs = list(getattr(cparts, "xfade", ()) or ())
        xfs += [False] * (len(cparts.cuts) - len(xfs))
        cx = [((round(a * fr) / fr, round(b * fr) / fr), x) for (a, b), x in zip(cparts.cuts, xfs) if b <= dur]
        cx = [(c, x) for c, x in cx if c[1] > c[0]]
        cuts = [c for c, x in cx]
        if cuts:
            # the kept spans are trimmed out of picture AND sound and concatenated together, so both shift by
            # exactly the same amount. (select/aselect was used first: on ffmpeg 8.0 aselect dropped NOTHING, the
            # picture was cut but the sound was not — every cut pushed the sound further behind the picture.)
            label_audio()
            keep = _ct.CutMap([{"from": a, "to": b} for a, b in cuts], dur).keep()
            # safe_cuts() never overlaps two cuts, so join i is cut i (crossfade where the join is not a shot change)
            xjoin = [x for c, x in cx if 0 < c[0] and c[1] < dur]
            vp, ap, vo_, ao_ = _ct.cut_graph(keep, last, amap.strip("[]"), xjoin, fr)
            vf += vp
            af += ap
            last, amap = vo_, f"[{ao_}]"
            out_dur = dur - sum(b - a for a, b in cuts)
            cmap = _ct.CutMap([{"from": a, "to": b} for a, b in cuts], end_t)
            job.log(f"Biên kịch: cắt {len(cuts)} đoạn lặng, {dur - out_dur:.1f}s → video còn {out_dur:.1f}s")
    tt_planned = (seconds is None and step == "render" and s.get("tiktok_check", True) and s.get("tiktok_autofix", True)
                  and abs(W / H - 9 / 16) >= 0.02)
    fdir_c = os.path.relpath(APP_FONTS, workdir).replace("\\", "/")
    if cparts and cparts.items and not tt_planned:      # overlays on the (only) output, after the cuts
        _ct.ass_file(workdir / "content_main.ass", W, H, _ct.rows(cparts, W, H, "main", cmap))
        vf.append(f"[{last}]subtitles=content_main.ass:fontsdir='{fdir_c}'[vcm]")
        last = "vcm"
    fc = ";".join(vf + af)
    (workdir / "filter.txt").write_text(fc, encoding="utf-8")
    enc = s["video_encoder"]
    venc = (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "21", "-b:v", "0"] if enc == "h264_nvenc"
            else ["-c:v", "libx264", "-preset", "medium", "-crf", "20"])
    # TikTok copy in the SAME pass (non-9:16 final render): the frames are decoded and filtered once, NVENC
    # encodes the two outputs side by side — the TikTok step then only checks it (a 38-min film: ~12 min saved)
    tt_args = []
    if tt_planned:
        from . import tiktokcheck as tk
        fps = (tk.probe(workdir / "source.mp4")["video"] or {}).get("fps") or 30.0
        vf.append(f"[{last}]split[vmain][vtk0]")
        vf.append(tk.tiktok_filters(W, H, fps, "[vtk0]", "[vtk1]" if (cparts and cparts.items) else "[vtk]", tag="r"))
        last = "vmain"
        if cparts and cparts.items:      # Biên kịch overlays: big, in the free bands of the 9:16 copy / on the picture
            _ct.ass_file(workdir / "content_tt.ass", 1080, 1920, _ct.rows(cparts, 1080, 1920, "tt", cmap))
            vf.append(f"[vtk1]subtitles=content_tt.ass:fontsdir='{fdir_c}'[vtk]")
            _ct.ass_file(workdir / "content_main.ass", W, H, _ct.rows(cparts, W, H, "main", cmap))
            vf.append(f"[vmain]subtitles=content_main.ass:fontsdir='{fdir_c}'[vmain2]")
            last = "vmain2"
        if amap.startswith("["):
            vf_a = f"{amap}asplit=2[amain][atk0];[atk0]{tk.TIKTOK_AF}[atk]"
            af.append(vf_a)
            amap, tmap, taf = "[amain]", "[atk]", []
        else:
            tmap, taf = amap, ["-filter:a", tk.TIKTOK_AF]
        (workdir / "filter.txt").write_text(";".join(vf + af), encoding="utf-8")
        tt_args = ["-map", "[vtk]", "-map", tmap, *taf, *tk.tiktok_venc(s, tk.video_kbps(out_dur), fps), "-pix_fmt", "yuv420p",
                   *tk.TIKTOK_ACODEC, "-movflags", "+faststart", "-metadata:s:v", "rotate=0", "-t", f"{out_dur:.3f}",
                   tk.OUT]
        (workdir / tk.OUT).unlink(missing_ok=True)
        (workdir / tk.PRE).unlink(missing_ok=True)
    args = [*inputs, "-filter_complex_script", "filter.txt", "-map", f"[{last}]", "-map", amap,
            *venc, "-pix_fmt", "yuv420p", *acodec, "-movflags", "+faststart",
            "-t", f"{out_dur:.3f}", out_name, *tt_args]
    try:
        util.ffmpeg(job, args, duration=out_dur, step=step, cwd=workdir, scale=(0.5, 1.0) if segs else (0.0, 1.0))
    except RuntimeError as e:
        if hw and any(k in str(e).lower() for k in ("cuda", "hwaccel", "nvdec", "cuvid")):
            job.log("Giải mã GPU lỗi, dùng giải mã CPU…", "warn")
            return render(job, workdir, dict(s, _no_hwdec=True), band, out_name, seconds, step)
        if enc == "h264_nvenc" and "nvenc" in str(e).lower():
            job.log("NVENC lỗi, chuyển sang libx264 (CPU)…", "warn")
            s = dict(s, video_encoder="libx264")
            return render(job, workdir, s, band, out_name, seconds, step)
        raise
    if tt_args:
        from . import tiktokcheck as tk
        tk.mark_premade(workdir, out_name)
        job.log("Đã tạo luôn bản TikTok 9:16 trong cùng lượt render")
    return workdir / out_name


def make_work_copy(job, src: Path, out: Path, short: int, duration=None):
    """1080p (output size) working copy of a 4K source: every later step — OCR, text removal, render, TikTok
    check — decodes this instead of 4K frames. High quality (NVENC CQ 16 / x264 CRF 16), audio copied as is."""
    W, H = util.video_size(src)
    w, h = out_size(W, H, short)
    tmp = out.with_suffix(".tmp.mp4")
    tries = [(["-hwaccel", "cuda"], ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "16", "-b:v", "0"], ["-c:a", "copy"]),
             ([], ["-c:v", "libx264", "-preset", "fast", "-crf", "16"], ["-c:a", "copy"]),
             ([], ["-c:v", "libx264", "-preset", "fast", "-crf", "16"], ["-c:a", "aac", "-b:a", "256k"])]
    last = None
    for hw, venc, aenc in tries:
        try:
            util.ffmpeg(job, [*hw, "-i", str(src), "-map", "0:v:0", "-map", "0:a?", "-vf",
                              f"scale={w}:{h}:flags=lanczos", "-pix_fmt", "yuv420p", *venc, *aenc,
                              "-movflags", "+faststart", str(tmp)], duration=duration, step="download", scale=(0.7, 0.95))
            tmp.replace(out)
            return w, h
        except RuntimeError as e:
            last = e
            tmp.unlink(missing_ok=True)
    raise last


def make_view_copy(src: Path, out: Path, job=None, duration=None):
    """Browser-friendly copy of the source (Douyin often serves HEVC 4K, which many browsers can't play)."""
    import subprocess
    W, H = util.video_size(src)
    w, h = out_size(W, H, 720)
    tmp = out.with_suffix(".tmp.mp4")
    for enc in (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "26"], ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"]):
        args = ["-i", str(src), "-vf", f"scale={w}:{h}", *enc, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", str(tmp)]
        try:
            if job:
                util.ffmpeg(job, args, duration=duration, step="download", scale=(0.95, 1.0))
            else:
                subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True,
                               creationflags=util.CREATE_NO_WINDOW)
            tmp.replace(out)
            return
        except Exception:
            continue
    raise RuntimeError("Không tạo được bản xem thử của video gốc")


def outro_rows(s, W, H, t0, t1):
    """Ending card over the blurred last frame: "Theo dõi", one line per channel handle, then a short line."""
    from . import brand
    clean = lambda x: (x or "").replace("{", "(").replace("}", ")")  # noqa: E731
    names = [clean(n) for _, n in brand.items(s)] or ["kênh để xem thêm"]
    small = clean(s.get("outro_text") or "❤ Thả tim nếu bạn thấy hay — hẹn gặp ở video sau!")
    short = min(W, H)
    f1, f2, f3 = int(short * 0.068), int(short * 0.05), int(short * 0.036)
    block = f1 + len(names) * f2 * 1.2               # height of "Theo dõi" + handles
    yc = int(H * 0.44)
    head = (f"{{\\fs{f1}\\b1}}Theo dõi\\N{{\\fs{f2}\\b1}}" + "\\N".join(names))
    y2 = int(yc + block / 2 + f3 * 1.4)
    return [f"Dialogue: 3,{_ass(t0)},{_ass(t1)},Default,,0,0,0,,{{\\an5\\pos({W // 2},{yc})\\bord{max(2, f1 // 14)}"
            f"\\shad0\\fad(250,0)}}{head}",
            f"Dialogue: 3,{_ass(t0 + 0.2)},{_ass(t1)},Default,,{int(W * 0.08)},{int(W * 0.08)},0,,{{\\an8\\pos({W // 2},{y2})"
            f"\\fs{f3}\\bord{max(1, f3 // 16)}\\shad0\\fad(250,0)}}{small}"]


def job_lines(workdir: Path):
    import json
    return json.loads((workdir / "lines.json").read_text(encoding="utf-8"))
