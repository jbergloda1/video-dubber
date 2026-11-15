from dubber import render


def L(start, end, box, **k):
    return {"start": start, "end": end, "box": box, **k}


def test_next_caption_at_the_same_place_ends_the_previous_one():
    a = L(0.0, 4.3, [0.1, 0.5, 0.9, 0.55])
    b = L(3.2, 6.3, [0.1, 0.5, 0.9, 0.56])          # padded: would overlap `a` for 1.1 s
    assert render.show_ends([a, b]) == [3.2, 6.3]


def test_captions_elsewhere_on_screen_keep_their_time():
    title = L(0.0, 10.0, [0.1, 0.05, 0.9, 0.12])
    caption = L(2.0, 5.0, [0.1, 0.7, 0.9, 0.75])
    assert render.show_ends([title, caption]) == [10.0, 5.0]


def test_moving_text_is_left_alone():
    a = L(0.0, 4.0, [0.1, 0.5, 0.9, 0.55], track=[[0, 0.1, 0.5, 0.9, 0.55]])
    b = L(3.0, 6.0, [0.1, 0.5, 0.9, 0.55])
    assert render.show_ends([a, b]) == [4.0, 6.0]
