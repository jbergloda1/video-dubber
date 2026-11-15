"""🤖 Autopilot: every night, pick videos from the video library and the Trending lists and queue them, so in the
morning they are dubbed / subtitled and waiting in the final folder for you to review and post.

Off by default — you turn it on and choose what it may pick (sources, categories, length, likes, age, how many per
day, which hours). "Xem trước" shows exactly what it would pick without queueing anything.

Per tick (every minute, while the server runs):
  1. outside the hours, switched off, or today's quota used → nothing
  2. once per night (optional) refresh the Trending lists of the chosen categories (lists only, same crawler as the
     Trending tab — it never bypasses captchas / logins)
  3. pick the best new candidates (most likes first; library videos first), never a video that already has a job
  4. queue them with the chosen mode (default ✨ auto-recognition) — the normal job queue runs them one by one
State (today's count, everything ever picked, a short log) lives in data/autopilot.json.
"""
import json
import threading
import time
from pathlib import Path

from . import config

STATE = config.DATA / "autopilot.json"
_lock = threading.Lock()
_thread = None

DEFAULTS = {
    "autopilot_enabled": False,
    "autopilot_start_hour": 1,        # run window, local time (01:00–07:00 by default; may wrap past midnight)
    "autopilot_end_hour": 7,
    "autopilot_max_per_day": 5,
    "autopilot_library": True,        # stored videos that were never processed
    "autopilot_trending": True,       # Trending lists
    "autopilot_categories": [],       # Trending category ids ([] = any category)
    "autopilot_min_duration": 15,
    "autopilot_max_duration": 600,
    "autopilot_min_likes": 10000,
    "autopilot_max_age_days": 30,
    "autopilot_refresh": False,       # crawl the chosen categories' lists once per night before picking
    "autopilot_mode": "auto",         # output mode for the jobs (auto = recognise each video)
    "autopilot_burn_subtitles": True, # burn the Vietnamese subtitles of the speech into autopilot videos
}


def _today():
    return time.strftime("%Y-%m-%d")


def load_state() -> dict:
    try:
        st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    except Exception:
        st = {}
    st.setdefault("day", _today())
    st.setdefault("count", 0)
    st.setdefault("picked", [])         # every video ever picked: {"key", "title", "job", "t", "source"}
    st.setdefault("log", [])
    if st["day"] != _today():
        st["day"], st["count"] = _today(), 0
    return st


def _save(st):
    st["log"] = st["log"][-60:]
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def _log(st, msg):
    st["log"].append({"t": time.time(), "msg": msg})


def in_window(s, now=None) -> bool:
    h = (now or time.localtime()).tm_hour
    a, b = int(s.get("autopilot_start_hour", 1)), int(s.get("autopilot_end_hour", 7))
    return a <= h < b if a <= b else (h >= a or h < b)          # e.g. 22 → 6 wraps past midnight


# ---- candidates -------------------------------------------------------------------------------------------------
def _job_urls():
    """URLs AND titles of every job: the same video often comes back under another link (share link vs library)."""
    from .jobs import get_manager
    jobs = get_manager().jobs.values()
    return {j.url for j in jobs} | {"title:" + _norm(j.title) for j in jobs if _norm(j.title)}


def _norm(title):
    import re
    t = re.sub(r"#\S+", "", title or "")
    return re.sub(r"\W+", "", t).lower()[:40]


def candidates(s, job_urls=None, state=None) -> list:
    """Videos autopilot may take, best first, each with why it qualifies. Nothing is queued here."""
    from . import categories, library, trending
    st = state or load_state()
    taken = {p["key"] for p in st["picked"]}
    urls = job_urls if job_urls is not None else _job_urls()
    lo, hi = float(s.get("autopilot_min_duration", 15)), float(s.get("autopilot_max_duration", 600))
    out = []
    if s.get("autopilot_library", True):
        for it in sorted(library.load().values(), key=lambda x: x.get("created", 0)):
            key = "library:" + it["id"]
            if it["status"] != "done" or it.get("jobs") or key in taken or key in urls or it.get("url") in urls                     or ("title:" + _norm(it.get("title"))) in urls:
                continue
            if not (lo <= float(it.get("duration") or 0) <= hi):
                continue
            rec = (it.get("analysis") or {}).get("mode")
            out.append({"key": key, "source": "library", "id": it["id"], "url": key, "title": it.get("title", ""),
                        "duration": it.get("duration"), "likes": None, "why": "Kho video, chưa xử lý"
                        + (f" · gợi ý {rec}" if rec else "")})
    if s.get("autopilot_trending", True):
        want = set(s.get("autopilot_categories") or [])
        nodes = categories.flatten(categories.load()) if want else []
        min_likes = int(s.get("autopilot_min_likes", 0) or 0)
        max_age = float(s.get("autopilot_max_age_days", 0) or 0)
        now = time.time()
        rows = []
        for it in trending.load().values():
            key = "trending:" + it["id"]
            if it.get("hidden") or it.get("status", "new") != "new" or it.get("is_image"):
                continue
            if key in taken or it["url"] in urls or ("title:" + _norm(it.get("title"))) in urls:
                continue
            if not (lo <= float(it.get("duration") or 0) <= hi) or (it.get("likes") or 0) < min_likes:
                continue
            if max_age and it.get("created") and now - it["created"] > max_age * 86400:
                continue
            cats = categories.classify(it, nodes) if want else []
            if want and not (want & set(cats)):
                continue
            rows.append({"key": key, "source": "trending", "id": it["id"], "url": it["url"],
                         "title": it.get("title", ""), "duration": it.get("duration"), "likes": it.get("likes"),
                         "why": f"Trending {it.get('likes', 0):,} like".replace(",", ".")
                         + (f" · {', '.join(c for c in cats if c in want)}" if want else "")})
        out += sorted(rows, key=lambda r: -(r["likes"] or 0))
    return out


# ---- queueing ---------------------------------------------------------------------------------------------------
def queue(s, n, reason="lịch đêm") -> list:
    """Queue up to n candidates as jobs. Returns what was queued."""
    from . import library, trending
    from .jobs import Job, get_manager
    with _lock:
        st = load_state()
        picks = candidates(s, state=st)[:max(0, n)]
        opts = {"output_mode": s.get("autopilot_mode", "auto"), "wait_for_preview": False,
                "content_review": False,        # nobody is there at night to approve a Biên kịch plan
                "burn_subtitles": bool(s.get("autopilot_burn_subtitles", True))}     # Vietnamese subtitles on
        done = []
        for c in picks:
            job = Job(c["url"], dict(opts))
            job.title = (c["title"] or "")[:120]
            get_manager().submit(job)
            if c["source"] == "library":
                it = library.load().get(c["id"]) or {}
                library.update(c["id"], jobs=(it.get("jobs") or []) + [job.id])
            else:
                trending.update(c["id"], status="queued", job_id=job.id)
            st["count"] += 1
            st["picked"].append({"key": c["key"], "title": c["title"][:80], "job": job.id, "t": time.time(),
                                 "source": c["source"]})
            done.append({**c, "job": job.id})
        if done:
            _log(st, f"Đưa {len(done)} video vào hàng đợi ({reason}): " + " | ".join(d["title"][:40] for d in done))
        _save(st)
        return done


def tick(s=None):
    """One scheduler step (called every minute)."""
    from . import categories, trending
    s = s or config.load()
    if not s.get("autopilot_enabled") or not in_window(s):
        return None
    st = load_state()
    left = int(s.get("autopilot_max_per_day", 5)) - st["count"]
    if left <= 0:
        return None
    if s.get("autopilot_trending", True) and s.get("autopilot_refresh") and st.get("crawled_day") != _today():
        if trending.state.get("running"):
            return None
        nodes = categories.flatten(categories.load())
        targets = []
        for cid in s.get("autopilot_categories") or []:
            targets += [t for t in categories.crawl_targets(cid, nodes) if t not in targets]
        st["crawled_day"] = _today()
        _log(st, f"Làm mới danh sách Trending: {len(targets)} nguồn" if targets else "Không có nguồn để làm mới")
        _save(st)
        if targets:
            threading.Thread(target=trending.crawl, args=(targets, 2), daemon=True).start()
            return "crawling"
    if trending.state.get("running"):
        return None                       # wait for the list refresh to finish before picking
    return queue(s, left)


def start():
    """Background scheduler (one per server process)."""
    global _thread
    if _thread and _thread.is_alive():
        return

    def loop():
        while True:
            try:
                tick()
            except Exception as e:
                try:
                    with _lock:
                        st = load_state()
                        _log(st, f"Lỗi: {str(e).splitlines()[0][:200]}")
                        _save(st)
                except Exception:
                    pass
            time.sleep(60)
    _thread = threading.Thread(target=loop, daemon=True, name="autopilot")
    _thread.start()
