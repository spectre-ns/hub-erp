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
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

from .release import utcnow
from .rules import sha256_of

DEFAULT_MODEL = "stand-in"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# ---------------------------------------------------------------- letting go of the hub lock while a model thinks
_held = threading.local()


@contextmanager
def thinking_releases(lock):
    """While a cog works under the hub lock (the queue worker), every wait on a model gives the lock up, so pages and
    saves are served meanwhile; the cog takes the lock back before it reads or writes anything."""
    prev = getattr(_held, "lock", None)
    _held.lock = lock
    try:
        yield
    finally:
        _held.lock = prev


def _fetch_json(req, timeout: float):
    lock = getattr(_held, "lock", None)
    saved = None
    if lock is not None and getattr(lock, "_is_owned", lambda: False)():
        saved = lock._release_save()  # all the way out of the re-entrant lock
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    finally:
        if saved is not None:
            lock._acquire_restore(saved)

PROMISE = __import__("re").compile(r"\b(let me (check|look|search|find|see)|i'?ll (check|look|search|find)|checking|looking (that|it) up|one moment)\b", __import__("re").I)


def ollama_url() -> str:
    """Where a local Ollama answers (HUBPROTO_OLLAMA_URL, e.g. http://localhost:11434); empty when not configured."""
    return (os.environ.get("HUBPROTO_OLLAMA_URL") or "").rstrip("/")


class ModelUnavailable(Exception):
    """A cog's model is not reachable: the cog is not available (a cog is bundled with its model; nothing stands in)."""


def stand_in_allowed() -> bool:
    """Development and tests only: a deterministic stand-in answers for any model. Never in a real hub."""
    return (os.environ.get("HUBPROTO_ALLOW_STAND_IN") or "").lower() in ("1", "true", "yes", "on")


def backend_for(model: str) -> str:
    """Which backend serves a profile's model: 'anthropic' for claude-* with a key, 'ollama' for 'ollama/<name>' or a
    bare name with an Ollama configured, 'stand-in' when allowed, else 'none'. A claude-* model is never served locally."""
    if os.environ.get("HUBPROTO_MODEL_FAKE"):
        return "stand-in"
    if not model or model == DEFAULT_MODEL:
        return "stand-in" if stand_in_allowed() else "none"
    if model.startswith("claude"):
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic"
    elif ollama_url():
        return "ollama"
    return "stand-in" if stand_in_allowed() else "none"


CLAUDE_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
_TAGS: dict = {"at": 0.0, "models": None, "error": ""}


def served_name(model: str) -> str:
    """The Ollama model a profile names: ``ollama/<name>`` or a bare ``<name>``."""
    return model.split("/", 1)[1] if model.startswith("ollama/") else model


_CTX: dict[str, int] = {}


def context_length(model: str) -> int:
    """The longest context the model was trained for, as Ollama reports it (qwen3:14b: 40,960 tokens)."""
    backend = backend_for(model)
    if backend == "anthropic":
        return 200000
    if backend != "ollama":
        return 32768
    name = served_name(model)
    if name not in _CTX:
        n = 32768
        try:
            req = urllib.request.Request(ollama_url() + "/api/show", data=json.dumps({"model": name}).encode("utf-8"), method="POST",
                                         headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=4) as r:
                info = json.load(r).get("model_info") or {}
            found = [int(v) for k, v in info.items() if k.endswith(".context_length") and str(v).isdigit()]
            if found:
                n = found[0]
        except Exception:
            pass
        _CTX[name] = n
    return _CTX[name]


def num_ctx_for(model: str) -> int:
    """The window each call asks for: the model's own length, capped (HUBPROTO_MODEL_CTX_MAX, default 65536) or set
    (HUBPROTO_MODEL_CTX); never more than the model was trained for. One value per model, so Ollama does not reload."""
    native = context_length(model)
    want = int(os.environ.get("HUBPROTO_MODEL_CTX") or 0) or min(native, int(os.environ.get("HUBPROTO_MODEL_CTX_MAX") or 65536))
    return max(2048, min(want, native))


def prompt_budget_chars(model: str, max_tokens: int = 1200) -> int:
    """How many characters of prompt fit the window with room for the answer (about 3.2 characters a token for
    English with markup), unless HUBPROTO_MODEL_MAX_CHARS says otherwise."""
    if os.environ.get("HUBPROTO_MODEL_MAX_CHARS"):
        return int(os.environ["HUBPROTO_MODEL_MAX_CHARS"])
    return int(max(4096, num_ctx_for(model) - max_tokens - 1024) * 3.2)


def ollama_models(refresh: bool = False) -> tuple[list[dict], str]:
    """The models installed in Ollama (cached for a minute): [{name, params, family, quant, size_gb}], and an error."""
    url = ollama_url()
    if not url:
        return [], "no Ollama is configured (HUBPROTO_OLLAMA_URL)"
    if not refresh and _TAGS["models"] is not None and time.monotonic() - _TAGS["at"] < 60:
        return _TAGS["models"], _TAGS["error"]
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=4) as r:
            data = json.load(r)
        models = [{"name": m["name"], "params": (m.get("details") or {}).get("parameter_size") or "", "family": (m.get("details") or {}).get("family") or "",
                   "quant": (m.get("details") or {}).get("quantization_level") or "", "size_gb": round((m.get("size") or 0) / 1e9, 1)}
                  for m in data.get("models") or []]
        _TAGS.update(at=time.monotonic(), models=sorted(models, key=lambda m: m["name"]), error="")
    except Exception as e:
        _TAGS.update(at=time.monotonic(), models=[], error=f"Ollama at {url} did not answer: {e}")
    return _TAGS["models"], _TAGS["error"]


def _installed(name: str, models: list[dict]) -> bool:
    names = {m["name"] for m in models}
    return name in names or f"{name}:latest" in names or name.removesuffix(":latest") in names


def available_models(refresh: bool = False) -> dict:
    """What a cog's profile can name: installed Ollama models, Claude models when a key is set, and what a claude-*
    name falls back to without one."""
    local, err = ollama_models(refresh)
    key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    local = sorted(local, key=lambda m: -(m.get("size_gb") or 0))  # the most capable (largest) installed model first
    choices = [{"value": f"ollama/{m['name']}", "label": f"{m['name']} ({m['params']} {m['family']}, local)".replace("( ", "("), "available": True} for m in local]
    choices += [{"value": c, "label": c + (" (Anthropic)" if key else " (needs an Anthropic key: not available here)"), "available": key} for c in CLAUDE_MODELS]
    return {"ollama": local, "ollama_error": err, "anthropic_key": key, "choices": choices}


def resolve_model(model: str, refresh: bool = False) -> dict:
    """Who would answer for a profile's model right now, and whether that model is there."""
    model = model or DEFAULT_MODEL
    backend = backend_for(model)
    if backend == "ollama":
        name = served_name(model)
        local, err = ollama_models(refresh)
        ok = _installed(name, local)
        return {"asked": model, "backend": "ollama", "served": name, "available": ok,
                "note": "local" if ok else (err or f"{name} is not installed in Ollama (ollama pull {name})")}
    if backend == "anthropic":
        return {"asked": model, "backend": "anthropic", "served": model, "available": True, "note": "Anthropic API"}
    if backend == "stand-in":
        return {"asked": model, "backend": "stand-in", "served": "stand-in", "available": True, "note": "development stand-in (HUBPROTO_ALLOW_STAND_IN)"}
    if not model or model == DEFAULT_MODEL:
        why = "the profile names no model"
    elif model.startswith("claude"):
        why = f"{model} needs an Anthropic key (ANTHROPIC_API_KEY)"
    else:
        why = f"{model} needs an Ollama (HUBPROTO_OLLAMA_URL)"
    return {"asked": model, "backend": "none", "served": "", "available": False, "note": why}


def cog_availability(bundle: dict, cog: str) -> tuple[bool, str]:
    """Whether a cog can be used now: it is bundled with its profile's model, and that model must answer."""
    prof = (bundle.get("cogs") or {}).get(cog)
    if prof is None:
        return (True, "") if stand_in_allowed() else (False, f"{cog} has no profile naming its model")
    r = resolve_model(prof.get("model") or "")
    return (True, "") if r["available"] else (False, f"{cog} is not available: {r['note']}")


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
        if backend == "none":
            raise ModelUnavailable(resolve_model(model)["note"])
        try:
            if backend == "anthropic":
                text = self._anthropic(model, system, user, max_tokens, history)
            elif backend == "ollama":
                text = self._ollama(model, system, user, max_tokens, history)
            else:
                text = self._stand_in(system, user)
        except Exception as e:
            if not stand_in_allowed():
                raise ModelUnavailable(f"{model} did not answer: {e}") from None
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
        data = _fetch_json(req, 60)
        return "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")

    def _ollama(self, model: str, system: str, user: str, max_tokens: int, history: list[dict] | None = None) -> str:
        """A local model through Ollama's chat API (``ollama/<name>`` or any name when HUBPROTO_OLLAMA_URL is set)."""
        name = served_name(model)
        ceiling = prompt_budget_chars(model, max_tokens)  # what fits in the model's window with room to answer
        if len(system) + len(user) > ceiling:
            keep = max(ceiling - len(user) - 200, 2000)
            system = system[:keep] + "\n[... context trimmed to fit the model's window]"
        payload = {"model": name, "stream": False,
                   "options": {"num_predict": max_tokens, "num_ctx": num_ctx_for(model)},
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
                data = _fetch_json(req, float(os.environ.get("HUBPROTO_MODEL_TIMEOUT") or 180))
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
        if backend == "none":
            raise ModelUnavailable(resolve_model(model)["note"])
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
            if not stand_in_allowed():
                raise ModelUnavailable(f"{model} did not answer: {e}") from None
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
        name = served_name(model)
        msgs = [{"role": "system", "content": system}] + [{"role": t["role"], "content": t["content"]} for t in (history or []) if t.get("content")] \
            + [{"role": "user", "content": user}]
        fns = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in tools]
        usage = {"prompt_tokens": 0, "output_tokens": 0, "served_model": name}
        for round_ in range(max_rounds + 1):
            payload = {"model": name, "stream": False, "messages": msgs, "tools": fns if round_ < max_rounds else [],
                       "options": {"num_predict": max_tokens, "num_ctx": num_ctx_for(model)}}
            if not os.environ.get("HUBPROTO_MODEL_THINK"):
                payload["think"] = False
            req = urllib.request.Request(ollama_url() + "/api/chat", data=json.dumps(payload).encode("utf-8"), method="POST",
                                         headers={"content-type": "application/json"})
            try:
                data = _fetch_json(req, float(os.environ.get("HUBPROTO_MODEL_TIMEOUT") or 240))
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
            data = _fetch_json(req, 120)
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
