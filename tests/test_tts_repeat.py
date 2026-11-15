import threading

import numpy as np
import soundfile as sf

from dubber import translate, tts


def _tone(path, secs=1.0, sr=24000, f=220.0, amp=0.2):
    t = np.arange(int(secs * sr)) / sr
    sf.write(str(path), (amp * np.sin(2 * np.pi * f * t)).astype("float32"), sr, format="WAV")


def test_repeated_sentences_are_made_once(job, tmp_path, monkeypatch):
    calls, lock = [], threading.Lock()

    def fake_google(text, out, s, log=None):
        with lock:
            calls.append(text)
        _tone(out.with_suffix(".tmp.wav"), 0.6)
        out.with_suffix(".tmp.wav").replace(out)
    monkeypatch.setattr(tts, "google_tts", fake_google)
    lines = [{"tgt": t, "start": i * 1.5, "end": i * 1.5 + 1.0, "gender": "female"}
             for i, t in enumerate(["Xông lên!", "Trời ơi!", "Xông lên!", "Xông lên!", "Trời ơi!"])]
    s = {"tts_provider": "google", "google_speed": 1.25, "max_speedup": 1.5, "voice_gender_mode": "female"}
    out = tts.generate(job, lines, 9.0, s, tmp_path)
    assert sorted(calls) == ["Trời ơi!", "Xông lên!"]          # 2 distinct sentences → 2 requests
    assert len(out) == 5 and (tmp_path / "voice.wav").exists()


def test_placeholder_lines_are_dropped(job):
    lines = [{"tgt": "[Không rõ lời]"}, {"tgt": "Xông lên!"}, {"tgt": "(tiếng hét)"}, {"tgt": "…"}]
    assert [l["tgt"] for l in translate.drop_placeholders(job, lines)] == ["Xông lên!"]


def test_elevenlabs_first_line_per_voice_runs_alone(job, tmp_path, monkeypatch):
    import time as _t
    events, lock = [], threading.Lock()

    def fake_eleven(text, out, s, voice, prev=None, nxt=None):
        with lock:
            events.append(("start", voice, text))
        _t.sleep(0.05)
        _tone(out, 0.5)
        with lock:
            events.append(("end", voice, text))
    monkeypatch.setattr(tts, "eleven_tts", fake_eleven)
    lines = [{"tgt": f"câu {i}", "start": i * 1.5, "end": i * 1.5 + 1.0, "gender": "male" if i % 2 else "female"}
             for i in range(6)]
    s = {"tts_provider": "elevenlabs", "elevenlabs_model": "eleven_flash_v2_5", "elevenlabs_stability": 0.5,
         "elevenlabs_similarity": 0.75, "elevenlabs_style": 0, "elevenlabs_speed": 1.0, "max_speedup": 1.5,
         "voice_gender_mode": "auto", "elevenlabs_voice_male": "VM", "elevenlabs_voice_female": "VF"}
    tts.generate(job, lines, 10.0, s, tmp_path)
    for v in ("VM", "VF"):
        first_end = next(k for k, e in enumerate(events) if e[0] == "end" and e[1] == v)
        later_starts = [k for k, e in enumerate(events) if e[0] == "start" and e[1] == v][1:]
        assert all(k > first_end for k in later_starts), v      # the voice was added before parallel use


def test_elevenlabs_409_is_retried(tmp_path, monkeypatch):
    import io
    import urllib.error
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {}, io.BytesIO(
                b'{"detail":{"status":"voice_add_conflict","message":"Multiple voice additions"}}'))

        class R(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): pass
        return R(b"mp3-bytes")
    monkeypatch.setattr(tts.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(tts.time, "sleep", lambda s: None)
    s = {"elevenlabs_api_key": "k", "elevenlabs_model": "eleven_flash_v2_5", "elevenlabs_stability": 0.5,
         "elevenlabs_similarity": 0.75, "elevenlabs_style": 0, "elevenlabs_speed": 1.0, "target_lang": "vi"}
    tts.eleven_tts("xin chào", tmp_path / "o.mp3", s, "VOICE")
    assert len(calls) == 2 and (tmp_path / "o.mp3").read_bytes() == b"mp3-bytes"
