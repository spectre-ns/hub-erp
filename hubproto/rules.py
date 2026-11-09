"""Rule statements extracted from Frames, and their schema.

Every rule carries a ``source`` that points at the exact sentence in the Frame
it came from: ``{"frame": name, "version": v, "line": n, "text": sentence}``.
The Source-Grounding Guard checks that text really is in the Frame.
"""

from __future__ import annotations

import hashlib
import json

KINDS = {
    "role": {"name"},
    "member": {"principal", "principal_type", "roles"},
    "folder": {"path"},
    "permission": {"subject", "action", "object", "resource", "effect"},
    "threshold": {"name", "value", "unit"},
    "routing": {"process", "step", "condition", "assignee", "mode"},
    "sla": {"process", "step", "days"},
    "approval_criteria": {"approver", "process", "max_amount", "currency", "conditions"},
    "sod": {"object", "roles"},
    "process": {"name", "folder", "steps", "terminal", "reject_to"},
    # A cog's identity: what it does (about), the model it is bundled with, its persona. What it follows, what it may do and
    # its duties are derived from the SOPs (hubproto.release).
    "cog": {"name", "model"},
    # A scoping Frame: the SOP and policy Frames it compiles, in order, its level, and the scopes it layers on.
    "scope": {"name", "level", "compiles", "layers_on"},
    # A persona: a behaviour document shared by any number of cogs (tone, stance, lenses, adversarial passes, when it
    # escalates, its behaviour prompt).
    "persona": {"name", "stance", "behaviour"},
    # A cog's standing goal, stated in the SOP that owns the work ("Every day the quality-assistant cog finds ...").
    "duty": {"cog", "task", "every"},
    # A row of a document's Roles and responsibilities table: who (a role, a cog, a group) has what part in it. The table
    # says who must read the document and what a cog follows.
    "responsibility": {"frame", "who", "text"},
    # The IT SOP's validation of configuration changes: which kinds of delta item are of which risk, and what evidence
    # each risk class needs before a change order is approved (hubproto.validation).
    "validation_class": {"risk", "kinds"},
    "validation_need": {"risk", "evidence"},
    # A named set of members, people and cogs (hubproto.groups): listed, or everyone holding some roles, or both. A group
    # may carry permissions as a role does and be named in a Roles and responsibilities table.
    "group": {"name", "members", "roles"},
    # A committee, defined on a group (``group``; earlier texts list ``members``), with a person as its ``chair``: it decides
    # by consensus, every member agreeing (``quorum`` from an earlier "Agree: n of m" is kept, not used). It decides at a
    # gate of a process graph, reviews on a change order's panel, and reads the SOPs into rules (compile-frame).
    "committee": {"name"},
}

CONDITION_VOCABULARY = {
    "the three-way match is within tolerance": "match_within_tolerance",
    "the supplier is approved": "supplier_approved",
    "the risk is low": "risk_low",
    "the change is minor": "change_minor",
    "the document is a minor revision": "change_minor",
    "no open nonconformance exists": "no_open_ncr",
    "the risk is not high": "risk_not_high",
    "the bill of materials is resolved": "bom_resolved",
    "the design checks are clean": "design_checks_clean",
    "the item is known": "item_known",
    "the part is active": "part_active",
    "the variance is within tolerance": "variance_within_tolerance",
    "every requirement is accepted": "requirements_accepted",
    # Judgment: the cog reviews the item against the procedures it follows (with its model) and the review passes.
    # The review is filed as evidence and cited in the signature.
    "its review passes": "review_passes",
    # Several independent adversarial reviews, each a fresh read with one brief (a "lens"), each trying to find a reason
    # not to approve; all must pass. How many is the criterion's ``adversarial_reviews`` ("when 3 adversarial reviews pass").
    "adversarial reviews pass": "adversarial_reviews_pass",
}
NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of(obj) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def rule_key(rule: dict) -> str:
    """Stable identity of a rule, independent of where it was stated."""
    body = {k: v for k, v in rule.items() if k not in ("source", "key")}
    return sha256_of(body)[:16]


def validate_rule(rule: dict) -> list[str]:
    errors: list[str] = []
    kind = rule.get("kind")
    if kind not in KINDS:
        return [f"unknown rule kind {kind!r}"]
    for field_name in KINDS[kind]:
        if field_name not in rule:
            errors.append(f"{kind}: missing field {field_name!r}")
    src = rule.get("source")
    if not isinstance(src, dict) or not {"frame", "version", "line", "text"} <= set(src):
        errors.append(f"{kind}: missing or incomplete source")
    if kind == "permission" and rule.get("effect") not in ("allow", "deny", "propose"):
        errors.append("permission: effect must be allow, deny or propose")
    if kind == "permission" and rule.get("effect") == "propose" and not rule.get("confirmer"):
        errors.append("permission: a proposing permission names who confirms (a role, or a person)")
    if kind in ("validation_class", "validation_need") and rule.get("risk") not in ("high", "medium", "low"):
        errors.append(f"{kind}: risk must be high, medium or low")
    if kind == "validation_class" and not rule.get("kinds"):
        errors.append("validation_class: name at least one kind of change")
    if kind == "validation_need" and rule.get("evidence") not in ("test-record", "test-note", "review"):
        errors.append("validation_need: evidence must be a test record, a test note or the review of the delta")
    if kind == "duty" and rule.get("every") not in ("day", "week", "month"):
        errors.append("duty: every must be day, week or month")
    if kind == "member" and rule.get("principal_type") not in ("person", "cog"):
        errors.append("member: principal_type must be person or cog")
    if kind == "routing":
        cond = rule.get("condition")
        if cond is not None and not isinstance(cond, dict):
            errors.append("routing: condition must be a mapping or null")
    if kind == "approval_criteria":
        for c in rule.get("conditions", []):
            if c not in CONDITION_VOCABULARY.values():
                errors.append(f"approval_criteria: condition {c!r} is not evaluable")
        if "adversarial_reviews_pass" in (rule.get("conditions") or []):
            n = rule.get("adversarial_reviews")
            if not isinstance(n, int) or not 1 <= n <= 6:
                errors.append("approval_criteria: say how many adversarial reviews must pass (1 to 6)")
    if kind == "process" and not rule.get("steps") and not rule.get("statuses"):
        errors.append("process: needs at least one step")
    if kind == "process" and rule.get("statuses"):
        errors += validate_graph(rule)
    if kind == "sod" and rule.get("rule") not in (None, "cog-work-signed-by-person"):
        errors.append(f"sod: unknown rule {rule.get('rule')!r}")
    return errors


def validate_graph(process: dict) -> list[str]:
    """Schema of a process written as statuses and transitions (change control 10.1)."""
    errors: list[str] = []
    ids = [s.get("id") for s in process.get("statuses") or []]
    for s in process.get("statuses") or []:
        if s.get("category") not in ("todo", "in_progress", "done"):
            errors.append(f"process: status {s.get('id')!r} has category {s.get('category')!r} (to do, in progress or done)")
        if s.get("outcome") and (s.get("category") != "done" or s["outcome"] not in ("completed", "rejected", "cancelled", "superseded")):
            errors.append(f"process: status {s.get('id')!r} has outcome {s['outcome']!r}, which only a done status has (completed, rejected, cancelled, superseded)")
    if len(set(ids)) != len(ids):
        errors.append("process: two statuses share an id")
    tids = [t.get("id") for t in process.get("transitions") or []]
    if len(set(tids)) != len(tids):
        errors.append("process: two transitions share an id")
    for t in process.get("transitions") or []:
        where = f"process: transition {t.get('id')!r}"
        if t.get("type") not in ("initial", "directed", "global"):
            errors.append(f"{where} has type {t.get('type')!r}")
        if t.get("to") not in ids:
            errors.append(f"{where} goes to {t.get('to')!r}, which is not a status")
        for f in t.get("from") or []:
            if f not in ids:
                errors.append(f"{where} comes from {f!r}, which is not a status")
        if t.get("type") == "directed" and not t.get("from"):
            errors.append(f"{where} says no status it comes from")
        g = t.get("gate") or {}
        if g.get("kind") not in ("person", "cog", "committee", "auto"):
            errors.append(f"{where} has no gate (a person, a cog, a committee or automatic)")
        elif g["kind"] == "committee" and not g.get("committee"):
            errors.append(f"{where}: a committee gate names the committee")
        elif g["kind"] == "cog" and not (g.get("cog") and g.get("fallback")):
            errors.append(f"{where}: a cog gate names the cog and the role it falls back to")
        elif g["kind"] == "auto" and g.get("when") not in ("actions-succeed", *CONDITION_VOCABULARY.values()):
            errors.append(f"{where}: an automatic transition fires when its actions succeed or on a condition of the vocabulary")
        for v in t.get("validators") or []:
            if not isinstance(v, dict) or not set(v) & {"fields_set", "status_visited", "condition"}:
                errors.append(f"{where}: validator {v!r} is not one the hub checks (fields set, a status visited, a condition)")
    return errors
