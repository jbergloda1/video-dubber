"""Categories (classification, inheritance, validation) and crawl isolation between sources/feeds."""
import contextlib
import json

import pytest

from dubber import categories, trending

CATS = [
    {"id": "sports", "name": "Thể thao", "sources": {"douyin": {"feeds": ["tab:体育"]}},
     "children": [{"id": "football", "name": "Bóng đá", "match": ["足球", "欧冠"]},
                  {"id": "tennis", "name": "Quần vợt", "match": ["网球"]}]},
    {"id": "food", "name": "Ẩm thực", "sources": {"douyin": {"feeds": ["tab:美食"]}}},
]


def item(title, feeds=(), platform="douyin"):
    return {"id": title, "title": title, "feeds": list(feeds), "platform": platform}


def test_flatten_inherits_feeds():
    nodes = {n["id"]: n for n in categories.flatten(CATS)}
    assert nodes["football"]["feeds"] == {"douyin": ["tab:体育"]}
    assert nodes["football"]["parent"] == "sports" and nodes["football"]["depth"] == 1
    assert categories.crawl_targets("tennis", list(nodes.values())) == [("douyin", "tab:体育")]


def test_classify_keyword_from_any_feed_and_parent_rollup():
    nodes = categories.flatten(CATS)
    # a football video found in the generic featured feed still lands in Football (and Sports)
    assert set(categories.classify(item("欧冠决赛 绝杀", ["featured"]), nodes)) == {"football", "sports"}
    # a sports-tab video without football/tennis keywords is only in Sports
    assert categories.classify(item("马拉松", ["tab:体育"]), nodes) == ["sports"]
    assert categories.classify(item("红烧肉", ["tab:美食"]), nodes) == ["food"]
    assert categories.classify(item("随便", ["featured"]), nodes) == []


def test_default_categories_valid_and_unique():
    raw = json.loads(categories.DEFAULT.read_text(encoding="utf-8"))
    categories.validate(raw["categories"])
    nodes = categories.flatten(raw["categories"])
    assert {"football", "tennis", "basketball", "world_history", "chinese_history", "vietnam_history",
            "gold", "stocks", "ai", "documentary", "tech"} <= {n["id"] for n in nodes}


def test_validate_rejects_duplicates():
    with pytest.raises(ValueError):
        categories.validate([{"id": "a", "name": "A"}, {"id": "a", "name": "B"}])


def test_merge_keeps_user_state_and_unions_feeds():
    items = {}
    trending._merge(items, {"id": "1", "source": "tab", "feed": "tab:体育", "title": "x", "likes": 5})
    items["1"].update(status="queued", job_id="J1", hidden=True)
    trending._merge(items, {"id": "1", "source": "featured", "feed": "featured", "title": "", "likes": 0})
    it = items["1"]
    assert it["feeds"] == ["featured", "tab:体育"] and it["title"] == "x" and it["likes"] == 5
    assert (it["status"], it["job_id"], it["hidden"]) == ("queued", "J1", True)


def test_one_failing_feed_does_not_stop_others(monkeypatch, tmp_path):
    from dubber import sources
    from dubber.sources.base import SourceAdapter

    class Bad(SourceAdapter):
        id, label = "bad", "Bad"

        def discover(self, page, feed, pages, s, progress):
            raise RuntimeError("blocked")

    class Good(SourceAdapter):
        id, label = "good", "Good"

        def discover(self, page, feed, pages, s, progress):
            return [{"id": "good:1", "source": "x", "feed": feed, "title": "ok", "cover_url": None}]

    monkeypatch.setattr(sources, "REGISTRY", {"bad": Bad(), "good": Good()})
    monkeypatch.setattr(trending, "STORE", tmp_path / "t.json")
    monkeypatch.setattr(trending, "CRAWL_STATE", tmp_path / "crawl_state.json")
    monkeypatch.setattr(trending, "adapter_context", lambda pw, a, s: contextlib.nullcontext(None))
    import playwright.sync_api as psa
    monkeypatch.setattr(psa, "sync_playwright", lambda: contextlib.nullcontext(None))
    trending.crawl([("bad", "x"), ("good", "y")], pages=1, s={"crawl_gap_s": 0})
    assert "good:1" in trending.load()
    assert trending.state["errors"] and "Bad" in trending.state["errors"][0]
    assert trending.state["error"] == ""        # partial success is not an error


def test_cover_name_safe_for_prefixed_ids():
    assert trending.cover_name("fb:123") == "fb_123" and trending.cover_name("7706") == "7706"


def _fake_crawl_env(monkeypatch, tmp_path, adapters):
    from dubber import sources
    monkeypatch.setattr(sources, "REGISTRY", adapters)
    monkeypatch.setattr(trending, "STORE", tmp_path / "t.json")
    monkeypatch.setattr(trending, "CRAWL_STATE", tmp_path / "crawl_state.json")
    monkeypatch.setattr(trending, "adapter_context", lambda pw, a, s: contextlib.nullcontext(None))
    import playwright.sync_api as psa
    monkeypatch.setattr(psa, "sync_playwright", lambda: contextlib.nullcontext(None))


def test_load_more_goes_deeper_than_last_time(monkeypatch, tmp_path):
    from dubber.sources.base import SourceAdapter
    seen_pages = []

    class A(SourceAdapter):
        id, label = "a", "A"

        def discover(self, page, feed, pages, s, progress):
            seen_pages.append(pages)
            return [{"id": f"a:{i}", "source": "x", "feed": feed, "title": str(i)} for i in range(pages)]

    _fake_crawl_env(monkeypatch, tmp_path, {"a": A()})
    trending.crawl([("a", "f")], pages=3, s={"crawl_gap_s": 0})
    trending.crawl([("a", "f")], pages=3, s={"crawl_gap_s": 0}, more=True)
    trending.crawl([("a", "f")], pages=3, s={"crawl_gap_s": 0})          # a normal crawl starts from the top again
    assert seen_pages == [3, 6, 3]
    assert trending.load_crawl_state()["a|f"]["pages"] == 3
    assert len(trending.load()) == 6                                      # deduplicated across runs


def test_cancel_keeps_finished_feeds(monkeypatch, tmp_path):
    from dubber.sources.base import SourceAdapter

    class A(SourceAdapter):
        id, label = "a", "A"

        def discover(self, page, feed, pages, s, progress):
            if feed == "second":
                trending.cancel()                 # user presses Stop while this feed is scrolling
                assert progress(0.5, "scrolling") is False      # adapter is told to stop scrolling
            return [{"id": f"{feed}:1", "source": "x", "feed": feed, "title": feed}]

    _fake_crawl_env(monkeypatch, tmp_path, {"a": A()})
    trending.crawl([("a", "first"), ("a", "second"), ("a", "third")], pages=1, s={"crawl_gap_s": 0})
    assert set(trending.load()) == {"first:1", "second:1"}     # partial feed kept, third never started
    assert trending.state["running"] is False and "dừng" in trending.state["message"].lower()


def test_platform_gap_waits(monkeypatch):
    import time
    from dubber import downloader

    class J:
        def log(self, *a, **k): pass
        def step(self, *a, **k): pass
        def check_cancel(self): pass
    downloader._last_platform.pop("testplat", None)
    downloader.platform_gap(J(), "testplat", 1.2)
    t = time.time()
    downloader.platform_gap(J(), "testplat", 1.2)
    assert time.time() - t >= 0.9



def test_stopped_crawl_records_pages_actually_loaded(monkeypatch, tmp_path):
    from dubber.sources.base import SourceAdapter

    class A(SourceAdapter):
        id, label = "a", "A"

        def discover(self, page, feed, pages, s, progress):
            for i in range(pages - 1):
                if i == 2:
                    trending.cancel()
                if progress(0.5, f"Cuộn trang {i + 2}/{pages}…") is False:
                    break
            return [{"id": "a:1", "source": "x", "feed": feed, "title": "x"}]

    _fake_crawl_env(monkeypatch, tmp_path, {"a": A()})
    trending.crawl([("a", "f")], pages=8, s={"crawl_gap_s": 0})
    assert trending.load_crawl_state()["a|f"]["pages"] == 3      # loaded pages 1-3, stopped before page 4
