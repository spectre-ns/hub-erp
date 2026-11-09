"""Validation of configuration changes, in proportion to their risk (the IT SOP; computer software assurance).

The IT SOP's "## Validation" section says, in sentences, which changes are high, medium or low risk and what each class
needs before the change order is approved:

    Widenings are high risk.
    Changes to permissions, approval criteria, ... are high risk.
    Changes to routing, due dates, ... are medium risk.
    Changes to form layouts and wording are low risk.
    High-risk changes need a test record.
    Medium-risk changes need a test note.

The section is read from the IT SOP's text in the release in force (a change to it is a change order like any other);
the hub's defaults below are used when a release has no such section.

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

_CLASS = re.compile(r"^(?:Changes to )?(?P<what>.+?) are (?P<risk>high|medium|low) risk\.$", re.I)
_NEED = re.compile(r"^(?P<risk>High|Medium|Low)-risk changes need (?:an? )?(?P<need>.+?)\.$")


def _kinds_named(words: str) -> set[str]:
    """The item kinds a list of the IT SOP's words names ("permissions, approval criteria and segregation of duties")."""
    text, out = " " + words.lower() + " ", set()
    for key in sorted(WORDS, key=len, reverse=True):
        pat = re.compile(r"(?<![a-z'])" + re.escape(key) + r"(?![a-z])")
        if pat.search(text):
            out |= WORDS[key]
            text = pat.sub(" ", text)
    return out


def policy(hub=None, text: str | None = None) -> dict:
    """{"classes": {kind: risk}, "needs": {risk: need}, "source": "it-sop"|"default"}: from the IT SOP's Validation
    section in the release in force (or ``text``), the hub's defaults for anything it does not say."""
    if text is None and hub is not None:
        try:
            meta = hub.releases.current()
            text = hub.releases.frame_texts(meta["digest"]).get("it-sop") if meta else None
        except Exception:
            text = None
        if text is None:
            try:
                text = hub.released_frames().get("it-sop").text
            except Exception:
                text = None
    classes, needs, found = dict(DEFAULT_CLASSES), dict(DEFAULT_NEEDS), False
    inside = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            inside = line.lower() == "## validation"
            continue
        if not inside or not line:
            continue
        m = _NEED.match(line)
        if m:
            need = NEEDS.get(m.group("need").lower())
            if need:
                needs[m.group("risk").lower()] = need
                found = True
            continue
        m = _CLASS.match(line)
        if m:
            for kind in _kinds_named(m.group("what")):
                classes[kind] = m.group("risk").lower()
                found = True
    return {"classes": classes, "needs": needs, "source": "it-sop" if found else "default"}


def _kinds(item: dict) -> set[str]:
    """The kinds an item is about: its rule's kind, a widening, a workflow gate (a process whose checkpoints change),
    or the change order's form, record-type and workflow changes."""
    if item.get("kind") in ("form", "record_type", "workflow", "gate"):
        return {item["kind"]}
    rule = item.get("rule") or {}
    kind = rule.get("kind") or ("text" if item.get("op") == "text" else "")
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
    """The items with their risk class ("risk") and what that class needs ("validation")."""
    pol = pol or {"classes": DEFAULT_CLASSES, "needs": DEFAULT_NEEDS}
    return [{**i, "risk": classify(i, pol), "validation": pol["needs"].get(classify(i, pol), "review")} for i in items]


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
                   + ("; ..." if len(need_record) > 4 else "") + "): attach a document of type test-evidence to the change order, or record "
                   "a test record (what was run, expected and actual result, pass or fail)")
    if need_note:
        out.append(f"{len(need_note)} medium-risk item(s) need a test note (" + "; ".join((i.get("reason") or i["id"])[:80] for i in need_note[:4])
                   + ("; ..." if len(need_note) > 4 else "") + "): record what was tried and what was seen")
    return out
