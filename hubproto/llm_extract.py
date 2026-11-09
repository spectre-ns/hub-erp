"""Reading rules from SOP prose with a model, behind the same guards and approvals.

Sentences in the shapes the compiler knows (see the writing-policy-frames reference) are read exactly, as before. The
model reads the prose for the other rules it states: each with the line it comes from and the exact words it quotes, in
the organization's own names for roles, folders, processes and cogs. Nothing it returns is trusted on its own:

* the grounding guard checks every quote is really in the SOP, at that line;
* the schema and consistency guards check the rule is well formed and names what exists;
* each rule read by the model is shown to the reviewers as read by the model, with its quote;
* every change still needs its approvals (two policy owners for a widening) before a release.

Reading is **diff-based** (change control v2, section 4). An SOP whose text is the one the release in force was compiled
from keeps the rules that release read from its prose, with no model call. An SOP whose text differs is read as the
change it is: the reader gets the unified diff with line numbers and the rules in force from that SOP (and those of the
processes it touches), and answers with rules to add, remove or change (each with its line and exact quote) and
**questions** where the changed prose is ambiguous. A rule in force on an unchanged line keeps its reading (moved to its
new line); one on a line that changed and that no reader restated or removed is left out, with a question. A large
diff is read in parts, section by section, so each call fits the model's window (``prompt_budget_chars``). An SOP with
no version in force (a new SOP) is read whole, as before.

The model is a local one (the largest Ollama serves here, or HUBPROTO_EXTRACTOR_MODEL); the hub talks to hosted
models only when they are switched on (HUBPROTO_COMPILE_HOSTED for compilation alone: Claude Opus reads the prose,
cogs keep their models). Reading runs in background jobs (the compile-frame automation, the command line), never
inside a page: a page or preview uses what was read before. What the model read is cached by the exact text it was
given and the model: compiling the same text again gives the same rules, so a release never changes because the model
answered differently. A sentence that looks like a rule and that neither the compiler nor the model could read still
stops the release.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .frames import Frame, load_frame_text
from .rules import CONDITION_VOCABULARY, KINDS, rule_key, validate_rule

PROMPT_VERSION = "1"
DIFF_PROMPT_VERSION = "d1"
MODEL_KINDS = ("permission", "threshold", "routing", "sla", "approval_criteria", "sod")
MAX_TOKENS = 4000  # what one reading may answer with
CONTEXT_LINES = 2  # unchanged lines shown around each change in the diff
COMPILE_HOSTED_MODEL = "claude-opus-5-5"
HOSTED_BUDGET = int((200000 - MAX_TOKENS - 1024) * 3.2)  # Claude's window, in characters, with room to answer


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


def compile_hosted() -> bool:
    """An administrator's switch for compilation only: HUBPROTO_COMPILE_HOSTED=1, with ANTHROPIC_API_KEY, has Claude Opus
    (``COMPILE_HOSTED_MODEL``) read the SOPs' prose for the compile-frame committee. Cogs keep their bundled models, and
    hosted models stay off everywhere else (HUBPROTO_HOSTED_MODELS is neither needed nor changed). Off by default."""
    on = (os.environ.get("HUBPROTO_COMPILE_HOSTED") or "").strip().lower() in ("1", "on", "yes", "true")
    return on and bool(os.environ.get("ANTHROPIC_API_KEY"))


def hosted_reader() -> dict | None:
    """How the readers reach Claude Opus when compilation is switched to it: {"model", "call", "budget"}; else None."""
    if not compile_hosted():
        return None
    from .model import ModelGateway

    def call(model: str, system: str, user: str) -> str:
        return ModelGateway._anthropic(model, system, user, MAX_TOKENS)  # the Anthropic Messages API, over the stdlib
    return {"model": COMPILE_HOSTED_MODEL, "call": call, "budget": HOSTED_BUDGET}


SYSTEM = """You read a company's standard operating procedure (SOP) and list the rules it states, as data, so a hub can
enforce them. You return JSON only. You never invent a rule the text does not state, and you quote the exact words."""

_KINDS = """- permission: {{"subject": "role:<role>", "action": "<read|create|update|approve|assign|delete>", "object": "<singular record type, or *>",
  "resource": "folder:<folder path>", "effect": "allow|deny"}}
- threshold: {{"name": "<slug>", "value": <number>, "unit": "<USD|EUR|days|percent|...>"}}
- routing: {{"process": "<process>", "step": "<approve|step id|*>", "condition": null or {{"field": "<amount|risk|doc_type|change_type|overdue>",
  "op": "<>|<|>=|<=|==>", "value": <value>, "currency": "<code, for amounts>"}}, "assignee": "<role:x|cog:x|field:x>", "mode": "assign|inform"}}
- sla: {{"process": "<process>", "step": "<approve|step id>", "days": <integer>}}
- approval_criteria: {{"approver": "cog:<cog>", "process": "<process>", "max_amount": <number or null>, "currency": "<code or null>",
  "conditions": [<keys from: {vocab}>], "countersign": "<role or null>"}}
- sod: {{"object": "<record type>", "roles": ["<role a>", "<role b>"]}}
"""

_NAMES = """Use only these names (a rule that needs another name is not a rule you can return):
roles: {roles}
folders: {folders}
processes and their steps: {processes}
cogs: {cogs}
record types: {types}
"""

PROMPT = """SOP {name} (version {version}). Its lines, numbered as in the file:

{numbered}

Rules the hub has ALREADY read from it (do not return these again):
{already}

Return every OTHER rule this SOP states, of these kinds only (fields exactly as shown):
""" + _KINDS + "\n" + _NAMES + """
Answer with a JSON object: {{"rules": [{{"kind": "...", "fields": {{...}}, "line": <line number>, "quote": "<the exact words of that line
the rule comes from>"}}]}}. An empty list is a fine answer when the SOP states no other rule."""

DIFF_SYSTEM = """You read the changes made to a company's standard operating procedure (SOP) and say what they do to the rules a
hub enforces, as data. You return JSON only. You never invent a rule the text does not state, you quote the exact words,
and where the changed prose is ambiguous you ask instead of guessing."""

DIFF_PROMPT = """SOP {name} is being revised. The changes to its text, as a unified diff ('-' a line taken out, '+' a line put in,
' ' an unchanged line around them; the number on a '-' line is its line in the version in force, every other number is
its line in the new text):

{diff}

Rules in force that were read from the prose of this SOP (ids on the left; lines of the version in force):
{in_force}

Rules the hub reads exactly from the sentences on these lines of the new text (it rereads them itself; never return these):
{exact}

Other rules in force for the processes this SOP touches (context only: they are stated elsewhere):
{others}

Say what the changes do to the rules, of these kinds only (fields exactly as shown):
""" + _KINDS + "\n" + _NAMES + """
Answer with a JSON object:
{{"changes": [{{"op": "add", "kind": "...", "fields": {{...}}, "line": <line in the new text>, "quote": "<the exact words of that line>"}},
             {{"op": "remove", "ref": "<id of a rule in force>"}},
             {{"op": "change", "ref": "<id of a rule in force>", "kind": "...", "fields": {{...}}, "line": <line in the new text>,
              "quote": "<the exact words of that line>"}}],
 "questions": [{{"line": <line in the new text>, "quote": "<the words>", "text": "<what the prose leaves open, asked as a question>"}}]}}
A rule in force whose line was taken out or rewritten is either removed or changed (restated at its new line, even when it
says the same). Say nothing about rules on unchanged lines: they stay as they are. Ask a question where a changed line is
ambiguous ("larger bills go to the controller": over what amount?) rather than guess. Empty lists are a fine answer."""


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
        out = gateway.complete(model=model, system=system, user=user, max_tokens=MAX_TOKENS, by="sop-reader")
        if out.get("stand_in"):
            raise RuntimeError(f"{model} is not available here (a stand-in answered)")
        return out.get("text") or ""
    return call


def _sentence(rule: dict, processes: dict) -> str:
    from .settings import sentence

    try:
        return sentence(rule, processes)
    except Exception:
        return f"{rule.get('kind')} " + json.dumps({k: v for k, v in rule.items() if k not in ("kind", "source")}, sort_keys=True, default=str)


def _names(context: dict) -> dict:
    c = context or {}
    return {"vocab": ", ".join(sorted(set(CONDITION_VOCABULARY.values()))), "roles": ", ".join(c.get("roles") or []),
            "folders": ", ".join(c.get("folders") or []),
            "processes": "; ".join(f"{p} ({', '.join(s)})" for p, s in (c.get("processes") or {}).items()),
            "cogs": ", ".join(c.get("cogs") or []), "types": ", ".join(c.get("types") or [])}


def _build_rule(frame: Frame, item: dict, read_by: str) -> tuple[dict | None, dict | None]:
    """A rule the model returned, in the hub's shape and cited at its line; or (None, a note) when it may not return it."""
    kind = item.get("kind")
    fields = item.get("fields") or {}
    line = item.get("line")
    quote = str(item.get("quote") or "").strip()
    if kind not in MODEL_KINDS or not isinstance(fields, dict) or kind not in KINDS:
        return None, {"frame": frame.name, "line": line or 0, "text": quote, "severity": "warning",
                      "note": f"the model returned a {kind!r} rule, which it may not (only {', '.join(MODEL_KINDS)})"}
    rule = {"kind": kind, **{k: v for k, v in fields.items() if k not in ("kind", "source")},
            "source": {"frame": frame.name, "version": frame.version, "line": line, "text": quote, "read_by": read_by}}
    if kind == "routing":
        rule.setdefault("mode", "assign")
        rule.setdefault("condition", None)
    if kind == "approval_criteria":
        rule.setdefault("conditions", [])
        rule.setdefault("max_amount", None)
        rule.setdefault("currency", None)
    return rule, None


def _malformed(frame: Frame, rule: dict) -> dict | None:
    problems = validate_rule(rule)
    if not problems:
        return None
    src = rule.get("source") or {}
    return {"frame": frame.name, "line": src.get("line") or 0, "text": src.get("text") or "", "severity": "error",
            "note": "the model read a rule that is not well formed: " + "; ".join(problems[:3])}


def _copy(conv):
    """A copy of the compiler's reading for one committee member (each member's notes are its own)."""
    from .extract import Extraction

    return Extraction(rules=list(conv.rules), notes=list(conv.notes))


# ---------------------------------------------------------------- the diff against the version in force
@dataclass
class DiffPlan:
    """What a reader is given for an SOP that differs from its version in force, and what stays without a reading."""
    old: Frame
    new: Frame
    changed: bool  # the body differs (a new version number alone is no change to read)
    hunks: list[dict]  # {"section", "lines": [(tag, old line, new line, text)]}
    in_force: list[tuple[str, dict]]  # (id, rule): the rules in force read from this SOP's prose
    kept: list[dict]  # those on unchanged lines, cited at their line in the new text
    orphans: list[tuple[str, dict, int]]  # (id, rule, a line near where it was): on lines that changed
    others: list[dict]  # rules in force from other SOPs about the processes this one touches
    section_of: dict[str, str] = field(default_factory=dict)  # rule id -> the section it is in now


def _section_at(frame: Frame, line: int | None) -> str:
    lines = frame.lines
    i = (line or frame.body_start) - frame.body_start
    for k in range(min(i, len(lines) - 1), -1, -1):
        if lines[k].startswith("#"):
            return lines[k].strip()
    return ""


def _is_prose_rule(rule: dict, name: str) -> bool:
    src = rule.get("source") or {}
    return src.get("frame") == name and bool(src.get("read_by"))


def diff_plan(old_text: str, frame: Frame, base_rules: list[dict]) -> DiffPlan:
    """The diff of ``frame`` against the text in force (``old_text``), and the in-force rules it keeps or puts in doubt."""
    old = load_frame_text(old_text, frame.path)
    a, b = old.lines, frame.lines
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    line_map: dict[int, int] = {}
    near: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        for k in range(i2 - i1):
            o = old.body_start + i1 + k
            if tag == "equal":
                line_map[o] = frame.body_start + j1 + k
            else:
                near[o] = frame.body_start + min(j1 + k, max(j1, j2 - 1))
    hunks = []
    for group in sm.get_grouped_opcodes(CONTEXT_LINES):
        lines: list[tuple] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines += [(" ", old.body_start + i1 + k, frame.body_start + j1 + k, b[j1 + k]) for k in range(i2 - i1)]
                continue
            lines += [("-", old.body_start + i, None, a[i]) for i in range(i1, i2)]
            lines += [("+", None, frame.body_start + j, b[j]) for j in range(j1, j2)]
        first = next((ln[2] for ln in lines if ln[0] != " " and ln[2] is not None), None) or (frame.body_start + group[0][3])
        hunks.append({"section": _section_at(frame, first), "lines": lines})
    prose = sorted((r for r in base_rules if _is_prose_rule(r, frame.name)), key=lambda r: int((r.get("source") or {}).get("line") or 0))
    in_force = [(f"r{i}", r) for i, r in enumerate(prose, start=1)]
    kept, orphans, section_of = [], [], {}
    for ref, r in in_force:
        ln = int(r["source"].get("line") or 0)
        if ln in line_map:
            kept.append({**r, "source": {**r["source"], "line": line_map[ln], "version": frame.version}})
            section_of[ref] = _section_at(frame, line_map[ln])
        else:
            at = near.get(ln, frame.body_start)
            orphans.append((ref, r, at))
            section_of[ref] = _section_at(frame, at)
    mine = [r for r in base_rules if (r.get("source") or {}).get("frame") == frame.name]
    touched = {r.get("process") for r in mine if r.get("process")} | {r.get("name") for r in mine if r.get("kind") == "process"}
    others = [r for r in base_rules if (r.get("source") or {}).get("frame") != frame.name and r.get("kind") in MODEL_KINDS
              and r.get("process") in touched]
    return DiffPlan(old=old, new=frame, changed=a != b, hunks=hunks, in_force=in_force, kept=kept, orphans=orphans,
                    others=others, section_of=section_of)


def render_hunk(hunk: dict) -> str:
    out = [f"@@ {hunk['section'] or '(the top of the SOP)'} @@"]
    for tag, o, n, text in hunk["lines"]:
        out.append(f"{tag}{(o if tag == '-' else n):5d}: {text}")
    return "\n".join(out)


@dataclass
class Reading:
    """One reader's answer about a diff: rules to add (changes included), keys of rules in force it removes, questions."""
    adds: list[dict] = field(default_factory=list)
    removes: set[str] = field(default_factory=set)
    questions: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)


def _assemble(frame: Frame, conv, plan: DiffPlan, adds: list[dict], removes: set[str], questions: list[dict],
              notes: list[dict], read: bool, processes: dict | None = None):
    """The SOP's rules after a diff reading: its sentences as the compiler reads them, the in-force readings on unchanged
    lines (unless removed), what the readers added or changed. A reading in force on a changed line that nobody restated
    or removed is left out, and asked about."""
    from .compile import question
    from .extract import Extraction

    out = Extraction(rules=list(conv.rules), notes=[], questions=[])
    have = {rule_key(r) for r in conv.rules}
    covered: set[int] = set()
    for r in plan.kept:
        k = rule_key(r)
        if k in removes:
            continue
        covered.add(r["source"]["line"])
        if k not in have:
            have.add(k)
            out.rules.append(r)
    for r in adds:
        k = rule_key(r)
        if isinstance(r["source"].get("line"), int):
            covered.add(r["source"]["line"])
        if k not in have:
            have.add(k)
            out.rules.append(r)
    asked = list(questions)
    notes = list(notes)
    for _ref, r, at in plan.orphans:
        k = rule_key(r)
        if k in removes or k in have:
            continue
        said = _sentence(r, processes or {})
        if read:
            asked.append(question(frame.name, at, f"The line the rule “{said}” was read from (line {r['source'].get('line')} of the "
                                  "version in force) changed, and the readers neither restated nor removed it, so it is left "
                                  "out. Does it still apply?", raised_by="hub", code="unread", quote=r["source"].get("text") or ""))
        notes.append({"frame": frame.name, "line": at, "text": r["source"].get("text") or "", "severity": "warning",
                      "note": f"left out: “{said}” was read from a line that changed" + ("" if read else ", and the change is not read yet")})
    for n in conv.notes:
        if n.get("severity") == "error" and n.get("line") in covered:
            continue  # the compiler could not read this sentence; the model did, and the reviewers see what it read
        out.notes.append(n)
    seen = set()
    for n in notes:
        key = (n.get("line"), n.get("note"))
        if key not in seen:
            seen.add(key)
            out.notes.append(n)
    by_id: dict[str, dict] = {}
    for q in asked:
        if q["id"] in by_id:  # two members asked the same: one question, raised by both
            prior = by_id[q["id"]]
            names = [x for x in (prior["raised_by"] + ", " + q["raised_by"]).split(", ") if x]
            prior["raised_by"] = ", ".join(dict.fromkeys(names))
        else:
            by_id[q["id"]] = dict(q)
    out.questions = list(by_id.values())
    return out


class _Baseline:
    """The release in force, as a reader needs it: each SOP's text and the rules (resolved on first use)."""

    def __init__(self, source):
        self.source = source
        self._value: dict | None = None

    def get(self) -> dict:
        if self._value is None:
            src = self.source
            self._value = (src() if callable(src) else src) or {}
        return self._value


def baseline_for(hub) -> dict:
    """The release in force for diff reading: {"digest", "texts": {SOP: text}, "rules": [...]}."""
    meta = hub.releases.current()
    if not meta:
        return {"digest": None, "texts": {}, "rules": []}
    return {"digest": meta["digest"], "texts": hub.releases.frame_texts(meta["digest"]), "rules": hub.releases.rules(meta["digest"])}


def plan_for(baseline: dict | None, frame: Frame) -> DiffPlan | None:
    """The diff plan of a frame against its version in force, or None when it has none (a new SOP: read whole)."""
    old_text = ((baseline or {}).get("texts") or {}).get(frame.name)
    if old_text is None:
        return None
    try:
        return diff_plan(old_text, frame, (baseline or {}).get("rules") or [])
    except Exception:
        return None  # the text in force does not load (an old release): read the SOP whole


class ModelExtractor:
    """The compiler's sentences first, then the model for the prose (see the module docstring)."""

    name = "hybrid"

    def __init__(self, cache_dir: Path | None = None, context: dict | None = None, model: str | None = None, call=None,
                 gateway=None, calls: bool = True, member: str = "", baseline=None, budget: int | None = None):
        self.member = member  # a committee member reads on its own: its reading is its own (cached apart)
        self.model = model or local_model()
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.context = context or {}
        self.call = call or (gateway_call(gateway) if gateway is not None else None)
        self.allow_calls = calls  # False: only what was read before (from the cache); pages never wait on the model
        self.calls = 0
        self.baseline = baseline  # the release in force ({"texts", "rules"} or a function giving it): diff reading
        self.budget = budget  # characters a call may send (default: what fits the model's window)
        self.prompts: list[str] = []  # what was sent, for tests

    # -- the release in force, and the window
    def _base(self) -> dict | None:
        if self.baseline is None:
            return None
        if not isinstance(self.baseline, _Baseline):
            self.baseline = _Baseline(self.baseline)
        return self.baseline.get()

    def _budget(self) -> int:
        if self.budget:
            return int(self.budget)
        from .model import prompt_budget_chars

        try:
            return prompt_budget_chars(self.model, MAX_TOKENS)
        except Exception:
            return 24000

    def _system(self, base: str) -> str:
        return base + (f"\nYou are the {self.member} cog, one member of a committee that reads this SOP; read it on your own." if self.member else "")

    def _ask(self, path: Path | None, system: str, user: str) -> dict:
        """One call, cached at ``path`` (keyed by exactly what it is about)."""
        if path is not None and path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        if not self.allow_calls or self.call is None or not self.model:
            raise LookupError("not read by the model yet: the compile-frame job reads it")
        self.calls += 1
        self.prompts.append(user)
        data = _json_of(self.call(self.model, system, user))
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        return data

    def _path(self, key: str) -> Path | None:
        return self.cache_dir / f"{key}.json" if self.cache_dir else None

    # -- a new SOP: read whole, as before
    def _read(self, frame: Frame, already: list[dict]) -> dict:
        path = self._path(_key(frame, self.context, self.model, self.member))
        if path is not None and path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        if not self.allow_calls or self.call is None or not self.model:
            raise LookupError("not read by the model yet: the compile-frame job reads it")
        numbered = "\n".join(f"{i:4d}: {line}" for i, line in enumerate(frame.lines, start=frame.body_start))
        said = [f"- line {r['source']['line']}: {_sentence(r, self.context.get('process_defs') or {})}" for r in already]
        prompt = PROMPT.format(name=frame.name, version=frame.version, numbered=numbered, already="\n".join(said) or "- (nothing)",
                               **_names(self.context))
        return self._ask(path, self._system(SYSTEM), prompt)

    def _extract_whole(self, frame: Frame, conv):
        from .extract import Extraction

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
        out = Extraction(rules=list(conv.rules), notes=[])
        covered = set()
        for item in data.get("rules") or []:
            rule, note = _build_rule(frame, item, self.model)
            if rule is None:
                out.notes.append(note)
                continue
            if rule_key(rule) in have:
                continue
            bad = _malformed(frame, rule)
            if bad:
                out.notes.append(bad)
                continue
            have.add(rule_key(rule))
            out.rules.append(rule)
            if isinstance(rule["source"].get("line"), int):
                covered.add(rule["source"]["line"])
        for n in conv.notes:
            if n.get("severity") == "error" and n.get("line") in covered:
                continue  # the compiler could not read this sentence; the model did, and the reviewers see what it read
            out.notes.append(n)
        return out

    # -- an SOP that differs from its version in force: read the change
    def _prompt_parts(self, frame: Frame, plan: DiffPlan, conv, hunks: list[dict], whole: bool) -> tuple[str, str]:
        procs = self.context.get("process_defs") or {}
        sections = {h["section"] for h in hunks}
        new_lines = {n for h in hunks for (_t, _o, n, _x) in h["lines"] if n is not None}
        refs = [(ref, r) for ref, r in plan.in_force if whole or plan.section_of.get(ref) in sections]
        in_force = "\n".join(f"- {ref} (line {r['source'].get('line')}): {_sentence(r, procs)}" for ref, r in refs) or "- (none)"
        exact = "\n".join(f"- line {r['source']['line']}: {_sentence(r, procs)}" for r in conv.rules
                          if (r.get("source") or {}).get("line") in new_lines) or "- (none)"
        others = "\n".join(f"- {_sentence(r, procs)} ({(r.get('source') or {}).get('frame')})" for r in plan.others[:40]) or "- (none)"
        diff = "\n\n".join(render_hunk(h) for h in hunks)
        user = DIFF_PROMPT.format(name=frame.name, diff=diff, in_force=in_force, exact=exact, others=others, **_names(self.context))
        # Cached by what the reading is about (the change, the readings in force, the sentences read exactly), not by the
        # names: a later release that leaves this SOP alone does not have the model read the same change again.
        key = hashlib.sha256(json.dumps({"frame": frame.name, "diff": diff, "in_force": in_force, "exact": exact, "model": self.model,
                                         "member": self.member, "v": DIFF_PROMPT_VERSION}, sort_keys=True).encode()).hexdigest()[:32]
        return user, key

    def chunks(self, frame: Frame, plan: DiffPlan, conv) -> list[tuple[str, str]]:
        """The calls a diff takes, as (prompt, cache key): one when it fits the budget; else whole sections together while
        they fit, a section too large on its own by hunks, a hunk too large on its own by runs of lines."""
        system = self._system(DIFF_SYSTEM)
        budget = self._budget()

        def fits(hs: list[dict], whole: bool = False) -> bool:
            user, _ = self._prompt_parts(frame, plan, conv, hs, whole)
            return len(system) + len(user) <= budget

        if fits(plan.hunks, True):
            return [self._prompt_parts(frame, plan, conv, plan.hunks, True)]
        pieces: list[dict] = []
        for h in plan.hunks:
            if fits([h]):
                pieces.append(h)
                continue
            size = max(1, len(h["lines"]) // 2)
            while size > 1 and not fits([{**h, "lines": h["lines"][:size]}]):
                size //= 2
            pieces += [{**h, "lines": h["lines"][i:i + size]} for i in range(0, len(h["lines"]), size)]
        groups: list[list[dict]] = []
        for p in pieces:
            if groups and groups[-1][-1]["section"] == p["section"]:
                groups[-1].append(p)
            else:
                groups.append([p])
        packed: list[list[dict]] = []
        for g in groups:
            for part in ([g] if fits(g) else [[p] for p in g]):
                if packed and fits(packed[-1] + part):
                    packed[-1] += part
                else:
                    packed.append(list(part))
        return [self._prompt_parts(frame, plan, conv, c, False) for c in packed]

    def read_changes(self, frame: Frame, plan: DiffPlan, conv) -> Reading:
        """This reader's reading of the diff (raises LookupError when it is not read yet and calls are off)."""
        from .compile import question

        system = self._system(DIFF_SYSTEM)
        answers = [self._ask(self._path(key), system, user) for user, key in self.chunks(frame, plan, conv)]
        refs = dict(plan.in_force)
        out = Reading()
        raised_by = self.member or self.model or "the model"
        have = {rule_key(r) for r in conv.rules}
        for data in answers:
            items = list(data.get("changes") or []) + [dict(x, op="add") for x in data.get("rules") or [] if isinstance(x, dict)]
            for item in items:
                if not isinstance(item, dict):
                    continue
                op = str(item.get("op") or "add").lower()
                if op in ("remove", "change"):
                    r = refs.get(str(item.get("ref") or ""))
                    if r is None:
                        out.notes.append({"frame": frame.name, "line": item.get("line") or 0, "text": str(item.get("quote") or ""),
                                          "severity": "warning",
                                          "note": f"the model would {op} {item.get('ref')!r}, which is not a rule in force from {frame.name}"})
                    else:
                        out.removes.add(rule_key(r))
                    if op == "remove":
                        continue
                rule, note = _build_rule(frame, item, self.model)
                if rule is None:
                    out.notes.append(note)
                    continue
                if rule_key(rule) in have:
                    continue
                bad = _malformed(frame, rule)
                if bad:
                    out.notes.append(bad)
                    continue
                out.adds.append(rule)
            for q in data.get("questions") or []:
                if not isinstance(q, dict) or not str(q.get("text") or "").strip():
                    continue
                line = q.get("line") if isinstance(q.get("line"), int) else 0
                out.questions.append(question(frame.name, line, str(q["text"]).strip(), raised_by=raised_by, code="ambiguous",
                                              quote=str(q.get("quote") or "")))
        return out

    def plan(self, frame: Frame) -> DiffPlan | None:
        return plan_for(self._base(), frame)

    def extract(self, frame: Frame):
        from .extract import ConventionExtractor

        conv = ConventionExtractor().extract(frame)
        if frame.scope in ("reference", "persona") or frame.name == "scopes":
            return conv  # structured sections only: nothing in prose to read
        plan = self.plan(frame)
        if plan is None:  # no version in force (a new SOP): read whole
            return self._extract_whole(frame, conv)
        procs = self.context.get("process_defs") or {}
        if not plan.changed:  # the text in force: its readings stand, no model call
            return _assemble(frame, conv, plan, [], set(), [], [], read=True, processes=procs)
        try:
            rd = self.read_changes(frame, plan, conv)
        except LookupError as e:  # not read yet (a page, a preview): what stands on unchanged lines stays meanwhile
            return _assemble(frame, conv, plan, [], set(), [], [{"frame": frame.name, "line": 0, "text": "", "severity": "info", "note": str(e)}],
                             read=False, processes=procs)
        except Exception as e:  # no model: likewise, and unread rule-like sentences still stop a release
            return _assemble(frame, conv, plan, [], set(), [], [{"frame": frame.name, "line": 0, "text": "", "severity": "warning",
                                                                 "note": f"the model could not read the changes to the prose of {frame.name} "
                                                                         f"({type(e).__name__}: {str(e)[:160]})"}], read=False, processes=procs)
        return _assemble(frame, conv, plan, rd.adds, rd.removes, rd.questions, rd.notes, read=True, processes=procs)


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
    reviewers as disputed, with who read them, and left out. Reading a change, a rule in force is removed only when
    ``quorum`` members remove it; any member may ask a question."""

    name = "committee"

    def __init__(self, members: list[tuple[str, str]], quorum: int, cache_dir: Path | None = None, context: dict | None = None,
                 gateway=None, calls: bool = True, call=None, baseline=None, budget: int | None = None):
        self.members = [(n, m) for n, m in members if m]
        self.quorum = max(1, int(quorum or 1))
        self.context = context or {}
        self.baseline = baseline if baseline is None or isinstance(baseline, _Baseline) else _Baseline(baseline)
        self.readers = [ModelExtractor(cache_dir=cache_dir, context=context, model=m, gateway=gateway, calls=calls, call=call, member=n,
                                       baseline=self.baseline, budget=budget)
                        for n, m in self.members]
        self.model = ", ".join(f"{n} ({m})" for n, m in self.members)

    @property
    def calls(self) -> int:
        return sum(r.calls for r in self.readers)

    def _short(self, frame: Frame) -> dict:
        return {"frame": frame.name, "line": 0, "text": "", "severity": "warning",
                "note": f"the compile-frame committee has {len(self.readers)} member(s) with a model here, fewer than the "
                        f"{self.quorum} that must agree: the prose of {frame.name} is not read"}

    def extract(self, frame: Frame):
        from .extract import ConventionExtractor

        conv = ConventionExtractor().extract(frame)
        if frame.scope in ("reference", "persona") or frame.name == "scopes":
            return conv
        plan = plan_for(self.baseline.get() if self.baseline is not None else None, frame)
        if plan is None:
            return self._extract_whole(frame, conv)
        procs = self.context.get("process_defs") or {}
        if not plan.changed:
            return _assemble(frame, conv, plan, [], set(), [], [], read=True, processes=procs)
        if len(self.readers) < self.quorum:
            return _assemble(frame, conv, plan, [], set(), [], [self._short(frame)], read=False, processes=procs)
        adds: dict[str, list[str]] = {}
        removes: dict[str, list[str]] = {}
        first: dict[str, dict] = {}
        questions: list[dict] = []
        notes: list[dict] = []
        read = 0
        for (name, _model), reader in zip(self.members, self.readers):
            try:
                rd = reader.read_changes(frame, plan, conv)
            except LookupError as e:
                notes.append({"frame": frame.name, "line": 0, "text": "", "severity": "info", "note": str(e)})
                continue
            except Exception as e:
                notes.append({"frame": frame.name, "line": 0, "text": "", "severity": "warning",
                              "note": f"the model could not read the changes to the prose of {frame.name} ({type(e).__name__}: {str(e)[:160]})"})
                continue
            read += 1
            for r in rd.adds:
                k = rule_key(r)
                adds.setdefault(k, []).append(name)
                first.setdefault(k, r)
            for k in rd.removes:
                removes.setdefault(k, []).append(name)
            questions += rd.questions
            notes += [x for x in rd.notes if x.get("severity") in ("warning", "info")]
        n = len(self.readers)
        agreed = []
        for k, who in adds.items():
            r = first[k]
            if len(who) >= self.quorum:
                agreed.append({**r, "source": {**r["source"], "read_by": f"the compile-frame committee ({', '.join(who)}: {len(who)} of {n} agree)"}})
            else:
                notes.append({"frame": frame.name, "line": r["source"].get("line") or 0, "text": r["source"].get("text") or "", "severity": "warning",
                              "note": f"disputed, left out: only {', '.join(who)} ({len(who)} of {n}; {self.quorum} must agree) read a {r['kind']} rule"})
        removed = set()
        in_force = {rule_key(r): r for _ref, r in plan.in_force}
        for k, who in removes.items():
            if len(who) >= self.quorum:
                removed.add(k)
                continue
            src = (in_force.get(k) or {}).get("source") or {}
            notes.append({"frame": frame.name, "line": src.get("line") or 0, "text": src.get("text") or "", "severity": "warning",
                          "note": f"disputed, kept: only {', '.join(who)} ({len(who)} of {n}; {self.quorum} must agree) removed the "
                                  f"{(in_force.get(k) or {}).get('kind')} rule in force"})
        return _assemble(frame, conv, plan, agreed, removed, questions, notes, read=read >= self.quorum, processes=procs)

    def _extract_whole(self, frame: Frame, conv):
        from .extract import Extraction

        if len(self.readers) < self.quorum:
            conv.notes.append(self._short(frame))
            return conv
        votes: dict[str, list[str]] = {}
        first: dict[str, dict] = {}
        notes: list[dict] = []
        for (name, _model), reader in zip(self.members, self.readers):
            out = reader._extract_whole(frame, _copy(conv))
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
