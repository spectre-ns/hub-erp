"""The workflow engine's guards on change orders and dates: every gate kind honours the change order's approval
readiness; a change order's approval is always a person's; a chair who contributed to the item does not intervene; and
no test, or due date, depends on which side of midnight it runs."""

import shutil
from datetime import date, timedelta

import pytest

from conftest import ROOT, release_all, seen
from hubproto import changecontrol as cc
from hubproto import sopdocs
from hubproto.app import Hub
from hubproto.runner import RunError
from test_change_control import decide_all, items, new_co, revise
from test_eco import NEW, OLD


def converted_hub(tmp_path, edits: dict[str, list[tuple[str, str]]] | None = None) -> Hub:
    """The repository's Frames (edited first), released and converted to documents, as test_change_control's hub."""
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    for rel, pairs in (edits or {}).items():
        path = tmp_path / "frames" / rel
        text = path.read_text()
        for old, new in pairs:
            assert old in text, old
            text = text.replace(old, new)
        path.write_text(text)
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    sopdocs.convert(h, by="carol")
    for name in ("finance-policy", "document-control-sop", "change-control-sop"):
        h.kernel.set_fields(sopdocs.in_force(h.kernel, name)["locator"], {"owner": "alice"}, by="setup")
    return h


# The change-order process as a graph, approved by a change board deciding by consensus (correctness H2).
BOARD = {
    "company/org-model.frame.md": [
        ("- junior-cogs:", "- change-board: carol and bob\n- junior-cogs:"),
        ("## Folders", "## Committee: change-board\nGroup: change-board\nChair: carol\nPurpose: Approves change orders.\n\n## Folders")],
    "iso9001/change-control-sop.frame.md": [
        ("1. [staff] Prepare the change order (record-filer)\n2. [system] Check the change order is ready (frame-stager)\n"
         "3. [quality-manager] Approve the change order",
         "Statuses: Open (to do), Awaiting approval [awaiting-approve-the-change-order] (in progress), Approved (done, completed), "
         "Rejected (done, rejected), Cancelled (done, cancelled).\n"
         'Starts in Open ("Prepare the change order" [prepare]): the staff.\n'
         'From Open to Awaiting approval ("Check the change order is ready" [check]): automatically, after the record filer and the frame stager.\n'
         'From Awaiting approval to Approved ("Approve the change order" [approve-the-change-order]): the change-board committee decides.\n'
         'From Awaiting approval to Rejected ("Reject"): the change-board committee decides.\n'
         'From any status except done to Cancelled ("Cancel"): the owner or an approver.'),
        ("| Quality manager | Approves", "| the change-board group | Approves change orders by consensus. |\n| Quality manager | Approves")],
}


def board_co(hub, author="alice"):
    co = new_co(hub)
    revise(hub, co, "finance-policy", OLD, NEW, by=author)
    cc.request_review(hub, co["number"], "alice")  # the suggested panel (leaving it out would be a warning to accept)
    cc.send_for_approval(hub, co["number"], "alice")
    return hub.kernel.get(co["locator"]), hub.runner.run_for_record(co["locator"])["id"]


def test_a_committee_gate_on_a_change_order_honours_its_approval_readiness(tmp_path):
    hub = converted_hub(tmp_path, BOARD)
    co, run_id = board_co(hub)
    state = hub.runner.load(run_id)
    assert state["pending"]["committee"]["members"] == ["carol", "bob"]
    assert cc.approval_reasons(hub, co["number"]), "its items are not decided yet"
    for who in ("carol", "bob"):
        with pytest.raises(RunError, match="the change order cannot be approved yet: not decided yet"):
            hub.runner.decide(run_id, who, "approve", meaning="fine", payload_digest=seen(hub.runner, run_id))
    assert hub.kernel.get(co["locator"])["state"] != "approved" and not hub.runner.load(run_id)["pending"]["committee"]["votes"]
    # Once every item is decided the board agrees, and the change order is approved by consensus.
    decide_all(hub, co, who=("bob",))
    for who in ("carol", "bob"):
        hub.runner.decide(run_id, who, "approve", meaning="agreed", payload_digest=seen(hub.runner, run_id))
    assert hub.kernel.get(co["locator"])["state"] in ("approved", "released")


def test_a_committee_that_agrees_on_a_change_order_that_became_unready_pauses_for_its_chair(tmp_path, monkeypatch):
    hub = converted_hub(tmp_path, BOARD)
    co, run_id = board_co(hub)
    decide_all(hub, co, who=("bob",))
    hub.runner.decide(run_id, "carol", "approve", meaning="agreed", payload_digest=seen(hub.runner, run_id))
    # Between the votes something makes it unready (the backstop at the conclusion, not only at each vote).
    calls = iter([[], ["a warning is not accepted yet"]])
    monkeypatch.setattr(type(hub.runner), "change_order_blocks", lambda self, state: next(calls, ["a warning is not accepted yet"]))
    state = hub.runner.decide(run_id, "bob", "approve", meaning="agreed", payload_digest=seen(hub.runner, run_id))
    cm = state["pending"]["committee"]
    assert state["status"] == "waiting" and [p["kind"] for p in cm["paused"]] == ["not-ready"]
    assert hub.kernel.get(co["locator"])["state"] != "approved"


COG_SIGNS = {"iso9001/change-control-sop.frame.md": [
    ("Approvals in change-order go to the quality manager.",
     "Approvals in change-order go to the qa-manager cog.\nApprovals in change-order go to the quality manager."),
    ("Quality managers may approve change-orders in Quality/Changes.\n",
     "Quality managers may approve change-orders in Quality/Changes.\nThe qa-manager cog may approve change-orders in Quality/Changes.\n"),
    ("## Approval criteria\n", "## Approval criteria\nThe qa-manager cog may approve change-order change-orders.\n")]}


def test_the_compiler_refuses_a_policy_that_lets_a_cog_sign_a_change_order(tmp_path):
    from hubproto.compile import CompileError

    with pytest.raises(CompileError, match="a person signs a change order"):
        converted_hub(tmp_path, COG_SIGNS)


def test_a_change_order_is_approved_by_a_person_never_by_a_cog(tmp_path, monkeypatch):
    # Governance H4: the policy routes change-order approvals to a cog and gives it criteria and the permission (the
    # compile refuses that; with the compile's guard switched off, the runner still refuses a cog's signature).
    from hubproto import guards

    monkeypatch.setattr(guards, "change_order_signer_guard", lambda rules: [])
    monkeypatch.setattr(cc, "cog_sign_problems", lambda rules: [])  # the change order's own readiness check, too
    hub = converted_hub(tmp_path, {"iso9001/change-control-sop.frame.md": [
        ("Approvals in change-order go to the quality manager.",
         "Approvals in change-order go to the qa-manager cog.\nApprovals in change-order go to the quality manager."),
        ("Quality managers may approve change-orders in Quality/Changes.\n",
         "Quality managers may approve change-orders in Quality/Changes.\nThe qa-manager cog may approve change-orders in Quality/Changes.\n"),
        ("## Approval criteria\n", "## Approval criteria\nThe qa-manager cog may approve change-order change-orders.\n")]})
    assert any(c["approver"] == "cog:qa-manager" and c["process"] == "change-order" for c in hub.current_bundle()["approval_criteria"])
    co = new_co(hub)
    revise(hub, co, "finance-policy", OLD, NEW)
    cc.request_review(hub, co["number"], "alice", panel=[])
    decide_all(hub, co, who=("bob",))
    cc.send_for_approval(hub, co["number"], "alice")
    run_id = hub.runner.run_for_record(co["locator"])["id"]
    state = hub.runner.load(run_id)
    declined = [e for e in hub.runner.track(run_id) if e["type"] == "gate.declined" and e.get("by") == "qa-manager"]
    assert declined and "a person signs it" in declined[0]["reasons"][0]
    assert state["status"] == "waiting" and state["pending"]["assignee_type"] == "person" and "qa-manager" not in state["pending"]["assignees"]
    assert not [d for d in hub.kernel.get(co["locator"])["decisions"] if d.get("by_type") == "cog" and d.get("verdict") == "approve"]
    assert "a person signs it" in hub.runner.may_step_in(state, "qa-manager")
    with pytest.raises(RunError, match="not an assignee|a person signs it"):
        hub.runner.decide(run_id, "qa-manager", "approve", meaning="x", payload_digest=seen(hub.runner, run_id), delegating_rule={"text": "x"})


def test_a_chair_who_contributed_to_the_change_order_does_not_intervene(tmp_path):
    # Governance H5: carol chairs the board and wrote the revision the change order carries.
    hub = converted_hub(tmp_path, BOARD)
    co, run_id = board_co(hub, author="carol")
    assert "carol" in cc.contributors(hub, co["number"])
    state = hub.runner.load(run_id)
    cm = state["pending"]["committee"]
    pause = next(p for p in cm["paused"] if p.get("member") == "carol")
    assert pause["kind"] == "excluded" and "contributor" in pause["reason"]
    assert "carol" not in cm["interveners"] and "escalated under the overdue rule" in pause["who"]
    with pytest.raises(RunError, match="only the chair|may not decide this item"):
        hub.runner.intervene(run_id, "carol", "decide", member="carol", verdict="approve", meaning="I wrote it, it is fine",
                             payload_digest=seen(hub.runner, run_id))


# --------------------------------------------------------------------------------------------- midnight
def test_due_dates_and_escalation_follow_the_clock_across_midnight(tmp_path, monkeypatch):
    """Correctness L1: the runner reads the day when it routes; a test reads the same clock. An item routed just before
    midnight is due three days after that day, the next Gate (after midnight) five days after the next day, and
    escalation counts from those dates."""
    import hubproto.runner as runner_module
    from test_workflow import P, build_hub

    clock = {"day": date(2026, 11, 9)}

    class Day(date):
        @classmethod
        def today(cls):
            return clock["day"]
    monkeypatch.setattr(runner_module, "date", Day)
    h = build_hub(tmp_path)
    release_all(h)
    run_id = h.runner.submit(P, {"record": {"title": "w"}}, owner="erin")  # 23:59 on 9 November
    assert h.runner.load(run_id)["pending"]["due"] == "2026-11-12"
    clock["day"] = date(2026, 11, 10)  # midnight passes while the item waits
    assert h.runner.load(run_id)["pending"]["due"] == "2026-11-12", "a waiting Gate keeps its due date"
    assert h.runner.tick(today="2026-11-12") == [], "due that day, not yet overdue"
    state = h.runner.decide(run_id, "erin", "approve", meaning="ok", payload_digest=seen(h.runner, run_id))
    assert state["pending"]["due"] == (date(2026, 11, 10) + timedelta(days=5)).isoformat()
    assert [e["run"] for e in h.runner.tick(today="2026-11-16")] == [run_id]
