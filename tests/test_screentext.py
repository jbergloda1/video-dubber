"""On-screen text: tracking static vs moving text, font matching, moving-text rendering."""
from dubber import config, render, screentext


def blk(text, x0, y0, x1, y1, lh=0.04):
    return {"text": text, "box": (x0, y0, x1, y1), "lh": lh, "rows": [text]}


def test_static_text_is_one_event():
    frames = [[blk("标题", 0.2, 0.1, 0.8, 0.15)] for _ in range(6)]
    ev = screentext.track(frames, 0.5)
    assert len(ev) == 1 and ev[0]["start"] == 0 and ev[0]["end"] == 2.5
    assert not screentext.is_moving(ev[0]["boxes"])


def test_scrolling_text_is_tracked_as_one_moving_event():
    # a caption sliding left 8% of the width per sample (IoU with the previous box drops below the static rule)
    frames = [[blk("移动的标题文字", 0.6 - 0.08 * i, 0.5, 0.9 - 0.08 * i, 0.55)] for i in range(6)]
    ev = screentext.track(frames, 0.5)
    assert len(ev) == 1 and len(ev[0]["frames"]) == 6
    assert screentext.is_moving(ev[0]["boxes"])


def test_different_text_at_same_place_is_a_new_event():
    frames = [[blk("第一句话内容", 0.2, 0.8, 0.8, 0.85)]] * 3 + [[blk("完全不同的句子", 0.2, 0.8, 0.8, 0.85)]] * 3
    assert len(screentext.track(frames, 0.5)) == 2


def test_box_at_interpolates_track():
    L = {"box": [0, 0, 1, 1], "track": [[0.0, 0.0, 0.0, 0.2, 0.1], [2.0, 0.4, 0.2, 0.6, 0.3]]}
    assert render.box_at(L, 1.0) == [0.2, 0.1, 0.4, 0.2]
    assert render.box_at(L, -1) == [0.0, 0.0, 0.2, 0.1] and render.box_at(L, 9) == [0.4, 0.2, 0.6, 0.3]
    assert render.box_at({"box": [1, 2, 3, 4]}, 5) == [1, 2, 3, 4]


def test_pick_font_follows_weight_or_user_choice():
    s = dict(config.DEFAULTS)
    assert render.pick_font({"weight": "regular"}, s) == ("Be Vietnam Pro", 400)    # thin, elegant original
    assert render.pick_font({"weight": "bold"}, s) == ("Be Vietnam Pro", 700)
    assert render.pick_font({"weight": "heavy"}, s) == ("Be Vietnam Pro", 800)
    assert render.pick_font({"weight": "heavy"}, dict(s, screen_text_font="Impact")) == ("Impact", 700)


def test_moving_text_rendered_with_move_segments():
    s = dict(config.DEFAULTS)
    ev = [{"tgt": "Tiêu đề chạy", "box": [0.3, 0.5, 0.6, 0.55], "line_h": 0.04, "rows": 1, "start": 0.0, "end": 2.0,
           "fill": "#FFFFFF", "outline": "#000000", "weight": "bold",
           "track": [[0.0, 0.3, 0.5, 0.6, 0.55], [1.0, 0.2, 0.5, 0.5, 0.55], [2.0, 0.1, 0.5, 0.4, 0.55]]}]
    rows = render.screen_text_rows(render.screen_text_layout(ev, 1000, 1000, s), s, 1000, 1000)
    moves = [r for r in rows if "\\move(" in r]
    assert len(moves) == 2
    assert "\\move(450,525,350,525,0,1000)" in moves[0] and "\\move(350,525,250,525,0,1000)" in moves[1]


def test_static_text_uses_pos_and_matched_font():
    s = dict(config.DEFAULTS)
    ev = [{"tgt": "Chú thích", "box": [0.2, 0.8, 0.8, 0.85], "line_h": 0.03, "rows": 1, "start": 1, "end": 3,
           "fill": "#FFFFFF", "outline": "#000000", "weight": "regular"}]
    row = render.screen_text_rows(render.screen_text_layout(ev, 1000, 1000, s), s, 1000, 1000)[0]
    assert r"\pos(500,825)" in row and r"\fnBe Vietnam Pro" in row and r"\b400" in row and r"\fsp" in row and r"\blur" in row


def test_vertical_and_edge_text_stays_inside_the_frame():
    s = dict(config.DEFAULTS)
    W, H = 1080, 1920
    # vertical column at the very left edge (like 抽藕丝): small horizontal block, fully on screen
    ev = [{"tgt": "Rút tơ sen", "box": [0.01, 0.3, 0.06, 0.45], "line_h": 0.03, "rows": 1, "start": 0, "end": 2,
           "fill": "#FFFFFF", "outline": "#000000", "weight": "regular", "vertical": True},
          {"tgt": "Một câu chú thích rất dài " * 6, "box": [0.6, 0.5, 0.99, 0.53], "line_h": 0.03, "rows": 1,
           "start": 0, "end": 2, "fill": "#FFFFFF", "outline": "#000000", "weight": "bold"}]
    for L in render.screen_text_layout(ev, W, H, s):
        font = render._measure_font(L["font"], L["fs"], L["bold"])
        tw = max(font.getlength(l) for l in L["lines"])
        assert L["cx"] - tw / 2 >= 0 and L["cx"] + tw / 2 <= W, L["lines"]
        assert L["fs"] < 0.04 * H                      # not a giant title
def test_subtitle_lines_become_timed_events():
    from dubber import subdetect
    a = {"box": [0.3, 0.9, 0.7, 0.97], "text": "外星人如果我们", "lh": 0.07, "fill": "#FFFFFF", "outline": "#000000"}
    b = {**a, "box": [0.4, 0.9, 0.6, 0.97], "text": "地球会怎么样"}
    frames = [a, a, None, a, b, b, None, None, None, None, b]          # one OCR miss inside line a is bridged
    ev = subdetect._events(frames, 0.25, 10)
    assert [e["src"] for e in ev] == ["外星人如果我们", "地球会怎么样", "地球会怎么样"]
    assert ev[0]["start"] == 0 and ev[0]["end"] >= ev[1]["start"]      # no uncovered gap between lines
    assert ev[1]["box"][0] == 0.4                                        # cover follows each line's width


def test_stroke_mask_same_line():
    import numpy as np
    from dubber import subdetect
    img = np.zeros((40, 100, 3), np.uint8)
    img[12:28, 20:80:4] = 255                       # thin bright strokes, like a caption
    m1 = subdetect._stroke_mask(img, (15, 5, 85, 35))
    assert subdetect._iou_mask(m1, subdetect._stroke_mask(img, (15, 5, 85, 35))) == 1.0
    img2 = img.copy(); img2[12:28, 82:84] = 255; img2[12:28, 10:14] = 255; img2[12:28, 40:60] = 0
    assert subdetect._iou_mask(m1, subdetect._stroke_mask(img2, (15, 5, 85, 35))) < 0.85   # different line
    bright = np.full((40, 100, 3), 250, np.uint8)
    assert subdetect._stroke_mask(bright, (15, 5, 85, 35)) is None    # white background: no shortcut, run OCR


def test_speech_captions_are_reused_not_retranslated():
    from dubber.screentext import speech_captions
    band = {"x0": 0.05, "x1": 0.95, "y0": 0.68, "y1": 0.76}
    lines = [{"start": 10.0, "end": 12.5}, {"start": 12.6, "end": 14.0}]
    events = [{"id": 1, "start": 10.1, "end": 12.7, "box": [0.17, 0.70, 0.83, 0.74]},   # caption of the speech
              {"id": 2, "start": 10.0, "end": 14.0, "box": [0.1, 0.05, 0.9, 0.12]},     # title at the top
              {"id": 3, "start": 30.0, "end": 32.0, "box": [0.2, 0.70, 0.8, 0.74]}]     # in band, nobody speaking
    assert speech_captions(events, band, lines) == {1}
    assert speech_captions(events, None, lines) == set()


def test_ocr_cache_shared_between_steps_and_tied_to_video(tmp_path):
    from dubber.screentext import OcrCache
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video-v1")
    calls = []

    def ocr(path):
        calls.append(path)
        return [[[[0, 0], [10, 0], [10, 5], [0, 5]], "抖音", 0.9]], 0.1
    a = OcrCache(video, 1280)                       # e.g. the watermark step, every 1 s
    for i in range(4):
        a.get(ocr, i * 1.0, tmp_path / f"w{i}.jpg")
    a.save()
    b = OcrCache(video, 1280)                       # the on-screen text step, every 0.5 s
    res = [b.get(ocr, i * 0.5, tmp_path / f"s{i}.jpg") for i in range(8)]
    assert len(calls) == 4 + 4 and b.hits == 4      # 0, 1, 2, 3 s reused; only 0.5, 1.5, … are new
    assert res[0][0][1] == "抖音"
    assert OcrCache(video, 960).data == {}          # other frame size: not shared
    video.write_bytes(b"video-v2")                  # another video in the same folder: cache ignored
    assert OcrCache(video, 1280).data == {}
