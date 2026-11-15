"""Import from disk, export with Vietnamese names, narrator / character roles."""
import json

from dubber import export, narrator


def test_safe_name_keeps_vietnamese_drops_illegal():
    assert export.safe_name('Loài người biến mất, Trái Đất sẽ ra sao? 🌍 #fyp') == "Loài người biến mất, Trái Đất sẽ ra sao 🌍"
    assert export.safe_name('a/b\\c:d*e"f<g>h|i') == "a b c d e f g h i"
    assert export.safe_name("") == "video"
    assert len(export.safe_name("x " * 200)) <= 110


class FakeJob:
    def __init__(self, d, files, title="Tiêu đề gốc"):
        self.dir, self.files, self.title, self.id = d, files, title, "J1"


def test_export_names_and_replaces_previous(tmp_path):
    d = tmp_path / "job"
    d.mkdir()
    (d / "output.mp4").write_bytes(b"video")
    (d / "subs.srt").write_text("1", encoding="utf-8")
    (d / "post.json").write_text(json.dumps({"title": "Đua xe qua Dubai"}), encoding="utf-8")
    s = {"export_dir": str(tmp_path / "out")}
    done = export.run(FakeJob(d, {"output": "output.mp4"}), d, s)
    assert (tmp_path / "out" / "Đua xe qua Dubai (Lồng tiếng).mp4").read_bytes() == b"video"
    assert done["output_srt"].endswith("Đua xe qua Dubai (Lồng tiếng).srt")
    # new title: the old export is replaced, not duplicated
    (d / "post.json").write_text(json.dumps({"title": "Tên mới"}), encoding="utf-8")
    export.run(FakeJob(d, {"output": "output.mp4"}), d, s)
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert names == ["Tên mới (Lồng tiếng).mp4", "Tên mới (Lồng tiếng).srt"]


def test_narrator_is_speaker_with_most_speech_and_manual_role_wins():
    lines = [{"speaker": "S1", "start": 0, "end": 8}, {"speaker": "S2", "start": 8, "end": 10},
             {"speaker": "S1", "start": 10, "end": 16}, {"speaker": "S2", "start": 16, "end": 17, "role": "narrator",
                                                          "role_set": True}]
    narrator.assign_roles(lines)
    assert [l["role"] for l in lines] == ["narrator", "character", "narrator", "narrator"]


def test_upload_endpoint_creates_job(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from dubber import app as appmod, config
    monkeypatch.setattr(config, "JOBS", tmp_path)
    submitted = []
    monkeypatch.setattr(appmod, "get_manager", lambda: type("M", (), {"submit": lambda self, j, *a: submitted.append(j)})())
    c = TestClient(appmod.app, client=("127.0.0.1", 50000))
    r = c.post("/api/jobs/upload", files={"file": ("clip đẹp.mp4", b"x" * 5000, "video/mp4")},
               data={"options": json.dumps({"output_mode": "sub"})})
    assert r.status_code == 200
    j = submitted[0]
    assert j.url == "upload:clip đẹp.mp4" and j.options["output_mode"] == "sub"
    assert (tmp_path / j.id / "upload.mp4").stat().st_size == 5000
    bad = c.post("/api/jobs/upload", files={"file": ("x.txt", b"x" * 5000, "text/plain")})
    assert bad.status_code == 400


def test_default_folder_is_final(monkeypatch, tmp_path):
    from dubber import config
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    assert export.folder({}).name == "final"


def test_title_falls_back_to_translated_original(tmp_path):
    (tmp_path / "title_vi.txt").write_text("Nhà vô địch Dakar băng qua Dubai", encoding="utf-8")
    assert export._title(tmp_path, FakeJob(tmp_path, {})) == "Nhà vô địch Dakar băng qua Dubai"
