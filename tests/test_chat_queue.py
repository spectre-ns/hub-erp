"""A chat line goes to the cog's queue and jumps it: a person waiting at the messenger is answered before the
tasks already waiting there (the task the cog is on is finished first), and the page never waits on the model."""

import shutil

import pytest

from conftest import ROOT, release_all
from hubproto.app import Hub
from hubproto.workqueue import CogQueue


@pytest.fixture
def hub(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    return h


def test_a_chat_is_taken_before_older_tasks(tmp_path):
    q = CogQueue(tmp_path / "q.db", mode="service")
    old = q.submit("quality-assistant", "task", by="carol", title="a long task", payload={"goal": "x"})
    older = q.submit("quality-assistant", "message", by="bob", title="a message", payload={"msg": "m"})
    chat = q.submit("quality-assistant", "chat", by="carol", title="where is the test document?", payload={"text": "?"})
    assert q.position(chat) == 1 and q.position(old) == 2 and q.position(older) == 3
    assert q.claim()["id"] == chat["id"]
    # the cog is on the chat now; the others wait
    assert q.claim() is None
    q._set(chat["id"], status="done")
    assert q.claim()["id"] == old["id"]


def test_a_chat_waits_only_for_the_task_the_cog_is_on(tmp_path):
    q = CogQueue(tmp_path / "q.db", mode="service")
    running = q.submit("quality-assistant", "task", by="carol", title="running", payload={"goal": "x"})
    q.claim()
    q.submit("quality-assistant", "task", by="carol", title="queued", payload={"goal": "y"})
    chat = q.submit("quality-assistant", "chat", by="dave", title="hello", payload={"text": "hello"})
    assert q.get(running["id"])["status"] == "working" and q.position(chat) == 2  # the running task, then the chat


def test_the_chat_answer_lands_in_the_thread(hub):
    me = hub.inbox.chat_append(sender="carol", to="quality-assistant", body="Find the test document and give me a link")
    t = hub.queue.submit("quality-assistant", "chat", by="carol", title=me["body"], payload={"text": me["body"], "about": None, "line": me["id"]})
    assert t["status"] == "done", t.get("error")
    thread = hub.inbox.chat_thread("carol", "quality-assistant")
    assert [m["from"] for m in thread] == ["carol", "quality-assistant"] and thread[-1]["body"].strip()


def test_chats_keep_their_order_and_count_every_cogs_work_ahead(tmp_path, monkeypatch):
    monkeypatch.setenv("HUBPROTO_COG_WORKERS", "1")  # one thread works every cog: everything running counts
    q = CogQueue(tmp_path / "q.db", mode="service")
    other = q.submit("qa-manager", "task", by="bob", title="a long task", payload={"goal": "x"})
    q.claim()  # the one worker is on qa-manager's task
    first = q.submit("quality-assistant", "chat", by="carol", title="first", payload={"text": "1"})
    second = q.submit("doc-reviewer", "chat", by="dave", title="second", payload={"text": "2"})
    q.submit("quality-assistant", "task", by="carol", title="later task", payload={"goal": "y"})
    assert q.position(first) == 2 and q.position(second) == 3, "the running task, then the chats in the order asked"
    q._set(other["id"], status="done")
    assert [q.claim()["id"], q.claim()["id"]] == [first["id"], second["id"]]


def test_a_find_question_is_searched_before_the_model_answers(hub):
    from hubproto.app import chat_lookup
    from hubproto.tools import RecordTools

    doc = hub.dms.create(title="Test Document", doc_class="controlled", folder="Quality/Controlled Documents", by="carol",
                         content=b"# Test\n", filename="Test Document.md", mime="text/markdown", doc_type="sop")
    found = chat_lookup(RecordTools(hub, "carol").call, "Find the test document provide a link")
    assert found["arguments"]["type"] == "document" and doc["number"] in [r["number"] for r in found["result"]["records"]]
    assert chat_lookup(RecordTools(hub, "carol").call, "good morning!") is None


def test_with_a_worker_per_cog_only_that_cogs_work_is_ahead(tmp_path, monkeypatch):
    monkeypatch.delenv("HUBPROTO_COG_WORKERS", raising=False)
    q = CogQueue(tmp_path / "q.db", mode="service")
    q.submit("qa-manager", "task", by="bob", title="another cog's task", payload={"goal": "x"})
    q.claim()
    mine = q.submit("quality-assistant", "task", by="carol", title="queued", payload={"goal": "y"})
    chat = q.submit("quality-assistant", "chat", by="carol", title="hi", payload={"text": "hi"})
    assert q.position(chat) == 1 and q.position(mine) == 2


def test_the_model_gate_gives_chats_the_next_turn(monkeypatch):
    import threading
    import time

    from hubproto.model import ModelGate

    gate, order = ModelGate(1), []
    hold = threading.Event()

    def call(pri, who):
        with gate.turn(pri, who):
            order.append(who)
            if who == "running":
                hold.wait(5)

    threads = [threading.Thread(target=call, args=(0, "running"))]
    threads[0].start()
    time.sleep(0.1)
    for pri, who in ((0, "task-1"), (0, "task-2"), (10, "chat-1"), (10, "chat-2")):
        threads.append(threading.Thread(target=call, args=(pri, who)))
        threads[-1].start()
        time.sleep(0.05)
    assert gate.ahead("chat-1") == 1 and gate.ahead("chat-2") == 2 and gate.ahead("task-1") == 3
    hold.set()
    for t in threads:
        t.join(5)
    assert order == ["running", "chat-1", "chat-2", "task-1", "task-2"]


def test_a_searched_answer_says_what_it_looked_up():
    from hubproto.shell.messenger import looked_words

    assert looked_words([{"tool": "search_records", "arguments": {"query": "test", "type": "document"}}]) == "searched “test” documents"
    assert looked_words([{"tool": "search_records", "arguments": {}}]) == "searched"


def test_chat_replies_are_plain_text():
    from hubproto.app import plain_chat

    md = "1. **[DOC-0375](#)** - *Test Document* (open)\n## Next\nsee __this__"
    assert plain_chat(md) == "1. DOC-0375 - Test Document (open)\nNext\nsee this"
    assert plain_chat("2 * 3 = 6") == "2 * 3 = 6"
