"""Regression (job 8414): a sentence split over several lines was translated piece by piece in Chinese word order
("Người đây sáng mà thiếu một bát" / "mỡ đuôi cừu mềm như tàu hũ," / "thì coi như phí cả ngày!") and each piece was
voiced as its own clip with silence between them. Lines must be whole sentences; long ones are split only on screen."""
from dubber import render, subsplit, translate


def _l(a, b, tgt, **kw):
    return {"src": "x", "tgt": tgt, "start": a, "end": b, **kw}


def test_half_sentences_are_joined():
    lines = [_l(8.68, 11.56, "Có tiền chưa chắc mua được món hiếm này đâu!"),
             _l(12.02, 13.9, "Người đây sáng mà thiếu một bát"),
             _l(13.9, 16.52, "mỡ đuôi cừu mềm như tàu hũ,"),
             _l(17.08, 18.52, "thì coi như phí cả ngày!"),
             _l(18.52, 20.3, "Để kịp buổi chợ,"),
             _l(20.3, 21.78, "quê mở lúc sáu giờ sáng,")]
    out, n = translate.join_fragments(lines)
    assert n == 3
    assert [l["tgt"] for l in out] == [
        "Có tiền chưa chắc mua được món hiếm này đâu!",
        "Người đây sáng mà thiếu một bát mỡ đuôi cừu mềm như tàu hũ, thì coi như phí cả ngày!",
        "Để kịp buổi chợ, quê mở lúc sáu giờ sáng,"]
    assert (out[1]["start"], out[1]["end"]) == (12.02, 18.52)


def test_join_respects_sentence_end_pause_speaker_and_length():
    # full stop, then capital → two sentences
    assert translate.join_fragments([_l(0, 2, "Xong rồi."), _l(2.1, 4, "rồi sao")])[1] == 0
    # a long pause is a real break
    assert translate.join_fragments([_l(0, 2, "Tôi đi"), _l(3.5, 4, "về nhà.")])[1] == 0
    # two different speakers are never merged
    assert translate.join_fragments([_l(0, 2, "Anh đi", speaker="S1"), _l(2.1, 4, "đâu vậy?", speaker="S2")])[1] == 0
    # never longer than JOIN_MAX_S
    long = [_l(i * 2.0, i * 2.0 + 1.9, "và cứ thế") for i in range(12)]
    out, _ = translate.join_fragments(long)
    assert all(l["end"] - l["start"] <= translate.JOIN_MAX_S for l in out)
    # the opening hook stays apart
    assert translate.join_fragments([_l(0, 1, "Xem đến cuối", hook=True), _l(1.1, 3, "mới biết")])[1] == 0


def test_prompt_asks_for_whole_sentences():
    sysmsg, _ = translate.build_prompt([{"start": 0, "end": 2, "text": "你好"}], "vi", "tự nhiên")
    assert "CÂU TRỌN Ý" in sysmsg and "BẮT BUỘC tách thành nhiều cụm tại dấu phẩy" not in sysmsg


def test_split_on_natural_breaks_only():
    s = "Ở đây, sáng nào không có bát mỡ đuôi cừu mềm như tàu hũ thì coi như phí cả ngày!"
    parts = subsplit.split(s)
    assert " ".join(parts) == s
    assert all(len(p.split()) <= subsplit.MAX_WORDS + 4 for p in parts)   # never cut a phrase just for length
    assert all(len(p.split()) >= subsplit.MIN_WORDS for p in parts)
    assert parts[1].startswith("thì")            # breaks before the linking word, not mid-phrase
    assert subsplit.split("Câu ngắn thôi.") == ["Câu ngắn thôi."]


def test_cues_follow_the_voice_and_cover_the_sentence():
    text = "Để kịp buổi chợ quê lúc sáu giờ sáng, trời chưa sáng bác Bu đã chở mấy con cừu đuôi béo về."
    cs = subsplit.cues(text, 18.5, 24.9, read_end=24.6)
    assert len(cs) >= 2 and cs[0][0] == 18.5 and cs[-1][1] == 24.9
    assert all(a < b for a, b, _ in cs)
    assert all(cs[k][1] == cs[k + 1][0] for k in range(len(cs) - 1))     # no gaps, no overlaps
    assert " ".join(t for _, _, t in cs) == text


def test_srt_shows_sentences_in_cues(tmp_path):
    lines = [{"tgt": "Ở đây, sáng nào không có bát mỡ đuôi cừu mềm như tàu hũ thì coi như phí cả ngày!",
              "start": 12.0, "end": 18.5, "vstart": 12.0, "vend": 17.8},
             {"tgt": "Bác Bu năm nay bảy mươi.", "start": 18.6, "end": 20, "vstart": 18.6, "vend": 19.9}]
    p = tmp_path / "s.srt"
    render.write_srt(lines, p, "dub")
    blocks = p.read_text(encoding="utf-8").strip().split("\n\n")
    assert len(blocks) == 3 and blocks[-1].endswith("Bác Bu năm nay bảy mươi.")
