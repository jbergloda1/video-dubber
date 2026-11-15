"""Job model, persistence, and the single GPU worker queue."""
import json
import queue
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import config

STEPS = [
    ("download", "Tải video"),
    ("analyze", "Nhận diện kiểu video"),
    ("extract", "Tách âm thanh"),
    ("separate", "Tách nhạc nền (GPU)"),
    ("transcribe", "Nhận dạng giọng nói (GPU)"),
    ("content", "Biên kịch (AI)"),
    ("translate", "Dịch & chia câu"),
    ("speakers", "Nhận diện người nói (GPU)"),
    ("tts", "Tạo giọng đọc"),
    ("detect_subs", "Dò phụ đề gốc"),
    ("watermark", "Tìm watermark gốc"),
    ("screen_text", "Dịch chữ trên màn hình"),
    ("preview", "Render preview"),
    ("render", "Render video cuối"),
    ("summary", "Viết tiêu đề & hashtag"),
    ("tiktok", "Kiểm tra chuẩn TikTok"),
]
STEP_IDS = [s[0] for s in STEPS]


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, url: str, options: dict, jid: str | None = None):
        self.id = jid or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.url = url
        self.options = options          # per-job overrides of settings
        self.title = ""
        self.status = "queued"          # queued | running | awaiting_approval | done | error | cancelled
        self.error = ""
        self.created = time.time()
        self.steps = {sid: {"status": "pending", "progress": 0.0, "detail": ""} for sid in STEP_IDS}
        self.logs: list[dict] = []
        self.files: dict[str, str] = {}
        self.start_from = "download"
        self.cancel_flag = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.version = 0                # bumps on every change (for SSE)
        self.keep_text = False          # rerun: reuse transcript/translation
        self.force_steps = set()        # rerun: steps redone even if their inputs did not change
        self.ai_calls = 0               # AI (LLM) requests made by the current run
        self.video_duration = 0.0       # seconds, known after download (for estimates)
        self._lock = threading.Lock()
        self._saved = 0.0               # when job.json was last written (saves are throttled)
        self.defer_tail = False         # worker: run the post-render steps (title, TikTok check) in the background
        self.tail_fn = None             # set by pipeline.run when defer_tail: the post-render steps
        self.tail_active = False
        self.tail_done = threading.Event()
        self.tail_done.set()

    # ---------- paths ----------
    @property
    def dir(self) -> Path:
        d = config.JOBS / self.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def path(self, name: str) -> Path:
        return self.dir / name

    def settings(self) -> dict:
        s = config.load()
        s.update({k: v for k, v in (self.options or {}).items() if v is not None and v != ""})
        return s

    # ---------- state updates ----------
    SAVE_EVERY = 1.0        # seconds between writes of job.json while only progress / log lines change

    def _touch(self, force=True):
        """Bump the version (SSE) and persist. job.json holds the whole log: writing it for every progress tick
        and log line was a hot spot, so those writes are throttled; state changes are written at once."""
        self.version += 1
        if force or time.time() - self._saved >= self.SAVE_EVERY:
            self.save()

    def log(self, msg: str, level: str = "info"):
        with self._lock:
            self.logs.append({"t": time.time(), "level": level, "msg": msg})
            if len(self.logs) > 2000:
                self.logs = self.logs[-1500:]
            self._touch(force=level in ("warn", "error"))

    def step(self, sid: str, status: str | None = None, progress: float | None = None, detail: str | None = None):
        with self._lock:
            st = self.steps[sid]
            if status is not None:
                # remember how long each step really took (used by the cost/time estimate)
                if status == "running" and st.get("status") != "running":
                    st["_t0"] = time.time()
                    st.pop("secs", None)
                elif status == "done" and st.get("_t0"):
                    st["secs"] = round(time.time() - st.pop("_t0"), 1)
                st["status"] = status
            if progress is not None:
                st["progress"] = max(0.0, min(1.0, progress))
            if detail is not None:
                st["detail"] = detail
            self._touch(force=status is not None)

    def add_file(self, key: str, name: str):
        with self._lock:
            self.files[key] = name
            self._touch(force=True)

    def check_cancel(self):
        if self.cancel_flag.is_set():
            raise Cancelled()

    def to_dict(self, logs_from: int | None = 0) -> dict:
        d = {
            "id": self.id, "url": self.url, "title": self.title, "status": self.status,
            "error": self.error, "created": self.created, "options": self.options,
            "steps": [{"id": sid, "name": name, **self.steps[sid]} for sid, name in STEPS],
            "files": self.files, "version": self.version, "log_count": len(self.logs),
            "video_duration": self.video_duration,
            "resume_step": getattr(self, "resume_step", None),     # where "Duyệt" continues (Biên kịch → preview)
            "finishing": bool(self.tail_active),      # video done; title / TikTok check still running in the background
        }
        if logs_from is not None:
            d["logs"] = self.logs[logs_from:]
        return d

    def save(self):
        self._saved = time.time()
        data = self.to_dict()
        data["start_from"] = self.start_from
        tmp = self.path("job.json.tmp")
        text = json.dumps(data, ensure_ascii=False)
        # Windows refuses to replace a file another process has open (antivirus, indexer, a script reading
        # job.json…). Retry briefly; if it still fails, skip this save — the next change saves again.
        # Persisting progress must never break the job itself.
        for i in range(8):
            try:
                tmp.write_text(text, encoding="utf-8")
                tmp.replace(self.path("job.json"))
                return
            except PermissionError:
                time.sleep(0.05 * (i + 1))
            except OSError:
                return

    @classmethod
    def load(cls, folder: Path) -> "Job | None":
        try:
            d = json.loads((folder / "job.json").read_text(encoding="utf-8"))
        except Exception:
            return None
        j = cls(d["url"], d.get("options") or {}, d["id"])
        j.title = d.get("title", "")
        j.status = d.get("status", "error")
        j.error = d.get("error", "")
        j.created = d.get("created", 0)
        for s in d.get("steps", []):
            if s["id"] in j.steps:
                j.steps[s["id"]] = {k: s.get(k) for k in ("status", "progress", "detail")}
                if s.get("secs"):
                    j.steps[s["id"]]["secs"] = s["secs"]
        j.logs = d.get("logs", [])
        j.files = d.get("files", {})
        j.video_duration = float(d.get("video_duration") or 0)
        j.resume_step = d.get("resume_step")
        if j.status == "done":                  # server restarted while the post-render steps were running
            for sid in ("summary", "tiktok"):
                if j.steps[sid]["status"] == "running":
                    j.steps[sid] = {"status": "error", "progress": 1.0, "detail": "Bị ngắt khi server khởi động lại — bấm ↻ trong thẻ"}
        if j.status in ("running", "queued"):   # server restarted mid-run
            j.status = "error"
            j.error = "Server đã khởi động lại khi job đang chạy. Bấm 'Chạy lại' để tiếp tục."
            for s in j.steps.values():
                if s["status"] == "running":
                    s["status"] = "error"
        return j


class Manager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.q: "queue.Queue[str]" = queue.Queue()
        for f in sorted(config.JOBS.iterdir()) if config.JOBS.exists() else []:
            if f.is_dir():
                j = Job.load(f)
                if j:
                    self.jobs[j.id] = j
        self.tails: "queue.Queue[tuple]" = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._tail_worker, daemon=True).start()

    def busy(self) -> list[str]:
        """Jobs that must not be interrupted (running, queued, or finishing their post-render steps)."""
        return [j.id for j in self.jobs.values() if j.status in ("running", "queued") or j.tail_active or j.tail_fn]

    def stop_tail(self, job: Job, wait=30.0):
        """A new run of this job starts: its background post-render steps (if any) stop first."""
        job.tail_fn = None
        if job.tail_active:
            job.cancel_flag.set()
            if job.proc and job.proc.poll() is None:
                from .util import kill_tree
                kill_tree(job.proc)
            job.tail_done.wait(wait)

    def submit(self, job: Job, start_from: str = "download"):
        self.stop_tail(job)
        job.start_from = start_from
        job.cancel_flag.clear()
        job.status = "queued"
        job.error = ""
        idx = STEP_IDS.index(start_from)
        for sid in STEP_IDS[idx:]:
            job.steps[sid] = {"status": "pending", "progress": 0.0, "detail": ""}
        for sid in STEP_IDS[:idx]:   # earlier steps are reused as they are
            if job.steps[sid]["status"] in ("error", "cancelled", "pending", "running"):
                job.steps[sid] = {"status": "done", "progress": 1.0, "detail": "Dùng kết quả có sẵn"}
        self.jobs[job.id] = job
        job.log(f"Đưa vào hàng đợi (bắt đầu từ bước: {dict(STEPS)[start_from]})")
        self.q.put(job.id)

    def cancel(self, job: Job):
        job.tail_fn = None
        job.cancel_flag.set()
        if job.proc and job.proc.poll() is None:
            from .util import kill_tree
            kill_tree(job.proc)            # the whole tree: a shim's child (real ffmpeg) would keep running
        if job.status == "queued":
            job.status = "cancelled"
            job._touch()

    def _worker(self):
        from . import pipeline   # heavy imports lazily
        while True:
            jid = self.q.get()
            job = self.jobs.get(jid)
            if not job or job.cancel_flag.is_set():
                continue
            job.status = "running"
            job.defer_tail = True
            job._touch(force=True)
            try:
                result = pipeline.run(job)
                job.status = result
                if result == "done" and job.tail_fn:
                    self.tails.put((job, job.tail_fn))
                job.log("Hoàn tất ✔" if result == "done" else ("Đang chờ bạn duyệt kịch bản (tab 🎬 Biên kịch)…" if getattr(job, "resume_step", None) in ("preview", "translate") else "Đang chờ bạn duyệt preview…"))
            except Cancelled:
                job.status = "cancelled"
                for s in job.steps.values():
                    if s["status"] == "running":
                        s["status"] = "cancelled"
                job.log("Đã huỷ.", "warn")
            except Exception as e:
                job.status = "error"
                job.error = str(e)
                for s in job.steps.values():
                    if s["status"] == "running":
                        s["status"] = "error"
                job.log(f"LỖI: {e}", "error")
                job.log(traceback.format_exc(), "debug")
            finally:
                job.proc = None
                job._touch(force=True)

    def _tail_worker(self):
        """Post-render steps (title & hashtags, TikTok check, export) of finished jobs, one job at a time, while the
        main worker already runs the next job. They never change the job's status."""
        while True:
            job, fn = self.tails.get()
            if job.tail_fn is not fn:           # a new run of the job replaced / cancelled it
                continue
            job.tail_done.clear()
            job.tail_active = True
            job._touch(force=True)
            try:
                fn()
            except Cancelled:
                for s in job.steps.values():
                    if s["status"] == "running":
                        s.update(status="pending", progress=0.0, detail="Bị ngắt (chạy lại job)")
            except Exception as e:
                job.log(f"Phần hoàn thiện sau render lỗi: {str(e).splitlines()[0][:200]}", "warn")
                for s in job.steps.values():
                    if s["status"] == "running":
                        s.update(status="error", progress=1.0)
            finally:
                if job.tail_fn is fn:
                    job.tail_fn = None
                job.tail_active = False
                job.proc = None
                job.tail_done.set()
                job._touch(force=True)


manager: Manager | None = None


def get_manager() -> Manager:
    global manager
    if manager is None:
        manager = Manager()
    return manager
