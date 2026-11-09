"""What widens authority (two policy owners) and what narrows it: a lower cog limit, a route that sends fewer items to a
cog, or more items to a person, is not a widening; a higher one is. And "or the same in <currency>" in limit sentences."""

from conftest import edit_frame
from hubproto.extract import ConventionExtractor
from hubproto.frames import load_frame_text
from hubproto.routing import condition_holds, evaluate_criteria
from hubproto.settings import sentence

CRIT = "The ap-reviewer cog may approve purchase-to-pay bills under 2,000 USD when the three-way match is within tolerance and the supplier is approved."
COG_ROUTE = "Approvals in purchase-to-pay at or under 2,000 USD go to the ap-reviewer cog."
PERSON_ROUTE = "Approvals in purchase-to-pay over 10,000 USD go to the controller."
THRESHOLD = "The match tolerance is 2 percent."


def changed(hub, old, new, kind):
    edit_frame(hub, "finance-policy", old, new)
    p = hub.compile(save=False)
    [c] = [c for c in p["changes"] if c["op"] == "add" and c["rule"]["kind"] == kind]
    assert c.get("changes_from"), "one change, not an unrelated add and remove"
    return c


def test_a_lower_cog_limit_narrows_and_a_higher_one_widens(released):
    assert not changed(released, CRIT, CRIT.replace("2,000", "1,500"), "approval_criteria")["widening"]
    assert changed(released, CRIT.replace("2,000", "1,500"), CRIT.replace("2,000", "2,500"), "approval_criteria")["widening"]


def test_a_route_that_sends_fewer_items_to_a_cog_narrows_and_more_widens(released):
    lower = changed(released, COG_ROUTE, COG_ROUTE.replace("2,000", "1,500"), "routing")
    assert not lower["widening"] and "1500" in lower["reason"]
    assert changed(released, COG_ROUTE.replace("2,000", "1,500"), COG_ROUTE.replace("2,000", "3,000"), "routing")["widening"]


def test_more_items_to_a_person_is_not_a_widening(released):
    assert not changed(released, PERSON_ROUTE, PERSON_ROUTE.replace("10,000", "5,000"), "routing")["widening"]
    assert not changed(released, PERSON_ROUTE.replace("10,000", "5,000"), PERSON_ROUTE.replace("10,000", "20,000"), "routing")["widening"]


def test_a_changed_threshold_needs_both_owners_either_way(released):
    """governance-2/8: a threshold can cut either way (a lower tolerance narrows, a lower sample size widens)."""
    assert changed(released, THRESHOLD, THRESHOLD.replace("2", "1"), "threshold")["widening"]
    assert changed(released, THRESHOLD.replace("2", "1"), THRESHOLD.replace("2", "4"), "threshold")["widening"]


def test_or_the_same_in_another_currency(released, tmp_path):
    crit = CRIT.replace("2,000 USD", "1,500 USD or the same in EUR")
    route = COG_ROUTE.replace("2,000 USD", "1,500 USD or the same in EUR")
    f = released.frames.get("finance-policy")
    frame = load_frame_text(f.text.replace(CRIT, crit).replace(COG_ROUTE, route), f.path)
    ex = ConventionExtractor().extract(frame)
    [c] = [r for r in ex.rules if r["kind"] == "approval_criteria"]
    [r] = [r for r in ex.rules if r["kind"] == "routing" and r["assignee"] == "cog:ap-reviewer"]
    assert c["max_amount"] == 1500.0 and c["currency"] == "USD" and c["also_currencies"] == ["EUR"]
    assert r["condition"]["also_currencies"] == ["EUR"] and not [n for n in ex.notes if n.get("severity") == "error"]
    procs = released.current_bundle()["processes"]
    assert sentence(c, procs) == crit and sentence(r, procs) == route, "the sentence writer writes it back the same"
    facts = {"conditions": {"match_within_tolerance": True, "supplier_approved": True}}
    assert evaluate_criteria(c, {"amount": 1000, "currency": "EUR", **facts})[0]
    assert not evaluate_criteria(c, {"amount": 1000, "currency": "GBP", **facts})[0]
    assert condition_holds(r["condition"], {"amount": 1000, "currency": "EUR"}) and not condition_holds(r["condition"], {"amount": 1600, "currency": "EUR"})
    edit_frame(released, "finance-policy", CRIT, crit)
    p = released.compile(save=False)
    [ch] = [x for x in p["changes"] if x["op"] == "add" and x["rule"]["kind"] == "approval_criteria"]
    assert ch["widening"], "the limit now holds in EUR too: more bills for the cog"
    assert not [f for f in p["findings"] if f["severity"] == "error"]
