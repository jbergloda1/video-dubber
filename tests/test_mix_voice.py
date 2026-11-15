import wave

import numpy as np

from dubber import tts


class J:
    def log(self, *a, **k): pass
    def step(self, *a, **k): pass
    def check_cancel(self): pass
    def __getattr__(self, n): return lambda *a, **k: None


def _wav(path, secs=0.2, amp=0.1):
    x = (np.sin(np.linspace(0, 440 * 2 * np.pi * secs, int(44100 * secs))) * amp * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(44100), w.writeframes(x.tobytes())


def _read(path):
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16), w.getframerate()


def test_many_clips_are_mixed_in_groups_and_land_at_their_time(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "MIX_GROUP", 2)               # 5 clips -> 3 ffmpeg groups + 1 final sum
    tdir = tmp_path / "tts"
    tdir.mkdir()
    lines = []
    for i in range(5):
        _wav(tdir / f"{i:04d}.wav")
        lines.append({"vstart": 0.5 * i, "speed": 1.0})
    tts.mix_voice(J(), lines, tdir, tmp_path, 3.0)
    x, sr = _read(tmp_path / "voice.wav")
    assert abs(len(x) / sr - 3.0) < 0.05
    for i in range(5):                                      # sound at each clip's start, silence just before it
        on = np.abs(x[int((0.5 * i + 0.05) * sr):int((0.5 * i + 0.15) * sr)].astype(float)).mean()
        off = np.abs(x[int((0.5 * i + 0.3) * sr):int((0.5 * i + 0.45) * sr)].astype(float)).mean()
        assert on > 1000 and off < 50, (i, on, off)
    assert not list(tdir.glob("mixpart*"))                  # temporary parts removed
