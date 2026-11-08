"""The gated stock movements: adjustments (routed by value: the purchasing assistant within its rail, the storekeeper,
the controller over the threshold; never signed by whoever approved the order the stock came from), releases from
quarantine (the quality manager; never while an open nonconformance names the lot), and an NCR that quarantines its
stock at once and whose disposition scraps, releases, reworks or returns it."""

import pytest

from conftest import make_part, release_all, seen
from hubproto import inventory


@pytest.fixture
def inv(hub):
    release_all(hub)
    return hub


def stores(k):
    return inventory.location_of_kind(k, "MAIN", "store")["locator"]


def adjustment(hub, lot, new_quantity, owner="dave", reason="cycle count"):
    run_id = hub.runner.submit("stock-adjustment", {"record": {"lot": lot["locator"], "new_quantity": new_quantity, "reason": reason}}, owner=owner)
    return hub.runner.load(run_id)


def test_a_small_adjustment_within_tolerance_is_signed_by_the_purchasing_assistant(inv):
    k = inv.kernel
    part = make_part(k, "WASH-1", title="washer", unit_cost=0.5)
    [lot] = inventory.receive(k, part=part, quantity=1000, location=stores(k), unit_cost=0.5, by="dave")
    st = adjustment(inv, lot, 990)  # 1%: within the tolerance, 5 USD: under the cog's 100 USD
    assert st["status"] == "completed", inv.runner.track(st["id"])[-4:]
    decided = next(e for e in inv.runner.track(st["id"]) if e["type"] == "gate.decided")
    assert decided["by"] == "purchasing-assistant" and decided["by_type"] == "cog"
    assert k.get(lot["locator"])["fields"]["quantity"] == 990
    assert inventory.history(k, lot)[-1]["type"] == "stock.adjusted" and inventory.ledger_consistent(k, lot)


def test_an_adjustment_outside_the_cogs_rail_goes_to_the_controller(inv):
    k = inv.kernel
    part = make_part(k, "FIX-9", title="fixture", unit_cost=20.0)
    [lot] = inventory.receive(k, part=part, quantity=100, location=stores(k), unit_cost=20.0, by="dave")
    st = adjustment(inv, lot, 60, reason="40 missing at the count")
    rec = k.get(st["record"])
    assert rec["fields"]["amount"] == 800.0 and rec["fields"]["conditions"]["variance_within_tolerance"] is False
    assert st["status"] == "waiting" and st["pending"]["assignees"] == ["bob"], "over 500 USD: the controller"
    inv.runner.decide(st["id"], "bob", "approve", meaning="written off", payload_digest=seen(inv.runner, st["id"]))
    assert k.get(lot["locator"])["fields"]["quantity"] == 60 and k.get(st["record"])["state"] == "applied"


@pytest.mark.parametrize("change", ["split", "count"])
def test_an_adjustment_is_never_applied_to_a_lot_that_changed_since_its_check(inv, change):
    """The approval covers the change the check worked out (1000 -> 800: -200, 100 USD). A free split or count
    before the approval would make the absolute 800 book another change: it is refused, and a retry checks the lot
    again and asks for the approval again on what it holds now."""
    k = inv.kernel
    part = make_part(k, f"STALE-{change}", title="stale", unit_cost=0.5)
    [lot] = inventory.receive(k, part=part, quantity=1000, location=stores(k), unit_cost=0.5, by="dave")
    st = adjustment(inv, lot, 800, reason="200 missing")
    rec = k.get(st["record"])
    assert rec["fields"]["quantity_before"] == 1000 and rec["fields"]["amount"] == 100.0 and st["pending"]["assignees"] == ["bob"]
    if change == "split":
        inventory.split(k, lot, 500, by="dave", reason="kitting")
    else:
        bundle = inv.releases.bundle(inv.releases.current()["digest"])
        inventory.count(k, lot, 990, bundle=bundle, by="dave")  # within the tolerance: free
    now = k.get(lot["locator"])["fields"]["quantity"]
    inv.runner.decide(st["id"], "bob", "approve", meaning="written off", payload_digest=seen(inv.runner, st["id"]))
    st = inv.runner.load(st["id"])
    assert st["status"] == "failed" and k.get(lot["locator"])["fields"]["quantity"] == now, "nothing booked on a stale approval"
    assert not any(e["type"] == "stock.adjusted" for e in inventory.history(k, lot)) and not k.get(st["record"])["fields"].get("applied")
    failed = [e for e in inv.runner.track(st["id"]) if e["type"] == "run.failed"][-1]
    assert f"holds {now:g} now, not 1000" in failed["reason"] and failed["recheck"] == "adjustment-checker"
    st = inv.runner.retry(st["id"], "dave", note="check it again")
    rec = k.get(st["record"])
    assert st["status"] == "waiting" and rec["fields"]["quantity_before"] == now, "the retry ran the check again"
    assert rec["fields"]["amount"] == round(abs(800 - now) * 0.5, 2) and st["pending"]["assignees"] == ["bob"]
    voided = [e for e in inv.runner.track(st["id"]) if e["type"] == "gate.voided"]
    assert voided and voided[-1]["was_decided_by"] == "bob", "the first approval was given on the old check: void"
    inv.runner.decide(st["id"], "bob", "approve", meaning="approved on what it holds now", payload_digest=seen(inv.runner, st["id"]))
    assert k.get(lot["locator"])["fields"]["quantity"] == 800 and k.get(st["record"])["state"] == "applied"
    assert inventory.ledger_consistent(k, lot) and inventory.count_baseline(k, lot) == 800, "an approved adjustment is the new baseline"


def test_the_purchasing_assistant_never_signs_the_adjustment_of_stock_it_ordered(inv):
    k = inv.kernel
    part = make_part(k, "R-47", title="47R", unit_cost=0.01)
    pr = inv.runner.submit("purchase-request", {"record": {"title": "47R", "part": part["locator"], "quantity": 1000}}, owner="alice")
    req = k.get(inv.runner.load(pr)["record"])
    po = k.get(req["fields"]["po"])
    assert po["fields"]["approved_by"] == "purchasing-assistant", "the cog ordered it within its purchasing rail"
    gr = inv.runner.submit("goods-receipt", {"record": {"po": po["locator"], "location": stores(k), "lines": [{"part": part["locator"], "quantity": 1000}]}},
                           owner="alice")
    inv.runner.decide(gr, "dave", "approve", meaning="received", payload_digest=seen(inv.runner, gr))
    [lot] = inventory.lots(k, part=part)
    st = adjustment(inv, lot, 995)  # tiny and within tolerance, but the cog approved the order
    rec = k.get(st["record"])
    assert rec["fields"]["ordered_by"] == "purchasing-assistant"
    assert st["status"] == "waiting" and st["pending"]["assignees"] == ["bob"], inv.runner.track(st["id"])[-3:]
    assert not any(e["type"] == "gate.decided" and e.get("by") == "purchasing-assistant" for e in inv.runner.track(st["id"]))


def test_an_ncr_quarantines_its_stock_and_a_scrap_disposition_scraps_it(inv):
    k = inv.kernel
    part = make_part(k, "CAP-22", title="22u", stock=200)
    [lot] = inventory.lots(k, part=part)
    run_id = inv.runner.submit("nonconformance", {"record": {"title": "Cracked capacitors", "description": "visible cracks on the body",
                                                             "severity": "medium", "source": "inspection",
                                                             "stock_items": [{"lot": lot["locator"], "quantity": 30}]}}, owner="alice")
    st = inv.runner.load(run_id)
    ncr = k.get(st["record"])
    [held_ref] = ncr["fields"]["quarantined"]
    held = k.get(held_ref)
    assert held["fields"]["status"] == "QUARANTINED" and held["fields"]["quantity"] == 30 and held["fields"]["ncr"] == ncr["locator"]
    assert k.get(lot["locator"])["fields"]["quantity"] == 170 and inventory.available(k, part) == 170, "containment did not wait"
    assert st["status"] == "waiting" and st["pending"]["assignees"] == ["carol"]
    # A release outside the disposition is refused while the NCR is open.
    rel = inv.runner.submit("stock-release", {"record": {"lot": held["locator"], "reason": "looks fine"}}, owner="dave")
    assert inv.runner.load(rel)["status"] == "failed"
    inv.runner.decide(run_id, "carol", "approve", meaning="scrap them", fields={"disposition": "scrap", "disposition_rationale": "cracks"},
                      payload_digest=seen(inv.runner, run_id))
    held = k.get(held["locator"])
    assert held["state"] == "scrapped" and held["fields"]["status"] == "DESTROYED" and held["fields"]["quantity"] == 0
    assert held["fields"]["location"] == inventory.location_of_kind(k, "MAIN", "scrap")["locator"]
    assert k.get(ncr["locator"])["fields"]["disposition_applied"][0]["status"] == "DESTROYED"


def test_a_use_as_is_disposition_releases_the_stock(inv):
    k = inv.kernel
    part = make_part(k, "CON-4", title="4-pin connector", stock=40)
    [lot] = inventory.lots(k, part=part)
    run_id = inv.runner.submit("nonconformance", {"record": {"title": "Scuffed housings", "description": "cosmetic scuffs", "severity": "medium",
                                                             "source": "inspection", "stock_items": [{"lot": lot["locator"]}]}}, owner="alice")
    st = inv.runner.load(run_id)
    held = k.get(k.get(st["record"])["fields"]["quarantined"][0])
    assert held["locator"] == lot["locator"] and held["fields"]["status"] == "QUARANTINED", "the whole lot"
    assert st["status"] == "waiting" and st["pending"]["assignees"] == ["carol"]
    inv.runner.decide(run_id, "carol", "approve", meaning="use as is", fields={"disposition": "use-as-is", "disposition_rationale": "cosmetic only"},
                      payload_digest=seen(inv.runner, run_id))
    back = k.get(lot["locator"])
    assert back["fields"]["status"] == "OK" and back["fields"]["location"] == stores(k) and inventory.available(k, part) == 40


def test_a_stock_release_is_approved_by_the_quality_manager(inv):
    k = inv.kernel
    part = make_part(k, "SPR-1", title="spring", stock=10)
    [lot] = inventory.lots(k, part=part)
    inventory.quarantine(k, lot, by="dave", reason="suspect batch")
    run_id = inv.runner.submit("stock-release", {"record": {"lot": lot["locator"], "reason": "supplier certificate received"}}, owner="dave")
    st = inv.runner.load(run_id)
    assert st["status"] == "waiting" and st["pending"]["assignees"] == ["carol"]
    with pytest.raises(Exception):
        inv.runner.decide(run_id, "dave", "approve", meaning="mine", payload_digest=seen(inv.runner, run_id))
    inv.runner.decide(run_id, "carol", "approve", meaning="released", payload_digest=seen(inv.runner, run_id))
    assert k.get(lot["locator"])["fields"]["status"] == "OK" and k.get(st["record"])["state"] == "released"
