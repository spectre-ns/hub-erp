"""Cogs' inventory tools: look up parts and stock; move, count and quarantine stock where it is free and both the cog
and the person it works for may; ask for an adjustment or a release where it needs an approval. The purchasing
assistant signs small write-offs, so it keeps no stock: it is refused moves, counts and adjustment requests, cleanly."""

import pytest

from conftest import edit_frame, make_part, release_all
from hubproto import inventory
from hubproto.agent import AgentStore, AgentTools


@pytest.fixture
def inv(hub):
    release_all(hub)
    return hub


def tools(hub, requester, cog="purchasing-assistant"):
    return AgentTools(hub, cog, f"t-{requester}", "act", 8, AgentStore(hub.root / "state" / "agents"), requester=requester)


def test_a_cog_finds_parts_and_their_stock(inv):
    k = inv.kernel
    part = make_part(k, "RC0603FR-0710KL", title="10k 0603", manufacturer="Yageo", unit_cost=0.02, stock=250)
    t = tools(inv, "dave")
    found = t.execute("find_parts", {"query": "0710kl"})
    assert found["count"] == 1 and found["parts"][0]["part"] == part["number"] and found["parts"][0]["available"] == 250
    st = t.execute("stock_of", {"part": part["number"]})
    assert st["available"] == 250 and st["lots"][0]["location"] == "Main site / Stores" and st["supplier_parts"][0]["supplier"] == "acme-metals"


def test_the_purchasing_assistant_keeps_no_stock(inv):
    k = inv.kernel
    part = make_part(k, "M4-10", title="M4 screw", stock=100)
    [lot] = inventory.lots(k, part=part)
    prod = inventory.location_of_kind(k, "MAIN", "production")["locator"]
    t = tools(inv, "dave")  # dave is a storekeeper; the cog is not his way to custody it does not have
    for name, args in (("transfer_stock", {"lot": lot["number"], "to_location": prod, "quantity": 10}),
                       ("count_stock", {"lot": lot["number"], "counted": 99})):
        out = t.execute(name, args)
        assert out.get("refused") and "purchasing-assistant keeps no stock" in out["error"] and "storekeeper" in out["error"], (name, out)
    req = t.execute("request_stock_adjustment", {"lot": lot["number"], "new_quantity": 90, "reason": "count found 90"})
    assert req.get("refused") and "cannot file a stock-adjustment for dave" in req["error"], req
    lot = k.get(lot["locator"])
    assert lot["fields"]["quantity"] == 100 and lot["fields"]["location"] != prod and not k.query(type_="stock-adjustment")
    held = tools(inv, "alice").execute("quarantine_stock", {"lot": lot["number"], "quantity": 5, "reason": "rust on the heads"})
    assert held["ok"] and held["status"] == "QUARANTINED", "anyone on the staff may hold stock, cog or person: containment never waits"
    rel = t.execute("request_stock_release", {"lot": held["lot"], "reason": "the rust wiped off; certificate received"})
    assert rel["ok"] and rel["process"] == "stock-release" and rel["waiting_for"] == ["carol"], rel


def test_a_cog_with_custody_moves_and_counts_where_both_may(hub):
    """A cog given custody (the quality assistant, in this test's policy only) uses the same tools: always where both
    it and the person it works for may, and a run of counts shares one tolerance."""
    edit_frame(hub, "inventory-policy", "- purchasing-assistant (cog): stock-reviewer",
               "- purchasing-assistant (cog): stock-reviewer\n- quality-assistant (cog): storekeeper")
    release_all(hub)
    k = hub.kernel
    part = make_part(k, "NUT-M4", title="M4 nut", unit_cost=0.05, stock=1000)
    [lot] = inventory.lots(k, part=part)
    prod = inventory.location_of_kind(k, "MAIN", "production")["locator"]
    refused = tools(hub, "alice", cog="quality-assistant").execute("transfer_stock", {"lot": lot["number"], "to_location": prod, "quantity": 10})
    assert refused.get("refused") and "alice, for whom you work, may not" in refused["error"], "the cog is never her way round it"
    t = tools(hub, "dave", cog="quality-assistant")
    moved = t.execute("transfer_stock", {"lot": lot["number"], "to_location": prod, "quantity": 10})
    assert moved["ok"] and moved["quantity"] == 10 and moved["location"] == "Main site / Production"
    ok = t.execute("count_stock", {"lot": lot["number"], "counted": 980})  # 1% from the 990 the move's split left
    assert ok["ok"] and ok["quantity"] == 980
    out = t.execute("count_stock", {"lot": lot["number"], "counted": 970})  # 1% more, 2.02% from 990: no second free 2%
    assert out.get("needs_approval") and out["baseline"] == 990 and "from 990" in out["error"] and k.get(lot["locator"])["fields"]["quantity"] == 980
    req = t.execute("request_stock_adjustment", {"lot": lot["number"], "new_quantity": 970, "reason": "count found 970"})
    assert req["ok"] and req["process"] == "stock-adjustment" and req["status"] == "waiting", req
    assert req["waiting_for"] == ["bob"], "0.50 USD, but outside the tolerance from the baseline: not the purchasing assistant's to sign"
