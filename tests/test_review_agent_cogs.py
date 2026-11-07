"""Review 7 Nov 2026, AGENT, cogs on qwen3:14b and the AGENT parts of governance and UX: intents (cogs-2, 13), the
record in the cog's context (cogs-3), honest run summaries (cogs-4), reviews that cite the SOP and list findings once
(cogs-7, 11, ux-10), outcomes in words (cogs-12, ux-8, ux-16), search by values (cogs-5 in the triage), prompts that
say what a cog does not know (cogs-8, 10), "minor" checked not believed (governance-1), released SOP text
(governance-9), who may ask a cog to sign (governance-13) and confirm (governance-15)."""

import shutil

import pytest

from conftest import ROOT, edit_frame, release_all
from hubproto import office
from hubproto import review as review_mod
from hubproto.agent import Agent, AgentStore, AgentTools, may_ask_step_in
from hubproto.app import Hub
from hubproto.cogs import ReviewerCog, RunContext, cog_context
from hubproto.mentions import intent_by_keywords, intent_of
from hubproto.tools import state_words


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    root = tmp_path_factory.mktemp("hub")
    shutil.copytree(ROOT / "frames", root / "frames")
    h = Hub(root)
    h.kernel.seed()
    release_all(h)
    return h


def bundle(hub):
    return hub.releases.bundle(hub.releases.current()["digest"])


def ctx_for(hub, cog, rec=None, model=None):
    b = bundle(hub)
    return RunContext(run_id="t-ctx", release=hub.releases.current()["digest"], bundle=b, owner=cog, kernel=hub.kernel, runner=hub.runner,
                      inbox=hub.inbox, record=rec["locator"] if rec else None, dms=hub.dms, frames=lambda: hub.released_frames(),
                      model=model, extras={"cog_profile": b["cogs"][cog]})


def submit_doc(hub, title, change_type, owner="alice", **extra):
    run_id = hub.runner.submit("document-control", {"record": {"title": title, "change_type": change_type, **extra,
                                                               "body": f"# {title}\n\n1. Purpose\n2. Scope\n3. Steps"}}, owner=owner)
    return hub.runner.load(run_id)


# ---------------------------------------------------------------- cogs-2, cogs-13: intents
@pytest.mark.parametrize("text", ["Has this been sent for approval yet?", "Who approves releases like this one?",
                                  "When will the approval be done?", "I didn't approve this, did you?"])
def test_a_question_about_approval_is_not_a_request_to_sign(text):
    assert intent_by_keywords(text) != "approve"

    class Says:
        def available(self, m):
            return True

        def complete(self, **kw):
            return {"text": "APPROVE"}

    assert intent_of(text, model=Says(), model_name="x") != "approve", "signing is never on a model's reading"


@pytest.mark.parametrize("text", ["Please approve this", "@qa-manager approve this document please", "Please move this to approval"])
def test_an_explicit_request_to_approve_is_one(text):
    assert intent_by_keywords(text) == "approve"


def test_an_imperative_edit_costs_no_classification_call():
    class Counting:
        n = 0

        def available(self, m):
            return True

        def complete(self, **kw):
            Counting.n += 1
            return {"text": "ANSWER"}

    assert intent_of("Replace yearly with monthly in the scope section", model=Counting(), model_name="x") == "edit" and Counting.n == 0


# ---------------------------------------------------------------- cogs-3: the record in the context
def test_a_cog_sees_the_fields_of_a_record_it_is_asked_about(hub):
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "Burr", "source": "inspection", "lot": "L-77", "quantity": 12,
                                                               "description": "Burr on the bracket edge"}, by="carol")
    prompt = cog_context(ctx_for(hub, "quality-assistant", ncr), ncr, task="answer")
    assert "L-77" in prompt and "Burr on the bracket edge" in prompt and "<<<UNTRUSTED RECORD FIELDS" in prompt


# ---------------------------------------------------------------- cogs-4: what did not happen is said
def test_the_run_summary_says_what_the_hub_refused_and_finish_counts_once(hub):
    store = AgentStore(hub.root / "state" / "agents")
    tools = AgentTools(hub, "quality-assistant", "t-honest", "act", 4, store, requester="carol")
    store.append("t-honest", {"type": "started", "cog": "quality-assistant", "goal": "comment", "by": "carol"})
    assert "error" in tools.execute("comment", {"number": "NCR-9999-0001", "text": "done"})
    assert tools.execute("finish", {"summary": "I commented on NCR-9999-0001."})["ok"]
    assert "already finished" in tools.execute("finish", {"summary": "And then something else."})["note"]
    out = Agent(hub, "quality-assistant").conclude({"tools": tools, "run": "t-honest", "by": "carol", "goal": "comment"}, {"text": ""})
    assert out["summary"].startswith("I commented on NCR-9999-0001.") and "Not done (the hub refused): comment" in out["summary"]
    assert "something else" not in out["summary"]


# ---------------------------------------------------------------- cogs-7, cogs-11, ux-10: reviews
class Says:
    def __init__(self, text):
        self.text = text

    def available(self, m):
        return True

    def complete(self, **kw):
        return {"text": self.text, "stand_in": False}


def _review(hub, text, rec):
    ctx = ctx_for(hub, "quality-assistant", rec, model=Says(text))
    return review_mod.review("quality-assistant", bundle(hub)["cogs"]["quality-assistant"], ctx, rec)


def test_an_error_that_quotes_no_sop_sentence_is_a_remark_and_does_not_fail(hub):
    d = hub.dms.create(title="Check sheet", doc_class="working", folder="Quality/Controlled Documents", by="carol",
                       content=b"# Check\nMeasure.\n", filename="c.md", mime="text/markdown", doc_type="document")
    r = _review(hub, "Looks mostly fine.\n- [error] sod: the requester and approver must be four different people\nVerdict: fail",
                hub.kernel.get(d["locator"]))
    assert r["verdict"] == "pass"
    [f] = [p for p in r["findings"] if "four different people" in p["detail"]]
    assert f["severity"] == "warning" and "not found in the procedures" in f["detail"]


def test_an_error_quoting_its_sop_sentence_stands(hub):
    rec = hub.kernel.get(hub.dms.create(title="Check sheet 2", doc_class="working", folder="Quality/Controlled Documents", by="carol",
                                        content=b"# Check\n", filename="c2.md", mime="text/markdown", doc_type="document")["locator"])
    sops = ctx_for(hub, "quality-assistant").sop_texts()
    sentence = next(s.strip() for t in sops.values() for s in t.split(".") if len(s.strip()) > 40 and '"' not in s and "\n" not in s.strip())
    r = _review(hub, f'- [error] scope: "{sentence}" is not met\nVerdict: fail', rec)
    assert r["verdict"] == "fail" and any(p["severity"] == "error" for p in r["findings"])


def test_findings_are_listed_once_and_the_placeholder_word_is_dropped(hub):
    rec = hub.kernel.get(hub.dms.create(title="Check sheet 3", doc_class="working", folder="Quality/Controlled Documents", by="carol",
                                        content=b"# Check\n", filename="c3.md", mime="text/markdown", doc_type="document")["locator"])
    text = ("- [warning] check: the revision table is missing\n- [warning] layout: the revision table is missing\n"
            "Summary: the revision table is missing, otherwise fine.\nVerdict: pass")
    r = _review(hub, text, rec)
    same = [p for p in r["findings"] if "revision table is missing" in p["detail"]]
    assert len(same) == 1 and same[0]["check"] != "check"
    assert "revision table is missing" not in r["summary"]


def test_condition_keys_read_as_words(hub):
    said = ReviewerCog._in_words("condition risk_low not met")
    assert "risk_low" not in said and "not the case here" in said
    assert "_" not in " ".join(review_mod.routing_notes({"conditions": {"risk_low": False}}) or [""])


# ---------------------------------------------------------------- ux-8, ux-16: outcome first, in words
def test_a_signature_waiting_for_a_countersign_is_not_called_approved(hub):
    state = submit_doc(hub, "SOP-AG-major", "major")
    reply = hub.send("bob", "qa-manager", "Approval", "@qa-manager please approve this document", record=state["record"])
    body = reply["body"] if "body" in reply else [m for m in hub.inbox.on_record(state["record"]) if m["from"] == "qa-manager"][-1]["body"]
    assert body.startswith("Signed; waiting for a countersignature from carol"), body[:200]
    assert not body.lower().startswith("approved")


def test_states_read_as_words():
    assert state_words("awaiting-verify-the-actions") == "waiting at Verify the actions"
    assert state_words("in-review") == "in review"


# ---------------------------------------------------------------- triage cogs-5: search by values
def test_search_matches_values_not_field_names(hub):
    t = AgentTools(hub, "qa-manager", "t-search", "read", 4, AgentStore(hub.root / "state" / "agents"), requester="carol")
    hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "Warped housing", "source": "inspection", "lot": "Q-1"}, by="carol")
    assert "Warped housing" in str(t.execute("search_records", {"query": "warped"}))
    assert "Warped housing" not in str(t.execute("search_records", {"query": "lot"}))


# ---------------------------------------------------------------- cogs-8, cogs-10: prompts
def test_prompts_say_what_a_cog_does_not_know():
    assert "never claim that something did not happen" in office.writing_interface("chat")
    assert "you do not follow it" in office.writing_interface("answer")
    review = office.writing_interface("review")
    assert "quoted word for word" in review and "Fail only when there is at least one error" in review


# ---------------------------------------------------------------- governance-1: minor is checked
def test_a_new_document_declared_minor_is_major(hub):
    state = submit_doc(hub, "SOP-AG-new", "minor")
    rec = hub.kernel.get(state["record"])
    assert rec["fields"]["conditions"]["change_minor"] is False
    assert "doc-reviewer" not in state["pending"]["assignees"]


# ---------------------------------------------------------------- governance-13: who may ask a cog to sign
def test_only_a_person_waited_for_may_ask_a_cog_to_sign(hub):
    state = submit_doc(hub, "SOP-AG-ask", "major")
    rec = hub.kernel.get(state["record"])
    b = bundle(hub)
    assert "prepared this item" in may_ask_step_in(b, state, rec, "alice")
    assert "only a person" in may_ask_step_in(b, state, rec, "quality-assistant")
    waited = state["pending"]["assignees"][0]
    assert may_ask_step_in(b, state, rec, waited) == ""
    before = len(hub.runner.track(state["id"]))
    reply = hub.send("alice", "qa-manager", "Approval", "@qa-manager please approve this document", record=state["record"])
    body = reply.get("body") or [m for m in hub.inbox.on_record(state["record"]) if m["from"] == "qa-manager"][-1]["body"]
    assert "may not sign" in body and hub.runner.load(state["id"])["status"] == "waiting"
    assert not [e for e in hub.runner.track(state["id"])[before:] if e["type"] in ("gate.signed", "gate.decided")]


def test_a_failed_judgment_binds_for_that_version(hub, monkeypatch):
    state = submit_doc(hub, "SOP-AG-bind", "major")
    real = review_mod.review

    def failing(*a, **kw):
        r = real(*a, **kw)
        return {**r, "verdict": "fail", "findings": [{"severity": "error", "check": "scope", "detail": "no scope section"}]}

    monkeypatch.setattr(review_mod, "review", failing)
    hub.send("bob", "qa-manager", "Approval", "@qa-manager please approve this document", record=state["record"])
    monkeypatch.setattr(review_mod, "review", real)
    # Asked again about the same content, it does not shop for a passing review.
    hub.send("bob", "qa-manager", "Approval", "@qa-manager please approve this document", record=state["record"])
    after = hub.runner.load(state["id"])
    assert after["status"] == "waiting" and not [e for e in hub.runner.track(state["id"]) if e["type"] in ("gate.signed", "gate.decided")]


# ---------------------------------------------------------------- governance-9: the released SOP text
def test_cogs_read_the_released_sop_not_an_unreleased_edit(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    name = next(iter(bundle(h)["cogs"]["quality-assistant"].get("follows") or []))
    body = h.frames.get(name).body
    first = next(ln for ln in body.splitlines() if len(ln.strip()) > 30 and not ln.startswith(("#", "-", "|")))
    edit_frame(h, name, first, first + " UNRELEASED-SENTENCE-XYZ.")
    texts = ctx_for(h, "quality-assistant").sop_texts([name])
    assert "UNRELEASED-SENTENCE-XYZ" not in texts.get(name, "")


# ---------------------------------------------------------------- governance-15: confirm
def test_proposed_actions_are_confirmed_by_the_task_giver_and_replayed_as_the_proposing_cog(hub):
    store = AgentStore(hub.root / "state" / "agents")
    run = "t-confirm"
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "Scratch", "source": "inspection"}, by="carol")
    store.append(run, {"type": "started", "cog": "quality-assistant", "goal": "comment on it", "by": "carol"})
    store.append(run, {"type": "finished", "status": "proposed", "summary": "", "actions": 0,
                       "proposed": [{"tool": "comment", "arguments": {"number": ncr["number"], "text": "please add the lot"}}]})
    with pytest.raises(ValueError, match="only carol"):
        Agent(hub, "quality-assistant").confirm(run, "dave")
    out = Agent(hub, "qa-manager").confirm(run, "carol")  # asked through another cog: still replayed as quality-assistant
    assert out["results"][0]["result"].get("ok")
    [msg] = [m for m in hub.inbox.on_record(ncr["locator"]) if "please add the lot" in (m.get("body") or "")]
    assert msg["from"] == "quality-assistant"
