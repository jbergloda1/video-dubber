"""The subtitle strip (e.g. 960×126) must not be blown up before text detection: the default OCR engine resized it
to 5600×736 and spent 136 of 166 ms per frame there (detect_subs was the slowest step per second of video)."""
import numpy as np
import pytest

pytest.importorskip("rapidocr_onnxruntime")
from dubber import subdetect


def test_strip_engine_does_not_upscale():
    eng = subdetect._strip_engine()
    det = eng.text_det
    assert det.limit_type == "max" and det.limit_side_len == subdetect.STRIP_SIDE
    img = np.zeros((126, 960, 3), np.uint8)
    x = det.get_preprocess(max(img.shape[:2]))(img)
    assert x.shape[2] <= 160 and x.shape[3] <= 992          # ~ the strip's own size (multiples of 32)
    assert eng is not subdetect._engine()                    # whole frames keep the default engine


def test_whole_frames_keep_default_engine():
    assert subdetect._engine().text_det.limit_type == "min"
