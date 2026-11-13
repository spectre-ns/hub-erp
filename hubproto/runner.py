"""The Op runner and the workflow engine, one mechanism seen from two sides.

A run walks a record through its process's graph (``hubproto.process_graph``):
at each status an automatic transition fires when its condition holds (its
actions, the cog steps, run in order), or the item waits at a Gate whose
options are the gated transitions out of that status. A decision chooses one
of them (approve and reject name the forward and the rejection transitions);
a failed action moves the item to ``failed``, and Retry resumes it. A record's
state is the projection of its transitions, so every state change is on a
Track, and nothing moves a record outside a run. From each move on, a run
follows the release in force then (change control 5.1).

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
from . import process_graph as pg
from .cogs import CogRegistry, RunContext
from .dms import DMS
from .envelope import gate_default
from .kernel import Kernel, KernelGuardError, ScopedKernel
from .release import ReleaseStore, utcnow
from .routing import candidates, principals_for
from .rules import sha256_of
from .track import Track, hub_secret, sign, verify_signature

FIELD_ROLES = {"assignee": "assignee", "owner": "owner", "author": "author", "requester": "requested_by",
               "auditor": "auditor", "chair": "chair", "evaluator": "evaluator", "reporter": "reported_by"}
LIVE_STATUSES = ("submitted", "running", "waiting", "completed")
OPEN_RUN = ("submitted", "running", "waiting")
# Words in a segregation-of-duties sentence that name the deciding side, not a person to keep out.
DECIDER_WORDS = {"approver", "reviewer", "verifier", "decider", "signer", "countersigner"}
# Who made a change order, as segregation-of-duties sentences and a Gate's "not" name them: set aside only when one of
# them rejects it (``Runner.maker_may_reject``).
CO_MAKER_ROLES = {"contributor", "author", "requester", "preparer", "owner"}
SOD_FIELDS = {**{k: v for k, v in {"assignee": "assignee", "owner": "owner", "author": "author", "requester": "requested_by",
                                    "auditor": "auditor", "chair": "chair", "evaluator": "evaluator", "reporter": "reported_by"}.items()},
              "designer": "designer", "preparer": "owner", "filer": "filed_by", "captured-by": "captured_by", "raiser": "raised_by"}


def pending_title(step: dict) -> str:
    return (step.get("gate") or {}).get("title") or step.get("title") or "Approval"


PRE_DECISION_HOOKS: dict = {}  # record type -> fn(kernel, record, verdict); raise KernelGuardError to block
# record type -> fn(kernel, record, verdict, *, by, fields, bundle) -> the fields to set: checked before a decision's fields
# are set, so fields a guard refuses never reach the record (raise KernelGuardError to refuse them), whoever decides and
# by whatever path (a person's form, a cog's step, the library)
DECISION_FIELD_GUARDS: dict = {}
# record type -> fn(kernel, bundle, fields, owner): checked when a process files an item of that type (a new one, or an
# existing item it is started on), before anything is written; raise KernelGuardError to refuse it
START_GUARDS: dict = {}
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
        self.state_dir = self.root.parent  # <hub>/state: the signing secret lives there
        self.frames_store = None  # set by the Hub: the SOP texts a context cog reads
        self.frames_for_release = None  # set by the Hub: the SOP texts as a given release pinned them
        self.model = None  # set by the Hub: the model gateway context cogs draft with

    # ------------------------------------------------------------ persistence
    def _dir(self, run_id: str) -> Path:
        return self.root / run_id

    def load(self, run_id: str) -> dict:
        matches = [p for p in self.root.iterdir() if p.is_dir() and p.name.startswith(run_id)]
        if len(matches) != 1:
            raise RunError(f"run {run_id!r}: {len(matches)} matches")
        state = json.loads((matches[0] / "state.json").read_text())
        # The Track is the source of truth: a crash between writing it and saving the state cannot leave a Gate that
        # looks open after it was decided (see reconcile).
        tracked = Track(matches[0]).status()
        if tracked not in ("unknown", state.get("status")):
            state["status"] = tracked
        pending = state.get("pending")
        if pending and pending.get("kind", "decision") == "decision":
            if "kind" not in pending or not pending.get("route"):
                self._upgrade_pending(state)  # saved by earlier code: say what it is, and the route that put it there
            state["seen_digest"] = self.seen_digest(state)
        return state

    def _upgrade_pending(self, state: dict) -> None:
        """A Gate saved by earlier code names no ``kind`` and no ``route``. It is a decision, and its route is the routing
        sentence it cites (``route_rule``, in the run's release, else the one in force), or the step's role when it cites
        the process step. The route is set only when the people it names are the ones the Gate waits for, so this
        never widens who may decide; otherwise it stays unset (the strict default)."""
        pending = state["pending"]
        pending.setdefault("kind", "decision")
        if pending.get("route"):
            return
        rule = pending.get("route_rule") or {}
        bundles = []
        for d in (state.get("release"), (self.releases.current() or {}).get("digest")):
            try:
                if d:
                    bundles.append(self.releases.bundle(d))
            except KeyError:
                continue
        route = None
        for b in bundles:
            hit = next((r for r in b.get("routing") or [] if r.get("process") == state.get("process")
                        and (r.get("source") or {}).get("text") == rule.get("text")), None)
            if hit:
                route = hit["assignee"]
                break
        if route is None and rule.get("frame") == "(process step)":
            role = str(rule.get("text") or "").partition("]")[0].lstrip("[")
            route = (f"field:{FIELD_ROLES[role]}" if role in FIELD_ROLES else f"role:{role}") if role else None
        if route is None:
            return
        kind, _, name = route.partition(":")
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        people = set(pending.get("assignees") or [])
        if kind == "field":
            value = ((rec or {}).get("fields") or {}).get(name)
            named = {authz.principal_name(v) for v in (value if isinstance(value, list) else [value]) if v}
            if not people or not people <= named:
                return  # the Gate waits for someone the field does not name (escalated, reassigned): keep the strict default
        elif kind == "person" and people != {name}:
            return
        pending["route"] = route

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
                seen.update(self._content_of(rec))
        return sha256_of(seen)[:16]

    def _content_of(self, rec: dict) -> dict:
        """Links and attached documents as a signer sees them. Evidence filed about the item (a review, a snapshot) is
        not the item: filing it does not change what is being approved."""
        def evidence(loc):
            r = self.kernel.get(loc) if str(loc).startswith("rec:document:") else None
            return bool(r) and r["fields"].get("doc_class") == "evidence"
        docs = [d for d in (self.dms.documents_of(rec["locator"]) if self.dms else []) if d["fields"].get("doc_class") != "evidence"]
        return {"links": sorted((l["rel"], l["target"]) for l in rec["links"] if not evidence(l["target"])),
                "documents": sorted((d["locator"], str(d["fields"].get("content_sha") or ""), str(d["fields"].get("revision") or "")) for d in docs)}

    def record_digest(self, rec: dict) -> str:
        """What a signature binds to: the record's fields and its documents' content (not only its fields)."""
        return sha256_of({"fields": rec["fields"], **self._content_of(rec)})

    def secret(self) -> str:
        return hub_secret(self.state_dir)

    def verify_signatures(self, run_id: str) -> list[str]:
        """Problems with a run's signatures and Track chain (an empty list means it verifies)."""
        state = self.load(run_id)
        track = Track(self._dir(state["id"]))
        problems = list(track.verify())
        secret = self.secret()
        for ev in track.read():
            if ev["type"] in ("gate.decided", "gate.signed", "gate.vote") and verify_signature(secret, ev) == "forged":
                problems.append(f"the signature by {ev.get('by')} at {ev.get('step')} does not verify")
        return problems

    def _save(self, state: dict) -> None:
        state["status"] = Track(self._dir(state["id"])).status()
        if state["status"] in ("failed", "rejected", "completed", "cancelled"):
            state["pending"] = None
        path = self._dir(state["id"]) / "state.json"
        tmp = path.with_suffix(".json.tmp")
        # The seen digest is what the item is *now*: computed on every load, never stored (a stored one goes stale when
        # the item moves, follows a release or is moved by a state map, and a card quoting it could never be signed).
        tmp.write_text(json.dumps({k: v for k, v in state.items() if k != "seen_digest"}, indent=1, sort_keys=True))
        tmp.replace(path)  # atomic: a crash never leaves a half-written state

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.root.iterdir()):
            if (p / "state.json").exists():
                try:
                    st = json.loads((p / "state.json").read_text())
                except json.JSONDecodeError:
                    continue
                st.pop("seen_digest", None)  # one saved by an earlier version is stale: readers compute it (seen_digest)
                out.append(st)
        return sorted(out, key=lambda s: (s["created_at"], s.get("created_ns", 0)))

    def track(self, run_id: str) -> list[dict]:
        return Track(self._dir(self.load(run_id)["id"])).read()

    def run_for_record(self, locator: str) -> dict | None:
        runs = [s for s in self.list() if s.get("record") == locator]
        return runs[-1] if runs else None

    def pending_for(self, principal: str) -> list[dict]:
        """Runs waiting for a decision or a resubmission by this principal (at a Gate whose options include one they
        decide)."""
        return [s for s in self.list() if s.get("pending") and s["status"] == "waiting" and self.waits_for(s, principal)]

    @staticmethod
    def waits_for(state: dict, principal: str) -> bool:
        p = state.get("pending") or {}
        return principal in (p.get("assignees") or []) or any(principal in (o.get("assignees") or []) for o in p.get("options") or [])

    # ------------------------------------------------------------- the graph a run walks
    def _graph(self, state: dict, bundle: dict | None = None) -> dict:
        """The graph the run follows: its release's workflow for the process (a run saved before graphs, or a release
        rendered before them, gets the graph its process compiles to)."""
        wf = state.get("workflow") or {}
        if pg.is_graph_shape(wf):
            self._ensure_position(state, wf)
            return wf
        bundle = bundle or self.releases.bundle(state["release"])
        wf = (bundle.get("workflows") or {}).get(state["process"]) or {}
        if not pg.is_graph_shape(wf):
            from .release import render_workflow

            proc = (bundle.get("processes") or {}).get(state["process"]) or {}
            sla = (wf or {}).get("sla") or {}
            wf = render_workflow(proc, sla, (wf or {}).get("record_type") or state["manifest"].get("record_type") or "")
        state["workflow"] = wf
        self._ensure_position(state, wf)
        return wf

    def _ensure_position(self, state: dict, wf: dict) -> None:
        """A run saved before graphs: where it stands in the graph (status, path taken, Gate carriers)."""
        if "at" in state and "path" in state:
            return
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        pending = state.get("pending") or {}
        at = (rec or {}).get("state")
        if pending.get("kind", "decision") == "decision" and pending.get("checkpoint"):
            at = f"awaiting-{pending['checkpoint']}"
        if not pg.status_of(wf, at):
            at = (pg.initial(wf) or {}).get("to", "open")
        state["at"] = at
        # Numbered steps: the path is the main line, so step positions are those of the manifest.
        path, cur = [], (pg.initial(wf) or {}).get("to", "open")
        for _ in range(len(wf.get("statuses") or [])):
            t = next((x for x in pg.transitions_out(wf, cur, include_global=False)
                      if not x.get("system") and pg.option_verdict(wf, x) == "approve"), None)
            if t is None:
                break
            path.append({"transition": t["id"], "from": cur, "to": t["to"], "actions": [a for a in t.get("actions") or [] if isinstance(a, str)]})
            cur = t["to"]
        state["path"] = path
        state.setdefault("moving", None)
        state["carriers"] = {f"awaiting-{s['gate']['step']}": s["id"] for s in state["manifest"]["steps"]
                             if (s.get("gate") or {}).get("type") == "human"}

    @staticmethod
    def _action_sid(t: dict, i: int, a) -> str:
        return a if isinstance(a, str) else f"{t['id']}#{i}"

    def _executed(self, state: dict) -> list[str]:
        """The action steps of the moves the run has taken, in order (a step id may repeat when the run loops)."""
        return [a for e in state.get("path") or [] for a in e.get("actions") or []]

    def _rewind_path(self, state: dict, pos: int) -> None:
        """Go back to the action at position ``pos`` of the path: the run stands where that move started and the move
        resumes from that action (the actions before it in the same move stay done)."""
        n = 0
        for i, e in enumerate(state.get("path") or []):
            k = len(e.get("actions") or [])
            if pos < n + k:
                state["path"] = state["path"][: i + 1]
                state["at"] = e["from"]
                state["moving"] = {"transition": e["transition"], "from": e["from"], "to": e["to"], "actions": list(e["actions"]),
                                   **({"close": e["close"]} if e.get("close") else {})}
                return
            n += k

    def _follow_release(self, state: dict, track: Track, move: str) -> bool:
        """From its next move an item follows the release in force at that moment (change control 5.1): its routing,
        limits, checks and next steps. Its status is unchanged and a Gate already waiting keeps its assignees until the
        item moves. The Track records each change of release. An item part-way through a move, or in a status the
        release in force does not have (a state map moves it), stays where it is until then."""
        cur = self.releases.current()
        if not cur or cur["digest"] == state["release"]:
            return False
        bundle = self.releases.bundle(cur["digest"])
        wf = (bundle.get("workflows") or {}).get(state["process"])
        manifest = (bundle.get("ops") or {}).get(state["process"])
        old = state["release"]
        why = ""
        if not wf or not manifest:
            why = f"the release in force has no process {state['process']}"
        else:
            if not pg.is_graph_shape(wf):
                why = "the release in force predates workflow graphs"
            else:
                self._graph(state)
                if not pg.status_of(wf, state.get("at")):
                    why = f"the release in force has no status {state.get('at')!r}; a state map moves the item"
                elif state.get("moving"):
                    why = "it is part-way through a move; it follows the release in force from its next move"
        if why:
            if not any(e.get("type") == "run.release_kept" and e.get("to") == cur["digest"] for e in track.read()[-5:]):
                track.append({"type": "run.release_kept", "run_id": state["id"], "release": old, "to": cur["digest"], "move": move, "reason": why})
            return False
        state["release"] = cur["digest"]
        state["workflow"] = wf
        state["manifest"] = manifest
        track.append({"type": "run.release_changed", "run_id": state["id"], "from": old, "to": cur["digest"], "status": state.get("at"),
                      "move": move, "manifest_digest": sha256_of(manifest)})
        self._save(state)  # the saved state and the Track agree even if the move is then refused
        return True

    # ------------------------------------------------------------- submission
    def may_start(self, process: str, who: str, record: str | None = None) -> str:
        """Empty when ``who`` may start this process (on this record): the same checks as submit, without starting it."""
        release = self.releases.current()
        if release is None:
            return "no policy release is in effect"
        bundle = self.releases.bundle(release["digest"])
        manifest = bundle["ops"].get(process)
        if manifest is None:
            return f"there is no process {process!r}"
        d = authz.check(bundle, who, "create", manifest["object"], manifest["folder"])
        if not d["allowed"]:
            return f"{who} may not start {process}: {d['why']}"
        first_role = manifest.get("start_role") or manifest["steps"][0]["role"]
        if first_role not in ("system", *FIELD_ROLES) and first_role not in authz.roles_of(bundle, who):
            return f"{who} does not hold the role {first_role!r} that starts {process}"
        if record:
            rec = self.kernel.get(record)
            if rec is None:
                return "no such record"
            return self._may_submit_existing(bundle, rec, bundle["workflows"][process], who)
        return ""

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
            # Credit what opened it: the automatic step of the parent run (a CAPA opened from an NCR's disposition),
            # not the person whose decision set it going.
            opener = next((e.get("cog") for e in reversed(Track(self._dir(parent["id"])).read()) if e["type"] == "step.started"), None)
        else:
            decision = authz.check(bundle, owner, "create", manifest["object"], manifest["folder"])
            if not decision["allowed"]:
                raise RunError(f"{owner} may not start {process}: {decision['why']}")
            first_role = manifest.get("start_role") or manifest["steps"][0]["role"]
            if first_role not in ("system", *FIELD_ROLES) and first_role not in authz.roles_of(bundle, owner):
                raise RunError(f"{owner} does not hold the role {first_role!r} that starts {process}")
        run_id = uuid.uuid4().hex[:10]
        if not parent_run:
            opener = None
        if record:
            rec = self.kernel.require(record)
            why = self._may_submit_existing(bundle, rec, wf, owner_name)
            if why:
                raise RunError(why)
            self._start_guard(wf["record_type"], bundle, {**rec["fields"], **(inputs.get("record") or {})}, owner_name)
            home = self.folder_for(manifest, rec["fields"])
            if home != manifest["folder"] and not parent_run:
                d = authz.check(bundle, owner, "create", manifest["object"], home)
                if not d["allowed"]:
                    raise RunError(f"{owner} may not file a {rec['fields'].get('doc_type')} in {home}: {d['why']}")
            if rec["folder"] != home:
                rec = self.kernel.move(record, home, by=owner_name, run_id=run_id, release=release["digest"],
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
            if wf["record_type"] == "document" and fields.get("title"):
                revises, why = self._revision_of(bundle, str(fields["title"]), owner_name)
                if why:
                    raise RunError(why)
                if revises:
                    fields["revises"] = revises
            home = self.folder_for(manifest, fields)
            if home != manifest["folder"] and not parent_run:
                d = authz.check(bundle, owner, "create", manifest["object"], home)
                if not d["allowed"]:
                    raise RunError(f"{owner} may not file a {fields.get('doc_type')} in {home}: {d['why']}")
            self._start_guard(wf["record_type"], bundle, fields, owner_name)
            try:
                rec = self.kernel.create(wf["record_type"], home, fields, by=opener or owner_name, run_id=run_id,
                                         release=release["digest"], state="open")
            except KernelGuardError as e:
                raise RunError(f"cannot create {wf['record_type']}: {e}") from None
        state = {
            "id": run_id, "process": process, "owner": owner_name, "release": release["digest"], "manifest": manifest,
            "workflow": wf, "record": rec["locator"], "parent_run": parent_run, "inputs": inputs, "steps": {},
            "pending": None, "grant": None, "decision_fields": {}, "created_at": utcnow(), "created_ns": time.time_ns(),
            "status": "submitted", "opened_by": opener,
            # Where it stands in the graph: the status, the move under way, the moves taken, the step that carries each Gate.
            "at": (pg.initial(wf) or {}).get("to", "open"), "moving": None, "path": [], "carriers": {},
            "opened_from": (self.load(parent_run).get("record") if parent_run else None),
        }
        track = Track(self._dir(run_id))
        track.append({"type": "run.submitted", "run_id": run_id, "release_digest": release["digest"], "process": process,
                      "owner": owner_name, "record": rec["locator"],
                      "owner_type": bundle["members"].get(owner_name, {}).get("type", "system"), "parent_run": parent_run,
                      "manifest_digest": sha256_of(manifest), "inputs_digest": sha256_of(inputs), "admission": decision["why"],
                      "opened_by": opener, "opened_from": state["opened_from"]})
        self._save(state)
        self.advance(run_id)
        return run_id

    def _start_guard(self, record_type: str, bundle: dict, fields: dict, owner: str) -> None:
        guard = START_GUARDS.get(record_type)
        if guard is not None:
            try:
                guard(self.kernel, bundle, fields, owner)
            except KernelGuardError as e:
                raise RunError(str(e)) from None

    @staticmethod
    def folder_for(manifest: dict, fields: dict) -> str:
        """Where the process files an item: its folder, or the folder it names for that document type ("Policies are
        filed in Policy.")."""
        return (manifest.get("files_by_type") or {}).get(str((fields or {}).get("doc_type") or ""), manifest["folder"])

    def _may_submit_existing(self, bundle: dict, rec: dict, wf: dict, owner: str) -> str:
        """Why an existing item may not be put through a process by this person (empty when it may): it must be of the
        process's record type, not finished, not in a live run, not evidence or a received file, readable by the
        starter, and theirs (owner, author, requester, filer), assigned to them, or (documents) a document controller's.
        Checked before anything moves."""
        f = rec["fields"]
        if rec["type"] != wf["record_type"]:
            return f"{rec['number']} is a {rec['type']}, not a {wf['record_type']}: this process cannot take it"
        if f.get("doc_class") in ("evidence", "external"):
            return f"{rec['number']} is {f['doc_class']}: it is kept as filed and never goes through approval again"
        if rec["state"] in self.NOT_RESTARTABLE or rec["state"] == "closed" or rec["state"] == wf.get("terminal"):
            return f"{rec['number']} is {rec['state']}; it changes through a new revision or a new item, not by approving it again"
        live = self.run_for_record(rec["locator"])
        if live and live["status"] in OPEN_RUN:
            return f"{rec['number']} is already in a run ({live['process']}, {live['status']})"
        if not authz.check(bundle, owner, "read", "*", rec["folder"])["allowed"]:
            return f"{owner} may not read {rec['folder']}"
        mine = owner in {f.get("owner"), f.get("author"), f.get("requested_by"), f.get("reported_by"), f.get("filed_by"), rec.get("created_by")}
        if not (mine or owner in (rec.get("assignees") or [])
                or (rec["type"] == "document" and "document-controller" in authz.roles_of(bundle, owner))):
            return f"{rec['number']} is not {owner}'s: only its owner, whoever it is assigned to" + \
                (" or a document controller" if rec["type"] == "document" else "") + " may start a process on it"
        if rec["type"] == "document" and f.get("title") and not f.get("revises"):  # a named revision keeps its lineage, never a title match
            revises, why = self._revision_of(bundle, str(f["title"]), owner, exclude=rec["locator"])
            if why:
                return why
            if revises and f.get("revises") != revises:
                self.kernel.set_fields(rec["locator"], {"revises": revises}, by=owner)
        return ""

    def _revision_of(self, bundle: dict, title: str, by: str, exclude: str | None = None) -> tuple[str, str]:
        """A document with the title of one in force is a revision of it: (its number, "") when ``by`` may revise it
        (its owner or author, or a document controller), else ("", why not)."""
        for other in self.kernel.query(type_="document", state="effective"):
            if other["locator"] == exclude or other["fields"].get("title") != title:
                continue
            of = other["fields"]
            if by in (of.get("owner"), of.get("author")) or "document-controller" in authz.roles_of(bundle, by):
                return other["number"], ""
            return "", (f"{other['number']} “{title}” is in force; only its owner ({of.get('owner') or of.get('author')}) or a document "
                        f"controller may revise it. Give the new document another title, or ask them")
        return "", ""

    def fields_locked(self, rec: dict) -> str:
        """Why a record's fields may not be changed outside a Gate (empty when they may): it waits at a Gate, it is
        finished, or someone signed it since it was last open. A change after a signature goes through rework
        (rejection and resubmission) or a new revision, so no signature ends up on content it did not see."""
        state = rec.get("state") or ""
        if state.startswith("awaiting-"):
            return f"{rec['number']} waits at a Gate; change it through the decision or a rejection"
        if state in self.NOT_RESTARTABLE or state in ("closed", "cancelled", "rejected", "approved", "acknowledged", "ordered",
                                                      "received", "active", "accepted", "released", "posted", "effective"):
            return f"{rec['number']} is {state}; it is finished"
        live = self.run_for_record(rec["locator"])
        if live and live["status"] in OPEN_RUN and state not in ("open", "draft", "returned", "failed"):
            return f"{rec['number']} is in a run ({live['process']}) past its start"
        if rec.get("decisions") and state not in ("open", "draft", "returned", "failed"):
            return f"{rec['number']} carries signatures; change it through rework or a new revision"
        return ""

    def reconcile(self) -> list[str]:
        """Runs whose saved state disagrees with their Track after a crash: a Gate decided in the Track but still open in
        the state is closed (the decision recorded where it is missing) and the run carries on. Returns the run ids."""
        fixed = []
        for state in self.list():
            if state.get("status") != "waiting" or not state.get("pending"):
                continue
            track = Track(self._dir(state["id"]))
            if track.status() == "waiting":
                continue
            evs = track.read()
            last = next((e for e in reversed(evs) if e["type"] == "gate.decided"), None)
            if not last or last.get("step") not in (state["pending"].get("step"), state["pending"].get("checkpoint")):
                continue
            sid = state["pending"]["step"]
            decision = {k: v for k, v in last.items() if k not in ("type", "seq", "prev", "entry_digest")}
            state["steps"].setdefault(sid, {})["decision"] = decision
            state["pending"] = None
            if last.get("verdict") == "approve":
                state["steps"][sid]["status"] = "passed"
                grant = next((e["grant"] for e in reversed(evs) if e["type"] == "grant.issued"), None)
                state["grant"] = grant
                wf = self._graph(state)
                t = pg.transition(wf, last.get("transition") or last.get("step")) or pg.primary_option(wf, state.get("at"))
                if t is not None and not state.get("moving"):
                    self._take(state, t)
                rec = self.kernel.get(state["record"]) if state.get("record") else None
                if rec and not any(d.get("signature") == decision.get("signature") for d in rec.get("decisions") or []):
                    self.kernel.append("record.decision", state["record"], decision, run_id=state["id"], release=state["release"], by=decision.get("by", ""))
            self._save(state)
            if last.get("verdict") == "approve":
                self.advance(state["id"])
            fixed.append(state["id"])
        return fixed

    # -------------------------------------------------------------- execution
    def _ctx(self, state: dict, bundle: dict, cog) -> RunContext:
        scoped = ScopedKernel(self.kernel, cog_id=cog.id, reaches=cog.reaches, run_id=state["id"],
                              record=state.get("record"), grant=state.get("grant"))
        dms = DMS(scoped, self.dms.blobs) if self.dms is not None else None
        return RunContext(run_id=state["id"], release=state["release"], bundle=bundle, owner=state["owner"],
                          kernel=scoped, grant=state.get("grant"), runner=self, inbox=self.inbox,
                          record=state.get("record"), dms=dms,
                          frames=(lambda d=state["release"]: self.frames_for_release(d)) if self.frames_for_release else self.frames_store,
                          model=self.model,
                          extras={"folder": state["manifest"]["folder"], "object": state["manifest"]["object"],
                                  "process": state["process"], "decision_fields": dict(state.get("decision_fields") or {}),
                                  "cog_profile": (bundle.get("cogs") or {}).get(cog.id)})

    def advance(self, run_id: str) -> dict:
        """Walk the item on through its graph: finish the move under way (its actions, in order), then from the status
        it reaches fire the first automatic transition whose condition holds, or open the Gate there, or close it."""
        state = self.load(run_id)
        if state["status"] in ("completed", "failed", "rejected", "waiting", "cancelled"):
            return state
        bundle = self.releases.bundle(state["release"])
        track = Track(self._dir(state["id"]))
        wf = self._graph(state, bundle)
        for _ in range(4 * len(wf.get("transitions") or []) + 8):
            mv = state.get("moving")
            if mv:
                if not self._run_actions(state, bundle, track, wf, mv):
                    return state
                if self._arrive(state, track, wf, mv):
                    return state
                continue
            at = state.get("at")
            st = pg.status_of(wf, at)
            if st is None:
                self._fail(state, track, str(at), f"{state['process']} has no status {at!r}", kind="organisation")
                return state
            if st["category"] == "done":
                self._finish(state, track, wf, at, None)
                return state
            autos = [t for t in pg.transitions_out(wf, at, include_global=False) if pg.is_auto(t)]
            auto = next((t for t in autos if self._auto_holds(state, t)), None)
            if auto is not None and self.is_change_order(state) and self._approves(wf, auto):
                self._fail(state, track, f"to-{auto['to']}", f"{state['process']} approves a change order automatically "
                           f"(“{auto.get('name') or auto['id']}”): {self.PERSON_SIGNS_CONFIGURATION}", kind="organisation")
                return state
            if auto is not None:
                why = self._validators_block(state, wf, auto)
                if why:
                    self._fail(state, track, f"to-{auto['to']}", why)
                    return state
                self._take(state, auto)
                continue
            if pg.options(wf, at):
                outcome = self._open_gate(state, bundle, track, wf)
                self._save(state)
                if outcome == "waiting" and (state.get("pending") or {}).get("committee"):
                    return self._committee_cogs(state)
                if outcome == "waiting" and (state.get("pending") or {}).get("assignee_type") == "cog":
                    return self.give_to_cog(state)
                return state
            why = "none of its automatic ways out applies" if autos else "nothing leads out of it"
            self._fail(state, track, str(at), f"{st['name']}: {why}", kind="organisation")
            return state
        self._fail(state, track, str(state.get("at")), f"{state['process']} moved automatically too many times without stopping; "
                                                       "its automatic transitions loop", kind="organisation")
        return state

    def _take(self, state: dict, t: dict, close: dict | None = None) -> None:
        """Start a move along a transition: its actions run afresh (a loop runs them again)."""
        actions = list(t.get("actions") or [])
        sids = [self._action_sid(t, i, a) for i, a in enumerate(actions)]
        for sid in sids:
            state["steps"].pop(sid, None)
        mv = {"transition": t["id"], "from": state.get("at"), "to": t["to"], "actions": sids, **({"close": close} if close else {})}
        state["moving"] = mv
        state.setdefault("path", []).append(dict(mv))

    def _run_actions(self, state: dict, bundle: dict, track: Track, wf: dict, mv: dict) -> bool:
        """Run the move's actions in order (those already done in it are skipped). False when one failed: the item is
        then in ``failed`` and Retry resumes the move."""
        t = pg.transition(wf, mv["transition"]) or {}
        raw = list(t.get("actions") or [])
        target = pg.status_of(wf, mv["to"]) or {}
        for i, sid in enumerate(mv["actions"]):
            done = state["steps"].get(sid)
            if done and done["status"] in ("passed", "pass-with-problems"):
                continue
            if done and done["status"] == "gate-pending":
                continue
            if state.get("record"):
                r = self.kernel.get(state["record"])
                if r and r["hold"]:
                    self._fail(state, track, sid, f"{r['number']} is on hold; lift the hold and retry the run")
                    return False
            inline = next((a for j, a in enumerate(raw) if isinstance(a, dict) and self._action_sid(t, j, a) == sid), None)
            if inline is not None:
                self._inline_action(state, track, sid, inline)
                continue
            step = next((x for x in state["manifest"]["steps"] if x["id"] == sid), None)
            if step is None:
                self._fail(state, track, sid, f"{state['process']} has no step {sid!r} (the release in force does not define it)", kind="organisation")
                return False
            cog = self.registry.get(step["cog"]["id"])
            if (i == len(mv["actions"]) - 1 and cog.id == "document-renderer" and state.get("record")
                    and target.get("category") == "done" and target.get("outcome", "completed") == "completed"):
                # Close the record first so the evidence snapshot shows the final state and every signature.
                if not self._close_record(state, track, mv["to"]):
                    return False
            payloads = {k: {"payload": v["envelope"].get("payload")} for k, v in state["steps"].items()}
            step_input = {k: _resolve(v, state["inputs"], payloads) for k, v in step["input"].items()}
            track.append({"type": "step.started", "run_id": state["id"], "step": sid, "cog": cog.id, "cog_version": cog.version,
                          "task": step["cog"]["task"], "input_digest": sha256_of(step_input)})
            env = cog.invoke(step["cog"]["task"], step_input, self._ctx(state, bundle, cog))
            verdict = gate_default(env)
            track.append({"type": "step.result", "run_id": state["id"], "step": sid, "ok": env["ok"], "problems": env["problems"],
                          "error": env["error"], "timing": env["timing"], "payload_digest": sha256_of(env["payload"]),
                          "gate": verdict})
            state["steps"][sid] = {"status": verdict if verdict != "pass" else "passed", "envelope": env}
            if verdict == "fail":
                detail = (env["error"] or {}).get("detail") or "; ".join(p["detail"] for p in env["problems"] if p["severity"] == "error") \
                    or "step result has error-severity problems"
                self._fail(state, track, sid, detail, recheck=str((env["error"] or {}).get("recheck") or ""))
                return False
            self._save(state)
        return True

    def _inline_action(self, state: dict, track: Track, sid: str, action: dict) -> None:
        """An action the hub does itself: set a field, or notify someone."""
        if "set" in action and state.get("record"):
            self.kernel.set_fields(state["record"], dict(action["set"]), by="hub", run_id=state["id"], release=state["release"])
            track.append({"type": "record.fields_set", "run_id": state["id"], "step": sid, "by": "hub", "fields": sorted(action["set"])})
        elif "notify" in action:
            kind, _, who = str(action["notify"]).partition(":")
            rec = self.kernel.get(state["record"]) if state.get("record") else None
            if kind == "field":
                value = state["owner"] if who == "owner" else ((rec or {}).get("fields") or {}).get(FIELD_ROLES.get(who, who))
                people = [authz.principal_name(value)] if value else []
            else:
                people = principals_for(self.releases.bundle(state["release"]), f"role:{who}")
            self._notify(people, kind="moved", subject=f"{(rec or {}).get('number') or state['process']}: {state['process'].replace('-', ' ')}",
                         body=f"{(rec or {}).get('number') or 'The item'} moved on in {state['process'].replace('-', ' ')}; it is now "
                              f"{(rec or {}).get('state', '').replace('-', ' ')}.", run_id=state["id"])
        state["steps"][sid] = {"status": "passed", "envelope": {"ok": True, "payload": {"action": action}, "problems": [], "error": None, "timing": {}}}

    def _arrive(self, state: dict, track: Track, wf: dict, mv: dict) -> bool:
        """The move's actions are done: the item enters the target status. True when that ended the run."""
        to = mv["to"]
        state["at"] = to
        state["moving"] = None
        st = pg.status_of(wf, to) or {}
        if st.get("category") == "done":
            self._finish(state, track, wf, to, mv.get("close"))
            return True
        if not pg.options(wf, to) and state.get("record"):
            # A status nobody decides at (the item moves on automatically from it): the record shows it.
            rec = self.kernel.get(state["record"])
            if rec and rec["state"] != to:
                t = pg.transition(wf, mv["transition"]) or {}
                try:
                    self.kernel.transition(state["record"], to, by="hub", run_id=state["id"], release=state["release"],
                                           reason=t.get("name") or "moved on")
                except KernelGuardError as e:
                    self._fail(state, track, str(to), str(e))
                    return True
        self._save(state)
        return False

    def _finish(self, state: dict, track: Track, wf: dict, to: str, close: dict | None) -> None:
        """The item reached a done status: completed (the process's end), or rejected or cancelled by a decision."""
        st = pg.status_of(wf, to) or {}
        if st.get("outcome", "completed") == "completed" and not close:
            self._complete(state, track, to)
            return
        close = close or {}
        by = close.get("by") or "hub"
        event = "run.cancelled" if st.get("outcome") == "cancelled" else "run.rejected"
        track.append({"type": event, "run_id": state["id"], "step": close.get("step"), "by": by, "to": to,
                      **({"reason": close["meaning"]} if event == "run.cancelled" and close.get("meaning") else {})})
        state["grant"] = None
        state["pending"] = None
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        if rec and (rec["state"] != to or close.get("decision")):
            try:
                self.kernel.transition(state["record"], to, by=by, run_id=state["id"], release=state["release"],
                                       decision=close.get("decision"), reason=close.get("meaning") or "", assignees=[], due=None)
            except KernelGuardError as e:
                track.append({"type": "run.failed", "run_id": state["id"], "step": close.get("step"), "reason": str(e)})
        word = "cancelled" if event == "run.cancelled" else "rejected"
        self._notify([state["owner"]], kind="rejection", subject=f"{state['process']} {state.get('record')} {word}",
                     body=f"{by} {word} it: {close.get('meaning') or ''}", run_id=state["id"])
        self._save(state)

    def _auto_holds(self, state: dict, t: dict) -> bool:
        when = (t.get("gate") or {}).get("when") or "actions-succeed"
        return when == "actions-succeed" or self._condition(state, when)

    def _condition(self, state: dict, key: str) -> bool:
        """A closed-vocabulary condition on the item, from what its fields say."""
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        f = (rec or {}).get("fields") or {}
        facts = dict(f.get("conditions") or {})
        derived = {"risk_low": f.get("risk") == "low", "risk_not_high": f.get("risk") in ("low", "medium"),
                   "change_minor": f.get("change_type") == "minor", "supplier_approved": f.get("supplier_approved")}
        if key in facts:
            return bool(facts[key])
        return bool(derived.get(key))

    def _validators_block(self, state: dict, wf: dict, t: dict) -> str:
        """Why a transition may not be taken now (its validators), in a sentence; empty when it may."""
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        f = (rec or {}).get("fields") or {}
        name = t.get("name") or t["id"]
        for v in t.get("validators") or []:
            if "fields_set" in v:
                missing = [x for x in v["fields_set"] if f.get(x) in (None, "", [], {})]
                if missing:
                    return f"{name} needs {', '.join(x.replace('_', ' ') for x in missing)} first"
            elif "status_visited" in v:
                sid = v["status_visited"]
                if not any(sid in (e.get("from"), e.get("to")) for e in state.get("path") or []) and state.get("at") != sid:
                    return f"{name} is taken only after the item has been {(pg.status_of(wf, sid) or {}).get('name', sid)}"
            elif "condition" in v and not self._condition(state, v["condition"]):
                return f"{name} is taken only when {pg.condition_phrase(v['condition'])}"
        return ""

    def _fail(self, state: dict, track: Track, sid: str, reason: str, kind: str = "data", gate: str = "", recheck: str = "") -> None:
        """A failed run goes back to its owner: the record waits in state ``failed`` assigned to them, with a
        message. ``kind`` says what fixes it: 'data' (correct the item and retry) or 'organisation' (nobody may take
        the step; a policy owner changes who may, the item's fields cannot fix it). ``recheck`` names the cog of an
        earlier step whose check no longer holds (a step's error says so): a retry starts again from that step."""
        track.append({"type": "run.failed", "run_id": state["id"], "step": sid, "reason": reason, "kind": kind, "gate": gate,
                      **({"recheck": recheck} if recheck else {})})
        state["pending"] = None
        if state.get("record"):
            try:
                self.kernel.transition(state["record"], "failed", by="hub", run_id=state["id"], release=state["release"],
                                       reason=reason, assignees=[state["owner"]])
            except KernelGuardError:
                pass
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        what = (rec or {}).get("number") or state.get("record") or state["id"]
        where = gate or sid.replace("-", " ")
        if kind == "organisation":
            body = (f"{reason.rstrip('.')}. Changing the item will not help: a policy owner can add someone who may take this step "
                    f"or change who it goes to; then retry, or cancel it.")
        else:
            body = f"{reason.rstrip('.')}. Correct what it says on the item and press Retry, or cancel it."
        self._notify([state["owner"]], kind="failure", subject=f"{what} stopped at {where}", body=body, run_id=state["id"])
        self._save(state)

    def _close_record(self, state: dict, track: Track, target: str | None = None) -> bool:
        terminal = target or self._graph(state).get("terminal") or state["workflow"].get("terminal")
        rec = self.kernel.get(state["record"])
        if rec and rec["fields"].get("held_for") and terminal == "effective":
            # Approved, held for its change order: it comes into force when the change order is released.
            terminal = "approved"
        if rec and rec["state"] == terminal:
            return True
        try:
            self.kernel.transition(state["record"], terminal, by="hub", run_id=state["id"], release=state["release"],
                                   reason="process completed")
            return True
        except KernelGuardError as e:
            self._fail(state, track, "complete", f"cannot close as {terminal}: {e}")
            return False

    def _complete(self, state: dict, track: Track, target: str | None = None) -> None:
        if state.get("record") and not self._close_record(state, track, target):
            return
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        track.append({"type": "run.completed", "run_id": state["id"], "record": state.get("record"),
                      "state": rec["state"] if rec else (target or state["workflow"]["terminal"]),
                      "outputs_digest": sha256_of({k: v["envelope"]["payload"] for k, v in state["steps"].items()})})
        self._save(state)
        for hook in list(self.on_completed):  # automations triggered by a process completing (hubproto.automation)
            try:
                hook(self.load(state["id"]), rec)
            except Exception as e:  # an automation that cannot start never undoes the run; it says why
                track.append({"type": "automation.not_started", "run_id": state["id"], "reason": f"{type(e).__name__}: {e}"})

    # ------------------------------------------------------------ human gates
    def _routing_ctx(self, payload: dict, rec: dict | None = None) -> dict:
        f = (rec or {}).get("fields", {})
        get = lambda k: payload.get(k, f.get(k))
        return {"amount": get("amount"), "currency": get("currency"), "risk": get("risk"), "doc_type": get("doc_type"),
                "change_type": get("change_type"), "conditions": payload.get("conditions", f.get("conditions", {}))}

    def _sod_applies(self, bundle: dict, state: dict) -> bool:
        return any(s["object"] == state["manifest"]["object"] and not s.get("rule") for s in bundle["sod"])

    def excluded(self, bundle: dict, state: dict, route: str = "role:", gate_not: list | None = None, signing: bool = True,
                 makers: bool = True) -> dict[str, str]:
        """Who may not decide this item, and why. Every process keeps its preparer (who filed it, the run's owner)
        from approving it on a route to a role or a cog; a segregation sentence also keeps out everyone it names on
        the record (the reporter, the author, the requester...). A step the SOP routes to a named field (the
        requester verifies, the assignee completes) is that person's by design, unless a sentence says otherwise."""
        out: dict[str, str] = {}
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        f = (rec or {}).get("fields") or {}
        owner = authz.principal_name(state["owner"])
        child = bool(state.get("parent_run"))
        if child:
            # A process opened automatically from another (a CAPA from an NCR's disposition) was prepared by whoever
            # raised the original item, not by the person whose decision opened it.
            try:
                owner = authz.principal_name(self.load(state["parent_run"])["owner"])
            except RunError:
                pass
        named_route = str(route or "").startswith(("field:", "person:"))
        rules = [r for r in bundle.get("sod") or [] if r["object"] == state["manifest"]["object"] and not r.get("rule")]
        # makers=False (a change order's maker rejecting it, ``maker_may_reject``): leave out only the rules that keep
        # whoever made it from deciding it; every other exclusion stands.
        skip = set() if makers or not self.is_change_order(state) else CO_MAKER_ROLES
        if (not named_route or rules) and not skip:
            out[owner] = "segregation of duties: they prepared this item"
            created = (rec or {}).get("created_by")
            if created and not child and created not in ("hub", "system", "seed") and created not in out:
                out[created] = "segregation of duties: they filed this item"
        for r in rules:
            for role in r.get("roles") or []:
                if role in DECIDER_WORDS or role in skip:
                    continue
                field = SOD_FIELDS.get(role) or role.replace("-", "_")
                vals = f.get(field)
                for v in vals if isinstance(vals, list) else [vals]:
                    if v and isinstance(v, str):
                        out.setdefault(authz.principal_name(v), f"segregation of duties: they are the {role.replace('-', ' ')} of this item "
                                                                f"({r.get('source', {}).get('text', 'policy')})")
        if self.is_change_order(state) and not skip:
            self._change_order_signers(state, out, owner, rec, signing=signing)
        # The gate's own exclusions ("the quality manager decides, not the author"; not whoever decided another step).
        nots = gate_not if gate_not is not None else (((state.get("pending") or {}).get("gate") or {}).get("not") or [])
        for n in nots:
            if n in skip:
                continue
            if n in SOD_FIELDS or n in FIELD_ROLES:
                v = owner if n in ("owner", "preparer") else f.get(SOD_FIELDS.get(n) or FIELD_ROLES.get(n) or n)
                for x in v if isinstance(v, list) else [v]:
                    if x and isinstance(x, str):
                        out.setdefault(authz.principal_name(x), f"the process says the {n.replace('-', ' ')} does not decide this")
                continue
            # The latest decision of that step (an item that loops back is decided afresh on each pass).
            last = next((ev for ev in reversed(Track(self._dir(state["id"])).read())
                         if ev["type"] == "gate.decided" and ev.get("transition") == n and ev.get("by")), None)
            for who in (last or {}).get("voters") or ([last["by"]] if last else []):  # a committee: each member who voted
                out.setdefault(who, f"segregation of duties: they decided {n.replace('-', ' ')} on this item")
        return out

    # ------------------------------------------------------------ change orders (configuration approvals)
    @staticmethod
    def is_change_order(state: dict) -> bool:
        """The item is a change order: its approval puts configuration in force."""
        return ((state.get("workflow") or {}).get("record_type") or (state.get("manifest") or {}).get("record_type")) == "change-order"

    def change_order_blocks(self, state: dict) -> list[str]:
        """Why the change order may not be approved now (``changecontrol.approval_reasons``: every item decided, every
        warning accepted, the delta current...); empty for any other item. Checked at every gate kind that approves it."""
        if not self.is_change_order(state) or getattr(self, "hub", None) is None or not state.get("record"):
            return []
        from . import eco

        return eco.check_approval(self.hub, state["record"])

    PERSON_SIGNS_CONFIGURATION = ("a change order's approval puts configuration in force, so a person signs it: a cog may review it "
                                  "and say what it found, never approve it (a guard, like a cog never signing its own work)")

    def contributors(self, state: dict) -> list[str]:
        """Who contributed to the item and so never decides it: for a change order, whoever wrote a revision it carries or
        made a direct change on it, and its requester (``changecontrol.contributors``)."""
        if not self.is_change_order(state) or not state.get("record"):
            return []
        rec = self.kernel.get(state["record"]) or {}
        out = list(rec.get("fields", {}).get("contributor") or [])
        if getattr(self, "hub", None) is not None:
            try:
                from . import changecontrol

                out += list(changecontrol.contributors(self.hub, state["record"]) or [])
            except Exception:  # a change order the change-control module cannot read: the recorded field stands
                pass
        return sorted({authz.principal_name(x) for x in out if x})

    def maker_may_reject(self, state: dict, by: str, by_type: str, verdict: str) -> bool:
        """Whether ``by`` is a person who made this change order (prepared it, filed it or contributed to it) rejecting
        it: then the rules that keep its makers from deciding it are set aside for that rejection (``decide`` checks
        every other exclusion again with ``excluded(makers=False)``). A rejection approves nothing and puts nothing in
        force, so it is never an approval of their own change (issue #63). Approving keeps every rule: a maker is never
        its only approver. (A revision's author is a contributor too, but is never routed the Gate at all: the routing
        leaves them off it, ``_change_order_signers``.)"""
        if verdict != "reject" or by_type != "person" or not self.is_change_order(state):
            return False
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        makers = set(self.contributors(state)) | {authz.principal_name(state["owner"])}
        if rec and rec.get("created_by"):
            makers.add(authz.principal_name(rec["created_by"]))
        return authz.principal_name(by) in makers

    def _change_order_signers(self, state: dict, out: dict, owner: str, rec: dict | None, signing: bool = True) -> None:
        """Who may sign a change order (change control 3.6, as agreed): the author of a revision it carries never; any other
        contributor (its requester and preparer included) only alongside an independent approver, a person who
        contributed nothing and approved every item of its delta (``changecontrol.may_not_sign``)."""
        contribs = set(self.contributors(state)) | {owner} | ({rec.get("created_by")} if rec and rec.get("created_by") else set())
        blocked: set[str] | None = None
        authors: set[str] = set()
        if getattr(self, "hub", None) is not None:
            try:
                from . import changecontrol

                blocked = set(changecontrol.may_not_sign(self.hub, state["record"]))
                authors = set(changecontrol.revision_authors(self.hub, state["record"]))
            except Exception:  # the change-control module cannot read it: every contributor stays off the Gate
                blocked = None
        if blocked is None:
            blocked = contribs
        said = dict(out)
        for p in list(out):
            if p in contribs and p not in blocked:
                del out[p]  # a contributor alongside an independent approver: they may sign
        for p in sorted(blocked):
            if not signing and p not in authors:
                out.pop(p, None)  # routed the Gate all the same: they sign once an independent approver has approved every item
                continue
            if p in authors:
                out[p] = "segregation of duties: they wrote a revision this change order carries, and never approve it"
            else:
                why = said.get(p) or "segregation of duties: they are a contributor of this change order"
                out[p] = (f"{why}; a contributor signs only alongside an independent approver (a person who contributed nothing "
                          "and approved every item of its delta)")

    @staticmethod
    def cog_work_needs_person(bundle: dict, obj: str) -> dict | None:
        """The segregation-of-duties sentence saying a cog's work is signed by a person (for any item, or this kind)."""
        return next((r for r in bundle.get("sod") or [] if r.get("rule") == "cog-work-signed-by-person"
                     and r.get("object") in ("*", obj)), None)

    def prepared_by_cog(self, bundle: dict, state: dict) -> str:
        """The cog that prepared or wrote this item, if any, where the policy says a cog's work is signed by a person
        (a segregation-of-duties sentence; a cog never signs its own work whatever the policy says: see ``excluded``)."""
        if not self.cog_work_needs_person(bundle, state["manifest"]["object"]):
            return ""
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        f = (rec or {}).get("fields") or {}
        for who in (authz.principal_name(state["owner"]), f.get("author"), (rec or {}).get("created_by")):
            if who and (bundle["members"].get(who) or {}).get("type") == "cog":
                return who
        return ""

    def _next_state(self, state: dict, step_id: str) -> tuple[str, str]:
        """Where approving and rejecting the decision ``step_id`` lead (the primary and the rejection transitions)."""
        wf = self._graph(state)
        t = pg.transition(wf, step_id)
        at = (t.get("from") or [None])[0] if t else (state.get("pending") or {}).get("status")
        rej = pg.rejection_option(wf, at) if at else None
        if t is not None:
            return t["to"], (rej or {}).get("to", "rejected")
        return wf.get("terminal") or "closed", "rejected"

    # ------------------------------------------------------------ the waiting decision at a status
    def _gate_dict(self, state: dict, t: dict) -> dict:
        """The Gate a decision transition puts at its status, in the shape the routing machinery reads."""
        g = t.get("gate") or {}
        d = {"type": "human", "step": t["id"], "route": {"process": state["process"], "step": t["id"]},
             "title": t.get("name") or t["id"], "role": g.get("role") or g.get("fallback") or "system"}
        if g.get("kind") == "cog":
            d.update({"cog": g.get("cog"), "fallback": g.get("fallback")})
        if g.get("kind") == "committee":
            d.update({"committee": g.get("committee"), "role": ""})
        if g.get("not"):
            d["not"] = list(g["not"])
        return d

    def _gate_step(self, state: dict, pending: dict | None = None) -> dict:
        """The step carrying the waiting Gate (its payload is what the decider sees) and the Gate itself."""
        pending = pending or state.get("pending") or {}
        sid = pending.get("step")
        step = next((x for x in state["manifest"]["steps"] if x["id"] == sid), None) or {"id": sid, "title": sid, "role": "system"}
        gate = pending.get("gate") or step.get("gate") or {}
        if not gate or gate.get("type") != "human":
            gate = {"type": "human", "step": pending.get("checkpoint"), "route": {"process": state["process"], "step": pending.get("checkpoint")},
                    "title": pending.get("title"), "role": "system"}
        return {**step, "id": sid, "gate": gate}

    def _carrier(self, state: dict, at: str) -> str:
        """The step whose result the Gate at ``at`` shows: the last action of the move that entered it (or, when that move
        had none, an empty one for the status)."""
        last = (state.get("path") or [])[-1:] or [{}]
        acts = [a for a in last[0].get("actions") or [] if a in state["steps"]] if last[0].get("to") == at else []
        if acts:
            return acts[-1]
        sid = f"at-{at}"
        state["steps"][sid] = {"status": "passed", "envelope": {"ok": True, "payload": {}, "problems": [], "error": None, "timing": {}}}
        return sid

    def _open_gate(self, state: dict, bundle: dict, track: Track, wf: dict) -> str:
        at = state["at"]
        prim = pg.primary_option(wf, at)
        carrier = self._carrier(state, at)
        state.setdefault("carriers", {})[at] = carrier
        step = {"id": carrier, "gate": self._gate_dict(state, prim)}
        outcome = self._human_gate(state, bundle, step, state["steps"][carrier]["envelope"], track)
        if outcome == "waiting":
            state["steps"][carrier]["status"] = "gate-pending"
        return outcome

    def _options(self, state: dict, bundle: dict, at: str, gate: dict) -> list[dict]:
        """The ways forward at this Gate: each gated transition out of the status. One decided by the Gate's own decider
        is taken by its assignees; one decided by someone else carries its own assignees."""
        wf = self._graph(state, bundle)
        prim = pg.transition(wf, gate.get("step")) or pg.primary_option(wf, at) or {}
        key = lambda t: {k: v for k, v in (t.get("gate") or {}).items() if k != "not"}
        out = []
        for t in pg.options(wf, at):
            o = {"id": t["id"], "name": t.get("name") or t["id"], "to": t["to"], "verdict": pg.option_verdict(wf, t),
                 "fields": list(t.get("fields") or [])}
            if key(t) != key(prim):
                o["assignees"] = self._option_assignees(state, bundle, t)
                role = (t.get("gate") or {}).get("role") or (t.get("gate") or {}).get("fallback") or ""
                o["route"] = f"field:{FIELD_ROLES[role]}" if role in FIELD_ROLES else f"role:{role}"
                o["not"] = list((t.get("gate") or {}).get("not") or [])
            out.append(o)
        return out

    def _option_assignees(self, state: dict, bundle: dict, t: dict) -> list[str]:
        """People who decide an option that is not the Gate's own: routed by a sentence naming it, else its role."""
        g = t.get("gate") or {}
        role = g.get("role") or g.get("fallback") or ""
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        obj, folder = state["manifest"]["object"], state["manifest"]["folder"]
        rules = [r for r in candidates(bundle, state["process"], t["id"], {}) if r.get("mode", "assign") == "assign"]
        people: list[str] = []
        for r in rules:
            people += principals_for(bundle, r["assignee"])
        if not rules and role in FIELD_ROLES:
            v = state["owner"] if role == "owner" else ((rec or {}).get("fields") or {}).get(FIELD_ROLES[role])
            people = [authz.principal_name(v)] if v else []
        elif not rules:
            people = principals_for(bundle, f"role:{role}")
        route = f"field:{FIELD_ROLES[role]}" if role in FIELD_ROLES else f"role:{role}"
        excluded = self.excluded(bundle, state, route, gate_not=g.get("not") or [])
        return sorted({p for p in people if (bundle["members"].get(p) or {}).get("type") == "person" and p not in excluded
                       and authz.check(bundle, p, "approve", obj, folder)["allowed"]})

    def _human_gate(self, state: dict, bundle: dict, step: dict, env: dict, track: Track) -> str:
        """Route the Gate: the first eligible candidate, a cog or people, gets the item; the others are kept as
        fallbacks for when a cog declines. A cog is a user here: it is assigned, notified, and decides through
        ``decide`` like anyone else (see ``cog_decide``)."""
        payload = env["payload"] or {}
        record = self.kernel.get(state["record"]) if state.get("record") else None
        rctx = self._routing_ctx(payload, record)
        gate = step["gate"]
        checkpoint = gate["step"]
        if gate.get("committee"):  # every member of the committee decides (consensus), not a route
            return self._committee_gate(state, bundle, step, payload, rctx, track)
        cands = [r for r in candidates(bundle, gate["route"]["process"], checkpoint, rctx) if r.get("mode", "assign") == "assign"]
        if gate.get("cog"):
            # A cog gate: the cog decides within its approval criteria, else the fallback role (or a routing sentence).
            src = self._statement_source(bundle, state["process"], checkpoint) or {
                "frame": "(process)", "line": 0, "text": f"{gate.get('title')}: the {gate['cog']} cog decides, else the {gate.get('fallback')}"}
            mine = {"assignee": f"cog:{gate['cog']}", "process": state["process"], "step": checkpoint, "condition": None, "source": src}
            cands = [mine] + [c for c in cands if c["assignee"] != mine["assignee"]]
            if gate.get("fallback") and not any(c["assignee"] == f"role:{gate['fallback']}" for c in cands):
                cands.append({**mine, "assignee": f"role:{gate['fallback']}"})
        elif not cands and gate.get("role"):
            role = gate["role"]
            kind = "field" if role in FIELD_ROLES else "role"
            src = self._statement_source(bundle, state["process"], checkpoint) or {"frame": "(process step)", "line": 0, "text": f"[{role}] {gate['title']}"}
            cands = [{"assignee": f"{kind}:{FIELD_ROLES.get(role, role)}", "process": state["process"], "step": checkpoint,
                      "condition": None, "source": src}]
        if not cands:
            self._fail(state, track, step["id"], f"No routing sentence sends “{gate.get('title', 'the approval')}” of {state['process']} to anyone",
                       kind="organisation", gate=gate.get("title", ""))
            return "failed"
        return self._pend_next(state, bundle, step, payload, rctx, track, cands)

    @staticmethod
    def _statement_source(bundle: dict, process: str, tid: str) -> dict | None:
        """The sentence that states a transition of a process written as a graph (None for numbered steps)."""
        for t in ((bundle.get("processes") or {}).get(process) or {}).get("transitions") or []:
            if t.get("id") == tid and t.get("source"):
                src = t["source"]
                return {"frame": src.get("frame"), "line": src.get("line"), "text": src.get("text")}
        return None

    def _eligible(self, state: dict, bundle: dict, step: dict, rule: dict, payload: dict, record: dict | None, track: Track) -> list[str]:
        """Who may take this route: a cog with the permission, or the people holding the role or named in the field."""
        assignee = rule["assignee"]
        kind, _, name = assignee.partition(":")
        excluded = self.excluded(bundle, state, assignee, gate_not=(step.get("gate") or {}).get("not") or [], signing=False)
        obj, folder = state["manifest"]["object"], state["manifest"]["folder"]

        def skip(reason):
            track.append({"type": "gate.skipped", "run_id": state["id"], "step": step["id"], "candidate": assignee,
                          "reason": reason, "route_rule": rule["source"]})
            return []
        if kind == "cog":
            allowed = authz.check(bundle, name, "approve", obj, folder)
            if not allowed["allowed"]:
                return skip(allowed["why"])
            if name in excluded:
                return skip(excluded[name])
            by_cog = self.prepared_by_cog(bundle, state)
            if by_cog:
                rule_ = self.cog_work_needs_person(bundle, obj) or {}
                return skip(f"{by_cog} (a cog) prepared or wrote this item: a person signs a cog's work, never another cog"
                            + (f" ({rule_['source']['frame']} line {rule_['source']['line']})" if rule_.get("source") else ""))
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
                return skip(why)
            return [name]

        dropped: list[str] = []
        if kind == "field":
            value = payload.get(name) or (record or {}).get("fields", {}).get(name)
            who = authz.principal_name(value) if value else ""
            people = [who] if who and bundle["members"].get(who) else []
            if people and bundle["members"][who]["type"] == "cog" and name != "assignee":
                # Only a task's assignee may be a cog: a cog named in requested_by, owner or reporter would sign a
                # step with no approval criteria, chosen by whoever filled the field. The auditor is the other work
                # field (the step is recording the findings, not approving them): a cog there must hold the step's own
                # role and its permission, as a person auditor does, so naming a cog gives it nothing it does not have.
                role = (step.get("gate") or {}).get("role") or next((s.get("role") for s in state["manifest"]["steps"] if s["id"] == step["id"]), "")
                why = self.work_cog_refused(bundle, state, who, name, role)
                if why:
                    return skip(why)
        else:
            people = [p for p in principals_for(bundle, assignee) if bundle["members"][p]["type"] == "person"]
            if not people:
                dropped.append(f"nobody holds the {name.replace('-', ' ')} role")
        allowed_people = []
        for p in people:
            if not authz.check(bundle, p, "approve", obj, folder)["allowed"]:
                dropped.append(f"{p} may not approve a {obj.replace('-', ' ')} in {folder}")
            elif p in excluded:
                dropped.append(f"{p}: {excluded[p]}")
            else:
                allowed_people.append(p)
        if not allowed_people:
            return skip("; ".join(dropped) or "no eligible person holds this route")
        return allowed_people

    def work_cog_refused(self, bundle: dict, state: dict, cog: str, field: str, role: str) -> str:
        """Why this cog, named in a field, may not take the step as its work (empty when it may): only a work field
        (``WORK_ROUTES``) other than the assignee's, and only a cog that holds the step's role, may approve the object
        there, follows the SOP and is available."""
        if f"field:{field}" not in self.WORK_ROUTES:
            return f"{cog} is a cog: a cog cannot be named in {field} to take this step (only a task's assignee may be a cog)"
        if not role or role not in authz.roles_of(bundle, cog):
            return f"{cog} is a cog that does not hold the {(role or 'step').replace('-', ' ')} role, so it cannot be the {field} here"
        allowed = authz.check(bundle, cog, "approve", state["manifest"]["object"], state["manifest"]["folder"])
        if not allowed["allowed"]:
            return allowed["why"]
        why = self.cog_follows(bundle, cog, state["process"])
        if why:
            return why
        from .model import cog_availability

        ok, why = cog_availability(bundle, cog)
        return "" if ok else why

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
        mark = len(track.read())
        while remaining:
            rule = remaining.pop(0)
            people = self._eligible(state, bundle, step, rule, payload, record, track)
            if not people:
                continue
            # A route to a cog, or a field naming one (a task assigned to a cog): the cog takes it from its queue.
            is_cog = rule["assignee"].startswith("cog:") or all((bundle["members"].get(p) or {}).get("type") == "cog" for p in people)
            wf = self._graph(state, bundle)
            at = state.get("at") if pg.status_of(wf, state.get("at")) else f"awaiting-{checkpoint}"
            sla = (pg.status_of(wf, at) or {}).get("sla_days") or state["workflow"].get("sla", {}).get(checkpoint)
            due = (date.today() + timedelta(days=int(sla))).isoformat() if sla and not is_cog else None
            state["pending"] = {"kind": "decision", "step": step["id"], "checkpoint": checkpoint, "assignees": people,
                                "assignee_type": "cog" if is_cog else "person", "route_rule": rule["source"], "route": rule["assignee"],
                                "payload_digest": sha256_of(payload), "title": gate.get("title", "Approve"), "due": due,
                                "remaining": remaining, "routing_ctx": rctx,
                                "gate": gate, "status": at, "transition": checkpoint, "options": self._options(state, bundle, at, gate)}
            track.append({"type": "gate.pending", "run_id": state["id"], "step": step["id"], "assignees": people,
                          "assignee_type": state["pending"]["assignee_type"], "route_rule": rule["source"], "route": rule["assignee"],
                          "payload_digest": sha256_of(payload), "routing_ctx": rctx, "due": due})
            if state.get("record"):
                try:
                    self.kernel.transition(state["record"], at, by="hub", run_id=state["id"],
                                           release=state["release"], assignees=people, due=due,
                                           reason=f"routed by {rule['source']['text']}")
                except KernelGuardError as e:
                    self._fail(state, track, step["id"], str(e))
                    return "failed"
            self._notify(people, kind="approval", subject=f"{gate.get('title', 'Approve')}: {state['process']} {state.get('record') or state['id']}",
                         body=f"Routed by {rule['source']['frame']} line {rule['source']['line']}: {rule['source']['text']}"
                              + (f" Due {due}." if due else ""), run_id=state["id"])
            return "waiting"
        why = []
        for ev in track.read()[mark:]:
            if ev["type"] == "gate.skipped" and ev.get("step") == step["id"]:
                who = str(ev.get("candidate") or "").partition(":")
                label = f"the {who[2]} cog" if who[0] == "cog" else (who[2].replace("-", " ") if who[2] else "the route")
                why.append(f"{label}: {ev.get('reason')}")
        for ev in reversed(track.read()):
            if ev["type"] == "gate.declined" and ev.get("step") == step["id"]:
                why.insert(0, f"{ev.get('by')} declined: {'; '.join(ev.get('reasons') or []) or 'its criteria do not hold'}")
                break
        title = gate.get("title", "the approval")
        self._fail(state, track, step["id"], f"Nobody can take “{title}”" + (": " + " | ".join(why) if why else ""),
                   kind="organisation", gate=title)
        return "failed"

    # ------------------------------------------------------------ committees (change control 11): consensus at a Gate
    # A status whose decision is "the <name> committee decides" waits for every member of the committee (people and
    # cogs): each vote is a signature with its meaning (agree, or object with a reason). The item moves when every member
    # agrees on the same way forward; one objection returns it for revision at once. There is no standing aside. When a
    # cog member's approval criteria do not hold (or it is unavailable, or may not decide this item), or a member has not
    # answered by the due date, the decision pauses and the chair intervenes: decides in that member's place (recorded as
    # an intervention with its meaning), extends the due date, or returns the item. If the chair is the member in question
    # or absent, the pause escalates under the SOP's overdue rule. Nothing passes by default. The tally is on the card
    # (``pending["committee"]``).
    COMMITTEE_PAUSES = {"not-ready": "the change order is not ready to be approved", "criteria": "its approval criteria do not hold", "unavailable": "is not available",
                        "excluded": "may not decide this item", "late": "has not answered by the due date",
                        "differ": "the members chose different ways forward"}

    @staticmethod
    def committee_outstanding(pending: dict) -> list[str]:
        """The members of the committee deciding here who have not voted yet."""
        cm = (pending or {}).get("committee") or {}
        return [m for m in cm.get("members") or [] if m not in (cm.get("votes") or {})]

    @staticmethod
    def _paused(cm: dict, member: str) -> dict | None:
        return next((p for p in cm.get("paused") or [] if p.get("member") == member), None)

    def committee_options(self, state: dict, at: str | None = None) -> list[dict]:
        """The ways the committee deciding at the item's status may take: the forward ones (agreement) and the way back
        (an objection)."""
        wf = self._graph(state)
        name = (((state.get("pending") or {}).get("committee") or {}).get("name"))
        return [t for t in pg.options(wf, at or state.get("at")) if (t.get("gate") or {}).get("kind") == "committee"
                and (name is None or t["gate"].get("committee") == name)]

    def _committee_ways(self, state: dict) -> tuple[dict | None, dict | None]:
        """(the forward option an agreement takes by default, the option an objection takes)."""
        wf = self._graph(state)
        opts = self.committee_options(state)
        fwd = next((t for t in opts if pg.option_verdict(wf, t) == "approve"), None)
        back = next((t for t in opts if pg.option_verdict(wf, t) == "reject"), None) or pg.rejection_option(wf, state.get("at"))
        return fwd, back

    def _committee_assignees(self, state: dict) -> list[str]:
        """Who the item waits for now: the members still to vote (a paused cog waits for the chair, not itself) and, while
        the decision is paused, whoever intervenes."""
        pending = state["pending"]
        cm = pending["committee"]
        out = [m for m in self.committee_outstanding(pending)
               if not (cm.get("types", {}).get(m) == "cog" and self._paused(cm, m))]
        if cm.get("paused"):
            out += [p for p in cm.get("interveners") or [] if p not in out]
        return out

    def _committee_gate(self, state: dict, bundle: dict, step: dict, payload: dict, rctx: dict, track: Track) -> str:
        """Open the committee's decision at the item's status: every member is asked; the due date is the status's."""
        from .groups import committee as _committee

        gate = step["gate"]
        checkpoint = gate["step"]
        name = gate["committee"]
        title = gate.get("title") or "Decide"
        c = _committee(bundle, name)
        if not c or not c.get("members"):
            self._fail(state, track, step["id"], f"“{title}” is decided by the {name} committee, which "
                       + ("the release does not define" if not c else "has nobody in it"), kind="organisation", gate=title)
            return "failed"
        wf = self._graph(state, bundle)
        at = state.get("at")
        sla = (pg.status_of(wf, at) or {}).get("sla_days")
        due = (date.today() + timedelta(days=int(sla))).isoformat() if sla else None
        src = self._statement_source(bundle, state["process"], checkpoint) or {
            "frame": "(process)", "line": 0, "text": f"{title}: the {name} committee decides"}
        members = list(c["members"])
        cm = {"name": name, "group": c.get("group") or "", "chair": c.get("chair") or "", "purpose": c.get("purpose") or "",
              "members": members, "types": {m: (bundle["members"].get(m) or {}).get("type", "person") for m in members},
              "votes": {}, "paused": [], "interveners": [], "due": due, "opened": utcnow()}
        state["pending"] = {"kind": "decision", "step": step["id"], "checkpoint": checkpoint, "assignees": list(members),
                            "assignee_type": "committee", "route_rule": src, "route": f"committee:{name}",
                            "payload_digest": sha256_of(payload), "title": title, "due": due, "remaining": [], "routing_ctx": rctx,
                            "gate": gate, "status": at, "transition": checkpoint, "committee": cm,
                            "options": self._options(state, bundle, at, gate)}
        track.append({"type": "gate.pending", "run_id": state["id"], "step": step["id"], "assignees": members, "assignee_type": "committee",
                      "committee": name, "chair": cm["chair"], "route_rule": src, "route": f"committee:{name}",
                      "payload_digest": sha256_of(payload), "routing_ctx": rctx, "due": due})
        if state.get("record"):
            try:
                self.kernel.transition(state["record"], at, by="hub", run_id=state["id"], release=state["release"], assignees=members, due=due,
                                       reason=f"the {name} committee decides ({src.get('text')})")
            except KernelGuardError as e:
                self._fail(state, track, step["id"], str(e))
                return "failed"
        people = [m for m in members if cm["types"][m] == "person"]
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        self._notify(people, kind="approval", subject=f"{title}: your vote on {(rec or {}).get('number') or state['process']} ({name} committee)",
                     body=(f"Your vote: the {name} committee decides by consensus; one reasoned objection sends it back. Agree to a way "
                           "forward, or object with your reason; there is no standing aside. Routed by {src.get('frame')} line {src.get('line')}: {src.get('text')}" + (f" Due {due}." if due else "")),
                     run_id=state["id"])
        # A member who may not decide this item (it prepared it, a segregation sentence keeps it out) is a pause at once.
        excluded = self.excluded(bundle, state, f"committee:{name}")
        for m in members:
            if m in excluded:
                self._committee_pause(state, track, bundle, m, "excluded", excluded[m], save=False)
                if state["status"] == "failed" or not state.get("pending"):
                    return "failed"
        state["pending"]["assignees"] = self._committee_assignees(state)
        return "waiting"

    def _committee_cogs(self, state: dict) -> dict:
        """Each cog member that has not voted takes its turn (from its queue; inline, at once)."""
        pending = state.get("pending") or {}
        cm = pending.get("committee")
        if not cm or not self.cog_users_inline:
            return state
        for cog in [m for m in self.committee_outstanding(pending) if cm.get("types", {}).get(m) == "cog" and not self._paused(cm, m)]:
            if self.queue is None:
                self.committee_cog_vote(state["id"], cog)
            else:
                rec = state.get("record")
                self.queue.submit(cog, "gate", by=state.get("owner") or "hub", title=f"Vote ({cm['name']} committee): {pending.get('title') or 'Decide'}: {rec or state['process']}",
                                  record=rec, payload={"run": state["id"], "step": pending.get("step")}, key=f"gate:{state['id']}:{pending.get('step')}:{cog}")
            state = self.load(state["id"])
            if state["status"] != "waiting" or not (state.get("pending") or {}).get("committee"):
                break
        return self.load(state["id"])

    def committee_cog_vote(self, run_id: str, cog_name: str) -> dict:
        """A cog member of the committee votes: it agrees where its approval criteria hold (its review, as at any Gate);
        otherwise the decision pauses for the chair, naming the cog and the reason. A cog never objects on its own
        judgment alone: a person decides what its findings mean."""
        state = self.load(run_id)
        pending = state.get("pending") or {}
        cm = pending.get("committee") or {}
        if state["status"] != "waiting" or cog_name not in self.committee_outstanding(pending) or self._paused(cm, cog_name):
            return state
        track = Track(self._dir(state["id"]))
        bundle = self.releases.bundle(state["release"])
        from .model import cog_availability
        from .routing import criteria_for

        if self.is_change_order(state):
            return self._committee_pause(state, track, bundle, cog_name, "excluded", self.PERSON_SIGNS_CONFIGURATION)
        ok, why = cog_availability(bundle, cog_name)
        if not ok:
            return self._committee_pause(state, track, bundle, cog_name, "unavailable", why)
        excluded = self.excluded(bundle, state, pending.get("route") or "role:")
        if cog_name in excluded:
            return self._committee_pause(state, track, bundle, cog_name, "excluded", excluded[cog_name])
        by_cog = self.prepared_by_cog(bundle, state)
        if by_cog:
            return self._committee_pause(state, track, bundle, cog_name, "excluded",
                                         f"{by_cog} (a cog) prepared or wrote this item: a person signs a cog's work, never another cog")
        why = self.cog_follows(bundle, cog_name, state["process"])
        if why:
            return self._committee_pause(state, track, bundle, cog_name, "criteria", why)
        if not criteria_for(bundle, f"cog:{cog_name}", state["process"]):
            return self._committee_pause(state, track, bundle, cog_name, "criteria",
                                         f"no approval criteria let {cog_name} agree to {state['process']} items")
        if cog_name not in self.registry.ids():
            return self._committee_pause(state, track, bundle, cog_name, "unavailable", f"{cog_name} is not installed in this hub")
        step = self._gate_step(state, pending)
        cog = self.registry.get(cog_name)
        env = state["steps"].get(step["id"], {}).get("envelope") or {}
        seen = self.seen_digest(state)
        review = cog.invoke("review", {"payload": env.get("payload") or {}, "process": state["process"], "routing_ctx": pending.get("routing_ctx") or {}},
                            self._ctx(state, bundle, cog))
        rp = review.get("payload") or {}
        verdict = rp.get("verdict", "escalate")
        reasons = list(rp.get("reasons") or [])
        track.append({"type": "gate.cog_review", "run_id": state["id"], "step": step["id"], "cog": cog_name, "cog_version": cog.version,
                      "verdict": verdict, "reasons": reasons, "seen": seen, "judged": bool(rp.get("judged")), "route_rule": pending.get("route_rule"),
                      "delegating_rule": rp.get("delegating_rule"), "committee": cm.get("name")})
        if verdict == "reviewing":
            track.append({"type": "gate.reviewing", "run_id": state["id"], "step": step["id"], "cog": cog_name,
                          "lenses": list(rp.get("lenses") or []), "reasons": reasons})
            return self.load(run_id)
        state = self.load(run_id)  # its review may have filed evidence on the item: what it agrees to is the item now
        if verdict == "approve":
            fwd, _ = self._committee_ways(state)
            basis = "on its review, under its approval criteria" if rp.get("judged") else "within its approval criteria"
            return self._committee_vote(state, track, cog_name, "agree", f"agrees {basis}: " + "; ".join(reasons) if reasons else f"agrees {basis}",
                                        self.seen_digest(state), (fwd or {}).get("id"), delegating_rule=rp.get("delegating_rule"))
        return self._committee_pause(state, track, bundle, cog_name, "criteria", "; ".join(reasons) or self.COMMITTEE_PAUSES["criteria"])

    def _person_vote(self, state: dict, pending: dict, by: str, verdict: str | None, meaning: str, payload_digest: str | None,
                     transition: str | None, fields: dict | None) -> dict:
        """A person member's vote: agree (the forward way, or the one named) or object (the way back) with a reason."""
        cm = pending["committee"]
        bundle = self.releases.bundle(state["release"])
        wf = self._graph(state)
        if by not in self.committee_outstanding(pending):
            raise RunError(f"{by} has already voted on this item")
        excluded = self.excluded(bundle, state, pending.get("route") or "role:")
        if by in excluded:
            raise RunError(f"{excluded[by]}; {by} cannot vote on it (the chair decides in their place)")
        if not payload_digest:
            raise RunError("a vote must quote what you saw: pass the run's seen_digest (shown on the card and by inbox-pending)")
        if payload_digest != self.seen_digest(state):
            raise RunError("the item changed since you opened it (its fields, links or documents differ); open it again and vote on what it is now")
        if fields:
            raise RunError("a committee member's vote sets no fields; set them on the item first, then vote")
        ways = self.committee_options(state)
        if transition:
            option = next((t for t in ways if t["id"] == transition), None)
            if option is None:
                raise RunError(f"{transition!r} is not a way the {cm['name']} committee decides here")
        else:
            if verdict not in ("approve", "reject"):
                raise RunError("verdict must be approve (agree) or reject (object)")
            fwd, back = self._committee_ways(state)
            option = fwd if verdict == "approve" else back
            if option is None:
                raise RunError(f"the {cm['name']} committee has no way to {'agree' if verdict == 'approve' else 'object'} here")
        vote = "agree" if pg.option_verdict(wf, option) == "approve" else "object"
        if not meaning.strip():
            raise RunError("an objection needs its reason: say what must change for you to agree" if vote == "object" else
                           "a vote needs its meaning: say in a few words what you agree to")
        if vote == "agree":
            why = self._validators_block(state, wf, option)
            if why:
                raise RunError(why)
            blocked = self.change_order_blocks(state)
            if blocked:
                raise RunError("the change order cannot be approved yet: " + "; ".join(blocked))
        return self._committee_vote(state, Track(self._dir(state["id"])), by, vote, meaning.strip(), payload_digest, option["id"])

    def _committee_vote(self, state: dict, track: Track, member: str, verdict: str, meaning: str, seen: str, option: str | None,
                        *, by: str | None = None, intervention: dict | None = None, delegating_rule=None) -> dict:
        """Record one member's vote (signed), then see where the committee stands."""
        pending = state["pending"]
        cm = pending["committee"]
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        signer = by or member
        vote = {"by": signer, "member": member, "verdict": verdict, "option": option, "meaning": meaning, "at": utcnow(), "seen": seen,
                "payload_digest": pending["payload_digest"], "record_digest": self.record_digest(rec) if rec else None,
                "committee": cm["name"], "step": pending.get("checkpoint"), "run_id": state["id"], "release_digest": state["release"],
                "record": state.get("record"), "by_type": "person" if (intervention or cm.get("types", {}).get(member) != "cog") else "cog"}
        if intervention:
            vote["intervention"] = intervention
        if delegating_rule:
            vote["delegating_rule"] = delegating_rule
        vote["signature"] = sign(self.secret(), vote)
        cm.setdefault("votes", {})[member] = vote
        cm["paused"] = [p for p in cm.get("paused") or [] if p.get("member") not in (member, None)]
        track.append({"type": "gate.vote", "run_id": state["id"], "step": pending["step"], **vote})
        if verdict == "object":
            return self._committee_conclude(state, track, "object", vote)
        return self._committee_tally(state, track)

    def _committee_tally(self, state: dict, track: Track) -> dict:
        """Where the committee stands after a vote: votes given on an earlier version of the item no longer count; when
        every member has agreed on one way forward, the item moves; members who chose different ways forward pause the
        decision for the chair."""
        pending = state["pending"]
        cm = pending["committee"]
        seen = self.seen_digest(state)
        for m, v in list((cm.get("votes") or {}).items()):
            if v.get("seen") != seen:
                del cm["votes"][m]
                track.append({"type": "gate.vote_voided", "run_id": state["id"], "step": pending["step"], "member": m,
                              "reason": "the item changed after this vote; the member votes again on what it is now"})
                if cm.get("types", {}).get(m) == "person":
                    self._notify([m], kind="approval", subject=f"Vote again: {pending.get('title')} ({cm['name']} committee)",
                                 body="The item changed after you voted; your vote no longer counts. Vote again on what it is now.", run_id=state["id"])
        votes = cm.get("votes") or {}
        if all(m in votes for m in cm["members"]):
            ways = sorted({v.get("option") for v in votes.values()}, key=str)
            if len(ways) == 1:
                return self._committee_conclude(state, track, "agree")
            bundle = self.releases.bundle(state["release"])
            said = "; ".join(f"{m}: {v.get('option')}" for m, v in votes.items())
            return self._committee_pause(state, track, bundle, None, "differ", f"the members chose different ways forward ({said})")
        pending["assignees"] = self._committee_assignees(state)
        self._save(state)
        if state.get("record"):
            self.kernel.assign(state["record"], list(pending["assignees"]), by="hub", run_id=state["id"], release=state["release"])
        return self.load(state["id"])

    def _committee_conclude(self, state: dict, track: Track, how: str, vote: dict | None = None) -> dict:
        """The committee's decision: every member agreed (``agree``), a member objected (``object``) or the chair returned
        it (``return``). Signed as the committee's, with every vote, and the item moves."""
        pending = state["pending"]
        cm = pending["committee"]
        self._follow_release(state, track, "a committee decision")  # the decision is a move (change control 5.1)
        wf = self._graph(state)
        votes = [cm["votes"][m] for m in cm["members"] if m in (cm.get("votes") or {})]
        fwd, back = self._committee_ways(state)
        if how == "agree":
            blocked = self.change_order_blocks(state)
            if blocked:  # every member agreed, but the change order is not ready: nothing passes; the chair is asked
                if any(p.get("kind") == "not-ready" for p in cm.get("paused") or []):
                    return self.load(state["id"])
                return self._committee_pause(state, track, self.releases.bundle(state["release"]), None, "not-ready",
                                             "the change order cannot be approved yet: " + "; ".join(blocked))
            option = pg.transition(wf, votes[0].get("option")) or fwd
            verdict = "approve"
            meaning = (f"agreed by consensus of the {cm['name']} committee ({', '.join(cm['members'])}): "
                       + "; ".join(f"{v['member']}: {v['meaning']}" for v in votes))
        else:
            option = (pg.transition(wf, (vote or {}).get("option")) if how == "object" else None) or back
            if option is not None and pg.option_verdict(wf, option) != "reject":
                option = back
            verdict = "reject"
            who = (vote or {}).get("member") or (vote or {}).get("by") or "the chair"
            meaning = (f"returned for revision by the {cm['name']} committee: {who} objects: {(vote or {}).get('meaning')}" if how == "object"
                       else f"returned for revision by {who}, chair of the {cm['name']} committee: {(vote or {}).get('meaning')}")
            if option is None:
                self._fail(state, track, pending["step"], f"{meaning}; but the process has no way back from {(pg.status_of(wf, state.get('at')) or {}).get('name')}",
                           kind="organisation", gate=pending.get("title") or "")
                return self.load(state["id"])
        step = self._gate_step(state, pending)
        if verdict == "approve" and self.is_change_order(state):
            # A person signs a change order: the committee's approval is its people's signatures (every vote a person's;
            # a cog member was paused for the chair), recorded as theirs so the release accepts it.
            people = [v for v in votes if v.get("by_type") == "person"]
            if len(people) != len(votes) or not people:
                return self._committee_pause(state, track, self.releases.bundle(state["release"]), None, "excluded", self.PERSON_SIGNS_CONFIGURATION)
            chair = cm.get("chair")
            lead = next((v for v in people if v["by"] == chair), people[0])
            for v in people:
                if v is not lead and state.get("record"):
                    self.kernel.append("record.decision", state["record"], {**v, "verdict": "approve", "step": pending.get("checkpoint"),
                                                                             "committee": cm["name"], "cosigns": lead["by"]},
                                       run_id=state["id"], release=state["release"], by=v["by"])
            self._record_decision(state, step, track, by=lead["by"], by_type="person", verdict=verdict, meaning=meaning,
                                  payload_digest=pending["payload_digest"], meaning_given=True, option=option,
                                  extra={"committee": cm["name"], "votes": votes, "voters": [v["member"] for v in votes],
                                         "cosigned_by": [v["by"] for v in people if v is not lead]})
            self._save(state)
            result = self.advance(state["id"]) if state.get("moving") else self.load(state["id"])
            return self._committee_cogs(result) if (result.get("pending") or {}).get("committee") else result
        self._record_decision(state, step, track, by=f"committee:{cm['name']}", by_type="committee", verdict=verdict, meaning=meaning,
                              payload_digest=pending["payload_digest"], meaning_given=True, option=option,
                              extra={"committee": cm["name"], "votes": votes, "voters": [v["member"] for v in votes],
                                     **({"objection": vote} if how == "object" else {}), **({"returned_by": vote.get("by")} if how == "return" and vote else {})})
        self._save(state)
        result = self.advance(state["id"]) if state.get("moving") else self.load(state["id"])
        owner = authz.principal_name(state["owner"])
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        bundle = self.releases.bundle(state["release"])
        if (bundle["members"].get(owner) or {}).get("type") == "person":
            nxt = result.get("pending") or {}
            where = (f"it now waits for {', '.join(nxt.get('assignees') or [])} at {nxt.get('title', 'the next step')}" if result["status"] == "waiting"
                     else f"it is now {(rec or {}).get('state', result['status']).replace('-', ' ')}")
            self._notify([owner], kind="moved" if verdict == "approve" else "returned",
                         subject=f"The {cm['name']} committee {'agreed to' if verdict == 'approve' else 'returned'} {pending.get('title', 'a step')} on {(rec or {}).get('number') or state['process']}",
                         body=f"{meaning}. {where[0].upper() + where[1:]}.", run_id=state["id"])
        return self._committee_cogs(result) if (result.get("pending") or {}).get("committee") else result

    def _interveners(self, state: dict, bundle: dict, cm: dict, member: str | None) -> tuple[list[str], str]:
        """Who intervenes in a paused committee decision, and why them: the chair; or, when the chair is the member in
        question or absent (not a person of the organization now, or kept out of this item), the people the SOP's overdue
        rule sends overdue items of this process to."""
        from .routing import candidates

        chair = cm.get("chair") or ""
        excluded = self.excluded(bundle, state, "role:")
        why = ""
        if not chair or (bundle["members"].get(chair) or {}).get("type") != "person":
            why = f"the chair{(' ' + chair) if chair else ''} is absent (not a person of the organization)"
        elif chair == member:
            why = f"the chair, {chair}, is the member in question"
        elif chair in excluded:
            why = f"the chair, {chair}, may not decide this item ({excluded[chair]})"
        else:
            return [chair], "the chair"
        rules = [r for r in candidates(bundle, state["process"], "*", {"overdue": True}) if r["step"] == "*" and r.get("mode", "assign") == "assign"]
        people = []
        for r in rules:
            people += [p for p in principals_for(bundle, r["assignee"]) if (bundle["members"].get(p) or {}).get("type") == "person"
                       and p != member and p not in excluded and p not in people]
        cite = f" ({rules[0]['source']['text']})" if rules else ""
        return people, f"{why}: escalated under the overdue rule{cite}"

    def _committee_pause(self, state: dict, track: Track, bundle: dict, member: str | None, kind: str, reason: str, save: bool = True) -> dict:
        """The committee cannot decide without a person: the chair (or, failing them, the overdue route) is asked to
        intervene, naming the member and the reason. Nothing passes meanwhile."""
        pending = state["pending"]
        cm = pending["committee"]
        if member is not None and self._paused(cm, member):
            return self.load(state["id"]) if save else state
        people, who = self._interveners(state, bundle, cm, member)
        title = pending.get("title") or "the decision"
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        number = (rec or {}).get("number") or state["process"]
        what = f"{member} {reason}" if member and kind != "differ" else reason
        if not people:
            self._fail(state, track, pending["step"], f"The {cm['name']} committee cannot decide “{title}”: {what}; and nobody can intervene ({who}; "
                       "no overdue rule names anyone)", kind="organisation", gate=title)
            return self.load(state["id"])
        pause = {"member": member, "kind": kind, "reason": reason, "at": utcnow(), "interveners": people, "who": who}
        cm.setdefault("paused", []).append(pause)
        cm["interveners"] = sorted(set(cm.get("interveners") or []) | set(people))
        track.append({"type": "gate.paused", "run_id": state["id"], "step": pending["step"], "committee": cm["name"], **pause})
        pending["assignees"] = self._committee_assignees(state)
        if state.get("record"):
            self.kernel.assign(state["record"], list(pending["assignees"]), by="hub", run_id=state["id"], release=state["release"])
        self._notify(people, kind="intervention", subject=f"Intervene: {title} on {number} ({cm['name']} committee): {what}",
                     body=(f"The {cm['name']} committee's decision on {number} is paused: {what}. You are asked as {who}. You may decide in "
                           + (f"{member}'s place (with your reason)" if member and kind != "differ" else "the members' place")
                           + ", extend the due date, or return the item for revision. Nothing passes by default."), run_id=state["id"])
        if save:
            self._save(state)
            return self.load(state["id"])
        return state

    def intervene(self, run_id: str, by: str, action: str, *, member: str | None = None, meaning: str = "", verdict: str | None = None,
                  transition: str | None = None, due: str | None = None, payload_digest: str | None = None) -> dict:
        """The chair (or whoever the pause escalated to) intervenes in a paused committee decision. ``action``:
        ``decide`` (in ``member``'s place: ``verdict`` approve/agree or reject/object, or ``transition``; recorded as an
        intervention with its meaning), ``extend`` (a new ``due`` date; the late members, and the paused cogs, are asked
        again) or ``return`` (the item goes back for revision). Every intervention needs its meaning."""
        state = self.load(run_id)
        by = authz.principal_name(by)
        pending = state.get("pending") or {}
        cm = pending.get("committee")
        if state["status"] != "waiting" or not cm:
            raise RunError(f"run {run_id} is not waiting for a committee")
        if cm.get("due") and cm["due"] < date.today().isoformat() and any(not self._paused(cm, m) for m in self.committee_outstanding(pending)):
            # Past the due date: a member who has not responded pauses it now, as the tick would (people and cogs alike).
            self._tick_committee(state, date.today().isoformat())
            state = self.load(run_id)
            pending = state.get("pending") or {}
            cm = pending.get("committee")
            if state["status"] != "waiting" or not cm:
                raise RunError(f"run {run_id} is not waiting for a committee")
        if not cm.get("paused"):
            raise RunError(f"the {cm['name']} committee's decision is not paused: its members are still voting")
        if by not in (cm.get("interveners") or []):
            whom = sorted(set(p for x in cm["paused"] for p in x.get("interveners") or []))
            raise RunError(f"only the chair of the {cm['name']} committee ({cm.get('chair') or 'none'}) intervenes"
                           + (f"; this pause was escalated to {', '.join(whom)}" if whom and whom != [cm.get("chair")] else ""))
        if not (meaning or "").strip():
            raise RunError("an intervention needs its meaning: say why you intervene")
        bundle = self.releases.bundle(state["release"])
        if by in self.excluded(bundle, state, "role:") and action != "extend":
            raise RunError(f"{by} may not decide this item; it is escalated under the overdue rule")
        track = Track(self._dir(state["id"]))
        meaning = meaning.strip()
        if action == "decide":
            pause = self._paused(cm, member) if member else next((p for p in cm["paused"] if p.get("member") is None), None)
            if member and pause is None:
                raise RunError(f"{member} is not a member the decision is paused on")
            if payload_digest != self.seen_digest(state):
                raise RunError("the item changed since you opened it; open it again and decide on what it is now")
            wf = self._graph(state)
            if transition:
                option = next((t for t in self.committee_options(state) if t["id"] == transition), None)
            else:
                fwd, back = self._committee_ways(state)
                option = fwd if verdict in ("approve", "agree") else back if verdict in ("reject", "object") else None
            if option is None:
                raise RunError("say how you decide: approve (agree) or reject (object), or a way the committee decides here")
            vote = "agree" if pg.option_verdict(wf, option) == "approve" else "object"
            if vote == "agree":
                why = self._validators_block(state, wf, option)
                if why:
                    raise RunError(why)
                blocked = self.change_order_blocks(state)
                if blocked:
                    raise RunError("the change order cannot be approved yet: " + "; ".join(blocked))
            members = [member] if member else [m for m in cm["members"]]
            intervention = {"by": by, "action": "decide", "meaning": meaning, "for": member, "pause": pause, "at": utcnow()}
            track.append({"type": "gate.intervened", "run_id": state["id"], "step": pending["step"], **intervention})
            if not member:  # the members chose different ways forward: the chair's choice is the committee's
                cm["paused"] = [p for p in cm["paused"] if p.get("member") is not None]
                for m in members:
                    cm["votes"][m] = {**cm["votes"].get(m, {}), "option": option["id"]}
                return self._committee_conclude(state, track, "agree" if vote == "agree" else "return",
                                                None if vote == "agree" else {"by": by, "meaning": meaning})
            return self._committee_vote(state, track, member, vote, f"decided by {by} in {member}'s place: {meaning}", self.seen_digest(state),
                                        option["id"], by=by, intervention=intervention)
        if action == "extend":
            if not due or due <= date.today().isoformat():
                raise RunError("say the new due date (YYYY-MM-DD), after today")
            again = [p for p in cm["paused"] if p.get("kind") in ("late", "unavailable", "criteria") and (member is None or p.get("member") == member)]
            cm["paused"] = [p for p in cm["paused"] if p not in again]
            cm["due"] = due
            pending["due"] = due
            pending.pop("escalated", None)
            if not cm["paused"]:
                cm["interveners"] = []
            track.append({"type": "gate.intervened", "run_id": state["id"], "step": pending["step"], "by": by, "action": "extend", "due": due,
                          "meaning": meaning, "for": member, "asked_again": [p.get("member") for p in again], "at": utcnow()})
            pending["assignees"] = self._committee_assignees(state)
            if state.get("record"):
                self.kernel.assign(state["record"], list(pending["assignees"]), by=by, due=due, run_id=state["id"], release=state["release"])
            people = [p.get("member") for p in again if p.get("member") and cm.get("types", {}).get(p.get("member")) == "person"]
            self._notify(people, kind="approval", subject=f"{pending.get('title')}: your vote is due {due} ({cm['name']} committee)",
                         body=f"{by} extended the due date: {meaning}", run_id=state["id"])
            self._save(state)
            return self._committee_cogs(self.load(state["id"]))
        if action == "return":
            track.append({"type": "gate.intervened", "run_id": state["id"], "step": pending["step"], "by": by, "action": "return",
                          "meaning": meaning, "for": member, "at": utcnow()})
            return self._committee_conclude(state, track, "return", {"by": by, "meaning": meaning})
        raise RunError(f"{action!r} is not an intervention (decide, extend or return)")

    def _tick_committee(self, state: dict, today: str) -> dict | None:
        """A committee decision past its due date: each member who has not answered pauses it for the chair; one already
        paused past its due date escalates under the overdue rule."""
        pending = state["pending"]
        cm = pending["committee"]
        track = Track(self._dir(state["id"]))
        bundle = self.releases.bundle(state["release"])
        late = [m for m in self.committee_outstanding(pending) if not self._paused(cm, m)]
        if late:
            for m in late:
                self._committee_pause(state, track, bundle, m, "late", f"has not answered by {pending['due']}", save=False)
                if state.get("status") == "failed" or not state.get("pending"):
                    return {"run": state["id"], "record": state.get("record"), "to": [], "informed": [], "paused": late}
            self._save(state)
            return {"run": state["id"], "record": state.get("record"), "to": list(cm.get("interveners") or []), "informed": [], "paused": late}
        if cm.get("paused") and not pending.get("escalated"):
            from .routing import candidates

            rules = [r for r in candidates(bundle, state["process"], "*", {"overdue": True}) if r["step"] == "*" and r.get("mode", "assign") == "assign"]
            excluded = self.excluded(bundle, state, "role:")
            added = []
            for r in rules:
                added += [p for p in principals_for(bundle, r["assignee"]) if (bundle["members"].get(p) or {}).get("type") == "person"
                          and p not in excluded and p not in cm.get("interveners", []) and p not in added]
            pending["escalated"] = today
            cm["interveners"] = sorted(set(cm.get("interveners") or []) | set(added))
            pending["assignees"] = self._committee_assignees(state)
            track.append({"type": "gate.escalated" if added else "gate.overdue", "run_id": state["id"], "step": pending["step"], "assignees": added,
                          "informed": sorted(set(pending["assignees"])), "due": pending.get("due"), "committee": cm["name"],
                          "route_rule": rules[0]["source"] if rules else None})
            self._notify(sorted(set(added + pending["assignees"])), kind="escalation", subject=f"Overdue: {pending['title']} on {state.get('record')}",
                         body=f"The {cm['name']} committee's decision is paused past its due date {pending.get('due')}"
                              + (f"; escalated by {rules[0]['source']['text']}" if rules else "; no escalation route"), run_id=state["id"])
            if state.get("record"):
                self.kernel.assign(state["record"], list(pending["assignees"]), by="hub", run_id=state["id"], release=state["release"])
            self._save(state)
            return {"run": state["id"], "record": state.get("record"), "to": added, "informed": [], "paused": []}
        return None

    # ------------------------------------------------------------ cogs as users
    cog_users_inline = True  # False: an item waiting for a cog is not put in its queue (a sweep gives it later)
    queue = None  # the cogs' work queue (hubproto.workqueue.CogQueue), set by the Hub
    on_completed: list = []  # callables (state, record) run after a run completes: automation triggers, set by the Hub

    def give_to_cog(self, state: dict) -> dict:
        """An item now waits for a cog: it goes in that cog's queue, like a task in a person's tray. In inline mode the
        cog takes its turn before this returns; otherwise the item waits until the cog gets to it."""
        pending = state.get("pending") or {}
        if pending.get("committee"):
            return self._committee_cogs(state)
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
        if pending.get("committee") and state["status"] == "waiting":
            return self.committee_cog_vote(run_id, cog_name)  # a member's vote, not the Gate's decision
        if state["status"] != "waiting" or cog_name not in pending.get("assignees", []):
            raise RunError(f"run {run_id} is not waiting for {cog_name}")
        if self._follow_release(state, Track(self._dir(state["id"])), "a cog's turn"):
            self._save(state)
        bundle = self.releases.bundle(state["release"])
        from .model import cog_availability

        ok, why = cog_availability(bundle, cog_name)
        if not ok:
            return self._decline(run_id, cog_name, [why])
        step = self._gate_step(state, pending)
        track = Track(self._dir(state["id"]))
        cog = self.registry.get(cog_name)
        env = state["steps"].get(step["id"], {}).get("envelope") or {}
        payload = env.get("payload") or {}
        # What the cog is shown, fixed before it starts: if the item changes while its model thinks, its decision no
        # longer matches and is refused (the item goes on to people), exactly as for a person with a stale card.
        seen = self.seen_digest(state)
        review = cog.invoke("review", {"payload": payload, "process": state["process"], "routing_ctx": pending.get("routing_ctx") or {}},
                            self._ctx(state, bundle, cog))
        rp = review.get("payload") or {}
        verdict = rp.get("verdict", "escalate")
        reasons = list(rp.get("reasons") or [])
        track.append({"type": "gate.cog_review", "run_id": state["id"], "step": step["id"], "cog": cog_name,
                      "cog_version": cog.version, "verdict": verdict, "reasons": reasons, "seen": seen, "judged": bool(rp.get("judged")),
                      "route_rule": pending["route_rule"], "delegating_rule": rp.get("delegating_rule")})
        if verdict == "reviewing":
            # Its adversarial reviews are in its queue: the item keeps waiting for it, and it takes its turn again when they are in.
            track.append({"type": "gate.reviewing", "run_id": state["id"], "step": step["id"], "cog": cog_name,
                          "lenses": list(rp.get("lenses") or []), "reasons": reasons})
            return self.load(run_id)
        if verdict in ("approve", "reject"):
            basis = "on its review, under its approval criteria" if rp.get("judged") else "within fixed criteria"
            meaning = f"{verdict}d {basis}: " + "; ".join(reasons) if reasons else f"{verdict}d {basis}"
            try:
                # A cog may fill in fields at a Gate its role may set (change control 7), as a person does.
                return self.decide(run_id, cog_name, verdict, meaning=meaning, payload_digest=seen,
                                   delegating_rule=rp.get("delegating_rule"), countersign=rp.get("countersign"),
                                   evidence=rp.get("evidence"), fields=rp.get("fields") or None, transition=rp.get("transition"))
            except RunError as e:
                reasons = [str(e)]
        return self._decline(run_id, cog_name, reasons)

    def cog_lens(self, run_id: str, cog_name: str, lens: str, i: int, n: int, lenses: list[str]) -> dict:
        """One adversarial review (a lens) for an item waiting for the cog. When the last lens is in, or one fails, the
        cog takes its turn at the Gate again (from its queue) and signs or declines on what the lenses found."""
        state = self.load(run_id)
        bundle = self.releases.bundle(state["release"])
        cog = self.registry.get(cog_name)
        env = cog.invoke("lens", {"lens": lens, "i": i, "n": n, "lenses": lenses}, self._ctx(state, bundle, cog))
        p = env.get("payload") or {}
        if env.get("error") or not p:
            raise RunError(f"{cog_name} could not do the {lens} review: {(env.get('error') or {}).get('detail') or 'no result'}")
        pending = state.get("pending") or {}
        Track(self._dir(state["id"])).append({"type": "gate.lens", "run_id": state["id"], "step": pending.get("step"), "cog": cog_name,
                                              "lens": lens, "of": [i, n], "verdict": p.get("verdict"), "passes": p.get("passes"),
                                              "findings": p.get("findings") or []})
        waiting = state["status"] == "waiting" and cog_name in (pending.get("assignees") or [])
        if waiting and (p.get("done") or p.get("failed")):
            if p.get("failed") and self.queue is not None:
                for t in self.queue.open_tasks(cog=cog_name, record=state.get("record")):
                    if t["kind"] == "lens" and t["status"] == "queued" and (t.get("payload") or {}).get("run") == run_id:
                        self.queue.cancel(t["id"], by=cog_name, why=f"the {lens} review did not pass; the others are not needed")
            self.give_to_cog(state)
        return {"run": run_id, "lens": lens, "passes": p.get("passes"), "verdict": p.get("verdict"), "done": p.get("done"), "failed": p.get("failed")}

    def _decline(self, run_id: str, cog_name: str, reasons: list[str]) -> dict:
        state = self.load(run_id)
        pending = state["pending"]
        bundle = self.releases.bundle(state["release"])
        step = self._gate_step(state, pending)
        track = Track(self._dir(state["id"]))
        track.append({"type": "gate.declined", "run_id": state["id"], "step": step["id"], "by": cog_name, "reasons": reasons})
        if pending.get("stepped_in_from"):
            # It stepped in when asked and could not sign: the item stays with the people it was waiting for.
            state["pending"] = pending["stepped_in_from"]
            track.append({"type": "gate.pending", "run_id": state["id"], "step": step["id"], "assignees": state["pending"].get("assignees"),
                          "assignee_type": state["pending"].get("assignee_type"), "route_rule": state["pending"].get("route_rule"),
                          "payload_digest": state["pending"].get("payload_digest"), "back_from": cog_name})
            self._save(state)
            if state.get("record"):
                self.kernel.assign(state["record"], list(state["pending"].get("assignees") or []), by=cog_name, run_id=state["id"], release=state["release"])
            return self.load(run_id)
        env = state["steps"].get(step["id"], {}).get("envelope") or {}
        outcome = self._pend_next(state, bundle, step, env.get("payload") or {}, pending.get("routing_ctx") or {}, track,
                                  list(pending.get("remaining") or []))
        if outcome == "waiting":
            state["steps"][step["id"]]["status"] = "gate-pending"
        self._save(state)
        if outcome == "waiting" and (state.get("pending") or {}).get("assignee_type") == "cog" and cog_name not in state["pending"].get("assignees", []):
            return self.give_to_cog(self.load(run_id))
        return self.load(run_id)

    # ------------------------------------------------------------ a cog asked to sign, or given the work
    def may_step_in(self, state: dict, cog_name: str, asked_by: str | None = None) -> str:
        """Empty when the cog may take this Gate when asked (it has approval criteria for the process, may approve the
        object, follows the SOP, is available and did not prepare the item; and, when ``asked_by`` is given, the asker
        is a person waited for at this Gate or a policy owner, not the item's preparer or author, and the cog's own
        review of this very version has not already failed); otherwise why not."""
        pending = state.get("pending") or {}
        if state["status"] != "waiting" or pending.get("kind", "decision") != "decision":
            return f"it is not waiting for a decision (it is {state['status']})"
        if self.is_change_order(state):
            return self.PERSON_SIGNS_CONFIGURATION
        if pending.get("countersign_of"):
            return "it waits for a person's countersignature"
        if pending.get("committee"):
            return f"the {pending['committee']['name']} committee decides it by consensus"
        bundle = self.releases.bundle(state["release"])
        excluded = self.excluded(bundle, state, "role:")
        if asked_by is not None:
            asker = authz.principal_name(asked_by)
            if (bundle["members"].get(asker) or {}).get("type") != "person":
                return f"only a person may ask a cog to sign ({asker} is not one)"
            if asker in excluded:
                return f"{asker} may not choose who approves an item they prepared or wrote"
            if asker not in (pending.get("assignees") or []) and "policy-owner" not in authz.roles_of(bundle, asker):
                return f"only someone this step waits for, or a policy owner, may ask a cog to sign it ({asker} is neither)"
            seen = self.seen_digest(state)
            for ev in Track(self._dir(state["id"])).read():
                if (ev["type"] == "gate.cog_review" and ev.get("cog") == cog_name and ev.get("seen") == seen
                        and ev.get("verdict") not in ("approve",)):
                    return (f"{cog_name} already reviewed this version and could not sign it; its finding stands until the item "
                            f"changes or a person decides")
        if (bundle["members"].get(cog_name) or {}).get("type") != "cog" or cog_name not in self.registry.ids():
            return f"{cog_name} is not a cog of the organization"
        from .routing import criteria_for

        if not criteria_for(bundle, f"cog:{cog_name}", state["process"]):
            return f"no approval criteria let {cog_name} sign {state['process']} items"
        allowed = authz.check(bundle, cog_name, "approve", state["manifest"]["object"], state["manifest"]["folder"])
        if not allowed["allowed"]:
            return allowed["why"]
        if cog_name in excluded:
            return excluded[cog_name]
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        if rec and cog_name == rec["fields"].get("author"):
            return f"segregation of duties: {cog_name} wrote this item"
        by_cog = self.prepared_by_cog(bundle, state)
        if by_cog:
            return f"{by_cog} (a cog) prepared or wrote this item: a person signs it"
        why = self.cog_follows(bundle, cog_name, state["process"])
        if why:
            return why
        from .model import cog_availability

        ok, why = cog_availability(bundle, cog_name)
        return "" if ok else why

    def cog_step_in(self, run_id: str, cog_name: str, asked_by: str) -> dict:
        """Someone asked a cog to approve an item waiting for people: where the policy lets the cog sign it, the cog takes
        the Gate for this turn. If it cannot sign (its criteria or its review say no), the item goes back to the people."""
        state = self.load(run_id)
        if cog_name in ((state.get("pending") or {}).get("assignees") or []):
            return self.cog_decide(run_id, cog_name)
        why = self.may_step_in(state, cog_name)
        if why:
            raise RunError(why)
        why = self.may_step_in(state, cog_name, asked_by=asked_by)
        if why:  # the cog could sign, but not because this person asked: recorded as its answer, nothing changes
            Track(self._dir(state["id"])).append({"type": "gate.declined", "run_id": state["id"], "step": state["pending"]["step"],
                                                  "by": cog_name, "reasons": [why], "asked_by": asked_by, "step_in_refused": True})
            return self.load(run_id)
        self._follow_release(state, Track(self._dir(state["id"])), "a cog asked to sign")
        before = {k: v for k, v in state["pending"].items() if k != "stepped_in_from"}
        state["pending"] = {**before, "assignees": [cog_name], "assignee_type": "cog", "stepped_in_from": before, "asked_by": asked_by}
        Track(self._dir(state["id"])).append({"type": "gate.stepped_in", "run_id": state["id"], "step": before["step"], "cog": cog_name,
                                              "asked_by": asked_by, "was_waiting_for": before.get("assignees")})
        self._save(state)
        return self.cog_decide(run_id, cog_name)

    # The fields whose holder a step routed to is given the work itself (the assignee does the task, the auditor records
    # the findings), not an approval: a cog routed there does the work and completes the step, within what the Gate takes.
    WORK_ROUTES = ("field:assignee", "field:auditor")

    @staticmethod
    def is_assigned_work(state: dict, cog_name: str) -> bool:
        """The item waits for the cog because it was assigned the step (a field or a name), not routed to it to approve."""
        pending = state.get("pending") or {}
        return (state["status"] == "waiting" and cog_name in (pending.get("assignees") or [])
                and pending.get("route") in Runner.WORK_ROUTES and not pending.get("stepped_in_from"))

    def cog_complete(self, run_id: str, cog_name: str, summary: str, fields: dict | None = None) -> dict:
        """A cog finishes a step it was assigned (complete the task, do the work): its summary is the signature's meaning;
        ``fields`` are what its work produced (the auditor's findings), only those the Gate takes."""
        state = self.load(run_id)
        if not self.is_assigned_work(state, cog_name):
            raise RunError(f"run {run_id} is not waiting for {cog_name} to do assigned work")
        return self.decide(run_id, cog_name, "approve", meaning=f"completed: {summary.strip() or 'done'}", payload_digest=self.seen_digest(state),
                           fields=fields or None)

    def hand_back(self, run_id: str, cog_name: str, reason: str) -> dict:
        """A cog gives assigned work back: the step now waits for the person who filed the item, with the cog's reason."""
        state = self.load(run_id)
        pending = state.get("pending") or {}
        if state["status"] != "waiting" or cog_name not in (pending.get("assignees") or []):
            raise RunError(f"run {run_id} is not waiting for {cog_name}")
        self._follow_release(state, Track(self._dir(state["id"])), "a hand-back")
        bundle = self.releases.bundle(state["release"])
        owner = authz.principal_name(state["owner"])
        to = owner if (bundle["members"].get(owner) or {}).get("type") == "person" else None
        if to is None:
            rec = self.kernel.get(state["record"]) if state.get("record") else None
            by = (rec or {}).get("created_by")
            to = by if (bundle["members"].get(by) or {}).get("type") == "person" else None
        people = [to] if to else []
        if not people and pending.get("route") == "field:auditor":
            # A cog filed it (an audit the auditor cog planned): it goes to the people in the step's own seat who may
            # take it, as a route to that role would give it (the permission, segregation of duties).
            role = (pending.get("gate") or {}).get("role") or next((s.get("role") for s in state["manifest"]["steps"] if s["id"] == pending.get("step")), "")
            excluded = self.excluded(bundle, state, f"role:{role}")
            obj, folder = state["manifest"]["object"], state["manifest"]["folder"]
            people = [p for p in principals_for(bundle, f"role:{role}") if (bundle["members"].get(p) or {}).get("type") == "person"
                      and p not in excluded and authz.check(bundle, p, "approve", obj, folder)["allowed"]] if role and role not in FIELD_ROLES else []
            if not people:
                raise RunError(f"there is no person to hand it back to: a cog filed it, and no person in the {(role or 'step').replace('-', ' ')} "
                               "role may take it")
        if not people:
            raise RunError("there is no person to hand it back to")
        to = people[0]
        route = f"person:{to}" if len(people) == 1 else f"role:{role}"
        track = Track(self._dir(state["id"]))
        track.append({"type": "gate.handed_back", "run_id": state["id"], "step": pending["step"], "by": cog_name, "to": to, "assignees": people,
                      "reason": reason})
        state["pending"] = {**{k: v for k, v in pending.items() if k != "stepped_in_from"}, "assignees": people, "assignee_type": "person",
                            "route": route, "handed_back_by": cog_name}
        self._save(state)
        if state.get("record"):
            self.kernel.assign(state["record"], people, by=cog_name, run_id=state["id"], release=state["release"])
        self._notify(people, kind="handed-back", subject=f"{cog_name} handed back {pending.get('title', 'the item')}: {state.get('record') or state['process']}",
                     body=reason, run_id=state["id"])
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
                         payload_digest: str, delegating_rule=None, meaning_given: bool = True, option: dict | None = None,
                         extra: dict | None = None) -> None:
        """Sign the decision and make the move it chose: forward (a grant, then the transition's actions), or a rejection
        (to ``rejected``, back to the owner for rework, back to an earlier Gate, or on to a status that moves anew)."""
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        wf = self._graph(state)
        if option is None:
            option = pg.transition(wf, step["gate"]["step"]) if verdict == "approve" else pg.rejection_option(wf, state.get("at"))
        decision = {"by": by, "by_type": by_type, "verdict": verdict, "meaning": meaning, "meaning_given": meaning_given,
                    "at": utcnow(), "payload_digest": payload_digest, "record_digest": self.record_digest(rec) if rec else None,
                    "delegating_rule": delegating_rule, "step": step["gate"]["step"], "run_id": state["id"],
                    "release_digest": state["release"], "record": state.get("record"),
                    "route": (state.get("pending") or {}).get("route"), "countersigns": (state.get("pending") or {}).get("countersign_of"),
                    "transition": (option or {}).get("id"), **(extra or {})}
        decision["signature"] = sign(self.secret(), decision)
        state["steps"][step["id"]]["decision"] = decision
        state["pending"] = None
        track.append({"type": "gate.decided", "run_id": state["id"], "step": step["id"], **decision})
        if verdict == "approve":
            grant = {"id": uuid.uuid4().hex[:8], "run_id": state["id"], "decided_by": by, "by_type": by_type,
                     "payload_digest": payload_digest, "issued_at": utcnow(), "signature": decision["signature"],
                     "steps": [s["id"] for s in state["manifest"]["steps"] if s["id"] != step["id"]]}
            state["grant"] = grant
            track.append({"type": "grant.issued", "run_id": state["id"], "grant": grant})
            state["steps"][step["id"]]["status"] = "passed"
            if state.get("record"):
                self.kernel.append("record.decision", state["record"], decision, run_id=state["id"], release=state["release"], by=by)
            if option is not None:
                self._take(state, option)
            return
        target = (option or {}).get("to") or "rejected"
        st = pg.status_of(wf, target) or {}
        state["grant"] = None
        close = {"by": by, "decision": decision, "meaning": meaning, "step": step["id"]}
        if st.get("category") == "done" or not st:
            if option is not None and option.get("actions"):
                self._take(state, option, close=close)  # its actions run, then it closes (hubproto.runner._finish)
                return
            state["at"] = target
            self._finish(state, track, wf, target, close)
            return
        rejected = {"type": "run.rejected", "run_id": state["id"], "step": step["id"], "by": by, "to": target}
        if self._rework_transition(wf, target) is not None:
            track.append(rejected)
            state["at"] = target
            self._return_to_owner(state, step, track, decision, meaning)
            return
        if pg.options(wf, target) and target in (state.get("carriers") or {}) and not (option or {}).get("actions"):
            # Back to a Gate it passed: the people who decided it then get it back.
            track.append(rejected)
            assignees, due = self._prior_pending(state, track, target)
            if state.get("record"):
                try:
                    self.kernel.transition(state["record"], target, by=by, run_id=state["id"], release=state["release"],
                                           decision=decision, reason=meaning, assignees=assignees, due=due)
                except KernelGuardError as e:
                    track.append({"type": "run.failed", "run_id": state["id"], "step": step["id"], "reason": str(e)})
            self._rewind(state, target, track, assignees, due)
            return
        # Back to a status that moves on, or to a Gate it has not passed: the item moves there and goes on afresh.
        if state.get("record"):
            self.kernel.append("record.decision", state["record"], decision, run_id=state["id"], release=state["release"], by=by)
        if option is not None:
            self._take(state, option)

    def _return_to_owner(self, state: dict, step: dict, track: Track, decision: dict, meaning: str) -> None:
        """A rejection that returns the item to its owner for rework: state ``returned``; resubmission reopens the run."""
        wf = self._graph(state)
        here = state.get("at") if self._rework_transition(wf, state.get("at")) is not None else "returned"
        resub = self._rework_transition(wf, here)
        first = next((a for a in (resub or {}).get("actions") or [] if isinstance(a, str)), None)
        reject_to = first or state["workflow"].get("reject_to")
        state["at"] = here
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
                            "due": None, "reason": meaning, "status": "returned", "transition": (resub or {}).get("id", "resubmit")}
        track.append({"type": "gate.returned", "run_id": state["id"], "step": step["id"], "to_step": reject_to,
                      "assignees": [state["owner"]], "reason": meaning})
        self._notify([state["owner"]], kind="returned", subject=f"{state['process']} {state.get('record')} returned for rework",
                     body=f"{decision['by']} rejected '{step['gate']['title']}': {meaning}. Correct what it says and resubmit it, or use Back to the start.", run_id=state["id"])

    def _prior_pending(self, state: dict, track: Track, status: str) -> tuple[list[str], str | None]:
        carrier = (state.get("carriers") or {}).get(status)
        if carrier is None:
            step = next((s for s in state["manifest"]["steps"] if f"awaiting-{s.get('gate', {}).get('step')}" == status), None)
            carrier = (step or {}).get("id")
        if carrier is None:
            return [], None
        for ev in reversed(track.read()):
            if ev["type"] == "gate.pending" and ev.get("step") == carrier:
                return list(ev.get("assignees") or []), ev.get("due")
        return [], None

    def _prior_route(self, track: Track, step_id: str) -> str | None:
        for ev in reversed(track.read()):
            if ev["type"] == "gate.pending" and ev.get("step") == step_id and ev.get("route"):
                return ev["route"]
        return None

    def _rewind(self, state: dict, status: str, track: Track, people: list[str], due: str | None) -> None:
        """Back to the Gate at ``status``: the moves after it are forgotten (their steps run again when it is decided)."""
        wf = self._graph(state)
        carrier = (state.get("carriers") or {}).get(status)
        if carrier is None or carrier not in state["steps"]:
            return
        path = state.get("path") or []
        idx = max((i for i, e in enumerate(path) if e.get("to") == status), default=None)
        if idx is not None:
            for e in path[idx + 1:]:
                for sid in e.get("actions") or []:
                    if sid != carrier:
                        state["steps"].pop(sid, None)
            state["path"] = path[: idx + 1]
        state["at"] = status
        state["moving"] = None
        env = state["steps"][carrier]["envelope"]
        state["steps"][carrier]["status"] = "gate-pending"
        state["steps"][carrier].pop("decision", None)
        prim = pg.primary_option(wf, status) or {}
        gate = self._gate_dict(state, prim) if prim else {}
        bundle = self.releases.bundle(state["release"])
        if gate.get("committee"):  # back to a committee: every member decides afresh (consensus), not the earlier deciders
            self._committee_gate(state, bundle, {"id": carrier, "gate": gate}, env["payload"] or {}, {}, track)
            return
        state["pending"] = {"kind": "decision", "step": carrier, "checkpoint": gate.get("step"), "assignees": people,
                            "route": self._prior_route(track, carrier),
                            "route_rule": {"frame": "(rejection)", "line": 0, "text": "returned by a rejection"},
                            "payload_digest": sha256_of(env["payload"] or {}), "title": gate.get("title"), "due": due,
                            "gate": gate, "status": status, "transition": gate.get("step"), "options": self._options(state, bundle, status, gate)}
        track.append({"type": "gate.pending", "run_id": state["id"], "step": carrier, "assignees": people,
                      "route_rule": state["pending"]["route_rule"], "payload_digest": state["pending"]["payload_digest"],
                      "routing_ctx": {}, "due": due})
        self._notify(people, kind="approval", subject=f"{gate.get('title')} (returned): {state['process']} {state.get('record')}",
                     body="An item you decided earlier was rejected at a later step and has come back to you.", run_id=state["id"])

    # ---------------------------------------------------------- human actions
    def _load_waiting(self, run_id: str, by: str, kind: str, transition: str | None = None) -> tuple[dict, dict]:
        state = self.load(run_id)
        by = authz.principal_name(by)
        pending = state.get("pending")
        if state["status"] != "waiting" or not pending or pending.get("kind", "decision") != kind:
            raise RunError(f"run {run_id} is not waiting for a {kind} (status {state['status']})")
        others = [o for o in pending.get("options") or [] if by in (o.get("assignees") or [])]
        if by not in pending["assignees"] and others and transition and transition not in [o["id"] for o in others]:
            name = next((o["name"] for o in pending.get("options") or [] if o["id"] == transition), transition)
            raise RunError(f"{by} may not choose “{name}” here")
        if by not in pending["assignees"] and not others:
            raise RunError(f"{by} is not an assignee of this decision (assigned: {', '.join(pending['assignees'])})")
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        if rec and rec["hold"]:
            raise RunError(f"{rec['number']} is on hold ({rec.get('hold_reason') or 'no reason given'}); lift the hold first")
        return state, pending

    def allowed_decision_fields(self, state: dict, transition: str | None = None) -> list[str]:
        """Fields a decider (a person, or a cog at a Gate its role may decide) may supply with a decision: what the next
        state requires, the fields the transition collects, and the type's decision fields."""
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        if rec is None or not state.get("pending"):
            return []
        td = self.kernel.types.get(rec["type"], {})
        tid = transition or state["pending"].get("transition") or state["pending"]["checkpoint"]
        nxt, _ = self._next_state(state, tid)
        collected = set((pg.transition(self._graph(state), tid) or {}).get("fields") or [])
        missing = self._missing_for_person(state, nxt)  # a signer supplies what is still missing, never rewrites what is there
        machines = set(self.registry.ids()) | {"hub", "system"}
        setters = rec.get("setters") or {}
        declared = {k for k, v in rec["fields"].items()  # values a person put there (at Start or at an earlier Gate)
                    if v not in (None, "", [], {}) and setters.get(k, rec.get("created_by")) not in machines}
        return sorted((set(missing) | set(td.get("decision_fields") or []) | collected) - set(td.get("system_fields") or []) - declared)

    def required_at_start(self, process: str, at: str | None = None) -> list[str]:
        """Fields the submitter must supply: what the first Gate's state requires, minus system fields and defaults."""
        release = self.releases.current(at)
        if release is None:
            return []
        bundle = self.releases.bundle(release["digest"])
        wf = (bundle.get("workflows") or {}).get(process)
        start = [f for f in ((pg.initial(wf) or {}).get("fields") or [])] if pg.is_graph_shape(wf) else []
        if not wf or not wf.get("checkpoints"):
            return start
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
        return list(dict.fromkeys(start + [f for f in td.get("required_by_state", {}).get(first, []) if f not in skip]))

    def choose(self, run_id: str, by: str, transition: str, meaning: str = "", payload_digest: str | None = None,
               fields: dict | None = None) -> dict:
        """Take a transition by its id: one of the options at the Gate the item waits at (a signed decision, as
        ``decide``), Retry or Resubmit, or a global transition (Cancel, or another the process allows from here)."""
        state = self.load(run_id)
        by = authz.principal_name(by)
        pending = state.get("pending") or {}
        if state["status"] == "waiting" and pending.get("kind", "decision") == "decision" \
                and transition in [o["id"] for o in pending.get("options") or []]:
            return self.decide(run_id, by, None, meaning=meaning, payload_digest=payload_digest, fields=fields, transition=transition)
        wf = self._graph(state)
        t = pg.transition(wf, transition)
        if t is None:
            raise RunError(f"{state['process']} has no transition {transition!r}")
        if t["id"] == "retry" or (t.get("system") and state["status"] == "failed"):
            return self.retry(run_id, by, note=meaning, fields=fields)
        if (state["status"] == "waiting" and pending.get("kind") == "resubmit"
                and (t["id"] == pending.get("transition") or t is self._rework_transition(wf, state.get("at")))):
            return self.resubmit(run_id, by, fields=fields, note=meaning)
        at = state.get("at")
        if t.get("type") != "global" or not pg.global_applies(wf, t, at) or state["status"] in ("completed", "cancelled", "rejected"):
            raise RunError(f"“{t.get('name') or t['id']}” is not a way out of {(pg.status_of(wf, at) or {}).get('name', at)} now")
        target = pg.status_of(wf, t["to"]) or {}
        if target.get("category") == "done" and target.get("outcome") == "cancelled":
            return self.cancel(run_id, by, reason=meaning, transition=t["id"])
        if not meaning.strip():
            raise RunError(f"say why you take “{t.get('name') or t['id']}”")
        if self.is_change_order(state) and self._approves(wf, t):
            raise RunError(f"“{t.get('name') or t['id']}” would approve the change order outside its Gate: {self.PERSON_SIGNS_CONFIGURATION}")
        bundle = self.releases.bundle(state["release"])
        why = self._may_take(state, bundle, t, by)
        if why:
            raise RunError(why)
        track = Track(self._dir(state["id"]))
        self._follow_release(state, track, t.get("name") or t["id"])
        self._withdraw(state, track, f"{by} took “{t.get('name') or t['id']}”: {meaning.strip()}")
        track.append({"type": "transition.taken", "run_id": state["id"], "transition": t["id"], "by": by, "meaning": meaning.strip(),
                      "from": at, "to": t["to"]})
        state["grant"] = None
        if state["status"] == "failed":
            state["moving"] = None
        self._take(state, t)
        self._save(state)
        return self.advance(run_id)

    def _change_request_waits(self, state: dict, option: dict) -> str:
        """A change request a change order implements is verified (closed) only once that change order is released: the
        change it asks for is then in force. Empty when it may be."""
        if (state.get("workflow") or {}).get("record_type") != "change-request" or not state.get("record"):
            return ""
        if not self._approves(self._graph(state), option):
            return ""
        rec = self.kernel.get(state["record"]) or {}
        cos = [c for c in self.kernel.query(type_="change-order") if rec.get("number") in (c["fields"].get("implements") or [])]
        if not cos:
            return ""  # no change order implements it (a change that needs none): nothing to wait for
        if any(c["state"] == "released" or c["fields"].get("stage") == "released" or c["fields"].get("released_release") for c in cos):
            return ""
        where = ", ".join(f"{c['number']} is {c['fields'].get('stage') or c['state']}" for c in cos)
        return f"{option.get('name') or 'Verify'} waits until the change order implementing {rec.get('number')} is released ({where})"

    @staticmethod
    def _approves(wf: dict, t: dict) -> bool:
        """The transition ends the item as completed (for a change order: approves it)."""
        st = pg.status_of(wf, t.get("to")) or {}
        return st.get("category") == "done" and st.get("outcome", "completed") == "completed"

    def _may_take(self, state: dict, bundle: dict, t: dict, by: str) -> str:
        """Why ``by`` may not take this transition's gate outside a Gate (a global such as Cancel); empty when they may."""
        g = t.get("gate") or {}
        if g.get("kind") != "person":
            return f"“{t.get('name') or t['id']}” is not taken by a person"
        role = g.get("role") or "system"
        obj, folder = state["manifest"]["object"], state["manifest"]["folder"]
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        if g.get("also") == "approvers" and authz.check(bundle, by, "approve", obj, folder)["allowed"]:
            return ""
        if role == "owner":
            ok = by == authz.principal_name(state["owner"])
        elif role in FIELD_ROLES:
            ok = by == authz.principal_name(((rec or {}).get("fields") or {}).get(FIELD_ROLES[role]) or "")
        elif role == "system":
            ok = True
        else:
            ok = role in authz.roles_of(bundle, by)
        if not ok:
            return f"only the {role.replace('-', ' ')} may take “{t.get('name') or t['id']}”" + (" (or someone who may approve it)" if g.get("also") else "")
        return ""

    def _withdraw(self, state: dict, track: Track, why: str) -> None:
        """A Gate waiting at the item is withdrawn explicitly: its assignees are told."""
        pending = state.get("pending") or {}
        if not pending:
            return
        people = sorted(set(pending.get("assignees") or []) | {p for o in pending.get("options") or [] for p in o.get("assignees") or []})
        track.append({"type": "gate.withdrawn", "run_id": state["id"], "step": pending.get("step"), "assignees": people, "reason": why})
        state["pending"] = None
        if pending.get("step") in state["steps"] and state["steps"][pending["step"]].get("status") == "gate-pending":
            state["steps"][pending["step"]]["status"] = "passed"
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        self._notify(people, kind="withdrawn", subject=f"No longer waiting for you: {pending.get('title', 'a decision')} on {(rec or {}).get('number') or state['process']}",
                     body=why, run_id=state["id"])

    def _chosen(self, state: dict, pending: dict, by: str, verdict: str | None, transition: str | None) -> dict:
        """The transition a decision takes: the one named, else the option an approval or a rejection takes for this
        decider. Resolved against the graph of the release the item follows now."""
        wf = self._graph(state)
        at = pending.get("status") or state.get("at")
        offered = pending.get("options") or [
            {"id": t["id"], "name": t.get("name") or t["id"], "to": t["to"], "verdict": pg.option_verdict(wf, t)} for t in pg.options(wf, at)]

        def mine(o):
            return by in (o["assignees"] if o.get("assignees") is not None else pending.get("assignees") or [])
        names = ", ".join(f"“{o['name']}”" for o in offered if mine(o)) or "none"
        if transition:
            o = next((x for x in offered if x["id"] == transition), None)
            if o is None:
                raise RunError(f"{transition!r} is not a way forward from here; the options are {names}")
            if not mine(o):
                raise RunError(f"{by} may not choose “{o['name']}” here")
        else:
            own = [x for x in offered if mine(x)]
            o = next((x for x in own if x["id"] == pending.get("transition") and verdict == "approve"), None) or \
                next((x for x in own if x.get("verdict") == verdict), None)
            if o is None:
                raise RunError(f"“{pending.get('title') or 'this step'}” offers no way to {verdict} it; the options are {names}")
        now = [t for t in pg.options(wf, at)]
        t = next((x for x in now if x["id"] == o["id"]), None)
        if t is None:
            t = pg.primary_option(wf, at) if o.get("verdict") == "approve" else pg.rejection_option(wf, at)
        if t is None:
            raise RunError(f"the release in force offers no way to {o.get('verdict')} it from here")
        return {**t, "verdict": pg.option_verdict(wf, t)}

    def decide(self, run_id: str, by: str, verdict: str | None, meaning: str = "", payload_digest: str | None = None,
               fields: dict | None = None, delegating_rule=None, countersign: str | None = None, evidence: str | None = None,
               transition: str | None = None) -> dict:
        """A signed decision at the Gate the item waits at. ``verdict`` approve or reject takes the forward or the
        rejection transition; ``transition`` names any option instead (``choose`` does the same by id)."""
        state, pending = self._load_waiting(run_id, by, "decision", transition=transition)
        by = authz.principal_name(by)
        cm = pending.get("committee")
        if cm:
            # At a committee's decision a vote is a member's (consensus); another way out that someone else decides (its own
            # assignees) is taken only when named.
            ways = {t["id"] for t in self.committee_options(state)}
            other = next((o for o in pending.get("options") or [] if o.get("assignees") is not None and by in o["assignees"]
                          and o["id"] == transition and o["id"] not in ways), None)
            if other is None:
                if by in self.committee_outstanding(pending) and (transition is None or transition in ways):
                    return self._person_vote(state, pending, by, verdict, meaning, payload_digest, transition, fields)
                if by in (cm.get("votes") or {}) and by not in (cm.get("interveners") or []):
                    raise RunError(f"{by} has already voted on this item")
                raise RunError(f"the {cm['name']} committee decides this by consensus: "
                               + (f"as {'its chair' if by == cm.get('chair') else 'the person it was escalated to'} you intervene (decide in "
                                  "the member's place, extend the due date, or return it)" if by in (cm.get("interveners") or [])
                                  else f"{by} is not one of its members"))
        if Track(self._dir(state["id"])).has_decision(pending["step"], pending.get("checkpoint")):
            # One signature per Gate: a decision that landed before a crash is not followed by a second, contrary one.
            raise RunError("this step was already decided; open the item again to see where it stands")
        track = Track(self._dir(state["id"]))
        self._follow_release(state, track, "a decision")  # from this move on, the release in force (change control 5.1)
        bundle = self.releases.bundle(state["release"])
        by_type = bundle["members"].get(by, {}).get("type", "person")
        if by_type == "cog" and fields:
            # A cog fills in fields at a Gate its role may decide, within what the Gate accepts (change control 7).
            permitted = set(self.allowed_decision_fields(state, transition))
            refused = sorted(k for k in fields if k not in permitted)
            if refused:
                raise RunError(f"a decision may not set {', '.join(refused)}; this Gate accepts {', '.join(sorted(permitted)) or 'no fields'}")
        if transition is None and verdict not in ("approve", "reject"):
            raise RunError("verdict must be approve or reject")
        option = self._chosen(state, pending, by, verdict, transition)
        verdict = option["verdict"]
        rec_now = self.kernel.get(state["record"]) if state.get("record") else None
        allowed = authz.check(bundle, by, "approve", state["manifest"]["object"], rec_now["folder"] if rec_now else state["manifest"]["folder"])
        if not allowed["allowed"]:
            raise RunError(allowed["why"])
        if verdict == "approve" and self.is_change_order(state):
            if by_type != "person":
                raise RunError(f"{by} may not approve {state.get('record') or 'this change order'}: {self.PERSON_SIGNS_CONFIGURATION}")
            blocked = self.change_order_blocks(state)
            if blocked:
                raise RunError("the change order cannot be approved yet: " + "; ".join(blocked))
        if pending.get("countersign_of"):
            role = pending.get("countersign_role") or str(pending.get("route") or "").partition(":")[2]
            if by_type != "person":
                raise RunError("a countersignature is a person's")
            if role and role not in authz.roles_of(bundle, by):
                raise RunError(f"a countersignature here needs a {role.replace('-', ' ')}; {by} is not one")
            if by == pending["countersign_of"] or by in (pending.get("excluded") or []):
                raise RunError(f"segregation of duties: {by} may not countersign this item")
        own = next((o for o in pending.get("options") or [] if o["id"] == option["id"] and o.get("assignees") is not None), None)
        if own is not None and by not in (pending.get("assignees") or []):
            exclusions = lambda **kw: self.excluded(bundle, state, own.get("route") or "role:", gate_not=own.get("not") or [], **kw)
        else:
            exclusions = lambda **kw: self.excluded(bundle, state, pending.get("route") or "role:", **kw)
        excluded = exclusions()
        if by in excluded and self.maker_may_reject(state, by, by_type, verdict):
            excluded = exclusions(makers=False)  # a maker rejecting: only the rules about making it are set aside
        if by in excluded:
            raise RunError(f"{excluded[by]}; {by} cannot decide it")
        if by_type == "cog" and verdict == "approve" and not delegating_rule and not self.is_assigned_work(state, by):
            raise RunError(f"{by} signs only within its approval criteria, and none covers this step")
        if not payload_digest:
            raise RunError("a decision must quote what you saw: pass the run's seen_digest (shown on the card and by inbox-pending)")
        if payload_digest != self.seen_digest(state):
            raise RunError("the item changed since you opened it (its fields, links or documents differ); open it again and decide on what it is now")
        if not meaning.strip():
            raise RunError("a signature needs its meaning: say in a few words what you are confirming "
                           "(for example 'invoice matches the order and goods were received')")
        step = self._gate_step(state, pending)
        track = Track(self._dir(state["id"]))
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        if verdict == "reject" and not option.get("fields"):
            fields = None  # a rejection changes nothing on the record; its reason is the meaning
        guard = DECISION_FIELD_GUARDS.get(rec["type"]) if rec is not None else None
        if guard is not None:
            try:
                fields = guard(self.kernel, rec, verdict, by=by, fields=fields or {}, bundle=bundle) or None
            except KernelGuardError as e:
                raise RunError(str(e)) from None
        if fields and rec is not None:
            permitted = set(self.allowed_decision_fields(state, option["id"]))
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
        why = self._validators_block(state, self._graph(state), option) or self._change_request_waits(state, option)
        if why:
            raise RunError(why)
        if verdict == "approve" and rec is not None:
            nxt = option["to"]
            missing = self._missing_for_person(state, nxt)
            if missing:
                raise RunError(f"{nxt} needs {', '.join(missing)} before approval; supply them with the decision")
            hook = PRE_DECISION_HOOKS.get(rec["type"])
            if hook:
                try:
                    hook(self.kernel, rec, verdict)
                except KernelGuardError as e:
                    raise RunError(str(e)) from None
        if by_type == "cog" and verdict == "approve" and countersign:
            return self._await_countersign(state, pending, step, track, bundle, by, meaning.strip(), countersign, delegating_rule, evidence)
        if pending.get("countersign_of"):
            meaning = f"{meaning.strip()} (countersigning {pending['countersign_of']})"
        owner = authz.principal_name(state["owner"])
        if by_type == "cog" and self.is_assigned_work(state, by):
            self._notify([state["owner"]], kind="cog-done", subject=f"{by} finished {pending.get('title', 'the step')} on {state.get('record') or state['process']}",
                         body=meaning.strip(), run_id=state["id"])
        elif by_type == "cog":
            rule = pending.get("route_rule") or {}
            self._notify([state["owner"]], kind="auto-decided",
                         subject=f"{pending.get('title', 'Approval')} on {state.get('record') or state['process']}: {verdict}d automatically by {by}",
                         body=f"The {by} cog {verdict}d this under the approval criteria in {rule.get('frame')} line {rule.get('line')}. "
                              f"{meaning.strip()}. No person reviewed it; the criteria and the reasons are on the run page.", run_id=state["id"])
        self._record_decision(state, step, track, by=by, by_type=by_type, verdict=verdict,
                              meaning=meaning.strip(), payload_digest=pending["payload_digest"], meaning_given=True,
                              delegating_rule=delegating_rule, option=option)
        self._save(state)
        result = self.advance(run_id) if state.get("moving") else self.load(run_id)
        if (by_type == "person" and by != owner and verdict == "reject" and (bundle["members"].get(owner) or {}).get("type") == "person"
                and result.get("status") == "waiting" and (result.get("pending") or {}).get("kind") == "decision"):
            nxt = result["pending"]
            self._notify([owner], kind="moved", subject=f"{by} rejected {pending.get('title', 'a step')}; it went back to {', '.join(nxt.get('assignees') or [])}",
                         body=f"{by} rejected it: {meaning.strip()}. It now waits again at {nxt.get('title', 'an earlier step')}.", run_id=state["id"])
        if by_type == "person" and by != owner and (bundle["members"].get(owner) or {}).get("type") == "person" and verdict == "approve":
            # The owner hears when someone else moves their item, and where it is now.
            now = self.kernel.get(state["record"]) if state.get("record") else None
            nxt = result.get("pending") or {}
            where = (f"it now waits for {', '.join(nxt.get('assignees') or [])} at {nxt.get('title', 'the next step')}" if result["status"] == "waiting"
                     else f"it is now {(now or {}).get('state', result['status']).replace('-', ' ')}")
            self._notify([owner], kind="moved", subject=f"{by} approved {pending.get('title', 'a step')} on {(now or {}).get('number') or state['process']}",
                         body=f"{by} approved it: {meaning.strip()}. {where[0].upper() + where[1:]}.", run_id=state["id"])
        hook = POST_DECISION_HOOKS.get(rec["type"]) if rec is not None else None
        if hook:
            hook(self.kernel, self.kernel.get(rec["locator"]), verdict)
        if (result.get("pending") or {}).get("committee") and result.get("status") == "waiting":
            result = self._committee_cogs(result)  # back at a committee's decision: its cog members take their turn
        return result

    def _await_countersign(self, state: dict, pending: dict, step: dict, track: Track, bundle: dict, by: str, meaning: str,
                           role: str, delegating_rule, evidence: str | None) -> dict:
        """The cog signed, and its criteria ask a person to countersign: its signature is recorded and the Gate now waits
        for the people holding the countersigning role (never the item's preparer, never a cog)."""
        obj, folder = state["manifest"]["object"], state["manifest"]["folder"]
        owner = authz.principal_name(state["owner"])
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        author = (rec or {}).get("fields", {}).get("author")
        excluded = set(self.excluded(bundle, state, "role:")) | {owner, by} | ({author} if author else set())
        holders = [p for p in principals_for(bundle, f"role:{role}") if bundle["members"][p]["type"] == "person"]
        people = [p for p in holders if authz.check(bundle, p, "approve", obj, folder)["allowed"] and p not in excluded]
        signature = {"by": by, "by_type": "cog", "verdict": "approve", "meaning": meaning, "at": utcnow(), "step": step["gate"]["step"],
                     "payload_digest": pending["payload_digest"], "delegating_rule": delegating_rule, "evidence": evidence,
                     "countersign_needed": role, "run_id": state["id"], "release_digest": state["release"], "record": state.get("record")}
        signature["record_digest"] = self.record_digest(rec) if rec else None
        signature["signature"] = sign(self.secret(), signature)
        track.append({"type": "gate.signed", "run_id": state["id"], "step": step["id"], **signature})
        state["steps"][step["id"]]["first_signature"] = signature
        if state.get("record"):
            self.kernel.append("record.decision", state["record"], {**signature, "countersign_needed": role}, run_id=state["id"],
                               release=state["release"], by=by)
        if not people:
            who = ", ".join(sorted(holders)) or "nobody"
            self._fail(state, track, step["id"],
                       f"{by} approved it, but it needs a {role.replace('-', ' ')} to countersign and no one can: the role is held by {who}, "
                       f"and whoever prepared or wrote the item may not countersign it. A policy owner can give the role to "
                       f"another person or name another countersigner", kind="organisation", gate=pending.get("title", ""))
            self._save(state)
            return self.load(state["id"])
        rest = {k: v for k, v in pending.items() if k not in ("stepped_in_from", "asked_by")}
        state["pending"] = {**rest, "assignees": people, "assignee_type": "person", "route": f"role:{role}", "countersign_of": by,
                            "countersign_role": role, "excluded": sorted(x for x in excluded if x),
                            "title": f"Countersign: {pending.get('title', 'Approve')}", "remaining": []}
        track.append({"type": "gate.pending", "run_id": state["id"], "step": step["id"], "assignees": people, "assignee_type": "person",
                      "route_rule": delegating_rule, "payload_digest": pending["payload_digest"], "countersign_of": by})
        self._save(state)
        if state.get("record"):
            self.kernel.assign(state["record"], people, by=by, run_id=state["id"], release=state["release"])
        self._notify(people, kind="approval", subject=f"Countersign: {by} approved {state.get('record') or state['process']}",
                     body=f"{by} approved it on its review and its approval criteria ask a {role.replace('-', ' ')} to countersign. {meaning}",
                     run_id=state["id"])
        self._notify([state["owner"]], kind="auto-decided", subject=f"{by} approved {state.get('record') or state['process']}; a {role.replace('-', ' ')} countersigns next",
                     body=meaning, run_id=state["id"])
        return self.load(state["id"])

    def resubmit(self, run_id: str, by: str, fields: dict | None = None, attachments: list[dict] | None = None,
                 note: str = "") -> dict:
        """The owner reworks a returned item: fields and attachments are applied and the run restarts from the return step."""
        state, pending = self._load_waiting(run_id, by, "resubmit")
        by = authz.principal_name(by)
        track = Track(self._dir(state["id"]))
        self._graph(state)
        self._follow_release(state, track, "a resubmission")
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
        wf = self._graph(state)
        rework = self._rework_transition(wf, state.get("at"))
        if rework is None:
            # A run saved before graphs: the process runs again from the step the rejection returned it to.
            ids = self._executed(state)
            start = (len(ids) - 1 - ids[::-1].index(pending["step"])) if pending["step"] in ids else 0
            for sid in ids[start:]:
                state["steps"].pop(sid, None)
            if start < len(ids):
                self._rewind_path(state, start)
            else:
                state["at"], state["moving"], state["path"] = (pg.initial(wf) or {}).get("to", "open"), None, []
        # The rerun reads the record as it now is: the original submission's one-shot inputs are spent.
        record_input = dict(state["inputs"].get("record") or {})
        record_input.update(fields or {})
        for spent in ("user_needs", "attachments"):
            record_input.pop(spent, None)
        state["inputs"] = {**state["inputs"], "record": record_input}
        state["pending"] = None
        state["grant"] = None
        track.append({"type": "run.resubmitted", "run_id": state["id"], "by": by, "from_step": pending["step"], "note": note,
                      "fields": sorted(fields or {}), "attachments": [a.get("name") for a in attachments or []],
                      **({"transition": rework["id"]} if rework else {})})
        if pg.status_of(wf, "open") or not pg.is_graph_shape(wf):
            self.kernel.transition(state["record"], "open", by=by, run_id=state["id"], release=state["release"], reason=f"resubmitted: {note}")
        if rework is not None:
            # The rework loop is the transition out of the returned status: its steps (from the one the rejection
            # returned the item to) run again, then the item waits where it leads.
            self._take(state, rework)
        self._save(state)
        return self.advance(run_id)

    @staticmethod
    def _rework_transition(wf: dict, sid: str | None) -> dict | None:
        """The rework loop out of a status (numbered steps' Resubmit out of ``returned``): the owner resubmits."""
        if not sid or not pg.is_graph_shape(wf):
            return None
        return next((t for t in pg.transitions_out(wf, sid, include_global=False)
                     if t["id"] == "resubmit" and (t.get("gate") or {}).get("role") == "owner"), None)

    def retry(self, run_id: str, by: str, note: str = "", fields: dict | None = None,
              attachments: list[dict] | None = None) -> dict:
        """Retry a failed run from the failed step, fixing the record's fields and attachments on the way."""
        state = self.load(run_id)
        by = authz.principal_name(by)
        if state["status"] != "failed":
            raise RunError(f"run {run_id} is not failed (status {state['status']})")
        track = Track(self._dir(state["id"]))
        self._graph(state)
        self._follow_release(state, track, "a retry")
        bundle = self.releases.bundle(state["release"])
        if by != state["owner"] and not authz.check(bundle, by, "approve", state["manifest"]["object"], state["manifest"]["folder"])["allowed"]:
            raise RunError(f"{by} may not retry this run: only its owner or an approver of {state['manifest']['object']} may")
        rec_before = self.kernel.get(state["record"]) if state.get("record") else None
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
        # Positions are those of the path the run took (for numbered steps, the manifest's order). A failure that is not
        # an action's (no one could take a Gate, a check before an automatic move) resumes where the item stands.
        ids = self._executed(state)
        if sid in ids:
            start = len(ids) - 1 - ids[::-1].index(sid)
        elif sid in (None, "complete") and not self._graph(state).get("form") == "graph":
            start = 0
        else:
            start = len(ids)
        voided: set[str] = set()
        if fields or attachments:
            # Changed inputs: a signature covers the record as it was when it was given. If the record changed after
            # an approval, that approval no longer matches it: the run goes back to the first signed Gate and the
            # item is approved again on what it says now (a quantity raised after approval is never issued on it).
            decided = [i for i, x in enumerate(ids) if (state["steps"].get(x) or {}).get("decision")]
            changed_signed = bool(decided) and bool(fields) and rec_before is not None and any(
                rec_before["fields"].get(k) != v for k, v in fields.items())
            if changed_signed:
                start = min(start, decided[0])
                for i in decided:
                    d = (state["steps"].get(ids[i]) or {}).get("decision") or {}
                    voided.add(ids[i])
                    track.append({"type": "gate.voided", "run_id": state["id"], "step": ids[i], "by": by,
                                  "was_decided_by": d.get("by"), "fields": sorted(fields),
                                  "reason": "the record changed after it was approved; it is approved again on what it says now"})
            else:
                start = min(start, (decided[-1] + 1) if decided else 0)
            sid = ids[start] if start < len(ids) else sid
        recheck = (failed or {}).get("recheck")
        cog_of = {s["id"]: (s.get("cog") or {}).get("id") for s in state["manifest"]["steps"]}
        back = next((i for i, x in enumerate(ids) if cog_of.get(x) == recheck), None) if recheck else None
        if back is not None and back < start:
            # The failed step found that what an earlier step checked has changed since (a lot's quantity since its
            # adjustment was checked): the run starts again from that check, and an approval given after it is void,
            # because it was given on what the check said then.
            for i in range(back, len(ids)):
                d = (state["steps"].get(ids[i]) or {}).get("decision")
                if d and ids[i] not in voided:
                    track.append({"type": "gate.voided", "run_id": state["id"], "step": ids[i], "by": by, "was_decided_by": d.get("by"),
                                  "fields": [], "reason": f"what it was approved on changed since {recheck} checked it; it is checked and approved again"})
            start, sid = back, ids[back]
            state["grant"] = None
        for s in ids[start:]:
            state["steps"].pop(s, None)
        if start < len(ids):
            self._rewind_path(state, start)
        state["pending"] = None
        rec = self.kernel.get(state["record"]) if state.get("record") else None
        if rec and rec["state"] == "failed":
            wf = self._graph(state)
            back_to = state.get("at") if wf.get("form") == "graph" and pg.status_of(wf, state.get("at")) else "open"
            self.kernel.transition(state["record"], back_to, by=by, run_id=state["id"], release=state["release"], reason=f"retried: {note}")
        track.append({"type": "run.retried", "run_id": state["id"], "by": by, "from_step": sid, "note": note})
        self._save(state)
        return self.advance(run_id)

    def cancel(self, run_id: str, by: str, reason: str = "", transition: str | None = None, _checked: bool = False) -> dict:
        """Cancel the item: the process's Cancel (a global transition to a cancelled status). Numbered steps compile one
        its owner or anyone who may approve it takes; a graph names who may."""
        state = self.load(run_id)
        by = authz.principal_name(by)
        if state["status"] in ("completed", "cancelled"):
            raise RunError(f"run {run_id} is {state['status']} and cannot be cancelled")
        track = Track(self._dir(state["id"]))
        wf = self._graph(state)
        self._follow_release(state, track, "a cancellation")
        wf = self._graph(state)
        bundle = self.releases.bundle(state["release"])
        at = state.get("at")
        cancels = [t for t in pg.transitions_out(wf, at) if (pg.status_of(wf, t["to"]) or {}).get("outcome") == "cancelled"
                   and t.get("type") == "global" and (transition in (None, t["id"]))]
        t = cancels[0] if cancels else None
        if t is None and wf.get("form") == "graph":
            raise RunError(f"{state['process']} has no Cancel from {(pg.status_of(wf, at) or {}).get('name', at)}")
        if not _checked:
            if t is not None and not ((t.get("gate") or {}).get("also") == "approvers" and (t.get("gate") or {}).get("role") == "owner"):
                why = self._may_take(state, bundle, t, by)
                if why:
                    raise RunError(why)
            elif by != state["owner"] and not authz.check(bundle, by, "approve", state["manifest"]["object"], state["manifest"]["folder"])["allowed"]:
                raise RunError(f"{by} may not cancel this run")
        to = (t or {}).get("to") or "cancelled"
        track.append({"type": "run.cancelled", "run_id": state["id"], "by": by, "reason": reason, **({"transition": t["id"]} if t else {})})
        state["pending"] = None
        state["grant"] = None
        state["moving"] = None
        state["at"] = to
        if state.get("record"):
            try:
                self.kernel.transition(state["record"], to, by=by, run_id=state["id"], release=state["release"], reason=reason)
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
            self.cancel(run["id"], by, reason=f"back to the start: {reason}", _checked=True)
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

    # ------------------------------------------------------------- state maps
    def previous_workflow(self, process: str, meta: dict | None = None) -> dict | None:
        """The process's workflow in the release that was in force before ``meta`` (the release in force by default)."""
        meta = meta or self.releases.current()
        if not meta:
            return None
        prev = None
        if meta.get("parent"):
            try:
                prev = self.releases.get(meta["parent"])
            except KeyError:
                prev = None
        if prev is None:
            order = [m["digest"] for m in self.releases.list()]
            i = order.index(meta["digest"]) if meta["digest"] in order else -1
            prev = self.releases.get(order[i - 1]) if i > 0 else None
        return (self.releases.bundle(prev["digest"]).get("workflows") or {}).get(process) if prev else None

    def check_state_map(self, process: str, mapping: dict[str, str], old_graph: dict | None, new_graph: dict | None) -> list[str]:
        """Why a state map may not be applied (empty when it may): the release changed the process; each source is a
        status the old graph had and the new one does not; each target is a status of the new graph (not the hub's own)."""
        if not new_graph or not pg.is_graph_shape(new_graph):
            return [f"the release in force has no process {process!r}"]
        if not old_graph:
            return [f"{process} is new in this release: no item is in a status it removed"]
        def ids(g):
            return {x["id"] for x in g.get("statuses") or [] if not x.get("system")} if pg.is_graph_shape(g) else set(g.get("states") or [])
        def same(a, b):
            strip = lambda g: {k: v for k, v in pg.graph_only(g).items() if k not in ("source", "form")}
            return pg.is_graph_shape(a) and strip(a) == strip(b)
        out = []
        if same(old_graph, new_graph):
            out.append(f"the release did not change {process}: a state map moves only items in statuses a change removed")
        old_ids, new_ids = ids(old_graph), ids(new_graph)
        for src, dst in mapping.items():
            if src in new_ids:
                out.append(f"{src!r} is still a status of {process}: only items in a status the change removed are moved")
            elif src not in old_ids:
                out.append(f"{src!r} was not a status of {process}")
            if dst not in new_ids:
                out.append(f"the state map sends {src!r} to {dst!r}, which is not a status of {process} in the release in force")
        return out

    def _of_process(self, process: str, bundle: dict, record_type: str) -> dict[str, dict | None]:
        """The items of a process: {record locator: its latest run} for the items whose latest run is of this process,
        and the items of its record type with no run when no other process of the release files that type."""
        latest: dict[str, dict] = {}
        for st in self.list():
            if st.get("record"):
                latest[st["record"]] = st
        alone = [n for n, w in (bundle.get("workflows") or {}).items() if w.get("record_type") == record_type] == [process]
        out: dict[str, dict | None] = {}
        for rec in self.kernel.query(type_=record_type):
            run = latest.get(rec["locator"])
            if rec.get("archived"):
                continue
            if (run and run.get("process") == process) or (run is None and alone):
                out[rec["locator"]] = run
        return out

    def apply_state_map(self, process: str, mapping: dict[str, str], by: str, co: str) -> list[dict]:
        """See ``apply_state_map`` (module level)."""
        meta = self.releases.current()
        if not meta:
            raise RunError("no policy release is in effect")
        bundle = self.releases.bundle(meta["digest"])
        wf = (bundle.get("workflows") or {}).get(process)
        problems = self.check_state_map(process, dict(mapping or {}), self.previous_workflow(process, meta), wf)
        if problems:
            raise RunError(f"{co}'s state map for {process} is refused: " + "; ".join(problems))
        moved = []
        items = self._of_process(process, bundle, wf["record_type"])
        for rec in self.kernel.query(type_=wf["record_type"]):
            if rec["locator"] not in items:
                continue  # never an item of another process
            run = items[rec["locator"]]
            live = run is not None and run.get("status") in ("waiting", "failed", "running", "submitted")
            here = (run.get("at") if live and run.get("status") == "failed" and run.get("at") else rec["state"])
            if here not in mapping or rec.get("archived"):
                continue
            to = mapping[here]
            name_of = lambda sid: (pg.status_of(wf, sid) or {}).get("name") or sid.replace("-", " ").capitalize()
            old_wf = (run or {}).get("workflow") or {}
            old_name = (pg.status_of(old_wf, here) or {}).get("name") if pg.is_graph_shape(old_wf) else None
            words = f"Moved from {old_name or here.replace('-', ' ').capitalize()} to {name_of(to)} by {co} (state map)"
            # The move itself runs no validator and no action; the fields the target status needs were a warning on the CO.
            self.kernel.append("record.transitioned", rec["locator"], {"from": rec["state"], "to": to, "reason": words, "assignees": [], "due": None},
                               run_id=(run or {}).get("id", ""), release=meta["digest"], by=by)
            moved.append({"record": rec["locator"], "run": (run or {}).get("id"), "from": here, "to": to})
            if not live:
                continue
            state = self.load(run["id"])
            track = Track(self._dir(state["id"]))
            self._graph(state)
            self._withdraw(state, track, f"{words}: this decision is no longer asked for")
            old_release = state["release"]
            state.update(release=meta["digest"], workflow=wf, manifest=(bundle.get("ops") or {}).get(process) or state["manifest"])
            track.append({"type": "run.moved", "run_id": state["id"], "from": here, "to": to, "by": by, "co": co, "reason": words,
                          "release_from": old_release, "release": meta["digest"]})
            state.update(at=to, moving=None, grant=None)
            state.setdefault("path", []).append({"transition": f"state-map:{co}", "from": here, "to": to, "actions": []})
            self._save(state)
            self.advance(state["id"])
        return moved

    # ------------------------------------------------------------- escalation
    def tick(self, today: str | None = None) -> list[dict]:
        """Escalate overdue items. A route written 'go to' adds assignees; one written 'are reported to' only informs."""
        today = today or date.today().isoformat()
        escalated = []
        if getattr(self, "hub", None) is not None:  # committee members late on a change order's panel: the chair is told
            try:
                from .changecontrol import tick_panels

                tick_panels(self.hub, today)
            except Exception:
                pass
        for state in self.list():
            pending = state.get("pending")
            if not pending or state["status"] != "waiting" or not pending.get("due") or pending["due"] >= today or pending.get("escalated"):
                continue
            if pending.get("committee"):  # late members pause it for the chair; a pause past due escalates
                done = self._tick_committee(state, today)
                if done:
                    escalated.append(done)
                continue
            track = Track(self._dir(state["id"]))
            self._follow_release(state, track, "an escalation")  # a reassignment is a move (change control 5.1)
            bundle = self.releases.bundle(state["release"])
            rules = [r for r in candidates(bundle, state["process"], "*", {"overdue": True}) if r["step"] == "*"]
            added: list[str] = []
            informed: list[str] = []
            obj, folder = state["manifest"]["object"], state["manifest"]["folder"]
            excluded = set(self.excluded(bundle, state, pending.get("route") or "role:")) | set(pending.get("excluded") or [])
            role = pending.get("countersign_role") if pending.get("countersign_of") else None

            def may_take(p):  # escalation adds only people who could have been routed this step themselves
                return (authz.check(bundle, p, "approve", obj, folder)["allowed"] and p not in excluded
                        and (role is None or role in authz.roles_of(bundle, p)))
            for rule in rules:
                people = [p for p in principals_for(bundle, rule["assignee"]) if bundle["members"][p]["type"] == "person"]
                if rule.get("mode") == "notify":
                    informed += people
                else:
                    added += [p for p in people if p not in pending["assignees"] and may_take(p)]
                    informed += [p for p in people if p in pending["assignees"] or not may_take(p)]
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


# ------------------------------------------------------------------------------- state maps (change control 5.2)
def items_in_removed_statuses(hub, old_graph: dict, new_graph: dict) -> dict[str, int]:
    """{status id: number of items in it} for each status of ``old_graph`` that ``new_graph`` no longer has (by id, so a
    rename is not a removal). Counts the items of the process's record type standing in that status."""
    old_ids = [s["id"] for s in (old_graph or {}).get("statuses") or [] if not s.get("system")]
    if not old_ids and (old_graph or {}).get("states"):
        old_ids = list(old_graph["states"])  # a release rendered before graphs
    new_ids = {s["id"] for s in (new_graph or {}).get("statuses") or []} | set((new_graph or {}).get("states") or [])
    removed = [s for s in old_ids if s not in new_ids]
    if not removed:
        return {}
    rt = (new_graph or {}).get("record_type") or (old_graph or {}).get("record_type")
    process = (old_graph or {}).get("process") or (new_graph or {}).get("process")
    counts = {s: 0 for s in removed}
    for loc, where in _positions(hub, process, rt).items():
        if where in counts:
            counts[where] += 1
    return {s: n for s, n in counts.items() if n}


def _positions(hub, process: str | None, record_type: str | None) -> dict[str, str]:
    """Where each item of the process stands (only its own items, never another process's of the same record type): the
    status of its latest run (a failed run stands where it failed), else its record state."""
    if not record_type:
        return {}
    meta = hub.releases.current()
    bundle = hub.releases.bundle(meta["digest"]) if meta else {}
    out: dict[str, str] = {}
    for loc, run in hub.runner._of_process(process, bundle, record_type).items():
        rec = hub.kernel.get(loc)
        out[loc] = run.get("at") if run and run.get("status") == "failed" and run.get("at") else rec["state"]
    return out


def apply_state_map(hub, process: str, mapping: dict[str, str], by: str, co: str) -> list[dict]:
    """Move the items in statuses a release removed (``mapping`` {old status: new status}) when that release comes into
    force. No validator or action runs; a Gate waiting on an item is withdrawn explicitly; each item's Track (and its
    record) says "Moved from X to Y by CO-n (state map)"; whoever it waited for, and who it now waits for, are told.
    Called by the release automation after the new release is in force. Returns [{record, run, from, to}]."""
    return hub.runner.apply_state_map(process, mapping, by, co)


def state_map_questions(hub, proposal: dict) -> list[dict]:
    """Questions for a proposal that removes statuses items are in ("4 nonconformances are in Awaiting disposition,
    which this change removes: where do they go?"): the change order answers with a state map."""
    from .compile import question
    from .release import render

    meta = hub.releases.current()
    if not meta:
        return []
    try:
        before = hub.releases.bundle(meta["digest"]).get("workflows") or {}
        after = render(proposal.get("rules") or [], hub.kernel.types).get("workflows") or {}
    except Exception:
        return []
    out = []
    for name, old in sorted(before.items()):
        new = after.get(name) or {}
        counts = items_in_removed_statuses(hub, old, new) if new else {}
        proc = next((r for r in proposal.get("rules") or [] if r.get("kind") == "process" and r.get("name") == name), None)
        src = (proc or {}).get("source") or {}
        rt = new.get("record_type") or old.get("record_type") or "item"
        for sid, n in counts.items():
            st = pg.status_of(old, sid) or {"name": sid.replace("-", " ").capitalize()}
            noun = rt.replace("-", " ") + ("" if n == 1 else "s")
            out.append(question(src.get("frame", ""), src.get("line", 0),
                                f"{n} {noun} {'is' if n == 1 else 'are'} in {st['name']}, which this change removes from {name}: where do they go?",
                                raised_by="hub", code="state-map", process=name, state=sid, count=n))
    return out
