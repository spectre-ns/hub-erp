"""Review 7 Nov 2026, AGENT, reliability: the inbox under two processes and torn lines (reliability-7, 8, 14), side
effects that survive a crash once (10), queue leases (11), a worker that survives its errors (12), version-unique WOPI
stamps (13), and a bounded model-call log."""

import json
import multiprocessing
import socket
import subprocess
import sys
import threading
import time
import types

import pytest

from conftest import release_all  # noqa: F401
from hubproto import model, wopi, workqueue
from hubproto.inbox import Inbox
from hubproto.workqueue import CogQueue, CogWorker


# ---------------------------------------------------------------- reliability-7, 8, 14: the inbox
def _appender(root: str, n: int) -> None:
    box = Inbox(root)
    for i in range(n):
        box.notify("alice", kind="t", subject=f"append-{i}", body="x", record="rec:ncr:1")


def _marker(root: str, msg_id: str, seconds: float) -> None:
    end = time.time() + seconds
    n = 0
    while time.time() < end:
        Inbox(root).mark(msg_id, seen=n)
        n += 1


def test_appends_in_one_process_survive_marks_in_another(tmp_path):
    first = Inbox(tmp_path).notify("carol", kind="t", subject="first", body="x")
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_appender, args=(str(tmp_path), 300)), ctx.Process(target=_marker, args=(str(tmp_path), first["id"], 2.0))]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
        assert p.exitcode == 0
    msgs = Inbox(tmp_path).all()
    assert len([m for m in msgs if m["subject"].startswith("append-")]) == 300
    assert Inbox(tmp_path).find(first["id"])["seen"] >= 1


def test_a_torn_line_from_before_does_not_take_the_chatter_down(tmp_path):
    good = {"id": "aaaa0001", "from": "alice", "to": "carol", "kind": "message", "record": "rec:ncr:1", "body": "first"}
    later = {"id": "aaaa0002", "from": "bob", "to": "carol", "kind": "message", "record": "rec:ncr:1", "body": "third"}
    (tmp_path / "messages.jsonl").write_text(json.dumps(good) + "\n" + '{"id": "aaaa00' + "\n" + json.dumps(later) + "\n")
    box = Inbox(tmp_path)
    assert [m["body"] for m in box.on_record("rec:ncr:1")] == ["first", "third"]
    assert (tmp_path / "messages.bad.jsonl").read_text().startswith('{"id": "aaaa00')
    box.post(sender="carol", to="alice", subject="s", body="fourth", record="rec:ncr:1")
    assert [m["body"] for m in Inbox(tmp_path).on_record("rec:ncr:1")] == ["first", "third", "fourth"]


def test_an_items_chatter_is_an_indexed_lookup(tmp_path):
    box = Inbox(tmp_path)
    with box._db() as db:
        plan = " ".join(r[-1] for r in db.execute("EXPLAIN QUERY PLAN SELECT data FROM messages WHERE record = ?", ("x",)))
    assert "messages_record" in plan


# ---------------------------------------------------------------- reliability-10: side effects once
def test_a_task_rerun_after_a_crash_writes_its_messages_once(tmp_path):
    box = Inbox(tmp_path)

    def run():
        workqueue._CURRENT.task, workqueue._CURRENT.n = "t-crash", 0
        try:
            box.post(sender="quality-assistant", to="carol", subject="Review", body="pass", record="rec:document:9")
            box.post(sender="quality-assistant", to="alice", subject="FYI", body="done", record="rec:document:9")
        finally:
            workqueue._CURRENT.task, workqueue._CURRENT.n = None, 0

    run()
    run()  # the same task worked again after the worker died
    assert [m["to"] for m in box.on_record("rec:document:9")] == ["carol", "alice"]


def _doc_with_pending_edit(hub, title):
    content, mime = wopi.blank_document("md")
    doc = hub.dms.create(title=title, doc_class="working", folder="Finance/Payables", by="alice", content=b"# Plan\nAudit yearly.\n",
                         filename=title + ".md", mime="text/markdown", doc_type="document")
    loc = doc["locator"]
    parent = hub.inbox._append({"id": f"{abs(hash(title)) % 16**8:08x}", "kind": "message", "from": "alice", "to": "quality-assistant",
                                "record": loc, "body": "@quality-assistant say twice a year", "at": "2026-11-07T10:00:00Z"})
    reply = hub.inbox._append({"id": f"{(abs(hash(title)) + 1) % 16**8:08x}", "kind": "reply", "from": "quality-assistant", "to": "alice",
                               "record": loc, "in_reply_to": parent["id"], "body": "Working on it",
                               "edit": {"kind": "text", "asked_by": "alice", "ops": [{"op": "replace", "target": "yearly", "with": "twice a year"}]}})
    return loc, reply


def test_apply_replies_after_a_crash_does_not_apply_the_edit_twice(released, monkeypatch):
    hub = released
    host = wopi.WopiHost(hub)
    loc, reply = _doc_with_pending_edit(hub, "Crash plan")
    before = len(hub.dms.versions(loc))
    real_mark = hub.inbox.mark

    def crash(*a, **k):
        raise RuntimeError("power cut")

    monkeypatch.setattr(hub.inbox, "mark", crash)
    try:
        host.apply_replies(hub.kernel.get(loc), "alice")
    except RuntimeError:
        pass
    monkeypatch.setattr(hub.inbox, "mark", real_mark)
    assert len(hub.dms.versions(loc)) == before + 1  # the version was saved, the reply not marked
    assert host.apply_replies(hub.kernel.get(loc), "alice") == []
    assert len(hub.dms.versions(loc)) == before + 1
    assert hub.inbox.find(reply["id"])["applied_in"] == before + 1
    content, _ = hub.dms.content(loc)
    assert content.decode().count("twice a year") == 1


def test_verify_edit_after_a_crash_does_one_fix(released, monkeypatch):
    hub = released
    host = wopi.WopiHost(hub)
    loc, reply = _doc_with_pending_edit(hub, "Verify plan")
    before = len(hub.dms.versions(loc))
    real_mark = hub.inbox.mark

    def crash(*a, **k):
        raise RuntimeError("power cut")

    monkeypatch.setattr(hub.inbox, "mark", crash)
    with pytest.raises(RuntimeError):
        host.verify_edit(hub.kernel.get(loc), reply["id"], "alice")  # the fix is saved, the reply not marked
    monkeypatch.setattr(hub.inbox, "mark", real_mark)
    assert len(hub.dms.versions(loc)) == before + 1
    again = host.verify_edit(hub.kernel.get(loc), reply["id"], "alice")
    assert again.get("already") and len(hub.dms.versions(loc)) == before + 1
    assert hub.dms.content(loc)[0].decode().count("twice a year") == 1


def test_an_edit_whose_asker_may_no_longer_edit_is_not_written(released):
    hub = released
    host = wopi.WopiHost(hub)
    loc, reply = _doc_with_pending_edit(hub, "Refused plan")
    hub.inbox.mark(reply["id"], edit={**reply["edit"], "asked_by": "dave"})
    before = len(hub.dms.versions(loc))
    host.apply_replies(hub.kernel.get(loc), "alice")
    assert len(hub.dms.versions(loc)) == before and "Could not" in hub.inbox.find(reply["id"])["body"]


# ---------------------------------------------------------------- reliability-11: leases
def test_recover_takes_back_only_expired_leases_or_dead_local_workers(tmp_path):
    q = CogQueue(tmp_path / "q.db", mode="service")
    a = q.submit("cog-a", "task", by="alice", title="a", payload={"goal": "x"})
    b = q.submit("cog-b", "task", by="alice", title="b", payload={"goal": "y"})
    c = q.submit("cog-c", "task", by="alice", title="c", payload={"goal": "z"})
    for _ in range(3):
        q.claim()
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    host = socket.gethostname()
    q._set(a["id"], worker="other-host:4242", lease_until=time.time() + 600)  # the cog-users service, alive
    q._set(b["id"], worker="other-host:4242", lease_until=time.time() - 1)    # stopped renewing
    q._set(c["id"], worker=f"{host}:{gone.pid}", lease_until=time.time() + 600)  # a process here that is gone
    assert q.recover() == 2
    assert q.get(a["id"])["status"] == "working" and q.get(b["id"])["status"] == "queued" and q.get(c["id"])["status"] == "queued"


def test_a_worker_that_lost_its_task_does_not_overwrite_the_result(tmp_path):
    q = CogQueue(tmp_path / "q.db", mode="service")
    t = q.submit("cog-a", "task", by="alice", title="a", payload={"goal": "x"})
    q.claim()
    q._set(t["id"], worker="other-host:1")  # taken over meanwhile
    q._finish(t["id"], status="done", result={"text": "late"})
    assert q.get(t["id"])["status"] == "working"


# ---------------------------------------------------------------- reliability-12: the worker survives
def test_the_worker_survives_an_error_and_a_dead_worker_shows_as_stalled(tmp_path):
    q = CogQueue(tmp_path / "q.db", mode="worker")
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        return None

    q.work_one = flaky
    hub = types.SimpleNamespace(queue=q)
    w = CogWorker(hub, threading.RLock(), threads=1).start()
    q.wake.set()
    deadline = time.time() + 15
    while time.time() < deadline and calls["n"] < 2:
        q.wake.set()
        time.sleep(0.1)
    state = q.workers["cog-worker-0"]
    assert calls["n"] >= 2 and state["errors"] == 1 and "database is locked" in state["last_error"] and state["alive"]
    w.shutdown()
    for t in w._threads:
        t.join(10)
    t = q.submit("cog-a", "task", by="alice", title="waits", payload={"goal": "x"})
    q._set(t["id"], enqueued_at="2026-01-01T00:00:00Z")
    h = q.health()
    assert h["stalled"] and h["oldest_queued_seconds"] > 60 and not h["workers"]["cog-worker-0"]["alive"]


# ---------------------------------------------------------------- reliability-13: version-unique stamps
def test_two_versions_saved_in_the_same_second_have_different_stamps():
    v1 = {"at": "2026-11-07T10:00:00Z", "version": 3, "by": "alice"}
    v2 = {"at": "2026-11-07T10:00:00Z", "version": 4, "by": "hub"}
    assert wopi.version_stamp(v1) != wopi.version_stamp(v2)
    assert wopi.version_stamp(v1).endswith(".000003Z")
    # An editor that saw version 3 saves after the hub wrote version 4 in the same second: it is told to reload.
    assert wopi.is_stale_save(v2, wopi.version_stamp(v1), {"quality-assistant"})
    assert not wopi.is_stale_save(v2, wopi.version_stamp(v2), {"quality-assistant"})


# ---------------------------------------------------------------- growth: the model-call log is bounded
def test_the_model_gateway_keeps_a_bounded_call_log(monkeypatch, capsys):
    monkeypatch.setattr(model, "MAX_CALLS_KEPT", 5)
    g = model.ModelGateway()
    for i in range(8):
        g.complete(model="stand-in", system="s", user=f"u{i}", by="quality-assistant")
    capsys.readouterr()
    assert len(g.calls) == 5 and g.calls[-1]["prompt_digest"] and list(g.calls)[-1] is g.calls[-1]
