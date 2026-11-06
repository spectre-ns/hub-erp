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
import urllib.request

from .release import utcnow
from .rules import sha256_of

DEFAULT_MODEL = "stand-in"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


class ModelGateway:
    def __init__(self):
        self.calls: list[dict] = []

    def available(self, model: str) -> bool:
        return bool(model and model != DEFAULT_MODEL and model.startswith("claude") and os.environ.get("ANTHROPIC_API_KEY")
                    and not os.environ.get("HUBPROTO_MODEL_FAKE"))

    def complete(self, *, model: str, system: str, user: str, max_tokens: int = 1200, by: str = "") -> dict:
        """Returns {"text", "model", "stand_in", "prompt_digest", "response_digest", "ms"}."""
        t0 = time.monotonic()
        model = model or DEFAULT_MODEL
        if self.available(model):
            text = self._anthropic(model, system, user, max_tokens)
            stand_in = False
        else:
            text = self._stand_in(system, user)
            stand_in = True
        out = {"text": text, "model": model, "stand_in": stand_in, "by": by, "at": utcnow(),
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
