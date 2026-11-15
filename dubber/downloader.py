"""Download a video from TikTok / Douyin / anything yt-dlp supports."""
import json
import re
import threading
import time
import urllib.request
from pathlib import Path

from . import config, util

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def is_douyin(url: str) -> bool:
    return "douyin.com" in url or "iesdouyin.com" in url


def is_tiktok(url: str) -> bool:
    return bool(re.search(r"(?:tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)/", url or "", re.I))


def is_bilibili(url: str) -> bool:
    return bool(re.search(r"(?:bilibili\.com|b23\.tv|bili2233\.cn)/", url or "", re.I))


def is_dtk_supported(url: str) -> bool:
    return is_douyin(url) or is_tiktok(url) or is_bilibili(url)


def is_youtube(url: str) -> bool:
    return bool(re.search(r"(?:youtube\.com|youtu\.be)/", url or "", re.I))


def _node():
    import shutil
    return shutil.which("node")


def ytdlp_args(url, s):
    """Extra yt-dlp options. YouTube needs a JavaScript runtime now (without one, requests can stall or formats go
    missing) — the Node.js already on this PC is used. Videos are capped at 1080p H.264 (the app renders ≤ 1080p;
    the best format is often 4K AV1: huge and slow). Network stalls time out instead of hanging forever."""
    a = ["--socket-timeout", "30", "--retries", "5", "--fragment-retries", "5"]
    if is_youtube(url):
        if _node():
            a += ["--js-runtimes", "node"]
        short = int(s.get("output_short_side") or 1080)
        a += ["-S", f"res:{max(short, 720)},vcodec:h264,acodec:m4a"]
    return a


def is_facebook(url: str) -> bool:
    return "facebook.com" in url or "fb.watch" in url


_last_platform = {}


def platform_gap(job, platform, seconds):
    """Minimum time between two downloads from the same platform (batch runs stay gentle)."""
    wait = float(seconds or 0) - (time.time() - _last_platform.get(platform, 0))
    if wait > 0:
        job.log(f"Giãn cách giữa các lần tải {platform}: chờ {wait:.0f}s…")
        end = time.time() + wait
        while time.time() < end:
            job.check_cancel()
            job.step("download", detail=f"Chờ {end - time.time():.0f}s (giãn cách tải)")
            time.sleep(1)
    _last_platform[platform] = time.time()


def _facebook(job, url, out: Path, s):
    """Facebook Reels / videos through yt-dlp. Public reels need no login; if you logged in yourself in the
    app's Facebook window, that session is passed to yt-dlp so it can see what your account can see."""
    from .sources import facebook
    platform_gap(job, "facebook", s.get("facebook_gap_s", 20))
    ck = out.parent / "fb_cookies.txt"
    try:
        s2 = dict(s, cookies_file=str(ck)) if facebook.export_cookies(ck) else dict(s, cookies_file="")
        if s2["cookies_file"]:
            job.log("Dùng phiên đăng nhập Facebook của bạn trong app")
        return _ytdlp(job, url, out, s2)
    except RuntimeError as e:
        low = str(e).lower()
        if any(k in low for k in ("login", "log in", "cookies", "not available", "unavailable", "private")):
            raise RuntimeError("Facebook không cho tải video này khi chưa đăng nhập, hoặc video không công khai/đã bị "
                               "xoá. Nếu bạn có quyền xem: ⚙ Cài đặt → Đăng nhập Facebook, rồi bấm 'Chạy lại'.\n"
                               f"Chi tiết: {str(e)[-300:]}")
        raise
    finally:
        ck.unlink(missing_ok=True)


def stream_info(url, s):
    """Direct stream URL + required HTTP headers via DTK MCP (when enabled) or yt-dlp (no download) — for quick preview."""
    from . import dtk_client
    url = extract_url(url)
    if is_dtk_supported(url) and dtk_client.configured(s):
        try:
            aweme, plat = dtk_client.resolve_aweme(url, s)
            urls = preview_urls(aweme)
            if urls:
                ref = ("https://www.bilibili.com/" if plat == "bilibili"
                       else "https://www.tiktok.com/" if plat == "tiktok" else "https://www.douyin.com/")
                return urls[0], {"User-Agent": UA, "Referer": ref}, aweme.get("desc") or aweme.get("preview_title") or ""
        except Exception:
            pass
    import tempfile
    import yt_dlp
    from .sources import facebook
    with tempfile.TemporaryDirectory() as td:
        ck = Path(td) / "c.txt"
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
                "format": "best[ext=mp4][vcodec^=avc1]/best[ext=mp4]/best"}
        if is_facebook(url) and facebook.export_cookies(ck):
            opts["cookiefile"] = str(ck)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    return info["url"], dict(info.get("http_headers") or {}), info.get("title") or ""


def from_upload(job, d: Path, s) -> dict:
    """Imported file (saved as upload.<ext> in the job folder) -> source.mp4 (remux, or re-encode if needed)."""
    src = next((p for p in d.glob("upload.*")), None)
    if not src:
        raise RuntimeError("Không thấy file đã tải lên (có thể đã bị xoá)")
    name = job.url.split(":", 1)[1]
    out = d / "source.mp4"
    streams = util.probe(src).get("streams", [])
    v = next((x for x in streams if x.get("codec_type") == "video"), None)
    a = next((x for x in streams if x.get("codec_type") == "audio"), None)
    if not v:
        raise RuntimeError("File không có hình (video)")
    if not a:
        raise RuntimeError("File không có âm thanh")
    dur = util.duration(src)
    copy_ok = v.get("codec_name") in ("h264", "hevc") and a.get("codec_name") in ("aac", "mp3")
    if copy_ok:
        job.step("download", detail="Chuyển sang MP4 (không nén lại)…")
        util.ffmpeg(job, ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-movflags", "+faststart",
                          str(out)], duration=dur, step="download")
    else:
        job.step("download", detail=f"Chuyển {v.get('codec_name')}/{a.get('codec_name')} sang H.264/AAC…")
        enc = ["-c:v", "h264_nvenc", "-cq", "18"] if s.get("video_encoder") == "h264_nvenc" else ["-c:v", "libx264", "-crf", "17"]
        util.ffmpeg(job, ["-i", str(src), "-map", "0:v:0", "-map", "0:a:0", *enc, "-pix_fmt", "yuv420p",
                          "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)], duration=dur, step="download")
    src.unlink(missing_ok=True)          # keep only one copy of a possibly large file
    title = Path(name).stem
    return {"title": title, "uploader": "", "webpage_url": "", "imported": name}


def extract_url(text: str) -> str:
    """Users often paste the whole share text ('7.66 复制打开抖音… https://v.douyin.com/xxx/ …')."""
    m = re.search(r"https?://[^\s，。]+", text)
    return m.group(0) if m else text.strip()


def download(job, url: str, out: Path, s: dict) -> dict:
    from . import dtk_client
    url = extract_url(url)
    errors = []
    if is_facebook(url):
        return _facebook(job, url, out, s)
    if not is_douyin(url):
        if (is_tiktok(url) or is_bilibili(url)) and dtk_client.configured(s):
            try:
                hit = _probe_cache.pop(url, None)
                if hit and time.time() - hit[0] < 600:
                    aweme, cookies = hit[1], hit[2]
                else:
                    aweme, _ = dtk_client.resolve_aweme(url, s)
                    cookies = []
                job.log("Tải video qua DTK MCP (không watermark)…")
                return _download_aweme(job, aweme, cookies, out)
            except Exception as e:
                job.log(f"DTK MCP không tải được ({e}), chuyển sang yt-dlp…", "debug")
        try:
            return _ytdlp(job, url, out, s)
        except Exception as e:
            raise RuntimeError(f"yt-dlp không tải được: {e}")
    # Douyin: try DTK MCP first (when enabled), then yt-dlp with cookies_file, then headless Chromium
    if dtk_client.configured(s) and not re.search(r"douyin\.com/hot/(\d+)", url):
        try:
            hit = _probe_cache.pop(url, None)
            if hit and time.time() - hit[0] < 600:
                aweme, cookies = hit[1], hit[2]
            else:
                aweme, _ = dtk_client.resolve_aweme(url, s)
                cookies = []
            job.log("Tải video Douyin qua DTK MCP (không watermark)…")
            return _download_aweme(job, aweme, cookies, out)
        except Exception as e:
            errors.append(f"dtk: {e}")
            job.log(f"DTK MCP không lấy được ({e}), chuyển sang phương thức dự phòng…", "debug")
    if s.get("cookies_file"):
        try:
            return _ytdlp(job, url, out, s)
        except Exception as e:
            errors.append(f"yt-dlp: {e}")
            job.log("yt-dlp thất bại, chuyển sang trình duyệt ẩn…", "warn")
    try:
        return _douyin_browser(job, url, out, s)
    except Exception as e:
        errors.append(f"browser: {e}")
    raise RuntimeError("Không tải được video Douyin.\n" + "\n".join(errors))


# ---------------------------------------------------------------- yt-dlp
YTDLP_STALL_S = 180
def _ytdlp(job, url, out: Path, s):
    job.log("Tải bằng yt-dlp…")
    tmpl = str(out.with_suffix("")) + ".%(ext)s"
    args = [util.PY, "-m", "yt_dlp", "--newline", "--progress", "--no-playlist", "-f", "bv*+ba/b", *ytdlp_args(url, s),
            "--merge-output-format", "mp4", "-o", tmpl, "--no-simulate",
            "--print", "after_move:INFO%(.{title,uploader,description})j", url]
    if s.get("cookies_file"):
        args[3:3] = ["--cookies", s["cookies_file"]]
    info = {}
    last = [time.time()]
    done = threading.Event()

    def watchdog():          # yt-dlp printed nothing for STALL_S: the connection is stuck → stop it with an error
        while not done.wait(10):
            if time.time() - last[0] > YTDLP_STALL_S:
                p = getattr(job, "proc", None)
                if p:
                    p.kill()
                return
    threading.Thread(target=watchdog, daemon=True).start()

    def on_line(line):
        nonlocal info
        last[0] = time.time()
        if line.startswith("INFO{"):          # only the fields we need (the full info JSON is huge on YouTube)
            try:
                info = json.loads(line[4:])
            except ValueError:
                pass
            return
        m = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
        if m:
            job.step("download", progress=float(m.group(1)) / 100 * 0.95)
        elif line.strip() and not line.startswith("[download]"):
            job.log(line.strip()[:300], "debug")

    try:
        util.run(job, args, on_line=on_line)
    except RuntimeError as e:
        if time.time() - last[0] > YTDLP_STALL_S:
            raise RuntimeError(f"yt-dlp đứng yên {YTDLP_STALL_S // 60} phút không nhận được dữ liệu (mạng/YouTube treo) "
                               "→ đã dừng. Bấm Thử lại.") from e
        raise
    finally:
        done.set()
    if not out.exists():
        cands = list(out.parent.glob(out.stem + ".*"))
        cands = [c for c in cands if c.suffix in (".mp4", ".mkv", ".webm", ".mov")]
        if not cands:
            raise RuntimeError("yt-dlp chạy xong nhưng không thấy file")
        if cands[0].suffix != ".mp4":
            util.ffmpeg(job, ["-i", cands[0], "-c:v", "copy", "-c:a", "aac", out])
            cands[0].unlink()
        else:
            cands[0].rename(out)
    return {"title": info.get("title") or info.get("description") or "", "uploader": info.get("uploader", "")}


# ---------------------------------------------------------------- Douyin via headless Chromium
def _find_aweme(obj, aweme_id):
    """Recursively find the aweme object with this id in any JSON payload."""
    if isinstance(obj, dict):
        if str(obj.get("aweme_id")) == aweme_id and isinstance(obj.get("video"), dict):
            return obj
        for v in obj.values():
            r = _find_aweme(v, aweme_id)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_aweme(v, aweme_id)
            if r:
                return r
    return None


PROFILE = config.DATA / "douyin_profile"   # the app's own browser profile (your Douyin login lives here)
_profile_lock = threading.Lock()
login_state = {"open": False}
_last_fetch = [0.0]


def _launch(pw, headless, profile=None, locale="zh-CN"):
    """Installed Chrome (signed) first, then Edge, then Playwright's bundled Chromium.
    Some Windows security policies (e.g. Smart App Control) block the unsigned bundled chrome.exe."""
    profile = Path(profile or PROFILE)
    profile.mkdir(parents=True, exist_ok=True)
    last = None
    for channel in ("chrome", "msedge", None):
        try:
            return pw.chromium.launch_persistent_context(
                str(profile), channel=channel, headless=headless, locale=locale,
                viewport=None if not headless else {"width": 1280, "height": 800},
                args=["--mute-audio"] if headless else ["--window-size=1280,860"])
        except Exception as e:
            last = e
    raise RuntimeError(f"Không mở được trình duyệt (Chrome/Edge/Chromium): {str(last).splitlines()[0]}")


def open_login_window():
    """Open a visible window on douyin.com with the app profile; you log in / solve captcha yourself."""
    from playwright.sync_api import sync_playwright
    if not _profile_lock.acquire(blocking=False):
        raise RuntimeError("Trình duyệt Douyin của app đang được dùng (đang tải video hoặc cửa sổ đăng nhập đang mở)")
    login_state["open"] = True
    try:
        with sync_playwright() as pw:
            ctx = _launch(pw, headless=False)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=60000)
            # stay open until the user closes the window
            while ctx.pages:
                try:
                    ctx.pages[0].wait_for_timeout(1000)
                except Exception:
                    break
            try:
                ctx.close()
            except Exception:
                pass
    finally:
        login_state["open"] = False
        _profile_lock.release()


def login_status():
    """Is there a logged-in Douyin session in the app profile? (reads the profile, no network)"""
    if login_state["open"]:
        return {"open": True, "logged_in": None}
    from playwright.sync_api import sync_playwright
    if not PROFILE.exists():
        return {"open": False, "logged_in": False}
    if not _profile_lock.acquire(blocking=False):
        return {"open": False, "logged_in": None, "busy": True}
    try:
        with sync_playwright() as pw:
            ctx = _launch(pw, headless=True)
            names = {c["name"] for c in ctx.cookies("https://www.douyin.com")}
            ctx.close()
        return {"open": False, "logged_in": bool(names & {"sessionid", "sessionid_ss", "sid_tt"})}
    finally:
        _profile_lock.release()


def _douyin_browser(job, url, out: Path, s):
    """No account needed. Tries, in order:
    1. the app's saved profile (hidden)      2. a brand-new profile (hidden)
    3. a visible window where YOU solve the captcha (no login required)."""
    hit = _probe_cache.pop(url, None)
    if hit and time.time() - hit[0] < 600:
        job.log("Dùng thông tin video vừa lấy khi ước tính (không mở lại Douyin)")
        aweme, cookies = hit[1], hit[2]
    else:
        aweme, cookies = resolve_aweme(job, url, s)
    return _download_aweme(job, aweme, cookies, out)


_probe_cache = {}      # url -> (time, aweme, cookies): the estimate's lookup is reused by the download


class _Quiet:
    proc = None

    def log(self, *a, **k): pass
    def step(self, *a, **k): pass
    def check_cancel(self): pass


def probe(url, s):
    """Duration / title / author of a video WITHOUT downloading it (for the estimate)."""
    from . import dtk_client
    url = extract_url(url)
    if is_douyin(url):
        aweme, cookies = resolve_aweme(_Quiet(), url, s, allow_visible=False, use_gap=False)
        _probe_cache[url] = (time.time(), aweme, cookies)
        v = aweme.get("video") or {}
        dur = aweme.get("duration") or v.get("duration") or 0
        return {"duration": dur / 1000 if dur > 1000 else dur, "title": aweme.get("desc") or "",
                "uploader": (aweme.get("author") or {}).get("nickname", ""), "platform": "douyin",
                "width": v.get("width"), "height": v.get("height")}
    if (is_tiktok(url) or is_bilibili(url)) and dtk_client.configured(s):
        try:
            aweme, plat = dtk_client.resolve_aweme(url, s)
            _probe_cache[url] = (time.time(), aweme, [])
            v = aweme.get("video") or {}
            dur = aweme.get("duration") or v.get("duration") or 0
            return {"duration": dur / 1000 if dur > 1000 else dur, "title": aweme.get("desc") or "",
                    "uploader": (aweme.get("author") or {}).get("nickname", ""), "platform": plat,
                    "width": v.get("width"), "height": v.get("height")}
        except Exception:
            pass
    import tempfile
    import yt_dlp
    from .sources import facebook
    with tempfile.TemporaryDirectory() as td:
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True, "socket_timeout": 30}
        if is_youtube(url) and _node():
            opts["js_runtimes"] = {"node": {}}
        ck = Path(td) / "c.txt"
        if is_facebook(url) and facebook.export_cookies(ck):
            opts["cookiefile"] = str(ck)
        elif s.get("cookies_file"):
            opts["cookiefile"] = s["cookies_file"]
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    return {"duration": float(info.get("duration") or 0), "title": info.get("title") or "",
            "uploader": info.get("uploader") or "", "platform": info.get("extractor_key", "").lower(),
            "width": info.get("width"), "height": info.get("height")}


def resolve_aweme(job, url, s, allow_visible=True, use_gap=True):
    """Get the video's data (title, play URLs…) without downloading it."""
    import shutil
    import tempfile
    from . import dtk_client
    if dtk_client.configured(s) and not re.search(r"douyin\.com/hot/(\d+)", url):
        try:
            aweme, _ = dtk_client.resolve_aweme(url, s)
            if _play_urls(aweme):
                job.log("Lấy được dữ liệu video qua DTK MCP (không cần mở Chrome)")
                return aweme, []
        except Exception as e:
            job.log(f"DTK MCP không lấy được ({e}) → dùng trình duyệt của app", "debug")
    headless = bool(s.get("douyin_headless", True))
    gap = float(s.get("douyin_gap_s") or 0) - (time.time() - _last_fetch[0])
    if use_gap and gap > 0:
        job.log(f"Giãn cách giữa các lần tải Douyin: chờ {gap:.0f}s…")
        end = time.time() + gap
        while time.time() < end:
            job.check_cancel()
            job.step("download", detail=f"Chờ {end - time.time():.0f}s (giãn cách tải)")
            time.sleep(1)
    _last_fetch[0] = time.time()
    have_profile = _profile_lock.acquire(timeout=2)
    if not have_profile:
        job.log("Cửa sổ 'Đăng nhập Douyin' đang mở → dùng profile tạm để tải", "warn")
    tmp = Path(tempfile.mkdtemp(prefix="dy_profile_"))
    attempts = []
    if have_profile:
        attempts.append(("profile của app", PROFILE, headless, 25))
    attempts.append(("profile mới", tmp, True, 25))
    if headless and allow_visible:
        attempts.append(("cửa sổ xác minh", PROFILE if have_profile else tmp, False, 180))
    try:
        last = None
        for name, prof, hl, wait in attempts:
            if not hl:
                job.log("Douyin yêu cầu xác minh. Đã mở một cửa sổ Chrome: nếu thấy captcha (kéo thanh trượt) "
                        "thì giải giúp — KHÔNG cần đăng nhập. App tự tiếp tục khi thấy video (tối đa 3 phút).", "warn")
                job.step("download", detail="⏳ Chờ bạn giải captcha trong cửa sổ Douyin…")
            try:
                aweme, cookies = _fetch_aweme(job, url, s, hl, wait, prof)
                job.log(f"Lấy được dữ liệu video (cách: {name})")
                return aweme, cookies
            except NeedsUser as e:
                last = e
                job.log(f"Cách '{name}' bị Douyin chặn, thử cách tiếp theo…", "warn")
        raise last
    finally:
        if have_profile:
            _profile_lock.release()
        shutil.rmtree(tmp, ignore_errors=True)


class NeedsUser(RuntimeError):
    pass


def _fetch_aweme(job, url, s, headless, wait_s, profile=None):
    from playwright.sync_api import sync_playwright

    job.log("Mở Douyin bằng Chrome" + (" ẩn" if headless else "") + " để lấy link video…")
    with sync_playwright() as pw:
        ctx = _launch(pw, headless, profile)
        if s.get("cookies_file"):
            ctx.add_cookies(_load_cookies_txt(s["cookies_file"]))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        found = {}
        aweme_id = _aweme_id(url)

        def on_response(resp):
            if found.get("aweme") or "json" not in (resp.headers.get("content-type") or ""):
                return
            if "/aweme/" not in resp.url:
                return
            try:
                data = resp.json()
            except Exception:
                return
            aid = found.get("id")
            if aid:
                a = _find_aweme(data, aid)
                if a:
                    found["aweme"] = a
            elif found.get("hot") and "aweme/detail" in resp.url:
                lst = []
                _walk_all(data, lst)
                if lst:
                    found["aweme"] = lst[0]     # the topic page's main video

        page.on("response", on_response)

        hot = re.search(r"douyin\.com/hot/(\d+)", url)
        if hot and not aweme_id:   # hot-board topic: its page plays the topic's main video
            found["hot"] = True
            job.log(f"Mở trang chủ đề 热榜 {hot.group(1)} để lấy video chính…")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                job.log(f"Mở trang lỗi: {e}", "debug")
            t0 = time.time()
            while time.time() - t0 < max(wait_s, 15) and not found.get("aweme"):
                job.check_cancel()
                try:
                    page.wait_for_timeout(700)
                except Exception:
                    break
            if found.get("aweme"):
                aweme_id = str(found["aweme"]["aweme_id"])
                job.log(f"Video chính của chủ đề: {aweme_id}")
                try:
                    cookies = ctx.cookies()
                    ctx.close()
                except Exception:
                    cookies = []
                return found["aweme"], cookies
            raise NeedsUser("Không lấy được video của chủ đề 热榜 (Douyin chặn hoặc chủ đề không còn video).")

        if not aweme_id:  # short link: follow redirects to learn the id
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            aweme_id = _aweme_id(page.url)
            if not aweme_id:
                raise RuntimeError(f"Không đọc được ID video từ link: {page.url}")
        found["id"] = aweme_id
        job.log(f"ID video Douyin: {aweme_id}")
        job.step("download", progress=0.05)

        targets = [f"https://www.iesdouyin.com/share/video/{aweme_id}/",   # public share page
                   f"https://www.douyin.com/video/{aweme_id}",
                   f"https://www.douyin.com/jingxuan?modal_id={aweme_id}"]
        if not headless:   # don't navigate away while the user is solving a captcha
            targets = targets[1:2]
        per_target = max(wait_s / len(targets), 10)
        for target in targets:
            try:
                page.goto(target, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                job.log(f"Mở trang lỗi: {e}", "debug")
            t0 = time.time()
            while time.time() - t0 < per_target and not found.get("aweme"):
                job.check_cancel()
                try:
                    a = _aweme_from_html(page.content(), aweme_id)   # server-rendered data
                    if a:
                        found["aweme"] = a
                        break
                except Exception:
                    pass
                try:
                    page.wait_for_timeout(700)
                except Exception:   # user closed the window
                    break
            if found.get("aweme"):
                break
        try:
            cookies = ctx.cookies()
            ctx.close()
        except Exception:
            cookies = []

    if not found.get("aweme"):
        raise NeedsUser("Không lấy được dữ liệu video: Douyin đang chặn/yêu cầu xác minh. "
                        "Bấm 'Chạy lại' — app sẽ mở cửa sổ Chrome để bạn kéo captcha (không cần tài khoản). "
                        "Nếu vẫn lỗi, thử lại sau ít phút hoặc đổi mạng (Douyin hạn chế khi truy cập nhiều lần liên tục).")
    return found["aweme"], cookies


def _download_aweme(job, aweme, cookies, out: Path):
    urls = _play_urls(aweme)
    if not urls:
        raise RuntimeError("Có dữ liệu video nhưng không có link phát")
    cookie_hdr = "; ".join(f"{c['name']}={c['value']}" for c in cookies if "douyin" in c.get("domain", ""))
    last = None
    for u in urls:
        try:
            _http_download(job, u, out, cookie_hdr, referer=_referer_for(u, aweme))
            if util.duration(out) > 0:
                break
        except Exception as e:
            last = e
            job.log(f"Link lỗi, thử link khác: {e}", "warn")
    else:
        raise RuntimeError(f"Tất cả link tải đều lỗi: {last}")
    if not util.has_audio(out):
        raise RuntimeError("File tải về không có âm thanh")
    desc = aweme.get("desc") or aweme.get("preview_title") or ""
    author = (aweme.get("author") or {}).get("nickname", "")
    return {"title": desc, "uploader": author}


def _walk_all(o, out):
    if isinstance(o, dict):
        if o.get("aweme_id") and isinstance(o.get("video"), dict):
            out.append(o)
            return
        for v in o.values():
            _walk_all(v, out)
    elif isinstance(o, list):
        for v in o:
            _walk_all(v, out)


def _load_cookies_txt(path):
    """Read a Netscape cookies.txt exported from the user's own browser."""
    cookies = []
    for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _, cpath, secure, expires, name, value = parts[:7]
        c = {"name": name, "value": value, "domain": domain, "path": cpath or "/",
             "secure": secure.upper() == "TRUE"}
        if expires.isdigit() and int(expires) > 0:
            c["expires"] = int(expires)
        cookies.append(c)
    return cookies


def _aweme_id(url):
    for pat in (r"/video/(\d{15,22})", r"modal_id=(\d{15,22})", r"/note/(\d{15,22})", r"aweme_id=(\d{15,22})"):
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def _aweme_from_html(html, aweme_id):
    from urllib.parse import unquote
    m = re.search(r'<script id="RENDER_DATA" type="application/json">(.*?)</script>', html, re.S)
    if m:
        try:
            a = _find_aweme(json.loads(unquote(m.group(1))), aweme_id)
            if a:
                return a
        except Exception:
            pass
    m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", html, re.S)   # share page
    if m:
        try:
            return _find_aweme(json.loads(m.group(1)), aweme_id)
        except Exception:
            pass
    return None


def preview_urls(aweme):
    """Light H.264 streams for quick in-browser preview (lowest bitrate first), then anything playable."""
    v = aweme.get("video") or {}
    light = []
    for br in v.get("bit_rate") or []:
        if br.get("is_h265"):
            continue
        for u in (br.get("play_addr") or {}).get("url_list") or []:
            light.append((br.get("bit_rate") or 0, u))
    light.sort(key=lambda x: x[0])
    urls = [u for _, u in light] + [u for u in _play_urls(aweme)]
    out, seen = [], set()
    for u in urls:
        u = "https:" + u if u.startswith("//") else u
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _play_urls(aweme):
    v = aweme.get("video") or {}
    cands = []
    for br in v.get("bit_rate") or []:
        pa = br.get("play_addr") or {}
        score = (br.get("bit_rate") or 0) + (0 if br.get("is_h265") else 1)  # prefer best, h264 on tie
        for u in pa.get("url_list") or []:
            cands.append((score, u))
    cands.sort(key=lambda x: -x[0])
    urls = [u for _, u in cands]
    for key in ("play_addr", "play_addr_h264", "download_addr"):
        urls += (v.get(key) or {}).get("url_list") or []
    seen, res = set(), []
    for u in urls:
        if u.startswith("//"):
            u = "https:" + u
        if u not in seen:
            seen.add(u)
            res.append(u)
    return res


def _referer_for(url: str, aweme: dict | None = None) -> str:
    plat = str(((aweme or {}).get("_dtk") or {}).get("platform") or "").lower()
    if plat == "bilibili" or "bilivideo" in url or "hdslb" in url or "bilibili" in url:
        return "https://www.bilibili.com/"
    if plat == "tiktok" or "tiktok" in url or "byteoversea" in url or "musical.ly" in url:
        return "https://www.tiktok.com/"
    return "https://www.douyin.com/"


def _http_download(job, url, out: Path, cookie="", referer="https://www.douyin.com/"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer,
                                               "Cookie": cookie})
    with urllib.request.urlopen(req, timeout=60) as r, open(out, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got, t0 = 0, time.time()
        while True:
            job.check_cancel()
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                job.step("download", progress=0.1 + 0.85 * got / total,
                         detail=f"{got/1e6:.1f}/{total/1e6:.1f} MB")
    if got < 10000:
        raise RuntimeError(f"File quá nhỏ ({got} bytes)")
