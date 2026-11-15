"""Subtitle cues from whole sentences.

A translated line is a whole sentence, read as ONE voice clip. On screen a long sentence is shown in several cues,
split where a Vietnamese speaker would pause (punctuation first, then before a linking word), never in the middle of a
phrase, and timed by syllable count across the time the voice reads it.
"""
import re

MAX_WORDS = 13            # one cue (≤ two rows in the white box)
MIN_WORDS = 4             # no cue of a few orphan words
STRONG = re.compile(r"[,;:…!?.—–]$|[\"”’»)]$")
# a cue may start with these (the sentence turns here); breaking before them reads naturally
LINK = {"thì", "mà", "nhưng", "nên", "vì", "để", "khi", "rồi", "còn", "và", "hay", "hoặc", "nếu", "dù", "tuy",
        "chứ", "cho", "lại", "cũng", "liền", "bèn", "trong", "sau", "trước", "từ", "với", "ai", "ngay"}
PAUSE = 1.5               # a comma / full stop takes about this many syllables of time


def split(text: str, max_words=MAX_WORDS, min_words=MIN_WORDS) -> list[str]:
    words = text.split()
    if len(words) <= max_words:
        return [text.strip()] if text.strip() else []
    best, best_score, best_kind = None, None, 8
    for i in range(min_words, len(words) - min_words + 1):
        left, nxt = words[i - 1], words[i].lower().strip("\"“'(")
        if STRONG.search(left):
            kind = 0
        elif nxt in LINK:
            kind = 3
        else:
            kind = 8
        # balance: the two halves as even as possible, but a strong break wins over balance
        score = kind + abs(len(words) - 2 * i) / len(words) * 4
        if best_score is None or score < best_score:
            best, best_score, best_kind = i, score, kind
    if best_kind == 8 and len(words) <= max_words + 4:
        return [" ".join(words)]                   # a bit long, but cutting a phrase in two reads worse
    if best is None:                               # too short to split with the minimum on both sides
        best = len(words) // 2
    return (split(" ".join(words[:best]), max_words, min_words)
            + split(" ".join(words[best:]), max_words, min_words))


def _weight(part: str) -> float:
    return len(part.split()) + (PAUSE if STRONG.search(part.strip()) else 0.0)


def cues(text: str, a: float, b: float, read_end: float | None = None) -> list[tuple[float, float, str]]:
    """Cues of one sentence shown from a to b. read_end = when the voice finishes (cues follow the voice; the last
    one stays up until b)."""
    parts = split(" ".join(text.split()))
    if len(parts) <= 1:
        return [(a, b, parts[0] if parts else text)]
    e = read_end if read_end and a < read_end <= b else b
    w = [_weight(p) for p in parts]
    tot = sum(w)
    out, t = [], a
    for k, (p, x) in enumerate(zip(parts, w)):
        t2 = b if k == len(parts) - 1 else t + (e - a) * x / tot
        out.append((round(t, 3), round(t2, 3), p))
        t = t2
    return out
