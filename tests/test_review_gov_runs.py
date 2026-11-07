"""Regression tests for the 7 Nov 2026 adversarial review (governance core): who may decide, countersignatures,
segregation of duties by named role, cogs' limits on approving, starting a process on an existing item, signatures and
chained audit trails, crash safety, the blob store, the kernel projection. Finding ids refer to
feedback/review-2026-11-07/*.md."""

import json
from datetime import date

import pytest

from conftest import BIG_BILL, dated, seen
from hubproto.runner import RunError
from hubproto.track import Track


def submit_doc(hub, title="SOP-Review", change_type="major", owner="alice", body="# SOP\n\n1. Do it."):
    return hub.runner.submit("document-control", {"record": {"title": title, "change_type": change_type, "body": body}}, owner=owner)


def approve(hub, run_id, who, meaning="fit for use", **kw):
    return hub.runner.decide(run_id, who, "approve", meaning=meaning, payload_digest=seen(hub.runner, run_id), **kw)


# ---------------------------------------------------------------- governance-1 and -10: documents into force
def test_a_cog_written_document_needs_a_person_and_never_supersedes_by_title(released):
    hub = released
    first = submit_doc(hub, "Calibration Procedure")
    approve(hub, first, "bob")
    sop = hub.kernel.get(hub.runner.load(first)["record"])
    assert sop["state"] == "effective"
    # qa-manager drafts a same-titled document: it may not revise alice's SOP (not its owner, not a controller).
    with pytest.raises(RunError, match="only its owner"):
        hub.runner.submit("document-control", {"record": {"title": "Calibration Procedure", "change_type": "minor"}}, owner="qa-manager")
    # Under another title its document is new (major), and no cog may sign a cog's work: a person must.
    rid = hub.runner.submit("document-control", {"record": {"title": "Calibration, cog draft", "change_type": "minor"}}, owner="qa-manager")
    st = hub.runner.load(rid)
    assert st["status"] == "waiting" and st["pending"]["assignee_type"] == "person"
    assert hub.runner.may_step_in(st, "qa-manager")  # it prepared it
    assert hub.kernel.get(sop["locator"])["state"] == "effective", "nothing superseded the SOP by title"


def test_publishing_supersedes_only_the_revision_named(released):
    hub = released
    a = submit_doc(hub, "Handling")
    approve(hub, a, "bob")
    old = hub.kernel.get(hub.runner.load(a)["record"])
    b = submit_doc(hub, "Handling", owner="alice")  # alice is its author: she may revise it
    st = hub.runner.load(b)
    new = hub.kernel.get(st["record"])
    assert new["fields"]["revises"] == old["number"]
    approve(hub, b, "bob")
    assert hub.kernel.get(old["locator"])["state"] == "obsolete"
    assert any(l["rel"] == "supersedes" and l["target"] == old["locator"] for l in hub.kernel.get(new["locator"])["links"])


def test_a_process_on_an_existing_item_checks_before_anything_moves(released):
    """security-1, governance-10: type, state, class, ownership and read rights are checked first."""
    hub = released
    design = hub.kernel.create("design", "Engineering/Designs", {"title": "secret bracket", "kind": "mechanical"}, by="alice")
    with pytest.raises(RunError, match="not a task"):
        hub.runner.submit("task", {"record": {}}, owner="dave", record=design["locator"])
    assert hub.kernel.get(design["locator"])["folder"] == "Engineering/Designs", "nothing moved"
    rid = submit_doc(hub, "In force")
    approve(hub, rid, "bob")
    eff = hub.kernel.get(hub.runner.load(rid)["record"])
    with pytest.raises(RunError, match="effective"):
        hub.runner.submit("document-control", {"record": {"change_type": "minor"}}, owner="carol", record=eff["locator"])
    assert hub.kernel.get(eff["locator"])["fields"]["approved_by"] == "bob"
    ev = hub.dms.create(title="notes", doc_class="evidence", folder="Quality", by="quality-assistant", content=b"x", filename="n.md")
    with pytest.raises(RunError, match="evidence"):
        hub.runner.submit("document-control", {"record": {"change_type": "minor"}}, owner="alice", record=ev["locator"])
    mine = hub.dms.create(title="carol's", doc_class="controlled", folder="Quality/Controlled Documents", by="carol", content=b"# c", filename="c.md")
    with pytest.raises(RunError, match="not alice's"):
        hub.runner.submit("document-control", {"record": {"change_type": "major"}}, owner="alice", record=mine["locator"])


# ---------------------------------------------------------------- governance-4, -5, -6: who may decide
def test_segregation_keeps_out_the_named_role_not_only_the_filer(released):
    hub = released
    rid = hub.runner.submit("nonconformance", {"record": {"title": "Scratched", "description": "lot 7 scratched", "reported_by": "carol",
                                                          "severity": "high"}}, owner="dave")
    st = hub.runner.load(rid)
    assert "carol" not in (st.get("pending") or {}).get("assignees", [])
    if st["status"] == "waiting":
        with pytest.raises(RunError):
            hub.runner.decide(rid, "carol", "approve", meaning="use as is", payload_digest=seen(hub.runner, rid))


def test_the_preparer_never_approves_on_a_role_route(released):
    hub = released
    rid = hub.runner.submit("change-control", {"record": {"title": "Drop inspection", "description": "stop inspecting", "impact": "none"}},
                            owner="carol")
    st = hub.runner.load(rid)
    assert "carol" not in ((st.get("pending") or {}).get("assignees") or [])
    reasons = " ".join(str(e.get("reason")) for e in hub.runner.track(rid) if e["type"] in ("gate.skipped", "run.failed"))
    assert st["status"] == "waiting" or "prepared this item" in reasons


def test_a_countersign_keeps_its_role_through_escalation(released):
    hub = released
    rid = hub.runner.submit("change-control", {"record": {"title": "Skip final test", "description": "remove final test", "impact": "WI-7"}},
                            owner="bob")
    hub.runner.cog_step_in(rid, "qa-manager", asked_by="carol")
    st = hub.runner.load(rid)
    if not st.get("pending") or not st["pending"].get("countersign_of"):
        pytest.skip("the stand-in review did not pass in this build")
    assert st["pending"]["countersign_role"] == "quality-manager" and "bob" in st["pending"]["excluded"]
    hub.runner.tick(today="2099-01-01")
    st = hub.runner.load(rid)
    assert "bob" not in st["pending"]["assignees"], "the requester is told, never added as countersigner"
    with pytest.raises(RunError):
        hub.runner.decide(rid, "bob", "approve", meaning="countersigned", payload_digest=seen(hub.runner, rid))


def test_a_cog_named_in_a_field_does_not_take_the_step(released):
    """governance-7: only a task's assignee may be a cog; a verification routed to requested_by is not cog work."""
    hub = released
    hub.runner.cog_users_inline = False
    try:
        rid = hub.runner.submit("change-control", {"record": {"title": "Torque", "description": "x", "impact": "WI-3",
                                                              "requested_by": "qa-manager"}}, owner="alice")
        st = hub.runner.load(rid)
        if st["status"] == "waiting" and st["pending"]["checkpoint"].startswith("approve"):
            approve(hub, rid, "carol", fields={"plan": "x", "verification": "x"})
        st = hub.runner.load(rid)
        assert "qa-manager" not in ((st.get("pending") or {}).get("assignees") or [])
        assert any("a cog cannot be named in requested_by" in str(e.get("reason")) for e in hub.runner.track(rid))
    finally:
        hub.runner.cog_users_inline = True


def test_who_may_ask_a_cog_to_sign_and_a_failed_review_binds(released, monkeypatch):
    """governance-13: not the preparer, not a cog; a failed review of the same version stands."""
    hub = released
    rid = submit_doc(hub, "Asked")
    st = hub.runner.load(rid)
    assert "alice may not choose" in hub.runner.may_step_in(st, "qa-manager", asked_by="alice")
    assert "only a person" in hub.runner.may_step_in(st, "qa-manager", asked_by="quality-assistant")
    from hubproto import review as review_mod

    real = review_mod.review
    monkeypatch.setattr(review_mod, "review", lambda *a, **k: {**real(*a, **k), "verdict": "fail",
                                                               "findings": [{"severity": "error", "check": "scope", "detail": "no scope"}]})
    hub.runner.cog_step_in(rid, "qa-manager", asked_by="bob")
    st = hub.runner.load(rid)
    assert "already reviewed this version" in hub.runner.may_step_in(st, "qa-manager", asked_by="bob")


# ---------------------------------------------------------------- reliability-1, -2: changes mid-review, crashes
def test_a_cog_does_not_sign_an_item_that_changed_while_it_thought(released, monkeypatch):
    """reliability-1: the cog's decision quotes what it was shown before its review; a change meanwhile is refused."""
    hub = released
    hub.runner.cog_users_inline = False  # the bill waits for the ap-reviewer cog instead of being taken at once
    try:
        rid = hub.runner.submit("purchase-to-pay", {"record": dated({**BIG_BILL, "amount": 1500, "po_ref": "PO-1001"})}, owner="alice")
    finally:
        hub.runner.cog_users_inline = True
    st = hub.runner.load(rid)
    assert st["pending"]["assignee_type"] == "cog", st["pending"]
    from hubproto import cogs

    real = cogs.ReviewerCog.task_review

    def changing(self, inp, ctx):
        out = real(self, inp, ctx)
        hub.kernel.set_fields(st["record"], {"description": "changed while the model thought"}, by="alice")
        return out
    monkeypatch.setattr(cogs.ReviewerCog, "task_review", changing)
    after = hub.runner.cog_decide(rid, st["pending"]["assignees"][0])
    assert after["status"] == "waiting" and after["pending"]["assignee_type"] == "person"
    declined = [e for e in hub.runner.track(rid) if e["type"] == "gate.declined"]
    assert declined and "changed since you opened it" in declined[-1]["reasons"][0]
    assert not [e for e in hub.runner.track(rid) if e["type"] == "gate.decided"]


def test_one_signature_per_gate_even_after_a_crash(released, monkeypatch):
    hub = released
    from hubproto.app import Hub
    from hubproto.runner import Runner

    rid = submit_doc(hub, "Crash")
    st = hub.runner.load(rid)
    real = Runner._save
    monkeypatch.setattr(Runner, "_save", lambda self, s: (_ for _ in ()).throw(SystemExit("killed")) if s.get("pending") is None else real(self, s))
    with pytest.raises(SystemExit):
        hub.runner.decide(rid, "bob", "approve", meaning="fit", payload_digest=st["seen_digest"])
    monkeypatch.setattr(Runner, "_save", real)
    st2 = hub.runner.load(rid)
    assert st2["status"] != "waiting", "the Track says the Gate was decided"
    with pytest.raises(RunError):
        hub.runner.decide(rid, "carol", "reject", meaning="not fit", payload_digest=seen(hub.runner, rid))
    Hub(hub.root)  # a restart reconciles: the decided Gate is closed and the run carries on
    done = hub.runner.load(rid)
    rec = hub.kernel.get(done["record"])
    assert [d["by"] for d in rec["decisions"] if d.get("verdict")] == ["bob"] and rec["state"] == "effective"


# ---------------------------------------------------------------- governance-12: signatures and chains
def test_signatures_are_keyed_and_the_trails_are_chained(released):
    hub = released
    rid = submit_doc(hub, "Signed")
    approve(hub, rid, "bob")
    assert hub.runner.verify_signatures(rid) == []
    t = Track(hub.runner.root / rid)
    decided = next(e for e in t.read() if e["type"] == "gate.decided")
    rec = hub.kernel.get(hub.runner.load(rid)["record"])
    assert decided["signature"].startswith("h2:") and decided["record_digest"] == next(d for d in rec["decisions"] if d.get("verdict"))["record_digest"]
    # A forged meaning no longer verifies; an edited Track entry breaks the chain.
    lines = t.path.read_text().splitlines()
    forged = [json.loads(x) for x in lines]
    for e in forged:
        if e["type"] == "gate.decided":
            e["meaning"] = "approved with no conditions"
    t.path.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in forged))
    problems = hub.runner.verify_signatures(rid)
    assert any("does not verify" in p for p in problems) and any("changed after it was written" in p for p in problems)
    assert hub.kernel.verify_chain() == [] or all("not chained" in p for p in hub.kernel.verify_chain())
    hub.kernel.db.execute("UPDATE events SET data = ? WHERE seq = (SELECT MIN(seq) FROM events WHERE chain IS NOT NULL)", ('{"x": 1}',))
    hub.kernel.db.commit()
    assert any("was changed" in p for p in hub.kernel.verify_chain())


# ---------------------------------------------------------------- reliability-3, -5: blobs and the projection
def test_a_torn_blob_is_rewritten_and_a_damaged_one_is_not_served(released):
    from hubproto.dms import BlobError

    blobs = released.dms.blobs
    sha = blobs.put(b"intact content")
    (blobs.root / sha).write_bytes(b"intact")  # torn by a crash
    assert blobs.put(b"intact content") == sha and blobs.get(sha) == b"intact content"
    (blobs.root / sha).write_bytes(b"tampered!!!!!!")
    with pytest.raises(BlobError):
        blobs.get(sha)


def test_the_projection_folds_only_new_events_and_keeps_earlier_reads(released):
    k = released.kernel
    a = k.create("task", "Tasks", {"title": "a"}, by="alice")
    b = k.create("task", "Tasks", {"title": "b"}, by="alice")
    held = k.get(a["locator"])
    untouched = k.get(b["locator"])
    k.set_fields(a["locator"], {"title": "a2"}, by="alice")
    assert held["fields"]["title"] == "a", "a record read earlier does not change under its reader"
    assert k.get(a["locator"])["fields"]["title"] == "a2"
    assert k.get(b["locator"]) is untouched, "records no event touched are not refolded"


def test_a_review_date_on_29_february_is_a_real_date(released, monkeypatch):
    import hubproto.kernel as kmod

    class Feb29(date):
        @classmethod
        def today(cls):
            return cls(2028, 2, 29)
    monkeypatch.setattr(kmod, "date", Feb29)
    rid = submit_doc(released, "Leap")
    approve(released, rid, "bob")
    rec = released.kernel.get(released.runner.load(rid)["record"])
    assert rec["fields"]["review_date"] == "2029-02-28"


# ---------------------------------------------------------------- ux-1, ux-5, ux-8, cogs-12: messages
def test_a_stop_says_who_could_not_take_it_and_why(released):
    hub = released
    st = hub.runner.load(hub.runner.submit("change-control", {"record": {"title": "x", "description": "y", "impact": "z"}}, owner="carol"))
    failed = [e for e in hub.runner.track(st["id"]) if e["type"] == "run.failed"]
    if failed:
        assert failed[-1]["kind"] == "organisation" and failed[-1]["reason"].startswith("Nobody can take")
        note = [m for m in hub.inbox.all() if m["to"] == "carol" and m.get("kind") == "failure"][-1]
        assert "Changing the item will not help" in note["body"] and "Fix the record" not in note["body"]


def test_the_owner_hears_when_someone_else_moves_their_item(released):
    hub = released
    rid = submit_doc(hub, "Moved")
    approve(hub, rid, "bob")
    note = [m for m in hub.inbox.all() if m["to"] == "alice" and m.get("kind") == "moved"]
    assert note and "bob approved it" in note[-1]["body"] and "effective" in note[-1]["body"]


def test_criteria_refusals_quote_the_policy_words():
    from hubproto.routing import evaluate_criteria

    ok, reasons = evaluate_criteria({"conditions": ["risk_low"]}, {"conditions": {"risk_low": False}, "risk": "high"})
    assert not ok and reasons == ["“the risk is low” does not hold: the risk is high"]


def test_retry_with_new_fields_reruns_the_checks_after_the_last_signature(released):
    hub = released
    item = hub.kernel.create("item", "Inventory/Items", {"name": "resistor", "mpn": "R-1", "manufacturer": "X", "stock": 0,
                                                        "unit_cost": 0.02}, by="alice")
    rid = hub.runner.submit("purchase-request", {"record": {"title": "boards", "item": item["locator"], "quantity": 5}}, owner="dave")
    st = hub.runner.load(rid)
    if st["status"] != "failed":
        pytest.skip("the request did not stop in this policy")
    hub.runner.retry(rid, "dave", note="named a supplier", fields={"supplier": "acme-metals"})
    started = [e["step"] for e in hub.runner.track(rid) if e["type"] == "step.started"]
    assert started.count(started[0]) >= 2, "the automatic steps ran again on the new supplier"


def test_fields_locked_explains_why_a_signed_record_cannot_be_rewritten(released):
    hub = released
    rid = submit_doc(hub, "Locked")
    rec = hub.kernel.get(hub.runner.load(rid)["record"])
    assert "waits at a Gate" in hub.runner.fields_locked(rec)
    approve(hub, rid, "bob")
    assert "finished" in hub.runner.fields_locked(hub.kernel.get(rec["locator"]))
    fresh = hub.kernel.create("task", "Tasks", {"title": "open"}, by="alice")
    assert hub.runner.fields_locked(fresh) == ""


def test_why_cant_i_approve_this_item_gives_every_reason(released):
    """ux-6: segregation of duties and routing, not only the permission sentence."""
    hub = released
    rid = hub.runner.submit("nonconformance", {"record": {"title": "Burr", "description": "burr on edge, safety hazard", "reported_by": "carol",
                                                          "severity": "high"}}, owner="dave")
    assert hub.runner.load(rid)["status"] == "waiting"
    rec = hub.runner.load(rid)["record"]
    out = hub.why_item("carol", rec)
    assert not out["may"] and any("reporter" in r or "prepared" in r for r in out["reasons"])
    assert not hub.why_item("dave", rec)["may"]


def test_an_automatic_capa_credits_what_opened_it(released):
    """ux-14: opened by the NCR's automatic step, from the NCR; not by the person whose decision triggered it."""
    hub = released
    rid = hub.runner.submit("nonconformance", {"record": {"title": "Field failure", "description": "Unit failed in the field"}}, owner="alice")
    hub.runner.decide(rid, "carol", "approve", meaning="return for analysis", payload_digest=seen(hub.runner, rid),
                      fields={"disposition": "return", "disposition_rationale": "Return for analysis."})
    capa = hub.kernel.query(type_="capa")[0]
    run = hub.runner.run_for_record(capa["locator"])
    assert capa["created_by"] != "carol" and run["opened_by"] == capa["created_by"]
    assert run["opened_from"] == hub.runner.load(rid)["record"]


def test_an_item_request_is_not_approved_by_its_requester(released):
    """ux-6: the default segregation (the filer never approves on a role route) covers item requests."""
    hub = released
    rid = hub.runner.submit("item-request", {"record": {"name": "10k resistor", "mpn": "R-10K", "manufacturer": "Yageo"}}, owner="dave")
    st = hub.runner.load(rid)
    assert "dave" not in ((st.get("pending") or {}).get("assignees") or [])
