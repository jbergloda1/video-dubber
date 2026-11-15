"""Subprocess helpers with live output, progress parsing and cancellation."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def kill_tree(p):
    """Stop a process AND its children. Chocolatey's ffmpeg.exe is a shim that starts the real ffmpeg as a child:
    p.kill() stopped only the shim, the real ffmpeg kept the output pipe open and the job never ended."""
    if p is None or p.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, timeout=15,
                           creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass
    try:
        p.kill()
    except Exception:
        pass


def run(job, args, cwd=None, on_line=None, env=None, check=True, input_text=None) -> str:
    """Run a command, stream output lines (split on \\r and \\n) to on_line, support cancel."""
    job.check_cancel()
    e = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    if env:
        e.update(env)
    p = subprocess.Popen(
        [str(a) for a in args], cwd=cwd, env=e,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW,
    )
    job.proc = p
    if input_text is not None:
        p.stdin.write(input_text.encode("utf-8"))
        p.stdin.close()
    # read in chunks and split on CR / LF (progress bars use CR). A bytearray, not bytes += byte: yt-dlp prints
    # YouTube's info JSON as ONE multi-MB line, and growing an immutable bytes object byte by byte froze for minutes.
    out, buf = [], bytearray()

    def emit():
        line = buf.decode("utf-8", "replace")
        out.append(line)
        if on_line:
            on_line(line)
        buf.clear()
    while True:
        chunk = p.stdout.read1(65536) if hasattr(p.stdout, "read1") else p.stdout.read(1)
        if not chunk:
            break
        for b in chunk:
            if b in (13, 10):
                if buf:
                    emit()
            else:
                buf.append(b)
    if buf:
        emit()
    p.wait()
    job.proc = None
    job.check_cancel()
    text = "\n".join(out)
    if check and p.returncode != 0:
        tail = "\n".join(out[-15:])
        raise RuntimeError(f"Lệnh lỗi ({Path(str(args[0])).name}, mã {p.returncode}):\n{tail}")
    return text


def ffmpeg(job, args, duration=None, step=None, cwd=None, scale=(0.0, 1.0), stall_s=180):
    """Run ffmpeg with -progress so the step's progress bar moves. A watchdog stops it with a clear error if its
    output time does not move for `stall_s` seconds (a stuck filter graph used to wait forever, silently)."""
    import threading
    import time as _t
    lo, hi = scale
    st = {"t": _t.time(), "last": None, "stalled": False, "done": False}

    def on_line(line):
        if line.startswith("out_time_us="):
            v = line.split("=")[1].strip()
            if v != st["last"]:
                st["last"], st["t"] = v, _t.time()
            if step and duration:
                try:
                    t = int(v) / 1e6
                    job.step(step, progress=lo + (hi - lo) * min(t / duration, 1.0))
                except ValueError:
                    pass

    def watchdog():
        while not st["done"]:
            _t.sleep(5)
            if not st["done"] and _t.time() - st["t"] > stall_s:
                st["stalled"] = True
                kill_tree(getattr(job, "proc", None))
                return

    if stall_s:
        threading.Thread(target=watchdog, daemon=True).start()
    try:
        return run(job, ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-y",
                         "-progress", "pipe:1", *args], cwd=cwd, on_line=on_line)
    except RuntimeError as e:
        if st["stalled"]:
            raise RuntimeError(f"ffmpeg đứng yên quá {stall_s // 60} phút không tiến triển nên đã bị dừng "
                               f"(bước {step or '?'}). Bấm 'Chạy lại' — nếu vẫn lỗi, gửi nhật ký để kiểm tra.") from e
        raise
    finally:
        st["done"] = True


def probe(path) -> dict:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,codec_name,width,height,duration,r_frame_rate",
         "-of", "json", str(path)], creationflags=CREATE_NO_WINDOW)
    return json.loads(out)


def duration(path) -> float:
    return float(probe(path)["format"]["duration"])


def video_size(path) -> tuple[int, int]:
    for s in probe(path)["streams"]:
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise RuntimeError("Không tìm thấy luồng video")


def has_audio(path) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path)["streams"])


PY = sys.executable
_cuda = None


def cuda_ok() -> bool:
    """Does the installed PyTorch see the GPU? (checked in a subprocess, cached)"""
    global _cuda
    if _cuda is None:
        try:
            out = subprocess.run([sys.executable, "-c", "import torch;print(torch.cuda.is_available())"],
                                 capture_output=True, text=True, timeout=60, creationflags=CREATE_NO_WINDOW)
            _cuda = out.stdout.strip().endswith("True")
        except Exception:
            _cuda = False
    return _cuda
PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)%")
