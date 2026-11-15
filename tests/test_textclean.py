"""Text-stroke mask used before inpainting (OpenCV / LaMa): must catch gradient outlines, not the background."""
import cv2
import numpy as np

from dubber import textclean

GREEN = (40, 160, 70)            # BGR pitch


def caption(h=60, w=300):
    """Green background, thick orange->light-yellow 'outline' strokes with a white core (like the bug case)."""
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = GREEN
    for i, y in enumerate((20, 40)):
        outline = (20, 120, 230) if i else (150, 230, 250)      # row 2 orange, row 1 pale yellow tint
        cv2.putText(img, "TEXT ROW", (10, y + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, outline, 7, cv2.LINE_AA)
        cv2.putText(img, "TEXT ROW", (10, y + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    return img


def L():
    return {"fill": "#FFFFFF", "outline": "#E67814", "line_h": 0.03, "box": [0, 0, 1, 1]}


def test_mask_covers_gradient_outline_and_core():
    img = caption()
    text = np.abs(img.astype(int) - np.array(GREEN)).max(-1) > 60          # ground truth: anything not pitch
    mask = textclean._text_mask(img, L()) > 0
    missed = text & ~mask
    assert missed.sum() / max(text.sum(), 1) < 0.02                           # pale-yellow tint is caught too


def test_mask_leaves_background_alone():
    img = caption()
    bg = np.abs(img.astype(int) - np.array(GREEN)).max(-1) < 10
    mask = textclean._text_mask(img, L()) > 0
    assert (mask & bg).sum() / bg.sum() < 0.01


def test_background_rule_off_on_busy_border():
    rng = np.random.default_rng(0)
    noisy = rng.integers(0, 255, (40, 120, 3), dtype=np.uint8)
    assert not textclean._differs_from_background(noisy).any()


def test_boundary_fill_matches_surroundings():
    img = np.zeros((64, 160, 3), np.uint8)
    img[:] = GREEN
    mask = np.zeros((64, 160), np.uint8)
    img[20:44, 30:130] = (255, 255, 255)
    mask[20:44, 30:130] = 255
    filled = textclean._boundary_fill(img, mask)
    inside = filled[24:40, 40:120]
    assert np.abs(inside - np.array(GREEN)).max() < 25                         # hole takes the surrounding colour


def test_clean_windows_only_where_text_is():
    from dubber import textclean
    L = lambda a, b: {"start": a, "end": b}
    w = textclean.windows([L(58.5, 61.5), L(61.5, 63.5), L(96.5, 99.5), L(106.5, 107.5), L(400, 410)], 375)
    assert w == [(58.2, 63.8), (96.2, 99.8), (106.2, 107.8)]        # merged when < 2 s apart, clipped to video
    assert textclean.windows([], 100) == []
