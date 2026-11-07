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
import uuid
from pathlib import Path

from .cogs import RunContext
from .release import utcnow


class Inbox:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "messages.jsonl"
        self.outbox = self.root / "outbox"
        self.outbox.mkdir(exist_ok=True)

    def _append(self, msg: dict) -> dict:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(msg, sort_keys=True) + "\n")
        return msg

    def _rewrite(self, msgs: list[dict]) -> None:
        tmp = self.path.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(m, sort_keys=True) + "\n" for m in msgs), encoding="utf-8")
        tmp.replace(self.path)

    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def for_principal(self, principal: str) -> list[dict]:
        return [m for m in self.all() if m["to"] == principal]

    def notify(self, to: str, *, kind: str, subject: str, body: str, run_id: str = "", record: str | None = None) -> dict:
        return self._append({"id": uuid.uuid4().hex[:8], "at": utcnow(), "from": "hub", "to": to, "channel": "dm",
                             "kind": kind, "subject": subject, "body": body, "run_id": run_id, "record": record, "attachments": []})

    def notify_all(self, people, **kw) -> list[dict]:
        return [self.notify(p, **kw) for p in people]

    def reply(self, msg: dict, *, sender: str, body: str, record: str | None = None, envelope: dict | None = None) -> dict:
        """A reply to a message, in the thread and on the item's chatter when the message was about one."""
        env = envelope or {}
        reply = {"id": uuid.uuid4().hex[:8], "at": utcnow(), "from": sender, "to": msg["from"], "channel": msg.get("channel", "dm"),
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
        return self._append({"id": uuid.uuid4().hex[:8], "at": utcnow(), "from": sender, "to": to, "channel": "chat", "kind": "chat",
                             "subject": "", "body": body, "about": about, "run_id": "", "attachments": [], "model": model, "stand_in": stand_in,
                             "tools": tools or []})

    def chat_thread(self, a: str, b: str, limit: int = 60) -> list[dict]:
        """The chat between two members, oldest first."""
        pair = {a, b}
        msgs = [m for m in self.all() if m.get("kind") == "chat" and {m["from"], m["to"]} == pair and not m.get("cleared")]
        return msgs[-limit:]

    def chat_clear(self, a: str, b: str) -> int:
        """Start a fresh conversation: the old lines stay on file (marked), the cog no longer sees them."""
        pair = {a, b}
        msgs = self.all()
        n = 0
        for m in msgs:
            if m.get("kind") == "chat" and {m["from"], m["to"]} == pair and not m.get("cleared"):
                m["cleared"] = True
                n += 1
        if n:
            self._rewrite(msgs)
        return n

    def mark(self, msg_id: str, **fields) -> None:
        """Set fields on a stored message (e.g. applied_in: the document version a reply was written into)."""
        msgs = self.all()
        changed = False
        for m in msgs:
            if m["id"] == msg_id:
                m.update(fields)
                changed = True
        if changed:
            self._rewrite(msgs)

    def on_record(self, locator: str) -> list[dict]:
        """The chatter of an item: every message, reply and notification about it, oldest first."""
        return [m for m in self.all() if m.get("record") == locator]

    def send(self, *, sender: str, to: str, subject: str, body: str, channel: str = "dm", attachments=None,
             bundle: dict | None = None, registry=None, runner=None, kernel=None, release: str = "",
             record: str | None = None, hub=None, comment_id: str | None = None) -> dict:
        msg = {"id": uuid.uuid4().hex[:8], "at": utcnow(), "from": sender, "to": to, "channel": channel,
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
                                                                                owner=to, kernel=kernel, runner=runner, inbox=self))
            task = hub.queue.submit(to, "message", by=sender, title=subject, record=record, payload={"msg": msg["id"]})
            msg["queued"] = [task]
            if task["status"] == "done" and (task.get("result") or {}).get("replies"):
                return task["result"]["replies"][0]
        return msg

    def post(self, *, sender: str, to: str, subject: str, body: str, record: str | None = None, kind: str = "message") -> dict:
        """A cog's report on an item (its chatter), without setting off any cog."""
        return self._append({"id": uuid.uuid4().hex[:8], "at": utcnow(), "from": sender, "to": to, "channel": "dm", "kind": kind,
                             "subject": subject, "body": body, "run_id": "", "record": record, "attachments": []})

    def find(self, msg_id: str) -> dict | None:
        return next((m for m in self.all() if m["id"] == msg_id), None)

    def answer_as_cog(self, msg: dict, cog_id: str, hub) -> dict:
        """A cog answers a message sent to it (worked from its queue)."""
        meta = hub.releases.current()
        bundle = hub.releases.bundle(meta["digest"]) if meta else {"members": {}}
        ctx = RunContext(run_id="inbox-" + msg["id"], release=meta["digest"] if meta else "", bundle=bundle, owner=cog_id,
                         kernel=hub.kernel, runner=hub.runner, inbox=self)
        return self._answer_with(msg, cog_id, hub.registry.get(cog_id), ctx)

    def _answer_with(self, msg: dict, to: str, cog, ctx) -> dict:
        env = cog.invoke("inbox", {"message": msg}, ctx)
        payload = env.get("payload") or {}
        reply_body = payload.get("reply") or f"{to} could not handle the message: {(env.get('error') or {}).get('detail')}"
        reply = {"id": uuid.uuid4().hex[:8], "at": utcnow(), "from": to, "to": msg["from"], "channel": msg.get("channel", "dm"),
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
