"""Settings: the policy's rules edited on screen as data, with no SOP sentences and no Python.

The rules a release enforces (roles, members, folders, permissions, thresholds, routing, due dates, approval criteria
and their limits, segregation of duties, processes, cog profiles) are the same rule records whether they came from a
Frame or from these settings. A person's edits go into their own settings draft, which starts from the release in
force; checking it runs the same guards as a compile; proposing it makes a proposal the policy owners review (two for
widenings) and release, exactly as a Frame change. A rule edited here carries its source as "settings", with the
sentence it reads as, who wrote it and when, so the /why page and signatures still quote it.

Once a release has come from settings, the settings are the source of truth: compiling the Frames is refused, and the
Frames are the SOPs people and cogs read."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from .release import utcnow
from .rules import CONDITION_VOCABULARY, KINDS, rule_key, validate_rule

SETTINGS = "settings"
_PHRASE = {}
for _phrase, _key in CONDITION_VOCABULARY.items():
    _PHRASE.setdefault(_key, _phrase)  # the first phrase of each condition reads best


def _words(slug: str) -> str:
    return (slug or "").replace("-", " ")


def plural(words: str) -> str:
    w = _words(words)
    if not w or w.endswith(("staff", "s")):
        return w
    if w.endswith("y") and not w.endswith(("ay", "ey", "oy", "uy")):
        return w[:-1] + "ies"
    if w.endswith(("ch", "sh", "x")):
        return w + "es"
    return w + "s"


def money(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:,.0f}" if f == int(f) else f"{f:,.2f}"


def who(assignee: str) -> str:
    kind, _, name = (assignee or "").partition(":")
    if kind == "role":
        return "the " + _words(name)
    if kind == "cog":
        return f"the {name} cog"
    if kind in ("field", "group"):  # a group is named as a role is ("the quality board"); the compiler resolves it
        return "the " + _words(name)
    return name or assignee


def condition_words(cond) -> str:
    if not cond:
        return ""
    if "all" in cond:
        return " and ".join(condition_words(c) for c in cond["all"])
    f, op, v = cond.get("field"), cond.get("op"), cond.get("value")
    if f == "amount":
        word = {">": "over", "<": "under", "<=": "at or under", ">=": "at or over"}.get(op, op)
        also = cond.get("also_currencies") or []
        return f"{word} {money(v)} {cond.get('currency') or ''}".strip() + (f" or the same in {' or '.join(also)}" if also else "")
    if f == "risk" and op == "==":
        return f"with {v} risk"
    if f == "change_type" and op == "==":
        return f"with a {v} change"
    if f == "doc_type" and op == "==":
        return f"for {plural(str(v))}"
    if f == "overdue":
        return "when overdue"
    return f"when {_words(str(f))} {op} {v}"


def step_title(process: dict | None, step: str) -> str:
    for s in (process or {}).get("steps") or []:
        if s.get("id") == step:
            return s.get("title") or step
    return _words(step).capitalize()


def sentence(rule: dict, processes: dict | None = None) -> str:
    """How a rule reads: the sentence a person would write for it (shown on /why, in proposals and signatures)."""
    k = rule.get("kind")
    processes = processes or {}
    if k == "role":
        return f"- {rule['name']}"
    if k == "member":
        return f"- {rule['principal']} ({rule['principal_type']}): {', '.join(rule.get('roles') or [])}"
    if k == "group":
        parts = list(rule.get("members") or [])
        if rule.get("roles"):
            parts.append("everyone holding " + " or ".join(rule["roles"]))
        return f"- {rule['name']}: " + (", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else (parts[0] if parts else ""))
    if k == "committee":
        lines = [f"## Committee: {rule['name']}"]
        if rule.get("group"):
            lines.append(f"Group: {rule['group']}")
        elif rule.get("members"):
            lines.append("Members: " + ", ".join(rule["members"]))
        if rule.get("chair"):
            lines.append(f"Chair: {rule['chair']}")
        if rule.get("purpose"):
            lines.append(f"Purpose: {rule['purpose']}")
        return "\n".join(lines)
    if k == "folder":
        return f"- {rule['path']}"
    if k == "permission":
        kind_, _, name_ = rule["subject"].partition(":")
        subject = f"The {name_} cog" if kind_ == "cog" else f"The {name_} group" if kind_ == "group" else plural(name_).capitalize()
        neg = "not " if rule.get("effect") == "deny" else ""
        folder = rule["resource"].partition(":")[2]
        if rule["action"] == "read" and rule["object"] == "*":
            return f"{subject} may {neg}read {folder}."
        obj = "anything" if rule["object"] == "*" else plural(rule["object"])
        on = " on" if rule["action"] == "comment" else ""
        confirm = ""
        if rule.get("effect") == "propose":
            cf = rule.get("confirmer") or "person"
            confirm = f" when {'an' if cf[:1] in 'aeiou' else 'a'} {_words(cf)} confirms"
        return f"{subject} may {neg}{rule['action']}{on} {obj} in {folder}{confirm}."
    if k == "threshold":
        return f"The {_words(rule['name'])} is {money(rule['value'])} {rule['unit']}."
    if k == "routing":
        cond = rule.get("condition")
        verb = "go to" if rule.get("mode", "assign") == "assign" else "are reported to"
        if cond and cond.get("field") == "overdue":
            return f"Overdue items in {rule['process']} {verb} {who(rule['assignee'])}."
        head = "Approvals" if rule["step"] in ("approve", "*") else f"At {step_title(processes.get(rule['process']), rule['step'])}"
        extra = condition_words(cond)
        return f"{head} in {rule['process']}{(' ' + extra) if extra else ''} {verb} {who(rule['assignee'])}."
    if k == "sla":
        if rule["step"] == "approve":
            return f"Approvals in {rule['process']} are due within {rule['days']} days."
        return f"{step_title(processes.get(rule['process']), rule['step'])} in {rule['process']} is due within {rule['days']} days."
    if k == "approval_criteria":
        cog = rule["approver"].partition(":")[2]
        obj = plural((processes.get(rule["process"]) or {}).get("object") or "item")
        s = f"The {cog} cog may approve {rule['process']} {obj}"
        if rule.get("max_amount") is not None:
            s += f" under {money(rule['max_amount'])} {rule.get('currency') or ''}".rstrip()
            if rule.get("also_currencies"):
                s += " or the same in " + " or ".join(rule["also_currencies"])
        conds = [f"{int(rule.get('adversarial_reviews') or 0)} adversarial reviews pass" if c == "adversarial_reviews_pass" else _PHRASE.get(c, _words(c))
                 for c in rule.get("conditions") or []]
        if conds:
            s += " when " + " and ".join(conds)
        if rule.get("max_per_day"):
            s += f", at most {int(rule['max_per_day'])} a day"
        if rule.get("max_amount_per_month"):
            s += f", at most {money(rule['max_amount_per_month'])} {rule.get('currency') or 'USD'} a month"
        if rule.get("countersign"):
            s += f", countersigned by a {_words(rule['countersign'])}"
        return s + "."
    if k == "sod" and rule.get("rule") == "cog-work-signed-by-person":
        return "A cog's work " + ("" if rule.get("object") in (None, "*") else f"on a {rule['object']} ") + "is signed by a person."
    if k == "sod":
        # Written as the grammar reads them: the roles and the object keep their hyphens ("received-by", "stock-adjustment").
        a, b = rule["roles"]
        if rule.get("only"):
            return f"Whoever {_words(a)} on a {rule['object']} is not its only approver."
        return f"The {a} and the {b} of a {rule['object']} must be different people."
    if k == "duty":
        return f"Every {rule.get('every') or 'day'} the {rule['cog']} cog {rule['task']}"
    if k == "responsibility":
        kind_, _, name_ = (rule.get("who") or "").partition(":")
        who_ = f"the {name_} cog" if kind_ == "cog" else f"the {name_} group" if kind_ == "group" else _words(name_).capitalize()
        return f"| {who_} | {rule.get('text') or ''} |"
    if k == "cog":
        lines = [f"## Cog: {rule['name']}"]
        if rule.get("about"):
            lines.append(f"About: {rule['about']}")
        lines.append(f"Model: {rule.get('model') or ''}")
        if rule.get("persona"):
            lines.append(f"Persona: {rule['persona']}")
        if rule.get("prompt_from"):
            lines.append(f"Prompt from: {rule['prompt_from']}")
        if rule.get("references"):
            lines.append("References: " + ", ".join(rule["references"]))
        return "\n".join(lines)
    if k == "persona":
        lines = [f"## Persona: {rule['name']}"]
        for key in ("tone", "stance"):
            if rule.get(key):
                lines.append(f"{key.capitalize()}: {rule[key]}")
        if rule.get("lenses"):
            lines.append("Lenses: " + ", ".join(rule["lenses"]))
        if rule.get("passes"):
            lines.append(f"Reviews: {int(rule['passes'])} adversarial pass{'es' if int(rule['passes']) != 1 else ''}")
        if rule.get("escalates"):
            lines.append(f"Escalates: {rule['escalates']}")
        if rule.get("behaviour"):
            lines.append(f"Behaviour: {rule['behaviour']}")
        return "\n".join(lines)
    if k == "process":
        lines = [f"## Process: {rule['name']}", f"Records are filed in {rule['folder']}."]
        for what, folder in (rule.get("files_by_type") or {}).items():
            lines.append(f"{plural(what).capitalize()} are filed in {folder}.")
        if rule.get("record_type"):
            lines.append(f"The record type is {rule['record_type']}.")
        if rule.get("terminal") and rule["terminal"] != "closed":
            lines.append(f"Closes as {rule['terminal']}.")
        for st in rule.get("steps") or []:
            cog = f" ({', '.join(st['swarm'])})" if st.get("swarm") else (f" ({st['cog']})" if st.get("cog") else "")
            lines.append(f"{st['n']}. [{st['role']}] {st['title']}{cog}")
        if rule.get("statuses"):  # a process written as a graph (hubproto.process_graph)
            from .process_graph import to_sentences

            lines += to_sentences(rule)
        return "\n".join(lines)
    return json.dumps({x: y for x, y in rule.items() if x != "source"})


def stamp(rule: dict, by: str, processes: dict | None = None) -> dict:
    """The rule with its source set to these settings: the sentence it reads as, who wrote it, when."""
    r = {x: y for x, y in rule.items() if x != "source"}
    try:
        text = sentence(r, processes)
    except (KeyError, TypeError, ValueError):  # an incomplete rule: validation says what is missing
        text = json.dumps(r, default=str)
    r["source"] = {"frame": SETTINGS, "version": SETTINGS, "line": 0, "text": text, "by": by, "at": utcnow()}
    return r


# ---------------------------------------------------------------- drafts
class SettingsDrafts:
    """Each person's working copy of the rules (state/settings/drafts/<person>.json), based on a release."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, user: str) -> Path:
        return self.root / f"{user}.json"

    def load(self, user: str) -> dict | None:
        p = self.path(user)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def save(self, user: str, draft: dict) -> dict:
        draft["updated_at"] = utcnow()
        self.path(user).write_text(json.dumps(draft, indent=1, default=str), encoding="utf-8")
        return draft

    def discard(self, user: str) -> None:
        self.path(user).unlink(missing_ok=True)


def apply_edit(rules: list[dict], edit: dict, by: str, processes: dict | None = None) -> tuple[list[dict], str]:
    """One edit to a list of rules. edit: {"action": "add"|"remove"|"replace", "key": <rule key>, "rule": {...}}.
    Returns (new rules, a sentence saying what changed). Raises ValueError for an invalid edit."""
    rules = copy.deepcopy(rules)
    action = edit.get("action")
    keyed = {rule_key(r): i for i, r in enumerate(rules)}
    if action in ("remove", "replace"):
        key = edit.get("key") or ""
        if key not in keyed:
            raise ValueError("that setting is not in the draft any more (someone changed it, or it was removed)")
        old = rules[keyed[key]]
    if action in ("add", "replace"):
        new = dict(edit.get("rule") or {})
        if new.get("kind") not in KINDS:
            raise ValueError(f"unknown kind of setting {new.get('kind')!r}")
        new = stamp(new, by, processes)
        problems = validate_rule(new)
        if problems:
            raise ValueError("; ".join(problems))
        if rule_key(new) in keyed and (action == "add" or rule_key(new) != edit.get("key")):
            raise ValueError("that setting is already there")
    if action == "add":
        rules.append(new)
        return rules, f"added: {new['source']['text']}"
    if action == "remove":
        del rules[keyed[edit["key"]]]
        return rules, f"removed: {sentence(old, processes)}"
    if action == "replace":
        rules[keyed[edit["key"]]] = new
        return rules, f"changed: {sentence(old, processes)} → {new['source']['text']}"
    raise ValueError(f"unknown edit {action!r}")
