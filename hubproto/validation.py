"""Validation of configuration changes, in proportion to their risk (the IT SOP; computer software assurance).

The IT SOP's "## Validation" section says, in sentences the compiler reads into the release (rule kinds
``validation_class`` and ``validation_need``; the bundle's ``validation``), which changes are high, medium or low risk
and what each class needs before the change order is approved:

    Widenings are high risk.
    Changes to permissions, approval criteria, ... are high risk.
    Changes to routing, due dates, ... are medium risk.
    Changes to form layouts and wording are low risk.
    High-risk changes need a test record.
    Medium-risk changes need a test note.

The hub runs on what the release in force says, never on the prose; only when the release states no validation classes
are the hub's built-in defaults used, and ``basis`` (and every requirement) says so.

* ``classify(item)``: "high", "medium" or "low" for one delta item (a change of a rule, a wording change, or a change
  order's form, record-type or workflow change);
* ``requirements(hub, co)``: what is still missing before the change order may be approved ("high-risk items need test
  evidence: ..."), which workstream 1's approval readiness calls;
* ``record_evidence(hub, co, ...)``: record a test record (a scripted check: what was run, expected and actual result,
  pass or fail) or a test note against items of the change order; a document of type test-evidence attached to the
  change order counts as a test record too.
"""

from __future__ import annotations

import re

from .release import utcnow

RISKS = ("low", "medium", "high")
NEEDS = {"test record": "test-record", "test records": "test-record", "test note": "test-note", "test notes": "test-note",
         "the review of the delta": "review", "review of the delta": "review", "no test": "review"}
# What the IT SOP's words name, as the kinds of delta items they cover.
WORDS = {
    "widenings": {"widening"}, "permissions": {"permission"}, "approval criteria": {"approval_criteria"},
    "segregation of duties": {"sod"}, "members": {"member"}, "roles": {"role"}, "members and roles": {"member", "role"},
    "groups": {"group"}, "committees": {"committee"}, "cog profiles": {"cog"}, "a cog's model or persona": {"cog", "persona"},
    "personas": {"persona"}, "record types": {"record_type"}, "workflow gates": {"gate"}, "workflows": {"gate", "workflow"},
    "routing": {"routing"}, "due dates": {"sla"}, "processes": {"process"}, "thresholds": {"threshold"}, "limits": {"threshold"},
    "required fields": {"required_field"}, "duties": {"duty"}, "folders": {"folder"}, "scopes": {"scope"},
    "responsibilities": {"responsibility"}, "roles and responsibilities": {"responsibility"}, "form layouts": {"form"},
    "forms": {"form"}, "wording": {"text"},
}
DEFAULT_CLASSES = {
    "widening": "high", "permission": "high", "approval_criteria": "high", "sod": "high", "member": "high", "role": "high",
    "group": "high", "committee": "high", "cog": "high", "persona": "high", "record_type": "high", "gate": "high", "workflow": "high",
    "routing": "medium", "sla": "medium", "process": "medium", "threshold": "medium", "required_field": "medium", "duty": "medium",
    "folder": "medium", "scope": "medium", "responsibility": "medium", "form": "low", "text": "low",
}
DEFAULT_NEEDS = {"high": "test-record", "medium": "test-note", "low": "review"}
EVIDENCE_TYPE = "test-evidence"

def kinds_named(words: str) -> set[str]:
    """The item kinds a list of the IT SOP's words names ("permissions, approval criteria and segregation of duties")."""
    text, out = " " + words.lower() + " ", set()
    for key in sorted(WORDS, key=len, reverse=True):
        pat = re.compile(r"(?<![a-z'])" + re.escape(key) + r"(?![a-z])")
        if pat.search(text):
            out |= WORDS[key]
            text = pat.sub(" ", text)
    return out


_WORD_OF = {"widening": "widenings", "permission": "permissions", "approval_criteria": "approval criteria", "sod": "segregation of duties",
            "member": "members", "role": "roles", "group": "groups", "committee": "committees", "cog": "cog profiles", "persona": "personas",
            "record_type": "record types", "gate": "workflow gates", "workflow": "workflows", "routing": "routing", "sla": "due dates",
            "process": "processes", "threshold": "thresholds", "required_field": "required fields", "duty": "duties", "folder": "folders",
            "scope": "scopes", "responsibility": "responsibilities", "form": "form layouts", "text": "wording"}


def kind_words(kinds: list[str]) -> str:
    """The words for item kinds, as a validation sentence lists them ("permissions, routing and wording")."""
    words = [_WORD_OF.get(k, k.replace("_", " ")) for k in kinds]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1] if words else ""


def policy(hub=None, bundle: dict | None = None) -> dict:
    """{"classes": {kind: risk}, "needs": {risk: need}, "source": "release"|"default", "basis": words}: the validation
    classes the release in force states (compiled from the IT SOP), or the hub's built-in defaults when it states none."""
    if bundle is None and hub is not None:
        bundle = hub.current_bundle() or {}
    v = (bundle or {}).get("validation") or {}
    if not v.get("classes"):
        return {"classes": dict(DEFAULT_CLASSES), "needs": dict(DEFAULT_NEEDS), "source": "default",
                "basis": "the hub's built-in defaults: the release in force states no validation classes"}
    frames = sorted({s_.get("frame") for s_ in v.get("sources") or [] if s_.get("frame")})
    return {"classes": dict(v["classes"]), "needs": {**DEFAULT_NEEDS, **(v.get("needs") or {})}, "source": "release",
            "basis": "the validation classes of the release in force (" + ", ".join(frames) + ")"}


def basis(hub) -> str:
    """Where the classes come from, for the change order's page: the release in force, or the built-in defaults."""
    return policy(hub)["basis"]


def _kinds(item: dict) -> set[str]:
    """The kinds an item is about: its rule's kind, a widening, a workflow gate (a process whose checkpoints change),
    or the change order's form, record-type and workflow changes."""
    if item.get("kind") in ("form", "record_type", "workflow", "gate"):
        return {item["kind"]}
    rule = item.get("rule") or {}
    kind = rule.get("kind") or ("text" if item.get("op") == "text" else "")
    if kind == "retire":  # retiring a document is its wording leaving; the rules its Frame stated are items of their own
        kind = "text"
    out = {kind} if kind else set()
    if item.get("widening"):
        out.add("widening")
    if kind == "process":
        def cps(r):
            return {s.get("id") for s in (r or {}).get("steps") or [] if s.get("kind") == "checkpoint"}
        before = item.get("changes_from") or item.get("changed_to")
        if before is None or cps(before) != cps(rule):
            out.add("gate")
    return out


def classify(item: dict, pol: dict | None = None) -> str:
    """"high", "medium" or "low": the highest class any of the item's kinds has (unknown kinds count as medium)."""
    pol = pol or {"classes": DEFAULT_CLASSES}
    risks = [pol["classes"].get(k, "medium") for k in _kinds(item)] or ["medium"]
    return max(risks, key=RISKS.index)


def items_of(hub, co: dict) -> list[dict]:
    """Every item the change order would change: the staged delta, and its form, record-type and workflow changes."""
    from . import changecontrol

    out = [dict(i) for i in changecontrol.delta(hub, co["locator"]) if i.get("state") != "rejected"]
    f = co.get("fields") or {}
    out += [{"id": f"form:{n}", "kind": "form", "reason": f"form layout {n}"} for n in sorted(f.get("form_changes") or {})]
    out += [{"id": f"record-type:{i}", "kind": "record_type", "reason": f"record type change {(c or {}).get('type') or i}"}
            for i, c in enumerate(f.get("record_type_changes") or [])]
    wc = f.get("workflow_changes") or {}
    out += [{"id": f"workflow:{n}", "kind": "workflow", "reason": f"workflow {n}"} for n in (sorted(wc) if isinstance(wc, dict) else range(len(wc)))]
    return out


def annotate(items: list[dict], pol: dict | None = None) -> list[dict]:
    """The items with their risk class ("risk"), what that class needs ("validation") and where the classes come from
    ("validation_basis")."""
    pol = pol or policy()
    return [{**i, "risk": classify(i, pol), "validation": pol["needs"].get(classify(i, pol), "review"),
             "validation_basis": pol.get("basis", "")} for i in items]


def _co(hub, co_ref) -> dict:
    co = co_ref if isinstance(co_ref, dict) and co_ref.get("locator") else hub.kernel.get(co_ref)
    if co is None or co.get("type") != "change-order":
        raise ValueError(f"{co_ref!r} is not a change order")
    return co


def evidence(hub, co_ref) -> list[dict]:
    """The validation evidence on a change order: recorded test records and notes, and attached test-evidence documents
    (each a test record; its result field, when set, says pass or fail)."""
    co = _co(hub, co_ref)
    out = [dict(e) for e in (co["fields"].get("validation_evidence") or [])]
    for d in hub.dms.documents_of(co["locator"]):
        if (d.get("fields") or {}).get("doc_type") == EVIDENCE_TYPE:
            out.append({"kind": "test-record", "document": d["number"], "result": str(d["fields"].get("result") or "pass").lower(),
                        "text": f"{d['number']} {d['fields'].get('title') or ''}".strip() + (f": {d['fields']['evidence_note']}" if d["fields"].get("evidence_note") else ""),
                        "items": list(d["fields"].get("items") or []), "by": d["fields"].get("author"), "at": d.get("created_at")})
    return out


def record_evidence(hub, co_ref, *, by: str, kind: str, text: str, result: str = "pass", items: list[str] | None = None,
                    expected: str = "", actual: str = "") -> dict:
    """Record validation evidence on a change order: a test record (``kind`` "test-record": what was run, what was
    expected and what happened, pass or fail) or a test note ("test-note": what was tried and what was seen). ``items``
    names the delta items it covers (none: all of them). Returns the entry."""
    co = _co(hub, co_ref)
    if kind not in ("test-record", "test-note"):
        raise ValueError("evidence is a test-record (a scripted check) or a test-note (an unscripted try-out)")
    if (hub.current_bundle() or {}).get("members", {}).get(by) is None:
        raise ValueError(f"{by} is not a member of the organization")
    if not (text or "").strip():
        raise ValueError("say what was checked and what was seen")
    result = (result or "").lower()
    if result not in ("pass", "fail"):
        raise ValueError("the result is pass or fail")
    if kind == "test-record" and not (expected.strip() and actual.strip()):
        raise ValueError("a test record says what was expected and what actually happened")
    entry = {"kind": kind, "text": " ".join(text.split()), "result": result, "items": list(items or []), "by": by, "at": utcnow(),
             **({"expected": expected.strip(), "actual": actual.strip()} if kind == "test-record" else {})}
    hub.kernel.set_fields(co["locator"], {"validation_evidence": list(co["fields"].get("validation_evidence") or []) + [entry]}, by=by)
    return entry


def attach_evidence(hub, co_ref, *, by: str, filename: str, content: bytes, title: str = "", result: str = "pass",
                    items: list[str] | None = None, text: str = "") -> dict:
    """Attach a test report (a file) to a change order as validation evidence: it is filed in the change order's folder
    as a document of type test-evidence, with its result (pass or fail), the delta items it covers (none: every item)
    and what it shows, and counts as a test record (``evidence``). Returns the document."""
    co = _co(hub, co_ref)
    from . import eco

    if eco.finished(co):
        raise ValueError(f"{co['number']} is finished: evidence is recorded before approval")
    if (hub.current_bundle() or {}).get("members", {}).get(by) is None:
        raise ValueError(f"{by} is not a member of the organization")
    if not filename or not content:
        raise ValueError("choose the test report to attach")
    result = (result or "").lower()
    if result not in ("pass", "fail"):
        raise ValueError("the result is pass or fail")
    doc = hub.dms.create(title=title or filename, doc_class="external", folder=co["folder"], by=by, content=content,
                         filename=filename, doc_type=EVIDENCE_TYPE,
                         fields={"result": result, "items": list(items or []), "evidence_note": " ".join((text or "").split())},
                         provenance={"channel": "shell", "sender": by, "received_at": utcnow(), "evidence_for": co["number"]},
                         attach_to=co["locator"])
    return doc


def _covers(e: dict, item_id: str) -> bool:
    return not e.get("items") or item_id in e["items"]


def requirements(hub, co_ref, items: list[dict] | None = None) -> list[str]:
    """What the change order still needs before approval, from the IT SOP's Validation section: one sentence per unmet
    requirement (empty when every high-risk item has a passing test record and every medium-risk item a test note or
    record). A failing test record on an item is a reason of its own."""
    co = _co(hub, co_ref)
    pol = policy(hub)
    its = annotate(items if items is not None else items_of(hub, co), pol)
    ev = evidence(hub, co)
    out = []
    failed = [i for i in its if any(e["result"] == "fail" and _covers(e, i["id"]) for e in ev)]
    for i in failed:
        out.append(f"a test of {i.get('reason') or i['id']} failed: fix the change or record a passing test")
    need_record = [i for i in its if i["validation"] == "test-record" and i not in failed
                   and not any(e["kind"] == "test-record" and e["result"] == "pass" and _covers(e, i["id"]) for e in ev)]
    need_note = [i for i in its if i["validation"] == "test-note" and i not in failed
                 and not any(e["result"] == "pass" and _covers(e, i["id"]) for e in ev)]
    if need_record:
        out.append(f"{len(need_record)} high-risk item(s) need test evidence (" + "; ".join((i.get("reason") or i["id"])[:80] for i in need_record[:4])
                   + ("; ..." if len(need_record) > 4 else "") + "): on the change order's Validation section, record a test record (what was run, expected and actual "
                   "result, pass or fail) or attach a test report as evidence")
    if need_note:
        out.append(f"{len(need_note)} medium-risk item(s) need a test note (" + "; ".join((i.get("reason") or i["id"])[:80] for i in need_note[:4])
                   + ("; ..." if len(need_note) > 4 else "") + "): record what was tried and what was seen")
    if out and pol["source"] == "default":
        out = [x + f" (classed by {pol['basis']})" for x in out]
    return out
