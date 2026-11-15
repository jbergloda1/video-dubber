import time

import pytest

from dubber import autopilot


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Fake library / trending / job queue + a private state file."""
    from dubber import categories, library, trending
    monkeypatch.setattr(autopilot, "STATE", tmp_path / "autopilot.json")
    now = time.time()
    lib = {"L1": {"id": "L1", "status": "done", "jobs": [], "duration": 120, "title": "kho 1", "created": 1,
                  "url": "https://v.douyin.com/a/"},
           "L2": {"id": "L2", "status": "done", "jobs": ["j0"], "duration": 120, "title": "đã làm", "created": 2},
           "L3": {"id": "L3", "status": "downloading", "jobs": [], "duration": 120, "title": "đang tải", "created": 3}}
    tr = {"T1": {"id": "T1", "url": "https://www.douyin.com/video/T1", "title": "hot", "duration": 60, "likes": 90000,
                 "created": now - 86400, "status": "new"},
          "T2": {"id": "T2", "url": "https://www.douyin.com/video/T2", "title": "ít like", "duration": 60,
                 "likes": 500, "created": now, "status": "new"},
          "T3": {"id": "T3", "url": "https://www.douyin.com/video/T3", "title": "quá dài", "duration": 4000,
                 "likes": 99999, "created": now, "status": "new"},
          "T4": {"id": "T4", "url": "https://www.douyin.com/video/T4", "title": "cũ", "duration": 60,
                 "likes": 99999, "created": now - 90 * 86400, "status": "new"},
          "T5": {"id": "T5", "url": "https://www.douyin.com/video/T5", "title": "hot 2", "duration": 60,
                 "likes": 200000, "created": now, "status": "new"}}
    monkeypatch.setattr(library, "load", lambda: lib)
    monkeypatch.setattr(library, "update", lambda iid, **kw: lib[iid].update(kw))
    monkeypatch.setattr(trending, "load", lambda: tr)
    monkeypatch.setattr(trending, "update", lambda vid, **kw: tr[vid].update(kw))
    monkeypatch.setattr(categories, "load", lambda: [])
    submitted = []

    class M:
        jobs = {}

        def submit(self, job, start_from="download"):
            submitted.append(job)
            self.jobs[job.id] = job
    m = M()
    import dubber.jobs as jobs
    monkeypatch.setattr(jobs, "get_manager", lambda: m)
    s = {**autopilot.DEFAULTS, "autopilot_min_likes": 10000, "autopilot_max_age_days": 30}
    return s, lib, tr, submitted


def test_candidates_filter_and_order(world):
    s, *_ = world
    c = autopilot.candidates(s, job_urls=set())
    assert [x["key"] for x in c] == ["library:L1", "trending:T5", "trending:T1"]


def test_queue_respects_quota_and_never_picks_twice(world):
    s, lib, tr, submitted = world
    done = autopilot.queue(s, 2)
    assert [d["key"] for d in done] == ["library:L1", "trending:T5"]
    assert lib["L1"]["jobs"] and tr["T5"]["status"] == "queued"
    assert submitted[0].url == "library:L1" and submitted[0].options["output_mode"] == "auto"
    done2 = autopilot.queue(s, 5)
    assert [d["key"] for d in done2] == ["trending:T1"]            # L1/T5 already taken
    assert autopilot.load_state()["count"] == 3


def test_tick_does_nothing_when_off_or_outside_hours(world, monkeypatch):
    s, *_ , submitted = world
    assert autopilot.tick(dict(s, autopilot_enabled=False)) is None
    monkeypatch.setattr(autopilot, "in_window", lambda s, now=None: False)
    assert autopilot.tick(dict(s, autopilot_enabled=True)) is None
    assert not submitted


def test_tick_queues_up_to_daily_max(world, monkeypatch):
    s, *_, submitted = world
    monkeypatch.setattr(autopilot, "in_window", lambda s, now=None: True)
    autopilot.tick(dict(s, autopilot_enabled=True, autopilot_max_per_day=2))
    autopilot.tick(dict(s, autopilot_enabled=True, autopilot_max_per_day=2))      # quota used: nothing more
    assert len(submitted) == 2


def test_window_wraps_past_midnight():
    s = {"autopilot_start_hour": 22, "autopilot_end_hour": 6}
    at = lambda h: time.struct_time((2026, 11, 14, h, 0, 0, 5, 318, 0))  # noqa: E731
    assert autopilot.in_window(s, at(23)) and autopilot.in_window(s, at(3)) and not autopilot.in_window(s, at(12))
    s = {"autopilot_start_hour": 1, "autopilot_end_hour": 7}
    assert autopilot.in_window(s, at(1)) and not autopilot.in_window(s, at(7))


def test_same_video_under_another_link_is_skipped(world):
    s, lib, *_ = world
    lib["L1"]["title"] = "达喀尔冠军横穿迪拜 #极限运动"
    urls = {"https://v.douyin.com/other-share-link/", "title:" + autopilot._norm("达喀尔冠军横穿迪拜")}
    assert "library:L1" not in [x["key"] for x in autopilot.candidates(s, job_urls=urls)]


def test_autopilot_jobs_always_get_vietnamese_subtitles(world):
    s, *_, submitted = world
    autopilot.queue(dict(s, autopilot_mode="dub"), 1)
    assert submitted[0].options["burn_subtitles"] is True
