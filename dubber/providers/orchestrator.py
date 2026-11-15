"""Run a translation task across the configured providers with bounded retries and automatic fallback.

Rules (see docs/ROADMAP.md, M1):
- try providers in order: the selected one first, then `translate_fallbacks`; each at most once per call
  -> no infinite fallback loops;
- skip providers that are not configured, not reachable (local ones are probed first: Ollama is never
  assumed to be running), or in cooldown after a quota/auth/unreachable failure;
- retry the SAME provider only for retryable errors (rate limit, timeout, 5xx, malformed output), with
  bounded exponential backoff;
- secrets are redacted from every message;
- if nothing works, raise NoProvider with a per-provider explanation.
"""
import threading
import time

from ..jobs import Cancelled
from .base import ProviderError, redact

ALIASES = {"google": "google_free"}
_state: dict[str, dict] = {}         # provider id -> {"until": ts, "reason": str}   (persisted, survives restarts)
_last_call: dict[str, float] = {}    # provider id -> time of the last request (rate limiting)
_lock = threading.Lock()
STATE_FILE = None                    # set by _state_file(); tests can point it elsewhere


def _state_file():
    from .. import config
    return STATE_FILE or (config.DATA / "provider_state.json")


def _load_state():
    import json
    try:
        data = json.loads(_state_file().read_text(encoding="utf-8"))
        now = time.time()
        _state.update({k: v for k, v in data.items() if v.get("until", 0) > now})
    except Exception:
        pass


def _save_state():
    import json
    try:
        _state_file().write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def throttle(job, pid, s):
    """Per-provider minimum interval between requests (settings: translate_min_interval_s = {id: seconds})."""
    gap = float((s.get("translate_min_interval_s") or {}).get(pid, 0) or 0)
    if gap <= 0:
        return
    with _lock:
        wait = _last_call.get(pid, 0) + gap - time.time()
    if wait > 0:
        _sleep(job, wait)
    with _lock:
        _last_call[pid] = time.time()


class NoProvider(RuntimeError):
    pass


def registry():
    from . import REGISTRY
    return REGISTRY


def all_secret_keys():
    keys = set()
    for p in registry().values():
        keys.update(p.secrets)
    return keys


def chain(s) -> list[str]:
    primary = ALIASES.get(s.get("translate_provider"), s.get("translate_provider"))
    order = [primary]
    if s.get("translate_fallback_enabled", True):
        order += [ALIASES.get(p, p) for p in (s.get("translate_fallbacks") or [])]
    seen, out = set(), []
    for p in order:
        if p in registry() and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def cooldown(pid, seconds, reason):
    if seconds > 0:
        with _lock:
            _state[pid] = {"until": time.time() + seconds, "reason": reason}
            _save_state()


def cooling(pid):
    with _lock:
        if not _state and not getattr(cooling, "_loaded", False):
            cooling._loaded = True
            _load_state()
        st = _state.get(pid)
        if st and st["until"] > time.time():
            return st
        if _state.pop(pid, None):
            _save_state()
        return None


def clear(pid):
    with _lock:
        if _state.pop(pid, None):
            _save_state()


def reset_cooldowns():
    with _lock:
        _state.clear()
        _last_call.clear()
        cooling._loaded = True


def status(s, probe=True):
    """For the settings UI: every provider with configured / reachable / cooldown info."""
    out = []
    order = chain(s)
    keys = all_secret_keys()
    for pid, p in registry().items():
        ok, why = p.configured(s)
        reach, rwhy = (True, "")
        if ok and probe and p.is_local(s):
            reach, rwhy = p.probe(s)
        cool = cooling(pid)
        out.append({"id": pid, "label": p.label, "kind": p.kind, "local": p.is_local(s), "model": p.model_name(s),
                    "configured": ok, "reachable": reach, "in_chain": pid in order,
                    "position": order.index(pid) if pid in order else None,
                    "reason": redact((why if not ok else "") or (rwhy if not reach else "")
                                     or (cool or {}).get("reason", ""), s, keys),
                    "cooldown_until": cool["until"] if cool else None})
    return out


def run(job, s, task, label="dịch", order=None):
    """task(provider) -> result. Returns (result, provider_id). `order`: provider ids to try instead of the
    translation chain (the content step has its own: ChatGPT → Claude → Ollama)."""
    keys = all_secret_keys()
    max_retries = int(s.get("translate_max_retries", 3))
    base_delay = float(s.get("translate_backoff_s", 2.0))
    failures = []
    for pid in ([x for x in order if x in registry()] if order else chain(s)):
        p = registry()[pid]
        cool = cooling(pid)
        if cool:
            failures.append(f"{pid}: tạm bỏ qua ({cool['reason'][:120]})")
            continue
        ok, why = p.configured(s)
        if not ok:
            failures.append(f"{pid}: {why}")
            continue
        if p.is_local(s) or pid == "ollama":
            ok, why = p.probe(s)
            if not ok:
                failures.append(f"{pid}: {why}")
                cooldown(pid, 60, why)
                continue
        attempt = 0
        while True:
            job.check_cancel()
            try:
                if failures:
                    job.log(f"→ Thử {label} bằng {pid}" + (f" ({p.model_name(s)})" if p.model_name(s) else ""), "warn")
                throttle(job, pid, s)
                result = task(p)
                if p.kind != "mt":
                    try:      # AI calls of this job (written to the log when the job ends)
                        job.ai_calls = (getattr(job, "ai_calls", 0) or 0) + 1
                    except Exception:
                        pass
                return result, pid
            except Cancelled:
                raise
            except ProviderError as e:
                msg = redact(str(e), s, keys)
                if e.retryable and attempt < max_retries:
                    delay = min(60.0, base_delay * (2 ** attempt))
                    if e.retry_after:
                        delay = min(120.0, max(delay, float(e.retry_after)))
                    attempt += 1
                    job.log(f"{pid}: {msg[:200]} → thử lại lần {attempt}/{max_retries} sau {delay:.0f}s", "warn")
                    _sleep(job, delay)
                    continue
                cooldown(pid, e.cooldown, msg)
                failures.append(f"{pid}: {msg[:200]}")
                job.log(f"{pid} không dùng được ({e.kind}): {msg[:200]}", "warn")
                break
            except Exception as e:          # unexpected bug in an adapter: don't retry, move on
                msg = redact(f"{type(e).__name__}: {e}", s, keys)
                failures.append(f"{pid}: {msg[:200]}")
                job.log(f"{pid} lỗi không xác định: {msg[:200]}", "warn")
                break
    raise NoProvider("Không có công cụ dịch nào dùng được:\n- " + "\n- ".join(failures or ["chưa cấu hình provider nào"]) +
                     "\n→ Vào ⚙ Cài đặt → Dịch để đăng nhập Claude / nhập API key / bật Ollama, rồi bấm 'Chạy lại'.")


def _sleep(job, seconds):
    end = time.time() + seconds
    while time.time() < end:
        job.check_cancel()
        time.sleep(max(0.0, min(0.5, end - time.time())))
