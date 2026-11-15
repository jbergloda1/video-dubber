import subprocess

import pytest

from dubber import tiktokcheck as tc


def _info(w=1080, h=1920, dur=45.0, mb=60, fps=30, vbr=8e6, audio=True, fmt="mov,mp4,m4a,3gp,3g2,mj2"):
    return {"duration": dur, "size": int(mb * 1e6), "format": fmt, "bitrate": int(vbr),
            "video": {"codec": "h264", "profile": "High", "w": w, "h": h, "fps": fps, "pix_fmt": "yuv420p",
                      "bitrate": int(vbr)},
            "audio": {"codec": "aac", "rate": 48000, "channels": 2, "bitrate": 192000} if audio else None}


AN_OK = {"black": [], "freeze": [], "silence": 0.0, "lufs": -14.2, "true_peak": -1.5}
TXT_OK = {"frames": 40, "wm": [], "cjk": [], "zones": {}}
POST = {"platforms": {"tiktok": {"caption": "Xe máy lên đỉnh tháp 828 m 🏍 #xemay #mạohiểm #redbull #xuhuong"}}}


def by_key(items):
    return {x["key"]: x for x in items}


def test_perfect_video_passes():
    items = tc.evaluate(_info(), AN_OK, TXT_OK, "dub", POST)
    v = tc.verdict(items)
    assert v["verdict"] == "pass", [x for x in items if x["status"] in ("warn", "fail")]
    assert v["score"] == 100 and not v["fixable"]


def test_landscape_low_loudness_is_fixable_warning():
    an = dict(AN_OK, lufs=-23.0)
    items = tc.evaluate(_info(w=1920, h=1080), an, TXT_OK, "dub", POST)
    k = by_key(items)
    assert k["aspect"]["status"] == "warn" and k["loudness"]["status"] == "warn"
    v = tc.verdict(items)
    assert v["verdict"] == "warn" and {"aspect", "loudness"} <= set(v["fixable"])


def test_leftover_platform_watermark_fails_fyf():
    txt = dict(TXT_OK, wm=[{"t": 3.0, "text": "抖音号: abc123"}, {"t": 4.0, "text": "抖音号: abc123"}])
    k = by_key(tc.evaluate(_info(), AN_OK, txt, "dub", POST))
    assert k["wm"]["status"] == "fail" and "抖音" in k["wm"]["detail"]


def test_limits():
    k = by_key(tc.evaluate(_info(w=480, h=854, dur=2.0, mb=300), AN_OK, TXT_OK, "dub", POST))
    assert k["resolution"]["status"] == "fail" and k["duration"]["status"] == "fail"
    assert k["size"]["status"] == "warn"
    k = by_key(tc.evaluate(_info(dur=700), AN_OK, TXT_OK, "dub", POST))
    assert k["duration"]["status"] == "warn" and k["rewards"]["status"] == "info"


def test_sub_only_is_less_original_and_caption_rules():
    post = {"platforms": {"tiktok": {"caption": "视频 #a #b #c #d #e #f #g #h #i"}}}
    k = by_key(tc.evaluate(_info(), AN_OK, TXT_OK, "sub", post))
    assert k["original"]["status"] == "warn"
    assert k["hashtags"]["status"] == "warn" and k["caption_lang"]["status"] == "warn"
    k = by_key(tc.evaluate(_info(), AN_OK, TXT_OK, "dub", None))
    assert k["caption"]["status"] == "warn"


def test_safe_zone_mapping():
    # vertical video: subtitle at 85% height sits under the caption area
    assert tc.unsafe_zone(tc.to_screen((100, 0.84 * 1920, 980, 0.90 * 1920), 1080, 1920)) == "bottom"
    # landscape video is shown in the middle band of the phone screen: its bottom subtitles are safe
    assert tc.unsafe_zone(tc.to_screen((300, 900, 1600, 1000), 1920, 1080)) is None
    # text on the right edge in the middle of a vertical video is under the like/comment buttons
    assert tc.unsafe_zone(tc.to_screen((960, 1000, 1075, 1100), 1080, 1920)) == "right"
    txt = dict(TXT_OK, zones={"bottom": [{"t": float(i), "text": "Phụ đề"} for i in range(10)]})
    k = by_key(tc.evaluate(_info(), AN_OK, txt, "dub", POST))
    assert k["zone_bottom"]["status"] == "warn" and "zones" not in k


def test_own_handle_is_not_a_foreign_watermark():
    hs = tc._own_handles({"brand_tiktok": "kenhcuatoi"})
    assert tc._is_own("@kenhcuatoi", hs) and not tc._is_own("抖音号: xyz", hs)
    assert tc._is_own("@ke", hs) and not tc._is_own("@zz", hs)      # partial OCR read of a moving handle


def test_bitrate_fits_app_limit():
    assert tc.video_kbps(30) == 8000
    long = tc.video_kbps(600)
    assert 2500 <= long < 8000 and (long + 192) * 600 / 8000 <= tc.APP_LIMIT_MB


def _ffmpeg_ok():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg not installed")
def test_make_tiktok_fixes_landscape_and_loudness(job, tmp_path):
    src = tmp_path / "output.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=6",
                    "-f", "lavfi", "-i", "sine=f=440:d=6", "-af", "volume=-30dB", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True)
    info = tc.probe(src)
    an = tc.analyse(job, src, info)
    assert an["lufs"] is not None and an["lufs"] < -30
    assert tc.verdict(tc.evaluate(info, an, None, "dub", POST))["fixable"]
    out = tc.make_tiktok(job, tmp_path, {"video_encoder": "libx264"}, "output.mp4")
    i2 = tc.probe(tmp_path / out)
    assert (i2["video"]["w"], i2["video"]["h"]) == (1080, 1920)
    a2 = tc.analyse(job, tmp_path / out, i2)
    assert abs(a2["lufs"] + 14) < 2.5
    k = by_key(tc.evaluate(i2, a2, None, "dub", POST))
    assert k["aspect"]["status"] == "ok" and k["loudness"]["status"] == "ok"


def test_outro_card_lists_every_handle_on_its_own_line():
    from dubber import render
    rows = render.outro_rows({"brand_tiktok": "kenh", "brand_youtube": "kenh2"}, 1080, 1920, 10.0, 12.5)
    assert len(rows) == 2 and rows[0].startswith("Dialogue: 3,0:00:10.00,0:00:12.50")
    assert "Theo dõi\\N" in rows[0] and "@kenh\\N@kenh2" in rows[0]
    assert "hẹn gặp" in rows[1]
    assert "kênh để xem thêm" in render.outro_rows({}, 1080, 1920, 0, 2)[0]       # no handles set
