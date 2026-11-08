"""Cogs are users: a Gate pends for a cog like for a person, the cog decides through ``decide`` with a
signature, a seen digest and a meaning, and declines back to the next candidate on the route."""

import pytest

from conftest import SMALL_BILL, dated, seen
from hubproto.runner import RunError


def types_of(hub, run_id):
    return [e["type"] for e in hub.runner.track(run_id)]


def test_a_cog_is_assigned_notified_and_signs_like_a_person(released):
    run_id = released.runner.submit("purchase-to-pay", {"record": dated(SMALL_BILL)}, owner="alice")
    state = released.runner.load(run_id)
    assert state["status"] == "completed"
    events = released.runner.track(run_id)
    pend = next(e for e in events if e["type"] == "gate.pending")
    assert pend["assignees"] == ["ap-reviewer"] and pend["assignee_type"] == "cog"
    review = next(e for e in events if e["type"] == "gate.cog_review")
    decided = next(e for e in events if e["type"] == "gate.decided")
    assert review["verdict"] == "approve" and decided["by"] == "ap-reviewer" and decided["by_type"] == "cog"
    assert decided["signature"] and decided["delegating_rule"]["text"] and "within fixed criteria" in decided["meaning"]
    assert decided["payload_digest"] == pend["payload_digest"], "the cog signed what the card showed"
    # The cog's inbox saw the approval request; the owner was told a cog decided.
    assert any(m["kind"] == "approval" for m in released.inbox.for_principal("ap-reviewer"))
    assert any(m["kind"] == "auto-decided" and "ap-reviewer" in m["subject"] for m in released.inbox.for_principal("alice"))
    rec = released.kernel.get(state["record"])
    assert rec["decisions"][-1]["by"] == "ap-reviewer" and rec["state"] == "posted"


def test_a_cog_declines_outside_its_criteria_and_the_item_goes_to_a_person(released):
    # Under the cog's routing limit, but outside the match tolerance: the cog is assigned, reviews, and declines.
    run_id = released.runner.submit("purchase-to-pay", {"record": dated({**SMALL_BILL, "amount": 1400})}, owner="alice")
    state = released.runner.load(run_id)
    assert state["status"] == "waiting" and state["pending"]["assignee_type"] == "person"
    first = next(e for e in released.runner.track(run_id) if e["type"] == "gate.pending")
    assert first["assignees"] == ["ap-reviewer"], "the cog was assigned first, as a user"
    types = types_of(released, run_id)
    assert "gate.declined" in types and types.index("gate.declined") < types.index("gate.pending", types.index("gate.declined"))
    review = next(e for e in released.runner.track(run_id) if e["type"] == "gate.cog_review")
    assert review["verdict"] == "escalate" and review["reasons"]
    assert "ap-reviewer" not in state["pending"]["assignees"]


def test_service_mode_leaves_the_item_waiting_for_the_cog_until_it_works(released):
    released.runner.cog_users_inline = False
    try:
        run_id = released.runner.submit("purchase-to-pay", {"record": dated(SMALL_BILL)}, owner="alice")
        state = released.runner.load(run_id)
        assert state["status"] == "waiting" and state["pending"]["assignees"] == ["ap-reviewer"]
        assert released.kernel.get(state["record"])["assignees"] == ["ap-reviewer"]
        assert [s["id"] for s in released.runner.pending_for("ap-reviewer")] == [run_id]
        # A person cannot take the cog's turn; the cog cannot be impersonated with fields.
        with pytest.raises(RunError, match="not an assignee"):
            released.runner.decide(run_id, "dave", "approve", meaning="x", payload_digest=seen(released.runner, run_id))
        with pytest.raises(RunError, match="supplies no fields"):
            released.runner.decide(run_id, "ap-reviewer", "approve", meaning="x", fields={"amount": 1}, payload_digest=seen(released.runner, run_id))
        touched = released.runner.cog_users_work()
        assert [t["id"] for t in touched] == [run_id] and released.runner.load(run_id)["status"] == "completed"
    finally:
        released.runner.cog_users_inline = True


def test_a_cog_may_not_take_an_item_when_the_next_state_needs_a_person(released):
    # The purchasing cog's criteria hold, but the next state needs a human-supplied field: it declines.
    k = released.kernel
    from conftest import make_part

    item = make_part(k, "W-1", title="washer", unit_cost=0.1)
    run_id = released.runner.submit("purchase-request", {"record": {"title": "washers", "part": item["locator"], "quantity": 10}}, owner="alice")
    state = released.runner.load(run_id)
    decided = [e for e in released.runner.track(run_id) if e["type"] == "gate.decided"]
    assert decided and decided[0]["by"] == "purchasing-assistant" and decided[0]["by_type"] == "cog"
    assert k.get(state["record"])["state"] == "ordered"


def test_a_cog_cannot_decide_an_item_it_prepared(released):
    """Segregation of duties applies to cogs: the quality-assistant files an NCR and may not disposition it."""
    bundle = released.releases.bundle(released.releases.current()["digest"])
    assert bundle["members"]["quality-assistant"]["type"] == "cog"
    run_id = released.runner.submit("nonconformance", {"record": {"title": "cog-filed", "description": "low risk scratch", "severity": "low",
                                                                  "source": "inspection"}}, owner="quality-assistant")
    state = released.runner.load(run_id)
    skipped = [e for e in released.runner.track(run_id) if e["type"] == "gate.skipped" and e["candidate"] == "cog:quality-assistant"]
    assert skipped and "segregation" in skipped[0]["reason"]
    assert state["status"] == "waiting" and "quality-assistant" not in state["pending"]["assignees"]
