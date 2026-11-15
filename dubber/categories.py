"""Hierarchical topic categories (data/categories.json, user-editable; defaults in categories_default.json).

Membership is computed at read time from each video's text + the feeds it was found in, so editing the
keywords re-classifies the whole library without crawling again:
  - a category with "match" keywords contains every video whose title/topic/author matches one of them
    (from any source) — that is how sub-categories (Football, Tennis…) are carved out of a broad tab;
  - a category without "match" contains every video found in one of its feeds;
  - a parent also contains everything in its children.
"""
import json
import shutil
from pathlib import Path

from . import config

DEFAULT = Path(__file__).parent / "categories_default.json"
FILE = config.DATA / "categories.json"


def load() -> list[dict]:
    if not FILE.exists():
        shutil.copyfile(DEFAULT, FILE)
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        data = json.loads(DEFAULT.read_text(encoding="utf-8"))
    return data.get("categories", [])


def save(cats: list[dict]):
    validate(cats)
    raw = json.loads(DEFAULT.read_text(encoding="utf-8"))
    FILE.write_text(json.dumps({"_help": raw.get("_help", ""), "categories": cats}, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def validate(cats, seen=None):
    seen = set() if seen is None else seen
    if not isinstance(cats, list):
        raise ValueError("categories phải là danh sách")
    for c in cats:
        if not isinstance(c, dict) or not c.get("id") or not c.get("name"):
            raise ValueError("mỗi danh mục cần 'id' và 'name'")
        if c["id"] in seen:
            raise ValueError(f"id trùng: {c['id']}")
        seen.add(c["id"])
        if "match" in c and not isinstance(c["match"], list):
            raise ValueError(f"'match' của {c['id']} phải là danh sách")
        validate(c.get("children") or [], seen)


def flatten(cats, parent=None, inherited=None, out=None):
    """-> list of {id, name, parent, depth, feeds{platform:[...]}, match[...], children[ids]}"""
    out = [] if out is None else out
    for c in cats:
        feeds = {k: list(v) for k, v in (inherited or {}).items()}
        for platform, conf in (c.get("sources") or {}).items():
            feeds[platform] = list(conf.get("feeds") or [])
        node = {"id": c["id"], "name": c["name"], "parent": parent, "feeds": feeds,
                "match": [m.lower() for m in c.get("match") or []],
                "children": [ch["id"] for ch in c.get("children") or []],
                "depth": 0 if parent is None else next(n["depth"] for n in out if n["id"] == parent) + 1}
        out.append(node)
        flatten(c.get("children") or [], c["id"], feeds, out)
    return out


def _text(item):
    return " ".join(str(item.get(k) or "") for k in ("title", "topic", "author")).lower()


DEDICATED = ("user:", "page:", "search:")      # a channel / page / search picked FOR this topic


def _own_member(node, item):
    platform = item.get("platform") or "douyin"
    item_feeds = set(item.get("feeds") or ([item["feed"]] if item.get("feed") else [item.get("source")]))
    mine = set(node["feeds"].get(platform, []))
    if node["match"]:
        # everything from the topic's own channels belongs to it; from broad tabs only what matches a keyword
        if item_feeds & {f for f in mine if f.startswith(DEDICATED)}:
            return True
        t = _text(item)
        return any(m in t for m in node["match"])
    return bool(item_feeds & mine)


def classify(item, nodes) -> list[str]:
    by_id = {n["id"]: n for n in nodes}
    member = {n["id"]: _own_member(n, item) for n in nodes}
    # a parent contains what its children contain (bottom-up: nodes are in pre-order)
    for n in reversed(nodes):
        if any(member[c] for c in n["children"]):
            member[n["id"]] = True
    return [cid for cid, ok in member.items() if ok and cid in by_id]


def crawl_targets(node_id, nodes) -> list[tuple[str, str]]:
    """(platform, feed) pairs to crawl for a category (its own + inherited feeds)."""
    n = next((x for x in nodes if x["id"] == node_id), None)
    if not n:
        return []
    return [(platform, f) for platform, feeds in n["feeds"].items() for f in feeds]
