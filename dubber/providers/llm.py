"""LLM providers: Claude Code CLI (subscription login), Anthropic API, OpenAI-compatible, Gemini, Ollama."""
import json
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from .. import util
from .base import (AuthError, BadOutput, NotConfigured, Provider, ProviderError, QuotaExhausted, RateLimited,
                   Transient, Unreachable, http_json)


# ------------------------------------------------------------------ Claude Code CLI
def find_claude(path_setting="claude"):
    """Resolve the real claude.exe (the npm .cmd shim mangles multi-line args on Windows)."""
    p = shutil.which(path_setting) or (path_setting if Path(path_setting).exists() else None)
    if not p:
        return None
    p = Path(p)
    if p.suffix.lower() in (".cmd", ".ps1", ""):
        exe = p.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if exe.exists():
            return str(exe)
    return str(p)


class ClaudeCLI(Provider):
    id, label = "claude_cli", "Claude (gói Pro/Max qua Claude Code CLI)"
    vision = True          # images as base64 blocks through --input-format stream-json

    def configured(self, s):
        if not find_claude(s.get("claude_cli_path") or "claude"):
            return False, "Chưa cài Claude Code CLI (npm i -g @anthropic-ai/claude-code)"
        return True, ""

    def model_name(self, s):
        return s.get("claude_cli_model") or "sonnet"

    def complete(self, job, system, user, schema, s, images=None):
        exe = find_claude(s.get("claude_cli_path") or "claude")
        if not exe:
            raise NotConfigured("Chưa cài Claude Code CLI")
        with tempfile.TemporaryDirectory() as td:
            spf = Path(td) / "system.txt"
            spf.write_text(system, encoding="utf-8")
            fmt = ["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"] if images \
                else ["--output-format", "json"]
            args = [exe, "-p", *fmt, "--model", self.model_name(s),
                    "--system-prompt-file", spf, "--tools", "", "--no-session-persistence", "--setting-sources", ""]
            if schema:
                args += ["--json-schema", json.dumps(schema)]
            stdin = user
            if images:
                import base64
                blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                       "data": base64.b64encode(Path(i).read_bytes()).decode()}}
                          for i in images]
                stdin = json.dumps({"type": "user", "message": {"role": "user", "content": blocks + [
                    {"type": "text", "text": user}]}}, ensure_ascii=False) + "\n"
            else:
                args.append("Xử lý nội dung gửi qua stdin theo đúng quy tắc, trả về theo JSON schema.")
            try:
                out = util.run(job, args, input_text=stdin, cwd=td, check=False)
            except RuntimeError as e:
                raise Transient(str(e))
        payload = None
        for line in reversed(out.splitlines()):      # the result object is the last JSON line
            line = line.strip()
            if line.startswith("{") and '"type"' in line:
                try:
                    payload = json.loads(line)
                    break
                except ValueError:
                    continue
        if payload is not None and payload.get("type") != "result":
            payload = next((json.loads(x) for x in reversed(out.splitlines())
                            if x.strip().startswith("{") and '"type":"result"' in x.replace(" ", "")), payload)
        if payload is None:
            low = out.lower()
            if any(k in low for k in ("not logged in", "please run /login", "log in", "authenticat")):
                raise AuthError("Claude CLI chưa đăng nhập (⚙ Cài đặt → 🔑 Đăng nhập Claude)")
            raise Transient(f"Claude CLI trả về không đúng định dạng: {out[-300:]}")
        if payload.get("is_error"):
            msg = str(payload.get("result") or payload.get("subtype") or "")
            low = msg.lower()
            if any(k in low for k in ("login", "log in", "authenticat", "api key", "oauth")):
                raise AuthError("Claude CLI chưa đăng nhập (⚙ Cài đặt → 🔑 Đăng nhập Claude)")
            if any(k in low for k in ("usage limit", "limit reached", "quota", "credit")):
                raise QuotaExhausted(f"Gói Claude đã hết hạn mức tạm thời: {msg[:200]}")
            if any(k in low for k in ("rate limit", "429", "overloaded", "529")):
                raise RateLimited(f"Claude bận/giới hạn tốc độ: {msg[:200]}")
            raise Transient(f"Claude CLI lỗi: {msg[:300]}")
        if isinstance(payload.get("structured_output"), dict):
            return json.dumps(payload["structured_output"], ensure_ascii=False)
        return payload.get("result", "")


# ------------------------------------------------------------------ Anthropic API
class AnthropicAPI(Provider):
    id, label, secrets = "anthropic", "Claude API (API key)", ("anthropic_api_key",)

    def configured(self, s):
        return (bool(s.get("anthropic_api_key")), "Chưa nhập Anthropic API key")

    def model_name(self, s):
        return s.get("anthropic_model") or "claude-sonnet-5-5"

    def complete(self, job, system, user, schema, s):
        import anthropic
        if not s.get("anthropic_api_key"):
            raise NotConfigured("Chưa nhập Anthropic API key")
        client = anthropic.Anthropic(api_key=s["anthropic_api_key"], timeout=float(s.get("translate_timeout_s") or 300),
                                     max_retries=0)       # retries are handled by the orchestrator
        text = ""
        try:
            with client.messages.stream(model=self.model_name(s), max_tokens=16000, system=system,
                                        messages=[{"role": "user", "content": user}]) as stream:
                for chunk in stream.text_stream:
                    text += chunk
                    job.check_cancel()
                    job.step("translate", detail=f"Claude API đang viết… {len(text)} ký tự")
        except anthropic.AuthenticationError as e:
            raise AuthError(f"Anthropic API key không hợp lệ: {e}")
        except anthropic.PermissionDeniedError as e:
            raise AuthError(f"Anthropic API từ chối quyền: {e}")
        except anthropic.RateLimitError as e:
            raise RateLimited(f"Anthropic giới hạn tốc độ: {e}")
        except anthropic.NotFoundError as e:
            raise NotConfigured(f"Model Anthropic không tồn tại: {e}")
        except anthropic.APIConnectionError as e:
            raise Transient(f"Không kết nối được Anthropic: {e}")
        except anthropic.APIStatusError as e:
            low = str(e).lower()
            if "credit" in low or "billing" in low:
                raise QuotaExhausted(f"Tài khoản Anthropic hết credit: {e}")
            if e.status_code >= 500 or e.status_code == 529:
                raise Transient(f"Anthropic lỗi {e.status_code}: {e}")
            raise ProviderError(f"Anthropic lỗi {e.status_code}: {e}")
        return text


# ------------------------------------------------------------------ OpenAI-compatible (OpenAI, OpenRouter, LM Studio, vLLM…)
def _is_local(url):
    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local")


class OpenAICompat(Provider):
    id, label, secrets = "openai", "OpenAI / tương thích OpenAI (API key hoặc server local)", ("openai_api_key",)

    def base(self, s):
        return (s.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")

    def is_local(self, s):
        return _is_local(self.base(s))

    def configured(self, s):
        if not s.get("openai_model"):
            return False, "Chưa chọn model OpenAI-compatible"
        if not s.get("openai_api_key") and not _is_local(self.base(s)):
            return False, "Chưa nhập API key OpenAI-compatible"
        return True, ""

    def probe(self, s):
        if not _is_local(self.base(s)):
            return True, ""
        try:      # local server (LM Studio, vLLM…): is it running?
            http_json(self.base(s) + "/models", headers=self._headers(s), timeout=3)
            return True, ""
        except ProviderError as e:
            return False, f"Server local {self.base(s)} không chạy: {e}"

    def model_name(self, s):
        return s.get("openai_model") or ""

    def _headers(self, s):
        return {"Authorization": f"Bearer {s['openai_api_key']}"} if s.get("openai_api_key") else {}

    def complete(self, job, system, user, schema, s):
        body = {"model": self.model_name(s), "temperature": 0.3,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_object"}}
        timeout = float(s.get("translate_timeout_s") or 300)
        try:
            data = http_json(self.base(s) + "/chat/completions", body, self._headers(s), timeout)
        except ProviderError as e:
            if "response_format" in str(e).lower():       # some local servers reject json mode
                body.pop("response_format")
                data = http_json(self.base(s) + "/chat/completions", body, self._headers(s), timeout)
            else:
                raise
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise BadOutput(f"Phản hồi OpenAI-compatible lạ: {str(data)[:200]}")


# ------------------------------------------------------------------ Gemini (REST)
class Gemini(Provider):
    id, label, secrets = "gemini", "Google Gemini (API key)", ("gemini_api_key",)

    def configured(self, s):
        return (bool(s.get("gemini_api_key")), "Chưa nhập Gemini API key")

    def model_name(self, s):
        return s.get("gemini_model") or "gemini-2.5-flash"

    def complete(self, job, system, user, schema, s):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name(s)}:generateContent"
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"}}
        data = http_json(url, body, {"x-goog-api-key": s["gemini_api_key"]}, float(s.get("translate_timeout_s") or 300))
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError):
            reason = (data.get("promptFeedback") or {}).get("blockReason") if isinstance(data, dict) else None
            raise BadOutput(f"Gemini không trả nội dung ({reason or str(data)[:200]})")


# ------------------------------------------------------------------ Ollama (local)
class Ollama(Provider):
    id, label, local = "ollama", "Ollama (local, GPU)", True

    def url(self, s):
        return (s.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/")

    def model_name(self, s):
        return s.get("ollama_model") or "qwen2.5:7b"

    def configured(self, s):
        return (bool(self.model_name(s)), "Chưa chọn model Ollama")

    def probe(self, s):
        """Never assume Ollama is running: ask it, and check the model is pulled."""
        try:
            tags = http_json(self.url(s) + "/api/tags", timeout=3)
        except ProviderError as e:
            return False, f"Ollama không chạy ở {self.url(s)} ({e.kind})"
        names = {m.get("name") for m in tags.get("models", [])} | {m.get("model") for m in tags.get("models", [])}
        want = self.model_name(s)
        if want not in names and f"{want}:latest" not in names:
            return False, f"Ollama chưa có model {want} (chạy: ollama pull {want})"
        return True, ""

    vision = True          # with a vision model (qwen2.5vl:7b) — set by the caller in s["ollama_model"]

    def complete(self, job, system, user, schema, s, images=None):
        msg = {"role": "user", "content": user}
        if images:
            import base64
            msg["images"] = [base64.b64encode(Path(i).read_bytes()).decode() for i in images]
        body = {"model": self.model_name(s), "stream": False, "format": schema or "json",
                "options": {"temperature": 0.3, "num_ctx": 16384},
                "messages": [{"role": "system", "content": system}, msg]}
        data = http_json(self.url(s) + "/api/chat", body, timeout=float(s.get("translate_timeout_s") or 900))
        try:
            return data["message"]["content"]
        except (KeyError, TypeError):
            raise BadOutput(f"Phản hồi Ollama lạ: {str(data)[:200]}")


# ------------------------------------------------------------------ Google Translate (free, no key) — machine translation
class GoogleFree(Provider):
    id, label, kind = "google_free", "Google Dịch miễn phí (không cần key, hay bị chặn)", "mt"
    _last = [0.0]

    def translate_list(self, job, texts, target, s):
        from urllib.parse import quote
        from deep_translator import GoogleTranslator
        from deep_translator.exceptions import RequestError, TooManyRequests, TranslationNotFound
        tr = GoogleTranslator(source="auto", target=target)
        out, i = [], 0

        def call(text):
            wait = 0.8 - (time.time() - self._last[0])       # stay well under 5 requests/second
            if wait > 0:
                time.sleep(wait)
            self._last[0] = time.time()
            try:
                return tr.translate(text) or ""
            except TooManyRequests as e:
                raise RateLimited(f"Google Dịch: quá nhiều yêu cầu ({e})", retry_after=10)
            except RequestError as e:
                raise Unreachable(f"Google Dịch không truy cập được (có thể bị chặn): {e}")
            except TranslationNotFound:
                return ""
            except Exception as e:
                raise Transient(f"Google Dịch lỗi: {e}")

        while i < len(texts):
            job.check_cancel()
            # pack lines into one request (GET URL: budget by url-encoded length) = batch processing
            chunk, size = [], 0
            while i + len(chunk) < len(texts):
                n = len(quote(texts[i + len(chunk)])) + 3
                if chunk and size + n > 4000:
                    break
                size += n
                chunk.append(texts[i + len(chunk)])
            res = [r.strip() for r in call("\n".join(chunk)).split("\n") if r.strip()]
            if len(res) != len(chunk):        # line count drifted: one by one
                res = [call(t) for t in chunk]
            out += res
            i += len(chunk)
        return out
