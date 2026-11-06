"""The chain: user needs -> requirements -> design documentation -> review -> part and items ->
purchase requests -> approved supplier -> purchase order -> goods receipt -> bill -> ledger."""

import base64

import pytest

from conftest import dated, release_all, seen
from hubproto import worker
from hubproto.runner import RunError
from test_cad import SCHEMATIC, fcstd_bytes


@pytest.fixture
def fake_worker(monkeypatch):
    monkeypatch.setenv("HUBPROTO_CAD_FAKE", "1")
    server = worker.serve_in_thread("127.0.0.1", 0)
    monkeypatch.setenv("HUBPROTO_CAD_WORKER_URL", f"http://127.0.0.1:{server.server_address[1]}")
    yield
    server.shutdown()


def design_input():
    return {"record": {
        "title": "Sensor board", "kind": "electrical", "change_class": "3",
        "user_needs": ["The sensor must read temperature to within 0.5 C between -20 and 85 C.",
                       {"text": "The board must survive a 1 m drop onto concrete.", "kind": "performance", "source": "customer"}],
        "attachments": [{"name": "sensor.kicad_sch", "content": SCHEMATIC, "doc_class": "controlled", "doc_type": "schematic"},
                        {"name": "bracket.FCStd", "content_b64": base64.b64encode(fcstd_bytes()).decode(), "doc_class": "controlled", "doc_type": "model"}]}}


def test_design_to_order_to_receipt_to_ledger(hub, fake_worker):
    release_all(hub)
    k = hub.kernel
    # Inventory: one known part from an approved supplier with a unit cost; the other BOM line is unknown.
    k.create("item", "Inventory/Items", {"name": "10k 0603", "mpn": "RC0603FR-0710KL", "manufacturer": "Yageo", "stock": 1,
                                         "supplier": "acme-metals", "unit_cost": 0.02, "currency": "USD"}, by="alice")

    # 1. The design engineer submits the design with user needs and CAD documents.
    run_id = hub.runner.submit("design-release", design_input(), owner="alice")
    state = hub.runner.load(run_id)
    assert state["status"] == "waiting", [e for e in hub.runner.track(run_id) if e["type"] in ("run.failed",)]
    design = k.get(state["record"])
    reqs = k.query(type_="requirement")
    assert len(reqs) == 2 and all(r["state"] == "awaiting-accept-the-requirement" for r in reqs)
    assert design["fields"]["requirements_accepted"] is False
    trace_doc = [d for d in hub.dms.documents_of(design["locator"]) if d["fields"]["doc_type"] == "requirements-trace"]
    assert trace_doc and b"user-need" in hub.dms.content(trace_doc[0]["locator"])[0]
    # The design waits for the design authority; the risk is high while requirements are unaccepted.
    assert state["pending"]["assignees"] == ["bob"] and design["fields"]["risk"] == "high"

    # 2. The design authority accepts both requirements, giving acceptance criteria at the Gate.
    for r in reqs:
        rr = hub.runner.run_for_record(r["locator"])
        with pytest.raises(RunError, match="not an assignee"):
            hub.runner.decide(rr["id"], "alice", "approve", meaning="ok", payload_digest=seen(hub.runner, rr["id"]))
        hub.runner.decide(rr["id"], "bob", "approve", meaning="requirement accepted",
                          fields={"acceptance_criteria": "Verified by the acceptance test procedure", "verification_method": "test"}, payload_digest=seen(hub.runner, rr["id"]))
        assert k.get(r["locator"])["state"] == "accepted"

    # The waiting design is refreshed by the acceptance itself, before anyone opens its review card.
    design = k.get(design["locator"])
    assert design["fields"]["requirements_accepted"] is True and design["fields"]["risk"] != "high"
    assert all(t["state"] == "accepted" for t in design["fields"]["requirement_trace"])

    # 3. A second submission (the first is still waiting) now traces to accepted requirements only.
    run2 = hub.runner.submit("design-release", {"record": {"title": "Sensor board rev", "kind": "electrical", "change_class": "3",
                                                           "requirements": [r["locator"] for r in reqs],
                                                           "attachments": design_input()["record"]["attachments"]}}, owner="alice")
    s2 = hub.runner.load(run2)
    d2 = k.get(s2["record"])
    assert d2["fields"]["requirements_accepted"] is True and d2["fields"]["risk"] == "low"
    snapshot_before = hub.dms.render_snapshot(d2["locator"], by="test")
    assert b"## Requirements" in hub.dms.content(snapshot_before["locator"])[0]

    # 4. The design authority approves the review: the part is released, the unknown item is created,
    #    and purchase requests are raised for the shortages under the design's grant.
    hub.runner.decide(run2, "bob", "approve", meaning="design package reviewed and released", payload_digest=seen(hub.runner, run2))
    d2 = k.get(d2["locator"])
    assert d2["state"] == "released" and k.get(d2["fields"]["part"])["state"] == "released"
    created = {k.get(ref)["fields"]["mpn"]: k.get(ref) for ref in d2["fields"]["items_created"]}
    assert set(created) == {"CL10B104KB8NNNC", "PN-BRK-01"}, "the capacitor and the FreeCAD bracket become items"
    assert created["CL10B104KB8NNNC"]["fields"]["stock"] == 0
    prs = [k.get(ref) for ref in d2["fields"]["purchase_requests"]]
    assert len(prs) == 3
    by_mpn = {pr["fields"]["mpn"]: pr for pr in prs}
    # Known item, approved supplier, tiny amount: the purchasing cog may order it within criteria.
    known = by_mpn["RC0603FR-0710KL"]
    assert known["state"] == "ordered" and known["fields"]["supplier_approved"] is True
    po = k.get(known["fields"]["po"])
    assert po["type"] == "purchase-order" and po["fields"]["supplier"] == "acme-metals" and po["state"] == "issued"
    pr_run = hub.runner.run_for_record(known["locator"])
    decision = next(e for e in hub.runner.track(pr_run["id"]) if e["type"] == "gate.decided")
    assert decision["by"] == "purchasing-assistant" and decision["by_type"] == "cog" and "purchasing-assistant cog may approve" in decision["delegating_rule"]["text"]
    assert pr_run["parent_run"] == run2
    # Unknown supplier: the cog escalates and the buyer must decide, supplying the supplier.
    unknown = by_mpn["CL10B104KB8NNNC"]
    assert unknown["state"] == "awaiting-approve-the-purchase" and unknown["assignees"] == ["dave"]
    u_run = hub.runner.run_for_record(unknown["locator"])
    review = next(e for e in hub.runner.track(u_run["id"]) if e["type"] == "gate.cog_review")
    assert review["verdict"] == "escalate"
    with pytest.raises(RunError, match="segregation of duties|not an assignee"):
        hub.runner.decide(u_run["id"], "alice", "approve", meaning="ok", payload_digest=seen(hub.runner, u_run["id"]))
    hub.runner.decide(u_run["id"], "dave", "approve", meaning="ordered from Acme", fields={"supplier": "acme-metals", "unit_cost": 0.05}, payload_digest=seen(hub.runner, u_run["id"]))
    unknown = k.get(unknown["locator"])
    assert unknown["state"] == "ordered"
    po2 = k.get(unknown["fields"]["po"])
    assert po2["fields"]["supplier"] == "acme-metals"
    # The PO aggregate exists for three-way matching, not yet received.
    assert k.purchase_orders()[f"po:{po['number']}"]["received"] is False

    # 5. Goods receipt books stock and marks the order received.
    gr = hub.runner.submit("goods-receipt", {"record": {"po": po["locator"], "lines": [{"item": k.get(known["fields"]["item"])["locator"],
                                                                                        "quantity": 1}]}}, owner="alice")
    gstate = hub.runner.load(gr)
    assert gstate["status"] == "waiting" and gstate["pending"]["assignees"] == ["dave"]
    hub.runner.decide(gr, "dave", "approve", meaning="goods checked and received", payload_digest=seen(hub.runner, gr))
    assert k.get(gstate["record"])["state"] == "received"
    assert k.get(known["fields"]["item"])["fields"]["stock"] == 2
    assert k.purchase_orders()[f"po:{po['number']}"]["received"] is True and k.get(po["locator"])["state"] == "received"

    # 6. The supplier's bill against that PO is matched, approved within criteria and posted to the ledger.
    bill = dated({"supplier": "acme-metals", "amount": round(po["fields"]["amount"], 2), "currency": "USD",
                  "po_ref": po["number"], "description": "resistors"})
    b_run = hub.runner.submit("purchase-to-pay", {"record": bill}, owner="alice")
    b_state = hub.runner.load(b_run)
    assert b_state["status"] == "completed", [e for e in hub.runner.track(b_run) if e["type"] in ("run.failed", "gate.skipped", "gate.cog_review")]
    journal = next(iter(k.journals().values()))
    assert journal["prepared_by"] == "alice" and journal["approved_by"] == "ap-reviewer"
    assert abs(journal["lines"][0]["debit"] - po["fields"]["amount"]) < 0.005

    # 7. Everything is on one spine: the PO links the request, the item and the design.
    po_links = {(l["rel"], l["target"]) for l in k.get(po["locator"])["links"]}
    assert ("fulfils", known["locator"]) in po_links and ("concerns", d2["locator"]) in po_links


def test_purchase_above_threshold_goes_to_the_controller(hub):
    release_all(hub)
    k = hub.kernel
    item = k.create("item", "Inventory/Items", {"name": "CNC fixture", "mpn": "FIX-1", "manufacturer": "Acme", "stock": 0,
                                                "supplier": "acme-metals", "unit_cost": 3000.0}, by="alice")
    run_id = hub.runner.submit("purchase-request", {"record": {"title": "fixtures", "item": item["locator"], "quantity": 4}}, owner="alice")
    state = hub.runner.load(run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["bob"]
    assert k.get(state["record"])["fields"]["amount"] == 12000.0
    assert state["pending"]["due"], "the 3-day SLA sets a due date"


def test_unapproved_supplier_never_reaches_the_cog(hub):
    release_all(hub)
    k = hub.kernel
    item = k.create("item", "Inventory/Items", {"name": "tool", "mpn": "T-1", "manufacturer": "NewCo", "stock": 0,
                                                "supplier": "newco-tools", "unit_cost": 10.0}, by="alice")
    run_id = hub.runner.submit("purchase-request", {"record": {"title": "tools", "item": item["locator"], "quantity": 2}}, owner="alice")
    state = hub.runner.load(run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["dave"]
    rec = k.get(state["record"])
    assert rec["fields"]["supplier_approved"] is False and "not approved" in rec["fields"]["supplier_note"]
    review = next(e for e in hub.runner.track(run_id) if e["type"] == "gate.cog_review")
    assert review["verdict"] == "escalate" and any("supplier_approved" in r for r in review["reasons"])
