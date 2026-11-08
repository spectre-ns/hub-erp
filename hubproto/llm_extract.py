"""Reading rules from SOP prose with a model, behind the same guards and approvals.

Sentences in the shapes the compiler knows (see the writing-policy-frames reference) are read exactly, as before. The
model then reads the whole SOP, with what was already read marked, and returns the other rules its prose states:
each with the line it comes from and the exact words it quotes, in the organization's own names for roles, folders,
processes and cogs. Nothing it returns is trusted on its own:

* the grounding guard checks every quote is really in the SOP, at that line;
* the schema and consistency guards check the rule is well formed and names what exists;
* each rule read by the model is shown to the reviewers as read by the model, with its quote;
* every change still needs its approvals (two policy owners for a widening) before a release.

The model is a local one (the largest Ollama serves here, or HUBPROTO_EXTRACTOR_MODEL); the hub talks to hosted
models only when they are switched on. Reading runs in background jobs (the compile-frame automation, the command
line), never inside a page: a page or preview uses what was read before. What the model read is cached by the SOP's exact text, the names it was given and the model: compiling the same text
again gives the same rules, so a release never changes because the model answered differently. A sentence that looks
like a rule and that neither the compiler nor the model could read still stops the release.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .frames import Frame
from .rules import CONDITION_VOCABULARY, KINDS, rule_key, validate_rule

PROMPT_VERSION = "1"
MODEL_KINDS = ("permission", "threshold", "routing", "sla", "approval_criteria", "sod")


def local_model() -> str:
    """The model that reads SOPs: HUBPROTO_EXTRACTOR_MODEL, else the largest model Ollama serves here. Never a hosted one
    unless hosted models are switched on."""
    from .model import hosted_allowed, ollama_models

    named = (os.environ.get("HUBPROTO_EXTRACTOR_MODEL") or "").strip()
    if named and (not named.startswith("claude") or hosted_allowed()):
        return named
    models, _ = ollama_models()
    models = sorted(models or [], key=lambda m: -(m.get("size_gb") or 0))
    return f"ollama/{models[0]['name']}" if models else ""

SYSTEM = """You read a company's standard operating procedure (SOP) and list the rules it states, as data, so a hub can
enforce them. You return JSON only. You never invent a rule the text does not state, and you quote the exact words."""

PROMPT = """SOP {name} (version {version}). Its lines, numbered as in the file:

{numbered}

Rules the hub has ALREADY read from it (do not return these again):
{already}

Return every OTHER rule this SOP states, of these kinds only (fields exactly as shown):
- permission: {{"subject": "role:<role>", "action": "<read|create|update|approve|assign|delete>", "object": "<singular record type, or *>",
  "resource": "folder:<folder path>", "effect": "allow|deny"}}
- threshold: {{"name": "<slug>", "value": <number>, "unit": "<USD|EUR|days|percent|...>"}}
- routing: {{"process": "<process>", "step": "<approve|step id|*>", "condition": null or {{"field": "<amount|risk|doc_type|change_type|overdue>",
  "op": "<>|<|>=|<=|==>", "value": <value>, "currency": "<code, for amounts>"}}, "assignee": "<role:x|cog:x|field:x>", "mode": "assign|inform"}}
- sla: {{"process": "<process>", "step": "<approve|step id>", "days": <integer>}}
- approval_criteria: {{"approver": "cog:<cog>", "process": "<process>", "max_amount": <number or null>, "currency": "<code or null>",
  "conditions": [<keys from: {vocab}>], "countersign": "<role or null>"}}
- sod: {{"object": "<record type>", "roles": ["<role a>", "<role b>"]}}

Use only these names (a rule that needs another name is not a rule you can return):
roles: {roles}
folders: {folders}
processes and their steps: {processes}
cogs: {cogs}
record types: {types}

Answer with a JSON object: {{"rules": [{{"kind": "...", "fields": {{...}}, "line": <line number>, "quote": "<the exact words of that line
the rule comes from>"}}]}}. An empty list is a fine answer when the SOP states no other rule."""


def _key(frame: Frame, context: dict, model: str, member: str = "") -> str:
    blob = json.dumps({"text": frame.text, "context": context, "model": model, "member": member, "v": PROMPT_VERSION}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def _json_of(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    return json.loads(t[start:end + 1]) if start != -1 and end > start else {"rules": []}


def gateway_call(gateway):
    """A call through the hub's model gateway (the local models the cogs use, at the model gate)."""
    def call(model: str, system: str, user: str) -> str:
        out = gateway.complete(model=model, system=system, user=user, max_tokens=4000, by="sop-reader")
        if out.get("stand_in"):
            raise RuntimeError(f"{model} is not available here (a stand-in answered)")
        return out.get("text") or ""
    return call


class ModelExtractor:
    """The compiler's sentences first, then the model for the prose (see the module docstring)."""

    name = "hybrid"

    def __init__(self, cache_dir: Path | None = None, context: dict | None = None, model: str | None = None, call=None,
                 gateway=None, calls: bool = True, member: str = ""):
        self.member = member  # a committee member reads on its own: its reading is its own (cached apart)
        self.model = model or local_model()
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.context = context or {}
        self.call = call or (gateway_call(gateway) if gateway is not None else None)
        self.allow_calls = calls  # False: only what was read before (from the cache); pages never wait on the model
        self.calls = 0

    def _read(self, frame: Frame, already: list[dict]) -> dict:
        key = _key(frame, self.context, self.model, self.member)
        path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if path is not None and path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        if not self.allow_calls or self.call is None or not self.model:
            raise LookupError("not read by the model yet: the compile-frame job reads it")
        numbered = "\n".join(f"{i:4d}: {line}" for i, line in enumerate(frame.lines, start=frame.body_start))
        from .settings import sentence

        said = []
        for r in already:
            try:
                said.append(f"- line {r['source']['line']}: {sentence(r, self.context.get('process_defs') or {})}")
            except Exception:
                said.append(f"- line {r['source']['line']}: {r['kind']}")
        c = self.context
        prompt = PROMPT.format(name=frame.name, version=frame.version, numbered=numbered, already="\n".join(said) or "- (nothing)",
                               vocab=", ".join(sorted(set(CONDITION_VOCABULARY.values()))), roles=", ".join(c.get("roles") or []),
                               folders=", ".join(c.get("folders") or []), processes="; ".join(f"{p} ({', '.join(s)})" for p, s in (c.get("processes") or {}).items()),
                               cogs=", ".join(c.get("cogs") or []), types=", ".join(c.get("types") or []))
        self.calls += 1
        system = SYSTEM + (f"\nYou are the {self.member} cog, one member of a committee that reads this SOP; read it on your own." if self.member else "")
        data = _json_of(self.call(self.model, system, prompt))
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        return data

    def extract(self, frame: Frame):
        from .extract import ConventionExtractor, Extraction

        conv = ConventionExtractor().extract(frame)
        if frame.scope in ("reference", "persona") or frame.name == "scopes":
            return conv  # structured sections only: nothing in prose to read
        try:
            data = self._read(frame, conv.rules)
        except LookupError as e:  # not read yet (a page, a preview): the compiler's reading stands meanwhile
            conv.notes.append({"frame": frame.name, "line": 0, "text": "", "severity": "info", "note": str(e)})
            return conv
        except Exception as e:  # no model: the compiler's reading stands, and unread rule-like sentences still stop a release
            conv.notes.append({"frame": frame.name, "line": 0, "text": "", "severity": "warning",
                               "note": f"the model could not read the prose of {frame.name} ({type(e).__name__}: {str(e)[:160]})"})
            return conv
        have = {rule_key(r) for r in conv.rules}
        lines = {i: line for i, line in enumerate(frame.lines, start=frame.body_start)}
        out = Extraction(rules=list(conv.rules), notes=[])
        covered = set()
        for item in data.get("rules") or []:
            kind = item.get("kind")
            fields = item.get("fields") or {}
            line = item.get("line")
            quote = str(item.get("quote") or "").strip()
            if kind not in MODEL_KINDS or not isinstance(fields, dict) or kind not in KINDS:
                out.notes.append({"frame": frame.name, "line": line or 0, "text": quote, "severity": "warning",
                                  "note": f"the model returned a {kind!r} rule, which it may not (only {', '.join(MODEL_KINDS)})"})
                continue
            rule = {"kind": kind, **{k: v for k, v in fields.items() if k not in ("kind", "source")},
                    "source": {"frame": frame.name, "version": frame.version, "line": line, "text": quote, "read_by": self.model}}
            if kind == "routing":
                rule.setdefault("mode", "assign")
                rule.setdefault("condition", None)
            if kind == "approval_criteria":
                rule.setdefault("conditions", [])
                rule.setdefault("max_amount", None)
                rule.setdefault("currency", None)
            if rule_key(rule) in have:
                continue
            problems = validate_rule(rule)
            if problems:
                out.notes.append({"frame": frame.name, "line": line or 0, "text": quote, "severity": "error",
                                  "note": "the model read a rule that is not well formed: " + "; ".join(problems[:3])})
                continue
            have.add(rule_key(rule))
            out.rules.append(rule)
            if isinstance(line, int):
                covered.add(line)
        for n in conv.notes:
            if n.get("severity") == "error" and n.get("line") in covered:
                continue  # the compiler could not read this sentence; the model did, and the reviewers see what it read
            out.notes.append(n)
        return out


def context_for(hub) -> dict:
    """The names the model may use: from the release in force and the record types."""
    meta = hub.releases.current()
    b = hub.releases.bundle(meta["digest"]) if meta else {}
    procs = b.get("processes") or {}
    return {"roles": sorted(b.get("roles") or []), "folders": sorted(b.get("folders") or []),
            "processes": {n: [s.get("id") for s in p.get("steps") or []] for n, p in sorted(procs.items())},
            "process_defs": procs,
            "cogs": sorted(n for n, m in (b.get("members") or {}).items() if m.get("type") == "cog"),
            "types": sorted(t for t in hub.kernel.types if not t.startswith("$"))}


class CommitteeExtractor:
    """The compile-frame committee: each member cog reads the prose on its own (its own model, its own cached reading);
    a rule goes in when at least ``quorum`` members read it the same way. Rules fewer members read are shown to the
    reviewers as disputed, with who read them, and left out."""

    name = "committee"

    def __init__(self, members: list[tuple[str, str]], quorum: int, cache_dir: Path | None = None, context: dict | None = None,
                 gateway=None, calls: bool = True, call=None):
        self.members = [(n, m) for n, m in members if m]
        self.quorum = max(1, int(quorum or 1))
        self.readers = [ModelExtractor(cache_dir=cache_dir, context=context, model=m, gateway=gateway, calls=calls, call=call, member=n)
                        for n, m in self.members]
        self.model = ", ".join(f"{n} ({m})" for n, m in self.members)

    def extract(self, frame: Frame):
        from .extract import ConventionExtractor, Extraction

        conv = ConventionExtractor().extract(frame)
        if frame.scope in ("reference", "persona") or frame.name == "scopes":
            return conv
        if len(self.readers) < self.quorum:
            conv.notes.append({"frame": frame.name, "line": 0, "text": "", "severity": "warning",
                               "note": f"the compile-frame committee has {len(self.readers)} member(s) with a model here, fewer than the "
                                       f"{self.quorum} that must agree: the prose of {frame.name} is not read"})
            return conv
        votes: dict[str, list[str]] = {}
        first: dict[str, dict] = {}
        notes: list[dict] = []
        for (name, _model), reader in zip(self.members, self.readers):
            out = reader.extract(frame)
            for r in out.rules:
                if not (r.get("source") or {}).get("read_by"):
                    continue
                k = rule_key(r)
                votes.setdefault(k, []).append(name)
                first.setdefault(k, r)
            notes += [n for n in out.notes if n.get("severity") in ("warning", "info") and "the model" in n.get("note", "") or n.get("severity") == "info"]
        out = Extraction(rules=list(conv.rules), notes=[])
        covered = set()
        n = len(self.readers)
        for k, who in votes.items():
            r = first[k]
            if len(who) >= self.quorum:
                rule = {**r, "source": {**r["source"], "read_by": f"the compile-frame committee ({', '.join(who)}: {len(who)} of {n} agree)"}}
                out.rules.append(rule)
                if isinstance(r["source"].get("line"), int):
                    covered.add(r["source"]["line"])
            else:
                out.notes.append({"frame": frame.name, "line": r["source"].get("line") or 0, "text": r["source"].get("text") or "", "severity": "warning",
                                  "note": f"disputed, left out: only {', '.join(who)} ({len(who)} of {n}; {self.quorum} must agree) read a {r['kind']} rule"})
        for note in conv.notes:
            if note.get("severity") == "error" and note.get("line") in covered:
                continue
            out.notes.append(note)
        seen = set()
        for note in notes:  # each member's own problems (no model, not read yet), once
            key = note.get("note")
            if key not in seen:
                seen.add(key)
                out.notes.append(note)
        return out

