"""One-time: split dubber/static/index.html (CSS + one 1 600-line script) into app.css and js/*.js.

The scripts stay CLASSIC scripts loaded in order (not ES modules), so every top-level function / let / const is still
a global shared by all files — behaviour is unchanged, the code is just easier to find and edit.
The original is kept as index.single.html.
"""
import re
import shutil
from pathlib import Path

ST = Path(__file__).resolve().parent.parent / "dubber" / "static"
src = (ST / "index.html").read_text(encoding="utf-8")
if "/static/js/" in src:
    raise SystemExit("Đã tách rồi.")
shutil.copyfile(ST / "index.html", ST / "index.single.html")

# ---- CSS
m = re.search(r"<style>\n(.*?)</style>\n", src, re.S)
(ST / "app.css").write_text(m.group(1), encoding="utf-8")
src = src[:m.start()] + '<link rel="stylesheet" href="/static/app.css?v=__V__">\n' + src[m.end():]

# ---- JS
m = re.search(r"<script>\n(.*?)</script>\n", src, re.S)
js = m.group(1)
FILES = [  # (file, first section marker it starts at; None = from the top)
    ("01-core.js", None),
    ("02-settings.js", "settings dialog"),
    ("03-jobs.js", "jobs list"),
    ("04-content.js", "Biên kịch (content plan)"),
    ("05-views.js", "view switch"),
    ("06-review.js", "📋 Đánh giá & sửa"),
    ("07-hot.js", "🚀 Link hot"),
    ("08-library.js", "video library"),
    ("09-trending.js", "trending"),
    ("10-boot.js", "boot"),
]
pos = []
for name, mark in FILES:
    if mark is None:
        pos.append(0)
        continue
    k = js.index(f"/* ---------------- {mark} ---------------- */")
    assert js.count(f"/* ---------------- {mark} ---------------- */") == 1, mark
    pos.append(k)
assert pos == sorted(pos), pos
(ST / "js").mkdir(exist_ok=True)
tags = []
for (name, _), a, b in zip(FILES, pos, pos[1:] + [len(js)]):
    (ST / "js" / name).write_text(js[a:b].rstrip("\n") + "\n", encoding="utf-8")
    tags.append(f'<script src="/static/js/{name}?v=__V__"></script>')
src = src[:m.start()] + "\n".join(tags) + "\n" + src[m.end():]
(ST / "index.html").write_text(src, encoding="utf-8")
print("index.html:", len(src.splitlines()), "dòng;", "app.css:", len((ST / "app.css").read_text(encoding="utf-8").splitlines()), "dòng")
for name, _ in FILES:
    print(f"  js/{name}: {len((ST / 'js' / name).read_text(encoding='utf-8').splitlines())} dòng")
