"""Settings as SOP edits.

Once the SOPs are the documents the Frame is compiled from, a change made in Settings is not a separate source of
rules: it is written into the SOP the rule belongs to, as an edit to a draft revision carried on a change order. A
changed rule replaces its sentence (the line its source names), a removed rule deletes it, and a new rule is added to
the section of its kind ("## Routing", "## Approval criteria", ...) in the SOP that defines its process (or, for people,
roles and folders, the organization model). The change order's staged Frame then shows whether each sentence reads
back as the rule that was set.
"""

from __future__ import annotations

from .rules import rule_key
from .settings import sentence

SECTION = {"permission": "Permissions", "routing": "Routing", "sla": "Routing", "approval_criteria": "Approval criteria",
           "sod": "Segregation of duties", "threshold": "Thresholds", "member": "Members", "role": "Roles", "folder": "Folders"}
SECTIONED = {"cog": "Cog: {name}", "process": "Process: {name}"}  # rules written as a whole section


def slot(rule: dict) -> tuple:
    """What a rule is about, so a changed rule replaces the one it changes rather than adding a second."""
    k = rule.get("kind")
    if k == "permission":
        return (k, rule.get("subject"), rule.get("action"), rule.get("object"), rule.get("resource"), rule.get("effect"))
    if k == "routing":
        return (k, rule.get("process"), rule.get("step"), repr(rule.get("condition")))
    if k == "sla":
        return (k, rule.get("process"), rule.get("step"))
    if k == "approval_criteria":
        return (k, rule.get("approver"), rule.get("process"), tuple(sorted(rule.get("conditions") or [])), rule.get("countersign"))
    if k == "threshold":
        return (k, rule.get("name"))
    if k == "sod":
        return (k, rule.get("object"), tuple(rule.get("roles") or []))
    if k == "member":
        return (k, rule.get("principal"))
    if k in ("cog", "process", "role"):
        return (k, rule.get("name"))
    if k == "folder":
        return (k, rule.get("path"))
    return (k, rule_key(rule))


def _frame_of(rule: dict) -> str:
    src = rule.get("source") or {}
    return src.get("frame") if src.get("frame") not in (None, "", "settings") else ""


def home_frame(rule: dict, base_rules: list[dict]) -> str:
    """The SOP a new rule is written into."""
    k = rule.get("kind")
    procs = {r["name"]: _frame_of(r) for r in base_rules if r.get("kind") == "process"}
    if k in ("routing", "sla", "approval_criteria") and procs.get(rule.get("process")):
        return procs[rule["process"]]
    if k == "process":
        return procs.get(rule.get("name")) or next((f for f in procs.values() if f), "")
    if k in ("member", "role", "folder", "cog"):
        same = next((_frame_of(r) for r in base_rules if r.get("kind") == k and _frame_of(r)), "")
        if same:
            return same
    if k == "permission":
        same = next((_frame_of(r) for r in base_rules if r.get("kind") == "permission" and r.get("resource") == rule.get("resource") and _frame_of(r)), "")
        if same:
            return same
        folder = str(rule.get("resource") or "").partition(":")[2]
        owner = next((_frame_of(r) for r in base_rules if r.get("kind") == "process" and str(r.get("folder") or "") == folder), "")
        if owner:
            return owner
    if k == "sod":
        owner = next((_frame_of(r) for r in base_rules if r.get("kind") == "process" and r.get("object") == rule.get("object")), "")
        if owner:
            return owner
    if k == "threshold":
        same = next((_frame_of(r) for r in base_rules if r.get("kind") == "threshold" and _frame_of(r)), "")
        if same:
            return same
    return next((_frame_of(r) for r in base_rules if r.get("kind") == "member" and _frame_of(r)), "org-model")


def _lines(rule: dict, processes: dict) -> list[str]:
    return sentence(rule, processes).splitlines()


def plan(base_rules: list[dict], new_rules: list[dict], processes: dict) -> dict[str, list[dict]]:
    """{SOP name: [edit operations]} that turn the base rules into the new ones, sentence by sentence."""
    base = {rule_key(r): r for r in base_rules}
    new = {rule_key(r): r for r in new_rules}
    removed = [r for k, r in base.items() if k not in new]
    added = [r for k, r in new.items() if k not in base]
    ops: dict[str, list[dict]] = {}
    for a in added:
        old = next((r for r in removed if slot(r) == slot(a) and _frame_of(r)), None) or \
            next((r for r in removed if r.get("kind") == a.get("kind") and a.get("kind") in SECTIONED and r.get("name") == a.get("name") and _frame_of(r)), None)
        if old is not None:
            removed.remove(old)
            frame = _frame_of(old)
            if old["kind"] in SECTIONED:
                op = {"op": "replace_section", "target": SECTIONED[old["kind"]].format(name=old["name"]), "lines": _lines(a, processes)[1:]}
            else:
                op = {"op": "replace_paragraph", "target": old["source"]["text"], "lines": _lines(a, processes)}
            ops.setdefault(frame, []).append({**op, "rule": a, "was": old})
            continue
        frame = home_frame(a, base_rules)
        if a["kind"] in SECTIONED:
            op = {"op": "insert", "at": "end", "lines": _lines(a, processes)}
        else:
            op = {"op": "insert", "section_end": SECTION.get(a["kind"], "Rules"), "lines": _lines(a, processes)}
        ops.setdefault(frame, []).append({**op, "rule": a})
    for r in removed:
        frame = _frame_of(r)
        if not frame:
            continue
        if r["kind"] in SECTIONED:
            op = {"op": "delete_section", "target": SECTIONED[r["kind"]].format(name=r["name"])}
        else:
            op = {"op": "delete_paragraph", "target": r["source"]["text"]}
        ops.setdefault(frame, []).append({**op, "was": r})
    return ops


def apply_plan(hub, co_ref: str, plan_ops: dict[str, list[dict]], *, by: str) -> list[dict]:
    """Write the edits into draft revisions on the change order. Returns a report per edit."""
    from . import edits, eco

    report = []
    for frame, ops in sorted(plan_ops.items()):
        draft = eco.add_sop(hub, co_ref, frame, by=by)
        content, v = hub.dms.content(draft["locator"])
        ext = (v.get("filename") or "x.docx").rsplit(".", 1)[-1].lower()
        for op in ops:
            clean = {k: x for k, x in op.items() if k not in ("rule", "was")}
            out, rep = edits.apply_ops(content, ext, [clean])
            if out is None and clean["op"] == "insert" and clean.get("section_end"):
                # The SOP has no section for this kind of rule yet: add one at the end.
                clean = {"op": "insert", "at": "end", "lines": [f"## {clean['section_end']}"] + list(clean["lines"])}
                out, rep = edits.apply_ops(content, ext, [clean])
            ok = out is not None
            if ok:
                content = out
            report.append({"sop": frame, "draft": draft["number"], "ok": ok, "detail": (rep[0]["detail"] if rep else ""),
                           "says": " / ".join(op.get("lines") or []) or "(removed)",
                           "was": (op.get("was") or {}).get("source", {}).get("text")})
        hub.dms.add_version(draft["locator"], content, by=by, filename=v.get("filename") or f"{frame}.docx",
                            mime=v.get("mime") or "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            note="changes made in Settings")
    return report
