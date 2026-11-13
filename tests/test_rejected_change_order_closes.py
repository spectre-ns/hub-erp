"""A rejected change order closes (issue #65): it is not revised again, its drafts are withdrawn so the SOP can be
revised on a new change order, and its page says it is closed. One that a rejection sent back to revising before this
(CO-2026-0007, CO-2026-0001 on the live hub) closes too, unless someone has requested a review since."""

from datetime import datetime, timedelta

from hubproto import changecontrol as cc
from test_change_control import gate, hub, new_co, revise  # noqa: F401  (the converted hub fixture)
from test_change_order_pages import carry_revision, co_page, shell  # noqa: F401  (the served, converted hub)
from test_change_order_pages import new_co as new_co_page
from test_eco import NEW, OLD
from test_shell_views import get, plain


def rejected_the_old_way(hub, co, draft):
    """What the hub did before issue #65: the rejection reopened it in revising, its draft left open."""
    run = hub.runner.run_for_record(co["locator"])
    dec = next(d for d in reversed(hub.kernel.get(co["locator"]).get("decisions") or []) if d.get("verdict") == "reject")
    if hub.kernel.get(draft["locator"])["state"] == "withdrawn":
        hub.kernel.transition(draft["locator"], "draft", by="test", reason="as the old hub left it")
    hub.kernel.transition(co["locator"], "open", by="hub", reason="its approval was rejected: back to revising")
    hub.kernel.set_fields(co["locator"], {"stage": "revising", "frozen": None,
                                          "rejected": {"by": dec["by"], "meaning": dec["meaning"], "at": dec["at"]}}, by="hub")
    assert run["status"] == "rejected"
    return hub.kernel.get(co["locator"])


def sent_and_rejected(hub, title):
    co = new_co(hub, title=title)
    draft = revise(hub, co, "finance-policy", OLD, NEW)
    cc.request_review(hub, co["number"], "alice")
    cc.send_for_approval(hub, co["number"], "alice")
    gate(hub, co, verdict="reject", meaning="Declined")
    return co, draft


def test_a_change_order_an_earlier_rejection_reopened_closes_and_frees_its_sop(hub):
    co, draft = sent_and_rejected(hub, "rejected before the fix")
    co = rejected_the_old_way(hub, co, draft)
    assert co["state"] == "open" and hub.kernel.get(draft["locator"])["state"] == "draft"
    co = cc.sync(hub, co["number"])
    assert co["state"] == "rejected" and cc.stage_of(co) == "rejected" and co["fields"]["rejected"]["closed"] is True
    assert hub.kernel.get(draft["locator"])["state"] == "withdrawn"


def test_a_new_revision_of_the_sop_closes_an_old_rejected_change_order_nobody_looked_at(hub):
    co, draft = sent_and_rejected(hub, "rejected, never opened again")
    rejected_the_old_way(hub, co, draft)
    again = new_co(hub, title="the next try")
    assert revise(hub, again, "finance-policy", OLD, NEW)["state"] == "draft", "not 'already being revised'"
    assert hub.kernel.get(co["locator"])["state"] == "rejected" and hub.kernel.get(draft["locator"])["state"] == "withdrawn"


def test_a_change_order_someone_reworks_after_an_earlier_rejection_stays_open(hub):
    co, draft = sent_and_rejected(hub, "rejected, then reworked")
    co = rejected_the_old_way(hub, co, draft)
    later = (datetime.fromisoformat(co["fields"]["rejected"]["at"]) + timedelta(minutes=5)).isoformat(timespec="seconds")
    rounds = list(co["fields"].get("rounds") or []) + [{"n": 9, "requested_by": "alice", "at": later, "status": "reviewed"}]
    hub.kernel.set_fields(co["locator"], {"rounds": rounds}, by="test")  # a review round requested after the rejection
    co = cc.sync(hub, co["number"])
    assert co["state"] == "open" and cc.stage_of(co) == "revising", "its requester is reworking it: left alone"
    assert hub.kernel.get(draft["locator"])["state"] == "draft"


def test_a_rejected_change_order_page_says_it_is_closed(shell):
    hub = shell["hub"]
    co = new_co_page(shell, title="Declined on the page")
    draft = carry_revision(shell, co)
    cc.request_review(hub, co["number"], "alice")
    cc.send_for_approval(hub, co["number"], "alice")
    gate(hub, co, verdict="reject", meaning="Declined: the limit stays.")
    page = plain(co_page(shell, hub.kernel.get(co["locator"]), "alice"))
    assert "Rejected." in page and "carol rejected its approval: “Declined: the limit stays.” It is closed" in page
    assert "It is closed: nothing more is done with it" in page and f"revision {draft['number']} is withdrawn" in page
    assert "Rejected: closed without a release." in page and "back to revising" not in page
    status, listing = get(shell, "/change-orders", **{"as": "alice"})
    assert status == 200
