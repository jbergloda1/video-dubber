"""VieNeu-TTS (https://github.com/pnnbao97/VieNeu-TTS, Apache-2.0): natural Vietnamese voices, local on the GPU.

It needs torch 2.8 + transformers 4.57.6, so it lives in its own environment (install_vieneu.ps1, default
F:\VieNeu-TTS\env — setting `vieneu_python`) and runs as a
long-lived worker process (dubber/vieneu_worker.py): the model is loaded once and kept on the GPU (~1–2 GB VRAM),
sentences are sent in batches (≈50× real time on an RTX 3060)."""
import collections
import json
import os
import subprocess
import threading
from pathlib import Path

from . import config, util

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PY = r"F:\VieNeu-TTS\env\Scripts\python.exe"
WORKER = Path(__file__).resolve().parent / "vieneu_worker.py"
BATCH = 24                       # sentences per request (VieNeu batches up to 32 on the GPU)

# v3 Turbo preset voices (fallback only: the worker's own list, with gender · region · style, wins when it answers).
PRESETS = [
    ("Hải Đăng", "male", ""), ("Thiện Minh", "male", ""), ("Adam bựa", "male", ""),
    ("Thiền Tâm Đức", "male", ""), ("Quang Sơn", "male", ""), ("Minh Đức", "male", ""),
    ("Phạm Tuyên", "male", ""), ("Xuân Vĩnh", "male", ""), ("Thanh Bình", "male", ""),
    ("Quốc Tuấn", "male", ""), ("Adam", "male", ""), ("Thái Sơn", "male", ""),
    ("Minh Triết", "male", ""), ("Đức Trí", "male", ""),
    ("Mai Anh", "female", ""), ("Trúc Ly", "female", ""), ("Thùy Dung", "female", ""),
    ("Ngọc Huyền", "female", ""), ("Ngọc Trân", "female", ""), ("Ngọc Linh", "female", ""),
    ("Đoan Trang", "female", ""), ("Quỳnh Anh", "female", ""), ("Thục Đoan", "female", ""),
    ("Mỹ Duyên", "female", ""), ("Kim Thanh", "female", ""),
]
_GENDER = {name: g for name, g, _ in PRESETS}


def python_path() -> Path:
    return Path(config.load().get("vieneu_python") or DEFAULT_PY)


def installed() -> bool:
    return python_path().exists()


def parse_label(label, vid):
    """'⭐ Mai Anh — Nữ · Bắc · Phong cách tin tức' -> {gender, info}"""
    rest = label.split("—", 1)[1].strip() if "—" in label else ""
    parts = [x.strip() for x in rest.split("·")]
    g = _GENDER.get(vid, "male")
    if parts and parts[0].lower().startswith("nữ"):
        g = "female"
    elif parts and parts[0].lower().startswith("nam"):
        g = "male"
    region = {"Bắc": "miền Bắc", "Trung": "miền Trung", "Nam": "miền Nam"}.get(parts[1], parts[1]) if len(parts) > 1 else ""
    style = parts[2].replace("Phong cách ", "").replace("Giọng đọc ", "") if len(parts) > 2 else ""
    info = ", ".join(x for x in (region, style) if x)
    return {"id": vid, "name": ("⭐ " if label.startswith("⭐") else "") + vid, "gender": g, "info": info}


def voices():
    """[{id, name, gender, info}] from the worker's own list (gender · region · style in its labels); the README
    table when the worker cannot start."""
    try:
        return [parse_label(label, vid) for label, vid in _worker().request({"cmd": "voices"}, timeout=180)["voices"]]
    except Exception:
        return [{"id": n, "name": n, "gender": g, "info": info} for n, g, info in PRESETS]

class _Worker:
    def __init__(self):
        if not installed():
            raise RuntimeError(f"Chưa cài VieNeu-TTS (không thấy {python_path()}) — chạy install_vieneu.ps1, "
                               "xem README mục VieNeu-TTS")
        py = python_path()
        env = dict(os.environ)
        # the model download (~1 GB) goes next to the environment (F:\VieNeu-TTS\hf-cache), not to drive C
        env.setdefault("HF_HOME", str(py.parent.parent.parent / "hf-cache"))
        env["PYTHONIOENCODING"] = "utf-8"
        self.p = subprocess.Popen([str(py), "-u", str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, cwd=str(ROOT), env=env, creationflags=util.CREATE_NO_WINDOW,
                                  encoding="utf-8", errors="replace", bufsize=1)
        self.err = collections.deque(maxlen=40)
        threading.Thread(target=self._drain, daemon=True).start()
        self.lock = threading.Lock()
        first = self._read(timeout=900)            # first start downloads the model (~1 GB) then loads it
        if not first.get("ready"):
            raise RuntimeError("VieNeu không khởi động được: " + self._tail())
        self.device = first.get("device")

    def _drain(self):
        for line in self.p.stderr:
            self.err.append(line.rstrip())

    def _tail(self):
        return " | ".join(list(self.err)[-4:]) or f"exit {self.p.poll()}"

    def _read(self, timeout):
        box = {}

        def rd():
            while True:
                line = self.p.stdout.readline()
                if not line:
                    box["eof"] = True
                    return
                try:
                    box["msg"] = json.loads(line)
                    return
                except ValueError:
                    continue
        t = threading.Thread(target=rd, daemon=True)
        t.start()
        t.join(timeout)
        if "msg" in box:
            return box["msg"]
        if t.is_alive():
            self.p.kill()
            raise RuntimeError(f"VieNeu không trả lời sau {timeout}s")
        raise RuntimeError("VieNeu đã dừng: " + self._tail())

    def alive(self):
        return self.p.poll() is None

    def request(self, obj, timeout=600):
        with self.lock:
            self.p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self.p.stdin.flush()
            r = self._read(timeout)
        if not r.get("ok"):
            raise RuntimeError("VieNeu: " + str(r.get("error")))
        return r

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(10)
        except Exception:
            self.p.kill()


_w = None
_wlock = threading.Lock()


def _worker() -> _Worker:
    global _w
    with _wlock:
        if _w is None or not _w.alive():
            _w = _Worker()
        return _w


def unload():
    """Free the GPU memory (e.g. before a heavy GPU step). The next request starts the worker again."""
    global _w
    with _wlock:
        if _w is not None:
            _w.close()
            _w = None


IDLE_UNLOAD = 600               # seconds without a request before the model leaves the GPU
_idle = [None]


def _arm_idle():
    if _idle[0]:
        _idle[0].cancel()
    _idle[0] = threading.Timer(IDLE_UNLOAD, unload)
    _idle[0].daemon = True
    _idle[0].start()


def synth(items, progress=None, cancel=None):
    """items: [(text, voice, out_path)]. Writes 48 kHz wav files; batches of BATCH sentences."""
    _arm_idle()
    w = _worker()
    for k in range(0, len(items), BATCH):
        if cancel:
            cancel()
        chunk = items[k:k + BATCH]
        w.request({"cmd": "synth", "items": [{"text": t, "voice": v, "out": str(o)} for t, v, o in chunk]},
                  timeout=120 + 20 * len(chunk))
        if progress:
            progress(len(chunk))
    _arm_idle()


def synth_one(text, out: Path, voice):
    synth([(text, voice, out)])

