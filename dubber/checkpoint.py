"""Step checkpoints: a step is skipped when its INPUTS did not change since it last ran.

Each step that is worth skipping records, in checkpoints.json:
  key   hash of everything that decides its result — input file contents, the settings it reads, and the source
        code of the module that does the work (a code change makes the old result stale)
  out   fingerprints of the files it produced
  detail / data   what to show when it is reused, and small results to re-apply (e.g. speaker labels)

Re-running a job from step X always redoes X ("force"); every later step is re-checked and reused when its inputs
are still the same — so re-running "Tạo giọng đọc" no longer re-translates, re-OCRs or re-renders what did not change.

Fingerprints: small files (≤ 2 MB) by full content; big media by size + three 256 KB samples (start, middle, end),
so a 1 GB video is fingerprinted in milliseconds and a regenerated-but-identical file still matches.
"""
import hashlib
import json
import threading
import time
from pathlib import Path

FILE = "checkpoints.json"
_SAMPLE = 256 * 1024
_SMALL = 2 * 1024 * 1024
_code_cache = {}
_lock = threading.Lock()


def fp(path) -> str | None:
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    h = hashlib.sha1()
    with open(p, "rb") as f:
        if st.st_size <= _SMALL:
            h.update(f.read())
        else:
            h.update(str(st.st_size).encode())
            for pos in (0, st.st_size // 2, max(0, st.st_size - _SAMPLE)):
                f.seek(pos)
                h.update(f.read(_SAMPLE))
    return h.hexdigest()[:20]


def code(*modules) -> str:
    """Hash of the source of dubber modules (e.g. code("translate", "styles"))."""
    here = Path(__file__).parent
    out = []
    for m in modules:
        if m not in _code_cache:
            try:
                _code_cache[m] = hashlib.sha1((here / f"{m}.py").read_bytes()).hexdigest()[:12]
            except OSError:
                _code_cache[m] = "?"
        out.append(_code_cache[m])
    return "-".join(out)


def key(*parts) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def pick(s, *names):
    """The settings a step reads (only these invalidate it)."""
    return {n: s.get(n) for n in names}


class Store:
    def __init__(self, d: Path, force=()):
        self.d = Path(d)
        self.path = self.d / FILE
        self.force = set(force or ())
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except Exception:
            self.data = {}

    def hit(self, step, k, outputs=()):
        """The stored record if `step` can be reused (same key, outputs still there and unchanged), else None."""
        if step in self.force:
            return None
        rec = self.data.get(step)
        if not rec or rec.get("key") != k:
            return None
        for o in outputs:
            if fp(self.d / o) != rec.get("out", {}).get(o):
                return None
        return rec

    def save(self, step, k, outputs=(), detail="", data=None):
        rec = {"key": k, "out": {o: fp(self.d / o) for o in outputs}, "detail": detail or "",
               "t": time.time(), **({"data": data} if data is not None else {})}
        with _lock:                 # the picture steps save from their own thread
            self.data[step] = rec
            self._write()

    def drop(self, step):
        with _lock:
            if self.data.pop(step, None) is not None:
                self._write()

    def _write(self):               # caller holds _lock
        if True:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
