"""Processes as graphs (change control v2, section 10): the graph grammar, compiling numbered steps and graph sentences
into one shape, the runner walking graphs (derived Gates and their options, loops, returns, globals, cog and automatic
gates, validators, failed and Retry), items following the release in force, state maps, cogs supplying fields, and
"a cog's work is signed by a person" as an SOP sentence."""

import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from conftest import ROOT, release_all, seen
from hubproto import process_graph as pg
from hubproto.app import Hub
from hubproto.extract import ConventionExtractor
from hubproto.frames import load_frame
from hubproto.release import legacy_transitions, render
from hubproto.runner import RunError, apply_state_map, items_in_removed_statuses, state_map_questions

TEST_FRAMES = Path(__file__).resolve().parent / "frames"
G = "gizmo-review"

GIZMO = '''---
type: frame [0.2]
name: gizmo-policy
description: Test-only policy for gizmo reviews, written as a graph.
visibility: internal
version: "1.0.0"
scope: department
maintainer: tests
inherits: org-model
---
# Gizmo policy (test fixture)

## Roles
- gizmo-maker
- gizmo-checker
- gizmo-supervisor

## Members
- maya (person): gizmo-maker
- kim (person): gizmo-checker
- lee (person): gizmo-checker
- sam (person): gizmo-supervisor
- doc-reviewer (cog): gizmo-maker
- quality-assistant (cog): gizmo-checker, gizmo-maker

## Folders
- Gizmos

## Permissions
Gizmo makers may create gizmos in Gizmos.
Gizmo makers may read Gizmos.
Gizmo makers may approve gizmos in Gizmos.
Gizmo checkers may approve gizmos in Gizmos.
Gizmo checkers may read Gizmos.
Gizmo supervisors may read Gizmos.

## Approval criteria
The quality-assistant cog may approve gizmo-triage gizmos when the risk is low.

## Process: gizmo-review
Records are filed in Gizmos.
The record type is task.
Statuses: Open (to do), Checking (in progress, due within 3 days), Under review [review] (in progress), Approved (done, completed), Rejected (done, rejected), Cancelled (done, cancelled).
Starts in Open ("Open the gizmo"): the gizmo maker; needs title.
From Open to Checking: automatically, after the record filer.
From Checking to Under review ("Check"): the gizmo checker.
From Checking to Open ("Ask for detail"): the owner.
From Under review to Approved ("Approve"): the gizmo checker decides, not whoever decided "Check"; needs description; then the record linker runs.
From Under review to Open ("Send back"): the gizmo checker.
From Under review to Rejected ("Reject"): the gizmo checker.
From any status except done to Cancelled ("Cancel"): the gizmo supervisor.

## Process: gizmo-triage
Records are filed in Gizmos.
The record type is task.
Statuses: New (to do), Triage (in progress), Fast lane (in progress), Done (done).
Starts in New ("Open the gizmo"): the gizmo maker.
From New to Fast lane: automatically when the risk is low.
From New to Triage: automatically, after the record filer.
From Fast lane to Done ("Pass"): the quality-assistant cog decides, else the gizmo checker.
From Triage to Done ("Pass" [triage-pass]): the gizmo checker; then set priority to high and notify the owner.
'''


def gizmo_hub(tmp_path, edits: dict | None = None) -> Hub:
    (tmp_path / "frames" / "company").mkdir(parents=True)
    shutil.copy(ROOT / "frames" / "company" / "org-model.frame.md", tmp_path / "frames" / "company")
    text = GIZMO
    for old, new in (edits or {}).items():
        assert old in text, old
        text = text.replace(old, new)
    (tmp_path / "frames" / "gizmo-policy.frame.md").write_text(text)
    h = Hub(tmp_path)
    h.kernel.seed()
    return h


def edit_gizmo(hub: Hub, old: str, new: str, version: str) -> None:
    path = hub.frames.get("gizmo-policy").path
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new).replace('version: "1.0.0"', f'version: "{version}"').replace('version: "1.1.0"', f'version: "{version}"'))
    hub.frames = type(hub.frames)(hub.frames.root)


@pytest.fixture
def gizmos(tmp_path) -> Hub:
    h = gizmo_hub(tmp_path)
    proposal = release_all(h)
    assert proposal["digest"]
    return h


def rules_of(text: str, tmp_path) -> list[dict]:
    p = tmp_path / "x.frame.md"
    p.write_text(text)
    ex = ConventionExtractor().extract(load_frame(p))
    return ex


def proc(rules, name=G) -> dict:
    return next(r for r in rules if r["kind"] == "process" and r["name"] == name)


def events(hub, run_id, type_=None):
    return [e for e in hub.runner.track(run_id) if type_ is None or e["type"] == type_]


def decide(hub, run_id, by, transition=None, verdict=None, meaning="ok", **kw):
    return hub.runner.decide(run_id, by, verdict, meaning=meaning, payload_digest=seen(hub.runner, run_id), transition=transition, **kw)


def submit(hub, title="Gizmo", owner="maya", **fields):
    return hub.runner.submit(G, {"record": {"title": title, **fields}}, owner=owner)


# ---------------------------------------------------------------------------------------------- grammar
def test_graph_sentences_read_into_statuses_and_transitions(tmp_path):
    ex = rules_of(GIZMO, tmp_path)
    assert ex.notes == []
    p = proc(ex.rules)
    assert p["object"] == "gizmo" and p["record_type"] == "task" and p["steps"] == [] and p["terminal"] == "approved"
    assert [(s["id"], s["category"], s.get("outcome"), s.get("sla_days")) for s in p["statuses"]] == [
        ("open", "todo", None, None), ("checking", "in_progress", None, 3), ("review", "in_progress", None, None),
        ("approved", "done", "completed", None), ("rejected", "done", "rejected", None), ("cancelled", "done", "cancelled", None)]
    t = {x["id"]: x for x in p["transitions"]}
    assert list(t) == ["open-the-gizmo", "open-to-checking", "check", "ask-for-detail", "approve", "send-back", "reject", "cancel"]
    start = t["open-the-gizmo"]
    assert start["type"] == "initial" and start["to"] == "open" and start["gate"] == {"kind": "person", "role": "gizmo-maker"}
    assert start["fields"] == ["title"] and start["validators"] == [{"fields_set": ["title"]}]
    assert t["open-to-checking"]["gate"] == {"kind": "auto", "when": "actions-succeed"} and t["open-to-checking"]["actions"] == ["record-filer"]
    assert t["ask-for-detail"]["gate"] == {"kind": "person", "role": "owner"}
    assert t["approve"]["gate"] == {"kind": "person", "role": "gizmo-checker", "not": ["check"]}
    assert t["approve"]["fields"] == ["description"] and t["approve"]["actions"] == ["record-linker"]
    assert t["cancel"]["type"] == "global" and t["cancel"]["except"] == ["approved", "rejected", "cancelled"]
    assert t["approve"]["source"]["line"] > 0 and t["approve"]["source"]["text"].startswith("From Under review to Approved")
    triage = {x["id"]: x for x in proc(ex.rules, "gizmo-triage")["transitions"]}
    assert triage["new-to-fast-lane"]["gate"] == {"kind": "auto", "when": "risk_low"}
    assert triage["pass"]["gate"] == {"kind": "cog", "cog": "quality-assistant", "fallback": "gizmo-checker"}
    assert triage["triage-pass"]["actions"] == [{"set": {"priority": "high"}}, {"notify": "field:owner"}]


@pytest.mark.parametrize("line,why", [
    ("From Nowhere to Checking: the gizmo checker.", "is not one of the statuses this process declares"),
    ("From Checking to Under review (\"Peek\"): perhaps someone.", "does not say who decides"),
    ("From Checking to Under review (\"Peek\"): automatically when the moon is full.", "not a condition the hub can evaluate"),
    ("From Checking to Under review (\"Check\"): the gizmo checker.", "two transitions have the id 'check'"),
    ("From Checking to Under review (\"Peek\"): the gizmo checker; then a miracle happens.", "is not an action"),
    ("From Checking to Under review (\"Peek\"): the gizmo checker; perhaps later.", "does not read"),
    ("1. [gizmo-maker] Open the gizmo (record-filer)", "either as numbered steps or as statuses"),
])
def test_graph_sentences_that_do_not_read_are_errors(tmp_path, line, why):
    text = GIZMO.replace("From any status except done", line + "\nFrom any status except done", 1)
    notes = [n for n in rules_of(text, tmp_path).notes if n.get("severity") == "error"]
    assert notes and why in notes[0]["note"], notes


def test_statuses_must_come_before_transitions_and_steps_cannot_mix(tmp_path):
    text = GIZMO.replace("Statuses: Open (to do), Checking", "From Open to Checking: the gizmo checker.\nStatuses: Open (to do), Checking", 1)
    assert any("declare the statuses" in n["note"] for n in rules_of(text, tmp_path).notes)


def test_compile_guards_the_graph_sentences(tmp_path):
    h = gizmo_hub(tmp_path, {"then the record linker runs": "then the record linkerer runs",
                             "From Triage to Done (\"Pass\" [triage-pass]): the gizmo checker": "From Triage to Done (\"Pass\" [triage-pass]): the gizmo approver",
                             "The quality-assistant cog may approve gizmo-triage gizmos when the risk is low.\n": ""})
    errors = [f["detail"] for f in h.compile()["findings"] if f["severity"] == "error"]
    assert any("runs 'record-linkerer', which is not a cog" in d for d in errors), errors
    assert any("'gizmo-approver', which is not a role" in d for d in errors), errors
    assert any("quality-assistant cog, which has no approval criteria for gizmo-triage" in d for d in errors), errors


def test_grounding_checks_each_graph_sentence(tmp_path):
    from hubproto.guards import grounding_guard

    h = gizmo_hub(tmp_path)
    rules = ConventionExtractor().extract(h.frames.get("gizmo-policy")).rules
    p = proc(rules)
    assert not [f for f in grounding_guard([p], h.frames.frames)]
    p["transitions"][3]["source"] = {**p["transitions"][3]["source"], "text": "From Checking to Done: anyone."}
    assert any("does not match the Frame" in f.detail for f in grounding_guard([p], h.frames.frames))


def test_workflow_model_checks_are_called_when_present(tmp_path, monkeypatch):
    import sys
    import types

    seen_graphs = []
    fake = types.ModuleType("hubproto.workflow_model")
    fake.checks = lambda graph: seen_graphs.append(graph) or [{"severity": "error", "message": "an unreachable status", "element": "review"}]
    monkeypatch.setitem(sys.modules, "hubproto.workflow_model", fake)
    import hubproto

    monkeypatch.setattr(hubproto, "workflow_model", fake, raising=False)  # the real module may be imported already (the editor's)
    h = gizmo_hub(tmp_path)
    found = [f for f in h.compile()["findings"] if f["guard"] == "workflow"]
    graph_errors = [f for f in found if f["severity"] == "error"]
    assert graph_errors and graph_errors[0]["detail"] == f"{G}: an unreachable status" and graph_errors[0]["line"] > 0
    assert seen_graphs and all("states" not in g and "statuses" in g for g in seen_graphs), "it is given the 10.1 graph"


# ---------------------------------------------------------------------------------------------- compile
def test_a_graph_process_renders_the_10_1_shape(gizmos):
    b = gizmos.current_bundle()
    wf = b["workflows"][G]
    graph = pg.graph_only(wf)
    assert set(graph) == {"process", "record_type", "statuses", "transitions", "source", "form"}
    assert graph["process"] == G and graph["record_type"] == "task" and graph["form"] == "graph"
    assert [s["id"] for s in graph["statuses"]] == ["open", "checking", "review", "approved", "rejected", "cancelled", "failed"]
    assert pg.status_of(wf, "failed")["system"] is True
    assert [t["id"] for t in graph["transitions"]][-1] == "retry"
    assert graph["source"]["frame"] == "gizmo-policy" and graph["source"]["lines"]["approve"] > 0
    # The view of the earlier shape, for its readers.
    assert wf["states"] == ["open", "checking", "review", "approved", "rejected", "cancelled"] and wf["terminal"] == "approved"
    assert [(c["id"], c["state"], c["sla_days"]) for c in wf["checkpoints"]] == [("check", "checking", 3), ("approve", "review", None)]
    assert [t["step"] for t in legacy_transitions(wf) if t["kind"] == "human"] == ["check", "approve"]
    op = b["ops"][G]
    assert [s["id"] for s in op["steps"]] == ["record-filer", "record-linker"] and op["start_role"] == "gizmo-maker"
    assert op["steps"][0]["role"] == "gizmo-maker" and op["object"] == "gizmo"


def test_numbered_steps_compile_into_the_graph_with_no_change_of_meaning(hub):
    rules = ConventionExtractor().extract(load_frame(TEST_FRAMES / "widget-policy.frame.md")).rules
    b = render(rules, hub.kernel.types)
    wf = b["workflows"]["widget-request"]
    assert wf["form"] == "steps"
    assert [(s["id"], s["category"], s.get("outcome")) for s in wf["statuses"]] == [
        ("open", "todo", None), ("awaiting-check-the-widget", "in_progress", None), ("awaiting-approve-the-widget", "in_progress", None),
        ("done", "done", "completed"), ("failed", "in_progress", None), ("cancelled", "done", "cancelled")]
    assert pg.initial(wf)["gate"] == {"kind": "person", "role": "widget-requester"}
    # A rejection is a transition, Cancel a global, and failed has its Retry loop.
    rej = pg.rejection_option(wf, "awaiting-approve-the-widget")
    assert rej["id"] == "reject-approve-the-widget" and rej["to"] == "awaiting-check-the-widget"
    cancel = pg.transition(wf, "cancel")
    assert cancel["type"] == "global" and cancel["except"] == ["done", "cancelled"] and pg.global_applies(wf, cancel, "open")
    assert pg.transition(wf, "retry")["from"] == ["failed"]
    assert [o["id"] for o in pg.options(wf, "awaiting-check-the-widget")] == ["check-the-widget", "reject-check-the-widget"]
    gadget = b["workflows"]["gadget-request"]
    assert pg.rejection_option(gadget, "awaiting-approve-the-gadget")["to"] == "rejected"
    assert pg.status_of(gadget, "rejected") == {"id": "rejected", "name": "Rejected", "category": "done", "outcome": "rejected"}


def test_a_return_to_the_owner_compiles_to_returned_with_a_resubmit(released):
    wf = released.current_bundle()["workflows"]["design-release"]
    resub = pg.transition(wf, "resubmit")
    assert resub["from"] == ["returned"] and resub["actions"][0] == "submit-the-design" and "resume_from" not in resub
    assert pg.rejection_option(wf, "awaiting-review-the-design")["to"] == "returned"


def test_the_graph_writes_back_as_sentences_that_read_to_the_same_graph(tmp_path, gizmos):
    p = proc(ConventionExtractor().extract(gizmos.frames.get("gizmo-policy")).rules)
    lines = pg.to_sentences(p)
    assert lines[0].startswith("Statuses: Open (to do), Checking (in progress, due within 3 days), Under review [review] (in progress)")
    text = GIZMO.split("## Process: gizmo-review")[0] + "## Process: gizmo-review\nRecords are filed in Gizmos.\nThe record type is task.\n" + "\n".join(lines) + "\n"
    again = proc(rules_of(text, tmp_path).rules)
    strip = lambda ts: [{k: v for k, v in t.items() if k not in ("line", "source")} for t in ts]
    assert strip(again["transitions"]) == strip(p["transitions"])
    assert [{k: v for k, v in s.items() if k != "line"} for s in again["statuses"]] == [{k: v for k, v in s.items() if k != "line"} for s in p["statuses"]]


def test_a_process_of_numbered_steps_converts_to_graph_sentences(tmp_path, hub):
    rules = ConventionExtractor().extract(load_frame(TEST_FRAMES / "widget-policy.frame.md")).rules
    b = render(rules, hub.kernel.types)
    wf, op = b["workflows"]["widget-request"], b["ops"]["widget-request"]
    lines = pg.to_sentences(wf, cogs={s["id"]: s["cog"]["id"] for s in op["steps"]})
    # Ids are kept (a rename keeps the id): the compiled ones are written out where they are not the name's slug.
    assert 'From Open to Awaiting check the widget ("Automatically" [auto-awaiting-check-the-widget]): automatically, after the record filer.' in lines
    text = GIZMO.split("## Process: gizmo-review")[0] + "## Process: widget-request\nRecords are filed in Gizmos.\nThe record type is task.\n" + "\n".join(lines) + "\n"
    ex = rules_of(text, tmp_path)
    assert ex.notes == [], ex.notes
    p = proc(ex.rules, "widget-request")
    assert [t["id"] for t in p["transitions"]] == [t["id"] for t in wf["transitions"] if not t.get("system")]
    assert [s["id"] for s in p["statuses"]] == [s["id"] for s in wf["statuses"] if not s.get("system")]


def test_the_owner_or_an_approver_and_a_swarm_read_and_write_back(tmp_path):
    text = GIZMO.replace('From any status except done to Cancelled ("Cancel"): the gizmo supervisor.',
                         'From any status except done to Cancelled ("Cancel"): the owner or an approver.\n'
                         'From Checking to Checking ("Second look"): the gizmo checker; then the quality assistant with the doc reviewer run.')
    ex = rules_of(text, tmp_path)
    assert ex.notes == [], ex.notes
    t = {x["id"]: x for x in proc(ex.rules)["transitions"]}
    assert t["cancel"]["gate"] == {"kind": "person", "role": "owner", "also": "approvers"}
    assert t["second-look"]["actions"] == [{"swarm": ["quality-assistant", "doc-reviewer"]}]
    lines = pg.to_sentences(proc(ex.rules))
    assert 'From any status except done to Cancelled ("Cancel"): the owner or an approver.' in lines
    assert any(x.endswith("then the quality assistant with the doc reviewer runs.") for x in lines), lines
    from hubproto.release import render_graph_manifest

    step = next(x for x in render_graph_manifest(proc(ex.rules))["steps"] if x["cog"]["id"] == "swarm")
    assert step["input"]["cogs"] == ["quality-assistant", "doc-reviewer"] and step["input"]["question"] == "Second look"


def repository_processes():
    out = []
    for path in sorted(list((ROOT / "frames").rglob("*.frame.md")) + list((ROOT / "profiles").rglob("*.frame.md")) + list(TEST_FRAMES.glob("*.frame.md"))):
        try:
            frame = load_frame(path)
        except Exception:
            continue
        out += [(path.relative_to(ROOT), r) for r in ConventionExtractor().extract(frame).rules if r["kind"] == "process"]
    return out


def test_every_repository_process_round_trips_through_its_graph_sentences(tmp_path):
    """The property behind a workflow change on a change order: the graph the compiler produces, stated as the editor
    states it (``workflows.process_rule``) and written as the drafter writes it, compiles back to the same rule and the
    same graph; and for numbered steps that graph is the one they compile to (step ids aside: the prose names cogs)."""
    from hubproto import workflows
    from hubproto.prose_drafting import graph_lines, same_rule
    from hubproto.release import render_workflow

    head = '---\ntype: frame [0.2]\nname: rt\ndescription: x\nvisibility: internal\nversion: "1.0.0"\nscope: company\nmaintainer: t\n---\n# x\n'
    procs = repository_processes()
    assert len(procs) >= 36
    for where, r in procs:
        wf = render_workflow(r, {}, r.get("record_type") or "x")
        rule1 = workflows.process_rule({"processes": {r["name"]: r}}, r["name"], pg.graph_only(wf))
        p = tmp_path / "rt.frame.md"
        p.write_text(head + "\n".join(graph_lines(rule1)) + "\n")
        ex = ConventionExtractor().extract(load_frame(p))
        assert not [n for n in ex.notes if n.get("severity") == "error"], (where, r["name"], ex.notes)
        rule2 = next(x for x in ex.rules if x["kind"] == "process")
        assert same_rule(rule1, {**rule2, "object": rule1["object"]}), (where, r["name"])
        g1, g2 = (pg.graph_only(render_workflow(x, {}, "x")) for x in (rule1, rule2))
        assert {k: v for k, v in g1.items() if k != "source"} == {k: v for k, v in g2.items() if k != "source"}, (where, r["name"])
        # Against the numbered steps' own graph: the same statuses, and the same transitions, gates and targets.
        key = lambda g: {t["id"]: (t["type"], tuple(t.get("from") or []), t["to"], t.get("gate"), tuple(t.get("except") or []))
                         for t in g["transitions"]}
        assert key(g2) == key(pg.graph_only(wf)), (where, r["name"])
        assert sorted(map(str, g2["statuses"])) == sorted(map(str, pg.graph_only(wf)["statuses"])), (where, r["name"])


# ---------------------------------------------------------------------------------------------- the runner on a graph
def test_the_waiting_decision_is_derived_from_the_status(gizmos, today):
    run_id = submit(gizmos)
    state = gizmos.runner.load(run_id)
    assert state["status"] == "waiting" and state["at"] == "checking"
    p = state["pending"]
    assert p["checkpoint"] == "check" and p["title"] == "Check" and p["assignees"] == ["kim", "lee"]
    assert p["due"] == (today + timedelta(days=3)).isoformat(), "the due date comes from the status"
    assert [(o["id"], o["verdict"], o.get("assignees")) for o in p["options"]] == [("check", "approve", None), ("ask-for-detail", "reject", ["maya"])]
    rec = gizmos.kernel.get(state["record"])
    assert rec["state"] == "checking" and rec["assignees"] == ["kim", "lee"]
    assert [e["step"] for e in events(gizmos, run_id, "step.started")] == ["record-filer"]
    # One Gate per status and decider: the owner sees it too, for the option only she decides.
    assert run_id in [r["id"] for r in gizmos.runner.pending_for("maya")]
    with pytest.raises(RunError, match="maya may not choose “Check” here"):
        decide(gizmos, run_id, "maya", "check")
    with pytest.raises(RunError, match="not a way forward from here"):
        decide(gizmos, run_id, "kim", "approve")


def test_a_loop_back_runs_the_actions_again_and_waits_afresh(gizmos):
    run_id = submit(gizmos)
    state = decide(gizmos, run_id, "maya", "ask-for-detail", meaning="what colour is it?")
    assert state["status"] == "waiting" and state["at"] == "checking" and state["pending"]["assignees"] == ["kim", "lee"]
    assert [e["step"] for e in events(gizmos, run_id, "step.started")] == ["record-filer", "record-filer"]
    rec = gizmos.kernel.get(state["record"])
    assert [t["to"] for t in rec["transitions"]] == ["checking", "open", "checking"]
    decided = events(gizmos, run_id, "gate.decided")[-1]
    assert decided["transition"] == "ask-for-detail" and decided["verdict"] == "reject" and decided["by"] == "maya"


def test_a_gate_keeps_out_whoever_decided_an_earlier_step(gizmos):
    run_id = submit(gizmos)
    state = decide(gizmos, run_id, "kim", "check")
    assert state["at"] == "review" and state["pending"]["assignees"] == ["lee"], "not whoever decided “Check”"
    assert [o["id"] for o in state["pending"]["options"]] == ["approve", "send-back", "reject"]


def test_a_validator_blocks_the_move_with_a_sentence(gizmos):
    run_id = submit(gizmos)
    decide(gizmos, run_id, "kim", "check")
    with pytest.raises(RunError, match="Approve needs description first"):
        decide(gizmos, run_id, "lee", "approve")
    assert gizmos.runner.load(run_id)["status"] == "waiting" and not [e for e in events(gizmos, run_id, "gate.decided") if e["by"] == "lee"]
    assert "description" in gizmos.runner.allowed_decision_fields(gizmos.runner.load(run_id), "approve")
    state = decide(gizmos, run_id, "lee", "approve", fields={"description": "blue, 4 cm"})
    assert state["status"] == "completed" and state["at"] == "approved"
    rec = gizmos.kernel.get(state["record"])
    assert rec["state"] == "approved" and rec["fields"]["description"] == "blue, 4 cm"
    assert [e["step"] for e in events(gizmos, run_id, "step.started")] == ["record-filer", "record-linker"], "then its actions run"


def test_approve_and_reject_map_to_the_primary_and_the_rejection_transitions(gizmos):
    run_id = submit(gizmos)
    decide(gizmos, run_id, "kim", None, verdict="approve")
    assert gizmos.runner.load(run_id)["at"] == "review"
    state = decide(gizmos, run_id, "lee", None, verdict="reject", meaning="needs a drawing")
    assert state["at"] == "checking" and state["status"] == "waiting", "reject takes the first rejection: Send back, then on afresh"
    assert events(gizmos, run_id, "gate.decided")[-1]["transition"] == "send-back" and not events(gizmos, run_id, "run.rejected")
    decide(gizmos, run_id, "lee", "check")
    state = decide(gizmos, run_id, "kim", "reject", meaning="not wanted")
    assert state["status"] == "rejected" and gizmos.kernel.get(state["record"])["state"] == "rejected"
    assert gizmos.kernel.get(state["record"])["transitions"][-1]["by"] == "kim"


def test_globals_apply_from_any_status_except_their_list(gizmos):
    run_id = submit(gizmos)
    with pytest.raises(RunError, match="only the gizmo supervisor may take “Cancel”"):
        gizmos.runner.cancel(run_id, "maya", reason="not needed")
    state = gizmos.runner.choose(run_id, "sam", "cancel", meaning="duplicate of another gizmo")
    assert state["status"] == "cancelled" and state["at"] == "cancelled"
    assert gizmos.kernel.get(state["record"])["state"] == "cancelled"
    assert events(gizmos, run_id, "run.cancelled")[0]["transition"] == "cancel"
    with pytest.raises(RunError, match="not a way out of Cancelled"):
        gizmos.runner.choose(run_id, "sam", "cancel", meaning="again")


def test_a_failed_action_moves_to_failed_and_retry_resumes_it(gizmos):
    run_id = submit(gizmos)
    loc = gizmos.runner.load(run_id)["record"]
    decide(gizmos, run_id, "kim", "check")
    gizmos.kernel.hold(loc, True, "waiting for the lab", by="kim")
    with pytest.raises(RunError, match="on hold"):
        decide(gizmos, run_id, "lee", "send-back")
    gizmos.kernel.hold(loc, False, "", by="kim")
    state = decide(gizmos, run_id, "lee", "approve", fields={"description": "fine"})
    assert state["status"] == "completed"
    # An action that fails: the item goes to failed (the system status) and Retry resumes the move.
    run2 = submit(gizmos, title="Second")
    loc2 = gizmos.runner.load(run2)["record"]
    decide(gizmos, run2, "kim", "check")
    decide(gizmos, run2, "lee", "send-back", meaning="again")
    assert gizmos.runner.load(run2)["at"] == "checking"
    decide(gizmos, run2, "kim", "check")
    state = gizmos.runner.load(run2)
    gizmos.kernel.hold(loc2, True, "lab", by="kim")
    gizmos.kernel.hold(loc2, False, "", by="kim")
    import hubproto.runner as runner_module

    real = runner_module.Runner._run_actions
    calls = {"n": 0}

    def flaky(self, state, bundle, track, wf, mv):
        if mv["transition"] == "approve" and not calls["n"]:
            calls["n"] += 1
            self._fail(state, track, "record-linker", "the link service is down")
            return False
        return real(self, state, bundle, track, wf, mv)
    runner_module.Runner._run_actions = flaky
    try:
        state = decide(gizmos, run2, "lee", "approve", fields={"description": "fine"})
        assert state["status"] == "failed" and state["at"] == "review" and state["moving"]["transition"] == "approve"
        assert gizmos.kernel.get(loc2)["state"] == "failed"
        state = gizmos.runner.choose(run2, "maya", "retry", meaning="service is back")
    finally:
        runner_module.Runner._run_actions = real
    assert state["status"] == "completed" and gizmos.kernel.get(loc2)["state"] == "approved"
    assert events(gizmos, run2, "run.retried")[0]["from_step"] == "record-linker"


def test_an_automatic_transition_fires_when_its_condition_holds(gizmos):
    low = gizmos.runner.submit("gizmo-triage", {"record": {"title": "low", "risk": "low", "conditions": {"risk_low": True}}}, owner="maya")
    high = gizmos.runner.submit("gizmo-triage", {"record": {"title": "high", "risk": "high"}}, owner="maya")
    assert gizmos.runner.load(high)["at"] == "triage" and gizmos.runner.load(high)["pending"]["assignees"] == ["kim", "lee"]
    # The fast lane is a cog gate: the cog decides within its approval criteria.
    state = gizmos.runner.load(low)
    assert state["status"] == "completed", events(gizmos, low)
    decided = events(gizmos, low, "gate.decided")[0]
    assert decided["by"] == "quality-assistant" and decided["by_type"] == "cog" and decided["transition"] == "pass"
    assert "may approve gizmo-triage" in decided["delegating_rule"]["text"]
    assert events(gizmos, low, "gate.pending")[0]["route_rule"]["text"].startswith("From Fast lane to Done")


def test_inline_actions_set_fields_and_notify(gizmos):
    run_id = gizmos.runner.submit("gizmo-triage", {"record": {"title": "high", "risk": "high"}}, owner="maya")
    state = decide(gizmos, run_id, "kim", "triage-pass")
    assert state["status"] == "completed"
    rec = gizmos.kernel.get(state["record"])
    assert rec["fields"]["priority"] == "high" and rec["state"] == "done"
    assert any(m["kind"] == "moved" and m["run_id"] == run_id for m in gizmos.inbox.for_principal("maya"))


def test_a_cog_gate_falls_back_to_its_role_outside_its_criteria(gizmos):
    run_id = gizmos.runner.submit("gizmo-triage", {"record": {"title": "low", "risk": "low", "conditions": {"risk_low": True}}}, owner="doc-reviewer")
    state = gizmos.runner.load(run_id)
    skipped = events(gizmos, run_id, "gate.skipped")
    assert skipped and "a person signs a cog's work" in skipped[0]["reason"] and "org-model line" in skipped[0]["reason"]
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["kim", "lee"], "the fallback role"


# ---------------------------------------------------------------------------------------------- cogs (change control 7)
def test_a_cogs_work_is_signed_by_a_person_because_an_sop_says_so(tmp_path):
    h = gizmo_hub(tmp_path)
    release_all(h)
    sod = [s for s in h.current_bundle()["sod"] if s.get("rule") == "cog-work-signed-by-person"]
    assert sod and sod[0]["object"] == "*" and sod[0]["source"]["text"] == "A cog's work is signed by a person."
    org = h.frames.get("org-model").path
    org.write_text(org.read_text().replace("\n## Segregation of duties\nA cog's work is signed by a person.\n", "\n"))
    h.frames = type(h.frames)(h.frames.root)
    p = h.compile()
    removed = [c for c in p["changes"] if c["op"] == "remove" and c["rule"]["kind"] == "sod"]
    assert removed and removed[0]["widening"], "removing it is a widening (two policy owners)"
    for r in ("bob", "carol"):
        h.review(p["id"], r, approve_all=True)
    h.release(p["id"], "bob")
    run_id = h.runner.submit("gizmo-triage", {"record": {"title": "low", "risk": "low", "conditions": {"risk_low": True}}}, owner="doc-reviewer")
    decided = events(h, run_id, "gate.decided")
    assert decided and decided[0]["by"] == "quality-assistant", "without the sentence a cog may sign another cog's work"


def test_a_cog_never_signs_its_own_work_whatever_the_policy_says(gizmos):
    run_id = gizmos.runner.submit("gizmo-triage", {"record": {"title": "low", "risk": "low", "conditions": {"risk_low": True}}}, owner="quality-assistant")
    skipped = [e for e in events(gizmos, run_id, "gate.skipped") if e["candidate"] == "cog:quality-assistant"]
    assert skipped and "they prepared this item" in skipped[0]["reason"]


def test_a_cog_supplies_fields_at_a_gate_its_role_may_decide(gizmos):
    gizmos.runner.cog_users_inline = False
    try:
        run_id = gizmos.runner.submit("gizmo-triage", {"record": {"title": "low", "risk": "low", "conditions": {"risk_low": True}}}, owner="maya")
        state = gizmos.runner.load(run_id)
        assert state["pending"]["assignees"] == ["quality-assistant"]
        crit = gizmos.current_bundle()["approval_criteria"][0]["source"]
        with pytest.raises(RunError, match="may not set risk"):
            decide(gizmos, run_id, "quality-assistant", "pass", fields={"risk": "none"}, delegating_rule=crit)
        state = decide(gizmos, run_id, "quality-assistant", "pass", fields={"description": "fast lane: low risk"}, delegating_rule=crit,
                       meaning="approved within its criteria")
    finally:
        gizmos.runner.cog_users_inline = True
    assert state["status"] == "completed"
    rec = gizmos.kernel.get(state["record"])
    assert rec["fields"]["description"] == "fast lane: low risk" and rec["setters"]["description"] == "quality-assistant"


# ---------------------------------------------------------------------------------------------- the release in force (5.1)
def test_an_item_follows_the_release_in_force_from_its_next_move(gizmos):
    first = gizmos.releases.current()["digest"]
    run_id = submit(gizmos)
    edit_gizmo(gizmos, 'From Under review to Approved ("Approve"): the gizmo checker decides',
               'From Under review to Approved ("Approve"): the gizmo supervisor decides', "1.1.0")
    edit_gizmo(gizmos, "Gizmo supervisors may read Gizmos.", "Gizmo supervisors may read Gizmos.\nGizmo supervisors may approve gizmos in Gizmos.", "1.2.0")
    second = release_all(gizmos)["digest"]
    assert second != first
    state = gizmos.runner.load(run_id)
    assert state["release"] == first and state["pending"]["assignees"] == ["kim", "lee"], "the waiting Gate keeps its assignees"
    state = decide(gizmos, run_id, "kim", "check")
    assert state["release"] == second and state["pending"]["assignees"] == ["sam"], "the next Gate is routed under the release in force"
    changed = events(gizmos, run_id, "run.release_changed")
    assert [(e["from"], e["to"], e["status"], e["move"]) for e in changed] == [(first, second, "checking", "a decision")]
    assert events(gizmos, run_id, "gate.decided")[0]["release_digest"] == second


def remove_under_review(gizmos) -> dict:
    """A change of gizmo-review that removes Under review (Check approves directly); Rejected goes with it (only Under
    review led there, and the workflow checks refuse a status nothing leads to). Returns its proposal."""
    edit_gizmo(gizmos, "Under review [review] (in progress), ", "", "1.1.0")
    edit_gizmo(gizmos, "Rejected (done, rejected), ", "", "1.1.0")
    text = gizmos.frames.get("gizmo-policy").path.read_text()
    text = text.replace('From Checking to Under review ("Check"): the gizmo checker.', 'From Checking to Approved ("Check"): the gizmo checker.')
    for line in ('From Under review to Approved ("Approve")', 'From Under review to Open ("Send back")', 'From Under review to Rejected ("Reject")'):
        text = "\n".join(x for x in text.splitlines() if not x.startswith(line)) + "\n"
    gizmos.frames.get("gizmo-policy").path.write_text(text)
    gizmos.frames = type(gizmos.frames)(gizmos.frames.root)
    proposal = gizmos.compile()
    assert not [f for f in proposal["findings"] if f["severity"] == "error"], [f for f in proposal["findings"] if f["severity"] == "error"]
    return proposal


def release_proposal(hub, proposal):
    for r in ("bob", "carol"):
        hub.review(proposal["id"], r, approve_all=True)
    hub.release(proposal["id"], "bob")


def test_an_item_in_a_status_the_new_release_removed_stays_until_a_state_map_moves_it(gizmos):
    run_id = submit(gizmos)
    decide(gizmos, run_id, "kim", "check")
    old_graph = gizmos.current_bundle()["workflows"][G]
    proposal = remove_under_review(gizmos)
    asked = [q for q in proposal["questions"] if q.get("code") == "state-map"]
    # The status removed is the question's ``state``; its own ``status`` is the question's (open, answered...).
    assert [(q["process"], q["state"], q["count"], q["status"]) for q in asked] == [(G, "review", 1, "open")]
    assert asked[0]["text"] == f"1 task is in Under review, which this change removes from {G}: where do they go?"
    assert asked[0]["id"] == state_map_questions(gizmos, proposal)[0]["id"], "the same question has the same id"
    release_proposal(gizmos, proposal)
    new_graph = gizmos.current_bundle()["workflows"][G]
    assert items_in_removed_statuses(gizmos, old_graph, new_graph) == {"review": 1}
    # The release itself does not move the waiting item: it stays in its status, on its release, until the state map.
    assert gizmos.runner.load(run_id)["at"] == "review" and gizmos.runner.load(run_id)["status"] == "waiting"
    moved = apply_state_map(gizmos, G, {"review": "checking"}, by="bob", co="CO-2026-0007")
    loc = gizmos.runner.load(run_id)["record"]
    assert moved == [{"record": loc, "run": run_id, "from": "review", "to": "checking"}]
    state = gizmos.runner.load(run_id)
    assert state["at"] == "checking" and state["status"] == "waiting" and state["release"] == gizmos.releases.current()["digest"]
    withdrawn = events(gizmos, run_id, "gate.withdrawn")
    assert withdrawn and withdrawn[0]["assignees"] == ["lee"]
    mv = events(gizmos, run_id, "run.moved")[0]
    assert mv["reason"] == "Moved from Under review to Checking by CO-2026-0007 (state map)" and mv["co"] == "CO-2026-0007"
    rec = gizmos.kernel.get(loc)
    assert any(t["reason"] == mv["reason"] for t in rec["transitions"])
    assert any(m["kind"] == "withdrawn" and m["run_id"] == run_id for m in gizmos.inbox.for_principal("lee"))
    assert state["pending"]["checkpoint"] == "check", "the item waits at the Gate of its new status"
    assert len([e for e in events(gizmos, run_id, "step.started")]) == 1, "no action ran"
    state = decide(gizmos, run_id, "lee", "check")
    assert state["status"] == "completed" and gizmos.kernel.get(loc)["state"] == "approved"
    assert items_in_removed_statuses(gizmos, old_graph, new_graph) == {}


def card_digest(hub, run_id, who):
    """The digest the inbox card quotes: from the waiting list, as the shell draws it."""
    run = next(r for r in hub.runner.pending_for(who) if r["id"] == run_id)
    return run.get("seen_digest") or hub.runner.seen_digest(run)


@pytest.mark.parametrize("how", ["follows the release", "moved by a state map"])
def test_an_item_waiting_when_a_release_comes_into_force_is_decided_from_a_fresh_card(gizmos, how):
    """Usability B5: a card drawn after the release is signable; a card drawn before a move is not (signatures bind what
    was seen)."""
    run_id = submit(gizmos)
    decide(gizmos, run_id, "kim", "check")
    before = card_digest(gizmos, run_id, "lee")
    if how == "follows the release":
        edit_gizmo(gizmos, "Gizmo supervisors may read Gizmos.", "Gizmo supervisors may read Gizmos.\nGizmo supervisors may approve gizmos in Gizmos.", "1.1.0")
        release_all(gizmos)
        option = "approve"
    else:
        release_proposal(gizmos, remove_under_review(gizmos))
        apply_state_map(gizmos, G, {"review": "checking"}, by="bob", co="CO-1")
        option = "check"
        with pytest.raises(RunError, match="changed since you opened it"):
            gizmos.runner.decide(run_id, "lee", None, meaning="ok", payload_digest=before, transition=option)
    digest = card_digest(gizmos, run_id, "lee")
    assert digest == gizmos.runner.seen_digest(gizmos.runner.load(run_id))
    state = gizmos.runner.decide(run_id, "lee", None, meaning="ok", payload_digest=digest, transition=option,
                                 fields={"description": "fine"} if option == "approve" else None)
    assert state["status"] == "completed"
    assert all("seen_digest" not in json.loads(p.read_text()) for p in gizmos.runner.root.glob("*/state.json"))


def test_a_state_map_moves_only_removed_statuses_to_statuses_in_force(gizmos):
    with pytest.raises(RunError, match="gizmo-review is new in this release"):
        apply_state_map(gizmos, G, {"review": "checking"}, by="bob", co="CO-1")
    run_id = submit(gizmos)
    decide(gizmos, run_id, "kim", "check")
    release_proposal(gizmos, remove_under_review(gizmos))
    refused = {
        "'checking' is still a status of gizmo-review": {"checking": "open"},
        "'nowhere' was not a status of gizmo-review": {"nowhere": "checking"},
        "sends 'review' to 'limbo', which is not a status of gizmo-review": {"review": "limbo"},
        "sends 'review' to 'failed'": {"review": "failed"},
    }
    for why, mapping in refused.items():
        with pytest.raises(RunError, match=why):
            apply_state_map(gizmos, G, mapping, by="bob", co="CO-1")
    with pytest.raises(RunError, match="the release did not change gizmo-triage"):
        apply_state_map(gizmos, "gizmo-triage", {"new": "triage"}, by="bob", co="CO-1")
    assert gizmos.runner.load(run_id)["at"] == "review", "a refused map moves nothing"
    assert not events(gizmos, run_id, "run.moved")


def test_a_state_map_never_moves_items_of_another_process(gizmos):
    # A task of gizmo-triage (the same record type) standing in a status named like the removed one is not touched.
    other = gizmos.runner.submit("gizmo-triage", {"record": {"title": "other", "risk": "high"}}, owner="maya")
    loc = gizmos.runner.load(other)["record"]
    gizmos.kernel.append("record.transitioned", loc, {"from": "triage", "to": "review", "reason": "test", "assignees": [], "due": None}, by="test")
    old_graph = gizmos.current_bundle()["workflows"][G]
    release_proposal(gizmos, remove_under_review(gizmos))
    assert items_in_removed_statuses(gizmos, old_graph, gizmos.current_bundle()["workflows"][G]) == {}
    assert apply_state_map(gizmos, G, {"review": "checking"}, by="bob", co="CO-1") == []
    assert gizmos.kernel.get(loc)["state"] == "review" and not events(gizmos, other, "run.moved")


# ---------------------------------------------------------------------------------------------- shell
def test_the_inbox_card_offers_each_option_as_a_button(gizmos):
    from hubproto.shell.inbox import InboxMixin

    run_id = submit(gizmos)
    decide(gizmos, run_id, "kim", "check")
    run = gizmos.runner.load(run_id)

    class Card(InboxMixin):
        pass
    card = Card()
    html = card._option_buttons(run, "lee", "approve", False)
    assert "name='transition' value='approve'" in html and ">Approve<" in html
    rej = card._option_buttons(run, "lee", "reject", False)
    assert "value='send-back'" in rej and "value='reject'" in rej
    assert card._option_buttons({**run, "workflow": {"form": "steps"}}, "lee", "approve", False) == "", "numbered steps keep their buttons"
