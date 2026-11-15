"""Settings (persisted to data/settings.json) and paths."""
import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
JOBS = DATA / "jobs"
SETTINGS_FILE = DATA / "settings.json"
JOBS.mkdir(parents=True, exist_ok=True)

SECRET_KEYS = {"anthropic_api_key", "elevenlabs_api_key", "openai_api_key", "gemini_api_key", "tiktok_client_secret"}

DEFAULTS = {
    # --- output mode ---
    "output_mode": "dub",             # dub | sub | text | voiceover (original audio kept quiet + Vietnamese narration)
    # --- language ---
    "source_lang": "auto",            # whisper language code or "auto"
    "target_lang": "vi",
    # --- speech recognition (runs on GPU) ---
    "whisper_model": "large-v3-turbo",
    "whisper_device": "cuda",
    # --- translation ---
    "translate_provider": "claude_cli",   # claude_cli | anthropic | ollama | google
    "translate_parallel": 3,
    # ── bước "Biên kịch" (content): hook, giọng kể, cắt khoảng lặng, cái kết, caption — off until it is stable
    "content_enabled": False,
    "content_review": True,               # stop after the plan is written until you approve it (tab Biên kịch)
    "content_persona": "auto",            # auto | toi | nguoi_dan_chuyen | nhan_vat_nu
    "content_tone": "auto",               # auto | tram_it_chu | ngon_tinh | doi_thuong
    "content_vision_provider": "codex_cli",
    "content_writer_provider": "codex_cli",
    "content_codex_model": "",            # empty = Codex CLI's default model
    "content_ollama_vision_model": "qwen2.5vl:7b",
    "content_ollama_model": "qwen2.5:7b",
    "content_voice_provider": "vieneu",   # the channel's narrator: VieNeu, else Edge
    "content_voice": "Thiền Tâm Đức",     # VieNeu voice (deep male) — Edge fallback: vi-VN-NamMinhNeural
    "content_vo_volume": 1.5,
    "content_short_s": 30,                # shorter = short_clip (hook text + caption only)
    "content_sparse_ratio": 0.15,         # silence ≥ this share of the video = story_sparse (full narration)
    "content_narrated_share": 0.7,        # one voice ≥ this share of the dialogue = already narrated          # long videos: blocks after the first are translated this many at a time
    "claude_cli_path": "claude",
    "claude_cli_model": "sonnet",
    "anthropic_api_key": "",
    "anthropic_model": "claude-sonnet-5-5",
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "qwen2.5:7b",
    # fallback chain: tried in this order after the selected provider; unconfigured / unreachable ones are skipped
    "translate_fallbacks": ["claude_cli", "codex_cli", "gemini_cli", "anthropic", "openai", "gemini", "ollama",
                            "google_free", "local_nllb"],
    "codex_model": "",                # empty = Codex CLI default model
    "gemini_cli_model": "",           # empty = Gemini CLI default model
    "translate_providers_seen": [],   # providers the user has already seen in the fallback editor
    "local_mt_model": "nllb-1.3B",    # built-in offline engine (local_nllb): nllb-1.3B | nllb-600M
    "translate_fallback_enabled": True,
    "translate_min_interval_s": {"google_free": 0.8, "gemini": 1.0, "gemini_cli": 1.0},   # per-provider pacing
    "translate_max_retries": 3,       # per provider, only for retryable errors (rate limit, timeout, 5xx, bad JSON)
    "translate_backoff_s": 2.0,       # exponential: 2, 4, 8… capped at 60 s
    "translate_timeout_s": 300,
    "openai_api_key": "",
    "openai_base_url": "https://api.openai.com/v1",   # or OpenRouter / LM Studio (http://127.0.0.1:1234/v1) / vLLM…
    "openai_model": "",
    "gemini_api_key": "",
    "gemini_model": "gemini-2.5-flash",
    "fit_to_time": True,              # after the voice is made, lines read > 1.15x too fast are rewritten shorter
    "hook_intro": True,               # first line(s) rewritten as a curiosity hook (+ own line in a silent intro)
    "translate_style_preset": "auto",  # auto (AI picks per video) | natural | bua | hype | story | cute | serious | custom
    "style_auto_lean": "bua",          # auto mode: bua = prefer the cheeky style for entertainment videos | neutral
    "translate_style":"Giọng kể tự nhiên, gần gũi như người Việt nói chuyện, câu ngắn gọn để đọc kịp thời lượng.",
    # --- voice ---
    "tts_provider": "edge",           # edge | elevenlabs | piper (local, offline)
    "voice_gender_mode": "auto",      # auto = detect speakers & gender | male | female (one voice for all)
    "edge_voice_male": "vi-VN-NamMinhNeural",
    "edge_voice_female": "vi-VN-HoaiMyNeural",
    "elevenlabs_voice_male": "pNInz6obpgDQGcFmaJgB",     # Adam (premade)
    "elevenlabs_voice_female": "21m00Tcm4TlvDq8ikWAM",   # Rachel (premade)
    "piper_voice_male": "vi_VN-vivos-x_low#57",
    "piper_voice_female": "vi_VN-vais1000-medium",
    "vieneu_voice_male": "Hải Đăng",
    "vieneu_voice_female": "Mai Anh",
    "vieneu_python": r"F:\VieNeu-TTS\env\Scripts\python.exe",   # the separate environment made by install_vieneu.ps1
    "tts_fallback_local": True,       # if Edge/ElevenLabs fail, make that line with Piper locally
    "edge_voice": "vi-VN-NamMinhNeural",
    "google_speed": 1.25,             # Google Dịch voice speed (its natural pace is slow)
    "edge_rate": "+10%",
    "edge_pitch": "+0Hz",
    "elevenlabs_api_key": "",
    "elevenlabs_voice_id": "",
    "elevenlabs_model": "eleven_flash_v2_5",
    "elevenlabs_stability": 0.5,
    "elevenlabs_similarity": 0.75,
    "elevenlabs_style": 0.0,
    "elevenlabs_speed": 1.0,
    "max_speedup": 1.45,
    # --- audio mix ---
    "separate_music": True,           # AI voice/background split on GPU: drop original voice, keep music/SFX
    "separate_model": "bs_roformer",  # bs_roformer (best) | melband (fast) | htdemucs_ft | htdemucs
    "bg_duck": True,                  # lower the background a little while the Vietnamese voice speaks
    "bg_volume": 0.85,
    "voice_volume": 1.35,
    "original_voice_volume": 0.0,     # >0 keeps a little of the original voice under the dub
    "narrator_remove": False,         # video has a narrator voice-over AND characters talking: remove the narrator only
    "character_volume": 1.0,          # level of the kept character voices (narrator_remove)
    "vo_original_volume": 0.5,        # voice-over mode: original voice level (ducked further under narration)
    "vo_music_volume": 1.0,           # voice-over mode: music/effects level
    # --- video ---
    "burn_subtitles": True,
    "screen_text": False,             # OCR titles/captions in the picture, translate, redraw in place
    "screen_text_interval": 0,        # seconds between OCR samples (0 = auto: 0.5s ≤2min, else 1s)
    "screen_text_min_height": 0.022,  # ignore text smaller than this fraction of the height (logos, boards)
    "screen_text_font": "auto",       # auto = match the original weight (Arial Black / Arial bold / Arial)
    "screen_text_clean": "inpaint",   # how the original text is removed: inpaint | blur (soft edges) | box
    # --- estimate & publishing ---
    "export_dir": "",                 # finished videos are also placed here with a Vietnamese name ("" = ./exports)
    "confirm_estimate": True,         # show time/cost estimate before a job starts
    "post_summary": True,             # write title, description, hashtags after render
    "tiktok_check": True,             # TikTok pre-publish quality check after render
    "tiktok_autofix": True,           # if something fixable is off, also make a 9:16 / -14 LUFS "TikTok version"
    "elevenlabs_usd_per_1k": 0.30,    # what 1000 ElevenLabs credits cost you beyond your plan
    "vnd_per_usd": 26000,
    "vnd_per_kwh": 2500,
    "pc_watts": 250,                  # average PC power while a job runs
    # --- watermarks ---
    "wm_remove": "auto",              # off | auto: find platform watermarks (抖音号, TikTok @…) and remove them
    "wm_clean": "blur",               # blur (fast, like subtitles) | box | lama | inpaint  (how a watermark is hidden)
    "brand_enabled": False,           # your own moving watermark on the result
    "brand_tiktok": "", "brand_youtube": "", "brand_facebook": "",
    "brand_motion": "bounce",         # bounce | corners | static
    "brand_show": "cycle",            # cycle (one platform at a time) | stack (all together)
    "brand_cycle_s": 5,
    "brand_opacity": 0.7,
    "brand_size": 0.03,               # text height as a fraction of the short side
    "brand_credit": False,            # small "Nguồn: <author>" credit in the corner
    "hide_original_subs": "auto",     # auto | off | manual
    "manual_sub_band": [0.86, 0.98],  # top/bottom as fraction of height (manual mode)
    "hide_style": "blur",             # blur | erase (inpaint) | lama | box — follows each subtitle line
    "sub_font": "Arial",
    "sub_font_scale": 0.045,          # font size as a fraction of the video's short side
    "sub_color": "#FFFFFF",
    "sub_outline_color": "#000000",
    "sub_style": "box",               # box = white rounded card over the blurred original subs · outline = white text, black outline
    "allow_lan": False,               # also let in the home Wi-Fi (192.168.x…); Tailscale + this PC are always allowed
    "sub_box_opacity": 0.92,
    "output_short_side": 1080,
    "work_proxy": True,
    "tiktok_client_key": "",           # your TikTok developer app (Login Kit + Content Posting API)
    "tiktok_client_secret": "",
    "tiktok_redirect_uri": "",         # "" = http://localhost:8765/api/tiktok/callback/ (Desktop); or an https tunnel URL
    "tiktok_post_mode": "inbox",       # inbox = into your TikTok inbox to finish in the app | direct = post (private until audited)
    "transform_grade": False,         # B6: light colour grade (contrast/saturation) — less identical to the source
    "transform_zoom": 1.0,            # B6: zoom the whole frame (1.04 = 4 %), applied over everything
    "outro_seconds": 0,               # B6: ending card length (0 = off): blurred last frame + "Theo dõi @…"
    "outro_text": "",                 # second line of the ending card ("" = default)               # 4K sources: process a working copy at the output size (much faster)
    "video_encoder": "h264_nvenc",
    "preview_seconds": 30,
    "wait_for_preview": False,        # pause after preview until you approve
    # --- download ---
    "cookies_file": "",               # optional Netscape cookies.txt for yt-dlp
    "douyin_headless": True,
    "douyin_gap_s": 30,
    "library_parallel": 3,            # video library: downloads running at the same time
    "library_douyin_gap_s": 8,        # library: seconds between two Douyin lookups (the transfers run in parallel)
    "facebook_gap_s": 20,             # minimum seconds between two Facebook downloads
    "crawl_gap_s": 5,                 # pause between two feeds when crawling lists
}

_lock = threading.Lock()


def load() -> dict:
    s = dict(DEFAULTS)
    from .autopilot import DEFAULTS as _AP       # 🤖 autopilot keys (defined next to the feature)
    for k, v in _AP.items():
        s.setdefault(k, v)
    stored = {}
    if SETTINGS_FILE.exists():
        try:
            stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            s.update(stored)
        except Exception:
            pass
    # providers added in a later version join the end of a saved fallback chain (ones the user
    # deliberately unticked are in translate_providers_seen and stay out)
    if "translate_fallbacks" in stored:
        seen = set(stored.get("translate_providers_seen") or []) | set(stored["translate_fallbacks"])
        if not stored.get("translate_providers_seen"):
            seen |= {"claude_cli", "anthropic", "openai", "gemini", "ollama", "google_free", "google"}
        s["translate_fallbacks"] = list(stored["translate_fallbacks"]) + \
            [p for p in DEFAULTS["translate_fallbacks"] if p not in seen]
    return s


def save(new: dict) -> dict:
    with _lock:
        cur = load()
        for k, v in new.items():
            if k not in DEFAULTS:
                continue
            if k in SECRET_KEYS and isinstance(v, str) and v.startswith("••"):
                continue  # masked value sent back unchanged
            cur[k] = v
        SETTINGS_FILE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        return cur


def masked(s: dict) -> dict:
    out = dict(s)
    for k in SECRET_KEYS:
        v = out.get(k) or ""
        out[k] = ("••••" + v[-4:]) if v else ""
    return out
