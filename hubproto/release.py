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
            steps[-1]["gate"] = {"type": "human", "step": step["id"], "route": {"process": process["name"], "step": "approve"},
                                 "title": step["title"]}
            continue
        entry = {
            "id": step["id"],
            "title": step["title"],
            "role": step["role"],
            "cog": {"id": step["cog"], "task": "run"},
            "input": {"record": {"$from": "inputs.record"}},
            "gate": {"type": "default"},
        }
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
        "object": process["object"],
        "inputs": [{"name": "record", "required": True}],
        "steps": steps,
    }


def render(rules: list[dict]) -> dict:
    members: dict[str, dict] = {}
    roles: list[str] = []
    folders: list[str] = []
    permissions, routing, criteria, sod, thresholds, processes = [], [], [], [], {}, {}
    for r in rules:
        body = {k: v for k, v in r.items() if k != "kind"}
        kind = r["kind"]
        if kind == "role":
            roles.append(r["name"])
        elif kind == "member":
            members[r["principal"]] = {"type": r["principal_type"], "roles": sorted(r["roles"]),
                                       "source": r["source"]}
        elif kind == "folder":
            folders.append(r["path"])
        elif kind == "permission":
            permissions.append(body)
        elif kind == "threshold":
            thresholds[r["name"]] = {"value": r["value"], "unit": r["unit"], "source": r["source"]}
        elif kind == "routing":
            routing.append(body)
        elif kind == "approval_criteria":
            criteria.append(body)
        elif kind == "sod":
            sod.append(body)
        elif kind == "process":
            processes[r["name"]] = body
    # Specific routes before default routes; the order is part of the bundle.
    routing.sort(key=lambda x: (x["process"], x["step"], 0 if x["condition"] else 1, x["source"]["line"]))
    permissions.sort(key=lambda x: (x["resource"], x["subject"], x["action"], x["object"], x["effect"]))
    criteria.sort(key=lambda x: (x["approver"], x["process"]))
    sod.sort(key=lambda x: (x["object"], x["roles"]))
    ops = {name: render_manifest(p) for name, p in sorted(processes.items())}
    return {
        "profile": PROFILE,
        "roles": sorted(set(roles)),
        "members": dict(sorted(members.items())),
        "folders": sorted(set(folders)),
        "permissions": permissions,
        "thresholds": dict(sorted(thresholds.items())),
        "routing": routing,
        "approval_criteria": criteria,
        "sod": sod,
        "processes": dict(sorted(processes.items())),
        "ops": ops,
    }


def strip_sources(obj):
    """The same bundle without its ``source`` citations.

    Sources point at sentences in Frames and move when a Frame is edited;
    the rendered rules may not have changed. The digest covers only what
    the runtime acts on, so a no-op edit yields the same release.
    """
    if isinstance(obj, dict):
        return {k: strip_sources(v) for k, v in obj.items() if k != "source"}
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
             bom: list[dict], rules: list[dict], parent: str | None, now: str | None = None) -> dict:
        d = digest(bundle)
        rel_dir = self.root / d
        meta = {
            "digest": d,
            "created_at": now or utcnow(),
            "effective_from": effective_from,
            "released_by": released_by,
            "proposal_id": proposal_id,
            "parent": parent,
            "frames": bom,
            "inheritance": "transitive",
        }
        # Re-saving the same digest refreshes the citations (same semantics, current Frame lines).
        rel_dir.mkdir(exist_ok=True)
        (rel_dir / "bundle.json").write_text(canonical(bundle))
        (rel_dir / "rules.json").write_text(json.dumps(rules, indent=1, sort_keys=True))
        (rel_dir / "release.json").write_text(json.dumps(meta, indent=1))
        index = [m for m in self._index() if m["digest"] != d]
        index.append(meta)
        self.index_path.write_text(json.dumps(index, indent=1))
        return meta

    def list(self) -> list[dict]:
        return sorted(self._index(), key=lambda m: (m["created_at"], m["digest"]))

    def get(self, d: str) -> dict:
        matches = [m for m in self._index() if m["digest"].startswith(d)]
        if len(matches) != 1:
            raise KeyError(f"release {d!r}: {len(matches)} matches")
        return matches[0]

    def bundle(self, d: str) -> dict:
        meta = self.get(d)
        return json.loads((self.root / meta["digest"] / "bundle.json").read_text())

    def rules(self, d: str) -> list[dict]:
        meta = self.get(d)
        return json.loads((self.root / meta["digest"] / "rules.json").read_text())

    def current(self, at: str | None = None) -> dict | None:
        at = at or utcnow()
        live = [m for m in self._index() if m["effective_from"] <= at]
        if not live:
            return None
        live.sort(key=lambda m: (m["effective_from"], m["created_at"]))
        return live[-1]
