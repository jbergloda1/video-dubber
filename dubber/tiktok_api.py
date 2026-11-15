"""Post finished videos to TikTok with the official Content Posting API (your own developer app).

Setup (once): create an app at developers.tiktok.com, add Login Kit (Desktop) + Content Posting API, register the
redirect URI shown in ⚙ Cài đặt (http://localhost:8765/api/tiktok/callback/, platform DESKTOP), copy Client Key / Client Secret into
the settings, then "Đăng nhập TikTok": TikTok's own page asks you to sign in and allow the app — the app never sees
your password, only a token (kept on this PC in data/tiktok_token.json, never sent to the browser).

Two ways to post (setting tiktok_post_mode):
  inbox   scope video.upload — the video lands in your TikTok inbox; you finish the post in the TikTok app
          (≤ 5 pending videos per 24 h)
  direct  scope video.publish — posted with the caption; apps not yet audited by TikTok can only post PRIVATELY
          (SELF_ONLY) until TikTok reviews the app

API facts used here (TikTok docs, 2026): authorize https://www.tiktok.com/v2/auth/authorize/ with PKCE (desktop:
code_challenge = SHA-256 of the verifier, HEX); token POST https://open.tiktokapis.com/v2/oauth/token/ (access 2 h,
refresh 180 d, the refresh token ROTATES on every refresh); upload in 5–64 MB chunks (last ≤ 128 MB), in order,
PUT with Content-Range; the upload URL lives 1 h; 6 requests/min per token.
"""
import hashlib
import json
import math
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com"
TOKEN_FILE = config.DATA / "tiktok_token.json"
REDIRECT_PATH = "/api/tiktok/callback/"
SCOPES = {"inbox": "user.info.basic,video.upload", "direct": "user.info.basic,video.publish"}
CHUNK = 32 * 1024 * 1024          # 32 MB parts (TikTok: 5–64 MB, the last one takes the remainder ≤ 128 MB)
_pending = {}                     # state -> {"verifier", "redirect", "t"}
_lock = threading.Lock()


class TikTokError(RuntimeError):
    pass


def redirect_uri(port=8765, s=None):
    """Desktop Login Kit accepts http://localhost:<port>/… (TikTok's own example: http://localhost:3455/callback/).
    `tiktok_redirect_uri` overrides it (e.g. an https tunnel that forwards to this server)."""
    custom = ((s or {}).get("tiktok_redirect_uri") or "").strip()
    return custom or f"http://localhost:{port}{REDIRECT_PATH}"


# ---- OAuth -------------------------------------------------------------------------------------------------------
def pkce_pair():
    verifier = secrets.token_urlsafe(48)[:64]                     # 43–128 chars of A-Z a-z 0-9 - _
    challenge = hashlib.sha256(verifier.encode()).hexdigest()     # desktop Login Kit: hex, not base64url
    return verifier, challenge


def login_url(s, port=8765) -> str:
    key = (s.get("tiktok_client_key") or "").strip()
    if not key or not s.get("tiktok_client_secret"):
        raise TikTokError("Chưa nhập Client Key / Client Secret của ứng dụng TikTok (⚙ Cài đặt → Đăng TikTok)")
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    with _lock:
        now = time.time()
        for k in [k for k, v in _pending.items() if now - v["t"] > 900]:
            _pending.pop(k, None)
        _pending[state] = {"verifier": verifier, "redirect": redirect_uri(port, s), "t": now}
    q = {"client_key": key, "scope": SCOPES.get(s.get("tiktok_post_mode", "inbox"), SCOPES["inbox"]),
         "response_type": "code", "redirect_uri": redirect_uri(port, s), "state": state,
         "code_challenge": challenge, "code_challenge_method": "S256"}
    return AUTH_URL + "?" + urllib.parse.urlencode(q)


def _post_form(url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    return _send(req)


def _send(req, raw=False, timeout=60):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return (r.status, dict(r.headers), body) if raw else json.loads(body or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read()
        if raw:
            return e.code, dict(e.headers), body
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = {}
        err = data.get("error")
        msg = (err.get("message") or err.get("code")) if isinstance(err, dict) else (data.get("error_description") or err)
        code = err.get("code") if isinstance(err, dict) else err
        raise TikTokError(f"TikTok HTTP {e.code}: {code or ''} {msg or body[:200]}".strip())


def _save_token(tok):
    now = time.time()
    data = {"access_token": tok["access_token"], "refresh_token": tok["refresh_token"],
            "open_id": tok.get("open_id"), "scope": tok.get("scope", ""),
            "expires_at": now + float(tok.get("expires_in") or 7200) - 120,
            "refresh_expires_at": now + float(tok.get("refresh_expires_in") or 15552000) - 3600}
    old = load_token() or {}
    data["display_name"] = old.get("display_name", "")
    TOKEN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def exchange_code(s, code, state):
    with _lock:
        p = _pending.pop(state, None)
    if not p:
        raise TikTokError("Phiên đăng nhập không hợp lệ hoặc đã hết hạn (state không khớp) — bấm Đăng nhập lại")
    tok = _post_form(API + "/v2/oauth/token/", {
        "client_key": s["tiktok_client_key"].strip(), "client_secret": s["tiktok_client_secret"].strip(),
        "code": code, "grant_type": "authorization_code", "redirect_uri": p["redirect"], "code_verifier": p["verifier"]})
    if not tok.get("access_token"):
        raise TikTokError(f"TikTok không trả token: {tok.get('error_description') or tok.get('error') or tok}")
    data = _save_token(tok)
    try:
        info = user_info(s)
        data["display_name"] = info.get("display_name", "")
        TOKEN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    return data


def load_token():
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8")) if TOKEN_FILE.exists() else None
    except Exception:
        return None


def access_token(s) -> str:
    """A valid access token, refreshed when needed (the refresh token rotates: always store the new one)."""
    tok = load_token()
    if not tok:
        raise TikTokError("Chưa đăng nhập TikTok (⚙ Cài đặt → Đăng TikTok → Đăng nhập TikTok)")
    if time.time() < tok["expires_at"]:
        return tok["access_token"]
    if time.time() > tok.get("refresh_expires_at", 0):
        raise TikTokError("Phiên TikTok đã hết hạn (180 ngày) — đăng nhập lại")
    with _lock:
        tok = load_token()
        if time.time() < tok["expires_at"]:
            return tok["access_token"]
        new = _post_form(API + "/v2/oauth/token/", {
            "client_key": s["tiktok_client_key"].strip(), "client_secret": s["tiktok_client_secret"].strip(),
            "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        if not new.get("access_token"):
            raise TikTokError("Không gia hạn được phiên TikTok — đăng nhập lại")
        return _save_token(new)["access_token"]


def logout():
    TOKEN_FILE.unlink(missing_ok=True)


def status(s) -> dict:
    """For the settings dialog: never contains a token."""
    tok = load_token()
    return {"configured": bool(s.get("tiktok_client_key") and s.get("tiktok_client_secret")),
            "connected": bool(tok), "display_name": (tok or {}).get("display_name", ""),
            "scope": (tok or {}).get("scope", ""), "mode": s.get("tiktok_post_mode", "inbox"),
            "redirect_uri": redirect_uri(s=s), "expires_days": round(((tok or {}).get("refresh_expires_at", 0)
                                                                    - time.time()) / 86400) if tok else None}


# ---- API calls -------------------------------------------------------------------------------------------------
def _api(s, path, body=None, method="POST"):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {access_token(s)}",
                                          "Content-Type": "application/json; charset=UTF-8"})
    data = _send(req)
    err = data.get("error") or {}
    if isinstance(err, dict) and err.get("code") not in (None, "", "ok"):
        raise TikTokError(f"TikTok: {err.get('code')} — {err.get('message', '')}")
    return data.get("data") or {}


def user_info(s):
    return (_api(s, "/v2/user/info/?fields=open_id,display_name,avatar_url", method="GET") or {}).get("user", {})


def creator_info(s):
    """Direct post only: privacy options this account allows, max length, whether comments/duet/stitch are off."""
    return _api(s, "/v2/post/publish/creator_info/query/", {})


def chunk_plan(size: int, chunk=CHUNK):
    """(chunk_size, total_chunk_count) by TikTok's rules: < 5 MB or ≤ one chunk → single chunk; otherwise
    floor(size / chunk) chunks, the remainder merged into the last one."""
    if size <= chunk:
        return size, 1
    return chunk, max(1, size // chunk)


def upload_file(upload_url, path: Path, chunk_size, total, progress=None):
    size = path.stat().st_size
    with open(path, "rb") as f:
        for i in range(total):
            start = i * chunk_size
            end = size - 1 if i == total - 1 else start + chunk_size - 1
            f.seek(start)
            data = f.read(end - start + 1)
            t0 = time.time()
            for attempt in range(4):
                req = urllib.request.Request(upload_url, data=data, method="PUT", headers={
                    "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                    "Content-Range": f"bytes {start}-{end}/{size}"})
                code, _, body = _send(req, raw=True, timeout=180)
                if code in (200, 201, 206):
                    break
                if code >= 500 and attempt < 3:
                    time.sleep(2 + 3 * attempt)
                    continue
                raise TikTokError(f"Tải lên TikTok lỗi ở phần {i + 1}/{total}: HTTP {code} {body[:200]!r}")
            if progress:
                progress(i + 1, total, len(data), time.time() - t0)


def publish(s, path: Path, caption="", privacy=None, allow_comment=True, allow_duet=True, allow_stitch=True,
            aigc=True, progress=None, log=print) -> dict:
    """Upload a video. inbox mode → your TikTok inbox (finish there); direct mode → posted with the caption."""
    mode = s.get("tiktok_post_mode", "inbox")
    size = path.stat().st_size
    chunk_size, total = chunk_plan(size)
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size, "total_chunk_count": total}
    if mode == "direct":
        ci = creator_info(s)
        opts = ci.get("privacy_level_options") or ["SELF_ONLY"]
        privacy = privacy if privacy in opts else ("SELF_ONLY" if "SELF_ONLY" in opts else opts[0])
        body = {"post_info": {"title": caption[:2200], "privacy_level": privacy,
                              "disable_comment": not allow_comment or bool(ci.get("comment_disabled")),
                              "disable_duet": not allow_duet or bool(ci.get("duet_disabled")),
                              "disable_stitch": not allow_stitch or bool(ci.get("stitch_disabled")),
                              "is_aigc": bool(aigc), "brand_content_toggle": False, "brand_organic_toggle": False},
                "source_info": source}
        init = _api(s, "/v2/post/publish/video/init/", body)
    else:
        init = _api(s, "/v2/post/publish/inbox/video/init/", {"source_info": source})
        if caption:                          # the inbox API takes no caption — keep it handy for the app
            log(f"TikTok: hộp thư không nhận caption — dán khi đăng trong app:\n{caption}")
    pid = init["publish_id"]
    log(f"TikTok: tải lên {size / 1e6:.0f} MB ({total} phần) · publish_id {pid}")
    t_up = time.time()

    def part_done(i, n, nbytes, secs):
        log(f"TikTok: phần {i}/{n} xong — {nbytes / 1e6:.0f} MB trong {secs:.0f}s ({nbytes / 1e6 / max(secs, 0.01):.1f} MB/s)")
        if progress:
            progress(i / n)
    upload_file(init["upload_url"], path, chunk_size, total, part_done)
    log(f"TikTok: đã tải lên xong trong {time.time() - t_up:.0f}s, chờ TikTok xử lý…")
    st, last = {}, None
    for _ in range(40):                      # ≤ ~7 min; 6 requests/min per token → ask every 10 s
        time.sleep(10)
        st = _api(s, "/v2/post/publish/status/fetch/", {"publish_id": pid})
        if st.get("status") != last:
            last = st.get("status")
            log(f"TikTok: trạng thái {last}")
        if st.get("status") in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX", "FAILED"):
            break
    if st.get("status") == "FAILED":
        raise TikTokError(f"TikTok xử lý video lỗi: {st.get('fail_reason') or st}")
    return {"publish_id": pid, "status": st.get("status", "PROCESSING"), "mode": mode,
            "privacy": privacy if mode == "direct" else None}
