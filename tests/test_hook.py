import json

from dubber import translate


class P:
    """Fake LLM provider: returns a fixed JSON answer and records the prompts it got."""
    kind, id = "llm", "fake"

    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def complete(self, job, system, user, schema, s):
        self.prompts.append(system)
        return json.dumps(self.answer, ensure_ascii=False)


def _asr(start):
    return {"lines": [{"start": start, "end": start + 2.0, "text": "大胆一点卧室不买床"}],
            "words": [{"w": c, "s": start + i * 0.2, "e": start + i * 0.2 + 0.2} for i, c in enumerate("大胆一点卧室不买床")]}


def _run(job, monkeypatch, asr, answer, hook=True):
    prov = P(answer)
    monkeypatch.setattr(translate.orchestrator, "run", lambda job, s, task, what: (task(prov), prov.id))
    s = {"target_lang": "vi", "translate_style": "Tự nhiên", "hook_intro": hook}
    out, _ = translate.translate(job, asr, s, "tiêu đề")
    return out, prov.prompts[0]


def test_speech_at_start_rewrites_first_lines_only(job, monkeypatch):
    out, prompt = _run(job, monkeypatch, _asr(0.0),
                       {"lines": [{"src": "大胆一点卧室不买床", "tgt": "Phòng ngủ KHÔNG giường? Xem hết mới hiểu!"}]})
    assert "MỞ ĐẦU CHẤN ĐỘNG" in prompt and '"hook"' not in prompt     # no silent gap → no separate hook line
    assert len(out) == 1 and not out[0].get("hook")


def test_silent_intro_gets_its_own_hook_line(job, monkeypatch):
    out, prompt = _run(job, monkeypatch, _asr(3.0),
                       {"hook": "Một cái giường biến mất, căn phòng rộng gấp đôi?",
                        "lines": [{"src": "大胆一点卧室不买床", "tgt": "Mạnh dạn lên, phòng ngủ khỏi mua giường"}]})
    assert '"hook"' in prompt
    assert out[0]["hook"] and out[0]["start"] < 1 and out[0]["end"] <= 3.0
    assert out[1]["tgt"].startswith("Mạnh dạn")


def test_hook_can_be_turned_off(job, monkeypatch):
    out, prompt = _run(job, monkeypatch, _asr(3.0),
                       {"hook": "bỏ qua", "lines": [{"src": "大胆一点卧室不买床", "tgt": "x"}]}, hook=False)
    assert "MỞ ĐẦU CHẤN ĐỘNG" not in prompt and len(out) == 1


def test_add_hook_ignores_empty_or_short_gap():
    lines = [{"tgt": "a", "start": 0.5, "end": 1}]
    assert translate.add_hook(lines, "Câu mở", 0.5) == lines
    assert translate.add_hook(lines, "", 3.0) == lines
    assert translate.add_hook(lines, "[Không rõ lời]", 3.0) == lines
