"""🚀 Link hot: TikTok items → records, top-N by views / rising, merge, keyword filter."""
import time

from dubber import hot


def _a(i, views, created, uid="u", dur=30, desc="x"):
    return {"id": str(i), "desc": desc, "createTime": created, "author": {"uniqueId": uid, "nickname": uid.upper()},
            "stats": {"playCount": views, "diggCount": views // 10, "commentCount": 1, "shareCount": 2},
            "video": {"duration": dur, "cover": f"https://c/{i}.jpg"}}


def test_tt_item():
    it = hot.tt_item(_a(7705, 12300000, 1793960282, uid="ducduy_official"), "cat:Công nghệ")
    assert it["url"] == "https://www.tiktok.com/@ducduy_official/video/7705"
    assert it["views"] == 12300000 and it["likes"] == 1230000 and it["feeds"] == ["cat:Công nghệ"]
    assert hot.tt_item({"id": "1", "imagePost": {"images": []}}, "x") is None       # photo post: can't dub


def test_top_default_ten_by_views_and_rising():
    now = time.time()
    items = [hot.tt_item(_a(i, (i + 1) * 1000, now - (i + 1) * 3600 * 24), "cat:A") for i in range(30)]
    t = hot.top(items)
    assert len(t) == 10 and [x["views"] for x in t] == sorted([x["views"] for x in t], reverse=True)
    assert t[0]["views"] == 30000
    fresh = hot.tt_item(_a(99, 5000, now - 3600), "cat:A")                # 5k views in 1 hour beats 30k in 30 days
    assert hot.top(items + [fresh], "rising")[0]["id"] == "99"
    assert all(x["duration"] <= 20 for x in hot.top(items + [hot.tt_item(_a(98, 1, now, dur=15), "cat:A")], max_dur=20))


def test_merge_keeps_feeds_and_updates_counts():
    d = {"items": {}, "suggest": {}}
    assert hot.merge(d, [hot.tt_item(_a(1, 100, 1), "cat:A")]) == 1
    assert hot.merge(d, [hot.tt_item(_a(1, 900, 1), "search:x")]) == 0
    it = d["items"]["tiktok:1"]
    assert it["views"] == 900 and it["feeds"] == ["cat:A", "search:x"]


def test_search_local():
    items = [hot.tt_item(_a(1, 1, 1, desc="Review điện thoại mới"), "f"), hot.tt_item(_a(2, 1, 1, desc="Mẹo bếp"), "f")]
    assert [i["id"] for i in hot.search_local("điện thoại", items)] == ["1"]


def test_start_validates():
    import pytest
    with pytest.raises(ValueError):
        hot.start("cat", "Không có", 1)
    with pytest.raises(ValueError):
        hot.start("search", "  ", 1)


def test_douyin_links_open_in_any_browser(monkeypatch):
    from dubber import trending
    monkeypatch.setattr(trending, "load", lambda: {"7600": {"id": "7600", "url": "https://www.douyin.com/video/7600",
                                                            "title": "t", "feed": "tab:体育", "feeds": ["tab:体育"]}})
    it = hot.douyin_items()[0]
    assert it["url"] == "https://www.douyin.com/jingxuan?modal_id=7600"
    import re
    from dubber import downloader
    src = open(downloader.__file__, encoding="utf-8").read()
    assert "modal_id=(" in src          # the downloader still finds the video id in this form
