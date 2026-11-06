"""The messenger: an informal chat with a cog from a window in the corner of every page."""

import json
import threading
import time
from urllib.parse import quote

from test_shell import get, post, shell  # noqa: F401  (the module-scoped live shell)


def test_the_widget_is_on_every_page_for_people_and_never_for_a_watched_cog(shell):
    status, text = get(shell, "/", **{"as": "carol"})
    assert status == 200 and 'id="messenger"' in text and "Chat with a cog" in text and '<option value="quality-assistant">' in text
    status, text = get(shell, "/inbox", **{"as": "dave"})
    assert 'id="messenger"' in text
    status, text = get(shell, "/", **{"as": "quality-assistant"})
    assert 'id="messenger"' not in text, "watching a cog: nothing is sent in its name"
    status, text = get(shell, "/", **{"as": "nobody"})
    assert 'id="messenger"' not in text


def test_a_chat_turn_is_kept_apart_from_mail_and_chatter_and_knows_the_page(shell):
    hub = shell["hub"]
    doc = hub.dms.create(title="Torque WI", doc_class="working", folder="Quality/Controlled Documents", by="carol",
                         content=b"# Torque\nM4 screws to 2 Nm.", filename="torque.md", mime="text/markdown", doc_type="wi")
    seen = {}
    real = hub.model.complete

    def fake(**kw):
        seen.update(kw)
        return {"text": "Looks fine to me, 2 Nm matches the procedure.", "stand_in": False, "served_model": "fake", "model": "fake"}

    hub.model.complete = fake
    try:
        status, text = get(shell, "/document/" + quote(doc["locator"], safe=":"), **{"as": "carol"})
        assert f'data-about="{doc["locator"]}"' in text, "the widget knows which item is on screen"
        status, url, body = post(shell, "/chat", {"as": "carol", "to": "quality-assistant", "body": "hey, is the torque right here?", "about": doc["locator"]})
        assert status == 200, body
        reply = json.loads(body)["reply"]
        assert reply["from"] == "quality-assistant" and "2 Nm" in reply["body"]
        assert "M4 screws to 2 Nm." in seen["system"] and "HOW TO TALK HERE" in seen["system"] and "YOU ARE CHATTING WITH carol" in seen["system"]
        # A second turn carries the conversation so far.
        post(shell, "/chat", {"as": "carol", "to": "quality-assistant", "body": "thanks, and the record step?", "about": doc["locator"]})
        assert [t["role"] for t in seen["history"]] == ["user", "assistant"] and seen["history"][0]["content"].startswith("hey")
    finally:
        hub.model.complete = real
    status, body = get(shell, "/chat-thread", **{"as": "carol", "with": "quality-assistant", "about": doc["locator"]})
    j = json.loads(body)
    assert [m["mine"] for m in j["messages"]][-4:] == [True, False, True, False] and doc["number"] in j["context"]
    assert hub.inbox.on_record(doc["locator"]) == [], "a chat is not the item's chatter"
    status, text = get(shell, "/inbox", **{"as": "carol"})
    assert "hey, is the torque right here?" not in text, "nor a message in the inbox"
    # A fresh conversation.
    status, url, body = post(shell, "/chat-clear", {"as": "carol", "with": "quality-assistant"})
    assert json.loads(body)["cleared"] >= 4 and json.loads(get(shell, "/chat-thread", **{"as": "carol", "with": "quality-assistant"})[1])["messages"] == []


def test_bad_chat_requests_are_refused_with_a_reason(shell):
    status, url, body = post(shell, "/chat", {"as": "carol", "to": "bob", "body": "hi"})
    assert status == 400 and "not a cog you can chat with" in json.loads(body)["error"]
    status, url, body = post(shell, "/chat", {"as": "carol", "to": "quality-assistant", "body": "  "})
    assert status == 400 and "say something" in json.loads(body)["error"]
    status, url, body = post(shell, "/chat", {"as": "quality-assistant", "to": "doc-reviewer", "body": "hi"})
    assert status in (400, 403)


def test_a_slow_cog_does_not_hold_up_the_rest_of_the_hub(shell):
    hub = shell["hub"]
    real = hub.model.complete

    def slow(**kw):
        time.sleep(2.0)
        return {"text": "done thinking", "stand_in": False, "served_model": "slow", "model": "slow"}

    hub.model.complete = slow
    result = {}
    try:
        t = threading.Thread(target=lambda: result.update(r=post(shell, "/chat", {"as": "carol", "to": "doc-reviewer", "body": "take your time"})))
        t.start()
        time.sleep(0.3)
        t0 = time.time()
        status, text = get(shell, "/inbox", **{"as": "bob"})
        assert status == 200 and time.time() - t0 < 1.5, "another person's page was served while the cog was thinking"
        t.join(10)
    finally:
        hub.model.complete = real
    assert json.loads(result["r"][2])["reply"]["body"] == "done thinking"


def test_without_a_model_the_cog_says_so_plainly(shell):
    hub = shell["hub"]
    line = hub.chat("carol", "doc-reviewer", "hello!")
    assert line["kind"] == "chat" and line["from"] == "doc-reviewer" and "Hi! I'm doc-reviewer." in line["body"]
