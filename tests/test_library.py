"""Video library: link parsing, de-duplication, parallel queue, job hand-off without re-download."""
import json

from dubber import library


def test_split_links_from_share_text():
    text = ("7.41 :2pm 07/24 y@g.od dNJ:/ 达喀尔冠军横穿迪拜 https://v.douyin.com/88JkZQJ-CZE/ 复制此链接\n"
            "https://www.tiktok.com/@a/video/123\nhttps://v.douyin.com/88JkZQJ-CZE/")
    assert library.split_links(text) == ["https://v.douyin.com/88JkZQJ-CZE/", "https://www.tiktok.com/@a/video/123"]
    assert library.platform_of("https://www.tiktok.com/@a/video/1") == "tiktok"


def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(library, "STORE", tmp_path / "library.json")
    monkeypatch.setattr(library, "DIR", tmp_path / "library")
    started = []

    class Ex:
        def submit(self, fn, *a):
            started.append(a[0])
    monkeypatch.setattr(library, "_executor", lambda s: Ex())
    return started


def test_add_queues_each_link_once(monkeypatch, tmp_path):
    started = _iso(monkeypatch, tmp_path)
    ids = library.add("https://a.com/v/1 https://a.com/v/2", {})
    again = library.add("https://a.com/v/1", {})
    assert len(ids) == 2 and again == [ids[0]] and started == ids          # no second download of the same link
    assert all(it["status"] == "queued" for it in library.load().values())


def test_stored_video_goes_into_job_without_download(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    src = tmp_path / "library" / "X1"
    src.mkdir(parents=True)
    (src / "source.mp4").write_bytes(b"video")
    (src / "view.mp4").write_bytes(b"view")
    (src / "meta.json").write_text(json.dumps({"title": "t", "uploader": "u"}), encoding="utf-8")
    job = tmp_path / "job"
    job.mkdir()
    meta = library.to_job_dir("X1", job)
    assert meta["title"] == "t" and (job / "source.mp4").read_bytes() == b"video"
    assert (job / "source_view.mp4").read_bytes() == b"view"
