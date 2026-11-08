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
    "cog": {"name", "model", "prompt", "follows", "reviews", "prompt_from", "references", "autonomy", "duties"},
    # A scoping Frame: the SOP and policy Frames it compiles, in order, its level, and the scopes it layers on.
    "scope": {"name", "level", "compiles", "layers_on"},
    # A persona (a cog or a job role): the scopes and Frames it layers, in order (later layers add and may only narrow).
    "persona": {"name", "of", "layers"},
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
    if kind == "permission" and rule.get("effect") not in ("allow", "deny"):
        errors.append("permission: effect must be allow or deny")
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
    if kind == "process" and not rule.get("steps"):
        errors.append("process: needs at least one step")
    return errors
