"""The ISO 9001 seed profile: install, compile clean, release, and run every process."""

import shutil
from pathlib import Path

import pytest

from conftest import ROOT, seen
from hubproto.app import Hub
from hubproto.profile import conformance, install, load_profile
from hubproto.runner import RunError

PROCESSES = {"document-control", "training-acknowledgement", "nonconformance", "capa", "internal-audit",
             "supplier-evaluation", "change-control", "management-review", "calibration", "complaint", "task"}


def fresh_hub(tmp_path: Path) -> Hub:
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    return hub


def unseeded_frames(tmp_path: Path) -> Path:
    """The example company as it was before `hubproto seed iso9001`: no iso9001/ directory,
    the original document-control SOP under quality/, nothing in .replaced/. Works whether or
    not the repository itself has been seeded."""
    root = tmp_path / "frames"
    shutil.copytree(ROOT / "frames", root)
    shutil.rmtree(root / "iso9001", ignore_errors=True)
    original = root / "quality" / "document-control-sop.frame.md"
    if not original.exists():
        moved = root / ".replaced" / "quality" / "document-control-sop.frame.md.replaced"
        source = moved if moved.exists() else Path(load_profile("iso9001")["dir"]) / "frames" / "document-control-sop.frame.md"
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, original)
    shutil.rmtree(root / ".replaced", ignore_errors=True)
    return root


def compile_clean(hub: Hub) -> dict:
    proposal = hub.compile()
    errors = [f for f in proposal["findings"] if f["severity"] == "error"]
    assert errors == [], [f"{f['guard']}: {f['detail']} ({f['frame']}:{f['line']})" for f in errors]
    return proposal


def release_profile(hub: Hub) -> dict:
    proposal = compile_clean(hub)
    hub.review(proposal["id"], "bob", approve_all=True)
    hub.review(proposal["id"], "carol", approve_all=True)
    return hub.release(proposal["id"], "bob")


@pytest.fixture
def seeded(tmp_path) -> Hub:
    """The example company with the ISO 9001 profile installed and released."""
    hub = fresh_hub(tmp_path)
    install(hub, "iso9001", force=True, log=None)
    release_profile(hub)
    return hub


def run_state(hub: Hub, run_id: str) -> dict:
    return hub.runner.load(run_id)


def events(hub: Hub, run_id: str) -> list[str]:
    return [e["type"] for e in hub.runner.track(run_id)]


# ----------------------------------------------------------------- install and compile

def test_profile_lists_every_frame_and_the_clause_table():
    profile = load_profile("iso9001")
    pdir = Path(profile["dir"])
    for item in profile["frames"] + profile["templates"] + [profile["org_model_template"]]:
        assert (pdir / item).exists(), item
    names = {Path(f).name for f in profile["frames"]}
    for required in ("quality-manual", "document-control-sop", "training-sop", "ncr-sop", "capa-sop", "audit-sop",
                     "supplier-sop", "change-control-sop", "management-review-sop", "calibration-sop",
                     "complaint-sop", "tasks-sop"):
        assert f"{required}.frame.md" in names
    assert len([n for n in names if n.startswith("wi-")]) == 8
    assert profile["clauses"]["4.1"].startswith("Understanding the organization")
    assert profile["clauses"]["10.3"] == "Continual improvement"
    assert "quality-manager" in profile["required_roles"] and profile["overlays"] == []


def test_install_replaces_the_example_sop_and_skips_existing_files(tmp_path):
    root = unseeded_frames(tmp_path)
    hub = Hub(tmp_path)
    said = []
    paths = install(hub, "iso9001", log=said.append)
    assert (root / "iso9001" / "ncr-sop.frame.md").exists()
    assert (root / "iso9001" / "templates" / "ncr.md").exists()
    moved = root / ".replaced" / "quality" / "document-control-sop.frame.md.replaced"
    assert moved.exists() and not (root / "quality" / "document-control-sop.frame.md").exists()
    assert any("replaced Frame 'document-control-sop'" in s for s in said)
    assert hub.frames.get("document-control-sop").path == root / "iso9001" / "document-control-sop.frame.md"
    # The example company has an org-model, so the template is not installed.
    assert not (root / "iso9001" / "org-model.frame.md").exists()
    assert any("template not installed" in s for s in said)
    assert len(paths) == len(load_profile("iso9001")["frames"]) + len(load_profile("iso9001")["templates"])
    # A second install keeps what is there; force rewrites it.
    assert install(hub, "iso9001", log=None) == []
    assert len(install(hub, "iso9001", force=True, log=None)) == len(paths)


def test_org_model_template_is_installed_on_a_fresh_hub_and_demands_members(tmp_path):
    (tmp_path / "frames").mkdir()
    hub = Hub(tmp_path)
    install(hub, "iso9001", log=None)
    template = tmp_path / "frames" / "iso9001" / "org-model.frame.md"
    assert template.exists() and hub.frames.get("org-model").path == template
    assert "## Members" in template.read_text()
    proposal = hub.compile()
    errors = [f["detail"] for f in proposal["findings"] if f["severity"] == "error"]
    assert any("fill the org model" in d for d in errors), errors
    assert proposal["reviewers"] == [], "no policy owner exists until the org model is filled"


def test_profile_compiles_with_no_errors_and_releases(tmp_path):
    hub = fresh_hub(tmp_path)
    install(hub, "iso9001", force=True, log=None)
    proposal = compile_clean(hub)
    assert not [n for n in proposal["notes"] if "unparsed" in n["note"]], proposal["notes"]
    assert not [f for f in proposal["findings"] if "no routing sentence" in f["detail"]]
    hub.review(proposal["id"], "bob", approve_all=True)
    hub.review(proposal["id"], "carol", approve_all=True)
    hub.release(proposal["id"], "bob")
    bundle = hub.current_bundle()
    assert PROCESSES | {"purchase-to-pay"} <= set(bundle["ops"]), "every profile process compiled (other Frames may add more)"
    assert bundle["thresholds"]["effectiveness-check-interval"]["value"] == 90.0
    assert bundle["workflows"]["nonconformance"]["sla"] == {"disposition-the-nonconformance": 5}
    assert bundle["workflows"]["training-acknowledgement"]["terminal"] == "acknowledged"
    assert set(bundle["members"]["quality-assistant"]["roles"]) >= {"staff"}
    assert any(p["subject"] == "cog:quality-assistant" and p["action"] == "approve" for p in bundle["permissions"]), \
        "its authority is named in the SOP, not borrowed from the quality manager's role"


def test_conformance_reports_covered_and_uncovered_clauses(seeded):
    report = conformance(seeded)
    assert report["release"] == seeded.releases.current()["digest"]
    for clause, frame in (("7.5", "document-control-sop"), ("8.7", "ncr-sop"), ("9.2", "audit-sop"),
                          ("9.3", "management-review-sop"), ("10.2", "ncr-sop"), ("7.1.5", "calibration-sop"),
                          ("7.2", "training-sop"), ("8.4", "supplier-sop"), ("6.3", "change-control-sop"),
                          ("4.1", "quality-manual")):
        assert frame in report["covered"].get(clause, []), clause
    assert "capa-sop" in report["covered"]["10.2"]
    assert "document-control-sop" in report["covered"]["7.5.3"], "a claimed clause covers its sub-clauses"
    assert "8.3" in report["uncovered"] and "8.6" in report["uncovered"]
    assert not set(report["covered"]) & set(report["uncovered"])
    assert set(report["covered"]) | set(report["uncovered"]) == set(report["titles"])
    assert report["uncovered"] == sorted(report["uncovered"], key=lambda c: tuple(int(x) for x in c.split(".")))


def test_conformance_falls_back_to_the_frames_without_a_release(tmp_path):
    hub = fresh_hub(tmp_path)
    install(hub, "iso9001", force=True, log=None)
    report = conformance(hub)
    assert report["release"] is None and "ncr-sop" in report["covered"]["8.7"]


def test_cli_seed_and_conformance(tmp_path, capsys):
    from hubproto.cli import build_parser

    unseeded_frames(tmp_path)
    parser = build_parser()
    args = parser.parse_args(["--root", str(tmp_path), "seed", "iso9001", "--force"])
    args.fn(args)
    out = capsys.readouterr().out
    assert "installed iso9001/ncr-sop.frame.md" in out and "replaced Frame 'document-control-sop'" in out
    assert "seeded profile 'iso9001'" in out
    args = parser.parse_args(["--root", str(tmp_path), "conformance"])
    args.fn(args)
    out = capsys.readouterr().out
    assert "8.7      covered" in out and "8.3      uncovered" in out and "clauses covered" in out


# ----------------------------------------------------------------- nonconformance and CAPA

def test_low_risk_ncr_is_approved_by_the_quality_assistant_cog(seeded):
    run_id = seeded.runner.submit("nonconformance", {"record": {"title": "Scratched panel",
                                                                 "description": "Cosmetic scratch on one front panel"}},
                                  owner="alice")
    state = run_state(seeded, run_id)
    assert state["status"] == "completed", events(seeded, run_id)
    decision = state["steps"]["score-the-risk"]["decision"]
    assert decision["by"] == "quality-assistant" and decision["by_type"] == "cog"
    assert seeded.kernel.get(state["record"])["state"] == "closed"


def test_high_severity_ncr_waits_for_the_quality_manager_and_opens_a_capa(seeded):
    run_id = seeded.runner.submit("nonconformance", {"record": {"title": "Cracked housing",
                                                                 "description": "Crack in the housing; a safety concern"}},
                                  owner="alice")
    state = run_state(seeded, run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["carol"]
    assert state["pending"]["due"], "the 5-day SLA sets a due date"
    ncr = seeded.kernel.get(state["record"])
    assert ncr["state"] == "awaiting-disposition-the-nonconformance" and ncr["fields"]["risk"] == "high"
    assert ncr["fields"]["severity"] == "high" and ncr["fields"]["reported_by"] == "alice"
    with pytest.raises(RunError, match="not an assignee"):
        seeded.runner.decide(run_id, "alice", "approve", meaning="ok", payload_digest=seen(seeded.runner, run_id))
    state = seeded.runner.decide(run_id, "carol", "approve", meaning="scrap the lot",
                                 fields={"disposition": "scrap", "disposition_rationale": "The crack cannot be reworked safely."}, payload_digest=seen(seeded.runner, run_id))
    assert state["status"] == "completed", events(seeded, run_id)
    ncr = seeded.kernel.get(state["record"])
    assert ncr["state"] == "closed" and ncr["fields"]["disposition"] == "scrap"
    capas = seeded.kernel.query(type_="capa")
    assert len(capas) == 1
    capa = capas[0]
    assert ncr["fields"]["capa"] == capa["locator"]
    assert any(l["rel"] == "caused" and l["target"] == ncr["locator"] for l in seeded.kernel.links_of(capa["locator"]))
    assert capa["fields"]["owner"] == "carol" and capa["state"] == "awaiting-verify-the-actions"
    capa_run = seeded.runner.run_for_record(capa["locator"])
    assert capa_run["parent_run"] == run_id and capa_run["status"] == "waiting"
    assert capa_run["pending"]["assignees"] == ["carol"] and "go to the owner" in capa_run["pending"]["route_rule"]["text"]
    assert seeded.dms.documents_of(ncr["locator"]), "the closed NCR was rendered to an evidence document"


def test_capa_runs_to_closure_with_an_effectiveness_check(seeded):
    run_id = seeded.runner.submit("nonconformance", {"record": {"title": "Field failure",
                                                                 "description": "Unit failed in the field"}}, owner="alice")
    seeded.runner.decide(run_id, "carol", "approve", fields={"disposition": "return", "disposition_rationale": "Return for analysis."}, meaning="ok", payload_digest=seen(seeded.runner, run_id))
    capa = seeded.kernel.query(type_="capa")[0]
    cid = seeded.runner.run_for_record(capa["locator"])["id"]
    state = seeded.runner.decide(cid, "carol", "approve", meaning="actions complete",
                                 fields={"root_cause": "Wrong torque on the retaining screw."}, payload_digest=seen(seeded.runner, cid))
    assert state["status"] == "waiting" and state["pending"]["checkpoint"] == "verify-effectiveness"
    capa = seeded.kernel.get(capa["locator"])
    assert capa["state"] == "awaiting-verify-effectiveness" and capa["fields"]["effectiveness_check_date"]
    state = seeded.runner.decide(cid, "carol", "approve", fields={"effectiveness_result": "No recurrence in 90 days."}, meaning="ok", payload_digest=seen(seeded.runner, cid))
    assert state["status"] == "completed" and seeded.kernel.get(capa["locator"])["state"] == "closed"


def test_quality_manager_cannot_disposition_her_own_ncr(seeded):
    run_id = seeded.runner.submit("nonconformance", {"record": {"title": "Wrong label", "description": "Label mismatch, safety hazard"}},
                                  owner="carol")
    state = run_state(seeded, run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["bob"]
    assert "management representative" in state["pending"]["route_rule"]["text"]


# ----------------------------------------------------------------- documents, training, escalation

def test_effective_document_assigns_training_and_tick_reports_overdue_trainings(seeded):
    run_id = seeded.runner.submit("document-control", {"record": {"title": "SOP-12 Handling", "change_type": "minor",
                                                                   "training_roles": ["staff"]}}, owner="alice")
    # A new document is a major change whatever its author says (review governance-1): a person approves it.
    seeded.runner.decide(run_id, "bob", "approve", meaning="fit for use", payload_digest=seen(seeded.runner, run_id))
    assert run_state(seeded, run_id)["status"] == "completed", events(seeded, run_id)
    trainings = seeded.kernel.query(type_="training")
    assert {t["fields"]["person"] for t in trainings} == {"alice", "bob", "carol", "dave"}
    for t in trainings:
        assert t["state"] == "awaiting-acknowledge-the-document" and t["assignees"] == [t["fields"]["person"]]
        assert t["due"], "the 14-day SLA sets a due date"
    mine = next(t for t in trainings if t["fields"]["person"] == "alice")
    tr = seeded.runner.run_for_record(mine["locator"])
    assert tr["parent_run"] == run_id
    with pytest.raises(RunError, match="not an assignee"):
        seeded.runner.decide(tr["id"], "bob", "approve", meaning="ok", payload_digest=seen(seeded.runner, tr["id"]))
    state = seeded.runner.decide(tr["id"], "alice", "approve", meaning="read and understood", payload_digest=seen(seeded.runner, tr["id"]))
    assert state["status"] == "completed" and seeded.kernel.get(mine["locator"])["state"] == "acknowledged"
    # Only the trainee acknowledges a training: an overdue one is reported to the quality manager, never reassigned.
    mine_too = {t["locator"] for t in trainings}
    overdue = [e for e in seeded.runner.tick(today="2099-01-01") if e["record"] in mine_too]
    assert {e["record"] for e in overdue} == {t["locator"] for t in trainings if t["fields"]["person"] != "alice"}
    assert all(e["to"] == [] and e["informed"] == ["carol"] for e in overdue)
    bobs = next(t for t in seeded.kernel.query(type_="training") if t["fields"]["person"] == "bob")
    assert bobs["assignees"] == ["bob"]
    assert any(m["to"] == "carol" and m["kind"] == "escalation" for m in seeded.inbox.all())
    assert seeded.runner.tick(today="2099-01-02") == [], "an item is escalated once"


def test_document_control_keeps_its_semantics(seeded):
    run_id = seeded.runner.submit("document-control", {"record": {"title": "SOP-7 Calibration", "change_type": "major"}},
                                  owner="alice")
    state = run_state(seeded, run_id)
    assert state["status"] == "waiting" and set(state["pending"]["assignees"]) == {"bob", "carol"}
    assert state["pending"]["due"], "the 10-day review SLA sets a due date"


# ----------------------------------------------------------------- the other processes

def test_task_routes_to_the_assignee(seeded):
    run_id = seeded.runner.submit("task", {"record": {"title": "Update the label artwork", "assignee": "bob"}}, owner="alice")
    state = run_state(seeded, run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["bob"]
    assert "go to the assignee" in state["pending"]["route_rule"]["text"]
    state = seeded.runner.decide(run_id, "bob", "approve", meaning="done", payload_digest=seen(seeded.runner, run_id))
    assert state["status"] == "completed" and seeded.kernel.get(state["record"])["state"] == "done"
    own = seeded.runner.submit("task", {"record": {"title": "Check the stock count"}}, owner="dave")
    assert run_state(seeded, own)["pending"]["assignees"] == ["dave"], "the assignee defaults to the owner"


def test_calibration_out_of_tolerance_opens_an_ncr_after_review(seeded):
    rec = {"asset": "AST-0001", "standard": "Mass set M1-2026", "result": "out-of-tolerance", "next_due": "2027-05-01",
           "measured": {"nominal": 100.0, "actual": 101.4}}
    run_id = seeded.runner.submit("calibration", {"record": rec}, owner="alice")
    state = run_state(seeded, run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["carol"]
    assert seeded.kernel.query(type_="ncr") == []
    state = seeded.runner.decide(run_id, "carol", "approve", meaning="confirmed out of tolerance", payload_digest=seen(seeded.runner, run_id))
    assert state["status"] == "completed", events(seeded, run_id)
    cal = seeded.kernel.get(state["record"])
    ncrs = seeded.kernel.query(type_="ncr")
    assert len(ncrs) == 1 and cal["state"] == "closed" and cal["fields"]["ncr"] == ncrs[0]["locator"]
    ncr = ncrs[0]
    assert ncr["fields"]["source"] == "calibration"
    assert any(l["target"] == cal["locator"] for l in seeded.kernel.links_of(ncr["locator"]))
    nrun = seeded.runner.run_for_record(ncr["locator"])
    assert nrun["parent_run"] == run_id and nrun["status"] == "waiting"
    assert nrun["pending"]["assignees"] == ["bob"], "carol opened it, so segregation of duties sends it to bob"
    in_tol = seeded.runner.submit("calibration", {"record": {**rec, "result": "in-tolerance"}}, owner="alice")
    seeded.runner.decide(in_tol, "carol", "approve", meaning="ok", payload_digest=seen(seeded.runner, in_tol))
    assert len(seeded.kernel.query(type_="ncr")) == 1, "an in-tolerance result opens nothing"


def test_complaint_is_answered_and_opens_an_ncr(seeded):
    run_id = seeded.runner.submit("complaint", {"record": {"title": "Late delivery of order 77", "customer": "cust-1",
                                                           "description": "Order arrived a week late", "severity": "high"}},
                                  owner="dave")
    state = run_state(seeded, run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["carol"]
    assert seeded.kernel.get(state["record"])["fields"]["risk"] == "high"
    with pytest.raises(RunError, match="not an assignee"):
        seeded.runner.decide(run_id, "dave", "approve", meaning="ok", payload_digest=seen(seeded.runner, run_id))
    state = seeded.runner.decide(run_id, "carol", "approve", fields={"response": "Apologised; credit note issued."}, meaning="ok", payload_digest=seen(seeded.runner, run_id))
    assert state["status"] == "completed", events(seeded, run_id)
    ncr = seeded.kernel.query(type_="ncr")[0]
    assert ncr["fields"]["source"] == "complaint" and seeded.kernel.get(state["record"])["fields"]["ncr"] == ncr["locator"]


def test_internal_audit_records_findings_and_the_quality_manager_accepts_them(seeded):
    run_id = seeded.runner.submit("internal-audit", {"record": {"title": "Audit of nonconformances", "planned": "2026-12-01",
                                                                "processes": ["nonconformance"]}}, owner="bob")  # bob decides no step of it
    state = run_state(seeded, run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["bob"]
    audit = seeded.kernel.get(state["record"])
    assert audit["fields"]["auditor"] == "bob"
    steps = seeded.current_bundle()["processes"]["nonconformance"]["steps"]
    assert [c["process"] for c in audit["fields"]["checklist"]] == ["nonconformance"] * len(steps)
    findings = [{"id": "F-1", "clause": "7.5.3", "criterion": "Obsolete documents are withdrawn.", "found": "Two obsolete copies on the shop floor",
                 "evidence": [audit["number"]], "grade": "minor"}]
    state = seeded.runner.decide(run_id, "bob", "approve", fields={"findings": findings}, meaning="ok", payload_digest=seen(seeded.runner, run_id))
    assert state["status"] == "waiting" and state["pending"]["checkpoint"] == "accept-the-findings"
    assert state["pending"]["assignees"] == ["carol"]
    assert seeded.kernel.get(audit["locator"])["state"] == "awaiting-accept-the-findings"
    state = seeded.runner.decide(run_id, "carol", "approve", fields={"summary": "One minor finding; NCR raised.",
                                                                     "finding_decisions": [{"finding": "F-1", "verdict": "accept"}]},
                                 meaning="ok", payload_digest=seen(seeded.runner, run_id))
    assert state["status"] == "completed" and seeded.kernel.get(audit["locator"])["state"] == "closed"


def test_change_request_is_approved_then_verified_by_the_requester(seeded):
    run_id = seeded.runner.submit("change-control", {"record": {"title": "Move final inspection", "description": "Inspect before packing",
                                                                "impact": "WI-4 and the packing SOP change"}}, owner="alice")
    state = run_state(seeded, run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["carol"]
    assert seeded.kernel.get(state["record"])["state"] == "awaiting-approve-the-change"
    state = seeded.runner.decide(run_id, "carol", "approve", meaning="agreed", payload_digest=seen(seeded.runner, run_id))
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["alice"]
    assert "go to the requester" in state["pending"]["route_rule"]["text"]
    with pytest.raises(RunError, match="needs verification"):
        seeded.runner.decide(run_id, "alice", "approve", meaning="ok", payload_digest=seen(seeded.runner, run_id))
    state = seeded.runner.decide(run_id, "alice", "approve", fields={"verification": "Both documents revised and trained."}, meaning="ok", payload_digest=seen(seeded.runner, run_id))
    assert state["status"] == "completed" and seeded.kernel.get(state["record"])["state"] == "closed"


def test_supplier_evaluation_and_management_review(seeded):
    sup = seeded.runner.submit("supplier-evaluation", {"record": {"supplier": "newco-tools", "score": 82, "scope": "Tooling",
                                                                  "reevaluation_date": "2027-11-01"}}, owner="carol")
    state = run_state(seeded, sup)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["bob"]
    state = seeded.runner.decide(sup, "bob", "approve", meaning="approved for tooling", payload_digest=seen(seeded.runner, sup))
    assert state["status"] == "completed" and seeded.kernel.get(state["record"])["state"] == "approved"
    with pytest.raises(RunError, match="alice may not start management-review"):
        seeded.runner.submit("management-review", {"record": {"title": "x", "date": "2026-12-15", "inputs": {}}}, owner="alice")
    mr = seeded.runner.submit("management-review", {"record": {"title": "2026 review", "date": "2026-12-15",
                                                               "inputs": {"audits": 1, "complaints": 1}}}, owner="bob")
    state = run_state(seeded, mr)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["bob"], "the chair defaults to the preparer"
    state = seeded.runner.decide(mr, "bob", "approve", fields={"outputs": {"decisions": ["hire a second auditor"]}}, meaning="ok", payload_digest=seen(seeded.runner, mr))
    assert state["status"] == "completed"
    review = seeded.kernel.get(state["record"])
    assert review["state"] == "closed" and seeded.dms.documents_of(review["locator"]), "the minutes were rendered"
