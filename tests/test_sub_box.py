from dubber import render

S = {"sub_font": "Arial", "sub_style": "box", "sub_box_opacity": 0.92}


def test_box_rows_draw_card_then_text_centered_in_it():
    rows = render.box_rows("Đừng tưởng đây là hang đất sơ sài.", "0:00:01.00", "0:00:03.00", 1080, 1920, 48, 600, 64, S)
    assert len(rows) == 3
    shade, card, text = rows
    assert "\\p1}m " in card and "\\1c&HFFFFFF&" in card and " b " in card        # rounded (bézier) white card
    assert text.startswith("Dialogue: 2,") and "\\1c&H111111&" in text and "\\an5\\pos(540," in text
    nums = [float(v) for v in card.split("}m ")[1].split() if v.replace(".", "").isdigit()]
    xs = nums[0::2]
    assert min(xs) > 64 and max(xs) < 1080 - 64                                   # inside the side margins


def test_long_line_wraps_into_two_balanced_lines():
    long = "Muốn sở hữu biệt phủ đẳng cấp này, bạn phải đào sâu xuống lòng đất"
    text = render.box_rows(long, "0:00:00.00", "0:00:02.00", 1080, 1920, 48, 600, 64, S)[2]
    body = text.split("\\q2}")[1]
    a, b = body.split("\\N")
    assert abs(len(a) - len(b)) < 20


def test_rounded_rect_radius_is_clamped():
    path = render.rounded_rect(0, 0, 100, 20, 50)        # radius larger than half the height
    assert path.startswith("m 10 0")
