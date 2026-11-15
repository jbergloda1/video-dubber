"""“📋 Đánh giá” — you check a finished video, score it and write down what to fix. Everything you write is kept:
  - per job:  data/jobs/<id>/review.json   (scores, notes, background-music settings)
  - all jobs: data/feedback/log.jsonl      (every note, append-only, for later analysis)

Three kinds of notes:
  script — the AI proposes the rewritten lines / narration; you see old vs new and apply it (voice + render again).
  tech   — on-screen text, blur, UI, pipeline bugs. Once you approve one it goes into the queue for Claude
           (data/feedback/queue.json + QUEUE.md, with the job, the time, a frame and the end of the log). You then
           ask Claude Code to fix the queue; Claude marks each item fixed there and the app shows it.
  other  — kept, nothing automatic.

Background music: the AI suggests what fits (length, mood, tempo, structure) and writes a prompt to paste into a
music generator; the file you upload replaces the original background (bgm.wav) at the next render.
"""
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from . import config

FILE = "review.json"
FEEDBACK = config.DATA / "feedback"
QUEUE = FEEDBACK / "queue.json"
QUEUE_MD = FEEDBACK / "QUEUE.md"
LOG = FEEDBACK / "log.jsonl"
FRAMES = FEEDBACK / "frames"
KINDS = ("script", "tech", "other")
AREAS = {"screen_text": "Dịch chữ trên màn hình", "blur": "Che mờ / xoá watermark / phụ đề gốc",
         "subs": "Phụ đề Việt", "voice": "Giọng đọc / đồng bộ", "cuts": "Cắt cảnh / Biên kịch kỹ thuật",
         "ui": "Giao diện app", "pipeline": "Pipeline / lỗi xử lý", "other": "Khác"}
CATS = {"overall": "Tổng thể", "script": "Bản dịch & kịch bản", "voice": "Giọng đọc", "subs": "Phụ đề & chữ màn hình",
        "picture": "Hình (che mờ, watermark, cắt)", "music": "Nhạc nền"}
OUTPUTS = ("output_tiktok.mp4", "output.mp4", "output_vo.mp4", "output_sub.mp4", "output_text.mp4")
BGM = "bgm.wav"


# ------------------------------------------------------------------ store
def load(d: Path) -> dict:
    p = d / FILE
    r = {}
    if p.exists():
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            r = {}
    r.setdefault("ratings", {})
    r.setdefault("notes", [])
    r.setdefault("music", {})
    q = {x["id"]: x for x in _queue()}                 # Claude marks queued items fixed in the queue file
    for n in r["notes"]:
        if n["id"] in q and n.get("status") in ("queued", "fixed"):
            n["status"] = q[n["id"]].get("status", n["status"])
            n["fix"] = q[n["id"]].get("fix", "")
    return r


def save(d: Path, r: dict):
    r["updated"] = time.time()
    (d / FILE).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")


def _log(entry: dict):
    FEEDBACK.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.time(), **entry}, ensure_ascii=False) + "\n")


def set_ratings(job, ratings: dict, comment: str = "") -> dict:
    r = load(job.dir)
    r["ratings"] = {k: int(v) for k, v in (ratings or {}).items() if k in CATS and str(v).isdigit() and 0 <= int(v) <= 5}
    r["comment"] = (comment or "").strip()[:2000]
    save(job.dir, r)
    _log({"type": "rating", "job": job.id, "ratings": r["ratings"], "comment": r["comment"]})
    return r


def output_file(d: Path):
    return next((d / f for f in OUTPUTS if (d / f).exists()), None)


def add_note(job, kind: str, text: str, t=None, area: str = "") -> dict:
    text = (text or "").strip()
    if kind not in KINDS or not text:
        raise ValueError("Cần loại ghi chú và nội dung")
    n = {"id": uuid.uuid4().hex[:10], "kind": kind, "text": text[:4000], "t": float(t) if t not in (None, "") else None,
         "area": area if area in AREAS else ("other" if kind == "tech" else ""), "status": "open", "created": time.time()}
    r = load(job.dir)
    r["notes"].append(n)
    save(job.dir, r)
    _log({"type": "note", "job": job.id, **n})
    return n


def update_note(job, nid: str, **fields) -> dict:
    r = load(job.dir)
    n = next((x for x in r["notes"] if x["id"] == nid), None)
    if not n:
        raise KeyError(nid)
    n.update(fields)
    save(job.dir, r)
    _log({"type": "note_update", "job": job.id, "id": nid, **{k: v for k, v in fields.items() if k != "proposal"}})
    return n


def delete_note(job, nid: str):
    r = load(job.dir)
    r["notes"] = [x for x in r["notes"] if x["id"] != nid]
    save(job.dir, r)
    q = [x for x in _queue() if x["id"] != nid]
    _write_queue(q)


# ------------------------------------------------------------------ tech notes → queue for Claude
def _queue() -> list[dict]:
    if QUEUE.exists():
        try:
            return json.loads(QUEUE.read_text(encoding="utf-8"))
        except ValueError:
            return []
    return []


def _write_queue(q: list[dict]):
    FEEDBACK.mkdir(parents=True, exist_ok=True)
    QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
    open_ = [x for x in q if x.get("status") == "queued"]
    md = ["# Hàng chờ lỗi kỹ thuật (từ tab 📋 Đánh giá)", "",
          "Dữ liệu gốc: `data/feedback/queue.json`. Sửa xong một mục: đặt `status` = `fixed`, ghi `fix` (đã sửa gì) "
          "và `fixed_at` (epoch). App tự hiện trạng thái này cho người dùng.", "",
          f"Đang chờ: {len(open_)} · Đã sửa: {sum(1 for x in q if x.get('status') == 'fixed')}", ""]
    for x in q:
        md += [f"## [{x.get('status')}] {x.get('area_name', '')} — job {x['job_id']}" + (f" @ {x['t']:.1f}s" if x.get("t") is not None else ""),
               "", x["text"], "", f"- Video: {x.get('job_title', '')}", f"- File: `{x.get('output', '')}`"]
        if x.get("frame"):
            md.append(f"- Khung hình: `{x['frame']}`")
        if x.get("fix"):
            md.append(f"- Đã sửa: {x['fix']}")
        md.append("")
    QUEUE_MD.write_text("\n".join(md), encoding="utf-8")


def grab_frame(src: Path, t: float, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t):.2f}", "-i", str(src), "-frames:v", "1",
                        "-vf", "scale='min(960,iw)':-2", str(dest)], capture_output=True,
                       creationflags=0x08000000 if os.name == "nt" else 0)
    return r.returncode == 0 and dest.exists()


def approve_tech(job, nid: str) -> dict:
    """You approved a tech note: it goes into Claude's queue with what Claude needs to reproduce it."""
    r = load(job.dir)
    n = next((x for x in r["notes"] if x["id"] == nid), None)
    if not n or n["kind"] != "tech":
        raise KeyError(nid)
    out = output_file(job.dir)
    frame = ""
    if out and n.get("t") is not None and grab_frame(out, n["t"], FRAMES / f"{nid}.jpg"):
        frame = str(FRAMES / f"{nid}.jpg")
    warns = [l["msg"] for l in job.logs if l.get("level") in ("warn", "error")][-15:]
    item = {"id": nid, "job_id": job.id, "job_title": job.title, "job_dir": str(job.dir), "area": n["area"],
            "area_name": AREAS.get(n["area"], ""), "t": n.get("t"), "text": n["text"], "output": str(out or ""),
            "frame": frame, "mode": job.settings().get("output_mode"), "warnings": warns,
            "steps": {sid: (st.get("detail") or "") for sid, st in job.steps.items() if st.get("detail")},
            "created": n["created"], "queued_at": time.time(), "status": "queued", "fix": ""}
    q = [x for x in _queue() if x["id"] != nid] + [item]
    _write_queue(q)
    n.update(status="queued", frame=frame)
    save(job.dir, r)
    _log({"type": "queued", "job": job.id, "id": nid})
    return n


def queue_items() -> list[dict]:
    return _queue()


# ------------------------------------------------------------------ script notes → AI proposal → apply
SCRIPT_SYSTEM = """Bạn là biên tập viên lồng tiếng tiếng Việt cho video ngắn. Người dùng đã xem video và góp ý về bản dịch /
kịch bản. Hãy sửa ĐÚNG những chỗ góp ý nhắm tới (và chỗ liên quan trực tiếp cho mạch câu liền), không sửa lan man.
Quy tắc:
- Mỗi câu "lines" là một câu đọc liền một hơi, phải trọn ý, tự nhiên như người Việt nói; giữ độ dài gần như cũ để
  vẫn đọc kịp thời lượng (cột dur).
- Giữ xưng hô, giọng văn của bản hiện tại trừ khi góp ý yêu cầu đổi.
- Lời kể Biên kịch ("narration"), hook, thanh tiêu đề, câu loop: chỉ sửa khi góp ý nói tới.
- Không bịa thêm sự kiện không có trong video.
Trả về JSON: {"summary": "đã sửa gì (1–2 câu)", "lines": [{"i": <số thứ tự>, "tgt": "câu mới"}],
 "narration": [{"i": <số thứ tự>, "text": "câu mới"}], "hook_overlay": "...", "title_bar": "...", "loop_vo": "..."}
Bỏ trống / không ghi trường nào không đổi."""
SCRIPT_SCHEMA = {"type": "object", "required": ["summary"], "properties": {
    "summary": {"type": "string"},
    "lines": {"type": "array", "items": {"type": "object", "required": ["i", "tgt"],
                                         "properties": {"i": {"type": "integer"}, "tgt": {"type": "string"}}}},
    "narration": {"type": "array", "items": {"type": "object", "required": ["i", "text"],
                                             "properties": {"i": {"type": "integer"}, "text": {"type": "string"}}}},
    "hook_overlay": {"type": "string"}, "title_bar": {"type": "string"}, "loop_vo": {"type": "string"}}}


class _Q:
    """Job shim for AI calls made from the review card (logs go to the job, no pipeline step)."""
    proc = None

    def __init__(self, job):
        self._job = job

    def log(self, m, level="info"):
        self._job.log(m, level)

    def step(self, *a, **k):
        pass

    def check_cancel(self):
        pass


def _lines(d: Path) -> list[dict]:
    p = d / "lines.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def script_prompt(lines, plan, feedback: str, t=None) -> str:
    rows = "\n".join(f"[{i}] {l['start']:.1f}s dur={l['end'] - l['start']:.1f} | gốc: {l.get('src', '')} | dịch: {l['tgt']}"
                     for i, l in enumerate(lines) if not l.get("hook"))
    u = f"GÓP Ý CỦA NGƯỜI DÙNG{f' (ở khoảng giây {t:.1f})' if t is not None else ''}:\n{feedback}\n\nCÁC CÂU HIỆN TẠI:\n{rows}"
    if plan:
        nar = "\n".join(f"[{i}] {n.get('start', 0):.1f}s: {n.get('text', '')}" for i, n in enumerate(plan.get("narration") or []))
        u += (f"\n\nBIÊN KỊCH:\nHook (chữ): {(plan.get('hook') or {}).get('overlay', '')}\nThanh tiêu đề: {plan.get('title_bar', '')}"
              f"\nCâu loop: {(plan.get('ending') or {}).get('loop_vo', '')}\nLời kể:\n{nar or '(không có)'}")
    return u


def propose_script(job, s, nid: str) -> dict:
    from . import content
    from .providers import orchestrator
    from .providers.base import BadOutput
    r = load(job.dir)
    n = next((x for x in r["notes"] if x["id"] == nid), None)
    if not n or n["kind"] != "script":
        raise KeyError(nid)
    lines, plan = _lines(job.dir), content.load(job.dir)
    if not lines and not plan:
        raise ValueError("Job này chưa có bản dịch / kịch bản để sửa")
    user = script_prompt(lines, plan, n["text"], n.get("t"))
    q = _Q(job)

    def task(p):
        if p.kind == "mt":
            raise BadOutput("cần AI viết (không dùng máy dịch)")
        raw = p.complete(q, SCRIPT_SYSTEM, user, SCRIPT_SCHEMA, s)
        try:
            return json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        except Exception as e:
            raise BadOutput(f"Đề xuất sửa không đúng JSON: {e}")
    data, pid = orchestrator.run(q, s, task, "sửa kịch bản theo góp ý")
    prop = build_proposal(lines, plan, data)
    prop["provider"] = pid
    n.update(status="proposed", proposal=prop)
    save(job.dir, r)
    _log({"type": "script_proposal", "job": job.id, "id": nid, "summary": prop["summary"], "n": len(prop["changes"])})
    return n


def build_proposal(lines, plan, data) -> dict:
    """AI answer -> list of {where, i, old, new} (only real changes, valid indexes)."""
    ch = []
    for x in data.get("lines") or []:
        i, new = x.get("i"), (x.get("tgt") or "").strip()
        if isinstance(i, int) and 0 <= i < len(lines) and new and new != lines[i]["tgt"] and not lines[i].get("hook"):
            ch.append({"where": "line", "i": i, "t": lines[i]["start"], "old": lines[i]["tgt"], "new": new})
    if plan:
        nar = plan.get("narration") or []
        for x in data.get("narration") or []:
            i, new = x.get("i"), (x.get("text") or "").strip()
            if isinstance(i, int) and 0 <= i < len(nar) and new and new != nar[i].get("text"):
                ch.append({"where": "narration", "i": i, "t": nar[i].get("start"), "old": nar[i].get("text", ""), "new": new})
        for key, old in (("hook_overlay", (plan.get("hook") or {}).get("overlay", "")), ("title_bar", plan.get("title_bar", "")),
                         ("loop_vo", (plan.get("ending") or {}).get("loop_vo", ""))):
            new = (data.get(key) or "").strip()
            if new and new != old:
                ch.append({"where": key, "i": None, "t": None, "old": old, "new": new})
    return {"summary": (data.get("summary") or "").strip(), "changes": ch}


def apply_script(job, nid: str, keep: list[int] | None = None) -> str:
    """Write the accepted changes. Returns the step to run again ('tts' / 'preview' / 'translate')."""
    from . import content
    r = load(job.dir)
    n = next((x for x in r["notes"] if x["id"] == nid), None)
    if not n or not n.get("proposal"):
        raise KeyError(nid)
    chs = [c for k, c in enumerate(n["proposal"]["changes"]) if keep is None or k in keep]
    lines, plan = _lines(job.dir), content.load(job.dir)
    plan_changed = False
    for c in chs:
        if c["where"] == "line" and c["i"] < len(lines):
            lines[c["i"]]["tgt"], lines[c["i"]]["edited"] = c["new"], True
        elif plan and c["where"] == "narration" and c["i"] < len(plan.get("narration") or []):
            plan["narration"][c["i"]]["text"], plan_changed = c["new"], True
        elif plan and c["where"] == "hook_overlay":
            plan.setdefault("hook", {})["overlay"], plan_changed = c["new"], True
        elif plan and c["where"] == "title_bar":
            plan["title_bar"], plan_changed = c["new"], True
        elif plan and c["where"] == "loop_vo":
            plan.setdefault("ending", {})["loop_vo"], plan_changed = c["new"], True
    if any(c["where"] == "line" for c in chs):
        (job.dir / "lines.json").write_text(json.dumps(lines, ensure_ascii=False, indent=1), encoding="utf-8")
    if plan_changed:
        plan["edited"] = True
        content.save(job.dir, plan)
    n.update(status="applied", applied=[c for c in chs], applied_at=time.time())
    save(job.dir, r)
    _log({"type": "script_applied", "job": job.id, "id": nid, "changes": chs})
    job.log(f"Áp dụng góp ý kịch bản: {len(chs)} chỗ sửa")
    if plan_changed:
        return "translate"          # the plan is read from there on; unchanged steps are reused by their checkpoints
    return "tts" if job.settings()["output_mode"] in ("dub", "voiceover") else "preview"


# ------------------------------------------------------------------ background music
MUSIC_SYSTEM = """Bạn là music supervisor cho video ngắn TikTok tiếng Việt. Dựa vào nội dung video, hãy đề xuất nhạc nền
KHÔNG LỜI thay cho nhạc gốc: hợp cảm xúc, không lấn giọng đọc, hợp xu hướng TikTok Việt Nam.
Trả về JSON: {"mood": "...", "genre": "...", "bpm": <số>, "instruments": ["..."], "energy": "thấp|vừa|cao",
"structure": [{"from": <giây>, "to": <giây>, "desc": "đoạn này nhạc làm gì"}],
"avoid": "những gì nên tránh", "why": "vì sao hợp video (1–2 câu)",
"prompt_en": "prompt tiếng Anh cho công cụ tạo nhạc (Suno/Udio/…): instrumental, thể loại, mood, BPM, nhạc cụ, cấu trúc, độ dài",
"prompt_vi": "bản tiếng Việt của prompt", "search_keywords": ["từ khoá tìm nhạc có sẵn trong thư viện nhạc TikTok/YouTube"]}
Cấu trúc phải phủ đúng thời lượng video, cao trào trùng đoạn kịch tính/twist nếu có."""
MUSIC_SCHEMA = {"type": "object", "required": ["mood", "genre", "bpm", "prompt_en"], "properties": {
    "mood": {"type": "string"}, "genre": {"type": "string"}, "bpm": {"type": "number"},
    "instruments": {"type": "array", "items": {"type": "string"}}, "energy": {"type": "string"},
    "structure": {"type": "array", "items": {"type": "object", "properties": {
        "from": {"type": "number"}, "to": {"type": "number"}, "desc": {"type": "string"}}}},
    "avoid": {"type": "string"}, "why": {"type": "string"}, "prompt_en": {"type": "string"}, "prompt_vi": {"type": "string"},
    "search_keywords": {"type": "array", "items": {"type": "string"}}}}


def video_length(d: Path) -> float:
    from . import util
    out = output_file(d)
    for p in ([out] if out else []) + [d / "voice.wav", d / "source.mp4"]:
        if p and p.exists():
            try:
                return float(util.duration(p))
            except Exception:
                pass
    return 0.0


def music_prompt_input(job, d: Path) -> str:
    from . import content
    lines = _lines(d)
    text = " ".join(l["tgt"] for l in lines)
    if len(text) > 4000:
        text = text[:2600] + " … " + text[-1200:]
    post = {}
    if (d / "post.json").exists():
        try:
            post = json.loads((d / "post.json").read_text(encoding="utf-8"))
        except ValueError:
            pass
    plan = content.load(d) or {}
    dur = video_length(d)
    u = (f"Thời lượng video (bản sẽ đăng): {dur:.1f} giây\nTiêu đề: {post.get('title') or job.title}\n"
         f"Kiểu video: {job.settings().get('output_mode')} · tone: {plan.get('tone') or (plan.get('_meta') or {}).get('tone', '')}\n")
    if plan.get("narration"):
        u += "Lời kể / twist: " + " | ".join(f"{n.get('start', 0):.0f}s {n.get('text', '')}" for n in plan["narration"][:12]) + "\n"
    u += f"Lời thoại (đã dịch, theo thời gian):\n{text or '(không có lời)'}"
    return u


def suggest_music(job, s) -> dict:
    from .providers import orchestrator
    from .providers.base import BadOutput
    user = music_prompt_input(job, job.dir)
    q = _Q(job)

    def task(p):
        if p.kind == "mt":
            raise BadOutput("cần AI viết (không dùng máy dịch)")
        raw = p.complete(q, MUSIC_SYSTEM, user, MUSIC_SCHEMA, s)
        try:
            return json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        except Exception as e:
            raise BadOutput(f"Gợi ý nhạc không đúng JSON: {e}")
    data, pid = orchestrator.run(q, s, task, "gợi ý nhạc nền")
    dur = video_length(job.dir)
    data["duration"] = round(dur, 1)
    if dur and "second" not in data.get("prompt_en", "").lower():
        data["prompt_en"] = data["prompt_en"].rstrip(". ") + f". Instrumental, no vocals, length about {round(dur)} seconds."
    data["provider"] = pid
    r = load(job.dir)
    r["music"]["suggestion"] = data
    save(job.dir, r)
    _log({"type": "music_suggestion", "job": job.id, "mood": data.get("mood"), "genre": data.get("genre")})
    return data


def prepare_bgm(job, src: Path, volume: float = 1.0, keep_original: float = 0.0, offset: float = 0.0) -> float:
    """Your music file -> bgm.wav: as long as the video's sound (looped if shorter), loudness-matched, faded out,
    optionally mixed with a little of the original background. Returns its length."""
    from . import util
    d = job.dir
    ref = d / "no_vocals.wav" if (d / "no_vocals.wav").exists() else d / "voice.wav"
    L = max(util.duration(ref) if ref.exists() else 0.0, util.duration(d / "voice.wav") if (d / "voice.wav").exists() else 0.0)
    if L <= 0:
        raise ValueError("Job chưa có âm thanh để khớp thời lượng")
    fade = min(2.5, L / 4)
    music = (f"[0:a]atrim=start={max(0.0, offset):.2f},asetpts=PTS-STARTPTS,aloop=loop=-1:size=2e9,atrim=0:{L:.3f},"
             f"loudnorm=I=-20:TP=-2:LRA=11,volume={max(0.0, min(volume, 3.0)):.2f},"
             f"afade=t=in:d=0.4,afade=t=out:st={L - fade:.3f}:d={fade:.3f},aformat=sample_rates=44100:channel_layouts=stereo[m]")
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src)]
    if keep_original > 0 and (d / "no_vocals.wav").exists():
        cmd += ["-i", str(d / "no_vocals.wav")]
        flt = (music + f";[1:a]volume={min(keep_original, 1.0):.2f},aformat=sample_rates=44100:channel_layouts=stereo[o];"
               f"[m][o]amix=inputs=2:normalize=0:duration=first[a]")
        lab = "[a]"
    else:
        flt, lab = music, "[m]"
    cmd += ["-filter_complex", flt, "-map", lab, "-t", f"{L:.3f}", "-ar", "44100", str(d / BGM)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        raise RuntimeError("Không xử lý được file nhạc: " + (err[-1][:200] if err else "lỗi ffmpeg"))
    rv = load(d)
    rv["music"].update(file=src.name, volume=volume, keep_original=keep_original, offset=offset, length=round(L, 2),
                       prepared=time.time())
    save(d, rv)
    _log({"type": "music_set", "job": job.id, "file": src.name, "volume": volume, "keep_original": keep_original})
    return L


def bgm_track(workdir: Path, s) -> str | None:
    """The background track the render should use instead of the separated original (None = the original)."""
    return BGM if s.get("bgm_enabled") and (workdir / BGM).exists() else None
