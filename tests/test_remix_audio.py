"""Only the SOUND changed since the last render (music, volumes) → the picture is kept (stream copy) and the sound
is mixed again. The picture must stay bit-identical and the length the same."""
import hashlib
import subprocess

import pytest

from dubber import render, util


def _ffmpeg_ok():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


class J:
    proc = None

    def __init__(self):
        self.said = []

    def log(self, m, level="info"):
        self.said.append(m)

    def step(self, *a, **k):
        pass

    def check_cancel(self):
        pass


def _video_md5(path):
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v", "-c", "copy", "-f", "md5", "-"],
                         capture_output=True, text=True).stdout
    return out.strip()


def _rms(path):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    x = np.frombuffer(raw, np.int16).astype("float32") / 32768
    return float((x ** 2).mean() ** 0.5)


S = {"output_mode": "dub", "separate_music": True, "bg_volume": 1.0, "voice_volume": 1.0, "bg_duck": False,
     "outro_seconds": 0, "original_voice_volume": 0}


@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg missing")
def test_remix_keeps_picture_and_changes_sound(tmp_path):
    d = tmp_path
    run = lambda *a: subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True, cwd=d)
    run("-f", "lavfi", "-i", "testsrc=s=160x90:r=30:d=4", "-f", "lavfi", "-i", "sine=f=200:d=4", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "source.mp4")
    run("-f", "lavfi", "-i", "sine=f=500:d=4", "voice.wav")
    run("-f", "lavfi", "-i", "sine=f=120:d=4", "-af", "volume=0.05", "no_vocals.wav")
    # the "previous render": same picture, quiet sound
    run("-i", "source.mp4", "-i", "no_vocals.wav", "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt",
        "yuv420p", "-c:a", "aac", "output.mp4")
    before_v, before_a, before_len = _video_md5(d / "output.mp4"), _rms(d / "output.mp4"), util.duration(d / "output.mp4")
    j = J()
    assert render.remix_audio(j, d, S, "output.mp4") is True
    assert _video_md5(d / "output.mp4") == before_v                      # picture untouched
    assert abs(util.duration(d / "output.mp4") - before_len) < 0.06
    assert _rms(d / "output.mp4") > before_a * 3                         # now with the voice
    assert not (d / "remix_output.mp4").exists()
    assert any("chỉ trộn lại tiếng" in m for m in j.said)


def test_remix_refuses_when_it_cannot(tmp_path):
    assert render.remix_audio(J(), tmp_path, S, "output.mp4") is False                    # nothing rendered yet
    (tmp_path / "output.mp4").write_bytes(b"x")
    assert render.remix_audio(J(), tmp_path, dict(S, output_mode="sub"), "output.mp4") is False
    assert render.remix_audio(J(), tmp_path, dict(S, content_enabled=True), "output.mp4") is False   # cuts move the picture


def test_audio_settings_do_not_touch_the_picture_key():
    assert "bg_volume" in render.AUDIO_KEYS and "bgm_enabled" in render.AUDIO_KEYS
    assert "sub_font_scale" not in render.AUDIO_KEYS and "hide_style" not in render.AUDIO_KEYS
