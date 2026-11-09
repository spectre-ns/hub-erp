"""Processes: sentence grammar -> rendered workflows -> the runner (Gates, SLAs, rejections, children)."""

import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from conftest import ROOT, release_all, seen
from hubproto.app import Hub
from hubproto.extract import ConventionExtractor, object_of, slug
from hubproto.frames import load_frame
from hubproto.release import checkpoint_for, legacy_transitions, render
from hubproto.runner import RunError
from hubproto.track import Track

TEST_FRAMES = Path(__file__).resolve().parent / "frames"
P = "widget-request"
G = "gadget-request"


def rules_of(name: str) -> list[dict]:
    return ConventionExtractor().extract(load_frame(TEST_FRAMES / f"{name}.frame.md")).rules


def build_hub(tmp_path, edits: dict | None = None) -> Hub:
    """A hub with the repository org model plus the widget policy, optionally edited (every old must occur).

    Only org-model is taken from the repository so these tests do not depend on the other company Frames.
    """
    (tmp_path / "frames" / "company").mkdir(parents=True)
    shutil.copy(ROOT / "frames" / "company" / "org-model.frame.md", tmp_path / "frames" / "company")
    text = (TEST_FRAMES / "widget-policy.frame.md").read_text()
    for old, new in (edits or {}).items():
        assert old in text, old
        text = text.replace(old, new)
    (tmp_path / "frames" / "widget-policy.frame.md").write_text(text)
    h = Hub(tmp_path)
    h.kernel.seed()
    return h


@pytest.fixture
def widgets(tmp_path) -> Hub:
    h = build_hub(tmp_path)
    release_all(h)
    return h


def types_of(hub, run_id):
    return [e["type"] for e in hub.runner.track(run_id)]


def event(hub, run_id, type_):
    return [e for e in hub.runner.track(run_id) if e["type"] == type_]


def record_of(hub, run_id):
    return hub.kernel.get(hub.runner.load(run_id)["record"])


def findings(hub, severity=None):
    return [f for f in hub.compile()["findings"] if severity is None or f["severity"] == severity]


# ---------------------------------------------------------------------- grammar

def test_routing_sentences_name_the_checkpoint():
    routing = {r["source"]["text"]: r for r in rules_of("grammar-sample") if r["kind"] == "routing"}
    at = routing["At Review the sample in sample-review go to the assignee."]
    assert at["process"] == "sample-review" and at["condition"] is None and at["assignee"] == "field:assignee"
    assert at["step"] == slug("Review the sample") == "review-the-sample"
    frame = load_frame(TEST_FRAMES / "grammar-sample.frame.md")
    assert at["source"]["frame"] == "grammar-sample" and at["source"]["version"] == "1.0.0"
    assert frame.line(at["source"]["line"]) == at["source"]["text"], "the citation points at the sentence"
    approvals = routing["Approvals in sample-review go to the sample reviewer."]
    assert approvals["step"] == "approve" and approvals["condition"] is None
    assert approvals["assignee"] == "role:sample-reviewer"
    overdue = routing["Overdue items in sample-review go to the sample reviewer."]
    assert overdue["step"] == "*" and overdue["condition"] == {"field": "overdue", "op": "==", "value": True}


def test_routing_conditions():
    routing = [r for r in rules_of("grammar-sample") if r["kind"] == "routing"]
    by_text = {r["source"]["text"]: r for r in routing}
    drawings = by_text["At Review the sample in sample-review for drawings with a minor change go to the sample reviewer."]
    assert drawings["step"] == "review-the-sample"
    assert drawings["condition"] == {"all": [{"field": "doc_type", "op": "==", "value": "drawing"},
                                             {"field": "change_type", "op": "==", "value": "minor"}]}
    amount = by_text["Approvals in sample-review over 10,000 USD go to the sample reviewer."]
    assert amount["condition"] == {"field": "amount", "op": ">", "value": 10000.0, "currency": "USD"}
    risk = by_text["At Review the sample in sample-review with high risk go to the sample reviewer."]
    assert risk["condition"] == {"field": "risk", "op": "==", "value": "high"}


def test_sla_sentences():
    slas = {r["step"]: r for r in rules_of("grammar-sample") if r["kind"] == "sla"}
    assert slas["review-the-sample"]["days"] == 3 and slas["review-the-sample"]["process"] == "sample-review"
    assert slas["approve"]["days"] == 5
    assert slas["review-the-sample"]["source"]["text"] == "Review the sample in sample-review is due within 3 days."


def test_process_section_terminal_rejections_and_record_type():
    proc = next(r for r in rules_of("grammar-sample") if r["kind"] == "process")
    assert proc["name"] == "sample-review" and proc["folder"] == "Samples"
    assert proc["terminal"] == "done" and proc["reject_to"] == "review-the-sample" and proc["record_type"] == "task"
    assert proc["object"] == "sample"
    assert [(s["n"], s["id"], s["role"], s["cog"], s["kind"]) for s in proc["steps"]] == [
        (1, "file-the-sample", "sample-reviewer", "record-filer", "cog"),
        (2, "review-the-sample", "sample-reviewer", None, "checkpoint"),
        (3, "link-the-sample", "system", "record-linker", "cog"),
        (4, "approve-the-sample", "sample-reviewer", None, "checkpoint"),
    ]
    gadget = next(r for r in rules_of("widget-policy") if r["kind"] == "process" and r["name"] == G)
    assert gadget["reject_to"] is None and gadget["terminal"] == "done"
    widget = next(r for r in rules_of("widget-policy") if r["kind"] == "process" and r["name"] == P)
    assert widget["reject_to"] == "check-the-widget"


def test_process_defaults_when_no_sentence_says_otherwise(tmp_path):
    text = (TEST_FRAMES / "widget-policy.frame.md").read_text()
    for line in ("The record type is task.", "Closes as done.", "Rejections return to Check the widget."):
        text = text.replace(line + "\n", "")
    path = tmp_path / "bare.frame.md"
    path.write_text(text)
    proc = next(r for r in ConventionExtractor().extract(load_frame(path)).rules if r["kind"] == "process" and r["name"] == P)
    assert proc["terminal"] == "closed" and proc["reject_to"] is None and "record_type" not in proc


def test_object_of_and_thresholds():
    assert object_of("File the training record") == "training-record"
    assert object_of("Open the nonconformance") == "nonconformance"
    assert object_of("Extract a bill") == "bill"
    th = {r["name"]: r for r in rules_of("grammar-sample") if r["kind"] == "threshold"}
    assert th["sample-review-interval"]["value"] == 14.0 and th["sample-review-interval"]["unit"] == "days"
    assert th["sample-tolerance"]["value"] == 2.5 and th["sample-tolerance"]["unit"] == "percent"


def test_every_sentence_in_the_test_frames_parses():
    for name in ("grammar-sample", "widget-policy"):
        ex = ConventionExtractor().extract(load_frame(TEST_FRAMES / f"{name}.frame.md"))
        assert ex.notes == [], name


# ----------------------------------------------------------------------- render

@pytest.fixture
def bundle(hub):
    return render(rules_of("widget-policy"), hub.kernel.types)


def test_checkpoint_for_resolves_ids_and_the_approve_alias():
    proc = next(r for r in rules_of("widget-policy") if r["kind"] == "process" and r["name"] == P)
    assert checkpoint_for(proc, "approve")["id"] == "approve-the-widget"
    assert checkpoint_for(proc, "check-the-widget")["title"] == "Check the widget"
    assert checkpoint_for(proc, "request-the-widget") is None, "a cog step is not a checkpoint"
    assert checkpoint_for(proc, "nope") is None
    gadget = next(r for r in rules_of("widget-policy") if r["kind"] == "process" and r["name"] == G)
    assert checkpoint_for(gadget, "approve")["id"] == "approve-the-gadget"


def test_render_resolves_the_approve_alias_in_routing_and_slas(bundle):
    steps = {(r["process"], r["assignee"]): r["step"] for r in bundle["routing"]}
    assert steps[(P, "role:widget-approver")] == "approve-the-widget"
    assert steps[(G, "role:widget-approver")] == "approve-the-gadget"
    assert steps[(P, "field:assignee")] == "check-the-widget"
    assert steps[(P, "role:widget-supervisor")] == "*"
    assert bundle["workflows"][P]["sla"] == {"check-the-widget": 3, "approve-the-widget": 5}
    assert bundle["thresholds"]["widget-review-interval"] == {"value": 14.0, "unit": "days",
                                                             "source": bundle["thresholds"]["widget-review-interval"]["source"]}


def test_render_workflow_states_and_transitions(bundle):
    wf = bundle["workflows"][P]
    assert wf["record_type"] == "task" and wf["object"] == "widget"
    assert wf["states"] == ["open", "awaiting-check-the-widget", "awaiting-approve-the-widget", "done"]
    assert wf["terminal"] == "done" and wf["reject_to"] == "check-the-widget"
    assert [(c["id"], c["role"], c["state"], c["sla_days"]) for c in wf["checkpoints"]] == [
        ("check-the-widget", "widget-checker", "awaiting-check-the-widget", 3),
        ("approve-the-widget", "widget-approver", "awaiting-approve-the-widget", 5)]
    # The workflow is a graph now (change control 10.1); the earlier transition list is a view of it.
    auto = [t for t in legacy_transitions(wf) if t["kind"] == "automatic"]
    assert [(t["from"], t["to"], t["steps"]) for t in auto] == [
        ("open", "awaiting-check-the-widget", ["request-the-widget"]),
        ("awaiting-check-the-widget", "awaiting-approve-the-widget", ["link-the-widget"])]
    assert [(t["id"], t.get("from"), t["to"], t["gate"]["kind"], t["actions"]) for t in wf["transitions"] if t["type"] == "directed"] == [
        ("auto-awaiting-check-the-widget", ["open"], "awaiting-check-the-widget", "auto", ["request-the-widget"]),
        ("check-the-widget", ["awaiting-check-the-widget"], "awaiting-approve-the-widget", "person", ["link-the-widget"]),
        ("reject-check-the-widget", ["awaiting-check-the-widget"], "awaiting-check-the-widget", "person", []),
        ("approve-the-widget", ["awaiting-approve-the-widget"], "done", "person", []),
        ("reject-approve-the-widget", ["awaiting-approve-the-widget"], "awaiting-check-the-widget", "person", []),
        ("retry", ["failed"], "failed", "person", [])]
    human = [t for t in legacy_transitions(wf) if t["kind"] == "human"]
    assert human == [
        {"from": "awaiting-check-the-widget", "to": "awaiting-approve-the-widget", "kind": "human", "step": "check-the-widget",
         "title": "Check the widget", "on_reject": "awaiting-check-the-widget", "sla_days": 3},
        {"from": "awaiting-approve-the-widget", "to": "done", "kind": "human", "step": "approve-the-widget",
         "title": "Approve the widget", "on_reject": "awaiting-check-the-widget", "sla_days": 5}]


def test_render_workflow_without_reject_to_ends_in_rejected(bundle):
    wf = bundle["workflows"][G]
    assert wf["states"] == ["open", "awaiting-approve-the-gadget", "done", "rejected"]
    assert wf["reject_to"] is None and wf["sla"] == {}
    human = [t for t in legacy_transitions(wf) if t["kind"] == "human"]
    assert human == [{"from": "awaiting-approve-the-gadget", "to": "done", "kind": "human", "step": "approve-the-gadget",
                      "title": "Approve the gadget", "on_reject": "rejected", "sla_days": None}]


def test_render_manifest_gates_the_step_before_each_checkpoint(bundle):
    op = bundle["ops"][P]
    assert op["record_type"] == "task" and op["object"] == "widget" and op["folder"] == "Widgets/Requests"
    assert [s["id"] for s in op["steps"]] == ["request-the-widget", "link-the-widget"], "checkpoints are not steps"
    assert op["steps"][0]["gate"] == {"type": "human", "step": "check-the-widget", "title": "Check the widget",
                                      "role": "widget-checker", "route": {"process": P, "step": "check-the-widget"}}
    assert op["steps"][1]["gate"]["route"] == {"process": P, "step": "approve-the-widget"}
    assert op["steps"][1]["depends_on"] == ["request-the-widget"]
    assert bundle["ops"][G]["steps"][0]["gate"]["step"] == "approve-the-gadget"
    assert bundle["ops"][G]["record_type"] == "task"


def test_render_derives_the_record_type_from_the_object_when_not_stated(hub):
    rules = [r for r in rules_of("widget-policy") if r["kind"] == "process" and r["name"] == P]
    rules[0] = {k: v for k, v in rules[0].items() if k != "record_type"}
    assert render(rules, hub.kernel.types)["workflows"][P]["record_type"] == "widget", "no type has object 'widget'"
    rules[0] = {**rules[0], "object": "nonconformance"}
    assert render(rules, hub.kernel.types)["workflows"][P]["record_type"] == "ncr"


# ------------------------------------------------------------------ consistency

def test_routing_to_a_missing_checkpoint_is_an_error(tmp_path):
    h = build_hub(tmp_path, {"At Check the widget in widget-request go to the assignee.":
                             "At Inspect the widget in widget-request go to the assignee."})
    errors = [f["detail"] for f in findings(h, "error")]
    assert any("routing names step 'inspect-the-widget', which is not a checkpoint of widget-request" in d for d in errors)


def test_checkpoint_without_a_routing_sentence_is_a_warning_and_routes_to_its_role(tmp_path):
    h = build_hub(tmp_path, {"Approvals in widget-request go to the widget approver.\n": ""})
    fs = findings(h)
    assert not [f for f in fs if f["severity"] == "error" and "widget" in f["detail"]]
    warning = next(f for f in fs if "checkpoint 'Approve the widget' of widget-request has no routing sentence" in f["detail"])
    assert warning["severity"] == "warning" and warning["frame"] == "widget-policy" and warning["line"] > 0
    release_all(h)
    run_id = h.runner.submit(P, {"record": {"title": "w"}}, owner="erin")
    h.runner.decide(run_id, "erin", "approve", meaning="ok", payload_digest=seen(h.runner, run_id))
    pending = h.runner.load(run_id)["pending"]
    assert pending["checkpoint"] == "approve-the-widget" and pending["assignees"] == ["hank", "jack"]
    assert pending["route_rule"]["frame"] == "(process step)" and pending["route_rule"]["text"] == "[widget-approver] Approve the widget"


def test_routed_role_without_a_member_is_an_error(tmp_path):
    h = build_hub(tmp_path, {"- hank (person): widget-approver": "- hank (person): widget-checker",
                             "- jack (person): widget-approver": "- jack (person): widget-checker"})
    errors = [f for f in findings(h, "error") if "role:widget-approver" in f["detail"]]
    assert errors and all("fill the org model" in f["detail"] for f in errors)
    assert {f["frame"] for f in errors} == {"widget-policy"}


def test_unknown_record_type_for_a_process_is_an_error(tmp_path):
    h = build_hub(tmp_path, {"The record type is task.": "The record type is gizmo."})
    errors = [f["detail"] for f in findings(h, "error")]
    assert any("process widget-request needs record type 'gizmo'" in d for d in errors)


def test_sla_naming_an_unknown_step_is_an_error(tmp_path):
    h = build_hub(tmp_path, {"Check the widget in widget-request is due within 3 days.":
                             "Inspect the widget in widget-request is due within 3 days."})
    errors = [f["detail"] for f in findings(h, "error")]
    assert any("SLA names step 'inspect-the-widget', which is not a checkpoint of widget-request" in d for d in errors)


def test_rejections_returning_to_an_unknown_step_is_an_error(tmp_path):
    h = build_hub(tmp_path, {"Rejections return to Check the widget.": "Rejections return to Inspect the widget."})
    errors = [f["detail"] for f in findings(h, "error")]
    assert any("rejections return to unknown step 'inspect-the-widget'" in d for d in errors)


def test_compound_routing_condition_compiles(tmp_path):
    h = build_hub(tmp_path, {"Approvals in widget-request go to the widget approver.":
                             "Approvals in widget-request go to the widget approver.\n"
                             "Approvals in widget-request for drawings with a minor change go to the widget supervisor."})
    proposal = h.compile()
    assert not [f for f in proposal["findings"] if f["severity"] == "error" and "widget" in f["detail"]]
    change = next(c for c in proposal["changes"] if c["rule"]["kind"] == "routing"
                  and c["rule"]["source"]["text"].startswith("Approvals in widget-request for drawings"))
    assert "widget-supervisor" in change["reason"]


# ----------------------------------------------------------------------- runner

def test_submit_creates_the_record_open_with_defaults(widgets):
    run_id = widgets.runner.submit(P, {"record": {"title": "Blue widget"}}, owner="erin")
    state = widgets.runner.load(run_id)
    assert state["process"] == P and state["owner"] == "erin" and state["release"] == widgets.releases.current()["digest"]
    rec = widgets.kernel.get(state["record"])
    assert rec["type"] == "task" and rec["folder"] == "Widgets/Requests" and rec["number"].startswith("TASK-")
    assert rec["fields"]["assignee"] == "erin", "the task default $owner is applied"
    assert rec["fields"]["title"] == "Blue widget" and rec["fields"]["filed_by"] == "erin"
    created = widgets.kernel.events(aggregate=rec["locator"])[0]
    assert created["type"] == "record.created" and created["data"]["state"] == "open"
    assert created["run_id"] == run_id and created["release"] == state["release"] and created["by"] == "erin"
    assert rec["transitions"][0]["from"] == "open"
    submitted = widgets.runner.track(run_id)[0]
    assert submitted["type"] == "run.submitted" and submitted["record"] == rec["locator"]
    assert submitted["owner_type"] == "person" and submitted["parent_run"] is None
    assert "allowed by widget-policy line" in submitted["admission"]


def test_submit_checks_the_owner_role_and_create_permission(widgets):
    with pytest.raises(RunError, match="may not start widget-request"):
        widgets.runner.submit(P, {"record": {"title": "x"}}, owner="hank")
    with pytest.raises(RunError, match="is not a member"):
        widgets.runner.submit(P, {"record": {"title": "x"}}, owner="zed")
    with pytest.raises(RunError, match="defines no process 'no-such-process'"):
        widgets.runner.submit("no-such-process", {"record": {}}, owner="erin")
    assert widgets.kernel.query(type_="task") == []


def test_first_checkpoint_waits_with_assignees_due_date_and_inbox(widgets, today):
    run_id = widgets.runner.submit(P, {"record": {"title": "w"}}, owner="erin")
    state = widgets.runner.load(run_id)
    due = (today + timedelta(days=3)).isoformat()
    assert state["status"] == "waiting" and state["grant"] is None
    pending = state["pending"]
    assert pending["checkpoint"] == "check-the-widget" and pending["step"] == "request-the-widget"
    assert pending["assignees"] == ["erin"] and pending["due"] == due and pending["title"] == "Check the widget"
    assert pending["route_rule"]["text"] == "At Check the widget in widget-request go to the assignee."
    rec = widgets.kernel.get(state["record"])
    assert rec["state"] == "awaiting-check-the-widget" and rec["assignees"] == ["erin"] and rec["due"] == due
    assert rec["transitions"][-1]["reason"].startswith("routed by At Check the widget")
    approvals = [m for m in widgets.inbox.for_principal("erin") if m["kind"] == "approval" and m["run_id"] == run_id]
    assert len(approvals) == 1
    assert approvals[0]["subject"] == f"Check the widget: {P} {rec['locator']}" and f"Due {due}." in approvals[0]["body"]
    assert types_of(widgets, run_id) == ["run.submitted", "step.started", "step.result", "gate.pending"]
    assert event(widgets, run_id, "gate.pending")[0]["due"] == due
    assert [r["number"] for r in widgets.kernel.query(overdue_at="2999-01-01")] == [rec["number"]]


def test_role_route_puts_an_approval_in_every_assignees_inbox(widgets, today):
    run_id = widgets.runner.submit(P, {"record": {"title": "w"}}, owner="erin")
    widgets.runner.decide(run_id, "erin", "approve", meaning="ok", payload_digest=seen(widgets.runner, run_id))
    pending = widgets.runner.load(run_id)["pending"]
    assert pending["checkpoint"] == "approve-the-widget" and pending["assignees"] == ["hank", "jack"]
    assert pending["due"] == (today + timedelta(days=5)).isoformat()
    rec = record_of(widgets, run_id)
    assert rec["state"] == "awaiting-approve-the-widget" and rec["assignees"] == ["hank", "jack"]
    for p in ("hank", "jack"):
        mine = [m for m in widgets.inbox.for_principal(p) if m["kind"] == "approval" and m["run_id"] == run_id]
        assert len(mine) == 1 and mine[0]["subject"].startswith("Approve the widget:")
    assert not [m for m in widgets.inbox.for_principal("ivy") if m["run_id"] == run_id]


def test_only_an_assignee_may_decide(widgets):
    run_id = widgets.runner.submit(P, {"record": {"title": "w"}}, owner="erin")
    with pytest.raises(RunError, match="frank is not an assignee of this decision \\(assigned: erin\\)"):
        widgets.runner.decide(run_id, "frank", "approve", meaning="ok", payload_digest=seen(widgets.runner, run_id))
    with pytest.raises(RunError, match="not an assignee"):
        widgets.runner.decide(run_id, "person:hank", "approve", meaning="ok", payload_digest=seen(widgets.runner, run_id))
    with pytest.raises(RunError, match="verdict must be approve or reject"):
        widgets.runner.decide(run_id, "erin", "defer", meaning="ok", payload_digest=seen(widgets.runner, run_id))
    state = widgets.runner.load(run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["erin"]
    assert not event(widgets, run_id, "gate.decided")


def test_decide_with_fields_sets_them_and_advances(widgets):
    run_id = widgets.runner.submit(P, {"record": {"title": "w"}}, owner="erin")
    state = widgets.runner.decide(run_id, "erin", "approve", meaning="checked the drawing",
                                  fields={"description": "fits"}, payload_digest=seen(widgets.runner, run_id))
    assert state["status"] == "waiting" and state["pending"]["checkpoint"] == "approve-the-widget"
    rec = widgets.kernel.get(state["record"])
    assert rec["fields"]["description"] == "fits"
    assert rec["state"] == "awaiting-approve-the-widget"
    decision = state["steps"]["request-the-widget"]["decision"]
    assert decision["by"] == "erin" and decision["verdict"] == "approve" and decision["meaning"] == "checked the drawing"
    assert decision["signature"] and decision["step"] == "check-the-widget"
    assert state["grant"]["decided_by"] == "erin" and state["grant"]["run_id"] == run_id
    types = types_of(widgets, run_id)
    assert types[4:] == ["record.fields_set", "gate.decided", "grant.issued", "step.started", "step.result", "gate.pending"]
    assert event(widgets, run_id, "record.fields_set")[0]["fields"] == ["description"]
    with pytest.raises(RunError, match="must be one of low,medium,high"):
        widgets.runner.decide(run_id, "hank", "approve", fields={"priority": "asap"}, meaning="ok", payload_digest=seen(widgets.runner, run_id))
    assert widgets.runner.load(run_id)["status"] == "waiting"


def test_completion_applies_the_terminal_state(widgets):
    run_id = widgets.runner.submit(P, {"record": {"title": "w"}}, owner="erin")
    widgets.runner.decide(run_id, "erin", "approve", meaning="ok", payload_digest=seen(widgets.runner, run_id))
    state = widgets.runner.decide(run_id, "jack", "approve", meaning="approved", payload_digest=seen(widgets.runner, run_id))
    assert state["status"] == "completed" and state["pending"] is None
    rec = widgets.kernel.get(state["record"])
    assert rec["state"] == "done" and rec["assignees"] == [] and rec["due"] is None
    assert [t["to"] for t in rec["transitions"]] == ["awaiting-check-the-widget", "awaiting-approve-the-widget", "done"]
    assert rec["transitions"][-1]["reason"] == "process completed" and rec["transitions"][-1]["run_id"] == run_id
    done = event(widgets, run_id, "run.completed")
    assert len(done) == 1 and done[0]["record"] == rec["locator"] and done[0]["state"] == "done"
    assert types_of(widgets, run_id)[-1] == "run.completed"
    with pytest.raises(RunError, match="not waiting"):
        widgets.runner.decide(run_id, "jack", "approve", meaning="ok", payload_digest=seen(widgets.runner, run_id))
    assert [r["number"] for r in widgets.kernel.query(type_="task", state="done")] == [rec["number"]]


def test_rejection_returns_to_the_first_checkpoint(widgets):
    run_id = widgets.runner.submit(P, {"record": {"title": "w"}}, owner="erin")
    widgets.runner.decide(run_id, "erin", "approve", meaning="ok", payload_digest=seen(widgets.runner, run_id))
    state = widgets.runner.decide(run_id, "hank", "reject", meaning="wrong size", payload_digest=seen(widgets.runner, run_id))
    assert state["status"] == "waiting"
    assert state["pending"]["checkpoint"] == "check-the-widget" and state["pending"]["step"] == "request-the-widget"
    assert state["pending"]["route_rule"]["text"] == "returned by a rejection"
    assert state["steps"]["request-the-widget"]["status"] == "gate-pending"
    assert "link-the-widget" not in state["steps"], "the steps after the target checkpoint are forgotten"
    rec = widgets.kernel.get(state["record"])
    assert rec["state"] == "awaiting-check-the-widget"
    assert [(d["by"], d["verdict"], d["meaning"]) for d in rec["decisions"]] == [
        ("erin", "approve", "ok"), ("hank", "reject", "wrong size")]
    rejected = event(widgets, run_id, "run.rejected")
    assert len(rejected) == 1 and rejected[0]["to"] == "awaiting-check-the-widget" and rejected[0]["by"] == "hank"
    assert types_of(widgets, run_id)[-2:] == ["run.rejected", "gate.pending"]


def test_rewound_gate_keeps_its_assignees_so_it_can_be_decided_again(widgets):
    run_id = widgets.runner.submit(P, {"record": {"title": "w"}}, owner="erin")
    widgets.runner.decide(run_id, "erin", "approve", meaning="ok", payload_digest=seen(widgets.runner, run_id))
    state = widgets.runner.decide(run_id, "hank", "reject", meaning="wrong size", payload_digest=seen(widgets.runner, run_id))
    assert state["pending"]["assignees"] == ["erin"]
    assert widgets.kernel.get(state["record"])["assignees"] == ["erin"]
    state = widgets.runner.decide(run_id, "erin", "approve", meaning="resized", payload_digest=seen(widgets.runner, run_id))
    assert state["status"] == "waiting" and state["pending"]["checkpoint"] == "approve-the-widget"


def test_rejection_without_reject_to_ends_the_run(widgets):
    run_id = widgets.runner.submit(G, {"record": {"title": "g"}}, owner="erin")
    assert widgets.runner.load(run_id)["pending"]["assignees"] == ["hank", "jack"]
    state = widgets.runner.decide(run_id, "hank", "reject", meaning="not needed", payload_digest=seen(widgets.runner, run_id))
    assert state["status"] == "rejected" and state["pending"] is None and state["grant"] is None
    rec = widgets.kernel.get(state["record"])
    assert rec["state"] == "rejected" and rec["decisions"][0]["by"] == "hank"
    assert rec["transitions"][-1]["reason"] == "not needed" and rec["transitions"][-1]["by"] == "hank"
    rejected = event(widgets, run_id, "run.rejected")
    assert len(rejected) == 1 and rejected[0]["to"] == "rejected"
    assert types_of(widgets, run_id)[-1] == "run.rejected"
    with pytest.raises(RunError, match="not waiting"):
        widgets.runner.decide(run_id, "hank", "approve", meaning="ok", payload_digest=seen(widgets.runner, run_id))
    assert not event(widgets, run_id, "run.completed")


# -------------------------------------------------------------------------- tick

def test_tick_escalates_overdue_items_once(widgets, today):
    run_id = widgets.runner.submit(P, {"record": {"title": "w"}}, owner="erin")
    due = (today + timedelta(days=3)).isoformat()
    assert widgets.runner.tick(today=today.isoformat()) == [], "not yet due"
    escalated = widgets.runner.tick(today="2999-01-01")
    assert [(e["run"], e["record"], e["to"]) for e in escalated] == [(run_id, widgets.runner.load(run_id)["record"], ["ivy"])]
    state = widgets.runner.load(run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["erin", "ivy"]
    assert state["pending"]["escalated"] == "2999-01-01"
    ev = event(widgets, run_id, "gate.escalated")
    assert len(ev) == 1 and ev[0]["assignees"] == ["ivy"] and ev[0]["due"] == due
    assert ev[0]["route_rule"]["text"] == "Overdue items in widget-request go to the widget supervisor."
    rec = record_of(widgets, run_id)
    assert rec["assignees"] == ["erin", "ivy"] and rec["state"] == "awaiting-check-the-widget" and rec["due"] == due
    msgs = [m for m in widgets.inbox.for_principal("ivy") if m["run_id"] == run_id]
    assert len(msgs) == 1 and msgs[0]["kind"] == "escalation"
    assert msgs[0]["subject"] == f"Overdue: Check the widget on {rec['locator']}" and f"Due {due}" in msgs[0]["body"]
    assert widgets.runner.tick(today="2999-01-02") == []
    assert len(event(widgets, run_id, "gate.escalated")) == 1
    assert len([m for m in widgets.inbox.for_principal("ivy") if m["run_id"] == run_id]) == 1
    state = widgets.runner.decide(run_id, "ivy", "approve", meaning="escalated approval", payload_digest=seen(widgets.runner, run_id))
    assert state["pending"]["checkpoint"] == "approve-the-widget"


def test_tick_ignores_runs_that_are_not_waiting(widgets):
    run_id = widgets.runner.submit(G, {"record": {"title": "g"}}, owner="erin")
    assert widgets.runner.load(run_id)["pending"]["due"] is None, "no SLA on the gadget approval"
    assert widgets.runner.tick(today="2999-01-01") == []
    widgets.runner.decide(run_id, "hank", "approve", meaning="ok", payload_digest=seen(widgets.runner, run_id))
    assert widgets.runner.tick(today="2999-01-01") == []
    assert not event(widgets, run_id, "gate.escalated")


# ----------------------------------------------------------------- decide checks

def test_decide_precheck_requires_the_terminal_fields_when_the_gate_is_last(widgets):
    run_id = widgets.runner.submit(G, {"record": {"description": "untitled"}}, owner="erin")
    assert widgets.runner.load(run_id)["status"] == "waiting"
    with pytest.raises(RunError, match="done needs title before approval"):
        widgets.runner.decide(run_id, "hank", "approve", meaning="ok", payload_digest=seen(widgets.runner, run_id))
    state = widgets.runner.load(run_id)
    assert state["status"] == "waiting" and state["grant"] is None
    assert not event(widgets, run_id, "gate.decided") and record_of(widgets, run_id)["state"] == "awaiting-approve-the-gadget"
    state = widgets.runner.decide(run_id, "hank", "approve", fields={"title": "Gadget"}, meaning="ok", payload_digest=seen(widgets.runner, run_id))
    assert state["status"] == "completed"
    rec = widgets.kernel.get(state["record"])
    assert rec["state"] == "done" and rec["fields"]["title"] == "Gadget"


# ----------------------------------------------------------------- child runs

def test_child_process_is_admitted_by_the_parent_grant(widgets):
    parent = widgets.runner.submit(P, {"record": {"title": "parent"}}, owner="erin")
    widgets.runner.decide(parent, "erin", "approve", meaning="ok", payload_digest=seen(widgets.runner, parent))
    assert widgets.runner.load(parent)["grant"]
    child = widgets.runner.submit(G, {"record": {"title": "child"}}, owner="erin", parent_run=parent)
    state = widgets.runner.load(child)
    assert state["parent_run"] == parent and state["status"] == "waiting"
    submitted = widgets.runner.track(child)[0]
    assert submitted["type"] == "run.submitted" and submitted["parent_run"] == parent
    assert f"admitted by the grant on parent run {parent} ({P}), decided by erin" == submitted["admission"]
    assert widgets.kernel.get(state["record"])["fields"]["title"] == "child"


def test_child_process_needs_a_parent_with_a_grant(widgets):
    parent = widgets.runner.submit(P, {"record": {"title": "parent"}}, owner="erin")
    assert widgets.runner.load(parent)["grant"] is None
    with pytest.raises(RunError, match=f"parent run {parent} holds no grant"):
        widgets.runner.submit(G, {"record": {"title": "child"}}, owner="erin", parent_run=parent)
    assert [s["process"] for s in widgets.runner.list()] == [P]
    with pytest.raises(RunError, match="0 matches"):
        widgets.runner.submit(G, {"record": {"title": "child"}}, owner="erin", parent_run="nosuchrun")


# ------------------------------------------------------------------ run listing

def test_run_for_record_returns_the_latest_run(widgets, monkeypatch):
    import hubproto.runner as runner_module

    clock = iter(f"2026-11-06T10:00:{s:02d}+00:00" for s in range(60))
    monkeypatch.setattr(runner_module, "utcnow", lambda: next(clock))  # each run gets its own second
    r1 = widgets.runner.submit(G, {"record": {"title": "g"}}, owner="erin")
    loc = widgets.runner.load(r1)["record"]
    assert widgets.runner.run_for_record(loc)["id"] == r1
    assert widgets.runner.run_for_record("rec:task:TASK-0999") is None
    widgets.runner.decide(r1, "hank", "reject", meaning="again please", payload_digest=seen(widgets.runner, r1))
    r2 = widgets.runner.submit(G, {"record": {}}, owner="erin", record=loc)
    assert widgets.runner.load(r2)["record"] == loc
    assert [r["number"] for r in widgets.kernel.query(type_="task")] == [widgets.kernel.get(loc)["number"]], "no second record"
    assert widgets.runner.run_for_record(loc)["id"] == r2
    assert [s["id"] for s in widgets.runner.list()] == [r1, r2], "runs are listed oldest first"


def test_run_for_record_is_the_latest_even_within_one_second(widgets, monkeypatch):
    import uuid

    import hubproto.runner as runner_module

    monkeypatch.setattr(runner_module, "utcnow", lambda: "2026-11-06T10:00:00+00:00")
    ids = iter(["f" * 32, "0" * 32])  # the second run's id sorts before the first one's

    class Ids:
        @staticmethod
        def uuid4():
            try:
                return type("U", (), {"hex": next(ids)})()
            except StopIteration:
                return uuid.uuid4()

    monkeypatch.setattr(runner_module, "uuid", Ids)
    r1 = widgets.runner.submit(G, {"record": {"title": "g"}}, owner="erin")
    loc = widgets.runner.load(r1)["record"]
    widgets.runner.decide(r1, "hank", "reject", meaning="again please", payload_digest=seen(widgets.runner, r1))
    r2 = widgets.runner.submit(G, {"record": {}}, owner="erin", record=loc)
    assert (r1, r2) == ("f" * 10, "0" * 10)
    assert widgets.runner.run_for_record(loc)["id"] == r2


def test_list_statuses_are_derived_from_the_track(widgets):
    assert widgets.runner.list() == []
    r1 = widgets.runner.submit(G, {"record": {"title": "g"}}, owner="erin")
    loc = widgets.runner.load(r1)["record"]
    widgets.runner.decide(r1, "hank", "reject", meaning="again please", payload_digest=seen(widgets.runner, r1))
    r2 = widgets.runner.submit(G, {"record": {}}, owner="erin", record=loc)
    statuses = {s["id"]: s["status"] for s in widgets.runner.list()}
    assert statuses == {r1: "rejected", r2: "waiting"}
    widgets.runner.decide(r2, "jack", "approve", meaning="ok", payload_digest=seen(widgets.runner, r2))
    statuses = {s["id"]: s["status"] for s in widgets.runner.list()}
    assert statuses == {r1: "rejected", r2: "completed"}
    for s in widgets.runner.list():
        assert s["status"] == Track(widgets.runner.root / s["id"]).status()
    assert widgets.kernel.get(loc)["state"] == "done"
    # The status is a projection of the Track: a Track with the terminal event removed reads differently.
    track = widgets.runner.root / r2 / "track.jsonl"
    lines = track.read_text().splitlines()
    assert '"type": "run.completed"' in lines[-1]
    track.write_text("\n".join(lines[:-1]) + "\n")
    assert Track(widgets.runner.root / r2).status() == "running"


# -------------------------------------------------------------- field routing

def test_field_assignee_route_waits_for_the_named_person(widgets):
    run_id = widgets.runner.submit(P, {"record": {"title": "w", "assignee": "frank"}}, owner="erin")
    state = widgets.runner.load(run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["frank"]
    rec = widgets.kernel.get(state["record"])
    assert rec["fields"]["assignee"] == "frank" and rec["assignees"] == ["frank"]
    assert [m["to"] for m in widgets.inbox.all() if m["kind"] == "approval" and m["run_id"] == run_id] == ["frank"]
    with pytest.raises(RunError, match="erin is not an assignee"):
        widgets.runner.decide(run_id, "erin", "approve", meaning="ok", payload_digest=seen(widgets.runner, run_id))
    assert widgets.runner.decide(run_id, "frank", "approve", meaning="ok", payload_digest=seen(widgets.runner, run_id))["pending"]["checkpoint"] == "approve-the-widget"


def test_field_assignee_without_approve_permission_fails_the_run(widgets):
    run_id = widgets.runner.submit(P, {"record": {"title": "w", "assignee": "gina"}}, owner="erin")
    state = widgets.runner.load(run_id)
    assert state["status"] == "failed" and state["pending"] is None
    skipped = event(widgets, run_id, "gate.skipped")
    assert len(skipped) == 1 and skipped[0]["candidate"] == "field:assignee"
    assert skipped[0]["reason"] == "gina may not approve a widget in Widgets/Requests"  # who and why, not a generic line
    failed = event(widgets, run_id, "run.failed")
    assert len(failed) == 1 and failed[0]["reason"].startswith("Nobody can take") and "gina may not approve" in failed[0]["reason"]
    assert failed[0]["kind"] == "organisation"
    rec = widgets.kernel.get(state["record"])
    assert rec["state"] == "failed" and rec["assignees"] == [widgets.runner.load(run_id)["owner"]]
    assert not [m for m in widgets.inbox.all() if m["kind"] == "approval" and m["run_id"] == run_id]
    with pytest.raises(RunError, match="not waiting"):
        widgets.runner.decide(run_id, "gina", "approve", meaning="ok", payload_digest=seen(widgets.runner, run_id))
