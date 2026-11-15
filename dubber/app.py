"""FastAPI server: REST + Server-Sent Events + static UI."""
import asyncio
import json
import re
import shutil
import threading
import tempfile
import time
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from . import config, tts
from .jobs import STEP_IDS, Job, get_manager

app = FastAPI(title="Video Dubber")


@app.middleware("http")
async def only_me(request: Request, call_next):
    """The server listens on all network cards for Tailscale; refuse anyone who is not this PC or a Tailscale device."""
    from . import access
    host = request.client.host if request.client else None
    if not access.allowed(host) and not access.allowed(host, access.lan_enabled()):
        return HTMLResponse("<h3>403 — Video Dubber chỉ mở cho máy này và các thiết bị trong Tailscale của bạn.</h3>"
                            "<p>Bật “Cho phép mạng nhà (Wi-Fi)” trong ⚙ Cài đặt → Server nếu muốn vào qua Wi-Fi.</p>",
                            status_code=403)
    return await call_next(request)
STATIC = Path(__file__).parent / "static"
FILE_WHITELIST = {"source.mp4", "source_view.mp4","preview.mp4", "output.mp4", "output_sub.mp4", "output_text.mp4", "output_vo.mp4", "output_tiktok.mp4", "voice.wav", "no_vocals.wav",
                  "subs.srt", "lines.json"}


def _job(jid) -> Job:
    j = get_manager().jobs.get(jid)
    if not j:
        raise HTTPException(404, "Không có job này")
    return j


@app.on_event("startup")
def _startup():
    get_manager()
    try:
        from . import library
        library.resume_pending(config.load())       # downloads interrupted by a restart start again
    except Exception:
        pass
    from . import autopilot
    autopilot.start()                               # 🤖 nightly picker (does nothing unless switched on)


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


# ------------------------------------------------------------------ settings & voices
@app.get("/api/settings")
def get_settings():
    return {"settings": config.masked(config.load()), "defaults": config.DEFAULTS,
            "claude_cli_found": bool(shutil.which(config.load()["claude_cli_path"]))}


@app.put("/api/settings")
def put_settings(data: dict = Body(...)):
    return config.masked(config.save(data))


@app.get("/api/voices")
async def voices(provider: str = "edge", lang: str = "vi"):
    s = config.load()
    try:
        if provider == "elevenlabs":
            if not s["elevenlabs_api_key"]:
                return {"voices": tts.ELEVEN_PRESETS, "warning": "Chưa nhập API key — đang hiện giọng mặc định."}
            return await asyncio.to_thread(tts.eleven_voices, s["elevenlabs_api_key"])
        if provider == "piper":
            return {"voices": tts.PIPER_PRESETS}
        if provider == "vieneu":
            from . import vieneu_tts
            if not vieneu_tts.installed():
                return {"voices": [], "warning": "Chưa cài VieNeu-TTS — chạy install_vieneu.ps1 (xem README, mục VieNeu-TTS) hoặc sửa đường dẫn trong ⚙ Cài đặt"}
            return {"voices": await asyncio.to_thread(vieneu_tts.voices)}
        if provider == "google":
            return {"voices": tts.GOOGLE_VOICES}
        return {"voices": await tts.edge_voices(lang)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/tts-test")
async def tts_test(data: dict = Body(...)):
    s = config.load()
    s.update({k: v for k, v in (data.get("options") or {}).items() if v not in (None, "")})
    text = data.get("text") or "Xin chào, đây là giọng đọc thử nghiệm cho video của bạn."
    out = Path(tempfile.mkdtemp()) / "test.audio"
    try:
        await asyncio.to_thread(tts.synth_one, text, out, s, data.get("gender") or "male")
    except Exception as e:
        raise HTTPException(500, str(e))
    mt = "audio/wav" if out.read_bytes()[:4] == b"RIFF" else "audio/mpeg"
    return FileResponse(out, media_type=mt)


# ------------------------------------------------------------------ translation providers
@app.get("/api/providers")
def providers_status():
    from .providers import orchestrator
    return {"providers": orchestrator.status(config.load()), "chain": orchestrator.chain(config.load())}


_MT_DL = {"running": False, "error": "", "model": ""}


@app.post("/api/providers/local_nllb/download")
def local_mt_download(data: dict = Body(default={})):
    """Download the built-in offline translation model in the background (~0.6–1.4 GB, once)."""
    import threading
    from .providers import local_mt
    name = data.get("model") or config.load().get("local_mt_model") or "nllb-1.3B"
    if name not in local_mt.MODELS:
        raise HTTPException(400, "Model không hợp lệ")
    if _MT_DL["running"]:
        return {"ok": True, "running": True}

    def run():
        _MT_DL.update(running=True, error="", model=name)
        try:
            local_mt.download(name)
        except Exception as e:
            _MT_DL["error"] = str(e).splitlines()[0][:300]
        finally:
            _MT_DL["running"] = False
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "running": True}


@app.get("/api/providers/local_nllb/download")
def local_mt_download_status():
    from .providers import local_mt
    name = config.load().get("local_mt_model") or "nllb-1.3B"
    return {**_MT_DL, "downloaded": local_mt.is_downloaded(name), "models": list(local_mt.MODELS)}


@app.post("/api/providers/{pid}/test")
def provider_test(pid: str, data: dict = Body(default={})):
    """Translate one short line with exactly this provider (no fallback) and report what happened."""
    from . import providers
    from .providers import orchestrator
    from .providers.base import ProviderError, redact
    p = providers.REGISTRY.get(pid)
    if not p:
        raise HTTPException(404, "Không có provider này")
    s = config.load()
    s.update({k: v for k, v in (data.get("options") or {}).items() if v not in (None, "") and not str(v).startswith("••")})
    keys = orchestrator.all_secret_keys()
    ok, why = p.configured(s)
    if ok:
        ok, why = p.probe(s)
    if not ok:
        return {"ok": False, "error": redact(why, s, keys)}
    t0 = time.time()
    try:
        if p.kind == "mt":
            out = p.translate_list(_QuietJob(), ["你好，世界！"], s["target_lang"], s)[0]
        else:
            raw = p.complete(_QuietJob(), "Dịch sang tiếng Việt. Trả về JSON {\"tgt\": \"...\"}.", "你好，世界！",
                             {"type": "object", "required": ["tgt"], "properties": {"tgt": {"type": "string"}}}, s)
            m = __import__("re").search(r"\{.*\}", raw, __import__("re").S)
            out = json.loads(m.group(0))["tgt"] if m else raw[:100]
        orchestrator.clear(pid)                    # it works again: clear any cooldown
        return {"ok": True, "result": out, "seconds": round(time.time() - t0, 1), "model": p.model_name(s)}
    except ProviderError as e:
        return {"ok": False, "error": redact(str(e), s, keys)[:400], "kind": e.kind}
    except Exception as e:
        return {"ok": False, "error": redact(f"{type(e).__name__}: {e}", s, keys)[:400]}


# ------------------------------------------------------------------ Codex CLI / Gemini CLI sign-in (official, your account)
_cli_login = {}


@app.get("/api/cli/{pid}/status")
def cli_status(pid: str):
    from . import providers
    from .providers import cli
    if pid not in cli.LOGIN:
        raise HTTPException(404)
    p = providers.REGISTRY[pid]
    proc = _cli_login.get(pid)
    st = {"installed": bool(p.cmd()), "logging_in": bool(proc and proc.poll() is None)}
    if st["installed"]:
        st["logged_in"] = p.logged_in() if pid == "codex_cli" else cli.gemini_logged_in()
    return st


@app.post("/api/cli/{pid}/login")
def cli_login(pid: str):
    """Open a console window running the vendor's own sign-in (it opens your browser)."""
    import subprocess
    from . import providers
    from .providers import cli
    if pid not in cli.LOGIN:
        raise HTTPException(404)
    cmd = providers.REGISTRY[pid].cmd()
    if not cmd:
        raise HTTPException(400, "Chưa cài CLI này")
    proc = _cli_login.get(pid)
    if proc and proc.poll() is None:
        return {"ok": True, "already_open": True}
    _cli_login[pid] = subprocess.Popen(cmd + cli.LOGIN[pid]["login"], creationflags=subprocess.CREATE_NEW_CONSOLE)
    return {"ok": True}


@app.post("/api/cli/{pid}/logout")
def cli_logout(pid: str):
    import subprocess
    from . import providers
    from .providers import cli
    conf = cli.LOGIN.get(pid)
    if not conf:
        raise HTTPException(404)
    if conf["logout"] is None:
        (Path.home() / ".gemini" / "oauth_creds.json").unlink(missing_ok=True)
        return {"ok": True}
    r = subprocess.run(providers.REGISTRY[pid].cmd() + conf["logout"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30, creationflags=0x08000000)
    return {"ok": r.returncode == 0}


# ------------------------------------------------------------------ Claude Code CLI login (Claude subscription)
_claude_login = {"proc": None}


def _claude_exe():
    from .translate import find_claude
    exe = find_claude(config.load()["claude_cli_path"])
    if not exe:
        raise HTTPException(400, "Chưa cài Claude Code CLI. Chạy: npm i -g @anthropic-ai/claude-code")
    return exe


@app.get("/api/claude/status")
def claude_status():
    import subprocess
    from .translate import find_claude
    exe = find_claude(config.load()["claude_cli_path"])
    p = _claude_login["proc"]
    logging_in = bool(p and p.poll() is None)
    if not exe:
        return {"installed": False, "logged_in": False, "logging_in": False}
    try:
        out = subprocess.run([exe, "auth", "status", "--json"], capture_output=True, text=True, timeout=30,
                             encoding="utf-8", errors="replace", creationflags=0x08000000)
        d = json.loads(out.stdout[out.stdout.find("{"):] or "{}")
    except Exception as e:
        return {"installed": True, "logged_in": False, "logging_in": logging_in, "error": str(e)}
    acct = d.get("account") if isinstance(d.get("account"), dict) else {}
    return {"installed": True, "logged_in": bool(d.get("loggedIn")), "method": d.get("authMethod"),
            "email": d.get("email") or acct.get("email") or acct.get("emailAddress"),
            "expires": d.get("loginExpiresAt"), "logging_in": logging_in}


@app.post("/api/claude/login")
def claude_login(data: dict = Body(default={})):
    """Open a console window running `claude auth login`; the CLI opens the browser for you to sign in."""
    import subprocess
    exe = _claude_exe()
    p = _claude_login["proc"]
    if p and p.poll() is None:
        return {"ok": True, "already_open": True}
    args = [exe, "auth", "login", "--console" if data.get("console") else "--claudeai"]
    _claude_login["proc"] = subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_CONSOLE)
    return {"ok": True}


@app.post("/api/claude/logout")
def claude_logout():
    import subprocess
    exe = _claude_exe()
    out = subprocess.run([exe, "auth", "logout"], capture_output=True, text=True, timeout=30,
                         encoding="utf-8", errors="replace", creationflags=0x08000000)
    return {"ok": out.returncode == 0, "message": (out.stdout or out.stderr).strip()[-300:]}


# ------------------------------------------------------------------ Facebook login (app's own Facebook profile)
@app.post("/api/facebook/login")
def facebook_login():
    import threading
    from .sources import facebook
    if facebook.login_state["open"]:
        return {"ok": True, "already_open": True}
    threading.Thread(target=facebook.open_login_window, daemon=True).start()
    return {"ok": True}


@app.get("/api/facebook/status")
def facebook_status():
    from .sources import facebook
    st = {"open": facebook.login_state["open"], "error": facebook.login_state.get("error", "")}
    if not st["open"]:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            try:
                st["logged_in"] = facebook.export_cookies(Path(td) / "c.txt")
            except Exception as e:
                st["logged_in"], st["error"] = None, str(e).splitlines()[0]
    return st


# ------------------------------------------------------------------ 📋 Đánh giá (review)
def _idle_job(jid):
    job = _job(jid)
    if job.status in ("running", "queued"):
        raise HTTPException(409, "Job đang chạy, đợi xong đã")
    return job


@app.get("/api/jobs/{jid}/review")
def review_get(jid: str):
    from . import review
    job = _job(jid)
    r = review.load(job.dir)
    return {**r, "cats": review.CATS, "areas": review.AREAS, "duration": review.video_length(job.dir),
            "bgm_enabled": bool(job.options.get("bgm_enabled")), "has_bgm": job.path(review.BGM).exists()}


@app.put("/api/jobs/{jid}/review/ratings")
def review_ratings(jid: str, data: dict = Body(...)):
    from . import review
    return review.set_ratings(_job(jid), data.get("ratings") or {}, data.get("comment") or "")


@app.post("/api/jobs/{jid}/review/notes")
def review_add(jid: str, data: dict = Body(...)):
    from . import review
    try:
        return review.add_note(_job(jid), data.get("kind", ""), data.get("text", ""), data.get("t"), data.get("area", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/jobs/{jid}/review/notes/{nid}")
def review_del(jid: str, nid: str):
    from . import review
    review.delete_note(_job(jid), nid)
    return {"ok": True}


@app.post("/api/jobs/{jid}/review/notes/{nid}/dismiss")
def review_dismiss(jid: str, nid: str):
    from . import review
    try:
        return review.update_note(_job(jid), nid, status="dismissed")
    except KeyError:
        raise HTTPException(404, "Không thấy ghi chú")


@app.post("/api/jobs/{jid}/review/notes/{nid}/approve")
def review_approve(jid: str, nid: str):
    """Tech note approved → Claude's queue (data/feedback/queue.json)."""
    from . import review
    try:
        return review.approve_tech(_job(jid), nid)
    except KeyError:
        raise HTTPException(404, "Không thấy ghi chú kỹ thuật")


@app.post("/api/jobs/{jid}/review/notes/{nid}/propose")
def review_propose(jid: str, nid: str):
    """Script note → the AI's rewrite of the lines it is about (shown old/new; nothing applied yet)."""
    from . import review
    job = _job(jid)
    try:
        return review.propose_script(job, job.settings(), nid)
    except KeyError:
        raise HTTPException(404, "Không thấy ghi chú kịch bản")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"AI không đề xuất được: {str(e).splitlines()[0][:200]}")


@app.post("/api/jobs/{jid}/review/notes/{nid}/apply")
def review_apply(jid: str, nid: str, data: dict = Body(default={})):
    from . import review
    job = _idle_job(jid)
    try:
        step = review.apply_script(job, nid, data.get("keep"))
    except KeyError:
        raise HTTPException(404, "Chưa có đề xuất để áp dụng")
    if data.get("rerun", True):
        if job.status == "awaiting_approval":
            job.resume_step = None
        get_manager().submit(job, step)
    return {"ok": True, "from_step": step}


@app.post("/api/jobs/{jid}/review/music/suggest")
def review_music_suggest(jid: str):
    from . import review
    job = _job(jid)
    try:
        return review.suggest_music(job, job.settings())
    except Exception as e:
        raise HTTPException(502, f"AI không gợi ý được: {str(e).splitlines()[0][:200]}")


@app.post("/api/jobs/{jid}/review/music")
async def review_music_upload(jid: str, file: UploadFile = File(...), volume: float = Form(1.0),
                              keep_original: float = Form(0.0), offset: float = Form(0.0), render: bool = Form(True)):
    """Your background music replaces the original one (bgm.wav), then the video is rendered again."""
    from . import review
    job = _idle_job(jid)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".webm", ".mp4"):
        raise HTTPException(400, "Cần file nhạc (mp3, wav, m4a, ogg, flac…)")
    dest = job.path("bgm_source" + ext)
    with open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    try:
        L = await asyncio.to_thread(review.prepare_bgm, job, dest, volume, keep_original, offset)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    job.options["bgm_enabled"] = True
    job.save()
    if render:
        get_manager().submit(job, "render" if job.files.get("output") or job.files.get("output_vo") else "preview")
    return {"ok": True, "length": L}


@app.post("/api/jobs/{jid}/review/music/off")
def review_music_off(jid: str, data: dict = Body(default={})):
    """Back to the original background."""
    job = _idle_job(jid)
    job.options["bgm_enabled"] = False
    job.save()
    if data.get("render", True):
        get_manager().submit(job, "render")
    return {"ok": True}


@app.get("/api/feedback/queue")
def feedback_queue():
    from . import review
    return review.queue_items()


@app.get("/feedback/frame/{nid}")
def feedback_frame(nid: str):
    from . import review
    p = review.FRAMES / f"{re.sub(r'[^0-9a-f]', '', nid)}.jpg"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")


# ------------------------------------------------------------------ 🔥 Link hot
@app.get("/api/hot")
def hot_list(platform: str = "tiktok", cat: str = "", q: str = "", sort: str = "views", limit: int = 10,
             max_dur: int = 0, min_views: int = 0):
    """Top videos. platform tiktok: cat = Explore category ('' = all found); douyin: cat = Trending category id."""
    from . import hot
    d = hot.load()
    if platform == "douyin":
        items = hot.douyin_items(cat)
        if sort == "views":
            sort = "likes"                       # Douyin's web lists carry likes, not views
    else:
        items = list(d["items"].values())
        if cat:
            items = [i for i in items if f"cat:{cat}" in (i.get("feeds") or [])]
    if q:
        items = hot.search_local(q, items)
    jobs = get_manager().jobs
    rows = hot.top(items, sort, max(0, min(limit, 500)), max_dur, min_views)
    for r in rows:
        j = jobs.get(r.get("job_id") or "")
        r["job_status"] = j.status if j else None
    return {"items": rows, "total": len(items), "state": hot.state, "categories": hot.TT_CATEGORIES,
            "suggest": d["suggest"].get(q, []) if q else [], "creative_center": hot.CREATIVE_CENTER}


@app.get("/api/hot/insights")
def hot_insights():
    from . import hot
    d = hot.load()
    tt = hot.top(list(d["items"].values()), "rising", 10)
    recent = list(d["suggest"].items())[-8:][::-1]
    words = list(dict.fromkeys(w for _, ws in recent for w in ws))[:30]
    return {"douyin_hot": hot.douyin_hot(), "tiktok_rising": tt, "suggest": words,
            "creative_center": hot.CREATIVE_CENTER}


@app.post("/api/hot/crawl")
def hot_crawl(data: dict = Body(...)):
    """{kind: 'cat'|'search', arg, pages} for TikTok. Douyin goes through /api/trending/crawl."""
    from . import hot
    try:
        hot.start(data.get("kind", ""), str(data.get("arg") or ""), int(data.get("pages") or 3))
    except (RuntimeError, ValueError) as e:
        raise HTTPException(409 if isinstance(e, RuntimeError) else 400, str(e))
    return {"ok": True}


@app.post("/api/hot/cancel")
def hot_cancel():
    from . import hot
    hot.state["cancel"] = True
    return {"ok": True}


# ------------------------------------------------------------------ TikTok (VN) login (app's own browser profile)
@app.post("/api/tiktokweb/login")
def tiktokweb_login():
    from .sources import tiktok_web
    if tiktok_web.login_state["open"]:
        return {"ok": True, "already_open": True}
    if tiktok_web.busy():
        raise HTTPException(409, "Trình duyệt TikTok của app đang được dùng, thử lại sau ít giây")
    threading.Thread(target=tiktok_web.open_login_window, daemon=True).start()
    return {"ok": True}


@app.get("/api/tiktokweb/status")
def tiktokweb_status():
    from .sources import tiktok_web
    return tiktok_web.status()


# ------------------------------------------------------------------ Douyin login (app's own browser profile)
@app.post("/api/douyin/login")
def douyin_login():
    import threading
    from . import downloader
    if downloader.login_state["open"]:
        return {"ok": True, "already_open": True}

    def run():
        try:
            downloader.open_login_window()
            downloader.login_state.pop("error", None)
        except Exception as e:
            downloader.login_state["error"] = str(e).splitlines()[0]
            print("douyin login window:", e)
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


@app.get("/api/douyin/status")
def douyin_status():
    from . import downloader
    try:
        st = downloader.login_status()
        if downloader.login_state.get("error"):
            st["error"] = "Không mở được cửa sổ: " + downloader.login_state["error"]
        return st
    except Exception as e:
        return {"open": False, "logged_in": None, "error": str(e)}


# ------------------------------------------------------------------ DTK MCP / REST (Evil0ctal/Douyin_TikTok_Download_API)
def _merge_dtk_opts(data: dict | None = None) -> dict:
    s = config.load()
    for k, v in ((data or {}).get("options") or (data or {})).items():
        if k in ("dtk_enabled", "dtk_url", "dtk_api_key", "dtk_timeout_s") and v is not None:
            if k == "dtk_api_key" and isinstance(v, str) and v.startswith("••"):
                continue
            s[k] = v
    return s


@app.get("/api/dtk/status")
def dtk_status():
    from . import dtk_client
    return dtk_client.status(config.load())


@app.post("/api/dtk/status")
def dtk_status_post(data: dict = Body(default={})):
    from . import dtk_client
    return dtk_client.status(_merge_dtk_opts(data))


@app.post("/api/dtk/test")
def dtk_test(data: dict = Body(default={})):
    from . import downloader, dtk_client
    s = _merge_dtk_opts(data)
    url = str(data.get("url") or "").strip()
    t0 = time.time()
    if not url:
        st = dtk_client.status(s)
        st["seconds"] = round(time.time() - t0, 2)
        return st
    try:
        aweme, plat = dtk_client.resolve_aweme(url, s)
        urls = downloader._play_urls(aweme)
        return {
            "ok": True,
            "platform": plat,
            "aweme_id": aweme.get("aweme_id"),
            "title": (aweme.get("desc") or aweme.get("preview_title") or "")[:120],
            "author": (aweme.get("author") or {}).get("nickname", ""),
            "streams": len(urls),
            "seconds": round(time.time() - t0, 2),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "seconds": round(time.time() - t0, 2)}


# ------------------------------------------------------------------ trending (crawl list only; you decide what to run)
@app.get("/api/trending")
def trending_list():
    from . import categories, trending
    items = list(trending.load().values())
    jobs = get_manager().jobs
    nodes = categories.flatten(categories.load())
    counts = {n["id"]: 0 for n in nodes}
    for it in items:
        j = jobs.get(it.get("job_id") or "")
        if j:
            it["job_status"] = j.status
        elif it.get("job_id"):
            it["job_status"] = "deleted"
        it["has_cover"] = (trending.COVERS / f"{trending.cover_name(it['id'])}.jpg").exists()
        it.setdefault("platform", "douyin")
        it["categories"] = categories.classify(it, nodes)
        if not it.get("hidden"):
            for c in it["categories"]:
                counts[c] += 1
    return {"items": items, "state": trending.state, "categories": nodes, "counts": counts,
            "depth": trending.load_crawl_state()}


@app.get("/api/categories")
def categories_get():
    from . import categories
    return {"categories": categories.load()}


@app.put("/api/categories")
def categories_put(data: dict = Body(...)):
    from . import categories
    try:
        categories.save(data.get("categories"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/sources")
def sources_list():
    from .sources import REGISTRY
    s = config.load()
    return [{"id": a.id, "label": a.label, "feeds": a.feeds(s), "login_hint": a.login_hint} for a in REGISTRY.values()]


@app.post("/api/trending/crawl")
def trending_crawl(data: dict = Body(default={})):
    """Crawl lists only. Body: {category: id} | {targets: [[source, feed], …]} | {source: "hot"|"featured"} (legacy)."""
    import threading
    from . import categories, trending
    if trending.state["running"]:
        raise HTTPException(409, "Đang crawl rồi")
    pages = max(1, min(int(data.get("pages") or 3), 10))
    if data.get("category"):
        targets = categories.crawl_targets(data["category"], categories.flatten(categories.load()))
        if not targets:
            raise HTTPException(400, "Danh mục này chưa khai báo nguồn (sources) để lấy video")
    elif data.get("targets"):
        targets = [tuple(t) for t in data["targets"]]
    else:
        targets = [("douyin", data.get("source") or "featured")]
    threading.Thread(target=trending.crawl, args=(targets, pages), kwargs={"more": bool(data.get("more"))},
                     daemon=True).start()
    return {"ok": True, "targets": targets, "more": bool(data.get("more"))}


@app.post("/api/trending/cancel")
def trending_cancel():
    from . import trending
    trending.cancel()
    return {"ok": True}


@app.post("/api/trending/queue")
def trending_queue(data: dict = Body(...)):
    from . import trending
    ids = data.get("ids") or []
    items = trending.load()
    created = []
    for vid in ids:
        it = items.get(vid)
        if not it:
            continue
        job = Job(it["url"], dict(data.get("options") or {}))
        job.title = it.get("title", "")[:120]
        get_manager().submit(job)
        it.update(status="queued", job_id=job.id)
        created.append(job.id)
    trending.save(items)
    return {"ok": True, "jobs": created}


@app.post("/api/trending/hide")
def trending_hide(data: dict = Body(...)):
    from . import trending
    items = trending.load()
    for vid in data.get("ids") or []:
        if vid in items:
            items[vid]["hidden"] = bool(data.get("hidden", True))
    trending.save(items)
    return {"ok": True}


@app.post("/api/trending/delete")
def trending_delete(data: dict = Body(...)):
    from . import trending
    trending.remove(data.get("ids") or [])
    return {"ok": True}


# ------------------------------------------------------------------ quick preview (stream via this server, no download)
_PREVIEW: dict[str, dict] = {}
_VID = __import__("re").compile(r"[A-Za-z0-9_:-]{3,80}")


class _QuietJob:
    proc = None

    def log(self, *a, **k): pass
    def step(self, *a, **k): pass
    def check_cancel(self): pass


@app.get("/api/preview/{vid}")
def preview_resolve(vid: str):
    from . import downloader
    if not _VID.fullmatch(vid):
        raise HTTPException(400, "ID không hợp lệ")
    from . import trending
    c = _PREVIEW.get(vid)
    if c and time.time() - c["t"] < 1200:
        return {"ok": True, "stream": f"/preview/stream/{vid}", "title": c["title"]}
    it = trending.load().get(vid) or {}
    url = it.get("url") or f"https://www.douyin.com/video/{vid}"
    if not downloader.is_douyin(url):          # Facebook & other yt-dlp sources
        try:
            stream, headers, title = downloader.stream_info(url, config.load())
        except Exception as e:
            raise HTTPException(502, f"Không lấy được link xem: {str(e).splitlines()[0][:300]}")
        _PREVIEW[vid] = {"urls": [stream], "cookie": "", "headers": headers, "t": time.time(), "title": title}
        if it and title and (it.get("generic_title") or not it.get("title")):
            trending.update(vid, title=title, generic_title=False)      # the list only had a placeholder title
        return {"ok": True, "stream": f"/preview/stream/{vid}", "title": title}
    try:
        aweme, cookies = downloader.resolve_aweme(_QuietJob(), url, config.load(), allow_visible=False, use_gap=False)
    except Exception as e:
        raise HTTPException(502, f"Không lấy được link xem: {str(e).splitlines()[0]}")
    if it:
        trending.fill_from_aweme(vid, aweme)
    urls = downloader.preview_urls(aweme)
    if not urls:
        raise HTTPException(404, "Video không có link phát (có thể là bài ảnh hoặc đã bị xoá)")
    cookie = "; ".join(f"{k['name']}={k['value']}" for k in cookies if "douyin" in k.get("domain", ""))
    _PREVIEW[vid] = {"urls": urls, "cookie": cookie, "t": time.time(), "title": aweme.get("desc", "")}
    return {"ok": True, "stream": f"/preview/stream/{vid}", "title": aweme.get("desc", "")}


@app.get("/preview/stream/{vid}")
def preview_stream(vid: str, request: Request):
    import urllib.error
    import urllib.request
    from fastapi.responses import Response
    from . import downloader
    c = _PREVIEW.get(vid)
    if not c:
        raise HTTPException(404, "Bấm Xem lại để lấy link")
    rng = request.headers.get("range") or "bytes=0-"
    last = None
    for u in list(c["urls"]):
        hdr = dict(c.get("headers") or {"User-Agent": downloader.UA, "Referer": "https://www.douyin.com/",
                                         "Cookie": c["cookie"]})
        hdr["Range"] = rng
        req = urllib.request.Request(u, headers=hdr)
        try:
            r = urllib.request.urlopen(req, timeout=30)
        except (urllib.error.URLError, OSError) as e:
            last = e
            c["urls"].remove(u)          # dead mirror: don't try it again
            continue
        headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
        for h in ("Content-Range", "Content-Length"):
            if r.headers.get(h):
                headers[h] = r.headers[h]

        def body():
            try:
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                r.close()
        return StreamingResponse(body(), status_code=r.status, media_type="video/mp4", headers=headers)
    _PREVIEW.pop(vid, None)
    return Response(f"Không phát được: {last}", status_code=502)


@app.get("/trending/cover/{vid}")
def trending_cover(vid: str):
    from . import trending
    p = trending.COVERS / f"{trending.cover_name(vid)}.jpg"
    if not _VID.fullmatch(vid) or not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


# ------------------------------------------------------------------ jobs
@app.post("/api/jobs")
def create_job(data: dict = Body(...)):
    url = (data.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "Thiếu link video")
    job = Job(url, data.get("options") or {})
    get_manager().submit(job)
    return {"id": job.id}


@app.post("/api/estimate")
def estimate_job(data: dict = Body(...)):
    """Time + cost estimate before running. Looks the video up (duration) without downloading it, unless
    durations are given (trending items already know theirs)."""
    from . import downloader, estimate
    s = {**config.load(), **(data.get("options") or {})}
    durs = [float(x) for x in data.get("durations") or [] if x]
    if durs:
        parts = [estimate.estimate(dd, s) for dd in durs]
        tot = dict(parts[0])
        tot.update(duration=round(sum(durs), 1), count=len(durs),
                   total_secs=sum(p["total_secs"] for p in parts), total_usd=round(sum(p["total_usd"] for p in parts), 4),
                   total_vnd=sum(p["total_vnd"] for p in parts), disk_mb=sum(p["disk_mb"] for p in parts),
                   steps=[{**st, "secs": sum(p["steps"][i]["secs"] for p in parts)} for i, st in enumerate(parts[0]["steps"])],
                   money=[{**m, "usd": (round(sum((p["money"][i]["usd"] or 0) for p in parts), 4) if m["usd"] is not None else None),
                           **({"vnd": sum(p["money"][i].get("vnd", 0) for p in parts)} if "vnd" in m else {})}
                          for i, m in enumerate(parts[0]["money"])])
        return tot
    url = (data.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "Thiếu link video")
    try:
        info = downloader.probe(url, s)
    except Exception as e:
        raise HTTPException(502, f"Không lấy được thông tin video để ước tính: {str(e).splitlines()[0][:200]}")
    if not info.get("duration"):
        raise HTTPException(502, "Không biết độ dài video")
    return estimate.estimate(info["duration"], s, info)


@app.get("/api/jobs/{jid}/exports")
def job_exports(jid: str):
    from . import export
    job = _job(jid)
    files = {k: v for k, v in export.load(job.dir).items() if Path(v).exists()}
    return {"files": files, "folder": str(export.folder({**config.load(), **job.options}))}


@app.post("/api/jobs/{jid}/open-folder")
def open_job_folder(jid: str, data: dict = Body(default={})):
    """Open Explorer on the exported video (or the export folder). Exports first if this job has none yet."""
    from . import export
    job = _job(jid)
    s = {**config.load(), **job.options}
    files = {k: v for k, v in export.load(job.dir).items() if Path(v).exists()}
    if not files and any(job.files.get(k) for k in export.LABELS):
        files = export.run(job, job.dir, s)
    target = files.get(data.get("key") or "") or next((v for k, v in files.items() if not k.endswith("_srt")), None)
    export.reveal(Path(target) if target else export.folder(s))
    return {"ok": True, "path": target or str(export.folder(s))}


@app.post("/api/exports/all")
def export_all_jobs():
    """Gather every finished video of every job into the final folder (Vietnamese file names)."""
    from . import export
    logs = []
    done = export.export_all(config.load(), log=logs.append)
    n = sum(1 for files in done.values() for k in files if not k.endswith("_srt"))
    return {"ok": True, "jobs": len(done), "videos": n, "folder": str(export.folder(config.load())), "log": logs[-5:]}


@app.post("/api/exports/open")
def open_exports():
    from . import export
    export.reveal(export.folder(config.load()))
    return {"ok": True}


@app.get("/api/jobs/{jid}/post")
def get_post(jid: str):
    from . import postgen
    post = postgen.load(_job(jid).dir)
    if not post:
        raise HTTPException(404, "Chưa có")
    return post


@app.post("/api/jobs/{jid}/post")
def regen_post(jid: str):
    """(Re)write title, description and hashtags now (takes ~10–60 s with an LLM)."""
    from . import postgen
    job = _job(jid)
    if job.status in ("running", "queued"):
        raise HTTPException(409, "Job đang chạy")
    s = {**config.load(), **job.options}
    try:
        post = postgen.generate(job, job.dir, s)
    except Exception as e:
        raise HTTPException(502, f"Không viết được: {str(e).splitlines()[0][:300]}")
    job.add_file("post", postgen.FILE)
    job.step("summary", status="done", progress=1.0, detail=post["title"][:80])
    try:
        from . import export
        export.run(job, job.dir, s)        # new title -> rename the exported files
    except Exception:
        pass
    return post


@app.get("/api/styles")
def list_styles():
    from . import styles
    return {"presets": styles.menu()}


@app.get("/api/jobs/{jid}/style")
def job_style(jid: str):
    from . import styles
    st = styles.load(_job(jid).dir)
    if not st:
        raise HTTPException(404, "Chưa chọn phong cách")
    return {k: v for k, v in st.items() if k != "key"}


@app.get("/api/jobs/{jid}/tiktok")
def get_tiktok_check(jid: str):
    from . import tiktokcheck
    rep = tiktokcheck.load(_job(jid).dir)
    if not rep:
        raise HTTPException(404, "Chưa kiểm tra")
    return rep


_tt_lock = threading.Lock()


@app.post("/api/jobs/{jid}/tiktok")
def run_tiktok_check(jid: str):
    """(Re)run the TikTok pre-publish check now (and make the TikTok version if something fixable is off)."""
    from . import export, tiktokcheck
    from .pipeline import OUTPUTS
    job = _job(jid)
    if job.status in ("running", "queued"):
        raise HTTPException(409, "Job đang chạy")
    s = {**config.load(), **job.options}
    mode = s.get("output_mode", "sub")
    name = OUTPUTS.get(mode, OUTPUTS["sub"])[0]
    if not job.path(name).exists():
        name = next((n for n, _ in OUTPUTS.values() if job.path(n).exists()), None)
        mode = next((m for m, (n, _) in OUTPUTS.items() if n == name), mode)
    if not name:
        raise HTTPException(404, "Chưa có video cuối")
    if not _tt_lock.acquire(blocking=False):
        raise HTTPException(409, "Đang kiểm tra một video khác")
    try:
        job.step("tiktok", status="running", progress=0.0, detail="Đo file, âm lượng, quét chữ…")
        rep = tiktokcheck.run(job, job.dir, s, name, mode)
        job.add_file("tiktok_check", tiktokcheck.FILE)
        job.step("tiktok", status="done", progress=1.0, detail=tiktokcheck.summary(rep))
    except Exception as e:
        job.step("tiktok", status="error", progress=1.0, detail="Lỗi — bấm ↻ Kiểm tra lại")
        raise HTTPException(500, f"Không kiểm tra được: {str(e).splitlines()[0][:300]}")
    finally:
        _tt_lock.release()
    job.save()
    try:
        export.run(job, job.dir, s)
    except Exception:
        pass
    return rep


# ---- restart to load new code (run.bat restarts the server when it exits with code 3) ----------------------------
def _code_hash():
    import hashlib
    h = hashlib.sha1()
    for f in sorted(Path(__file__).parent.glob("**/*.py")):
        try:
            h.update(f.read_bytes())
        except OSError:
            pass
    return h.hexdigest()


_BOOT_CODE = _code_hash()
_BOOT_TIME = time.time()


def _busy():
    """What would be interrupted by a restart (jobs, library downloads / recognition)."""
    from . import library
    busy = get_manager().busy()          # running, queued, or finishing the post-render steps
    lib = [i["id"] for i in library.load().values() if i["status"] in ("queued", "downloading") or i.get("analyzing")]
    return busy, lib


@app.get("/api/autopilot")
def autopilot_status():
    """Settings, today's state, recent log and a dry-run: what it WOULD pick now (nothing is queued)."""
    from . import autopilot
    s = config.load()
    st = autopilot.load_state()
    cands = autopilot.candidates(s, state=st)
    left = max(0, int(s.get("autopilot_max_per_day", 5)) - st["count"])
    return {"settings": {k: s.get(k) for k in autopilot.DEFAULTS}, "today": st["count"], "left": left,
            "in_window": autopilot.in_window(s), "would_pick": cands[:left] if left else [],
            "candidates": len(cands), "log": st["log"][-15:], "picked_total": len(st["picked"]),
            "categories": [{"id": n["id"], "name": n["name"], "depth": n.get("depth", 0)}
                           for n in __import__("dubber.categories", fromlist=["x"]).flatten(
                               __import__("dubber.categories", fromlist=["x"]).load())]}


@app.post("/api/autopilot/run")
def autopilot_run(data: dict = Body(default={})):
    """Queue now (outside the night window), up to today's remaining quota or `n`."""
    from . import autopilot
    s = config.load()
    st = autopilot.load_state()
    left = max(0, int(s.get("autopilot_max_per_day", 5)) - st["count"])
    n = min(int(data.get("n") or left), left) if left else 0
    if not n:
        raise HTTPException(409, "Đã đủ số video của hôm nay (tăng “Tối đa mỗi ngày” nếu muốn thêm)")
    done = autopilot.queue(s, n, reason="bấm Chạy ngay")
    if not done:
        raise HTTPException(404, "Không có video nào khớp bộ lọc")
    return {"ok": True, "jobs": [d["job"] for d in done]}


# ---- TikTok (official Content Posting API, your own developer app) ---------------------------------------------
@app.get("/api/tiktok/status")
def tiktok_status():
    from . import tiktok_api
    return tiktok_api.status(config.load())


@app.get("/api/tiktok/login")
def tiktok_login():
    """Opens TikTok's own sign-in / consent page (the app never sees your password)."""
    from fastapi.responses import RedirectResponse
    from . import tiktok_api
    try:
        return RedirectResponse(tiktok_api.login_url(config.load()))
    except tiktok_api.TikTokError as e:
        return HTMLResponse(f"<meta charset=utf-8><p style='font-family:sans-serif'>❌ {e}</p>", status_code=400)


@app.get("/api/tiktok/callback/")
@app.get("/api/tiktok/callback")
def tiktok_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    from . import tiktok_api
    page = "<meta charset=utf-8><body style='font-family:sans-serif;padding:30px'>{}<p>Bạn có thể đóng tab này.</p>"
    if error:
        return HTMLResponse(page.format(f"<h3>❌ TikTok từ chối: {error}</h3><p>{error_description}</p>"), status_code=400)
    try:
        data = tiktok_api.exchange_code(config.load(), code, state)
    except tiktok_api.TikTokError as e:
        return HTMLResponse(page.format(f"<h3>❌ Không đăng nhập được</h3><p>{e}</p>"), status_code=400)
    return HTMLResponse(page.format(f"<h3>✅ Đã kết nối TikTok{(': ' + data['display_name']) if data.get('display_name') else ''}</h3>"))


@app.post("/api/tiktok/logout")
def tiktok_logout():
    from . import tiktok_api
    tiktok_api.logout()
    return {"ok": True}


@app.get("/api/tiktok/creator")
def tiktok_creator():
    """Direct-post options for the connected account (privacy levels allowed, max duration…)."""
    from . import tiktok_api
    try:
        return tiktok_api.creator_info(config.load())
    except tiktok_api.TikTokError as e:
        raise HTTPException(400, str(e))


@app.post("/api/jobs/{jid}/tiktok/publish")
def tiktok_publish(jid: str, data: dict = Body(default={})):
    """Upload this job's video to TikTok (in the background; the result goes to the job log)."""
    from . import tiktok_api
    from .pipeline import OUTPUTS
    job = _job(jid)
    s = {**config.load(), **job.options}
    name = data.get("file") or ("output_tiktok.mp4" if job.path("output_tiktok.mp4").exists()
                                else OUTPUTS.get(s.get("output_mode"), OUTPUTS["sub"])[0])
    if not job.path(name).exists():
        raise HTTPException(404, "Job chưa có video để đăng")
    if not tiktok_api.load_token():
        raise HTTPException(400, "Chưa đăng nhập TikTok (⚙ Cài đặt → Đăng TikTok)")

    def run():
        try:
            job.log(f"TikTok: bắt đầu đăng {name} ({'vào hộp thư TikTok' if s.get('tiktok_post_mode', 'inbox') == 'inbox' else 'đăng trực tiếp'})…")
            r = tiktok_api.publish(s, job.path(name), caption=data.get("caption", ""), privacy=data.get("privacy"),
                                   allow_comment=data.get("allow_comment", True), allow_duet=data.get("allow_duet", True),
                                   allow_stitch=data.get("allow_stitch", True), aigc=data.get("aigc", True),
                                   log=job.log)
            msg = {"SEND_TO_USER_INBOX": "đã gửi vào hộp thư TikTok — mở app TikTok, bấm thông báo để hoàn tất đăng",
                   "PUBLISH_COMPLETE": f"đã đăng ({r.get('privacy')})"}.get(r["status"], f"TikTok đang xử lý ({r['status']})")
            job.log(f"TikTok: {msg} · publish_id {r['publish_id']}")
            (job.path("tiktok_publish.json")).write_text(json.dumps({**r, "t": time.time(), "file": name},
                                                                    ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            job.log(f"TikTok: đăng lỗi — {str(e).splitlines()[0][:300]}", "error")
    threading.Thread(target=run, daemon=True, name="tiktok-publish").start()
    return {"ok": True, "file": name, "mode": s.get("tiktok_post_mode", "inbox")}


@app.get("/api/admin/status")
def admin_status():
    import os
    jobs, lib = _busy()
    return {"stale": _code_hash() != _BOOT_CODE, "supervised": bool(os.environ.get("DUBBER_SUPERVISED")),
            "busy_jobs": len(jobs), "busy_library": len(lib), "started": _BOOT_TIME}


@app.post("/api/admin/restart")
def admin_restart():
    import os
    if not os.environ.get("DUBBER_SUPERVISED"):
        raise HTTPException(400, "Server này không được mở bằng run.bat mới nên không tự khởi động lại được. "
                                 "Đóng cửa sổ server rồi nhấp đúp run.bat một lần; từ đó về sau dùng được nút này.")
    jobs, lib = _busy()
    if jobs or lib:
        raise HTTPException(409, f"Đang bận: {len(jobs)} job, {len(lib)} video trong kho. Đợi xong rồi khởi động lại.")
    threading.Timer(0.6, lambda: os._exit(3)).start()       # after the HTTP answer has been sent
    return {"ok": True}


_sys_prev = {}


@app.get("/api/system")
def system_stats():
    """CPU / RAM / disk / GPU snapshot for the monitor popover (polled while it is open)."""
    import subprocess
    import psutil
    from . import util
    vm = psutil.virtual_memory()
    du = shutil.disk_usage(config.DATA)
    proc = psutil.Process()
    try:
        kids = proc.children(recursive=True)
    except Exception:
        kids = []
    app_rss = proc.memory_info().rss + sum(_safe_rss(k) for k in kids)
    out = {"cpu": psutil.cpu_percent(interval=None), "cpu_per_core": psutil.cpu_percent(interval=None, percpu=True),
           "cpu_count": psutil.cpu_count(), "cpu_freq": getattr(psutil.cpu_freq(), "current", None),
           "ram_used": vm.used, "ram_total": vm.total, "ram_pct": vm.percent,
           "disk_free": du.free, "disk_total": du.total, "app_rss": app_rss, "app_procs": 1 + len(kids),
           "gpu": None}
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,"
                            "power.draw,power.limit,fan.speed,clocks.gr,utilization.encoder",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                           creationflags=util.CREATE_NO_WINDOW)
        f = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
        num = lambda x: float(x) if x.replace(".", "", 1).isdigit() else None
        out["gpu"] = {"name": f[0], "util": num(f[1]), "mem_used": num(f[2]), "mem_total": num(f[3]), "temp": num(f[4]),
                      "power": num(f[5]), "power_limit": num(f[6]), "fan": num(f[7]), "clock": num(f[8]),
                      "enc": num(f[9]) if len(f) > 9 else None}
    except Exception:
        pass
    running = [j for j in get_manager().jobs.values() if j.status == "running"]
    if running:
        j = running[0]
        cur = next((sid for sid in STEP_IDS if j.steps[sid]["status"] == "running"), "")
        out["job"] = {"id": j.id, "title": j.title[:60], "step": cur,
                      "progress": j.steps[cur]["progress"] if cur else 0}
    return out


def _safe_rss(p):
    try:
        return p.memory_info().rss
    except Exception:
        return 0


@app.post("/api/jobs/upload")
async def upload_job(file: UploadFile = File(...), options: str = Form("{}")):
    """Import a video from your computer: stored in the job folder, then processed like a downloaded one."""
    ext = Path(file.filename or "video.mp4").suffix.lower()
    if ext not in (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".ts"):
        raise HTTPException(400, f"Định dạng {ext or '?'} chưa hỗ trợ (mp4, mov, mkv, webm, avi, m4v, flv, ts)")
    try:
        opts = json.loads(options or "{}")
    except ValueError:
        opts = {}
    name = Path(file.filename).name
    job = Job("upload:" + name, opts)
    job.title = Path(name).stem[:120]
    dest = job.path("upload" + ext)
    size = 0
    with open(dest, "wb") as f:
        while True:
            chunk = await file.read(4 << 20)
            if not chunk:
                break
            f.write(chunk)
            size += len(chunk)
    if size < 1000:
        shutil.rmtree(job.dir, ignore_errors=True)
        raise HTTPException(400, "File rỗng")
    job.log(f"Đã nhận file {name} ({size / 1e6:.0f} MB)")
    get_manager().submit(job)
    return {"id": job.id}


# ---------------------------------------------------------------- video library ("Kho video")
@app.get("/api/library")
def library_list():
    from . import library
    items = sorted(library.load().values(), key=lambda it: -it["created"])
    jobs = get_manager().jobs
    for it in items:
        it["job_status"] = [{"id": j, "status": jobs[j].status} for j in it.get("jobs", []) if j in jobs]
    counts = {}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    return {"items": items, "counts": counts}


@app.post("/api/library/add")
def library_add(data: dict = Body(...)):
    from . import library
    ids = library.add(data.get("text") or "", config.load())
    if not ids:
        raise HTTPException(400, "Không thấy link nào (cần link bắt đầu bằng http…)")
    return {"ids": ids}


@app.post("/api/library/{iid}/retry")
def library_retry(iid: str):
    from . import library
    library.retry(iid, config.load())
    return {"ok": True}


@app.post("/api/library/{iid}/cancel")
def library_cancel(iid: str):
    from . import library
    library.cancel(iid)
    return {"ok": True}


@app.post("/api/library/delete")
def library_delete(data: dict = Body(...)):
    from . import library
    for iid in data.get("ids") or []:
        library.remove(iid)
    return {"ok": True}


@app.post("/api/library/analyze")
def library_analyze(data: dict = Body(...)):
    """Recognise the selected stored videos and recommend an output mode for each (runs in the background)."""
    from . import library
    n = library.analyze_items(data.get("ids") or [], config.load())
    return {"ok": True, "queued": n}


@app.post("/api/library/process")
def library_process(data: dict = Body(...)):
    """Make jobs from stored videos (no new download)."""
    from . import library
    items = library.load()
    created = []
    for iid in data.get("ids") or []:
        it = items.get(iid)
        if not it or it["status"] != "done":
            continue
        job = Job("library:" + iid, dict(data.get("options") or {}))
        job.title = it.get("title", "")[:120]
        get_manager().submit(job)
        library.update(iid, jobs=(it.get("jobs") or []) + [job.id])
        created.append(job.id)
    if not created:
        raise HTTPException(400, "Chưa chọn video nào đã tải xong")
    return {"jobs": created}


@app.get("/library/{iid}/{name}")
def library_file(iid: str, name: str):
    from . import library
    if name not in ("thumb.jpg", "view.mp4", "source.mp4") or not re.fullmatch(r"[\w-]+", iid):
        raise HTTPException(404)
    p = library.DIR / iid / name
    if name == "view.mp4" and not p.exists():
        p = library.DIR / iid / "source.mp4"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg" if name.endswith(".jpg") else "video/mp4")


@app.get("/api/jobs")
def list_jobs():
    from . import export
    jobs = sorted(get_manager().jobs.values(), key=lambda j: -j.created)
    out = []
    for j in jobs:
        d = j.to_dict(logs_from=None)
        try:
            d["title_vi"] = export.vi_title(j)
        except OSError:
            d["title_vi"] = ""
        out.append(d)
    _titles_later(jobs)
    return out


_TITLES = {"busy": False, "next": 0.0}


def _titles_later(jobs):
    """Jobs whose title has no Vietnamese version yet get one in the background (one request for all of them),
    so the history list is searchable in Vietnamese. After a failure, wait 10 min before trying again."""
    from . import export
    if _TITLES["busy"] or time.time() < _TITLES["next"]:
        return
    need = [j for j in jobs if export.needs_title(j)]
    if not need:
        return
    _TITLES["busy"] = True

    def work():
        try:
            n = export.translate_titles_of(need, config.load(), log=lambda m: None)
            if n < len(need):
                _TITLES["next"] = time.time() + 600
        except Exception:
            _TITLES["next"] = time.time() + 600
        finally:
            _TITLES["busy"] = False
    threading.Thread(target=work, daemon=True).start()


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    return _job(jid).to_dict()


@app.get("/api/jobs/{jid}/events")
async def job_events(jid: str, since: int = 0):
    """SSE stream: full state on change + new log lines."""
    job = _job(jid)

    async def gen():
        last_version, log_pos = -1, since
        idle = 0
        while True:
            if job.version != last_version:
                last_version = job.version
                d = job.to_dict(logs_from=log_pos)
                log_pos += len(d["logs"])
                yield f"data: {json.dumps(d, ensure_ascii=False)}\n\n"
                idle = 0
            else:
                idle += 1
                if idle % 30 == 0:
                    yield ": ping\n\n"
            await asyncio.sleep(0.3)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/jobs/{jid}/rerun")
def rerun(jid: str, data: dict = Body(default={})):
    job = _job(jid)
    if job.status in ("running", "queued"):
        raise HTTPException(409, "Job đang chạy")
    step = data.get("from_step") or _resume_step(job)
    if step not in STEP_IDS:
        raise HTTPException(400, "Bước không hợp lệ")
    if data.get("options"):
        new_mode = data["options"].get("output_mode")
        if new_mode and new_mode != job.settings()["output_mode"]:
            step = _mode_switch_step(job, step, new_mode)
        job.options.update(data["options"])
    # the step you chose is redone; later steps are reused when their inputs did not change (checkpoint.py)
    job.force_steps = {step} if data.get("force", True) and data.get("from_step") else set()
    get_manager().submit(job, step)
    return {"ok": True, "from_step": step}


def _mode_switch_step(job, step, new_mode):
    """Earliest step that must run again when a finished job is re-made in another output mode,
    reusing whatever is already on disk (transcript, translation, on-screen text)."""
    i = STEP_IDS.index(step)
    if new_mode == "text":
        need = "preview" if job.path("screentext.json").exists() else "screen_text"
        return need if i > STEP_IDS.index(need) else step
    # dub / sub need the speech steps
    need = "extract" if not job.path("audio16k.wav").exists() else "separate"
    if new_mode in ("dub", "voiceover") and job.path("voice.wav").exists() and job.path("vocals.wav").exists():
        need = "preview"      # dub <-> voice-over: same Vietnamese voice and stems, only the mix differs
    if i > STEP_IDS.index(need):
        # reuse transcript + translation unless the user asked to redo them
        job.keep_text = i > STEP_IDS.index("translate")
        return need
    return step


def _resume_step(job):
    for sid in STEP_IDS:
        if job.steps[sid]["status"] not in ("done", "skipped"):
            return sid
    return "preview"


@app.post("/api/jobs/{jid}/approve")
def approve(jid: str):
    job = _job(jid)
    if job.status != "awaiting_approval":
        raise HTTPException(409, "Job không ở trạng thái chờ duyệt")
    step = getattr(job, "resume_step", None) or "render"
    job.resume_step = None
    if step in ("preview", "translate"):       # approving the “Biên kịch” plan
        from . import content
        plan = content.load(job.dir)
        if plan:
            plan["approved"] = True
            content.save(job.dir, plan)
    get_manager().submit(job, step)
    return {"ok": True}


@app.post("/api/jobs/{jid}/cancel")
def cancel(jid: str):
    get_manager().cancel(_job(jid))
    return {"ok": True}


@app.delete("/api/jobs/{jid}")
def delete(jid: str):
    job = _job(jid)
    if job.status in ("running", "queued"):
        raise HTTPException(409, "Huỷ job trước khi xoá")
    get_manager().jobs.pop(jid, None)
    shutil.rmtree(job.dir, ignore_errors=True)
    return {"ok": True}


@app.get("/api/jobs/{jid}/lines")
def get_lines(jid: str):
    p = _job(jid).path("lines.json")
    if not p.exists():
        raise HTTPException(404, "Chưa có bản dịch")
    return json.loads(p.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ Biên kịch (content.py)
@app.get("/api/jobs/{jid}/content")
def get_content(jid: str):
    from . import content
    job = _job(jid)
    plan = content.load(job.dir)
    tl = json.loads(job.path("content_timeline.json").read_text(encoding="utf-8")) if job.path("content_timeline.json").exists() else {}
    return {"plan": plan, "enabled": bool(job.settings().get("content_enabled")),
            "awaiting": job.status == "awaiting_approval" and getattr(job, "resume_step", None) in ("preview", "translate"),
            "timeline": {k: tl.get(k) for k in ("duration", "silences", "small_text", "video_type_rule", "video_type_rule_why")},
            "types": content.VIDEO_TYPES, "personas": content.PERSONAS, "tones": content.TONES,
            "files": {k: v for k, v in job.files.items() if k in ("cover", "post") or k.startswith("teaser_")}}


@app.put("/api/jobs/{jid}/content")
def put_content(jid: str, data: dict = Body(...)):
    """Save your edits to the plan (kept on later runs); `render: true` re-renders straight away — only the changed
    narration lines are read again, the film is not watched again."""
    from . import content
    job = _job(jid)
    if job.status in ("running", "queued"):
        raise HTTPException(409, "Job đang chạy")
    old = content.load(job.dir) or {}
    tl = json.loads(job.path("content_timeline.json").read_text(encoding="utf-8")) if job.path("content_timeline.json").exists() else {}
    plan, probs = content.validate(data.get("plan") or {}, float(tl.get("duration") or job.video_duration or 0) or 1e9,
                                   {b["id"]: b["start"] for b in tl.get("small_text") or []}, tl.get("silences") or [],
                                   tl.get("lines") or [])
    plan["edited"] = True
    plan["approved"] = bool(data.get("approve")) or old.get("approved", False)
    for k in ("_meta", "video_type_set"):
        if k in old and k not in plan:
            plan[k] = old[k]
    content.save(job.dir, plan)
    if data.get("render"):
        if job.status == "awaiting_approval":
            job.resume_step = None
        job.options["content_enabled"] = True
        # from the translation: the plan's voice may change it; every finished step is reused by its checkpoint
        get_manager().submit(job, "translate")
    return {"ok": True, "problems": probs}


@app.post("/api/jobs/{jid}/content/rewrite")
def rewrite_content(jid: str, data: dict = Body(default={})):
    """AI writes the plan again (persona / tone / video type you chose). The scene notes are reused."""
    from . import content
    job = _job(jid)
    if job.status in ("running", "queued"):
        raise HTTPException(409, "Job đang chạy")
    for k in ("content_persona", "content_tone"):
        if data.get(k):
            job.options[k] = data[k]
    old = content.load(job.dir) or {}
    if data.get("video_type") in content.VIDEO_TYPES:
        old["video_type_set"] = data["video_type"]
    old.pop("edited", None)
    old.pop("approved", None)
    content.save(job.dir, old)
    job.options["content_enabled"] = True
    job.force_steps = {"content"}
    get_manager().submit(job, "content")
    return {"ok": True}


@app.post("/api/jobs/{jid}/content/voice-test")
async def content_voice_test(jid: str, data: dict = Body(...)):
    """One narration line in the channel's narrator voice."""
    from . import content
    job = _job(jid)
    text = (data.get("text") or "").strip()[:300]
    if not text:
        raise HTTPException(400, "Trống")

    class _J:
        def log(self, *a, **k): pass
        def step(self, *a, **k): pass
        def check_cancel(self): pass
    try:
        res = await asyncio.to_thread(content.synth_vo, _J(), job.dir, job.settings(), [text])
    except Exception as e:
        raise HTTPException(500, str(e))
    return FileResponse(res[text][0], media_type="audio/wav")


@app.put("/api/jobs/{jid}/lines")
def put_lines(jid: str, data: dict = Body(...)):
    """Save edited translation, then re-run from TTS (dub) or preview (subtitle mode)."""
    job = _job(jid)
    if job.status in ("running", "queued"):
        raise HTTPException(409, "Job đang chạy")
    old = json.loads(job.path("lines.json").read_text(encoding="utf-8"))
    edits = data.get("lines") or []
    if len(edits) != len(old):
        raise HTTPException(400, "Số câu không khớp")
    for o, e in zip(old, edits):
        tgt = (e.get("tgt") or "").strip() or o["tgt"]
        if tgt != o["tgt"]:
            o["tgt"], o["edited"] = tgt, True        # hand-edited: the fit-to-time pass never rewrites it
        if e.get("gender") in ("male", "female"):
            o["gender"] = e["gender"]
        if e.get("role") in ("narrator", "character") and e["role"] != o.get("role"):
            o["role"], o["role_set"] = e["role"], True          # your choice wins over the automatic guess
        for k in ("start", "end"):
            if isinstance(e.get(k), (int, float)):
                o[k] = float(e[k])
    job.path("lines.json").write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
    job.log("Đã lưu bản dịch đã sửa")
    if data.get("rerun", True):
        step = "tts" if job.settings()["output_mode"] in ("dub", "voiceover") else "preview"
        get_manager().submit(job, step)
    return {"ok": True}


@app.get("/api/jobs/{jid}/screentext")
def get_screentext(jid: str):
    p = _job(jid).path("screentext.json")
    if not p.exists():
        raise HTTPException(404, "Chưa có")
    return json.loads(p.read_text(encoding="utf-8")) or []


@app.put("/api/jobs/{jid}/screentext")
def put_screentext(jid: str, data: dict = Body(...)):
    """Save edited on-screen text translations, then re-render (preview + final)."""
    job = _job(jid)
    if job.status in ("running", "queued"):
        raise HTTPException(409, "Job đang chạy")
    p = job.path("screentext.json")
    events = json.loads(p.read_text(encoding="utf-8")) or []
    edits = {int(e["id"]): e for e in data.get("items") or []}
    keep = []
    for e in events:
        ed = edits.get(e["id"])
        if ed is not None:
            if ed.get("remove"):
                continue      # leave this text untouched in the video
            e["tgt"] = (ed.get("tgt") or "").strip() or e["tgt"]
        keep.append(e)
    p.write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
    trp = job.path("screentext_tr.json")
    if trp.exists():
        try:
            tr_all = json.loads(trp.read_text(encoding="utf-8"))
            for known in tr_all.values():
                for e in keep:
                    if e["src"] in known:
                        known[e["src"]] = e["tgt"]
            trp.write_text(json.dumps(tr_all, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass
    job.log("Đã lưu bản dịch chữ trên màn hình")
    job.options["screen_text"] = True
    get_manager().submit(job, "preview")
    return {"ok": True}


@app.get("/api/jobs/{jid}/watermarks")
def get_watermarks(jid: str):
    from . import util, watermark
    job = _job(jid)
    src = job.path("source.mp4")
    if not src.exists():
        raise HTTPException(404, "Chưa có video gốc")
    W, H = util.video_size(src)
    return {"items": watermark.load(job.dir), "w": W, "h": H, "duration": util.duration(src),
            "detected": job.path(watermark.FILE).exists()}


@app.put("/api/jobs/{jid}/watermarks")
def put_watermarks(jid: str, data: dict = Body(...)):
    """Save watermark boxes (auto ones toggled on/off, boxes you drew), then re-render preview + final."""
    from . import util, watermark
    job = _job(jid)
    if job.status in ("running", "queued"):
        raise HTTPException(409, "Job đang chạy")
    dur = util.duration(job.path("source.mp4"))
    old = {it["id"]: it for it in watermark.load(job.dir)}
    items = []
    for e in data.get("items") or []:
        base = dict(old.get(e.get("id"), {})) if e.get("auto") else {}
        box = [min(max(float(v), 0.0), 1.0) for v in e["box"]]
        if box[2] - box[0] < 0.005 or box[3] - box[1] < 0.005:
            continue
        a, b = max(0.0, float(e.get("start") or 0)), min(dur, float(e.get("end") or dur))
        it = {**base, "box": box, "start": a, "end": b if b > a else dur, "enabled": bool(e.get("enabled", True)),
              "src": (e.get("src") or base.get("src") or "Vùng tự vẽ")[:80],
              "line_h": base.get("line_h") or round(min(box[3] - box[1], 0.04), 4)}
        if base and base.get("box") != box:
            it.pop("mask", None)          # moved/resized: remove the whole box instead of the old stroke mask
            it.pop("track", None)
        if not e.get("auto"):
            it.pop("auto", None)
        else:
            it["auto"] = True
        items.append(it)
    watermark.save(job.dir, items)
    job.add_file("watermarks", watermark.FILE)
    job.log(f"Đã lưu {sum(1 for i in items if i['enabled'])} vùng watermark cần xoá")
    if (job.options.get("wm_remove") or config.load().get("wm_remove")) == "off":
        job.options["wm_remove"] = "auto"
    if data.get("render", True):
        get_manager().submit(job, "preview")
    return {"ok": True}


@app.get("/api/jobs/{jid}/frame")
def job_frame(jid: str, t: float = 0.0):
    """One frame of the original video (for drawing watermark boxes)."""
    import subprocess
    from fastapi.responses import Response
    from . import util
    job = _job(jid)
    src = job.path("source_view.mp4") if job.path("source_view.mp4").exists() else job.path("source.mp4")
    if not src.exists():
        raise HTTPException(404)
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(t, 0):.2f}", "-i", str(src), "-frames:v", "1",
                        "-vf", "scale='min(960,iw)':-2", "-f", "image2", "-c:v", "mjpeg", "-q:v", "4", "-"],
                       capture_output=True, timeout=30, creationflags=util.CREATE_NO_WINDOW)
    if r.returncode != 0 or not r.stdout:
        raise HTTPException(500, "Không lấy được khung hình")
    return Response(r.stdout, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/files/{jid}/{name}")
def files(jid: str, name: str, dl: int = 0):
    if name not in FILE_WHITELIST:
        raise HTTPException(404)
    job = _job(jid)
    p = job.path(name)
    if name == "source_view.mp4" and not p.exists() and job.path("source.mp4").exists():
        from .render import make_view_copy
        make_view_copy(job.path("source.mp4"), p)   # older jobs: build the playable copy on demand
    if not p.exists():
        raise HTTPException(404)
    nice = {"output.mp4": "long_tieng_viet.mp4", "output_sub.mp4": "sub_viet.mp4", "output_text.mp4": "dich_chu_man_hinh.mp4", "output_vo.mp4": "thuyet_minh.mp4", "output_tiktok.mp4": "ban_tiktok.mp4", "voice.wav": "giong_doc.wav",
            "no_vocals.wav": "nhac_nen.wav", "subs.srt": "phu_de.srt"}.get(name, name)
    return FileResponse(p, filename=f"{jid}-{nice}",
                        content_disposition_type="attachment" if dl else "inline")
