"""Inventory after InvenTree, governed: parts, locations (a tree; structural ones hold nothing), lots with a status, and
a ledger of movements on the hash-chained kernel log. Free: receiving, transfers within a site, splits, merges within a
batch, counts within tolerance, quarantine. Gated: adjustments (counts outside tolerance), releases from quarantine,
and NCR dispositions."""

import shutil

import pytest

from conftest import ROOT, make_part, release_all, seen
from hubproto import inventory
from hubproto.inventory import NeedsApproval, StockError


@pytest.fixture
def inv(hub):
    release_all(hub)
    return hub


def loc(k, kind):
    return inventory.location_of_kind(k, "MAIN", kind)["locator"]


def test_the_seed_lays_out_a_site_and_categories(inv):
    k = inv.kernel
    names = {r["fields"]["name"]: r for r in inventory.locations(k)}
    assert {"Main site", "Stores", "Receiving", "Quarantine", "Scrap", "Transit"} <= set(names)
    assert names["Main site"]["fields"]["structural"] is True
    assert inventory.location_path(k, names["Stores"]["locator"]) == "Main site / Stores"
    assert inventory.site_of(k, names["Quarantine"]["locator"]) == "MAIN"
    cats = {r["fields"]["name"] for r in k.query(type_="part-category")}
    assert {"Electronics", "Resistors", "Capacitors", "Mechanical"} <= cats


def test_receiving_makes_lots_and_serial_parts_get_one_lot_each(inv):
    k = inv.kernel
    resistor = make_part(k, "RC0603FR-0710KL", title="10k 0603", manufacturer="Yageo", unit_cost=0.02)
    [lot] = inventory.receive(k, part=resistor, quantity=500, location=loc(k, "store"), batch="Y-1", unit_cost=0.02, by="dave")
    assert lot["fields"]["quantity"] == 500 and lot["state"] == "in-stock" and lot["fields"]["status"] == "OK"
    assert inventory.available(k, resistor) == 500 and inventory.ledger_consistent(k, lot)
    sensor = make_part(k, "SNS-1", title="sensor", trackable="serial")
    with pytest.raises(StockError, match="one serial number for each"):
        inventory.receive(k, part=sensor, quantity=2, location=loc(k, "store"), serials=["S1"], by="dave")
    lots = inventory.receive(k, part=sensor, quantity=2, location=loc(k, "store"), serials=["S1", "S2"], by="dave")
    assert [x["fields"]["serial"] for x in lots] == ["S1", "S2"] and all(x["fields"]["quantity"] == 1 for x in lots)
    with pytest.raises(StockError, match="already exist"):
        inventory.receive(k, part=sensor, quantity=1, location=loc(k, "store"), serials=["S2"], by="dave")
    batched = make_part(k, "GLUE-1", title="glue", trackable="lot")
    with pytest.raises(StockError, match="give the batch"):
        inventory.receive(k, part=batched, quantity=1, location=loc(k, "store"), by="dave")
    root = next(r for r in inventory.locations(k) if r["fields"]["structural"])
    with pytest.raises(StockError, match="structural"):
        inventory.receive(k, part=resistor, quantity=1, location=root["locator"], by="dave")


def test_transfers_stay_on_their_site_and_split_what_they_take(inv):
    k = inv.kernel
    part = make_part(k, "M3-8", title="M3 screw", stock=100)
    [lot] = inventory.lots(k, part=part)
    moved = inventory.transfer(k, lot, loc(k, "production"), quantity=30, by="dave", reason="kitting")
    assert moved["fields"]["quantity"] == 30 and moved["fields"]["location"] == loc(k, "production")
    assert k.get(lot["locator"])["fields"]["quantity"] == 70
    assert ("split-from", lot["locator"]) in {(l["rel"], l["target"]) for l in moved["links"]}
    other_site = k.create("stock-location", "Inventory/Locations", {"name": "Second site", "kind": "store", "site": "SECOND"}, by="seed", state="active")
    with pytest.raises(StockError, match="another site"):
        inventory.transfer(k, moved, other_site["locator"], by="dave")
    via = inventory.transfer(k, moved, loc(k, "transit"), by="dave", reason="to the second site")
    assert inventory.transfer(k, via, other_site["locator"], by="dave")["fields"]["location"] == other_site["locator"], "through transit"
    with pytest.raises(StockError, match="disposition"):
        inventory.transfer(k, k.get(lot["locator"]), loc(k, "scrap"), by="dave")
    assert all(inventory.ledger_consistent(k, x) for x in inventory.lots(k, part=part))


def test_a_count_within_tolerance_is_booked_and_outside_it_needs_an_approval(inv):
    k = inv.kernel
    bundle = inv.releases.bundle(inv.releases.current()["digest"])
    part = make_part(k, "WASH-M3", title="washer", stock=1000)
    [lot] = inventory.lots(k, part=part)
    counted = inventory.count(k, lot, 985, bundle=bundle, by="dave")  # 1.5% under: within the 2% tolerance
    assert counted["fields"]["quantity"] == 985
    ev = inventory.history(k, lot)[-1]
    assert ev["type"] == "stock.counted" and ev["data"]["within_tolerance"] and ev["data"]["quantity"] == -15
    with pytest.raises(NeedsApproval, match="outside the count variance tolerance of 2%") as e:
        inventory.count(k, counted, 900, bundle=bundle, by="dave")
    assert e.value.info["variance"] == -85
    assert k.get(lot["locator"])["fields"]["quantity"] == 985, "nothing changes until the adjustment is approved"


def test_counts_share_one_tolerance_until_the_lot_is_set_otherwise(inv):
    """No salami: counts of 1% each do not add up to a write-off nobody approved. Free counts are measured from the
    baseline (the quantity the receipt or an approved adjustment set, carried through splits and merges)."""
    k = inv.kernel
    bundle = inv.releases.bundle(inv.releases.current()["digest"])
    part = make_part(k, "SALAMI-1", title="salami", stock=1000)
    [lot] = inventory.lots(k, part=part)
    lot = inventory.count(k, lot, 990, bundle=bundle, by="dave")  # 1% from 1000
    lot = inventory.count(k, lot, 981, bundle=bundle, by="dave")  # 1.9% from 1000
    assert inventory.history(k, lot)[-1]["data"]["cumulative_pct"] == 1.9
    with pytest.raises(NeedsApproval, match=r"a variance of 2\.1% from 1000 \(the counts since it was last set to 1000 share one tolerance\)") as e:
        inventory.count(k, lot, 979, bundle=bundle, by="dave")  # only 0.2% from 981
    assert e.value.info["baseline"] == 1000 and e.value.info["cumulative_variance"] == -21 and e.value.info["variance"] == -2
    assert k.get(lot["locator"])["fields"]["quantity"] == 981 and inventory.count_baseline(k, lot) == 1000
    inventory.adjust(k, lot, 979, by="adjustment-applier", reason="ADJ-00001 approved")  # an approved adjustment
    assert inventory.count_baseline(k, lot) == 979
    assert inventory.count(k, k.get(lot["locator"]), 970, bundle=bundle, by="dave")["fields"]["quantity"] == 970, "0.9% from the new baseline"
    child = inventory.split(k, k.get(lot["locator"]), 470, by="dave")
    share = round(470 * 979 / 970, 6)
    assert inventory.count_baseline(k, child) == share and inventory.count_baseline(k, lot) == round(979 - share, 6), \
        "a split shares the baseline in proportion: the drift counted so far goes with the stock"


def test_splitting_counting_down_and_merging_back_never_escapes_the_tolerance(inv):
    """Splitting stock off, counting it down by the tolerance and merging it back would start a fresh tolerance each
    time if a split or a merge set the baseline; they carry it instead, so the drift adds up and is caught."""
    k = inv.kernel
    bundle = inv.releases.bundle(inv.releases.current()["digest"])
    part = make_part(k, "SALAMI-2", title="salami")
    [lot] = inventory.receive(k, part=part, quantity=1000, location=loc(k, "store"), batch="B1", by="dave")
    lot = inventory.count(k, lot, 981, bundle=bundle, by="dave")  # 1.9% down, free
    child = inventory.split(k, lot, 500, by="dave")
    with pytest.raises(NeedsApproval, match="outside the count variance tolerance"):
        inventory.count(k, child, 491, bundle=bundle, by="dave")  # 1.8% from 500, but 3.6% from its share of the baseline
    merged = inventory.merge(k, [child], k.get(lot["locator"]), by="dave")
    assert merged["fields"]["quantity"] == 981 and inventory.count_baseline(k, merged) == 1000, "the baselines add back up"
    with pytest.raises(NeedsApproval, match=r"from 1000"):
        inventory.count(k, merged, 975, bundle=bundle, by="dave")


def test_merges_only_within_one_batch_and_never_serials(inv):
    k = inv.kernel
    part = make_part(k, "R-100", title="100R")
    a, = inventory.receive(k, part=part, quantity=10, location=loc(k, "store"), batch="B1", by="dave")
    b, = inventory.receive(k, part=part, quantity=5, location=loc(k, "store"), batch="B1", by="dave")
    c, = inventory.receive(k, part=part, quantity=5, location=loc(k, "store"), batch="B2", by="dave")
    with pytest.raises(StockError, match="differ in batch"):
        inventory.merge(k, [c], a, by="dave")
    merged = inventory.merge(k, [b], a, by="dave")
    assert merged["fields"]["quantity"] == 15 and k.get(b["locator"])["state"] == "depleted"
    assert ("merged-into", a["locator"]) in {(l["rel"], l["target"]) for l in k.get(b["locator"])["links"]}


def test_quarantine_is_free_and_release_needs_the_ncr_closed(inv):
    k = inv.kernel
    part = make_part(k, "CAP-1", title="100n", stock=50)
    [lot] = inventory.lots(k, part=part)
    held = inventory.quarantine(k, lot, quantity=10, by="alice", reason="cracked parts")
    assert held["fields"]["status"] == "QUARANTINED" and held["fields"]["location"] == loc(k, "quarantine")
    assert held["fields"]["quarantined_from"] == loc(k, "store") and held["fields"]["quantity"] == 10
    assert inventory.available(k, part) == 40, "quarantined stock is not available"
    with pytest.raises(StockError, match="through a stock release"):
        inventory.transfer(k, held, loc(k, "store"), by="dave")
    back = inventory.release_from_quarantine(k, held, by="carol", reason="inspected: fine")
    assert back["fields"]["status"] == "OK" and back["fields"]["location"] == loc(k, "store")
    assert inventory.available(k, part) == 50


def test_held_stock_has_no_way_round_the_release(inv):
    k = inv.kernel
    part = make_part(k, "HOLD-1", title="held", stock=100)
    [lot] = inventory.lots(k, part=part)
    with pytest.raises(StockError, match="quarantine LOT-.* instead"):
        inventory.transfer(k, lot, loc(k, "quarantine"), by="dave")  # holding stock is a quarantine, never a plain move
    ncr = k.create("ncr", "Quality/NCR", {"title": "suspect lot", "description": "d"}, by="alice")
    held = inventory.quarantine(k, lot, quantity=40, by="dave", reason="suspect", ncr=ncr["locator"])
    assert inventory.open_ncr_for(k, held)["locator"] == ncr["locator"], "the hold's NCR, even where the NCR does not list the lot"
    with pytest.raises(StockError, match="its status changes only through a stock release"):
        inventory.mark(k, held, "ATTENTION", by="dave", reason="relabel")  # ATTENTION would let it be moved out freely
    with pytest.raises(StockError, match="its disposition decides"):
        inventory.split(k, held, 10, by="dave")  # a split-off lot would be named by no NCR
    with pytest.raises(StockError, match="through a stock release"):
        inventory.transfer(k, held, loc(k, "store"), by="dave")
    with pytest.raises(StockError, match="which is open"):
        inventory.release_from_quarantine(k, held, by="carol", reason="looks fine")
    assert inventory.available(k, part) == 60 and inventory.ledger_consistent(k, held)


def test_the_ledger_is_on_the_hash_chain(inv):
    k = inv.kernel
    part = make_part(k, "CHAIN-1", title="chained", stock=5)
    [lot] = inventory.lots(k, part=part)
    inventory.transfer(k, lot, loc(k, "production"), by="dave")
    types = [e["type"] for e in inventory.history(k, lot)]
    assert types == ["stock.received", "stock.moved"]
    assert k.verify_chain() == [], "every movement is a chained event"


def test_a_hub_seeded_before_inventory_gets_its_locations_on_the_next_start_once(tmp_path):
    """The live hub was seeded before stock locations existed: its next start (hubproto init, run by the entrypoint
    at every start) adds the location tree and the part categories, once, and leaves everything else alone."""
    from hubproto import cli
    from hubproto.app import Hub

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    old = Hub(tmp_path)
    old.kernel._seed_finance()  # how the hub was seeded before: suppliers, finance POs, periods; no locations
    assert old.kernel.events(prefix="supplier:") and not old.kernel.query(type_="stock-location")
    before = len(old.kernel.events())
    for _ in range(2):
        assert cli.main(["--root", str(tmp_path), "init"]) == 0
    k = Hub(tmp_path).kernel
    names = sorted(r["fields"]["name"] for r in k.query(type_="stock-location"))
    assert names == sorted(["Main site", "Stores", "Receiving", "Inspection", "Quarantine", "Scrap", "Production", "Transit"]), "the tree, once"
    assert len(k.query(type_="part-category")) == 6 and len(k.events(prefix="supplier:")) == 2, "suppliers untouched"
    added = k.events()[before:]
    assert added and all(e["by"] == "seed" for e in added)


def test_items_are_archived_and_never_offered(inv):
    assert inv.kernel.types["item"].get("archived") is True
    b = inv.releases.bundle(inv.releases.current()["digest"])
    assert "item-request" not in b["ops"] and "part-request" in b["ops"]
    assert {"stock-adjustment", "stock-release"} <= set(b["ops"])


def test_a_part_released_with_its_design_is_a_state_the_policy_knows(inv):
    """A part reaches "released" with its design (the CAD release step creates it so), not through a process: the
    record type says the hub sets that state itself, so the policy is not warned that nothing reaches it."""
    found = [f["detail"] for f in inv.compile(save=False)["findings"]]
    assert not [d for d in found if "record type part" in d], [d for d in found if "record type part" in d]
