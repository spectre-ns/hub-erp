"""Cog profiles (what a cog does, its model and persona; what it follows is derived), @mentions in an item's chatter,
committees, and swarm reviews. Models draft; they never sign."""

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
    company = ["org-model", "quality-manual", "tasks-sop"]  # the company scope every member follows
    assert prof["model"] == "ollama/gemma4:12b-it-qat" and "Matches supplier bills" in prof["about"] and prof["follows"] == company + ["finance-policy", "working-documents-sop"]
    assert prof["persona"] == "reviewer" and prof["stance"] == "reviewer", "its behaviour comes from its persona"
    assert "You are the ap-reviewer cog" in prof["prompt"] and "say plainly what does not match" in prof["prompt"]
    assert {"ncr-sop", "capa-sop", "training-sop", "design-control-sop"} <= set(b["cogs"]["quality-assistant"]["follows"])
    # A new model in its profile is a widening: two policy owners.
    edit_frame(released, "cog-profiles", "Persona: reviewer\n\n## Cog: doc-reviewer", "Persona: executor\n\n## Cog: doc-reviewer", bump="2.1.0")
    proposal = released.compile()
    change = next(c for c in proposal["changes"] if c["rule"]["kind"] == "cog" and c["rule"]["name"] == "ap-reviewer" and c["op"] == "add")
    assert change["widening"] is True and "persona executor" in change["reason"]
    released.review(proposal["id"], "bob", approve_all=True)
    with pytest.raises(CompileError, match="needs 2 approval"):
        released.release(proposal["id"], "bob")


def test_a_cog_profile_must_name_a_cog_member_and_a_persona_that_exists(released):
    edit_frame(released, "cog-profiles", "## Cog: ap-reviewer", "## Cog: nobody-cog", bump="2.1.0")
    proposal = released.compile()
    errors = [f["detail"] for f in proposal["findings"] if f["severity"] == "error"]
    assert any("nobody-cog" in d and "no matching cog member" in d for d in errors)
    edit_frame(released, "cog-profiles", "## Cog: nobody-cog", "## Cog: ap-reviewer")
    edit_frame(released, "cog-profiles", "Persona: reviewer\n\n## Cog: doc-reviewer", "Persona: auditor\n\n## Cog: doc-reviewer", bump="2.2.0")
    proposal = released.compile()
    assert any("no persona 'auditor'" in f["detail"] for f in proposal["findings"] if f["severity"] == "error")


def test_the_old_profile_lines_are_refused_with_where_they_went(released):
    edit_frame(released, "cog-profiles", "## Cog: ap-reviewer\n", "## Cog: ap-reviewer\nAutonomy: act\nFollows: finance-policy\n", bump="2.1.0")
    errors = [f["detail"] for f in released.compile(save=False)["findings"] if f["severity"] == "error"]
    assert any("autonomy is derived per action" in d for d in errors) and any("follows is derived" in d for d in errors)


# ------------------------------------------------------------------ a cog works only from the SOPs it follows
def test_a_cog_is_not_routed_work_from_an_sop_it_does_not_follow(released):
    """What a cog follows is derived from the SOPs that name it, so a route to it always names an SOP it follows; the
    runner still refuses a cog whose follows (in a release) leave out the process's SOP."""
    b = bundle_of(released)
    assert "finance-policy" in b["cogs"]["ap-reviewer"]["follows"], "the routing sentence that names it makes it follow the policy"
    assert b["cogs"]["ap-reviewer"]["follows_why"]["finance-policy"] == ["cog:ap-reviewer", "role:finance-clerk", "role:payables-reviewer"]
    narrowed = {**b, "cogs": {**b["cogs"], "ap-reviewer": {**b["cogs"]["ap-reviewer"], "follows": ["org-model", "purchasing-policy"]}}}
    why = released.runner.cog_follows(narrowed, "ap-reviewer", "purchase-to-pay")
    assert "does not follow finance-policy" in why and "purchasing-policy" in why


# ------------------------------------------------------------------ the model gateway
def test_the_gateway_stands_in_without_a_key_and_records_every_call(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")  # hosted models switched on deliberately (they are off by default)
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
    # The outcome in words (looks right / problems found), not a bare pass or fail.
    assert "Review of" in reply["body"] and ("looks right" in reply["body"] or "problems found" in reply["body"])
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
        # The outcome leads the reply (ux-8): approved, signed and waiting for a countersignature, or not signed.
        assert reply["body"].startswith(("Approved:", "Signed; waiting for a countersignature", "I cannot sign")), reply["body"][:200]
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


def test_the_gateway_uses_ollama_when_configured(monkeypatch):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = {}

    class Fake(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.update(body)
            out = json.dumps({"message": {"role": "assistant", "content": "Looks fine.\nVerdict: pass"}}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("HUBPROTO_OLLAMA_URL", f"http://127.0.0.1:{srv.server_address[1]}")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HUBPROTO_MODEL_FAKE", raising=False)
        g = ModelGateway()
        out = g.complete(model="ollama/qwen2.5", system="You review.", user="Review this.", by="quality-assistant")
        assert out["backend"] == "ollama" and out["stand_in"] is False and "Looks fine" in out["text"]
        assert seen["model"] == "qwen2.5" and seen["messages"][0]["role"] == "system" and seen["stream"] is False
        # A claude-* cog is never served by a local model: without a key it is unavailable.
        from hubproto.model import ModelUnavailable

        monkeypatch.delenv("HUBPROTO_ALLOW_STAND_IN", raising=False)
        monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")  # hosted models on, and no key: that is what it needs
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ModelUnavailable, match="needs an Anthropic key"):
            g.complete(model="claude-sonnet-5-5", system="s", user="u")
        monkeypatch.delenv("HUBPROTO_HOSTED_MODELS", raising=False)
        monkeypatch.setenv("HUBPROTO_ALLOW_STAND_IN", "1")
        # A model that does not answer never blocks the work.
        monkeypatch.setenv("HUBPROTO_OLLAMA_URL", "http://127.0.0.1:9")
        out = g.complete(model="ollama/nothing", system="s", user="u")
        assert out["stand_in"] is True and "did not answer" in out["text"]
    finally:
        srv.shutdown()


def test_the_cogs_system_prompt_carries_its_process_the_whole_document_and_every_thread(released):
    from hubproto.cogs import RunContext, cog_context

    content = b"# WI-12\nTorque the M4 screws to 2 Nm.\nRecord the value."
    doc = released.dms.create(title="WI-12 Torque", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                              filename="wi12.md", mime="text/markdown", doc_type="wi")
    released.kernel.set_fields(doc["locator"], {"text": content.decode(), "office_comments": [
        {"id": "0", "author": "carol", "text": "@quality-assistant is 2 Nm right?", "parent": None},
        {"id": "1", "author": "quality-assistant", "text": "Yes, per the procedure.", "parent": "0"},
        {"id": "2", "author": "carol", "text": "Which procedure?", "parent": "1"}]}, by="carol")
    bundle = released.releases.bundle(released.releases.current()["digest"])
    ctx = RunContext(run_id="t", release="", bundle=bundle, owner="quality-assistant", kernel=released.kernel, frames=lambda: released.frames,
                     extras={"cog_profile": bundle["cogs"]["quality-assistant"]})
    system = cog_context(ctx, released.kernel.get(doc["locator"]))
    assert system.startswith("== WHO YOU ARE ==\nYou are the quality-assistant cog") and "Reviews drawings, designs and nonconformances" in system
    assert "- ncr-sop: " in system and "- design-control-sop: " in system, "the SOPs it is trained on, as an index of their sections"
    assert "THE PROCESS THAT GOVERNS THIS WORK: document-control" in system, "the item's process, not whole SOPs"
    assert "DOCUMENT " + doc["number"] in system and "Torque the M4 screws to 2 Nm." in system, "the whole document"
    assert "- [0] carol: @quality-assistant is 2 Nm right?" in system and "  - [1] quality-assistant (reply to 0): Yes" in system and "    - [2] carol (reply to 1): Which procedure?" in system


def test_reviews_do_not_show_the_model_technical_fields(released):
    from hubproto import review as _review
    from hubproto.cogs import RunContext

    doc = released.dms.create(title="Spec", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=b"body",
                              filename="spec.md", mime="text/markdown", doc_type="document")
    released.kernel.set_fields(doc["locator"], {"wopi_lock": "cool-lock1", "editors": ["carol"], "checked_out_by": "carol"}, by="carol")
    ctx = RunContext(run_id="t", release="", bundle=released.releases.bundle(released.releases.current()["digest"]), owner="quality-assistant",
                     kernel=released.kernel, dms=released.dms, frames=lambda: released.frames, extras={})
    ev = _review.gather(ctx, released.kernel.get(doc["locator"]))
    assert "wopi_lock" not in ev["fields"] and "mime" not in ev["fields"] and "editors" not in ev["fields"] and ev["fields"]["title"] == "Spec"


def test_an_explicit_approval_phrase_is_not_second_guessed_by_the_model(monkeypatch):
    from hubproto.mentions import intent_of

    class Model:
        def available(self, m):
            return True

        calls = 0

        def complete(self, **kw):
            Model.calls += 1
            return {"text": "REVIEW"}

    assert intent_of("Please move this document to approval", model=Model(), model_name="x") == "approve"
    # An imperative edit is taken as it reads, with no classification call (cogs-13).
    assert intent_of("Please make it a table", model=Model(), model_name="x") == "edit" and Model.calls == 0
    assert intent_of("What do you make of the scope section?", model=Model(), model_name="x") == "review", "otherwise the model decides"
    assert Model.calls == 1


def test_an_approval_request_files_a_working_document_into_the_process_folder_and_starts_it(released):
    doc = released.dms.create(title="Draft WI from Tasks", doc_class="working", folder="Tasks", by="carol", content=b"# WI\nbody",
                              filename="wi.md", mime="text/markdown", doc_type="wi")
    reply = released.send("carol", "ap-reviewer", "Approval", "Please move this document to approval", record=doc["locator"])
    assert reply["kind"] == "reply" and reply["from"] == "ap-reviewer" and "Review of" not in reply["body"], "an approval ask is not a review"
    run = released.runner.run_for_record(doc["locator"])
    assert run is not None and run["process"] == "document-control"
    rec = released.kernel.get(doc["locator"])
    assert rec["folder"] == "Quality/Controlled Documents", "filed where document-control files its records"
    assert "started document-control" in reply["body"] or "waiting for" in reply["body"]
    assert any(e["type"] == "record.moved" for e in released.kernel.events(aggregate=doc["locator"]))


def test_a_process_started_on_an_existing_document_reads_its_fields(released):
    doc = released.dms.create(title="Draft WI 9", doc_class="working", folder="Tasks", by="carol", content=b"# WI 9\nbody",
                              filename="wi9.md", mime="text/markdown", doc_type="wi")
    released.send("carol", "quality-assistant", "Approval", "Please move this document to approval", record=doc["locator"])
    run = released.runner.run_for_record(doc["locator"])
    assert run["status"] != "failed", [e.get("reason") for e in released.runner.track(run["id"]) if e["type"] == "run.failed"]
    assert run["inputs"]["record"]["title"] == "Draft WI 9"


def test_proposing_an_existing_docx_for_approval_keeps_its_content(released):
    from hubproto import wopi, office

    content, mime = wopi.blank_document("docx")
    content = office.insert_paragraphs(content, "docx", ["# WI 20", "Torque to 2 Nm."], "start")
    doc = released.dms.create(title="WI 20", doc_class="working", folder="Tasks", by="carol", content=content, filename="wi20.docx", mime=mime, doc_type="wi")
    released.runner.submit("document-control", {"record": {}}, owner="carol", record=doc["locator"])
    run = released.runner.run_for_record(doc["locator"])
    assert run["status"] == "waiting", [e.get("reason") for e in released.runner.track(run["id"]) if e["type"] == "run.failed"]
    latest = released.dms.versions(doc["locator"])[-1]
    assert latest["filename"] == "wi20.docx" and latest["version"] == 1, "no markdown draft replaced the docx"
    assert "Torque to 2 Nm." in office.extract_text(released.dms.content(doc["locator"])[0], "docx")


def test_a_cog_whose_model_is_not_available_is_not_used(released, monkeypatch):
    """A cog is bundled with its model: without it the cog takes no Gate, no chat, no mention, no task, and nothing stands in."""
    from conftest import SMALL_BILL, dated
    from hubproto.agent import Agent
    from hubproto.model import cog_availability

    hub = released
    monkeypatch.delenv("HUBPROTO_ALLOW_STAND_IN", raising=False)
    monkeypatch.delenv("HUBPROTO_OLLAMA_URL", raising=False)
    b = hub.releases.bundle(hub.releases.current()["digest"])
    ok, why = cog_availability(b, "ap-reviewer")
    assert not ok and "needs an Ollama" in why
    # A small bill would go to ap-reviewer; it is skipped with the reason and the person after it gets the item.
    run_id = hub.runner.submit("purchase-to-pay", {"record": dated(SMALL_BILL)}, owner="alice")
    state = hub.runner.load(run_id)
    skipped = [e for e in hub.runner.track(run_id) if e["type"] == "gate.skipped" and e["candidate"] == "cog:ap-reviewer"]
    assert skipped and "not available" in skipped[0]["reason"] and state["pending"]["assignees"] == ["dave"]
    with pytest.raises(ValueError, match="not available"):
        hub.chat("alice", "ap-reviewer", "hi")
    with pytest.raises(ValueError, match="not available"):
        Agent(hub, "quality-assistant").run("anything", by="carol")
    hub.send("alice", "ap-reviewer", "Review", "@ap-reviewer please review this", record=state["record"])
    assert any(m["kind"] == "mention" and "ap-reviewer is not available" in m["subject"] for m in hub.inbox.for_principal("alice"))
    # The compiler warns that the cogs' models cannot be reached here.
    p = hub.compile(save=False)
    assert any(f["guard"] == "model" and "ap-reviewer is bundled with ollama/gemma4:12b-it-qat" in f["detail"] for f in p["findings"])
