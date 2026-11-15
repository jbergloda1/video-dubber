"""Video library ("Kho video"): paste links, download them first — several at the same time — then pick which
ones to process. Processing a stored video never downloads it again.

data/library/<id>/source.mp4 (+ view.mp4 browser copy, thumb.jpg, meta.json); index in data/library.json.
Concurrency: `library_parallel` downloads at once. Douyin's lookup step (browser) runs one at a time with a
short gap (`library_douyin_gap_s`) so Douyin does not block us; the file transfers themselves run in parallel.
"""
import json
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, util

STORE = config.DATA / "library.json"
DIR = config.DATA / "library"
URL_RE = re.compile(r"https?://[^\s，。\"'<>]+")

_lock = threading.Lock()
_douyin_lock = threading.Lock()
_pool = None
_pool_size = 0
_running = {}          # id -> ItemJob


def load() -> dict:
    if not STORE.exists():
        return {}
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(items):
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    for i in range(8):
        try:
            tmp.replace(STORE)
            return
        except PermissionError:
            time.sleep(0.05 * (i + 1))


def update(iid, **kw):
    with _lock:
        items = load()
        if iid in items:
            items[iid].update(kw)
            _save(items)
            return items[iid]


def platform_of(url):
    from .downloader import is_bilibili, is_douyin, is_facebook, is_tiktok
    return "douyin" if is_douyin(url) else "facebook" if is_facebook(url) else \
        "tiktok" if is_tiktok(url) else "bilibili" if is_bilibili(url) else \
        "youtube" if ("youtube.com" in url or "youtu.be" in url) else "web"


def split_links(text):
    """All links in a pasted blob (share texts, one per line, mixed with other words)."""
    seen, out = set(), []
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(".,;)")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


class ItemJob:
    """Just enough of jobs.Job for the downloader: progress, log, cancel."""

    def __init__(self, iid):
        self.id, self.proc, self.cancelled, self._last = iid, None, False, 0.0

    def log(self, msg, level="info"):
        if level in ("warn", "error"):
            update(self.id, detail=msg[:200])

    def step(self, sid, status=None, progress=None, detail=None):
        now = time.time()
        tick = detail is None or re.search(r"\d/\d|%", detail)      # a progress tick, not a status message
        if tick and now - self._last < 0.7 and progress is not None and progress < 0.99:
            return                                   # don't rewrite the index on every progress tick
        self._last = now
        kw = {}
        if progress is not None:
            kw["progress"] = round(max(0.0, min(1.0, progress)), 3)
        if detail:
            kw["detail"] = detail[:200]
        elif progress is not None and 0 < progress < 0.97:
            kw["detail"] = f"Đang tải {progress / 0.95:.0%}"      # yt-dlp reports 0–95 %; the rest is cover/preview
        if kw:
            update(self.id, **kw)

    def check_cancel(self):
        if self.cancelled:
            from .jobs import Cancelled
            raise Cancelled()


def _executor(s):
    global _pool, _pool_size
    n = max(1, min(int(s.get("library_parallel") or 3), 6))
    if _pool is None or _pool_size != n:
        _pool, _pool_size = ThreadPoolExecutor(n, thread_name_prefix="lib"), n
    return _pool


def add(text, s) -> list:
    """Add every link found in `text`; links already in the library are not downloaded twice."""
    links = split_links(text)
    added, new = [], []
    with _lock:
        items = load()
        known = {it["url"]: k for k, it in items.items()}
        for u in links:
            if u in known and items[known[u]]["status"] in ("done", "queued", "downloading"):
                added.append(known[u])
                continue
            iid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
            items[iid] = {"id": iid, "url": u, "platform": platform_of(u), "status": "queued", "progress": 0.0,
                          "detail": "Chờ tải…", "title": "", "uploader": "", "duration": 0, "size": 0,
                          "created": time.time(), "error": "", "jobs": []}
            added.append(iid)
            new.append(iid)
        _save(items)
    for iid in new:                       # only new links start a download
        _executor(s).submit(_download, iid, s)
    return added


def retry(iid, s):
    it = update(iid, status="queued", progress=0.0, detail="Chờ tải…", error="")
    if it:
        _executor(s).submit(_download, iid, s)


def cancel(iid):
    j = _running.get(iid)
    if j:
        j.cancelled = True
        if j.proc:
            try:
                j.proc.kill()
            except Exception:
                pass
    else:
        update(iid, status="cancelled", detail="Đã huỷ")


def remove(iid):
    cancel(iid)
    with _lock:
        items = load()
        items.pop(iid, None)
        _save(items)
    shutil.rmtree(DIR / iid, ignore_errors=True)


def resume_pending(s):
    """After a server restart: unfinished downloads start again."""
    for iid, it in load().items():
        if it["status"] in ("queued", "downloading"):
            retry(iid, s)


def _download(iid, s):
    from . import downloader, render
    from .jobs import Cancelled
    it = load().get(iid)
    if not it or it["status"] not in ("queued",):
        return
    job = ItemJob(iid)
    _running[iid] = job
    d = DIR / iid
    d.mkdir(parents=True, exist_ok=True)
    out = d / "source.mp4"
    update(iid, status="downloading", progress=0.0, detail="Đang lấy thông tin…")
    try:
        url = downloader.extract_url(it["url"])
        if downloader.is_douyin(url) and not s.get("cookies_file"):
            from . import dtk_client
            aweme, cookies = None, []
            if dtk_client.configured(s) and not re.search(r"douyin\.com/hot/(\d+)", url):
                try:
                    aweme, _ = dtk_client.resolve_aweme(url, s)
                    if not downloader._play_urls(aweme):
                        aweme = None
                except Exception as e:
                    job.log(f"DTK MCP không lấy được ({e}) → dùng trình duyệt của app", "debug")
            if aweme is None:
                # the browser lookup is one-at-a-time (gentle on Douyin); the file transfer runs in parallel
                with _douyin_lock:
                    sl = dict(s, douyin_gap_s=float(s.get("library_douyin_gap_s", 8)))
                    aweme, cookies = downloader.resolve_aweme(job, url, sl)
            info = downloader._download_aweme(job, aweme, cookies, out)
            v = aweme.get("video") or {}
            dur = aweme.get("duration") or v.get("duration") or 0
            info["duration"] = dur / 1000 if dur > 1000 else dur
        else:
            info = downloader.download(job, url, out, s)
        info["webpage_url"] = url
        job.step("download", progress=0.97, detail="Tạo ảnh bìa & bản xem…")
        dur = util.duration(out)
        W, H = util.video_size(out)
        import subprocess
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{min(1.0, dur / 3):.2f}", "-i", str(out),
                        "-frames:v", "1", "-vf", "scale=320:-2", str(d / "thumb.jpg")],
                       creationflags=util.CREATE_NO_WINDOW)
        try:
            render.make_view_copy(out, d / "view.mp4")
        except Exception:
            pass
        (d / "meta.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        title = re.sub(r"\s*#\S+", "", info.get("title") or "").strip() or (info.get("title") or "")[:80]
        update(iid, status="done", progress=1.0, detail=f"{W}x{H}", title=title[:200],
               uploader=info.get("uploader", ""), duration=round(dur, 1), width=W, height=H,
               size=out.stat().st_size, finished=time.time())
    except Cancelled:
        update(iid, status="cancelled", detail="Đã huỷ")
    except Exception as e:
        update(iid, status="error", error=str(e)[-600:], detail=str(e).splitlines()[0][:200] if str(e) else "Lỗi")
    finally:
        _running.pop(iid, None)


def to_job_dir(iid, d: Path):
    """Put a stored video into a job folder (hard link: no copy, no re-download). Returns meta."""
    import os
    src = DIR / iid
    if not (src / "source.mp4").exists():
        raise RuntimeError("Video trong kho không còn (đã bị xoá?)")
    for a, b in (("source.mp4", "source.mp4"), ("view.mp4", "source_view.mp4")):
        if (src / a).exists():
            dst = d / b
            dst.unlink(missing_ok=True)
            try:
                os.link(src / a, dst)
            except OSError:
                shutil.copy2(src / a, dst)
    meta = json.loads((src / "meta.json").read_text(encoding="utf-8")) if (src / "meta.json").exists() else {}
    return meta


# ---- "🔎 Nhận diện kiểu video": recognise stored videos and recommend the output mode ------------------------------
_an_queue = []
_an_lock = threading.Lock()
_an_thread = None


def analyze_items(ids, s):
    """Queue stored videos for recognition (one at a time: Whisper/OCR share the GPU)."""
    global _an_thread
    items = load()
    n = 0
    with _an_lock:
        for iid in ids:
            it = items.get(iid)
            if it and it["status"] == "done" and iid not in _an_queue:
                _an_queue.append(iid)
                update(iid, analyzing="queued")
                n += 1
        if _an_queue and (_an_thread is None or not _an_thread.is_alive()):
            _an_thread = threading.Thread(target=_analyze_loop, args=(s,), daemon=True, name="lib-analyze")
            _an_thread.start()
    return n


def _analyze_loop(s):
    from . import analyze
    while True:
        with _an_lock:
            if not _an_queue:
                return
            iid = _an_queue.pop(0)
        it = load().get(iid)
        if not it:
            continue
        update(iid, analyzing="running", detail="Đang nhận diện kiểu video…")
        try:
            res = analyze.analyze(None, DIR / iid / "source.mp4", s, it.get("title", ""), out_dir=DIR / iid,
                                  progress=lambda f, m, iid=iid: update(iid, detail=f"Nhận diện: {m}"))
            r = res["rec"]
            update(iid, analyzing="", analysis={"mode": r["mode"], "options": r.get("options", {}),
                                                "reason": r["reason"], "genre": r.get("genre", ""),
                                                "confidence": r.get("confidence"), "summary": analyze.summary(res),
                                                "secs": res.get("secs")},
                   detail=f"{it.get('width')}x{it.get('height')}")
        except Exception as e:
            update(iid, analyzing="", detail=f"Nhận diện lỗi: {str(e).splitlines()[0][:150]}")
