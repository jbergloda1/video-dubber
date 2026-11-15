"""Regression (round-2 review of job 6cf9): a cut must never clip speech. Every cut edge stays ≥ 0.4 s from any
voice (measured on the real audio, not the transcript), prefers shot changes, crossfades otherwise; narration never
lands on the dialogue; the writer's round-2 rules (no re-reading on-screen text, title bar without the twist…)."""
import random
import subprocess

import numpy as np
import pytest

from dubber import content

FPS = 30


def _ffmpeg_ok():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


def test_safe_cuts_keep_gap_from_speech_random():
    rng = random.Random(7)
    for _ in range(300):
        total = 120.0
        speech, t = [], 0.0
        while t < total - 2:
            t += rng.uniform(0.2, 6.0)
            a = t
            t += rng.uniform(0.3, 4.0)
            speech.append((a, min(t, total)))
        scenes = sorted(rng.uniform(0, total) for _ in range(30))
        want = [(x, x + rng.uniform(0.5, 6)) for x in sorted(rng.uniform(3, total - 6) for _ in range(8))]
        res = content.safe_cuts(want, speech, scenes, total, FPS, protect=3.0)
        prev = -1
        for z in res:
            assert z["from"] >= 3.0 - 1e-6
            assert z["to"] - z["from"] >= 0.5 - 1e-6
            assert z["from"] > prev
            prev = z["to"]
            for e in (z["from"], z["to"]):
                assert content.nearest_speech(e, speech) >= max(0.4, content.CUT_GAP) - 1e-6
                assert abs(e * FPS - round(e * FPS)) < 1e-3          # on the frame grid
            # nothing voiced is removed
            assert all(b <= z["from"] or a >= z["to"] for a, b in speech)


def test_cut_too_close_to_speech_is_dropped_or_shrunk():
    speech = [(10.0, 12.0), (13.2, 15.0)]          # 1.2 s gap: only 0.4 s left inside the safety margins
    assert content.safe_cuts([(12.0, 13.2)], speech, [], 30, FPS) == []
    res = content.safe_cuts([(49.97, 52.5)], [(45.0, 50.33), (53.0, 55.0)], [], 60, FPS)
    assert res and res[0]["from"] >= 50.73 - 1e-6 and res[0]["to"] <= 52.6 + 1e-6


def test_cut_prefers_shot_change_else_crossfade():
    speech = [(0, 5.0), (15.0, 20.0)]
    on = content.safe_cuts([(7.0, 11.0)], speech, [6.8, 11.2], 30, FPS)[0]
    assert (on["from"], on["to"]) == (round(6.8 * FPS) / FPS, round(11.2 * FPS) / FPS) and not on["xfade"]
    off = content.safe_cuts([(7.0, 11.0)], speech, [2.0, 25.0], 30, FPS)[0]
    assert off["xfade"]


def test_fit_reads_never_on_speech():
    speech = [(0.0, 2.0), (6.0, 9.0), (14.0, 20.0)]
    placed, dropped = content.fit_reads([(5.0, 2.5), (9.0, 3.0), (13.0, 5.0)], speech, 30.0)
    for a, b, i in placed:
        assert all(b + 0.3 <= s0 + 1e-9 or a >= s1 + 0.3 - 1e-9 for s0, s1 in speech)
    assert 2 in dropped                               # 5 s line, no 5 s hole within 3 s of 13 s
    assert [i for _, _, i in placed] == [0, 1]
    assert placed[0][0] == pytest.approx(3.2)         # moved back so it ends 0.3 s before the dialogue at 6 s


@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg missing")
def test_energy_spans_find_tone_bursts(tmp_path):
    w = tmp_path / "v.wav"
    on = "between(t,1,2)+between(t,4.5,5)"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:r=16000:d=7",
                    "-af", f"volume='if({on},0.5,0.001)':eval=frame", str(w)], check=True)
    sp = content.energy_spans(w, content.VOCALS_DB)
    assert len(sp) == 2
    assert abs(sp[0][0] - 1) < 0.1 and abs(sp[0][1] - 2) < 0.1 and abs(sp[1][0] - 4.5) < 0.1


@pytest.mark.skipif(not _ffmpeg_ok(), reason="ffmpeg missing")
@pytest.mark.parametrize("xf", [[True, False], [False, True], [True, True]])   # concat before xfade: time bases
def test_crossfade_join_keeps_length_and_sync(tmp_path, xf):
    events = [2.0, 9.0, 15.5]
    cuts = [{"from": 4.0, "to": 6.4}, {"from": 11.3, "to": 13.9}]
    on = "+".join(f"between(t,{e},{e + 0.1})" for e in events)
    src, out = tmp_path / "in.mkv", tmp_path / "out.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s=160x90:r={FPS}:d=20",
                    "-f", "lavfi", "-i", "sine=f=1000:r=48000:d=20", "-filter_complex",
                    f"[0:v]drawbox=c=white:t=fill:enable='{on}'[v];[1:a]volume='if({on},1,0)':eval=frame[a]",
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le",
                    str(src)], check=True)
    cm = content.CutMap(cuts, 20.0)
    vp, ap, vo, ao = content.cut_graph(cm.keep(), "0:v", "0:a", xf, FPS)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", ";".join(vp + ap),
                    "-map", f"[{vo}]", "-map", f"[{ao}]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(out)], check=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "csv=p=0",
                            str(out)], capture_output=True, text=True).stdout
    durs = [float(x.split(",")[1]) for x in probe.split() if "," in x and x.split(",")[1] not in ("", "N/A")]
    assert all(abs(d - cm.new_total) < 0.06 for d in durs), (durs, cm.new_total)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-vf", "format=gray,scale=8:8", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    lum = np.frombuffer(raw, np.uint8).reshape(-1, 64).mean(1)
    flashes = [i / FPS for i in range(1, len(lum)) if lum[i] > 128 >= lum[i - 1]]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
                         capture_output=True).stdout
    env = np.convolve(np.abs(np.frombuffer(raw, np.float32)), np.ones(40) / 40, "same")
    beeps = [i / 8000 for i in range(1, len(env)) if env[i] > 0.03 >= env[i - 1]]
    want = [cm.t(e) for e in events]
    assert len(flashes) == len(beeps) == len(events)
    for f, b, w in zip(flashes, beeps, want):
        assert abs(f - w) < 0.05 and abs(b - w) < 0.05 and abs(f - b) < 0.04


def _tl(**kw):
    tl = {"duration": 240.0, "screen": [], "lines": [], "silences": [], "small_text": []}
    tl.update(kw)
    return tl


def test_quality_round2_rules():
    notes = {"frames": [{"t": 30, "desc": "điện thoại hiện ngày tháng", "text_on_screen": "2019年10月21日"}]}
    plan = {"persona": {"her": "cô ấy", "me": "tôi"}, "title_bar": "Thành công sau khi cô ấy lấy chồng",
            "hook": {"vo": "Ai từng ngồi đó…?", "overlay": "Có xe sang, nhưng tôi vẫn nhìn chiếc xe điện bên kia đường"},
            "hooks": [{"vo": "a…", "overlay": "b nhưng c"}],
            "narration": [{"start": 31, "text": "Ngày ấy là 21 tháng 10 năm 2019."},
                          {"start": 60, "text": "Hôm ấy, cô ấy nhắn sắp tan làm."},
                          {"start": 90, "text": "Hôm ấy, tôi không về."}] + [{"start": 100 + i, "text": f"Câu {i} rất khác"} for i in range(8)],
            "ending": {"loop_vo": "Tôi thèm được đổi chỗ với người lái xe điện kia."}}
    tl = _tl(screen=[{"start": 58, "end": 62, "src": "我快下班了", "tgt": "Em sắp tan làm rồi"}])
    issues = content.quality_check_2(plan, notes, tl)
    text = " | ".join(issues)
    assert "21 tháng 10" in text and "ngày/giờ" in text
    assert "sắp tan làm" in text and "chữ trên màn hình" in text
    assert "cùng mở đầu “hôm ấy" in text
    assert "tối đa 8" in text
    assert "lộ twist" in text
    assert "tối đa 10" in text
    assert "3 phương án" in text


def test_quality_round2_good_plan_passes():
    plan = {"persona": {"her": "cô ấy", "me": "tôi"}, "title_bar": "Chiếc ghế phụ không ai ngồi",
            "hook": {"vo": "Người ngồi sau chiếc xe điện đó… từng là ai?", "overlay": "Có Maybach. Mất người ngồi ghế phụ."},
            "hooks": [{"vo": "Người ngồi sau chiếc xe điện đó… từng là ai?", "overlay": "Có Maybach. Mất người ngồi ghế phụ."},
                      {"vo": "Tôi giấu cô ấy một chuyện…", "overlay": "Giàu rồi, nhưng muộn"},
                      {"vo": "Nếu ngày đó tôi nói thật…?", "overlay": "Một câu chưa kịp nói"}],
            "narration": [{"start": 20, "text": "Tôi giấu cô ấy chuyện đi giao đồ ăn."},
                          {"start": 80, "text": "Chỉ tấm ảnh này, tôi chưa từng đổi."}],
            "ending": {"loop_vo": "Tôi thèm được đổi chỗ với người lái xe điện kia."}}
    assert content.quality_check_2(plan, {"frames": []}, _tl()) == []


def test_validate_keeps_three_hooks_and_choice():
    plan = {"hook": {"vo": "B vo", "overlay": "B ov"},
            "hooks": [{"vo": "A vo", "overlay": "A ov"}, {"vo": "B vo", "overlay": "B ov"}, {"vo": "C vo", "overlay": "C ov"}]}
    p, _ = content.validate(plan, 100.0)
    assert len(p["hooks"]) == 3 and p["hook_choice"] == 1
