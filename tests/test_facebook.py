"""Facebook Reels adapter helpers (no network)."""
import pytest

from dubber import downloader
from dubber.sources import REGISTRY
from dubber.sources.facebook import items_from_links, parse_count, reels_url


@pytest.mark.parametrize("txt,val", [("54K", 54000), ("1.2M", 1200000), ("180K", 180000), ("1,234", 1234),
                                     ("3,4 N", 3400), ("", 0), ("abc", 0), ("999", 999), ("5.4万", 54000),
                                     ("1.2亿", 120000000)])
def test_parse_count(txt, val):
    assert parse_count(txt) == val


def test_reels_url():
    assert reels_url("https://www.facebook.com/espn") == "https://www.facebook.com/espn/reels"
    assert reels_url("https://www.facebook.com/espn/reels/") == "https://www.facebook.com/espn/reels"
    assert reels_url("https://www.facebook.com/reel/1156486263745253") == "https://www.facebook.com/reel/1156486263745253"


def test_items_from_links_dedupes_and_normalizes():
    links = [{"href": "https://www.facebook.com/reel/1156486263745253/?s=x", "text": "54K", "img": "http://i/1.jpg", "page": "ESPN"},
             {"href": "https://www.facebook.com/reel/1156486263745253", "text": "54K"},
             {"href": "https://www.facebook.com/espn/videos/123", "text": "not a reel"},
             {"href": "https://www.facebook.com/reel/1807500207152598", "label": "Great save", "text": "180K"}]
    items = items_from_links(links, "page:https://www.facebook.com/espn")
    assert [i["id"] for i in items] == ["fb:1156486263745253", "fb:1807500207152598"]
    a, b = items
    assert a["platform"] == "facebook" and a["views"] == 54000 and a["url"].endswith("/reel/1156486263745253")
    assert a["author"] == "ESPN" and b["title"] == "Great save"


def test_generic_label_is_not_a_title():
    it = items_from_links([{"href": "https://www.facebook.com/reel/123456789", "label": "Reels 方块预览",
                            "text": "5.4万", "page": "ESPN"}], "f")[0]
    assert it["title"] == "Reel · ESPN" and it["generic_title"] and it["views"] == 54000


def test_routing_is_per_platform():
    assert downloader.is_facebook("https://www.facebook.com/reel/1") and not downloader.is_douyin("https://www.facebook.com/reel/1")
    assert REGISTRY["facebook"].handles_url("https://fb.watch/abc") and not REGISTRY["douyin"].handles_url("https://fb.watch/abc")
    assert downloader.extract_url("xem nè https://www.facebook.com/reel/42 hay lắm") == "https://www.facebook.com/reel/42"
