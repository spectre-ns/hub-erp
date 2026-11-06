"""The Op runner and the workflow engine, one mechanism seen from two sides.

A run executes one transition chain of a record: the automatic steps after
a human transition, up to the next human one. A record's state is the
projection of its transitions, so every state change is on a Track, pinned
to a policy release, and nothing moves a record outside a run.

A human Gate routes the item through the release routing table: a cog
candidate may decide only within its approval criteria; otherwise the item
waits in people's inboxes with a due date from the SLA. An approval issues a
grant; only then can reaching Cogs write beyond the run's own record.

Cogs see the kernel through a capability-scoped client (``ScopedKernel``):
their declared ``reaches`` and the run's grant are checked on the kernel
side, not by the Cog's good manners.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

from . import authz
from .cogs import CogRegistry, RunContext
from .dms import DMS
from .envelope import gate_default
from .kernel import Kernel, KernelGuardError, ScopedKernel
from .release import ReleaseStore, utcnow
from .routing import candidates, principals_for
from .rules import sha256_of
from .track import Track

FIELD_ROLES = {"assignee": "assignee", "owner": "owner", "author": "author", "requester": "requested_by",
               "auditor": "auditor", "chair": "chair", "evaluator": "evaluator", "reporter": "reported_by"}
LIVE_STATUSES = ("submitted", "running", "waiting", "completed")
def pending_title(step: dict) -> str:
    return (step.get("gate") or {}).get("title") or step.get("title") or "Approval"


PRE_DECISION_HOOKS: dict = {}  # record type -> fn(kernel, record, verdict); raise KernelGuardError to block
POST_DECISION_HOOKS: dict = {}  # record type -> fn(kernel, record, verdict); runs after the decision took effect


class RunError(Exception):
    pass


def _resolve(expr, inputs: dict, steps: dict):
    if isinstance(expr, dict) and "$from" in expr:
        path = expr["$from"].split(".")
        node: object = {"inputs": inputs, "steps": steps}
        for part in path:
            if isinstance(node, dict):
                node = node.get(part)
            else:
                return None
        return node
    if isinstance(expr, dict) and "$literal" in expr:
        return expr["$literal"]
    return expr


class Runner:
    def __init__(self, root: Path, kernel: Kernel, releases: ReleaseStore, registry: CogRegistry, inbox=None, dms=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.kernel = kernel
        self.releases = releases
        self.registry = registry
        self.inbox = inbox
        self.dms = dms
        self.frames_store = None  # set by the Hub: the SOP texts a context cog reads
        self.model = None  # set by the Hub: the model gateway context cogs draft with

    # ------------------------------------------------------------ persistence
    def _dir(self, run_id: str) -> Path:
        return self.root / run_id

    def load(self, run_id: str) -> dict:
        matches = [p for p in self.root.iterdir() if p.is_dir() and p.name.startswith(run_id)]
        if len(matches) != 1:
            raise RunError(f"run {run_id!r}: {len(matches)} matches")
        state = json.loads((matches[0] / "state.json").read_text())
        if state.get("pending") and state["pending"].get("kind") == "decision":
            state["seen_digest"] = self.seen_digest(state)
        return state

    def seen_digest(self, state: dict) -> str:
        """What a signer is confirming: the pending payload plus the record as it is now (fields, links, document
        versions). A decision must quote it, so a change to the item after the card was rendered is refused."""
        pending = state.get("pending") or {}
        seen = {"payload": pending.get("payload_digest"), "step": pending.get("step")}
        if state.get("record"):
            rec = self.kernel.get(state["record"])
            if rec:
                seen["fields"] = rec["fields"]
                seen["state"] = rec["state"]
                seen["links"] = sorted((l["rel"], l["target"]) for l in rec["links"])
                docs = self.dms.documents_of(rec["locator"]) if self.dms else []
                seen["documents"] = sorted((d["locator"], str(d["fields"].get("content_sha") or ""), str(d["fields"].get("revision") or "")) for d in docs)
        return sha256_of(seen)[:16]

    def _save(self, state: dict) -> None:
        state["status"] = Track(self._dir(state["id"])).status()
        if state["status"] in ("failed", "rejected", "completed", "cancelled"):
            state["pending"] = None
        path = self._dir(state["id"]) / "state.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
        tmp.replace(path)  # atomic: a crash never leaves a half-written state

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.root.iterdir()):
            if (p / "state.json").exists():
                try:
                    out.append(json.loads((p / "state.json").read_text()))
                except json.JSONDecodeError:
                    continue
        return sorted(out, key=lambda s: (s["created_at"], s.get("created_ns", 0)))

    def track(self, run_id: str) -> list[dict]:
        return Track(self._dir(self.load(run_id)["id"])).read()

    def run_for_record(self, locator: str) -> dict | None:
        runs = [s for s in self.list() if s.get("record") == locator]
        return runs[-1] if runs else None

    def pending_for(self, principal: str) -> list[dict]:
        """Runs waiting for a decision or a resubmission by this principal."""
        return [s for s in self.list() if s.get("pending") and s["status"] == "waiting" and principal in s["pending"]["assignees"]]

    # ------------------------------------------------------------- submission
    def submit(self, process: str, inputs: dict, owner: str, at: str | None = None, record: str | None = None,
               parent_run: str | None = None) -> str:
        release = self.releases.current(at)
        if release is None:
            raise RunError("no policy release is in effect")
        bundle = self.releases.bundle(release["digest"])
        manifest = bundle["ops"].get(process)
        if manifest is None:
            raise RunError(f"release {release['digest'][:12]} defines no process {process!r}")
        wf = bundle["workflows"][process]
        owner_name = authz.principal_name(owner)
        if parent_run:
            parent = self.load(parent_run)
            grant = parent.get("grant")
            if not grant or parent["status"] not in LIVE_STATUSES:
                raise RunError(f"parent run {parent_run} holds no grant or is not live (status {parent['status']}); a child process needs an approved, live parent")
            if owner_name not in (parent["owner"], grant.get("decided_by")):
                raise RunError(f"{owner_name} may not start a child of run {parent_run}: only its owner or the person who approved it may")
            decision = {"allowed": True, "why": f"admitted by the grant on parent run {parent_run} ({parent['process']}), decided by {grant['decided_by']}"}
        else:
            decision = authz.check(bundle, owner, "create", manifest["object"], manifest["folder"])
            if not decision["allowed"]:
                raise RunError(f"{owner} may not start {process}: {decision['why']}")
            first_role = manifest["steps"][0]["role"]
            if first_role not in ("system", *FIELD_ROLES) and first_role not in authz.roles_of(bundle, owner):
                raise RunError(f"{owner} does not hold the role {first_role!r} that starts {process}")
        run_id = uuid.uuid4().hex[:10]
        if record:
            rec = self.kernel.require(record)
            if rec["folder"] != manifest["folder"]:
                rec = self.kernel.move(record, manifest["folder"], by=owner_name, run_id=run_id, release=release["digest"],
                                       reason=f"filed by {process}")
            # The existing item is the submission: its fields seed the steps, under whatever the caller supplied.
            td = self.kernel.types.get(rec["type"], {})
            skip = set(td.get("system_fields") or []) | {"text", "office_comments", "checked_out_by", "checked_out_via", "editors", "wopi_lock", "content_sha"}
            seeded = {k: v for k, v in rec["fields"].items() if k not in skip and v not in (None, "", [], {})}
            inputs = {**inputs, "record": {**seeded, **(inputs.get("record") or {})}}
        else:
            fields = dict(inputs.get("record") or {})
            fields.pop("locator", None)
            fields.pop("attachments", None)  # filed as documents by record-filer, never stored as fields
            for k, v in (self.kernel.types.get(wf["record_type"], {}).get("defaults") or {}).items():
                if fields.get(k) in (None, ""):
                    fields[k] = {"$owner": owner_name, "$today": date.today().isoformat()}.get(v, v)
            missing = [f for f in self.required_at_start(process, at) if fields.get(f) in (None, "", [], {})]
            if missing:
                raise RunError(f"{process} needs {', '.join(missing)} before it can start")
            try:
                rec = self.kernel.create(wf["record_type"], manifest["folder"], fields, by=owner_name, run_id=run_id,
                                         release=release["digest"], state="open")
            except KernelGuardError as e:
                raise RunError(f"cannot create {wf['record_type']}: {e}") from None
        state = {
            "id": run_id, "process": process, "owner": owner_name, "release": release["digest"], "manifest": manifest,
            "workflow": wf, "record": rec["locator"], "parent_run": parent_run, "inputs": inputs, "steps": {},
            "pending": None, "grant": None, "decision_fields": {}, "created_at": utcnow(), "created_ns": time.time_ns(),
            "status": "submitted",
        }
        track = Track(self._dir(run_id))
        track.append({"type": "run.submitted", "run_id": run_id, "release_digest": release["digest"], "process": process,
                      "owner": owner_name, "record": rec["locator"],
                      "owner_type": bundle["members"].get(owner_name, {}).get("type", "system"), "parent_run": parent_run,
                      "manifest_digest": sha256_of(manifest), "inputs_digest": sha256_of(inputs), "admission": decision["why"]})
        self._save(state)
        self.advance(run_id)
        return run_id

    # -------------------------------------------------------------- execution
    def _ctx(self, state: dict, bundle: dict, cog) -> RunContext:
        scoped = ScopedKernel(self.kernel, cog_id=cog.id, reaches=cog.reaches, run_id=state["id"],
                              record=state.get("record"), grant=state.get("grant"))
        dms = DMS(scoped, self.dms.blobs) if self.dms is not None else None
        return RunContext(run_id=state["id"], release=state["release"], bundle=bundle, owner=state["owner"],
                          kernel=scoped, grant=state.get("grant"), runner=self, inbox=self.inbox,
                          record=state.get("record"), dms=dms, frames=self.frames_store, model=self.model,
                          extras={"folder": state["manifest"]["folder"], "object": state["manifest"]["object"],
                                  "process": state["process"], "decision_fields": dict(state.get("decision_fields") or {}),
                                  "cog_profile": (bundle.get("cogs") or {}).get(cog.id)})

    def advance(self, run_id: str) -> dict:
        state = self.load(run_id)
        if state["status"] in ("completed", "failed", "rejected", "waiting", "cancelled"):
            return state
        bundle = self.releases.bundle(state["release"])
        track = Track(self._dir(state["id"]))
        steps = state["manifest"]["steps"]
        for i, step in enumerate(steps):
            sid = step["id"]
            rec = state["steps"].get(sid)
            if rec and rec["status"] in ("passed", "pass-with-problems"):
                continue
            if rec and rec["status"] == "gate-pending":
                return state
            if state.get("record"):
                r = self.kernel.get(state["record"])
                if r and r["hold"]:
                    self._fail(state, track, sid, f"{r['number']} is on hold; lift the hold and retry the run")
                    return state
            cog = self.registry.get(step["cog"]["id"])
            if i == len(steps) - 1 and cog.id == "document-renderer" and state.get("record"):
                # Close the record first so the evidence snapshot shows the final state and every signature.
                if not self._close_record(state, track):
                    return state
            payloads = {k: {"payload": v["envelope"].get("payload")} for k, v in state["steps"].items()}
            step_input = {k: _resolve(v, state["inputs"], payloads) for k, v in step["input"].items()}
            track.append({"type": "step.started", "run_id": run_id, "step": sid, "cog": cog.id, "cog_version": cog.version,
                          "task": step["cog"]["task"], "input_digest": sha256_of(step_input)})
            env = cog.invoke(step["cog"]["task"], step_input, self._ctx(state, bundle, cog))
            verdict = gate_default(env)
            track.append({"type": "step.result", "run_id": run_id, "step": sid, "ok": env["ok"], "problems": env["problems"],
                          "error": env["error"], "timing": env["timing"], "payload_digest": sha256_of(env["payload"]),
                          "gate": verdict})
            state["steps"][sid] = {"status": verdict if verdict != "pass" else "passed", "envelope": env}
            if verdict == "fail":
                detail = (env["error"] or {}).get("detail") or "; ".join(p["detail"] for p in env["problems"] if p["severity"] == "error") \
                    or "step result has error-severity problems"
                self._fail(state, track, sid, detail)
                return state
            if step.get("gate", {}).get("type") == "human":
                outcome = self._human_gate(state, bundle, step, env, track)
                if outcome == "waiting":
                    state["steps"][sid]["status"] = "gate-pending"
                    self._save(state)
                    if (state.get("pending") or {}).get("assignee_type") == "cog":
                        return self.give_to_cog(state)
                    return state
                if outcome == "failed":
                    self._save(state)
                    return state
            self._save(state)
        self._complete(state, track)
        return state

    def _fail(self, state: dict, track: Track, sid: str, reason: str) -> None:
        """A failed run goes back to its owner: the record waits in state ``failed`` assigned to them, with a
        message; the owner may fix the record and retry, or cancel."""
        track.append({"type": "run.failed", "run_id": state["id"], "step": sid, "reason": reason})
        state["pending"] = None
        if state.get("record"):
            try:
                self.kernel.transition(state["record"], "failed", by="hub", run_id=state["id"], release=state["release"],
                                       reason=reason, assignees=[state["owner"]])
            except KernelGuardError:
                pass
        self._notify([state["owner"]], kind="failure", subject=f"{state['process']} {state.get('record') or state['id']} failed at {sid}",
                     body=f"{reason}. Fix the record and retry the run, or cancel it.", run_id=state["id"])
        self._save(state)

    def _close_record(self, state: dict, track: Track) -> bool:
        terminal = state["workflow"]["terminal"]
        rec = self.kernel.get(state["record"])
        if rec and rec["state"] == terminal:
            return True
        try:
            self.kernel.transition(state["record"], terminal, by="hub", run_id=state["id"], release=state["release"],
                                   reason="process completed")
            return True
        except KernelGuardError as e:
            self._fail(state, track, "complete", f"cannot close as {terminal}: {e}")
            return False

    def _complete(self, state: dict, track: Track) -> None:
        if state.get("record") and not self._close_record(state, track):
            return
        track.append({"type": "run.completed", "run_id": state["id"], "record": state.get("record"),
                      "state": state["workflow"]["terminal"],
                      "outputs_digest": sha256_of({k: v["envelope"]["payload"] for k, v in state["steps"].items()})})
        self._save(state)

    # ------------------------------------------------------------ human gates
    def _routing_ctx(self, payload: dict, rec: dict | None = None) -> dict:
        f = (rec or {}).get("fields", {})
        get = lambda k: payload.get(k, f.get(k))
        return {"amount": get("amount"), "currency": get("currency"), "risk": get("risk"), "doc_type": get("doc_type"),
                "change_type": get("change_type"), "conditions": payload.get("conditions", f.get("conditions", {}))}

    def _sod_applies(self, bundle: dict, state: dict) -> bool:
        return any(s["object"] == state["manifest"]["object"] for s in bundle["sod"])

    def _next_state(self, state: dict, step_id: str) -> tuple[str, str]:
        for t in state["workflow"]["transitions"]:
            if t["kind"] == "human" and t["step"] == step_id:
                return t["to"], t["on_reject"]
        return state["workflow"]["terminal"], "rejected"

    def _human_gate(self, state: dict, bundle: dict, step: dict, env: dict, track: Track) -> str:
        """Route the Gate: the first eligible candidate, a cog or people, gets the item; the others are kept as
        fallbacks for when a cog declines. A cog is a user here: it is assigned, notified, and decides through
        ``decide`` like anyone else (see ``cog_decide``)."""
        payload = env["payload"] or {}
        record = self.kernel.get(state["record"]) if state.get("record") else None
        rctx = self._routing_ctx(payload, record)
        gate = step["gate"]
        checkpoint = gate["step"]
        cands = [r for r in candidates(bundle, gate["route"]["process"], checkpoint, rctx) if r.get("mode", "assign") == "assign"]
        if not cands and gate.get("role"):
            role = gate["role"]
            kind = "field" if role in FIELD_ROLES else "role"
            cands = [{"assignee": f"{kind}:{FIELD_ROLES.get(role, role)}", "process": state["process"], "step": checkpoint,
                      "condition": None, "source": {"frame": "(process step)", "line": 0, "text": f"[{role}] {gate['title']}"}}]
        if not cands:
            self._fail(state, track, step["id"], "no routing rule matches this item")
            return "failed"
        return self._pend_next(state, bundle, step, payload, rctx, track, cands)

    def _eligible(self, state: dict, bundle: dict, step: dict, rule: dict, payload: dict, record: dict | None, track: Track) -> list[str]:
        """Who may take this route: a cog with the permission, or the people holding the role or named in the field."""
        assignee = rule["assignee"]
        kind, _, name = assignee.partition(":")
        sod = self._sod_applies(bundle, state)
        owner = authz.principal_name(state["owner"])
        obj, folder = state["manifest"]["object"], state["manifest"]["folder"]
        if kind == "cog":
            allowed = authz.check(bundle, name, "approve", obj, folder)
            if not allowed["allowed"]:
                track.append({"type": "gate.skipped", "run_id": state["id"], "step": step["id"], "candidate": assignee,
                              "reason": allowed["why"], "route_rule": rule["source"]})
                return []
            if sod and name == owner:
                track.append({"type": "gate.skipped", "run_id": state["id"], "step": step["id"], "candidate": assignee,
                              "reason": "segregation of duties: the cog prepared this item", "route_rule": rule["source"]})
                return []
            if name not in self.registry.ids() or bundle["members"].get(name, {}).get("type") != "cog":
                track.append({"type": "gate.skipped", "run_id": state["id"], "step": step["id"], "candidate": assignee,
                              "reason": "no such cog is a member", "route_rule": rule["source"]})
                return []
            why = self.cog_follows(bundle, name, state["process"])
            if not why:
                from .model import cog_availability

                ok, why = cog_availability(bundle, name)
                why = "" if ok else why
            if why:
                track.append({"type": "gate.skipped", "run_id": state["id"], "step": step["id"], "candidate": assignee,
                              "reason": why, "route_rule": rule["source"]})
                return []
            return [name]

        if kind == "field":
            value = payload.get(name) or (record or {}).get("fields", {}).get(name)
            people = [authz.principal_name(value)] if value and bundle["members"].get(authz.principal_name(value)) else []
        else:
            people = [p for p in principals_for(bundle, assignee) if bundle["members"][p]["type"] == "person"]
        people = [p for p in people if authz.check(bundle, p, "approve", obj, folder)["allowed"]]
        if sod:
            people = [p for p in people if p != owner]
        if not people:
            track.append({"type": "gate.skipped", "run_id": state["id"], "step": step["id"], "candidate": assignee,
                          "reason": "no eligible person holds this route", "route_rule": rule["source"]})
        return people

    @staticmethod
    def cog_follows(bundle: dict, cog_name: str, process: str) -> str:
        """Empty when the cog's profile lists the SOP that defines the process (or it has no profile); otherwise why not.
        A person is trained on the SOPs their role needs; a cog's Frame says which it follows."""
        profile = (bundle.get("cogs") or {}).get(cog_name)
        if not profile:
            return ""
        proc = (bundle.get("processes") or {}).get(process) or {}
        frame = (proc.get("source") or {}).get("frame", "")
        follows = profile.get("follows") or []
        if frame and frame not in follows:
            return f"{cog_name} does not follow {frame} (its profile lists: {', '.join(follows) or 'nothing'})"
        return ""
    def _pend_next(self, state: dict, bundle: dict, step: dict, payload: dict, rctx: dict, track: Track, cands: list[dict]) -> str:
        record = self.kernel.get(state["record"]) if state.get("record") else None
        gate = step["gate"]
        checkpoint = gate["step"]
        remaining = list(cands)
        while remaining:
            rule = remaining.pop(0)
            people = self._eligible(state, bundle, step, rule, payload, record, track)
            if not people:
                continue
            is_cog = rule["assignee"].startswith("cog:")
            sla = state["workflow"].get("sla", {}).get(checkpoint)
            due = (date.today() + timedelta(days=int(sla))).isoformat() if sla and not is_cog else None
            state["pending"] = {"kind": "decision", "step": step["id"], "checkpoint": checkpoint, "assignees": people,
                                "assignee_type": "cog" if is_cog else "person", "route_rule": rule["source"],
                                "payload_digest": sha256_of(payload), "title": gate.get("title", "Approve"), "due": due,
                                "remaining": remaining, "routing_ctx": rctx}
            track.append({"type": "gate.pending", "run_id": state["id"], "step": step["id"], "assignees": people,
                          "assignee_type": state["pending"]["assignee_type"], "route_rule": rule["source"],
                          "payload_digest": sha256_of(payload), "routing_ctx": rctx, "due": due})
            if state.get("record"):
                try:
                    self.kernel.transition(state["record"], f"awaiting-{checkpoint}", by="hub", run_id=state["id"],
                                           release=state["release"], assignees=people, due=due,
                                           reason=f"routed by {rule['source']['text']}")
                except KernelGuardError as e:
                    self._fail(state, track, step["id"], str(e))
                    return "failed"
            self._notify(people, kind="approval", subject=f"{gate.get('title', 'Approve')}: {state['process']} {state.get('record') or state['id']}",
                         body=f"Routed by {rule['source']['frame']} line {rule['source']['line']}: {rule['source']['text']}"
                              + (f" Due {due}." if due else ""), run_id=state["id"])
            return "waiting"
        self._fail(state, track, step["id"], "no eligible approver: no person or cog holds any route for this item")
        return "failed"

    # ------------------------------------------------------------ cogs as users
    cog_users_inline = True  # False: an item waiting for a cog is not put in its queue (a sweep gives it later)
    queue = None  # the cogs' work queue (hubproto.workqueue.CogQueue), set by the Hub

    def give_to_cog(self, state: dict) -> dict:
        """An item now waits for a cog: it goes in that cog's queue, like a task in a person's tray. In inline mode the
        cog takes its turn before this returns; otherwise the item waits until the cog gets to it."""
        pending = state.get("pending") or {}
        cog = (pending.get("assignees") or [None])[0]
        if not cog or not self.cog_users_inline:
            return state
        if self.queue is None:
            return self.cog_decide(state["id"], cog)
        self.queue.submit(cog, "gate", by=state.get("owner") or "hub", title=f"{pending.get('title') or 'Approval'}: {state.get('record') or state['process']}",
                          record=state.get("record"), payload={"run": state["id"], "step": pending.get("step")},
                          key=f"gate:{state['id']}:{pending.get('step')}:{cog}")
        return self.load(state["id"])

    def cog_decide(self, run_id: str, cog_name: str) -> dict:
        """A cog takes an item waiting for it: it reviews the payload against its approval criteria and decides
        through the same ``decide`` as a person (signature, seen digest, meaning), or declines and the item goes
        to the next candidate on the route."""
        state = self.load(run_id)
        pending = state.get("pending") or {}
        if state["status"] != "waiting" or cog_name not in pending.get("assignees", []):
            raise RunError(f"run {run_id} is not waiting for {cog_name}")
        bundle = self.releases.bundle(state["release"])
        from .model import cog_availability

        ok, why = cog_availability(bundle, cog_name)
        if not ok:
            return self._decline(run_id, cog_name, [why])
        step = next(s for s in state["manifest"]["steps"] if s["id"] == pending["step"])
        track = Track(self._dir(state["id"]))
        cog = self.registry.get(cog_name)
        env = state["steps"].get(step["id"], {}).get("envelope") or {}
        payload = env.get("payload") or {}
        review = cog.invoke("review", {"payload": payload, "process": state["process"], "routing_ctx": pending.get("routing_ctx") or {}},
                            self._ctx(state, bundle, cog))
        rp = review.get("payload") or {}
        verdict = rp.get("verdict", "escalate")
        reasons = list(rp.get("reasons") or [])
        track.append({"type": "gate.cog_review", "run_id": state["id"], "step": step["id"], "cog": cog_name,
                      "cog_version": cog.version, "verdict": verdict, "reasons": reasons,
                      "route_rule": pending["route_rule"], "delegating_rule": rp.get("delegating_rule")})
        if verdict in ("approve", "reject"):
            meaning = f"{verdict}d within fixed criteria: " + "; ".join(reasons) if reasons else f"{verdict}d within fixed criteria"
            try:
                return self.decide(run_id, cog_name, verdict, meaning=meaning, payload_digest=self.seen_digest(state),
                                   delegating_rule=rp.get("delegating_rule"))
            except RunError as e:
                reasons = [str(e)]
        return self._decline(run_id, cog_name, reasons)

    def _decline(self, run_id: str, cog_name: str, reasons: list[str]) -> dict:
        state = self.load(run_id)
        pending = state["pending"]
        bundle = self.releases.bundle(state["release"])
        step = next(s for s in state["manifest"]["steps"] if s["id"] == pending["step"])
        track = Track(self._dir(state["id"]))
        track.append({"type": "gate.declined", "run_id": state["id"], "step": step["id"], "by": cog_name, "reasons": reasons})
        env = state["steps"].get(step["id"], {}).get("envelope") or {}
        outcome = self._pend_next(state, bundle, step, env.get("payload") or {}, pending.get("routing_ctx") or {}, track,
                                  list(pending.get("remaining") or []))
        if outcome == "waiting":
            state["steps"][step["id"]]["status"] = "gate-pending"
        self._save(state)
        if outcome == "waiting" and (state.get("pending") or {}).get("assignee_type") == "cog" and cog_name not in state["pending"].get("assignees", []):
            return self.give_to_cog(self.load(run_id))
        return self.load(run_id)

    def cog_users_work(self, run_id: str | None = None, cogs: list[str] | None = None) -> list[dict]:
        """Let every cog with an item waiting take its turn (one run, or all). Returns the runs touched."""
        touched = []
        runs = [self.load(run_id)] if run_id else [s for s in self.list() if s["status"] == "waiting"]
        for state in runs:
            pending = state.get("pending") or {}
            if state["status"] != "waiting" or pending.get("kind", "decision") != "decision":
                continue
            for name in list(pending.get("assignees", [])):
                if name in self.registry.ids() and (cogs is None or name in cogs):
                    touched.append(self.cog_decide(state["id"], name))
                    break
        return touched

    def _notify(self, people, **kw) -> None:
        if self.inbox is not None:
            for p in people:
                self.inbox.notify(p, **kw)

    def _missing_for_person(self, state: dict, next_state: str) -> list[str]:
        """Required fields of the next state that the system does not set itself."""
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        if rec is None:
            return []
        system = set(self.kernel.types.get(rec["type"], {}).get("system_fields") or [])
        return [f for f in self.kernel.required_missing(rec, next_state) if f not in system]

    def _record_decision(self, state: dict, step: dict, track: Track, *, by: str, by_type: str, verdict: str, meaning: str,
                         payload_digest: str, delegating_rule=None, meaning_given: bool = True) -> None:
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        decision = {"by": by, "by_type": by_type, "verdict": verdict, "meaning": meaning, "meaning_given": meaning_given,
                    "at": utcnow(), "payload_digest": payload_digest, "record_digest": sha256_of(rec["fields"]) if rec else None,
                    "delegating_rule": delegating_rule, "step": step["gate"]["step"], "run_id": state["id"],
                    "release_digest": state["release"], "record": state.get("record")}
        decision["signature"] = sha256_of({k: decision[k] for k in ("by", "verdict", "meaning", "payload_digest", "record_digest",
                                                                       "at", "run_id", "step", "release_digest")})[:32]
        state["steps"][step["id"]]["decision"] = decision
        state["pending"] = None
        track.append({"type": "gate.decided", "run_id": state["id"], "step": step["id"], **decision})
        nxt, on_reject = self._next_state(state, step["gate"]["step"])
        if verdict == "approve":
            grant = {"id": uuid.uuid4().hex[:8], "run_id": state["id"], "decided_by": by, "by_type": by_type,
                     "payload_digest": payload_digest, "issued_at": utcnow(), "signature": decision["signature"],
                     "steps": [s["id"] for s in state["manifest"]["steps"] if s["id"] != step["id"]]}
            state["grant"] = grant
            track.append({"type": "grant.issued", "run_id": state["id"], "grant": grant})
            state["steps"][step["id"]]["status"] = "passed"
            if state.get("record"):
                self.kernel.append("record.decision", state["record"], decision, run_id=state["id"], release=state["release"], by=by)
            return
        track.append({"type": "run.rejected", "run_id": state["id"], "step": step["id"], "by": by, "to": on_reject})
        state["grant"] = None
        if on_reject == "returned":
            self._return_to_owner(state, step, track, decision, meaning)
            return
        assignees, due = [], None
        if on_reject.startswith("awaiting-"):
            assignees, due = self._prior_pending(state, track, on_reject[len("awaiting-"):])
        if state.get("record"):
            try:
                self.kernel.transition(state["record"], on_reject, by=by, run_id=state["id"], release=state["release"],
                                       decision=decision, reason=meaning, assignees=assignees, due=due)
            except KernelGuardError as e:
                track.append({"type": "run.failed", "run_id": state["id"], "step": step["id"], "reason": str(e)})
        if on_reject.startswith("awaiting-"):
            self._rewind(state, on_reject[len("awaiting-"):], track, assignees, due)
        else:
            self._notify([state["owner"]], kind="rejection", subject=f"{state['process']} {state.get('record')} rejected",
                         body=f"{by} rejected it: {meaning}", run_id=state["id"])

    def _return_to_owner(self, state: dict, step: dict, track: Track, decision: dict, meaning: str) -> None:
        """A rejection that returns the item to its owner for rework: state ``returned``; resubmission reopens the run."""
        reject_to = state["workflow"].get("reject_to")
        if state.get("record"):
            try:
                self.kernel.transition(state["record"], "returned", by=decision["by"], run_id=state["id"], release=state["release"],
                                       decision=decision, reason=meaning, assignees=[state["owner"]])
            except KernelGuardError as e:
                track.append({"type": "run.failed", "run_id": state["id"], "step": step["id"], "reason": str(e)})
                return
        state["pending"] = {"kind": "resubmit", "step": reject_to, "checkpoint": step["gate"]["step"], "assignees": [state["owner"]],
                            "route_rule": {"frame": "(rejection)", "line": 0, "text": f"returned to {reject_to} by {decision['by']}"},
                            "payload_digest": decision["payload_digest"], "title": f"Rework and resubmit ({step['gate']['title']} rejected)",
                            "due": None, "reason": meaning}
        track.append({"type": "gate.returned", "run_id": state["id"], "step": step["id"], "to_step": reject_to,
                      "assignees": [state["owner"]], "reason": meaning})
        self._notify([state["owner"]], kind="returned", subject=f"{state['process']} {state.get('record')} returned for rework",
                     body=f"{decision['by']} rejected '{step['gate']['title']}': {meaning}. Fix the record and resubmit.", run_id=state["id"])

    def _prior_pending(self, state: dict, track: Track, checkpoint: str) -> tuple[list[str], str | None]:
        step = next((s for s in state["manifest"]["steps"] if s.get("gate", {}).get("step") == checkpoint), None)
        if step is None:
            return [], None
        for ev in reversed(track.read()):
            if ev["type"] == "gate.pending" and ev.get("step") == step["id"]:
                return list(ev.get("assignees") or []), ev.get("due")
        return [], None

    def _rewind(self, state: dict, checkpoint: str, track: Track, people: list[str], due: str | None) -> None:
        target_step = next((s for s in state["manifest"]["steps"] if s.get("gate", {}).get("step") == checkpoint), None)
        if target_step is None:
            return
        ids = [s["id"] for s in state["manifest"]["steps"]]
        for sid in ids[ids.index(target_step["id"]) + 1:]:
            state["steps"].pop(sid, None)
        env = state["steps"][target_step["id"]]["envelope"]
        state["steps"][target_step["id"]]["status"] = "gate-pending"
        state["steps"][target_step["id"]].pop("decision", None)
        state["pending"] = {"kind": "decision", "step": target_step["id"], "checkpoint": checkpoint, "assignees": people,
                            "route_rule": {"frame": "(rejection)", "line": 0, "text": "returned by a rejection"},
                            "payload_digest": sha256_of(env["payload"] or {}), "title": target_step["gate"]["title"], "due": due}
        track.append({"type": "gate.pending", "run_id": state["id"], "step": target_step["id"], "assignees": people,
                      "route_rule": state["pending"]["route_rule"], "payload_digest": state["pending"]["payload_digest"],
                      "routing_ctx": {}, "due": due})
        self._notify(people, kind="approval", subject=f"{target_step['gate']['title']} (returned): {state['process']} {state.get('record')}",
                     body="An item you decided earlier was rejected at a later step and has come back to you.", run_id=state["id"])

    # ---------------------------------------------------------- human actions
    def _load_waiting(self, run_id: str, by: str, kind: str) -> tuple[dict, dict]:
        state = self.load(run_id)
        by = authz.principal_name(by)
        pending = state.get("pending")
        if state["status"] != "waiting" or not pending or pending.get("kind", "decision") != kind:
            raise RunError(f"run {run_id} is not waiting for a {kind} (status {state['status']})")
        if by not in pending["assignees"]:
            raise RunError(f"{by} is not an assignee of this decision (assigned: {', '.join(pending['assignees'])})")
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        if rec and rec["hold"]:
            raise RunError(f"{rec['number']} is on hold ({rec.get('hold_reason') or 'no reason given'}); lift the hold first")
        return state, pending

    def allowed_decision_fields(self, state: dict) -> list[str]:
        """Fields a person may supply with a decision: what the next state requires plus the type's decision fields."""
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        if rec is None or not state.get("pending"):
            return []
        td = self.kernel.types.get(rec["type"], {})
        nxt, _ = self._next_state(state, state["pending"]["checkpoint"])
        missing = self._missing_for_person(state, nxt)  # a signer supplies what is still missing, never rewrites what is there
        machines = set(self.registry.ids()) | {"hub", "system"}
        setters = rec.get("setters") or {}
        declared = {k for k, v in rec["fields"].items()  # values a person put there (at Start or at an earlier Gate)
                    if v not in (None, "", [], {}) and setters.get(k, rec.get("created_by")) not in machines}
        return sorted((set(missing) | set(td.get("decision_fields") or [])) - set(td.get("system_fields") or []) - declared)

    def required_at_start(self, process: str, at: str | None = None) -> list[str]:
        """Fields the submitter must supply: what the first Gate's state requires, minus system fields and defaults."""
        release = self.releases.current(at)
        if release is None:
            return []
        bundle = self.releases.bundle(release["digest"])
        wf = (bundle.get("workflows") or {}).get(process)
        if not wf or not wf.get("checkpoints"):
            return []
        td = self.kernel.types.get(wf["record_type"], {})
        first = wf["checkpoints"][0]["state"]
        skip = set(td.get("system_fields") or []) | set((td.get("defaults") or {}).keys())
        steps = (bundle["ops"].get(process) or {}).get("steps", [])
        for step in steps:  # cogs that run before the first Gate may fill fields the submitter leaves blank
            try:
                skip |= set(self.registry.get(step["cog"]["id"]).provides)
            except Exception:
                pass
            if step.get("gate", {}).get("type") == "human":
                break
        return [f for f in td.get("required_by_state", {}).get(first, []) if f not in skip]

    def decide(self, run_id: str, by: str, verdict: str, meaning: str = "", payload_digest: str | None = None,
               fields: dict | None = None, delegating_rule=None) -> dict:
        state, pending = self._load_waiting(run_id, by, "decision")
        by = authz.principal_name(by)
        bundle0 = self.releases.bundle(state["release"])
        by_type = bundle0["members"].get(by, {}).get("type", "person")
        if by_type == "cog" and fields:
            raise RunError("a cog decides within fixed criteria and supplies no fields")
        if verdict not in ("approve", "reject"):
            raise RunError("verdict must be approve or reject")
        bundle = self.releases.bundle(state["release"])
        allowed = authz.check(bundle, by, "approve", state["manifest"]["object"], state["manifest"]["folder"])
        if not allowed["allowed"]:
            raise RunError(allowed["why"])
        if self._sod_applies(bundle, state) and by == authz.principal_name(state["owner"]):
            raise RunError("segregation of duties: you prepared this item and cannot approve it")
        if not payload_digest:
            raise RunError("a decision must quote what you saw: pass the run's seen_digest (shown on the card and by inbox-pending)")
        if payload_digest != self.seen_digest(state):
            raise RunError("the item changed since you opened it (its fields, links or documents differ); open it again and decide on what it is now")
        if not meaning.strip():
            raise RunError("a signature needs its meaning: say in a few words what you are confirming "
                           "(for example 'invoice matches the order and goods were received')")
        step = next(s for s in state["manifest"]["steps"] if s["id"] == pending["step"])
        track = Track(self._dir(state["id"]))
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        if verdict == "reject":
            fields = None  # a rejection changes nothing on the record; its reason is the meaning
        if fields and rec is not None:
            permitted = set(self.allowed_decision_fields(state))
            refused = sorted(k for k in fields if k not in permitted)
            if refused:
                raise RunError(f"a decision may not set {', '.join(refused)}; this Gate accepts {', '.join(sorted(permitted)) or 'no fields'}")
            try:
                self.kernel.set_fields(state["record"], fields, by=by, run_id=state["id"], release=state["release"])
            except KernelGuardError as e:
                raise RunError(str(e)) from None
            state["decision_fields"] = {**(state.get("decision_fields") or {}), **fields}
            track.append({"type": "record.fields_set", "run_id": state["id"], "step": step["id"], "by": by, "fields": sorted(fields)})
            rec = self.kernel.get(state["record"])
        if verdict == "approve" and rec is not None:
            nxt, _ = self._next_state(state, pending["checkpoint"])
            missing = self._missing_for_person(state, nxt)
            if missing:
                raise RunError(f"{nxt} needs {', '.join(missing)} before approval; supply them with the decision")
            hook = PRE_DECISION_HOOKS.get(rec["type"])
            if hook:
                try:
                    hook(self.kernel, rec, verdict)
                except KernelGuardError as e:
                    raise RunError(str(e)) from None
        if by_type == "cog":
            rule = pending.get("route_rule") or {}
            self._notify([state["owner"]], kind="auto-decided",
                         subject=f"{pending.get('title', 'Approval')} on {state.get('record') or state['process']}: {verdict}d automatically by {by}",
                         body=f"The {by} cog {verdict}d this within the fixed criteria in {rule.get('frame')} line {rule.get('line')}. "
                              f"{meaning.strip()}. No person reviewed it; the criteria and the reasons are on the run page.", run_id=state["id"])
        self._record_decision(state, step, track, by=by, by_type=by_type, verdict=verdict,
                              meaning=meaning.strip(), payload_digest=pending["payload_digest"], meaning_given=True,
                              delegating_rule=delegating_rule)
        self._save(state)
        result = self.advance(run_id) if verdict == "approve" else self.load(run_id)
        hook = POST_DECISION_HOOKS.get(rec["type"]) if rec is not None else None
        if hook:
            hook(self.kernel, self.kernel.get(rec["locator"]), verdict)
        return result

    def resubmit(self, run_id: str, by: str, fields: dict | None = None, attachments: list[dict] | None = None,
                 note: str = "") -> dict:
        """The owner reworks a returned item: fields and attachments are applied and the run restarts from the return step."""
        state, pending = self._load_waiting(run_id, by, "resubmit")
        by = authz.principal_name(by)
        track = Track(self._dir(state["id"]))
        if fields:
            try:
                self.kernel.set_fields(state["record"], fields, by=by, run_id=state["id"], release=state["release"])
            except KernelGuardError as e:
                raise RunError(str(e)) from None
        if attachments and self.dms is not None:
            import base64

            for att in attachments:
                raw = att.get("content")
                content = base64.b64decode(att["content_b64"]) if att.get("content_b64") else (
                    raw.encode("utf-8") if isinstance(raw, str) else json.dumps(raw).encode("utf-8") if raw is not None else b"")
                self.dms.create(title=att.get("title") or att.get("name", "attachment"), doc_class=att.get("doc_class", "controlled"),
                                folder=state["manifest"]["folder"], by=by, content=content, filename=att.get("name", ""),
                                doc_type=att.get("doc_type", ""), run_id=state["id"], release=state["release"], attach_to=state["record"])
        ids = [s["id"] for s in state["manifest"]["steps"]]
        start = ids.index(pending["step"]) if pending["step"] in ids else 0
        for sid in ids[start:]:
            state["steps"].pop(sid, None)
        # The rerun reads the record as it now is: the original submission's one-shot inputs are spent.
        record_input = dict(state["inputs"].get("record") or {})
        record_input.update(fields or {})
        for spent in ("user_needs", "attachments"):
            record_input.pop(spent, None)
        state["inputs"] = {**state["inputs"], "record": record_input}
        state["pending"] = None
        state["grant"] = None
        track.append({"type": "run.resubmitted", "run_id": state["id"], "by": by, "from_step": pending["step"], "note": note,
                      "fields": sorted(fields or {}), "attachments": [a.get("name") for a in attachments or []]})
        self.kernel.transition(state["record"], "open", by=by, run_id=state["id"], release=state["release"], reason=f"resubmitted: {note}")
        self._save(state)
        return self.advance(run_id)

    def retry(self, run_id: str, by: str, note: str = "", fields: dict | None = None,
              attachments: list[dict] | None = None) -> dict:
        """Retry a failed run from the failed step, fixing the record's fields and attachments on the way."""
        state = self.load(run_id)
        by = authz.principal_name(by)
        if state["status"] != "failed":
            raise RunError(f"run {run_id} is not failed (status {state['status']})")
        bundle = self.releases.bundle(state["release"])
        if by != state["owner"] and not authz.check(bundle, by, "approve", state["manifest"]["object"], state["manifest"]["folder"])["allowed"]:
            raise RunError(f"{by} may not retry this run: only its owner or an approver of {state['manifest']['object']} may")
        track = Track(self._dir(state["id"]))
        if fields and state.get("record"):
            try:
                self.kernel.set_fields(state["record"], fields, by=by, run_id=state["id"], release=state["release"])
            except KernelGuardError as e:
                raise RunError(str(e)) from None
            track.append({"type": "record.fields_set", "run_id": state["id"], "by": by, "fields": sorted(fields)})
            inputs = state.setdefault("inputs", {})
            inputs["record"] = {**(inputs.get("record") or {}), **fields}
        if attachments:
            inputs = state.setdefault("inputs", {})
            inputs["record"] = {**(inputs.get("record") or {}), "attachments": attachments}
        failed = next((e for e in reversed(track.read()) if e["type"] == "run.failed"), None)
        sid = failed.get("step") if failed else None
        ids = [s["id"] for s in state["manifest"]["steps"]]
        start = ids.index(sid) if sid in ids else 0
        for s in ids[start:]:
            state["steps"].pop(s, None)
        state["pending"] = None
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        if rec and rec["state"] == "failed":
            self.kernel.transition(state["record"], "open", by=by, run_id=state["id"], release=state["release"], reason=f"retried: {note}")
        track.append({"type": "run.retried", "run_id": state["id"], "by": by, "from_step": sid, "note": note})
        self._save(state)
        return self.advance(run_id)

    def cancel(self, run_id: str, by: str, reason: str = "") -> dict:
        state = self.load(run_id)
        by = authz.principal_name(by)
        if state["status"] in ("completed", "cancelled"):
            raise RunError(f"run {run_id} is {state['status']} and cannot be cancelled")
        bundle = self.releases.bundle(state["release"])
        if by != state["owner"] and not authz.check(bundle, by, "approve", state["manifest"]["object"], state["manifest"]["folder"])["allowed"]:
            raise RunError(f"{by} may not cancel this run")
        track = Track(self._dir(state["id"]))
        track.append({"type": "run.cancelled", "run_id": state["id"], "by": by, "reason": reason})
        state["pending"] = None
        state["grant"] = None
        if state.get("record"):
            try:
                self.kernel.transition(state["record"], "cancelled", by=by, run_id=state["id"], release=state["release"], reason=reason)
            except KernelGuardError:
                pass
        self._save(state)
        return state

    # ------------------------------------------------------------- back to the start
    START_STATES = {"controlled": "draft", "template": "draft"}
    NOT_RESTARTABLE = {"effective", "obsolete", "superseded"}

    def may_restart(self, rec: dict, by: str) -> tuple[bool, str]:
        """Who may send an item back to the start: its owner, anyone who may approve it, or (documents) a document controller."""
        meta = self.releases.current()
        bundle = self.releases.bundle(meta["digest"]) if meta else {}
        by = authz.principal_name(by)
        f = rec["fields"]
        if rec["state"] in self.NOT_RESTARTABLE:
            return False, f"{rec['number']} is {rec['state']}; it changes through a new revision, not by starting over"
        owner = f.get("owner") or f.get("author") or f.get("filed_by") or rec.get("created_by")
        if by == owner:
            return True, ""
        if rec["type"] == "document" and "document-controller" in authz.roles_of(bundle, by):
            return True, ""
        obj = rec["type"]
        run = self.run_for_record(rec["locator"])
        if run:
            obj = run["manifest"]["object"]
        if bundle and authz.check(bundle, by, "approve", obj, rec["folder"])["allowed"]:
            return True, ""
        return False, f"only {owner or 'its owner'}, an approver of {obj}" + (" or a document controller" if rec["type"] == "document" else "") + " may"

    def restart(self, record: str, by: str, reason: str) -> dict:
        """Send an item back to the start of its workflow: the run it is in is withdrawn, the item returns to its first
        state with nobody assigned, and its owner and whoever it waited for are told. Versions and history stay."""
        rec = self.kernel.require(record)
        by = authz.principal_name(by)
        if not (reason or "").strip():
            raise RunError("say why the item goes back to the start")
        ok, why = self.may_restart(rec, by)
        if not ok:
            raise RunError(why)
        run = self.run_for_record(rec["locator"])
        waited = list((run or {}).get("pending", {}) and run["pending"].get("assignees") or [])
        if run and run["status"] not in ("completed", "cancelled", "rejected"):
            self.cancel(run["id"], by, reason=f"back to the start: {reason}")
        start = self.START_STATES.get(rec["fields"].get("doc_class") or "", "open")
        rec = self.kernel.get(rec["locator"])
        if rec["state"] != start:
            self.kernel.transition(rec["locator"], start, by=by, reason=f"back to the start: {reason}", assignees=[])
        if rec["fields"].get("checked_out_by"):
            self.kernel.set_fields(rec["locator"], {"checked_out_by": None, "checked_out_via": None, "editors": []}, by=by)
        f = rec["fields"]
        owner = f.get("owner") or f.get("author") or f.get("filed_by") or rec.get("created_by")
        people = [p for p in dict.fromkeys([owner, *waited]) if p and p != by]
        self._notify(people, kind="returned", subject=f"{rec['number']} is back at the start",
                     body=f"{by} sent {rec['number']} back to the start of its workflow: {reason}. It is {start} again; nothing waits on anyone.",
                     run_id=(run or {}).get("id", ""))
        return self.kernel.get(rec["locator"])

    # ------------------------------------------------------------- escalation
    def tick(self, today: str | None = None) -> list[dict]:
        """Escalate overdue items. A route written 'go to' adds assignees; one written 'are reported to' only informs."""
        today = today or date.today().isoformat()
        escalated = []
        for state in self.list():
            pending = state.get("pending")
            if not pending or state["status"] != "waiting" or not pending.get("due") or pending["due"] >= today or pending.get("escalated"):
                continue
            bundle = self.releases.bundle(state["release"])
            rules = [r for r in candidates(bundle, state["process"], "*", {"overdue": True}) if r["step"] == "*"]
            track = Track(self._dir(state["id"]))
            added: list[str] = []
            informed: list[str] = []
            for rule in rules:
                people = [p for p in principals_for(bundle, rule["assignee"]) if bundle["members"][p]["type"] == "person"]
                if rule.get("mode") == "notify":
                    informed += people
                else:
                    added += [p for p in people if p not in pending["assignees"]]
                    informed += [p for p in people if p in pending["assignees"]]
            pending["escalated"] = today
            if added:
                pending["assignees"] = pending["assignees"] + added
                if state.get("record"):
                    self.kernel.assign(state["record"], pending["assignees"], by="hub", run_id=state["id"], release=state["release"])
            track.append({"type": "gate.escalated" if added else "gate.overdue", "run_id": state["id"], "step": pending["step"],
                          "assignees": added, "informed": sorted(set(informed + pending["assignees"])), "due": pending["due"],
                          "route_rule": rules[0]["source"] if rules else None})
            self._notify(sorted(set(added + informed + pending["assignees"])), kind="escalation",
                         subject=f"Overdue: {pending['title']} on {state.get('record')}",
                         body=f"Due {pending['due']}" + (f"; escalated by {rules[0]['source']['text']}" if rules else "; no escalation route"),
                         run_id=state["id"])
            self._save(state)
            escalated.append({"run": state["id"], "record": state.get("record"), "to": added, "informed": informed})
        return escalated
