"""Post kit for the finished video: a catchy title, a short description and hashtags, per platform
(TikTok / YouTube / Facebook), written from the translated script, the on-screen text and the original title.

Uses the same provider chain as translation (Claude / ChatGPT / Gemini …). With only machine-translation
providers left (Google free, offline NLLB) it falls back to a simple version: translated original title,
first lines of the script, translated original hashtags + common Vietnamese discovery tags.

Hashtags are suggestions from the AI (topic tags + widely used discovery tags); the app does not read live
trending-hashtag data from the platforms.
"""
import json
import re
import unicodedata
from pathlib import Path

from . import translate
from .providers import orchestrator
from .providers.base import BadOutput

FILE = "post.json"
COMMON_TAGS = ["#xuhuong", "#viral", "#fyp", "#tiktokvietnam", "#thinhhanh"]
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"}, "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "original_hashtags": {"type": "array", "items": {"type": "string"}},
        "tiktok_caption": {"type": "string"}, "youtube_title": {"type": "string"},
        "youtube_description": {"type": "string"}, "facebook_caption": {"type": "string"},
    },
    "required": ["title", "description", "hashtags", "tiktok_caption", "youtube_title", "youtube_description",
                 "facebook_caption"],
}
SYSTEM = """Bạn là biên tập viên mạng xã hội Việt Nam. Từ nội dung một video (đã dịch sang tiếng Việt), hãy viết bộ
nội dung đăng bài bằng tiếng Việt tự nhiên, hấp dẫn, KHÔNG giật tít sai sự thật, KHÔNG bịa chi tiết không có
trong video.
- title: tiêu đề ngắn gây tò mò, tối đa 70 ký tự, có thể 1 emoji phù hợp.
- description: 2–3 câu tóm tắt nội dung, kết bằng một câu hỏi/kêu gọi bình luận.
- hashtags: 8–12 hashtag: 5–8 hashtag sát chủ đề (tiếng Việt không dấu, viết liền, ví dụ #lichsutrungquoc)
  + 2–4 hashtag khám phá phổ biến ở Việt Nam (ví dụ #xuhuong #fyp #viral). Mỗi hashtag bắt đầu bằng #.
- original_hashtags: hashtag gốc của video (nếu có) dịch sang tiếng Việt không dấu.
- tiktok_caption: ≤ 150 ký tự kể cả hashtag (title rút gọn + 4–6 hashtag).
- youtube_title: ≤ 90 ký tự, có từ khoá chính ở đầu; youtube_description: 3–5 câu + dòng hashtag (3 cái).
- facebook_caption: 2–4 câu kể chuyện + 3–5 hashtag.
{credit}Chỉ trả về JSON đúng schema."""


def _plain_tag(text):
    """'Lịch sử Trung Quốc' -> '#lichsutrungquoc'."""
    t = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^0-9A-Za-z]+", "", t).lower()
    return "#" + t if t else ""


def _clean_tags(tags):
    out = []
    for t in tags or []:
        t = t.strip()
        if not t:
            continue
        t = t if t.startswith("#") and re.fullmatch(r"#[0-9a-z_]+", t.lower()) else _plain_tag(t.lstrip("#"))
        if t and t.lower() not in [x.lower() for x in out] and len(t) > 2:
            out.append(t.lower())
    return out


def _material(d: Path):
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8")) if (d / "meta.json").exists() else {}
    title = meta.get("title") or ""
    orig_tags = re.findall(r"#\s*([^#\s]+)", title)
    script = []
    if (d / "lines.json").exists():
        script = [l["tgt"] for l in json.loads((d / "lines.json").read_text(encoding="utf-8"))]
    screen = []
    if (d / "screentext.json").exists():
        screen = [e.get("tgt") or e.get("src") for e in json.loads((d / "screentext.json").read_text(encoding="utf-8")) or []]
    return meta, title, orig_tags, script, screen


class _AsSummary:
    """Providers report progress on the "translate" step; while writing the post it belongs to "summary"."""
    def __init__(self, job):
        self._job = job

    def __getattr__(self, k):
        return getattr(self._job, k)

    def __setattr__(self, k, v):          # e.g. job.proc = <subprocess> must reach the real job (cancel)
        if k == "_job":
            object.__setattr__(self, k, v)
        else:
            setattr(self._job, k, v)

    def step(self, sid, *a, **k):
        return self._job.step("summary" if sid == "translate" else sid, *a, **k)


def generate(job, d: Path, s) -> dict:
    job = _AsSummary(job)
    meta, title, orig_tags, script, screen = _material(d)
    text = " ".join(script)
    if len(text) > 9000:                     # long video: beginning + end are enough for a post
        text = text[:6000] + " … " + text[-3000:]
    credit = (f"- Trong youtube_description và facebook_caption thêm dòng ghi nguồn: 'Nguồn: {meta.get('uploader')}'.\n"
              if s.get("brand_credit") and meta.get("uploader") else "")
    user = (f"Tiêu đề/mô tả gốc (có thể là tiếng Trung): {title or '(không có)'}\n"
            f"Tác giả gốc: {meta.get('uploader') or '(không rõ)'}\n"
            + (f"Chữ trên màn hình (đã dịch): {' | '.join(x for x in screen if x)[:1500]}\n" if screen else "")
            + f"Lời thoại đã dịch:\n{text or '(video không có lời thoại)'}")
    from . import styles
    st = styles.load(d)
    if st and st.get("preset") not in (None, "natural", "serious"):
        user += (f"\n\nGiọng văn của bản dịch: {st.get('name')}. Viết tiêu đề, mô tả và caption cùng giọng đó "
                 f"(vẫn không tục tĩu, không giật tít sai sự thật).")
    sysmsg = SYSTEM.format(credit=credit)

    def task(p):
        if p.kind == "mt":
            return _simple(job, p, s, title, orig_tags, script)
        raw = p.complete(job, sysmsg, user, SCHEMA, s)
        try:
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0))
            assert data.get("title") and isinstance(data.get("hashtags"), list)
            return data
        except Exception as e:
            raise BadOutput(f"Nội dung đăng bài không đúng JSON: {e}")

    data, pid = orchestrator.run(job, s, task, "viết tiêu đề & hashtag")
    tags = _clean_tags(data.get("hashtags"))
    for t in COMMON_TAGS[:2]:
        if len(tags) < 6 and t not in tags:
            tags.append(t)
    post = {
        "title": data["title"].strip(), "description": (data.get("description") or "").strip(),
        "hashtags": tags, "original_hashtags": _clean_tags(data.get("original_hashtags")),
        "source_title": title, "provider": pid,
        "platforms": {
            "tiktok": {"caption": (data.get("tiktok_caption") or "").strip()},
            "youtube": {"title": (data.get("youtube_title") or data["title"]).strip()[:100],
                        "description": (data.get("youtube_description") or "").strip()},
            "facebook": {"caption": (data.get("facebook_caption") or "").strip()},
        },
    }
    (d / FILE).write_text(json.dumps(post, ensure_ascii=False, indent=1), encoding="utf-8")
    return post


def _simple(job, p, s, title, orig_tags, script):
    """Machine-translation-only fallback."""
    clean_title = re.sub(r"#\S+", "", title).strip()
    items = [clean_title or (script[0] if script else "Video")] + orig_tags
    tr = p.translate_list(job, items, s.get("target_lang", "vi"), s)
    t = (tr[0] or items[0]).strip()[:70] if tr else items[0]
    tags = _clean_tags(tr[1:]) + COMMON_TAGS[:3]
    desc = " ".join(script[:2])[:220]
    line = " ".join(tags[:6])
    return {"title": t, "description": desc, "hashtags": tags, "original_hashtags": _clean_tags(tr[1:]),
            "tiktok_caption": f"{t[:90]} {line}"[:150], "youtube_title": t,
            "youtube_description": f"{desc}\n\n{' '.join(tags[:3])}", "facebook_caption": f"{t}\n{desc}\n{line}"}


def load(d: Path):
    p = d / FILE
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
