"""Step result envelope (version 1), the seam between an Op and a Cog.

``ok`` says the invocation completed with a parseable payload. It does not
mean the result is acceptable: that is the Gate's call, which reads the
problems list. A problem has a check, a one-sentence detail, and a severity
of ``error`` or ``warning``.
"""

from __future__ import annotations

import time


def make_envelope(cog_id: str, version: str, task: str, payload=None, problems=(), error=None,
                  raw=None, timing: float | None = None) -> dict:
    env = {
        "envelope": 1,
        "ok": error is None,
        "problems": list(problems),
        "cog": {"id": cog_id, "version": version},
        "task": task,
        "payload": None if error else payload,
        "error": error,
        "raw": raw,
        "timing": timing,
    }
    return env


def problem(check: str, detail: str, severity: str = "error") -> dict:
    return {"check": check, "detail": detail, "severity": severity}


def synthesized_failure(cog_id: str, version: str, task: str, code: str, detail: str) -> dict:
    return make_envelope(cog_id, version, task, error={"code": code, "detail": detail})


def gate_default(env: dict) -> str:
    """The profile's default Gate: pass, pass-with-problems, or fail."""
    if not env.get("ok") or env.get("error"):
        return "fail"
    if any(p["severity"] == "error" for p in env.get("problems", [])):
        return "fail"
    if env.get("problems"):
        return "pass-with-problems"
    return "pass"


class Timer:
    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.seconds = round(time.perf_counter() - self.start, 4)
