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
    [bad] = [n for n in ex.notes if n.get("severity") == "unread"]
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
    [reading] = [r for r in required_reading(b, "kim") if r["frame"] == "design-policy"]
    assert reading["groups"] == ["design-reviewers"] and reading["roles"] == [], "kim reads it as a member of the design-reviewers group"
    assert reading["responsibilities"] == ["Reviews each design on the design-review committee, by consensus."]
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


def test_every_member_agrees_and_the_item_moves(tmp_path, today):
    hub = design_hub(tmp_path)
    run_id = submit(hub, **LOW)
    state = hub.runner.load(run_id)
    cm = state["pending"]["committee"]
    assert state["at"] == "review" and cm["name"] == "design-review" and cm["chair"] == "sam"
    assert cm["members"] == ["kim", "doc-reviewer"] and state["pending"]["assignee_type"] == "committee"
    assert cm["due"] == (today + timedelta(days=3)).isoformat()
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


def test_a_late_member_pauses_for_the_chair_who_may_extend_the_due_date(tmp_path, today):
    hub = design_hub(tmp_path)
    run_id = submit(hub, **LOW)
    later = (today + timedelta(days=5)).isoformat()
    [out] = hub.runner.tick(today=later)
    assert out["paused"] == ["kim"] and out["to"] == ["sam"]
    state = hub.runner.load(run_id)
    [p] = state["pending"]["committee"]["paused"]
    assert p["member"] == "kim" and p["kind"] == "late" and "has not answered by" in p["reason"]
    assert set(state["pending"]["assignees"]) == {"kim", "sam"}, "kim may still answer; the chair is asked to intervene"
    with pytest.raises(RunError, match="new due date"):
        hub.runner.intervene(run_id, "sam", "extend", member="kim", due=today.isoformat(), meaning="on leave")
    new_due = (today + timedelta(days=10)).isoformat()
    state = hub.runner.intervene(run_id, "sam", "extend", member="kim", due=new_due, meaning="kim is on leave this week")
    assert state["pending"]["committee"]["paused"] == [] and state["pending"]["due"] == new_due and state["pending"]["assignees"] == ["kim"]
    assert hub.kernel.get(state["record"])["due"] == new_due
    assert any(e["action"] == "extend" and e["due"] == new_due for e in events(hub, run_id, "gate.intervened"))
    assert vote(hub, run_id, "kim")["status"] == "completed"


def test_a_late_member_answering_clears_the_pause(tmp_path, today):
    hub = design_hub(tmp_path)
    run_id = submit(hub, **LOW)
    hub.runner.tick(today=(today + timedelta(days=5)).isoformat())
    assert vote(hub, run_id, "kim", meaning="sorry, late: fine")["status"] == "completed"


def test_when_the_chair_is_the_member_in_question_it_escalates_under_the_overdue_rule(tmp_path, today):
    hub = design_hub(tmp_path, {"Chair: sam": "Chair: kim"})
    run_id = submit(hub, **LOW)
    [out] = hub.runner.tick(today=(today + timedelta(days=5)).isoformat())
    state = hub.runner.load(run_id)
    [p] = state["pending"]["committee"]["paused"]
    assert p["member"] == "kim" and "the chair, kim, is the member in question" in p["who"] and "Overdue items in design-review" in p["who"]
    assert p["interveners"] == ["lee"], "the overdue rule sends it to the designers (maya prepared it, so not her)"
    with pytest.raises(RunError, match="only the chair"):
        hub.runner.intervene(run_id, "maya", "return", meaning="x")
    with pytest.raises(RunError, match="escalated to lee"):  # the chair does not decide in their own place
        hub.runner.intervene(run_id, "kim", "decide", member="kim", verdict="approve", meaning="fine", payload_digest=seen(hub.runner, run_id))
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


# ---------------------------------------------------------------------------------------------- a committee on a CO panel
@pytest.fixture
def board_hub(tmp_path):
    """The example company with a change board (dave and the it-admin cog, chaired by bob) that the change-control SOP
    names as the reviewers of changes to limits; the SOPs are documents (change orders only)."""
    from hubproto import sopdocs

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    org = tmp_path / "frames" / "company" / "org-model.frame.md"
    org.write_text(org.read_text().replace("- junior-cogs:", "- change-board: dave and it-admin\n- junior-cogs:")
                   .replace("## Folders", "## Committee: change-board\nGroup: change-board\nChair: bob\nPurpose: Reviews changes to limits.\n\n## Folders"))
    sop = tmp_path / "frames" / "iso9001" / "change-control-sop.frame.md"
    sop.write_text(sop.read_text().replace("A change to roles, limits or segregation of duties is reviewed by the management cog.",
                                           "A change to roles, limits or segregation of duties is reviewed by the change-board committee."))
    h = Hub(tmp_path)
    h.kernel.seed()
    p = h.compile()
    assert not [f for f in p["findings"] if f["severity"] == "error"], [f for f in p["findings"] if f["severity"] == "error"]
    release_all(h)
    sopdocs.convert(h, by="carol")
    for name in ("finance-policy", "document-control-sop", "change-control-sop"):
        h.kernel.set_fields(sopdocs.in_force(h.kernel, name)["locator"], {"owner": "alice"}, by="setup")
    return h


def _limit_co(hub):
    from hubproto import eco
    from test_eco import NEW, OLD, write

    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Lower the AP cog limit", "description": "why", "requested_by": "alice"},
                           by="alice")
    d = eco.add_sop(hub, co["number"], "finance-policy", by="alice")
    write(hub, hub.kernel.get(d["locator"]), OLD, NEW, "alice")
    return co


def test_every_member_of_a_committee_on_the_panel_reviews_and_it_must_agree(board_hub):
    from hubproto import changecontrol as cc

    hub = board_hub
    co = _limit_co(hub)
    cc.request_review(hub, co["number"], "alice")
    rnd = cc.current_round(hub.kernel.get(co["locator"]))
    panel = {e["who"]: e for e in rnd["panel"]}
    assert panel["dave"]["committee"] == panel["it-admin"]["committee"] == "change-board" and "limits" in panel["dave"]["reason"]
    assert rnd["results"]["it-admin"]["status"] == "done" and rnd["results"]["dave"]["status"] == "queued"
    assert any(m["kind"] == "review" for m in hub.inbox.for_principal("dave")), "the person member is asked in their inbox"
    [c] = cc.committee_reviews(hub, co["number"])
    assert c["status"] == "waiting" and c["waiting"] == ["dave"] and c["chair"] == "bob"
    assert "the change-board committee has not agreed yet: waiting for dave" in cc.send_reasons(hub, co["number"])
    # One objection (a blocking finding) and the committee does not agree: the change goes back for revision.
    cc.submit_review(hub, co["number"], "dave", findings=[{"severity": "blocking", "text": "1,500 is below the median bill"}])
    [c] = cc.committee_reviews(hub, co["number"])
    assert c["status"] == "objected" and c["objections"] == [{"member": "dave", "text": "1,500 is below the median bill"}]
    assert any("dave objects: 1,500 is below the median bill" in r for r in cc.send_reasons(hub, co["number"]))
    with pytest.raises(ValueError, match="change-board committee does not agree"):
        cc.send_for_approval(hub, co["number"], "alice")
    # The next round: every member reviews again; nobody objects, so the committee agrees and the CO may be sent.
    cc.request_review(hub, co["number"], "alice")
    cc.submit_review(hub, co["number"], "dave", findings=[{"severity": "advisory", "text": "keep an eye on volumes"}])
    [c] = cc.committee_reviews(hub, co["number"])
    assert c["status"] == "agreed" and cc.overview(hub, co["number"])["committees"][0]["status"] == "agreed"
    assert cc.send_reasons(hub, co["number"]) == []


def test_a_cog_member_that_cannot_review_pauses_the_panel_committee_for_its_chair(board_hub, monkeypatch):
    from hubproto import changecontrol as cc
    from hubproto.model import ModelUnavailable

    hub = board_hub
    real = hub.model.complete

    def complete(**kw):
        if kw.get("by") == "it-admin":
            raise ModelUnavailable("its model is down")
        return real(**kw)
    monkeypatch.setattr(hub.model, "complete", complete)
    co = _limit_co(hub)
    cc.request_review(hub, co["number"], "alice", panel=["dave", "the change-board committee"])
    cc.submit_review(hub, co["number"], "dave")
    [c] = cc.committee_reviews(hub, co["number"])
    assert c["status"] == "paused" and c["paused"][0]["member"] == "it-admin" and "its chair, bob, intervenes" in c["reason"]
    [note] = [m for m in hub.inbox.for_principal("bob") if m["kind"] == "intervention"]
    assert "it-admin could not review" in note["subject"]
    with pytest.raises(ValueError, match="only the chair"):
        cc.intervene_review(hub, co["number"], "change-board", "it-admin", "carol", "looked at it")
    cc.intervene_review(hub, co["number"], "change-board", "it-admin", "bob", "I reviewed the limit change myself: fine")
    rnd = cc.current_round(hub.kernel.get(co["locator"]))
    assert rnd["results"]["it-admin"]["intervention"]["by"] == "bob" and "in it-admin's place" in rnd["results"]["it-admin"]["summary"]
    assert cc.committee_reviews(hub, co["number"])[0]["status"] == "agreed"


# ---------------------------------------------------------------------------------------------- the compile-frame committee
def test_the_compile_frame_committee_is_the_senior_cogs_each_reading_with_its_own_model(monkeypatch, tmp_path):
    from pathlib import Path

    from hubproto.frames import load_frame_text

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")  # no call is made: each reader's call is a fake
    c = hub.compile_committee()
    assert c["members"] == [("senior-organization", "claude-opus-5-5"), ("senior-qa-manager", "claude-opus-5-5")]
    assert (c["group"], c["chair"], c["unavailable"]) == ("senior-cogs", "carol", {})
    ex = hub.extractor(calls=True)
    prose = "Any bill of more than 6,000 USD must be approved by the controller."
    f = hub.frames.get("finance-policy")
    frame = load_frame_text(f.text.rstrip("\n") + "\n\n## Notes\n" + prose + "\n", Path(f.path))
    line = next(i for i, ln in enumerate(frame.lines, start=frame.body_start) if ln.strip() == prose)
    route = {"kind": "routing", "fields": {"process": "purchase-to-pay", "step": "approve", "assignee": "role:controller", "mode": "assign",
                                           "condition": {"field": "amount", "op": ">", "value": 6000, "currency": "USD"}},
             "line": line, "quote": prose}
    asked = []

    def fake(name):
        def call(model, system, user):
            asked.append((name, model))
            assert f"You are the {name} cog" in system
            return json.dumps({"changes": [dict(route, op="add")]} if "is being revised" in user else {"rules": [route]})
        return call
    for (name, _m), reader in zip(ex.members, ex.readers):
        reader.call = fake(name)
        reader.budget = 100000
    out = ex.extract(frame)
    assert asked == [("senior-organization", "claude-opus-5-5"), ("senior-qa-manager", "claude-opus-5-5")]
    [r] = [r for r in out.rules if (r.get("source") or {}).get("read_by")]
    assert r["source"]["read_by"] == "the compile-frame committee (senior-organization, senior-qa-manager: 2 of 2 agree)"
    # One senior unavailable (its profile names a model this hub cannot reach): the committee cannot reach consensus;
    # a member-unavailable question naming the chair, never a skip.
    real = hub.releases.bundle

    def bundle(digest):
        b = json.loads(json.dumps(real(digest)))
        b["cogs"]["senior-qa-manager"]["model"] = "ollama/qwen3:14b"
        return b
    monkeypatch.setattr(hub.releases, "bundle", bundle)
    monkeypatch.delenv("HUBPROTO_ALLOW_STAND_IN", raising=False)
    c = hub.compile_committee()
    assert c["members"][1] == ("senior-qa-manager", "") and "needs an Ollama" in c["unavailable"]["senior-qa-manager"]
    ex = hub.extractor(calls=True)
    for (name, _m), reader in zip(ex.members, ex.readers):
        reader.call = fake(name)
    asked.clear()
    out = ex.extract(frame)
    [q] = [q for q in out.questions if q["code"] == "member-unavailable"]
    assert asked == [] and q["member"] == "senior-qa-manager" and q["chair"] == "carol"
    assert {x["code"] for x in out.questions} == {"member-unavailable", "unread-rule"}, "and the sentence nobody read is the chair's too"
    assert not [r for r in out.rules if (r.get("source") or {}).get("read_by")], "nothing read without consensus"


# ---------------------------------------------------------------------------------------------- no private memories
def test_a_cogs_judgment_is_a_review_record_on_the_item_and_read_back_from_there(tmp_path, monkeypatch, today):
    """Cogs have no private memories (change control 13): the cog's judgment for an approval is filed on the item, with
    the version it judged, and a failed one binds for that version because it is read back from the item."""
    from hubproto import review as review_mod

    hub = design_hub(tmp_path, {"The doc-reviewer cog may approve design-review gizmos when the risk is low.":
                                "The doc-reviewer cog may approve design-review gizmos when its review passes."})
    calls = []

    def failing(cog_id, profile, ctx, record, question="", judgment=False, lens="", lens_of=(1, 1)):
        calls.append(cog_id)
        return {"cog": cog_id, "verdict": "fail", "findings": [{"check": "content", "detail": "the tolerance stack is not shown",
                                                                "severity": "error"}],
                "summary": "fails", "routing_notes": [], "model": "fake", "sops": [], "stand_in": False, "date": today.isoformat()}
    monkeypatch.setattr(review_mod, "review", failing)
    run_id = submit(hub)
    state = hub.runner.load(run_id)
    [p] = state["pending"]["committee"]["paused"]
    assert p["member"] == "doc-reviewer" and "its review did not pass" in p["reason"] and calls == ["doc-reviewer"]
    docs = [d for d in hub.dms.documents_of(state["record"]) if d["fields"].get("doc_class") == "evidence"]
    [doc] = [d for d in docs if (d["fields"].get("judgment") or {}).get("cog") == "doc-reviewer"]
    j = doc["fields"]["judgment"]
    assert j["verdict"] == "fail" and j["key"].startswith(state["record"]) and "tolerance stack" in j["findings"][0]
    assert not list((tmp_path / "state").glob("judgments/doc-reviewer.json")), "no private store of the cog's own"
    # Asked again on the same version (the chair asks it again), it does not judge again: the record on the item binds.
    hub.runner.intervene(run_id, "sam", "extend", member="doc-reviewer", due=(today + timedelta(days=9)).isoformat(),
                         meaning="look again, please")
    [p] = hub.runner.load(run_id)["pending"]["committee"]["paused"]
    assert calls == ["doc-reviewer"] and "the tolerance stack is not shown" in p["reason"], "read back from the item, not judged again"
    # The item changes: the record no longer applies and it judges again.
    hub.kernel.set_fields(state["record"], {"description": "tolerance stack added"}, by="maya")
    hub.runner.intervene(run_id, "sam", "extend", member="doc-reviewer", due=(today + timedelta(days=10)).isoformat(),
                         meaning="changed: look again")
    assert calls == ["doc-reviewer", "doc-reviewer"]


# ---------------------------------------------------------------------------------------------- prose for direct changes
def _converted(tmp_path):
    from hubproto import sopdocs

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    sopdocs.convert(h, by="carol")
    return h


def _co_with(hub, *items):
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Direct changes", "description": "x", "requested_by": "bob"}, by="bob")
    hub.kernel.set_fields(co["locator"], {"direct_changes": list(items)}, by="bob")
    return hub.kernel.get(co["locator"])


def test_a_direct_change_that_gives_a_role_or_a_group_a_part_adds_its_table_rows(tmp_path):
    from hubproto import eco, sopdocs
    from hubproto.extract import responsibilities_of
    from hubproto.prose_drafting import direct_item, draft_direct_changes
    from hubproto.settings import stamp

    hub = _converted(tmp_path)
    route = stamp({"kind": "routing", "process": "purchase-to-pay", "step": "approve", "mode": "assign", "assignee": "role:internal-auditor",
                   "condition": {"field": "amount", "op": ">", "value": 50000.0, "currency": "USD"}}, "bob")
    perm = stamp({"kind": "permission", "subject": "group:senior-cogs", "action": "update", "object": "bill", "resource": "folder:Finance/Payables",
                  "effect": "allow"}, "bob")
    co = _co_with(hub, direct_item(route, by="bob"), direct_item(perm, by="bob"))
    assert draft_direct_changes(hub, co["number"], "bob") == [], "both read back"
    draft = next(c["draft"] for c in eco.carried(hub, hub.kernel.get(co["locator"])) if c["name"] == "finance-policy")
    text = sopdocs.frame_text(hub, draft)
    assert "The senior-cogs group may update bills in Finance/Payables." in text
    from hubproto.frames import load_frame_text
    from pathlib import Path

    rows, _ = responsibilities_of(load_frame_text(text, Path("finance-policy.frame.md")))
    whos = {r["who"]: r["text"] for r in rows}
    assert whos["role:internal-auditor"].startswith("A route: Approvals in purchase-to-pay over 50,000 USD go to the internal auditor")
    assert whos["group:senior-cogs"].startswith("'update' on bills: The senior-cogs group may update bills")
    assert "role:finance-clerk" in whos, "the rows already there stay"
    items = hub.kernel.get(co["locator"])["fields"]["direct_changes"]
    assert items[0]["stated"]["rows"] == [{"who": "role:internal-auditor", "what": "a route"}]
    assert items[1]["stated"]["rows"] == [{"who": "group:senior-cogs", "what": "'update' on bills"}]
    # Nothing to add the next round, and no warning that the table does not list them.
    n = len(hub.dms.versions(draft["locator"]))
    assert draft_direct_changes(hub, co["number"], "bob") == [] and len(hub.dms.versions(draft["locator"])) == n
    pv = eco.preview(hub, co["number"])
    assert not [f for f in pv["findings"] if "does not list it" in f["detail"] and "finance-policy" in f["detail"]]


def test_a_graph_process_is_drafted_in_the_graph_sentences(tmp_path):
    from hubproto import eco, sopdocs
    from hubproto.prose_drafting import direct_item, draft_direct_changes

    hub = design_hub(tmp_path)
    sopdocs.convert(hub, by="carol")
    proc = next(r for r in hub.settings_rules() if r["kind"] == "process" and r["name"] == P)
    new = json.loads(json.dumps({k: v for k, v in proc.items() if k != "source"}))
    next(s for s in new["statuses"] if s["id"] == "review")["sla_days"] = 5
    co = _co_with(hub, direct_item(new, by="carol", op="change", was=proc))
    assert draft_direct_changes(hub, co["number"], "carol") == [], "the graph sentences read back as the same graph"
    draft = next(c["draft"] for c in eco.carried(hub, hub.kernel.get(co["locator"])) if c["name"] == "design-policy")
    text = sopdocs.frame_text(hub, draft)
    assert "Statuses: Draft (to do), Review (in progress, due within 5 days), Released (done, completed), Cancelled (done, cancelled)." in text
    assert 'From Review to Released ("Release"): the design-review committee decides.' in text
    assert "1. [" not in text, "not numbered steps"
    [item] = hub.kernel.get(co["locator"])["fields"]["direct_changes"]
    assert item["round_trip"]["ok"] and item["stated"]["lines"][0].startswith("Records are filed in Designs.")


def test_groups_and_committees_made_in_settings_are_direct_changes_that_read_back(tmp_path):
    from hubproto import eco, sopdocs
    from hubproto.prose_drafting import direct_item, draft_direct_changes
    from hubproto.settings import stamp

    hub = _converted(tmp_path)
    group = stamp({"kind": "group", "name": "quality-board", "members": ["carol", "qa-manager"], "roles": ["quality-manager"]}, "bob")
    board = stamp({"kind": "committee", "name": "quality-board", "group": "quality-board", "chair": "carol", "purpose": "Reviews quality changes.",
                   "members": [], "quorum": 0}, "bob")
    co = _co_with(hub, direct_item(group, by="bob"), direct_item(board, by="bob"))
    assert draft_direct_changes(hub, co["number"], "bob") == []
    draft = next(c["draft"] for c in eco.carried(hub, hub.kernel.get(co["locator"])) if c["name"] == "org-model")
    text = sopdocs.frame_text(hub, draft)
    assert "- quality-board: carol, qa-manager and everyone holding quality-manager" in text
    # A committee goes with the other committee definitions (the cog profiles hold the compile-frame committee).
    profiles = next(c["draft"] for c in eco.carried(hub, hub.kernel.get(co["locator"])) if c["name"] == "cog-profiles")
    assert "## Committee: quality-board\nGroup: quality-board\nChair: carol\nPurpose: Reviews quality changes." in sopdocs.frame_text(hub, profiles)
    assert all(i["round_trip"]["ok"] for i in hub.kernel.get(co["locator"])["fields"]["direct_changes"])


def test_why_item_explains_a_committee_decision(tmp_path):
    hub = design_hub(tmp_path)
    run_id = submit(hub, risk="high")
    rec = hub.runner.load(run_id)["record"]
    assert hub.why_item("kim", rec)["may"] and hub.why_item("kim", rec)["as"] == "member"
    assert hub.why_item("sam", rec)["as"] == "intervener", "the chair, while it is paused"
    why = hub.why_item("lee", rec)
    assert not why["may"] and "not one of its members" in why["reasons"][0] and "chair sam" in why["reasons"][0]


def test_adversarial_lens_reviews_are_review_records_on_the_item_too(tmp_path, monkeypatch):
    """The qa-manager's lens reviews are filed on the document (one record per lens, and the adversarial reviews as one),
    and a failed lens binds for that version because it is read back from there; no file of the cog's own."""
    from hubproto import review as review_mod

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    calls, real = [], review_mod.review

    def fake(cog_id, profile, ctx, record, question="", judgment=False, lens="", lens_of=(1, 1)):
        calls.append(lens)
        r = real(cog_id, profile, ctx, record, question, judgment=judgment, lens=lens, lens_of=lens_of)
        return {**r, "verdict": "fail", "findings": [{"severity": "error", "check": lens, "detail": "a gap"}]} if lens == "consistency" else r
    monkeypatch.setattr(review_mod, "review", fake)
    run_id = hub.runner.submit("document-control", {"record": {"title": "SOP-LENS", "change_type": "minor",
                                                               "body": "# SOP-LENS\n\n1. Purpose\n2. Scope\n3. Steps"}}, owner="alice")
    rec = hub.runner.load(run_id)["record"]
    hub.send("bob", "qa-manager", "Approval", "@qa-manager please approve this document", record=rec)
    assert calls == ["compliance", "consistency"]
    lens = {d["fields"]["lens_review"]["lens"]: d["fields"]["lens_review"] for d in hub.dms.documents_of(rec) if d["fields"].get("lens_review")}
    assert set(lens) == {"compliance", "consistency"} and lens["consistency"]["passes"] is False and lens["compliance"]["passes"] is True
    assert any((d["fields"].get("adversarial") or {}).get("state") == "fail" for d in hub.dms.documents_of(rec))
    hub.send("bob", "qa-manager", "Approval", "@qa-manager please approve this document", record=rec)
    assert calls == ["compliance", "consistency"], "read back from the item: not reviewed again"
    assert not (tmp_path / "state" / "judgments").exists(), "no private store"


def test_a_group_listed_in_a_table_is_trained_and_has_its_section_in_the_training_matrix(tmp_path):
    from hubproto import training
    from hubproto.cogs import assign_training

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    b = bundle(hub)
    assert training.groups_to_train(b, {"fields": {"sop_name": "cog-profiles"}}) == ["senior-cogs"]
    assert training.groups_to_train(b, {"fields": {"sop_name": "cog-profiles", "training_roles": ["staff"]}}) == [], "an override wins"
    assert "## The senior cogs group (senior-organization, senior-qa-manager)" in training.matrix_text(hub, b)
    # People in a group the table lists get the read-and-acknowledge task.
    b = json.loads(json.dumps(b))
    b["groups"]["readers"] = {"members": ["dave", "alice"], "roles": []}
    b["responsibilities"].append({"frame": "cog-profiles", "who": "group:readers", "text": "Reads it."})
    asked = []

    class Runner:
        def submit(self, process, inputs, owner, parent_run=None):
            asked.append(inputs["record"]["person"])
            return f"r{len(asked)}"

        def load(self, rid):
            return {"record": "rec:training:X"}

    class Kernel:
        def link(self, *a, **k):
            pass
    assign_training(Runner(), b, Kernel(), {"locator": "rec:document:D", "fields": {"sop_name": "cog-profiles", "title": "Cogs", "revision": "B"}},
                    [], owner="carol", parent_run="", by="hub")
    assert {"dave", "alice"} <= set(asked) and "bob" in asked, "the group's people, and the policy owners the table lists"


# ---------------------------------------------------------------------------------------------- usability review (S6)
def test_a_panel_committee_member_is_told_their_review_is_a_vote_with_a_due_date(board_hub):
    from hubproto import changecontrol as cc

    hub = board_hub
    co = _limit_co(hub)
    cc.request_review(hub, co["number"], "alice")
    [note] = [m for m in hub.inbox.for_principal("dave") if m["kind"] == "review"]
    due = (date.today() + timedelta(days=cc.COMMITTEE_REVIEW_DAYS)).isoformat()
    assert "Your vote: the change-board committee decides by consensus; one reasoned objection sends it back." in note["body"]
    assert f"due {due}" in note["subject"] and "a review without one is your agreement" in note["body"]
    [c] = cc.committee_reviews(hub, co["number"])
    assert c["due"] == due and c["vote"].startswith("Your vote: the change-board committee decides by consensus")


def test_the_chair_acts_for_a_late_person_member_of_a_panel_committee(board_hub):
    from hubproto import changecontrol as cc

    hub = board_hub
    co = _limit_co(hub)
    cc.request_review(hub, co["number"], "alice")
    with pytest.raises(ValueError, match="has until"):
        cc.intervene_review(hub, co["number"], "change-board", "dave", "bob", "too slow")
    late = (date.today() + timedelta(days=cc.COMMITTEE_REVIEW_DAYS + 1)).isoformat()
    [c] = cc.committee_reviews(hub, co["number"], today=late)
    assert c["status"] == "paused" and c["paused"][0]["kind"] == "late" and c["interveners"] == ["bob"]
    assert "dave has not responded by" in c["reason"] and "its chair, bob, intervenes" in c["reason"]
    hub.runner.tick(today=late)  # the runner's tick tells whoever intervenes, once
    assert cc.tick_panels(hub, today=late) == [], "told once"
    assert any(m["kind"] == "intervention" and "dave has not responded" in m["subject"] for m in hub.inbox.for_principal("bob"))
    # Extend: dave is asked again with the new date.
    later = (date.today() + timedelta(days=20)).isoformat()
    with pytest.raises(ValueError, match="meaning"):
        cc.intervene_review(hub, co["number"], "change-board", "dave", "bob", " ", action="extend", due=later, today=late)
    cc.intervene_review(hub, co["number"], "change-board", "dave", "bob", "dave is on leave", action="extend", due=later, today=late)
    assert cc.committee_reviews(hub, co["number"], today=late)[0]["status"] == "waiting"
    assert any("now due " + later in m["subject"] for m in hub.inbox.for_principal("dave"))
    # Late again: bob decides in dave's place (an objection with its reason sends it back).
    after = (date.today() + timedelta(days=21)).isoformat()
    res = cc.intervene_review(hub, co["number"], "change-board", "dave", "bob", "the limit needs the controller's view", verdict="object",
                              today=after)
    assert res["intervention"]["by"] == "bob" and res["findings"][0]["severity"] == "blocking"
    [c] = cc.committee_reviews(hub, co["number"], today=after)
    assert c["status"] == "objected" and "objects in dave's place" in c["objections"][0]["text"]


def test_a_chair_who_contributed_to_the_change_order_escalates(board_hub):
    from hubproto import changecontrol as cc
    from hubproto import eco
    from test_eco import write

    hub = board_hub
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Lower the AP cog limit", "description": "why", "requested_by": "bob"}, by="bob")
    d = eco.add_sop(hub, co["number"], "finance-policy", by="bob")
    from test_eco import NEW, OLD

    write(hub, hub.kernel.get(d["locator"]), OLD, NEW, "bob")
    cc.request_review(hub, co["number"], "bob")
    late = (date.today() + timedelta(days=cc.COMMITTEE_REVIEW_DAYS + 1)).isoformat()
    [c] = cc.committee_reviews(hub, co["number"], today=late)
    [p] = c["paused"]
    assert "the chair, bob, contributed to" in p["who"] and "bob" not in p["interveners"]
    with pytest.raises(ValueError, match="only the chair"):
        cc.intervene_review(hub, co["number"], "change-board", "dave", "bob", "fine by me", today=late)


def test_at_a_gate_the_chair_acts_for_a_late_person_once_the_due_date_has_passed(tmp_path, monkeypatch):
    import hubproto.runner as runner_mod

    hub = design_hub(tmp_path)
    run_id = submit(hub, **LOW)
    note = next(m for m in hub.inbox.for_principal("kim") if m["kind"] == "approval" and "committee" in m["subject"])
    assert note["body"].startswith("Your vote: the design-review committee decides by consensus; one reasoned objection sends it back.")

    class Later(date):
        @classmethod
        def today(cls):
            return date.today() + timedelta(days=5)
    monkeypatch.setattr(runner_mod, "date", Later)
    # No tick has run: kim is late all the same, and sam decides in her place.
    done = hub.runner.intervene(run_id, "sam", "decide", member="kim", verdict="approve", meaning="kim is away; I checked the drawing",
                                payload_digest=seen(hub.runner, run_id))
    assert done["status"] == "completed"
    v = next(e for e in events(hub, run_id, "gate.vote") if e["member"] == "kim")
    assert v["by"] == "sam" and v["intervention"]["pause"]["kind"] == "late"
