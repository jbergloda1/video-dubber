from dubber import styles, translate


def test_fixed_preset_needs_no_llm(job, tmp_path, monkeypatch):
    monkeypatch.setattr(styles, "suggest", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no LLM call")))
    s = styles.resolve(job, tmp_path, {"translate_style_preset": "bua"})
    assert "BỰA" in s["translate_style"]
    assert styles.load(tmp_path)["name"] == "Bựa, hài lầy"


def test_custom_uses_user_text(job, tmp_path):
    s = styles.resolve(job, tmp_path, {"translate_style_preset": "custom", "translate_style": "Giọng cụ già kể chuyện"})
    assert s["translate_style"] == "Giọng cụ già kể chuyện"


def test_auto_suggests_once_and_caches(job, tmp_path, monkeypatch):
    calls = []

    def fake(job, s, title, samples):
        calls.append(title)
        return {"preset": "bua", "note": "gọi nhân vật là 'ông anh Red Bull'", "why": "video mạo hiểm vui", "provider": "x"}
    monkeypatch.setattr(styles, "suggest", fake)
    s0 = {"translate_style_preset": "auto"}
    s = styles.resolve(job, tmp_path, s0, "Xe máy leo tháp", ["a", "b"])
    assert "ông anh Red Bull" in s["translate_style"] and "BỰA" in s["translate_style"]
    styles.resolve(job, tmp_path, s0, "Xe máy leo tháp", ["a", "b"])
    assert len(calls) == 1                       # second run reuses style.json
    st = styles.load(tmp_path)
    assert st["auto"] and st["why"] == "video mạo hiểm vui"
    # switching the preset resolves again (no LLM for a fixed preset)
    s2 = styles.resolve(job, tmp_path, {"translate_style_preset": "serious"})
    assert s2["translate_style"].startswith("Giọng nghiêm túc") and len(calls) == 1


def test_auto_failure_falls_back_to_natural(job, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no provider")
    monkeypatch.setattr(styles, "suggest", boom)
    s = styles.resolve(job, tmp_path, {"translate_style_preset": "auto"})
    assert s["translate_style"] == styles.PRESETS["natural"][1]
    assert any(level == "warn" for level, _ in job.logs)


def test_style_reaches_translation_prompt():
    style = styles.text_for("bua", "biệt danh: Sam lầy")
    sysmsg, _ = translate.build_prompt([{"start": 0, "end": 1, "text": "你好"}], "vi", style)
    assert "BỰA" in sysmsg and "Sam lầy" in sysmsg


def test_comedy_never_for_sensitive_is_in_prompt():
    assert "TUYỆT ĐỐI không chọn \"bua\"" in styles.SYSTEM


def test_first_translation_call_picks_the_style(job, tmp_path, monkeypatch):
    import json
    calls = []

    class P:
        kind, id = "llm", "fake"

        def complete(self, job, system, user, schema, s):
            calls.append(system)
            return json.dumps({"style": "bua", "style_note": "gọi nhân vật là bé hành", "style_why": "ẩm thực vui",
                               "lines": [{"src": "大胆一点", "tgt": "Chơi lớn đi nha!"}]}, ensure_ascii=False)
    monkeypatch.setattr(translate.orchestrator, "run", lambda job, s, task, what: (task(P()), "fake"))
    monkeypatch.setattr(styles, "suggest", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no separate call")))
    asr = {"lines": [{"start": 0.0, "end": 1.0, "text": "大胆一点"}],
           "words": [{"w": c, "s": i * 0.2, "e": i * 0.2 + 0.2} for i, c in enumerate("大胆一点")]}
    s = {"target_lang": "vi", "translate_style": "", "translate_style_preset": "auto", "hook_intro": False}
    assert not styles.cached(tmp_path, s)
    out, _ = translate.translate(job, asr, s, "tiêu đề", style_dir=tmp_path)
    assert len(calls) == 1 and "CHỌN 1 phong cách" in calls[0] and '"style"' in calls[0]
    assert out[0]["tgt"] == "Chơi lớn đi nha!"
    st = styles.load(tmp_path)
    assert st["preset"] == "bua" and "bé hành" in st["text"] and styles.cached(tmp_path, s)
    assert "BỰA" in styles.resolve(job, tmp_path, s)["translate_style"]      # later steps: cached, no AI call
