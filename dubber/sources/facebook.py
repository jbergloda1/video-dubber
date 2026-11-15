"""Facebook Reels: discover reels on a Page/profile's public Reels tab; download through yt-dlp.

- Public Page reels tabs load without login (tested 2026-11 on facebook.com/<page>/reels: ~10 reels with views
  + thumbnails); Facebook overlays a login prompt and may stop loading more when scrolling.
- Optional: you log in yourself in the app's own Facebook browser window (Settings), which lets the app see what
  your account can see. The app never bypasses login walls or restrictions; private/restricted reels fail clearly.
- There is no public "trending Reels" list, so discovery is per Page/profile URL that you provide
  (in categories.json as  "facebook": {"feeds": ["page:https://www.facebook.com/<page>"]}  or ad hoc in the UI).
"""
import re
import threading
from pathlib import Path

from .. import config
from .base import NeedsUser, SourceAdapter, SourceError

PROFILE = config.DATA / "facebook_profile"
_lock = threading.Lock()
login_state = {"open": False, "error": ""}
REEL_RE = re.compile(r"/reel/(\d{6,25})")


def parse_count(text: str) -> int:
    """'54K' -> 54000, '1.2M' -> 1200000, '1,234' -> 1234, '3,4 N' (vi) -> 3400, '5.4万' (zh) -> 54000."""
    t = (text or "").strip().upper().replace(",", ".").replace(" ", "")
    m = re.match(r"^([\d.]+)([KMBN万亿]|TR|TỶ)?", t)
    if not m:
        return 0
    num = m.group(1)
    if num.count(".") > 1 or (m.group(2) is None and re.fullmatch(r"\d{1,3}(\.\d{3})+", num)):
        num = num.replace(".", "")                     # thousands separators
    try:
        v = float(num)
    except ValueError:
        return 0
    mult = {"K": 1e3, "N": 1e3, "M": 1e6, "TR": 1e6, "B": 1e9, "TỶ": 1e9, "万": 1e4, "亿": 1e8}.get(m.group(2) or "", 1)
    return int(v * mult)


GENERIC_LABELS = re.compile(r"^(reels?( tile)?( preview)?|reels? 方块预览|thước phim|reel)$", re.I)


def reels_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if REEL_RE.search(url) or url.endswith("/reels") or "/reels/" in url:
        return url
    return url + "/reels"


def items_from_links(links, feed):
    out, seen = [], set()
    for l in links:
        m = REEL_RE.search(l.get("href") or "")
        if not m or m.group(1) in seen:
            continue
        rid = m.group(1)
        seen.add(rid)
        label = (l.get("label") or "").strip()
        if GENERIC_LABELS.match(label):
            label = ""                       # a generic "Reel tile preview" label is not a title
        txt = (l.get("text") or "").strip()
        first = txt.split("\n")[0] if txt else ""
        views = parse_count(first)
        caption = " ".join(x for x in txt.split("\n")[1:] if x.strip())[:120]
        page = l.get("page") or ""
        out.append({"id": f"fb:{rid}", "platform": "facebook", "url": f"https://www.facebook.com/reel/{rid}",
                    "title": label or caption or (first if not views else "") or f"Reel · {page or rid}",
                    "author": page, "generic_title": not (label or caption),
                    "duration": 0, "likes": 0, "views": views, "comments": 0, "shares": 0, "created": 0,
                    "cover_url": l.get("img") or None, "is_image": False, "source": "facebook", "feed": feed})
    return out


class FacebookReels(SourceAdapter):
    id, label = "facebook", "Facebook Reels"
    login_hint = "Trang Reels công khai xem được khi chưa đăng nhập; đăng nhập (tuỳ chọn) để xem nhiều hơn."

    def feeds(self, s):
        return [{"feed": "page:", "label": "Reels của một trang/Page (nhập URL)…"}]

    def handles_url(self, url):
        return "facebook.com" in url or "fb.watch" in url

    def open_context(self, pw, s):
        """Own browser profile (your Facebook login, if any, lives here), never shared with Douyin."""
        from ..downloader import _launch
        if not _lock.acquire(timeout=2):
            raise SourceError("Cửa sổ đăng nhập Facebook đang mở — đóng nó lại rồi thử lại")
        try:
            ctx = _launch(pw, headless=True, profile=PROFILE, locale="en-US")
        except Exception:
            _lock.release()
            raise

        def close():
            try:
                ctx.close()
            finally:
                _lock.release()
        return ctx, close

    def discover(self, page, feed, pages, s, progress):
        kind, _, arg = feed.partition(":")
        if kind != "page" or not arg.startswith("http"):
            raise SourceError("Feed Facebook cần dạng page:https://www.facebook.com/<trang>")
        url = reels_url(arg)
        progress(0.1, f"Mở {url}…")
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(6000)
        links = []
        for i in range(max(pages, 1)):
            links += page.evaluate("""() => [...document.querySelectorAll('a[href*="/reel/"]')].map(a => ({
                href: a.href, text: a.innerText || '', label: a.getAttribute('aria-label') || '',
                img: (a.querySelector('img') || {}).src || ''}))""")
            if i + 1 < pages:
                if progress(0.1 + 0.7 * (i + 1) / pages, f"Cuộn trang {i + 2}/{pages}…") is False:
                    break      # Stop pressed: keep what was collected
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(3500)
        page_name = (page.title() or "").split("|")[0].replace("Reels", "").strip()
        for l in links:
            l["page"] = page_name
        items = items_from_links(links, feed)
        if not items:
            login_wall = page.evaluate("() => !!document.querySelector('input[name=email], form[action*=login]')")
            if login_wall:
                raise NeedsUser("Facebook yêu cầu đăng nhập để xem trang này (⚙ Cài đặt → Đăng nhập Facebook), "
                                "hoặc trang không có Reels công khai")
            raise SourceError("Không thấy reel nào ở trang này")
        return items


def open_login_window():
    """Visible window on facebook.com with the app's Facebook profile; you log in yourself, then close it."""
    from playwright.sync_api import sync_playwright
    from ..downloader import _launch
    if not _lock.acquire(blocking=False):
        raise RuntimeError("Trình duyệt Facebook của app đang được dùng")
    login_state.update(open=True, error="")
    try:
        with sync_playwright() as pw:
            ctx = _launch(pw, headless=False, profile=PROFILE, locale="en-US")
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.facebook.com/login", wait_until="domcontentloaded", timeout=60000)
            while ctx.pages:
                try:
                    ctx.pages[0].wait_for_timeout(1000)
                except Exception:
                    break
            try:
                ctx.close()
            except Exception:
                pass
    except Exception as e:
        login_state["error"] = str(e).splitlines()[0]
    finally:
        login_state["open"] = False
        _lock.release()


def export_cookies(dest: Path) -> bool:
    """Write the app profile's facebook.com cookies (your own login, if any) as Netscape cookies.txt for yt-dlp."""
    from playwright.sync_api import sync_playwright
    from ..downloader import _launch
    if not PROFILE.exists() or not _lock.acquire(timeout=5):
        return False
    try:
        with sync_playwright() as pw:
            ctx = _launch(pw, headless=True, profile=PROFILE, locale="en-US")
            cookies = [c for c in ctx.cookies() if "facebook.com" in c.get("domain", "")]
            ctx.close()
    finally:
        _lock.release()
    if not any(c["name"] == "c_user" for c in cookies):       # not logged in: nothing useful to pass
        return False
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        dom = c["domain"]
        lines.append("\t".join([dom, "TRUE" if dom.startswith(".") else "FALSE", c.get("path", "/"),
                                "TRUE" if c.get("secure") else "FALSE", str(int(c.get("expires") or 0)) if (c.get("expires") or 0) > 0 else "0",
                                c["name"], c["value"]]))
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True
