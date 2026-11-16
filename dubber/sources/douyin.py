"""Douyin: hot board, featured feed, featured topic tabs, and (optional) keyword search.

Findings (2026-11, see docs/ROADMAP.md): topic tabs on /jingxuan (体育, 知识, 影视…) return topical feeds
without login; keyword search shows a captcha page when not logged in, so it is opt-in and may open a
window for the user to solve the captcha themselves.
"""
import json
import time
from urllib.parse import quote

from .base import NeedsUser, SourceAdapter

# tab label on douyin.com/jingxuan  ->  shown name
TABS = {"体育": "Thể thao", "知识": "Kiến thức", "公开课": "Bài giảng", "影视": "Phim ảnh", "游戏": "Game",
        "音乐": "Âm nhạc", "美食": "Ẩm thực", "旅行": "Du lịch", "动物": "Động vật", "汽车": "Ô tô",
        "二次元": "Anime", "小剧场": "Phim ngắn", "生活vlog": "Vlog", "亲子": "Gia đình", "三农": "Nông thôn",
        "美妆穿搭": "Làm đẹp"}
# 精选 has no 科技 (tech) tab (checked 2026-11-15: the 16 tabs above). A creator page's video list
# (/aweme/v1/web/aweme/post) answers 403 to the app's headless browser (Douyin bot protection, not worked
# around), so the Công nghệ topic is carved out of 知识 / 汽车 / 精选 by keywords instead.


def _collect(page, match):
    bucket = []

    def on(r):
        if match in r.url:
            try:
                bucket.append(json.loads(r.text()))
            except Exception:
                pass
    page.on("response", on)
    return bucket, lambda: page.remove_listener("response", on)


def _awemes(bucket, feed, item_from_aweme, walk):
    out, seen = [], set()
    for data in bucket:
        found = []
        walk(data, found)
        for a in found:
            aid = str(a["aweme_id"])
            if aid not in seen:
                seen.add(aid)
                it = item_from_aweme(a, feed.split(":")[0])
                it["feed"] = feed
                out.append(it)
    return out


class Douyin(SourceAdapter):
    id, label = "douyin", "Douyin"

    def feeds(self, s):
        return ([{"feed": "hot", "label": "热榜 – bảng hot"}, {"feed": "featured", "label": "精选 – nổi bật"}] +
                [{"feed": f"tab:{k}", "label": f"{k} – {v}"} for k, v in TABS.items()])

    def handles_url(self, url):
        return "douyin.com" in url

    def needs_browser(self, feed: str, s: dict) -> bool:
        from .. import dtk_client
        kind, _, _ = (feed or "").partition(":")
        if kind == "user" and dtk_client.configured(s):
            return False
        return True

    def discover(self, page, feed, pages, s, progress):
        from .. import dtk_client
        from ..trending import _item_from_aweme, _walk_awemes, hot_items
        kind, _, arg = feed.partition(":")
        if kind == "hot":
            if dtk_client.configured(s):
                try:
                    progress(0.2, "Đọc bảng 热榜 qua DTK…")
                    items = dtk_client.fetch_trending("douyin", "hot", s)
                    if items:
                        return items
                except Exception:
                    if page is None:
                        raise
            bucket, stop = _collect(page, "/hot/search/list")
            progress(0.1, "Đọc bảng 热榜…")
            page.goto("https://www.douyin.com/hot", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000)
            stop()
            return hot_items(bucket)
        if kind == "user":
            if not dtk_client.configured(s):
                raise NeedsUser("Quét kênh tác giả Douyin (user:…) cần bật DTK MCP Server trong ⚙ Cài đặt "
                                "(trình duyệt ẩn bị Douyin chặn 403 ở danh sách video kênh).")
            out, seen, cursor = [], set(), None
            for i in range(max(pages, 1)):
                if i > 0 and progress(0.1 + 0.7 * (i + 1) / pages, f"Cuộn trang {i + 1}/{pages}…") is False:
                    break
                if i == 0:
                    progress(0.1, f"Đọc kênh {arg[:40]} qua DTK MCP…")
                page_res = dtk_client.list_user_posts("douyin", arg, s, cursor=cursor, count=20)
                for a in page_res.get("items") or []:
                    aid = str(a.get("aweme_id") or "")
                    if aid and aid not in seen:
                        seen.add(aid)
                        it = _item_from_aweme(a, "user", {"feed": feed})
                        out.append(it)
                cursor = page_res.get("cursor")
                if not page_res.get("has_more") or not cursor:
                    break
            return out
        if kind == "featured":
            bucket, stop = _collect(page, "/module/feed")
            progress(0.1, "Đọc feed 精选…")
            page.goto("https://www.douyin.com/jingxuan", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000)
            self._scroll(page, pages, progress)
            stop()
            return _awemes(bucket, feed, _item_from_aweme, _walk_awemes)
        if kind == "tab":
            page.goto("https://www.douyin.com/jingxuan", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)
            bucket, stop = _collect(page, "/module/feed")      # only what loads AFTER switching tab
            progress(0.1, f"Mở tab {arg}…")
            clicked = page.evaluate("""(n) => {
                const els = [...document.querySelectorAll('span,div,a,li')]
                  .filter(e => e.children.length === 0 && e.innerText && e.innerText.trim() === n);
                for (const e of els) { const r = e.getBoundingClientRect();
                  if (r.width > 0 && r.top < 300) { e.click(); return true; } }
                return false; }""", arg)
            if not clicked:
                stop()
                raise NeedsUser(f"Không thấy tab '{arg}' trên trang 精选 (Douyin đổi giao diện hoặc đang bắt xác minh)")
            page.wait_for_timeout(5000)
            self._scroll(page, pages, progress)
            stop()
            return _awemes(bucket, feed, _item_from_aweme, _walk_awemes)
        if kind == "search":
            bucket, stop = _collect(page, "/search/")
            progress(0.1, f"Tìm '{arg}'…")
            page.goto(f"https://www.douyin.com/search/{quote(arg)}?type=video", wait_until="domcontentloaded",
                      timeout=45000)
            page.wait_for_timeout(6000)
            if "验证码" in (page.title() or ""):
                stop()
                raise NeedsUser("Douyin chặn tìm kiếm từ khoá khi chưa đăng nhập (DTK v5 không có API search cho Douyin, chỉ hỗ trợ Quét kênh Douyin và Bảng Hot). Để tìm từ khoá: vào ⚙ Cài đặt → 🔑 Đăng nhập Douyin, hoặc chọn Chủ đề bên trái / dán link kênh Douyin.")
            self._scroll(page, pages, progress)
            stop()
            return _awemes(bucket, feed, _item_from_aweme, _walk_awemes)
        raise ValueError(f"Feed Douyin không hợp lệ: {feed}")

    @staticmethod
    def _scroll(page, pages, progress):
        for i in range(max(pages - 1, 0)):
            if progress(0.1 + 0.7 * (i + 1) / pages, f"Cuộn trang {i + 2}/{pages}…") is False:
                break          # Stop pressed: return what was collected so far
            page.mouse.wheel(0, 5000)
            page.wait_for_timeout(3500)       # gentle pace: one page every few seconds
        time.sleep(0)
