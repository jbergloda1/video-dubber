"""Tests for Evil0ctal/Douyin_TikTok_Download_API (DTK MCP & REST) integration."""
import io
import json
import urllib.error
import urllib.request

import pytest

from dubber import config, downloader, dtk_client, hot, library, trending
from dubber.sources.douyin import Douyin

SAMPLE_CONTENT = {
    "platform": "douyin",
    "content_id": "7432109876543210123",
    "kind": "video",
    "web_url": "https://www.douyin.com/video/7432109876543210123",
    "title": "測試標題 #抖音",
    "description": "測試標題 #抖音",
    "created_at": "2026-11-10T08:00:00Z",
    "duration_ms": 42500,
    "author": {
        "platform": "douyin",
        "uid": "MS4wLjABAAAA_author_1",
        "sec_uid": "MS4wLjABAAAA_author_1",
        "unique_id": "author_dy",
        "nickname": "Tác Giả Douyin",
    },
    "stats": {
        "play_count": 150000,
        "digg_count": 12300,
        "comment_count": 456,
        "share_count": 789,
        "collect_count": 321,
    },
    "media": {
        "covers": [{"url": "https://p3.douyinpic.com/cover1.jpg", "urls": ["https://p9.douyinpic.com/cover1.jpg"]}],
        "video": {
            "url": "https://v26.douyinvod.com/clean_1080p.mp4",
            "urls": ["https://v3.douyinvod.com/clean_1080p.mp4"],
            "width": 1080,
            "height": 1920,
            "bitrate": 2400000,
            "format": "h264",
            "watermark": False,
        },
        "streams": [
            {
                "url": "https://v26.douyinvod.com/clean_540p.mp4",
                "urls": [],
                "width": 540,
                "height": 960,
                "bitrate": 800000,
                "format": "h264",
                "watermark": False,
            },
            {
                "url": "https://v26.douyinvod.com/watermarked.mp4",
                "urls": [],
                "width": 1080,
                "height": 1920,
                "bitrate": 3500000,
                "format": "h264",
                "watermark": True,
            },
        ],
        "images": [],
    },
    "tags": ["抖音"],
    "fetched_at": "2026-11-15T06:00:00Z",
}


class _FakeResp:
    def __init__(self, body: bytes | str, status: int = 200, headers: dict | None = None):
        self._data = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = headers or {}

    def read(self, n: int = -1) -> bytes:
        return self._data if n < 0 else self._data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sse_mcp_frame(payload: dict, is_error: bool = False) -> str:
    rpc = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "isError": is_error,
        },
    }
    return f"event: message\ndata: {json.dumps(rpc, ensure_ascii=False)}\n\n"


def test_sse_and_json_mcp_parsing_and_aweme_normalization():
    aweme = dtk_client.content_to_aweme(SAMPLE_CONTENT)
    assert aweme["aweme_id"] == "7432109876543210123"
    assert aweme["duration"] == 42500
    assert aweme["author"]["nickname"] == "Tác Giả Douyin"
    play = downloader._play_urls(aweme)
    # watermark=True stream is excluded when watermark-free streams exist; highest bitrate clean stream comes first
    assert play[0] == "https://v26.douyinvod.com/clean_1080p.mp4"
    assert "https://v26.douyinvod.com/watermarked.mp4" not in play
    # preview_urls puts the lightest H.264 stream first for instant browser playback
    prev = downloader.preview_urls(aweme)
    assert prev[0] == "https://v26.douyinvod.com/clean_540p.mp4"
    # trending._item_from_aweme compatibility
    tr = trending._item_from_aweme(aweme, "featured")
    assert tr["id"] == "7432109876543210123" and tr["duration"] == 42.5
    assert tr["likes"] == 12300 and tr["views"] == 150000 and tr["cover_url"] == "https://p3.douyinpic.com/cover1.jpg"


def test_mcp_pending_task_polls_get_task_result(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=30):
        body = json.loads(req.data.decode("utf-8"))
        name = body["params"]["name"]
        calls.append((name, body["params"]["arguments"]))
        if len(calls) == 1:
            return _FakeResp(_sse_mcp_frame({"status": "pending", "task_id": "t-123"}))
        return _FakeResp(_sse_mcp_frame({"status": "ok", "task_id": "t-123", "platform": "douyin", "data": SAMPLE_CONTENT}))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)
    s = {"dtk_enabled": True, "dtk_url": "http://127.0.0.1:8000", "dtk_api_key": "dtk_test_key"}
    aweme, plat = dtk_client.resolve_aweme("https://v.douyin.com/abc/", s)
    assert plat == "douyin" and aweme["aweme_id"] == "7432109876543210123"
    assert [c[0] for c in calls] == ["parse_url", "get_task_result"]
    assert calls[1][1] == {"task_id": "t-123"}


def test_mcp_tool_error_extracts_code_and_retryability(monkeypatch):
    err_prose = ("Endpoint douyin.content_detail is currently tripped. "
                 "This is retryable. (error code ENDPOINT_CIRCUIT_OPEN)")
    rpc = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": err_prose}], "isError": True}}
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=30: _FakeResp(json.dumps(rpc)))
    with pytest.raises(dtk_client.DtkError) as exc:
        dtk_client.call_tool("parse_url", {"url": "https://v.douyin.com/x/"}, {"dtk_url": "http://127.0.0.1:8000"})
    assert exc.value.code == "ENDPOINT_CIRCUIT_OPEN" and exc.value.retryable is True


def test_v4_fallback_when_mcp_returns_404(monkeypatch):
    def fake_urlopen(req, timeout=30):
        if req.full_url.endswith("/mcp/"):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b"not found"))
        if "/api/hybrid/video_data" in req.full_url:
            v4_body = {
                "code": 200,
                "data": {
                    "aweme_id": "7000111222333444555",
                    "desc": "Video từ DTK v4",
                    "duration": 18000,
                    "author": {"nickname": "Creator V4"},
                    "statistics": {"digg_count": 99},
                    "video": {"width": 720, "height": 1280, "play_addr": {"url_list": ["https://cdn.douyin.com/v4.mp4"]}},
                },
            }
            return _FakeResp(json.dumps(v4_body))
        raise AssertionError(f"Unexpected URL: {req.full_url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    s = {"dtk_enabled": True, "dtk_url": "http://127.0.0.1:8080"}
    aweme, plat = dtk_client.resolve_aweme("https://v.douyin.com/v4link/", s)
    assert plat == "douyin" and aweme["aweme_id"] == "7000111222333444555"
    assert downloader._play_urls(aweme) == ["https://cdn.douyin.com/v4.mp4"]


def test_downloader_probe_uses_dtk_and_falls_back_cleanly(monkeypatch):
    s = {"dtk_enabled": True, "dtk_url": "http://127.0.0.1:8000"}
    monkeypatch.setattr(dtk_client, "resolve_aweme", lambda url, cfg: (dtk_client.content_to_aweme(SAMPLE_CONTENT), "douyin"))
    info = downloader.probe("7.66 复制打开抖音 https://v.douyin.com/xyz/ ", s)
    assert info["duration"] == 42.5 and info["uploader"] == "Tác Giả Douyin" and info["platform"] == "douyin"
    assert "https://v.douyin.com/xyz/" in downloader._probe_cache


def test_douyin_user_feed_via_dtk_without_browser(monkeypatch, tmp_path):
    from dubber import sources
    pages_called = []

    def fake_list_user(platform, user_ref, s, cursor=None, count=20):
        pages_called.append((platform, user_ref, cursor))
        c = dict(SAMPLE_CONTENT, content_id=f"740000000000000000{len(pages_called)}")
        return {
            "uid": "MS4wLjABAAAA_author_1",
            "items": [dtk_client.content_to_aweme(c)],
            "cursor": f"cur_{len(pages_called)}",
            "has_more": len(pages_called) < 2,
        }

    monkeypatch.setattr(dtk_client, "list_user_posts", fake_list_user)
    monkeypatch.setattr(trending, "STORE", tmp_path / "trending.json")
    monkeypatch.setattr(trending, "CRAWL_STATE", tmp_path / "crawl_state.json")
    monkeypatch.setattr(trending, "_save_cover", lambda it: None)
    # Ensure Playwright browser is NOT launched for `user:...` when DTK is enabled
    monkeypatch.setattr(trending, "adapter_context", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Browser should not open")))

    s = {"dtk_enabled": True, "dtk_url": "http://127.0.0.1:8000", "crawl_gap_s": 0}
    trending.crawl([("douyin", "user:MS4wLjABAAAA_author_1")], pages=3, s=s)
    items = trending.load()
    assert len(items) == 2
    assert pages_called == [
        ("douyin", "user:MS4wLjABAAAA_author_1".split(":", 1)[1], None),
        ("douyin", "user:MS4wLjABAAAA_author_1".split(":", 1)[1], "cur_1"),
    ]


def test_secret_masking_and_library_platform():
    assert "dtk_api_key" in config.SECRET_KEYS
    m = config.masked({"dtk_api_key": "dtk_1234567890abcdef"})
    assert m["dtk_api_key"] == "••••cdef"
    assert library.platform_of("https://www.bilibili.com/video/BV1xx411c7mD") == "bilibili"
    assert library.platform_of("https://b23.tv/abc123") == "bilibili"
    assert hot.dtk_tt_item({**SAMPLE_CONTENT, "platform": "tiktok"}, "search:test")["key"] == "tiktok:7432109876543210123"
