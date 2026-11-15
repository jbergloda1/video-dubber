"""TikTok (Vietnam) login for discovering hot videos: the app's own browser profile, separate from Douyin/Facebook.

You log in yourself in a visible window (QR code from the TikTok app on your phone is the easiest); the app only
keeps that browser profile. It never sees or types your password and never works around TikTok's login walls,
captchas or limits. The same window also opens TikTok Creative Center, where logging in (TikTok One, free) shows
the full trending rankings instead of the first few rows.

This is NOT the TikTok posting connection (official OAuth, Settings → TikTok): that one uploads videos.
"""
import threading
from pathlib import Path

from .. import config

PROFILE = config.DATA / "tiktok_profile"
_lock = threading.Lock()
login_state = {"open": False, "error": ""}
LOGIN_URL = "https://www.tiktok.com/login?lang=vi-VN"
CREATIVE_URL = "https://ads.tiktok.com/creative/creativeCenter/trends/video?region=VN&period=7"


def busy() -> bool:
    return _lock.locked()


def open_login_window():
    """Visible window with the app's TikTok profile; you log in yourself, then close it."""
    from playwright.sync_api import sync_playwright
    from ..downloader import _launch
    if not _lock.acquire(blocking=False):
        login_state["error"] = "Trình duyệt TikTok của app đang được dùng"
        return
    login_state.update(open=True, error="")
    try:
        with sync_playwright() as pw:
            ctx = _launch(pw, headless=False, profile=PROFILE, locale="vi-VN")
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            try:                    # second tab: Creative Center (log in there too for the full rankings)
                ctx.new_page().goto(CREATIVE_URL, wait_until="domcontentloaded", timeout=60000)
                page.bring_to_front()
            except Exception:
                pass
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


def _cookies() -> list[dict]:
    from playwright.sync_api import sync_playwright
    from ..downloader import _launch
    if not PROFILE.exists():
        return []
    if not _lock.acquire(timeout=5):
        raise RuntimeError("Trình duyệt TikTok của app đang được dùng")
    try:
        with sync_playwright() as pw:
            ctx = _launch(pw, headless=True, profile=PROFILE, locale="vi-VN")
            cookies = [c for c in ctx.cookies() if "tiktok.com" in c.get("domain", "")]
            ctx.close()
        return cookies
    finally:
        _lock.release()


def status_from(cookies: list[dict]) -> dict:
    """tiktok.com: a 'sessionid' cookie = logged in. Creative Center (ads.tiktok.com) keeps its own session."""
    def has(domain_part, names):
        return any(c["name"] in names and c.get("value") and domain_part in c.get("domain", "") for c in cookies)
    return {"logged_in": has("tiktok.com", ("sessionid", "sessionid_ss")),
            "creative": has("ads.tiktok.com", ("sessionid_ads", "sso_uid_tt_ads", "sid_ucp_v1_ads", "csrftoken_ads"))}


def status() -> dict:
    st = {"open": login_state["open"], "error": login_state.get("error", "")}
    if not st["open"]:
        try:
            st.update(status_from(_cookies()))
        except Exception as e:
            st["logged_in"], st["error"] = None, str(e).splitlines()[0]
    return st


def export_cookies(dest: Path) -> bool:
    """Your own TikTok session as a Netscape cookies.txt (for yt-dlp). False when not logged in."""
    cookies = _cookies()
    if not status_from(cookies)["logged_in"]:
        return False
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        dom = c["domain"]
        exp = int(c.get("expires") or 0)
        lines.append("\t".join([dom, "TRUE" if dom.startswith(".") else "FALSE", c.get("path", "/"),
                                "TRUE" if c.get("secure") else "FALSE", str(exp if exp > 0 else 0),
                                c["name"], c["value"]]))
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True
