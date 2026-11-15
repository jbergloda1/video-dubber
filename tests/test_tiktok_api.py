import hashlib
import json
import time
import urllib.parse

import pytest

from dubber import tiktok_api as tk

S = {"tiktok_client_key": "ck123", "tiktok_client_secret": "secret", "tiktok_post_mode": "inbox"}


@pytest.fixture(autouse=True)
def tmp_token(tmp_path, monkeypatch):
    monkeypatch.setattr(tk, "TOKEN_FILE", tmp_path / "tiktok_token.json")
    tk._pending.clear()


def test_pkce_is_hex_sha256_of_a_valid_verifier():
    v, c = tk.pkce_pair()
    assert 43 <= len(v) <= 128 and all(ch.isalnum() or ch in "-._~" for ch in v)
    assert c == hashlib.sha256(v.encode()).hexdigest() and len(c) == 64


def test_login_url_has_the_required_params():
    u = urllib.parse.urlparse(tk.login_url(S))
    q = dict(urllib.parse.parse_qsl(u.query))
    assert u.netloc == "www.tiktok.com" and u.path == "/v2/auth/authorize/"
    assert q["client_key"] == "ck123" and q["response_type"] == "code" and q["code_challenge_method"] == "S256"
    assert q["scope"] == "user.info.basic,video.upload" and q["redirect_uri"] == "http://localhost:8765/api/tiktok/callback/"
    assert q["state"] in tk._pending
    with pytest.raises(tk.TikTokError):
        tk.login_url({})


def test_exchange_checks_state_and_saves_token(monkeypatch):
    sent = {}

    def fake_post(url, fields):
        sent.update(fields)
        return {"access_token": "AT", "refresh_token": "RT", "open_id": "u1", "scope": "video.upload",
                "expires_in": 7200, "refresh_expires_in": 15552000}
    monkeypatch.setattr(tk, "_post_form", fake_post)
    monkeypatch.setattr(tk, "user_info", lambda s: {"display_name": "Kênh Test"})
    with pytest.raises(tk.TikTokError):
        tk.exchange_code(S, "code", "forged-state")
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(tk.login_url(S)).query))["state"]
    tk.exchange_code(S, "the-code", state)
    assert sent["grant_type"] == "authorization_code" and sent["code"] == "the-code" and sent["code_verifier"]
    st = tk.status(S)
    assert st["connected"] and st["display_name"] == "Kênh Test" and "AT" not in json.dumps(st)
    with pytest.raises(tk.TikTokError):                       # a state works only once
        tk.exchange_code(S, "the-code", state)


def test_expired_access_token_is_refreshed_and_rotated(monkeypatch):
    tk.TOKEN_FILE.write_text(json.dumps({"access_token": "OLD", "refresh_token": "RT1", "expires_at": time.time() - 5,
                                         "refresh_expires_at": time.time() + 9999}), encoding="utf-8")
    calls = []

    def fake_post(url, fields):
        calls.append(fields)
        return {"access_token": "NEW", "refresh_token": "RT2", "expires_in": 7200, "refresh_expires_in": 15552000}
    monkeypatch.setattr(tk, "_post_form", fake_post)
    assert tk.access_token(S) == "NEW"
    assert calls[0]["grant_type"] == "refresh_token" and calls[0]["refresh_token"] == "RT1"
    assert tk.load_token()["refresh_token"] == "RT2"           # rotated token stored
    assert tk.access_token(S) == "NEW" and len(calls) == 1     # still valid: no second refresh


def test_chunk_plan_follows_tiktok_rules():
    assert tk.chunk_plan(3_000_000) == (3_000_000, 1)                    # small file: one chunk
    assert tk.chunk_plan(32 * 1024 * 1024) == (32 * 1024 * 1024, 1)
    cs, n = tk.chunk_plan(205 * 1000 * 1000)
    assert cs == 32 * 1024 * 1024 and n == 6                              # 205 MB → 6 parts (was 19 × 10 MB)


def test_upload_sends_ordered_chunks_with_content_range(tmp_path, monkeypatch):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x" * 25)
    seen = []

    def fake_send(req, raw=False, timeout=60):
        seen.append((req.get_header("Content-range"), int(req.get_header("Content-length"))))
        return (201 if len(seen) == 2 else 206), {}, b""
    monkeypatch.setattr(tk, "_send", fake_send)
    tk.upload_file("https://upload", f, 10, 2)
    assert seen == [("bytes 0-9/25", 10), ("bytes 10-24/25", 15)]


def test_publish_inbox_and_direct(tmp_path, monkeypatch):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x" * 100)
    calls = []

    def fake_api(s, path, body=None, method="POST"):
        calls.append((path, body))
        if path.endswith("creator_info/query/"):
            return {"privacy_level_options": ["SELF_ONLY"], "comment_disabled": False}
        if path.endswith("/init/"):
            return {"upload_url": "https://up", "publish_id": "p1"}
        return {"status": "SEND_TO_USER_INBOX" if s["tiktok_post_mode"] == "inbox" else "PUBLISH_COMPLETE"}
    monkeypatch.setattr(tk, "_api", fake_api)
    monkeypatch.setattr(tk, "upload_file", lambda *a, **k: None)
    monkeypatch.setattr(tk.time, "sleep", lambda x: None)
    r = tk.publish(S, f, caption="Xin chào #fyp", log=lambda m: None)
    assert r["status"] == "SEND_TO_USER_INBOX" and calls[0][0] == "/v2/post/publish/inbox/video/init/"
    assert calls[0][1] == {"source_info": {"source": "FILE_UPLOAD", "video_size": 100, "chunk_size": 100,
                                           "total_chunk_count": 1}}
    calls.clear()
    r = tk.publish(dict(S, tiktok_post_mode="direct"), f, caption="Xin chào", privacy="PUBLIC_TO_EVERYONE",
                   log=lambda m: None)
    init = next(b for p, b in calls if p == "/v2/post/publish/video/init/")
    assert init["post_info"]["privacy_level"] == "SELF_ONLY"   # not allowed → falls back to what TikTok offers
    assert init["post_info"]["is_aigc"] is True and init["post_info"]["title"] == "Xin chào"
    assert r["status"] == "PUBLISH_COMPLETE"


def test_custom_redirect_uri_for_a_tunnel():
    s = dict(S, tiktok_redirect_uri="https://abc.trycloudflare.com/api/tiktok/callback/")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(tk.login_url(s)).query))
    assert q["redirect_uri"] == "https://abc.trycloudflare.com/api/tiktok/callback/"
    assert tk._pending[q["state"]]["redirect"] == q["redirect_uri"]       # the token exchange must send the same one
