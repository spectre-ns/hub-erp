"""The auditor cog (issue #25): an internal auditor's seat and nothing more; the exact audit checks; findings that are
data, guarded against their evidence; the quality manager's verdict on each finding; actions raised only from accepted
findings; a bounded audit budget; the monthly duty."""

import json
from datetime import date, timedelta

import pytest

from conftest import seen
from hubproto import audit, authz
from hubproto.agent import Agent, AgentStore, due_duties, run_budget, run_duties
from hubproto.runner import RunError
from hubproto.track import Track

TOP = ("Engineering", "Finance", "Inventory", "Policy", "Purchasing", "Quality", "Tasks")


def bundle(hub):
    return hub.releases.bundle(hub.releases.current()["digest"])


def scripted(*scripts):
    """A stand-in for the model: each run (in order) calls its tools; a step may be a function of the earlier results."""
    queue = list(scripts)
    runs = []

    def fake(**kw):
        steps = queue.pop(0)
        calls, results = [], []
        for step in steps:
            name, args = step(results) if callable(step) else step
            res = kw["call_tool"](name, args)
            results.append(res)
            calls.append({"tool": name, "arguments": args, "ok": "error" not in res, "result": res})
        runs.append({"goal": kw["user"], "results": results})
        return {"text": "done", "stand_in": False, "served_model": "scripted", "model": "scripted", "tool_calls": calls}
    fake.runs = runs
    return fake


@pytest.fixture
def hosted(monkeypatch):
    """The auditor is a senior cog on a hosted model: tests switch hosted models on (no key: the stand-in answers)."""
    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")


# ---------------------------------------------------------------- the seat: an internal auditor's reach, nothing more
def test_the_auditor_cog_is_a_senior_cog_holding_only_the_internal_auditor_role(released):
    b = bundle(released)
    assert b["members"]["auditor"] == {**b["members"]["auditor"], "type": "cog", "roles": ["internal-auditor"]}
    assert b["cogs"]["auditor"]["model"] == "claude-opus-5-5", "a senior cog, on the hosted model like the other senior cogs"
    assert "auditor" not in (b["groups"]["senior-cogs"]["members"] or []), "it sits on no committee: it audits what the committee reads"
    assert "audit-sop" in b["cogs"]["auditor"]["follows"]
    for folder in TOP:
        assert authz.check(b, "auditor", "read", "*", folder)["allowed"], f"an internal auditor reads {folder}"
    # Every permission that reaches it comes through the role (a person holding internal-auditor has the same reach),
    # and through the cogs group only the tasks assigned to it (issue #73) and working documents (issue #74).
    perms = authz.permissions_of(b, "auditor")
    assert perms and {p["via"] for p in perms} == {"role:internal-auditor", "group:cogs"}, perms
    assert {(p["action"], p["resource"]) for p in perms if p["via"] == "group:cogs"} == {("approve", "folder:Tasks"), ("read", "folder:Tasks"),
                                                                                 ("create", "folder:Quality/Working Documents"),
                                                                                 ("read", "folder:Quality/Working Documents")}
    writes = sorted({(p["action"], p["object"], p["resource"]) for p in perms if p["action"] != "read"})
    assert writes == [("approve", "audit", "folder:Quality/Audits"), ("approve", "task", "folder:Tasks"),
                      ("create", "audit", "folder:Quality/Audits"), ("create", "document", "folder:Quality/Audits"),
                      ("create", "document", "folder:Quality/Working Documents")], writes
    for action, obj, folder in (("approve", "bill", "Finance/Payables"), ("create", "nonconformance", "Quality/Nonconformances"),
                                ("comment", "*", "Quality"), ("approve", "document", "Policy"), ("create", "change-order", "Quality/Changes"),
                                ("update", "stock-item", "Inventory/Stock")):
        assert not authz.check(b, "auditor", action, obj, folder)["allowed"], f"the auditor may not {action} {obj} in {folder}"
    assert not [c for c in b["approval_criteria"] if c["approver"] == "cog:auditor"], "it signs nothing within criteria"


def test_its_duty_is_monthly_and_its_budget_is_larger_but_bounded(released, monkeypatch):
    due = dict(due_duties(released, today="2026-11-07"))
    assert due["auditor"] == "Every month the auditor cog audits the next process in the programme."
    b = bundle(released)
    assert run_budget(b, "auditor") == {"actions": 30, "rounds": 40, "max_tokens": 2000}
    assert run_budget(b, "quality-assistant") == {"actions": 5, "rounds": 10, "max_tokens": 900}, "other cogs keep theirs"
    monkeypatch.setenv("HUBPROTO_AUDIT_ACTIONS", "5000")
    monkeypatch.setenv("HUBPROTO_AUDIT_ROUNDS", "0")
    assert run_budget(b, "auditor")["actions"] == audit.AUDIT_CEILING["actions"], "never past the ceiling"
    assert run_budget(b, "auditor")["rounds"] == 1, "never below one"
    assert run_budget(b, "auditor", max_actions=3)["actions"] == 3, "a caller may ask for less"


def test_without_its_hosted_model_the_audit_stays_due_and_nothing_stands_in(released, monkeypatch):
    monkeypatch.delenv("HUBPROTO_HOSTED_MODELS", raising=False)
    monkeypatch.setattr(released.model, "complete_with_tools", scripted(*[[("finish", {"summary": "ok"})]] * 6))
    out = {d["cog"]: d for d in run_duties(released, today="2026-11-07")}
    assert out["auditor"]["status"] == "unavailable" and "claude-opus-5-5" in out["auditor"]["summary"]
    assert "auditor" in dict(due_duties(released, today="2026-11-07")), "still due"
    assert not released.kernel.query(type_="audit")


def test_the_programme_never_offers_a_process_the_auditor_owns(released):
    rows = audit.programme(released, "auditor")
    owned = [r["process"] for r in rows if r["owned"]]
    assert owned == ["internal-audit"], "its only role has the audit's steps"
    assert rows[0]["owned"] is False and rows[-1]["process"] == "internal-audit"
    with pytest.raises(ValueError, match="owns internal-audit"):
        audit.plan_audit(released, "auditor", "internal-audit")
    assert {"internal-audit", "management-review"} <= audit.owned_processes(bundle(released), "bob"), "bob has steps in both"


# ---------------------------------------------------------------- the exact checks
def test_the_exact_checks_find_what_the_records_and_the_track_show(released, today):
    hub = released
    run_id = hub.runner.submit("nonconformance", {"record": {"title": "Burr on bracket", "description": "a safety risk found at inspection"}}, owner="alice")
    state = hub.runner.load(run_id)
    assert state["status"] == "waiting" and state["pending"]["due"]
    later = (today + timedelta(days=30)).isoformat()
    res = audit.audit_checks(hub, "nonconformance", today=later)
    ncr = hub.kernel.get(state["record"])
    overdue = res["checks"]["overdue"]["findings"]
    assert [f["evidence"][0] for f in overdue] == [ncr["number"]] and overdue[0]["evidence"][1].startswith(f"track:{run_id}#")
    assert res["checks"]["overdue"]["clause"] == "8.7", "the clause of the SOP that owns the process"
    assert audit.audit_checks(hub, "nonconformance", today=today.isoformat())["checks"]["overdue"]["count"] == 0, "not yet due"
    assert res["checks"]["self-approval"]["count"] == res["checks"]["skipped-routing"]["count"] == res["checks"]["chain"]["count"] == 0

    # A decision on the Track by someone the item was never routed to, who also prepared it.
    track = Track(hub.runner.root / run_id)
    ev = track.append({"type": "gate.decided", "run_id": run_id, "step": state["pending"]["step"], "by": "alice", "by_type": "person",
                       "verdict": "approve", "route": "role:quality-manager"})
    res = audit.audit_checks(hub, "nonconformance", today=today.isoformat())
    assert res["checks"]["skipped-routing"]["findings"][0]["evidence"] == [ncr["number"], f"track:{run_id}#{ev['seq']}"]
    assert "alice approved" in res["checks"]["self-approval"]["findings"][0]["found"]

    # An edited Track entry breaks the chain.
    lines = track.path.read_text().splitlines()
    first = json.loads(lines[0])
    first["by"] = "mallory"
    track.path.write_text("\n".join([json.dumps(first, sort_keys=True)] + lines[1:]) + "\n")
    chain = audit.audit_checks(hub, "nonconformance", today=today.isoformat())["checks"]["chain"]["findings"]
    assert chain and chain[0]["evidence"] == [f"track:{run_id}#1"] and "changed after it was written" in chain[0]["found"]

    # Clauses no Frame cites: the Frames cite ISO 9001 only, so every ISO 13485 clause is open; 9001 7.1.6 is open too.
    clauses = {f["standard"]: f for f in res["checks"]["clauses"]["findings"]}
    assert "4.2.3" in clauses["iso13485"]["clauses"] and "820.10" in clauses["21cfr820"]["clauses"] and "11.10" in clauses["21cfr11"]["clauses"]
    assert "9.2" not in clauses["iso9001"]["clauses"] and "7.5.3" not in clauses["iso9001"]["clauses"], "7.5 is cited, and covers 7.5.3"


def test_an_auditor_never_audits_a_process_they_own_whoever_files_it(released, hosted):
    """The audit SOP: "Auditors do not audit processes they own." Refused when the audit is filed, for a person and for
    the cog through the generic start_process tool; the independence check finds any audit that got in some other way."""
    from hubproto.agent import AgentTools

    hub = released
    with pytest.raises(RunError, match="bob owns management-review"):
        hub.runner.submit("internal-audit", {"record": {"title": "Reviews", "processes": ["management-review"], "planned": "2026-12-01"}}, owner="bob")
    with pytest.raises(RunError, match="bob owns .*internal-audit"):
        hub.runner.submit("internal-audit", {"record": {"title": "Everything", "planned": "2026-12-01"}}, owner="bob")
    t = AgentTools(hub, "auditor", "x2", "derived", 5, AgentStore(hub.root / "state" / "agents"))
    res = t.execute("start_process", {"process": "internal-audit", "fields": {"title": "Own", "processes": ["internal-audit"], "planned": "2026-12-01"}})
    assert "auditor owns internal-audit" in res["error"]
    assert not hub.kernel.query(type_="audit")
    # One filed behind the guards' back (straight into the kernel) is still found.
    forged = hub.kernel.create("audit", "Quality/Audits", {"title": "x", "processes": ["management-review"], "auditor": "bob"}, by="bob")
    found = audit.audit_checks(hub)["checks"]["independence"]["findings"]
    assert [f["evidence"] for f in found] == [[forged["number"]]] and "its auditor bob owns" in found[0]["found"]


def test_an_audit_is_filed_without_findings_verdicts_or_actions_by_any_path(released, hosted):
    """The reviewer's probe: the cog filing an audit through start_process with findings (one with no id, citing a record
    that does not exist) and the quality manager's verdicts already written in."""
    from hubproto.agent import AgentTools

    hub = released
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "x", "description": "y"}, by="carol")
    planted = {"findings": [{"id": "F-1", "clause": "8.7", "criterion": "c", "found": "f", "evidence": [ncr["number"]], "grade": "minor",
                             "action": "capa"}, {"clause": "8.7", "criterion": "c", "found": "f", "evidence": ["NCR-2099-0042"], "grade": "major"}],
               "finding_decisions": [{"finding": "F-1", "verdict": "accept", "by": "carol"}]}
    t = AgentTools(hub, "auditor", "x3", "derived", 5, AgentStore(hub.root / "state" / "agents"))
    for key in planted:
        res = t.execute("start_process", {"process": "internal-audit", "fields": {"title": "Planted", "processes": ["nonconformance"],
                                                                                  "planned": "2026-12-01", key: planted[key]}})
        assert "an audit starts without " + key in res["error"], res
    with pytest.raises(RunError, match="an audit starts without findings, finding_decisions"):
        hub.runner.submit("internal-audit", {"record": {"title": "x", "processes": ["capa"], **planted}}, owner="bob")
    assert not hub.kernel.query(type_="audit")


def test_the_findings_are_the_auditors_and_the_verdicts_the_quality_managers(released):
    """The reviewer's probes at the two Gates: the auditor writing verdicts, findings with no id, the quality manager
    rewriting the findings, a rejection with no reason, a verdict signed in someone else's name."""
    hub = released
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "x", "description": "y"}, by="carol")
    run_id = hub.runner.submit("internal-audit", {"record": {"title": "CAPA audit", "processes": ["capa"], "planned": "2026-12-01"}},
                               owner="bob")
    good = [{"id": "F-1", "clause": "8.7", "criterion": "c", "found": "f", "evidence": [ncr["number"]], "grade": "minor", "action": "capa"}]

    def decide(who, **fields):
        return hub.runner.decide(run_id, who, "approve", fields=fields, meaning="m", payload_digest=seen(hub.runner, run_id))
    with pytest.raises(RunError, match="the verdicts on the findings are the quality manager's"):
        decide("bob", findings=good, finding_decisions=[{"finding": "F-1", "verdict": "accept", "by": "carol"}])
    with pytest.raises(RunError, match="finding 1 has no id"):
        decide("bob", findings=[{"clause": "8.7", "statement": "In my own words", "severity": "minor"}])
    with pytest.raises(RunError, match="F-1 says it was recorded by carol"):
        decide("bob", findings=[{**good[0], "by": "carol"}])
    assert not hub.kernel.get(hub.runner.load(run_id)["record"])["fields"].get("findings"), "nothing refused reached the record"
    decide("bob", findings=good)
    rec = hub.kernel.get(hub.runner.load(run_id)["record"])
    assert rec["fields"]["findings"][0]["by"] == "bob" and rec["state"] == "awaiting-accept-the-findings"
    assert "finding_decisions" in hub.runner.allowed_decision_fields(hub.runner.load(run_id)), "the quality manager can still give hers"
    with pytest.raises(RunError, match="the findings stand as the auditor recorded them"):
        decide("carol", findings=[{**good[0], "evidence": ["NCR-2099-0001"]}], finding_decisions=[{"finding": "F-1", "verdict": "accept"}], summary="s")
    with pytest.raises(RunError, match="F-1 has no verdict"):
        decide("carol", summary="s")
    with pytest.raises(RunError, match="say why F-1 is rejected"):
        decide("carol", finding_decisions=[{"finding": "F-1", "verdict": "reject"}], summary="s")
    state = decide("carol", finding_decisions=[{"finding": "F-1", "verdict": "accept", "by": "bob"}], summary="s")
    rec = hub.kernel.get(rec["locator"])
    assert state["status"] == "completed" and rec["fields"]["finding_decisions"] == [{"finding": "F-1", "verdict": "accept", "reason": "", "by": "carol"}], \
        "the verdict is recorded as the decider's, whatever the payload said"
    assert rec["fields"]["findings"] == hub.kernel.get(rec["locator"])["fields"]["findings"] and rec["fields"]["actions_raised"][0]["process"] == "capa"


def test_training_not_assigned_when_a_document_took_effect_is_a_finding(released):
    from hubproto import sopdocs

    hub = released
    sopdocs.convert(hub, by="carol")  # the SOPs become documents in force, revision A; nobody is assigned their training

    def missing():
        found = [f for f in audit.audit_checks(hub)["checks"]["training"]["findings"] if doc["number"] in f["evidence"]]
        return found[0]["found"] if found else ""
    doc = sopdocs.in_force(hub.kernel, "audit-sop")
    assert "audit-sop, revision A" in missing() and "bob, carol" in missing(), "its table names the internal auditor and the quality manager"
    hub.kernel.create("training", "Quality/Training", {"person": "carol", "document": doc["locator"], "revision": doc["fields"]["revision"]}, by="hub")
    assert missing().endswith("no training was assigned to bob"), "carol's training is assigned now"


# ---------------------------------------------------------------- findings are data, guarded against their evidence
def test_a_finding_is_parsed_into_its_shape_and_its_evidence_must_exist(released):
    hub = released
    crit = audit.load_clauses(hub.kernel.config_dir)
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "x", "description": "y"}, by="carol")
    good = {"clause": "8.7", "criterion": "Nonconforming outputs are identified.", "found": "Identified.", "evidence": [ncr["number"]],
            "grade": "observation"}
    assert audit.validate_finding(good, kernel=hub.kernel, criteria=crit) == {**good, "action": "none", "source": "sample"}
    assert audit.validate_finding({**good, "clause": "iso13485:8.2.4", "evidence": [ncr["locator"]]}, kernel=hub.kernel, criteria=crit)["evidence"] == [ncr["number"]]
    for bad, words in (({**good, "evidence": ["NCR-2099-9999"]}, "NCR-2099-9999 does not exist"),
                       ({**good, "evidence": ["track:deadbeef#3"]}, "does not exist"),
                       ({**good, "evidence": []}, "cites its evidence"),
                       ({**good, "clause": "99.9"}, "not a clause of the audit criteria"),
                       ({**good, "grade": "catastrophic"}, "the grade is one of"),
                       ({**good, "action": "fire"}, "the action is one of"),
                       ({**good, "run": "rm -rf /"}, "has no field run"),
                       ("eval('1')", "is an object")):
        with pytest.raises(ValueError, match=words):
            audit.validate_finding(bad, kernel=hub.kernel, criteria=crit)
    hidden = hub.kernel.create("bill", "Finance/Payables", {"supplier": "acme-metals"}, by="alice") if "bill" in hub.kernel.types else None
    if hidden:
        with pytest.raises(ValueError, match="may not be read"):
            audit.validate_finding({**good, "evidence": [hidden["number"]]}, kernel=hub.kernel, criteria=crit, reader=lambda folder: folder.startswith("Quality"))


def test_the_gate_refuses_findings_citing_evidence_that_does_not_exist_whatever_path_they_take(released):
    hub = released
    run_id = hub.runner.submit("internal-audit", {"record": {"title": "CAPA audit", "processes": ["capa"], "planned": "2026-12-01"}},
                               owner="bob")
    fake = [{"id": "F-1", "clause": "8.7", "criterion": "c", "found": "f", "evidence": ["NCR-2099-0001"], "grade": "minor", "action": "ncr"}]
    with pytest.raises(RunError, match="NCR-2099-0001 does not exist"):
        hub.runner.decide(run_id, "bob", "approve", fields={"findings": fake}, meaning="recorded", payload_digest=seen(hub.runner, run_id))
    assert not hub.kernel.get(hub.runner.load(run_id)["record"])["fields"].get("findings"), "refused before the fields are set"


# ---------------------------------------------------------------- the whole audit, the cog signing nothing
def test_the_auditor_cog_runs_the_audit_and_the_quality_manager_decides_each_finding(released, hosted, monkeypatch):
    hub = released
    ncr = hub.kernel.get(hub.runner.load(hub.runner.submit("nonconformance", {"record": {"title": "Scratched lid", "description": "a safety risk at goods-in"}},
                                                           owner="alice"))["record"])

    def first_sample(results):
        item = next(r for r in results if "items" in r)["items"][0]
        return ("record_finding", {"number": audit_no[0], "clause": "8.7", "criterion": "ncr-sop: the quality manager dispositions within 5 days.",
                                   "found": f"{item['number']} waits for its disposition.", "evidence": [item["number"]], "grade": "minor",
                                   "action": "ncr", "source": "sample"})

    audit_no = []
    fake = scripted(
        [("audit_programme", {}), ("plan_audit", {"process": "nonconformance"}), ("finish", {"summary": "planned"})],   # the duty run: it plans the audit
        [("audit_checks", {"process": "nonconformance"}), ("sample_records", {"process": "nonconformance", "n": 3}),  # its step, nested
         lambda r: ("audit_checks", {}), first_sample,
         lambda r: ("record_finding", {"number": audit_no[0], "clause": "8.7", "criterion": "c", "found": "made up", "evidence": ["NCR-2099-0042"],
                                       "grade": "major"}),
         lambda r: ("record_finding", {"number": audit_no[0], "clause": "iso13485:4.2.3", "criterion": "A medical device file per device type.",
                                       "found": "No Frame cites it.", "evidence": [f"release:{hub.releases.current()['digest'][:16]}"],
                                       "grade": "observation", "source": "check"}),
         lambda r: ("draft_audit_report", {"number": audit_no[0]}),
         lambda r: ("submit_findings", {"number": audit_no[0], "summary": "two findings"}),
         ("finish", {"summary": "recorded"})])
    # Its step runs inside plan_audit (the queue is inline in tests): the audit's number is known once it is filed.
    def wrapped(**kw):
        if not audit_no and "next process" not in kw["user"]:
            audit_no.append(hub.kernel.query(type_="audit")[0]["number"])
        return fake(**kw)
    monkeypatch.setattr(hub.model, "complete_with_tools", wrapped)
    done = {d["cog"]: d for d in run_duties(hub, cog="auditor", today="2026-11-07")}
    assert done["auditor"]["status"] == "done", done

    rec = next(r for r in hub.kernel.query(type_="audit"))
    run = hub.runner.run_for_record(rec["locator"])
    assert rec["state"] != "failed", [(e.get("reason"), e.get("candidate")) for e in hub.runner.track(run["id"]) if e["type"] in ("run.failed", "gate.skipped")]
    assert rec["fields"]["auditor"] == "auditor" and rec["fields"]["processes"] == ["nonconformance"]
    assert rec["state"] == "awaiting-accept-the-findings" and run["pending"]["assignees"] == ["carol"]
    nested = fake.runs[0]["results"]  # its step ran inside plan_audit (the queue is inline in tests)
    assert "does not exist" in nested[4]["error"], "the made-up evidence was refused"
    assert [f["id"] for f in rec["fields"]["findings"]] == ["F-1", "F-2"]
    assert rec["fields"]["findings"][0] == {**rec["fields"]["findings"][0], "clause": "8.7", "grade": "minor", "action": "ncr",
                                             "evidence": [ncr["number"]], "by": "auditor"}
    report = next(d for d in hub.dms.documents_of(rec["locator"]) if d["fields"].get("doc_type") == "audit-report")
    assert report["state"] == "draft" and report["fields"]["author"] == "auditor"
    text = hub.dms.content(report["locator"])[0].decode()
    assert "F-1" in text and "ISO 13485:2016 4.2.3 Medical device file" in text and "not a certification" in text

    # Its step was its own assigned work, recorded as completed; it approved nothing.
    decided = [e for e in hub.runner.track(run["id"]) if e["type"] == "gate.decided"]
    assert [(e["by"], e["step"], e["route"]) for e in decided] == [("auditor", "record-the-findings", "field:auditor")]
    assert decided[0]["meaning"].startswith("completed:")
    with pytest.raises(RunError):
        hub.runner.decide(run["id"], "auditor", "approve", meaning="accept", payload_digest=seen(hub.runner, run["id"]))
    assert audit.audit_checks(hub, "internal-audit")["checks"]["self-approval"]["count"] == 0

    # The quality manager decides each finding; the accepted one that needs action becomes a nonconformance.
    with pytest.raises(ValueError, match="accept or reject F-2"):
        audit.accept_findings(hub, rec["number"], "carol", {"F-1": {"verdict": "accept"}}, summary="s", meaning="m",
                              payload_digest=seen(hub.runner, run["id"]))
    with pytest.raises(RunError, match="F-1, F-2 have no verdict"):
        hub.runner.decide(run["id"], "carol", "approve", fields={"summary": "s"}, meaning="m", payload_digest=seen(hub.runner, run["id"]))
    with pytest.raises(ValueError, match="say why F-2 is rejected"):
        audit.accept_findings(hub, rec["number"], "carol", {"F-1": {"verdict": "accept"}, "F-2": {"verdict": "reject"}}, summary="s",
                              meaning="m", payload_digest=seen(hub.runner, run["id"]))
    state = audit.accept_findings(hub, rec["number"], "carol", {"F-1": {"verdict": "accept"}, "F-2": {"verdict": "reject", "reason": "out of scope"}},
                                  summary="One minor finding; an NCR is raised.", meaning="findings reviewed",
                                  payload_digest=seen(hub.runner, run["id"]))
    assert state["status"] == "completed"
    rec = hub.kernel.get(rec["locator"])
    assert rec["state"] == "closed" and [d["verdict"] for d in rec["fields"]["finding_decisions"]] == ["accept", "reject"]
    raised = rec["fields"]["actions_raised"]
    assert [(r["finding"], r["process"]) for r in raised] == [("F-1", "nonconformance")], "only the accepted one, never the rejected"
    made = hub.kernel.get(raised[0]["record"])
    assert made["fields"]["source"] == "audit" and rec["number"] in made["fields"]["title"]
    assert hub.runner.run_for_record(made["locator"])["owner"] == "carol", "raised on the quality manager's decision"
    assert any(l["target"] == rec["locator"] for l in made["links"])
    people = [d for d in rec["decisions"] if d.get("by_type") == "person"]
    assert [d["by"] for d in people] == ["carol"], "the only signature on the findings is a person's"
    evs = AgentStore(hub.root / "state" / "agents").runs(cog="auditor")
    assert {e["trigger"] for e in evs} == {"duty", "assigned"}
    after = audit.audit_checks(hub)["checks"]
    assert after["skipped-routing"]["count"] == after["self-approval"]["count"] == 0, "every decision here was routed, none self-approved"


def test_another_cog_cannot_use_the_audit_tools(released):
    from hubproto.agent import AgentTools

    t = AgentTools(released, "quality-assistant", "x1", "derived", 5, AgentStore(released.root / "state" / "agents"))
    assert "an internal auditor's" in t.execute("audit_checks", {})["error"]
    assert "error" in t.execute("plan_audit", {"process": "nonconformance"})
    assert not released.kernel.query(type_="audit")


# ---------------------------------------------------------------- the page: the quality manager's verdicts
def test_the_quality_manager_accepts_or_rejects_each_finding_on_the_audit_page(released):
    from test_shell_views import get, plain, post
    from hubproto.shell import serve_in_thread

    hub = released
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "Loose screw", "description": "y"}, by="carol")
    run_id = hub.runner.submit("internal-audit", {"record": {"title": "CAPA audit", "processes": ["capa"], "planned": "2026-12-01"}},
                               owner="bob")
    findings = audit.number_findings([
        {"clause": "8.7", "criterion": "Dispositioned within 5 days.", "found": "Late.", "evidence": [ncr["number"]], "grade": "minor", "action": "capa"},
        {"clause": "9.2", "criterion": "Programme kept.", "found": "Kept.", "evidence": [ncr["number"]], "grade": "opportunity", "action": "none"}], "bob")
    hub.runner.decide(run_id, "bob", "approve", fields={"findings": findings}, meaning="recorded", payload_digest=seen(hub.runner, run_id))
    loc = hub.runner.load(run_id)["record"]
    server = serve_in_thread(hub, "127.0.0.1", 0)
    shell = {"base": f"http://127.0.0.1:{server.server_address[1]}", "hub": hub}
    try:
        status, page = get(shell, "/record/" + loc, **{"as": "carol"})
        text = plain(page)
        assert status == 200 and "Findings" in text and "ISO 9001:2015 8.7 Control of nonconforming outputs" in text
        assert "name='v-F-1'" in page and "Sign the verdicts" in text
        status, page = get(shell, "/record/" + loc, **{"as": "alice"})
        assert "name='v-F-1'" not in page and "to decide" in plain(page), "only the quality manager it waits for decides"
        digest = seen(hub.runner, run_id)
        status, url, body = post(shell, "/audit-accept", {"as": "carol", "record": loc, "digest": digest, "v-F-1": "accept", "v-F-2": "reject",
                                                          "summary": "One minor finding.", "meaning": "verdicts as marked"})
        assert "say why F-2 is rejected" in plain(body), plain(body)[:400]
        status, url, body = post(shell, "/audit-accept", {"as": "carol", "record": loc, "digest": digest, "v-F-1": "accept", "v-F-2": "reject",
                                                          "r-F-2": "not a finding", "summary": "One minor finding.", "meaning": "verdicts as marked"})
        assert status == 200 and "Signed the verdicts" in plain(body) and "1 action(s) were raised" in plain(body), plain(body)[:600]
    finally:
        server.shutdown()
    rec = hub.kernel.get(loc)
    assert rec["state"] == "closed" and rec["fields"]["actions_raised"][0]["process"] == "capa"
    capa = hub.kernel.get(rec["fields"]["actions_raised"][0]["record"])
    assert capa["type"] == "capa" and capa["fields"]["owner"] == "carol"


def test_only_a_cog_in_the_internal_auditors_seat_can_be_named_the_auditor(released, hosted):
    hub = released
    run_id = hub.runner.submit("internal-audit", {"record": {"title": "x", "processes": ["capa"], "planned": "2026-12-01",
                                                             "auditor": "quality-assistant"}}, owner="bob")
    skipped = [e for e in hub.runner.track(run_id) if e["type"] == "gate.skipped"]
    assert skipped and "quality-assistant is a cog that does not hold the internal auditor role" in skipped[0]["reason"]
    assert hub.runner.load(run_id)["status"] == "failed", "naming a cog gives it nothing it does not have"


def test_an_audit_the_cog_does_not_finish_goes_to_a_person_in_the_auditors_seat(released, hosted, monkeypatch):
    hub = released
    monkeypatch.setattr(hub.model, "complete_with_tools", scripted([("finish", {"summary": "ran out of time"})]))
    out = audit.plan_audit(hub, "auditor", "nonconformance")
    state = hub.runner.load(out["run"])
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["bob"] and state["pending"]["handed_back_by"] == "auditor"
    assert any(e["type"] == "gate.handed_back" and e["to"] == "bob" and "ran out of time" in e["reason"] for e in hub.runner.track(out["run"]))


# ---------------------------------------------------------------- review findings 5 to 11
def test_a_contributor_co_signing_with_an_independent_approver_is_no_self_approval(tmp_path, monkeypatch):
    """it-sop: "Whoever made a direct configuration change on a change-order is not its only approver." carol signs her
    own change order alongside bob, who is independent: permitted, so not a finding."""
    from test_workflow_graph_change_orders import test_a_contributor_signs_a_change_order_only_alongside_an_independent_approver as signed
    from hubproto.app import Hub

    signed(tmp_path, monkeypatch)
    hub = Hub(tmp_path)
    co = hub.kernel.query(type_="change-order")[0]
    found = [f for f in audit.audit_checks(hub)["checks"]["self-approval"]["findings"] if co["number"] in f["evidence"]]
    assert found == [], found


def test_the_checks_read_each_track_once_until_it_changes(released, monkeypatch):
    from hubproto import track as track_module

    hub = released
    for i in range(3):
        hub.runner.submit("nonconformance", {"record": {"title": f"t{i}", "description": "a safety risk"}}, owner="alice")
    audit.audit_checks(hub)
    reads = []
    real = track_module.Track.verify  # verified once per change; the view parses the bytes it hashed, not Track.read
    monkeypatch.setattr(track_module.Track, "verify", lambda self, events=None: reads.append(self.path) or real(self, events))
    audit.audit_checks(hub)
    audit.audit_checks(hub, "nonconformance")
    assert reads == [], "an unchanged Track is not read or verified again"
    run_id = hub.runner.list()[-1]["id"]
    track_module.Track(hub.runner.root / run_id).append({"type": "note", "run_id": run_id})
    reads.clear()
    audit.audit_checks(hub)
    assert [p.parent.name for p in reads] == [run_id], "a Track that changed is read again"


def test_the_checks_and_the_findings_keep_to_what_the_auditor_may_read(released, today, hosted):
    from hubproto.agent import AgentTools

    hub = released
    run_id = hub.runner.submit("nonconformance", {"record": {"title": "Burr", "description": "a safety risk"}}, owner="alice")
    later = (today + timedelta(days=30)).isoformat()
    assert audit.audit_checks(hub, today=later)["checks"]["overdue"]["count"] == 1
    no_quality = lambda folder: not folder.startswith("Quality")
    assert audit.audit_checks(hub, today=later, reader=no_quality)["checks"]["overdue"]["count"] == 0
    ncr = hub.kernel.get(hub.runner.load(run_id)["record"])
    crit = audit.load_clauses(hub.kernel.config_dir)
    seq = hub.runner.track(run_id)[-1]["seq"]
    with pytest.raises(ValueError, match="may not be read"):
        audit.validate_finding({"clause": "8.7", "criterion": "c", "found": "f", "evidence": [f"track:{run_id}#{seq}"], "grade": "minor"},
                               kernel=hub.kernel, criteria=crit, reader=no_quality)
    # The record_finding tool holds the cog to its own reading: here it may not read Quality.
    hub.queue.mode = "service"  # its step waits in its queue instead of being worked at once
    out = audit.plan_audit(hub, "auditor", "nonconformance")
    t = AgentTools(hub, "auditor", "x4", "derived", 5, AgentStore(hub.root / "state" / "agents"))
    t.may_read = lambda folder: folder in ("Quality/Audits",)
    res = t.execute("record_finding", {"number": out["audit"], "clause": "8.7", "criterion": "c", "found": "f", "evidence": [ncr["number"]],
                                       "grade": "minor"})
    assert "may not be read" in res.get("error", ""), res
    assert "an internal auditor's" not in str(t.execute("audit_checks", {"process": "nonconformance"}))
    assert t.execute("audit_checks", {"process": "nonconformance", "check": "overdue"})["checks"]["overdue"]["count"] == 0


def test_a_caller_cannot_lift_the_audit_budget_past_its_ceiling(released):
    b = bundle(released)
    assert run_budget(b, "auditor", max_actions=500, max_rounds=900) == {**run_budget(b, "auditor"), "actions": 80, "rounds": 100}


def test_a_hand_back_goes_only_to_people_who_may_take_the_step(released):
    hub = released
    run_id = hub.runner.submit("task", {"record": {"title": "Tidy", "assignee": "qa-manager"}}, owner="quality-assistant")
    state = hub.runner.load(run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["qa-manager"], "its own run could not hand it back either"
    with pytest.raises(RunError, match="there is no person to hand it back to"):
        hub.runner.hand_back(run_id, "qa-manager", "not mine")
    assert "alice" not in (hub.runner.load(run_id).get("pending") or {}).get("assignees", []), "never an arbitrary person holding staff"


def test_a_duty_failing_for_another_reason_is_not_reported_as_unavailable(released, monkeypatch):
    def boom(**kw):
        raise ValueError("boom")
    monkeypatch.setattr(released.model, "complete_with_tools", boom)
    with pytest.raises(ValueError, match="boom"):
        run_duties(released, cog="management", today="2026-11-07")


def test_only_the_auditor_is_offered_the_audit_tools(released, hosted):
    from hubproto.agent_audit import AUDIT_TOOLS

    mine = {s["name"] for s in Agent(released, "auditor").prepare("look", by="carol")["specs"]}
    theirs = {s["name"] for s in Agent(released, "quality-assistant").prepare("look", by="carol")["specs"]}
    assert AUDIT_TOOLS <= mine and not (AUDIT_TOOLS & theirs)


# ---------------------------------------------------------------- re-review findings N1 to N5
def test_the_chain_check_sees_an_in_place_change_to_a_kernel_event(released):
    hub = released
    hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "x", "description": "y"}, by="carol")
    assert audit.audit_checks(hub)["checks"]["chain"]["count"] == 0
    seq = hub.kernel.db.execute("SELECT MIN(seq) FROM events WHERE chain IS NOT NULL").fetchone()[0]
    hub.kernel.db.execute("UPDATE events SET by = 'mallory' WHERE seq = ?", (seq,))
    hub.kernel.db.commit()
    found = audit.audit_checks(hub)["checks"]["chain"]["findings"]
    assert any(f["evidence"] == [f"event:{seq}"] for f in found), "the count and the last event did not change: no cache may hide it"


def test_the_chain_check_sees_a_same_length_track_edit_with_its_time_put_back(released):
    import os

    hub = released
    run_id = hub.runner.submit("task", {"record": {"title": "closed one", "assignee": "alice"}}, owner="alice")
    assert audit.audit_checks(hub)["checks"]["chain"]["count"] == 0
    path = hub.runner.root / run_id / "track.jsonl"
    st = os.stat(path)
    text = path.read_text()
    assert '"run.submitted"' in text
    path.write_text(text.replace('"run.submitted"', '"run.submittex"', 1))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert os.stat(path).st_size == st.st_size and os.stat(path).st_mtime_ns == st.st_mtime_ns
    found = audit.audit_checks(hub)["checks"]["chain"]["findings"]
    assert any(f["evidence"] == [f"track:{run_id}#1"] for f in found), found


@pytest.mark.parametrize("process", ["purchase-request", "purchase-to-pay"])
def test_a_routing_sentence_makes_its_assignee_an_owner(released, process):
    """"Approvals in purchase-request over 10,000 USD go to the controller.": bob, the controller, owns purchase requests
    and bills, so he audits neither. An overdue escalation owns nothing."""
    hub = released
    b = bundle(hub)
    assert process in audit.owned_processes(b, "bob")
    with pytest.raises(RunError, match=f"bob owns {process}"):
        hub.runner.submit("internal-audit", {"record": {"title": "x", "processes": [process], "planned": "2026-12-01"}}, owner="bob")
    assert "calibration" not in audit.owned_processes(b, "bob"), "receiving calibration's overdue items is not owning it"


def test_a_policy_owner_owns_the_change_order_process(released):
    """it-sop: the policy owners decide a change order's items. That happens in change control, not at a runner step or
    through a Routing sentence, so it is checked on its own: bob, a policy owner, audits no change orders."""
    hub = released
    b = bundle(hub)
    assert "policy-owner" in authz.roles_of(b, "bob")
    assert "change-order" in audit.owned_processes(b, "bob")
    with pytest.raises(RunError, match="bob owns change-order"):
        hub.runner.submit("internal-audit", {"record": {"title": "x", "processes": ["change-order"], "planned": "2026-12-01"}},
                          owner="bob")
    assert "change-order" not in audit.owned_processes(b, "auditor"), "the auditor cog holds no policy-owner role"


def test_the_track_view_parses_the_bytes_it_hashed(released, monkeypatch):
    """The cache key is a hash of one read of the file; the entries must come from that same read, never a second one
    (a write landing between two reads would pair the old hash with new entries)."""
    hub = released
    run_id = hub.runner.submit("internal-audit", {"record": {"title": "x", "processes": ["capa"], "planned": "2026-12-01"}},
                               owner="bob")
    audit._TRACKS.clear()
    monkeypatch.setattr(Track, "read", lambda self: (_ for _ in ()).throw(AssertionError("a second read of the Track")))
    view = audit.track_view(hub, run_id)
    assert view["events"] and not view["problems"]


def test_record_the_findings_refuses_an_auditor_who_owns_the_process_or_cannot_read_the_evidence(released, monkeypatch):
    """The Gate's own checks, behind the filing guard: an audit's processes changed after it was filed, and evidence its
    auditor may not read."""
    hub = released
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "x", "description": "y"}, by="carol")
    good = [{"id": "F-1", "clause": "8.7", "criterion": "c", "found": "f", "evidence": [ncr["number"]], "grade": "minor"}]

    def file():
        run_id = hub.runner.submit("internal-audit", {"record": {"title": "x", "processes": ["capa"], "planned": "2026-12-01"}},
                                   owner="bob")
        return run_id, hub.runner.load(run_id)["record"]
    run_id, loc = file()
    hub.kernel.set_fields(loc, {"processes": ["management-review"]}, by="hub")  # behind the filing guard's back
    with pytest.raises(RunError, match="bob owns management-review"):
        hub.runner.decide(run_id, "bob", "approve", fields={"findings": good}, meaning="m", payload_digest=seen(hub.runner, run_id))
    run_id, loc = file()
    real = audit.reader_for
    monkeypatch.setattr(audit, "reader_for", lambda b, who: (lambda folder: folder != "Quality/Nonconformances") if who == "bob" else real(b, who))
    with pytest.raises(RunError, match="may not be read"):
        hub.runner.decide(run_id, "bob", "approve", fields={"findings": good}, meaning="m", payload_digest=seen(hub.runner, run_id))


def test_the_refusal_at_accept_says_what_the_quality_manager_can_do(released):
    hub = released
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "x", "description": "y"}, by="carol")
    run_id = hub.runner.submit("internal-audit", {"record": {"title": "x", "processes": ["capa"], "planned": "2026-12-01"}}, owner="bob")
    good = [{"id": "F-1", "clause": "8.7", "criterion": "c", "found": "f", "evidence": [ncr["number"]], "grade": "minor"}]
    hub.runner.decide(run_id, "bob", "approve", fields={"findings": good}, meaning="m", payload_digest=seen(hub.runner, run_id))
    with pytest.raises(RunError) as e:
        hub.runner.decide(run_id, "carol", "approve", fields={"findings": [{**good[0], "found": "other"}]}, meaning="m",
                          payload_digest=seen(hub.runner, run_id))
    assert "reject a finding that is wrong" in str(e.value) and "ends it as rejected, and file a new one" in str(e.value)
    assert "send the audit back" not in str(e.value)
    state = hub.runner.decide(run_id, "carol", "reject", meaning="wrong scope", payload_digest=seen(hub.runner, run_id))
    assert hub.kernel.get(state["record"])["state"] == "rejected", "as the message says: rejecting ends the audit"
