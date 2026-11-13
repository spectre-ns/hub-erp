"""A Claude model thinks before it answers, and the thinking counts against max_tokens (issue #60): the gateway gives
it room to think on top of the answer the caller asked for, and an answer cut off at the token limit is never used."""

import json

import pytest

from hubproto import model

SYSTEM, USER = "You answer questions.", "What does this procedure cover?"
THINKING = {"type": "thinking", "thinking": "", "signature": "x"}


def hosted(monkeypatch, replies):
    """Claude on (a fake API key, never sent anywhere): each request's body is kept, the replies come in turn."""
    sent = []

    def fetch(req, timeout):
        assert req.full_url.startswith(model.ANTHROPIC_URL), "only the fake Anthropic API is asked"
        sent.append({**json.loads(req.data), "timeout": timeout})
        reply = replies(json.loads(req.data)) if callable(replies) else replies.pop(0)
        return reply

    monkeypatch.setattr(model, "_fetch_json", fetch)
    monkeypatch.delenv("HUBPROTO_MODEL_FAKE", raising=False)
    monkeypatch.delenv("HUBPROTO_ANTHROPIC_THINKING_TOKENS", raising=False)
    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return sent


def tool_specs():
    return [{"name": "search_records", "description": "Search records.", "parameters": {"type": "object", "properties": {}}}]


def test_a_claude_call_leaves_room_to_think_beyond_the_answer_asked_for(monkeypatch):
    done = {"content": [THINKING, {"type": "text", "text": "It covers incoming inspection."}], "stop_reason": "end_turn"}
    sent = hosted(monkeypatch, lambda body: done)
    out = model.ModelGateway().complete(model="claude-opus-5-5", system=SYSTEM, user=USER, max_tokens=500)
    assert out["text"] == "It covers incoming inspection."
    assert sent[-1]["max_tokens"] == 8500, "the thinking (8000 tokens of room) does not eat the 500-token answer"
    model.ModelGateway().complete_with_tools(model="claude-opus-5-5", system=SYSTEM, user=USER, max_tokens=800,
                                             tools=tool_specs(), call_tool=lambda name, args: {})
    assert sent[-1]["max_tokens"] == 8800


@pytest.mark.parametrize("written, budget", [("2000", 2005), ("8,000", 8005), ("8 000", 8005), ("lots", 8005), ("", 8005),
                                             ("0", 5), ("500000", 128000)])
def test_the_room_to_think_is_read_as_the_manual_writes_it_and_never_asks_for_more_than_a_response_holds(monkeypatch, written, budget):
    done = {"content": [{"type": "text", "text": "EDIT"}], "stop_reason": "end_turn"}
    sent = hosted(monkeypatch, lambda body: done)
    monkeypatch.setenv("HUBPROTO_ANTHROPIC_THINKING_TOKENS", written)
    out = model.ModelGateway().complete(model="claude-opus-5-5", system=SYSTEM, user=USER, max_tokens=5)
    assert out["text"] == "EDIT" and sent[-1]["max_tokens"] == budget


def test_a_larger_budget_gets_longer_to_arrive(monkeypatch):
    done = {"content": [{"type": "text", "text": "read"}], "stop_reason": "end_turn"}
    sent = hosted(monkeypatch, lambda body: done)
    model.ModelGateway().complete(model="claude-opus-5-5", system=SYSTEM, user=USER, max_tokens=12000)  # an SOP reading
    assert sent[-1]["timeout"] == 20000 / 40, "a non-streamed response arrives only when it is written: time to write it all"
    model.ModelGateway().complete(model="claude-opus-5-5", system=SYSTEM, user=USER, max_tokens=500)
    assert sent[-1]["timeout"] == 8500 / 40
    monkeypatch.setenv("HUBPROTO_ANTHROPIC_THINKING_TOKENS", "0")
    model.ModelGateway().complete(model="claude-opus-5-5", system=SYSTEM, user=USER, max_tokens=500)
    assert sent[-1]["timeout"] == 180, "never less than three minutes"


def test_an_answer_claude_stopped_at_the_token_limit_is_refused_not_returned(monkeypatch):
    cut = {"content": [THINKING, {"type": "text", "text": "Bob, I can't give"}], "stop_reason": "max_tokens"}
    hosted(monkeypatch, lambda body: cut)
    monkeypatch.delenv("HUBPROTO_ALLOW_STAND_IN", raising=False)  # as on a real hub: no stand-in answers instead
    with pytest.raises(model.ModelUnavailable, match="cut off at the token limit"):
        model.ModelGateway().complete(model="claude-opus-5-5", system=SYSTEM, user=USER, max_tokens=500)
    with pytest.raises(model.ModelUnavailable, match="cut off at the token limit"):
        model.ModelGateway().complete_with_tools(model="claude-opus-5-5", system=SYSTEM, user=USER, max_tokens=800,
                                                 tools=tool_specs(), call_tool=lambda name, args: {})


def test_a_tool_call_cut_off_at_the_token_limit_is_not_run(monkeypatch):
    cut = {"content": [THINKING, {"type": "tool_use", "id": "t1", "name": "search_records", "input": {}}], "stop_reason": "max_tokens"}
    after = {"content": [{"type": "text", "text": "Found nothing."}], "stop_reason": "end_turn"}
    hosted(monkeypatch, [cut, after])
    monkeypatch.delenv("HUBPROTO_ALLOW_STAND_IN", raising=False)
    ran = []
    with pytest.raises(model.ModelUnavailable, match="cut off"):
        model.ModelGateway().complete_with_tools(model="claude-opus-5-5", system=SYSTEM, user=USER, tools=tool_specs(),
                                                 call_tool=lambda name, args: ran.append(name) or {})
    assert ran == [], "a tool call Claude did not finish writing is never run"


def test_a_senior_cog_whose_answer_was_cut_off_says_so_instead_of_filing_half_a_sentence(released, monkeypatch):
    """DOC-0513: senior-organization answered a question in a document with "Bob, I can't give"."""
    hub = released

    def replies(body):
        if body["system"].startswith("You classify requests"):
            return {"content": [THINKING, {"type": "text", "text": "ANSWER"}], "stop_reason": "end_turn"}
        return {"content": [THINKING, {"type": "text", "text": "Bob, I can't give"}], "stop_reason": "max_tokens"}

    sent = hosted(monkeypatch, replies)
    monkeypatch.delenv("HUBPROTO_ALLOW_STAND_IN", raising=False)
    from hubproto import wopi

    content, mime = wopi.blank_document("docx")
    doc = hub.dms.create(title="Budget question", doc_class="working", folder="Quality/Controlled Documents", by="bob",
                         content=content, filename="budget.docx", mime=mime, doc_type="document")
    hub.send("bob", "bob", "Comment", "@senior-organization can you give a decision on this section?", record=doc["locator"])
    assert any(not b["system"].startswith("You classify") for b in sent), "the senior cog asked Claude for its answer"
    replies_ = [m for m in hub.inbox.on_record(doc["locator"]) if m.get("kind") == "reply" and m["from"] == "senior-organization"]
    assert replies_, "the cog replied"
    body = replies_[-1]["body"]
    assert "I can't give" not in body, "the cut-off words are not filed as its answer"
    assert "could not answer" in body
