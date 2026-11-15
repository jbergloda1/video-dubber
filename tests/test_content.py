import pytest

from dubber import content


def L(a, b):
    return {"start": a, "end": b, "text": "x"}


# ---- silences
def test_silences_include_the_edges_and_merge_overlaps():
    lines = [L(4.0, 6.0), L(5.5, 7.0), L(8.0, 9.0), L(20.0, 21.0)]
    assert content.silences(lines, 30.0) == [(0.0, 4.0), (9.0, 20.0), (21.0, 30.0)]


def test_short_gaps_are_not_silences():
    assert content.silences([L(0.0, 2.0), L(4.5, 6.0)], 8.0) == []


# ---- CutMap
def test_cut_map_shifts_times_after_a_cut():
    m = content.CutMap([{"from": 10.0, "to": 15.0}, {"from": 30.0, "to": 32.0}], 60.0)
    assert m.removed == 7.0 and m.new_total == 53.0
    assert m.t(5.0) == 5.0 and m.t(20.0) == 15.0 and m.t(40.0) == 33.0
    assert m.t(12.0) == 10.0                               # inside a cut → where the cut is
    assert m.keep() == [(0.0, 10.0), (15.0, 30.0), (32.0, 60.0)]
    for x in (0.0, 9.0, 16.0, 33.0, 59.0):
        assert m.back(m.t(x)) == pytest.approx(x)


def test_cut_map_merges_overlaps_and_ignores_disabled_cuts():
    m = content.CutMap([{"from": 10, "to": 14}, {"from": 12, "to": 18}, {"from": 40, "to": 45, "enabled": False}], 50)
    assert m.cuts == [(10.0, 18.0)] and m.new_total == 42.0
    assert m.span(11, 13) is None and m.span(5, 20) == (5.0, 12.0)


# ---- validate
def test_validate_repairs_a_sloppy_plan():
    raw = {"hook": {"vo": "Sáu năm trước…", "overlay": "Giờ tôi có Maybach", "start": 0, "end": 9},
           "narration": [{"start": 20, "text": "Giờ tôi có tất cả."}, {"start": 400, "text": "ngoài video"},
                         {"start": 15, "text": "Năm đó tôi chẳng có gì.", "duck_original": 3}],
           "cuts": [{"from": 15, "to": 18}, {"from": 17, "to": 20}, {"from": 50, "to": 50.1}],
           "replace_screentext": [{"id": 52, "vo": ["Năm thứ sáu… cô ấy đi lấy chồng."]}, {"id": 999, "vo": ["x"]}],
           "post": {"caption": "Phim ngắn buồn #fyp", "hashtags": ["phimngan", "#xuhuong", "#fyp", "ngontinh"]},
           "ending": {"overlay": "Ghế phụ trống rồi", "loop_vo": "Tôi cũng từng hạnh phúc…"}}
    p, probs = content.validate(raw, 290.0, small_ids=[52], silences_=[(14.0, 31.0)])
    assert p["hook"]["end"] == 3.0                                           # hook ≤ 3 s
    assert [n["start"] for n in p["narration"]] == [15.0, 20.0]             # sorted, out-of-range dropped
    assert p["narration"][0]["duck_original"] == 1.0
    assert p["cuts"] == [{"from": 15.0, "to": 20.0, "reason": "", "enabled": True}]
    assert [r["id"] for r in p["replace_screentext"]] == [52]
    assert p["post"]["hashtags"] == ["#phimngan", "#ngontinh"] and "#fyp" not in p["post"]["caption"]
    assert any("#999" in x for x in probs)
    for k in ("title_bar", "retention_texts", "cover", "teasers", "ending"):
        assert k in p


def test_validate_flags_an_untold_long_silence_and_absurd_cuts():
    p, probs = content.validate({"hook": {"vo": "x"}}, 100.0, silences_=[(40.0, 52.0)])
    assert any("40–52" in x for x in probs)
    p, probs = content.validate({"hook": {"vo": "x"}, "cuts": [{"from": 0, "to": 80}]}, 100.0)
    assert p["cuts"] == [] and any("60%" in x for x in probs)


def test_keyframes_cover_scenes_silences_and_text_without_duplicates():
    tl = {"duration": 60.0, "scenes": [0.0, 10.0, 10.5, 30.0], "silences": [(14.0, 31.0)],
          "small_text": [{"start": 50.0, "end": 52.0}]}
    fr = content.keyframe_times(tl)
    ts = [f["t"] for f in fr]
    assert ts == sorted(ts) and all(b - a >= 1.2 for a, b in zip(ts, ts[1:]))
    assert any(f["kind"] == "text" for f in fr) and any(f["kind"] == "silence" for f in fr)


# ---- video type (rules, no AI)
def TL(dur, lines, small=()):
    return {"duration": dur, "lines": lines, "silences": content.silences(lines, dur), "small_text": list(small)}


def SP(a, b, spk="S1"):
    return {"start": a, "end": b, "speaker": spk}


def test_classify_short_sparse_dense_narrated():
    assert content.classify_rules(TL(20, [SP(0, 5)]))[0] == "short_clip"
    sparse = [SP(i * 10, i * 10 + 4, "S1" if i % 2 else "S2") for i in range(20)]           # 60 % silence
    assert content.classify_rules(TL(200, sparse))[0] == "story_sparse"
    dense = [SP(i * 2.0, i * 2.0 + 1.9, "S1" if i % 2 else "S2") for i in range(100)]      # talk all the time
    assert content.classify_rules(TL(200, dense))[0] == "story_dense"
    narr = [SP(i * 2.0, i * 2.0 + 1.9, "S2" if i % 10 == 0 else "S1") for i in range(100)]  # one voice 90 %
    assert content.classify_rules(TL(200, narr))[0] == "narrated"
    assert content.classify_rules(TL(200, dense), {"narrator_remove": True})[0] == "narrated"
    assert content.classify_rules(TL(200, dense), {"content_short_s": 300})[0] == "short_clip"   # threshold from settings


# ---- cuts never touch dialogue or the hook; narration never talks over dialogue
def test_cuts_are_clipped_to_silences_and_spare_the_hook():
    lines = [L(0.4, 13.0), L(31.5, 40.0)]
    sil = content.silences(lines, 60.0)                      # (13, 31.5), (40, 60)
    p, probs = content.validate({"hook": {"vo": "x", "end": 3}, "cuts": [{"from": 0, "to": 17}, {"from": 25, "to": 35}]},
                                60.0, silences_=sil, lines=lines)
    assert all(13.0 <= c["from"] and c["to"] <= 31.5 for c in p["cuts"])
    assert any("lấn vào thoại" in x for x in probs)
    p, _ = content.validate({"hook": {"vo": "x", "end": 3}, "narration": [{"start": 5, "end": 8, "text": "đè thoại"},
                                                                          {"start": 20, "end": 23, "text": "trong lặng"}]},
                            60.0, silences_=sil, lines=lines)
    assert [n["text"] for n in p["narration"]] == ["trong lặng"]


def test_short_clip_and_narrated_scopes():
    raw = {"video_type": "short_clip", "hook": {"overlay": "x"}, "narration": [{"start": 1, "text": "a"}],
           "cuts": [{"from": 5, "to": 9}], "teasers": [{"from": 0, "to": 20}]}
    p, _ = content.validate(raw, 25.0)
    assert p["narration"] == [] and p["cuts"] == [] and p["teasers"] == []
    p, _ = content.validate(dict(raw, video_type="narrated", cuts=[]), 100.0)
    assert p["narration"] == [] and p["video_type"] == "narrated"


# ---- narrated: the narrator's own lines are rewritten (same times), never a second narrator
def test_narrated_rewrite_is_matched_to_line_times_and_applied():
    lines = [dict(SP(1.0, 3.0, "S1"), tgt="cũ một"), dict(SP(4.0, 6.0, "S2"), tgt="nhân vật"), dict(SP(7.0, 9.0, "S1"), tgt="cũ hai")]
    assert content.narrator_speaker(lines) == "S1"
    p, _ = content.validate({"video_type": "narrated", "hook": {"vo": "x"},
                             "rewrite": [{"start": 1.1, "text": "mới một"}, {"start": 30, "text": "lạc"}, {"start": 7.0, "text": "mới hai"}]},
                            20.0, lines=lines)
    assert [x["start"] for x in p["rewrite"]] == [1.0, 7.0]
    assert content.apply_rewrite(lines, p) == 2
    assert [l["tgt"] for l in lines] == ["mới một", "nhân vật", "mới hai"] and lines[0]["tgt_ai"] == "cũ một"
    assert content.apply_rewrite(lines, p) == 0                     # applied once
    p2, _ = content.validate({"video_type": "story_sparse", "rewrite": [{"start": 1.0, "text": "x"}]}, 20.0, lines=lines)
    assert p2["rewrite"] == []                                       # only narrated videos rewrite


# ---- narration quality checks (sent back to the AI for one rewrite)
NOTES = {"frames": [{"t": 0.6, "desc": "Xe sang dừng cạnh xe điện ở ngã tư", "era": "hien_tai"},
                    {"t": 14.5, "desc": "Chàng trai ngồi trong xe sang", "era": "hien_tai"},
                    {"t": 21.0, "desc": "Ảnh khoá màn hình năm 2019", "era": "hoi_uc"}]}


def _plan(**k):
    base = {"persona": {"me": "tôi", "her": "cô ấy", "tone": "tram_it_chu"},
            "hook": {"vo": "Sáu năm trước, người ngồi sau chiếc xe điện đó… là cô ấy.", "overlay": "Giờ tôi có Maybach. Nhưng ghế phụ trống."},
            "hooks": [{"vo": "Sáu năm trước, người ngồi sau chiếc xe điện đó… là cô ấy.", "overlay": "Giờ tôi có Maybach. Nhưng ghế phụ trống."},
                      {"vo": "Tôi giấu cô ấy một chuyện…", "overlay": "Giàu rồi, nhưng muộn"},
                      {"vo": "Nếu ngày đó tôi nói thật…?", "overlay": "Một câu chưa kịp nói"}],
            "narration": [{"start": 21.0, "text": "Năm đó, tôi chẳng có gì."}],
            "ending": {"overlay": "x", "loop_vo": "Tôi cũng từng hạnh phúc… như cậu trai trên chiếc xe điện kia."}}
    base.update(k)
    return base


def test_a_good_plan_passes():
    assert content.quality_check(_plan(), NOTES, {"silences": []}) == []


def test_mixed_forms_of_address_are_flagged():
    p = _plan(narration=[{"start": 21.0, "text": "Năm đó, em vẫn chờ tôi."}])
    assert any("xưng hô" in x for x in content.quality_check(p, NOTES, {}))


def test_past_words_on_a_present_frame_and_abstract_feelings_are_flagged():
    p = _plan(narration=[{"start": 14.6, "text": "Ngày ấy, tôi còn nghèo."},
                         {"start": 21.0, "text": "Tôi đã nghe lời hẹn ấy như một lời đảm bảo."}])
    out = content.quality_check(p, NOTES, {})
    assert any("HIỆN TẠI" in x for x in out) and any("trừu tượng" in x for x in out)


def test_loop_that_only_describes_and_hook_that_only_summarises_are_flagged():
    p = _plan(hook={"vo": "Có xe sang rồi, lại mất người thương.", "overlay": "Có xe sang, mất người thương"},
              ending={"overlay": "x", "loop_vo": "Xe sang dừng cạnh xe điện ở ngã tư."})
    out = content.quality_check(p, NOTES, {})
    assert any("loop" in x for x in out) and any("hook.vo" in x for x in out) and any("hook.overlay" in x for x in out)


def test_translation_follows_the_plan_voice_only_with_a_plan():
    assert content.translation_voice(None) == ""
    v = content.translation_voice({"persona": {"tone": "trầm", "me": "tôi", "her": "cô ấy"}, "video_type": "narrated"})
    assert "cô ấy" in v and "trầm" in v and "THUYẾT MINH" in v


def test_film_watching_frames_need_only_the_video():
    fr = content.video_keyframes([0.0, 3.0, 3.5, 40.0], 120.0)
    ts = [f["t"] for f in fr]
    assert ts == sorted(ts) and all(b - a >= 2.0 for a, b in zip(ts, ts[1:])) and len(fr) <= 45
    assert len(content.video_keyframes([i * 0.5 for i in range(400)], 300.0)) <= 45
