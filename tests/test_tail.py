"""The post-render steps (title, TikTok check) run in the background: the job is “done” as soon as the video is
rendered, a new run of the same job stops them first, and job.json is not rewritten for every progress tick."""
import threading
import time

from dubber import jobs
from dubber.jobs import Cancelled, Job, Manager


def _mgr(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs.config, "JOBS", tmp_path)
    m = Manager.__new__(Manager)
    m.jobs, m.q, m.tails = {}, jobs.queue.Queue(), jobs.queue.Queue()
    threading.Thread(target=m._tail_worker, daemon=True).start()
    return m


def test_tail_runs_in_background_and_reports(tmp_path, monkeypatch):
    m = _mgr(tmp_path, monkeypatch)
    j = Job("x", {})
    m.jobs[j.id] = j
    j.status = "done"
    gate = threading.Event()

    def fin():
        j.step("summary", status="running")
        gate.wait(5)
        j.step("summary", status="done", detail="Tiêu đề")
    j.tail_fn = fin
    m.tails.put((j, fin))
    time.sleep(0.2)
    assert j.status == "done" and j.tail_active and j.id in m.busy() and j.to_dict()["finishing"]
    gate.set()
    assert j.tail_done.wait(5)
    time.sleep(0.05)
    assert not j.tail_active and j.steps["summary"]["status"] == "done" and j.id not in m.busy()


def test_new_run_stops_the_tail_first(tmp_path, monkeypatch):
    m = _mgr(tmp_path, monkeypatch)
    j = Job("x", {})
    m.jobs[j.id] = j
    j.status = "done"

    def fin():
        j.step("tiktok", status="running")
        for _ in range(200):
            j.check_cancel()
            time.sleep(0.02)
    j.tail_fn = fin
    m.tails.put((j, fin))
    time.sleep(0.2)
    m.submit(j, "tts")                           # e.g. “Lưu & render lại”
    assert not j.tail_active and j.status == "queued" and not j.cancel_flag.is_set()
    assert j.steps["tiktok"]["status"] == "pending"


def test_progress_ticks_do_not_rewrite_job_json(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs.config, "JOBS", tmp_path)
    j = Job("x", {})
    n = {"saves": 0}
    real = Job.save

    def counted(self):
        n["saves"] += 1
        real(self)
    monkeypatch.setattr(Job, "save", counted)
    j.step("render", status="running")
    base = n["saves"]
    for k in range(200):
        j.step("render", progress=k / 200)
        j.log(f"dòng {k}")
    assert n["saves"] - base <= 2                # throttled
    j.step("render", status="done")              # a state change is written at once
    assert n["saves"] - base >= 1 and j.path("job.json").exists()
    v = j.version
    j.log("cảnh báo", "warn")
    assert j.version == v + 1
