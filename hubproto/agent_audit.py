"""Audit tools for a cog in the internal auditor's seat (the auditor cog, issue #25).

Looks (free, read-only, no model): ``audit_programme`` (what to audit next), ``audit_checks`` (the exact findings
from the records and the Track), ``sample_records`` (a deterministic sample of a process's items with the Track
entries of their decisions).

Writes (each counts against the run's budget and is decided from the SOPs like any other): ``plan_audit`` starts
the internal-audit process (internal auditors may create audits); ``record_finding`` and ``submit_findings`` are the
auditor's own step at "Record the findings" (it may approve audits, and the step is assigned to it); and
``draft_audit_report`` files the report as a draft document on the audit (internal auditors may create documents in
Quality/Audits). Findings are held for the run and go onto the audit only with the step's completion, through the
decision, so nothing changes a record that waits at a Gate behind its back. Every finding is validated, its evidence
resolved, before it is kept (``hubproto.audit.validate_finding``)."""

from __future__ import annotations

from . import audit
from .agent import register
from .runner import RunError

AUDIT_TOOLS = {"audit_programme", "audit_checks", "sample_records", "plan_audit", "record_finding", "submit_findings", "draft_audit_report"}
FINDING_PROPS = {
    "clause": {"type": "string", "description": "the clause: ISO 9001 by number (8.7); others as iso13485:8.2.4, 21cfr820:820.35, 21cfr11:11.10"},
    "criterion": {"type": "string", "description": "what was expected: quote the SOP sentence or name the requirement"},
    "found": {"type": "string", "description": "what you found, plainly"},
    "evidence": {"type": "array", "items": {"type": "string"},
                 "description": "record numbers and Track entries (track:<run>#<seq>) exactly as the tools gave them"},
    "grade": {"type": "string", "enum": list(audit.GRADES)},
    "action": {"type": "string", "enum": list(audit.ACTIONS), "description": "ncr or capa to propose one (the quality manager decides); default none"},
    "process": {"type": "string"}, "title": {"type": "string", "description": "a short title"},
    "source": {"type": "string", "enum": ["check", "sample"], "description": "check: from audit_checks; sample: your reading of the records"},
}


def _held(t) -> dict:
    """The findings this run has recorded so far, by audit: held for the run (on its Track), never kept elsewhere."""
    if not hasattr(t, "audit_findings"):
        t.audit_findings = {}
    return t.audit_findings


def _audit_of(t, number: str) -> dict | None:
    rec = t._find(str(number or ""))
    return rec if rec is not None and rec["type"] == "audit" else None


def _own_step_intent(t, a: dict) -> dict:
    rec = _audit_of(t, a.get("number"))
    if rec is None:
        return {"error": f"no audit {a.get('number')!r} that {t.cog} may read"}
    try:
        audit.waiting_audit(t.hub, rec["locator"], t.cog, audit.RECORD_STEP)
    except ValueError as e:
        return {"error": f"{e}: findings are recorded only at your own step"}
    return {"action": "approve", "object": "audit", "folder": rec["folder"]}


def _plan_intent(t, a: dict) -> dict:
    proc = (t.bundle.get("processes") or {}).get(audit.PROCESS) or {}
    return {"action": "create", "object": proc.get("object") or "audit", "folder": proc.get("folder") or "Quality/Audits"}


def _report_intent(t, a: dict) -> dict:
    rec = _audit_of(t, a.get("number"))
    if rec is None:
        return {"error": f"no audit {a.get('number')!r} that {t.cog} may read"}
    return {"action": "create", "object": "document", "folder": rec["folder"]}


# ---------------------------------------------------------------- looks
def _auditor_only(fn):
    """The audit looks read across the hub: they are an internal auditor's, as the role's read permissions are."""
    def look(t, a: dict) -> dict:
        if audit.AUDIT_ROLE not in set((t.bundle.get("members") or {}).get(t.cog, {}).get("roles") or []):
            return {"error": f"{fn.__name__.replace('_', ' ')} is an internal auditor's; {t.cog} is not one"}
        return fn(t, a)
    look.__name__ = fn.__name__
    return look


@_auditor_only
def audit_programme(t, a: dict) -> dict:
    rows = audit.programme(t.hub, t.cog)
    nxt = next((r for r in rows if not r["owned"]), None)
    return {"next": nxt["process"] if nxt else None, "programme": rows[:40],
            "note": "audit the first process that is not yours (owned: one of your roles decides a step in it)"}


def _checks(t, process: str | None) -> dict:
    """The checks for this run, computed once per process and paged from (the tool's own cache, for this run only:
    the records may change, the run sees them as they were when it first looked)."""
    cache = t.__dict__.setdefault("audit_checks_cache", {})
    if process not in cache:
        cache[process] = audit.audit_checks(t.hub, process, reader=t.may_read)
    return cache[process]


@_auditor_only
def audit_checks(t, a: dict) -> dict:
    process = str(a.get("process") or "") or None
    if process and process not in (t.bundle.get("processes") or {}):
        return {"error": f"there is no process {process!r} in the policy release"}
    res = _checks(t, process)
    only = str(a.get("check") or "")
    if only and only not in res["checks"]:
        return {"error": f"there is no check {only!r}; the checks are {', '.join(res['checks'])}"}
    offset = max(0, int(a.get("offset") or 0))
    shown = {}
    for name, c in res["checks"].items():
        if only and name != only:
            continue
        n = 25 if only else 4
        shown[name] = {**c, "findings": c["findings"][offset: offset + n], "more": max(0, c["count"] - offset - n)}
    return {**res, "checks": shown, "note": "each finding here is exact; cite its evidence as given. Ask for one check (check=, offset=) to see more."}


@_auditor_only
def sample_records(t, a: dict) -> dict:
    try:
        return audit.sample(t.hub, str(a.get("process") or ""), int(a.get("n") or 5), reader=t.may_read)
    except ValueError as e:
        return {"error": str(e)}


# ---------------------------------------------------------------- writes
def plan_audit(t, a: dict) -> dict:
    try:
        return {"ok": True, **audit.plan_audit(t.hub, t.cog, str(a.get("process") or ""), title=str(a.get("title") or ""))}
    except (ValueError, RunError) as e:
        return {"error": str(e)}


def record_finding(t, a: dict) -> dict:
    rec = _audit_of(t, a.get("number"))
    raw = {k: v for k, v in a.items() if k != "number"}
    try:
        f = audit.validate_finding(raw, kernel=t.hub.kernel, criteria=audit.load_clauses(t.hub.kernel.config_dir), reader=t.may_read)
    except ValueError as e:
        return {"error": f"the finding was not recorded: {e}"}
    held = _held(t).setdefault(rec["locator"], [])
    held.append(f)
    return {"ok": True, "finding": f"F-{len(held)}", "recorded": len(held),
            "note": "held for this run; submit_findings puts them on the audit and completes your step"}


def submit_findings(t, a: dict) -> dict:
    rec = _audit_of(t, a.get("number"))
    held = _held(t).get(rec["locator"]) or []
    try:
        after = audit.submit_findings(t.hub, rec["locator"], t.cog, held, str(a.get("summary") or ""))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "findings": len(held), "status": after["status"], "waiting_for": (after.get("pending") or {}).get("assignees")}


def draft_audit_report(t, a: dict) -> dict:
    rec = _audit_of(t, a.get("number"))
    findings = rec["fields"].get("findings") or audit.number_findings(_held(t).get(rec["locator"]) or [], t.cog)
    try:
        procs = rec["fields"].get("processes") or []
        doc = audit.draft_report(t.hub, rec["locator"], t.cog, findings, checks=_checks(t, procs[0] if len(procs) == 1 else None))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "document": doc["number"], "state": doc["state"],
            "note": "a draft on the audit; a person sends it through document control"}


register({"name": "audit_programme", "description": "The audit programme: every process with when it was last audited, the next one to audit "
                                                    "first; processes you own are marked and never yours to audit.",
          "parameters": {"type": "object", "properties": {}}}, audit_programme, write=False)
register({"name": "audit_checks", "description": "The exact checks (no model): items overdue against their due date, approvals by whoever prepared "
                                                 "the item, decisions by someone it was not routed to, training not assigned when a document took "
                                                 "effect, the hash chains, clauses no Frame cites, auditors auditing their own processes. Each result "
                                                 "cites its evidence.",
          "parameters": {"type": "object", "properties": {"process": {"type": "string", "description": "one process; default the whole hub"},
                                                         "check": {"type": "string", "description": "one check, to see all its findings"},
                                                         "offset": {"type": "integer"}}}}, audit_checks, write=False)
register({"name": "sample_records", "description": "A sample of a process's items (spread over its history) with their state, run and the Track "
                                                   "entries of their decisions, to read against the SOP.",
          "parameters": {"type": "object", "properties": {"process": {"type": "string"}, "n": {"type": "integer", "description": "1 to 10, default 5"}},
                         "required": ["process"]}}, sample_records, write=False)
register({"name": "plan_audit", "description": "Plan an audit of one process (never one you own): starts the internal-audit process with you as "
                                               "the auditor; the audit then waits for you to record the findings.",
          "parameters": {"type": "object", "properties": {"process": {"type": "string"}, "title": {"type": "string"}}, "required": ["process"]}},
         plan_audit, write=True, intent=_plan_intent)
register({"name": "record_finding", "description": "Record one finding on the audit waiting for you at Record the findings: the clause, what was "
                                                   "expected, what was found, the evidence (which must exist) and the grade. Refused when the evidence "
                                                   "does not exist.",
          "parameters": {"type": "object", "properties": {"number": {"type": "string", "description": "the audit (AUD-...)"}, **FINDING_PROPS},
                         "required": ["number", "clause", "criterion", "found", "evidence", "grade"]}},
         record_finding, write=True, intent=_own_step_intent)
register({"name": "submit_findings", "description": "Complete Record the findings with the findings you recorded in this run; the quality manager "
                                                    "then accepts or rejects each one.",
          "parameters": {"type": "object", "properties": {"number": {"type": "string"}, "summary": {"type": "string"}}, "required": ["number", "summary"]}},
         submit_findings, write=True, intent=_own_step_intent)
register({"name": "draft_audit_report", "description": "Draft the audit report as a document on the audit (a draft; a person approves it): the "
                                                       "findings and the exact checks.",
          "parameters": {"type": "object", "properties": {"number": {"type": "string"}}, "required": ["number"]}},
         draft_audit_report, write=True, intent=_report_intent)
