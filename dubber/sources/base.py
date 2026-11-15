"""Video source adapters: discover videos on a platform (lists only — never downloads or processes).

An adapter turns a *feed* (e.g. "hot", "tab:体育", "page:https://…") into normalized items:
    {id, platform, url, title, author, duration, likes, comments, shares, created, cover_url, source, feed, ...}
`id` is the library key: bare video id for Douyin (backward compatible), "<platform>:<id>" for others.
Adapters must not raise for one bad item, and an adapter failure must not affect other adapters
(trending.crawl runs each feed in its own try/except).
"""


class SourceError(RuntimeError):
    pass


class NeedsUser(SourceError):
    """The platform wants a captcha / login that only the user can do."""


class SourceAdapter:
    id = ""
    label = ""
    login_hint = ""

    def feeds(self, s) -> list[dict]:
        """Feeds this source offers out of the box: [{"feed": "hot", "label": "…"}]."""
        return []

    def discover(self, page, feed: str, pages: int, s: dict, progress) -> list[dict]:
        """Read one feed with an already-open Playwright page. progress(fraction, message)."""
        raise NotImplementedError

    def handles_url(self, url: str) -> bool:
        return False
