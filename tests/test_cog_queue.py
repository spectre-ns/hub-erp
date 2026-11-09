"""Cogs work from a queue, like people from a tray: asking one returns at once, it takes one task at a time, oldest
first, and its answer lands where it was asked (the chatter, the comment thread, the Gate)."""

import json
import shutil
import threading
import time
from urllib.parse import quote

import pytest

from conftest import ROOT, SMALL_BILL, dated, release_all
from hubproto import model, wopi
from hubproto.app import Hub
from hubproto.shell import serve_in_thread
from hubproto.workqueue import CogQueue, CogWorker
from test_editor import docx_with_comment, wopi_call
from test_shell_views import get, post


@pytest.fixture(scope="module")
def shell(tmp_path_factory):
    root = tmp_path_factory.mktemp("hub")
    shutil.copytree(ROOT / "frames", root / "frames")
    hub = Hub(root)
    hub.kernel.seed()
    release_all(hub)
    server = serve_in_thread(hub, "127.0.0.1", 0)  # port 0: bound at once, no race with other test workers
    port = server.server_address[1]
    yield {"hub": hub, "root": root, "base": f"http://127.0.0.1:{port}", "port": port, "server": server}
    server.shutdown()
    server.server_close()


@pytest.fixture
def queued(shell):
    """Nobody works the queue until the test says so (as when the cog-users service is behind)."""
    hub = shell["hub"]
    hub.queue.drain()
    hub.queue.mode = "service"
    yield hub
    hub.queue.mode = "inline"
    hub.queue.drain()


def new_doc(hub, title="Queue test procedure"):
    content, mime = wopi.blank_document("docx")
    return hub.dms.create(title=title, doc_class="working", folder="Finance/Payables", by="alice", content=content,
                          filename=title + ".docx", mime=mime, doc_type="document")


def test_asking_a_cog_returns_at_once_and_its_answer_lands_in_the_chatter_later(shell, queued):
    hub = queued
    doc = new_doc(hub)
    page = "/document/" + quote(doc["locator"], safe=":")
    status, url, text = post(shell, "/msg", {"as": "alice", "to": "quality-assistant", "subject": "Review", "record": doc["locator"],
                                             "body": "@quality-assistant please review this document", "return": page + "?as=alice"})
    assert status == 200 and "quality-assistant: next in its queue" in text and "you can carry on" in text
    [task] = hub.queue.open_tasks(record=doc["locator"])
    assert task["cog"] == "quality-assistant" and task["kind"] == "mention" and task["by"] == "alice" and task["status"] == "queued"
    assert not [m for m in hub.inbox.on_record(doc["locator"]) if m.get("kind") == "reply"]
    # The chatter shows it waiting, and the page knows to keep asking.
    status, text = get(shell, page, **{"as": "alice"})
    log = text.split('id="chatter-log"')[1].split("</ol>")[0]
    assert "kind-working" in log and "in its queue" in log and "answer a mention" in log and "Withdraw" in log
    assert "id='live'" in text and "data-open='1'" in text
    before = json.loads(get(shell, "/queue-status", **{"as": "alice", "record": doc["locator"]})[1])
    assert before["open"] == 1
    # The cog gets to it: the answer is on the chatter and the waiting entry is gone.
    [done] = hub.queue.drain()
    assert done["status"] == "done" and done["result"]["replies"][0]["from"] == "quality-assistant"
    after = json.loads(get(shell, "/queue-status", **{"as": "alice", "record": doc["locator"]})[1])
    assert after["open"] == 0 and after["stamp"] != before["stamp"]
    status, text = get(shell, page, **{"as": "alice"})
    log = text.split('id="chatter-log"')[1].split("</ol>")[0]
    assert "kind-working" not in log and "Review of " + doc["number"] + " by quality-assistant" in log


def test_a_comment_saved_in_the_editor_is_queued_and_the_save_does_not_wait(shell, queued):
    hub = queued
    doc = new_doc(hub, "Queue comment doc")
    token = wopi.issue_token("alice", doc["number"], can_write=True, root=shell["root"])
    t0 = time.monotonic()
    status, body, _ = wopi_call(shell, f"/wopi/files/{doc['number']}/contents", token, "POST",
                                docx_with_comment("@quality-assistant what does this procedure cover?", author="alice"), {"X-WOPI-Override": "PUT"})
    assert status == 200 and time.monotonic() - t0 < 5
    [task] = hub.queue.open_tasks(record=doc["locator"])
    assert task["kind"] == "mention" and task["by"] == "alice"
    j = json.loads(get(shell, "/editor-replies/" + quote(doc["locator"], safe=":"), **{"as": "alice"})[1])
    assert j["pending"] == 0 and j["queue"] == ["quality-assistant is next to look at it"]
    hub.queue.drain()
    j = json.loads(get(shell, "/editor-replies/" + quote(doc["locator"], safe=":"), **{"as": "alice"})[1])
    assert j["queue"] == [] and j["replies"] and j["replies"][0]["from"] == "quality-assistant"


def test_an_item_waiting_for_a_cog_goes_in_its_queue(shell, queued):
    hub = queued
    run_id = hub.runner.submit("purchase-to-pay", {"record": dated({**SMALL_BILL, "po_ref": "PO-1001"})}, owner="alice")
    state = hub.runner.load(run_id)
    if state["status"] != "waiting":
        pytest.skip("the seeded PO was billed already in this module")
    assert state["pending"]["assignees"] == ["ap-reviewer"]
    [task] = hub.queue.open_tasks(cog="ap-reviewer")
    assert task["kind"] == "gate" and task["payload"]["run"] == run_id
    # Asking again (a sweep) does not queue it twice.
    assert hub.runner.give_to_cog(state) and len(hub.queue.open_tasks(cog="ap-reviewer")) == 1
    status, text = get(shell, "/user/ap-reviewer", **{"as": "bob"})
    section = text.split("id='cog-work'")[1].split("</section>")[0]
    assert "take its turn at a Gate" in section and "next" in section
    hub.queue.drain()
    assert hub.runner.load(run_id)["status"] == "completed"


def test_one_task_at_a_time_per_cog_oldest_first(tmp_path):
    q = CogQueue(tmp_path / "q.db", mode="service")
    a1 = q.submit("cog-a", "task", by="alice", title="first", payload={"goal": "x"})
    a2 = q.submit("cog-a", "task", by="alice", title="second", payload={"goal": "y"})
    b1 = q.submit("cog-b", "task", by="bob", title="other", payload={"goal": "z"})
    assert q.position(a1) == 1 and q.position(a2) == 2 and q.position(b1) == 1
    assert q.claim()["id"] == a1["id"]
    assert q.claim()["id"] == b1["id"]  # cog-a is busy: its second task waits; cog-b works side by side
    assert q.claim() is None
    assert q.position(q.get(a2["id"])) == 2 and q.counts() == {"cog-a": {"working": 1, "queued": 1}, "cog-b": {"working": 1}}
    # A worker that stopped mid-task: the task goes back in the queue.
    assert q.recover() == 0  # a live worker (this process, a fresh lease) keeps its tasks
    with q._db() as db:  # its lease runs out (it stopped renewing): now they go back in the queue
        db.execute("UPDATE tasks SET lease_until = 0 WHERE status = 'working'")
    assert q.recover() == 2 and q.get(a1["id"])["status"] == "queued"
    with pytest.raises(ValueError):
        q.submit("cog-a", "nonsense", by="alice", title="?")


def test_only_the_asker_or_a_policy_owner_withdraws_a_queued_task(shell, queued):
    hub = queued
    task = hub.queue.submit("purchasing-assistant", "task", by="alice", title="count the open POs", payload={"goal": "count the open POs"})
    status, url, text = post(shell, "/queue-withdraw", {"as": "dave", "task": task["id"], "return": "/user/purchasing-assistant?as=dave"})
    assert status == 403 and hub.queue.get(task["id"])["status"] == "queued"
    status, url, text = post(shell, "/queue-withdraw", {"as": "alice", "task": task["id"], "return": "/user/purchasing-assistant?as=alice"})
    assert status == 200 and "Withdrawn from purchasing-assistant" in text and hub.queue.get(task["id"])["status"] == "withdrawn"


def test_a_task_given_on_the_profile_goes_in_the_queue(shell, queued):
    hub = queued
    status, url, text = post(shell, "/agent-task", {"as": "alice", "cog": "quality-assistant", "goal": "list the open nonconformances"})
    assert status == 200 and "/user/quality-assistant" in url and "Given to quality-assistant: it is next" in text
    [task] = hub.queue.open_tasks(cog="quality-assistant")
    assert task["kind"] == "task"
    [done] = hub.queue.drain()
    assert done["status"] == "done" and done["result"]["run"]
    status, text = get(shell, "/queue", **{"as": "alice"})
    assert status == 200 and "list the open nonconformances" in text and "see the run" in text


def test_a_failed_task_tells_the_person_who_asked(shell, queued):
    hub = queued
    task = hub.queue.submit("quality-assistant", "mention", by="alice", title="gone", payload={"msg": "no-such-message"})
    [done] = hub.queue.drain()
    assert done["id"] == task["id"] and done["status"] == "failed" and "is gone" in done["error"]
    note = [m for m in hub.inbox.all() if m["to"] == "alice" and m.get("kind") == "cog-task"][-1]
    assert "could not answer a mention" in note["subject"]


def test_the_worker_lets_go_of_the_hub_lock_while_a_model_thinks(monkeypatch):
    lock = threading.RLock()
    seen = {}

    class Slow:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout=None):
        def page():  # another thread, as a page request: can it take the lock while the model thinks?
            if lock.acquire(timeout=1):
                lock.release()
                seen["other"] = True
        t = threading.Thread(target=page)
        t.start()
        t.join()
        return Slow()

    monkeypatch.setattr(model.urllib.request, "urlopen", fake_urlopen)
    with lock, lock, model.thinking_releases(lock):  # held twice, as a nested caller would
        assert model._fetch_json(object(), 1) == {"ok": True}
        assert lock._is_owned()  # taken back before the cog reads or writes again
    assert seen["other"] is True


def test_a_worker_thread_answers_in_the_background(shell):
    hub = shell["hub"]
    hub.queue.drain()
    hub.queue.mode = "worker"
    worker = CogWorker(hub, hub.lock).start()
    try:
        doc = new_doc(hub, "Worker thread doc")
        page = "/document/" + quote(doc["locator"], safe=":")
        status, url, text = post(shell, "/msg", {"as": "alice", "to": "quality-assistant", "subject": "Q", "record": doc["locator"],
                                                 "body": "@quality-assistant please review this document", "return": page + "?as=alice"})
        assert status == 200
        for _ in range(100):
            if not hub.queue.open_tasks(record=doc["locator"]):
                break
            time.sleep(0.1)
        [task] = hub.queue.list(record=doc["locator"])
        assert task["status"] == "done"
        assert any(m["from"] == "quality-assistant" and m.get("kind") == "reply" for m in hub.inbox.on_record(doc["locator"]))
    finally:
        worker.shutdown()
        hub.queue.mode = "inline"


def test_a_question_in_the_chatter_gets_an_answer_not_a_review(shell):
    hub = shell["hub"]
    doc = new_doc(hub, "Question doc")
    hub.send("alice", "alice", "Question", "@quality-assistant what does this procedure cover?", record=doc["locator"])
    reply = [m for m in hub.inbox.on_record(doc["locator"]) if m.get("kind") == "reply" and m["from"] == "quality-assistant"][-1]
    assert not reply["body"].startswith("Review of") and "Noted." in reply["body"]
    assert not [L for L in hub.kernel.get(doc["locator"])["links"] if L.get("rel") == "has-document"]  # nothing filed


def test_a_cog_has_a_kanban_board_of_its_work(tmp_path):
    """Like a person's board: what is in the cog's queue (in the order it takes it), what it works on, what waits on
    others, what is assigned to it, done and stopped."""
    import shutil

    from conftest import ROOT, release_all
    from hubproto.app import Hub
    from hubproto.shell import serve_in_thread
    from test_shell_views import get, main_of

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    hub.queue.mode = "service"
    hub.queue.submit("quality-assistant", "task", by="carol", title="Check the open NCRs", payload={"goal": "x"})
    hub.queue.submit("quality-assistant", "chat", by="dave", title="where is the test document?", payload={"text": "?"})
    server = serve_in_thread(hub, "127.0.0.1", 0)  # port 0: bound at once, no race with other test workers
    port = server.server_address[1]
    try:
        shell = {"hub": hub, "base": f"http://127.0.0.1:{port}"}
        status, text = get(shell, "/board/quality-assistant", **{"as": "carol"})
        body = main_of(text)
        assert status == 200 and "quality-assistant's board" in body
        for col in ("In its queue", "Working on", "Waiting on others", "Assigned to it", "Done", "Stopped"):
            assert col in body
        first = body.index("where is the test document?")
        assert first < body.index("Check the open NCRs"), "the chat is first in line"
        status, text = get(shell, "/boards", **{"as": "carol"})
        assert "/board/quality-assistant" in text
        status, text = get(shell, "/board/carol", **{"as": "carol"})
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
