"""Translation provider interface + error taxonomy.

Every provider raises one of the ProviderError subclasses below, so the orchestrator can decide
— without knowing the provider — whether to retry, wait, or move on to the next provider.
"""
import json
import socket
import urllib.error
import urllib.request


class ProviderError(Exception):
    retryable = False          # worth another attempt on the same provider?
    cooldown = 0               # seconds to skip this provider afterwards (0 = only for this call)
    kind = "error"

    def __init__(self, msg, retry_after=None):
        super().__init__(msg)
        self.retry_after = retry_after


class Transient(ProviderError):
    """Timeout, 5xx, overloaded, flaky network: retry with backoff."""
    retryable, kind = True, "transient"


class RateLimited(Transient):
    """Requests-per-second/minute limit (HTTP 429 that is not a daily quota)."""
    kind, cooldown = "rate_limited", 30


class BadOutput(Transient):
    """Model answered but not in the requested JSON shape."""
    kind = "bad_output"


class QuotaExhausted(ProviderError):
    """Daily / monthly quota or credit used up: no point retrying today."""
    kind, cooldown = "quota", 6 * 3600


class AuthError(ProviderError):
    """Missing/invalid key, not logged in, no permission."""
    kind, cooldown = "auth", 1800


class NotConfigured(ProviderError):
    """Provider not set up (no key, CLI not installed, model not pulled)."""
    kind = "not_configured"


class Unreachable(ProviderError):
    """Endpoint not running / connection refused (e.g. Ollama not started)."""
    kind, cooldown = "unreachable", 120


class Provider:
    id = ""
    label = ""
    kind = "llm"         # "llm": free-form prompt -> JSON text   |   "mt": list of strings -> list of strings
    vision = False       # complete(..., images=[paths]) can look at pictures
    local = False        # runs on this machine (checked for availability before use)
    secrets = ()         # settings keys that hold secrets (redacted from every message)

    def configured(self, s) -> tuple[bool, str]:
        """Cheap check, no network: are the required settings present?"""
        return True, ""

    def probe(self, s) -> tuple[bool, str]:
        """Is the service actually reachable right now? (local providers are always probed)"""
        return True, ""

    def model_name(self, s) -> str:
        return ""

    def is_local(self, s) -> bool:
        return self.local

    # llm providers
    def complete(self, job, system: str, user: str, schema: dict | None, s: dict) -> str:
        raise NotImplementedError

    # mt providers
    def translate_list(self, job, texts: list[str], target: str, s: dict) -> list[str]:
        raise NotImplementedError


# ------------------------------------------------------------------ HTTP helper with error classification
QUOTA_WORDS = ("quota", "per day", "perday", "daily", "insufficient_quota", "billing", "credit", "exceeded your current")


def classify_http(code: int, body: str) -> ProviderError:
    low = body.lower()
    if code in (401, 403):
        return AuthError(f"HTTP {code}: {body[:200]}")
    if code == 402:
        return QuotaExhausted(f"HTTP 402: {body[:200]}")
    if code == 429:
        if any(w in low for w in QUOTA_WORDS) and "per minute" not in low and "per second" not in low:
            return QuotaExhausted(f"HTTP 429 (hết hạn mức): {body[:200]}")
        return RateLimited(f"HTTP 429 (giới hạn tốc độ): {body[:200]}")
    if code == 404:
        return NotConfigured(f"HTTP 404 (sai model/đường dẫn?): {body[:200]}")
    if code in (408, 409, 425) or code >= 500:
        return Transient(f"HTTP {code}: {body[:200]}")
    return ProviderError(f"HTTP {code}: {body[:200]}")


def http_json(url, body=None, headers=None, timeout=120, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        err = classify_http(e.code, e.read().decode("utf-8", "ignore"))
        ra = e.headers.get("Retry-After") if e.headers else None
        if ra and ra.isdigit():
            err.retry_after = float(ra)
        raise err
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise Transient(f"Hết thời gian chờ: {url}")
        if isinstance(reason, ConnectionRefusedError) or "refused" in str(reason).lower() \
                or "actively refused" in str(reason).lower():
            raise Unreachable(f"Không kết nối được {url}: {reason}")
        raise Transient(f"Lỗi mạng {url}: {reason}")
    except (TimeoutError, socket.timeout):
        raise Transient(f"Hết thời gian chờ: {url}")
    except ConnectionError as e:
        raise Transient(f"Lỗi mạng {url}: {e}")


def redact(msg: str, s: dict, keys) -> str:
    """Remove secret values from a message before it reaches logs or the UI."""
    out = str(msg)
    for k in keys:
        v = (s or {}).get(k)
        if isinstance(v, str) and len(v) >= 6:
            out = out.replace(v, "••••" + v[-4:])
    return out
