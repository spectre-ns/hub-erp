"""Inbox and messaging: people and Cogs are addressed the same way.

A message has a sender, a recipient, a channel (``dm`` or ``email``), a
subject, a body and attachments. Delivering to a person stores it in their
inbox (and writes an .eml to the outbox for the email channel). Delivering
to a Cog invokes its ``inbox`` task; the Cog may act (file a record, start a
process) and its reply is delivered back to the sender.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from .cogs import RunContext
from .release import utcnow


def _new_id() -> str:
    from .workqueue import effect_id

    return effect_id("msg") or uuid.uuid4().hex[:8]


class Inbox:
    """Messages in SQLite (state/inbox/messages.db): an append is one INSERT and a mark one UPDATE of one row, so two
    processes (the shell and the cog-users service) never lose each other's writes, a torn write cannot take the
    chatter down, and an item's chatter is an indexed lookup however long the history grows. A messages.jsonl from
    before is imported once (lines that do not parse go to messages.bad.jsonl)."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "messages.db"
        self.outbox = self.root / "outbox"
        self.outbox.mkdir(exist_ok=True)
        with self._db() as db:
            db.executescript("""
CREATE TABLE IF NOT EXISTS messages (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, at TEXT, frm TEXT, too TEXT,
  kind TEXT, record TEXT, run_id TEXT, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS messages_record ON messages(record);
CREATE INDEX IF NOT EXISTS messages_to ON messages(too);
CREATE INDEX IF NOT EXISTS messages_from ON messages(frm);
CREATE INDEX IF NOT EXISTS messages_run ON messages(run_id);
""")
        self._import_jsonl()

    @contextmanager
    def _db(self):
        db = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        try:
            yield db
        finally:
            db.close()

    def _import_jsonl(self) -> None:
        old = self.root / "messages.jsonl"
        if not old.exists():
            return
        good, bad = [], []
        for line in old.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                m = json.loads(line)
                if isinstance(m, dict) and m.get("id"):
                    good.append(m)
                    continue
            except ValueError:
                pass
            bad.append(line)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            for m in good:
                db.execute("INSERT OR IGNORE INTO messages(id, at, frm, too, kind, record, run_id, data) VALUES (?,?,?,?,?,?,?,?)", self._row(m))
            db.execute("COMMIT")
        if bad:
            with (self.root / "messages.bad.jsonl").open("a", encoding="utf-8") as fh:
                fh.write("".join(x + "\n" for x in bad))
        old.replace(self.root / "messages.jsonl.imported")

    @staticmethod
    def _row(m: dict) -> tuple:
        return (m["id"], m.get("at"), m.get("from"), m.get("to"), m.get("kind"), m.get("record"), m.get("run_id") or "",
                json.dumps(m, sort_keys=True, default=str))

    def _append(self, msg: dict) -> dict:
        with self._db() as db:
            # A cog task re-run after a crash writes with the same ids (workqueue.effect_id): the first write stands.
            db.execute("INSERT OR IGNORE INTO messages(id, at, frm, too, kind, record, run_id, data) VALUES (?,?,?,?,?,?,?,?)", self._row(msg))
        return msg

    def _select(self, where: str = "", args: tuple = ()) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT data FROM messages" + (f" WHERE {where}" if where else "") + " ORDER BY seq", args).fetchall()
        out = []
        for (d,) in rows:
            try:
                out.append(json.loads(d))
            except ValueError:  # never one bad row taking a page down
                continue
        return out

    def all(self) -> list[dict]:
        return self._select()

    def for_principal(self, principal: str) -> list[dict]:
        return self._select("too = ?", (principal,))

    def by_run(self, run_id: str) -> list[dict]:
        return self._select("run_id = ?", (run_id,)) if run_id else []

    def sent_by(self, principal: str) -> list[dict]:
        return self._select("frm = ?", (principal,))

    def notify(self, to: str, *, kind: str, subject: str, body: str, run_id: str = "", record: str | None = None,
               key: str | None = None, data: dict | None = None) -> dict:
        """A message from the hub. ``key``, when given, is its id: a second notify with the same key writes nothing,
        so a step that is retried tells once. ``data`` adds fields to the message (never over its own)."""
        msg = {"id": key or _new_id(), "at": utcnow(), "from": "hub", "to": to, "channel": "dm",
               "kind": kind, "subject": subject, "body": body, "run_id": run_id, "record": record, "attachments": []}
        msg.update({k: v for k, v in (data or {}).items() if k not in msg})
        return self._append(msg)

    def notify_all(self, people, **kw) -> list[dict]:
        return [self.notify(p, **kw) for p in people]

    def reply(self, msg: dict, *, sender: str, body: str, record: str | None = None, envelope: dict | None = None) -> dict:
        """A reply to a message, in the thread and on the item's chatter when the message was about one."""
        env = envelope or {}
        reply = {"id": _new_id(), "at": utcnow(), "from": sender, "to": msg["from"], "channel": msg.get("channel", "dm"),
                 "kind": "reply", "subject": "Re: " + (msg.get("subject") or ""), "body": body, "in_reply_to": msg["id"],
                 "run_id": msg.get("run_id", ""), "record": record or msg.get("record"), "attachments": []}
        if env:
            reply["cog_envelope"] = {"ok": env.get("ok"), "problems": env.get("problems"), "error": env.get("error"),
                                     "cog": env.get("cog"), "timing": env.get("timing")}
        self._append(reply)
        if reply["channel"] == "email":
            self._write_eml(reply)
        return reply

    def chat_append(self, *, sender: str, to: str, body: str, about: str | None = None, model: str = "", stand_in: bool = False,
                    tools: list[dict] | None = None) -> dict:
        """One line of an informal chat (the messenger): kept apart from messages, notifications and an item's chatter."""
        return self._append({"id": _new_id(), "at": utcnow(), "from": sender, "to": to, "channel": "chat", "kind": "chat",
                             "subject": "", "body": body, "about": about, "run_id": "", "attachments": [], "model": model, "stand_in": stand_in,
                             "tools": tools or []})

    def chat_thread(self, a: str, b: str, limit: int = 60) -> list[dict]:
        """The chat between two members, oldest first."""
        pair = {a, b}
        msgs = [m for m in self._select("kind = 'chat' AND ((frm = ? AND too = ?) OR (frm = ? AND too = ?))", (a, b, b, a))
                if {m["from"], m["to"]} == pair and not m.get("cleared")]
        return msgs[-limit:]

    def chat_clear(self, a: str, b: str) -> int:
        """Start a fresh conversation: the old lines stay on file (marked), the cog no longer sees them."""
        n = 0
        for m in self.chat_thread(a, b, limit=100000):
            self.mark(m["id"], cleared=True)
            n += 1
        return n

    def mark(self, msg_id: str, **fields) -> None:
        """Set fields on a stored message (e.g. applied_in: the document version a reply was written into): one row,
        read and written in one transaction."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM messages WHERE id = ?", (msg_id,)).fetchone()
            if row:
                m = json.loads(row[0])
                m.update(fields)
                db.execute("UPDATE messages SET data = ?, kind = ?, record = ?, too = ? WHERE id = ?",
                           (json.dumps(m, sort_keys=True, default=str), m.get("kind"), m.get("record"), m.get("to"), msg_id))
            db.execute("COMMIT")

    def on_record(self, locator: str) -> list[dict]:
        """The chatter of an item: every message, reply and notification about it, oldest first."""
        return self._select("record = ?", (locator,))

    def send(self, *, sender: str, to: str, subject: str, body: str, channel: str = "dm", attachments=None,
             bundle: dict | None = None, registry=None, runner=None, kernel=None, release: str = "",
             record: str | None = None, hub=None, comment_id: str | None = None) -> dict:
        msg = {"id": _new_id(), "at": utcnow(), "from": sender, "to": to, "channel": channel,
               "kind": "message", "subject": subject, "body": body, "run_id": "", "record": record, "attachments": attachments or []}
        if comment_id is not None:
            msg["comment_id"] = str(comment_id)
        member = (bundle or {}).get("members", {}).get(to)
        if hub is not None and bundle and record and member and member["type"] == "cog" and "@" + to not in (body or "") and "@" + to not in (subject or ""):
            msg["subject"] = f"@{to} {subject}"  # addressed to the cog on an item: the same as mentioning it there
        self._append(msg)
        if channel == "email":
            self._write_eml(msg)
        if hub is not None and bundle and ("@" in (msg.get("body") or "") or "@" in (msg.get("subject") or "")):
            from . import mentions

            replies = mentions.handle(hub, msg, bundle)
            if replies and (not member or member["type"] != "cog" or to in {r["from"] for r in replies}):
                return replies[0] if len(replies) == 1 else {**msg, "replies": replies}
            if member and member["type"] == "cog" and (record or to in {t["cog"] for t in msg.get("queued") or []}):
                return msg  # the cog has it in its queue (or was told it is not available)
        if member and member["type"] == "cog" and (hub is not None or registry is not None):
            if hub is None:  # a bare inbox (tests, tools): answered here and now
                return self._answer_with(msg, to, registry.get(to), RunContext(run_id="inbox-" + msg["id"], release=release, bundle=bundle,
                                                                                owner=to, kernel=kernel, runner=runner, inbox=self,
                                                                                dms=getattr(runner, "dms", None)))
            task = hub.queue.submit(to, "message", by=sender, title=subject, record=record, payload={"msg": msg["id"]})
            msg["queued"] = [task]
            if task["status"] == "done" and (task.get("result") or {}).get("replies"):
                return task["result"]["replies"][0]
        return msg

    def post(self, *, sender: str, to: str, subject: str, body: str, record: str | None = None, kind: str = "message") -> dict:
        """A cog's report on an item (its chatter), without setting off any cog."""
        return self._append({"id": _new_id(), "at": utcnow(), "from": sender, "to": to, "channel": "dm", "kind": kind,
                             "subject": subject, "body": body, "run_id": "", "record": record, "attachments": []})

    def find(self, msg_id: str) -> dict | None:
        found = self._select("id = ?", (msg_id,))
        return found[0] if found else None

    def answer_as_cog(self, msg: dict, cog_id: str, hub) -> dict:
        """A cog answers a message sent to it (worked from its queue)."""
        meta = hub.releases.current()
        bundle = hub.releases.bundle(meta["digest"]) if meta else {"members": {}}
        ctx = RunContext(run_id="inbox-" + msg["id"], release=meta["digest"] if meta else "", bundle=bundle, owner=cog_id,
                         kernel=hub.kernel, runner=hub.runner, inbox=self, dms=hub.dms, frames=lambda: hub.released_frames())
        return self._answer_with(msg, cog_id, hub.registry.get(cog_id), ctx)

    def _answer_with(self, msg: dict, to: str, cog, ctx) -> dict:
        env = cog.invoke("inbox", {"message": msg}, ctx)
        payload = env.get("payload") or {}
        reply_body = payload.get("reply") or f"{to} could not handle the message: {(env.get('error') or {}).get('detail')}"
        reply = {"id": _new_id(), "at": utcnow(), "from": to, "to": msg["from"], "channel": msg.get("channel", "dm"),
                 "kind": "reply", "subject": "Re: " + (msg.get("subject") or ""), "body": reply_body, "in_reply_to": msg["id"],
                 "run_id": (payload.get("actions") or [{}])[0].get("run_id", ""), "attachments": [], "record": msg.get("record"),
                 "cog_envelope": {"ok": env["ok"], "problems": env["problems"], "error": env["error"],
                                  "cog": env["cog"], "timing": env["timing"]}}
        self._append(reply)
        if reply["channel"] == "email":
            self._write_eml(reply)
        return reply

    def _write_eml(self, msg: dict) -> Path:
        path = self.outbox / f"{msg['at'].replace(':', '')}-{msg['id']}.eml"
        lines = [f"From: {msg['from']}@hub.local", f"To: {msg['to']}@hub.local", f"Subject: {msg['subject']}",
                 f"Date: {msg['at']}", f"X-Hub-Run: {msg.get('run_id', '')}", "", msg["body"]]
        for att in msg.get("attachments", []):
            lines += ["", f"--- attachment: {att['name']}", json.dumps(att["content"], indent=1)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


def parse_eml(text: str) -> dict:
    """Parse the simple .eml form the outbox writes (and people can type)."""
    head, _, body = text.partition("\n\n")
    headers = {}
    for line in head.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
    attachments = []
    parts = re.split(r"^--- attachment: (.+)$", body, flags=re.M)
    main = parts[0].strip()
    for i in range(1, len(parts), 2):
        name, content = parts[i].strip(), parts[i + 1].strip()
        try:
            attachments.append({"name": name, "content": json.loads(content)})
        except json.JSONDecodeError:
            attachments.append({"name": name, "content": {"text": content}})

    def addr(v: str) -> str:
        return v.split("@", 1)[0].strip("<> ")

    return {"from": addr(headers.get("from", "")), "to": addr(headers.get("to", "")),
            "subject": headers.get("subject", ""), "body": main, "attachments": attachments}
