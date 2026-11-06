"""Regression tests for the core findings of the adversarial UX panel (feedback/ux/TRIAGE.md)."""

from datetime import date

import pytest

from conftest import BIG_BILL, SMALL_BILL, dated, edit_frame, release_all, seen
from hubproto.compile import CompileError
from hubproto.frames import FrameError, FrameStore
from hubproto.runner import RunError


# ------------------------------------------------------------------ signatures: meaning and what was seen
def test_a_person_must_state_the_meaning_and_quote_what_they_saw(released):
    run_id = released.runner.submit("purchase-to-pay", {"record": dated(BIG_BILL)}, owner="alice")
    state = released.runner.load(run_id)
    assert state["status"] == "waiting" and state["seen_digest"]
    with pytest.raises(RunError, match="quote what you saw"):
        released.runner.decide(run_id, "bob", "approve", meaning="ok")
    with pytest.raises(RunError, match="needs its meaning"):
        released.runner.decide(run_id, "bob", "approve", meaning="   ", payload_digest=seen(released.runner, run_id))
    assert released.runner.load(run_id)["status"] == "waiting"


def test_a_change_to_the_record_after_the_card_was_rendered_is_refused(released):
    run_id = released.runner.submit("purchase-to-pay", {"record": dated(BIG_BILL)}, owner="alice")
    digest = seen(released.runner, run_id)
    state = released.runner.load(run_id)
    # A credit note is attached after the approver opened the card: the digest no longer matches.
    released.dms.file_attachment(state["record"], "credit.txt", b"credit note: -500 USD", by="alice", doc_class="evidence") if hasattr(released.dms, "file_attachment") else \
        released.kernel.set_fields(state["record"], {"description": "amended"}, by="alice")
    assert seen(released.runner, run_id) != digest
    with pytest.raises(RunError, match="item changed since you opened it"):
        released.runner.decide(run_id, "bob", "approve", meaning="ok", payload_digest=digest)
    released.runner.decide(run_id, "bob", "approve", meaning="ok", payload_digest=seen(released.runner, run_id))
    assert released.runner.load(run_id)["status"] == "completed"


def test_a_signer_never_rewrites_what_a_person_declared_but_may_correct_a_cog_draft(released):
    k = released.kernel
    item = k.create("item", "Inventory/Items", {"name": "fixture", "mpn": "F-1", "manufacturer": "Acme", "stock": 0,
                                                "supplier": "acme-metals", "unit_cost": 3000.0}, by="alice")
    run_id = released.runner.submit("purchase-request", {"record": {"title": "fixtures", "item": item["locator"], "quantity": 4,
                                                                    "reason": "tooling for line 2"}}, owner="alice")
    state = released.runner.load(run_id)
    assert state["pending"]["assignees"] == ["bob"] and k.get(state["record"])["fields"]["amount"] == 12000.0
    allowed = released.runner.allowed_decision_fields(state)
    assert "reason" not in allowed, "alice declared the reason; bob may not rewrite it"
    assert "unit_cost" in allowed, "the price came from the item via a cog: a buyer may correct that draft"
    with pytest.raises(RunError, match="may not set reason"):
        released.runner.decide(run_id, "bob", "approve", meaning="ok", fields={"reason": "other"}, payload_digest=seen(released.runner, run_id))


def test_reject_needs_no_fields_and_writes_none(released):
    run_id = released.runner.submit("nonconformance", {"record": {"title": "Scratched housing", "description": "deep scratch, safety risk",
                                                                  "severity": "high", "source": "inspection"}}, owner="alice")
    state = released.runner.load(run_id)
    assert state["status"] == "waiting"
    who = state["pending"]["assignees"][0]
    released.runner.decide(run_id, who, "reject", meaning="not a nonconformance: cosmetic, within spec",
                           fields={"disposition": "scrap"}, payload_digest=seen(released.runner, run_id))
    rec = released.kernel.get(state["record"])
    assert rec["fields"].get("disposition") is None, "a rejection changes nothing on the record"
    assert rec["decisions"][-1]["verdict"] == "reject" and "cosmetic" in rec["decisions"][-1]["meaning"]


# ------------------------------------------------------------------ Start: validation before a record exists
@pytest.mark.parametrize("bad, message", [
    ({"amount": float("inf")}, "finite"), ({"amount": "12,000"}, "must be a number"), ({"amount": -5}, "negative"),
    ({"date": "06/11/2026"}, "YYYY-MM-DD"),
])
def test_bad_numbers_and_dates_are_refused_at_start(released, bad, message):
    before = len(released.kernel.query(type_="bill"))
    with pytest.raises(RunError, match=message):
        released.runner.submit("purchase-to-pay", {"record": {**dated(SMALL_BILL), **bad}}, owner="alice")
    assert len(released.kernel.query(type_="bill")) == before, "no junk record"


def test_owner_is_told_when_a_cog_decides(released):
    run_id = released.runner.submit("purchase-to-pay", {"record": dated(SMALL_BILL)}, owner="alice")
    assert released.runner.load(run_id)["status"] == "completed"
    notes = [m for m in released.inbox.for_principal("alice") if m.get("kind") == "auto-decided"]
    assert notes and "approved automatically by ap-reviewer" in notes[-1]["subject"]


def test_retry_fixes_the_record_on_the_way(released):
    run_id = released.runner.submit("purchase-to-pay", {"record": {**SMALL_BILL, "date": f"{date.today().year - 1}-12-15"}}, owner="alice")
    state = released.runner.load(run_id)
    assert state["status"] == "failed"
    with pytest.raises(RunError, match="may not retry"):
        released.runner.retry(run_id, "carol")  # neither the owner nor an approver of bills
    released.runner.retry(run_id, "alice", note="period corrected", fields={"date": date.today().isoformat()})
    assert released.runner.load(run_id)["status"] == "completed"
    assert released.kernel.get(state["record"])["fields"]["date"] == date.today().isoformat()


# ------------------------------------------------------------------ policy lifecycle
def test_only_a_policy_owner_releases_and_only_against_the_release_in_force(hub):
    release_all(hub)
    edit_frame(hub, "finance-policy", "Finance clerks may create bills in Finance/Payables.",
               "Finance clerks may create bills in Finance/Payables.\nStaff may create bills in Finance/Payables.", bump="1.1")
    stale = hub.compile()
    for r in ("bob", "carol"):
        hub.review(stale["id"], r, approve_all=True)
    with pytest.raises(CompileError, match="not a policy owner"):
        hub.release(stale["id"], "dave")
    # A second proposal is released first; the stale one is then refused instead of silently reverting it.
    edit_frame(hub, "finance-policy", "Staff may create bills in Finance/Payables.", "Staff may create bills in Finance/Payables.\nStaff may read bills in Finance/Payables.", bump="1.2")
    newer = hub.compile()
    for r in ("bob", "carol"):
        hub.review(newer["id"], r, approve_all=True)
    hub.release(newer["id"], "bob")
    with pytest.raises(CompileError, match="superseded|is in force; compile again"):
        hub.release(stale["id"], "bob")
    with pytest.raises(CompileError, match="is released"):
        hub.release(newer["id"], "bob")


def test_re_saving_a_release_keeps_its_history(hub):
    first = release_all(hub)
    proposal = hub.compile()  # no change: same digest
    assert not proposal["changes"]
    meta = hub.release(proposal["id"], "carol")
    assert meta["digest"] == first["digest"] and meta["released_by"] == first["released_by"] == "bob"
    assert meta["effective_from"] == first["effective_from"] and meta["parent"] == first["parent"]


def test_a_mistyped_heading_or_rule_blocks_the_release(hub):
    release_all(hub)
    edit_frame(hub, "finance-policy", "## Permissions", "## Permisions", bump="1.1")
    proposal = hub.compile()
    errors = [f for f in proposal["findings"] if f["severity"] == "error" and f["guard"] == "grammar"]
    assert errors and "did you mean '## Permissions'" in errors[0]["detail"]
    with pytest.raises(CompileError, match="not ready"):
        hub.release(proposal["id"], "bob")
    edit_frame(hub, "finance-policy", "## Permisions", "## Permissions")
    edit_frame(hub, "finance-policy", "Finance clerks may create bills in Finance/Payables.", "Finance clerks can create bills in Finance/Payables", bump="1.2")
    proposal = hub.compile()
    errors = [f for f in proposal["findings"] if f["severity"] == "error" and f["guard"] == "grammar"]
    assert errors and "looks like a rule but does not parse" in errors[0]["detail"] and errors[0]["frame"] == "finance-policy"


def test_citations_use_file_line_numbers(released):
    bundle = released.releases.bundle(released.releases.current()["digest"])
    rule = next(r for r in bundle["permissions"] if "bill" in r["object"] and r.get("source"))
    frame = released.frames.get(rule["source"]["frame"])
    n = rule["source"]["line"]
    assert frame.path.read_text().splitlines()[n - 1].strip() == rule["source"]["text"], "the cited line is the editor's line"


def test_a_broken_frame_names_its_file(tmp_path):
    (tmp_path / "bad.frame.md").write_text("name: no frontmatter fence\n")
    with pytest.raises(FrameError, match="bad.frame.md.*must start with"):
        FrameStore(tmp_path)


def test_why_accepts_the_plural_noun(released):
    assert released.why("alice", "create", "bills", "Finance/Payables")["allowed"]
    assert released.why("alice", "create", "bill", "Finance/Payables")["allowed"]


# ------------------------------------------------------------------ proposals: review gate, carry-forward, supersession
def test_review_needs_a_policy_owner_and_an_open_proposal(hub):
    release_all(hub)
    edit_frame(hub, "finance-policy", "Finance clerks may create bills in Finance/Payables.",
               "Finance clerks may create bills in Finance/Payables.\nStaff may create bills in Finance/Payables.", bump="1.1")
    proposal = hub.compile()
    with pytest.raises(CompileError, match="not a policy owner"):
        hub.review(proposal["id"], "dave", approve_all=True)
    hub.review(proposal["id"], "bob", approve_all=True)
    newer = hub.compile()  # same base: supersedes, carries bob's approvals forward
    assert hub.proposals.load(proposal["id"])["status"] == "superseded"
    assert newer["number"] == proposal["number"] + 1
    carried = [c for c in newer["changes"] if c["decisions"]]
    assert carried and all(d["by"] == "bob" and d.get("carried_from") == proposal["id"] for c in carried for d in c["decisions"])
    with pytest.raises(CompileError, match="is superseded"):
        hub.review(proposal["id"], "carol", approve_all=True)
    hub.review(newer["id"], "carol", approve_all=True)
    hub.release(newer["id"], "bob")


def test_citations_name_the_file(released):
    d = released.why("alice", "create", "bill", "Finance/Payables")
    assert "(" in d["why"] and ".frame.md)" in d["why"]


def test_a_broken_frame_stops_only_the_verbs_that_read_frames(tmp_path):
    from hubproto.app import Hub

    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "bad.frame.md").write_text("name: no fence\n")
    hub = Hub(tmp_path)
    assert hub.kernel.query() == []  # the kernel works
    with pytest.raises(FrameError, match="bad.frame.md"):
        hub.frames
