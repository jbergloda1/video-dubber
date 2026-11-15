"""Client for Evil0ctal/Douyin_TikTok_Download_API (DTK v5 MCP streamable-http + REST v5 & v4 fallback).

Connects to a self-hosted DTK instance (default http://127.0.0.1:8000):
  - Primary protocol: MCP streamable-http at `POST /mcp/` (tools: `parse_url`, `get_video`, `get_user`,
    `list_user_posts`, `list_comments`, `pool_status`, `get_task_result`).
  - REST v5 complement: `/api/v1/{platform}/trending`, `/api/v1/{platform}/search/posts`, `/api/v1/parse`.
  - Legacy v4 fallback: `/api/hybrid/video_data?url=...&minimal=false` when `/mcp/` returns 404.
"""
from __future__ import annotations

import datetime
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "VideoDubber-DTK-MCP/1.0"

RETRYABLE_CODES = {
    "RATE_LIMITED",
    "QUEUE_FULL",
    "IDENTITY_POOL_EXHAUSTED",
    "ENDPOINT_CIRCUIT_OPEN",
    "UPSTREAM_RISK_CONTROL",
    "SIGNING_FAILED",
    "UPSTREAM_UNREACHABLE",
    "DOWNLOADER_UNAVAILABLE",
    "TRANSCRIBER_UNAVAILABLE",
    "INTERNAL",
}


class DtkError(RuntimeError):
    def __init__(self, message: str, code: str = "DTK_ERROR", retryable: bool = False, status: int = 0):
        super().__init__(message)
        self.code = code
        self.retryable = retryable or (code in RETRYABLE_CODES)
        self.status = status


def base_url(s: dict) -> str:
    raw = str((s or {}).get("dtk_url") or "").strip().rstrip("/")
    for suffix in ("/mcp", "/api/v1", "/api"):
        if raw.endswith(suffix):
            raw = raw[:-len(suffix)].rstrip("/")
    return raw


def mcp_url(s: dict) -> str:
    b = base_url(s)
    return f"{b}/mcp/" if b else ""


def configured(s: dict) -> bool:
    return bool((s or {}).get("dtk_enabled", True) and base_url(s))


def _headers(s: dict, mcp: bool = False) -> dict[str, str]:
    h = {"User-Agent": UA, "Content-Type": "application/json"}
    if mcp:
        h["Accept"] = "application/json, text/event-stream"
    else:
        h["Accept"] = "application/json"
    key = str((s or {}).get("dtk_api_key") or "").strip()
    if key and not key.startswith("••"):
        h["X-API-Key"] = key
        h["Authorization"] = f"Bearer {key}"
    return h


def _timeout(s: dict, default: float = 45.0) -> float:
    try:
        return max(5.0, float((s or {}).get("dtk_timeout_s") or default))
    except (TypeError, ValueError):
        return default


def _parse_sse_or_json(raw_bytes: bytes) -> dict:
    """Parse an MCP streamable-http response (either `event: message\\ndata: {...}` SSE or plain JSON)."""
    text = raw_bytes.decode("utf-8", errors="replace").strip()
    if not text:
        raise DtkError("DTK trả về phản hồi rỗng", "EMPTY_RESPONSE")
    if text.startswith("{"):
        try:
            return json.loads(text)
        except ValueError as e:
            raise DtkError(f"DTK trả về JSON không hợp lệ: {text[:200]}", "BAD_JSON") from e
    # SSE stream: collect all `data:` lines per event, prefer the last JSON-RPC message with result/error
    last_msg = None
    data_lines: list[str] = []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif not line.strip() and data_lines:
            payload = "\n".join(data_lines).strip()
            data_lines = []
            if payload.startswith("{"):
                try:
                    obj = json.loads(payload)
                    if isinstance(obj, dict):
                        last_msg = obj
                except ValueError:
                    pass
    if last_msg is not None:
        return last_msg
    raise DtkError(f"Không đọc được phản hồi MCP từ DTK: {text[:200]}", "BAD_MCP_FRAME")


def _extract_error_code(prose: str) -> str:
    m = re.search(r"\(error code ([A-Z0-9_]+)\)", prose or "")
    return m.group(1) if m else "MCP_TOOL_ERROR"


def _unwrap_mcp_result(rpc: dict) -> dict:
    """Unwrap a JSON-RPC 2.0 `tools/call` response into the tool's dictionary payload."""
    if "error" in rpc and rpc["error"]:
        err = rpc["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        code = str(err.get("code", "RPC_ERROR")) if isinstance(err, dict) else "RPC_ERROR"
        raise DtkError(f"DTK MCP lỗi: {msg}", code)
    res = rpc.get("result")
    if not isinstance(res, dict):
        raise DtkError("DTK MCP thiếu trường result", "BAD_MCP_RESULT")
    is_error = bool(res.get("isError"))
    if isinstance(res.get("structuredContent"), dict) and not is_error:
        return res["structuredContent"]
    content = res.get("content") or []
    texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
    joined = "\n".join(t for t in texts if t).strip()
    if is_error:
        code = _extract_error_code(joined)
        raise DtkError(joined or "DTK MCP báo lỗi tool", code)
    if not joined:
        return res
    try:
        out = json.loads(joined)
        return out if isinstance(out, dict) else {"data": out}
    except ValueError:
        return {"text": joined}


_req_id = 0


def _mcp_post(method: str, params: dict, s: dict, timeout: float | None = None) -> dict:
    global _req_id
    _req_id += 1
    url = mcp_url(s)
    if not url:
        raise DtkError("Chưa cấu hình địa chỉ DTK (dtk_url)", "NOT_CONFIGURED")
    body = json.dumps({"jsonrpc": "2.0", "id": _req_id, "method": method, "params": params}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=_headers(s, mcp=True), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout or _timeout(s)) as r:
            return _parse_sse_or_json(r.read())
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        code = f"HTTP_{e.code}"
        msg = f"DTK HTTP {e.code}"
        if raw.strip().startswith("{"):
            try:
                err_obj = (json.loads(raw).get("error") or {})
                if isinstance(err_obj, dict):
                    code = err_obj.get("code") or code
                    msg = err_obj.get("message") or msg
            except ValueError:
                pass
        if e.code == 401:
            code = "UNAUTHENTICATED"
            msg = "DTK từ chối API key (401 UNAUTHENTICATED) — kiểm tra lại dtk_api_key trong Cài đặt"
        elif e.code == 403:
            code = "FORBIDDEN_SCOPE"
            msg = f"API key DTK thiếu quyền (403 FORBIDDEN_SCOPE): {msg}"
        raise DtkError(msg, code, status=e.code) from e
    except (urllib.error.URLError, OSError) as e:
        raise DtkError(f"Không kết nối được DTK tại {base_url(s)}: {e}", "UNREACHABLE", retryable=True) from e


def call_tool(name: str, arguments: dict | None = None, s: dict | None = None, poll_pending: bool = True) -> dict:
    """Call an MCP tool on `/mcp/` and automatically collect `pending` tasks via `get_task_result`."""
    s = s or {}
    timeout = _timeout(s)
    rpc = _mcp_post("tools/call", {"name": name, "arguments": arguments or {}}, s, timeout=timeout)
    out = _unwrap_mcp_result(rpc)
    if not poll_pending or out.get("status") != "pending" or not out.get("task_id"):
        return out
    task_id = str(out["task_id"])
    deadline = time.monotonic() + timeout
    delay = 1.0
    while time.monotonic() < deadline:
        time.sleep(delay)
        delay = min(delay * 1.5, 4.0)
        poll_rpc = _mcp_post(
            "tools/call",
            {"name": "get_task_result", "arguments": {"task_id": task_id}},
            s,
            timeout=min(15.0, max(5.0, deadline - time.monotonic())),
        )
        out = _unwrap_mcp_result(poll_rpc)
        if out.get("status") != "pending":
            return out
    raise DtkError(f"Tác vụ DTK {task_id} ({name}) chạy quá thời gian chờ ({timeout:.0f}s)", "TIMEOUT", retryable=True)


def list_tools(s: dict) -> list[str]:
    rpc = _mcp_post("tools/list", {}, s, timeout=10.0)
    tools = (rpc.get("result") or {}).get("tools") or []
    return [t.get("name", "") for t in tools if isinstance(t, dict) and t.get("name")]


# ------------------------------------------------------------------ REST v5 & v4 helpers
def rest_v5(method: str, path: str, s: dict, params: dict | None = None, body: dict | None = None,
            wait: int | None = 20) -> dict:
    """Call a DTK v5 REST `/api/v1/...` endpoint, unwrapping the envelope and polling 202 tasks if needed."""
    b = base_url(s)
    if not b:
        raise DtkError("Chưa cấu hình địa chỉ DTK (dtk_url)", "NOT_CONFIGURED")
    q = dict(params or {})
    if wait is not None and "wait" not in q:
        q["wait"] = wait
    url = f"{b}{path}"
    if q:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode({k: v for k, v in q.items() if v is not None})
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(s, mcp=False), method=method.upper())
    timeout = _timeout(s)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            env = json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", errors="replace")
            env = json.loads(raw)
            if isinstance(env, dict) and env.get("error"):
                err = env["error"]
                raise DtkError(err.get("message") or f"HTTP {e.code}", err.get("code") or f"HTTP_{e.code}",
                               status=e.code) from e
        except ValueError:
            pass
        raise DtkError(f"DTK REST HTTP {e.code}: {raw[:200]}", f"HTTP_{e.code}", status=e.code) from e
    except (urllib.error.URLError, OSError) as e:
        raise DtkError(f"Không kết nối được DTK REST tại {b}: {e}", "UNREACHABLE", retryable=True) from e

    if not isinstance(env, dict) or not env.get("success"):
        err = (env.get("error") if isinstance(env, dict) else None) or {}
        raise DtkError(err.get("message") or "DTK REST trả lỗi", err.get("code") or "REST_ERROR")

    payload = env.get("data")
    if isinstance(payload, dict) and payload.get("task_id") and payload.get("state") in ("queued", "running"):
        return _poll_v5_task(str(payload["task_id"]), s, timeout)
    return payload if isinstance(payload, dict) else {"items": payload}


def _poll_v5_task(task_id: str, s: dict, timeout: float) -> dict:
    b = base_url(s)
    deadline = time.monotonic() + timeout
    delay = 0.8
    while time.monotonic() < deadline:
        req = urllib.request.Request(f"{b}/api/v1/tasks/{task_id}", headers=_headers(s, mcp=False), method="GET")
        with urllib.request.urlopen(req, timeout=15.0) as r:
            env = json.loads(r.read().decode("utf-8", errors="replace"))
        if not env.get("success"):
            err = env.get("error") or {}
            raise DtkError(err.get("message") or "Lỗi đọc task DTK", err.get("code") or "TASK_ERROR")
        task = env.get("data") or {}
        st = task.get("state")
        if st == "done":
            res = task.get("data")
            return res if isinstance(res, dict) else {"items": res}
        if st == "failed":
            err = task.get("error") or {}
            raise DtkError(err.get("message") or "Task DTK thất bại", err.get("code") or "TASK_FAILED",
                           retryable=bool(err.get("retryable")))
        time.sleep(delay)
        delay = min(delay * 1.6, 4.0)
    raise DtkError(f"Task DTK {task_id} chạy quá thời gian chờ ({timeout:.0f}s)", "TIMEOUT", retryable=True)


def v4_hybrid_video(url: str, s: dict) -> dict:
    """Fallback for DTK v4 (`GET /api/hybrid/video_data?url=...&minimal=false`)."""
    b = base_url(s)
    q = urllib.parse.urlencode({"url": url, "minimal": "false"})
    req = urllib.request.Request(f"{b}/api/hybrid/video_data?{q}", headers=_headers(s, mcp=False), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_timeout(s)) as r:
            body = json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        raise DtkError(f"DTK v4 HTTP {e.code}", f"HTTP_{e.code}", status=e.code) from e
    except (urllib.error.URLError, OSError) as e:
        raise DtkError(f"Không kết nối được DTK v4 tại {b}: {e}", "UNREACHABLE", retryable=True) from e
    if not isinstance(body, dict) or body.get("code") not in (200, None):
        raise DtkError(str(body.get("message") or body.get("detail") or "DTK v4 không lấy được dữ liệu"), "V4_ERROR")
    data = body.get("data")
    if not isinstance(data, dict):
        raise DtkError("DTK v4 trả về data rỗng", "V4_EMPTY")
    return data


# ------------------------------------------------------------------ Normalization: DTK Content -> video-dubber aweme
def _epoch(val) -> int:
    if not val:
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str):
        if val.isdigit():
            return int(val)
        try:
            dt = datetime.datetime.fromisoformat(val.replace("Z", "+00:00"))
            return int(dt.timestamp())
        except ValueError:
            return 0
    return 0


def _urls_of(stream: dict | None) -> list[str]:
    if not isinstance(stream, dict):
        return []
    out: list[str] = []
    u0 = stream.get("url")
    if isinstance(u0, str) and u0.strip():
        out.append(u0.strip())
    for u in stream.get("urls") or []:
        if isinstance(u, str) and u.strip() and u.strip() not in out:
            out.append(u.strip())
    return out


def content_to_aweme(data: dict) -> dict:
    """Convert DTK v5 normalized `Content` (or v4 raw payload) into video-dubber's `aweme` dictionary shape."""
    if not isinstance(data, dict):
        raise DtkError("Dữ liệu video từ DTK không hợp lệ", "BAD_CONTENT")
    # If it is already a raw Douyin aweme dict (e.g., from v4 or `raw` field)
    if "aweme_id" in data and isinstance(data.get("video"), dict) and "media" not in data:
        return data
    # If it is a raw TikTok itemStruct from v4
    if "id" in data and isinstance(data.get("video"), dict) and "media" not in data and "content_id" not in data:
        v = data.get("video") or {}
        st = data.get("stats") or data.get("statsV2") or {}
        au = data.get("author") or {}
        play_urls = []
        for k in ("playAddr", "downloadAddr"):
            val = v.get(k)
            if isinstance(val, str) and val:
                play_urls.append(val)
            elif isinstance(val, dict):
                play_urls.extend(val.get("url_list") or val.get("UrlList") or [])
        cover = v.get("cover") or v.get("originCover") or ""
        return {
            "aweme_id": str(data["id"]),
            "desc": data.get("desc") or "",
            "duration": int(v.get("duration") or 0) * 1000 if (v.get("duration") or 0) < 1000 else int(v.get("duration") or 0),
            "create_time": _epoch(data.get("createTime")),
            "author": {"nickname": au.get("nickname") or au.get("uniqueId") or "",
                       "unique_id": au.get("uniqueId") or "", "sec_uid": au.get("secUid") or ""},
            "statistics": {
                "play_count": int(st.get("playCount") or 0),
                "digg_count": int(st.get("diggCount") or 0),
                "comment_count": int(st.get("commentCount") or 0),
                "share_count": int(st.get("shareCount") or 0),
                "collect_count": int(st.get("collectCount") or 0),
            },
            "video": {
                "width": v.get("width"),
                "height": v.get("height"),
                "duration": int(v.get("duration") or 0) * 1000 if (v.get("duration") or 0) < 1000 else int(v.get("duration") or 0),
                "cover": {"url_list": [cover] if cover else []},
                "play_addr": {"url_list": play_urls},
                "bit_rate": [{"bit_rate": int(v.get("bitrate") or 1000000), "is_h265": 0,
                              "play_addr": {"url_list": play_urls}}] if play_urls else [],
            },
            "_dtk": data,
        }

    cid = str(data.get("content_id") or data.get("aweme_id") or data.get("id") or "")
    if not cid:
        raise DtkError("DTK không trả về content_id của video", "MISSING_ID")

    media = data.get("media") or {}
    primary = media.get("video") if isinstance(media.get("video"), dict) else None
    streams = [s for s in (media.get("streams") or []) if isinstance(s, dict)]

    # Build bit_rate entries preferring watermark-free streams
    all_streams: list[dict] = []
    if primary:
        all_streams.append(primary)
    for st_item in streams:
        if st_item not in all_streams:
            all_streams.append(st_item)

    clean_streams = [s for s in all_streams if not s.get("watermark", False)] or all_streams
    bit_rate_list = []
    play_urls: list[str] = []
    width = primary.get("width") if primary else None
    height = primary.get("height") if primary else None

    for idx, st_item in enumerate(clean_streams):
        u_list = _urls_of(st_item)
        if not u_list:
            continue
        for u in u_list:
            if u not in play_urls:
                play_urls.append(u)
        fmt = str(st_item.get("format") or "").lower()
        is_h265 = 1 if ("265" in fmt or "hevc" in fmt or "bytevc1" in fmt) else 0
        br_val = int(st_item.get("bitrate") or (2_000_000 - idx * 100_000))
        bit_rate_list.append({
            "bit_rate": br_val,
            "is_h265": is_h265,
            "play_addr": {"url_list": u_list},
        })
        if not width and st_item.get("width"):
            width = st_item["width"]
        if not height and st_item.get("height"):
            height = st_item["height"]

    covers: list[str] = []
    for c in media.get("covers") or []:
        for u in _urls_of(c):
            if u not in covers:
                covers.append(u)

    author = data.get("author") or {}
    stats = data.get("stats") or {}
    dur_ms = int(data.get("duration_ms") or 0)
    kind = str(data.get("kind") or "video").lower()
    images = media.get("images") or []
    is_image = kind == "image_album" or (bool(images) and not play_urls)

    desc = (data.get("description") or data.get("title") or "").strip()
    title = (data.get("title") or desc).strip()

    return {
        "aweme_id": cid,
        "desc": desc,
        "preview_title": title,
        "duration": dur_ms,
        "create_time": _epoch(data.get("created_at")),
        "is_image": is_image,
        "images": images if is_image else None,
        "author": {
            "uid": str(author.get("uid") or ""),
            "sec_uid": str(author.get("sec_uid") or author.get("uid") or ""),
            "unique_id": str(author.get("unique_id") or ""),
            "nickname": str(author.get("nickname") or author.get("unique_id") or ""),
        },
        "statistics": {
            "play_count": int(stats.get("play_count") or 0),
            "digg_count": int(stats.get("digg_count") or 0),
            "comment_count": int(stats.get("comment_count") or 0),
            "share_count": int(stats.get("share_count") or 0),
            "collect_count": int(stats.get("collect_count") or 0),
        },
        "video": {
            "width": width,
            "height": height,
            "duration": dur_ms,
            "cover": {"url_list": covers},
            "origin_cover": {"url_list": covers},
            "play_addr": {"url_list": play_urls},
            "play_addr_h264": {
                "url_list": [u for br in bit_rate_list if not br.get("is_h265") for u in br["play_addr"]["url_list"]]
            },
            "bit_rate": bit_rate_list,
        },
        "_dtk": data,
    }


# ------------------------------------------------------------------ High-level API for video-dubber
def status(s: dict) -> dict:
    """Check DTK connectivity, mode (v5-mcp vs v4-rest), and identity pool health."""
    b = base_url(s)
    if not b:
        return {"ok": False, "enabled": bool((s or {}).get("dtk_enabled", True)), "error": "Chưa nhập địa chỉ DTK"}
    try:
        res = call_tool("pool_status", {}, s, poll_pending=False)
        data = res.get("data") if isinstance(res.get("data"), dict) else res
        return {
            "ok": True,
            "enabled": bool((s or {}).get("dtk_enabled", True)),
            "mode": "v5-mcp",
            "url": b,
            "usable_identities": data.get("usable_identities", 0),
            "identities": data.get("identities") or {},
            "endpoints": data.get("endpoints") or [],
            "summary": data.get("summary") or "Đã kết nối DTK MCP Server",
        }
    except DtkError as e:
        if e.status in (404, 405):
            # Probe DTK v4 (/openapi.json or /docs)
            try:
                req = urllib.request.Request(f"{b}/openapi.json", headers=_headers(s, mcp=False), method="GET")
                with urllib.request.urlopen(req, timeout=8.0) as r:
                    oa = json.loads(r.read().decode("utf-8", errors="replace"))
                paths = oa.get("paths") or {}
                if "/api/hybrid/video_data" in paths:
                    return {
                        "ok": True,
                        "enabled": bool((s or {}).get("dtk_enabled", True)),
                        "mode": "v4-rest",
                        "url": b,
                        "usable_identities": None,
                        "identities": {},
                        "endpoints": [],
                        "summary": "Đã kết nối DTK v4 (/api/hybrid/video_data)",
                    }
            except Exception:
                pass
        return {"ok": False, "enabled": bool((s or {}).get("dtk_enabled", True)), "url": b, "error": str(e),
                "code": e.code}


def parse_url(url: str, s: dict) -> dict:
    """Parse any Douyin / TikTok / Bilibili link or share text via MCP `parse_url` (with v4 fallback)."""
    try:
        res = call_tool("parse_url", {"url": url}, s)
        data = res.get("data") if isinstance(res.get("data"), dict) else res
        if isinstance(data, dict) and data:
            if "platform" in res and "platform" not in data:
                data = dict(data, platform=res["platform"])
            return data
        raise DtkError("DTK parse_url không trả về dữ liệu bài đăng", "EMPTY_DATA")
    except DtkError as e:
        if e.status in (404, 405):
            return v4_hybrid_video(url, s)
        raise


def resolve_aweme(url: str, s: dict) -> tuple[dict, str]:
    """Resolve a Douyin / TikTok / Bilibili URL into `(aweme_dict, platform)`."""
    data = parse_url(url, s)
    platform = str(data.get("platform") or "").lower()
    if not platform:
        if "tiktok.com" in url:
            platform = "tiktok"
        elif "bilibili.com" in url or "b23.tv" in url:
            platform = "bilibili"
        else:
            platform = "douyin"
    return content_to_aweme(data), platform


def get_video(platform: str, content_id: str, s: dict) -> dict:
    res = call_tool("get_video", {"platform": platform, "content_id": str(content_id)}, s)
    data = res.get("data") if isinstance(res.get("data"), dict) else res
    return content_to_aweme(data)


def resolve_user_uid(platform: str, user_ref: str, s: dict) -> str:
    """Turn a profile URL, `@handle` or `sec_uid` into the stable `uid` accepted by `list_user_posts`."""
    ref = (user_ref or "").strip()
    if not ref:
        raise DtkError("Thiếu ID hoặc link kênh tác giả", "INVALID_PARAM")
    # Extract directly from a Douyin user URL if present
    m = re.search(r"douyin\.com/user/([A-Za-z0-9_-]{10,})", ref)
    if m:
        return m.group(1)
    if ref.startswith(("http://", "https://")) or "douyin.com" in ref or "tiktok.com" in ref or "bilibili.com" in ref:
        res = call_tool("parse_url", {"url": ref}, s)
        data = res.get("data") if isinstance(res.get("data"), dict) else res
        # If the user pasted a video link instead of a profile link, use its author's sec_uid
        if isinstance(data.get("author"), dict):
            au = data["author"]
            uid = au.get("sec_uid") or au.get("uid")
            if uid:
                return str(uid)
        uid = data.get("sec_uid") or data.get("uid")
        if uid:
            return str(uid)
        raise DtkError(f"Không tìm thấy ID tác giả từ link: {ref}", "NOT_FOUND")
    if platform == "tiktok" and (ref.startswith("@") or not ref.startswith("MS4wLjAB")):
        res = call_tool("get_user", {"platform": "tiktok", "uid": ref.lstrip("@")}, s)
        data = res.get("data") if isinstance(res.get("data"), dict) else res
        uid = data.get("sec_uid") or data.get("uid")
        if uid:
            return str(uid)
    return ref


def list_user_posts(platform: str, user_ref: str, s: dict, cursor: str | None = None, count: int = 20) -> dict:
    """Fetch one page of an author's posts via MCP `list_user_posts`."""
    uid = resolve_user_uid(platform, user_ref, s)
    cap = 35 if platform == "tiktok" else 20 if platform == "bilibili" else 50
    args: dict = {"platform": platform, "uid": uid, "count": max(1, min(int(count or 20), cap))}
    if cursor:
        args["cursor"] = str(cursor)
    res = call_tool("list_user_posts", args, s)
    data = res.get("data") if isinstance(res.get("data"), dict) else res
    raw_items = data.get("items") or []
    awemes = []
    for it in raw_items:
        try:
            awemes.append(content_to_aweme(it))
        except Exception:
            pass
    return {
        "uid": uid,
        "items": awemes,
        "cursor": data.get("cursor"),
        "has_more": bool(data.get("has_more")),
    }


def fetch_trending(platform: str = "douyin", board: str = "hot", s: dict | None = None) -> list[dict]:
    """Fetch trending board via DTK v5 REST `GET /api/v1/{platform}/trending` and format for `trending.py`."""
    s = s or {}
    data = rest_v5("GET", f"/api/v1/{platform}/trending", s, params={"board": board})
    rows = data.get("items") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    out = []
    now = int(time.time())
    for idx, w in enumerate(rows):
        if not isinstance(w, dict):
            continue
        word = (w.get("word") or w.get("title") or "").strip()
        if not word:
            continue
        rank = w.get("rank") or (idx + 1)
        web_url = w.get("web_url") or f"https://www.douyin.com/hot/{urllib.parse.quote(word)}"
        cover_obj = w.get("cover") if isinstance(w.get("cover"), dict) else {}
        cover = (cover_obj.get("url") or ((cover_obj.get("urls") or [None])[0])) if cover_obj else None
        sid_m = re.search(r"/hot/(\d+)", web_url)
        sid = sid_m.group(1) if sid_m else f"dtk_{idx + 1}_{ abs(hash(word)) % 10**8 }"
        out.append({
            "id": sid,
            "url": web_url,
            "sentence_id": sid,
            "title": word,
            "author": "",
            "duration": 0,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "collects": 0,
            "created": now,
            "cover_url": cover,
            "is_image": False,
            "source": "hot",
            "feed": "hot",
            "topic": word,
            "hot_value": int(w.get("hot_value") or 0),
            "views": int(w.get("content_count") or 0),
            "rank": int(rank),
        })
    return out


def search_posts(platform: str, keyword: str, s: dict, cursor: str | None = None, count: int = 20) -> dict:
    """Search posts on TikTok or Bilibili via DTK v5 REST `GET /api/v1/{platform}/search/posts`."""
    cap = 35 if platform == "tiktok" else 20 if platform == "bilibili" else 50
    params: dict = {"keyword": keyword, "count": max(1, min(int(count or 20), cap))}
    if cursor:
        params["cursor"] = str(cursor)
    data = rest_v5("GET", f"/api/v1/{platform}/search/posts", s, params=params)
    raw_items = data.get("items") or []
    return {
        "items": [it for it in raw_items if isinstance(it, dict)],
        "cursor": data.get("cursor"),
        "has_more": bool(data.get("has_more")),
    }
