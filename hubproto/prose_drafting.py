"""Prose for direct changes: the qa-manager cog states each one in the SOP it belongs to (change control v2, 3.3 step 2).

A direct change (an edit made in Settings, the forms editor or the workflow editor) is stored on the change order as a
delta item with origin ``direct`` (``co.fields.direct_changes``); the editor that made it does not write prose. When a
round runs, the qa-manager cog writes the prose that states each direct change not yet stated into the draft revision of
the SOP the rule belongs to (the revision the change order carries, started when it carries none yet), in that SOP's own
style. Without a model for the cog here (or with only a stand-in) it writes the sentence the grammar reads exactly, the
one Settings wrote before.

Then the round trip: the drafted text is compiled (by the compile-frame committee when the hub reads prose with models,
else by the grammar) and must give back the same rule; a removed rule must be gone. A mismatch is a question with code
``round-trip``: the author answers it in the prose, and an unresolved one is a warning the approvers accept. Each direct
item is marked with the SOP, revision and line it was stated at (``stated``) and how its round trip went (``round_trip``).
"""

from __future__ import annotations

import json
from pathlib import Path

from . import eco, sop_settings, sopdocs
from .compile import question
from .frames import load_frame_text
from .kernel import KernelGuardError
from .release import utcnow
from .rules import rule_key, sha256_of
from .settings import sentence

QA_MANAGER = "qa-manager"
MAX_LINES = 6  # a drafted statement of one rule: a sentence or a short paragraph, never a section

DRAFT_SYSTEM = """{persona}
You are revising a controlled SOP: you write the prose that states one configuration change, in the SOP's own style. The
hub's compiler reads your words back into the rule, so every name, number, amount and condition stays exactly as given
and nothing is added. You return JSON only."""

DRAFT_PROMPT = """SOP {name}, {where}:
{section}

The change to state: {what}
The sentence the hub's grammar reads exactly: {sentence}
As data: {data}
{replaces}
Write the line or lines that state it, in the style of this section. The grammar sentence above is read back exactly; keep
to its shape unless the section's style needs other words that still say exactly this rule (the compile-frame committee
then reads them, and the hub checks they give back the same rule).
Answer with a JSON object: {{"lines": ["<a line of the SOP>"]}}"""

DEFAULT_PERSONA = "You are the QA manager. You draft and revise procedures in plain, numbered, testable language."


def _strip(rule: dict | None) -> dict:
    return {k: v for k, v in (rule or {}).items() if k not in ("source", "key")}


def direct_item(rule: dict, *, by: str, op: str = "add", was: dict | None = None, at: str | None = None) -> dict:
    """A direct change as a delta item (section 8), in the shape this module reads from ``co.fields.direct_changes``:
    ``op`` "add" (``rule`` is the new rule), "remove" (``rule`` is the rule removed) or "change" (``was`` -> ``rule``)."""
    ident = sha256_of({"op": op, "rule": _strip(rule), "was": _strip(was) if was else None})[:12]
    item = {"id": f"direct:{ident}", "op": op, "rule": _strip(rule),
            "origin": {"kind": "direct", "by": by, "frame": None, "line": None, "quote": None, "read_by": None, "at": at or utcnow()},
            "warnings": [], "stale": False}
    if was is not None:
        item["was"] = _strip(was)
    return item


def rules_of(item: dict) -> tuple[dict | None, dict | None]:
    """(the rule the item takes out or changes, the rule it puts in) of a direct item. Accepts ``was`` or ``changes_from``
    (an add paired with what it changes, as a compiled change is)."""
    op = item.get("op") or "add"
    was = item.get("was") or item.get("changes_from")
    if op == "remove":
        return (was or item.get("rule")), None
    return was, item.get("rule")


def same_rule(a: dict | None, b: dict | None) -> bool:
    """The same rule, wherever stated (unset optional fields count as absent; a process's steps are compared without
    the file lines they sit on)."""
    if not a or not b:
        return False
    if rule_key(a) == rule_key(b):
        return True

    def norm(r):
        out = {k: v for k, v in _strip(r).items() if v not in (None, [], {}, "")}
        if out.get("kind") == "process":
            out["steps"] = [{k: v for k, v in s.items() if k != "line"} for s in out.get("steps") or []]
        return out
    return norm(a) == norm(b)


def reads_back(item: dict, rules: list[dict]) -> bool:
    """The round trip: the rules compiled from the drafted text give back the item (its new rule is there; the rule it
    takes out is gone)."""
    old, new = rules_of(item)
    if new is not None and not any(same_rule(new, r) for r in rules):
        return False
    if old is not None and not same_rule(old, new) and any(same_rule(old, r) for r in rules):
        return False
    return True


def _says(item: dict, processes: dict) -> str:
    old, new = rules_of(item)
    try:
        if new is None:
            return "remove: " + sentence(old, processes)
        return sentence(new, processes)
    except Exception:
        return json.dumps(_strip(new or old), sort_keys=True, default=str)


def _writer(hub, bundle: dict, call=None):
    """(call, model, profile) through which the qa-manager cog drafts; None when it has no model here (the grammar
    sentence is written instead)."""
    prof = (bundle.get("cogs") or {}).get(QA_MANAGER) or {}
    if call is not None:
        return call, prof.get("model") or "test", prof
    if QA_MANAGER not in (bundle.get("members") or {}):
        return None
    model = prof.get("model") or ""
    from .model import backend_for

    if not model or backend_for(model) in ("none", "stand-in"):
        return None

    def through_gateway(m: str, system: str, user: str) -> str:
        out = hub.model.complete(model=m, system=system, user=user, max_tokens=800, by=QA_MANAGER)
        if out.get("stand_in"):
            raise RuntimeError(f"{m} is not available here (a stand-in answered)")
        return out.get("text") or ""
    return through_gateway, model, prof


def _section_of(text: str, op: dict) -> tuple[str, list[str]]:
    """Where the edit goes, in words, and the lines of that section of the draft (the style to write in)."""
    lines = text.splitlines()
    idx = None
    if op["op"] == "insert" and op.get("section_end"):
        head = f"## {op['section_end']}"
        idx = next((i for i, ln in enumerate(lines) if ln.strip().lower() == head.lower()), None)
        where = f"at the end of its section “{head}”" if idx is not None else f"in a new section “{head}” at its end"
    elif op["op"] == "replace_paragraph":
        at = next((i for i, ln in enumerate(lines) if ln.strip() == str(op.get("target") or "").strip()), None)
        if at is not None:
            idx = next((i for i in range(at, -1, -1) if lines[i].startswith("#")), None)
        where = f"in its section “{lines[idx].strip()}”, in place of the line quoted below" if idx is not None else "in place of the line quoted below"
    else:
        return "at its end", []
    if idx is None:
        return where, []
    out = [lines[idx]]
    for ln in lines[idx + 1:]:
        if ln.startswith("#"):
            break
        out.append(ln)
    while out and not out[-1].strip():
        out.pop()
    return where, out


def _draft(writer, name: str, text: str, op: dict, new: dict, old: dict | None, processes: dict) -> tuple[list[str], str, str]:
    """The qa-manager's lines for one edit: (lines, "model" or "sentence", a note when it fell back to the sentence)."""
    from .llm_extract import _json_of
    from .model import prompt_budget_chars

    call, model, prof = writer
    where, section = _section_of(text, op)
    system = DRAFT_SYSTEM.format(persona=(prof.get("prompt") or DEFAULT_PERSONA).strip())
    what = (f"change “{sentence(old, processes)}” to “{sentence(new, processes)}”" if old is not None
            else f"add “{sentence(new, processes)}”")

    def prompt(sec: list[str]) -> str:
        return DRAFT_PROMPT.format(name=name, where=where, section="\n".join(sec) or "(the section is empty)", what=what,
                                   sentence=" ".join(op["lines"]), data=json.dumps(_strip(new), sort_keys=True, default=str),
                                   replaces=f"It replaces this line: “{op['target']}”\n" if op["op"] == "replace_paragraph" else "")
    try:
        budget = prompt_budget_chars(model, 800)
    except Exception:
        budget = 24000
    while len(section) > 1 and len(system) + len(prompt(section)) > budget:
        section = section[: len(section) // 2]  # the heading and the first lines carry the style
    try:
        data = _json_of(call(model, system, prompt(section)))
    except Exception as e:
        return list(op["lines"]), "sentence", f"the qa-manager could not draft ({type(e).__name__}: {str(e)[:120]}); the grammar sentence was written"
    lines = data.get("lines") if isinstance(data, dict) else None
    if (isinstance(lines, list) and 0 < len(lines) <= MAX_LINES
            and all(isinstance(x, str) and x.strip() and "\n" not in x and not x.lstrip().startswith("#") for x in lines)):
        return [x.strip() for x in lines], "model", ""
    return list(op["lines"]), "sentence", "the qa-manager's draft was not usable; the grammar sentence was written"


def _line_of(text: str, line: str) -> int | None:
    want = line.strip()
    for i, ln in enumerate(text.splitlines(), start=1):
        if ln.strip() == want:
            return i
    return None


def _carried_draft(hub, co_ref: str, name: str) -> dict | None:
    return next((c["draft"] for c in eco.carried(hub, eco._co(hub, co_ref)) if c["name"] == name), None)


def _frame_of(hub, name: str, draft: dict):
    text = sopdocs.frame_text(hub, draft)
    store = hub.released_frames()
    known = store.frames.get(name)
    frame = load_frame_text(text, known.path if known else Path(f"{name}.frame.md"))
    frame.relpath = known.relpath if known else f"{name}.frame.md"
    return frame


def draft_direct_changes(hub, co_ref: str, by: str, *, call=None, extractor=None) -> list[dict]:
    """Have the qa-manager cog state each direct change of the change order not yet stated in the prose of the SOP it
    belongs to (``eco.add_sop`` makes sure the change order carries that SOP's draft revision), then check every stated
    item reads back (the round trip). Updates ``co.fields.direct_changes`` (``stated``, ``round_trip``) and returns the
    questions: one per item that could not be written or does not read back (code ``round-trip``).

    ``by`` is who runs the round (the requester or a document controller): the revision is started in their name, the
    prose is the qa-manager's. ``call`` stands in for the qa-manager's model (tests); ``extractor`` for the compile-frame
    reading of the round trip (default: how this hub reads SOPs, ``hub.extractor(calls=True)``)."""
    co = eco._co(hub, co_ref)
    items = [dict(i) for i in co["fields"].get("direct_changes") or []]
    if not items:
        return []
    if not sopdocs.converted(hub.kernel):
        raise ValueError("the SOPs are not documents yet: a direct change is stated in an SOP revision once they are")
    meta = hub.releases.current()
    bundle = hub.releases.bundle(meta["digest"])
    base = hub.releases.rules(meta["digest"])
    processes = bundle.get("processes") or {}
    writer = _writer(hub, bundle, call)
    questions: list[dict] = []
    for item in items:
        if item.get("stated") or item.get("stale"):
            continue
        old, new = rules_of(item)
        live = next((r for r in base if old is not None and same_rule(r, old)), None)
        after = [r for r in base if r is not live] + ([_strip(new)] if new else [])
        planned = sop_settings.plan(base, after, processes)
        if not planned:
            item["stated"] = {"sop": None, "line": None, "by": QA_MANAGER, "at": utcnow(),
                              "note": "nothing to write: the release in force already says this"}
            continue
        name, ops = sorted(planned.items())[0]  # one item is one edit, in the SOP the rule belongs to
        op = ops[0]
        how, why = "sentence", ""
        if writer and new is not None and new.get("kind") in sop_settings.SECTION and op.get("lines") \
                and op["op"] in ("insert", "replace_paragraph"):
            draft, store = _carried_draft(hub, co["locator"], name), hub.released_frames()
            text = sopdocs.frame_text(hub, draft) if draft else (store.get(name).text if name in store.frames else "")
            lines, how, why = _draft(writer, name, text, op, new, live, processes)  # the style of the revision as it stands
            op = {**op, "lines": lines}
        try:
            [rep] = sop_settings.apply_plan(hub, co["locator"], {name: [op]}, by=by, author=QA_MANAGER,
                                            note=f"the qa-manager states the direct change {item.get('id') or ''}".strip())
        except (ValueError, KernelGuardError) as e:
            rep = {"ok": False, "detail": str(e)}
        if not rep.get("ok"):
            q = question(name, 0, f"The direct change “{_says(item, processes)}” could not be written into {name}",
                         raised_by=QA_MANAGER, code="round-trip", item=item.get("id"), detail=rep.get("detail") or "")
            questions.append(q)
            item["round_trip"] = {"ok": False, "question": q["id"], "at": utcnow(), "detail": rep.get("detail") or ""}
            continue
        draft = _carried_draft(hub, co["locator"], name)
        text = sopdocs.frame_text(hub, draft)
        lines = list(op.get("lines") or [])
        item["stated"] = {"sop": name, "draft": draft["number"], "revision": draft["fields"].get("revision"),
                          "line": _line_of(text, lines[0]) if lines else None, "lines": lines,
                          "removed": op.get("target") if op["op"] == "delete_paragraph" else None,
                          "by": QA_MANAGER, "how": how, "at": utcnow(), **({"note": why} if why else {})}
    # The round trip, for every item stated (now or in an earlier round): the drafted text compiled must give it back.
    stated: dict[str, list[dict]] = {}
    for item in items:
        sop = (item.get("stated") or {}).get("sop")
        if sop and not item.get("stale"):
            stated.setdefault(sop, []).append(item)
    reader = None
    for name, its in sorted(stated.items()):
        draft = _carried_draft(hub, co["locator"], name)
        if draft is None:
            rules = []
        else:
            frame = _frame_of(hub, name, draft)
            for item in its:  # where each statement is now (later edits move lines)
                lines = item["stated"].get("lines") or []
                if lines:
                    item["stated"]["line"] = _line_of(frame.text, lines[0]) or item["stated"].get("line")
            reader = reader or extractor or hub.extractor(calls=True)
            rules = reader.extract(frame).rules
        for item in its:
            line = item["stated"].get("line")
            if draft is not None and reads_back(item, rules):
                item["round_trip"] = {"ok": True, "at": utcnow()}
                continue
            reads = [sentence(r, processes) for r in rules if line and (r.get("source") or {}).get("line") == line]
            q = question(name, line, f"The prose stating the direct change “{_says(item, processes)}” does not read back as that change",
                         raised_by="hub", code="round-trip", item=item.get("id"),
                         detail=("the line reads as: " + "; ".join(reads)) if reads else
                                ("the change order no longer carries the revision" if draft is None else "the line reads as no rule"))
            questions.append(q)
            item["round_trip"] = {"ok": False, "question": q["id"], "at": utcnow(), "reads_as": reads}
    hub.kernel.set_fields(co["locator"], {"direct_changes": items}, by="hub")
    return questions
