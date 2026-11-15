from dubber import analyze


def f(**k):
    base = {"speech_ratio": 0.6, "language": "zh", "speakers": 1, "text_ratio": 0.0, "subs_ratio": 0.0,
            "lines": [{"start": 0, "end": 2, "text": "你好"}]}
    return {**base, **k}


def test_narration_is_dubbed():
    r = analyze.rules(f())
    assert r["mode"] == "dub" and not r["options"]["screen_text"]


def test_dialogue_gets_voiceover():
    assert analyze.rules(f(speakers=3, speech_ratio=0.5))["mode"] == "voiceover"


def test_no_speech_with_captions_translates_text_only():
    r = analyze.rules(f(speech_ratio=0.0, lines=[], text_ratio=0.4))
    assert r["mode"] == "text" and r["options"]["screen_text"]


def test_no_speech_no_text_keeps_audio():
    assert analyze.rules(f(speech_ratio=0.01, lines=[], text_ratio=0.05))["mode"] == "sub"


def test_already_vietnamese_is_not_dubbed():
    assert analyze.rules(f(language="vi"))["mode"] in ("sub", "text")


def test_on_screen_text_turns_on_screen_translation():
    assert analyze.rules(f(text_ratio=0.5))["options"]["screen_text"] is True


def test_recommend_falls_back_to_rules_without_ai(monkeypatch):
    from dubber.providers import orchestrator

    def no_ai(job, s, task, what):
        raise orchestrator.NoProvider("không có provider")
    monkeypatch.setattr(orchestrator, "run", no_ai)
    r = analyze.recommend(None, f(speakers=2, speech_ratio=0.5), {"target_lang": "vi"}, "title")
    assert r["mode"] == "voiceover" and r["by"] == "rules"


def test_summary_line():
    res = {"features": f(subs_ratio=1.0, text_ratio=0.25), "rec": {"mode": "dub", "reason": "x"}}
    s = analyze.summary(res)
    assert s.startswith("🎙 Lồng tiếng") and "60%" in s and "1 người nói" in s and "phụ đề cứng 100%" in s


def test_recommendations_turn_subtitles_on_except_text_only():
    assert analyze.rules(f())["options"]["burn_subtitles"] is True
    assert analyze.rules(f(speakers=3, speech_ratio=0.5))["options"]["burn_subtitles"] is True
    assert "burn_subtitles" not in analyze.rules(f(speech_ratio=0.0, lines=[], text_ratio=0.4))["options"]
