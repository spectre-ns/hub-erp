"""The Op runner and the workflow engine, one mechanism seen from two sides.

A run executes one transition chain of a record: the automatic steps after
a human transition, up to the next human one. A record's state is the
projection of its transitions, so every state change is on a Track, pinned
to a policy release, and nothing moves a record outside a run.

A human Gate routes the item through the release routing table: a cog
candidate may decide only within its approval criteria; otherwise the item
waits in people's inboxes with a due date from the SLA. An approval issues a
grant; only then can reaching Cogs write.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

from . import authz
from .cogs import CogRegistry, RunContext
from .envelope import gate_default
from .kernel import Kernel, KernelGuardError
from .release import ReleaseStore, utcnow
from .routing import candidates, principals_for
from .rules import sha256_of
from .track import Track


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

    # ------------------------------------------------------------ persistence
    def _dir(self, run_id: str) -> Path:
        return self.root / run_id

    def load(self, run_id: str) -> dict:
        matches = [p for p in self.root.iterdir() if p.is_dir() and p.name.startswith(run_id)]
        if len(matches) != 1:
            raise RunError(f"run {run_id!r}: {len(matches)} matches")
        return json.loads((matches[0] / "state.json").read_text())

    def _save(self, state: dict) -> None:
        state["status"] = Track(self._dir(state["id"])).status()
        (self._dir(state["id"]) / "state.json").write_text(json.dumps(state, indent=1, sort_keys=True))

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.root.iterdir()):
            if (p / "state.json").exists():
                out.append(json.loads((p / "state.json").read_text()))
        return sorted(out, key=lambda s: (s["created_at"], s.get("created_ns", 0)))

    def track(self, run_id: str) -> list[dict]:
        return Track(self._dir(self.load(run_id)["id"])).read()

    def run_for_record(self, locator: str) -> dict | None:
        runs = [s for s in self.list() if s.get("record") == locator]
        return runs[-1] if runs else None

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
        if parent_run:
            parent = self.load(parent_run)
            if not parent.get("grant"):
                raise RunError(f"parent run {parent_run} holds no grant; a child process needs an approved parent")
            decision = {"allowed": True, "why": f"admitted by the grant on parent run {parent_run} ({parent['process']}), decided by {parent['grant']['decided_by']}"}
        else:
            decision = authz.check(bundle, owner, "create", manifest["object"], manifest["folder"])
            if not decision["allowed"]:
                raise RunError(f"{owner} may not start {process}: {decision['why']}")
            first_role = manifest["steps"][0]["role"]
            if first_role != "system" and first_role not in authz.roles_of(bundle, owner):
                raise RunError(f"{owner} does not hold the role {first_role!r} that starts {process}")
        run_id = uuid.uuid4().hex[:10]
        owner_name = authz.principal_name(owner)
        if record:
            rec = self.kernel.require(record)
        else:
            fields = dict(inputs.get("record") or {})
            fields.pop("locator", None)
            fields.pop("attachments", None)  # filed as documents by record-filer, never stored as fields
            for k, v in (self.kernel.types.get(wf["record_type"], {}).get("defaults") or {}).items():
                if fields.get(k) in (None, ""):
                    fields[k] = {"$owner": owner_name, "$today": date.today().isoformat()}.get(v, v)
            try:
                rec = self.kernel.create(wf["record_type"], manifest["folder"], fields, by=owner_name, run_id=run_id,
                                         release=release["digest"], state="open")
            except KernelGuardError as e:
                raise RunError(f"cannot create {wf['record_type']}: {e}") from None
        state = {
            "id": run_id,
            "process": process,
            "owner": owner_name,
            "release": release["digest"],
            "manifest": manifest,
            "workflow": wf,
            "record": rec["locator"],
            "parent_run": parent_run,
            "inputs": inputs,
            "steps": {},
            "pending": None,
            "grant": None,
            "created_at": utcnow(),
            "created_ns": time.time_ns(),
            "status": "submitted",
        }
        track = Track(self._dir(run_id))
        track.append({"type": "run.submitted", "run_id": run_id, "release_digest": release["digest"],
                      "process": process, "owner": owner_name, "record": rec["locator"],
                      "owner_type": bundle["members"].get(owner_name, {}).get("type", "system"), "parent_run": parent_run,
                      "manifest_digest": sha256_of(manifest), "inputs_digest": sha256_of(inputs),
                      "admission": decision["why"]})
        self._save(state)
        self.advance(run_id)
        return run_id

    # -------------------------------------------------------------- execution
    def _ctx(self, state: dict, bundle: dict) -> RunContext:
        return RunContext(run_id=state["id"], release=state["release"], bundle=bundle, owner=state["owner"],
                          kernel=self.kernel, grant=state.get("grant"), runner=self, inbox=self.inbox,
                          record=state.get("record"), dms=self.dms,
                          extras={"folder": state["manifest"]["folder"], "object": state["manifest"]["object"],
                                  "process": state["process"]})

    def advance(self, run_id: str) -> dict:
        state = self.load(run_id)
        if state["status"] in ("completed", "failed", "rejected", "waiting"):
            return state
        bundle = self.releases.bundle(state["release"])
        track = Track(self._dir(state["id"]))
        for step in state["manifest"]["steps"]:
            sid = step["id"]
            rec = state["steps"].get(sid)
            if rec and rec["status"] in ("passed", "pass-with-problems"):
                continue
            if rec and rec["status"] == "gate-pending":
                return state
            cog = self.registry.get(step["cog"]["id"])
            payloads = {k: {"payload": v["envelope"].get("payload")} for k, v in state["steps"].items()}
            step_input = {k: _resolve(v, state["inputs"], payloads) for k, v in step["input"].items()}
            track.append({"type": "step.started", "run_id": run_id, "step": sid, "cog": cog.id,
                          "cog_version": cog.version, "task": step["cog"]["task"],
                          "input_digest": sha256_of(step_input)})
            env = cog.invoke(step["cog"]["task"], step_input, self._ctx(state, bundle))
            verdict = gate_default(env)
            track.append({"type": "step.result", "run_id": run_id, "step": sid, "ok": env["ok"],
                          "problems": env["problems"], "error": env["error"], "timing": env["timing"],
                          "payload_digest": sha256_of(env["payload"]), "gate": verdict})
            state["steps"][sid] = {"status": verdict if verdict != "pass" else "passed", "envelope": env}
            if verdict == "fail":
                self._fail(state, track, sid, (env["error"] or {}).get("detail") or "step result has error-severity problems")
                return state
            if step.get("gate", {}).get("type") == "human":
                outcome = self._human_gate(state, bundle, step, env, track)
                if outcome == "waiting":
                    state["steps"][sid]["status"] = "gate-pending"
                    self._save(state)
                    return state
                if outcome == "failed":
                    self._save(state)
                    return state
            self._save(state)
        self._complete(state, bundle, track)
        return state

    def _fail(self, state: dict, track: Track, sid: str, reason: str) -> None:
        track.append({"type": "run.failed", "run_id": state["id"], "step": sid, "reason": reason})
        if state.get("record"):
            try:
                self.kernel.transition(state["record"], "failed", by="hub", run_id=state["id"], release=state["release"],
                                       reason=reason)
            except KernelGuardError:
                pass
        self._save(state)

    def _complete(self, state: dict, bundle: dict, track: Track) -> None:
        terminal = state["workflow"]["terminal"]
        try:
            self.kernel.transition(state["record"], terminal, by="hub", run_id=state["id"], release=state["release"],
                                   reason="process completed")
        except KernelGuardError as e:
            self._fail(state, track, "complete", f"cannot close as {terminal}: {e}")
            return
        track.append({"type": "run.completed", "run_id": state["id"], "record": state["record"], "state": terminal,
                      "outputs_digest": sha256_of({k: v["envelope"]["payload"] for k, v in state["steps"].items()})})
        self._save(state)

    # ------------------------------------------------------------ human gates
    def _routing_ctx(self, payload: dict, rec: dict | None = None) -> dict:
        f = (rec or {}).get("fields", {})
        get = lambda k: payload.get(k, f.get(k))
        return {"amount": get("amount"), "currency": get("currency"), "risk": get("risk"),
                "doc_type": get("doc_type"), "change_type": get("change_type"),
                "conditions": payload.get("conditions", f.get("conditions", {}))}

    def _sod_applies(self, bundle: dict, state: dict) -> bool:
        return any(s["object"] == state["manifest"]["object"] for s in bundle["sod"])

    def _next_state(self, state: dict, step_id: str) -> tuple[str, str]:
        for t in state["workflow"]["transitions"]:
            if t["kind"] == "human" and t["step"] == step_id:
                return t["to"], t["on_reject"]
        return state["workflow"]["terminal"], "rejected"

    def _human_gate(self, state: dict, bundle: dict, step: dict, env: dict, track: Track) -> str:
        payload = env["payload"] or {}
        record = self.kernel.get(state["record"]) if state.get("record") else None
        rctx = self._routing_ctx(payload, record)
        gate = step["gate"]
        checkpoint = gate["step"]
        cands = candidates(bundle, gate["route"]["process"], checkpoint, rctx)
        if not cands and gate.get("role"):
            # No routing sentence for this checkpoint: route to the role (or field) written on the step.
            kind = "field" if gate["role"] in ("assignee", "owner", "author", "requester", "auditor", "chair", "evaluator", "reporter") else "role"
            field_map = {"requester": "requested_by", "reporter": "reported_by"}
            cands = [{"assignee": f"{kind}:{field_map.get(gate['role'], gate['role'])}", "process": state["process"], "step": checkpoint, "condition": None,
                      "source": {"frame": "(process step)", "line": 0, "text": f"[{gate['role']}] {gate['title']}"}}]
        sod = self._sod_applies(bundle, state)
        owner = authz.principal_name(state["owner"])
        obj, folder = state["manifest"]["object"], state["manifest"]["folder"]
        if not cands:
            self._fail(state, track, step["id"], "no routing rule matches this item")
            return "failed"
        for rule in cands:
            assignee = rule["assignee"]
            kind, _, name = assignee.partition(":")
            if kind == "cog":
                allowed = authz.check(bundle, name, "approve", obj, folder)
                if not allowed["allowed"]:
                    track.append({"type": "gate.skipped", "run_id": state["id"], "step": step["id"], "candidate": assignee,
                                  "reason": allowed["why"], "route_rule": rule["source"]})
                    continue
                if sod and name == owner:
                    track.append({"type": "gate.skipped", "run_id": state["id"], "step": step["id"], "candidate": assignee,
                                  "reason": "segregation of duties: the cog prepared this item", "route_rule": rule["source"]})
                    continue
                cog = self.registry.get(name)
                review = cog.invoke("review", {"payload": payload, "process": state["process"], "routing_ctx": rctx},
                                    self._ctx(state, bundle))
                rp = review.get("payload") or {}
                verdict = rp.get("verdict", "escalate")
                track.append({"type": "gate.cog_review", "run_id": state["id"], "step": step["id"], "cog": name,
                              "cog_version": cog.version, "verdict": verdict, "reasons": rp.get("reasons"),
                              "route_rule": rule["source"], "delegating_rule": rp.get("delegating_rule")})
                if verdict == "approve":
                    self._record_decision(state, step, track, by=name, by_type="cog", verdict="approve",
                                          meaning="approved within fixed criteria", payload_digest=sha256_of(payload),
                                          delegating_rule=rp.get("delegating_rule"))
                    return "passed"
                if verdict == "reject":
                    self._record_decision(state, step, track, by=name, by_type="cog", verdict="reject",
                                          meaning="rejected within fixed criteria", payload_digest=sha256_of(payload),
                                          delegating_rule=rp.get("delegating_rule"))
                    return "failed"
                continue
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
                continue
            sla = state["workflow"].get("sla", {}).get(checkpoint)
            due = (date.today() + timedelta(days=int(sla))).isoformat() if sla else None
            state["pending"] = {"step": step["id"], "checkpoint": checkpoint, "assignees": people, "route_rule": rule["source"],
                                "payload_digest": sha256_of(payload), "title": gate.get("title", "Approve"), "due": due}
            track.append({"type": "gate.pending", "run_id": state["id"], "step": step["id"], "assignees": people,
                          "route_rule": rule["source"], "payload_digest": sha256_of(payload), "routing_ctx": rctx, "due": due})
            if state.get("record"):
                waiting = f"awaiting-{checkpoint}"
                try:
                    self.kernel.transition(state["record"], waiting, by="hub", run_id=state["id"], release=state["release"],
                                           assignees=people, due=due, reason=f"routed by {rule['source']['text']}")
                except KernelGuardError as e:
                    self._fail(state, track, step["id"], str(e))
                    return "failed"
            if self.inbox is not None:
                for p in people:
                    self.inbox.notify(p, kind="approval",
                                      subject=f"{gate.get('title', 'Approve')}: {state['process']} {state.get('record') or state['id']}",
                                      body=f"Routed by {rule['source']['frame']} line {rule['source']['line']}: {rule['source']['text']}"
                                           + (f" Due {due}." if due else ""),
                                      run_id=state["id"])
            return "waiting"
        self._fail(state, track, step["id"], "no eligible approver: every route was skipped or declined")
        return "failed"

    def _record_decision(self, state: dict, step: dict, track: Track, *, by: str, by_type: str, verdict: str,
                         meaning: str, payload_digest: str, delegating_rule=None) -> None:
        decision = {"by": by, "by_type": by_type, "verdict": verdict, "meaning": meaning, "at": utcnow(),
                    "payload_digest": payload_digest, "delegating_rule": delegating_rule, "step": step["gate"]["step"],
                    "run_id": state["id"]}
        decision["signature"] = sha256_of({k: decision[k] for k in ("by", "verdict", "payload_digest", "at")})[:24]
        state["steps"][step["id"]]["decision"] = decision
        state["pending"] = None
        track.append({"type": "gate.decided", "run_id": state["id"], "step": step["id"], **decision})
        nxt, on_reject = self._next_state(state, step["gate"]["step"])
        if verdict == "approve":
            grant = {"id": uuid.uuid4().hex[:8], "run_id": state["id"], "decided_by": by, "by_type": by_type,
                     "payload_digest": payload_digest, "issued_at": utcnow(),
                     "steps": [s["id"] for s in state["manifest"]["steps"] if s["id"] != step["id"]]}
            state["grant"] = grant
            track.append({"type": "grant.issued", "run_id": state["id"], "grant": grant})
            state["steps"][step["id"]]["status"] = "passed"
            if state.get("record") and nxt != state["workflow"]["terminal"]:
                pass  # the next waiting state is set when the next Gate is reached; the terminal when the run completes
            elif state.get("record") and nxt == state["workflow"]["terminal"] and self._remaining_after(state, step["id"]) == 0:
                pass  # _complete sets the terminal state
            if state.get("record"):
                self.kernel.append("record.decision", state["record"], decision, run_id=state["id"],
                                   release=state["release"], by=by)
        else:
            track.append({"type": "run.rejected", "run_id": state["id"], "step": step["id"], "by": by, "to": on_reject})
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

    def _prior_pending(self, state: dict, track: Track, checkpoint: str) -> tuple[list[str], str | None]:
        """The assignees and due date the target Gate had when it was last pending, from the Track."""
        step = next((s for s in state["manifest"]["steps"] if s.get("gate", {}).get("step") == checkpoint), None)
        if step is None:
            return [], None
        for ev in reversed(track.read()):
            if ev["type"] == "gate.pending" and ev.get("step") == step["id"]:
                return list(ev.get("assignees") or []), ev.get("due")
        return [], None

    def _immediate_next(self, state: dict, step_id: str) -> bool:
        """True when no automatic step runs between this Gate and the next state."""
        ids = [s["id"] for s in state["manifest"]["steps"]]
        return ids.index(step_id) == len(ids) - 1

    def _remaining_after(self, state: dict, step_id: str) -> int:
        ids = [s["id"] for s in state["manifest"]["steps"]]
        return len(ids) - ids.index(step_id) - 1

    def _rewind(self, state: dict, checkpoint: str, track: Track, people: list[str], due: str | None) -> None:
        """A rejection that returns to an earlier checkpoint: reopen that Gate with its original payload and assignees."""
        target_step = next((s for s in state["manifest"]["steps"] if s.get("gate", {}).get("step") == checkpoint), None)
        if target_step is None:
            return
        ids = [s["id"] for s in state["manifest"]["steps"]]
        for sid in ids[ids.index(target_step["id"]) + 1:]:
            state["steps"].pop(sid, None)
        env = state["steps"][target_step["id"]]["envelope"]
        state["steps"][target_step["id"]]["status"] = "gate-pending"
        state["steps"][target_step["id"]].pop("decision", None)
        state["pending"] = {"step": target_step["id"], "checkpoint": checkpoint, "assignees": people,
                            "route_rule": {"frame": "(rejection)", "line": 0, "text": "returned by a rejection"},
                            "payload_digest": sha256_of(env["payload"] or {}), "title": target_step["gate"]["title"], "due": due}
        track.append({"type": "gate.pending", "run_id": state["id"], "step": target_step["id"], "assignees": people,
                      "route_rule": state["pending"]["route_rule"], "payload_digest": state["pending"]["payload_digest"],
                      "routing_ctx": {}, "due": due})
        if self.inbox is not None:
            for p in people:
                self.inbox.notify(p, kind="approval", subject=f"{target_step['gate']['title']} (returned): {state['process']} {state.get('record')}",
                                  body="An item you decided earlier was rejected at a later step and has come back to you.", run_id=state["id"])

    # ---------------------------------------------------------- human decisions
    def decide(self, run_id: str, by: str, verdict: str, meaning: str = "", payload_digest: str | None = None,
               fields: dict | None = None) -> dict:
        state = self.load(run_id)
        by = authz.principal_name(by)
        pending = state.get("pending")
        if not pending:
            raise RunError(f"run {run_id} is not waiting for a decision")
        if verdict not in ("approve", "reject"):
            raise RunError("verdict must be approve or reject")
        if by not in pending["assignees"]:
            raise RunError(f"{by} is not an assignee of this decision (assigned: {', '.join(pending['assignees'])})")
        if payload_digest and payload_digest != pending["payload_digest"]:
            raise RunError("the payload you saw differs from the pending payload; the item changed")
        bundle = self.releases.bundle(state["release"])
        allowed = authz.check(bundle, by, "approve", state["manifest"]["object"], state["manifest"]["folder"])
        if not allowed["allowed"]:
            raise RunError(allowed["why"])
        if self._sod_applies(bundle, state) and by == authz.principal_name(state["owner"]):
            raise RunError("segregation of duties: you prepared this item and cannot approve it")
        step = next(s for s in state["manifest"]["steps"] if s["id"] == pending["step"])
        track = Track(self._dir(state["id"]))
        if fields and state.get("record"):
            try:
                self.kernel.set_fields(state["record"], fields, by=by, run_id=state["id"], release=state["release"])
            except KernelGuardError as e:
                raise RunError(str(e)) from None
            track.append({"type": "record.fields_set", "run_id": state["id"], "step": step["id"], "by": by,
                          "fields": sorted(fields)})
        if verdict == "approve" and state.get("record") and self._immediate_next(state, step["id"]):
            nxt, _ = self._next_state(state, pending["checkpoint"])
            missing = self.kernel.required_missing(self.kernel.get(state["record"]), nxt)
            if missing:
                raise RunError(f"{nxt} needs {', '.join(missing)} before approval; supply them with the decision")
        self._record_decision(state, step, track, by=by, by_type="person", verdict=verdict,
                              meaning=meaning or f"{verdict}d {pending['title'].lower()}",
                              payload_digest=pending["payload_digest"])
        self._save(state)
        if verdict == "approve":
            return self.advance(run_id)
        return self.load(run_id)

    # ------------------------------------------------------------- escalation
    def tick(self, today: str | None = None) -> list[dict]:
        """Escalate overdue items: add the overdue route's assignees and notify them."""
        today = today or date.today().isoformat()
        escalated = []
        for state in self.list():
            pending = state.get("pending")
            if not pending or not pending.get("due") or pending["due"] >= today or pending.get("escalated"):
                continue
            bundle = self.releases.bundle(state["release"])
            rules = candidates(bundle, state["process"], "*", {"overdue": True})
            extra: list[str] = []
            for rule in rules:
                if rule["step"] != "*":
                    continue
                for p in principals_for(bundle, rule["assignee"]):
                    if bundle["members"][p]["type"] == "person" and p not in pending["assignees"]:
                        extra.append(p)
            if not extra:
                continue
            pending["assignees"] = pending["assignees"] + extra
            pending["escalated"] = today
            track = Track(self._dir(state["id"]))
            track.append({"type": "gate.escalated", "run_id": state["id"], "step": pending["step"], "assignees": extra,
                          "due": pending["due"], "route_rule": rules[0]["source"]})
            if state.get("record"):
                self.kernel.assign(state["record"], pending["assignees"], by="hub", run_id=state["id"],
                                   release=state["release"])
            if self.inbox is not None:
                for p in extra:
                    self.inbox.notify(p, kind="escalation", subject=f"Overdue: {pending['title']} on {state.get('record')}",
                                      body=f"Due {pending['due']}; escalated by {rules[0]['source']['text']}", run_id=state["id"])
            self._save(state)
            escalated.append({"run": state["id"], "record": state.get("record"), "to": extra})
        return escalated
