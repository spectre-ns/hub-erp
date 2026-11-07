"""Ops under a release: Gates, grants, Tracks, cogs as team members, inbox."""

import pytest

from conftest import BIG_BILL, MID_BILL, SMALL_BILL, dated, edit_frame, release_all, seen
from hubproto.runner import RunError


def events(hub, run_id):
    return [e["type"] for e in hub.runner.track(run_id)]


def test_why_explains_with_the_policy_sentence(released):
    d = released.why("alice", "create", "bill", "Finance/Payables")
    assert d["allowed"] and "finance-policy line" in d["why"]
    d = released.why("carol", "create", "bill", "Finance/Payables")
    assert not d["allowed"] and "Quality managers may not create bills" in d["why"]
    d = released.why("alice", "read", "*", "Finance/Payables")
    assert d["allowed"], "a folder permission applies to its subfolders"
    d = released.why("dave", "create", "document", "Quality/Controlled Documents")
    assert not d["allowed"] and "no rule" in d["why"]


def test_small_clean_bill_is_approved_by_the_cog_and_posted(released):
    run_id = released.runner.submit("purchase-to-pay", {"record": dated(SMALL_BILL)}, owner="alice")
    state = released.runner.load(run_id)
    assert state["status"] == "completed", events(released, run_id)
    decision = state["steps"]["score-the-risk"]["decision"]
    assert decision["by"] == "ap-reviewer" and decision["by_type"] == "cog" and decision["verdict"] == "approve"
    assert "ap-reviewer cog may approve" in decision["delegating_rule"]["text"]
    journals = released.kernel.journals()
    assert len(journals) == 1
    j = next(iter(journals.values()))
    assert j["prepared_by"] == "alice" and j["approved_by"] == "ap-reviewer"
    assert j["release"] == state["release"] and j["run_id"] == run_id
    assert "grant.issued" in events(released, run_id)


def test_big_bill_waits_for_the_controller(released):
    run_id = released.runner.submit("purchase-to-pay", {"record": dated(BIG_BILL)}, owner="alice")
    state = released.runner.load(run_id)
    assert state["status"] == "waiting"
    assert state["pending"]["assignees"] == ["bob"]
    assert "over 10,000 USD" in state["pending"]["route_rule"]["text"]
    assert any(m["to"] == "bob" and m["kind"] == "approval" for m in released.inbox.all())
    with pytest.raises(RunError, match="not an assignee"):
        released.runner.decide(run_id, "dave", "approve", meaning="ok", payload_digest=seen(released.runner, run_id))
    with pytest.raises(RunError, match="item changed"):
        released.runner.decide(run_id, "bob", "approve", payload_digest="deadbeef", meaning="ok")
    state = released.runner.decide(run_id, "bob", "approve", meaning="approved for posting", payload_digest=seen(released.runner, run_id))
    assert state["status"] == "completed"
    assert released.kernel.journals()


def test_mid_bill_routes_to_the_human_default_not_the_cog(released):
    run_id = released.runner.submit("purchase-to-pay", {"record": dated(MID_BILL)}, owner="alice")
    state = released.runner.load(run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["dave"]
    # PO-1003 is not received, so a warning problem is recorded but the run goes on to the Gate.
    problems = state["steps"]["match-the-bill-to-purchase-orders"]["envelope"]["problems"]
    assert any(p["check"] == "receipt" for p in problems)


def test_cog_outside_criteria_escalates(released):
    # Small bill, approved supplier, but against a PO that is not received and priced differently:
    # the match is outside tolerance (warnings, not errors), so the cog must escalate to a person.
    bill = dated({**SMALL_BILL, "po_ref": "PO-1003"})
    run_id = released.runner.submit("purchase-to-pay", {"record": bill}, owner="alice")
    state = released.runner.load(run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["dave"]
    review = next(e for e in released.runner.track(run_id) if e["type"] == "gate.cog_review")
    assert review["verdict"] == "escalate" and any("the three-way match is within tolerance" in r for r in review["reasons"])


def test_bill_against_another_suppliers_po_fails_at_the_default_gate(released):
    bill = dated({**SMALL_BILL, "supplier": "newco-tools", "po_ref": "PO-1001"})
    run_id = released.runner.submit("purchase-to-pay", {"record": bill}, owner="alice")
    state = released.runner.load(run_id)
    assert state["status"] == "failed"
    result = next(e for e in released.runner.track(run_id) if e["type"] == "step.result" and e["gate"] == "fail")
    assert any(p["check"] == "po-supplier" and p["severity"] == "error" for p in result["problems"])
    assert not released.kernel.journals()


def test_segregation_of_duties_binds_people_and_cogs(released):
    # alice holds no approve permission, but dave as preparer could try to approve his own bill.
    run_id = released.runner.submit("purchase-to-pay", {"record": dated(MID_BILL)}, owner="alice")
    with pytest.raises(RunError, match="not an assignee"):
        released.runner.decide(run_id, "alice", "approve", meaning="ok", payload_digest=seen(released.runner, run_id))
    # The cog files a small bill itself: it is routed to itself, skipped for SoD, and a person decides.
    run_id = released.runner.submit("purchase-to-pay", {"record": dated(SMALL_BILL)}, owner="ap-reviewer")
    state = released.runner.load(run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["dave"]
    skipped = next(e for e in released.runner.track(run_id) if e["type"] == "gate.skipped")
    assert "segregation of duties" in skipped["reason"]


def test_rejection_is_final_and_nothing_posts(released):
    run_id = released.runner.submit("purchase-to-pay", {"record": dated(BIG_BILL)}, owner="alice")
    state = released.runner.decide(run_id, "bob", "reject", meaning="duplicate invoice", payload_digest=seen(released.runner, run_id))
    assert state["status"] == "rejected"
    assert not released.kernel.journals()
    with pytest.raises(RunError, match="not waiting"):
        released.runner.decide(run_id, "bob", "approve", meaning="ok", payload_digest=seen(released.runner, run_id))


def test_run_keeps_its_release_across_a_cutover(released):
    first = released.releases.current()["digest"]
    run_id = released.runner.submit("purchase-to-pay", {"record": dated(BIG_BILL)}, owner="alice")
    edit_frame(released, "finance-policy", "Approvals in purchase-to-pay over 10,000 USD go to the controller.",
               "Approvals in purchase-to-pay over 10,000 USD go to the payables reviewer.", bump="1.3.0")
    second = release_all(released)["digest"]
    assert released.releases.current()["digest"] == second != first
    state = released.runner.decide(run_id, "bob", "approve", meaning="ok", payload_digest=seen(released.runner, run_id))
    assert state["status"] == "completed" and state["release"] == first
    assert next(iter(released.kernel.journals().values()))["release"] == first
    # A new run uses the new routing: the big bill now goes to dave (against a fresh order; PO-1002 is fully billed).
    released.kernel.append("po.issued", "po:PO-1004", {"supplier": "acme-metals", "amount": 12000.0, "currency": "USD",
                                                       "description": "More fixtures", "received": True}, by="seed")
    run2 = released.runner.submit("purchase-to-pay", {"record": dated({**BIG_BILL, "po_ref": "PO-1004"})}, owner="alice")
    assert released.runner.load(run2)["pending"]["assignees"] == ["dave"]


def test_posting_without_a_grant_is_refused(released):
    from hubproto.cogs import LedgerPoster, RunContext

    bundle = released.current_bundle()
    ctx = RunContext(run_id="r1", release="x", bundle=bundle, owner="alice", kernel=released.kernel, grant=None)
    env = LedgerPoster().invoke("run", {"prior": dated(SMALL_BILL)}, ctx)
    assert env["ok"] is False and env["error"]["code"] == "no-grant"


def test_unbalanced_journal_is_refused_by_the_kernel(released):
    from hubproto.kernel import KernelGuardError

    grant = {"id": "g", "run_id": "r1", "decided_by": "bob"}
    with pytest.raises(KernelGuardError, match="balanced-journal"):
        released.kernel.post_journal({"date": dated(SMALL_BILL)["date"], "lines": [{"account": "a", "debit": 5, "credit": 0}]},
                                     run_id="r1", release="x", grant=grant, prepared_by="alice", approved_by="bob", by="t")
    with pytest.raises(KernelGuardError, match="period-open"):
        released.kernel.post_journal({"date": "2000-01-01", "lines": [{"account": "a", "debit": 5, "credit": 0},
                                                                      {"account": "b", "debit": 0, "credit": 5}]},
                                     run_id="r1", release="x", grant=grant, prepared_by="alice", approved_by="bob", by="t")


def test_document_control_minor_change_approved_by_doc_reviewer(released):
    # A new document is major whatever its author claims: a person approves it (review governance-1).
    run_id = released.runner.submit("document-control", {"record": {"title": "SOP-7 Calibration", "change_type": "minor"}},
                                    owner="alice")
    state = released.runner.load(run_id)
    assert state["status"] == "waiting" and set(state["pending"]["assignees"]) == {"bob", "carol"}
    released.runner.decide(run_id, "bob", "approve", meaning="fit for use", payload_digest=state["seen_digest"])
    first = released.kernel.get(released.runner.load(run_id)["record"])
    assert first["state"] == "effective" and first["fields"]["approved_by"] == "bob"
    # A minor revision of it, by its author: the doc-reviewer cog approves within its criteria, and the revision
    # (named when it was submitted) supersedes the one in force.
    run_id = released.runner.submit("document-control", {"record": {"title": "SOP-7 Calibration", "change_type": "minor"}},
                                    owner="alice")
    state = released.runner.load(run_id)
    assert state["status"] == "completed"
    rev = released.kernel.get(state["record"])
    assert rev["fields"]["revises"] == first["number"] and rev["fields"]["approved_by"] == "doc-reviewer"
    assert released.kernel.get(first["locator"])["state"] == "obsolete"


def test_cog_answers_dm_and_files_from_an_attachment(released):
    reply = released.send("alice", "ap-reviewer", "status", "what is the status?")
    assert reply["kind"] == "reply" and "Waiting for you" in reply["body"]
    reply = released.send("alice", "ap-reviewer", "invoice", "please file this",
                          attachments=[{"name": "acme.json", "content": dated(SMALL_BILL)}], channel="email")
    assert reply["kind"] == "reply" and reply["run_id"]
    state = released.runner.load(reply["run_id"])
    assert state["owner"] == "ap-reviewer"
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["dave"]
    emls = list(released.inbox.outbox.glob("*.eml"))
    assert len(emls) == 2, "the email and the reply were written to the outbox"
    assert any(m["to"] == "alice" and m["kind"] == "reply" for m in released.inbox.for_principal("alice"))


def test_mail_in_roundtrip(released, tmp_path):
    from hubproto.inbox import parse_eml

    eml = tmp_path / "in.eml"
    eml.write_text("From: alice@hub.local\nTo: ap-reviewer@hub.local\nSubject: bill\n\nPlease file the attached.\n\n"
                   "--- attachment: acme.json\n" + __import__("json").dumps(dated(SMALL_BILL)) + "\n")
    msg = parse_eml(eml.read_text())
    assert msg["to"] == "ap-reviewer" and msg["attachments"][0]["name"] == "acme.json"
    reply = released.send(msg["from"], msg["to"], msg["subject"], msg["body"], channel="email", attachments=msg["attachments"])
    assert reply["run_id"]


def test_track_pins_everything(released):
    run_id = released.runner.submit("purchase-to-pay", {"record": dated(SMALL_BILL)}, owner="alice")
    track = released.runner.track(run_id)
    submitted = track[0]
    assert submitted["type"] == "run.submitted" and submitted["release_digest"] and submitted["manifest_digest"]
    starts = [e for e in track if e["type"] == "step.started"]
    assert all(e["cog_version"] for e in starts)
    seqs = [e["seq"] for e in track]
    assert seqs == list(range(1, len(track) + 1))
    assert track[-1]["type"] == "run.completed"
