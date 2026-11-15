"""Official vendor CLIs that sign in with your own account (no API key):
- Codex CLI  (`codex login` with a ChatGPT plan)        -> provider "codex_cli"
- Gemini CLI (`gemini`, "Login with Google" on first run) -> provider "gemini_cli"
Prompts go through stdin (the npm .cmd shims mangle multi-line arguments on Windows), and the node entry
script is called directly when it can be found.
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import util
from .base import AuthError, NotConfigured, Provider, QuotaExhausted, RateLimited, Transient

NODE = shutil.which("node") or "node"


def _node_cmd(name, js_rel):
    """['node', '<…>/<pkg>/<entry>.js'] for an npm-installed CLI, else the shim itself."""
    shim = shutil.which(name)
    if not shim:
        return None
    js = Path(shim).parent / "node_modules" / js_rel
    return [NODE, str(js)] if js.exists() else [shim]


def _classify(out: str, who: str):
    low = out.lower()
    if any(k in low for k in ("not logged in", "please log in", "login required", "unauthorized", "401",
                              "authentication", "auth method", "please set an auth")):
        raise AuthError(f"{who} chưa đăng nhập (⚙ Cài đặt → Dịch → Đăng nhập {who})")
    if any(k in low for k in ("usage limit", "quota", "limit reached", "exceeded your", "resource_exhausted",
                              "plan limit")):
        raise QuotaExhausted(f"{who}: hết hạn mức của gói: {out[-200:]}")
    if any(k in low for k in ("rate limit", "429", "too many requests", "overloaded")):
        raise RateLimited(f"{who}: giới hạn tốc độ: {out[-200:]}")
    raise Transient(f"{who} lỗi: {out[-300:]}")


class CodexCLI(Provider):
    id, label = "codex_cli", "ChatGPT (gói Plus/Pro qua Codex CLI)"
    vision = True          # codex exec -i <file> (checked with codex-cli 0.168)

    def cmd(self):
        return _node_cmd("codex", "@openai/codex/bin/codex.js")

    def configured(self, s):
        return (bool(self.cmd()), "Chưa cài Codex CLI (npm i -g @openai/codex)")

    def model_name(self, s):
        return s.get("codex_model") or ""

    def logged_in(self):
        c = self.cmd()
        if not c:
            return None
        r = subprocess.run(c + ["login", "status"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, creationflags=util.CREATE_NO_WINDOW)
        out = (r.stdout + r.stderr).lower()
        return "logged in" in out and "not logged in" not in out

    def complete(self, job, system, user, schema, s, images=None):
        c = self.cmd()
        if not c:
            raise NotConfigured("Chưa cài Codex CLI")
        with tempfile.TemporaryDirectory() as td:
            last = Path(td) / "last.txt"
            args = c + ["exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only", "--color", "never",
                        "-o", str(last)]
            if self.model_name(s):
                args += ["-m", self.model_name(s)]
            for img in images or []:
                args += ["-i", str(img)]
            args.append("-")      # read the whole prompt from stdin
            shape = f"\nJSON phải đúng schema: {json.dumps(schema, ensure_ascii=False)}" if schema else ""
            prompt = (f"{system}\n\nKhông dùng công cụ, không chạy lệnh; chỉ trả lời bằng JSON theo yêu cầu.{shape}\n\n"
                      f"---\n{user}")
            job.step(s.get("_step") or "translate", detail=s.get("_step_detail") or "Codex (ChatGPT) đang dịch…")
            out = util.run(job, args, input_text=prompt, cwd=td, check=False)
            text = last.read_text(encoding="utf-8").strip() if last.exists() else ""
        if not text:
            if images and any(k in out.lower() for k in ("unexpected argument '-i'", "unknown option", "--image")):
                raise NotConfigured("Bản Codex CLI đang cài không nhận ảnh (-i) — cập nhật: npm i -g @openai/codex")
            _classify(out, "Codex")
        return text


def gemini_logged_in():
    """Gemini CLI needs BOTH a stored Google sign-in (~/.gemini/oauth_creds.json) and an auth method chosen in
    ~/.gemini/settings.json (written when you pick "Login with Google" on first interactive run) — or an
    env var such as GEMINI_API_KEY. Mirrors the CLI's own check ("Please set an Auth method…")."""
    import os
    if any(os.environ.get(k) for k in ("GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_GCA")):
        return True
    home = Path.home() / ".gemini"
    if not (home / "oauth_creds.json").exists():
        return False
    try:
        st = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    selected = st.get("selectedAuthType") or ((st.get("security") or {}).get("auth") or {}).get("selectedType")
    return bool(selected)


LOGIN = {   # provider id -> how to sign in / out with the official CLI (runs in a visible console window)
    "codex_cli": {"login": ["login"], "logout": ["logout"], "name": "ChatGPT (Codex CLI)"},
    "gemini_cli": {"login": [], "logout": None, "name": "Google (Gemini CLI)"},   # first run asks "Login with Google"
}


class GeminiCLI(Provider):
    id, label = "gemini_cli", "Gemini (tài khoản Google qua Gemini CLI)"

    def cmd(self):
        return _node_cmd("gemini", "@google/gemini-cli/bundle/gemini.js")

    def configured(self, s):
        return (bool(self.cmd()), "Chưa cài Gemini CLI (npm i -g @google/gemini-cli)")

    def model_name(self, s):
        return s.get("gemini_cli_model") or ""

    def complete(self, job, system, user, schema, s):
        c = self.cmd()
        if not c:
            raise NotConfigured("Chưa cài Gemini CLI")
        args = c + ["-o", "json", "--approval-mode", "plan"]
        if self.model_name(s):
            args += ["-m", self.model_name(s)]
        args += ["-p", "Làm đúng theo hướng dẫn trong nội dung gửi kèm (stdin). Chỉ trả về JSON."]
        job.step("translate", detail="Gemini CLI đang dịch…")
        with tempfile.TemporaryDirectory() as td:
            out = util.run(job, args, input_text=f"{system}\n\n---\n{user}", cwd=td, check=False)
        start = out.find("{")
        try:
            data = json.loads(out[start:]) if start >= 0 else None
        except ValueError:
            data = None
        if isinstance(data, dict):
            if data.get("error"):
                _classify(json.dumps(data["error"], ensure_ascii=False), "Gemini CLI")
            if data.get("response"):
                return data["response"]
        _classify(out, "Gemini CLI")
