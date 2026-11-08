"""Inventory in the shell: the parts list with what is available, the location tree, a part's stock by location and its
suppliers, a lot's ledger and genealogy, the free movements as forms (checked against the release like the cogs'
tools), a count outside the tolerance that offers the adjustment instead of booking it, receiving a purchase order
through goods-receipt, and items archived (readable, never offered)."""

from urllib.parse import quote

import pytest

from conftest import make_part, seen
from hubproto import inventory
from hubproto.agent import AgentStore, AgentTools
from hubproto.kernel import KernelGuardError
from test_shell import get, post, shell  # noqa: F401  (the module-scoped live shell)


def stores(k):
    return inventory.location_of_kind(k, "MAIN", "store")["locator"]


def page(locator):
    return "/record/" + quote(locator, safe=":")


def lot_of(hub, mpn, quantity=1000, unit_cost=0.5, **fields):
    part = make_part(hub.kernel, mpn, title=f"{mpn} part", unit_cost=unit_cost, **fields)
    [lot] = inventory.receive(hub.kernel, part=part, quantity=quantity, location=stores(hub.kernel), unit_cost=unit_cost, batch="B1", by="dave")
    return part, lot


def test_the_parts_list_shows_what_is_available_and_what_is_low(shell):
    hub = shell["hub"]
    part, lot = lot_of(hub, "UI-LIST-1", quantity=40, minimum_stock=100)
    inventory.quarantine(hub.kernel, lot, quantity=10, by="dave", reason="suspect")
    status, text = get(shell, "/inventory", **{"as": "alice"})
    assert status == 200 and "Parts and stock" in text
    row = text.split(f">{part['number']}</a>")[1].split("</tr>")[0]
    assert "<b>30 ea</b>" in row and ">low<" in row and "40 ea" in row, "available leaves the quarantined 10 out; on hand counts them"
    status, text = get(shell, "/inventory", **{"as": "alice", "q": "ui-list-1"})
    assert part["number"] in text, "found by its manufacturer part number"
    status, text = get(shell, "/inventory", **{"as": "alice", "q": "no-such-part-anywhere"})
    assert part["number"] not in text and "No parts match." in text
    status, text = get(shell, "/inventory", **{"as": "alice", "low": "1"})
    assert part["number"] in text


def test_the_location_tree_shows_what_each_location_holds(shell):
    hub = shell["hub"]
    k = hub.kernel
    part, lot = lot_of(hub, "UI-TREE-1", quantity=12)
    store = k.get(stores(k))
    status, text = get(shell, "/inventory/locations", **{"as": "alice", "loc": store["number"]})
    assert status == 200 and 'aria-current="page"' in text and "Main site / Stores" in text and lot["number"] in text
    root = next(r for r in inventory.locations(k) if r["fields"].get("structural"))
    status, text = get(shell, "/inventory/locations", **{"as": "alice", "loc": root["number"]})
    assert "it is structural" in text and lot["number"] in text.split("Stock under it")[1], "a structural location shows what is under it"
    status, _ = get(shell, "/inventory/locations", **{"as": "alice", "loc": "LOC-9999"})
    assert status == 404


def test_a_part_page_shows_stock_by_location_suppliers_and_manufacturers(shell):
    hub = shell["hub"]
    part, lot = lot_of(hub, "UI-PART-1", quantity=250, unit_cost=0.02)
    status, text = get(shell, page(part["locator"]), **{"as": "alice"})
    assert status == 200
    stock = text.split('id="stock"')[1] if 'id="stock"' in text else text.split("id='stock'")[1]
    assert "By location" in stock and "Main site / Stores" in stock and "250 ea" in stock
    assert "acme-metals" in stock.lower() or "Acme" in stock, "the supplier part"
    assert "1+: 0.02 USD" in stock and "UI-PART-1" in stock, "price breaks and the manufacturer part number"
    assert "Raise a purchase request" in stock, "alice may start a purchase request from the part"


def test_free_movements_from_a_lot_page_go_on_the_ledger(shell):
    hub = shell["hub"]
    k = hub.kernel
    part, lot = lot_of(hub, "UI-MOVE-1")
    status, text = get(shell, page(lot["locator"]), **{"as": "dave"})
    assert status == 200 and 'action="/stock-transfer"' in text and 'action="/stock-count"' in text and "Ledger" in text
    prod = inventory.location_of_kind(k, "MAIN", "production")["locator"]
    status, url, text = post(shell, "/stock-transfer", {"as": "dave", "lot": lot["locator"], "to_location": prod, "quantity": "30", "reason": "kitting"})
    assert status == 200 and "Moved+30+of" in url and k.get(lot["locator"])["fields"]["quantity"] == 970
    status, url, text = post(shell, "/stock-count", {"as": "dave", "lot": lot["locator"], "counted": "960"})  # 1%: within the 2% tolerance
    assert status == 200 and k.get(lot["locator"])["fields"]["quantity"] == 960
    status, url, text = post(shell, "/stock-split", {"as": "dave", "lot": lot["locator"], "quantity": "60"})
    assert status == 200 and k.get(lot["locator"])["fields"]["quantity"] == 900
    child = next(r for r in inventory.lots(k, part=part) if r["fields"].get("parent") == lot["locator"] and r["fields"]["location"] == stores(k))
    status, url, text = post(shell, "/stock-merge", {"as": "dave", "lot": lot["locator"], "sources": child["locator"]})
    assert status == 200 and k.get(lot["locator"])["fields"]["quantity"] == 960 and k.get(child["locator"])["state"] == "depleted"
    types = [e["type"] for e in inventory.history(k, lot)]
    assert types == ["stock.received", "stock.split", "stock.counted", "stock.split", "stock.merged"]
    assert all(inventory.ledger_consistent(k, r) for r in inventory.lots(k, part=part, live=False)) and k.verify_chain() == []
    status, text = get(shell, page(lot["locator"]), **{"as": "dave"})
    assert "Split into" in text and "Merged from" in text and "counted" in text, "genealogy and the ledger on the lot's page"
    status, url, text = post(shell, "/stock-split", {"as": "dave", "lot": lot["locator"], "quantity": "99999"})
    assert status == 400 and "Cannot split 99999" in text and k.get(lot["locator"])["fields"]["quantity"] == 960, "the guard refuses; the page says why"


def test_a_count_outside_the_tolerance_is_not_booked_and_offers_the_adjustment(shell):
    hub = shell["hub"]
    k = hub.kernel
    part, lot = lot_of(hub, "UI-COUNT-1")
    status, url, text = post(shell, "/stock-count", {"as": "dave", "lot": lot["locator"], "counted": "800"})
    assert status == 409 and k.get(lot["locator"])["fields"]["quantity"] == 1000, "nothing changes until the adjustment is approved"
    card = text.split('id="needs-approval"')[1] if 'id="needs-approval"' in text else text.split("id='needs-approval'")[1]
    assert "outside the count variance tolerance of 2%" in card and 'name="process" value="stock-adjustment"' in card and 'name="new_quantity" value="800"' in card
    status, url, text = post(shell, "/start", {"as": "dave", "process": "stock-adjustment", "lot": lot["locator"], "new_quantity": "800",
                                               "reason": "count found 800"})
    assert status == 200, text[:500]
    adj = [r for r in k.query(type_="stock-adjustment") if r["fields"].get("lot") == lot["locator"]][-1]
    run = hub.runner.run_for_record(adj["locator"])
    assert run["status"] == "waiting" and run["pending"]["assignees"] == ["bob"], "outside the cog's rail: the controller"
    status, text = get(shell, "/inbox", **{"as": "bob", "open": run["id"]})
    card = text.split('class="card decide"')[1].split("</article>")[0] if 'class="card decide"' in text else text.split("class='card decide'")[1].split("</article>")[0]
    assert adj["number"] in card and "1,000 ea → <b>800 ea</b>" in card and "outside tolerance" in card and "100.00 USD" in card, "the change and its value"
    hub.runner.decide(run["id"], "bob", "approve", meaning="written off", payload_digest=seen(hub.runner, run["id"]))
    assert k.get(lot["locator"])["fields"]["quantity"] == 800 and inventory.history(k, lot)[-1]["type"] == "stock.adjusted"


def test_stock_actions_need_the_right_in_the_release(shell):
    hub = shell["hub"]
    k = hub.kernel
    part, lot = lot_of(hub, "UI-RIGHTS-1", quantity=50)
    status, text = get(shell, page(lot["locator"]), **{"as": "alice"})
    assert 'action="/stock-transfer"' not in text and 'action="/stock-quarantine"' in text, "alice is staff, not a storekeeper"
    prod = inventory.location_of_kind(k, "MAIN", "production")["locator"]
    status, url, text = post(shell, "/stock-transfer", {"as": "alice", "lot": lot["locator"], "to_location": prod})
    assert status == 403 and "Who may: storekeepers" in text and k.get(lot["locator"])["fields"]["location"] == stores(k)
    status, url, text = post(shell, "/stock-quarantine", {"as": "alice", "lot": lot["locator"], "quantity": "5", "reason": "rust on the heads"})
    assert status == 200 and "Held+5+of" in url, "anyone on the staff may hold stock: containment never waits"
    held = next(r for r in inventory.lots(k, part=part) if r["fields"]["status"] == "QUARANTINED")
    status, text = get(shell, page(held["locator"]), **{"as": "dave"})
    assert "Request a release" in text and 'value="stock-release"' in text and 'action="/stock-quarantine"' not in text
    status, url, text = post(shell, "/stock-transfer", {"as": "dave", "lot": held["locator"], "to_location": stores(k)})
    assert status == 400 and "through a stock release" in text, "quarantined stock leaves only through a release"
    status, text = get(shell, page(lot["locator"]), **{"as": "purchasing-assistant"})
    assert 'action="/stock-transfer"' not in text and 'action="/stock-quarantine"' not in text, "watching a cog: nothing in its name"


def test_a_purchase_order_is_received_into_a_location_through_goods_receipt(shell):
    hub = shell["hub"]
    k = hub.kernel
    part = make_part(k, "UI-PO-1", title="47R", unit_cost=0.01)
    pr = hub.runner.submit("purchase-request", {"record": {"title": "47R", "part": part["locator"], "quantity": 1000}}, owner="alice")
    po = k.get(k.get(hub.runner.load(pr)["record"])["fields"]["po"])
    assert po["state"] == "issued"
    status, text = get(shell, page(po["locator"]), **{"as": "alice"})
    assert status == 200 and 'action="/po-receive"' in text and 'name="qty-0" value="1000"' in text
    recv = inventory.location_of_kind(k, "MAIN", "receiving")["locator"]
    status, url, text = post(shell, "/po-receive", {"as": "alice", "po": po["locator"], "location": recv, "qty-0": "600", "packing_slip": "PS-77"})
    assert status == 200 and "waits+for+dave" in url, url
    gr = [r for r in k.query(type_="receipt") if r["fields"].get("po") == po["locator"]][-1]
    assert gr["fields"]["location"] == recv and gr["fields"]["lines"][0]["quantity"] == 600 and gr["fields"]["packing_slip"] == "PS-77"
    run = hub.runner.run_for_record(gr["locator"])
    hub.runner.decide(run["id"], "dave", "approve", meaning="600 received", payload_digest=seen(hub.runner, run["id"]))
    [lot] = inventory.lots(k, part=part)
    assert lot["fields"]["quantity"] == 600 and lot["fields"]["location"] == recv and lot["fields"]["po"] == po["locator"]
    assert inventory.available(k, part) == 600, "receiving counts as available"
    status, text = get(shell, page(po["locator"]), **{"as": "alice"})
    assert 'name="qty-0" value="400"' in text and lot["number"] in text, "the rest is outstanding; the receipt and its lot are listed"
    status, url, text = post(shell, "/po-receive", {"as": "alice", "po": po["locator"], "location": recv})
    assert status == 400 and "at least one line" in text
    status, url, text = post(shell, "/po-receive", {"as": "alice", "po": po["locator"], "location": inventory.locations(k)[0]["locator"], "qty-0": "1"})
    assert status == 400 and "structural" in text, "a structural location holds nothing"


def test_an_order_placed_before_inventory_is_refused_cleanly_not_received(shell):
    """An order whose lines name items (archived) has nothing to receive into stock: the page says so, and a receipt
    recorded against it stops at the check with that reason instead of marking the order received."""
    hub = shell["hub"]
    k = hub.kernel
    po = k.create("purchase-order", "Purchasing/Orders", {"supplier": "acme-metals", "currency": "USD", "amount": 2.0,
                                                          "lines": [{"line": 1, "item": "rec:item:ITEM-00001", "quantity": 100, "unit_cost": 0.02}]},
                  by="purchasing-assistant", state="issued")
    status, text = get(shell, page(po["locator"]), **{"as": "alice"})
    assert status == 200 and 'action="/po-receive"' not in text and "its lines name items, which are archived" in text
    run_id = hub.runner.submit("goods-receipt", {"record": {"po": po["locator"], "lines": [{"part": "PN-99999", "quantity": 100}]}}, owner="alice")
    st = hub.runner.load(run_id)
    failed = [e for e in hub.runner.track(run_id) if e["type"] == "run.failed"][-1]
    assert st["status"] == "failed" and "orders no parts" in failed["reason"] and k.get(po["locator"])["state"] == "issued"


def test_an_ncr_from_the_shell_quarantines_the_lots_it_names(shell):
    hub = shell["hub"]
    k = hub.kernel
    part, lot = lot_of(hub, "UI-NCR-1", quantity=200)
    status, url, text = post(shell, "/start", {"as": "alice", "process": "nonconformance", "title": "Cracked caps", "description": "cracks",
                                               "severity": "medium", "source": "inspection", "stock_items": f"{lot['number']}, 30"})
    assert status == 200, text[:500]
    ncr = [r for r in k.query(type_="ncr") if r["fields"].get("title") == "Cracked caps"][-1]
    [held] = ncr["fields"]["quarantined"]
    assert k.get(held)["fields"]["status"] == "QUARANTINED" and k.get(lot["locator"])["fields"]["quantity"] == 170
    status, text = get(shell, page(ncr["locator"]), **{"as": "carol"})
    assert "Quarantined stock" in text and k.get(held)["number"] in text
    status, text = get(shell, page(held), **{"as": "dave"})
    assert "which is open" in text and 'value="stock-release"' not in text, "the NCR's disposition releases it, not a stock release"


def test_items_are_archived_readable_and_never_offered(shell):
    hub = shell["hub"]
    with pytest.raises(KernelGuardError, match="archived"):
        hub.kernel.create("item", "Inventory/Items", {"name": "old"}, by="alice")
    status, text = get(shell, "/", **{"as": "alice"})
    assert "Request an item" not in text and "item-request" not in text
    status, text = get(shell, "/app/inventory", **{"as": "alice"})
    assert status == 200 and "Stock lots" in text and "Parts and stock" in text
    assert text.count("/items/item?") == text.count(">Archived items</a>") == 1, "items are listed only as archived, to read"
    t = AgentTools(hub, "purchasing-assistant", "t-items", "act", 8, AgentStore(hub.root / "state" / "agents"), requester="dave")
    out = t.execute("create_record", {"type": "item", "fields": {"name": "old"}, "folder": "Inventory/Items"})
    assert "archived" in out.get("error", ""), out
