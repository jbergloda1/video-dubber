"""Watermark detection helpers, removal masks, your own moving watermark, and the separator fallback."""
import numpy as np
import pytest

from dubber import brand, textclean, watermark


def test_wm_text_patterns():
    for t in ("抖音号：58312946", "抖音", "TikTok", "@kenh.cua_ban", "快手号: abc123", "ID: 778899"):
        assert watermark.is_wm_text(t), t
    for t in ("这是钱塘江十分凶险的神龙摆尾潮", "Lafayette Avenue Station", "19:00"):
        assert not watermark.is_wm_text(t), t


def _ev(start, end, box, text="抖音号：1"):
    n = int(round((end - start) / 0.5)) + 1
    return {"start": start, "end": end, "texts": [text] * n, "boxes": [box] * n, "lhs": [0.02] * n,
            "frames": list(range(n)), "rows": 1}


def test_join_partial_readings_at_same_place():
    a = _ev(0, 13.5, (0.1, 0.07, 0.37, 0.12))
    part = _ev(14.0, 14.0, (0.1, 0.07, 0.22, 0.09), "抖音")          # only the first line was read
    b = _ev(14.5, 14.5, (0.1, 0.07, 0.37, 0.12))
    other = _ev(15.0, 30, (0.64, 0.8, 0.91, 0.86))
    out = watermark._join([a, part, b, other], 0.5)
    assert len(out) == 2 and out[0]["end"] == 14.5 and out[1]["start"] == 15.0


def test_handover_closes_gap_between_jumping_positions():
    items = watermark._handover([{"start": 0, "end": 13.0, "src": "x", "box": [0, 0, .1, .1]},
                                 {"start": 15.0, "end": 30, "src": "x", "box": [.8, .8, .9, .9]}])
    assert items[0]["end"] == 15.0 and items[1]["start"] == 13.0


def test_screen_text_that_is_a_watermark_is_not_translated():
    wm = [{"box": [0.6, 0.8, 0.9, 0.86], "start": 10, "end": 30, "enabled": True}]
    assert watermark.covers({"src": "抖音号：123456", "box": [0, 0, .1, .1], "start": 0, "end": 1}, [])
    assert watermark.covers({"src": "随便", "box": [0.62, 0.81, 0.88, 0.85], "start": 12, "end": 14}, wm)
    assert not watermark.covers({"src": "随便", "box": [0.62, 0.81, 0.88, 0.85], "start": 0, "end": 5}, wm)


def test_logo_square_left_of_text_is_found():
    E = np.zeros((40, 120), np.float32)
    E[5:35, 8:12] = 200          # logo outline left of the text
    E[5:35, 30:34] = 200
    assert watermark._logo_left(E, (40, 5, 110, 35), 60) > 0
    assert watermark._logo_left(np.zeros((40, 120), np.float32), (40, 5, 110, 35), 60) == 0


def test_mask_places_drawn_box_inside_region():
    roi = np.zeros((50, 80, 3), np.uint8)
    L = {"wm": True, "box": [0.2, 0.2, 0.3, 0.3]}            # 100x100 frame -> pixels 20..30
    m = textclean._mask(roi, L, 10, 10, 100, 100)           # region starts at (10,10)
    ys, xs = np.nonzero(m)
    assert (xs.min(), xs.max(), ys.min(), ys.max()) == (10, 19, 10, 19)


def test_brand_items_and_filters(tmp_path):
    s = {"brand_enabled": True, "brand_tiktok": "kenh", "brand_youtube": "@kenh", "brand_facebook": "Kênh Của Bạn",
         "brand_show": "cycle", "brand_size": 0.03, "brand_cycle_s": 5, "brand_opacity": 0.7, "brand_motion": "bounce"}
    assert brand.items(s) == [("tiktok", "@kenh"), ("youtube", "@kenh"), ("facebook", "Kênh Của Bạn")]
    names = brand.make_badges(tmp_path, s, 1080, 1920)
    assert names == ["brand_0.png", "brand_1.png", "brand_2.png"] and (tmp_path / "brand_2.png").exists()
    f, last = brand.filters(names, 3, "v2", s)
    assert last == "bo2" and "[3:v]" in f[0] and "between(mod(t,15.00),5.00" in f[3]
    assert brand.make_badges(tmp_path, dict(s, brand_enabled=False), 1080, 1920) == []
    assert len(brand.make_badges(tmp_path, dict(s, brand_show="stack"), 1080, 1920)) == 1


def test_separator_falls_back_to_demucs(monkeypatch, tmp_path):
    from dubber import separate, util
    calls = []

    class J:
        def log(self, *a, **k): pass
        def step(self, *a, **k): pass

    def bad(job, wav, out, model, dev):
        calls.append(model)
        raise RuntimeError("boom")

    def good(job, wav, out, model, dev):
        calls.append(model)
        out.mkdir(parents=True, exist_ok=True)
        (out / "a.wav").write_bytes(b"1")
        (out / "b.wav").write_bytes(b"2")
        return out / "a.wav", out / "b.wav"

    monkeypatch.setattr(separate, "_roformer", bad)
    monkeypatch.setattr(separate, "_demucs", good)
    monkeypatch.setattr(separate, "available", lambda: True)
    monkeypatch.setattr(util, "cuda_ok", lambda: True)
    used = separate.separate(J(), tmp_path, {"separate_model": "bs_roformer"})
    assert calls == ["bs_roformer", "htdemucs"] and "htdemucs" in used
    assert (tmp_path / "no_vocals.wav").read_bytes() == b"1"


def test_channel_name_and_corner_logo_are_candidates():
    assert watermark._is_channel("极限档案", "极限档案") and watermark._is_channel("极限档", "极限档案")
    assert not watermark._is_channel("天上也再没有一架飞机", "极限档案")
    w, h = 960, 540
    assert watermark._in_corner({"x0": 850, "x1": 950, "y0": 18, "y1": 52}, w, h)          # top-right logo
    assert not watermark._in_corner({"x0": 300, "x1": 660, "y0": 470, "y1": 520}, w, h)    # centred subtitle


def test_static_logo_joined_across_unreadable_gap():
    a = {"start": 2.0, "end": 55.0, "box": [0.88, 0.03, 0.99, 0.1], "src": "极限档案"}
    b = {"start": 64.0, "end": 284.0, "box": [0.88, 0.03, 0.99, 0.1], "src": "极限档案"}
    out = watermark._merge([dict(a), dict(b)], 1.0, long_gap=85)
    assert len(out) == 1 and out[0]["start"] == 2.0 and out[0]["end"] == 284.0
    assert len(watermark._merge([dict(a), dict(b, src="khác hẳn")], 1.0, long_gap=85)) == 2


def test_moving_watermark_text_is_not_translated():
    wm = [{"box": [0.1, 0.8, 0.2, 0.84], "start": 0, "end": 400, "enabled": True, "src": "AIGC.阿史",
           "track": [[0, 0.1, 0.8, 0.2, 0.84], [200, 0.42, 0.9, 0.52, 0.94]]}]
    # same text somewhere else on its path, read slightly differently by OCR
    assert watermark.covers({"src": "AIGC阿史", "box": [0.42, 0.9, 0.52, 0.94], "start": 199, "end": 201}, wm)
    assert not watermark.covers({"src": "杀青", "box": [0.3, 0.3, 0.5, 0.35], "start": 199, "end": 201}, wm)


def test_partial_reading_of_watermark_is_not_translated():
    wm = [{"box": [0.1, 0.8, 0.2, 0.84], "start": 0, "end": 400, "enabled": True, "src": "AIGC.阿史"}]
    assert watermark.covers({"src": "阿史", "box": [0.6, 0.1, 0.7, 0.14], "start": 10, "end": 11}, wm)


def test_misread_watermark_is_not_translated():
    wm = [{"box": [0.1, 0.8, 0.2, 0.84], "start": 0, "end": 400, "enabled": True, "src": "AIGC.阿史"}]
    assert watermark.covers({"src": "汇阿史", "box": [0.6, 0.1, 0.7, 0.14], "start": 10, "end": 11}, wm)
    assert not watermark.covers({"src": "装备识别战术护具识别完成", "box": [0.8, 0.47, 0.91, 0.61], "start": 29, "end": 32}, wm)


def test_moving_cover_starts_at_item_start_with_margin():
    from dubber import render
    L = {"start": 30.0, "end": 40.0, "box": [0.3, 0.5, 0.4, 0.55], "line_h": 0.04, "clean_style": "blur",
         "track": [[32.0, 0.3, 0.5, 0.4, 0.55], [34.0, 0.32, 0.52, 0.42, 0.57]]}
    items = render.wm_cover_items([L])
    assert items[0]["start"] == 30.0 and items[-1]["end"] >= 40.0          # no uncovered head / tail
    first = items[0]["box"]
    assert first[0] < 0.3 and first[1] < 0.5 and first[2] > 0.4           # margin around the OCR box


def test_moving_watermark_stretches_are_bridged():
    a = {"start": 330.0, "end": 345.0, "src": "AIGC.阿史", "box": [0, 0, .1, .1], "track": [[331, 0, 0, .1, .1]]}
    b = {"start": 349.0, "end": 370.0, "src": "AIGC.阿史", "box": [0, 0, .1, .1], "track": [[350, 0, 0, .1, .1]]}
    watermark._handover([a, b])
    assert a["end"] >= b["start"]
