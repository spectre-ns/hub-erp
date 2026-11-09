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

import hashlib
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
    step before it, routed through the release routing table. (A process written
    as a graph: one step per cog action, and the Gates come from the graph; see
    ``hubproto.process_graph`` and the runner.)
    """
    if process.get("statuses"):
        return render_graph_manifest(process)
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


def render_graph_manifest(process: dict) -> dict:
    """The Op manifest of a process written as a graph: one step per cog action (the actions say which run when; the
    graph says where the item goes). The first step carries the role that starts the process, as for numbered steps."""
    from .process_graph import action_steps

    init = next((t for t in process.get("transitions") or [] if t.get("type") == "initial"), None) or {}
    start_role = (init.get("gate") or {}).get("role") or "system"
    steps: list[dict] = []
    prev: dict[str, str] = {}
    for sid, cog, t in action_steps(process):
        entry = {"id": sid, "title": cog.replace("-", " ").capitalize(), "role": "system", "cog": {"id": cog, "task": "run"},
                 "input": {"record": {"$from": "inputs.record"}}, "gate": {"type": "default"}, "transition": t["id"]}
        if prev.get(t["id"]):
            entry["input"]["prior"] = {"$from": f"steps.{prev[t['id']]}.payload"}
            entry["depends_on"] = [prev[t["id"]]]
        prev[t["id"]] = sid
        steps.append(entry)
    if not steps:  # readers take the starting role from the first step
        steps.append({"id": "start", "title": init.get("name") or "Start", "role": start_role, "cog": {"id": "noop", "task": "run"},
                      "input": {"record": {"$from": "inputs.record"}}, "gate": {"type": "default"}})
    steps[0] = {**steps[0], "role": start_role}
    return {
        "schema": OP_PROFILE,
        "id": process["name"],
        "title": process["name"].replace("-", " "),
        "folder": process["folder"],
        **({"files_by_type": dict(process["files_by_type"])} if process.get("files_by_type") else {}),
        "object": process["object"],
        "inputs": [{"name": "record", "required": True}],
        "steps": steps,
        "start_role": start_role,
    }


def checkpoint_for(process: dict, step: str) -> dict | None:
    """Resolve a routing or SLA step name against a process: a checkpoint id, or the
    'approve' alias meaning the first checkpoint whose title starts with Approve.
    In a process written as a graph the checkpoints are its decisions (gated transitions)."""
    if process.get("statuses"):
        from .process_graph import gated_checkpoints

        cps = gated_checkpoints(process)
    else:
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
    """The graph a record type follows (change control 10.1), derived from the process section: statuses and
    transitions, plus the earlier state-machine keys derived from it (``hubproto.process_graph``)."""
    from .process_graph import build

    return build(process, slas, record_type)


def legacy_transitions(wf: dict | None) -> list[dict]:
    """A workflow's transitions in the earlier shape (kind automatic|human, step, on_reject), for its readers."""
    from .process_graph import legacy_transitions as _lt

    return _lt(wf)


def render(rules: list[dict], record_types: dict | None = None) -> dict:
    members: dict[str, dict] = {}
    roles: list[str] = []
    folders: list[str] = []
    permissions, routing, criteria, sod, thresholds, processes = [], [], [], [], {}, {}
    cogs: dict[str, dict] = {}
    slas: list[dict] = []
    scopes: dict[str, dict] = {}
    personas: dict[str, dict] = {}
    committees: dict[str, dict] = {}
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
        elif kind == "committee":
            committees[r["name"]] = body
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

    def type_for(obj: str) -> str:
        for tname, td in (record_types or {}).items():
            if td.get("object") == obj or tname == obj:
                return tname
        return obj

    for name, p_ in processes.items():  # a process written as a graph without a start sentence: its record type's object
        if p_.get("statuses") and not p_.get("object") and p_.get("record_type"):
            p_["object"] = ((record_types or {}).get(p_["record_type"]) or {}).get("object") or p_["record_type"]
    ops = {name: render_manifest(p) for name, p in sorted(processes.items())}
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
        "committees": dict(sorted(committees.items())),
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

    # The hub's content store (hubproto.dms.BlobStore): every Frame text a proposal was compiled from is kept there
    # under its hash, so a release records exactly the texts that were reviewed.
    blobs = None

    def keep_texts(self, texts) -> None:
        """Keep these Frame texts under their hashes (a compile keeps every text it compiled)."""
        if self.blobs is not None:
            for t in texts:
                self.blobs.put(t.encode("utf-8"))

    def text_by_sha(self, sha: str | None) -> str | None:
        """The kept Frame text with this hash, or None."""
        if self.blobs is None or not sha:
            return None
        try:
            return self.blobs.get(sha).decode("utf-8")
        except Exception:
            return None

    def frame_texts(self, d: str) -> dict[str, str]:
        """The Frame texts a release was compiled from (empty for releases made before texts were kept). Each must be
        the text its bill of materials names by hash: a stored text that is not (an earlier release read the file on
        disk when it was released, not the text that was compiled) is replaced by the kept text with that hash."""
        meta = self.get(d)
        fdir = self.root / meta["digest"] / "frames"
        if not fdir.exists():
            return {}
        bom = {f.get("name"): f.get("sha256") for f in meta.get("frames") or []}
        out = {}
        for p in sorted(fdir.glob("*.frame.md")):
            name, text = p.name[: -len(".frame.md")], p.read_text(encoding="utf-8")
            want = bom.get(name)
            if want and hashlib.sha256(text.encode("utf-8")).hexdigest() != want:
                text = self.text_by_sha(want) or text
            out[name] = text
        return out

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
