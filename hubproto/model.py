"""The model gateway: the one place a context cog turns its prompt, its SOPs and an item into words.

A cog's profile (its ``## Cog:`` section in a Frame) names the model and the prompt; this gateway calls that
model when a key is configured and otherwise answers with a deterministic stand-in, so the prototype runs the
same way with or without a model. Every call is recorded (model, prompt digest, response digest, timing) so a
Track can say which model drafted what. Models draft and answer; they never sign: Gate decisions stay with the
approval-criteria sentences.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from .release import utcnow
from .rules import sha256_of

DEFAULT_MODEL = "stand-in"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def ollama_url() -> str:
    """Where a local Ollama answers (HUBPROTO_OLLAMA_URL, e.g. http://localhost:11434); empty when not configured."""
    return (os.environ.get("HUBPROTO_OLLAMA_URL") or "").rstrip("/")


def backend_for(model: str) -> str:
    """Which backend serves a profile's model: 'anthropic' for claude-* with a key, 'ollama' for 'ollama/<name>'
    (or any other name when an Ollama URL is set), else 'stand-in'."""
    if not model or model == DEFAULT_MODEL or os.environ.get("HUBPROTO_MODEL_FAKE"):
        return "stand-in"
    if model.startswith("ollama/"):
        return "ollama" if ollama_url() else "stand-in"
    if model.startswith("claude"):
        return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else ("ollama" if ollama_url() and os.environ.get("HUBPROTO_OLLAMA_FALLBACK") else "stand-in")
    return "ollama" if ollama_url() else "stand-in"


class ModelGateway:
    def __init__(self):
        self.calls: list[dict] = []

    def available(self, model: str) -> bool:
        return backend_for(model) != "stand-in"

    def complete(self, *, model: str, system: str, user: str, max_tokens: int = 1200, by: str = "") -> dict:
        """Returns {"text", "model", "backend", "stand_in", "prompt_digest", "response_digest", "ms", "at"}."""
        t0 = time.monotonic()
        model = model or DEFAULT_MODEL
        backend = backend_for(model)
        try:
            if backend == "anthropic":
                text = self._anthropic(model, system, user, max_tokens)
            elif backend == "ollama":
                text = self._ollama(model, system, user, max_tokens)
            else:
                text = self._stand_in(system, user)
        except Exception as e:  # a model that does not answer never blocks the work: the stand-in says so
            text = f"[{backend} model {model} did not answer: {e}]\n" + self._stand_in(system, user)
            backend = "stand-in"
        stand_in = backend == "stand-in"
        out = {"text": text, "model": model, "backend": backend, "stand_in": stand_in, "by": by, "at": utcnow(),
               "prompt_digest": sha256_of({"system": system, "user": user})[:16], "response_digest": sha256_of(text)[:16],
               "ms": int((time.monotonic() - t0) * 1000)}
        self.calls.append(out)
        return out

    @staticmethod
    def _anthropic(model: str, system: str, user: str, max_tokens: int) -> str:
        body = json.dumps({"model": model, "max_tokens": max_tokens, "system": system,
                           "messages": [{"role": "user", "content": user}]}).encode("utf-8")
        req = urllib.request.Request(ANTHROPIC_URL, data=body, method="POST", headers={
            "content-type": "application/json", "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
        return "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")

    @staticmethod
    def _ollama(model: str, system: str, user: str, max_tokens: int) -> str:
        """A local model through Ollama's chat API (``ollama/<name>`` or any name when HUBPROTO_OLLAMA_URL is set)."""
        name = model.split("/", 1)[1] if model.startswith("ollama/") else (os.environ.get("HUBPROTO_OLLAMA_MODEL") or model)
        if model.startswith("claude"):
            name = os.environ.get("HUBPROTO_OLLAMA_MODEL") or "llama3.1"
        body = json.dumps({"model": name, "stream": False,
                           "options": {"num_predict": max_tokens, "num_ctx": int(os.environ.get("HUBPROTO_MODEL_CTX") or 16384)},
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode("utf-8")
        req = urllib.request.Request(ollama_url() + "/api/chat", data=body, method="POST", headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=float(os.environ.get("HUBPROTO_MODEL_TIMEOUT") or 180)) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            raise RuntimeError(f"HTTP {e.code} from Ollama for model {name!r}: {detail}") from None
        return (data.get("message") or {}).get("content", "") or data.get("response", "")

    @staticmethod
    def _stand_in(system: str, user: str) -> str:
        """Deterministic words for a run without a model: a review that walks the SOP headings it was given."""
        heads = [line[3:].strip() for line in system.splitlines() if line.startswith("## ")][:6]
        lines = ["Stand-in review (no model configured):"]
        for h in heads:
            lines.append(f"- {h}: checked against the item; no finding.")
        findings = user.split("FINDINGS:", 1)[1].split("\n\n", 1)[0].strip() if "FINDINGS:" in user else ""
        if findings:
            lines.append(findings)
        lines.append("Verdict: fail" if "- [error]" in findings else "Verdict: pass")
        return "\n".join(lines)
