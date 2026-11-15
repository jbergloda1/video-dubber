"""Manual live check of real providers (costs a tiny amount of quota): python -m tests.try_providers codex_cli local_nllb"""
import json
import sys
import time

from dubber import config, providers
from dubber.providers.base import ProviderError


class J:
    proc = None
    def log(self, m, level="info"): print(f"  [{level}] {m}")
    def step(self, *a, **k): pass
    def check_cancel(self): pass


s = config.load()
SCHEMA = {"type": "object", "required": ["tgt"], "properties": {"tgt": {"type": "string"}}}
for pid in sys.argv[1:]:
    p = providers.REGISTRY[pid]
    t = time.time()
    ok, why = p.configured(s)
    if ok:
        ok, why = p.probe(s)
    if not ok:
        print(f"{pid}: không dùng được — {why}")
        continue
    try:
        if p.kind == "mt":
            out = p.translate_list(J(), ["上半场，多纳鲁马失误送奥利塞首开纪录。", "如果人类消失了，地球会怎么样？"], "vi", s)
        else:
            out = p.complete(J(), "Dịch sang tiếng Việt. Trả về JSON {\"tgt\": \"...\"}.", "如果人类消失了，地球会怎么样？", SCHEMA, s)
        print(f"{pid}: OK {time.time() - t:.1f}s -> {out if isinstance(out, list) else out.strip()[:200]}")
    except ProviderError as e:
        print(f"{pid}: {e.kind} — {str(e)[:200]}  ({time.time() - t:.1f}s)")
