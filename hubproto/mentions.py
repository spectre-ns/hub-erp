"""@mentions in an item's chatter: "@quality-assistant please review this drawing", "@design-review-committee
please approve this document". A mention is a request; policy decides who may sign.

- A mentioned **cog** reviews the item against the SOPs it follows at once, answers in the chatter, files the
  review as evidence, and, when the item is waiting at a Gate assigned to that cog and the review passed,
  takes its turn there (``runner.cog_decide``).
- A mentioned **person** gets the request in their inbox, with the item linked.
- A mentioned **role** (a committee) fans out to its members: cogs review, people are asked.
- "approve" with nothing in flight starts the item's own process, if the sender may start it; the reply says
  whom it now waits for.
"""

from __future__ import annotations

import re

from . import authz
from .routing import principals_for

_MENTION = re.compile(r"(?<![\w@])@([A-Za-z][\w-]*)")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def intent_of(text: str) -> str:
    t = (text or "").lower()
    if re.search(r"\b(edit|insert|include|add|write|draft|rewrite|update|change) (this|the|a|an|it|some)?\b", t) and not re.search(r"\b(approve|approval)\b", t):
        return "edit"
    if re.search(r"\b(approve|approval|sign off|sign-off|release)\b", t):
        return "approve"
    if re.search(r"\b(review|check|look at|assess|inspect|comments?)\b", t):
        return "review"
    return "message"


def resolve(bundle: dict, text: str) -> tuple[list[str], list[str], list[str]]:
    """(cogs, people, unknown) named by @mentions, with roles expanded to their members."""
    cogs, people, unknown = [], [], []
    members = bundle.get("members") or {}
    roles = set(bundle.get("roles") or [])
    for raw in _MENTION.findall(text or ""):
        name = slug(raw)
        if name in members:
            targets = [name]
        elif name in roles:
            targets = principals_for(bundle, f"role:{name}")
        else:
            unknown.append(raw)
            continue
        for t in targets:
            bucket = cogs if members[t]["type"] == "cog" else people
            if t not in bucket:
                bucket.append(t)
    return cogs, people, unknown


def process_for(bundle: dict, record: dict, sender: str) -> str | None:
    """The process that moves this kind of item, which the sender may start."""
    for name, wf in (bundle.get("workflows") or {}).items():
        if wf.get("record_type") == record["type"]:
            op = bundle["ops"].get(name) or {}
            if authz.check(bundle, sender, "create", op.get("object", record["type"]), record["folder"])["allowed"]:
                return name
    return None


def handle(hub, msg: dict, bundle: dict) -> list[dict]:
    """Act on the mentions in a message about an item. Returns the replies/notifications written."""
    from .cogs import RunContext

    record = hub.kernel.get(msg.get("record")) if msg.get("record") else None
    cogs, people, unknown = resolve(bundle, (msg.get("subject") or "") + " " + (msg.get("body") or ""))
    out: list[dict] = []
    if not (cogs or people or unknown):
        return out
    sender = msg["from"]
    intent = intent_of(msg.get("body") or msg.get("subject") or "")
    release = (hub.releases.current() or {}).get("digest", "")
    actions: list[str] = []
    run = hub.runner.run_for_record(record["locator"]) if record else None
    # Nothing in flight and an approval asked for: start the item's own process so the routing sentences decide who signs.
    if record and intent == "approve" and (run is None or run["status"] not in ("waiting", "submitted", "running")):
        process = process_for(bundle, record, sender)
        if process:
            try:
                run_id = hub.runner.submit(process, {"record": {}}, owner=sender, record=record["locator"])
                run = hub.runner.load(run_id)
                waiting = ", ".join(run["pending"]["assignees"]) if run.get("pending") else run["status"]
                actions.append(f"started {process} for {record['number']}; it is now {waiting if run['status'] == 'waiting' else run['status']}"
                               + (" (waiting for " + waiting + ")" if run["status"] == "waiting" else ""))
            except Exception as e:  # the policy said no; the reply says why
                actions.append(f"could not start a process for {record['number']}: {e}")
        else:
            actions.append(f"no process that {sender} may start moves a {record['type']}; ask a policy owner")
    for cog_id in cogs:
        cog = hub.registry.get(cog_id)
        profile = (bundle.get("cogs") or {}).get(cog_id) or {}
        ctx = RunContext(run_id="inbox-" + msg["id"], release=release, bundle=bundle, owner=cog_id, kernel=hub.kernel,
                         runner=hub.runner, inbox=hub.inbox, record=record["locator"] if record else None, dms=hub.dms,
                         frames=lambda: hub.frames, model=hub.model, extras={"cog_profile": profile, "process": run["process"] if run else ""})
        if record and record["type"] == "document" and intent == "edit" and "edit-document" in cog.tasks:
            env = cog.invoke("edit-document", {"record": record["locator"], "request": msg.get("body") or msg.get("subject")}, ctx)
            r = env.get("payload") or {}
            from . import office, wopi as _wopi

            content, latest = hub.dms.content(record["locator"])
            ext = _wopi.extension_of(latest.get("filename") or record["fields"].get("filename") or "")
            new = office.insert_paragraphs(content, ext, r.get("paragraphs") or [], r.get("position") or "start") if r.get("paragraphs") else None
            if new is None:
                body = f"I cannot write into {record['number']}: " + ((env.get("error") or {}).get("detail") or f"the editor cannot change a .{ext or '?'} file")
            else:
                explanation = r.get("explanation") or "Written as asked."
                if msg.get("comment_id") not in (None, ""):
                    new = office.add_reply(new, ext, str(msg["comment_id"]), cog_id, explanation, date=msg.get("at") or "") or new
                try:
                    before = hub.kernel.get(record["locator"])["fields"]
                    holder, via, editors = before.get("checked_out_by"), before.get("checked_out_via"), list(before.get("editors") or [])
                    hub.dms.add_version(record["locator"], new, by="hub", filename=latest.get("filename") or "", mime=latest.get("mime") or "",
                                        note=f"edited by {cog_id}: {explanation}")
                    if holder:  # a new version clears the lock; the editing session goes on
                        hub.dms.checkout(record["locator"], by=holder, via=via or "manual")
                        if editors:
                            hub.kernel.set_fields(record["locator"], {"editors": editors}, by=cog_id)
                    _, version = hub.dms.content(record["locator"])
                    hub.kernel.set_fields(record["locator"], {"text": office.extract_text(new, ext),
                                                              "office_comments": [{k: c[k] for k in ("id", "author", "date", "text", "digest")} for c in office.extract_comments(new, ext)]}, by=cog_id)
                    body = f"{explanation} (version {version.get('version')} of {record['number']}" + (", drafted with the stand-in: configure a model for real wording" if r.get("stand_in") else f", drafted with {r.get('model')}") + ")"
                    reply = hub.inbox.reply(msg, sender=cog_id, body=body, record=record["locator"], envelope=env)
                    hub.inbox.mark(reply["id"], applied_in=version.get("version"))
                    out.append(reply)
                    continue
                except Exception as e:
                    body = f"I drafted the content but could not write it into {record['number']}: {e}"
            out.append(hub.inbox.reply(msg, sender=cog_id, body=body, record=record["locator"], envelope=env))
            continue
        if record and "review-document" in cog.tasks:
            env = cog.invoke("review-document", {"record": record["locator"], "question": msg.get("body") or msg.get("subject")}, ctx)
            r = env.get("payload") or {}
            verdict = r.get("verdict", "fail")
            lines = [f"Review of {record['number']} by {cog_id}: {verdict}."]
            for p in r.get("findings") or []:
                lines.append(f"- [{p['severity']}] {p['check']}: {p['detail']}")
            if r.get("summary") and r["summary"] not in "\n".join(lines):
                lines.append(r["summary"])
            if r.get("evidence"):
                lines.append(f"Filed as evidence {r['evidence']}.")
            # Progress the workflow only where policy already lets this cog decide.
            run = hub.runner.run_for_record(record["locator"])
            if run and run["status"] == "waiting" and cog_id in (run.get("pending") or {}).get("assignees", []):
                if verdict == "pass":
                    after = hub.runner.cog_decide(run["id"], cog_id)
                    last = next((e for e in reversed(hub.runner.track(run["id"])) if e["type"] in ("gate.decided", "gate.declined")), {})
                    lines.append(f"Then I took my turn at the Gate: {last.get('type', '').split('.')[-1]}; the item is now {after['status']}"
                                 + (f", waiting for {', '.join(after['pending']['assignees'])}" if after.get("pending") else "") + ".")
                else:
                    lines.append("The item is waiting for me at the Gate, but the review did not pass, so I have not signed it.")
            elif run and run["status"] == "waiting":
                lines.append(f"The item is waiting for {', '.join(run['pending']['assignees'])}; this review is attached for them.")
                hub.inbox.notify_all(run["pending"]["assignees"], kind="review", subject=f"{cog_id} reviewed {record['number']}: {verdict}",
                                     body="\n".join(lines), run_id=run["id"], record=record["locator"]) if hasattr(hub.inbox, "notify_all") else None
            body = "\n".join(lines + actions)
        else:
            env = cog.invoke("inbox", {"message": msg}, ctx)
            body = (env.get("payload") or {}).get("reply") or f"{cog_id} could not handle the message."
            if actions:
                body += "\n" + "\n".join(actions)
        out.append(hub.inbox.reply(msg, sender=cog_id, body=body, record=record["locator"] if record else None, envelope=env))
    for person in people:
        if person == sender:
            continue
        subject = f"{sender} asks you to {intent if intent != 'message' else 'look at'} {record['number'] if record else 'a message'}"
        out.append(hub.inbox.notify(person, kind="request", subject=subject, body=(msg.get("body") or "") + ("\n" + "\n".join(actions) if actions else ""),
                                    run_id=run["id"] if run else "", record=record["locator"] if record else None))
    if unknown or (actions and not cogs):
        note = []
        if unknown:
            note.append("No member or role is called " + ", ".join("@" + u for u in unknown) + ".")
        note += actions
        out.append(hub.inbox.notify(sender, kind="mention", subject=f"About your message on {record['number'] if record else 'the hub'}", body="\n".join(note),
                                    run_id=run["id"] if run else "", record=record["locator"] if record else None))
    return out
