import json
import re
import threading
import time

from dubber import translate
from dubber.providers import orchestrator


class Job:
    def log(self, *a, **k): pass
    def step(self, *a, **k): pass
    def check_cancel(self): pass


class FakeLLM:
    kind, id = "llm", "fake"

    def __init__(self):
        self.calls, self.lock, self.live, self.peak = [], threading.Lock(), 0, 0

    def complete(self, job, sysmsg, user, schema, s):
        with self.lock:
            self.live += 1
            self.peak = max(self.peak, self.live)
        time.sleep(0.05)
        src = re.findall(r"^\[[\d.]+s dur=[\d.]+\] (.+)$", user, re.M)
        with self.lock:
            self.calls.append(user)
            self.live -= 1
        return json.dumps({"hook": "MỞ ĐẦU", "lines": [{"src": x.strip(), "tgt": "vi:" + x.strip()} for x in src]},
                          ensure_ascii=False)


def _asr(n):
    lines = [{"start": i * 2.0, "end": i * 2.0 + 1.5, "text": f"句子{i:02d}"} for i in range(n)]
    words = [{"w": l["text"], "s": l["start"], "e": l["end"]} for l in lines]
    return {"lines": lines, "words": words}


def test_blocks_after_the_first_run_side_by_side_and_keep_order(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(orchestrator, "run", lambda job, s, task, what: (task(llm), "fake"))
    monkeypatch.setattr(translate, "CHUNK", 4)
    s = {"target_lang": "vi", "translate_style": "", "hook_intro": False, "translate_parallel": 3}
    out, _ = translate.translate(Job(), _asr(20), s)
    assert llm.peak >= 2                                   # blocks 2..5 went out together
    assert "句子00" in llm.calls[0]                         # block 1 first, alone
    later = [c for c in llm.calls[1:] if "句子04" in c.split("\n\n")[0]]
    assert later and "Câu gốc ngay trước khối này" in later[0] and "句子03" in later[0]
    tgts = [l["tgt"] for l in out]
    assert tgts == sorted(tgts)                            # assembled in the original order


def test_parallel_off_is_sequential(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(orchestrator, "run", lambda job, s, task, what: (task(llm), "fake"))
    monkeypatch.setattr(translate, "CHUNK", 4)
    s = {"target_lang": "vi", "translate_style": "", "hook_intro": False, "translate_parallel": 1}
    translate.translate(Job(), _asr(12), s)
    assert llm.peak == 1
