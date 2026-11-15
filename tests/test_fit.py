import json

from dubber import translate


class P:
    kind, id = "llm", "fake"

    def __init__(self, answer):
        self.answer, self.user = answer, ""

    def complete(self, job, system, user, schema, s):
        self.user = user
        return json.dumps({"items": [{"id": k, "tgt": v} for k, v in self.answer.items()]}, ensure_ascii=False)


def test_overlong_skips_hand_edited_and_tiny_lines():
    lines = [{"tgt": "Kê đồ kiểu thường thôi là phòng chật như hộp cá mòi", "need": 1.6},
             {"tgt": "Câu này vừa vặn thời lượng", "need": 1.0},
             {"tgt": "Câu bạn đã sửa tay rất dài dòng", "need": 1.8, "edited": True},
             {"tgt": "Ồ!", "need": 2.0}]
    assert translate.overlong(lines) == [0]


def test_shorten_uses_budget_and_only_accepts_shorter(job, monkeypatch):
    lines = [{"src": "常规布局挤得满满当当", "tgt": "Kê đồ kiểu thường thôi là phòng chật như hộp cá mòi", "need": 1.6},
             {"src": "大片空间白白浪费", "tgt": "Rất nhiều không gian bị lãng phí", "need": 1.3}]
    prov = P({0: "Kê kiểu thường là chật cứng", 1: "Rất nhiều không gian bị bỏ phí một cách vô ích"})
    monkeypatch.setattr(translate.orchestrator, "run", lambda job, s, task, what: (task(prov), prov.id))
    n = translate.shorten(job, lines, [0, 1], {"translate_style": "Bựa"})
    assert n == 1
    assert lines[0]["tgt"] == "Kê kiểu thường là chật cứng" and lines[0]["tgt_full"].startswith("Kê đồ kiểu thường")
    assert lines[1]["tgt"] == "Rất nhiều không gian bị lãng phí"      # longer answer rejected
    assert "TỐI ĐA: 8 âm tiết" in prov.user and "常规布局" in prov.user   # 12 syllables × 1.15 / 1.6 → 8


def test_machine_translation_provider_does_not_shorten(job, monkeypatch):
    class MT:
        kind, id = "mt", "google_free"
    lines = [{"src": "x", "tgt": "một hai ba bốn năm sáu", "need": 2.0}]
    monkeypatch.setattr(translate.orchestrator, "run", lambda job, s, task, what: (task(MT()), "google_free"))
    assert translate.shorten(job, lines, [0], {}) == 0 and lines[0]["tgt"] == "một hai ba bốn năm sáu"


def test_opening_line_keeps_hook_style_when_shortened(job, monkeypatch):
    lines = [{"src": "大胆一点卧室不买床", "tgt": "Không mua giường, phòng ngủ vẫn đỉnh nóc được sao?", "need": 1.5}]
    prov = P({0: "Không giường, phòng vẫn đỉnh?"})
    monkeypatch.setattr(translate.orchestrator, "run", lambda job, s, task, what: (task(prov), prov.id))
    translate.shorten(job, lines, [0], {"hook_intro": True})
    assert "CÂU MỞ ĐẦU GÂY TÒ MÒ" in prov.user


def test_shorten_cache_avoids_a_second_ai_call(job, monkeypatch):
    calls = []
    prov = P({0: "Kê kiểu thường là chật cứng"})
    def run(job, s, task, what):
        calls.append(1)
        return task(prov), prov.id
    monkeypatch.setattr(translate.orchestrator, "run", run)
    cache = {}
    mk = lambda: [{"src": "常规布局挤得满满当当", "tgt": "Kê đồ kiểu thường thôi là phòng chật như hộp cá mòi", "need": 1.6}]
    a = mk()
    assert translate.shorten(job, a, [0], {}, cache=cache) == 1 and len(calls) == 1
    b = mk()                                     # the same line again (e.g. after a re-translation)
    assert translate.shorten(job, b, [0], {}, cache=cache) == 1 and len(calls) == 1
    assert b[0]["tgt"] == "Kê kiểu thường là chật cứng"
