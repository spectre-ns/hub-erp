"""Cogs work from a queue, the way people work from a tray.

Everything asked of a cog lands here as a task: a comment or message that mentions it, a message sent to it, an item
waiting for it at a Gate, a task a person gives it, a standing duty. Asking returns at once; the cog takes its tasks
one at a time, oldest first, and answers where it was asked (the chatter, the comment thread in the document, the
Gate). Different cogs work side by side; one cog never works on two things at once. A chat line jumps the queue:
someone is waiting at the messenger, so it is taken before anything else queued (the task a cog is already working
on is finished first).

The queue is a small SQLite table (``state/queue.db``) so a worker in another process (the cog-users service) can
share it. How tasks get worked depends on ``HUBPROTO_COG_QUEUE``:

* ``inline`` (the default for the library, the CLI and the tests): a task is worked the moment it is queued, so the
  caller sees the result as before.
* ``worker``: a thread in the shell process works the queue (``hubproto shell`` uses this unless told otherwise).
  It holds the hub lock while it reads and writes and lets go of it while the model thinks, so pages keep loading.
* ``service``: the shell only queues; ``hubproto cog-users --loop 5`` works the queue in another process.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from pathlib import Path

from .kernel import utcnow

KINDS = {"mention": "answer a mention", "message": "answer a message", "gate": "take its turn at a Gate",
         "task": "do a task", "duty": "do a standing duty", "assigned": "work an item assigned to it", "chat": "answer a chat",
         "lens": "do an adversarial review", "automation": "run an automation", "co-review": "review a change order"}
PRIORITY = {"chat": 10}  # higher is taken first; everything else is 0 (oldest first)
OPEN = ("queued", "working")
MAX_ATTEMPTS = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, seq INTEGER, cog TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
  title TEXT, by TEXT, record TEXT, key TEXT, payload TEXT, result TEXT, error TEXT,
  enqueued_at TEXT, started_at TEXT, finished_at TEXT, attempts INTEGER DEFAULT 0, worker TEXT
);
CREATE INDEX IF NOT EXISTS tasks_open ON tasks(status, cog, seq);
CREATE INDEX IF NOT EXISTS tasks_record ON tasks(record);
"""


def queue_mode() -> str:
    mode = (os.environ.get("HUBPROTO_COG_QUEUE") or "").strip().lower()
    if not mode and (os.environ.get("HUBPROTO_COG_USERS") or "").lower() == "service":
        mode = "service"
    return mode if mode in ("inline", "worker", "service") else "inline"


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    t = dict(r)
    for k in ("payload", "result"):
        t[k] = json.loads(t[k]) if t.get(k) else ({} if k == "payload" else None)
    return t


class CogQueue:
    def __init__(self, path: Path, hub=None, mode: str | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.hub = hub
        self.mode = mode or queue_mode()
        self.wake = threading.Event()
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        # One worker per cog by default (cogs work side by side; the model gate serializes their model calls). With
        # HUBPROTO_COG_WORKERS=1 one thread works every cog's tasks, so a position counts every cog's work.
        self.lanes = int(os.environ.get("HUBPROTO_COG_WORKERS") or 0) or 1000
        with self._db() as db:
            db.executescript(SCHEMA)
            cols = {r[1] for r in db.execute("PRAGMA table_info(tasks)").fetchall()}
            for col, decl in (("lease_until", "REAL"), ("priority", "INTEGER DEFAULT 0")):
                if col not in cols:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {col} {decl}")
        self.workers: dict[str, dict] = {}  # this process's worker threads: name -> {"alive", "last_ok", "last_error"}

    # ------------------------------------------------------------ storage
    @contextmanager
    def _db(self):
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    def get(self, task_id: str) -> dict | None:
        with self._db() as db:
            return _row(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def _set(self, task_id: str, **fields) -> dict:
        for k in ("payload", "result"):
            if k in fields and not isinstance(fields[k], str):
                fields[k] = json.dumps(fields[k], default=str)
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._db() as db:
            db.execute(f"UPDATE tasks SET {cols} WHERE id=?", (*fields.values(), task_id))
        return self.get(task_id)

    # ------------------------------------------------------------ asking
    def submit(self, cog: str, kind: str, *, by: str, title: str, record: str | None = None, payload: dict | None = None,
               key: str | None = None) -> dict:
        """Put a task in the cog's queue. ``key`` makes it idempotent: while a task with the same key is open, asking
        again returns that task. In inline mode the task is worked before this returns."""
        if kind not in KINDS:
            raise ValueError(f"unknown kind of cog task {kind!r}")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if key:
                same = db.execute("SELECT * FROM tasks WHERE key=? AND status IN ('queued','working')", (key,)).fetchone()
                if same:
                    db.execute("COMMIT")
                    return _row(same)
            seq = (db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM tasks").fetchone()[0])
            tid = uuid.uuid4().hex[:10]
            db.execute("INSERT INTO tasks(id,seq,cog,kind,status,title,by,record,key,payload,enqueued_at,priority) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (tid, seq, cog, kind, "queued", (title or "")[:300], by, record, key, json.dumps(payload or {}, default=str), utcnow(),
                        PRIORITY.get(kind, 0)))
            db.execute("COMMIT")
        self.wake.set()
        if self.mode == "inline":
            return self.work_one(tid)
        return self.get(tid)

    def cancel(self, task_id: str, by: str, why: str = "") -> dict:
        t = self.get(task_id)
        if t is None:
            raise ValueError(f"there is no cog task {task_id!r}")
        if t["status"] != "queued":
            raise ValueError(f"the task is {t['status']}; only a task still waiting in the queue can be withdrawn")
        return self._set(task_id, status="withdrawn", finished_at=utcnow(), error=f"withdrawn by {by}" + (f": {why}" if why else ""))

    # ------------------------------------------------------------ reading
    def list(self, *, cog: str | None = None, record: str | None = None, status: tuple[str, ...] | None = None,
             limit: int = 50, newest_first: bool = False) -> list[dict]:
        where, args = [], []
        if cog:
            where.append("cog=?")
            args.append(cog)
        if record:
            where.append("record=?")
            args.append(record)
        if status:
            where.append("status IN (" + ",".join("?" * len(status)) + ")")
            args += list(status)
        sql = "SELECT * FROM tasks" + (" WHERE " + " AND ".join(where) if where else "") + \
              f" ORDER BY seq {'DESC' if newest_first else 'ASC'} LIMIT ?"
        with self._db() as db:
            return [_row(r) for r in db.execute(sql, (*args, limit)).fetchall()]

    def open_tasks(self, *, cog: str | None = None, record: str | None = None) -> list[dict]:
        return self.list(cog=cog, record=record, status=OPEN, limit=500)

    def position(self, task: dict) -> int:
        """1 = it is next; n = n-1 tasks will be worked before it: the ones being worked now, and queued ones taken
        first (a higher priority, or the same priority and older: chats keep their order among themselves). With one
        worker thread (the default) every cog shares it, so the count is across all cogs; with more, per cog."""
        if task["status"] == "working":
            return 0
        shared = self.lanes <= 1
        scope = "" if shared else "cog=? AND "
        args = () if shared else (task["cog"],)
        with self._db() as db:
            pri = task.get("priority") or 0
            ahead = db.execute(f"SELECT COUNT(*) FROM tasks WHERE {scope}(status='working' OR (status='queued' AND "
                               "(COALESCE(priority,0)>? OR (COALESCE(priority,0)=? AND seq<?))))",
                               (*args, pri, pri, task["seq"])).fetchone()[0]
        return ahead + 1

    def counts(self) -> dict[str, dict[str, int]]:
        with self._db() as db:
            rows = db.execute("SELECT cog, status, COUNT(*) n FROM tasks WHERE status IN ('queued','working') GROUP BY cog, status").fetchall()
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["cog"], {})[r["status"]] = r["n"]
        return out

    def stamp(self, record: str | None = None, cog: str | None = None) -> str:
        """Changes whenever a task (on this record, of this cog) is queued, started or finished: the page polls it."""
        where, args = [], []
        for col, val in (("record", record), ("cog", cog)):
            if val:
                where.append(f"{col}=?")
                args.append(val)
        with self._db() as db:
            q = "SELECT COUNT(*), MAX(seq), SUM(status='working'), SUM(status IN ('queued','working')), MAX(finished_at), MAX(started_at) FROM tasks"
            r = db.execute(q + (" WHERE " + " AND ".join(where) if where else ""), args).fetchone()
        return ":".join(str(x or 0) for x in r)

    # ------------------------------------------------------------ working
    def claim(self, cogs: list[str] | None = None) -> dict | None:
        """The next queued task of a cog that is not already working on something: chats first, then oldest first."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            busy = {r[0] for r in db.execute("SELECT cog FROM tasks WHERE status='working'").fetchall()}
            for r in db.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY COALESCE(priority,0) DESC, seq").fetchall():
                if r["cog"] in busy or (cogs and r["cog"] not in cogs):
                    continue
                db.execute("UPDATE tasks SET status='working', started_at=?, attempts=attempts+1, worker=?, lease_until=? WHERE id=?",
                           (utcnow(), self.worker_id, time.time() + LEASE, r["id"]))
                db.execute("COMMIT")
                return self.get(r["id"])
            db.execute("COMMIT")
        return None

    def recover(self) -> int:
        """Tasks left 'working' by a worker that stopped go back in the queue (or fail after MAX_ATTEMPTS). A task is
        taken back only when its lease ran out (its worker stopped renewing it) or its worker was a process on this
        host that is gone; a live worker's task, here or in the cog-users service, is never taken."""
        n = 0
        now = time.time()
        host = socket.gethostname()
        for t in self.list(status=("working",), limit=500):
            worker = str(t.get("worker") or "")
            w_host, _, w_pid = worker.rpartition(":")
            dead_here = w_host == host and w_pid.isdigit() and int(w_pid) != os.getpid() and not _pid_alive(int(w_pid))
            expired = (t.get("lease_until") or 0) < now
            if not (dead_here or expired):
                continue
            if t["attempts"] >= MAX_ATTEMPTS:
                self._set(t["id"], status="failed", finished_at=utcnow(), error="the worker stopped while working on it, three times")
            else:
                self._set(t["id"], status="queued", started_at=None, worker=None, lease_until=None)
            n += 1
        return n

    def renew(self, task_id: str) -> None:
        with self._db() as db:
            db.execute("UPDATE tasks SET lease_until=? WHERE id=? AND worker=? AND status='working'", (time.time() + LEASE, task_id, self.worker_id))

    def _finish(self, task_id: str, **fields) -> dict:
        """The final write of a task, only if this worker still holds it (another may have taken it over)."""
        for k in ("payload", "result"):
            if k in fields and not isinstance(fields[k], str):
                fields[k] = json.dumps(fields[k], default=str)
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._db() as db:
            cur = db.execute(f"UPDATE tasks SET {cols} WHERE id=? AND (worker=? OR worker IS NULL)", (*fields.values(), task_id, self.worker_id))
        if cur.rowcount == 0:
            _log(f"cog task {task_id}: another worker holds it now; this result is not written")
        return self.get(task_id)

    def health(self) -> dict:
        """For /queue and /healthz: are the worker threads alive, and how old is the oldest task still waiting?"""
        oldest = None
        with self._db() as db:
            r = db.execute("SELECT MIN(enqueued_at) FROM tasks WHERE status='queued'").fetchone()
            oldest = r[0] if r else None
        age = None
        if oldest:
            try:
                from datetime import datetime, timezone

                age = int((datetime.now(timezone.utc) - datetime.fromisoformat(oldest.replace("Z", "+00:00"))).total_seconds())
            except ValueError:
                age = None
        workers = {k: dict(v) for k, v in self.workers.items()}
        stalled = self.mode == "worker" and (not workers or not any(w.get("alive") for w in workers.values()))
        return {"mode": self.mode, "workers": workers, "oldest_queued_seconds": age, "stalled": bool(stalled and age and age > 60)}

    def work_one(self, task_id: str | None = None, cogs: list[str] | None = None) -> dict | None:
        """Work one task (the given one, or the next one claimable). Returns the finished task."""
        if task_id:
            t = self.get(task_id)
            if t is None or t["status"] != "queued":
                return t
            t = self._set(task_id, status="working", started_at=utcnow(), attempts=(t["attempts"] or 0) + 1, worker=self.worker_id,
                          lease_until=time.time() + LEASE)
        else:
            t = self.claim(cogs)
            if t is None:
                return None
        stop = threading.Event()
        renewer = threading.Thread(target=self._renew_loop, args=(t["id"], stop), daemon=True)
        renewer.start()
        prev = (getattr(_CURRENT, "task", None), getattr(_CURRENT, "n", 0))
        _CURRENT.task, _CURRENT.n = t["id"], 0
        from .model import model_priority

        try:
            with model_priority(t.get("priority") or PRIORITY.get(t["kind"], 0), t["id"]):
                result = execute(self.hub, t)
            done = self._finish(t["id"], status="done", finished_at=utcnow(), result=result, lease_until=None)
        except Exception as e:  # the task fails; the person who asked is told why
            detail = str(e) or e.__class__.__name__
            done = self._finish(t["id"], status="failed", finished_at=utcnow(), error=detail[:1000], lease_until=None)
            _log(f"cog task {t['id']} ({t['cog']} {t['kind']}) failed: {detail}\n{traceback.format_exc(limit=4)}")
            if t["kind"] == "chat" and self.hub is not None:  # the person is told in the chat itself
                try:
                    p = t.get("payload") or {}
                    self.hub.inbox.chat_append(sender=t["cog"], to=t["by"], body=f"Sorry, I could not answer that: {detail[:300]}",
                                               about=p.get("about"), stand_in=True)
                except Exception:
                    pass
            elif t.get("by") and self.hub is not None and t["by"] != t["cog"]:
                try:
                    self.hub.inbox.notify(t["by"], kind="cog-task", subject=f"{t['cog']} could not {KINDS[t['kind']]}: {t['title'][:80]}",
                                          body=detail, record=t.get("record"))
                except Exception:
                    pass
        finally:
            stop.set()
            _CURRENT.task, _CURRENT.n = prev
        _log(f"cog task {done['id']} {done['cog']} {done['kind']} {done['status']} record={done.get('record') or '-'}")
        return done

    def _renew_loop(self, task_id: str, stop: threading.Event) -> None:
        while not stop.wait(LEASE / 3):
            try:
                self.renew(task_id)
            except Exception as e:  # a missed renewal is retried; the lease is long
                _log(f"cog task {task_id}: lease renewal failed: {e}")

    def drain(self, cogs: list[str] | None = None, limit: int = 1000) -> list[dict]:
        out = []
        while len(out) < limit:
            t = self.work_one(cogs=cogs)
            if t is None:
                break
            out.append(t)
        return out


# Seconds a worker holds a task without renewing it; renewed every LEASE/3 by a thread of its own while the task runs
# (also while the model thinks). It is short because it is also how a task held by a worker that is gone comes back:
# a redeploy replaces the container, so the new hub (another hostname) cannot tell the old worker is dead and waits
# for its lease to run out (then for the next recovery check, every LEASE/3): about 3 minutes, not 20.
LEASE = float(os.environ.get("HUBPROTO_COG_LEASE") or 120)
_CURRENT = threading.local()


def current_task() -> str | None:
    """The cog task this thread is working, if any (writes made during it get ids derived from it)."""
    return getattr(_CURRENT, "task", None)


def effect_id(kind: str = "msg") -> str | None:
    """A deterministic id for the next write of the current task: a re-run after a crash produces the same ids, so
    its replies and notes are not written twice (the inbox ignores an id it already has)."""
    t = current_task()
    if not t:
        return None
    _CURRENT.n = getattr(_CURRENT, "n", 0) + 1
    import hashlib

    return hashlib.sha256(f"{t}:{kind}:{_CURRENT.n}".encode()).hexdigest()[:8]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _log(line: str) -> None:
    import sys

    sys.stdout.write(line.rstrip() + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------- what each kind of task does
def execute(hub, task: dict) -> dict:
    p = task.get("payload") or {}
    kind = task["kind"]
    cog = task["cog"]
    if kind == "mention":
        from . import mentions

        msg = hub.inbox.find(p["msg"])
        if msg is None:
            raise ValueError(f"the message {p['msg']} is gone")
        replies = mentions.answer(hub, msg, cog, actions=p.get("actions") or [])
        return {"replies": replies}
    if kind == "message":
        msg = hub.inbox.find(p["msg"])
        if msg is None:
            raise ValueError(f"the message {p['msg']} is gone")
        return {"replies": [hub.inbox.answer_as_cog(msg, cog, hub)]}
    if kind == "gate":
        state = hub.runner.load(p["run"])
        pending = state.get("pending") or {}
        if state["status"] != "waiting" or cog not in pending.get("assignees", []) or pending.get("step") != p.get("step", pending.get("step")):
            return {"skipped": f"the item is no longer waiting for {cog} there (it is {state['status']})", "run": p["run"]}
        if hub.runner.is_assigned_work(state, cog):
            # Assigned the step itself (a task filed for the cog): it does the work, then completes it or hands it back.
            return assigned_work(hub, task, state=state)
        after = hub.runner.cog_decide(p["run"], cog)
        last = next((e for e in reversed(hub.runner.track(p["run"])) if e["type"] in ("gate.decided", "gate.declined", "gate.signed")), {})
        return {"run": p["run"], "outcome": last.get("type", "").split(".")[-1], "status": after["status"],
                "waiting_for": (after.get("pending") or {}).get("assignees", [])}
    if kind == "assigned":
        return assigned_work(hub, task)
    if kind == "automation":
        from . import automation

        job = automation.run(hub, p["job"])
        return {"job": job["id"], "status": job["status"], "outcome": job.get("outcome")}
    if kind == "co-review":  # a panel review of a change order's delta (hubproto.changecontrol)
        from . import changecontrol

        return changecontrol.cog_review(hub, p["co"], int(p["round"]), cog)
    if kind == "lens":
        return hub.runner.cog_lens(p["run"], cog, p["lens"], int(p.get("i") or 1), int(p.get("n") or 1), list(p.get("lenses") or [p["lens"]]))
    if kind == "chat":

        sender = task.get("by") or ""
        run_id = f"chat-{task['id']}"
        # The cog's governed tools: it reads what both it and the asker may read, and acts within its autonomy and both their rights.
        tools, specs, mode, store = hub.chat_tools(cog, sender, run_id, p["text"], p.get("about"))
        prep = hub.chat_prepare(sender, cog, p["text"], p.get("about"), tools_now=specs, mode=mode)
        thread = hub.inbox.chat_thread(sender, cog)
        cut = next((i for i, m in enumerate(thread) if m.get("id") == p.get("line")), len(thread))
        prep["history"] = [{"role": "assistant" if m["from"] == cog else "user", "content": m["body"]} for m in thread[:cut][-12:]]
        ans = hub.chat_answer(prep, sender, p["text"], call_tool=tools.execute, specs=specs)
        body = ans["text"]
        failed = [e for e in store.load(run_id) if e.get("type") == "tool" and e.get("write") and not e.get("ok")]
        if tools.proposed:
            body += f"\n\n{len(tools.proposed)} action(s) wait for your confirmation: /agent-run/{run_id}"
        store.append(run_id, {"type": "finished", "status": "proposed" if tools.proposed else "done", "summary": body[:2000],
                              "actions": tools.actions, "proposed": tools.proposed, "model": ans["model"],
                              "refused": [f"{e['tool']}: {(e.get('result') or '')[:160]}" for e in failed[:6]]})
        line = hub.inbox.chat_append(sender=cog, to=sender, body=body, about=prep["about"], model=ans["model"],
                                     stand_in=ans["stand_in"], tools=ans["tool_calls"])
        return {"line": line.get("id"), "model": ans["model"], "stand_in": ans["stand_in"], "tools": len(ans["tool_calls"]),
                "run": run_id, "actions": tools.actions, "proposed": len(tools.proposed)}
    if kind in ("task", "duty"):
        from .agent import Agent, mark_duty

        agent = Agent(hub, cog)
        earlier = agent.resume_or_none(f"q{task['id']}", by=task.get("by") or cog)
        if earlier is not None:
            return earlier
        prep = agent.prepare(p["goal"], by=task.get("by") or cog, trigger=kind, run_id=f"q{task['id']}")
        res = agent.conclude(prep, agent.think_safely(prep))
        if kind == "duty":
            mark_duty(hub, cog, p["goal"])
        return {"run": res["run"], "status": res["status"], "summary": res["summary"], "actions": res["actions"]}
    raise ValueError(f"unknown kind of cog task {kind!r}")


def assigned_work(hub, task: dict, state: dict | None = None) -> dict:
    """A cog works an item it was assigned, the way a person picks up an assignment: it reads the item and the
    instruction, acts within its autonomy (comments, reviews, drafts, its turn at a Gate it may sign), and reports
    back on the item. A step assigned to it is completed or handed back; it is never left waiting silently."""
    from .agent import Agent
    from .tools import state_words

    cog, p = task["cog"], task.get("payload") or {}
    rec = hub.kernel.get(task.get("record") or p.get("record") or (state or {}).get("record") or "")
    if rec is None:
        raise ValueError("the item it was assigned is gone")
    run = state or hub.runner.run_for_record(rec["locator"])
    waiting = (run or {}).get("pending") or {}
    step_mine = bool(run) and hub.runner.is_assigned_work(hub.runner.load(run["id"]), cog)
    f = rec["fields"]
    what = f.get("description") or (hub.dms.text_of(rec) if rec["type"] == "document" else f.get("text") or "")[:600]
    goal = (f"You are assigned {rec['number']} ({rec['type']}: {f.get('title') or ''}) by {task.get('by') or 'a colleague'}."
            + (f" Their instruction: {p['instruction']}" if p.get("instruction") else "")
            + (f" What the item says: {what}" if what else "")
            + f" It is {state_words(rec['state'])}" + (f", waiting for {', '.join(waiting.get('assignees') or [])} at {waiting.get('title')}" if waiting else "") + "."
            + (" The step is assigned to you: do the work, then call complete_step with what you did, or hand_back with why you cannot."
               if step_mine else
               " Do what is asked within your autonomy. If it waits at an approval you may sign, use take_turn. Comment on the item with what you did.")
            + " Finish with a summary.")
    agent = Agent(hub, cog)
    res = agent.resume_or_none(f"q{task['id']}", by=task.get("by") or cog)
    if res is None:
        prep = agent.prepare(goal, by=task.get("by") or cog, trigger="assigned", run_id=f"q{task['id']}")
        res = agent.conclude(prep, agent.think_safely(prep))
    out = {"run": res["run"], "status": res["status"], "summary": res["summary"], "actions": res["actions"], "record": rec["number"]}
    if run and step_mine and hub.runner.is_assigned_work(hub.runner.load(run["id"]), cog):
        # It did not finish the step: the work goes back to a person with what it found, rather than sitting with a cog.
        if res["status"] == "proposed":
            reason = f"{cog} proposes actions that wait for confirmation (agent run {res['run']}): {res['summary']}"
        else:
            reason = f"{cog} did not complete it: {res['summary']}"
        hub.runner.hand_back(run["id"], cog, reason)
        out["handed_back"] = True
    hub.inbox.post(sender=cog, to=task.get("by") or rec.get("created_by") or cog, record=rec["locator"], kind="reply",
                   subject=f"{cog} on {rec['number']}", body=res["summary"] + (" (handed back)" if out.get("handed_back") else ""))
    return out


# ---------------------------------------------------------------- the worker thread
class CogWorker:
    """Works the queue in the background. ``lock`` is the hub lock the shell serializes on: the worker holds it while
    it reads and writes and gives it up while the model thinks (see model.thinking_releases)."""

    def __init__(self, hub, lock, threads: int | None = None):
        self.hub = hub
        self.lock = lock
        self.threads = max(1, int(threads or os.environ.get("HUBPROTO_COG_WORKERS") or 0) or self._cog_count())
        self.stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def _cog_count(self) -> int:
        """A worker per cog in the release in force (a claim skips a cog that is busy, so each cog gets its own)."""
        try:
            meta = self.hub.releases.current()
            members = self.hub.releases.bundle(meta["digest"])["members"] if meta else {}
            return max(1, sum(1 for m in members.values() if m.get("type") == "cog"))
        except Exception:
            return 1

    def start(self) -> "CogWorker":
        with self.lock:
            n = self.hub.queue.recover()
        if n:
            _log(f"cog queue: {n} task(s) left working by a stopped worker went back in the queue")
        for i in range(self.threads):
            t = threading.Thread(target=self._loop, name=f"cog-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        self.hub.queue.lanes = self.threads
        _log(f"cog queue: {self.threads} worker thread(s) taking the cogs' tasks; model calls take turns at the model gate")
        return self

    def _loop(self) -> None:
        from .model import thinking_releases

        q = self.hub.queue
        name = threading.current_thread().name
        state = q.workers.setdefault(name, {"alive": True, "last_ok": None, "last_error": None, "errors": 0})
        backoff = 1.0
        try:
            while not self.stop.is_set():
                q.wake.wait(timeout=5)
                q.wake.clear()
                try:
                    if time.time() - state.get("_recovered", 0) > LEASE / 3:  # take back expired leases now and then
                        with self.lock:
                            q.recover()
                        state["_recovered"] = time.time()
                    while not self.stop.is_set():
                        with self.lock, thinking_releases(self.lock):
                            t = q.work_one()
                        state["last_ok"] = utcnow()
                        if t is None:
                            break
                        time.sleep(0.05)  # let a waiting request in between tasks
                    backoff = 1.0
                except Exception as e:  # a locked database, a full disk: log it, wait, carry on; never die silently
                    state["errors"] = state.get("errors", 0) + 1
                    state["last_error"] = f"{utcnow()} {type(e).__name__}: {e}"
                    _log(f"cog worker {name}: {type(e).__name__}: {e}; retrying in {backoff:.0f} s\n{traceback.format_exc(limit=3)}")
                    self.stop.wait(backoff)
                    backoff = min(backoff * 2, 60.0)
        finally:
            state["alive"] = False

    def shutdown(self) -> None:
        self.stop.set()
        self.hub.queue.wake.set()
