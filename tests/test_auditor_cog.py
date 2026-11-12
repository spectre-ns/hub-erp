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
    # Every permission that reaches it comes through the role: a person holding internal-auditor has the same reach.
    perms = authz.permissions_of(b, "auditor")
    assert perms and {p["via"] for p in perms} == {"role:internal-auditor"}, perms
    writes = sorted({(p["action"], p["object"], p["resource"]) for p in perms if p["action"] != "read"})
    assert writes == [("approve", "audit", "folder:Quality/Audits"), ("create", "audit", "folder:Quality/Audits"),
                      ("create", "document", "folder:Quality/Audits")], writes
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


def test_an_auditor_auditing_their_own_process_is_a_finding(released):
    hub = released
    run_id = hub.runner.submit("internal-audit", {"record": {"title": "Reviews", "processes": ["management-review"], "planned": "2026-12-01"}},
                               owner="bob")
    audit_no = hub.kernel.get(hub.runner.load(run_id)["record"])["number"]
    found = audit.audit_checks(hub)["checks"]["independence"]["findings"]
    assert [f["evidence"] for f in found] == [[audit_no]] and "its auditor bob owns" in found[0]["found"]


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
    run_id = hub.runner.submit("internal-audit", {"record": {"title": "NCR audit", "processes": ["nonconformance"], "planned": "2026-12-01"}},
                               owner="bob")
    fake = [{"id": "F-1", "clause": "8.7", "criterion": "c", "found": "f", "evidence": ["NCR-2099-0001"], "grade": "minor", "action": "ncr"}]
    with pytest.raises(RunError, match="NCR-2099-0001 does not exist"):
        hub.runner.decide(run_id, "bob", "approve", fields={"findings": fake}, meaning="recorded", payload_digest=seen(hub.runner, run_id))
    plain = [{"clause": "8.7", "statement": "In my own words", "severity": "minor"}]
    state = hub.runner.decide(run_id, "bob", "approve", fields={"findings": plain}, meaning="recorded", payload_digest=seen(hub.runner, run_id))
    assert state["pending"]["checkpoint"] == "accept-the-findings", "a person's own findings (no id) are theirs to stand by"


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
    run_id = hub.runner.submit("internal-audit", {"record": {"title": "NCR audit", "processes": ["nonconformance"], "planned": "2026-12-01"}},
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
    run_id = hub.runner.submit("internal-audit", {"record": {"title": "x", "processes": ["nonconformance"], "planned": "2026-12-01",
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
