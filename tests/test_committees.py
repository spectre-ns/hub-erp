"""Groups and committees (change control v2, section 11; workstream 9): groups in the org model (listed, by role,
carrying permissions, in Roles and responsibilities tables); committees defined on a group that decide by consensus,
with a chair who intervenes; a committee as a gate of a process graph, on a change order's panel, and as the
compile-frame committee of senior cogs. No model is called: cogs answer with their stand-ins or with fakes."""

import json
import shutil
from datetime import date, timedelta

import pytest

from conftest import ROOT, release_all, seen
from hubproto import authz
from hubproto.app import Hub
from hubproto.extract import ConventionExtractor
from hubproto.frames import load_frame
from hubproto.groups import committee, group_members, groups_of
from hubproto.runner import RunError

P = "design-review"
TODAY = date.today()

DESIGN = '''---
type: frame [0.2]
name: design-policy
description: Test-only policy for design reviews by a committee.
visibility: internal
version: "1.0.0"
scope: department
maintainer: tests
inherits: org-model
---
# Design policy (test fixture)

## Roles
- designer
- design-reviewer
- design-lead

## Members
- maya (person): designer
- kim (person): design-reviewer
- lee (person): designer
- sam (person): design-lead
- doc-reviewer (cog): design-reviewer

## Groups
- design-reviewers: kim and everyone holding design-reviewer
- leads: sam

## Folders
- Designs

## Permissions
Designers may create gizmos in Designs.
Designers may read Designs.
Designers may approve gizmos in Designs.
Design reviewers may read Designs.
Leads may approve gizmos in Designs.
The design-reviewers group may comment on anything in Designs.

## Committee: design-review
Group: design-reviewers
Chair: sam
Purpose: Reviews a design before it is released; every reviewer agrees, or it goes back.

## Approval criteria
The doc-reviewer cog may approve design-review gizmos when the risk is low.

## Routing
Overdue items in design-review go to the designer.

## Process: design-review
Records are filed in Designs.
The record type is task.
Statuses: Draft (to do), Review (in progress, due within 3 days), Released (done, completed), Cancelled (done, cancelled).
Starts in Draft ("Open the gizmo"): the designer; needs title.
From Draft to Review ("Submit"): the assignee.
From Review to Released ("Release"): the design-review committee decides.
From Review to Draft ("Return"): the design-review committee decides.
From any status except done to Cancelled ("Cancel"): the design lead.

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Designer | Opens a design and submits it for review. |
| the design-reviewers group | Reviews each design on the design-review committee, by consensus. |
| the leads group | Chairs the design-review committee and approves designs. |
| Design lead | Cancels a design that will not go ahead. |
'''


def design_hub(tmp_path, edits: dict | None = None, release: bool = True) -> Hub:
    (tmp_path / "frames" / "company").mkdir(parents=True)
    shutil.copy(ROOT / "frames" / "company" / "org-model.frame.md", tmp_path / "frames" / "company")
    text = DESIGN
    for old, new in (edits or {}).items():
        assert old in text, old
        text = text.replace(old, new)
    (tmp_path / "frames" / "design-policy.frame.md").write_text(text)
    h = Hub(tmp_path)
    h.kernel.seed()
    if release:
        p = h.compile()
        errors = [f for f in p["findings"] if f["severity"] == "error"]
        assert not errors, errors
        release_all(h)
    return h


def bundle(hub) -> dict:
    return hub.releases.bundle(hub.releases.current()["digest"])


def events(hub, run_id, type_=None):
    return [e for e in hub.runner.track(run_id) if type_ is None or e["type"] == type_]


def submit(hub, owner="maya", **fields) -> str:
    run_id = hub.runner.submit(P, {"record": {"title": "Bracket", **fields}}, owner=owner)
    state = hub.runner.load(run_id)
    assert state["at"] == "draft" and state["pending"]["assignees"] == [owner]
    hub.runner.decide(run_id, owner, None, transition="submit", meaning="ready for review", payload_digest=seen(hub.runner, run_id))
    return run_id


def vote(hub, run_id, who, verdict="approve", meaning="fit to release", **kw):
    return hub.runner.decide(run_id, who, verdict, meaning=meaning, payload_digest=seen(hub.runner, run_id), **kw)


# ---------------------------------------------------------------------------------------------- groups
def test_group_grammar_listed_and_by_role(tmp_path):
    p = tmp_path / "g.frame.md"
    p.write_text(DESIGN.replace("- leads: sam", "- leads: sam\n- crew: kim, lee and everyone holding designer or design-lead\n- broken: Somebody Else"))
    ex = ConventionExtractor().extract(load_frame(p))
    groups = {r["name"]: r for r in ex.rules if r["kind"] == "group"}
    assert groups["design-reviewers"]["members"] == ["kim"] and groups["design-reviewers"]["roles"] == ["design-reviewer"]
    assert groups["crew"]["members"] == ["kim", "lee"] and groups["crew"]["roles"] == ["designer", "design-lead"]
    [bad] = [n for n in ex.notes if n.get("severity") == "error"]
    assert "is not a member name" in bad["note"] and "Somebody Else" in bad["text"]
    [c] = [r for r in ex.rules if r["kind"] == "committee"]
    assert (c["group"], c["chair"]) == ("design-reviewers", "sam") and c["purpose"].startswith("Reviews a design")


def test_a_group_by_role_follows_role_changes_and_carries_permissions(tmp_path):
    hub = design_hub(tmp_path)
    b = bundle(hub)
    assert group_members(b, "design-reviewers") == ["kim", "doc-reviewer"], "listed first, then everyone holding the role"
    assert b["groups"]["design-reviewers"]["resolved"] == ["kim", "doc-reviewer"]
    assert groups_of(b, "doc-reviewer") == ["design-reviewers", "junior-cogs"]
    # The plural "Leads" names the leads group (no such role): the permission is the group's, and "Why?" cites the group.
    perm = next(r for r in b["permissions"] if r["source"]["text"] == "Leads may approve gizmos in Designs.")
    assert perm["subject"] == "group:leads"
    d = hub.why("sam", "approve", "gizmo", "Designs")
    assert d["allowed"] and "Leads may approve gizmos in Designs." in d["why"] and "(through the leads group)" in d["why"]
    assert not hub.why("kim", "approve", "gizmo", "Designs")["allowed"]
    # The explicit form, for a cog in the group through its role.
    d = authz.check(b, "doc-reviewer", "comment", "gizmo", "Designs")
    assert d["allowed"] and "through the design reviewers group" in d["why"]
    # What a cog may do lists what it gains through a group.
    mine = authz.permissions_of(b, "doc-reviewer")
    assert any(r["via"] == "group:design-reviewers" and r["action"] == "comment" for r in mine)
    assert authz.subjects_of(b, "doc-reviewer") >= {"role:design-reviewer", "group:design-reviewers", "cog:doc-reviewer"}
    from hubproto.cogs import RunContext, capabilities_text

    ctx = RunContext(run_id="t", release="", bundle=b, owner="doc-reviewer", kernel=hub.kernel)
    text = capabilities_text(ctx)
    assert "The design-reviewers group may comment on anything in Designs." in text and "through the design reviewers group" in text
    # A role change moves someone into the group by role: lee becomes a design reviewer.
    from conftest import edit_frame

    edit_frame(hub, "design-policy", "- lee (person): designer", "- lee (person): designer, design-reviewer", bump="1.1.0")
    release_all(hub)
    assert group_members(bundle(hub), "design-reviewers") == ["kim", "doc-reviewer", "lee"]
    assert authz.check(bundle(hub), "lee", "comment", "gizmo", "Designs")["allowed"]


def test_a_group_in_a_table_is_required_reading_and_a_cogs_context(tmp_path):
    from hubproto.scopes import required_reading

    hub = design_hub(tmp_path)
    b = bundle(hub)
    assert "design-policy" in [r["frame"] for r in required_reading(b, "kim")], "kim reads it as a member of the design-reviewers group"
    from hubproto.release import follows_for

    assert follows_for(b, "doc-reviewer")["design-policy"] == {"group:design-reviewers"}, "its context, through its group"
    assert follows_for(b, "sam")["design-policy"] == {"group:leads", "role:design-lead"}


def test_group_guards(tmp_path):
    hub = design_hub(tmp_path, {"- leads: sam": "- leads: sam and nobody-here\n- designer: maya", "Chair: sam": "Chair: doc-reviewer"},
                     release=False)
    errors = [f["detail"] for f in hub.compile()["findings"] if f["severity"] == "error"]
    assert any("group leads lists nobody-here, who is not a member of the organization" in e for e in errors), errors
    assert any("group designer: a role has the same name" in e for e in errors), errors
    assert any("committee design-review: its chair doc-reviewer is not a person" in e for e in errors), errors
    hub = design_hub(tmp_path / "b", {"Group: design-reviewers\n": "", "| the leads group |": "| the boards group |"}, release=False)
    found = hub.compile()["findings"]
    assert any("committee design-review: say the group it is defined on" in f["detail"] for f in found if f["severity"] == "error")
    assert any("names the boards group, which the organization model does not declare" in f["detail"] for f in found)


def test_a_group_change_is_a_widening(tmp_path):
    hub = design_hub(tmp_path)
    from conftest import edit_frame

    edit_frame(hub, "design-policy", "- leads: sam", "- leads: sam and lee", bump="1.1.0")
    p = hub.compile()
    [c] = [c for c in p["changes"] if c["rule"]["kind"] == "group" and c["op"] == "add"]
    assert c["widening"] and "group leads: sam, lee" in c["reason"] and c["changes_from"]["members"] == ["sam"]
    [gone] = [c for c in p["changes"] if c["rule"]["kind"] == "group" and c["op"] == "remove"]
    assert not gone["widening"], "superseded by the change above"


# ---------------------------------------------------------------------------------------------- a committee at a gate
LOW = {"risk": "low", "conditions": {"risk_low": True}}


def test_the_committee_gate_reads_and_writes_back(tmp_path):
    from hubproto import process_graph as pg
    from hubproto.release import render

    p = tmp_path / "d.frame.md"
    p.write_text(DESIGN)
    rules = ConventionExtractor().extract(load_frame(p)).rules
    proc = next(r for r in rules if r["kind"] == "process")
    t = {x["id"]: x for x in proc["transitions"]}
    assert t["release"]["gate"] == {"kind": "committee", "committee": "design-review"}
    assert t["return"]["gate"] == {"kind": "committee", "committee": "design-review"}
    wf = render(rules)["workflows"][P]
    lines = pg.to_sentences(wf)
    assert 'From Review to Released ("Release"): the design-review committee decides.' in lines
    assert [c["id"] for c in pg.gated_checkpoints(proc)] == ["submit", "release", "return"]


def test_every_member_agrees_and_the_item_moves(tmp_path):
    hub = design_hub(tmp_path)
    run_id = submit(hub, **LOW)
    state = hub.runner.load(run_id)
    cm = state["pending"]["committee"]
    assert state["at"] == "review" and cm["name"] == "design-review" and cm["chair"] == "sam"
    assert cm["members"] == ["kim", "doc-reviewer"] and state["pending"]["assignee_type"] == "committee"
    assert cm["due"] == (TODAY + timedelta(days=3)).isoformat()
    # The cog member took its turn at once: its criteria hold (low risk), so it agrees; kim's vote is still wanted.
    assert cm["votes"]["doc-reviewer"]["verdict"] == "agree" and "within its approval criteria" in cm["votes"]["doc-reviewer"]["meaning"]
    assert state["pending"]["assignees"] == ["kim"] and hub.runner.waits_for(state, "kim")
    assert hub.kernel.get(state["record"])["assignees"] == ["kim"]
    with pytest.raises(RunError, match="lee is not an assignee"):
        vote(hub, run_id, "lee")
    with pytest.raises(RunError, match="sam is not an assignee"):
        vote(hub, run_id, "sam")
    with pytest.raises(RunError, match="meaning"):
        vote(hub, run_id, "kim", meaning=" ")
    done = vote(hub, run_id, "kim", meaning="drawing and tolerances are right")
    assert done["status"] == "completed" and done["at"] == "released"
    d = events(hub, run_id, "gate.decided")[-1]
    assert d["by"] == "committee:design-review" and d["by_type"] == "committee" and d["verdict"] == "approve"
    assert d["voters"] == ["kim", "doc-reviewer"] and "agreed by consensus of the design-review committee" in d["meaning"]
    assert [v["member"] for v in events(hub, run_id, "gate.vote")] == ["doc-reviewer", "kim"]
    assert hub.runner.verify_signatures(run_id) == [], "each vote is a signature with its meaning"
    rec = hub.kernel.get(state["record"])
    assert rec["state"] == "released" and rec["decisions"][-1]["committee"] == "design-review"
    assert any(m["kind"] == "moved" and "committee agreed" in m["subject"] for m in hub.inbox.for_principal("maya"))


def test_one_objection_returns_the_item_and_there_is_no_standing_aside(tmp_path):
    hub = design_hub(tmp_path)
    run_id = submit(hub, **LOW)
    with pytest.raises(RunError, match="objection needs its reason"):
        vote(hub, run_id, "kim", verdict="reject", meaning="")
    with pytest.raises(RunError, match="approve .agree. or reject .object."):
        hub.runner.decide(run_id, "kim", "abstain", meaning="I stand aside", payload_digest=seen(hub.runner, run_id))
    back = vote(hub, run_id, "kim", verdict="reject", meaning="the hole pattern does not match the mating part")
    assert back["at"] == "draft" and back["status"] == "waiting" and back["pending"]["assignees"] == ["maya"]
    d = events(hub, run_id, "gate.decided")[-1]
    assert d["verdict"] == "reject" and d["transition"] == "return" and d["objection"]["member"] == "kim"
    assert "kim objects: the hole pattern does not match" in d["meaning"]
    # Submitted again, the committee decides afresh: every member votes again.
    hub.runner.decide(run_id, "maya", None, transition="submit", meaning="hole pattern fixed", payload_digest=seen(hub.runner, run_id))
    state = hub.runner.load(run_id)
    assert state["pending"]["committee"]["votes"].keys() == {"doc-reviewer"} and state["pending"]["assignees"] == ["kim"]
    assert vote(hub, run_id, "kim")["status"] == "completed"


def test_a_cog_member_outside_its_criteria_pauses_for_the_chair(tmp_path):
    hub = design_hub(tmp_path)
    run_id = submit(hub, risk="high")
    state = hub.runner.load(run_id)
    cm = state["pending"]["committee"]
    [p] = cm["paused"]
    assert p["member"] == "doc-reviewer" and p["kind"] == "criteria" and "the risk is high" in p["reason"]
    assert cm["interveners"] == ["sam"] and "doc-reviewer" not in cm["votes"]
    assert state["pending"]["assignees"] == ["kim", "sam"], "kim still votes; sam, the chair, intervenes"
    [note] = [m for m in hub.inbox.for_principal("sam") if m["kind"] == "intervention"]
    assert "doc-reviewer" in note["subject"] and "the risk is high" in note["subject"] and "Nothing passes by default" in note["body"]
    assert vote(hub, run_id, "kim")["status"] == "waiting", "kim agrees; nothing passes while a member is paused"
    with pytest.raises(RunError, match="only the chair"):
        hub.runner.intervene(run_id, "kim", "decide", member="doc-reviewer", verdict="approve", meaning="x", payload_digest=seen(hub.runner, run_id))
    with pytest.raises(RunError, match="meaning"):
        hub.runner.intervene(run_id, "sam", "decide", member="doc-reviewer", verdict="approve", meaning="", payload_digest=seen(hub.runner, run_id))
    with pytest.raises(RunError, match="as its chair you intervene"):
        vote(hub, run_id, "sam")
    done = hub.runner.intervene(run_id, "sam", "decide", member="doc-reviewer", verdict="approve",
                                meaning="the high risk is covered by the FMEA attached", payload_digest=seen(hub.runner, run_id))
    assert done["status"] == "completed"
    v = next(e for e in events(hub, run_id, "gate.vote") if e["member"] == "doc-reviewer")
    assert v["by"] == "sam" and v["intervention"]["by"] == "sam" and v["intervention"]["pause"]["kind"] == "criteria"
    assert "decided by sam in doc-reviewer's place: the high risk is covered" in v["meaning"]
    assert hub.runner.verify_signatures(run_id) == []


def test_the_chair_may_return_the_item_instead(tmp_path):
    hub = design_hub(tmp_path)
    run_id = submit(hub, risk="high")
    back = hub.runner.intervene(run_id, "sam", "return", meaning="needs a risk assessment first")
    assert back["at"] == "draft" and back["pending"]["assignees"] == ["maya"]
    d = events(hub, run_id, "gate.decided")[-1]
    assert d["verdict"] == "reject" and d["returned_by"] == "sam" and "sam, chair of the design-review committee" in d["meaning"]


def test_a_late_member_pauses_for_the_chair_who_may_extend_the_due_date(tmp_path):
    hub = design_hub(tmp_path)
    run_id = submit(hub, **LOW)
    later = (TODAY + timedelta(days=5)).isoformat()
    [out] = hub.runner.tick(today=later)
    assert out["paused"] == ["kim"] and out["to"] == ["sam"]
    state = hub.runner.load(run_id)
    [p] = state["pending"]["committee"]["paused"]
    assert p["member"] == "kim" and p["kind"] == "late" and "has not answered by" in p["reason"]
    assert set(state["pending"]["assignees"]) == {"kim", "sam"}, "kim may still answer; the chair is asked to intervene"
    with pytest.raises(RunError, match="new due date"):
        hub.runner.intervene(run_id, "sam", "extend", member="kim", due=TODAY.isoformat(), meaning="on leave")
    new_due = (TODAY + timedelta(days=10)).isoformat()
    state = hub.runner.intervene(run_id, "sam", "extend", member="kim", due=new_due, meaning="kim is on leave this week")
    assert state["pending"]["committee"]["paused"] == [] and state["pending"]["due"] == new_due and state["pending"]["assignees"] == ["kim"]
    assert hub.kernel.get(state["record"])["due"] == new_due
    assert any(e["action"] == "extend" and e["due"] == new_due for e in events(hub, run_id, "gate.intervened"))
    assert vote(hub, run_id, "kim")["status"] == "completed"


def test_a_late_member_answering_clears_the_pause(tmp_path):
    hub = design_hub(tmp_path)
    run_id = submit(hub, **LOW)
    hub.runner.tick(today=(TODAY + timedelta(days=5)).isoformat())
    assert vote(hub, run_id, "kim", meaning="sorry, late: fine")["status"] == "completed"


def test_when_the_chair_is_the_member_in_question_it_escalates_under_the_overdue_rule(tmp_path):
    hub = design_hub(tmp_path, {"Chair: sam": "Chair: kim"})
    run_id = submit(hub, **LOW)
    [out] = hub.runner.tick(today=(TODAY + timedelta(days=5)).isoformat())
    state = hub.runner.load(run_id)
    [p] = state["pending"]["committee"]["paused"]
    assert p["member"] == "kim" and "the chair, kim, is the member in question" in p["who"] and "Overdue items in design-review" in p["who"]
    assert p["interveners"] == ["lee"], "the overdue rule sends it to the designers (maya prepared it, so not her)"
    with pytest.raises(RunError, match="only the chair"):
        hub.runner.intervene(run_id, "maya", "return", meaning="x")
    done = hub.runner.intervene(run_id, "lee", "decide", member="kim", verdict="approve", meaning="kim is away; I checked it",
                                payload_digest=seen(hub.runner, run_id))
    assert done["status"] == "completed"


def test_when_the_chair_is_absent_from_the_item_it_escalates(tmp_path):
    """The chair prepared the item (segregation of duties keeps them out): the pause goes under the overdue rule."""
    hub = design_hub(tmp_path, {"Chair: sam": "Chair: lee"})
    run_id = submit(hub, owner="lee", risk="high")
    [p] = hub.runner.load(run_id)["pending"]["committee"]["paused"]
    assert p["member"] == "doc-reviewer" and "the chair, lee, may not decide this item" in p["who"] and p["interveners"] == ["maya"]


def test_nothing_passes_when_nobody_can_intervene(tmp_path):
    hub = design_hub(tmp_path, {"Chair: sam": "Chair: lee", "Overdue items in design-review go to the designer.\n": ""})
    run_id = submit(hub, owner="lee", risk="high")
    state = hub.runner.load(run_id)
    assert state["status"] == "failed"
    [f] = events(hub, run_id, "run.failed")
    assert f["kind"] == "organisation" and "nobody can intervene" in f["reason"]


def test_a_member_who_prepared_the_item_is_kept_out_and_the_chair_decides_in_their_place(tmp_path):
    hub = design_hub(tmp_path, {"- maya (person): designer": "- maya (person): designer, design-reviewer"})
    run_id = submit(hub, **LOW)
    state = hub.runner.load(run_id)
    cm = state["pending"]["committee"]
    assert "maya" in cm["members"] and cm["paused"][0]["member"] == "maya" and cm["paused"][0]["kind"] == "excluded"
    with pytest.raises(RunError, match="prepared this item"):
        vote(hub, run_id, "maya")
    vote(hub, run_id, "kim")
    done = hub.runner.intervene(run_id, "sam", "decide", member="maya", verdict="approve", meaning="maya designed it; I agree for her",
                                payload_digest=seen(hub.runner, run_id))
    assert done["status"] == "completed"


def test_an_item_changed_after_a_vote_is_voted_on_again(tmp_path):
    hub = design_hub(tmp_path)
    run_id = submit(hub, **LOW)
    rec = hub.runner.load(run_id)["record"]
    hub.kernel.set_fields(rec, {"description": "added a chamfer"}, by="maya")
    state = vote(hub, run_id, "kim", meaning="fine as it is now")
    assert state["status"] == "waiting" and "doc-reviewer" not in state["pending"]["committee"]["votes"]
    assert any(e["member"] == "doc-reviewer" for e in events(hub, run_id, "gate.vote_voided"))
    done = hub.runner.cog_users_work(run_id)  # the cog takes its turn again on the item as it is now
    assert hub.runner.load(run_id)["status"] == "completed", done


def test_a_committee_gate_needs_a_way_back_and_a_defined_committee(tmp_path):
    hub = design_hub(tmp_path, {'From Review to Draft ("Return"): the design-review committee decides.\n': "",
                                "From Review to Released (\"Release\"): the design-review committee decides.":
                                    "From Review to Released (\"Release\"): the design-review committee decides.\n"
                                    "From Draft to Released (\"Skip\"): the design board committee decides."}, release=False)
    errors = [f["detail"] for f in hub.compile()["findings"] if f["severity"] == "error"]
    assert any("'Release' is decided by the design-review committee, but an objection has no way back" in e for e in errors), errors
    assert any("'Skip' is decided by the design-board committee, which is not defined" in e for e in errors), errors
