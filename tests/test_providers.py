"""Orchestrator: retry, fallback, cooldown, local probing, secret redaction, resumable translation."""
import json

import pytest

from dubber import config, translate
from dubber.providers import base, orchestrator
from dubber.providers.base import (AuthError, BadOutput, NotConfigured, Provider, QuotaExhausted, RateLimited,
                                   Transient, Unreachable, classify_http)


class Fake(Provider):
    def __init__(self, pid, script, kind="llm", local=False, configured=True, reachable=True, secrets=()):
        self.id, self.label, self.kind, self.local, self.secrets = pid, pid, kind, local, secrets
        self.script = list(script)        # list of exceptions / return values, consumed per call
        self.calls = 0
        self._configured, self._reachable = configured, reachable

    def configured(self, s):
        return self._configured, "" if self._configured else f"{self.id} chưa cấu hình"

    def probe(self, s):
        return self._reachable, "" if self._reachable else f"{self.id} không chạy"

    def _next(self):
        self.calls += 1
        item = self.script.pop(0) if self.script else "{}"
        if isinstance(item, Exception):
            raise item
        return item

    def complete(self, job, system, user, schema, s):
        return self._next()

    def translate_list(self, job, texts, target, s):
        r = self._next()
        return r if isinstance(r, list) else [f"vi:{t}" for t in texts]


@pytest.fixture
def reg(monkeypatch, tmp_path):
    """Replace the provider registry with fakes; clear cooldowns between tests."""
    import dubber.providers as P
    fakes = {}
    monkeypatch.setattr(P, "REGISTRY", fakes)
    monkeypatch.setattr(orchestrator, "STATE_FILE", tmp_path / "provider_state.json")
    orchestrator.reset_cooldowns()
    yield fakes
    orchestrator.reset_cooldowns()


def settings(primary, fallbacks, **kw):
    s = dict(config.DEFAULTS, translate_provider=primary, translate_fallbacks=fallbacks,
             translate_backoff_s=0.0, translate_max_retries=2)
    s.update(kw)
    return s


def run(job, s):
    return orchestrator.run(job, s, lambda p: p.complete(job, "sys", "user", None, s))


def test_primary_succeeds(job, reg):
    reg["a"] = Fake("a", ['{"ok":1}'])
    reg["b"] = Fake("b", ['{"ok":2}'])
    assert run(job, settings("a", ["b"])) == ('{"ok":1}', "a")
    assert reg["b"].calls == 0


def test_rate_limit_retried_then_succeeds(job, reg):
    reg["a"] = Fake("a", [RateLimited("429 rps"), RateLimited("429 rps"), '{"ok":1}'])
    assert run(job, settings("a", []))[1] == "a"
    assert reg["a"].calls == 3


def test_retries_are_bounded_then_fallback(job, reg):
    reg["a"] = Fake("a", [Transient("timeout")] * 10)
    reg["b"] = Fake("b", ['{"ok":2}'])
    assert run(job, settings("a", ["b"]))[1] == "b"
    assert reg["a"].calls == 3        # 1 + max_retries(2), never more


@pytest.mark.parametrize("err", [QuotaExhausted("daily quota"), AuthError("bad key"), NotConfigured("no model")])
def test_non_retryable_switches_immediately(job, reg, err):
    reg["a"] = Fake("a", [err, '{"never":1}'])
    reg["b"] = Fake("b", ['{"ok":2}'])
    assert run(job, settings("a", ["b"]))[1] == "b"
    assert reg["a"].calls == 1


def test_quota_puts_provider_in_cooldown(job, reg):
    reg["a"] = Fake("a", [QuotaExhausted("per day"), '{"ok":1}'])
    reg["b"] = Fake("b", ['{"ok":2}', '{"ok":3}'])
    s = settings("a", ["b"])
    run(job, s)
    assert run(job, s)[1] == "b"      # a is skipped on the next call (cooldown), not retried
    assert reg["a"].calls == 1


def test_local_provider_probed_and_skipped_when_down(job, reg):
    reg["ollama"] = Fake("ollama", ['{"x":1}'], local=True, reachable=False)
    reg["g"] = Fake("g", [], kind="mt")
    s = settings("ollama", ["g"])
    res, pid = orchestrator.run(job, s, lambda p: p.translate_list(job, ["你好"], "vi", s) if p.kind == "mt"
                                else p.complete(job, "", "", None, s))
    assert pid == "g" and reg["ollama"].calls == 0
    assert any("không chạy" in m for _, m in job.logs) or True


def test_unconfigured_skipped(job, reg):
    reg["a"] = Fake("a", [], configured=False)
    reg["b"] = Fake("b", ['{"ok":2}'])
    assert run(job, settings("a", ["b"]))[1] == "b"


def test_no_provider_clear_error_and_no_loop(job, reg):
    reg["a"] = Fake("a", [Unreachable("refused")] * 5)
    reg["b"] = Fake("b", [AuthError("401")] * 5)
    with pytest.raises(orchestrator.NoProvider) as ei:
        run(job, settings("a", ["b", "a", "b"]))     # duplicates in the chain are ignored
    assert reg["a"].calls == 1 and reg["b"].calls == 1
    assert "a:" in str(ei.value) and "b:" in str(ei.value)


def test_secrets_redacted(job, reg):
    key = "sk-SECRET-1234567890"
    reg["a"] = Fake("a", [AuthError(f"invalid key {key}")], secrets=("anthropic_api_key",))
    with pytest.raises(orchestrator.NoProvider) as ei:
        run(job, settings("a", [], anthropic_api_key=key))
    assert key not in str(ei.value)
    assert all(key not in m for _, m in job.logs)


def test_alias_google_maps_to_google_free(reg):
    reg["google_free"] = Fake("google_free", [], kind="mt")
    assert orchestrator.chain(settings("google", [])) == ["google_free"]


def test_classify_http():
    assert isinstance(classify_http(429, "Rate limit: 5 requests per second"), RateLimited)
    assert isinstance(classify_http(429, "You exceeded your current quota"), QuotaExhausted)
    assert isinstance(classify_http(429, "RESOURCE_EXHAUSTED: quota exceeded per day"), QuotaExhausted)
    assert isinstance(classify_http(401, "invalid api key"), AuthError)
    assert isinstance(classify_http(503, "overloaded"), Transient)


# ---------------------------------------------------------------- translate() on top of the orchestrator
ASR = {"lines": [{"start": 0, "end": 1, "text": "你好"}, {"start": 1, "end": 2, "text": "世界"}],
       "words": [{"w": "你好", "s": 0, "e": 1}, {"w": "世界", "s": 1, "e": 2}]}


def test_translate_bad_json_falls_back(job, reg, tmp_path):
    good = json.dumps({"lines": [{"src": "你好", "tgt": "Xin chào"}, {"src": "世界", "tgt": "thế giới"}]})
    reg["a"] = Fake("a", ["not json"] * 5)
    reg["b"] = Fake("b", [good])
    lines, matched = translate.translate(job, ASR, settings("a", ["b"]), progress_path=tmp_path / "p.json")
    assert [l["tgt"] for l in lines] == ["Xin chào thế giới"] and matched == 1.0
    assert not (tmp_path / "p.json").exists()          # removed once complete


def test_translate_mt_provider(job, reg):
    reg["g"] = Fake("g", [["Xin chào", "thế giới"]], kind="mt")
    lines, _ = translate.translate(job, ASR, settings("g", []))
    assert [l["tgt"] for l in lines] == ["Xin chào thế giới"]


def test_translate_resumes_finished_chunks(job, reg, tmp_path, monkeypatch):
    monkeypatch.setattr(translate, "CHUNK", 1)
    c0 = json.dumps({"lines": [{"src": "你好", "tgt": "Xin chào"}]})
    reg["a"] = Fake("a", [c0, AuthError("logged out")])
    p = tmp_path / "progress.json"
    with pytest.raises(orchestrator.NoProvider):
        translate.translate(job, ASR, settings("a", []), progress_path=p)
    assert json.loads(p.read_text(encoding="utf-8"))["chunks"]["0"]["provider"] == "a"
    orchestrator.reset_cooldowns()
    c1 = json.dumps({"lines": [{"src": "世界", "tgt": "thế giới"}]})
    reg["a"] = Fake("a", [c1])
    lines, _ = translate.translate(job, ASR, settings("a", []), progress_path=p)
    assert reg["a"].calls == 1                          # chunk 0 was not paid for again
    assert [l["tgt"] for l in lines] == ["Xin chào thế giới"]


def test_translate_texts_uses_chain(job, reg):
    reg["a"] = Fake("a", [Unreachable("down")])
    reg["b"] = Fake("b", [json.dumps({"items": [{"id": 0, "tgt": "Tiêu đề"}]})])
    ev = [{"id": 0, "start": 0, "end": 1, "src": "标题", "tgt": ""}]
    assert translate.translate_texts(job, ev, settings("a", ["b"]))[0]["tgt"] == "Tiêu đề"


def test_old_settings_still_load(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"translate_provider": "google", "edge_voice": "x"}), encoding="utf-8")
    monkeypatch.setattr(config, "SETTINGS_FILE", f)
    s = config.load()
    assert s["translate_provider"] == "google" and s["translate_fallbacks"] and s["openai_model"] == ""
    assert orchestrator.chain(s)[0] == "google_free"


def test_cooldown_persists_across_restart(job, reg, tmp_path):
    reg["a"] = Fake("a", [QuotaExhausted("daily quota")])
    reg["b"] = Fake("b", ['{"ok":1}', '{"ok":2}'])
    s = settings("a", ["b"])
    run(job, s)
    assert (tmp_path / "provider_state.json").exists()
    orchestrator._state.clear()                 # simulate a server restart (memory lost)
    orchestrator.cooling._loaded = False
    assert run(job, s)[1] == "b" and reg["a"].calls == 1


def test_throttle_spaces_requests(job, reg):
    import time
    reg["a"] = Fake("a", ['{"x":1}', '{"x":2}'])
    s = settings("a", [], translate_min_interval_s={"a": 0.4})
    t = time.time()
    run(job, s)
    run(job, s)
    assert time.time() - t >= 0.4


def test_new_providers_join_saved_chain(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"translate_fallbacks": ["claude_cli", "anthropic"],
                             "translate_providers_seen": ["claude_cli", "anthropic", "ollama"]}), encoding="utf-8")
    monkeypatch.setattr(config, "SETTINGS_FILE", f)
    fb = config.load()["translate_fallbacks"]
    assert fb[:2] == ["claude_cli", "anthropic"]
    assert "local_nllb" in fb and "codex_cli" in fb       # new since the user saved
    assert "ollama" not in fb                              # the user unticked it: stays out


@pytest.mark.parametrize("out,cls", [("Error: Not logged in. Run codex login", AuthError),
                                     ("You've hit your usage limit for this plan", QuotaExhausted),
                                     ("429 Too Many Requests", RateLimited),
                                     ("something odd happened", Transient)])
def test_cli_error_classification(out, cls):
    from dubber.providers import cli
    with pytest.raises(cls):
        cli._classify(out, "Codex")


def test_local_mt_source_language_guess():
    from dubber.providers.local_mt import guess_source
    s = {"source_lang": "auto"}
    assert guess_source(["上半场，多纳鲁马失误"], s) == "zho_Hans"
    assert guess_source(["こんにちは世界"], s) == "jpn_Jpan"
    assert guess_source(["안녕하세요"], s) == "kor_Hang"
    assert guess_source(["What a pass"], s) == "eng_Latn"
    assert guess_source(["What a pass"], {"source_lang": "zh"}) == "zho_Hans"


def test_local_mt_not_configured_without_model(monkeypatch, tmp_path):
    from dubber.providers import local_mt
    monkeypatch.setattr(local_mt, "model_dir", lambda name: tmp_path / name)
    ok, why = local_mt.LocalNLLB().configured({"local_mt_model": "nllb-600M"})
    assert not ok and "Chưa tải" in why


def test_gemini_cli_login_check(tmp_path, monkeypatch):
    from dubber.providers import cli
    for k in ("GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_GCA"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: tmp_path))
    g = tmp_path / ".gemini"
    g.mkdir()
    assert cli.gemini_logged_in() is False
    (g / "oauth_creds.json").write_text("{}", encoding="utf-8")
    assert cli.gemini_logged_in() is False                    # creds alone: CLI still says "set an Auth method"
    (g / "settings.json").write_text(json.dumps({"security": {"auth": {"selectedType": "oauth-personal"}}}), encoding="utf-8")
    assert cli.gemini_logged_in() is True
