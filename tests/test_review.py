"""📋 Đánh giá: notes are kept (job + global log), tech notes go to Claude's queue with a frame, script proposals only
contain real changes and are applied to lines / the Biên kịch plan, your music replaces the background."""
import json
import subprocess
from pathlib import Path

import pytest

from dubber import review


class FakeJob:
    def __init__(self, d: Path):
        self.id, self.dir, self.title = "job1", d, "Video thử"
        self.logs = [{"level": "warn", "msg": "chữ đè"}, {"level": "info", "msg": "ok"}]
        self.steps = {"translate": {"detail": "30 câu"}}
        self.options = {}
        self.said = []

    def settings(self):
        return {"output_mode": "dub"}

    def log(self, m, level="info"):
        self.said.append(m)


@pytest.fixture
def env(tmp_path, monkeypatch):
    fb = tmp_path / "feedback"
    monkeypatch.setattr(review, "FEEDBACK", fb)
    monkeypatch.setattr(review, "QUEUE", fb / "queue.json")
    monkeypatch.setattr(review, "QUEUE_MD", fb / "QUEUE.md")
    monkeypatch.setattr(review, "LOG", fb / "log.jsonl")
    monkeypatch.setattr(review, "FRAMES", fb / "frames")
    d = tmp_path / "job"
    d.mkdir()
    (d / "lines.json").write_text(json.dumps([
        {"src": "a", "tgt": "Người đây sáng mà thiếu một bát", "start": 12.0, "end": 14.0},
        {"src": "b", "tgt": "Câu ổn rồi.", "start": 15.0, "end": 17.0}], ensure_ascii=False), encoding="utf-8")
    return FakeJob(d)


def test_notes_and_ratings_are_kept(env):
    n = review.add_note(env, "script", "câu ở 0:12 vô nghĩa", 12.3)
    review.set_ratings(env, {"overall": 3, "voice": 5, "bogus": 4, "music": 9}, "ổn")
    r = review.load(env.dir)
    assert r["notes"][0]["id"] == n["id"] and r["notes"][0]["t"] == 12.3
    assert r["ratings"] == {"overall": 3, "voice": 5}
    log = [json.loads(x) for x in review.LOG.read_text(encoding="utf-8").splitlines()]
    assert [x["type"] for x in log] == ["note", "rating"]
    with pytest.raises(ValueError):
        review.add_note(env, "script", "  ")


def test_tech_note_goes_to_claude_queue_and_syncs_back(env):
    n = review.add_note(env, "tech", "chữ dịch đè lên chữ cũ", None, "screen_text")
    review.approve_tech(env, n["id"])
    q = json.loads(review.QUEUE.read_text(encoding="utf-8"))
    assert q[0]["status"] == "queued" and q[0]["area"] == "screen_text" and q[0]["warnings"] == ["chữ đè"]
    assert "chữ dịch đè" in review.QUEUE_MD.read_text(encoding="utf-8")
    q[0].update(status="fixed", fix="Đã sửa thứ tự lớp chữ")              # what Claude writes when done
    review.QUEUE.write_text(json.dumps(q, ensure_ascii=False), encoding="utf-8")
    got = review.load(env.dir)["notes"][0]
    assert got["status"] == "fixed" and got["fix"] == "Đã sửa thứ tự lớp chữ"


def test_proposal_keeps_only_real_changes_and_applies(env):
    lines = json.loads((env.dir / "lines.json").read_text(encoding="utf-8"))
    plan = {"narration": [{"start": 1, "text": "Cũ"}], "hook": {"overlay": "Hook"}, "title_bar": "T", "ending": {"loop_vo": "L"}}
    data = {"summary": "s", "lines": [{"i": 0, "tgt": "Người dân ở đây, sáng thiếu bát mỡ là phí cả ngày!"},
                                      {"i": 1, "tgt": "Câu ổn rồi."}, {"i": 9, "tgt": "ngoài phạm vi"}],
            "narration": [{"i": 0, "text": "Mới"}], "title_bar": "T"}
    p = review.build_proposal(lines, plan, data)
    assert [(c["where"], c["i"]) for c in p["changes"]] == [("line", 0), ("narration", 0)]
    # apply only the line (unchecked narration)
    n = review.add_note(env, "script", "x")
    review.update_note(env, n["id"], status="proposed", proposal=p)
    step = review.apply_script(env, n["id"], keep=[0])
    assert step == "tts"
    new = json.loads((env.dir / "lines.json").read_text(encoding="utf-8"))
    assert new[0]["tgt"].startswith("Người dân ở đây") and new[0]["edited"] and new[1]["tgt"] == "Câu ổn rồi."
    assert review.load(env.dir)["notes"][-1]["status"] == "applied"


def test_music_prompt_has_duration(env, monkeypatch):
    monkeypatch.setattr(review, "video_length", lambda d: 61.4)
    u = review.music_prompt_input(env, env.dir)
    assert "61.4 giây" in u and "Người đây sáng" in u


def _ffmpeg_ok():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg missing")
def test_bgm_is_looped_to_the_video_length(env):
    d = env.dir
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "9",
                    str(d / "voice.wav")], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=3", str(d / "song.mp3")], check=True)
    L = review.prepare_bgm(env, d / "song.mp3", volume=0.8)
    from dubber import util
    assert abs(L - 9) < 0.1 and abs(util.duration(d / review.BGM) - 9) < 0.15
    assert review.bgm_track(d, {"bgm_enabled": True}) == review.BGM
    assert review.bgm_track(d, {}) is None
    assert review.load(d)["music"]["file"] == "song.mp3"
