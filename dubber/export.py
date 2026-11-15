"""Finished videos also land in the "final" folder under a readable Vietnamese name, e.g.
    final/Loài người biến mất, Trái Đất sẽ ra sao (Lồng tiếng).mp4   (+ .srt)
The name comes from the post kit title (postgen), else the job title. Files are hard links when the export folder
is on the same drive (no extra disk space), copies otherwise. Re-exporting a job replaces its previous files.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import config

LABELS = {"output": "Lồng tiếng", "output_vo": "Thuyết minh", "output_sub": "Phụ đề Việt", "output_text": "Dịch chữ",
          "output_tiktok": "TikTok"}
FILE = "export.json"


def folder(s) -> Path:
    p = Path(s.get("export_dir") or (config.DATA.parent / "final"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_name(title: str, limit=110) -> str:
    t = re.sub(r"#\S+", "", title or "")                       # hashtags don't belong in a file name
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].strip(" .,")
    return t or "video"


def _title(d: Path, job):
    p = d / "post.json"
    if p.exists():
        try:
            t = json.loads(p.read_text(encoding="utf-8")).get("title") or ""
            if t:
                return t
        except Exception:
            pass
    tv = d / "title_vi.txt"                 # translated original title (jobs made before post kits existed)
    if tv.exists():
        return tv.read_text(encoding="utf-8").strip()
    return job.title or job.id


_VI = re.compile(r"[ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ]", re.I)
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_cache: dict = {}


def needs_title(job) -> bool:
    """No Vietnamese name yet, and the original title isn't Vietnamese already."""
    d = job.dir
    if (d / "post.json").exists() or (d / "title_vi.txt").exists():
        return False
    t = job.title or ""
    return bool(t.strip()) and (bool(_CJK.search(t)) or not _VI.search(t))


def vi_title(job) -> str:
    """Vietnamese name for the history list (same one the final folder uses); '' when there is none yet."""
    d = job.dir
    stamp = tuple((d / f).stat().st_mtime if (d / f).exists() else 0 for f in ("post.json", "title_vi.txt"))
    hit = _cache.get(job.id)
    if hit and hit[0] == stamp:
        return hit[1]
    t = _title(d, job) if any(stamp) else ""
    t = re.sub(r"\s+", " ", re.sub(r"#\S+", "", t)).strip()
    if t == (job.title or "").strip():
        t = ""
    _cache[job.id] = (stamp, t)
    return t


def translate_titles_of(jobs, s, log=print):
    """Translate the original titles of jobs that have no Vietnamese name — ONE request — into title_vi.txt."""
    need = [j for j in jobs if needs_title(j)]
    if not need:
        return 0
    from . import translate

    class Q:
        proc = None
        def log(self, m, level="info"): log(m)
        def step(self, *a, **k): pass
        def check_cancel(self): pass
    ev = [{"id": i, "src": re.sub(r"#\S+", "", j.title or "").strip() or j.title, "start": 0, "end": 0, "tgt": ""}
          for i, j in enumerate(need)]
    n = 0
    try:
        translate.translate_texts(Q(), [e for e in ev if e["src"]], s,
                                  "Tiêu đề video (dịch ngắn gọn, tự nhiên, dùng làm tên file)")
        for e, j in zip(ev, need):
            if e.get("tgt") and e["tgt"] != e["src"]:
                (j.dir / "title_vi.txt").write_text(e["tgt"].strip(), encoding="utf-8")
                n += 1
    except Exception as e:
        log(f"Không dịch được tiêu đề ({e}); dùng tiêu đề gốc")
    return n


def _place(src: Path, dst: Path):
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)                 # same drive: instant, no extra space
    except OSError:
        shutil.copy2(src, dst)


def run(job, d: Path, s) -> dict:
    out_dir = folder(s)
    old = load(d)
    for p in old.values():               # previous export of this job (title may have changed)
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass
    base = safe_name(_title(d, job))
    done = {}
    for key, label in LABELS.items():
        name = job.files.get(key)
        if not name or not (d / name).exists():
            continue
        stem = f"{base} ({label})"
        dst = out_dir / f"{stem}.mp4"
        n = 2
        while dst.exists() and str(dst) not in old.values():
            dst = out_dir / f"{stem} {n}.mp4"
            n += 1
        _place(d / name, dst)
        done[key] = str(dst)
        if (d / "subs.srt").exists() and key in ("output", "output_vo", "output_sub"):
            srt = dst.with_suffix(".srt")
            _place(d / "subs.srt", srt)
            done[key + "_srt"] = str(srt)
    (d / FILE).write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
    return done


def load(d: Path) -> dict:
    p = d / FILE
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def reveal(path: Path):
    """Open Explorer at the file (selected) or folder — on this PC, where the server runs."""
    path = Path(path)
    if os.name == "nt":
        if path.is_file():
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(str(path))
    else:
        subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])


def export_all(s, translate_titles=True, log=print) -> dict:
    """Put every finished video of every job into the final folder. Jobs without a Vietnamese title get
    their original title translated — all of them in ONE request to the translation provider."""
    from .jobs import Job
    jobs = [j for j in (Job.load(f) for f in sorted(config.JOBS.iterdir()) if (f / "job.json").exists()) if j]
    todo = [j for j in jobs if any(j.files.get(k) and (j.dir / j.files[k]).exists() for k in LABELS)]
    if translate_titles:
        translate_titles_of(todo, s, log)
    out = {}
    for j in todo:
        try:
            out[j.id] = run(j, j.dir, {**s, **j.options, "export_dir": s.get("export_dir", "")})
        except Exception as e:
            log(f"{j.id}: {e}")
    return out
