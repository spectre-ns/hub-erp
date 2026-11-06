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

    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def for_principal(self, principal: str) -> list[dict]:
        return [m for m in self.all() if m["to"] == principal]

    def notify(self, to: str, *, kind: str, subject: str, body: str, run_id: str = "") -> dict:
        return self._append({"id": uuid.uuid4().hex[:8], "at": utcnow(), "from": "hub", "to": to, "channel": "dm",
                             "kind": kind, "subject": subject, "body": body, "run_id": run_id, "attachments": []})

    def send(self, *, sender: str, to: str, subject: str, body: str, channel: str = "dm", attachments=None,
             bundle: dict | None = None, registry=None, runner=None, kernel=None, release: str = "") -> dict:
        msg = {"id": uuid.uuid4().hex[:8], "at": utcnow(), "from": sender, "to": to, "channel": channel,
               "kind": "message", "subject": subject, "body": body, "run_id": "", "attachments": attachments or []}
        self._append(msg)
        member = (bundle or {}).get("members", {}).get(to)
        if channel == "email":
            self._write_eml(msg)
        if member and member["type"] == "cog" and registry is not None:
            cog = registry.get(to)
            ctx = RunContext(run_id="inbox-" + msg["id"], release=release, bundle=bundle, owner=to, kernel=kernel,
                             runner=runner, inbox=self)
            env = cog.invoke("inbox", {"message": msg}, ctx)
            payload = env.get("payload") or {}
            reply_body = payload.get("reply") or f"{to} could not handle the message: {(env.get('error') or {}).get('detail')}"
            reply = {"id": uuid.uuid4().hex[:8], "at": utcnow(), "from": to, "to": sender, "channel": channel,
                     "kind": "reply", "subject": "Re: " + subject, "body": reply_body, "in_reply_to": msg["id"],
                     "run_id": (payload.get("actions") or [{}])[0].get("run_id", ""), "attachments": [],
                     "cog_envelope": {"ok": env["ok"], "problems": env["problems"], "error": env["error"],
                                      "cog": env["cog"], "timing": env["timing"]}}
            self._append(reply)
            if channel == "email":
                self._write_eml(reply)
            return reply
        return msg

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
