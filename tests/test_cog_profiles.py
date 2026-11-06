"""Cog profiles (model, prompt, the SOPs a cog follows), @mentions in an item's chatter, committees, and
swarm reviews. Models draft; they never sign."""

import pytest

from conftest import SMALL_BILL, dated, edit_frame, release_all, seen
from hubproto.compile import CompileError
from hubproto.model import ModelGateway


def bundle_of(hub):
    return hub.releases.bundle(hub.releases.current()["digest"])


# ------------------------------------------------------------------ profiles in the release
def test_profiles_compile_into_the_release_and_change_as_a_widening(released):
    b = bundle_of(released)
    prof = b["cogs"]["ap-reviewer"]
    assert prof["model"] == "claude-sonnet-5-5" and "payables reviewer" in prof["prompt"] and prof["follows"] == ["finance-policy"]
    assert b["cogs"]["quality-assistant"]["follows"] == ["ncr-sop", "capa-sop", "training-sop", "design-control-sop"]
    edit_frame(released, "cog-profiles", "Follows: finance-policy\n", "Follows: finance-policy, purchasing-policy\n", bump="1.1.0")
    proposal = released.compile()
    change = next(c for c in proposal["changes"] if c["rule"]["kind"] == "cog" and c["rule"]["name"] == "ap-reviewer" and c["op"] == "add")
    assert change["widening"] is True and "purchasing-policy" in change["reason"]
    released.review(proposal["id"], "bob", approve_all=True)
    with pytest.raises(CompileError, match="needs 2 approval"):
        released.release(proposal["id"], "bob")


def test_a_cog_profile_must_name_a_cog_member_and_real_frames(released):
    edit_frame(released, "cog-profiles", "## Cog: ap-reviewer", "## Cog: nobody-cog", bump="1.1.0")
    proposal = released.compile()
    errors = [f["detail"] for f in proposal["findings"] if f["severity"] == "error"]
    assert any("nobody-cog" in d and "Cog catalog" in d for d in errors)
    edit_frame(released, "cog-profiles", "## Cog: nobody-cog", "## Cog: ap-reviewer")
    edit_frame(released, "cog-profiles", "Follows: finance-policy\n", "Follows: finance-policy, no-such-sop\n", bump="1.2.0")
    proposal = released.compile()
    assert any("no-such-sop" in f["detail"] and "not a Frame" in f["detail"] for f in proposal["findings"] if f["severity"] == "error")


# ------------------------------------------------------------------ a cog works only from the SOPs it follows
def test_a_cog_is_not_routed_work_from_an_sop_it_does_not_follow(released):
    edit_frame(released, "cog-profiles", "Follows: finance-policy\n", "Follows: purchasing-policy\n", bump="1.1.0")
    release_all(released)
    run_id = released.runner.submit("purchase-to-pay", {"record": dated(SMALL_BILL)}, owner="alice")
    state = released.runner.load(run_id)
    skipped = next(e for e in released.runner.track(run_id) if e["type"] == "gate.skipped" and e["candidate"] == "cog:ap-reviewer")
    assert "does not follow finance-policy" in skipped["reason"] and "purchasing-policy" in skipped["reason"]
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["dave"]


# ------------------------------------------------------------------ the model gateway
def test_the_gateway_stands_in_without_a_key_and_records_every_call(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    g = ModelGateway()
    out = g.complete(model="claude-sonnet-5-5", system="## Scope\n## Checks", user="Review. FINDINGS:\n- [warning] x: y", by="quality-assistant")
    assert out["stand_in"] is True and "Verdict:" in out["text"] and out["prompt_digest"] and g.calls[-1]["by"] == "quality-assistant"


# ------------------------------------------------------------------ @mentions: review, approve, committee
def ncr(hub, by="alice", severity="low"):
    run_id = hub.runner.submit("nonconformance", {"record": {"title": "Scratched housing", "description": "cosmetic scratch on the housing",
                                                              "severity": severity, "source": "inspection"}}, owner=by)
    return hub.runner.load(run_id)


def test_a_mentioned_cog_reviews_the_item_answers_in_the_chatter_and_files_evidence(released):
    state = ncr(released, severity="high")  # high risk: waits for a person, not the cog
    loc = state["record"]
    reply = released.send("alice", "quality-assistant", "Review please", "@quality-assistant please provide a quality review on this nonconformance", record=loc)
    assert reply["kind"] == "reply" and reply["from"] == "quality-assistant" and reply["record"] == loc
    assert "Review of" in reply["body"] and ("pass" in reply["body"] or "fail" in reply["body"])
    docs = [d for d in released.dms.documents_of(loc) if d["fields"].get("doc_type") == "review"]
    assert docs and docs[0]["fields"]["doc_class"] == "evidence" and "quality-assistant" in docs[0]["fields"]["title"]
    chatter = released.inbox.on_record(loc)
    assert [m["kind"] for m in chatter if m["kind"] in ("message", "reply")] == ["message", "reply"]
    assert any(m["kind"] == "review" and m["to"] == "carol" for m in chatter), "the approver is told the review is there"
    assert "waiting for carol" in reply["body"], "the review is attached for the person who must decide"


def test_a_mentioned_cog_takes_its_turn_at_the_gate_when_the_review_passes(released):
    released.runner.cog_users_inline = False  # the item waits for the cog instead of being taken at once
    try:
        state = ncr(released, severity="low")
        loc = state["record"]
        assert state["pending"]["assignees"] == ["quality-assistant"]
        reply = released.send("alice", "quality-assistant", "Review", "@quality-assistant please review and approve this nonconformance", record=loc)
        assert "took my turn at the Gate" in reply["body"]
        after = released.runner.load(state["id"])
        assert after["status"] in ("completed", "waiting") and after["status"] != "failed"
        decided = [e for e in released.runner.track(state["id"]) if e["type"] == "gate.decided"]
        assert decided and decided[0]["by"] == "quality-assistant" and decided[0]["by_type"] == "cog"
    finally:
        released.runner.cog_users_inline = True


def test_a_committee_mention_fans_out_to_people_and_cogs(released):
    state = ncr(released, severity="high")
    loc = state["record"]
    reply = released.send("alice", "carol", "Approval", "@Design_review_committee please approve this document", record=loc)
    replies = reply.get("replies") or [reply]
    cog_replies = [r for r in replies if r.get("kind") == "reply" and r["from"] == "quality-assistant"]
    assert cog_replies, "the cog member of the committee reviewed at once"
    requests = [m for m in released.inbox.for_principal("bob") if m.get("kind") == "request"]
    assert requests and "alice asks you to approve" in requests[-1]["subject"] and requests[-1]["record"] == loc
    assert not [m for m in released.inbox.for_principal("alice") if m.get("kind") == "request"], "the sender is not asked to approve their own item"


def test_an_approval_mention_starts_the_items_process_when_nothing_is_in_flight(released):
    doc = released.dms.create(title="Work instruction WI-9", doc_class="controlled", folder="Quality/Controlled Documents", by="alice",
                              content=b"# WI-9\nbody", filename="wi-9.md", mime="text/markdown", doc_type="wi")
    assert released.runner.run_for_record(doc["locator"]) is None
    reply = released.send("alice", "bob", "Approval", "@bob please approve this document", record=doc["locator"])
    run = released.runner.run_for_record(doc["locator"])
    assert run is not None and run["process"] == "document-control"
    requests = [m for m in released.inbox.for_principal("bob") if m.get("kind") == "request"]
    assert requests and "started document-control" in requests[-1]["body"]


def test_an_unknown_mention_is_explained(released):
    state = ncr(released, severity="high")
    released.send("alice", "carol", "Hi", "@nobody-here please look", record=state["record"])
    notes = [m for m in released.inbox.for_principal("alice") if m.get("kind") == "mention"]
    assert notes and "No member or role is called @nobody-here" in notes[-1]["body"]


# ------------------------------------------------------------------ swarm step in a process
def test_the_design_process_swarms_two_cogs_and_records_their_reviews(hub, monkeypatch):
    from hubproto import worker
    from test_cad import SCHEMATIC

    monkeypatch.setenv("HUBPROTO_CAD_FAKE", "1")
    server = worker.serve_in_thread("127.0.0.1", 0)
    monkeypatch.setenv("HUBPROTO_CAD_WORKER_URL", f"http://127.0.0.1:{server.server_address[1]}")
    try:
        release_all(hub)
        run_id = hub.runner.submit("design-release", {"record": {"title": "Sensor board", "kind": "electrical", "change_class": "3",
                                                                 "user_needs": ["Reads temperature to 0.5 C"],
                                                                 "attachments": [{"name": "s.kicad_sch", "content": SCHEMATIC, "doc_class": "controlled", "doc_type": "schematic"}]}},
                                   owner="alice")
        state = hub.runner.load(run_id)
        assert state["status"] == "waiting" and state["pending"]["assignees"] == ["bob"], [e.get("reason") for e in hub.runner.track(run_id) if e["type"] == "run.failed"]
        design = hub.kernel.get(state["record"])
        reviews = design["fields"]["reviews"]
        assert [r["cog"] for r in reviews] == ["quality-assistant", "purchasing-assistant"]
        assert all(r["sops"] and "design-control-sop" in r["sops"] for r in reviews)
        assert design["fields"]["review_verdict"] in ("pass", "fail")
        evidence = [d for d in hub.dms.documents_of(design["locator"]) if d["fields"].get("doc_type") == "review"]
        assert evidence and "quality-assistant, purchasing-assistant" in evidence[0]["fields"]["title"]
        step = next(e for e in hub.runner.track(run_id) if e["type"] == "step.result" and e["step"] == "review-the-design-package")
        assert all(p["severity"] == "warning" for p in step["problems"]), "reviews advise the Gate; they do not stop the run"
    finally:
        server.shutdown()
