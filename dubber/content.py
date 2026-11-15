"""Bước "Biên kịch" (content): a layer of original content on top of a reup so TikTok does not treat it as an
unoriginal repost — hook, first-person narration in the silences, the twist hidden in small on-screen text read
aloud, cuts of dead air, retention text, an ending card + a line that loops back to the opening, cover, caption,
pinned comment and teaser clips.

1. timeline()      — no AI: dialogue (lines.json), silences ≥ 3 s, scene changes (PySceneDetect / ffmpeg), small
                     or long on-screen text blocks (screentext.json: where twists hide), the original title.
2. watch()         — AI looks at the film: keyframes in labelled 3×3 grids (one call), describes each, picks the
                     strongest scene (hook / cover) and the climax. Cached by the video fingerprint — rewriting
                     the plan never calls it again.
3. write()         — AI writes content.json from the timeline + the scene notes (one call).
4. validate()      — clamps/repairs the plan; CutMap — original time ↔ time after the cuts.

Spec: docs/noi-dung/prompt-ky-thuat-content-pipeline.md · reference: docs/noi-dung/20261114-203147-6cf9_ghe-phu.md
"""
import json
import re
import subprocess
import sys
from pathlib import Path

from . import util

FILE = "content.json"
VISION_VERSION = 2          # bump when the "watch the film" prompt changes (editing the writer must not re-watch)
VISION_FILE = "content_vision.json"
SILENCE_MIN = 3.0
PYDEPS = Path(__file__).resolve().parent.parent / "vendor" / "pydeps"

PERSONAS = {"toi": "ngôi thứ nhất, một nhân vật chính tự kể (xưng “tôi”)",
            "nguoi_dan_chuyen": "người dẫn chuyện đứng ngoài, kể về các nhân vật",
            "nhan_vat_nu": "nhân vật nữ tự kể (xưng “tôi”/“em”)"}
TONES = {"tram_it_chu": "trầm, ít chữ, câu ngắn, nghỉ dài giữa các câu",
         "ngon_tinh": "ngôn tình, giàu cảm xúc, hơi văn",
         "doi_thuong": "đời thường, gần gũi, như kể cho bạn nghe"}


# ------------------------------------------------------------------ 1. timeline (no AI)
def silences(lines, total, min_gap=SILENCE_MIN):
    """Gaps of at least `min_gap` s with no dialogue: [(start, end)], including before the first line and after
    the last one. Overlapping lines are merged first."""
    spans = sorted((float(l["start"]), float(l["end"])) for l in lines if l.get("end") is not None)
    out, cur = [], 0.0
    for a, b in spans:
        if a - cur >= min_gap:
            out.append((round(cur, 2), round(a, 2)))
        cur = max(cur, b)
    if total - cur >= min_gap:
        out.append((round(cur, 2), round(total, 2)))
    return out


def scenes(job, video: Path, total: float):
    """Scene-change times (s). PySceneDetect ContentDetector; ffmpeg select='gt(scene,0.3)' if it is missing."""
    try:
        if PYDEPS.exists() and str(PYDEPS) not in sys.path:
            sys.path.append(str(PYDEPS))
        from scenedetect import ContentDetector, SceneManager, open_video
        v = open_video(str(video))
        sm = SceneManager()
        sm.auto_downscale = True
        sm.add_detector(ContentDetector(threshold=27.0, min_scene_len=15))
        sm.detect_scenes(v)
        return [round(s[0].get_seconds(), 2) for s in sm.get_scene_list()]
    except Exception as e:
        job.log(f"PySceneDetect không chạy được ({e}) → dùng ffmpeg để tìm chuyển cảnh", "warn")
    out = util.run(job, ["ffmpeg", "-hide_banner", "-i", str(video), "-vf", "scale=320:-2,select='gt(scene,0.3)',showinfo",
                         "-an", "-f", "null", "-"], check=False)
    return [0.0] + [round(float(m), 2) for m in re.findall(r"pts_time:([\d.]+)", out)]


def small_text_blocks(d: Path):
    """On-screen text likely to hide the story (comments, messages, paragraphs): long, small or brief."""
    p = d / "screentext.json"
    if not p.exists():
        return []
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return _small_from(st["events"] if isinstance(st, dict) else st)


def _small_from(ev):
    out = []
    for e in ev:
        src = (e.get("src") or "").strip()
        dur = float(e["end"]) - float(e["start"])
        lh = float(e.get("line_h") or 0.05)
        r = e.get("rows") or 1
        rows = r if isinstance(r, int) else len(r)
        if len(src) >= 18 or rows >= 3 or (lh < 0.035 and len(src) >= 8) or (dur < 4 and len(src) >= 12):
            out.append({"id": e["id"], "start": round(float(e["start"]), 2), "end": round(float(e["end"]), 2),
                        "src": src, "tgt": e.get("tgt") or "", "small": lh < 0.035, "rows": rows})
    return out


def timeline(job, d: Path, s, total: float, lines=None, events=None):
    """Analysis timeline. `lines`: the transcript (start/end/text + speaker/gender) when the writer runs before the
    translation; else lines.json. `events`: on-screen text (OCR) — else screentext.json. Scenes come from the
    film-watching step (content_vision.json) when it ran, else are detected here."""
    if lines is None:
        lines = json.loads((d / "lines.json").read_text(encoding="utf-8")) if (d / "lines.json").exists() else []
    lines = [l for l in lines if not l.get("hook") and l.get("role") != "content_vo"]
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8")) if (d / "meta.json").exists() else {}
    vis = _vision_file(d)
    if vis and vis.get("scenes") is not None:
        sc = vis["scenes"]
    else:
        sc = scenes(job, _video(d), total)
    tl = {"duration": round(total, 2), "title": meta.get("title") or "", "uploader": meta.get("uploader") or "",
          "lines": [{"start": round(l["start"], 2), "end": round(l["end"], 2), "speaker": l.get("speaker"),
                     "gender": l.get("gender"), "src": l.get("src") or l.get("text") or "", "vi": l.get("tgt", "")}
                    for l in lines],
          "silences": silences(lines, total), "scenes": sc,
          "small_text": small_text_blocks(d) if events is None else _small_from(events)}
    if events is None and (d / "screentext.json").exists():
        try:
            st = json.loads((d / "screentext.json").read_text(encoding="utf-8"))
            events = st["events"] if isinstance(st, dict) else st
        except ValueError:
            events = []
    # every on-screen text (dates, messages, names): the narration must not read them out again
    tl["screen"] = [{"start": round(float(e["start"]), 2), "end": round(float(e["end"]), 2),
                     "src": (e.get("src") or "").strip(), "tgt": (e.get("tgt") or "").strip()}
                    for e in events or () if (e.get("src") or e.get("tgt"))]
    tl["video_type_rule"], tl["video_type_rule_why"] = classify_rules(tl, s)
    old = load(d)
    if old and old.get("video_type_set") in VIDEO_TYPES:       # you changed the type in the Biên kịch tab
        tl["video_type_set"] = old["video_type_set"]
    return tl


def _video(d: Path):
    for n in ("source_view.mp4", "source_orig.mp4", "source.mp4"):
        if (d / n).exists():
            return d / n
    return d / "source.mp4"


def _vision_file(d: Path):
    try:
        return json.loads((d / VISION_FILE).read_text(encoding="utf-8")) if (d / VISION_FILE).exists() else None
    except ValueError:
        return None


VIDEO_TYPES = {
    "story_sparse": "phim có cốt truyện, nhiều khoảng lặng/chữ dài → đầy đủ: hook, lời kể lấp khoảng lặng, cắt, cái kết đọc thành lời, loop",
    "story_dense": "phim có cốt truyện, thoại gần như liên tục → hook, thanh tiêu đề, cắt nhẹ, cái kết, caption; KHÔNG lời kể đè thoại",
    "narrated": "video đã có người thuyết minh → KHÔNG thêm giọng kể thứ hai; hook + caption + cái kết",
    "short_clip": "clip ngắn (hài/meme/khoảnh khắc) → chỉ hook chữ + caption + bình luận ghim; không lời kể, không cắt",
}


def narrator_speaker(lines):
    """The speaker who talks the most (narrator.py uses the same rule) — the voice-over of a narrated video."""
    talk = {}
    for l in lines:
        k = l.get("speaker") or "S1"
        talk[k] = talk.get(k, 0.0) + float(l["end"]) - float(l["start"])
    return max(talk, key=talk.get) if talk else None


def classify_rules(tl, s=None):
    """(video_type, why) from the timeline alone — duration, share of silence, long text, one dominant voice.
    Thresholds come from the settings (content_short_s, content_sparse_ratio, content_narrated_share)."""
    s = s or {}
    dur = float(tl["duration"]) or 1.0
    if dur < float(s.get("content_short_s", 30)):
        return "short_clip", f"dài {dur:.0f}s (< {s.get('content_short_s', 30)}s)"
    lines = tl["lines"]
    talk = {}
    for l in lines:
        talk[l.get("speaker") or "S1"] = talk.get(l.get("speaker") or "S1", 0.0) + float(l["end"]) - float(l["start"])
    tot = sum(talk.values()) or 1.0
    top = max(talk.values()) / tot if talk else 0.0
    if s.get("narrator_remove") or (len(talk) >= 2 and top >= float(s.get("content_narrated_share", 0.7))
                                    and len(lines) / dur > 0.25):
        return "narrated", (f"một giọng nói {top:.0%} thời lượng thoại" if not s.get("narrator_remove")
                            else "job đang bật “xoá giọng thuyết minh”")
    quiet = sum(b - a for a, b in tl["silences"]) / dur
    if quiet >= float(s.get("content_sparse_ratio", 0.15)) or any(len(b.get("src", "")) >= 30 for b in tl["small_text"]):
        return "story_sparse", f"khoảng lặng {quiet:.0%} thời lượng" + (", có khối chữ dài" if tl["small_text"] else "")
    return "story_dense", f"khoảng lặng chỉ {quiet:.0%} thời lượng — thoại gần như liên tục"


def keyframe_times(tl, limit=45):
    """One frame per scene, one in every long silence, one on every small-text block — at most `limit`."""
    pts = [("scene", t + 0.6) for t in tl["scenes"]]
    pts += [("silence", (a + b) / 2) for a, b in tl["silences"]]
    pts += [("text", (b["start"] + b["end"]) / 2) for b in tl["small_text"]]
    pts = sorted((k, min(max(0.0, t), tl["duration"] - 0.1)) for k, t in pts)
    pts.sort(key=lambda x: x[1])
    keep = []
    for k, t in pts:                                  # drop near-duplicates (< 1.2 s apart) — keep text frames
        if keep and t - keep[-1][1] < 1.2:
            if k == "text":
                keep[-1] = (k, t)
            continue
        keep.append((k, t))
    if len(keep) > limit:                             # thin out scene frames first
        scenes_only = [x for x in keep if x[0] == "scene"]
        others = [x for x in keep if x[0] != "scene"]
        step = max(1, len(scenes_only) // max(1, limit - len(others)))
        keep = sorted(others + scenes_only[::step], key=lambda x: x[1])[:limit]
    return [{"kind": k, "t": round(t, 2)} for k, t in keep]


def fmt_t(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def make_grids(job, video: Path, frames, out_dir: Path, cell=512):
    """Labelled 3×3 grids of keyframes (one image per 9 frames) → [paths]."""
    from PIL import Image, ImageDraw, ImageFont
    out_dir.mkdir(parents=True, exist_ok=True)
    cells = []
    for i, fr in enumerate(frames):
        p = out_dir / f"k_{i:03d}.jpg"
        if not p.exists():
            util.run(job, ["ffmpeg", "-v", "error", "-y", "-ss", f"{fr['t']:.2f}", "-i", str(video), "-frames:v", "1",
                           "-vf", f"scale={cell}:-2", str(p)], check=False)
        if p.exists():
            cells.append((fr, Image.open(p).convert("RGB")))
    try:
        font = ImageFont.truetype("arialbd.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
    paths = []
    for g in range(0, len(cells), 9):
        part = cells[g:g + 9]
        ch = max(im.height for _, im in part)
        grid = Image.new("RGB", (cell * 3, ch * ((len(part) + 2) // 3)), (0, 0, 0))
        dr = ImageDraw.Draw(grid)
        for k, (fr, im) in enumerate(part):
            x, y = (k % 3) * cell, (k // 3) * ch
            grid.paste(im, (x, y))
            label = f"{fmt_t(fr['t'])}" + {"text": " CHỮ", "silence": " LẶNG", "scene": ""}[fr["kind"]]
            dr.rectangle([x, y, x + 12 + int(dr.textlength(label, font=font)), y + 40], fill=(0, 0, 0))
            dr.text((x + 6, y + 4), label, fill=(255, 220, 0), font=font)
        p = out_dir / f"grid_{g // 9 + 1}.jpg"
        grid.save(p, quality=85)
        paths.append(p)
    return paths


# ------------------------------------------------------------------ 2–3. AI
VISION_SCHEMA = {"type": "object", "required": ["frames", "strongest", "climax"], "properties": {
    "frames": {"type": "array", "items": {"type": "object", "required": ["t", "desc"], "properties": {
        "t": {"type": "number"}, "desc": {"type": "string"}, "emotion": {"type": "string"},
        "text_on_screen": {"type": "string"}, "era": {"type": "string", "enum": ["hien_tai", "hoi_uc", "khong_ro"]}}}},
    "strongest": {"type": "object", "properties": {"t": {"type": "number"}, "why": {"type": "string"}}},
    "climax": {"type": "object", "properties": {"from": {"type": "number"}, "to": {"type": "number"},
                                                 "why": {"type": "string"}}},
    "cover": {"type": "array", "items": {"type": "number"}},
    "story": {"type": "string"}}}

PLAN_SCHEMA = {"type": "object", "required": ["persona", "hook", "hooks", "title_bar", "narration", "cuts", "ending", "post"],
               "properties": {
    "persona": {"type": "object", "required": ["pov", "tone", "me", "her"], "properties": {
        "pov": {"type": "string"}, "tone": {"type": "string"}, "me": {"type": "string"},
        "her": {"type": "string"}, "him": {"type": "string"}}},
    "hook": {"type": "object", "properties": {"vo": {"type": "string"}, "overlay": {"type": "string"},
                                              "start": {"type": "number"}, "end": {"type": "number"},
                                              "alt_cold_open": {"type": "object"}}},
    "hooks": {"type": "array", "items": {"type": "object", "required": ["vo", "overlay"], "properties": {
        "vo": {"type": "string"}, "overlay": {"type": "string"}, "angle": {"type": "string"}}}},
    "title_bar": {"type": "string"},
    "video_type": {"type": "string", "enum": list(VIDEO_TYPES)}, "video_type_why": {"type": "string"},
    "narration": {"type": "array", "items": {"type": "object", "required": ["start", "text"], "properties": {
        "start": {"type": "number"}, "end": {"type": "number"}, "text": {"type": "string"},
        "duck_original": {"type": "number"}}}},
    "cuts": {"type": "array", "items": {"type": "object", "required": ["from", "to"], "properties": {
        "from": {"type": "number"}, "to": {"type": "number"}, "reason": {"type": "string"}}}},
    "retention_texts": {"type": "array", "items": {"type": "object", "properties": {
        "at": {"type": "number"}, "text": {"type": "string"}}}},
    "replace_screentext": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "integer"}, "vo": {"type": "array", "items": {"type": "string"}}, "big_subs": {"type": "boolean"}}}},
    "ending": {"type": "object", "properties": {"overlay": {"type": "string"}, "loop_vo": {"type": "string"}}},
    "cover": {"type": "object", "properties": {"at": {"type": "number"}, "text": {"type": "string"}}},
    "post": {"type": "object", "properties": {"caption": {"type": "string"}, "hashtags": {"type": "array", "items": {"type": "string"}},
                                              "pinned_comment": {"type": "string"}, "ai_label": {"type": "boolean"},
                                              "credit": {"type": "string"}}},
    "teasers": {"type": "array", "items": {"type": "object", "properties": {
        "from": {"type": "number"}, "to": {"type": "number"}, "overlay": {"type": "string"}, "end_card": {"type": "string"}}}},
    "rewrite": {"type": "array", "items": {"type": "object", "required": ["start", "text"], "properties": {
        "start": {"type": "number"}, "text": {"type": "string"}}}}}}


def _order(s, key):
    first = s.get(key) or "codex_cli"
    out = []
    for p in [first, "codex_cli", "claude_cli", "ollama"]:
        if p != "gemini_cli" and p not in out:          # the user does not want Gemini in this step
            out.append(p)
    return out


def _parse(raw):
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise ValueError("AI không trả về JSON")
    return json.loads(m.group(0))


def video_keyframes(sc, total, limit=45):
    """Frames for the film-watching step before anything else is known: one per scene + an even sampling."""
    step = max(4.0, total / 40)
    pts = [("scene", t + 0.6) for t in sc] + [("even", t) for t in [x * step for x in range(1, int(total / step) + 1)]]
    pts = sorted((k, min(max(0.0, t), total - 0.1)) for k, t in pts)
    pts.sort(key=lambda x: x[1])
    keep = []
    for k, t in pts:
        if keep and t - keep[-1][1] < 2.0:
            continue
        keep.append((k, t))
    if len(keep) > limit:
        idx = [round(i * (len(keep) - 1) / (limit - 1)) for i in range(limit)]
        keep = [keep[i] for i in sorted(set(idx))]
    return [{"kind": "scene", "t": round(t, 2)} for k, t in keep]


def watch(job, d: Path, s, total: float):
    """AI looks at the film (video only — it runs right after the download, beside the sound steps): keyframe
    grids → per-frame notes with ERA (present / flashback), the strongest scene, the climax, a summary.
    Cached in content_vision.json by video fingerprint (+ frames + VISION_VERSION)."""
    from . import checkpoint as ck
    from .providers import orchestrator
    from .providers.base import BadOutput, NotConfigured
    video = _video(d)
    old = _vision_file(d)
    if old and old.get("video_fp") == ck.fp(video) and old.get("version") == VISION_VERSION and old.get("notes"):
        job.log("♻ AI xem phim: dùng lại ghi chú cảnh đã có (video không đổi)")
        return old["notes"]
    sc = scenes(job, video, total)
    frames = video_keyframes(sc, total)
    grids = make_grids(job, video, frames, d / "content_frames")
    job.log(f"AI xem phim: {len(sc)} cảnh, {len(frames)} khung hình trong {len(grids)} lưới 3×3")
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8")) if (d / "meta.json").exists() else {}
    system = ("Bạn là người dựng phim. Bạn được xem các lưới ảnh 3×3 theo thứ tự thời gian; góc trái mỗi ô ghi mốc "
              "(phút:giây) của khung hình.")
    user = (f"Phim: {meta.get('title') or ''}\nDài {fmt_t(total)}.\n\n"
            "Với MỖI ô: t (giây), desc (ai, đang làm gì, ở đâu — 1 câu), emotion, text_on_screen (nếu đọc được), "
            "era: 'hien_tai' nếu là thời điểm hiện tại của câu chuyện, 'hoi_uc' nếu là cảnh quá khứ/hồi tưởng, "
            "'khong_ro' nếu không chắc (dựa vào trang phục, đồ vật, ngày giờ trên màn hình, màu hình, mạch truyện). "
            "Rồi 'strongest' (cảnh mạnh nhất để làm hook/ảnh bìa, kèm lý do), 'climax' (đoạn cao trào from/to giây), "
            "'cover' (2–3 mốc giây đẹp làm ảnh bìa), 'story' (tóm tắt cả phim 3–4 câu, nêu rõ cú twist nếu thấy). Trả về JSON.")
    sv = dict(s, _step="content", _step_detail="AI đang xem phim…",
              ollama_model=s.get("content_ollama_vision_model") or "qwen2.5vl:7b")
    if s.get("content_codex_model"):
        sv["codex_model"] = s["content_codex_model"]

    def task(p):
        if not getattr(p, "vision", False) or p.kind != "llm":
            raise NotConfigured(f"{p.id} không xem được ảnh")
        raw = p.complete(job, system, user, VISION_SCHEMA, sv, images=grids)
        try:
            return _parse(raw)
        except ValueError as e:
            raise BadOutput(str(e))
    notes, pid = orchestrator.run(job, sv, task, "xem phim", order=_order(s, "content_vision_provider"))
    job.log(f"AI xem phim bằng: {pid}")
    (d / VISION_FILE).write_text(json.dumps({"video_fp": ck.fp(video), "version": VISION_VERSION, "provider": pid,
                                             "scenes": sc, "frames": frames, "notes": notes}, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
    return notes


RULES = """Quy tắc bắt buộc:
- XƯNG HÔ NHẤT QUÁN: chọn MỘT cách gọi cho người kể (persona.me, vd "tôi") và MỘT cách gọi nhân vật nữ (persona.her, vd "cô ấy" HOẶC "em") — mọi câu (hook, lời kể, đọc khối chữ, câu kết, câu loop) dùng đúng cách gọi đó, không đổi qua lại.
- HOOK (0–3 giây) tạo TÒ MÒ bằng một khoảng trống thông tin (úp mở: ai, khi nào, mất gì), KHÔNG tóm tắt cả phim. hook.vo = giọng kể đè lên cảnh mở, nói về đúng cảnh đang chiếu; hook.overlay = chữ to TỐI ĐA 10 CHỮ, sắc, có tương phản.
- 'hooks': viết 3 PHƯƠNG ÁN hook khác hẳn nhau (mỗi phương án {vo, overlay}, góc khác nhau: bí mật / mất mát / câu hỏi), phương án tốt nhất đứng đầu; 'hook' = phương án đầu.
- LỜI KỂ KHỚP HÌNH: mỗi câu kể đặt trong một khoảng lặng và phải đúng với cảnh đang chiếu ở mốc start của nó (xem bảng "Cảnh trong từng khoảng lặng"): hình là hiện tại thì không nói "ngày ấy/năm đó"; hình là hồi ức thì không nói như đang ở hiện tại.
- ÍT MÀ ĐẮT: tối đa khoảng 2 câu kể mỗi phút (phim 4 phút ≈ 8 câu), mỗi câu ≤ 12 chữ. Mỗi câu PHẢI mang điều hình không nói được (bí mật, việc đã xảy ra ngoài khung hình, điều nhân vật giấu) hoặc một cảm xúc ngầm CỤ THỂ (gắn với đồ vật, hành động). Câu chỉ thuật lại hình → BỎ. CẤM câu giải thích cảm xúc trừu tượng. duck_original 0.25–0.4.
- CẤM nhắc lại chữ đang hiện trên màn hình: không đọc lại ngày tháng, năm, giờ, tin nhắn, tên hiển thị mà người xem đang thấy (xem cột chữ trên màn hình / khối chữ nhỏ). Riêng cú twist trong chữ nhỏ thì đọc qua replace_screentext.
- Không quá 1 câu kể mở đầu bằng cùng một cụm từ (vd chỉ một câu "Hôm ấy…").
- Không khoảng lặng nào > 5 giây mà không có lời kể hoặc bị cắt: dùng cuts để rút khoảng lặng dài (giữ 2–4 giây hình).
- cuts CHỈ nằm TRONG các khoảng lặng đã liệt kê: KHÔNG BAO GIỜ cắt đoạn có thoại, không cắt cảnh mở 0–3 giây và không đổi thứ tự cảnh.
- Cú twist nằm trong chữ nhỏ trên màn hình BẮT BUỘC được đọc thành lời: replace_screentext với id khối chữ + vo (các câu ngắn), big_subs true.
- ending.overlay: câu chốt chữ to trên nền tối. ending.loop_vo: câu cuối MANG CẢM XÚC và NỐI NGHĨA về cảnh mở (người xem nghe xong muốn xem lại cảnh đầu) — KHÔNG tả lại hình.
- retention_texts: 1–2 chữ nhỏ giữ chân, ví dụ quanh mốc 1 phút: "Đoạn cuối mới thật sự đau…".
- title_bar: tiêu đề cố định ngắn (≤ 10 chữ), nói đúng chủ đề, hiện SUỐT video nên TUYỆT ĐỐI KHÔNG LỘ TWIST/CÁI KẾT (sai: "Thành công sau khi cô ấy lấy chồng" — lộ kết; đúng: "Chiếc ghế phụ không ai ngồi").
- cover: mốc giây của khung đẹp nhất + chữ trên ảnh bìa.
- post.caption: câu đầu chứa từ khoá tìm kiếm (thể loại + chủ đề), kết bằng MỘT CÂU HỎI GÂY TRANH CÃI. post.hashtags: 4–6 tag đúng chủ đề, KHÔNG dùng #fyp #xuhuong #viral #foryou. post.pinned_comment: câu hỏi mời bình luận. post.ai_label true nếu phim làm bằng AI/hoạt hình AI. post.credit: "Nguồn: <tên kênh gốc> (Douyin)".
- teasers: 2 clip mồi 30–60 giây (from/to theo giây gốc) có overlay hook riêng, end_card "Bản đầy đủ trên kênh 👆".
- Mọi mốc thời gian là giây theo video GỐC. Viết tiếng Việt tự nhiên, không sến, không bịa tình tiết trái với phim.

Ví dụ (phim khác, chỉ để thấy kiểu đúng/sai):
- Hook XẤU (chỉ tóm tắt): "Có xe sang rồi, lại mất người thương".
  Hook TỐT (úp mở, đúng cảnh mở — xe sang đỗ cạnh cặp đôi đi xe điện): vo "Sáu năm trước, người ngồi sau chiếc xe điện đó… là cô ấy." + overlay "Có Maybach. Mất người ngồi ghế phụ."
- Lời kể XẤU (cảm xúc trừu tượng): "Tôi đã nghe lời hẹn ấy như một lời đảm bảo." / "Em thương tôi, tôi lại thấy mình mắc nợ."
  Lời kể XẤU (đọc lại chữ trên màn hình / thuật lại hình): "Ngày ấy là 21 tháng 10 năm 2019." / "Cô ấy nhắn sắp tan làm." / "Tôi đứng nhìn chiếc xe."
  Lời kể TỐT (cụ thể, hình không tự nói được): "Chỉ tấm ảnh này… tôi chưa từng đổi." / "Năm đó, tôi chẳng có gì." / "Tôi giấu cô ấy… chuyện đi giao đồ ăn."
- Loop XẤU (tả lại hình): "Xe đen lại dừng cạnh xe giao hàng ở ngã tư."
  Loop TỐT (cảm xúc, nối nghĩa về cảnh mở): "Tôi cũng từng hạnh phúc… như cậu trai trên chiếc xe điện kia."
- Xưng hô XẤU: hook nói "cô ấy", lời kể sau lại gọi "em". TỐT: chọn một và giữ suốt."""


SCOPE = {
    "story_sparse": "Can thiệp ĐẦY ĐỦ.",
    "story_dense": "Thoại gần như liên tục: narration chỉ 0–3 câu thật ngắn và chỉ ở khoảng lặng; cắt nhẹ; vẫn có hook, title_bar, ending, caption.",
    "narrated": ("Video ĐÃ có người thuyết minh: narration = [] (KHÔNG thêm giọng kể thứ hai), không replace_screentext. "
                 "VIẾT LẠI chính lời thuyết minh theo ngôi kể/tone: 'rewrite' = [{start (giây, ĐÚNG giờ câu thuyết minh gốc), "
                 "text}] cho các câu đánh dấu [THUYẾT MINH], độ dài tương đương câu cũ (đọc kịp trong thời lượng câu). "
                 "Cộng hook, title_bar, cắt nhẹ, ending, caption."),
    "short_clip": "Clip ngắn: CHỈ hook (overlay, vo có thể rỗng), post (caption, hashtags, pinned_comment). narration = [], cuts = [], retention_texts = [], replace_screentext = [], teasers = [], ending.loop_vo rỗng.",
}


def write(job, d: Path, s, tl, notes, persona="auto", tone="auto"):
    from .providers import orchestrator
    from .providers.base import BadOutput
    narr = narrator_speaker(tl["lines"])
    text = "\n".join(f"[{l['start']:.1f}–{l['end']:.1f}] {l.get('speaker') or ''}({'nữ' if l.get('gender') == 'female' else 'nam'})"
                     f"{' [THUYẾT MINH]' if l.get('speaker') == narr else ''}: "
                     + (f"{l['vi']}  /  {l['src']}" if l.get("vi") else l["src"]) for l in tl["lines"])
    scenes_in = scene_table(tl, notes)
    sil = ", ".join(f"{a:.1f}–{b:.1f} ({b - a:.0f}s)" for a, b in tl["silences"]) or "(không)"
    small = "\n".join(f"#{b['id']} [{b['start']:.1f}–{b['end']:.1f}] {b['tgt'] or ''}  /  {b['src']}" for b in tl["small_text"]) or "(không)"
    shown = "\n".join(f"[{e['start']:.1f}–{e['end']:.1f}] {e['tgt'] or e['src']}" for e in (tl.get("screen") or [])[:80]) or "(không)"
    frames = "\n".join(f"[{float(x.get('t', 0)):.1f}] ({ERA.get(x.get('era'), '?')}) {x.get('desc', '')} ({x.get('emotion', '')})"
                       for x in notes.get("frames", []))
    pv = ("tự chọn ngôi kể hợp nhất (toi | nguoi_dan_chuyen | nhan_vat_nu)" if persona in (None, "", "auto")
          else f"{persona} = {PERSONAS.get(persona, persona)}")
    tn = "tự chọn tone hợp nhất (tram_it_chu | ngon_tinh | doi_thuong)" if tone in (None, "", "auto") else f"{tone} = {TONES.get(tone, tone)}"
    system = ("Bạn là biên kịch TikTok cho kênh phim ngắn reup tiếng Việt. Bạn viết LỚP NỘI DUNG MỚI (giọng kể, chữ, nhịp dựng) "
              "để video không còn là reup trơn và giữ chân người xem.\n" + RULES)
    rule = tl.get("video_type_rule", "story_sparse")
    forced = tl.get("video_type_set") if tl.get("video_type_set") in VIDEO_TYPES else None
    types = "\n".join(f"  {k}: {VIDEO_TYPES[k]} — {SCOPE[k]}" for k in VIDEO_TYPES)
    type_line = (f"Loại video (bạn đã chọn, giữ nguyên): {forced} — {SCOPE[forced]}" if forced else
                 f"Chọn 'video_type' (+ 'video_type_why') trong các loại sau rồi viết đúng phạm vi của loại đó:\n{types}\n"
                 f"Phân tích tự động gợi ý: {rule} ({tl.get('video_type_rule_why', '')}).")
    user = (f"{type_line}\n"
            f"Phim: {tl['title']} — kênh gốc: {tl['uploader'] or '?'} — dài {tl['duration']:.0f} giây.\n"
            f"Ngôi kể: {pv}. Tone: {tn}. Ghi lựa chọn vào persona: pov, tone, me (cách người kể xưng), her (cách gọi nhân vật nữ), him (nhân vật nam).\n\n"
            f"Tóm tắt (AI xem phim): {notes.get('story', '')}\n"
            f"Cảnh mạnh nhất: {json.dumps(notes.get('strongest', {}), ensure_ascii=False)}; cao trào: {json.dumps(notes.get('climax', {}), ensure_ascii=False)}; "
            f"gợi ý ảnh bìa: {notes.get('cover', [])}\n\nKhung hình:\n{frames}\n\nThoại (giờ bắt đầu–kết thúc, người nói, bản dịch / gốc):\n{text}\n\n"
            f"Khoảng lặng ≥ 3 giây: {sil}\n\nCảnh trong từng khoảng lặng (câu kể đặt ở đâu phải khớp cảnh ở đó):\n{scenes_in}\n\n"
            f"Khối chữ nhỏ/dài trên màn hình (id, thời gian, dịch / gốc):\n{small}\n\n"
            f"Mọi chữ đang hiện trên màn hình (người xem ĐÃ đọc được — lời kể KHÔNG nhắc lại):\n{shown}\n\n"
            f"Số câu kể tối đa cho phim này: {narration_cap(tl['duration'])}.\n"
            + ("Cảnh mở 0–3 giây ĐÃ CÓ THOẠI: giọng kể không được đè thoại → hook.vo (và vo của mọi phương án) để RỖNG, "
               "hook chỉ là chữ.\n" if any(float(l["start"]) < 3.0 for l in tl["lines"]) else "")
            + "Viết content plan JSON đúng schema.")
    sw = dict(s, _step="content", _step_detail="AI đang viết kịch bản…", ollama_model=s.get("content_ollama_model") or "qwen2.5:7b")
    if s.get("content_codex_model"):
        sw["codex_model"] = s["content_codex_model"]

    def task(p):
        if p.kind != "llm":
            raise BadOutput("cần model ngôn ngữ")
        raw = p.complete(job, system, user, PLAN_SCHEMA, sw)
        try:
            return _parse(raw)
        except ValueError as e:
            raise BadOutput(str(e))
    plan, pid = orchestrator.run(job, sw, task, "viết kịch bản", order=_order(s, "content_writer_provider"))
    job.log(f"AI viết kịch bản bằng: {pid}")
    issues = quality_check(plan, notes, tl)
    for rnd in (1, 2):                   # at most two rewrite rounds; the best draft (fewest problems) is kept
        if not issues:
            break
        job.log(f"Kịch bản chưa đạt ({len(issues)} lỗi: " + "; ".join(issues[:6]) + f") → AI viết lại (lượt {rnd})", "warn")
        fix_user = (user + "\n\nBẢN NHÁP TRƯỚC:\n" + json.dumps(plan, ensure_ascii=False) + "\n\nCÁC LỖI PHẢI SỬA:\n- "
                    + "\n- ".join(issues) + "\n\nViết lại TOÀN BỘ JSON, sửa hết các lỗi trên, giữ những phần đã đúng.")

        def task2(p, fix_user=fix_user):
            if p.kind != "llm":
                raise BadOutput("cần model ngôn ngữ")
            try:
                return _parse(p.complete(job, system, fix_user, PLAN_SCHEMA, sw))
            except ValueError as e:
                raise BadOutput(str(e))
        new, pid2 = orchestrator.run(job, sw, task2, "viết lại kịch bản", order=_order(s, "content_writer_provider"))
        left = quality_check(new, notes, tl)
        if len(left) <= len(issues):
            plan, pid, issues = new, pid2, left
        job.log(f"Viết lại lượt {rnd} xong" + (": còn " + "; ".join(left[:6]) if left else ": đạt"),
                "warn" if left else "info")
    plan["_issues"] = issues
    hooks = [h for h in plan.get("hooks") or [] if isinstance(h, dict) and (h.get("vo") or h.get("overlay"))]
    if hooks:                            # the default is option 1; the Biên kịch tab can switch to 2 or 3
        plan["hook"] = dict(plan.get("hook") or {}, vo=hooks[0].get("vo", ""), overlay=hooks[0].get("overlay", ""))
        plan["hook_choice"] = 0
    vtype = forced or (plan.get("video_type") if plan.get("video_type") in VIDEO_TYPES else rule)
    plan["video_type"] = vtype
    plan["video_type_why"] = plan.get("video_type_why") or tl.get("video_type_rule_why", "")
    plan, problems = validate(plan, tl["duration"], {b["id"]: b["start"] for b in tl["small_text"]}, tl["silences"], tl["lines"])
    for p in problems:
        job.log(f"Kịch bản: {p}", "warn")
    plan["_meta"] = {"provider": pid, "persona": persona, "tone": tone}
    return plan


NOW = re.compile(r"\b(giờ|bây giờ|hôm nay|hiện tại|giờ đây)\b", re.I)


def _near_era(notes, t):
    fr = notes.get("frames") or []
    return min(fr, key=lambda f: abs(float(f.get("t", 0)) - t)).get("era") if fr else None


def _near_desc(notes, t):
    fr = notes.get("frames") or []
    if not fr:
        return ""
    x = min(fr, key=lambda f: abs(float(f.get("t", 0)) - t))
    return f"{x.get('desc', '')} {x.get('text_on_screen', '')}".strip()


def scene_table(tl, notes):
    """What is on screen inside each silence (frame notes in that span) — the narration placed there must match."""
    out = []
    for a, b in tl["silences"]:
        fr = [f for f in notes.get("frames") or [] if a - 0.5 <= float(f.get("t", 0)) <= b + 0.5]
        desc = " | ".join(f"{float(f['t']):.1f}s ({ERA.get(f.get('era'), '?')}): {f.get('desc', '')}" for f in fr) \
            or _near_desc(notes, (a + b) / 2)
        out.append(f"{a:.1f}–{b:.1f}s: {desc}")
    return "\n".join(out) or "(không)"


HER_FORMS = ("cô ấy", "em", "nàng")
ERA = {"hien_tai": "hiện tại", "hoi_uc": "hồi ức", "khong_ro": "?"}
PAST = re.compile(r"\b(ngày ấy|ngày đó|năm ấy|năm đó|hồi ấy|hồi đó|thuở|ngày xưa|trước kia|\w+ năm trước)\b", re.I)
PRESENT_CUE = re.compile(r"\b(20[2-3]\d|hiện tại|bây giờ|hôm nay|hiện nay)\b", re.I)
ABSTRACT = re.compile(r"(như một lời|mắc nợ|giữ thể diện|thấy mình|điều tôi sợ|hóa ra|cứ tưởng|tưởng rằng|trong lòng|"
                      r"cảm giác|đảm bảo|tự ti|thương hại|xứng đáng|bình yên|day dứt|nuối tiếc|hạnh phúc thật sự)", re.I)
FEEL = re.compile(r"(nhớ|thèm|ước|giá như|muốn|tiếc|thương|đau)", re.I)
LINK = re.compile(r"\b(cũng|từng|như|giống|ngày xưa|hồi ấy|năm ấy|năm đó)\b", re.I)


def _texts(plan):
    h, e = plan.get("hook") or {}, plan.get("ending") or {}
    out = [("hook", h.get("vo") or ""), ("hook", h.get("overlay") or "")]
    out += [("narration", n.get("text") or "") for n in plan.get("narration") or []]
    out += [("twist", v) for r in plan.get("replace_screentext") or [] for v in r.get("vo") or []]
    out += [("ending", e.get("overlay") or ""), ("loop", e.get("loop_vo") or "")]
    return [(k, t) for k, t in out if t]


def _words(t):
    return set(re.findall(r"\w+", t.lower()))


def quality_check(plan, notes, tl):
    """Problems a reviewer would send back: mixed forms of address, narration contradicting the picture, abstract
    feeling-sentences in a sparse tone, a loop line that only describes the picture, a hook that only summarises."""
    out = []
    per = plan.get("persona") or {}
    her = str(per.get("her") or "").strip().lower()
    tx = _texts(plan)
    if her in HER_FORMS:
        others = [f for f in HER_FORMS if f != her]
        for k, t in tx:
            low = " " + t.lower() + " "
            bad = [f for f in others if re.search(r"(?<!\w)" + re.escape(f) + r"(?!\w)", low)
                   and not (f == "em" and her == "cô ấy" and re.search(r"(?<!\w)em (bé|trai|gái|út)\b", low))]
            if bad:
                out.append(f"xưng hô: đã chọn gọi nhân vật nữ là “{her}” nhưng câu {k} “{t}” dùng “{bad[0]}”")
    elif tx:
        out.append("persona.her trống: phải chọn MỘT cách gọi nhân vật nữ (“cô ấy” hoặc “em”)")
    for n in plan.get("narration") or []:
        t0 = float(n.get("start") or 0)
        d, era = _near_desc(notes, t0), _near_era(notes, t0)
        if PAST.search(n.get("text") or "") and (era == "hien_tai" or PRESENT_CUE.search(d)):
            out.append(f"lời kể {t0:.1f}s “{n['text']}” nói về quá khứ nhưng hình lúc đó là HIỆN TẠI ({d[:80]})")
        if NOW.search(n.get("text") or "") and era == "hoi_uc":
            out.append(f"lời kể {t0:.1f}s “{n['text']}” nói về hiện tại nhưng hình lúc đó là HỒI ỨC ({d[:80]})")
    tone = str(per.get("tone") or (plan.get("_meta") or {}).get("tone") or "").lower()
    if "tram" in tone or "trầm" in tone or not tone:
        for n in plan.get("narration") or []:
            if ABSTRACT.search(n.get("text") or ""):
                out.append(f"lời kể “{n['text']}” giải thích cảm xúc trừu tượng — đổi thành sự kiện/chi tiết cụ thể")
    loop = ((plan.get("ending") or {}).get("loop_vo") or "").strip()
    if loop:
        opening = _near_desc(notes, 1.0)
        overlap = len(_words(loop) & _words(opening)) / max(1, len(_words(loop)))
        me = str(per.get("me") or "tôi").lower()
        felt = LINK.search(loop) or FEEL.search(loop) or re.search(r"(?<!\w)" + re.escape(me) + r"(?!\w)", loop.lower())
        if not felt or overlap > 0.6:
            out.append(f"câu loop “{loop}” chỉ tả lại hình — phải mang cảm xúc và nối nghĩa về cảnh mở")
    h = plan.get("hook") or {}
    ov, vo = (h.get("overlay") or "").strip(), (h.get("vo") or "").strip()
    gap = re.compile(r"(…|\.\.\.|\?|là ai|năm trước|ngày trước|trước đó|là cô ấy|là em|chưa ai biết)", re.I)
    contrast = re.compile(r"(nhưng|mà|không còn|chẳng còn|vẫn|giờ)", re.I)
    if vo and not gap.search(vo):
        out.append(f"hook.vo “{vo}” chỉ kể/tóm tắt — phải úp mở (bỏ lửng “…”, câu hỏi, mốc “… năm trước”) để người xem muốn biết tiếp")
    if ov and not contrast.search(ov):
        out.append(f"hook.overlay “{ov}” thiếu tương phản (giờ… nhưng…, có… mà không còn…)")
    out += quality_check_2(plan, notes, tl)
    return out


STOP = {"tôi", "cô", "ấy", "em", "anh", "là", "của", "đã", "và", "có", "không", "một", "này", "đó", "thì", "mà", "rồi",
        "vẫn", "cũng", "với", "cho", "được", "những", "các", "lại", "đi", "ra", "vào", "khi", "nhưng", "người", "ta"}
DATE = re.compile(r"(\d|tháng\s+(\d+|một|hai|ba|tư|năm|sáu|bảy|tám|chín|mười)|năm\s+(hai|19|20)|giờ\s+\d|thứ\s+(hai|ba|tư|năm|sáu|bảy)|chủ nhật)", re.I)
SPOILER = re.compile(r"(sau khi|hóa ra|hoá ra|cuối cùng|lấy chồng|lấy vợ|kết hôn|đám cưới|cưới|chia tay|qua đời|"
                     r"phản bội|sự thật|bỏ đi|ra đi|đã mất|hối hận)", re.I)


def narration_cap(duration):
    """At most ~2 narration lines a minute (a 4-minute film ≈ 8)."""
    return max(3, int(round(float(duration) / 60.0 * 2)))


def _content_words(t):
    return {w for w in re.findall(r"\w+", (t or "").lower()) if w not in STOP and len(w) > 1}


def quality_check_2(plan, notes, tl):
    """Round-2 review rules: no narration that reads the on-screen text again (dates, messages), no two lines
    opening the same way, at most ~2 lines a minute, no line that only restates the picture, a title bar that does
    not give the twist away, a sharp hook text (≤ 10 words) and three hook options."""
    out = []
    nar = [n for n in plan.get("narration") or [] if (n.get("text") or "").strip()]
    screen = tl.get("screen") or []
    frames = notes.get("frames") or []
    for n in nar:
        t0, txt = float(n.get("start") or 0), n["text"]
        near = [e.get("tgt") or e.get("src") or "" for e in screen if e["start"] - 6 <= t0 <= e["end"] + 6]
        near += [f.get("text_on_screen") or "" for f in frames if abs(float(f.get("t", 0)) - t0) <= 6]
        near = [x for x in near if x.strip()]
        if DATE.search(txt) and (any(re.search(r"\d", x) for x in near) or re.search(r"\d", txt)):
            out.append(f"lời kể {t0:.1f}s “{txt}” đọc lại ngày/giờ/con số đang hiện trên màn hình — bỏ hoặc thay bằng điều hình không nói được")
            continue
        w = _content_words(txt)
        for x in near:
            hit = w & _content_words(x)
            if len(hit) >= 3 or (len(w) and len(hit) / len(w) >= 0.6 and len(hit) >= 2):
                out.append(f"lời kể {t0:.1f}s “{txt}” nhắc lại chữ trên màn hình (“{x[:50]}”) — người xem đã đọc được")
                break
        else:
            d = _near_desc(notes, t0)
            hit = w & _content_words(d)
            if len(w) >= 3 and len(hit) / len(w) >= 0.6:
                out.append(f"lời kể {t0:.1f}s “{txt}” chỉ thuật lại hình ({d[:60]}) — bỏ, hoặc nói điều hình không nói được")
    heads = {}
    for n in nar:
        ws = re.findall(r"\w+", n["text"].lower())[:2]
        if len(ws) == 2:
            heads.setdefault(" ".join(ws), []).append(n["text"])
    for h, xs in heads.items():
        if len(xs) > 1:
            out.append(f"{len(xs)} câu kể cùng mở đầu “{h}…” ({' / '.join(x[:30] for x in xs)}) — tối đa 1 câu")
    cap = narration_cap(tl.get("duration") or 0)
    if len(nar) > cap:
        out.append(f"{len(nar)} câu kể là quá nhiều cho phim {float(tl.get('duration') or 0) / 60:.0f} phút — tối đa {cap}, chỉ giữ câu đắt nhất")
    title = (plan.get("title_bar") or "").strip()
    if title:
        twist = " ".join([v for r in plan.get("replace_screentext") or [] for v in r.get("vo") or []]
                         + [(plan.get("ending") or {}).get("overlay") or ""])
        shared = _content_words(title) & _content_words(twist)
        if SPOILER.search(title) or len(shared) >= 2:
            out.append(f"title_bar “{title}” lộ twist/cái kết (hiện suốt video) — đổi thành chủ đề úp mở")
    h = plan.get("hook") or {}
    seen = set()
    for i, x in enumerate([h] + list(plan.get("hooks") or [])):
        ov = (x.get("overlay") or "").strip() if isinstance(x, dict) else ""
        nw = len(re.findall(r"\w+", ov))
        if ov and nw > 10 and ov not in seen:
            seen.add(ov)
            out.append(f"{'hook.overlay' if i == 0 else f'phương án hook {i}'} “{ov}” dài {nw} chữ — tối đa 10, sắc hơn")
    hs = [x for x in plan.get("hooks") or [] if isinstance(x, dict) and (x.get("overlay") or x.get("vo"))]
    if len(hs) < 3 and (plan.get("video_type") or "story_sparse") != "short_clip" or len(hs) < 1:
        out.append(f"'hooks' có {len(hs)} phương án — cần đúng 3 phương án hook khác nhau")
    return out


# ------------------------------------------------------------------ 4. validate + time remap
BANNED_TAGS = {"#fyp", "#foryou", "#foryoupage", "#xuhuong", "#viral", "#trending"}


def _num(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ 4a. speech-safe cuts (real audio energy)
CUT_GAP = 0.5          # (review asks ≥ 0.4) a cut edge is at least this far from any speech (original voice, dubbed voice, narration)
XFADE_S = 0.12         # picture + sound crossfade at a join that is not a shot change
VOCALS_DB = -40.0      # vocals stem: speech, breaths and word tails sit above this; separation bleed below
VOICE_DB = -50.0       # dubbed voice track: digital silence between clips


def _merge(spans, bridge=0.0):
    out = []
    for a, b in sorted((float(a), float(b)) for a, b in spans if b > a):
        if out and a <= out[-1][1] + bridge:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def energy_spans(path: Path, thr_db, hop=0.02, bridge=0.3, min_len=0.06):
    """Spans [(a, b)] s where the RMS level of `path` is above `thr_db` dBFS (20 ms frames; gaps < `bridge` joined,
    blips shorter than `min_len` dropped)."""
    import numpy as np
    sr = 16000
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                       capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    x = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0
    n = int(sr * hop)
    if len(x) < n:
        return []
    x = x[: len(x) // n * n].reshape(-1, n)
    on = 20 * np.log10(np.sqrt((x ** 2).mean(1)) + 1e-9) > thr_db
    spans, start = [], None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        elif not v and start is not None:
            spans.append((start * hop, i * hop))
            start = None
    if start is not None:
        spans.append((start * hop, len(on) * hop))
    return [(a, b) for a, b in _merge(spans, bridge) if b - a >= min_len]


def speech_spans(d: Path, extra=()):
    """Every span with a voice in it, original timeline: the vocals stem (energy), the dubbed Vietnamese track
    (energy — its real length, not the transcript's), the transcript lines, and `extra` (placed narration)."""
    sp = list(extra)
    for name, thr in (("vocals.wav", VOCALS_DB), ("voice.wav", VOICE_DB)):
        if (d / name).exists():
            sp += energy_spans(d / name, thr)
    for f in ("lines.json", "asr.json"):
        if (d / f).exists():
            try:
                js = json.loads((d / f).read_text(encoding="utf-8"))
            except ValueError:
                continue
            ls = js.get("lines", []) if isinstance(js, dict) else js
            sp += [(float(l["start"]), float(l["end"])) for l in ls if l.get("end") is not None
                   and not l.get("hook") and l.get("role") != "content_vo"]
    return _merge(sp)


def free_gaps(speech, total):
    out, cur = [], 0.0
    for a, b in _merge(speech):
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if total > cur:
        out.append((cur, total))
    return out


def nearest_speech(t, speech):
    """Distance (s) from time t to the closest speech span (0 inside one)."""
    best = 1e9
    for a, b in speech:
        best = min(best, 0.0 if a <= t <= b else min(abs(t - a), abs(t - b)))
    return best


def safe_cuts(cuts, speech, scene_starts, total, fps, gap=CUT_GAP, min_len=0.5, protect=0.0):
    """The plan's cuts made safe on the REAL audio: every edge ≥ `gap` s from any speech, on the frame grid, moved
    onto shot changes where one is close (a join on a shot change looks like a normal edit). A join that stays
    inside one shot gets a short crossfade (`xfade`). Nothing before `protect` (the hook) is cut.
    Returns [{from, to, want_from, want_to, xfade, scene_edges}] sorted, non-overlapping."""
    import math
    grid = lambda t: round(t * fps) / fps
    up = lambda t: math.ceil(t * fps - 1e-6) / fps
    dn = lambda t: math.floor(t * fps + 1e-6) / fps
    sc = sorted({grid(float(x)) for x in scene_starts if 0 < float(x) < total})
    gaps = [(g0 + gap if g0 > 0 else g0, g1 - gap if g1 < total else g1) for g0, g1 in free_gaps(speech, total)]
    out = []
    for c in cuts:
        a, b = max(float(c[0]), protect), float(c[1])
        for r0, r1 in gaps:
            r0 = max(r0, protect)
            ac, bc = max(a, r0), min(b, r1)
            if bc - ac < min_len:
                continue
            want = bc - ac
            S = [x for x in sc if max(r0, ac - 1.0) <= x <= min(r1, bc + 1.0)]
            opts = [(ac, bc)] + [(x, bc) for x in S if x < bc] + [(ac, x) for x in S if x > ac] \
                + [(x, y) for x in S for y in S if y > x]
            best, bscore = None, -1e9
            for x, y in opts:
                if y - x < max(min_len, 0.4 * want):
                    continue
                inside = [z for z in S if x - 0.5 / fps <= z <= y + 0.5 / fps]
                if not inside:
                    continue                      # same shot on both sides: not a clean edit
                on = sum(any(abs(z - e) < 0.5 / fps for z in S) for e in (x, y))
                ov = max(0.0, min(y, bc) - max(x, ac))
                score = ov - 0.5 * ((y - x) - ov) + 0.3 * on
                if score > bscore:
                    best, bscore = (x, y, on), score
            if best:
                x, y, on = best
                x = grid(x) if any(abs(z - x) < 0.5 / fps for z in S) else up(x)
                y = grid(y) if any(abs(z - y) < 0.5 / fps for z in S) else dn(y)
                xf = False
            else:
                x, y, on, xf = up(ac), dn(bc), 0, True
            if y - x >= min_len:
                out.append({"from": round(x, 6), "to": round(y, 6), "want_from": round(float(c[0]), 2),
                            "want_to": round(float(c[1]), 2), "xfade": xf, "scene_edges": on})
    out.sort(key=lambda z: z["from"])
    merged = []
    for z in out:
        if merged and z["from"] <= merged[-1]["to"] + 1.0 / fps:
            merged[-1]["to"] = max(merged[-1]["to"], z["to"])
            merged[-1]["xfade"] = merged[-1]["xfade"] and z["xfade"]
        else:
            merged.append(z)
    return merged


def fit_reads(reads, speech, end_t, pad=0.3, slack=3.0):
    """Place each voice line [(start, dur, ...)] where it overlaps no speech (± `pad`) and no previous line:
    at its wanted start if free, else the nearest free slot within `slack` s; a line that fits nowhere is dropped.
    Returns ([(a, b, idx)], [dropped idx])."""
    placed, dropped, cur = [], [], 0.0
    free = free_gaps(speech, end_t)
    for i, (a, dur) in enumerate(reads):
        lo = max(a, cur + pad) if placed else a
        best = None
        for g0, g1 in free:
            s0 = max(g0 + pad if g0 > 0 else g0, cur + pad if placed else 0.0)
            s1 = (g1 - pad if g1 < end_t else g1) - dur
            if s1 < s0:
                continue
            t = min(max(lo, s0), s1)
            # measured from where the line can start at the earliest (its time, or right after the previous
            # line): a block read as several lines follows on, it is not pulled back to the block's time
            if abs(t - lo) <= slack and (best is None or abs(t - lo) < abs(best - lo)):
                best = t
        if best is None:
            dropped.append(i)
            continue
        placed.append((best, best + dur, i))
        cur = best + dur
    return placed, dropped


def _inside_silences(a, b, sil, margin=CUT_GAP):
    """Parts of [a, b] that lie inside the silences (shrunk by `margin` so a cut never clips a word)."""
    out = []
    for s0, s1 in sil:
        x0, x1 = max(a, s0 + margin), min(b, s1 - margin)
        if x1 - x0 >= 0.5:
            out.append((x0, x1))
    return out


def validate(plan, total, small_ids=(), silences_=(), lines=()):
    # small_ids: ids of the small-text blocks, or {id: start time} (then reading a block counts as narration)
    """Normalise an AI (or hand-edited) plan: every section present, times inside the video, cuts merged, only
    inside silences (never on dialogue, never on the hook), never most of the video, narration sorted and off the
    dialogue, banned hashtags removed, the video type's scope enforced. Returns (plan, [problems])."""
    probs = []
    p = dict(plan or {})
    vtype = p.get("video_type") if p.get("video_type") in VIDEO_TYPES else "story_sparse"
    p["video_type"] = vtype
    p.setdefault("persona", {})
    hook = dict(p.get("hook") or {})
    hook["start"] = max(0.0, _num(hook.get("start"), 0.0))
    hook["end"] = min(max(hook["start"] + 1.0, _num(hook.get("end"), 3.0)), hook["start"] + 3.0)
    for k in ("vo", "overlay"):
        hook[k] = str(hook.get(k) or "").strip()
    hooks = []
    for x in p.get("hooks") or []:
        if isinstance(x, dict):
            vo, ov = str(x.get("vo") or "").strip(), str(x.get("overlay") or "").strip()
            if (vo or ov) and not any(h["vo"] == vo and h["overlay"] == ov for h in hooks):
                hooks.append({"vo": vo, "overlay": ov, "angle": str(x.get("angle") or "").strip()})
    hooks = hooks[:3]
    if (hook["vo"] or hook["overlay"]) and not any(h["vo"] == hook["vo"] and h["overlay"] == hook["overlay"] for h in hooks):
        hooks = ([{"vo": hook["vo"], "overlay": hook["overlay"], "angle": "đang dùng"}] + hooks)[:3]
    p["hooks"] = hooks
    ch = [i for i, h in enumerate(hooks) if h["vo"] == hook["vo"] and h["overlay"] == hook["overlay"]]
    p["hook_choice"] = ch[0] if ch else 0
    if not hook["vo"] and not hook["overlay"]:
        probs.append("thiếu hook")
    p["hook"] = hook
    p["title_bar"] = str(p.get("title_bar") or "").strip()

    nar = []
    for n in p.get("narration") or []:
        a = _num(n.get("start"))
        t = str(n.get("text") or "").strip()
        if a is None or not t or not (0 <= a < total):
            continue
        b = _num(n.get("end"), a + 3.0)
        nar.append({"start": round(a, 2), "end": round(min(max(b, a + 0.5), total), 2), "text": t,
                    "duck_original": min(1.0, max(0.0, _num(n.get("duck_original"), 0.3))),
                    "enabled": n.get("enabled", True) is not False})
    nar = sorted(nar, key=lambda n: n["start"])
    if lines:                       # narration never talks over the dialogue: drop a line that lands on speech
        keep_n = []
        for n in nar:
            over = sum(max(0.0, min(n["end"], float(l["end"])) - max(n["start"], float(l["start"]))) for l in lines)
            if over > 0.4 * (n["end"] - n["start"]):
                probs.append(f"câu kể {n['start']:.1f}s đè lên thoại → bỏ")
                continue
            keep_n.append(n)
        nar = keep_n
    if vtype in ("narrated", "short_clip"):
        nar = []
    p["narration"] = nar

    cuts = []
    for c in p.get("cuts") or []:
        a, b = _num(c.get("from")), _num(c.get("to"))
        if a is None or b is None or b - a < 0.3:
            continue
        a, b = max(0.0, a), min(total, b)
        a = max(a, hook["end"])                         # the hook plays over the opening scene
        parts = _inside_silences(a, b, silences_) if silences_ else ([(a, b)] if b - a >= 0.5 else [])
        if silences_ and sum(y - x for x, y in parts) < (b - a) - 0.3:
            probs.append(f"chỗ cắt {a:.1f}–{b:.1f}s lấn vào thoại → chỉ giữ phần trong khoảng lặng")
        for x0, x1 in parts:
            cuts.append({"from": round(x0, 2), "to": round(x1, 2), "reason": str(c.get("reason") or ""),
                         "enabled": c.get("enabled", True) is not False})
    cuts.sort(key=lambda c: c["from"])
    merged = []
    for c in cuts:
        if merged and c["from"] <= merged[-1]["to"]:
            merged[-1]["to"] = max(merged[-1]["to"], c["to"])
        else:
            merged.append(c)
    if sum(c["to"] - c["from"] for c in merged if c["enabled"]) > total * 0.6:
        probs.append("chỗ cắt bỏ quá 60% video → bỏ hết chỗ cắt")
        merged = []
    p["cuts"] = [] if vtype == "short_clip" else merged

    p["retention_texts"] = [{"at": round(_num(r.get("at")), 2), "text": str(r.get("text") or "").strip(),
                             "enabled": r.get("enabled", True) is not False}
                            for r in p.get("retention_texts") or [] if _num(r.get("at")) is not None
                            and 0 <= _num(r.get("at")) < total and str(r.get("text") or "").strip()]
    rep = []
    for r in p.get("replace_screentext") or []:
        try:
            i = int(r.get("id"))
        except (TypeError, ValueError):
            continue
        if small_ids and i not in small_ids:
            probs.append(f"khối chữ #{i} không có trong danh sách chữ nhỏ → bỏ")
            continue
        vo = [str(x).strip() for x in (r.get("vo") or []) if str(x).strip()]
        if vo:
            rep.append({"id": i, "vo": vo, "big_subs": r.get("big_subs", True) is not False,
                        "enabled": r.get("enabled", True) is not False})
    p["replace_screentext"] = [] if vtype in ("narrated", "short_clip") else rep
    # a sentence is read once: the twist (read with its text block) and the loop line (read on the ending card)
    # are not narration lines too
    norm = lambda x: re.sub(r"\W+", "", x.lower())
    spoken = {norm(v) for r in p["replace_screentext"] if r["enabled"] for v in r["vo"]}
    if vtype == "short_clip":
        p["retention_texts"], p["teasers"] = [], []
    end = dict(p.get("ending") or {})
    p["ending"] = {"overlay": str(end.get("overlay") or "").strip(), "loop_vo": str(end.get("loop_vo") or "").strip(),
                   "enabled": end.get("enabled", True) is not False}
    if p["ending"]["enabled"] and p["ending"]["loop_vo"]:
        spoken.add(norm(p["ending"]["loop_vo"]))
    dup = [n for n in p["narration"] if norm(n["text"]) in spoken]
    for r in p["replace_screentext"]:          # the block is read from the earliest place the AI wanted it
        early = [n["start"] for n in dup if norm(n["text"]) in {norm(v) for v in r["vo"]}]
        if early:
            r["read_at"] = min(early + ([r["read_at"]] if r.get("read_at") is not None else []))
    if dup:
        probs.append(f"{len(dup)} câu kể trùng câu đọc khối chữ / câu loop → chỉ đọc một lần")
        p["narration"] = [n for n in p["narration"] if norm(n["text"]) not in spoken]
    cov = dict(p.get("cover") or {})
    at = _num(cov.get("at"), min(3.0, total / 2))
    p["cover"] = {"at": round(min(max(0.0, at), max(0.0, total - 0.1)), 2), "text": str(cov.get("text") or "").strip()}
    post = dict(p.get("post") or {})
    tags = []
    for h in post.get("hashtags") or []:
        h = "#" + re.sub(r"[^\w]", "", str(h).lstrip("#"), flags=re.U)
        if len(h) > 1 and h.lower() not in BANNED_TAGS and h not in tags:
            tags.append(h)
    cap = str(post.get("caption") or "").strip()
    for b in BANNED_TAGS:
        cap = re.sub(re.escape(b) + r"\b", "", cap, flags=re.I).strip()
    p["post"] = {"caption": cap, "hashtags": tags, "pinned_comment": str(post.get("pinned_comment") or "").strip(),
                 "ai_label": bool(post.get("ai_label", True)), "credit": str(post.get("credit") or "").strip()}
    tz = []
    for t in p.get("teasers") or []:
        a, b = _num(t.get("from")), _num(t.get("to"))
        if a is not None and b is not None and 0 <= a < b <= total + 0.5 and b - a >= 5:
            tz.append({"from": round(a, 2), "to": round(min(b, total), 2), "overlay": str(t.get("overlay") or "").strip(),
                       "end_card": str(t.get("end_card") or "Bản đầy đủ trên kênh 👆").strip()})
    p["teasers"] = [] if vtype == "short_clip" else tz[:3]
    rw = []
    if vtype == "narrated":
        starts = [float(l["start"]) for l in lines or ()]
        for x in p.get("rewrite") or []:
            a, txt = _num(x.get("start")), str(x.get("text") or "").strip()
            if a is None or not txt:
                continue
            near = min(starts, key=lambda y: abs(y - a)) if starts else a
            if not starts or abs(near - a) <= 0.35:
                rw.append({"start": round(near, 2), "text": txt, "enabled": x.get("enabled", True) is not False})
    p["rewrite"] = rw
    # every long silence must be narrated or cut
    for a, b in (silences_ or ()) if vtype in ("story_sparse",) else ():
        if b - a <= 5:
            continue
        cut = sum(max(0.0, min(b, c["to"]) - max(a, c["from"])) for c in p["cuts"] if c["enabled"])
        told = any(a - 0.5 <= n["start"] <= b for n in p["narration"]) or (a <= hook["start"] < b)             or any(r["enabled"] and isinstance(small_ids, dict) and r["id"] in small_ids
                   and a - 1 <= small_ids[r["id"]] <= b for r in p["replace_screentext"])
        if not told and (b - a) - cut > 5:
            probs.append(f"khoảng lặng {a:.0f}–{b:.0f}s còn {(b - a) - cut:.0f}s không lời kể, không cắt")
    return p, probs


class CutMap:
    """Original time ↔ time after removing `cuts` (list of {from, to[, enabled]})."""

    def __init__(self, cuts, total):
        self.total = float(total)
        cs = sorted((float(c["from"]), float(c["to"])) for c in cuts or () if c.get("enabled", True))
        self.cuts = []
        for a, b in cs:
            a, b = max(0.0, a), min(self.total, b)
            if b <= a:
                continue
            if self.cuts and a <= self.cuts[-1][1]:
                self.cuts[-1] = (self.cuts[-1][0], max(self.cuts[-1][1], b))
            else:
                self.cuts.append((a, b))

    @property
    def removed(self):
        return sum(b - a for a, b in self.cuts)

    @property
    def new_total(self):
        return self.total - self.removed

    def keep(self):
        """Kept spans of the original timeline."""
        out, cur = [], 0.0
        for a, b in self.cuts:
            if a > cur:
                out.append((cur, a))
            cur = b
        if cur < self.total:
            out.append((cur, self.total))
        return out

    def t(self, x):
        """Original → new time. A time inside a cut lands where the cut is."""
        shift = 0.0
        for a, b in self.cuts:
            if x >= b:
                shift += b - a
            elif x > a:
                return a - shift
        return x - shift

    def span(self, a, b):
        """Original span → new span (None if it is cut away entirely)."""
        na, nb = self.t(a), self.t(b)
        return (na, nb) if nb - na > 0.05 else None

    def back(self, y):
        """New → original time."""
        x = y
        for a, b in self.cuts:
            if x >= a:
                x += b - a
        return x


def load(d: Path):
    p = d / FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def apply_rewrite(lines, plan):
    """Put the plan's rewritten narration into lines.json (narrated videos). Returns the number of lines changed;
    the AI's own text stays in tgt_ai so the rewrite can be undone."""
    n = 0
    for x in plan.get("rewrite") or []:
        if not x.get("enabled", True):
            continue
        for l in lines:
            if abs(float(l["start"]) - float(x["start"])) <= 0.05 and l.get("tgt") != x["text"]:
                l.setdefault("tgt_ai", l.get("tgt"))
                l["tgt"] = x["text"]
                l["content_rewrite"] = True
                n += 1
    return n


def translation_voice(plan):
    """Extra translation instructions so the dialogue sounds like the narration (persona / tone / forms of address).
    Empty when there is no plan — the translation is then exactly as before."""
    if not plan:
        return ""
    per = plan.get("persona") or {}
    bits = []
    if per.get("tone"):
        bits.append(f"giọng văn: {per['tone']}")
    if per.get("me"):
        bits.append(f"người kể xưng “{per['me']}”")
    if per.get("her"):
        bits.append(f"gọi nhân vật nữ là “{per['her']}”")
    if per.get("him"):
        bits.append(f"gọi nhân vật nam là “{per['him']}”")
    if not bits:
        return ""
    extra = ("Lời dịch cùng giọng với lời kể của kênh: " + "; ".join(bits) + ".")
    if plan.get("video_type") == "narrated":
        extra += (" Lời THUYẾT MINH (người nói nhiều nhất) viết lại theo ngôi kể và tone này, giữ nguyên ý và độ dài "
                  "để đọc kịp đúng thời gian câu gốc.")
    return extra


def save(d: Path, plan):
    (d / FILE).write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")





# ------------------------------------------------------------------ 5. render parts
class Parts:
    """What the render adds for a plan: the narration track, ASS rows (main picture and the 9:16 title bar),
    ducking windows, the ending length, cuts, and the on-screen text blocks that are read instead of drawn."""
    vo = None              # Path of content_vo.wav (original timeline)
    duck = ()              # [(a, b, level)]
    items = ()             # [(kind, a, b, text)] overlays, ORIGINAL timeline — rows() maps them through the cuts
    ending_s = 0.0         # seconds of ending card after the picture ends
    cuts = ()              # [(a, b)] enabled cuts
    replaced = frozenset()  # screentext ids read aloud (their translation is not drawn)
    hook_line = False      # drop the translate step's hook line
    speech = ()            # [(a, b)] voiced spans of the original timeline (energy + transcript)
    cut_info = ()          # safe_cuts() result (also content_cuts.json)
    xfade = ()             # per cut: crossfade the join (not a shot change)


def _voice(s):
    from . import vieneu_tts
    if (s.get("content_voice_provider") or "vieneu") == "vieneu" and vieneu_tts.installed():
        return "vieneu", s.get("content_voice") or "Thiền Tâm Đức"
    return "edge", s.get("content_voice_edge") or "vi-VN-NamMinhNeural"


def synth_vo(job, d: Path, s, texts):
    """{text: (wav path, seconds)} in the channel's narrator voice. Clips are cached by provider + voice + text,
    so editing one narration line re-reads only that line."""
    import asyncio
    import hashlib
    from . import tts
    prov, voice = _voice(s)
    sv = dict(s, tts_provider=prov, edge_rate="+0%", edge_pitch="+0Hz")
    cache = d / "tts" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    out_dir = d / "content_vo"
    out_dir.mkdir(exist_ok=True)
    texts = list(dict.fromkeys(texts))
    todo = [(t, cache / tts._cache_name(t, sv, prov, voice)) for t in texts]
    todo = [(t, raw) for t, raw in todo if not raw.exists()]
    if todo:
        job.log(f"Lời kể: đọc {len(todo)} câu bằng {prov} ({voice})")
        if prov == "vieneu":
            from . import vieneu_tts
            vieneu_tts.synth([(t, voice, raw) for t, raw in todo], cancel=job.check_cancel)
        else:
            for t, raw in todo:
                asyncio.run(tts._edge_one(t, raw, sv, voice, job.log))
    res = {}
    for t in texts:
        raw = cache / tts._cache_name(t, sv, prov, voice)
        wav = out_dir / (hashlib.sha1(raw.name.encode()).hexdigest()[:12] + ".wav")
        if not wav.exists():
            util.run(job, ["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-af",
                           "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
                           "silenceremove=start_periods=1:start_threshold=-45dB,areverse",
                           "-ar", "48000", "-ac", "1", str(wav)])
        res[t] = (wav, util.duration(wav))
    return res


def _card(text, a, b, W, H, fs, cy, fg="#FFFFFF", bg="#000000", alpha=0.45, layer=5, maxw=0.82, bold=800, an=5, cx=None):
    """Text on a rounded card (alpha < 1) or plain outlined text (alpha ≥ 1): rows for an ASS file."""
    from .render import _ass, _ass_color, _measure_font, _wrap, rounded_rect
    font = _measure_font("Be Vietnam Pro", fs, bold)
    clean = text.replace("{", "(").replace("}", ")")
    lines = _wrap(clean, font, W * maxw) or [clean]
    lh = fs * 1.22
    bw = max(font.getlength(x) for x in lines) + fs * 1.1
    bh = len(lines) * lh + fs * 0.6
    cx = W / 2 if cx is None else cx
    x0 = cx - bw / 2 if an == 5 else (cx if an == 4 else cx - bw)
    y0 = cy - bh / 2

    def col(h):
        return "&H" + _ass_color(h)[4:] + "&"
    ta, tb = _ass(a), _ass(b)
    rows = []
    if alpha < 1:
        rows.append(f"Dialogue: {layer},{ta},{tb},Default,,0,0,0,,{{\\an7\\pos(0,0)\\bord0\\shad0\\1c{col(bg)}"
                    f"\\1a&H{int(round(alpha * 255)):02X}&\\fad(150,150)\\p1}}{rounded_rect(x0, y0, x0 + bw, y0 + bh, fs * 0.45)}")
    edge = "\\bord0\\shad0" if alpha < 1 else f"\\bord{max(2, fs * 0.08):.1f}\\3c&H000000&\\shad{fs * 0.04:.1f}"
    rows.append(f"Dialogue: {layer + 1},{ta},{tb},Default,,0,0,0,,{{\\an5\\pos({x0 + bw / 2:.0f},{cy:.0f})\\fnBe Vietnam Pro"
                f"\\b{bold}\\fs{fs:.0f}{edge}\\1c{col(fg)}\\q2\\fad(150,150)}}" + "\\N".join(lines))
    return rows


def render_parts(job, d: Path, s, W, H, end_t, vertical):
    """Everything the render needs from content.json (None when the step is off or has no plan)."""
    plan = load(d) if s.get("content_enabled") else None
    if not plan:
        return None
    P = Parts()
    P.hook_line = True
    P.vertical = vertical
    hook, end = plan.get("hook") or {}, plan.get("ending") or {}
    blocks = {}
    st = d / "screentext.json"
    if st.exists():
        ev = json.loads(st.read_text(encoding="utf-8"))
        ev = ev["events"] if isinstance(ev, dict) else ev
        blocks = {e["id"]: e for e in ev}
    reads = []                                   # (start, text, duck level, kind) — original timeline
    if hook.get("vo"):
        reads.append((float(hook.get("start") or 0.0), hook["vo"], 0.3, "hook"))
    for n in plan.get("narration") or []:
        if n.get("enabled", True):
            reads.append((float(n["start"]), n["text"], float(n.get("duck_original", 0.3)), "nar"))
    rep_ids = set()
    for r in plan.get("replace_screentext") or []:
        if r.get("enabled", True) and r.get("id") in blocks:
            rep_ids.add(r["id"])
            at = float(r["read_at"]) if r.get("read_at") is not None else float(blocks[r["id"]]["start"])
            for i, v in enumerate(r["vo"]):
                reads.append((min(at, float(blocks[r["id"]]["start"])) + i * 0.01, v, 0.25,
                              "twist" if r.get("big_subs", True) else "nar"))
    P.replaced = frozenset(rep_ids)
    loop = end.get("loop_vo") if end.get("enabled", True) else ""
    clips = synth_vo(job, d, s, [t for _, t, _, _ in reads] + ([loop] if loop else []))
    # never two voices at once, and never over the dialogue: each line goes where the REAL audio (vocals stem +
    # dubbed track, by energy) is free — at its wanted time, else the nearest free slot ≤ 3 s away, else dropped
    speech = speech_spans(d)
    P.speech = speech
    reads = [r for r in sorted(reads, key=lambda x: x[0]) if r[0] < end_t]
    fit, dropped = fit_reads([(a, clips[t][1]) for a, t, _, _ in reads], speech, end_t)
    for i in dropped:
        job.log(f"Biên kịch: câu “{reads[i][1][:50]}” ({clips[reads[i][1]][1]:.1f}s) không vừa khoảng trống nào "
                f"quanh {reads[i][0]:.1f}s mà không đè thoại → bỏ", "warn")
    placed = []
    for a, b, i in fit:
        _, t, dk, kind = reads[i]
        if abs(a - reads[i][0]) > 0.3:
            job.log(f"Biên kịch: câu “{t[:40]}” dời {reads[i][0]:.1f}s → {a:.1f}s để không đè thoại")
        placed.append((a, b, t, dk, kind))
    (d / "content_reads.json").write_text(json.dumps(
        [{"start": round(a, 3), "end": round(b, 3), "text": t, "kind": kind, "want": round(reads[i][0], 3)}
         for (a, b, t, dk, kind), (_, _, i) in zip(placed, fit)], ensure_ascii=False, indent=1), encoding="utf-8")
    if loop:
        P.ending_s = max(3.0, clips[loop][1] + 1.4)
        placed.append((end_t + 0.6, end_t + 0.6 + clips[loop][1], loop, 0.2, "loop"))
    elif end.get("enabled", True) and end.get("overlay"):
        P.ending_s = 3.0
    if placed:
        vo = d / "content_vo.wav"
        inputs, filt = [], []
        for k, (a, b, t, dk, kind) in enumerate(placed):
            inputs += ["-i", str(clips[t][0])]
            ms = int(a * 1000)
            filt.append(f"[{k}:a]adelay={ms}|{ms}[c{k}]")
        mix = "".join(f"[c{k}]" for k in range(len(placed)))
        length = end_t + P.ending_s
        (d / "content_vo.txt").write_text(";".join(filt) + f";{mix}amix=inputs={len(placed)}:normalize=0,"
                                         f"apad=whole_dur={length:.3f}[out]", encoding="utf-8")
        util.run(job, ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex_script", str(d / "content_vo.txt"),
                       "-map", "[out]", "-t", f"{length:.3f}", "-ar", "48000", str(vo)])
        P.vo = vo
        P.duck = [(a - 0.12, b + 0.2, dk) for a, b, t, dk, kind in placed]
    items = []
    if hook.get("overlay"):
        items.append(("hook", float(hook.get("start") or 0), float(hook.get("end") or 3), hook["overlay"]))
    for a, b, t, dk, kind in placed:
        if kind in ("twist", "nar"):
            items.append((kind, a, b + 0.25, t))
    for r in plan.get("retention_texts") or []:
        if r.get("enabled", True) and r.get("text"):
            items.append(("ret", float(r["at"]), min(float(r["at"]) + 3.5, end_t), r["text"]))
    if P.ending_s and end.get("overlay"):
        items.append(("ending", end_t + 0.15, end_t + P.ending_s, end["overlay"]))
    title = (plan.get("title_bar") or "").strip()
    if title:
        items.append(("title", 0.0, end_t + P.ending_s, title))
    P.items = items
    want = [(float(c["from"]), float(c["to"])) for c in plan.get("cuts") or []
            if c.get("enabled", True) and float(c["to"]) <= end_t]
    P.cut_info = []
    if want:
        from .textclean import _fps
        fps = _fps(d / "source.mp4") or 30.0
        sc = (_vision_file(d) or {}).get("scenes") or []
        voiced = speech + [(a, b) for a, b, t, dk, kind in placed if kind != "loop"]
        hook_end = float(hook.get("end") or 3.0) if (hook.get("overlay") or hook.get("vo")) else 0.0
        P.cut_info = safe_cuts(want, voiced, sc, end_t, fps, protect=hook_end)
        for z in P.cut_info:
            z["gap_before"] = round(nearest_speech(z["from"], voiced), 3)
            z["gap_after"] = round(nearest_speech(z["to"], voiced), 3)
        moved = sum(1 for z in P.cut_info if abs(z["from"] - z["want_from"]) > 0.05 or abs(z["to"] - z["want_to"]) > 0.05)
        job.log(f"Biên kịch: {len(want)} chỗ cắt → {len(P.cut_info)} chỗ an toàn (cách tiếng nói ≥ {CUT_GAP}s; "
                f"{moved} chỗ dời mép, {sum(z['xfade'] for z in P.cut_info)} chỗ chuyển mờ, "
                f"{sum(1 for z in P.cut_info if not z['xfade'])} chỗ đúng chuyển cảnh)")
        (d / "content_cuts.json").write_text(json.dumps(P.cut_info, ensure_ascii=False, indent=1), encoding="utf-8")
    P.cuts = [(z["from"], z["to"]) for z in P.cut_info]
    P.xfade = [z["xfade"] for z in P.cut_info]
    if not plan.get("approved") and not s.get("content_review", True):
        job.log("Biên kịch: render với kịch bản CHƯA ĐƯỢC DUYỆT", "warn")
    return P


# where each overlay goes: on the picture (landscape output / vertical source) or in the bands of the 9:16 copy
LAYOUT = {   # kind: (font size × short side | px, centre y × H | px, card alpha, fg, bg, bold, maxw)
    "main": {"hook": (0.072, 0.42, 0.40, "#FFFFFF", "#000000", 800, 0.82),
             "nar": (0.045, 0.86, 0.08, "#111111", "#FFFFFF", 700, 0.82),
             "twist": (0.058, 0.50, 0.35, "#FFFFFF", "#000000", 800, 0.82),
             "ret": (0.034, 0.09, 0.45, "#FFFFFF", "#000000", 700, 0.45),
             "ending": (0.062, 0.44, 1.00, "#FFFFFF", "#000000", 800, 0.82),
             "title": (0.050, 0.13, 0.35, "#FFFFFF", "#000000", 800, 0.82)},
    # 1080×1920 copy of a landscape video: picture 656–1264 px; TikTok covers the top ~165 px (tabs), the right
    # column and the bottom 20 % (caption) — so the text sits in the free parts of the two bands
    "tt": {"title": (64, 470, 0.40, "#FFFFFF", "#000000", 800, 0.88),
           "ret": (40, 290, 0.45, "#FFFFFF", "#000000", 700, 0.70),
           "hook": (66, 1395, 0.45, "#FFFFFF", "#000000", 800, 0.86),
           "twist": (56, 1395, 0.40, "#FFFFFF", "#000000", 800, 0.86),
           "nar": (44, 1395, 0.08, "#111111", "#FFFFFF", 700, 0.80),
           "ending": (62, 960, 1.00, "#FFFFFF", "#000000", 800, 0.84)},
}


def rows(P, W, H, layout, cm=None):
    """ASS rows of the overlays for one output, times mapped through the cuts (`cm`: CutMap or None)."""
    L = LAYOUT[layout]
    out = []
    for kind, a, b, text in P.items:
        if kind == "title" and layout == "main" and not P.vertical:
            continue                                   # landscape output: the title bar is a 9:16 thing
        if cm is not None:
            sp = cm.span(a, b) if a < cm.total else (a - cm.removed, b - cm.removed)
            if not sp and kind in ("ret", "hook"):          # landed in a cut: show it at the join instead
                sp = (cm.t(a), cm.t(a) + (b - a))
            if not sp:
                continue
            a, b = sp
        fs, y, alpha, fg, bg, bold, maxw = L[kind]
        if layout == "main":
            fs, y = min(W, H) * fs, H * y
        layer = {"title": 14, "ret": 16, "nar": 18, "hook": 20, "twist": 22, "ending": 24}[kind]
        kw = {"an": 4, "cx": W * 0.04} if (kind == "ret" and layout == "main") else {}
        out += _card(text, a, b, W, H, fs, y, fg=fg, bg=bg, alpha=alpha, layer=layer, bold=bold, maxw=maxw, **kw)
    return out


def ass_file(path: Path, W, H, rows):
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding\nStyle: Default,Be Vietnam Pro,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,"
            "100,100,0,0,1,2,0,5,10,10,10,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
            "Effect, Text\n")
    path.write_text(head + "\n".join(rows) + "\n", encoding="utf-8-sig")


def duck_expr(windows):
    """ffmpeg volume expression: `level` inside each window, 1 elsewhere."""
    if not windows:
        return "1"
    return "*".join(f"(1-{1 - lv:.2f}*between(t,{max(0, a):.2f},{b:.2f}))" for a, b, lv in windows)




# ------------------------------------------------------------------ 6. extras: post.json, cover.jpg, teasers
def post_from_plan(d: Path, plan, title=""):
    """post.json (same shape as postgen's) from the plan's caption/hashtags/pinned comment/credit/AI label."""
    p = plan.get("post") or {}
    tags = list(p.get("hashtags") or [])
    cap = p.get("caption") or ""
    tiktok = "\n".join(x for x in (cap, " ".join(tags), p.get("credit") or "") if x)
    post = {"title": plan.get("title_bar") or (plan.get("hook") or {}).get("overlay") or title,
            "description": cap, "hashtags": tags, "original_hashtags": [], "source_title": title, "provider": "content",
            "pinned_comment": p.get("pinned_comment") or "", "ai_label": bool(p.get("ai_label", True)),
            "credit": p.get("credit") or "",
            "platforms": {"tiktok": {"caption": tiktok},
                          "youtube": {"title": (plan.get("title_bar") or title)[:100], "description": tiktok},
                          "facebook": {"caption": tiktok}}}
    (d / "post.json").write_text(json.dumps(post, ensure_ascii=False, indent=1), encoding="utf-8")
    return post


def _draw_cover(img_path: Path, out: Path, text: str):
    from PIL import Image, ImageDraw, ImageFont
    from .render import APP_FONTS, _wrap
    im = Image.open(img_path).convert("RGB")
    W, H = im.size
    fs = int(min(W, H) * 0.085)
    try:
        font = ImageFont.truetype(str(APP_FONTS / "BeVietnamPro-ExtraBold.ttf"), fs)
    except OSError:
        font = ImageFont.load_default()
    lines = _wrap(text, font, W * 0.84) or [text]
    dr = ImageDraw.Draw(im)
    lh = fs * 1.2
    y = H * 0.62 - len(lines) * lh / 2
    for ln in lines:
        w = dr.textlength(ln, font=font)
        dr.text(((W - w) / 2, y), ln, font=font, fill=(255, 255, 255), stroke_width=max(3, fs // 12), stroke_fill=(0, 0, 0))
        y += lh
    im.save(out, quality=92)


def extras(job, d: Path, s, out_name: str, plan, total: float):
    """cover.jpg + teaser_N.mp4 from the finished video (times mapped through the cuts) + post.json."""
    made = {}
    cm = CutMap(plan.get("cuts") or [], total)
    src = d / ("output_tiktok.mp4" if (d / "output_tiktok.mp4").exists() else out_name)
    cov = plan.get("cover") or {}
    if src.exists():
        t = cm.t(float(cov.get("at") or 0))
        raw = d / "cover_raw.jpg"
        util.run(job, ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1", str(raw)], check=False)
        if raw.exists():
            _draw_cover(raw, d / "cover.jpg", cov.get("text") or plan.get("title_bar") or "")
            raw.unlink(missing_ok=True)
            made["cover"] = "cover.jpg"
    from .render import APP_FONTS
    import os
    fdir = os.path.relpath(APP_FONTS, d).replace("\\", "/")
    for i, tz in enumerate(plan.get("teasers") or [], 1):
        a, b = cm.t(tz["from"]), cm.t(tz["to"])
        if b - a < 5 or not src.exists():
            continue
        W, H = util.video_size(src)
        tail = 2.5
        rows = []
        if tz.get("overlay"):
            rows += _card(tz["overlay"], 0, 3.2, W, H, min(W, H) * 0.07, H * 0.42, alpha=0.4, layer=20)
        rows += _card(tz.get("end_card") or "Bản đầy đủ trên kênh 👆", b - a + 0.1, b - a + tail, W, H, min(W, H) * 0.07,
                      H * 0.45, alpha=1.0, layer=24)
        ass = d / f"teaser_{i}.ass"
        ass_file(ass, W, H, rows)
        out = f"teaser_{i}.mp4"
        vf = (f"tpad=stop_mode=clone:stop_duration={tail},eq=brightness=-0.15:enable='gte(t,{b - a:.2f})',"
              f"gblur=sigma=18:enable='gte(t,{b - a:.2f})',subtitles={ass.name}:fontsdir='{fdir}'")
        # a FIXED output length (-t): with tpad (picture) + apad (sound) after a cut the graph could wait forever for
        # an end that never came (teaser_1 of job 6cf9 hung for minutes); util.ffmpeg's watchdog stops a stall anyway
        try:
            util.ffmpeg(job, ["-ss", f"{a:.2f}", "-i", src.name, "-t", f"{b - a + tail:.2f}", "-vf", vf,
                              "-af", f"apad,afade=t=out:st={b - a:.2f}:d={tail}", "-c:v", "libx264", "-preset", "veryfast",
                              "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                              out], duration=b - a + tail, step="summary", cwd=d, stall_s=90)
        except RuntimeError as e:
            from .jobs import Cancelled
            if isinstance(e, Cancelled):
                raise
            job.log(f"Không tạo được clip mồi {i}: {str(e).splitlines()[0][:160]}", "warn")
            (d / out).unlink(missing_ok=True)
            continue
        if (d / out).exists():
            made[f"teaser_{i}"] = out
    post_from_plan(d, plan, plan.get("title_bar") or "")
    made["post"] = "post.json"
    return made


def xfade_frames(fps):
    """Crossfade length in frames (even: half of it is taken from each side of the cut)."""
    return max(2, int(round(XFADE_S * fps / 2)) * 2)


def cut_graph(keep, vin, ain, xfade=None, fps=30.0):
    """ffmpeg filter parts that keep only `keep` [(a, b)] of picture `vin` and sound `ain` (labels without
    brackets) and join them: ([video parts], [audio parts], vout, aout). Trim + concat moves picture and sound by
    exactly the same amount (aselect on ffmpeg 8.0 silently kept every audio frame).
    `xfade[i]` True: join i (keep[i] → keep[i+1]) crossfades picture and sound over a few frames. Each side gives
    half of the overlap from INSIDE the cut (silence), so the output length — and every mapped time — is unchanged."""
    k = len(keep)
    xf = [bool(x) for x in (xfade or [])][: max(0, k - 1)]
    xf += [False] * (max(0, k - 1) - len(xf))
    if not any(xf):
        vp = [f"[{vin}]split={k}" + "".join(f"[kv{i}]" for i in range(k))]
        ap = [f"[{ain}]asplit={k}" + "".join(f"[ka{i}]" for i in range(k))]
        for i, (a, b) in enumerate(keep):
            vp.append(f"[kv{i}]trim=start={a:.6f}:end={b:.6f},setpts=PTS-STARTPTS[kvt{i}]")
            ap.append(f"[ka{i}]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS[kat{i}]")
        ap.append("".join(f"[kvt{i}][kat{i}]" for i in range(k)) + f"concat=n={k}:v=1:a=1[vcut][acut]")
        return vp, ap, "vcut", "acut"
    nf = xfade_frames(fps)
    D, h = nf / fps, nf / 2 / fps
    seg = []
    for i, (a, b) in enumerate(keep):
        a2 = a - h if i > 0 and xf[i - 1] else a
        b2 = b + h if i < k - 1 and xf[i] else b
        seg.append((a2, b2))
    vp = [f"[{vin}]split={k}" + "".join(f"[kv{i}]" for i in range(k))]
    ap = [f"[{ain}]asplit={k}" + "".join(f"[ka{i}]" for i in range(k))]
    for i, (a, b) in enumerate(seg):
        # one time base everywhere: xfade refuses inputs whose time bases differ (concat outputs 1/1000000)
        vp.append(f"[kv{i}]trim=start={a:.6f}:end={b:.6f},setpts=PTS-STARTPTS,settb=AVTB[kvt{i}]")
        ap.append(f"[ka{i}]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS[kat{i}]")
    cv, ca, length = "kvt0", "kat0", seg[0][1] - seg[0][0]
    for i in range(1, k):
        nv, na = f"kvj{i}", f"kaj{i}"
        if xf[i - 1]:
            vp.append(f"[{cv}][kvt{i}]xfade=transition=fade:duration={D:.6f}:offset={length - D:.6f},settb=AVTB[{nv}]")
            ap.append(f"[{ca}][kat{i}]acrossfade=d={D:.6f}:c1=tri:c2=tri[{na}]")
            length += (seg[i][1] - seg[i][0]) - D
        else:
            vp.append(f"[{cv}][kvt{i}]concat=n=2:v=1:a=0,settb=AVTB[{nv}]")
            ap.append(f"[{ca}][kat{i}]concat=n=2:v=0:a=1[{na}]")
            length += seg[i][1] - seg[i][0]
        cv, ca = nv, na
    vp.append(f"[{cv}]null[vcut]")
    ap.append(f"[{ca}]anull[acut]")
    return vp, ap, "vcut", "acut"

