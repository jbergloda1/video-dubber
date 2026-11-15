"""Quick manual test: python -m tests.try_download <url>"""
import sys
import tempfile
from pathlib import Path

from dubber import config, downloader


class FakeJob:
    proc = None
    def log(self, m, level="info"): print(f"[{level}] {m}")
    def step(self, *a, **k):
        if k.get("detail"): print("  ", k["detail"], end="\r")
    def check_cancel(self): pass


out = Path(tempfile.mkdtemp()) / "source.mp4"
info = downloader.download(FakeJob(), sys.argv[1], out, config.load())
print("\nOK", info, out, out.stat().st_size)
