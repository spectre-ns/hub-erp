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


INTENTS = ("edit", "review", "approve", "message")


def intent_by_keywords(text: str) -> str:
    t = (text or "").lower()
    negated = lambda word: re.search(r"\b(not|don't|didn't|do not|did not|never|no)\b[^.]{0,25}\b" + word, t) is not None
    if re.search(r"\b(edit|insert|include|add|write|draft|rewrite|revise|update|change|format|reformat|make|put|replace|fix|correct|clean)\b", t) and not re.search(r"\b(approve|approval)\b", t):
        return "edit"
    if re.search(r"\b(approve|approval|sign off|sign-off|release|move .{0,20}approval)\b", t) and not negated("approv"):
        return "approve"
    if re.search(r"\b(review|check|look at|assess|inspect)\b", t) and not negated("review"):
        return "review"
    return "message"


def intent_of(text: str, *, model=None, model_name: str = "", thread: list[dict] | None = None, cog_id: str = "") -> str:
    """What the writer wants: edit (write or change content), review, approve (move the item on), or an answer.
    The cog's model decides when one is configured, with the thread in view; keywords otherwise."""
    fallback = intent_by_keywords(text)
    if fallback == "approve":
        return "approve"  # an explicit approval phrase is not for a model to second-guess
    t = (text or "").lower()
    if fallback == "review" and not re.search(r"\b(edit|insert|include|add|write|draft|rewrite|revise|update|change|format|make|put|replace|fix)\b", t):
        return "review"  # "please review this" means review
    if model is None or not getattr(model, "available", lambda m: False)(model_name):
        return fallback
    context = "\n".join(f"- {t.get('author')}: {t.get('text')}" for t in (thread or [])[-6:])
    user = ((f"Thread so far:\n{context}\n\n" if context else "") +
            f"Latest comment to {cog_id or 'you'}:\n{text}\n\n"
            "Which ONE does the latest comment ask of you? Answer with a single word:\n"
            "EDIT (write, add, change, format or revise content in the document)\n"
            "REVIEW (assess the document and give findings)\n"
            "APPROVE (sign off, release, or move the item to approval)\n"
            "ANSWER (a question or remark to reply to in words)")
    try:
        out = model.complete(model=model_name, system="You classify requests. Reply with exactly one word.", user=user, max_tokens=5, by=cog_id)
        word = (out.get("text") or "").strip().upper().split()[0] if (out.get("text") or "").strip() else ""
    except Exception:
        return fallback
    return {"EDIT": "edit", "REVIEW": "review", "APPROVE": "approve", "ANSWER": "message"}.get(word.strip(".,:!"), fallback)


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
            for folder in (record["folder"], op.get("folder") or record["folder"]):
                if authz.check(bundle, sender, "create", op.get("object", record["type"]), folder)["allowed"]:
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
    intent = intent_by_keywords(msg.get("body") or msg.get("subject") or "")
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
    from .model import cog_availability

    for cog_id in list(cogs):
        ok, why = cog_availability(bundle, cog_id)
        if not ok:
            out.append(hub.inbox.notify(sender, kind="mention", subject=f"{cog_id} is not available", body=why + ". Nothing was done.",
                                        record=record["locator"] if record else None))
            cogs.remove(cog_id)
    for cog_id in cogs:
        cog = hub.registry.get(cog_id)
        profile = (bundle.get("cogs") or {}).get(cog_id) or {}
        if record:
            from . import office

            comments = record["fields"].get("office_comments") or []
            thread_ctx = office.thread_of(comments, str(msg["comment_id"])) if msg.get("comment_id") not in (None, "") else []
            intent = intent_of(msg.get("body") or msg.get("subject") or "", model=hub.model, model_name=profile.get("model") or "",
                               thread=[t for t in thread_ctx if str(t.get("id")) != str(msg.get("comment_id"))], cog_id=cog_id)
        ctx = RunContext(run_id="inbox-" + msg["id"], release=release, bundle=bundle, owner=cog_id, kernel=hub.kernel,
                         runner=hub.runner, inbox=hub.inbox, record=record["locator"] if record else None, dms=hub.dms,
                         frames=lambda: hub.frames, model=hub.model, extras={"cog_profile": profile, "process": run["process"] if run else ""})
        if record and intent == "approve":
            # Asked to approve: a cog takes its turn only where the Gate is assigned to it; otherwise it just reports.
            run = hub.runner.run_for_record(record["locator"])
            if run and run["status"] == "waiting" and cog_id in (run.get("pending") or {}).get("assignees", []):
                after = hub.runner.cog_decide(run["id"], cog_id)
                last = next((e for e in reversed(hub.runner.track(run["id"])) if e["type"] in ("gate.decided", "gate.declined")), {})
                body = f"I took my turn at the Gate: {last.get('type', '').split('.')[-1]}; {record['number']} is now {after['status']}" + \
                    (f", waiting for {', '.join(after['pending']['assignees'])}" if after.get("pending") else "") + "."
            elif run and run["status"] == "waiting":
                body = f"{record['number']} is waiting for {', '.join(run['pending']['assignees'])} at {run['pending'].get('title', 'approval')}; it is not mine to sign."
            else:
                body = "; ".join(actions) or f"{record['number']} has nothing in flight and no process I can start for it."
            out.append(hub.inbox.reply(msg, sender=cog_id, body=body, record=record["locator"]))
            continue
        if record and record["type"] == "document" and intent == "edit" and "edit-document" in cog.tasks:
            env = cog.invoke("edit-document", {"record": record["locator"], "request": msg.get("body") or msg.get("subject")}, ctx)
            r = env.get("payload") or {}
            if not r.get("paragraphs"):
                body = f"I cannot write into {record['number']}: " + ((env.get("error") or {}).get("detail") or "nothing to write")
                out.append(hub.inbox.reply(msg, sender=cog_id, body=body, record=record["locator"], envelope=env))
                continue
            explanation = (r.get("explanation") or "Written as asked.") + ("" if not r.get("stand_in") else " (drafted with the stand-in: configure a model for real wording)")
            reply = hub.inbox.reply(msg, sender=cog_id, body=explanation, record=record["locator"], envelope=env)
            # The content is written by the cog's seat in the live editor, or by the hub into the file when no editor is open.
            edit = {"paragraphs": r["paragraphs"], "position": r.get("position") or "start"}
            if r.get("kind") == "sheet":
                edit.update({"kind": "sheet", "rows": r.get("rows") or [], "start_row": r.get("start_row") or 1})
            hub.inbox.mark(reply["id"], edit=edit)
            out.append(reply)
            continue
        if record and record["type"] == "document" and intent == "message" and msg.get("comment_id") not in (None, "") and "answer" in cog.tasks:
            from . import office

            comments = record["fields"].get("office_comments") or []
            thread = [t for t in office.thread_of(comments, str(msg["comment_id"])) if t["id"] != str(msg["comment_id"])]
            env = cog.invoke("answer", {"record": record["locator"], "question": msg.get("body") or "", "thread": thread}, ctx)
            body = (env.get("payload") or {}).get("text") or f"{cog_id} could not answer."
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
    if record and record["type"] == "document" and cogs:
        from . import wopi as _wopi

        f = hub.kernel.get(record["locator"])["fields"]
        machines = set(hub.registry.ids()) | {"hub"}
        people_editing = f.get("checked_out_via") == "editor" and any(e not in machines for e in (f.get("editors") or [f.get("checked_out_by")]) if e)
        if not people_editing:  # no live seat will do it: the hub writes the answers into the file now
            try:
                _wopi.WopiHost(hub).apply_replies(hub.kernel.get(record["locator"]), sender)
            except Exception as e:
                out.append(hub.inbox.notify(sender, kind="mention", subject=f"Could not write into {record['number']}", body=str(e), record=record["locator"]))
    if unknown or (actions and not cogs):
        note = []
        if unknown:
            note.append("No member or role is called " + ", ".join("@" + u for u in unknown) + ".")
        note += actions
        out.append(hub.inbox.notify(sender, kind="mention", subject=f"About your message on {record['number'] if record else 'the hub'}", body="\n".join(note),
                                    run_id=run["id"] if run else "", record=record["locator"] if record else None))
    return out
