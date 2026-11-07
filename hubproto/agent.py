"""The agent harness: a cog working on its own within the scope of the system.

A run gives a cog a goal (a task someone hands it, or one of its standing duties) and lets its model plan and act
through tools until it finishes or runs out of budget. Autonomy never bypasses policy:

- The cog acts **as itself**: it reads what its roles may read, and every write goes through a governed path:
  a comment on an item, a message to a person, starting a process (the runner checks its permission and role),
  taking its own turn at a Gate assigned to it (bound by its approval criteria), reviewing a document (filed as
  evidence) or editing a working document. Nothing writes record fields directly.
- **Autonomy is policy**: the cog's Frame profile says ``Autonomy: read``, ``propose`` (actions wait for a person
  to confirm them) or ``act``; changing it is a widening that two policy owners sign. ``Duty:`` lines are standing
  goals the duty runner takes on a schedule.
- **Budgets and audit**: each run has a limit on model rounds and on actions; every tool call is on the run's Track
  (state/agents/<run>.jsonl); the person who handed over the task is told what was done.
- **Kill switch**: ``HUBPROTO_AGENTS=off`` stops every run before it starts.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from .release import utcnow
from .rules import sha256_of
from .tools import RecordTools, as_text, tool_specs

ACTION_TOOLS = [
    {"name": "comment", "description": "Post a comment on a record (it appears in the item's chatter and its owner's inbox). Use it to remind, ask or report.",
     "parameters": {"type": "object", "properties": {"number": {"type": "string"}, "text": {"type": "string"}}, "required": ["number", "text"]}},
    {"name": "message", "description": "Send a direct message to a person (not a cog).",
     "parameters": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to", "body"]}},
    {"name": "start_process", "description": "Start a process (e.g. nonconformance, purchase-request, document-control) on an existing record (`number`) or "
                                             "with new `fields`. The system checks you may start it; its Gates still decide.",
     "parameters": {"type": "object", "properties": {"process": {"type": "string"}, "number": {"type": "string"},
                                                    "fields": {"type": "object", "description": "the new record's fields, e.g. title, description"}}, "required": ["process"]}},
    {"name": "take_turn", "description": "Take your turn at an item's approval: one waiting for you, or one waiting for people that your approval criteria "
                                         "let you sign when asked. You approve within your criteria (judgment criteria run your review first) or decline.",
     "parameters": {"type": "object", "properties": {"number": {"type": "string"}}, "required": ["number"]}},
    {"name": "complete_step", "description": "Complete a step assigned to you (e.g. a task filed for you) once the work is done; `summary` says what you did.",
     "parameters": {"type": "object", "properties": {"number": {"type": "string"}, "summary": {"type": "string"}}, "required": ["number", "summary"]}},
    {"name": "hand_back", "description": "Give a step assigned to you back to the person who filed it, saying why you cannot do it.",
     "parameters": {"type": "object", "properties": {"number": {"type": "string"}, "reason": {"type": "string"}}, "required": ["number", "reason"]}},
    {"name": "draft_document", "description": "Draft a new controlled document (a procedure, work instruction, form) as yourself, its author. `text` is the "
                                              "content in the writing markup (# headings, - bullets, | tables |). With submit=true it is sent for approval "
                                              "(document-control); you can never approve your own draft.",
     "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "text": {"type": "string"},
                                                    "change_type": {"type": "string", "enum": ["minor", "major"]},
                                                    "folder": {"type": "string", "description": "default Quality/Controlled Documents"},
                                                    "submit": {"type": "boolean"}}, "required": ["title", "text"]}},
    {"name": "review_document", "description": "Review a record or document against the procedures you follow; the review is filed as evidence on it.",
     "parameters": {"type": "object", "properties": {"number": {"type": "string"}, "question": {"type": "string"}}, "required": ["number"]}},
    {"name": "edit_document", "description": "Change a working document (not one in force): `request` says what to change: add, replace, delete, "
                                             "rewrite a section. The result is a new version; you are told which changes were made.",
     "parameters": {"type": "object", "properties": {"number": {"type": "string"}, "request": {"type": "string"}}, "required": ["number", "request"]}},
    {"name": "finish", "description": "End the run with a short summary of what you did and what you left for people.",
     "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}},
]
WRITE_TOOLS = {"comment", "message", "start_process", "take_turn", "review_document", "edit_document", "complete_step", "hand_back", "draft_document"}
AUTONOMY = ("read", "propose", "act")


def agents_enabled() -> bool:
    return (os.environ.get("HUBPROTO_AGENTS") or "on").lower() not in ("off", "0", "false", "no")


class AgentStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def append(self, run_id: str, event: dict) -> dict:
        ev = {"at": utcnow(), "run": run_id, **event}
        with (self.root / f"{run_id}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, default=str, sort_keys=True) + "\n")
        return ev

    def load(self, run_id: str) -> list[dict]:
        path = self.root / f"{run_id}.jsonl"
        if not path.exists():
            matches = list(self.root.glob(f"{run_id}*.jsonl"))
            if len(matches) != 1:
                return []
            path = matches[0]
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def runs(self, cog: str | None = None, limit: int = 30) -> list[dict]:
        out = []
        for p in sorted(self.root.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
            evs = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
            if not evs:
                continue
            start = evs[0]
            if cog and start.get("cog") != cog:
                continue
            end = next((e for e in reversed(evs) if e["type"] == "finished"), None)
            out.append({"run": start["run"], "cog": start.get("cog"), "goal": start.get("goal"), "by": start.get("by"), "trigger": start.get("trigger"),
                        "mode": start.get("mode"), "at": start["at"], "status": (end or {}).get("status", "running"),
                        "summary": (end or {}).get("summary", ""), "actions": sum(1 for e in evs if e["type"] == "tool" and e.get("write"))})
            if len(out) >= limit:
                break
        return out


class AgentTools(RecordTools):
    """The cog's tools for one run: read as the cog; write through governed paths, within the run's budget and mode."""

    def __init__(self, hub, cog: str, run_id: str, mode: str, max_actions: int, store: AgentStore, requester: str = ""):
        super().__init__(hub, cog)
        self.cog, self.run_id, self.mode, self.max_actions, self.store, self.requester = cog, run_id, mode, max_actions, store, requester
        self.actions = 0
        self.finished_summary: str | None = None
        self.proposed: list[dict] = []

    def execute(self, name: str, args: dict) -> dict:
        args = args if isinstance(args, dict) else {}
        write = name in WRITE_TOOLS
        if name == "finish":
            self.finished_summary = str(args.get("summary") or "").strip() or "finished"
            res = {"ok": True}
        elif write and self.mode == "read":
            res = {"error": f"{self.cog} may only read (Autonomy: read in its profile); describe what should be done instead"}
        elif write and self.actions >= self.max_actions:
            res = {"error": f"the run's budget of {self.max_actions} actions is used up; finish and report what is left"}
        elif write and self.mode == "propose" and args.get("number") and self._find(str(args.get("number"))) is None:
            res = {"error": f"no record {args.get('number')!r} that {self.cog} may read; nothing to propose there"}
        elif write and self.mode == "propose":
            self.proposed.append({"tool": name, "arguments": args})
            self.actions += 1
            res = {"proposed": True, "note": "recorded as a proposal; a person confirms it before it happens"}
        elif write:
            self.actions += 1
            res = self.act(name, args)
        else:
            res = self.call(name, args)
        self.store.append(self.run_id, {"type": "tool", "tool": name, "arguments": args, "write": write, "ok": "error" not in res,
                                        "result": as_text(res, 1500)})
        return res

    # --- the governed writes
    def act(self, name: str, a: dict) -> dict:
        hub = self.hub
        try:
            if name == "comment":
                rec = self._find(a.get("number", ""))
                if rec is None:
                    return {"error": f"no record {a.get('number')!r} that {self.cog} may read"}
                from .shell.text import owner_of

                to = owner_of(rec) or self.requester or self.cog
                msg = hub.inbox.send(sender=self.cog, to=to, subject=f"{self.cog} on {rec['number']}", body=str(a.get("text") or ""),
                                     record=rec["locator"])  # no hub: a cog's words never set off other cogs
                return {"ok": True, "posted_to": rec["number"], "notified": to, "message": msg["id"]}
            if name == "message":
                to = str(a.get("to") or "").strip()
                if (self.bundle.get("members") or {}).get(to, {}).get("type") != "person":
                    return {"error": f"{to!r} is not a person in the organization"}
                msg = hub.inbox.send(sender=self.cog, to=to, subject=str(a.get("subject") or f"From {self.cog}"), body=str(a.get("body") or ""))
                return {"ok": True, "sent_to": to, "message": msg["id"]}
            if name == "start_process":
                process = str(a.get("process") or "")
                rec = self._find(a["number"]) if a.get("number") else None
                if a.get("number") and rec is None:
                    return {"error": f"no record {a.get('number')!r} that {self.cog} may read"}
                run_id = hub.runner.submit(process, {"record": dict(a.get("fields") or {})}, owner=self.cog, record=rec["locator"] if rec else None)
                state = hub.runner.load(run_id)
                item = hub.kernel.get(state["record"]) if state.get("record") else None
                return {"ok": True, "process": process, "record": item["number"] if item else None, "status": state["status"],
                        "waiting_for": (state.get("pending") or {}).get("assignees")}
            if name == "take_turn":
                rec = self._find(a.get("number", ""))
                run = hub.runner.run_for_record(rec["locator"]) if rec else None
                if not run or run["status"] != "waiting":
                    return {"error": f"{a.get('number')} is not waiting at an approval"}
                if hub.runner.is_assigned_work(run, self.cog):
                    return {"error": f"{a.get('number')} is assigned to you as work: use complete_step or hand_back"}
                if self.cog in (run.get("pending") or {}).get("assignees", []):
                    after = hub.runner.cog_decide(run["id"], self.cog)
                else:  # waiting for people: the cog signs only where its criteria let it when asked
                    after = hub.runner.cog_step_in(run["id"], self.cog, asked_by=self.requester or self.cog)
                last = next((e for e in reversed(hub.runner.track(run["id"])) if e["type"] in ("gate.decided", "gate.declined", "gate.signed")), {})
                return {"ok": True, "outcome": last.get("type", "").split(".")[-1], "status": after["status"], "reasons": last.get("reasons"),
                        "waiting_for": (after.get("pending") or {}).get("assignees")}
            if name in ("complete_step", "hand_back"):
                rec = self._find(a.get("number", ""))
                run = hub.runner.run_for_record(rec["locator"]) if rec else None
                if not run or not hub.runner.is_assigned_work(run, self.cog):
                    return {"error": f"{a.get('number')} has no step assigned to {self.cog}"}
                if name == "complete_step":
                    after = hub.runner.cog_complete(run["id"], self.cog, str(a.get("summary") or ""))
                else:
                    after = hub.runner.hand_back(run["id"], self.cog, str(a.get("reason") or "no reason given"))
                return {"ok": True, "status": after["status"], "waiting_for": (after.get("pending") or {}).get("assignees")}
            if name == "draft_document":
                return self._draft_document(a)
            if name == "review_document":
                rec = self._find(a.get("number", ""))
                if rec is None:
                    return {"error": f"no record {a.get('number')!r} that {self.cog} may read"}
                env = hub.registry.get(self.cog).invoke("review-document", {"record": rec["locator"], "question": a.get("question") or ""}, self._ctx(rec))
                r = env.get("payload") or {}
                return {"ok": env.get("ok", True), "verdict": r.get("verdict"), "findings": r.get("findings"), "evidence": r.get("evidence")}
            if name == "edit_document":
                rec = self._find(a.get("number", ""))
                if rec is None or rec["type"] != "document":
                    return {"error": f"no document {a.get('number')!r} that {self.cog} may read"}
                f = rec["fields"]
                machines = set(hub.registry.ids()) | {"hub"}
                if f.get("checked_out_by") and f["checked_out_by"] not in machines:
                    return {"error": f"{rec['number']} is being edited by {f['checked_out_by']}; comment on it instead"}
                if f.get("doc_class") in ("evidence", "external") or (f.get("doc_class") == "controlled" and rec["state"] not in ("open", "draft", "returned", "failed")):
                    return {"error": f"{rec['number']} is not a working document; propose a change through its process instead"}
                env = hub.registry.get(self.cog).invoke("edit-document", {"record": rec["locator"], "request": a.get("request") or ""}, self._ctx(rec))
                r = env.get("payload") or {}
                from . import office, wopi as _wopi

                from . import edits

                content, latest = hub.dms.content(rec["locator"])
                ext = _wopi.extension_of(latest.get("filename") or f.get("filename") or "")
                new, report = edits.apply_ops(content, ext, r.get("ops") or [])
                if new is None:
                    return {"error": "nothing was changed: " + ("; ".join(f"{x['op']} ({x['detail']})" for x in report) or "no operations")}
                hub.dms.add_version(rec["locator"], new, by="hub", filename=latest.get("filename") or "", mime=latest.get("mime") or "",
                                    note=f"edited by {self.cog} (agent run {self.run_id})")
                _wopi.WopiHost(hub).index(hub.kernel.get(rec["locator"]), new, "hub")
                return {"ok": True, "document": rec["number"], "version": hub.dms.versions(rec["locator"])[-1]["version"],
                        "changes": report}
        except Exception as e:  # the system refused (permission, criteria, state): the agent hears why
            return {"error": f"{type(e).__name__}: {e}"}
        return {"error": f"no action {name!r}"}

    def _draft_document(self, a: dict) -> dict:
        from . import authz, office, wopi as _wopi

        hub = self.hub
        title = str(a.get("title") or "").strip()
        if not title:
            return {"error": "a document needs a title"}
        folder = str(a.get("folder") or "Quality/Controlled Documents")
        allowed = authz.check(self.bundle, self.cog, "create", "document", folder)
        if not allowed["allowed"]:
            return {"error": allowed["why"]}
        change_type = a.get("change_type") if a.get("change_type") in ("minor", "major") else "major"
        lines = [ln for ln in str(a.get("text") or "").splitlines()]
        if not any(ln.strip() for ln in lines):
            return {"error": "the draft has no text"}
        if not lines[0].lstrip().startswith("# "):  # the title is the level-1 heading unless the text has one
            lines = [f"# {title}", ""] + lines
        blank, mime = _wopi.blank_document("docx")
        content = office.insert_paragraphs(blank, "docx", lines, "replace") or office.insert_paragraphs(blank, "docx", lines, "start")
        doc = hub.dms.create(title=title, doc_class="controlled", folder=folder, by=self.cog, content=content, filename=f"{title}.docx", mime=mime,
                             doc_type="sop", fields={"change_type": change_type, "drafted_by_run": self.run_id})
        out = {"ok": True, "document": doc["number"], "state": doc["state"], "change_type": change_type}
        if a.get("submit"):
            run_id = hub.runner.submit("document-control", {"record": {"title": title, "change_type": change_type}}, owner=self.cog,
                                       record=doc["locator"])
            state = hub.runner.load(run_id)
            out.update({"submitted": True, "status": state["status"], "waiting_for": (state.get("pending") or {}).get("assignees")})
        return out

    def _ctx(self, rec: dict | None):
        from .cogs import RunContext

        return RunContext(run_id=f"agent-{self.run_id}", release=(self.hub.releases.current() or {}).get("digest", ""), bundle=self.bundle, owner=self.cog,
                          kernel=self.hub.kernel, runner=self.hub.runner, inbox=self.hub.inbox, record=rec["locator"] if rec else None,
                          dms=self.hub.dms, frames=lambda: self.hub.frames, model=self.hub.model,
                          extras={"cog_profile": (self.bundle.get("cogs") or {}).get(self.cog) or {}})


AGENT_RULES = """YOU ARE WORKING ON YOUR OWN in the hub, as {cog}, on this goal: {goal}
Mode: {mode}. Budget: at most {actions} actions and {rounds} steps.
- Look first (search_records, get_record, read_document, waiting_for, record_history, overview), then act with the action tools.
- You act only through the tools. The system enforces permissions, your approval criteria and segregation of duties: if a tool refuses,
  say so in your summary and move on; do not try another way around it.
- Be economical: do what the goal needs and no more; never message or comment twice about the same thing.
- End with finish(summary): what you did, with record numbers, and what you left for people and why."""


class Agent:
    def __init__(self, hub, cog: str):
        self.hub = hub
        self.cog = cog
        self.store = AgentStore(hub.root / "state" / "agents")

    def profile(self) -> dict:
        meta = self.hub.releases.current()
        bundle = self.hub.releases.bundle(meta["digest"]) if meta else {}
        return (bundle.get("cogs") or {}).get(self.cog) or {}

    def prepare(self, goal: str, *, by: str, trigger: str = "task", max_actions: int | None = None, max_rounds: int | None = None) -> dict:
        """Fast, under the lock: who, what, the system prompt and the tool runner for one run."""
        meta = self.hub.releases.current()
        bundle = self.hub.releases.bundle(meta["digest"]) if meta else {"members": {}}
        if (bundle.get("members") or {}).get(self.cog, {}).get("type") != "cog":
            raise ValueError(f"{self.cog} is not a cog of the organization")
        if not agents_enabled():
            raise ValueError("agent runs are switched off (HUBPROTO_AGENTS=off)")
        from .model import cog_availability

        ok, why = cog_availability(bundle, self.cog)
        if not ok:
            raise ValueError(why)
        prof = (bundle.get("cogs") or {}).get(self.cog) or {}
        mode = prof.get("autonomy") or "propose"
        actions = int(max_actions or os.environ.get("HUBPROTO_AGENT_ACTIONS") or 5)
        rounds = int(max_rounds or os.environ.get("HUBPROTO_AGENT_ROUNDS") or 10)
        run_id = uuid.uuid4().hex[:10]
        self.store.append(run_id, {"type": "started", "cog": self.cog, "goal": goal, "by": by, "trigger": trigger, "mode": mode,
                                   "budget": {"actions": actions, "rounds": rounds}, "release": meta["digest"] if meta else ""})
        tools = AgentTools(self.hub, self.cog, run_id, mode, actions, self.store, requester=by)
        from .cogs import cog_context

        system = cog_context(tools._ctx(None), None) + "\n\n" + AGENT_RULES.format(cog=self.cog, goal=goal, mode={
            "act": "act: you may act on your own", "propose": "propose: your actions are recorded as proposals a person confirms",
            "read": "read: you may only look and report"}[mode], actions=actions, rounds=rounds)
        specs = tool_specs() + ([t for t in ACTION_TOOLS if t["name"] == "finish"] if mode == "read" else ACTION_TOOLS)
        return {"run": run_id, "tools": tools, "system": system, "specs": specs, "model": prof.get("model") or "stand-in", "mode": mode,
                "goal": goal, "by": by, "rounds": rounds}

    def think(self, prep: dict, call_tool=None) -> dict:
        """Slow, unlocked: the model plans and calls tools; ``call_tool`` (locked by the caller) runs each one."""
        return self.hub.model.complete_with_tools(model=prep["model"], system=prep["system"], user=prep["goal"], by=self.cog, max_tokens=900,
                                                  tools=prep["specs"], call_tool=call_tool or prep["tools"].execute, max_rounds=prep["rounds"])

    def conclude(self, prep: dict, out: dict) -> dict:
        tools: AgentTools = prep["tools"]
        summary = tools.finished_summary or (out.get("text") or "").strip() or "the run ended without a summary"
        status = "proposed" if tools.proposed else ("unavailable" if out.get("unavailable") else "done")
        if out.get("unavailable"):
            summary = f"{self.cog} is not available: {out['unavailable']}. Nothing more was done."
        ev = self.store.append(prep["run"], {"type": "finished", "status": status, "summary": summary, "actions": tools.actions,
                                             "proposed": tools.proposed, "model": out.get("served_model"), "rounds_used": len(out.get("tool_calls") or [])})
        if prep["by"] and prep["by"] != self.cog:
            self.hub.inbox.notify(prep["by"], kind="agent-run", subject=f"{self.cog} {'proposes' if tools.proposed else 'finished'}: {prep['goal'][:80]}",
                                  body=summary + (f"\n{len(tools.proposed)} action(s) wait for your confirmation." if tools.proposed else ""),
                                  run_id=prep["run"])
        return {"run": prep["run"], "status": status, "summary": summary, "actions": tools.actions, "proposed": tools.proposed, "event": ev}

    def run(self, goal: str, *, by: str, trigger: str = "task", **budget) -> dict:
        prep = self.prepare(goal, by=by, trigger=trigger, **budget)
        return self.conclude(prep, self.think_safely(prep))

    def think_safely(self, prep: dict, call_tool=None) -> dict:
        """think(), but a model that goes away mid-run ends the run as unavailable instead of with an error."""
        from .model import ModelUnavailable

        try:
            return self.think(prep, call_tool)
        except ModelUnavailable as e:
            return {"text": "", "stand_in": False, "unavailable": str(e), "tool_calls": [], "served_model": ""}

    def confirm(self, run_id: str, by: str) -> dict:
        """A person confirms a run's proposed actions: they are carried out now, as the cog, through the same paths."""
        evs = self.store.load(run_id)
        end = next((e for e in reversed(evs) if e["type"] == "finished"), None)
        if not end or end.get("status") != "proposed":
            raise ValueError(f"run {run_id} has no proposed actions waiting")
        start = evs[0]
        tools = AgentTools(self.hub, self.cog, run_id, "act", len(end["proposed"]), self.store, requester=by)
        results = []
        for p in end["proposed"]:
            results.append({"tool": p["tool"], "result": tools.execute(p["tool"], p["arguments"])})
        self.store.append(run_id, {"type": "finished", "status": "confirmed", "summary": f"{by} confirmed {len(results)} proposed action(s)",
                                   "actions": tools.actions, "proposed": [], "confirmed_by": by})
        return {"run": run_id, "goal": start.get("goal"), "results": results}

    def duties(self) -> list[str]:
        return list(self.profile().get("duties") or [])


def due_duties(hub, cog: str | None = None, today: str | None = None) -> list[tuple[str, str]]:
    """(cog, duty) pairs not yet run today."""
    from datetime import date

    today = today or date.today().isoformat()
    meta = hub.releases.current()
    bundle = hub.releases.bundle(meta["digest"]) if meta else {}
    done = _duty_log(hub)
    out = []
    for name, prof in sorted((bundle.get("cogs") or {}).items()):
        if cog and name != cog:
            continue
        for duty in prof.get("duties") or []:
            if done.get(f"{name}|{sha256_of(duty)[:12]}") != today:
                out.append((name, duty))
    return out


def _duty_log(hub) -> dict:
    path = hub.root / "state" / "agents" / "duties.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def mark_duty(hub, cog: str, duty: str, today: str | None = None) -> None:
    from datetime import date

    path = hub.root / "state" / "agents" / "duties.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    log = _duty_log(hub)
    log[f"{cog}|{sha256_of(duty)[:12]}"] = today or date.today().isoformat()
    path.write_text(json.dumps(log, indent=1, sort_keys=True))


def run_duties(hub, cog: str | None = None, today: str | None = None) -> list[dict]:
    """Take up every duty not yet done today; each is an agent run triggered by the duty, on behalf of the cog itself."""
    out = []
    for name, duty in due_duties(hub, cog, today):
        res = Agent(hub, name).run(duty, by=name, trigger="duty")
        mark_duty(hub, name, duty, today)
        out.append({"cog": name, "duty": duty, **{k: res[k] for k in ("run", "status", "summary", "actions")}})
    return out
