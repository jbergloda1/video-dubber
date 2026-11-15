"""Crawl Douyin trending lists into data/trending.json. Crawling only collects the list —
nothing is downloaded or processed until you pick videos in the Trending tab.

Sources (read the same way a normal visitor's browser would, at a gentle pace):
  hot       抖音热榜: ~50 hot topics, each with its main video
  featured  抖音精选: recommended popular videos, scrolled a few pages
"""
import json
import re
import shutil
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from . import config
from .downloader import PROFILE, _launch, _profile_lock

STORE = config.DATA / "trending.json"
COVERS = config.DATA / "trending_covers"
_lock = threading.Lock()
state = {"running": False, "source": "", "progress": 0.0, "message": "", "error": "", "added": 0, "finished": 0}


# ------------------------------------------------------------------ storage
def cover_name(vid: str) -> str:
    return str(vid).replace(":", "_")


def load() -> dict:
    if STORE.exists():
        try:
            return json.loads(STORE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save(items: dict):
    with _lock:
        tmp = STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        # Windows: replacing fails (WinError 5/32) while another request is reading the file — retry briefly
        for k in range(20):
            try:
                tmp.replace(STORE)
                return
            except PermissionError:
                if k == 19:
                    raise
                time.sleep(0.1 + 0.05 * k)


def update(vid: str, **fields):
    items = load()
    if vid in items:
        items[vid].update(fields)
        save(items)


def fill_from_aweme(vid, aweme):
    """After resolving a video (preview/run), fill in metadata the list didn't have."""
    items = load()
    it = items.get(vid)
    if not it:
        return
    full = _item_from_aweme(aweme, it.get("source", ""))
    for k in ("duration", "likes", "comments", "shares", "collects", "author"):
        if full.get(k) and not it.get(k):
            it[k] = full[k]
    it["video_id"] = full["id"]
    save(items)


def remove(ids):
    items = load()
    for i in ids:
        items.pop(i, None)
        (COVERS / f"{cover_name(i)}.jpg").unlink(missing_ok=True)
    save(items)


# ------------------------------------------------------------------ parsing
def _walk_awemes(o, out):
    if isinstance(o, dict):
        if o.get("aweme_id") and isinstance(o.get("video"), dict):
            out.append(o)
            return
        for v in o.values():
            _walk_awemes(v, out)
    elif isinstance(o, list):
        for v in o:
            _walk_awemes(v, out)


def _first(lst):
    return (lst or [None])[0]


def _item_from_aweme(a, source, extra=None):
    v = a.get("video") or {}
    st = a.get("statistics") or {}
    dur = a.get("duration") or v.get("duration") or 0
    cover = _first((v.get("cover") or {}).get("url_list")) or _first((v.get("origin_cover") or {}).get("url_list"))
    it = {
        "id": str(a["aweme_id"]),
        "url": f"https://www.douyin.com/video/{a['aweme_id']}",
        "title": (a.get("desc") or a.get("preview_title") or "").strip(),
        "author": (a.get("author") or {}).get("nickname", ""),
        "duration": round(dur / 1000, 1) if dur > 1000 else dur,
        "likes": st.get("digg_count") or 0,
        "comments": st.get("comment_count") or 0,
        "shares": st.get("share_count") or 0,
        "collects": st.get("collect_count") or 0,
        "created": a.get("create_time") or 0,
        "cover_url": cover,
        "is_image": bool(a.get("images")) or a.get("aweme_type") in (68, 150),
        "source": source,
    }
    if extra:
        it.update(extra)
    return it


def _merge(items, it):
    now = time.time()
    old = items.get(it["id"])
    if old:
        keep = {k: old[k] for k in ("first_seen", "job_id", "status", "hidden") if k in old}
        sources = set(old.get("sources", [old.get("source")])) | {it["source"]}
        feeds = set(old.get("feeds") or ([old["feed"]] if old.get("feed") else [])) | ({it["feed"]} if it.get("feed") else set())
        # don't overwrite good metadata with empty values from a thinner source
        merged = dict(old)
        merged.update({k: v for k, v in it.items() if v not in (None, "", 0) or k not in old})
        merged.update(keep)
        merged["sources"] = sorted(s for s in sources if s)
        merged["feeds"] = sorted(feeds)
        merged["last_seen"] = now
        items[it["id"]] = merged
        return False
    it["first_seen"] = it["last_seen"] = now
    it["sources"] = [it["source"]]
    it["feeds"] = [it["feed"]] if it.get("feed") else []
    it["status"] = "new"
    items[it["id"]] = it
    return True


def _save_cover(it):
    url = it.get("cover_url")
    if not url:
        return
    COVERS.mkdir(parents=True, exist_ok=True)
    p = COVERS / f"{cover_name(it['id'])}.jpg"
    if p.exists():
        return
    try:
        req = urllib.request.Request(url, headers={"Referer": "https://www.douyin.com/"})
        with urllib.request.urlopen(req, timeout=20) as r:
            p.write_bytes(r.read())
    except Exception:
        pass


# ------------------------------------------------------------------ crawl (orchestrates source adapters)
CRAWL_STATE = config.DATA / "crawl_state.json"     # per feed: how deep we already went (for "load more")
MAX_PAGES = 30


class CrawlCancelled(Exception):
    pass


def feed_key(src, feed):
    return f"{src}|{feed}"


def load_crawl_state() -> dict:
    try:
        return json.loads(CRAWL_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_crawl_state(st):
    try:
        CRAWL_STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def cancel():
    if state.get("running"):
        state["cancel"] = True


def crawl(targets, pages: int = 3, s: dict | None = None, more: bool = False):
    """Run in a background thread. targets: "hot" / "featured" (legacy) or a list of (source_id, feed).
    more=True: go deeper than last time (pages already fetched for that feed + `pages`) — "load more".
    Each feed runs in its own try/except: one failing source/feed never stops the others. cancel() stops
    between pages/feeds; feeds already finished stay saved."""
    from .sources import REGISTRY
    s = s or config.load()
    if isinstance(targets, str):
        targets = [("douyin", targets)]
    targets = [(src, feed) for src, feed in targets if src in REGISTRY]
    state.update(running=True, cancel=False, source=", ".join(f for _, f in targets), progress=0.0,
                 message="Bắt đầu…", error="", added=0, errors=[])
    found_total, added, errors = 0, 0, []
    depth = load_crawl_state()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            for n, (src, feed) in enumerate(targets):
                if state.get("cancel"):
                    raise CrawlCancelled()
                adapter = REGISTRY[src]
                base = n / max(len(targets), 1)
                key = feed_key(src, feed)
                n_pages = min(MAX_PAGES, (depth.get(key, {}).get("pages", 0) + pages) if more else pages)

                scrolled = [1]          # pages actually loaded (adapters report "Cuộn trang k/n")

                def progress(frac, msg, base=base, adapter=adapter, feed=feed, scrolled=scrolled):
                    """Returns False once Stop was pressed: the adapter stops scrolling and returns what it has."""
                    if state.get("cancel"):
                        state["message"] = f"[{adapter.label} · {feed}] Đang dừng, lưu phần đã lấy…"
                        return False
                    m = re.match(r"Cuộn trang (\d+)/", msg)
                    if m:
                        scrolled[0] = int(m.group(1))
                    state.update(progress=min(0.95, base + frac / max(len(targets), 1) * 0.9),
                                 message=f"[{adapter.label} · {feed}] {msg}")
                    return True
                try:
                    with adapter_context(pw, adapter, s) as page:
                        found = adapter.discover(page, feed, n_pages, s, progress)
                except CrawlCancelled:
                    raise
                except Exception as e:          # isolate: report and continue with the next feed
                    errors.append(f"{adapter.label} · {feed}: {str(e).splitlines()[0][:200]}")
                    continue
                items = load()
                new_here = 0
                for it in found:
                    if it.get("is_image"):
                        continue      # photo posts can't be dubbed
                    it.setdefault("platform", src)
                    if _merge(items, it):
                        added += 1
                        new_here += 1
                    _save_cover(it)
                save(items)
                found_total += len(found)
                done_pages = scrolled[0] if state.get("cancel") else n_pages
                depth[key] = {"pages": done_pages, "last": time.time(), "found": len(found), "new": new_here}
                _save_crawl_state(depth)
                if state.get("cancel"):
                    raise CrawlCancelled()
                if n + 1 < len(targets):
                    end = time.time() + float(s.get("crawl_gap_s", 5))      # be gentle between feeds
                    while time.time() < end:
                        if state.get("cancel"):
                            raise CrawlCancelled()
                        time.sleep(0.25)
        msg = f"Xong: {found_total} video, {added} video mới."
        if errors:
            msg += f" {len(errors)} nguồn lỗi."
        state.update(added=added, message=msg, errors=errors,
                     error="" if found_total or not errors else "Không lấy được video nào: " + " | ".join(errors))
    except CrawlCancelled:
        state.update(added=added, errors=errors, error="",
                     message=f"Đã dừng. Đã lưu {added} video mới lấy được trước khi dừng.")
    except Exception as e:
        state.update(error=str(e).splitlines()[0], message="Lỗi")
    finally:
        state.update(running=False, cancel=False, progress=1.0, finished=time.time())


class adapter_context:
    """Open the adapter's browser profile (Douyin: the app profile shared with the downloader,
    or a throw-away one if it's busy) and yield a page."""

    def __init__(self, pw, adapter, s):
        self.pw, self.adapter, self.s = pw, adapter, s
        self.tmp, self.locked, self.ctx = None, False, None

    def __enter__(self):
        if hasattr(self.adapter, "open_context"):
            self.ctx, self._close = self.adapter.open_context(self.pw, self.s)
            return self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.locked = _profile_lock.acquire(timeout=2)
        if not self.locked:
            self.tmp = Path(tempfile.mkdtemp(prefix="dy_trend_"))
        self.ctx = _launch(self.pw, headless=bool(self.s.get("douyin_headless", True)),
                           profile=PROFILE if self.locked else self.tmp)
        self._close = self.ctx.close
        return self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def __exit__(self, *exc):
        try:
            self._close()
        except Exception:
            pass
        if self.locked:
            _profile_lock.release()
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)
        return False


def hot_items(bucket):
    """Douyin hot board response -> items (one per topic; the topic page plays the real video)."""
    words = []
    for data in bucket:
        words = ((data.get("data") or {}).get("word_list")) or words
    out = []
    for w in words:
        gid = str(w.get("group_id") or "")
        sid = str(w.get("sentence_id") or "")
        if not gid or not sid:
            continue
        cover = _first((w.get("word_cover") or {}).get("url_list"))
        out.append({
            # group_id is NOT the playable video: the topic page (/hot/<sentence_id>) shows the real one
            "id": gid, "url": f"https://www.douyin.com/hot/{sid}", "sentence_id": sid, "title": w.get("word", ""),
            "author": "", "duration": 0, "likes": 0, "comments": 0, "shares": 0, "collects": 0,
            "created": w.get("event_time") or 0, "cover_url": cover, "is_image": False, "source": "hot",
            "feed": "hot", "topic": w.get("word", ""), "hot_value": w.get("hot_value") or 0,
            "views": w.get("view_count") or 0, "rank": w.get("position") or 0,
        })
    return out
