"""Translation style: presets + per-video auto suggestion.

settings:
  translate_style_preset  auto | <preset id> | custom
  translate_style         free text, used when the preset is "custom"
  style_auto_lean         bua | neutral — what "auto" leans to for entertainment videos

"auto" asks the translation LLM (one short call: title + first lines of the transcript / on-screen text) which
preset fits this video, plus a one-line note for it (e.g. "gọi nhân vật chính là 'ông anh Red Bull'"). Serious or
sensitive videos (death, disasters, war, illness, sad news) never get the comedy presets.

The resolved style is saved in style.json in the job folder, so re-running a step translates in the same style;
changing the preset (job page → "Dịch lại theo phong cách này") resolves it again.
"""
import json
import re
from pathlib import Path

FILE = "style.json"

PRESETS = {
    "natural": ("Tự nhiên",
                "Giọng kể tự nhiên, gần gũi như người Việt nói chuyện, câu ngắn gọn để đọc kịp thời lượng."),
    "bua": ("Bựa, hài lầy",
            "BỰA, hài lầy kiểu mạng xã hội Việt Nam. Dịch ĐÚNG Ý nhưng kể bằng giọng cà khịa, lầy lội: so sánh "
            "hài hước, phóng đại vui, chêm từ lóng Gen Z có chừng mực (vd: 'ối dồi ôi', 'xỉu ngang', 'khum', "
            "'đỉnh nóc kịch trần', 'pha xử lý cồng kềnh', 'tấu hài', 'quả này căng', 'lầy hết nấc', "
            "'ảo thật đấy', 'chuẩn không cần chỉnh', 'mặn mòi'). Xưng hô suồng sã hợp ngữ cảnh (tôi/ông/bà/"
            "anh em/mấy bồ, gọi nhân vật bằng biệt danh vui), thêm câu cảm thán/bình luận ngắn kiểu người xem "
            "('ủa alo?', 'thôi xong', 'nghe mà chảy nước miếng'). Khoảng một nửa số câu có 'muối', các câu còn lại vẫn "
            "kể rõ ràng để người xem theo kịp nội dung; đừng nhồi từ lóng vào mọi câu. KHÔNG chửi thề, KHÔNG "
            "tục tĩu, KHÔNG xúc phạm vùng miền/giới tính/tôn giáo/ngoại hình, KHÔNG bịa thêm sự kiện. "
            "Câu vẫn phải ngắn để đọc kịp thời lượng."),
    "hype": ("Kịch tính, hype",
             "Giọng bình luận viên thể thao / phim hành động: dồn dập, đầy năng lượng, câu ngắn có nhịp, "
             "cảm thán đúng lúc ('Không thể tin nổi!', 'Quá đỉnh!'), nhấn vào khoảnh khắc cao trào. "
             "Không bịa thêm chi tiết."),
    "story": ("Kể chuyện cuốn hút",
              "Giọng kể chuyện cuốn hút kiểu kênh lịch sử / khám phá: mở câu gợi tò mò, giữ mạch kể, dùng từ "
              "hình ảnh nhưng rõ nghĩa, giữ đúng sự kiện, tên người, mốc thời gian."),
    "cute": ("Dễ thương",
             "Giọng dễ thương, ấm áp, hơi nhí nhảnh (thú cưng, em bé, nấu ăn, đời thường): từ ngữ mềm, "
             "gọi con vật/em bé bằng tên cưng, cảm thán nhẹ ('ui dễ thương xỉu', 'thơm phức'). Câu ngắn."),
    "serious": ("Nghiêm túc, tin tức",
                "Giọng nghiêm túc, trung tính như bản tin: chính xác, rõ ràng, không đùa cợt, không cảm thán, "
                "tôn trọng người trong cuộc."),
}
COMEDY = {"bua"}
SCHEMA = {"type": "object", "required": ["preset", "why"],
          "properties": {"preset": {"type": "string"}, "note": {"type": "string"}, "why": {"type": "string"}}}
SYSTEM = """Bạn chọn PHONG CÁCH DỊCH lồng tiếng tiếng Việt cho một video ngắn, dựa vào tiêu đề và vài câu đầu.
Các phong cách:
{menu}
{lean}
TUYỆT ĐỐI không chọn "bua" cho nội dung nghiêm túc/nhạy cảm: tai nạn có người chết hoặc bị thương nặng, thiên tai,
chiến tranh, bệnh tật, tin buồn, tội phạm, tôn giáo, chính trị — khi đó chọn "serious" hoặc "story".
- preset: một id trong danh sách trên.
- note: (tuỳ chọn) 1 câu gợi ý riêng cho video này, vd biệt danh vui cho nhân vật chính, cách xưng hô, từ khoá nên giữ.
- why: lý do ngắn (≤ 15 từ, tiếng Việt).
Chỉ trả về JSON."""
LEAN = {
    "bua": 'Người dùng THÍCH phong cách bựa: video giải trí (hài, thử thách, mạo hiểm, game, thể thao vui, đời '
           'thường, review, thú vị/lạ) → chọn "bua". Chỉ chọn phong cách khác khi nội dung thật sự không hợp đùa.',
    "neutral": "Chọn phong cách hợp nhất với nội dung video.",
}


def menu():
    return [{"id": k, "name": v[0], "text": v[1]} for k, v in PRESETS.items()]


def _key(s):
    p = s.get("translate_style_preset") or "auto"
    return json.dumps([p, s.get("translate_style", "") if p == "custom" else "", s.get("style_auto_lean", "bua")],
                      ensure_ascii=False)


def text_for(preset, note=""):
    t = PRESETS.get(preset, PRESETS["natural"])[1]
    return t + (f"\nGhi chú riêng cho video này: {note}" if note else "")


def suggest(job, s, title="", samples=()) -> dict:
    """Ask the LLM which preset fits this video. Machine-translation providers can't do this → natural."""
    from .providers import orchestrator
    from .providers.base import BadOutput
    lines = [x for x in samples if x and x.strip()][:40]
    sysmsg = SYSTEM.format(menu="\n".join(f'- "{k}": {v[0]} — {v[1][:160]}' for k, v in PRESETS.items()),
                           lean=LEAN.get(s.get("style_auto_lean", "bua"), LEAN["neutral"]))
    user = f"Tiêu đề: {title or '(không có)'}\nVài câu đầu (lời nói / chữ trên màn hình):\n" + \
        ("\n".join(lines)[:2500] or "(không có lời)")

    def task(p):
        if p.kind == "mt":
            return {"preset": "natural", "note": "", "why": "Dịch máy không đổi được giọng văn"}
        raw = p.complete(job, sysmsg, user, SCHEMA, s)
        try:
            data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        except Exception as e:
            raise BadOutput(f"Gợi ý phong cách không đúng JSON: {e}")
        if data.get("preset") not in PRESETS:
            raise BadOutput(f"Phong cách không có trong danh sách: {data.get('preset')}")
        return data

    data, pid = orchestrator.run(job, s, task, "chọn phong cách dịch")
    return {"preset": data["preset"], "note": (data.get("note") or "").strip()[:200],
            "why": (data.get("why") or "").strip()[:120], "provider": pid}


def resolve(job, d: Path, s, title="", samples=()) -> dict:
    """Return settings with translate_style filled in for this video (and save the choice to style.json)."""
    preset = s.get("translate_style_preset") or "auto"
    key = _key(s)
    old = load(d)
    if old and old.get("key") == key and old.get("text"):
        return dict(s, translate_style=old["text"])
    if preset == "custom":
        info = {"preset": "custom", "auto": False, "text": s.get("translate_style") or text_for("natural")}
    elif preset in PRESETS:
        info = {"preset": preset, "auto": False, "text": text_for(preset)}
    else:
        try:
            g = suggest(job, s, title, samples)
            info = {"preset": g["preset"], "auto": True, "note": g["note"], "why": g["why"],
                    "text": text_for(g["preset"], g["note"])}
            job.log(f"Phong cách dịch (tự chọn): {PRESETS[g['preset']][0]} — {g['why']}"
                    + (f" · {g['note']}" if g["note"] else ""))
        except Exception as e:
            from .jobs import Cancelled
            if isinstance(e, Cancelled):
                raise
            job.log(f"Không tự chọn được phong cách ({str(e).splitlines()[0][:150]}) → dùng Tự nhiên", "warn")
            info = {"preset": "natural", "auto": True, "why": "Mặc định (lỗi khi tự chọn)", "text": text_for("natural")}
    info["key"] = key
    info["name"] = PRESETS[info["preset"]][0] if info["preset"] in PRESETS else "Tuỳ chỉnh"
    (d / FILE).write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return dict(s, translate_style=info["text"])


def load(d: Path):
    p = d / FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# ---- pick the style INSIDE the first translation call (one AI call instead of two) ---------------------------------
def cached(d: Path, s) -> bool:
    old = load(d)
    return bool(old and old.get("key") == _key(s) and old.get("text"))


def pick_rule(s) -> str:
    """Replaces the fixed style line of the translation prompt: the translator chooses the style itself."""
    menu = "\n".join(f'     - "{k}": {v[0]} — {v[1][:150]}' for k, v in PRESETS.items())
    return ("CHỌN 1 phong cách hợp nhất với video này (đọc hết lời gốc + tiêu đề trước):\n" + menu + "\n     "
            + LEAN.get(s.get("style_auto_lean", "bua"), LEAN["neutral"]) + "\n     TUYỆT ĐỐI không chọn \"bua\" "
            "cho nội dung nghiêm túc/nhạy cảm (tai nạn, thiên tai, chiến tranh, bệnh tật, tin buồn, tội phạm). "
            'Ghi id đã chọn vào "style", 1 câu gợi ý riêng (biệt danh, xưng hô…) vào "style_note", lý do ngắn vào '
            '"style_why"; rồi DỊCH TOÀN BỘ theo đúng phong cách đó (mô tả đầy đủ của từng phong cách ở trên).')


def record(job, d: Path, s, preset, note="", why="") -> dict:
    """Save a style the translator picked, exactly like resolve() would (later steps reuse it)."""
    preset = preset if preset in PRESETS else "natural"
    info = {"preset": preset, "auto": True, "note": (note or "").strip()[:200], "why": (why or "").strip()[:120],
            "text": text_for(preset, (note or "").strip()[:200]), "key": _key(s), "name": PRESETS[preset][0],
            "by": "translate"}
    (Path(d) / FILE).write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    job.log(f"Phong cách dịch (AI chọn khi dịch): {info['name']}" + (f" — {info['why']}" if info["why"] else "")
            + (f" · {info['note']}" if info["note"] else ""))
    return dict(s, translate_style=info["text"])
