import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FakeJob:
    """Minimal stand-in for dubber.jobs.Job."""
    proc = None

    def __init__(self):
        self.logs, self.cancelled = [], False

    def log(self, msg, level="info"):
        self.logs.append((level, str(msg)))

    def step(self, *a, **k):
        pass

    def check_cancel(self):
        if self.cancelled:
            from dubber.jobs import Cancelled
            raise Cancelled()


@pytest.fixture
def job():
    return FakeJob()
