"""Output modes: which step a re-made job restarts from, and that text-only mode skips speech steps."""
from pathlib import Path

import pytest

from dubber import app as app_mod
from dubber import pipeline
from dubber.jobs import STEP_IDS


class J:
    def __init__(self, tmp: Path, files=()):
        self.d, self.keep_text = tmp, False
        for f in files:
            (tmp / f).write_text("{}", encoding="utf-8")

    def path(self, name):
        return self.d / name


@pytest.mark.parametrize("files,new,expected,keep", [
    ([], "text", "screen_text", False),                                    # never OCR'd -> OCR + translate text
    (["screentext.json"], "text", "preview", False),                      # reuse translated screen text
    (["audio16k.wav", "asr.json", "lines.json"], "sub", "separate", True),  # reuse transcript + translation
    ([], "dub", "extract", True),                                         # was text-only: needs speech steps
])
def test_mode_switch_step(tmp_path, files, new, expected, keep):
    j = J(tmp_path, files)
    assert app_mod._mode_switch_step(j, "render", new) == expected
    if new != "text":
        assert j.keep_text is keep


def test_explicit_earlier_step_is_respected(tmp_path):
    j = J(tmp_path, ["screentext.json"])
    assert app_mod._mode_switch_step(j, "screen_text", "text") == "screen_text"


def test_text_mode_tables_consistent():
    assert set(pipeline.SPEECH_STEPS) <= set(STEP_IDS)
    assert "screen_text" not in pipeline.SPEECH_STEPS and "render" not in pipeline.SPEECH_STEPS
    assert pipeline.OUTPUTS["text"] == ("output_text.mp4", "output_text")
    assert "output_text.mp4" in app_mod.FILE_WHITELIST


def test_voiceover_mix_uses_stems_and_ducks_original_voice(tmp_path):
    from dubber import render
    for f in ("no_vocals.wav", "vocals.wav"):
        (tmp_path / f).write_bytes(b"")
    s = {"vo_original_volume": 0.3, "voice_volume": 1.4, "separate_music": True}
    inputs, af = render.voiceover_mix(tmp_path, s, ["-i", "clean.mp4", "-i", "source.mp4"], 1)
    assert inputs[-6:] == ["-i", "no_vocals.wav", "-i", "vocals.wav", "-i", "voice.wav"]
    assert "[3:a]volume=0.3[ov0]" in af and any("sidechaincompress" in x and x.startswith("[ov0]") for x in af)
    assert af[-1].startswith("[vm][ov][nv]amix=inputs=3")
    # no stems: the whole original soundtrack is turned down instead
    (tmp_path / "vocals.wav").unlink()
    inputs, af = render.voiceover_mix(tmp_path, s, ["-i", "source.mp4"], 0)
    assert inputs[-2:] == ["-i", "voice.wav"] and af[0] == "[0:a]volume=0.3[or0]"


def test_voiceover_runs_voice_steps():
    from dubber import estimate
    st = estimate.steps_for({"output_mode": "voiceover", "separate_music": True, "voice_gender_mode": "auto",
                             "hide_original_subs": "auto"})
    assert {"separate", "speakers", "tts", "transcribe"} <= set(st)


def test_job_save_survives_locked_file(monkeypatch, tmp_path):
    from pathlib import Path
    from dubber import config
    from dubber.jobs import Job
    monkeypatch.setattr(config, "JOBS", tmp_path)
    j = Job("u", {})
    calls = {"n": 0}
    real = Path.replace

    def flaky(self, target):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied")
        return real(self, target)
    monkeypatch.setattr(Path, "replace", flaky)
    j.log("x")                                  # would have raised before
    assert calls["n"] == 3 and (tmp_path / j.id / "job.json").exists()
    monkeypatch.setattr(Path, "replace", lambda self, t: (_ for _ in ()).throw(PermissionError(5, "denied")))
    j.log("y")                                  # permanently locked: skipped, no exception
