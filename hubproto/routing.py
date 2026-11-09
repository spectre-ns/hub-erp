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
    if cond["field"] == "amount" and ctx.get("currency") not in [cond.get("currency")] + list(cond.get("also_currencies") or []):
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


_OP_WORDS = {">": ("over", "not over"), "<": ("under", "not under"), ">=": ("at least", "less than"), "<=": ("at most", "more than"),
             "==": ("", "not")}


def condition_words(cond: dict | None, ctx: dict) -> tuple[bool, str]:
    """Whether a routing condition holds for the item, and why, in words ("1,500 USD is not under 500 USD")."""
    from .settings import money

    if cond is None:
        return True, "it applies to every item"
    if "all" in cond:
        parts = [condition_words(c, ctx) for c in cond["all"]]
        return all(h for h, _ in parts), "; ".join(w for _, w in parts)
    field, value = cond["field"], ctx.get(cond["field"])
    yes, no = _OP_WORDS.get(cond["op"], (cond["op"], "not " + cond["op"]))
    if field == "amount":
        cur = cond.get("currency") or ""
        if value is None:
            return False, "the item has no amount"
        if ctx.get("currency") not in [cond.get("currency")] + list(cond.get("also_currencies") or []):
            return False, f"the item is in {ctx.get('currency') or 'no currency'}, this sentence in {cur}"
        holds = condition_holds(cond, ctx)
        return holds, f"{money(value)} {ctx.get('currency') or ''} is {yes if holds else no} {money(cond['value'])} {cur}".replace("  ", " ")
    if value is None:
        return False, f"the item has no {field.replace('_', ' ')}"
    holds = condition_holds(cond, ctx)
    return holds, f"its {field.replace('_', ' ')} is {value}" + ("" if holds else f", not {yes} {cond['value']}".replace("  ", " "))


JUDGED = ("review_passes", "adversarial_reviews_pass")  # decided at the Gate by the cog's own review, not stored on the item


def explain(bundle: dict, process: str, step: str, ctx: dict, decided_by: str | None = None) -> list[dict]:
    """How a Gate is routed for this item: every routing sentence for the step, in release order (the order the runner
    tries them), with whether it applies and why; for a cog, whether the item is within the cog's approval criteria. The
    first that applies (and, for a cog, is within its criteria) takes the item: ``taken``; later ones are not needed."""
    out, taken = [], False
    for rule in bundle.get("routing") or []:
        if rule["process"] != process or rule["step"] != step or rule.get("mode", "assign") != "assign":
            continue
        holds, why = condition_words(rule.get("condition"), ctx)
        row = {"rule": rule, "holds": holds, "why": why, "taken": False, "not_needed": taken}
        kind, _, name = rule["assignee"].partition(":")
        if holds and kind == "cog":
            crits = criteria_for(bundle, rule["assignee"], process)
            if not crits:
                row.update(within=False, criteria=["the cog has no approval criteria for it"])
            else:
                judged = [c for c in crits[0].get("conditions") or [] if c in JUDGED]
                facts = {**(ctx.get("conditions") or {}), **{c: True for c in judged}}  # its review is its own, at the Gate
                within, reasons = evaluate_criteria(crits[0], {**ctx, "conditions": facts})
                row.update(within=within, criteria=reasons, limit=crits[0], judged=bool(judged))
        if holds and not taken and row.get("within", True):
            row["taken"], taken = True, True
        out.append(row)
    if decided_by:  # what happened: the sentence whose assignee decided the Gate took it
        who = decided_by.split(":", 1)[-1]
        took = next((r for r in out if r["holds"] and (who in principals_for({"members": {}, **bundle}, r["rule"]["assignee"])
                                                       or r["rule"]["assignee"] == f"cog:{who}")), None)
        if took is not None:
            seen = False
            for r in out:
                r["taken"], r["not_needed"] = r is took, seen
                if r is took:
                    seen = True
                    if r.get("judged") is not None:
                        r.update(within=True, criteria=[])
                elif not seen and r["holds"] and r.get("judged"):  # a cog before it whose own review did not pass
                    r.update(within=False, criteria=r.get("criteria") or ["its review did not pass"])
    return out


def sla_days(bundle: dict, process: str, step: str):
    wf = bundle.get("workflows", {}).get(process, {})
    return wf.get("sla", {}).get(step)


def principals_for(bundle: dict, assignee: str) -> list[str]:
    kind, _, name = assignee.partition(":")
    if kind in ("person", "cog"):
        return [name] if name in bundle["members"] else []
    if kind == "group":  # a route to a group: any of its members (hubproto.groups)
        from .groups import group_members

        return sorted(group_members(bundle, name))
    return sorted(p for p, m in bundle["members"].items() if name in m["roles"])


def criteria_for(bundle: dict, approver: str, process: str) -> list[dict]:
    return [c for c in bundle["approval_criteria"] if c["approver"] == approver and c["process"] == process]


def evaluate_criteria(crit: dict, ctx: dict) -> tuple[bool, list[str]]:
    """Fixed approval criteria. Returns (within_criteria, reasons)."""
    reasons = []
    if crit.get("max_amount") is not None:
        amount = ctx.get("amount")
        if amount is None or ctx.get("currency") not in [crit.get("currency")] + list(crit.get("also_currencies") or []):
            reasons.append("amount or currency missing")
        elif not amount > 0:
            reasons.append("amount is not positive")
        elif not amount < crit["max_amount"]:
            from .settings import money

            reasons.append(f"amount {money(amount)} is not under {money(crit['max_amount'])} {crit['currency']}")
    facts = ctx.get("conditions", {})
    for c in crit.get("conditions", []):
        if not facts.get(c):
            reasons.append(condition_not_met(c, ctx))
    return (not reasons), reasons


def condition_not_met(key: str, ctx: dict) -> str:
    """A criterion's condition that does not hold, in the words of the policy sentence and with what the item says."""
    from .rules import CONDITION_VOCABULARY

    phrase = next((p for p, k in CONDITION_VOCABULARY.items() if k == key), key.replace("_", " "))
    if key in ("risk_low", "risk_not_high") and ctx.get("risk"):
        return f"“{phrase}” does not hold: the risk is {ctx['risk']}"
    if key == "change_minor" and ctx.get("change_type"):
        return f"“{phrase}” does not hold: it is a {ctx['change_type']} change"
    return f"“{phrase}” does not hold"


def threshold(bundle: dict, name: str, default=None):
    t = bundle["thresholds"].get(name)
    return t["value"] if t else default
