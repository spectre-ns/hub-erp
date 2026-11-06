"""The Op runner.

A run pins the policy release that is current when it is submitted and keeps
it until it ends. Steps invoke Cogs through declared tasks and return
envelopes. A Gate decides on every result. A human Gate routes the result
through the release routing table: a cog candidate may decide only within
its approval criteria, otherwise the item waits in people's inboxes. An
approval issues a grant; only then can reaching Cogs write.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from . import authz
from .cogs import CogRegistry, RunContext
from .envelope import gate_default
from .kernel import Kernel
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
    def __init__(self, root: Path, kernel: Kernel, releases: ReleaseStore, registry: CogRegistry, inbox=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.kernel = kernel
        self.releases = releases
        self.registry = registry
        self.inbox = inbox

    # --- persistence ------------------------------------------------------
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
        return sorted(out, key=lambda s: s["created_at"])

    def track(self, run_id: str) -> list[dict]:
        return Track(self._dir(self.load(run_id)["id"])).read()

    # --- submission -------------------------------------------------------
    def submit(self, process: str, inputs: dict, owner: str, at: str | None = None) -> str:
        release = self.releases.current(at)
        if release is None:
            raise RunError("no policy release is in effect")
        bundle = self.releases.bundle(release["digest"])
        manifest = bundle["ops"].get(process)
        if manifest is None:
            raise RunError(f"release {release['digest'][:12]} defines no process {process!r}")
        decision = authz.check(bundle, owner, "create", manifest["object"], manifest["folder"])
        if not decision["allowed"]:
            raise RunError(f"{owner} may not start {process}: {decision['why']}")
        first_role = manifest["steps"][0]["role"]
        if first_role != "system" and first_role not in authz.roles_of(bundle, owner):
            raise RunError(f"{owner} does not hold the role {first_role!r} that starts {process}")
        run_id = uuid.uuid4().hex[:10]
        state = {
            "id": run_id,
            "process": process,
            "owner": owner,
            "release": release["digest"],
            "manifest": manifest,
            "inputs": inputs,
            "steps": {},
            "pending": None,
            "grant": None,
            "created_at": utcnow(),
            "status": "submitted",
        }
        track = Track(self._dir(run_id))
        track.append({"type": "run.submitted", "run_id": run_id, "release_digest": release["digest"],
                      "process": process, "owner": owner, "owner_type": bundle["members"][authz.principal_name(owner)]["type"],
                      "manifest_digest": sha256_of(manifest), "inputs_digest": sha256_of(inputs),
                      "admission": decision["why"]})
        self._save(state)
        self.advance(run_id)
        return run_id

    # --- execution --------------------------------------------------------
    def _ctx(self, state: dict, bundle: dict) -> RunContext:
        return RunContext(run_id=state["id"], release=state["release"], bundle=bundle, owner=state["owner"],
                          kernel=self.kernel, grant=state.get("grant"), runner=self, inbox=self.inbox,
                          extras={"folder": state["manifest"]["folder"], "object": state["manifest"]["object"]})

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
                track.append({"type": "run.failed", "run_id": run_id, "step": sid,
                              "reason": (env["error"] or {}).get("detail") or "step result has error-severity problems"})
                self._save(state)
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
        track.append({"type": "run.completed", "run_id": run_id,
                      "outputs_digest": sha256_of({k: v["envelope"]["payload"] for k, v in state["steps"].items()})})
        self._save(state)
        return state

    def _routing_ctx(self, payload: dict) -> dict:
        return {"amount": payload.get("amount"), "currency": payload.get("currency"), "risk": payload.get("risk"),
                "conditions": payload.get("conditions", {})}

    def _sod_applies(self, bundle: dict, state: dict) -> bool:
        return any(s["object"] == state["manifest"]["object"] for s in bundle["sod"])

    def _human_gate(self, state: dict, bundle: dict, step: dict, env: dict, track: Track) -> str:
        payload = env["payload"] or {}
        rctx = self._routing_ctx(payload)
        gate = step["gate"]
        cands = candidates(bundle, gate["route"]["process"], gate["route"]["step"], rctx)
        sod = self._sod_applies(bundle, state)
        owner = authz.principal_name(state["owner"])
        if not cands:
            track.append({"type": "run.failed", "run_id": state["id"], "step": step["id"],
                          "reason": "no routing rule matches this item"})
            return "failed"
        for rule in cands:
            assignee = rule["assignee"]
            kind, _, name = assignee.partition(":")
            if kind == "cog":
                allowed = authz.check(bundle, name, "approve", state["manifest"]["object"], state["manifest"]["folder"])
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
                verdict = (review.get("payload") or {}).get("verdict", "escalate")
                track.append({"type": "gate.cog_review", "run_id": state["id"], "step": step["id"], "cog": name,
                              "cog_version": cog.version, "verdict": verdict,
                              "reasons": (review.get("payload") or {}).get("reasons"), "route_rule": rule["source"],
                              "delegating_rule": (review.get("payload") or {}).get("delegating_rule")})
                if verdict == "approve":
                    self._record_decision(state, step, track, by=name, by_type="cog", verdict="approve",
                                          meaning="approved within fixed criteria",
                                          payload_digest=sha256_of(payload),
                                          delegating_rule=(review.get("payload") or {}).get("delegating_rule"))
                    return "passed"
                if verdict == "reject":
                    self._record_decision(state, step, track, by=name, by_type="cog", verdict="reject",
                                          meaning="rejected within fixed criteria", payload_digest=sha256_of(payload),
                                          delegating_rule=(review.get("payload") or {}).get("delegating_rule"))
                    return "failed"
                continue
            people = [p for p in principals_for(bundle, assignee) if bundle["members"][p]["type"] == "person"
                      and authz.check(bundle, p, "approve", state["manifest"]["object"], state["manifest"]["folder"])["allowed"]]
            if sod:
                people = [p for p in people if p != owner]
            if not people:
                track.append({"type": "gate.skipped", "run_id": state["id"], "step": step["id"], "candidate": assignee,
                              "reason": "no eligible person holds this route", "route_rule": rule["source"]})
                continue
            state["pending"] = {"step": step["id"], "assignees": people, "route_rule": rule["source"],
                                "payload_digest": sha256_of(payload), "title": gate.get("title", "Approve")}
            track.append({"type": "gate.pending", "run_id": state["id"], "step": step["id"], "assignees": people,
                          "route_rule": rule["source"], "payload_digest": sha256_of(payload), "routing_ctx": rctx})
            if self.inbox is not None:
                for p in people:
                    self.inbox.notify(p, kind="approval", subject=f"{gate.get('title', 'Approve')}: {state['process']} {state['id']}",
                                      body=f"Routed by {rule['source']['frame']} line {rule['source']['line']}: {rule['source']['text']}",
                                      run_id=state["id"])
            return "waiting"
        track.append({"type": "run.failed", "run_id": state["id"], "step": step["id"],
                      "reason": "no eligible approver: every route was skipped or declined"})
        return "failed"

    def _record_decision(self, state: dict, step: dict, track: Track, *, by: str, by_type: str, verdict: str,
                         meaning: str, payload_digest: str, delegating_rule=None) -> None:
        decision = {"by": by, "by_type": by_type, "verdict": verdict, "meaning": meaning, "at": utcnow(),
                    "payload_digest": payload_digest, "delegating_rule": delegating_rule}
        state["steps"][step["id"]]["decision"] = decision
        state["pending"] = None
        track.append({"type": "gate.decided", "run_id": state["id"], "step": step["id"], **decision,
                      "signature": sha256_of({k: decision[k] for k in ("by", "verdict", "payload_digest", "at")})[:24]})
        if verdict == "approve":
            grant = {"id": uuid.uuid4().hex[:8], "run_id": state["id"], "decided_by": by, "by_type": by_type,
                     "payload_digest": payload_digest, "issued_at": utcnow(),
                     "steps": [s["id"] for s in state["manifest"]["steps"] if s["id"] != step["id"]]}
            state["grant"] = grant
            track.append({"type": "grant.issued", "run_id": state["id"], "grant": grant})
            state["steps"][step["id"]]["status"] = "passed"
        else:
            track.append({"type": "run.rejected", "run_id": state["id"], "step": step["id"], "by": by})

    # --- human decisions --------------------------------------------------
    def decide(self, run_id: str, by: str, verdict: str, meaning: str = "", payload_digest: str | None = None) -> dict:
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
        self._record_decision(state, step, track, by=by, by_type="person", verdict=verdict,
                              meaning=meaning or f"{verdict}d {pending['title'].lower()}",
                              payload_digest=pending["payload_digest"])
        self._save(state)
        if verdict == "approve":
            return self.advance(run_id)
        return self.load(run_id)
