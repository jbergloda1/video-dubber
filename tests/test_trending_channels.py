"""Douyin 精选 has no 科技 tab: the Công nghệ topic is carved out of broad feeds by keywords; a topic's own
dedicated feeds (a Facebook page, a search) keep all their videos."""
import json
import time

from dubber import categories, trending


def _nodes():
    cats = json.loads(categories.DEFAULT.read_text(encoding="utf-8"))["categories"]
    return categories.flatten(cats)


def test_tech_topic_keywords():
    nodes = _nodes()
    tech = next(n for n in nodes if n["id"] == "tech")
    assert tech["feeds"]["douyin"] and tech["match"]
    assert "tech" not in categories.classify({"title": "遛狗 汪警小米", "feeds": ["tab:知识"]}, nodes)
    assert "tech" not in categories.classify({"title": "战术拆解：法甲最优雅的团队配合 #足球", "feeds": ["tab:体育"]}, nodes)
    assert "tech" in categories.classify({"title": "iPhone Duo拆机", "feeds": ["tab:知识"]}, nodes)
    assert "tech" in categories.classify({"title": "小米汽车 新能源 智驾测试", "feeds": ["tab:汽车"]}, nodes)


def test_dedicated_feed_keeps_all_its_videos():
    nodes = categories.flatten([{"id": "x", "name": "X", "match": ["足球"],
                                 "sources": {"facebook": {"feeds": ["page:https://www.facebook.com/espn"]}}}])
    it = {"title": "Big game tonight", "feeds": ["page:https://www.facebook.com/espn"], "platform": "facebook"}
    assert "x" in categories.classify(it, nodes)


def test_save_retries_while_file_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(trending, "STORE", tmp_path / "trending.json")
    calls = {"n": 0}
    real = type(tmp_path).replace

    def flaky(self, target):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied")
        return real(self, target)
    monkeypatch.setattr(type(tmp_path), "replace", flaky)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    trending.save({"a": {"id": "a"}})
    assert json.loads((tmp_path / "trending.json").read_text(encoding="utf-8")) == {"a": {"id": "a"}}
