"""Whoever made a change order may reject it at its approval Gate (issue #63): a rejection approves nothing and puts
nothing in force, so segregation of duties keeps a maker from approving their own change, never from rejecting it.
CO-2026-0007: carol prepared it, the Gate waited for carol (the quality manager), and she could neither approve nor
reject it."""

import pytest

from hubproto import changecontrol as cc
from hubproto import eco
from hubproto.runner import RunError
from test_change_control import gate, hub, new_co  # noqa: F401  (the converted hub fixture)
from test_eco import NEW, OLD, write


def carols_change_order(hub, title):
    """Carol prepares it and makes its one change herself, in Settings (CO-2026-0007)."""
    co = new_co(hub, by="carol", title=title)
    read = next(r for r in hub.settings_rules() if r["kind"] == "permission" and (r.get("source") or {}).get("text") == "Controllers may read Finance.")
    cc.add_direct_change(hub, co["number"], {"op": "remove", "rule": read}, "carol")
    return co


def waiting_for_carol(hub, co):
    cc.request_review(hub, co["number"], co["fields"]["requested_by"], panel=[])
    state = cc.send_for_approval(hub, co["number"], co["fields"]["requested_by"])
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["carol"], "the quality manager's Gate"
    return state


def test_the_quality_manager_rejects_a_change_order_she_prepared_and_cannot_approve_it(hub):
    co = carols_change_order(hub, "carol's own change")
    assert "carol" in cc.contributors(hub, co["number"])
    state = waiting_for_carol(hub, co)
    assert "contributor" in hub.runner.excluded(hub.current_bundle(), state)["carol"], "she still never approves it alone"
    with pytest.raises(RunError):
        gate(hub, co, who="carol", verdict="approve")
    run = gate(hub, co, who="carol", verdict="reject", meaning="Rejected: controllers keep reading Finance.")
    assert run["status"] != "waiting"
    co = cc.sync(hub, co["number"])
    assert cc.stage_of(co) == "revising" and co["fields"]["rejected"]["by"] == "carol"
    assert co["fields"]["rejected"]["meaning"] == "Rejected: controllers keep reading Finance."


def test_only_a_person_who_made_the_change_order_is_let_reject_it(hub):
    co = carols_change_order(hub, "who may reject")
    draft = eco.add_sop(hub, co["number"], "finance-policy", by="carol", author="alice")  # alice writes a revision on it
    write(hub, hub.kernel.get(draft["locator"]), OLD, NEW, "alice")
    assert "alice" in cc.revision_authors(hub, co["number"])
    state = waiting_for_carol(hub, co)
    runner = hub.runner
    assert runner.maker_may_reject(state, "carol", "person", "reject"), "the preparer"
    assert runner.maker_may_reject(state, "alice", "person", "reject"), "the revision's author"
    assert not runner.maker_may_reject(state, "carol", "person", "approve"), "approving keeps every rule"
    assert not runner.maker_may_reject(state, "dave", "person", "reject"), "someone who made nothing gains nothing"
    assert not runner.maker_may_reject(state, "qa-manager", "cog", "reject"), "a cog never decides a change order"
