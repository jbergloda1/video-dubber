import os
import time

from dubber import tiktokcheck as tk


def test_premade_copy_is_trusted_only_for_the_same_output(tmp_path):
    (tmp_path / "output.mp4").write_bytes(b"x" * 100)
    (tmp_path / tk.OUT).write_bytes(b"y" * 50)
    assert not tk.premade(tmp_path, "output.mp4")
    tk.mark_premade(tmp_path, "output.mp4")
    assert tk.premade(tmp_path, "output.mp4")
    time.sleep(0.01)
    (tmp_path / "output.mp4").write_bytes(b"x" * 101)          # re-rendered since: the copy is stale
    os.utime(tmp_path / "output.mp4", (time.time() + 10, time.time() + 10))
    assert not tk.premade(tmp_path, "output.mp4")


def test_vertical_h264_keeps_its_picture():
    info = {"size": 50 * 1024 * 1024, "video": {"w": 1080, "h": 1920, "codec": "h264", "fps": 30.0, "pix_fmt": "yuv420p"}}
    assert tk.picture_ok(info)
    assert not tk.picture_ok({**info, "video": {**info["video"], "w": 1920, "h": 1080}})       # landscape
    assert not tk.picture_ok({**info, "video": {**info["video"], "codec": "hevc"}})
    assert not tk.picture_ok({**info, "size": 400 * 1024 * 1024})                               # too big


def test_filters_can_be_embedded_in_the_render_graph():
    f = tk.tiktok_filters(1920, 1080, 30.0, "[vtk0]", "[vtk]", tag="r")
    assert f.startswith("[vtk0]split[tar][tbr]") and f.endswith("[vtk]")
    assert tk.tiktok_filters(1920, 1080, 30.0).startswith("[0:v]split[ta][tb]")   # standalone use unchanged
