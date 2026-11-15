"""Trimmed clips are cached beside the clip and measured from the WAV header: a rerun that changed one line trims
one clip, not all of them."""
import subprocess

import pytest

from dubber import tts


def _ffmpeg_ok():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg missing")
def test_trim_cache_and_wav_seconds(tmp_path):
    clip = tmp_path / "abc.mp3"
    # 0.5 s silence + 1 s tone + 0.5 s silence
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=2", "-af",
                    "volume='if(between(t,0.5,1.5),0.6,0)':eval=frame", str(clip)], check=True)
    out = tts.trimmed_path(clip)
    assert out.name == "abc.trim.wav"
    tts.trim_clip(clip, out)
    d = tts.wav_seconds(out)
    assert 0.9 < d < 1.15                                   # the silence at both ends is gone
    assert not list(tmp_path.glob("*.tmp.wav"))             # no temp file left behind
    m = out.stat().st_mtime_ns
    assert tts.trimmed_path(clip).exists() and out.stat().st_mtime_ns == m


def test_mix_voice_reads_given_wavs(tmp_path, monkeypatch):
    seen = {}

    def fake_run(job, cmd, **kw):
        seen["cmd"] = [str(c) for c in cmd]
    monkeypatch.setattr(tts.util, "run", fake_run)
    (tmp_path / "tts").mkdir()
    lines = [{"vstart": 0.0, "speed": 1.0}, {"vstart": 2.0, "speed": 1.1}]
    wavs = [tmp_path / "a.trim.wav", tmp_path / "b.trim.wav"]
    tts.mix_voice(None, lines, tmp_path / "tts", tmp_path, 5.0, wavs)
    assert str(wavs[0]) in seen["cmd"] and str(wavs[1]) in seen["cmd"]
