"""Rendering rules into a policy release, and the release store.

A release bundle is the deterministic configuration the runtime reads:
members and roles, folders, permissions, thresholds, routing, approval
criteria, segregation-of-duties rules, and one generated Op manifest per
process. Its identity is the SHA-256 of the canonical JSON. Release metadata
(who released it, when it takes effect, the Frame bill of materials) sits
beside the bundle and is not part of the digest, so an edit that changes no
rendered rule produces no new release.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .rules import canonical, sha256_of

PROFILE = "hubproto/policy-release [0.1]"
OP_PROFILE = "hubproto/op-manifest [0.1]"


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def render_manifest(process: dict) -> dict:
    """Turn a process rule into an Op manifest.

    Cog steps become steps. A checkpoint step becomes a human Gate on the
    step before it, routed through the release routing table.
    """
    steps: list[dict] = []
    prev_id = None
    for step in process["steps"]:
        if step["kind"] == "checkpoint":
            if not steps:
                raise ValueError("process starts with a checkpoint")
            if steps[-1].get("gate", {}).get("type") == "human":
                # Two checkpoints in a row: a no-op step carries the second Gate.
                carrier = {"id": f"reach-{step['id']}", "title": f"Reach {step['title'].lower()}", "role": "system",
                           "cog": {"id": "noop", "task": "run"}, "input": {"record": {"$from": "inputs.record"},
                                                                          "prior": {"$from": f"steps.{prev_id}.payload"}},
                           "depends_on": [prev_id], "gate": {"type": "default"}}
                steps.append(carrier)
                prev_id = carrier["id"]
            steps[-1]["gate"] = {"type": "human", "step": step["id"], "route": {"process": process["name"], "step": step["id"]},
                                 "title": step["title"], "role": step["role"]}
            continue
        entry = {
            "id": step["id"],
            "title": step["title"],
            "role": step["role"],
            "cog": {"id": step["cog"], "task": "run"},
            "input": {"record": {"$from": "inputs.record"}},
            "gate": {"type": "default"},
        }
        if step.get("swarm"):  # several cogs review in parallel and their findings merge: an Op within the Op
            entry["cog"] = {"id": "swarm", "task": "run"}
            entry["input"]["cogs"] = list(step["swarm"])
            entry["input"]["question"] = step["title"]
        if prev_id:
            entry["input"]["prior"] = {"$from": f"steps.{prev_id}.payload"}
            entry["depends_on"] = [prev_id]
        steps.append(entry)
        prev_id = step["id"]
    return {
        "schema": OP_PROFILE,
        "id": process["name"],
        "title": process["name"].replace("-", " "),
        "folder": process["folder"],
        **({"files_by_type": dict(process["files_by_type"])} if process.get("files_by_type") else {}),
        "object": process["object"],
        "inputs": [{"name": "record", "required": True}],
        "steps": steps,
    }


def checkpoint_for(process: dict, step: str) -> dict | None:
    """Resolve a routing or SLA step name against a process: a checkpoint id, or the
    'approve' alias meaning the first checkpoint whose title starts with Approve."""
    cps = [s for s in process["steps"] if s["kind"] == "checkpoint"]
    if step == "approve":
        for cp in cps:
            if cp["id"].startswith(("approv", "accept", "confirm", "authori")):
                return cp
        return None
    for cp in cps:
        if cp["id"] == step:
            return cp
    return None


def render_workflow(process: dict, slas: dict[str, int], record_type: str) -> dict:
    """The state machine a record type follows, derived from the process section."""
    states = ["open"]
    transitions: list[dict] = []
    checkpoints: list[dict] = []
    current = "open"
    pending_auto: list[str] = []
    for step in process["steps"]:
        if step["kind"] == "cog":
            pending_auto.append(step["id"])
            continue
        if checkpoints and not pending_auto:
            pending_auto.append(f"reach-{step['id']}")
        waiting = f"awaiting-{step['id']}"
        states.append(waiting)
        transitions.append({"from": current, "to": waiting, "kind": "automatic", "steps": pending_auto})
        checkpoints.append({"id": step["id"], "title": step["title"], "role": step["role"], "state": waiting,
                            "sla_days": slas.get(step["id"])})
        pending_auto = []
        current = waiting
    terminal = process.get("terminal") or "closed"
    states.append(terminal)
    reject_to = process.get("reject_to")
    cp_ids = {cp["id"] for cp in checkpoints}
    if reject_to and reject_to in cp_ids:
        on_reject = f"awaiting-{reject_to}"
    elif reject_to:
        on_reject = "returned"  # rejection returns the item to its owner for rework at that step
        states.append("returned")
    else:
        on_reject = "rejected"
        states.append("rejected")
    # Close the human transitions: each checkpoint approves into the next waiting state or the terminal.
    human = []
    for i, cp in enumerate(checkpoints):
        nxt = checkpoints[i + 1]["state"] if i + 1 < len(checkpoints) else terminal
        human.append({"from": cp["state"], "to": nxt, "kind": "human", "step": cp["id"], "title": cp["title"],
                      "on_reject": on_reject, "sla_days": cp["sla_days"]})
    if pending_auto or not checkpoints:
        transitions.append({"from": current, "to": terminal, "kind": "automatic", "steps": pending_auto})
    return {"record_type": record_type, "object": process["object"], "states": states,
            "transitions": transitions + human, "checkpoints": checkpoints, "terminal": terminal,
            "reject_to": reject_to, "sla": {cp["id"]: cp["sla_days"] for cp in checkpoints if cp["sla_days"]}}


def render(rules: list[dict], record_types: dict | None = None) -> dict:
    members: dict[str, dict] = {}
    roles: list[str] = []
    folders: list[str] = []
    permissions, routing, criteria, sod, thresholds, processes = [], [], [], [], {}, {}
    cogs: dict[str, dict] = {}
    slas: list[dict] = []
    scopes: dict[str, dict] = {}
    personas: dict[str, dict] = {}
    for r in rules:
        body = {k: v for k, v in r.items() if k != "kind"}
        kind = r["kind"]
        if kind == "role":
            roles.append(r["name"])
        elif kind == "member":
            cur = members.get(r["principal"])
            if cur:
                cur["roles"] = sorted(set(cur["roles"]) | set(r["roles"]))
                cur.setdefault("sources", []).append(r["source"])
            else:
                members[r["principal"]] = {"type": r["principal_type"], "roles": sorted(r["roles"]),
                                           "source": r["source"]}
        elif kind == "folder":
            folders.append(r["path"])
        elif kind == "permission":
            permissions.append(body)
        elif kind == "threshold":
            thresholds[r["name"]] = {"value": r["value"], "unit": r["unit"], "source": r["source"]}
        elif kind == "routing":
            routing.append({**body, "mode": r.get("mode", "assign")})
        elif kind == "sla":
            slas.append(dict(body))
        elif kind == "approval_criteria":
            criteria.append(body)
        elif kind == "sod":
            sod.append(body)
        elif kind == "process":
            processes[r["name"]] = body
        elif kind == "cog":
            cogs[r["name"]] = body
        elif kind == "scope":
            scopes[r["name"]] = body
        elif kind == "persona":
            personas[r["name"]] = body
    # Scopes and personas: each scope's Frames are those of the scopes it layers on, then its own, in order; a
    # persona's are those of its layers (scopes or Frames), then what it also reads. A cog's persona is what it works
    # from: its profile follows exactly those Frames.
    for name in scopes:
        scopes[name]["frames"] = scope_frames(scopes, name)
    for name, p in personas.items():
        p["frames"] = persona_frames(scopes, p)
        if p.get("of") == "cog" and name in cogs:
            cogs[name] = {**cogs[name], "follows": list(p["frames"]), "persona": name}
    # Resolve the 'approve' alias and bare checkpoint names to checkpoint ids.
    for r in routing + slas:
        proc = processes.get(r["process"])
        if proc and r["step"] != "*":
            cp = checkpoint_for(proc, r["step"])
            if cp:
                r["step"] = cp["id"]
    # Specific routes before default routes; the order is part of the bundle.
    routing.sort(key=lambda x: (x["process"], x["step"], 0 if x["condition"] else 1, x["source"]["line"]))
    permissions.sort(key=lambda x: (x["resource"], x["subject"], x["action"], x["object"], x["effect"]))
    criteria.sort(key=lambda x: (x["approver"], x["process"]))
    sod.sort(key=lambda x: (x["object"], x["roles"]))
    ops = {name: render_manifest(p) for name, p in sorted(processes.items())}

    def type_for(obj: str) -> str:
        for tname, td in (record_types or {}).items():
            if td.get("object") == obj or tname == obj:
                return tname
        return obj

    workflows = {}
    for name, p_ in sorted(processes.items()):
        sla_map = {x["step"]: x["days"] for x in slas if x["process"] == name}
        workflows[name] = render_workflow(p_, sla_map, p_.get("record_type") or type_for(p_["object"]))
    for name, op in ops.items():
        op["record_type"] = workflows[name]["record_type"]
    return {
        "profile": PROFILE,
        "workflows": workflows,
        "roles": sorted(set(roles)),
        "members": dict(sorted(members.items())),
        "folders": sorted(set(folders)),
        "permissions": permissions,
        "thresholds": dict(sorted(thresholds.items())),
        "routing": routing,
        "approval_criteria": criteria,
        "sod": sod,
        "processes": dict(sorted(processes.items())),
        "cogs": dict(sorted(cogs.items())),
        "scopes": dict(sorted(scopes.items())),
        "personas": dict(sorted(personas.items())),
        "ops": ops,
    }


def _dedupe(names: list[str]) -> list[str]:
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def scope_frames(scopes: dict, name: str, seen: tuple = ()) -> list[str]:
    """The Frames a scope compiles: those of the scopes it layers on (recursively), then its own."""
    if name in seen or name not in scopes:
        return []
    s = scopes[name]
    out: list[str] = []
    for parent in s.get("layers_on") or []:
        out += scope_frames(scopes, parent, seen + (name,))
    return _dedupe(out + list(s.get("compiles") or []))


def persona_frames(scopes: dict, persona: dict) -> list[str]:
    out: list[str] = []
    for layer in persona.get("layers") or []:
        out += scope_frames(scopes, layer) if layer in scopes else [layer]
    return _dedupe(out + list(persona.get("reads") or []))


def strip_sources(obj):
    """The same bundle without its ``source`` citations.

    Sources point at sentences in Frames and move when a Frame is edited;
    the rendered rules may not have changed. The digest covers only what
    the runtime acts on, so a no-op edit yields the same release.
    """
    if isinstance(obj, dict):
        return {k: strip_sources(v) for k, v in obj.items() if k not in ("source", "sources", "line", "n")}
    if isinstance(obj, list):
        return [strip_sources(x) for x in obj]
    return obj


def digest(bundle: dict) -> str:
    return sha256_of(strip_sources(bundle))


class ReleaseStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        if not self.index_path.exists():
            self.index_path.write_text("[]")

    def _index(self) -> list[dict]:
        return json.loads(self.index_path.read_text())

    def save(self, bundle: dict, *, effective_from: str, released_by: str, proposal_id: str,
             bom: list[dict], rules: list[dict], parent: str | None, now: str | None = None,
             config: dict | None = None, clauses: dict | None = None, frame_texts: dict[str, str] | None = None) -> dict:
        """Save a release. Its identity is the bundle's digest, unless that bundle was already released from other
        Frame texts: then the identity also covers the Frames' content, so a change to an SOP's wording is a new
        release in the history, never a rewrite of an earlier one. An identical release (same bundle, same Frames)
        returns the entry already there, unchanged."""
        bd = digest(bundle)
        shas = sorted((f.get("name"), f.get("sha256")) for f in bom or [])
        same_bundle = [m for m in self._index() if m.get("bundle_digest", m["digest"]) == bd]
        in_force = (self.current(now) or {}).get("digest")
        for m in same_bundle:
            if m["digest"] == in_force and sorted((f.get("name"), f.get("sha256")) for f in m.get("frames") or []) == shas:
                return m  # nothing new: the release in force, from the same texts
        # A bundle released before (from other texts, or an older release being put back) is a new entry in the history.
        d = bd if not same_bundle else sha256_of({"bundle": bd, "frames": shas, "parent": parent, "at": now or utcnow()})
        rel_dir = self.root / d
        meta = {
            "digest": d,
            "bundle_digest": bd,
            "created_at": now or utcnow(),
            "effective_from": effective_from,
            "released_by": released_by,
            "proposal_id": proposal_id,
            "parent": parent,
            "frames": bom,
            "config": config or {},
            "clauses": clauses or {},
            "inheritance": "transitive",
        }
        rel_dir.mkdir(exist_ok=True)
        (rel_dir / "bundle.json").write_text(canonical(bundle))
        (rel_dir / "rules.json").write_text(json.dumps(rules, indent=1, sort_keys=True))
        (rel_dir / "release.json").write_text(json.dumps(meta, indent=1))
        if frame_texts:
            fdir = rel_dir / "frames"
            fdir.mkdir(exist_ok=True)
            for name, text in frame_texts.items():
                (fdir / f"{name}.frame.md").write_text(text, encoding="utf-8")
        index = self._index()
        index.append(meta)
        self.index_path.write_text(json.dumps(index, indent=1))
        return meta

    def frame_texts(self, d: str) -> dict[str, str]:
        """The Frame texts a release was compiled from (empty for releases made before texts were kept)."""
        meta = self.get(d)
        fdir = self.root / meta["digest"] / "frames"
        if not fdir.exists():
            return {}
        return {p.name[: -len(".frame.md")]: p.read_text(encoding="utf-8") for p in sorted(fdir.glob("*.frame.md"))}

    def list(self) -> list[dict]:
        """Releases in the order they were made (timestamps are second-resolution, so the index order breaks ties)."""
        return [m for _, m in sorted(enumerate(self._index()), key=lambda im: (im[1]["created_at"], im[0]))]

    def get(self, d: str) -> dict:
        matches = [m for m in self._index() if m["digest"].startswith(d)]
        if len(matches) != 1:
            raise KeyError(f"release {d!r}: {len(matches)} matches")
        return matches[0]

    extra_folders = None  # a callable giving the library's folders (hubproto.library), set by the Hub
    extra_rights = None  # a callable giving {top-level library folder: the folder whose rights it takes}

    def bundle(self, d: str) -> dict:
        meta = self.get(d)
        b = json.loads((self.root / meta["digest"] / "bundle.json").read_text())
        if self.extra_folders is not None:
            # Folders people added to the library: subfolders take their parent's rights (rights match by prefix); a
            # top-level one takes the rights of the folder it names (library_rights).
            policy = list(b.get("folders") or [])
            rights = {k: v for k, v in (self.extra_rights() if self.extra_rights else {}).items() if v in policy}
            roots = policy + list(rights)
            extra = [f for f in self.extra_folders() if f in rights or any(f.startswith(p + "/") for p in roots)]
            if extra:
                b["folders"] = sorted(set(policy) | set(extra))
                b["library_folders"] = sorted(extra)
                b["library_rights"] = rights
        return b

    def rules(self, d: str) -> list[dict]:
        meta = self.get(d)
        return json.loads((self.root / meta["digest"] / "rules.json").read_text())

    def current(self, at: str | None = None) -> dict | None:
        at = at or utcnow()
        live = [m for m in self._index() if m["effective_from"] <= at]
        if not live:
            return None
        order = {m["digest"]: i for i, m in enumerate(self._index())}
        live.sort(key=lambda m: (m["effective_from"], m["created_at"], order.get(m["digest"], 0)))
        return live[-1]
