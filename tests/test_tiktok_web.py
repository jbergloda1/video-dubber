"""TikTok (VN) login for hot-video discovery: own profile, status read from the profile's cookies."""
from dubber.sources import tiktok_web


def test_status_from_cookies():
    assert tiktok_web.status_from([]) == {"logged_in": False, "creative": False}
    ck = [{"name": "sessionid", "value": "x", "domain": ".tiktok.com"},
          {"name": "sessionid_ads", "value": "y", "domain": ".ads.tiktok.com"}]
    assert tiktok_web.status_from(ck) == {"logged_in": True, "creative": True}
    # an empty value (logged out) is not a session
    assert not tiktok_web.status_from([{"name": "sessionid", "value": "", "domain": ".tiktok.com"}])["logged_in"]


def test_profile_is_separate():
    from dubber.sources import facebook
    from dubber import downloader
    assert tiktok_web.PROFILE not in (facebook.PROFILE, downloader.PROFILE)


def test_export_needs_login(tmp_path, monkeypatch):
    monkeypatch.setattr(tiktok_web, "_cookies", lambda: [{"name": "tt_csrf_token", "value": "a", "domain": ".tiktok.com"}])
    assert tiktok_web.export_cookies(tmp_path / "c.txt") is False
    monkeypatch.setattr(tiktok_web, "_cookies", lambda: [{"name": "sessionid", "value": "a", "domain": ".tiktok.com",
                                                          "path": "/", "secure": True, "expires": 2000000000}])
    assert tiktok_web.export_cookies(tmp_path / "c.txt") is True
    assert "sessionid\ta" in (tmp_path / "c.txt").read_text(encoding="utf-8")
