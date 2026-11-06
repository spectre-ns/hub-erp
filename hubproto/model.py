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
import sys
import time
import urllib.error
import urllib.request

from .release import utcnow
from .rules import sha256_of

DEFAULT_MODEL = "stand-in"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
PROMISE = __import__("re").compile(r"\b(let me (check|look|search|find|see)|i'?ll (check|look|search|find)|checking|looking (that|it) up|one moment)\b", __import__("re").I)


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
        self._last_usage = None

    def available(self, model: str) -> bool:
        return backend_for(model) != "stand-in"

    def complete(self, *, model: str, system: str, user: str, max_tokens: int = 1200, by: str = "", history: list[dict] | None = None) -> dict:
        """Returns {"text", "model", "backend", "stand_in", "prompt_digest", "response_digest", "ms", "at"}."""
        t0 = time.monotonic()
        model = model or DEFAULT_MODEL
        backend = backend_for(model)
        try:
            if backend == "anthropic":
                text = self._anthropic(model, system, user, max_tokens, history)
            elif backend == "ollama":
                text = self._ollama(model, system, user, max_tokens, history)
            else:
                text = self._stand_in(system, user)
        except Exception as e:  # a model that does not answer never blocks the work: the stand-in says so
            text = f"[{backend} model {model} did not answer: {e}]\n" + self._stand_in(system, user)
            backend = "stand-in"
        stand_in = backend == "stand-in"
        usage = getattr(self, "_last_usage", None) if backend == "ollama" else None
        self._last_usage = None
        out = {"text": text, "model": model, "served_model": (usage or {}).get("served_model") or (model if backend == "anthropic" else "stand-in"),
               "backend": backend, "stand_in": stand_in, "by": by, "at": utcnow(), "prompt_chars": len(system) + len(user), "usage": usage,
               "prompt_digest": sha256_of({"system": system, "user": user})[:16], "response_digest": sha256_of(text)[:16],
               "ms": int((time.monotonic() - t0) * 1000)}
        self.calls.append(out)
        u = usage or {}
        sys.stdout.write(f"model call by={by or '-'} model={model} served_by={out['served_model']} backend={backend} prompt_chars={out['prompt_chars']}"
                         + (f" prompt_tokens={u.get('prompt_tokens')} output_tokens={u.get('output_tokens')} done={u.get('done_reason')}" if u else "")
                         + f" ms={out['ms']}\n")
        sys.stdout.flush()
        return out

    @staticmethod
    def _anthropic(model: str, system: str, user: str, max_tokens: int, history: list[dict] | None = None) -> str:
        turns = [{"role": t["role"], "content": t["content"]} for t in (history or []) if t.get("content")]
        body = json.dumps({"model": model, "max_tokens": max_tokens, "system": system,
                           "messages": turns + [{"role": "user", "content": user}]}).encode("utf-8")
        req = urllib.request.Request(ANTHROPIC_URL, data=body, method="POST", headers={
            "content-type": "application/json", "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
        return "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")

    def _ollama(self, model: str, system: str, user: str, max_tokens: int, history: list[dict] | None = None) -> str:
        """A local model through Ollama's chat API (``ollama/<name>`` or any name when HUBPROTO_OLLAMA_URL is set)."""
        name = model.split("/", 1)[1] if model.startswith("ollama/") else (os.environ.get("HUBPROTO_OLLAMA_MODEL") or model)
        if model.startswith("claude"):
            name = os.environ.get("HUBPROTO_OLLAMA_MODEL") or "llama3.1"
        ceiling = int(os.environ.get("HUBPROTO_MODEL_MAX_CHARS") or 90000)  # ~22k tokens; under the 32k window with room to answer
        if len(system) + len(user) > ceiling:
            keep = max(ceiling - len(user) - 200, 2000)
            system = system[:keep] + "\n[... context trimmed to fit the model's window]"
        payload = {"model": name, "stream": False,
                   "options": {"num_predict": max_tokens, "num_ctx": int(os.environ.get("HUBPROTO_MODEL_CTX") or 32768)},
                   "messages": [{"role": "system", "content": system}]
                               + [{"role": t["role"], "content": t["content"]} for t in (history or []) if t.get("content")]
                               + [{"role": "user", "content": user}]}
        if not os.environ.get("HUBPROTO_MODEL_THINK"):
            payload["think"] = False  # a thinking model otherwise spends its budget before it answers
        data = None
        for attempt in (payload, {k: v for k, v in payload.items() if k != "think"}):
            body = json.dumps(attempt).encode("utf-8")
            req = urllib.request.Request(ollama_url() + "/api/chat", data=body, method="POST", headers={"content-type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=float(os.environ.get("HUBPROTO_MODEL_TIMEOUT") or 180)) as r:
                    data = json.load(r)
                break
            except urllib.error.HTTPError as e:
                detail = e.read()[:300].decode("utf-8", "replace")
                if "think" in detail.lower() and "think" in attempt:
                    continue  # this model does not take the flag; ask again without it
                raise RuntimeError(f"HTTP {e.code} from Ollama for model {name!r}: {detail}") from None
        msg = (data or {}).get("message") or {}
        self._last_usage = {"prompt_tokens": (data or {}).get("prompt_eval_count"), "output_tokens": (data or {}).get("eval_count"),
                            "done_reason": (data or {}).get("done_reason"), "served_model": name}
        return msg.get("content") or (data or {}).get("response") or msg.get("thinking") or ""

    # ------------------------------------------------------------ tool use (the record tools, MCP-shaped)
    def complete_with_tools(self, *, model: str, system: str, user: str, tools: list[dict], call_tool, by: str = "",
                            history: list[dict] | None = None, max_tokens: int = 800, max_rounds: int = 6) -> dict:
        """A turn in which the model may call tools (function calling) before it answers. ``call_tool(name, args)``
        runs a tool and returns a dict. Returns complete()'s shape plus ``tool_calls`` (what was looked at)."""
        from .tools import as_text

        t0 = time.monotonic()
        model = model or DEFAULT_MODEL
        backend = backend_for(model)
        calls: list[dict] = []

        def run(name, args):
            res = call_tool(name, args or {})
            calls.append({"tool": name, "arguments": args or {}, "ok": "error" not in res})
            return res

        usage = None
        try:
            if backend == "ollama":
                text, usage = self._ollama_tools(model, system, user, history, tools, run, max_tokens, max_rounds, as_text)
            elif backend == "anthropic":
                text = self._anthropic_tools(model, system, user, history, tools, run, max_tokens, max_rounds, as_text)
            else:
                text = self._stand_in_tools(user, run)
        except Exception as e:
            text = f"[{backend} model {model} did not answer: {e}]\n" + self._stand_in_tools(user, run)
            backend = "stand-in"
        out = {"text": text, "model": model, "served_model": (usage or {}).get("served_model") or (model if backend == "anthropic" else "stand-in"),
               "backend": backend, "stand_in": backend == "stand-in", "by": by, "at": utcnow(), "prompt_chars": len(system) + len(user),
               "usage": usage, "tool_calls": calls, "prompt_digest": sha256_of({"system": system, "user": user})[:16],
               "response_digest": sha256_of(text)[:16], "ms": int((time.monotonic() - t0) * 1000)}
        self.calls.append(out)
        u = usage or {}
        sys.stdout.write(f"model call by={by or '-'} model={model} served_by={out['served_model']} backend={backend} tools={len(calls)}"
                         f" [{', '.join(c['tool'] for c in calls)}] prompt_chars={out['prompt_chars']}"
                         + (f" prompt_tokens={u.get('prompt_tokens')} output_tokens={u.get('output_tokens')}" if u else "") + f" ms={out['ms']}\n")
        sys.stdout.flush()
        return out

    def _ollama_tools(self, model, system, user, history, tools, run, max_tokens, max_rounds, as_text):
        name = model.split("/", 1)[1] if model.startswith("ollama/") else (os.environ.get("HUBPROTO_OLLAMA_MODEL") or model)
        if model.startswith("claude"):
            name = os.environ.get("HUBPROTO_OLLAMA_MODEL") or "llama3.1"
        msgs = [{"role": "system", "content": system}] + [{"role": t["role"], "content": t["content"]} for t in (history or []) if t.get("content")] \
            + [{"role": "user", "content": user}]
        fns = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in tools]
        usage = {"prompt_tokens": 0, "output_tokens": 0, "served_model": name}
        for round_ in range(max_rounds + 1):
            payload = {"model": name, "stream": False, "messages": msgs, "tools": fns if round_ < max_rounds else [],
                       "options": {"num_predict": max_tokens, "num_ctx": int(os.environ.get("HUBPROTO_MODEL_CTX") or 32768)}}
            if not os.environ.get("HUBPROTO_MODEL_THINK"):
                payload["think"] = False
            req = urllib.request.Request(ollama_url() + "/api/chat", data=json.dumps(payload).encode("utf-8"), method="POST",
                                         headers={"content-type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=float(os.environ.get("HUBPROTO_MODEL_TIMEOUT") or 240)) as r:
                    data = json.load(r)
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"HTTP {e.code} from Ollama for model {name!r}: {e.read()[:300].decode('utf-8', 'replace')}") from None
            usage["prompt_tokens"] += data.get("prompt_eval_count") or 0
            usage["output_tokens"] += data.get("eval_count") or 0
            msg = data.get("message") or {}
            tcs = msg.get("tool_calls") or []
            if not tcs:
                text = (msg.get("content") or msg.get("thinking") or "").strip()
                if round_ < max_rounds and PROMISE.search(text) and len(text) < 240:
                    # It said it would look but did not call a tool: once, tell it to go ahead.
                    msgs.append({"role": "assistant", "content": text})
                    msgs.append({"role": "user", "content": "Go ahead: call the tools now, then answer with what you found."})
                    continue
                return text, usage
            msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tcs})
            for tc in tcs:
                fn = tc.get("function") or {}
                res = run(fn.get("name") or "", fn.get("arguments") or {})
                msgs.append({"role": "tool", "content": as_text(res), "tool_name": fn.get("name") or ""})
        return "", usage

    @staticmethod
    def _anthropic_tools(model, system, user, history, tools, run, max_tokens, max_rounds, as_text):
        msgs = [{"role": t["role"], "content": t["content"]} for t in (history or []) if t.get("content")] + [{"role": "user", "content": user}]
        specs = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
        for round_ in range(max_rounds + 1):
            body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": msgs}
            if round_ < max_rounds:
                body["tools"] = specs
            req = urllib.request.Request(ANTHROPIC_URL, data=json.dumps(body).encode("utf-8"), method="POST", headers={
                "content-type": "application/json", "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.load(r)
            content = data.get("content") or []
            uses = [b for b in content if b.get("type") == "tool_use"]
            if not uses:
                return "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
            msgs.append({"role": "assistant", "content": content})
            msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": b["id"], "content": as_text(run(b["name"], b.get("input") or {}))}
                                                     for b in uses]})
        return ""

    @staticmethod
    def _stand_in_tools(user: str, run) -> str:
        """Without a model: look up any record numbers mentioned, else search for the words; say what was found."""
        import re as _re

        numbers = _re.findall(r"\b[A-Z]{2,}-\d[\w-]*\b", user or "")
        lines = ["(No model is configured, so I can only look things up, not reason about them.)"]
        if numbers:
            for n in numbers[:3]:
                r = run("get_record", {"number": n})
                if "error" in r:
                    lines.append(f"{n}: {r['error']}.")
                else:
                    wait = f", waiting for {', '.join(r['waiting_for'])}" if r.get("waiting_for") else ""
                    lines.append(f"{r['number']} ({r['type']}) {r['title']}: {r['state']}{wait}.")
            return "\n".join(lines)
        low = (user or "").lower()
        if any(w in low for w in ("waiting", "my queue", "to do", "todo", "on my plate")):
            r = run("waiting_for", {})
            lines.append(f"{r['count']} item(s) wait for {r['who']}: " + "; ".join(f"{i['record']} ({i['step']})" for i in r["items"][:8]) + ".")
            return "\n".join(lines)
        words = [w for w in _re.findall(r"[a-zA-Z0-9]{4,}", low) if w not in {"what", "which", "where", "when", "about", "there", "have", "does", "with", "that", "this", "please", "could", "would", "find", "show", "tell", "know"}][:3]
        r = run("search_records", {"query": " ".join(words), "limit": 5})
        lines.append(f"Records matching '{' '.join(words)}': " + ("; ".join(f"{x['number']} {x['title']} ({x['state']})" for x in r["records"]) or "none") + ".")
        return "\n".join(lines)

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
