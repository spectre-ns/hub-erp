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


_EDIT_VERBS = (r"edit|insert|include|add|write|draft|rewrite|revise|update|change|format|reformat|make|put|replace|fix|correct|clean|"
               r"delete|remove|drop|strike|cut|rename|move|swap|shorten|merge|split|trim|erase|clear")
_POLITE = r"(?:(?:hi|hello|hey|thanks|ok|okay)[,!]?\s+)?(?:(?:please|pls|kindly)\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?"
_APPROVE_REQ = re.compile(r"^" + _POLITE + r"(?:approve|sign(?:\s+|-)off(?:\s+on)?|release|"
                          r"(?:move|send|put|submit)\s+(?:this|it|the\s+\w+)?\s*(?:\w+\s+)?(?:to|for|into)\s+approval)\b")
_EDIT_REQ = re.compile(r"^" + _POLITE + r"(?:" + _EDIT_VERBS + r")\b")
_REVIEW_REQ = re.compile(r"^" + _POLITE + r"(?:review|check|look at|assess|inspect)\b")
_HOLD = re.compile(r"\b(hold off|wait|until|not yet|don't|do not|no action|never)\b")


def _clauses(text: str) -> list[str]:
    t = _MENTION.sub(" ", (text or "")).lower()
    return [c.strip(" ,:-") for c in re.split(r"[.!?;\n]+|\band then\b", t) if c.strip(" ,:-")]


def intent_by_keywords(text: str) -> str:
    """What a message asks, from its wording. An approval is asked only by an explicit request ("please approve",
    "can you sign off on this"), never by a question about approval or a "hold off on approval"; an edit verb in a
    question is a question unless a clause asks for the edit outright."""
    t = (text or "").lower()
    clauses = _clauses(text)
    if any(_APPROVE_REQ.match(c) and not _HOLD.search(c) for c in clauses):
        return "approve"
    if any(_EDIT_REQ.match(c) for c in clauses):
        return "edit"
    if re.search(r"\b(" + _EDIT_VERBS + r")\b", t) and "?" not in t and not re.search(r"\b(approve|approval)\b", t):
        return "edit"
    if any(_REVIEW_REQ.match(c) for c in clauses) or (re.search(r"\b(review|check|look at|assess|inspect)\b", t) and "?" not in t
                                                      and not re.search(r"\b(not|don't|didn't|do not|did not|never|no)\b[^.]{0,25}\breview", t)):
        return "review"
    return "message"


def _unambiguous(text: str, intent: str) -> bool:
    """A plain imperative ("please delete the Records section") needs no model to tell what it asks."""
    clauses = _clauses(text)
    pat = {"edit": _EDIT_REQ, "review": _REVIEW_REQ, "approve": _APPROVE_REQ}.get(intent)
    return bool(pat) and "?" not in (text or "") and bool(clauses) and all(pat.match(c) or len(c) < 4 for c in clauses[:1])


def intent_of(text: str, *, model=None, model_name: str = "", thread: list[dict] | None = None, cog_id: str = "") -> str:
    """What the writer wants: edit (write or change content), review, approve (move the item on), or an answer.
    An explicit request is taken as it reads; anything else the cog's model decides, with the thread in view
    (keywords when no model is configured)."""
    fallback = intent_by_keywords(text)
    if fallback == "approve" or _unambiguous(text, fallback):
        return fallback  # an explicit request is not for a model to second-guess, nor worth a model call
    if model is None or not getattr(model, "available", lambda m: False)(model_name):
        return fallback
    context = "\n".join(f"- {t.get('author')}: {t.get('text')}" for t in (thread or [])[-6:])
    user = ((f"Thread so far:\n{context}\n\n" if context else "") +
            f"Latest comment to {cog_id or 'you'}:\n{text}\n\n"
            "Which ONE does the latest comment ask of you? Answer with a single word:\n"
            "EDIT (write, add, change, delete, replace, move, format or revise content in the document)\n"
            "REVIEW (assess the document and give findings)\n"
            "APPROVE (an explicit request that you sign it off now; a question about approval is ANSWER)\n"
            "ANSWER (a question or remark to reply to in words)")
    try:
        out = model.complete(model=model_name, system="You classify requests. Reply with exactly one word.", user=user, max_tokens=5, by=cog_id)
        word = (out.get("text") or "").strip().upper().split()[0] if (out.get("text") or "").strip() else ""
    except Exception:
        return fallback
    got = {"EDIT": "edit", "REVIEW": "review", "APPROVE": "approve", "ANSWER": "message"}.get(word.strip(".,:!"), fallback)
    return "message" if got == "approve" else got  # signing is never on a model's reading of a message


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
    msg["queued"] = []
    for cog_id in cogs:
        task = hub.queue.submit(cog_id, "mention", by=sender, title=(msg.get("body") or msg.get("subject") or "")[:200],
                                record=record["locator"] if record else None, payload={"msg": msg["id"], "actions": actions})
        msg["queued"].append(task)
        if task["status"] == "done":
            out.extend((task.get("result") or {}).get("replies") or [])
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


def answer(hub, msg: dict, cog_id: str, actions: list[str] | None = None) -> list[dict]:
    """One cog answers a message that mentions it, worked from its queue: it reads the item as it is now, decides
    what was asked (review, edit, answer, approve), and replies where it was asked. Returns the replies written."""
    meta = hub.releases.current()
    bundle = hub.releases.bundle(meta["digest"]) if meta else {"members": {}}
    release = meta["digest"] if meta else ""
    record = hub.kernel.get(msg.get("record")) if msg.get("record") else None
    sender = msg["from"]
    actions = list(actions or [])
    intent = intent_by_keywords(msg.get("body") or msg.get("subject") or "")
    run = hub.runner.run_for_record(record["locator"]) if record else None
    out: list[dict] = []
    from .model import cog_availability

    ok, why = cog_availability(bundle, cog_id)
    if not ok:
        return [hub.inbox.notify(sender, kind="mention", subject=f"{cog_id} is not available", body=why + ". Nothing was done.",
                                 record=record["locator"] if record else None)]
    try:
        _answer(hub, msg, cog_id, bundle, release, record, sender, actions, intent, run, out)
    finally:
        if record and out:
            _apply_into_file(hub, record, sender, out)
    return out


def _answer(hub, msg, cog_id, bundle, release, record, sender, actions, intent, run, out) -> None:
    from .cogs import RunContext

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
                     frames=lambda: hub.released_frames(), model=hub.model, extras={"cog_profile": profile, "process": run["process"] if run else ""})
    if record and intent == "approve":
        # Asked to approve: a cog takes its turn where the Gate is assigned to it, or steps in where its criteria allow
        # and the person asking is one the approval waits for; otherwise it says why not.
        from .agent import may_ask_step_in

        run = hub.runner.run_for_record(record["locator"])
        done = "; ".join(actions)
        lead = (done[0].upper() + done[1:] + ". ") if done else ""
        if run and run["status"] == "waiting" and cog_id in (run.get("pending") or {}).get("assignees", []):
            after = hub.runner.cog_decide(run["id"], cog_id)
            body = lead + _outcome(hub, run["id"], record, cog_id, after)
        elif run and run["status"] == "waiting" and not hub.runner.may_step_in(run, cog_id):
            waited = ", ".join(run["pending"]["assignees"])
            # The runner's rule (a person waited for or a policy owner; not the preparer; not after this cog's review of
            # this very version failed), and the same rule here for a hub without it.
            why = hub.runner.may_step_in(run, cog_id, asked_by=sender) or may_ask_step_in(bundle, run, record, sender)
            if why:
                body = lead + f"I may not sign {record['number']} on your request: {why}. It stays with {waited}."
            else:
                after = hub.runner.cog_step_in(run["id"], cog_id, asked_by=sender)
                body = lead + _outcome(hub, run["id"], record, cog_id, after, waited=waited)
        elif run and run["status"] == "waiting":
            why = hub.runner.may_step_in(run, cog_id)
            body = lead + f"{record['number']} is waiting for {', '.join(run['pending']['assignees'])} at {run['pending'].get('title', 'approval')}; it is not mine to sign ({why})."
        else:
            body = done or f"{record['number']} has nothing in flight and no process I can start for it."
        out.append(hub.inbox.reply(msg, sender=cog_id, body=body, record=record["locator"]))
        return
    if record and record["type"] == "document" and intent == "edit" and "edit-document" in cog.tasks:
        from .tools import may_edit_document

        ok, why = may_edit_document(bundle, sender, record)
        if not ok:
            # The cog writes as the asker's pen: it changes only what the asker may change.
            body = f"I cannot change {record['number']} for you: {why}. I can review it or answer questions about it instead."
            out.append(hub.inbox.reply(msg, sender=cog_id, body=body, record=record["locator"]))
            owner = record["fields"].get("owner") or record["fields"].get("author")
            if owner and owner not in (sender, cog_id):
                hub.inbox.notify(owner, kind="mention", subject=f"{sender} asked {cog_id} to change {record['number']}",
                                 body=f"Not done ({why}). The request was: {(msg.get('body') or '')[:400]}", record=record["locator"])
            return
        env = cog.invoke("edit-document", {"record": record["locator"], "request": msg.get("body") or msg.get("subject")}, ctx)
        r = env.get("payload") or {}
        if not r.get("ops"):
            body = f"I cannot change {record['number']}: " + ((env.get("error") or {}).get("detail") or "I found nothing to do")
            out.append(hub.inbox.reply(msg, sender=cog_id, body=body, record=record["locator"], envelope=env))
            return
        plan = (r.get("explanation") or "").replace("Changes:", "", 1).strip() or "the changes asked"
        explanation = f"Working on it: {plan}" + ("" if not r.get("stand_in") else " (drafted with the stand-in: configure a model for real wording)")
        reply = hub.inbox.reply(msg, sender=cog_id, body=explanation, record=record["locator"], envelope=env)
        # The cog's seat performs the operations live in the editor where it can; the hub does them in the file otherwise.
        edit = {"ops": r["ops"], "kind": r.get("kind") or "text", "before_text": (hub.kernel.get(record["locator"])["fields"].get("text") or ""),
                "asked_by": sender, "stand_in": bool(r.get("stand_in"))}
        for k in ("paragraphs", "position", "start_row"):
            if r.get(k):
                edit[k] = r[k]
        hub.inbox.mark(reply["id"], edit=edit)
        out.append(reply)
        return
    if record and intent == "message" and "answer" in cog.tasks and (record["type"] == "document" or msg.get("comment_id") in (None, "")):
        # A question gets an answer, like a person would give: in the comment thread when asked in the document, in
        # the chatter (with the recent conversation there) when asked there.
        from . import office

        if msg.get("comment_id") not in (None, ""):
            comments = record["fields"].get("office_comments") or []
            thread = [t for t in office.thread_of(comments, str(msg["comment_id"])) if t["id"] != str(msg["comment_id"])]
        else:
            thread = [{"id": m["id"], "author": m["from"], "text": m.get("body") or ""} for m in hub.inbox.on_record(record["locator"])
                      if m["id"] != msg["id"] and m.get("kind") in ("message", "reply")][-6:]
        env = cog.invoke("answer", {"record": record["locator"], "question": msg.get("body") or "", "thread": thread,
                                     "place": "comment" if msg.get("comment_id") not in (None, "") else "chatter"}, ctx)
        body = (env.get("payload") or {}).get("text") or f"{cog_id} could not answer."
        out.append(hub.inbox.reply(msg, sender=cog_id, body=body, record=record["locator"], envelope=env))
        return
    if record and "review-document" in cog.tasks:
        env = cog.invoke("review-document", {"record": record["locator"], "question": msg.get("body") or msg.get("subject")}, ctx)
        r = env.get("payload") or {}
        verdict = r.get("verdict", "fail")
        errors = [p for p in r.get("findings") or [] if p.get("severity") == "error"]
        remarks = [p for p in r.get("findings") or [] if p.get("severity") != "error"]
        outcome = ("problems found" if errors else "looks right" + (" with remarks" if remarks else ""))
        def said(p):
            what = "" if p.get("check") in (None, "", "model", "check") else f"{p['check']}: "
            return what + p["detail"]

        lines = [f"Review of {record['number']} by {cog_id}: {outcome}."]
        lines += [f"- Problem: {said(p)}" for p in errors]
        lines += [f"- Remark: {said(p)}" for p in remarks]
        if r.get("summary") and r["summary"] not in "\n".join(lines) and not r["summary"].endswith("listed above."):
            lines.append(r["summary"])
        for n in r.get("routing_notes") or []:
            lines.append(f"Who signs: {n}.")
        if r.get("evidence"):
            ev = hub.kernel.get(r["evidence"])
            lines.append(f"Filed as evidence {ev['number'] if ev else r['evidence']}.")
        # Progress the workflow only where policy already lets this cog decide.
        run = hub.runner.run_for_record(record["locator"])
        if run and run["status"] == "waiting" and cog_id in (run.get("pending") or {}).get("assignees", []):
            if verdict == "pass":
                after = hub.runner.cog_decide(run["id"], cog_id)
                lines.insert(0, _outcome(hub, run["id"], record, cog_id, after))  # the outcome first, the review behind it
            else:
                lines.append("The item is waiting for me at the Gate, but the review did not pass, so I have not signed it.")
        elif run and run["status"] == "waiting":
            lines.append(f"The item is waiting for {', '.join(run['pending']['assignees'])}; this review is attached for them.")
            hub.inbox.notify_all(run["pending"]["assignees"], kind="review", subject=f"{cog_id} reviewed {record['number']}: {outcome}",
                                 body="\n".join(lines), run_id=run["id"], record=record["locator"]) if hasattr(hub.inbox, "notify_all") else None
        body = "\n".join(lines + actions)
    else:
        env = cog.invoke("inbox", {"message": msg}, ctx)
        body = (env.get("payload") or {}).get("reply") or f"{cog_id} could not handle the message."
        if actions:
            body += "\n" + "\n".join(actions)
    out.append(hub.inbox.reply(msg, sender=cog_id, body=body, record=record["locator"] if record else None, envelope=env))


def _outcome(hub, run_id: str, record: dict, cog_id: str, after: dict, waited: str = "") -> str:
    """What happened at the Gate, outcome first: signed and waiting for a countersignature, approved, or declined."""
    from .tools import state_words

    last = next((e for e in reversed(hub.runner.track(run_id)) if e["type"] in ("gate.decided", "gate.declined", "gate.signed")), {})
    nxt = ", ".join((after.get("pending") or {}).get("assignees") or [])
    rec_now = hub.kernel.get(record["locator"]) or record
    if last.get("type") == "gate.signed":
        return (f"Signed; waiting for a countersignature from {nxt or 'a person'}: {record['number']} is not approved until then "
                f"(my criteria ask a {str(last.get('countersign_needed') or 'person').replace('-', ' ')}). My basis: {last.get('meaning', '')}.")
    if last.get("type") == "gate.decided" and last.get("by") == cog_id:
        verdict = last.get("verdict") or "approve"
        return (f"{'Approved' if verdict == 'approve' else 'Rejected'}: {record['number']} is now {state_words(rec_now.get('state'))}"
                + (f", waiting for {nxt}" if nxt else "") + f". My basis: {last.get('meaning', '')}.")
    return (f"I cannot sign {record['number']}: " + "; ".join(last.get("reasons") or ["my criteria do not hold"])
            + (f". It stays with {waited}." if waited else "."))


def _apply_into_file(hub, record: dict, sender: str, out: list[dict]) -> None:
    if record["type"] == "document":
        from . import wopi as _wopi

        f = hub.kernel.get(record["locator"])["fields"]
        machines = set(hub.registry.ids()) | {"hub"}
        people_editing = f.get("checked_out_via") == "editor" and any(e not in machines for e in (f.get("editors") or [f.get("checked_out_by")]) if e)
        if not people_editing:  # no live seat will do it: the hub writes the answers into the file now
            try:
                _wopi.WopiHost(hub).apply_replies(hub.kernel.get(record["locator"]), sender)
            except Exception as e:
                out.append(hub.inbox.notify(sender, kind="mention", subject=f"Could not write into {record['number']}", body=str(e), record=record["locator"]))
