"""Estimate (steps, money, history learning), post kit helpers, step timing."""
import json

from dubber import estimate, postgen


def base(**kw):
    s = {"output_mode": "dub", "separate_music": True, "voice_gender_mode": "auto", "hide_original_subs": "auto",
         "wm_remove": "auto", "screen_text": False, "post_summary": True, "translate_provider": "claude_cli",
         "tts_provider": "edge", "preview_seconds": 30, "output_short_side": 1080}
    s.update(kw)
    return s


def test_steps_follow_mode():
    assert "tts" in estimate.steps_for(base()) and "separate" in estimate.steps_for(base())
    sub = estimate.steps_for(base(output_mode="sub"))
    assert "tts" not in sub and "separate" not in sub and "transcribe" in sub
    text = estimate.steps_for(base(output_mode="text"))
    assert "transcribe" not in text and "screen_text" in text and "render" in text


def test_subscription_cli_is_free_api_is_priced():
    free = estimate.translation_cost(300, base())
    assert free["usd"] == 0 and "hạn mức" in free["note"]
    api = estimate.translation_cost(300, base(translate_provider="anthropic", anthropic_model="claude-sonnet-5-5"))
    assert 0.005 < api["usd"] < 0.5
    unknown = estimate.translation_cost(300, base(translate_provider="openai", openai_model="my-local-model"))
    assert unknown["usd"] is None


def test_elevenlabs_credits(monkeypatch):
    monkeypatch.setattr(estimate, "elevenlabs_quota", lambda s: {"used": 9000, "limit": 10000})
    m, warn = estimate.tts_cost(300, base(tts_provider="elevenlabs", elevenlabs_model="eleven_multilingual_v2"))
    assert warn and m["usd"] > 0                      # 300 s ≈ 5100 credits > 1000 left
    monkeypatch.setattr(estimate, "elevenlabs_quota", lambda s: {"used": 0, "limit": 100000})
    m, warn = estimate.tts_cost(300, base(tts_provider="elevenlabs", elevenlabs_model="eleven_flash_v2_5"))
    assert warn is None and m["usd"] == 0


def test_history_overrides_defaults(monkeypatch, tmp_path):
    from dubber import config
    monkeypatch.setattr(config, "JOBS", tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "job.json").write_text(json.dumps({"video_duration": 100, "steps": [
        {"id": "separate", "status": "done", "secs": 105}]}), encoding="utf-8")
    e = estimate.estimate(200, base())
    sep = next(x for x in e["steps"] if x["id"] == "separate")
    assert sep["samples"] == 1 and sep["secs"] == 5 + 1.0 * 200
    assert e["total_secs"] == sum(x["secs"] for x in e["steps"]) and e["total_vnd"] > 0


def test_step_records_duration():
    from dubber.jobs import Job
    j = Job.__new__(Job)
    import threading
    j._lock = threading.Lock()
    j.steps = {"render": {"status": "pending", "progress": 0, "detail": ""}}
    j._touch = lambda force=True: None
    j.step("render", status="running")
    j.step("render", status="done")
    assert "secs" in j.steps["render"] and "_t0" not in j.steps["render"]


def test_hashtag_cleanup():
    assert postgen._plain_tag("Lịch sử Trung Quốc") == "#lichsutrungquoc"
    assert postgen._clean_tags(["#XuHuong", "Đà Nẵng", "#xuhuong", "", "#a"]) == ["#xuhuong", "#danang"]
