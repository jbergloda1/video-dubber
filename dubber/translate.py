"""Translate + re-segment the transcript, then align phrases back to word timings."""
import difflib
import json
import threading
import re
from pathlib import Path


LANG_NAMES = {"vi": "tiếng Việt", "en": "English", "zh": "中文", "ja": "日本語", "ko": "한국어",
              "th": "ภาษาไทย", "id": "Bahasa Indonesia", "es": "Español", "fr": "Français"}

SYSTEM = """Bạn là biên dịch viên lồng tiếng chuyên nghiệp cho video ngắn (Douyin/TikTok).
Nhiệm vụ: từ bản nhận dạng giọng nói tự động (có thể sai chính tả/nghe nhầm), tạo kịch bản lồng tiếng {target}.

Quy tắc:
1. MỖI MỤC LÀ MỘT CÂU TRỌN Ý. Bản nhận dạng tự động cắt lời nói thành mẩu theo độ dài, thường cắt NGANG câu, có khi
   ngang cả một từ (vd "…嫩乎乎跟豆" / "花似的羊尾油"). Hãy GỘP các mẩu liên tiếp thành câu hoàn chỉnh rồi mới dịch.
   Mỗi mục được đọc thành MỘT hơi liền mạch, nên "tgt" phải là câu {target} ĐỨNG MỘT MÌNH VẪN HIỂU, đúng ngữ pháp,
   đúng trật tự từ {target} (không theo trật tự tiếng Trung). Tuyệt đối không để một mục là nửa câu phải chờ mục sau
   mới có nghĩa (SAI: "Người đây sáng mà thiếu một bát" / "mỡ đuôi cừu mềm như tàu hũ," / "thì coi như phí cả ngày!";
   ĐÚNG: một mục "Ở đây, sáng nào không có bát mỡ đuôi cừu mềm như tàu hũ là coi như phí cả ngày!").
   Độ dài: một câu thường 1–3 mẩu gốc, tối đa khoảng 8 giây lời gốc / ~30 từ tiếng Việt; câu quá dài thì tách tại
   chỗ ngắt ý tự nhiên sao cho MỖI PHẦN vẫn là câu trọn nghĩa. Không gộp lời của hai người khác nhau (hỏi – đáp,
   đối thoại) vào một mục; không gộp qua một khoảng lặng dài (> 1 giây) giữa hai ý.
2. "src" phải là đoạn LIÊN TIẾP của lời gốc, theo đúng thứ tự, không bỏ sót, không lặp. Được sửa lỗi nhận dạng (đồng âm, nghe nhầm) cho đúng nghĩa.
3. "tgt" là bản dịch {target} tự nhiên để ĐỌC THÀNH TIẾNG, không dịch word-by-word. Giữ ý, giữ cảm xúc, giữ xưng hô hợp ngữ cảnh.
   Viết như người Việt kể chuyện: chủ ngữ rõ ràng ("Người dân ở đây", không phải "Người đây"), từ nối mượt, không
   cụt lủn. Trước khi trả kết quả, đọc lại từng "tgt" MỘT MÌNH: câu nào không có nghĩa thì viết lại.
4. Tiếng Việt thường dài hơn tiếng Trung: dịch gọn để đọc kịp trong khoảng thời gian của câu gốc (cột dur, giây). Ưu tiên câu ngắn.
5. Tên riêng, thuật ngữ: dùng cách gọi phổ biến của người Việt (ví dụ 长城 → Vạn Lý Trường Thành).
6. Phong cách: {style}{hook_rule}
7. Chỉ trả về JSON hợp lệ dạng: {json_shape} — không giải thích gì thêm."""

# Opening hook: the first seconds decide whether a viewer scrolls away.
HOOK_RULE = """
8. MỞ ĐẦU CHẤN ĐỘNG (3 giây đầu quyết định người xem có lướt qua không): đọc hết lời gốc để biết điểm hấp dẫn nhất
   của video (kết quả bất ngờ, con số khủng, pha nguy hiểm, bí mật, cú twist), rồi viết lại 1–2 cụm "tgt" ĐẦU TIÊN thành
   câu mở đầu gây tò mò mạnh: câu hỏi gây sốc, lời hứa ("xem đến cuối mới thấy…"), hoặc úp mở kết quả. Vẫn khớp ý cụm gốc
   đó và đúng nội dung video, KHÔNG bịa sự kiện/con số không có trong video, KHÔNG giật tít sai sự thật, vẫn đủ ngắn để
   đọc kịp thời lượng. "src" của các cụm này giữ nguyên lời gốc như quy tắc 2."""
HOOK_GAP_RULE = """
   Video còn {gap:.1f} giây im lặng TRƯỚC câu nói đầu tiên: viết thêm "hook" — MỘT câu mở đầu chấn động riêng (tối đa
   {words} từ, đọc trong {gap:.1f} giây) đặt vào khoảng lặng đó; khi đó các cụm đầu có thể dịch bình thường."""
JSON_PLAIN = '{{"lines":[{{"src":"...","tgt":"..."}}, ...]}}'
JSON_HOOK = '{{"hook":"...","lines":[{{"src":"...","tgt":"..."}}, ...]}}'
STYLE_FIELDS = '"style":"<id>","style_note":"...","style_why":"...",'
HOOK_MIN_GAP = 1.5          # seconds of silence before the first line needed for a separate hook line


def hook_gap(lines):
    """Seconds of silence before the first spoken line (0 if speech starts right away)."""
    return float(lines[0]["start"]) if lines else 0.0


def build_prompt(lines, target, style, context="", hook=False, gap=0.0, pick_style=None):
    body = "\n".join(f"[{l['start']:.1f}s dur={l['end']-l['start']:.1f}] {l['text']}" for l in lines)
    rule, shape = "", JSON_PLAIN
    if hook:
        rule = HOOK_RULE
        if gap >= HOOK_MIN_GAP:
            rule += HOOK_GAP_RULE.format(gap=gap - 0.4, words=max(5, min(22, int((gap - 0.4) * 3.2))))
            shape = JSON_HOOK
    if pick_style:                      # the translator chooses the style (see styles.pick_rule)
        style = pick_style
        shape = shape.replace('{{"', '{{' + STYLE_FIELDS, 1)
    sysmsg = SYSTEM.format(target=LANG_NAMES.get(target, target), style=style, hook_rule=rule,
                           json_shape=shape.replace("{{", "{").replace("}}", "}"))
    user = (f"Ngữ cảnh video: {context}\n\n" if context else "") + "Lời gốc (theo thời gian):\n" + body
    return sysmsg, user


def add_hook(lines, hook, gap):
    """Put the separate hook sentence into the silence before the first line."""
    hook = (hook or "").strip()
    if not hook or gap < HOOK_MIN_GAP or PLACEHOLDER.match(hook):
        return lines
    return [{"src": "", "tgt": hook, "start": 0.15, "end": round(max(gap - 0.25, 0.9), 3), "hook": True}] + lines


PLACEHOLDER = re.compile(r"^\s*[\[\(（【].*[\]\)）】]\s*$|^\s*(?:\.{3}|…)\s*$")


def drop_placeholders(job, lines):
    """Lines the LLM could not make out ("[Không rõ lời]", "(tiếng hét)", "…") are not read aloud or subtitled."""
    keep = [l for l in lines if l.get("tgt", "").strip() and not PLACEHOLDER.match(l["tgt"])]
    if len(keep) < len(lines):
        job.log(f"Bỏ {len(lines) - len(keep)} câu không rõ lời (vd “{next(l['tgt'] for l in lines if l not in keep)}”)")
    return keep


def _parse_json(text):
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("LLM không trả về JSON")
    data = json.loads(m.group(0))
    out = [{"src": str(x.get("src", "")).strip(), "tgt": str(x.get("tgt", "")).strip()}
           for x in data["lines"] if str(x.get("tgt", "")).strip()]
    if not out:
        raise ValueError("JSON rỗng")
    return out


# ------------------------------------------------------------- providers (see dubber/providers/)
SCHEMA = {"type": "object", "required": ["lines"], "properties": {"hook": {"type": "string"},
    "style": {"type": "string"}, "style_note": {"type": "string"}, "style_why": {"type": "string"}, "lines": {"type": "array", "items": {
    "type": "object", "required": ["src", "tgt"],
    "properties": {"src": {"type": "string"}, "tgt": {"type": "string"}}}}}}

from .providers import orchestrator                     # noqa: E402
from .providers.base import BadOutput                   # noqa: E402
from .providers.llm import find_claude                  # noqa: E402,F401  (re-exported for app.py)

NoProvider = orchestrator.NoProvider
CHUNK = 120


def _progress_key(lines, s, title):
    import hashlib
    raw = json.dumps([[l["text"] for l in lines], s["target_lang"], s["translate_style"], title,
                      bool(s.get("hook_intro", True))], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def translate(job, asr, s, title="", progress_path: Path | None = None, style_dir: Path | None = None):
    """Segment + translate the transcript chunk by chunk through the provider chain.
    Finished chunks are saved to progress_path, so a failed/cancelled run resumes where it stopped
    instead of paying for the same chunks again. Returns (aligned lines, match ratio)."""
    lines = asr["lines"]
    key = _progress_key(lines, s, title)
    prog = {"key": key, "chunks": {}}
    if progress_path and progress_path.exists():
        try:
            old = json.loads(progress_path.read_text(encoding="utf-8"))
            if old.get("key") == key:
                prog = old
                if prog["chunks"]:
                    job.log(f"Dùng lại {len(prog['chunks'])} khối đã dịch từ lần chạy trước")
        except Exception:
            pass
    hook = ""
    use_hook, gap = bool(s.get("hook_intro", True)), hook_gap(lines)
    n_chunks = (len(lines) + CHUNK - 1) // CHUNK
    parts = {ci: prog["chunks"][str(ci)]["script"] for ci in range(n_chunks) if prog["chunks"].get(str(ci))}
    for ci in parts:
        hook = hook or prog["chunks"][str(ci)].get("hook", "")
    lock = threading.Lock()
    done_n = [len(parts)]

    def run_chunk(ci, s_now, context):
        part = lines[ci * CHUNK:(ci + 1) * CHUNK]
        # the opening hook is written in the FIRST block only (it sees the most of the video, incl. the title);
        # with style "✨ auto" the first block also picks the style (no separate AI call for it)
        from . import styles
        pick = styles.pick_rule(s_now) if (style_dir is not None and ci == 0) else None
        sysmsg, user = build_prompt(part, s_now["target_lang"], s_now["translate_style"], title,
                                    hook=use_hook and ci == 0, gap=gap if ci == 0 else 0.0, pick_style=pick)
        if context:   # keeps wording consistent across chunks, even if the provider changes
            user += "\n\n" + context

        def task(p, part=part, sysmsg=sysmsg, user=user):
            if p.kind == "mt":
                res = p.translate_list(job, [l["text"] for l in part], s_now["target_lang"], s_now)
                return [{"src": l["text"], "tgt": t} for l, t in zip(part, res) if t]
            job.step("translate", detail=f"{p.id} đang dịch khối {ci + 1}/{n_chunks}…")
            raw = p.complete(job, sysmsg, user, SCHEMA, s_now)
            try:
                out = _parse_json(raw)
                if ci == 0:
                    m = re.search(r"\{.*\}", raw, re.S)
                    data = json.loads(m.group(0))
                    out_hook = str(data.get("hook") or "").strip()
                    picked = {k: str(data.get(k) or "").strip() for k in ("style", "style_note", "style_why")}
                    return out, out_hook, picked
                return out, "", {}
            except (ValueError, KeyError, TypeError) as e:
                raise BadOutput(f"Kết quả không phải JSON hợp lệ: {e}")

        res, pid = orchestrator.run(job, s_now, task, "dịch lời nói")
        part_script, part_hook, picked = res if isinstance(res, tuple) else (res, "", {})
        with lock:
            if ci == 0 or pid != prog.get("provider"):
                job.log(f"Khối {ci + 1}/{n_chunks} dịch bằng: {pid}")
            prog["provider"] = pid
            prog["chunks"][str(ci)] = {"provider": pid, "script": part_script, "hook": part_hook}
            if progress_path:
                progress_path.write_text(json.dumps(prog, ensure_ascii=False), encoding="utf-8")
            parts[ci] = part_script
            done_n[0] += 1
            job.step("translate", progress=done_n[0] / n_chunks * 0.9)
        return part_script, part_hook, picked, pid

    def tail_tgt(ci):
        prev = parts.get(ci - 1)
        return ("(Đoạn trước đã dịch kết thúc bằng: " + " / ".join(x["tgt"] for x in prev[-3:]) + ")") if prev else ""

    # block 1 alone: it writes the hook and (style ✨ auto) picks the style every later block follows
    if 0 not in parts and n_chunks:
        _, part_hook, picked, _ = run_chunk(0, s, "")
        hook = hook or part_hook
        if style_dir is not None and picked:
            from . import styles
            if styles.pick_rule(s) is not None:
                s = styles.record(job, style_dir, s, picked.get("style"), picked.get("style_note"), picked.get("style_why"))
    rest = [ci for ci in range(1, n_chunks) if ci not in parts]
    par = max(1, int(s.get("translate_parallel", 3) or 1))
    if par > 1 and len(rest) > 1:
        # long video: the other blocks go out side by side. Each one sees the ORIGINAL end of the block before it
        # (its translation is not ready yet) plus the start of block 1's translation, for the same voice/wording
        first = parts.get(0) or []
        voice = (" / ".join(x["tgt"] for x in first[:4]))

        def ctx(ci):
            src_tail = " / ".join(l["text"] for l in lines[ci * CHUNK - 3:ci * CHUNK])
            return (f"(Giữ cùng giọng văn, cách xưng hô với phần đầu đã dịch: {voice})\n"
                    f"(Câu gốc ngay trước khối này: {src_tail})")
        job.log(f"Dịch song song {min(par, len(rest))} khối một lúc ({len(rest)} khối còn lại)")
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(min(par, len(rest))) as ex:
            for fut in [ex.submit(run_chunk, ci, s, ctx(ci)) for ci in rest]:
                fut.result()
    else:
        for ci in rest:
            run_chunk(ci, s, tail_tgt(ci))
    script = [row for ci in range(n_chunks) for row in parts.get(ci, [])]
    if progress_path and progress_path.exists():
        progress_path.unlink()          # complete: next explicit re-run translates fresh
    out, matched = align(script, asr["words"], asr["lines"])
    out, n_join = join_fragments(out)
    if n_join:
        job.log(f"Gộp {n_join} mẩu câu bị cắt ngang thành câu trọn ý (đọc liền một hơi)")
    if use_hook:
        before = len(out)
        out = add_hook(out, hook, gap)
        if len(out) > before:
            job.log(f"Câu mở đầu (khoảng lặng {gap:.1f}s đầu video): “{out[0]['tgt']}”")
        elif out:
            job.log(f"Mở đầu: “{out[0]['tgt']}”")
    return out, matched


# ------------------------------------------------------------- half sentences → one sentence
# Each line is voiced as ONE clip and placed at its own start time. A sentence split over two lines ("…thiếu một bát" /
# "mỡ đuôi cừu mềm như tàu hũ," / "thì coi như phí cả ngày!") was read as three clips with a silence wherever the
# Vietnamese finished before the next Chinese fragment began: choppy, and each piece made no sense on its own.
SENT_END = re.compile(r"[.!?…。！？][\"”’»)\]]*\s*$")
JOIN_MAX_S = 12.0          # never build a line longer than this (seconds of original speech)
JOIN_MAX_GAP = 0.8         # a longer pause between two fragments is a real break


def _continues(a: str, b: str) -> bool:
    """Does line b carry on the sentence line a started?"""
    a, b = a.strip(), b.strip()
    if not a or not b or SENT_END.search(a):
        return False
    first = next((c for c in b if c.isalpha()), "")
    if not first:
        return False
    return first.islower() or a[-1].isalnum()       # "…một bát" + "mỡ đuôi…" / a line cut with no punctuation


def join_fragments(lines):
    """Merge lines that are halves of one sentence (no sentence end + next line starts in lower case, or the line
    stops with no punctuation at all), when the pause between them is short. Returns (lines, how many merged)."""
    out, n = [], 0
    for l in lines:
        p = out[-1] if out else None
        if (p and not p.get("hook") and not l.get("hook") and p.get("speaker") == l.get("speaker")
                and _continues(p["tgt"], l["tgt"]) and l["start"] - p["end"] <= JOIN_MAX_GAP
                and l["end"] - p["start"] <= JOIN_MAX_S):
            p["src"] = (p.get("src", "") + l.get("src", "")).strip()
            p["tgt"] = p["tgt"].rstrip() + " " + l["tgt"].lstrip()
            p["end"] = max(p["end"], l["end"])
            n += 1
            continue
        out.append(dict(l))
    return out, n


# ------------------------------------------------------------- alignment
KEEP = re.compile(r"\w", re.U)


def align(script, words, asr_lines):
    """Map each script phrase (src) onto Whisper word timings using fuzzy char matching."""
    chars, times = [], []
    for w in words:
        cs = [c.lower() for c in w["w"] if KEEP.match(c)]
        d = (w["e"] - w["s"]) / max(len(cs), 1)
        for i, c in enumerate(cs):
            chars.append(c)
            times.append((w["s"] + i * d, w["s"] + (i + 1) * d))
    mine, owner = [], []
    for idx, row in enumerate(script):
        for c in row["src"]:
            if KEEP.match(c):
                mine.append(c.lower())
                owner.append(idx)
    sm = difflib.SequenceMatcher(None, mine, chars, autojunk=False)
    m2t = {}
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            m2t[a + k] = b + k
    pos_by_line = [[] for _ in script]
    for i, o in enumerate(owner):
        if i in m2t:
            pos_by_line[o].append(m2t[i])
    out = []
    for row, pos in zip(script, pos_by_line):
        out.append({"src": row["src"], "tgt": row["tgt"],
                    "start": round(times[min(pos)][0], 3) if pos else None,
                    "end": round(times[max(pos)][1], 3) if pos else None})
    # interpolate any phrase that failed to match
    for i, l in enumerate(out):
        if l["start"] is None:
            prev = next((out[j]["end"] for j in range(i - 1, -1, -1) if out[j]["end"] is not None), 0.0)
            nxt = next((out[j]["start"] for j in range(i + 1, len(out)) if out[j]["start"] is not None), prev + 1.5)
            l["start"], l["end"] = prev, max(nxt, prev + 0.5)
    # keep strictly ordered
    for i in range(1, len(out)):
        if out[i]["start"] < out[i - 1]["start"]:
            out[i]["start"] = out[i - 1]["start"]
            out[i]["end"] = max(out[i]["end"], out[i]["start"] + 0.3)
    matched = len(m2t) / max(len(mine), 1)
    return out, matched


# ------------------------------------------------------------- on-screen text (titles, captions in the picture)
TEXT_SCHEMA = {"type": "object", "required": ["items"], "properties": {"items": {"type": "array", "items": {
    "type": "object", "required": ["id", "tgt"],
    "properties": {"id": {"type": "integer"}, "tgt": {"type": "string"}}}}}}

TEXT_SYSTEM = """Bạn dịch CHỮ HIỂN THỊ TRÊN MÀN HÌNH của video ngắn (tiêu đề, chú thích, nhãn) sang {target}.
Chữ do OCR đọc nên có thể sai vài ký tự — hãy sửa theo ngữ cảnh.
Quy tắc:
- Dịch tự nhiên, ĐỘ DÀI tương đương bản gốc (chữ sẽ được vẽ đè đúng chỗ cũ, nên đừng dài hơn nhiều).
- Tiêu đề: ngắn, giật tít như báo Việt. Chú thích: câu kể chuyện gọn.
- Tên người/đội/địa danh: dùng tên quốc tế/Việt hoá thông dụng (vd 齐达内 → Zidane, 意大利 → Italia, 欧国联 → UEFA Nations League).
- Giữ nguyên số, tỉ số, ngày giờ.
- Phong cách: {style}
Trả về JSON: {{"items":[{{"id":<id>,"tgt":"..."}}]}}"""


def translate_texts(job, events, s, context=""):
    """Translate on-screen text events in one request; fills e['tgt']."""
    if not events:
        return events
    target = s["target_lang"]
    sysmsg = TEXT_SYSTEM.format(target=LANG_NAMES.get(target, target), style=s["translate_style"])
    user = (f"Ngữ cảnh video: {context}\n\n" if context else "") + "Các đoạn chữ (id, thời gian, nội dung):\n" + \
        "\n".join(f"[{e['id']}] ({e['start']:.1f}s–{e['end']:.1f}s) {e['src']}" for e in events)

    def task(p):
        if p.kind == "mt":
            res = p.translate_list(job, [e["src"] for e in events], target, s)
            return {e["id"]: t for e, t in zip(events, res) if t}
        raw = p.complete(job, sysmsg, user, TEXT_SCHEMA, s)
        try:
            m = re.search(r"\{.*\}", raw, re.S)
            return {int(x["id"]): str(x["tgt"]).strip() for x in json.loads(m.group(0))["items"]}
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise BadOutput(f"Kết quả dịch chữ màn hình không đúng JSON: {e}")

    got, pid = orchestrator.run(job, s, task, "dịch chữ trên màn hình")
    job.log(f"Chữ trên màn hình dịch bằng: {pid}")
    for e in events:
        e["tgt"] = got.get(e["id"]) or e["tgt"] or e["src"]
    return events


# ------------------------------------------------------------- fit to time (2nd pass, after TTS measured each line)
# Vietnamese runs longer than Chinese. After the voice is made, the TTS step knows how fast each line would have to
# be read to fit before the next one (line["need"]). Lines that would need > FIT_SPEED are rewritten shorter by the
# LLM — given the original Chinese, the neighbours and a syllable budget — and only those lines are voiced again.
FIT_SPEED = 1.15
SHORTEN_SYSTEM = """Bạn là biên tập viên lồng tiếng tiếng Việt. Các câu dưới đây ĐỌC KHÔNG KỊP thời lượng của câu gốc.
Viết lại từng câu NGẮN HƠN, KHÔNG VƯỢT số âm tiết tối đa ghi kèm (mỗi chữ tiếng Việt = 1 âm tiết):
- Giữ ĐÚNG ý chính của câu gốc (tiếng Trung/ngôn ngữ gốc) và mạch với câu trước/sau.
- Bỏ từ đệm, từ lặp ý; dùng từ ngắn hơn; đổi cấu trúc câu; có thể nói gọn kiểu văn nói.
- KHÔNG bỏ tên riêng, con số, thông tin quan trọng. Không thêm ý mới.
- Giữ phong cách: {style}
Chỉ trả về JSON: {{"items":[{{"id":<id>,"tgt":"..."}}]}}"""


def syllables(text):
    return len(re.findall(r"\w+", text or ""))


def overlong(lines, limit=FIT_SPEED):
    """Indexes of lines read too fast to fit, that the user did not edit by hand."""
    return [i for i, l in enumerate(lines)
            if l.get("need", 0) > limit and not l.get("edited") and syllables(l.get("tgt")) > 2]


def _short_key(l, s):
    return json.dumps([l.get("src", ""), l["tgt"], s.get("translate_style", "")], ensure_ascii=False)


def shorten(job, lines, idxs, s, limit=FIT_SPEED, cache=None):
    """Rewrite lines[idxs] shorter (in place). Returns how many lines got shorter. `cache` (dict) remembers
    earlier rewrites of the same line, so they cost no AI call when the line comes back."""
    if not idxs:
        return 0
    rows, budget, n_cached = [], {}, 0
    if cache is not None:
        rest = []
        for i in idxs:
            l = lines[i]
            hit = cache.get(_short_key(l, s))
            b = max(2, int(syllables(l["tgt"]) * limit / max(l["need"], 1e-6)))
            if hit and syllables(hit) <= b and syllables(hit) < syllables(l["tgt"]):
                l.setdefault("tgt_full", l["tgt"])
                l["tgt"] = hit
                n_cached += 1
            else:
                rest.append(i)
        idxs = rest
        if not idxs:
            return n_cached
    for i in idxs:
        l = lines[i]
        n = syllables(l["tgt"])
        budget[i] = max(2, int(n * limit / max(l["need"], 1e-6)))
        prev = lines[i - 1]["tgt"] if i else ""
        nxt = lines[i + 1]["tgt"] if i + 1 < len(lines) else ""
        opening = (i == 0 or l.get("hook")) and s.get("hook_intro", True)
        rows.append(f"[{i}] gốc: {l.get('src') or '(câu mở đầu)'}\n     hiện tại ({n} âm tiết): {l['tgt']}\n"
                    f"     TỐI ĐA: {budget[i]} âm tiết | câu trước: {prev} | câu sau: {nxt}"
                    + ("\n     ĐÂY LÀ CÂU MỞ ĐẦU GÂY TÒ MÒ: giữ kiểu câu hỏi / úp mở / gây sốc, chỉ nói gọn lại"
                       if opening else ""))
    sysmsg = SHORTEN_SYSTEM.format(style=s.get("translate_style", ""))
    user = "\n".join(rows)

    def task(p):
        if p.kind == "mt":
            return {}                     # machine translation can't rewrite to a length budget
        raw = p.complete(job, sysmsg, user, TEXT_SCHEMA, s)
        try:
            m = re.search(r"\{.*\}", raw, re.S)
            return {int(x["id"]): str(x["tgt"]).strip() for x in json.loads(m.group(0))["items"]}
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise BadOutput(f"Kết quả rút gọn không đúng JSON: {e}")

    got, pid = orchestrator.run(job, s, task, "rút gọn câu cho vừa thời lượng")
    n_ok = 0
    for i in idxs:
        new = got.get(i, "")
        if new and not PLACEHOLDER.match(new) and syllables(new) < syllables(lines[i]["tgt"]):
            if cache is not None:
                cache[_short_key(lines[i], s)] = new
            lines[i].setdefault("tgt_full", lines[i]["tgt"])      # the longer first version, for reference
            lines[i]["tgt"] = new
            n_ok += 1
    return n_ok + n_cached
