"""TikTok pre-publish quality check for the finished video, plus an auto-fixed "TikTok version".

Checks (each item: ok / warn / fail / info, with a fix hint):
  file      container, codec (H.264/HEVC + AAC), pixel format, size (app upload ~287 MB), bitrate
  picture   resolution (1080p recommended, < 720p fails), 9:16 aspect, frame rate 23–60
  length    3 s … 10 min (in-app upload limit), > 1 min noted (Creator Rewards needs > 1 min)
  sound     audio present, loudness ≈ −14 LUFS, true peak ≤ −1 dBTP, not mostly silent
  quality   black opening, frozen picture (static / slideshow-like video)
  FYF       leftover watermark of another platform (抖音 / TikTok @… / 快手 …) = not eligible for the For You
            feed; leftover Chinese text; Vietnamese voice/subs = transformed content; text under TikTok's UI
            (top tabs, right-side buttons, bottom caption) — mapped onto the 9:16 phone screen
  caption   caption present, hashtag count, no Chinese characters, length

Numbers follow TikTok's published guidance and common creator specs; TikTok can change them at any time, and no
check can promise For-You distribution — the report says what is likely to hurt it.

make_tiktok(): re-encodes to 1080×1920 (landscape/square videos get a blurred fill instead of black bars),
H.264 High + AAC 48 kHz, loudness normalised to −14 LUFS / −1 dBTP, ≤ 60 fps, bitrate capped so the file stays
under the app upload limit, faststart. The fixed file is checked again.
"""
import json
import re
import tempfile
from pathlib import Path

from . import util

FILE = "tiktok_check.json"
OUT = "output_tiktok.mp4"
PRE = "output_tiktok.pre.json"   # written by the render when it made output_tiktok.mp4 in the same pass
LONG_S = 600                     # files longer than this are checked on keyframes only
APP_LIMIT_MB = 287
WEB_LIMIT_MB = 4096
# TikTok UI over a 9:16 phone screen (fractions of the screen): tabs on top, buttons on the right, caption,
# username and the sound ticker at the bottom.
UNSAFE = [("top", (0.0, 0.0, 1.0, 0.085)),
          ("right", (0.86, 0.30, 1.0, 0.80)),
          ("bottom", (0.0, 0.80, 1.0, 1.0))]
UNSAFE_NAMES = {"top": "thanh tab phía trên", "right": "cột nút thích/bình luận bên phải",
                "bottom": "vùng caption & tên kênh phía dưới"}
CJK = re.compile(r"[\u3400-\u9fff]")
HASHTAG = re.compile(r"#[^\s#]+")
TRANSFORMED = {"dub": "lồng tiếng Việt", "voiceover": "thuyết minh tiếng Việt"}


def _item(key, group, label, status, detail, fix="", fixable=False):
    return {"key": key, "group": group, "label": label, "status": status, "detail": detail, "fix": fix,
            "fixable": fixable}


# ---- measurements ---------------------------------------------------------------------------------------------
def probe(path: Path) -> dict:
    import subprocess
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size,bit_rate,format_name:stream=codec_type,codec_name,profile,width,height,"
         "r_frame_rate,avg_frame_rate,pix_fmt,bit_rate,sample_rate,channels:stream_side_data=rotation",
         "-of", "json", str(path)], creationflags=util.CREATE_NO_WINDOW)
    j = json.loads(out)
    v = next((x for x in j.get("streams", []) if x.get("codec_type") == "video"), None)
    a = next((x for x in j.get("streams", []) if x.get("codec_type") == "audio"), None)
    f = j.get("format", {})
    info = {"duration": float(f.get("duration") or 0), "size": int(f.get("size") or path.stat().st_size),
            "format": f.get("format_name", ""), "bitrate": int(f.get("bit_rate") or 0), "video": None,
            "audio": None}
    if v:
        w, h = int(v["width"]), int(v["height"])
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            rot = int(sd.get("rotation", 0) or 0)
        if abs(rot) in (90, 270):
            w, h = h, w
        num, _, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 0.0
        info["video"] = {"codec": v.get("codec_name"), "profile": v.get("profile", ""), "w": w, "h": h,
                         "fps": round(fps, 2), "pix_fmt": v.get("pix_fmt", ""), "bitrate": int(v.get("bit_rate") or 0)}
    if a:
        info["audio"] = {"codec": a.get("codec_name"), "rate": int(a.get("sample_rate") or 0),
                         "channels": int(a.get("channels") or 0), "bitrate": int(a.get("bit_rate") or 0)}
    return info


def analyse(job, path: Path, info: dict, progress=None) -> dict:
    """One decode pass: black / frozen picture, silence, integrated loudness and true peak."""
    dur = info["duration"] or 1.0
    has_a = bool(info["audio"])
    # a long file is judged on its keyframes (every ~2 s): decoding 38 min frame by frame took minutes
    long_file = dur > LONG_S
    vf = ("scale=192:-2,blackdetect=d=2:pix_th=0.10,freezedetect=n=0.003:d=6" if long_file
          else "fps=5,scale=192:-2,blackdetect=d=0.4:pix_th=0.10,freezedetect=n=0.003:d=2")
    args = ["ffmpeg", "-hide_banner", "-nostats", "-progress", "pipe:1", *(["-skip_frame", "nokey"] if long_file else []),
            "-i", str(path), "-map", "0:v:0", "-vf", vf]
    if has_a:
        args += ["-map", "0:a:0", "-af", "silencedetect=n=-45dB:d=2,ebur128=peak=true:framelog=verbose"]
    args += ["-f", "null", "-"]

    def on_line(line):
        if progress and line.startswith("out_time_us="):
            try:
                progress(min(int(line.split("=")[1]) / 1e6 / dur, 1.0))
            except ValueError:
                pass
    text = util.run(job, args, on_line=on_line, check=False)
    res = {"black": [], "freeze": [], "silence": 0.0, "lufs": None, "true_peak": None}
    for m in re.finditer(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)", text):
        res["black"].append((float(m.group(1)), float(m.group(2))))
    starts = [float(x) for x in re.findall(r"freeze_start:\s*([\d.]+)", text)]
    ends = [float(x) for x in re.findall(r"freeze_end:\s*([\d.]+)", text)]
    for i, s0 in enumerate(starts):
        res["freeze"].append((s0, ends[i] if i < len(ends) else dur))
    sil = 0.0
    s_starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", text)]
    s_ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", text)]
    for i, s0 in enumerate(s_starts):
        sil += (s_ends[i] if i < len(s_ends) else dur) - max(0.0, s0)
    res["silence"] = sil
    summ = text[text.rfind("Summary:"):] if "Summary:" in text else ""
    m = re.search(r"I:\s*(-?[\d.]+|-inf)\s*LUFS", summ)
    if m and m.group(1) != "-inf":
        res["lufs"] = float(m.group(1))
    m = re.search(r"True peak:\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS", summ, re.S)
    if m and m.group(1) != "-inf":
        res["true_peak"] = float(m.group(1))
    return res


def _own_handles(s):
    from . import brand
    out = []
    for _, name in brand.items(s):
        n = re.sub(r"[^0-9a-z]", "", name.lower())
        if len(n) >= 3:
            out.append(n)
    return out


def _is_own(text, handles):
    t = re.sub(r"[^0-9a-z]", "", (text or "").lower())
    # OCR often reads only part of a moving handle ("@JP" for "@JP Linh"): a partial "@…" read counts too
    short_ok = (text or "").strip().startswith("@") and len(t) >= 2
    return bool(t) and any(h in t or ((len(t) >= 4 or short_ok) and t in h) for h in handles)


def to_screen(box, W, H):
    """Video-pixel box -> fractions of a 9:16 phone screen (the video is fitted inside, centred)."""
    x0, y0, x1, y1 = box
    sc = min(1080 / W, 1920 / H)
    ox, oy = (1080 - W * sc) / 2, (1920 - H * sc) / 2
    return ((ox + x0 * sc) / 1080, (oy + y0 * sc) / 1920, (ox + x1 * sc) / 1080, (oy + y1 * sc) / 1920)


def unsafe_zone(sb, min_frac=0.3):
    """Name of the TikTok UI zone covering at least `min_frac` of this screen box, else None."""
    x0, y0, x1, y1 = sb
    area = max((x1 - x0) * (y1 - y0), 1e-9)
    for name, (a, b, c, d) in UNSAFE:
        ix, iy = min(x1, c) - max(x0, a), min(y1, d) - max(y0, b)
        if ix > 0 and iy > 0 and ix * iy / area >= min_frac:
            return name
    return None


def scan_text(job, path: Path, info: dict, s, progress=None) -> dict:
    """OCR sampled frames: leftover platform watermarks, leftover Chinese text, text under TikTok's UI."""
    from . import watermark
    from .subdetect import _engine
    dur = info["duration"]
    W, H = info["video"]["w"], info["video"]["h"]
    step = 1.0 if dur <= 120 else 2.0 if dur <= 600 else max(3.0, dur / 300)
    handles = _own_handles(s)
    wm, cjk, zones, n = [], [], {}, 0
    o = _engine()
    with tempfile.TemporaryDirectory() as td:
        skip = ["-skip_frame", "nokey"] if dur > LONG_S else []      # keyframes are plenty at one frame per 3+ s
        util.run(job, ["ffmpeg", "-hide_banner", "-loglevel", "error", *skip, "-i", str(path), "-vf",
                       f"fps=1/{step},scale='min(1080,iw)':-2", "-q:v", "3", f"{td}/f_%05d.jpg"])
        frames = sorted(Path(td).glob("f_*.jpg"))
        if not frames:
            return {"frames": 0, "wm": [], "cjk": [], "zones": {}}
        from PIL import Image
        fw, fh = Image.open(frames[0]).size
        kx, ky = W / fw, H / fh
        for i, f in enumerate(frames):
            job.check_cancel()
            t = round(i * step, 1)
            r, _ = o(str(f))
            n += 1
            for b, txt, sc in (r or []):
                if float(sc) < 0.6 or not txt.strip():
                    continue
                xs, ys = [p[0] * kx for p in b], [p[1] * ky for p in b]
                box = (min(xs), min(ys), max(xs), max(ys))
                own = _is_own(txt, handles)
                if watermark.is_wm_text(txt) and not own:
                    wm.append({"t": t, "text": txt})
                elif len(CJK.findall(txt)) >= 2:
                    cjk.append({"t": t, "text": txt})
                elif not CJK.search(txt) and len(txt.strip()) >= 2:
                    z = unsafe_zone(to_screen(box, W, H))
                    if z:
                        key = "own_" + z if own else z
                        zones.setdefault(key, []).append({"t": t, "text": txt})
            if progress:
                progress((i + 1) / len(frames))
    return {"frames": n, "step": step, "wm": wm, "cjk": cjk, "zones": zones}


# ---- the rules ------------------------------------------------------------------------------------------------
def _times(hits, k=4):
    ts = sorted({h["t"] for h in hits})
    return ", ".join(f"{int(t // 60)}:{t % 60:04.1f}" for t in ts[:k]) + (" …" if len(ts) > k else "")


def evaluate(info: dict, an: dict | None, txt: dict | None, mode: str, post: dict | None, extra=None) -> list:
    it = []
    v, a, dur = info["video"], info["audio"], info["duration"]
    mb = info["size"] / 1e6
    # --- file
    fmt = info["format"]
    it.append(_item("container", "Tệp", "Định dạng", "ok" if ("mp4" in fmt or "mov" in fmt) else "fail",
                    "MP4" if "mp4" in fmt else fmt, "Xuất lại MP4", True))
    if not v:
        it.append(_item("video", "Tệp", "Luồng hình", "fail", "Không có luồng hình"))
        return it
    vc = v["codec"]
    it.append(_item("vcodec", "Tệp", "Mã hoá hình",
                    "ok" if vc in ("h264", "hevc") else "fail",
                    f"{vc} {v['profile']}".strip() + ("" if v["pix_fmt"] in ("yuv420p", "yuvj420p") else f", {v['pix_fmt']}"),
                    "Mã hoá lại H.264 yuv420p", True))
    if v["pix_fmt"] not in ("yuv420p", "yuvj420p") and vc in ("h264", "hevc"):
        it[-1]["status"] = "warn"
    if mb > WEB_LIMIT_MB:
        st, fx = "fail", "Quá giới hạn 4 GB của bản web"
    elif mb > APP_LIMIT_MB:
        st, fx = "warn", f"Ứng dụng điện thoại chỉ nhận ~{APP_LIMIT_MB} MB; đăng qua web hoặc dùng bản TikTok"
    else:
        st, fx = "ok", ""
    it.append(_item("size", "Tệp", "Dung lượng", st, f"{mb:.0f} MB", fx, st != "ok"))
    vbr = (v["bitrate"] or info["bitrate"]) / 1e6
    short = min(v["w"], v["h"])
    if vbr > 25:
        st, d = "warn", "Quá cao, TikTok sẽ nén lại mạnh"
    elif vbr and vbr < (2.0 if short >= 1080 else 1.2):
        st, d = "warn", "Thấp, hình có thể bị vỡ sau khi TikTok nén"
    else:
        st, d = "ok", ""
    it.append(_item("bitrate", "Tệp", "Bitrate hình", st, f"{vbr:.1f} Mbps" + (f" — {d}" if d else ""),
                    "Bản TikTok dùng 6–10 Mbps", st != "ok"))
    # --- picture
    if short >= 1080:
        st = "ok"
    elif short >= 720:
        st = "warn"
    else:
        st = "fail"
    it.append(_item("resolution", "Hình", "Độ phân giải", st, f"{v['w']}×{v['h']}",
                    "TikTok khuyên 1080×1920; dưới 720p bị coi là chất lượng thấp", st != "ok"))
    ar = v["w"] / v["h"]
    if abs(ar - 9 / 16) < 0.02:
        st, d = "ok", "9:16 dọc, đầy màn hình"
    elif ar < 1:
        st, d = "warn", f"Dọc nhưng không phải 9:16 ({ar:.2f})"
    else:
        st, d = "warn", ("Ngang" if ar > 1.05 else "Vuông") + " — trên điện thoại chỉ chiếm phần giữa màn hình"
    it.append(_item("aspect", "Hình", "Tỉ lệ khung", st, d, "Bản TikTok: 9:16, nền mờ thay cho viền đen", st != "ok"))
    fps = v["fps"]
    st = "ok" if 23 <= fps <= 60.5 else "warn"
    it.append(_item("fps", "Hình", "Khung hình/giây", st, f"{fps:g} fps", "Nên trong khoảng 24–60 fps", st != "ok"))
    # --- length
    if dur < 3:
        st, d = "fail", "Ngắn hơn 3 giây"
    elif dur < 10:
        st, d = "warn", "Rất ngắn; video 15 giây trở lên thường giữ người xem tốt hơn"
    elif dur > 3600:
        st, d = "fail", "Dài hơn 60 phút"
    elif dur > 600:
        st, d = "warn", "Ứng dụng cho đăng tối đa 10 phút; dài hơn chỉ đăng được qua web ở một số tài khoản"
    else:
        st, d = "ok", ""
    m_, s_ = divmod(int(round(dur)), 60)
    it.append(_item("duration", "Thời lượng", "Độ dài", st, f"{m_}:{s_:02d}" + (f" — {d}" if d else "")))
    if dur > 60:
        it.append(_item("rewards", "Thời lượng", "Creator Rewards", "info",
                        "Dài hơn 1 phút — đủ điều kiện độ dài của Creator Rewards Program"))
    # --- sound
    if not a:
        it.append(_item("audio", "Âm thanh", "Âm thanh", "warn", "Video không có tiếng",
                        "Video có âm thanh thường được đề xuất tốt hơn"))
    else:
        it.append(_item("acodec", "Âm thanh", "Mã hoá tiếng", "ok" if a["codec"] in ("aac", "mp3") else "warn",
                        f"{a['codec']} {a['rate'] // 1000 if a['rate'] else '?'} kHz, {a['channels']} kênh",
                        "Mã hoá lại AAC 48 kHz", a["codec"] not in ("aac", "mp3")))
        if an and an.get("lufs") is not None:
            L = an["lufs"]
            st = "ok" if -16.5 <= L <= -11.5 else "warn"
            d = "vừa" if st == "ok" else ("nhỏ, người xem phải tăng âm lượng" if L < -16.5 else "quá to")
            it.append(_item("loudness", "Âm thanh", "Độ to", st, f"{L:.1f} LUFS ({d}; chuẩn mạng xã hội ≈ −14)",
                            "Bản TikTok chuẩn hoá về −14 LUFS", st != "ok"))
        if an and an.get("true_peak") is not None:
            tp = an["true_peak"]
            st = "ok" if tp <= -0.5 else "warn"
            it.append(_item("peak", "Âm thanh", "Đỉnh âm", st, f"{tp:.1f} dBTP" + ("" if st == "ok" else " — dễ rè sau khi nén"),
                            "Bản TikTok giới hạn −1 dBTP", st != "ok"))
        if an:
            frac = an["silence"] / max(dur, 1)
            st = "warn" if frac > 0.5 else "ok"
            it.append(_item("silence", "Âm thanh", "Khoảng lặng", st, f"{frac * 100:.0f}% thời lượng im lặng",
                            "Thêm nhạc nền hoặc lời đọc" if st != "ok" else ""))
    # --- picture quality
    if an:
        b0 = next((b for b in an["black"] if b[0] < 1.0), None)
        st = "warn" if b0 and b0[1] - b0[0] >= 0.5 else "ok"
        it.append(_item("opening", "Chất lượng", "Mở đầu", st,
                        f"Màn hình đen {b0[1] - b0[0]:.1f} giây đầu" if st == "warn" else "Có hình ngay từ đầu",
                        "3 giây đầu quyết định người xem có lướt qua không; cắt đoạn đen ở đầu" if st == "warn" else ""))
        fr = sum(e - s0 for s0, e in an["freeze"])
        frac = fr / max(dur, 1)
        st = "warn" if frac > 0.4 else "ok"
        it.append(_item("static", "Chất lượng", "Hình chuyển động", st,
                        f"{frac * 100:.0f}% thời lượng hình đứng yên",
                        "Hình tĩnh/slideshow dễ bị đánh giá chất lượng thấp" if st == "warn" else ""))
    # --- For You feed eligibility
    if txt is not None:
        st = "fail" if len({h["t"] for h in txt["wm"]}) >= 2 else ("warn" if txt["wm"] else "ok")
        it.append(_item("wm", "Đề xuất (FYF)", "Watermark nền tảng khác", st,
                        (f"Còn đọc được ở {_times(txt['wm'])}: “{txt['wm'][0]['text']}”" if txt["wm"]
                         else f"Không thấy (đã quét {txt['frames']} khung)"),
                        "Video có logo/tên kênh của nền tảng khác không được đề xuất trên For You. "
                        "Mở thẻ Watermark gốc, vẽ thêm vùng cần xoá rồi render lại" if txt["wm"] else ""))
        nc = len({h["t"] for h in txt["cjk"]})
        st = "warn" if nc >= 2 else "ok"
        it.append(_item("cjk", "Đề xuất (FYF)", "Chữ Trung còn sót", st,
                        f"{nc} khung, vd {_times(txt['cjk'])}: “{txt['cjk'][0]['text']}”" if txt["cjk"] else "Không thấy",
                        "Bật Dịch chữ trên màn hình hoặc sửa ở bảng Chữ trên màn hình" if st == "warn" else ""))
        for z in ("bottom", "right", "top"):
            hits = txt["zones"].get(z, [])
            nz = len({h["t"] for h in hits})
            if nz >= max(2, 0.15 * txt["frames"]):
                it.append(_item("zone_" + z, "Đề xuất (FYF)", "Chữ bị giao diện TikTok che", "warn",
                                f"Chữ nằm dưới {UNSAFE_NAMES[z]} ở {nz} khung, vd “{hits[0]['text'][:40]}”",
                                "Đặt phụ đề cao hơn (khoảng 65–78% chiều cao) để không bị caption che"))
        own = sum(len({h['t'] for h in txt["zones"].get("own_" + z, [])}) for z in ("bottom", "right", "top"))
        if own:
            it.append(_item("zone_own", "Đề xuất (FYF)", "Watermark của bạn", "info",
                            f"Bị nút/caption TikTok che ở {own} khung (watermark chạy nên vẫn hiện ở khung khác)"))
        if not any(x["key"].startswith("zone_") and x["status"] == "warn" for x in it):
            it.append(_item("zones", "Đề xuất (FYF)", "Vùng an toàn", "ok", "Chữ không nằm dưới nút/caption của TikTok"))
    if mode in TRANSFORMED:
        it.append(_item("original", "Đề xuất (FYF)", "Nội dung đã biến đổi", "ok",
                        f"Có {TRANSFORMED[mode]} — không phải đăng lại nguyên bản"))
    else:
        it.append(_item("original", "Đề xuất (FYF)", "Nội dung đã biến đổi", "warn",
                        "Chỉ thêm chữ/phụ đề — TikTok có thể xem là đăng lại ít chỉnh sửa (unoriginal)",
                        "Làm thêm bản Lồng tiếng hoặc Thuyết minh, hoặc thêm bình luận/giới thiệu của bạn"))
    # --- caption
    cap = ((post or {}).get("platforms", {}).get("tiktok", {}) or {}).get("caption", "").strip()
    if not cap:
        it.append(_item("caption", "Caption", "Caption", "warn", "Chưa có caption",
                        "Bấm ↻ Viết lại trong thẻ Nội dung đăng"))
    else:
        tags = HASHTAG.findall(cap)
        st = "ok" if 3 <= len(tags) <= 6 else "warn"
        it.append(_item("hashtags", "Caption", "Hashtag", st, f"{len(tags)} hashtag",
                        "" if st == "ok" else "Nên 3–6 hashtag sát chủ đề; quá nhiều trông như spam"))
        st = "warn" if CJK.search(cap) else "ok"
        it.append(_item("caption_lang", "Caption", "Ngôn ngữ caption", st,
                        "Còn chữ Trung" if st == "warn" else "Tiếng Việt", "Sửa caption" if st == "warn" else ""))
        n = len(cap)
        st = "fail" if n > 4000 else "ok"
        it.append(_item("caption_len", "Caption", "Độ dài caption", st,
                        f"{n} ký tự" + (" — chỉ khoảng 100 ký tự đầu hiện ngay trên video" if n > 150 else ""),
                        "TikTok giới hạn 4000 ký tự" if st == "fail" else ""))
    for x in extra or []:
        it.append(x)
    return it


def verdict(items):
    nf = sum(x["status"] == "fail" for x in items)
    nw = sum(x["status"] == "warn" for x in items)
    score = max(0, 100 - 25 * nf - 7 * nw)
    if nf:
        v = "fail"
    elif nw:
        v = "warn"
    else:
        v = "pass"
    return {"verdict": v, "score": score, "fails": nf, "warns": nw,
            "fixable": [x["key"] for x in items if x["fixable"] and x["status"] in ("warn", "fail")]}


def check_file(job, d: Path, s, name: str, mode: str, scale=(0.0, 1.0), ocr=True, txt=None) -> dict:
    """`txt`: a text scan to reuse (the TikTok version shows the same text at the same phone-screen positions —
    the scan already maps every box onto a 9:16 screen — so it is not OCR'd a second time)."""
    lo, hi = scale
    path = d / name
    info = probe(path)

    def prog(a, b):
        return lambda f: job.step("tiktok", progress=lo + (hi - lo) * (a + (b - a) * f))
    an = analyse(job, path, info, prog(0.0, 0.45))
    if txt is None:
        txt = scan_text(job, path, info, s, prog(0.45, 1.0)) if ocr and info["video"] else None
    post = None
    if (d / "post.json").exists():
        try:
            post = json.loads((d / "post.json").read_text(encoding="utf-8"))
        except Exception:
            post = None
    items = evaluate(info, an, txt, mode, post)
    return {"file": name, "info": info, "items": items, **verdict(items), "_txt": txt}


# ---- the fix --------------------------------------------------------------------------------------------------
def tiktok_filters(W, H, fps, src="[0:v]", out="[v]", tag=""):
    """ffmpeg -filter_complex for a 1080×1920 TikTok picture (`src`/`out` labels let the render embed it)."""
    rate = "" if fps <= 60.5 else ",fps=30"
    if abs(W / H - 9 / 16) < 0.02:
        return f"{src}scale=1080:1920:flags=lanczos,setsar=1{rate},format=yuv420p{out}"
    # fit inside 1080×1920, blurred zoomed copy of the same picture behind it (no black bars)
    return (f"{src}split[ta{tag}][tb{tag}];"
            f"[ta{tag}]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,scale=270:480,"
            f"gblur=sigma=12,eq=brightness=-0.08:saturation=1.1,scale=1080:1920[tbg{tag}];"
            f"[tb{tag}]scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos[tfg{tag}];"
            f"[tbg{tag}][tfg{tag}]overlay=(W-w)/2:(H-h)/2,setsar=1{rate},format=yuv420p{out}")


def tiktok_venc(s, kb, fps):
    """Encoder args of the TikTok copy (bitrate kept under the app upload limit)."""
    g = ["-g", str(int(round(min(fps, 60) * 2)) or 60)]
    if s.get("video_encoder", "h264_nvenc") == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-profile:v", "high", "-rc", "vbr", "-b:v", f"{kb}k",
                "-maxrate", f"{int(kb * 1.5)}k", "-bufsize", f"{kb * 2}k", *g]
    return ["-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-b:v", f"{kb}k",
            "-maxrate", f"{int(kb * 1.5)}k", "-bufsize", f"{kb * 2}k", *g]


TIKTOK_AF = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
TIKTOK_ACODEC = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def video_kbps(dur, limit_mb=APP_LIMIT_MB * 0.93, audio_kbps=192, want=8000):
    """Bitrate that keeps the file under the app upload limit (never below 2.5 Mbps)."""
    fit = limit_mb * 8000 / max(dur, 1) - audio_kbps
    return int(max(2500, min(want, fit)))


def premade(d: Path, src_name: str) -> bool:
    """output_tiktok.mp4 was written by the render together with `src_name` (same pass) and is still current."""
    try:
        m = json.loads((d / PRE).read_text(encoding="utf-8"))
        st = (d / src_name).stat()
        return (d / OUT).exists() and m.get("src") == src_name and m.get("size") == st.st_size \
            and abs(m.get("mtime", 0) - st.st_mtime) < 2
    except (OSError, ValueError):
        return False


def mark_premade(d: Path, src_name: str):
    st = (d / src_name).stat()
    (d / PRE).write_text(json.dumps({"src": src_name, "size": st.st_size, "mtime": st.st_mtime}), encoding="utf-8")


def picture_ok(info) -> bool:
    """9:16 H.264 yuv420p ≤ 60 fps under the size limit: only the sound needs fixing, the picture is copied."""
    v = info["video"]
    return bool(v) and abs(v["w"] / v["h"] - 9 / 16) < 0.02 and v["codec"] == "h264" and v["fps"] <= 60.5 \
        and v.get("pix_fmt") in ("yuv420p", "yuvj420p") and info["size"] <= APP_LIMIT_MB * 1024 * 1024 * 0.95


def make_tiktok(job, d: Path, s, src_name: str, scale=(0.0, 1.0)) -> str:
    src = d / src_name
    if premade(d, src_name):
        job.log("Bản TikTok đã được tạo cùng lượt render — không mã hoá lại")
        return OUT
    info = probe(src)
    v, dur = info["video"], info["duration"]
    if picture_ok(info) and info["audio"]:
        # vertical video already in TikTok's format: copy the picture, re-encode only the sound (seconds, not minutes)
        job.log("Hình đã đúng chuẩn TikTok — giữ nguyên hình, chỉ chuẩn hoá âm thanh")
        util.ffmpeg(job, ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
                          "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000", "-c:a", "aac", "-b:a", "192k",
                          "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(d / OUT)],
                    duration=dur, step="tiktok", scale=scale)
        return OUT
    kb = video_kbps(dur)
    enc = s.get("video_encoder", "h264_nvenc")
    if enc == "h264_nvenc":
        venc = ["-c:v", "h264_nvenc", "-preset", "p5", "-profile:v", "high", "-rc", "vbr",
                "-b:v", f"{kb}k", "-maxrate", f"{int(kb * 1.5)}k", "-bufsize", f"{kb * 2}k"]
    else:
        venc = ["-c:v", "libx264", "-preset", "medium", "-profile:v", "high",
                "-b:v", f"{kb}k", "-maxrate", f"{int(kb * 1.5)}k", "-bufsize", f"{kb * 2}k"]
    args = ["-i", str(src), "-filter_complex", tiktok_filters(v["w"], v["h"], v["fps"]), "-map", "[v]"]
    if info["audio"]:
        args += ["-map", "0:a:0", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000",
                 "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    args += [*venc, "-g", str(int(round(min(v["fps"], 60) * 2)) or 60), "-movflags", "+faststart",
             "-metadata:s:v", "rotate=0", str(d / OUT)]
    try:
        util.ffmpeg(job, args, duration=dur, step="tiktok", scale=scale)
    except RuntimeError:
        if enc != "h264_nvenc":
            raise
        job.log("NVENC lỗi khi tạo bản TikTok — mã hoá lại bằng CPU (libx264)", "warn")
        return make_tiktok(job, d, dict(s, video_encoder="libx264"), src_name, scale)
    return OUT


def run(job, d: Path, s, src_name: str, mode: str) -> dict:
    """Check the finished video; if something fixable is off, make output_tiktok.mp4 and check that too."""
    rep = check_file(job, d, s, src_name, mode, scale=(0.0, 0.45))
    report = {"source": rep, "tiktok": None}
    if s.get("tiktok_autofix", True) and rep["fixable"]:
        job.step("tiktok", detail="Tạo bản chuẩn TikTok…")
        make_tiktok(job, d, s, src_name, scale=(0.45, 0.75))
        job.add_file("output_tiktok", OUT)
        report["tiktok"] = check_file(job, d, s, OUT, mode, scale=(0.75, 1.0), txt=rep.get("_txt"))
    elif (d / OUT).exists():                # an older TikTok version would be stale now
        (d / OUT).unlink()
        job.files.pop("output_tiktok", None)
    for r in (report["source"], report["tiktok"]):
        if r:
            r.pop("_txt", None)
    save(d, report)
    return report


def best(report):
    return report.get("tiktok") or report.get("source")


def summary(report) -> str:
    r = best(report)
    name = {"pass": "Đạt", "warn": "Đạt, có lưu ý", "fail": "Chưa đạt"}[r["verdict"]]
    s = f"{name} · {r['score']}/100"
    if report.get("tiktok"):
        s += " (bản TikTok)"
    return s


def save(d: Path, report):
    (d / FILE).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


def load(d: Path):
    p = d / FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
