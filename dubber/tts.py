"""Text-to-speech (Edge TTS / Google Dịch / ElevenLabs / Piper local / VieNeu local GPU), per-speaker voices, fallback, timing fit, mixing."""
import asyncio
import hashlib
import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

from . import config, util

logging.getLogger("piper").setLevel(logging.ERROR)

# ElevenLabs premade voices (multilingual; usable even if your key cannot list voices)
ELEVEN_PRESETS = [
    {"id": "pNInz6obpgDQGcFmaJgB", "name": "Adam", "gender": "male", "info": "trầm, kể chuyện"},
    {"id": "JBFqnCBsd6RMkjVDRZzb", "name": "George", "gender": "male", "info": "ấm, trung niên"},
    {"id": "nPczCjzI2devNBz1zQrb", "name": "Brian", "gender": "male", "info": "sâu, truyền cảm"},
    {"id": "onwK4e9ZLuTAKqWW03F9", "name": "Daniel", "gender": "male", "info": "phát thanh viên"},
    {"id": "TX3LPaxmHKxFdv7VOQHJ", "name": "Liam", "gender": "male", "info": "trẻ"},
    {"id": "iP95p4xoKVk53GoZ742B", "name": "Chris", "gender": "male", "info": "tự nhiên"},
    {"id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel", "gender": "female", "info": "rõ ràng, kể chuyện"},
    {"id": "EXAVITQu4vr4xnZzMB6F", "name": "Sarah", "gender": "female", "info": "mềm mại"},
    {"id": "XrExE9yKIg1WjnnlVkGX", "name": "Matilda", "gender": "female", "info": "ấm áp"},
    {"id": "pFZP5JQG7iQjIQuC4Bku", "name": "Lily", "gender": "female", "info": "trong trẻo"},
    {"id": "cgSgspJ2msm6clMCkdW9", "name": "Jessica", "gender": "female", "info": "trẻ, vui"},
    {"id": "XB0fDUnXU5powFXDhCwa", "name": "Charlotte", "gender": "female", "info": "nhẹ nhàng"},
]

PIPER_DIR = config.DATA / "piper_voices"
PIPER_PRESETS = [
    {"id": "vi_VN-vais1000-medium", "name": "VAIS1000 (nữ, chất lượng vừa)", "gender": "female"},
    {"id": "vi_VN-25hours_single-low", "name": "25hours (nữ)", "gender": "female"},
    {"id": "vi_VN-vivos-x_low#57", "name": "VIVOS #57 (nam, trầm)", "gender": "male"},
    {"id": "vi_VN-vivos-x_low#56", "name": "VIVOS #56 (nam)", "gender": "male"},
    {"id": "vi_VN-vivos-x_low#29", "name": "VIVOS #29 (nam)", "gender": "male"},
]


def voice_for(s, gender, provider=None):
    """Voice id for a line's gender ('male' / 'female')."""
    provider = provider or s["tts_provider"]
    mode = s.get("voice_gender_mode", "auto")
    g = gender if mode == "auto" else mode
    g = g if g in ("male", "female") else "male"
    if provider == "google":
        return "google-vi"            # Google Dịch has one Vietnamese voice ("chị Google")
    return s[{"edge": f"edge_voice_{g}", "elevenlabs": f"elevenlabs_voice_{g}", "piper": f"piper_voice_{g}",
              "vieneu": f"vieneu_voice_{g}"}[provider]]


# ------------------------------------------------------------- Google Dịch ("chị Google")
GOOGLE_VOICES = [{"id": "google-vi", "name": "Chị Google (Google Dịch)", "gender": "female"}]


def google_tts(text, out: Path, s, log=None):
    """The Google Translate voice (unofficial endpoint via gTTS, free). Sped up with atempo (its natural pace
    is slow); retried when Google rate-limits."""
    import subprocess
    from gtts import gTTS
    speed = float(s.get("google_speed") or 1.25)
    mp3 = out.with_suffix(".mp3")
    last = None
    for attempt in range(5):
        try:
            gTTS(text, lang="vi", tld="com.vn", timeout=20).save(str(mp3))
            if mp3.exists() and mp3.stat().st_size > 500:
                break
            last = "file rỗng"
        except Exception as e:
            last = e
        wait = 2 * (attempt + 1)
        if log and attempt >= 1:
            log(f"Google Dịch chậm/lỗi ({str(last)[:80]}), thử lại sau {wait}s…", "warn")
        time.sleep(wait)
    else:
        raise RuntimeError(f"Google Dịch không tạo được giọng cho câu '{text[:40]}': {last}")
    tmp = out.with_suffix(".tmp.wav")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-af",
                        f"atempo={max(0.5, min(speed, 2.0)):.3f}", "-ar", "24000", "-ac", "1", str(tmp)],
                       creationflags=util.CREATE_NO_WINDOW)
    mp3.unlink(missing_ok=True)
    if r.returncode != 0 or not tmp.exists():
        raise RuntimeError("Không chuyển được âm thanh Google Dịch")
    tmp.replace(out)


# ------------------------------------------------------------- Edge
async def _edge_one(text, out: Path, s, voice, log=None):
    import edge_tts
    last = None
    tmp = out.with_suffix(".part")
    for attempt in range(6):
        try:
            comm = edge_tts.Communicate(text, voice, rate=s["edge_rate"], pitch=s["edge_pitch"], receive_timeout=30)
            await asyncio.wait_for(comm.save(str(tmp)), timeout=60)
            if tmp.exists() and tmp.stat().st_size > 1000:
                tmp.replace(out)
                return
            last = "file rỗng"
        except Exception as e:
            last = e
        wait = min(2 * (attempt + 1), 10)
        if log and attempt >= 1:
            log(f"Edge TTS chậm/lỗi ({last}), thử lại lần {attempt + 2} sau {wait}s…", "warn")
        await asyncio.sleep(wait)
    raise RuntimeError(f"Edge TTS lỗi cho câu '{text[:40]}': {last}")


async def edge_voices(lang=None):
    import edge_tts
    vs = await edge_tts.list_voices()
    out = [{"id": v["ShortName"], "name": f"{v['ShortName']} ({v['Gender']})", "locale": v["Locale"],
            "gender": v["Gender"].lower()} for v in vs]
    if lang:
        out = [v for v in out if v["locale"].lower().startswith(lang.lower())] or out
    return out


# ------------------------------------------------------------- ElevenLabs
def eleven_tts(text, out: Path, s, voice, prev_text=None, next_text=None):
    if not s.get("elevenlabs_api_key"):
        raise RuntimeError("Chưa nhập ElevenLabs API key trong Cài đặt")
    if not voice:
        raise RuntimeError("Chưa chọn giọng ElevenLabs")
    body = {"text": text, "model_id": s["elevenlabs_model"],
            "voice_settings": {"stability": float(s["elevenlabs_stability"]),
                               "similarity_boost": float(s["elevenlabs_similarity"]),
                               "style": float(s["elevenlabs_style"]),
                               "speed": float(s["elevenlabs_speed"])}}
    if s["elevenlabs_model"] in ("eleven_flash_v2_5", "eleven_turbo_v2_5"):
        body["language_code"] = s["target_lang"]
    if s["elevenlabs_model"] != "eleven_v3":
        if prev_text:
            body["previous_text"] = prev_text
        if next_text:
            body["next_text"] = next_text
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"xi-api-key": s["elevenlabs_api_key"],
                                          "Content-Type": "application/json", "Accept": "audio/mpeg"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                out.write_bytes(r.read())
            return
        except urllib.error.HTTPError as e:
            msg = eleven_error(e)
            # 409: a Voice Library voice is being added to the account by another request at the same moment
            if e.code in (409, 429, 500, 502, 503) and attempt < 4:
                time.sleep(2 + 2 * attempt)
                continue
            raise RuntimeError(f"ElevenLabs HTTP {e.code}: {msg}")
        except OSError as e:
            if attempt < 4:
                time.sleep(2 + 2 * attempt)
                continue
            raise RuntimeError(f"ElevenLabs lỗi mạng: {e}")


def eleven_error(e):
    raw = e.read().decode("utf-8", "ignore")
    try:
        d = json.loads(raw)["detail"]
        if d.get("status") == "missing_permissions":
            return (f"API key thiếu quyền ({d.get('message')}). Vào elevenlabs.io → API Keys → sửa key, "
                    "bật quyền Text to Speech (và Voices: Read nếu muốn tải danh sách giọng).")
        if d.get("status") in ("quota_exceeded",):
            return "Hết credit ElevenLabs."
        return d.get("message") or raw[:300]
    except Exception:
        return raw[:300]


def eleven_voices(api_key):
    """Your voice library, or the built-in preset list if the key can't read voices."""
    req = urllib.request.Request("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        msg = eleven_error(e)
        return {"voices": ELEVEN_PRESETS, "warning": f"Không đọc được danh sách giọng của bạn: {msg} "
                                                      "→ đang hiện danh sách giọng mặc định của ElevenLabs."}
    voices = []
    for v in data.get("voices", []):
        labels = v.get("labels") or {}
        voices.append({"id": v["voice_id"], "name": v["name"], "gender": (labels.get("gender") or "").lower(),
                       "info": ", ".join(str(x) for k, x in labels.items() if k != "gender")})
    return {"voices": voices or ELEVEN_PRESETS}


# ------------------------------------------------------------- Piper (local, offline)
_piper = {}
_piper_lock = threading.Lock()


def piper_tts(text, out: Path, voice_id):
    from piper import PiperVoice, SynthesisConfig
    name, _, spk = voice_id.partition("#")
    path = PIPER_DIR / f"{name}.onnx"
    if not path.exists():
        download_piper(name)
    with _piper_lock:
        if name not in _piper:
            _piper[name] = PiperVoice.load(str(path))
        v = _piper[name]
        tmp = out.with_suffix(".part")
        with wave.open(str(tmp), "wb") as wf:
            v.synthesize_wav(text, wf, syn_config=SynthesisConfig(speaker_id=int(spk) if spk else None))
    tmp.replace(out)


def download_piper(name):
    import subprocess
    PIPER_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run([util.PY, "-m", "piper.download_voices", "--download-dir", str(PIPER_DIR), name],
                   check=True, creationflags=util.CREATE_NO_WINDOW)


# ------------------------------------------------------------- single clip + cache
def _cache_name(text, s, provider, voice):
    if provider == "elevenlabs":
        key = ["el", voice, s["elevenlabs_model"], s["elevenlabs_stability"], s["elevenlabs_similarity"],
               s["elevenlabs_style"], s["elevenlabs_speed"], text]
    elif provider == "piper":
        key = ["piper", voice, text]
    elif provider == "vieneu":
        key = ["vieneu", voice, text]
    elif provider == "google":
        key = ["google", float(s.get("google_speed") or 1.25), text]
    else:
        key = ["edge", voice, s["edge_rate"], s["edge_pitch"], text]
    return hashlib.sha1(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16] + ".audio"


def synth_one(text, out: Path, s, gender="male"):
    """Single clip (voice test button)."""
    p = s["tts_provider"]
    v = voice_for(s, gender, p)
    if p == "elevenlabs":
        eleven_tts(text, out, s, v)
    elif p == "piper":
        piper_tts(text, out, v)
    elif p == "vieneu":
        from . import vieneu_tts
        vieneu_tts.synth_one(text, out, v)
    elif p == "google":
        google_tts(text, out, s)
    else:
        asyncio.run(_edge_one(text, out, s, v))


# ------------------------------------------------------------- full pipeline step
END_TAIL = 0.6        # seconds of breathing room after the last word
MAX_EXTEND = 6.0      # the video may be held on its last frame at most this long to finish the last line
MIX_GROUP = 150      # clips per ffmpeg call when mixing the voice (Windows command line ≤ 32 767 chars)


def generate(job, lines, total, s, workdir: Path):
    tdir = workdir / "tts"
    cache = tdir / "cache"          # clips cached by (provider + voice + settings + text): reruns only redo changes
    cache.mkdir(parents=True, exist_ok=True)
    for f in tdir.glob("*.wav"):
        f.unlink()
    n = len(lines)
    provider = s["tts_provider"]
    voices = [voice_for(s, l.get("gender", "male"), provider) for l in lines]
    clips = [cache / _cache_name(l["tgt"], s, provider, v) for l, v in zip(lines, voices)]
    orig = list(clips)
    first = {}                       # the same sentence ("Xông lên!" ×10) is made ONCE: parallel workers writing
    for i, c in enumerate(clips):    # the same cache file used to crash with WinError 32
        first.setdefault(c, i)
    todo = [i for i in range(n) if first[clips[i]] == i and not clips[i].exists()]
    if len(todo) < n:
        job.log(f"Dùng lại {n - len(todo)} câu đã tạo trước đó / lặp lại, tạo mới {len(todo)} câu")
    done = [n - len(todo)]
    fallback = []
    lock = threading.Lock()

    def bump():
        with lock:
            done[0] += 1
            job.step("tts", progress=0.7 * done[0] / n, detail=f"{done[0]}/{n} câu")

    def local_fallback(i, err):
        """The online service failed for this line: make it with Piper (offline) instead."""
        if not s.get("tts_fallback_local", True) or provider == "piper":
            raise err
        pv = voice_for(s, lines[i].get("gender", "male"), "piper")
        with lock:
            fallback.append(i)
            if len(fallback) == 1:
                job.log(f"{provider} lỗi ({err}) → dùng giọng local Piper cho các câu bị lỗi", "warn")
        piper_tts(lines[i]["tgt"], clips[i].with_name("piper_" + clips[i].name), pv)
        # don't cache a fallback under the online voice's name: next run will retry online
        clips[i] = clips[i].with_name("piper_" + clips[i].name)

    if provider == "google":
        from concurrent.futures import ThreadPoolExecutor

        def gone(i):
            job.check_cancel()
            try:
                google_tts(lines[i]["tgt"], clips[i], s, job.log)
            except RuntimeError as e:
                local_fallback(i, e)
            bump()
        with ThreadPoolExecutor(3) as ex:          # gentle: Google rate-limits bursts
            list(ex.map(gone, todo))
    elif provider == "elevenlabs":
        from concurrent.futures import ThreadPoolExecutor

        def one(i):
            job.check_cancel()
            try:
                eleven_tts(lines[i]["tgt"], clips[i], s, voices[i],
                           lines[i - 1]["tgt"] if i else None, lines[i + 1]["tgt"] if i + 1 < n else None)
            except RuntimeError as e:
                local_fallback(i, e)
            bump()
        # the first line of each voice goes alone: a Voice Library voice is added to the account on its first use,
        # and 3 parallel first uses made ElevenLabs answer 409 ("multiple voice additions … at the same time")
        firsts, seen_v = [], set()
        for i in todo:
            if voices[i] not in seen_v:
                seen_v.add(voices[i])
                firsts.append(i)
        for i in firsts:
            one(i)
        with ThreadPoolExecutor(3) as ex:
            list(ex.map(one, [i for i in todo if i not in firsts]))
    elif provider == "piper":
        for i in todo:
            job.check_cancel()
            piper_tts(lines[i]["tgt"], clips[i], voices[i])
            bump()
    elif provider == "vieneu":
        from . import vieneu_tts
        job.step("tts", detail="Khởi động VieNeu (lần đầu tải model ~1 GB)…")
        step = vieneu_tts.BATCH
        for k in range(0, len(todo), step):
            chunk = todo[k:k + step]
            try:
                vieneu_tts.synth([(lines[i]["tgt"], voices[i], clips[i]) for i in chunk], cancel=job.check_cancel)
                for _ in chunk:
                    bump()
            except RuntimeError as e:
                for i in chunk:
                    if not clips[i].exists():
                        local_fallback(i, e)
                    bump()
    else:
        async def run_all():
            sem = asyncio.Semaphore(2)

            async def one(i):
                async with sem:
                    job.check_cancel()
                    try:
                        await _edge_one(lines[i]["tgt"], clips[i], s, voices[i], job.log)
                    except RuntimeError as e:
                        await asyncio.to_thread(local_fallback, i, e)
                    bump()
            res = await asyncio.gather(*(one(i) for i in todo), return_exceptions=True)
            errs = [r for r in res if isinstance(r, BaseException)]
            if errs:
                raise errs[0]
        asyncio.run(run_all())
    if fallback:
        job.log(f"{len(fallback)} câu dùng giọng local Piper. Bấm 'Chạy lại từ: Tạo giọng đọc' để thử lại dịch vụ online.", "warn")
    for i in range(n):               # repeated sentences use the clip made for their first occurrence
        if i != first[orig[i]]:
            clips[i] = clips[first[orig[i]]]

    # trim silence + measure. The trimmed clip is cached beside the clip (same text + voice → same file), so a rerun
    # that changed one line trims one clip instead of all of them; new clips are trimmed 6 at a time and the length
    # is read from the WAV header (this used to be 2 processes per line on every run: ~4 min for 800 lines).
    wavs = [trimmed_path(c) for c in clips]
    need = sorted({k for k, w in enumerate(wavs) if not w.exists() or w.stat().st_mtime < clips[k].stat().st_mtime},
                  key=lambda k: str(wavs[k]))
    need = [k for n_, k in enumerate(need) if n_ == 0 or wavs[k] != wavs[need[n_ - 1]]]      # one job per file
    if need:
        from concurrent.futures import ThreadPoolExecutor

        def trim(k):
            job.check_cancel()
            trim_clip(clips[k], wavs[k])
        with ThreadPoolExecutor(6) as ex:
            list(ex.map(trim, need))
    for i, l in enumerate(lines):
        l["tdur"] = wav_seconds(wavs[i])
    job.step("tts", progress=0.8, detail="Căn thời gian")

    # schedule: start at the source phrase start (or right after the previous clip), speed up if needed
    gap, cursor, maxsp = 0.08, 0.0, float(s["max_speedup"])
    for i, l in enumerate(lines):
        nxt = lines[i + 1]["start"] if i + 1 < n else total
        start = max(l["start"], cursor + gap)
        window = max(nxt - start, 0.3)
        speed = min(max(l["tdur"] / (window + 0.35), 1.0), maxsp)
        l["speed"], l["vstart"] = round(speed, 4), round(start, 3)
        l["need"] = round(l["tdur"] / (window + 0.35), 3)        # uncapped: how fast it would have to be read
        l["vend"] = round(start + l["tdur"] / speed, 3)
        cursor = l["vend"]
    fast = sum(1 for l in lines if l["speed"] > 1.25)
    lag = max((l["vstart"] - l["start"] for l in lines), default=0)
    job.log(f"Giọng đọc: {n} câu, {fast} câu phải đọc nhanh >1.25x, lệch tối đa {lag:.2f}s so với lời gốc")
    # the last line is never cut: the voice may run past the video (the render then holds the last frame)
    vlen = max(total, min(cursor + END_TAIL, total + MAX_EXTEND))
    if cursor + END_TAIL > total:
        job.log(f"Câu cuối kết thúc ở {cursor:.1f}s, video dài {total:.1f}s → giữ khung hình cuối thêm "
                f"{vlen - total:.1f}s để đọc hết câu")
    if cursor > total + MAX_EXTEND:
        job.log(f"Cảnh báo: giọng đọc dài hơn video {cursor - total:.1f}s (quá {MAX_EXTEND:.0f}s), đoạn cuối sẽ bị cắt",
                "warn")

    mix_voice(job, lines, tdir, workdir, vlen, wavs)
    write_srt(lines, workdir / "subs.srt")
    job.step("tts", progress=1.0)
    return lines


TRIM_AF = ("silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
           "silenceremove=start_periods=1:start_threshold=-45dB,areverse")


def trimmed_path(clip: Path) -> Path:
    return clip.with_name(clip.stem + ".trim.wav")


def trim_clip(clip: Path, out: Path):
    """Leading / trailing silence removed, 44.1 kHz mono. Written to a temp name first: an interrupted run never
    leaves a half-written file in the cache."""
    tmp = out.with_name(out.stem + f".{os.getpid()}.{threading.get_ident()}.tmp.wav")
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-af", TRIM_AF, "-ar", "44100", "-ac", "1",
                        str(tmp)], check=True, capture_output=True, creationflags=util.CREATE_NO_WINDOW)
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            tmp.unlink()


def wav_seconds(path: Path) -> float:
    import wave
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (wave.Error, EOFError):
        return util.duration(path)          # not plain PCM: ask ffprobe


def mix_voice(job, lines, tdir, workdir, vlen, wavs=None):
    n = len(lines)
    wavs = wavs or [tdir / f"{i:04d}.wav" for i in range(n)]
    # mix: atempo per clip + adelay, then sum. Every clip is an ffmpeg input, and Windows limits a command line to
    # 32 767 characters — a long video (832 lines) failed with WinError 206. Mix in groups of MIX_GROUP clips into
    # part files, then sum the parts (amix with normalize=0 is a plain sum, so the result is the same).
    groups = [list(range(k, min(k + MIX_GROUP, n))) for k in range(0, n, MIX_GROUP)]
    parts = []
    for g, idx in enumerate(groups):
        inputs, filt = [], []
        for j, i in enumerate(idx):
            l = lines[i]
            inputs += ["-i", wavs[i]]
            ms = int(l["vstart"] * 1000)
            filt.append(f"[{j}:a]atempo={l['speed']:.4f},adelay={ms}|{ms}[d{j}]")
        mix = "".join(f"[d{j}]" for j in range(len(idx)))
        script = ";".join(filt) + f";{mix}amix=inputs={len(idx)}:normalize=0,apad=whole_dur={vlen:.3f}[out]"
        out = workdir / "voice.wav" if len(groups) == 1 else tdir / f"mixpart{g:02d}.wav"
        (tdir / "mix.txt").write_text(script, encoding="utf-8")
        util.run(job, ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex_script", tdir / "mix.txt",
                       "-map", "[out]", "-t", f"{vlen:.3f}", "-ar", "44100", out])
        parts.append(out)
    if len(parts) > 1:
        ins = [a for p in parts for a in ("-i", p)]
        mix = "".join(f"[{k}:a]" for k in range(len(parts)))
        util.run(job, ["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex",
                       f"{mix}amix=inputs={len(parts)}:normalize=0[out]", "-map", "[out]", "-t", f"{vlen:.3f}",
                       "-ar", "44100", workdir / "voice.wav"])
        for p in parts:
            p.unlink(missing_ok=True)


def _ts(t, sep=","):
    t = max(t, 0)
    h, r = divmod(t, 3600)
    m, sec = divmod(r, 60)
    ms = min(int(round((sec - int(sec)) * 1000)), 999)
    return f"{int(h):02d}:{int(m):02d}:{int(sec):02d}{sep}{ms:03d}"


def sub_times(lines):
    res = []
    for i, l in enumerate(lines):
        end = l["vend"] + 0.2
        if i + 1 < len(lines):
            end = min(end, lines[i + 1]["vstart"])
        res.append((l["vstart"], max(end, l["vstart"] + 0.4)))
    return res


def write_srt(lines, path: Path):
    with open(path, "w", encoding="utf-8") as f:
        from .render import sub_cues
        for i, (a, b, text) in enumerate(sub_cues(lines, "dub")):
            f.write(f"{i+1}\n{_ts(a)} --> {_ts(b)}\n{text}\n\n")
