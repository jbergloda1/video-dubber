"""Regression: the Biên kịch cuts must move picture and sound together (the first version cut only the picture)."""
import subprocess

import numpy as np
import pytest

from dubber import content

EVENTS = [2.0, 9.0, 15.5, 22.0]
CUTS = [{"from": 4.0, "to": 6.4}, {"from": 11.3, "to": 13.9}]
FPS = 30


def _ffmpeg_ok():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg missing")
def test_cut_graph_keeps_picture_and_sound_in_sync(tmp_path):
    on = "+".join(f"between(t,{e},{e + 0.1})" for e in EVENTS)
    src, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s=160x90:r={FPS}:d=25",
                    "-f", "lavfi", "-i", "sine=f=1000:r=48000:d=25", "-filter_complex",
                    f"[0:v]drawbox=c=white:t=fill:enable='{on}'[v];[1:a]volume='if({on},1,0)':eval=frame[a]",
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le",
                    str(src.with_suffix(".mkv"))], check=True)
    cm = content.CutMap(CUTS, 25.0)
    vp, ap, vo, ao = content.cut_graph(cm.keep(), "0:v", "0:a")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src.with_suffix(".mkv")), "-filter_complex", ";".join(vp + ap),
                    "-map", f"[{vo}]", "-map", f"[{ao}]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(out)], check=True)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-vf", "format=gray,scale=8:8", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    lum = np.frombuffer(raw, np.uint8).reshape(-1, 64).mean(1)
    flashes = [i / FPS for i in range(1, len(lum)) if lum[i] > 128 >= lum[i - 1]]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
                         capture_output=True).stdout
    env = np.convolve(np.abs(np.frombuffer(raw, np.float32)), np.ones(40) / 40, "same")
    beeps = [i / 8000 for i in range(1, len(env)) if env[i] > 0.03 >= env[i - 1]]
    want = [cm.t(e) for e in EVENTS]
    assert len(flashes) == len(beeps) == len(EVENTS)
    for f, b, w in zip(flashes, beeps, want):
        assert abs(f - w) < 0.05 and abs(b - w) < 0.05 and abs(f - b) < 0.04
