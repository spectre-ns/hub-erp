"""Regression tests for the acceptance panel's blockers and majors (feedback/*.md)."""

import json
import shutil
from datetime import date

import pytest

from conftest import BIG_BILL, SMALL_BILL, dated, release_all
from hubproto.app import Hub
from hubproto.kernel import Kernel, KernelGuardError, ScopedKernel
from hubproto.runner import RunError


def _item(k, mpn, supplier="acme-metals", unit_cost=0.5, stock=0, manufacturer="Acme"):
    return k.create("item", "Inventory/Items", {"name": mpn, "mpn": mpn, "manufacturer": manufacturer, "stock": stock,
                                                "supplier": supplier, "unit_cost": unit_cost, "currency": "USD"}, by="alice")


# ---------------------------------------------------------------- inventory / accounting / code review: zero-cost orders
def test_cog_never_orders_without_a_price_and_buyer_price_reaches_the_po(released):
    k = released.kernel
    item = _item(k, "R-1", unit_cost=None)
    run_id = released.runner.submit("purchase-request", {"record": {"title": "resistors", "item": item["locator"], "quantity": 1000000}}, owner="alice")
    state = released.runner.load(run_id)
    assert state["status"] == "waiting", "no price: the cog must not approve, the buyer decides"
    review = next(e for e in released.runner.track(run_id) if e["type"] == "gate.cog_review")
    assert review["verdict"] == "escalate"
    with pytest.raises(RunError, match="may not set"):
        released.runner.decide(run_id, "dave", "approve", fields={"requested_by": "dave", "risk": "low"})
    released.runner.decide(run_id, "dave", "approve", meaning="ordered", fields={"unit_cost": 0.02})
    pr = k.get(state["record"])
    po = k.get(pr["fields"]["po"])
    assert po["fields"]["amount"] == 20000.0 and pr["fields"]["amount"] == 20000.0
    assert k.purchase_orders()[f"po:{po['number']}"]["amount"] == 20000.0


def test_buyer_cannot_issue_a_po_to_an_unapproved_supplier(released):
    k = released.kernel
    item = _item(k, "T-9", supplier=None, unit_cost=10.0)
    run_id = released.runner.submit("purchase-request", {"record": {"title": "tools", "item": item["locator"], "quantity": 2}}, owner="alice")
    state = released.runner.decide(run_id, "dave", "approve", meaning="use NewCo", fields={"supplier": "newco-tools"})
    assert state["status"] == "failed"
    failed = next(e for e in released.runner.track(run_id) if e["type"] == "run.failed")
    assert "not approved" in failed["reason"]
    rec = k.get(state["record"])
    assert rec["state"] == "failed" and rec["assignees"] == ["alice"] and not rec["fields"].get("po")
    assert any(m["to"] == "alice" and m["kind"] == "failure" for m in released.inbox.all())


def test_negative_quantities_are_refused(released):
    k = released.kernel
    item = _item(k, "N-1", unit_cost=1.0)
    run_id = released.runner.submit("purchase-request", {"record": {"title": "neg", "item": item["locator"], "quantity": -5}}, owner="alice")
    assert released.runner.load(run_id)["status"] == "failed"


# ---------------------------------------------------------------- accounting: duplicate bills, negative bills, reversal
def test_duplicate_bill_against_a_fully_billed_po_is_refused(released):
    first = released.runner.submit("purchase-to-pay", {"record": dated(SMALL_BILL)}, owner="alice")
    assert released.runner.load(first)["status"] == "completed"
    second = released.runner.submit("purchase-to-pay", {"record": dated(SMALL_BILL)}, owner="alice")
    state = released.runner.load(second)
    assert state["status"] == "failed"
    result = next(e for e in released.runner.track(second) if e["type"] == "step.result" and e["step"].startswith("match"))
    assert any(p["check"] == "po-overbilled" for p in result["problems"])
    assert len(released.kernel.journals()) == 1


def test_negative_bill_never_posts(released):
    run_id = released.runner.submit("purchase-to-pay", {"record": dated({**SMALL_BILL, "amount": -1500})}, owner="alice")
    assert released.runner.load(run_id)["status"] == "failed"
    with pytest.raises(KernelGuardError, match="negative"):
        released.kernel.post_journal({"date": date.today().isoformat(), "lines": [{"account": "a", "debit": -5, "credit": 0}, {"account": "b", "debit": 0, "credit": -5}]},
                                     run_id="r", release="x", grant={"id": "g", "run_id": "r", "decided_by": "bob"}, prepared_by="alice", approved_by="bob", by="t")


def test_closed_period_bill_fails_before_anyone_signs(released):
    run_id = released.runner.submit("purchase-to-pay", {"record": {**SMALL_BILL, "date": f"{date.today().year - 1}-12-15"}}, owner="alice")
    state = released.runner.load(run_id)
    assert state["status"] == "failed"
    assert not any(e["type"] == "gate.decided" for e in released.runner.track(run_id))


def test_journal_reversal_is_a_gated_mirror_posting(released):
    run_id = released.runner.submit("purchase-to-pay", {"record": dated(SMALL_BILL)}, owner="alice")
    journal = next(iter(released.kernel.journals().values()))
    rev = released.runner.submit("journal-reversal", {"record": {"journal": journal["locator"], "reason": "duplicate supplier invoice"}}, owner="alice")
    state = released.runner.load(rev)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["bob"]
    with pytest.raises(RunError, match="segregation|not an assignee"):
        released.runner.decide(rev, "alice", "approve")
    released.runner.decide(rev, "bob", "approve", meaning="reversal approved")
    journals = list(released.kernel.journals().values())
    assert len(journals) == 2
    reversal = next(j for j in journals if j["id"] != journal["id"])
    assert reversal["lines"][0]["credit"] == journal["lines"][0]["debit"] and reversal["lines"][1]["debit"] == journal["lines"][1]["credit"]
    assert any(l["rel"] == "supersedes" for l in released.kernel.get(reversal["locator"])["links"])
    again = released.runner.submit("journal-reversal", {"record": {"journal": journal["locator"], "reason": "twice"}}, owner="alice")
    released.runner.decide(again, "bob", "approve", meaning="oops")
    assert released.runner.load(again)["status"] == "failed", "a journal is reversed once"


# ---------------------------------------------------------------- inventory: receipts
def test_partial_and_over_receipts(released):
    k = released.kernel
    item = _item(k, "P-1", unit_cost=2.0)
    pr = released.runner.submit("purchase-request", {"record": {"title": "ten", "item": item["locator"], "quantity": 10}}, owner="alice")
    po = k.get(k.get(released.runner.load(pr)["record"])["fields"]["po"])
    own = released.runner.submit("goods-receipt", {"record": {"po": po["locator"], "lines": [{"item": item["locator"], "quantity": 1}]}}, owner="dave")
    assert released.runner.load(own)["status"] == "failed", "the only buyer recorded it himself: segregation of duties leaves no approver"
    gr1 = released.runner.submit("goods-receipt", {"record": {"po": po["locator"], "lines": [{"item": item["locator"], "quantity": 4}]}}, owner="alice")
    with pytest.raises(RunError, match="not an assignee"):
        released.runner.decide(gr1, "alice", "approve")
    released.runner.decide(gr1, "dave", "approve", meaning="4 received")
    assert k.get(item["locator"])["fields"]["stock"] == 4
    assert k.get(po["locator"])["state"] == "issued", "a partial receipt leaves the order open"
    assert k.purchase_orders()[f"po:{po['number']}"]["received"] is False
    gr2 = released.runner.submit("goods-receipt", {"record": {"po": po["locator"], "lines": [{"item": item["locator"], "quantity": 7}]}}, owner="alice")
    assert released.runner.load(gr2)["status"] == "failed", "7 exceeds the 6 outstanding"
    gr3 = released.runner.submit("goods-receipt", {"record": {"po": po["locator"], "lines": [{"item": item["locator"], "quantity": -2}]}}, owner="alice")
    assert released.runner.load(gr3)["status"] == "failed"
    gr4 = released.runner.submit("goods-receipt", {"record": {"po": po["locator"], "lines": [{"item": item["locator"], "quantity": 6}]}}, owner="alice")
    released.runner.decide(gr4, "dave", "approve", meaning="rest received")
    assert k.get(item["locator"])["fields"]["stock"] == 10 and k.get(po["locator"])["state"] == "received"
    gr5 = released.runner.submit("goods-receipt", {"record": {"po": po["locator"], "lines": [{"item": item["locator"], "quantity": 1}]}}, owner="alice")
    assert released.runner.load(gr5)["status"] == "failed", "nothing outstanding"


def test_people_can_create_items_through_a_process(released):
    run_id = released.runner.submit("item-request", {"record": {"name": "M3 bolt", "mpn": "DIN933-M3"}}, owner="alice")
    state = released.runner.load(run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["dave"]
    released.runner.decide(run_id, "dave", "approve", meaning="item approved", fields={"supplier": "acme-metals", "unit_cost": 0.1})
    item = released.kernel.get(state["record"])
    assert item["state"] == "active" and item["fields"]["supplier"] == "acme-metals"


# ---------------------------------------------------------------- workflow controls
def test_hold_blocks_decisions_and_failed_runs_are_retryable(released):
    run_id = released.runner.submit("purchase-to-pay", {"record": dated(BIG_BILL)}, owner="alice")
    rec = released.runner.load(run_id)["record"]
    released.kernel.hold(rec, True, "legal hold", by="bob")
    with pytest.raises(RunError, match="on hold"):
        released.runner.decide(run_id, "bob", "approve")
    released.kernel.hold(rec, False, "", by="bob")
    assert released.runner.decide(run_id, "bob", "approve", meaning="ok")["status"] == "completed"
    bad = released.runner.submit("purchase-to-pay", {"record": {**SMALL_BILL, "date": "2000-01-01", "po_ref": "PO-1003"}}, owner="alice")
    with pytest.raises(RunError, match="not waiting"):
        released.runner.decide(bad, "dave", "approve")
    with pytest.raises(RunError, match="may not retry"):
        released.runner.retry(bad, "carol")
    released.kernel.set_fields(released.runner.load(bad)["record"], {"date": date.today().isoformat(), "po_ref": "PO-1003"}, by="alice")
    state = released.runner.retry(bad, "alice", note="fixed the date")
    assert state["status"] == "waiting" and any(e["type"] == "run.retried" for e in released.runner.track(bad))
    cancelled = released.runner.cancel(bad, "alice", reason="withdrawn")
    assert cancelled["status"] == "cancelled" and released.kernel.get(cancelled["record"])["state"] == "cancelled"


def test_decisions_pre_check_required_fields_at_every_gate(released):
    run_id = released.runner.submit("nonconformance", {"record": {"title": "Scratched housing", "description": "Cosmetic scratch on the housing, safety risk noted",
                                                                  "quantity": 3}}, owner="alice")
    state = released.runner.load(run_id)
    assert state["status"] == "waiting", "a safety NCR waits for a person"
    rec = released.kernel.get(state["record"])
    assert rec["fields"]["risk"] == "high", "an NCR is scored as an NCR even with a quantity"
    assignee = state["pending"]["assignees"][0]
    with pytest.raises(RunError, match="needs disposition"):
        released.runner.decide(run_id, assignee, "approve", meaning="ok")
    released.runner.decide(run_id, assignee, "approve", meaning="scrap it", fields={"disposition": "scrap", "disposition_rationale": "beyond repair"})
    assert released.kernel.get(state["record"])["state"] == "closed"


def test_rejection_to_a_cog_step_returns_the_item_for_rework(hub, monkeypatch):
    monkeypatch.delenv("HUBPROTO_CAD_WORKER_URL", raising=False)
    release_all(hub)
    run_id = hub.runner.submit("design-release", {"record": {"title": "Widget", "kind": "mechanical", "user_needs": ["Must fit the slot."]}}, owner="alice")
    for req in hub.kernel.query(type_="requirement"):
        hub.runner.decide(hub.runner.run_for_record(req["locator"])["id"], "bob", "approve", meaning="ok",
                          fields={"acceptance_criteria": "fits", "verification_method": "inspection"})
    state = hub.runner.decide(run_id, "bob", "reject", meaning="needs a drawing")
    assert state["status"] == "waiting" and state["pending"]["kind"] == "resubmit" and state["pending"]["assignees"] == ["alice"]
    design = hub.kernel.get(state["record"])
    assert design["state"] == "returned" and "returned" in state["workflow"]["states"]
    assert any(m["to"] == "alice" and m["kind"] == "returned" for m in hub.inbox.all())
    with pytest.raises(RunError, match="not an assignee"):
        hub.runner.resubmit(run_id, "bob")
    state = hub.runner.resubmit(run_id, "alice", fields={"description": "drawing added"},
                                attachments=[{"name": "bracket.pdf", "content": "%PDF-1.4 drawing", "doc_class": "controlled", "doc_type": "drawing"}],
                                note="added the drawing")
    assert state["status"] == "waiting" and state["pending"]["kind"] == "decision" and state["pending"]["assignees"] == ["bob"]
    assert any(d["fields"]["doc_type"] == "drawing" for d in hub.dms.documents_of(design["locator"]))
    assert hub.runner.decide(run_id, "bob", "approve", meaning="released")["status"] == "completed"


def test_design_cannot_be_released_with_unaccepted_or_no_requirements(hub, monkeypatch):
    monkeypatch.delenv("HUBPROTO_CAD_WORKER_URL", raising=False)
    release_all(hub)
    none = hub.runner.submit("design-release", {"record": {"title": "No reqs", "kind": "mechanical"}}, owner="alice")
    with pytest.raises(RunError, match="traces to no requirement"):
        hub.runner.decide(none, "bob", "approve", meaning="go")
    some = hub.runner.submit("design-release", {"record": {"title": "Reqs", "kind": "mechanical", "user_needs": ["Must be blue."]}}, owner="alice")
    with pytest.raises(RunError, match="requirements not accepted"):
        hub.runner.decide(some, "bob", "approve", meaning="go")
    req = hub.kernel.query(type_="requirement")[-1]
    assert req["fields"]["statement"] == "Must be blue."
    hub.runner.decide(hub.runner.run_for_record(req["locator"])["id"], "bob", "approve", meaning="ok",
                      fields={"acceptance_criteria": "RAL 5010", "verification_method": "inspection"})
    design = hub.kernel.get(hub.runner.load(some)["record"])
    hub.runner.decide(some, "bob", "approve", meaning="released")
    design = hub.kernel.get(design["locator"])
    assert design["state"] == "released" and design["fields"]["requirements_accepted"] is True and design["fields"]["risk"] != "high"


def test_notify_only_escalation_adds_no_approver(released):
    run_id = released.runner.submit("document-control", {"record": {"title": "SOP-X", "change_type": "minor", "training_roles": ["staff"]}}, owner="alice")
    assert released.runner.load(run_id)["status"] == "completed"
    escalated = released.runner.tick(today="2099-01-01")
    trainings = {e["record"]: e for e in escalated if released.kernel.get(e["record"])["type"] == "training"}
    assert trainings and all(e["to"] == [] and "carol" in e["informed"] for e in trainings.values())
    for ref in trainings:
        assert released.kernel.get(ref)["assignees"] == [released.kernel.get(ref)["fields"]["person"]]


def test_child_runs_need_a_live_parent_and_a_related_owner(released):
    run_id = released.runner.submit("purchase-to-pay", {"record": dated(BIG_BILL)}, owner="alice")
    released.runner.decide(run_id, "bob", "approve", meaning="ok")
    with pytest.raises(RunError, match="only its owner or the person who approved"):
        released.runner.submit("task", {"record": {"title": "x"}}, owner="carol", parent_run=run_id)
    rejected = released.runner.submit("purchase-to-pay", {"record": dated({**BIG_BILL, "po_ref": "PO-1003", "amount": 12000})}, owner="alice")
    with pytest.raises(RunError, match="holds no grant"):
        released.runner.submit("task", {"record": {"title": "x"}}, owner="alice", parent_run=rejected)


# ---------------------------------------------------------------- kernel: capabilities, cross-process numbering
def test_scoped_kernel_enforces_reaches_and_grants(released):
    k = released.kernel
    rec = k.create("task", "Tasks", {"title": "t"}, by="alice")
    other = k.create("task", "Tasks", {"title": "u"}, by="alice")
    reader = ScopedKernel(k, cog_id="reader", reaches=["kernel:read"], run_id="r1", record=rec["locator"], grant=None)
    assert reader.get(rec["locator"])["number"] == rec["number"]
    with pytest.raises(KernelGuardError, match="reach"):
        reader.set_fields(rec["locator"], {"title": "x"}, by="reader")
    writer = ScopedKernel(k, cog_id="writer", reaches=["kernel:records"], run_id="r1", record=rec["locator"], grant=None)
    writer.set_fields(rec["locator"], {"title": "mine"}, by="writer")
    with pytest.raises(KernelGuardError, match="grant"):
        writer.set_fields(other["locator"], {"title": "not mine"}, by="writer")
    granted = ScopedKernel(k, cog_id="writer", reaches=["kernel:records"], run_id="r1", record=rec["locator"], grant={"id": "g", "run_id": "r1"})
    granted.set_fields(other["locator"], {"title": "with grant"}, by="writer")
    assert k.get(other["locator"])["fields"]["title"] == "with grant"


def test_two_processes_never_share_a_number(released, tmp_path):
    a = released.kernel
    b = Kernel(a.path)  # a second process: its own connection and cache
    r1 = a.create("task", "Tasks", {"title": "from a"}, by="alice")
    r2 = b.create("task", "Tasks", {"title": "from b"}, by="alice")
    r3 = a.create("task", "Tasks", {"title": "from a again"}, by="alice")
    assert len({r1["number"], r2["number"], r3["number"]}) == 3
    assert b.get(r3["locator"])["fields"]["title"] == "from a again", "the other process sees the write"
    assert a.get(r2["locator"]) is not None


def test_effective_documents_are_immutable_and_reviewers_come_from_the_release(released, tmp_path):
    run_id = released.runner.submit("document-control", {"record": {"title": "SOP-Z", "change_type": "minor"}}, owner="alice")
    doc = released.kernel.get(released.runner.load(run_id)["record"])
    assert doc["state"] == "effective"
    with pytest.raises(KernelGuardError, match="controlled"):
        released.dms.add_version(doc["locator"], b"tampered", by="carol")
    # A Frame edit that appoints a new policy owner cannot make that person a reviewer of its own proposal.
    org = released.frames.get("org-model").path
    org.write_text(org.read_text().replace("- dave (person): payables-reviewer, staff", "- dave (person): payables-reviewer, staff, policy-owner"))
    released.frames = type(released.frames)(released.frames.root)
    proposal = released.compile()
    assert "dave" not in proposal["reviewers"] and set(proposal["reviewers"]) == {"bob", "carol"}
