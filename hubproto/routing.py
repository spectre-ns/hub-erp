"""Routing and approval criteria from a policy release.

``candidates`` returns, in release order, every routing rule whose condition
holds for the work item. The runner tries them in turn: a cog candidate may
decide only within its approval criteria; a role or person candidate puts
the item in those people's inbox.
"""

from __future__ import annotations

_OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}


def condition_holds(cond: dict | None, ctx: dict) -> bool:
    if cond is None:
        return True
    if "all" in cond:
        return all(condition_holds(c, ctx) for c in cond["all"])
    value = ctx.get(cond["field"])
    if value is None:
        return False
    if cond["field"] == "amount" and ctx.get("currency") != cond.get("currency"):
        return False
    return _OPS[cond["op"]](value, cond["value"])


def candidates(bundle: dict, process: str, step: str, ctx: dict) -> list[dict]:
    out = []
    for rule in bundle["routing"]:
        if rule["process"] != process or not condition_holds(rule["condition"], ctx):
            continue
        if rule["step"] == step or (step != "*" and rule["step"] == "*" and ctx.get("overdue")):
            out.append(rule)
    return out


def sla_days(bundle: dict, process: str, step: str):
    wf = bundle.get("workflows", {}).get(process, {})
    return wf.get("sla", {}).get(step)


def principals_for(bundle: dict, assignee: str) -> list[str]:
    kind, _, name = assignee.partition(":")
    if kind in ("person", "cog"):
        return [name] if name in bundle["members"] else []
    return sorted(p for p, m in bundle["members"].items() if name in m["roles"])


def criteria_for(bundle: dict, approver: str, process: str) -> list[dict]:
    return [c for c in bundle["approval_criteria"] if c["approver"] == approver and c["process"] == process]


def evaluate_criteria(crit: dict, ctx: dict) -> tuple[bool, list[str]]:
    """Fixed approval criteria. Returns (within_criteria, reasons)."""
    reasons = []
    if crit.get("max_amount") is not None:
        amount = ctx.get("amount")
        if amount is None or ctx.get("currency") != crit.get("currency"):
            reasons.append("amount or currency missing")
        elif not amount < crit["max_amount"]:
            reasons.append(f"amount {amount} is not under {crit['max_amount']} {crit['currency']}")
    facts = ctx.get("conditions", {})
    for c in crit.get("conditions", []):
        if not facts.get(c):
            reasons.append(f"condition {c} not met")
    return (not reasons), reasons


def threshold(bundle: dict, name: str, default=None):
    t = bundle["thresholds"].get(name)
    return t["value"] if t else default
