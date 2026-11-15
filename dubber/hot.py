"""“🔥 Link hot”: find the most-watched videos to dub, by category, by trend, by keyword.

Sources (checked 2026-11-15, in the app's headless browser):
  - TikTok Vietnam Explore (tiktok.com/explore): 21 categories, each list comes with view counts — works without login.
  - TikTok search (tiktok.com/search/video?q=): answers empty without login → used only when you logged in
    (Settings → TikTok Việt Nam). The search box's suggestions (/api/search/general/preview) work without login.
  - TikTok Creative Center refuses the headless browser (HTTP error) → only linked, for you to open yourself.
  - Douyin: the Trending library (categories, 热榜 hot board) — Douyin's web lists have likes, not views.
The app only reads what these pages load for a normal visitor; it never works around logins, captchas or blocks.
"""
import json
import shutil
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote

from . import config

STORE = config.DATA / "hotlinks.json"
TT_CATEGORIES = ["Tất cả", "Chương trình", "Giải Trí", "Truyện tranh & hoạt hình", "Trò chơi", "Đời Sống",
                 "Ca hát & Khiêu vũ", "Thể Thao", "Giáo dục", "Ẩm Thực", "Xã hội", "Mối quan hệ", "Chăm sóc sắc đẹp",
                 "Thú Cưng", "Xe hơi", "Hát nhép", "Bé Yêu & Gia Đình", "Trang phục", "Công nghệ", "Chuyện Drama",
                 "Gym & Sức Khỏe"]
CREATIVE_CENTER = "https://ads.tiktok.com/creative/creativeCenter/trends/video?region=VN&period=7"
_lock = threading.Lock()
state = {"running": False, "message": "", "progress": 0.0, "error": "", "finished": 0, "cancel": False}


# ------------------------------------------------------------------ store
def load() -> dict:
    if STORE.exists():
        try:
            d = json.loads(STORE.read_text(encoding="utf-8"))
            d.setdefault("items", {})
            d.setdefault("suggest", {})
            return d
        except Exception:
            pass
    return {"items": {}, "suggest": {}}


def save(d: dict):
    with _lock:
        tmp = STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        for k in range(20):                  # Windows: another request may be reading the file this instant
            try:
                tmp.replace(STORE)
                return
            except PermissionError:
                if k == 19:
                    raise
                time.sleep(0.1 + 0.05 * k)


def tt_item(a: dict, feed: str) -> dict | None:
    """TikTok item (explore/search API) -> our record."""
    if not a.get("id") or a.get("imagePost"):
        return None                                  # photo posts can't be dubbed
    st = a.get("stats") or a.get("statsV2") or {}
    v = a.get("video") or {}
    au = a.get("author") or {}
    uid = au.get("uniqueId") or ""

    def n(k):
        try:
            return int(float(st.get(k) or 0))
        except (TypeError, ValueError):
            return 0
    return {"key": f"tiktok:{a['id']}", "platform": "tiktok", "id": str(a["id"]),
            "url": f"https://www.tiktok.com/@{uid}/video/{a['id']}" if uid else f"https://www.tiktok.com/video/{a['id']}",
            "title": (a.get("desc") or "").strip(), "author": au.get("nickname") or uid, "author_id": uid,
            "views": n("playCount"), "likes": n("diggCount"), "comments": n("commentCount"), "shares": n("shareCount"),
            "duration": v.get("duration") or 0, "created": int(a.get("createTime") or 0),
            "cover": v.get("cover") or v.get("originCover") or "", "feeds": [feed]}


def dtk_tt_item(c: dict, feed: str) -> dict | None:
    """DTK Content (or converted aweme) -> our TikTok hot record."""
    from . import dtk_client
    try:
        a = dtk_client.content_to_aweme(c)
    except Exception:
        return None
    if a.get("is_image") or not a.get("aweme_id"):
        return None
    vid = str(a["aweme_id"])
    au = a.get("author") or {}
    st = a.get("statistics") or {}
    v = a.get("video") or {}
    uid = au.get("unique_id") or ""
    dur = a.get("duration") or v.get("duration") or 0
    covers = (v.get("cover") or {}).get("url_list") or []
    return {
        "key": f"tiktok:{vid}",
        "platform": "tiktok",
        "id": vid,
        "url": f"https://www.tiktok.com/@{uid}/video/{vid}" if uid else f"https://www.tiktok.com/video/{vid}",
        "title": (a.get("desc") or a.get("preview_title") or "").strip(),
        "author": au.get("nickname") or uid,
        "author_id": uid,
        "views": int(st.get("play_count") or 0),
        "likes": int(st.get("digg_count") or 0),
        "comments": int(st.get("comment_count") or 0),
        "shares": int(st.get("share_count") or 0),
        "duration": round(dur / 1000, 1) if dur > 1000 else dur,
        "created": int(a.get("create_time") or 0),
        "cover": covers[0] if covers else "",
        "feeds": [feed],
    }


def _dtk_tiktok_search(arg: str, pages: int, s: dict) -> list[dict]:
    from . import dtk_client
    feed = f"search:{arg}"
    out, cursor = [], None
    is_user = arg.startswith("@") or "tiktok.com/@" in arg
    for i in range(max(pages, 1)):
        if state["cancel"]:
            break
        state.update(progress=0.1 + 0.8 * (i + 1) / max(pages, 1),
                     message=f"DTK MCP · TikTok “{arg}” (trang {i + 1}/{pages})…")
        if is_user:
            res = dtk_client.list_user_posts("tiktok", arg, s, cursor=cursor, count=20)
        else:
            res = dtk_client.search_posts("tiktok", arg, s, cursor=cursor, count=20)
        for raw in res.get("items") or []:
            it = dtk_tt_item(raw, feed)
            if it:
                out.append(it)
        cursor = res.get("cursor")
        if not res.get("has_more") or not cursor:
            break
    return out


def merge(d: dict, items: list[dict]) -> int:
    new = 0
    now = time.time()
    for it in items:
        old = d["items"].get(it["key"])
        if old:
            feeds = sorted(set(old.get("feeds") or []) | set(it["feeds"]))
            old.update({k: v for k, v in it.items() if v not in (None, "", 0) or k in ("views", "likes")})
            old["feeds"], old["seen"] = feeds, now
        else:
            it["found"] = it["seen"] = now
            d["items"][it["key"]] = it
            new += 1
    return new


def velocity(it: dict, now: float | None = None) -> float:
    """Views per hour since it was posted (rising videos beat old evergreen ones)."""
    if not it.get("created"):
        return 0.0
    hours = max(((now or time.time()) - it["created"]) / 3600, 1.0)
    return (it.get("views") or 0) / hours


def top(items: list[dict], sort="views", limit=10, max_dur=0, min_views=0) -> list[dict]:
    now = time.time()
    rows = [dict(i, vph=round(velocity(i, now))) for i in items
            if (not max_dur or (i.get("duration") or 0) <= max_dur) and (i.get("views") or 0) >= min_views]
    key = {"views": lambda i: i.get("views") or 0, "likes": lambda i: i.get("likes") or 0,
           "rising": lambda i: i["vph"], "new": lambda i: i.get("created") or 0}.get(sort) or (lambda i: i.get("views") or 0)
    rows.sort(key=key, reverse=True)
    return rows[:limit] if limit else rows


# ------------------------------------------------------------------ TikTok crawling
def _ctx(pw):
    """The app's TikTok profile (your login, if any) when free; a throw-away profile otherwise."""
    from .downloader import _launch
    from .sources import tiktok_web
    if tiktok_web._lock.acquire(blocking=False):
        try:
            ctx = _launch(pw, headless=True, profile=tiktok_web.PROFILE, locale="vi-VN")
        except Exception:
            tiktok_web._lock.release()
            raise
        return ctx, lambda: (ctx.close(), tiktok_web._lock.release())
    tmp = Path(tempfile.mkdtemp(prefix="tt_hot_"))
    ctx = _launch(pw, headless=True, profile=tmp, locale="vi-VN")
    return ctx, lambda: (ctx.close(), shutil.rmtree(tmp, ignore_errors=True))


def _scroll(page, pages, base, msg):
    for i in range(max(pages - 1, 0)):
        if state["cancel"]:
            break
        state.update(progress=base + (1 - base) * 0.9 * (i + 1) / pages, message=f"{msg} — cuộn trang {i + 2}/{pages}…")
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(3000)


def _tiktok(kind: str, arg: str, pages: int) -> tuple[list[dict], list[str]]:
    from playwright.sync_api import sync_playwright
    found, sugg = [], []
    with sync_playwright() as pw:
        ctx, close = _ctx(pw)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            live = {"on": kind == "search" or arg == "Tất cả"}

            def on(r):
                try:
                    if "/api/explore/item_list" in r.url and live["on"]:
                        found.extend(r.json().get("itemList") or [])
                    elif "/api/search/item/full" in r.url:
                        found.extend(r.json().get("item_list") or [])
                    elif "/api/search/general/preview" in r.url:
                        sugg.extend(s.get("content", "") for s in r.json().get("sug_list") or [])
                except Exception:
                    pass                      # an empty / non-JSON answer (e.g. search without login)
            page.on("response", on)
            if kind == "cat":
                state.update(message=f"Mở TikTok Explore · {arg}…", progress=0.05)
                page.goto("https://www.tiktok.com/explore?lang=vi-VN", wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(6000)
                if arg != "Tất cả":
                    live["on"] = True         # only what loads AFTER switching category
                    ok = page.evaluate("""(n) => { const el = [...document.querySelectorAll('button,[role=tab],span,div')]
                        .find(e => e.children.length === 0 && e.innerText && e.innerText.trim() === n);
                        if (el) { el.click(); return true } return false }""", arg)
                    if not ok:
                        raise RuntimeError(f"Không thấy thể loại “{arg}” trên TikTok Explore (TikTok đổi giao diện?)")
                    page.wait_for_timeout(5000)
                _scroll(page, pages, 0.2, f"TikTok · {arg}")
            else:
                state.update(message=f"Tìm “{arg}” trên TikTok…", progress=0.05)
                page.goto(f"https://www.tiktok.com/search/video?q={quote(arg)}&lang=vi-VN",
                          wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(8000)
                if found:
                    _scroll(page, pages, 0.2, f"TikTok · “{arg}”")
            page.remove_listener("response", on)
        finally:
            close()
    feed = f"cat:{arg}" if kind == "cat" else f"search:{arg}"
    items = [x for x in (tt_item(a, feed) for a in found) if x]
    return items, list(dict.fromkeys(s for s in sugg if s))


def crawl(kind: str, arg: str, pages: int = 3):
    """Background thread: one TikTok category or keyword search."""
    from . import dtk_client
    from .sources import tiktok_web
    state.update(running=True, cancel=False, error="", message="Bắt đầu…", progress=0.0)
    try:
        s = config.load()
        items, sugg = [], []
        if kind == "search" and (arg.startswith("@") or "tiktok.com/@" in arg) and dtk_client.configured(s):
            try:
                items = _dtk_tiktok_search(arg, pages, s)
            except Exception:
                items = []
        if not items:
            items, sugg = _tiktok(kind, arg, pages)
        if not items and kind == "search" and dtk_client.configured(s):
            try:
                items = _dtk_tiktok_search(arg, pages, s)
            except Exception:
                pass
        d = load()
        if sugg:
            d["suggest"].pop(arg, None)           # newest last
            d["suggest"][arg] = sugg[:15]
            save(d)                               # suggestions are useful even when the search itself is blocked
        if not items:
            if kind == "search":
                logged = tiktok_web.status_from(tiktok_web._cookies()).get("logged_in") if not tiktok_web.busy() else None
                raise RuntimeError("TikTok không trả kết quả tìm kiếm" + (
                    "" if logged else " — khi chưa đăng nhập TikTok chặn tìm kiếm. Đăng nhập ở ⚙ Cài đặt → TikTok "
                                      "Việt Nam, hoặc bấm một thể loại / gợi ý bên dưới"))
            raise RuntimeError(f"Không lấy được video nào từ TikTok · {arg}")
        new = merge(d, items)
        save(d)
        state.update(message=f"Xong: {len(items)} video ({new} mới) từ TikTok · {arg}", progress=1.0)
    except Exception as e:
        state.update(error=str(e).splitlines()[0][:300], message="Lỗi")
    finally:
        state.update(running=False, finished=time.time())


def start(kind: str, arg: str, pages: int = 3):
    if state["running"]:
        raise RuntimeError("Đang tìm, đợi xong đã")
    if kind not in ("cat", "search") or not arg.strip():
        raise ValueError("Cần thể loại hoặc từ khoá")
    if kind == "cat" and arg not in TT_CATEGORIES:
        raise ValueError(f"Thể loại TikTok không có: {arg}")
    state["running"] = True
    threading.Thread(target=crawl, args=(kind, arg.strip(), max(1, min(pages, 10))), daemon=True).start()


# ------------------------------------------------------------------ Douyin (from the Trending library)
def douyin_items(category: str = "") -> list[dict]:
    from . import categories, trending
    nodes = categories.flatten(categories.load())
    out = []
    for it in trending.load().values():
        if it.get("hidden") or it.get("platform", "douyin") != "douyin" or it.get("feed") == "hot" or it.get("is_image"):
            continue
        if category and category not in categories.classify(it, nodes):
            continue
        # douyin.com/video/<id> opens to an empty page in a browser that isn't logged in to Douyin; the 精选 page
        # with modal_id plays it for anyone (and the downloader reads the id from either form)
        out.append({"key": f"douyin:{it['id']}", "platform": "douyin", "id": it["id"],
                    "url": f"https://www.douyin.com/jingxuan?modal_id={it['id']}",
                    "title": it.get("title", ""), "author": it.get("author", ""), "views": it.get("views") or 0,
                    "likes": it.get("likes") or 0, "comments": it.get("comments") or 0, "shares": it.get("shares") or 0,
                    "duration": it.get("duration") or 0, "created": it.get("created") or 0,
                    "cover": f"/trending/cover/{it['id']}"
                    if (trending.COVERS / f"{trending.cover_name(it['id'])}.jpg").exists() else it.get("cover_url", ""),
                    "job_id": it.get("job_id")})
    return out


def douyin_hot() -> list[dict]:
    from . import trending
    rows = [it for it in trending.load().values() if it.get("feed") == "hot" or "hot" in (it.get("feeds") or [])]
    rows.sort(key=lambda i: (i.get("rank") or 999, -(i.get("hot_value") or 0)))
    return [{"title": i.get("title", ""), "hot_value": i.get("hot_value") or 0, "url": i.get("url", ""),
             "rank": i.get("rank") or 0, "seen": i.get("crawled") or i.get("seen") or 0} for i in rows[:50]]


def search_local(q: str, items: list[dict]) -> list[dict]:
    words = [w for w in q.lower().split() if w]
    return [i for i in items if all(w in (i.get("title", "") + " " + i.get("author", "")).lower() for w in words)]
