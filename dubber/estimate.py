"""Estimate a job before it runs: time per step, money per paid service, electricity, disk.

- Time: learned from YOUR past jobs (each step records how long it took per second of video, see
  jobs.Job.step); steps never measured yet use defaults measured on an RTX 3060 (2026-11).
- Text volume: Chinese speech ≈ 5.3 chars/s, the Vietnamese translation ≈ 17 chars/s (measured on 3 jobs).
- Prices are list prices (USD) as of 2026-11; edit PRICES when they change. Subscription CLIs (Claude Code,
  Codex, Gemini CLI) cost nothing extra but use your plan's quota.
"""
import json
import statistics
import time

from . import config

# step -> (fixed seconds, seconds per second of video)
DEFAULT_RATE = {
    "download": (15, 0.12), "extract": (2, 0.02), "separate": (5, 0.27), "transcribe": (8, 0.12),
    "translate": (10, 0.25), "speakers": (5, 0.05), "tts": (5, 0.9), "detect_subs": (6, 0.02),
    "watermark": (5, 0.12), "screen_text": (10, 0.5), "content": (60, 0.15), "preview": (15, 0.0), "render": (5, 0.8),
    "summary": (15, 0.0), "tiktok": (10, 0.35), "analyze": (15, 0.05),
}
SRC_CHARS_S, VI_CHARS_S = 5.3, 17.0

# USD per 1M tokens (input, output)
PRICES = {
    "claude-fable-5-2": (8.0, 40.0), "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0), "claude-sonnet-5": (2.0, 10.0), "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-5-5": (0.10, 0.50), "claude-haiku-4-5": (1.0, 5.0),
    "gemini-2.5-flash": (0.30, 2.50), "gemini-2.5-pro": (1.25, 10.0), "gemini-2.5-flash-lite": (0.10, 0.40),
    "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.0), "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1": (2.0, 8.0),
}
FREE = {"claude_cli": "trừ vào hạn mức gói Claude", "codex_cli": "trừ vào hạn mức gói ChatGPT",
        "gemini_cli": "trừ vào hạn mức tài khoản Google", "ollama": "chạy trên GPU của bạn",
        "google_free": "miễn phí (hay bị chặn)", "local_nllb": "offline trên GPU của bạn"}
ELEVEN_CREDIT = {"eleven_flash_v2_5": 0.5, "eleven_turbo_v2_5": 0.5}     # credits per character (others 1)


def steps_for(s):
    """Which pipeline steps will actually run with these settings (mirrors pipeline.run)."""
    mode = s.get("output_mode", "dub")
    auto = mode == "auto"
    if auto:
        mode = "dub"          # not known before the video is analysed: estimate the most expensive common case
    text_only = mode == "text"
    on = {"download": True, "preview": True, "render": True,
          "summary": bool(s.get("post_summary", True)),
          "tiktok": bool(s.get("tiktok_check", True)), "analyze": auto}
    if not text_only:
        on.update(extract=True, transcribe=True, translate=True,
                  separate=mode in ("dub", "voiceover") and bool(s.get("separate_music")),
                  speakers=mode in ("dub", "voiceover") and (s.get("voice_gender_mode") == "auto" or bool(s.get("narrator_remove"))),
                  tts=mode in ("dub", "voiceover"), detect_subs=s.get("hide_original_subs") == "auto")
    on["watermark"] = (s.get("wm_remove") or "auto") != "off"
    on["screen_text"] = text_only or bool(s.get("screen_text"))
    from .jobs import STEP_IDS
    return [sid for sid in STEP_IDS if on.get(sid)]


def history():
    """step -> list of seconds-per-video-second from finished jobs on this machine."""
    out = {}
    for f in config.JOBS.glob("*/job.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        dur = float(d.get("video_duration") or 0)
        if dur < 3:
            continue
        for st in d.get("steps", []):
            secs = st.get("secs")
            if secs and st.get("status") == "done":
                fixed = DEFAULT_RATE.get(st["id"], (0, 0))[0]
                out.setdefault(st["id"], []).append(max(secs - fixed, 0) / dur)
    return out


def _fmt_usd(x):
    return 0.0 if x is None else round(x, 4)


def translate_tokens(dur, s):
    """(input, output) tokens for translating `dur` seconds of speech (+ on-screen text, + post summary)."""
    from .translate import CHUNK
    lines = dur * 0.4
    chunks = max(1, int(lines // CHUNK) + 1)
    tin = 1500 * chunks + dur * (SRC_CHARS_S * 1.2 + 0.4 * 15)
    tout = dur * (VI_CHARS_S / 2 + 0.4 * 20)
    if s.get("output_mode") == "text":
        tin, tout = 1500 + dur * 4, dur * 3
    elif s.get("screen_text"):
        tin, tout = tin + 1500 + dur * 4, tout + dur * 3
    if s.get("post_summary", True):
        tin, tout = tin + 1200 + min(dur * VI_CHARS_S / 2, 4000), tout + 600
    return int(tin), int(tout)


def translation_cost(dur, s):
    pid = s.get("translate_provider") or "claude_cli"
    tin, tout = translate_tokens(dur, s)
    if pid in FREE:
        return {"item": "Dịch (" + pid + ")", "usd": 0.0, "note": FREE[pid] + f" · ~{tin + tout:,} token"}
    model = {"anthropic": s.get("anthropic_model") or "claude-sonnet-5-5", "openai": s.get("openai_model") or "",
             "gemini": s.get("gemini_model") or "gemini-2.5-flash"}.get(pid, "")
    price = PRICES.get(model)
    if pid == "anthropic":
        tout *= 2           # adaptive thinking tokens are billed as output
    if not price:
        return {"item": f"Dịch ({pid} {model})", "usd": None,
                "note": f"~{tin:,} token vào + {tout:,} token ra; chưa có giá của model này trong bảng"}
    usd = tin / 1e6 * price[0] + tout / 1e6 * price[1]
    note = f"{model}: ~{tin:,} token vào + {tout:,} token ra"
    if pid == "gemini":
        note += " (có hạn mức miễn phí, khi còn hạn mức thì 0 đ)"
    return {"item": "Dịch (API)", "usd": usd, "note": note}


def elevenlabs_quota(s):
    """Your ElevenLabs credits left this period (None if no key / unreachable). The key never leaves the server."""
    key = s.get("elevenlabs_api_key")
    if not key:
        return None
    import urllib.request
    try:
        req = urllib.request.Request("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": key})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
        return {"used": d.get("character_count"), "limit": d.get("character_limit"),
                "reset": d.get("next_character_count_reset_unix"), "tier": d.get("tier")}
    except Exception:
        return None


def tts_cost(dur, s):
    prov = s.get("tts_provider") or "edge"
    chars = int(dur * VI_CHARS_S)
    if prov != "elevenlabs":
        return {"item": "Giọng đọc (" + ("Edge" if prov == "edge" else "Piper") + ")", "usd": 0.0,
                "note": f"miễn phí · ~{chars:,} ký tự"}, None
    model = s.get("elevenlabs_model") or "eleven_flash_v2_5"
    credits = int(chars * ELEVEN_CREDIT.get(model, 1.0))
    per_1k = float(s.get("elevenlabs_usd_per_1k") or 0.30)
    q = elevenlabs_quota(s)
    note = f"{model}: ~{chars:,} ký tự = ~{credits:,} credit"
    warn = None
    if q and q.get("limit"):
        left = q["limit"] - (q["used"] or 0)
        note += f" · gói còn {left:,}/{q['limit']:,} credit"
        if credits > left:
            warn = f"ElevenLabs có thể không đủ credit (cần ~{credits:,}, còn {left:,}) → câu thiếu sẽ đọc bằng giọng local"
        usd = 0.0 if credits <= left else (credits - left) / 1000 * per_1k
        note += " (trong hạn mức gói → không tốn thêm)" if credits <= left else ""
    else:
        usd = credits / 1000 * per_1k
        note += f" · ≈ ${per_1k}/1000 credit"
    return {"item": "Giọng đọc ElevenLabs", "usd": usd, "note": note}, warn


def estimate(dur, s, info=None):
    hist = history()
    big = max((info or {}).get("width") or 0, (info or {}).get("height") or 0) >= 2500
    steps = []
    from .jobs import STEPS
    names = dict(STEPS)
    lama = s.get("screen_text_clean") == "lama" or ((s.get("wm_remove") or "auto") != "off" and s.get("wm_clean") == "lama")
    for sid in steps_for(s):
        fixed, rate = DEFAULT_RATE.get(sid, (5, 0.1))
        samples = hist.get(sid) or []
        if samples:
            rate = statistics.median(samples)
        secs = fixed + rate * dur
        if not samples and sid in ("download", "render", "preview", "watermark", "screen_text") and big:
            secs *= 1.4                      # 4K source: decoding/downloading is slower (defaults measured on 1080p)
        if sid == "preview":
            secs = fixed + rate * min(dur, float(s.get("preview_seconds") or 30)) + (0 if samples else 0.8 * min(dur, 30))
        steps.append({"id": sid, "name": names.get(sid, sid), "secs": round(secs), "samples": len(samples)})
    total = sum(x["secs"] for x in steps)
    money, warns = [translation_cost(dur, s)], []
    if s.get("output_mode", "dub") in ("dub", "voiceover"):
        t, w = tts_cost(dur, s)
        money.append(t)
        if w:
            warns.append(w)
    # electricity: whole PC under GPU load ≈ 250 W
    watts = float(s.get("pc_watts") or 250)
    vnd_kwh = float(s.get("vnd_per_kwh") or 2500)
    kwh = watts * total / 3600 / 1000
    money.append({"item": "Điện (PC ~%d W)" % watts, "usd": None, "vnd": round(kwh * vnd_kwh),
                  "note": f"~{kwh:.3f} kWh × {vnd_kwh:,.0f} đ/kWh"})
    usd = sum(m["usd"] or 0 for m in money)
    rate_vnd = float(s.get("vnd_per_usd") or 26000)
    # disk: final + preview + intermediates (wav stems, clean video when cleaning text/watermarks)
    short = int(s.get("output_short_side") or 1080)
    mb_s = {720: 0.45, 1080: 0.85, 1440: 1.4, 2160: 2.6}.get(short, 0.85)
    disk = dur * (mb_s * 1.1 + 0.18 * 4 + 1.5) + (dur * mb_s * 1.6 if (lama or s.get("screen_text")) else 0)
    unknown = any(m["usd"] is None and "vnd" not in m for m in money)
    if any(x["samples"] == 0 for x in steps):
        warns.append("Một số bước chưa có số đo trên máy bạn → dùng số mặc định (sẽ chính xác dần sau vài job)")
    return {"duration": round(dur, 1), "title": (info or {}).get("title", ""), "uploader": (info or {}).get("uploader", ""),
            "steps": steps, "total_secs": round(total), "money": [{**m, "usd": _fmt_usd(m["usd"]) if m["usd"] is not None else None} for m in money],
            "total_usd": round(usd, 4), "total_vnd": round(usd * rate_vnd + sum(m.get("vnd", 0) for m in money)),
            "price_unknown": unknown, "disk_mb": round(disk), "warnings": warns, "at": time.time()}
