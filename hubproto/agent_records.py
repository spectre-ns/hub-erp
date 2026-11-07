"""Record and admin tools for cogs working on their own: create, update, move, assign, link, restart, tasks,
permission checks and handing work to another cog.

Every tool acts as the cog, through the same governed paths a person's action takes: the policy release decides
what the cog may create, change or move (authz), the runner starts processes and restarts items, the kernel guards
field values, and assignments and hand-offs go into queues. A write tool counts against the run's budget and follows
the cog's autonomy (registered with write=True); check_permission only looks."""

from __future__ import annotations

import hashlib

from . import authz
from .agent import register
from .kernel import KernelGuardError

# Fields a cog never sets directly: the runner, the DMS and the editor manage them.
MANAGED_FIELDS = {"doc_class", "revision", "minor", "checked_out_by", "checked_out_via", "editors", "wopi_lock", "content_sha",
                  "text", "office_comments", "owner", "author", "created_by", "filed_by", "proposal", "proposal_number",
                  "withdrawn", "withdrawn_by", "risk", "conditions", "state", "decisions", "assignees"}
FINISHED_STATES = {"closed", "effective", "obsolete", "superseded", "posted", "cancelled", "rejected", "done", "completed"}


def _type_of(hub, name: str) -> str | None:
    """A record type from its key ('ncr') or its object name ('nonconformance', 'nonconformances')."""
    from .extract import singular_role

    name = (name or "").strip().lower()
    if name in hub.kernel.types and name != "$comment":
        return name
    single = singular_role(name)
    for key, td in hub.kernel.types.items():
        if key != "$comment" and isinstance(td, dict) and td.get("object") in (name, single):
            return key
    return single if single in hub.kernel.types else None


def _object_of(hub, type_: str) -> str:
    td = hub.kernel.types.get(type_) or {}
    return td.get("object") or type_


def _process_for_type(bundle: dict, type_: str, wanted: str = "") -> str | None:
    workflows = bundle.get("workflows") or {}
    if wanted:
        return wanted if (workflows.get(wanted) or {}).get("record_type") == type_ else None
    return next((p for p, wf in workflows.items() if wf.get("record_type") == type_), None)


def _member(bundle: dict, name: str) -> dict | None:
    return (bundle.get("members") or {}).get(name)


# ---------------------------------------------------------------- create_record
def create_record(t, a: dict) -> dict:
    hub, bundle = t.hub, t.bundle
    type_ = _type_of(hub, str(a.get("type") or ""))
    if type_ is None:
        return {"error": f"there is no record type {a.get('type')!r}; known types: {', '.join(sorted(k for k in hub.kernel.types if k != '$comment'))}"}
    fields = dict(a.get("fields") or {})
    process = _process_for_type(bundle, type_, str(a.get("process") or ""))
    if a.get("process") and process is None:
        return {"error": f"process {a.get('process')!r} does not file {type_} records"}
    if process:
        # The process files the record: its Gates, routing and checks apply as for anyone who starts it.
        run_id = hub.runner.submit(process, {"record": fields}, owner=t.cog)
        state = hub.runner.load(run_id)
        rec = hub.kernel.get(state["record"])
        return {"ok": True, "number": rec["number"], "type": type_, "process": process, "status": state["status"],
                "state": rec["state"], "waiting_for": (state.get("pending") or {}).get("assignees")}
    folder = str(a.get("folder") or "")
    if not folder:
        return {"error": f"no process files {type_} records, so say which folder it goes in"}
    if folder not in (bundle.get("folders") or []):
        return {"error": f"there is no folder {folder!r} in the policy release"}
    obj = _object_of(hub, type_)
    allowed = authz.check(bundle, t.cog, "create", obj, folder)
    if not allowed["allowed"]:
        return {"error": allowed["why"]}
    rec = hub.kernel.create(type_, folder, fields, by=t.cog, run_id=f"agent-{t.run_id}")
    return {"ok": True, "number": rec["number"], "type": type_, "folder": folder, "state": rec["state"], "why": allowed["why"]}


register({"name": "create_record",
          "description": "Create a new record (a nonconformance, change request, purchase request, task...) with its fields. "
                         "When a process files that type, the process is started and its Gates decide; otherwise it is filed in `folder` "
                         "if the policy lets you create it there.",
          "parameters": {"type": "object", "properties": {
              "type": {"type": "string", "description": "record type, e.g. ncr, change-request, task, purchase-request"},
              "fields": {"type": "object", "description": "the record's fields, e.g. title, description"},
              "folder": {"type": "string", "description": "only when no process files this type"},
              "process": {"type": "string", "description": "optional: the process to start, when several file this type"}},
              "required": ["type", "fields"]}},
         create_record, write=True)


# ---------------------------------------------------------------- update_fields
def update_fields(t, a: dict) -> dict:
    hub, bundle = t.hub, t.bundle
    rec = t._find(str(a.get("number") or ""))
    if rec is None:
        return {"error": f"no record {a.get('number')!r} that {t.cog} may read"}
    fields = dict(a.get("fields") or {})
    if not fields:
        return {"error": "say which fields to set"}
    td = hub.kernel.types.get(rec["type"]) or {}
    managed = sorted(k for k in fields if k in MANAGED_FIELDS or k in set(td.get("system_fields") or []) or k.startswith("_"))
    if managed:
        return {"error": f"{', '.join(managed)} {'is' if len(managed) == 1 else 'are'} managed by the hub (its process, the document system "
                         "or the editor) and cannot be set directly"}
    if rec["state"] in FINISHED_STATES:
        return {"error": f"{rec['number']} is {rec['state']}; it is not changed in place"}
    if rec["type"] == "document" and rec["fields"].get("doc_class") in ("evidence", "external"):
        return {"error": f"{rec['number']} is {rec['fields'].get('doc_class')} and is kept as it was filed"}
    obj = _object_of(hub, rec["type"])
    allowed = authz.check(bundle, t.cog, "update", obj, rec["folder"])
    if not allowed["allowed"]:
        allowed = authz.check(bundle, t.cog, "create", obj, rec["folder"])
    if not allowed["allowed"]:
        return {"error": allowed["why"]}
    try:
        after = hub.kernel.set_fields(rec["locator"], fields, by=t.cog, run_id=f"agent-{t.run_id}")
    except KernelGuardError as e:
        return {"error": str(e)}
    return {"ok": True, "number": after["number"], "set": sorted(fields), "why": allowed["why"]}


register({"name": "update_fields",
          "description": "Set fields on a record you may change (for example a description, a due date or a priority). "
                         "Fields the hub manages (state, owner, revision, decisions, assignees) cannot be set, and finished records are not changed.",
          "parameters": {"type": "object", "properties": {"number": {"type": "string"}, "fields": {"type": "object"}},
                         "required": ["number", "fields"]}},
         update_fields, write=True)


# ---------------------------------------------------------------- move_record
def move_record(t, a: dict) -> dict:
    hub, bundle = t.hub, t.bundle
    rec = t._find(str(a.get("number") or ""))
    if rec is None:
        return {"error": f"no record {a.get('number')!r} that {t.cog} may read"}
    folder = str(a.get("folder") or "").strip()
    if folder not in (bundle.get("folders") or []):
        return {"error": f"there is no folder {folder!r} in the policy release"}
    if folder == rec["folder"]:
        return {"error": f"{rec['number']} is already in {folder}"}
    run = hub.runner.run_for_record(rec["locator"])
    if run and run["status"] in ("waiting", "submitted", "running"):
        return {"error": f"{rec['number']} is in a {run['process']} run; it is filed where its process keeps it"}
    obj = _object_of(hub, rec["type"])
    allowed = authz.check(bundle, t.cog, "create", obj, folder)
    if not allowed["allowed"]:
        return {"error": allowed["why"]}
    try:
        after = hub.kernel.move(rec["locator"], folder, by=t.cog, run_id=f"agent-{t.run_id}", reason=str(a.get("reason") or f"moved by {t.cog}"))
    except KernelGuardError as e:
        return {"error": str(e)}
    return {"ok": True, "number": after["number"], "from": rec["folder"], "to": after["folder"]}


register({"name": "move_record",
          "description": "Move a record to another folder, where the policy lets you file that kind of record. "
                         "A record in a running process stays where its process keeps it.",
          "parameters": {"type": "object", "properties": {"number": {"type": "string"}, "folder": {"type": "string"}, "reason": {"type": "string"}},
                         "required": ["number", "folder"]}},
         move_record, write=True)


# ---------------------------------------------------------------- assign
def assign(t, a: dict) -> dict:
    hub, bundle = t.hub, t.bundle
    rec = t._find(str(a.get("number") or ""))
    if rec is None:
        return {"error": f"no record {a.get('number')!r} that {t.cog} may read"}
    to = str(a.get("to") or "").strip()
    instruction = str(a.get("instruction") or "").strip()
    member = _member(bundle, to)
    if member is None:
        return {"error": f"{to!r} is not a member of the organization"}
    if to == t.cog:
        return {"error": "you cannot assign an item to yourself"}
    ok, _ = hub.runner.may_restart(rec, t.cog)
    if not (ok or t.cog in (rec.get("assignees") or []) or rec.get("created_by") == t.cog):
        return {"error": f"only its owner, whoever it is assigned to, or someone who may approve it assigns {rec['number']}"}
    assignees = list(dict.fromkeys([*(rec.get("assignees") or []), to]))
    if member["type"] == "cog":
        from .model import cog_availability

        if not instruction:
            return {"error": f"say what {to} should do with {rec['number']} (instruction)"}
        avail, why = cog_availability(bundle, to)
        if not avail:
            return {"error": f"{to} is not available: {why}"}
        hub.kernel.assign(rec["locator"], assignees, by=t.cog)
        hub.inbox.post(sender=t.cog, to=to, record=rec["locator"], subject=f"Assigned {rec['number']} to {to}", body=instruction)
        task = hub.queue.submit(to, "assigned", by=t.cog, title=instruction, record=rec["locator"],
                                payload={"record": rec["locator"], "instruction": instruction})
        return {"ok": True, "number": rec["number"], "assigned_to": to, "queued": task["status"] in ("queued", "working"),
                "task": task["id"], "status": task["status"]}
    hub.kernel.assign(rec["locator"], assignees, by=t.cog)
    hub.inbox.post(sender=t.cog, to=to, record=rec["locator"], subject=f"Assigned {rec['number']} to you",
                   body=instruction or f"{t.cog} assigned {rec['number']} to you.")
    return {"ok": True, "number": rec["number"], "assigned_to": to, "notified": True}


register({"name": "assign",
          "description": "Assign an item to a person or another cog with an instruction. "
                         "A person is told in their inbox; a cog takes it from its queue. You may assign items you own, are assigned, filed, or may approve.",
          "parameters": {"type": "object", "properties": {"number": {"type": "string"}, "to": {"type": "string"},
                                                         "instruction": {"type": "string"}}, "required": ["number", "to"]}},
         assign, write=True)


# ---------------------------------------------------------------- link_records
def link_records(t, a: dict) -> dict:
    hub = t.hub
    rec = t._find(str(a.get("number") or ""))
    other = t._find(str(a.get("target") or ""))
    if rec is None or other is None:
        missing = a.get("number") if rec is None else a.get("target")
        return {"error": f"no record {missing!r} that {t.cog} may read"}
    rel = str(a.get("rel") or "").strip()
    vocab = {k: v for k, v in hub.kernel.link_vocab.items() if not k.startswith("$")}
    if rel not in vocab:
        return {"error": f"unknown link {rel!r}; use one of: {', '.join(sorted(vocab))}"}
    if any(l["rel"] == rel and l["target"] == other["locator"] for l in rec["links"]):
        return {"error": f"{rec['number']} already {rel} {other['number']}"}
    try:
        hub.kernel.link(rec["locator"], rel, other["locator"], by=t.cog, run_id=f"agent-{t.run_id}")
    except KernelGuardError as e:
        return {"error": str(e)}
    return {"ok": True, "link": f"{rec['number']} {rel} {other['number']}", "inverse": f"{other['number']} {vocab[rel]} {rec['number']}"}


register({"name": "link_records",
          "description": "Link two records with a named relation (concerns, caused, references, affects, supersedes...). "
                         "The link shows on both records.",
          "parameters": {"type": "object", "properties": {"number": {"type": "string"}, "rel": {"type": "string"}, "target": {"type": "string"}},
                         "required": ["number", "rel", "target"]}},
         link_records, write=True)


# ---------------------------------------------------------------- send_back_to_start
def send_back_to_start(t, a: dict) -> dict:
    hub = t.hub
    rec = t._find(str(a.get("number") or ""))
    if rec is None:
        return {"error": f"no record {a.get('number')!r} that {t.cog} may read"}
    reason = str(a.get("reason") or "").strip()
    if not reason:
        return {"error": "say why it goes back to the start (reason)"}
    ok, why = hub.runner.may_restart(rec, t.cog)
    if not ok:
        return {"error": why}
    after = hub.runner.restart(rec["locator"], t.cog, reason)
    return {"ok": True, "number": after["number"], "state": after["state"]}


register({"name": "send_back_to_start",
          "description": "Send an item back to the first step of its workflow, stopping the run it is in, with a reason. "
                         "Its owner and whoever it waited for are told; versions and history stay.",
          "parameters": {"type": "object", "properties": {"number": {"type": "string"}, "reason": {"type": "string"}},
                         "required": ["number", "reason"]}},
         send_back_to_start, write=True)


# ---------------------------------------------------------------- create_task
def create_task(t, a: dict) -> dict:
    hub, bundle = t.hub, t.bundle
    title = str(a.get("title") or "").strip()
    if not title:
        return {"error": "a task needs a title"}
    assignee = str(a.get("assignee") or "").strip()
    if assignee and _member(bundle, assignee) is None:
        return {"error": f"{assignee!r} is not a member of the organization"}
    if "task" not in (bundle.get("workflows") or {}):
        return {"error": "the policy release defines no task process"}
    fields = {"title": title, "description": str(a.get("description") or ""), "assignee": assignee, "due": a.get("due") or "",
              "priority": a.get("priority") or ""}
    fields = {k: v for k, v in fields.items() if v not in (None, "")}
    run_id = hub.runner.submit("task", {"record": fields}, owner=t.cog)
    state = hub.runner.load(run_id)
    rec = hub.kernel.get(state["record"])
    return {"ok": True, "number": rec["number"], "status": state["status"], "waiting_for": (state.get("pending") or {}).get("assignees")}


register({"name": "create_task",
          "description": "File a task for a person or a cog, with a title, a description and an optional due date (YYYY-MM-DD). "
                         "It waits for the assignee until they complete it.",
          "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "description": {"type": "string"},
                                                         "assignee": {"type": "string"}, "due": {"type": "string"},
                                                         "priority": {"type": "string"}}, "required": ["title"]}},
         create_task, write=True)


# ---------------------------------------------------------------- delegate_to_cog
def delegate_to_cog(t, a: dict) -> dict:
    hub, bundle = t.hub, t.bundle
    other = str(a.get("cog") or "").strip()
    goal = str(a.get("goal") or "").strip()
    if not goal:
        return {"error": "say what the other cog should do (goal)"}
    if other == t.cog:
        return {"error": "you cannot hand work to yourself"}
    m = _member(bundle, other)
    if not m or m.get("type") != "cog" or other not in hub.registry.ids():
        return {"error": f"{other!r} is not a cog of the organization"}
    from .model import cog_availability

    avail, why = cog_availability(bundle, other)
    if not avail:
        return {"error": f"{other} is not available: {why}"}
    key = f"delegate:{t.cog}:{other}:" + hashlib.sha256((goal + str(a.get("number") or "")).encode("utf-8")).hexdigest()[:16]
    if a.get("number"):
        rec = t._find(str(a["number"]))
        if rec is None:
            return {"error": f"no record {a.get('number')!r} that {t.cog} may read"}
        hub.inbox.post(sender=t.cog, to=other, record=rec["locator"], subject=f"{t.cog} asks {other} about {rec['number']}", body=goal)
        task = hub.queue.submit(other, "assigned", by=t.cog, title=goal, record=rec["locator"],
                                payload={"record": rec["locator"], "instruction": goal}, key=key)
    else:
        task = hub.queue.submit(other, "task", by=t.cog, title=goal, payload={"goal": goal}, key=key)
    return {"ok": True, "cog": other, "task": task["id"], "status": task["status"],
            "result": (task.get("result") or {}).get("summary") if task["status"] == "done" else None}


register({"name": "delegate_to_cog",
          "description": "Hand a piece of work to another cog: it goes into that cog's queue, about an item when you give its number. "
                         "Asking twice for the same thing while it is still open does not queue it again.",
          "parameters": {"type": "object", "properties": {"cog": {"type": "string"}, "goal": {"type": "string"}, "number": {"type": "string"}},
                         "required": ["cog", "goal"]}},
         delegate_to_cog, write=True)


# ---------------------------------------------------------------- check_permission (look)
def check_permission(t, a: dict) -> dict:
    principal = str(a.get("principal") or t.cog).strip()
    action = str(a.get("action") or "").strip().lower()
    obj = str(a.get("object") or "").strip()
    folder = str(a.get("folder") or "").strip()
    if not (action and obj and folder):
        return {"error": "say the action (create, read, approve, update, delete), the object (bill, document...) and the folder"}
    d = authz.check(t.bundle, principal, action, obj, folder)
    src = (d.get("rule") or {}).get("source") or {}
    return {"principal": principal, "action": action, "object": obj, "folder": folder, "allowed": d["allowed"], "why": d["why"],
            "rule": {"frame": src.get("frame"), "line": src.get("line"), "text": src.get("text")} if src else None}


register({"name": "check_permission",
          "description": "Check whether someone (you, by default) may create, read, approve, update or delete a kind of record in a folder. "
                         "The answer quotes the policy sentence that decides it.",
          "parameters": {"type": "object", "properties": {"action": {"type": "string"}, "object": {"type": "string"}, "folder": {"type": "string"},
                                                         "principal": {"type": "string"}}, "required": ["action", "object", "folder"]}},
         check_permission, write=False)
