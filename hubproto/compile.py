"""The policy compile pipeline: Frames -> proposal -> Gate -> release.

Steps (each one is a Cog step in the production design; here they are
functions so the prototype runs in one process):

1. resolve      the Frames in scope, in application order (deterministic)
2. extract      rule statements with sentence-level sources
3. guard        schema, grounding, consistency, drift
4. gate         a policy owner decides each change; widenings need two
5. render       the release bundle; digest; effective date
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from .extract import ConventionExtractor
from .frames import FrameStore
from .guards import consistency_guard, drift_guard, grounding_guard, schema_guard
from .release import ReleaseStore, render, utcnow
from .rules import rule_key

POLICY_OWNER_ROLE = "policy-owner"


class CompileError(Exception):
    pass


def _reviewers(rules: list[dict]) -> set[str]:
    return {r["principal"] for r in rules if r["kind"] == "member" and POLICY_OWNER_ROLE in r["roles"]}


def compile_proposal(store: FrameStore, scopes: list[str], cog_ids: set[str], releases: ReleaseStore,
                     extractor=None, now: str | None = None, record_types: dict | None = None,
                     config_digests: dict | None = None, resolve_document=None) -> dict:
    extractor = extractor or ConventionExtractor()
    frames = store.resolve(scopes)
    rules: list[dict] = []
    notes: list[dict] = []
    for frame in frames:
        ex = extractor.extract(frame)
        rules.extend(ex.rules)
        notes.extend(ex.notes)
    # A cog's linked documents (its prompt, its references) are pinned to the version current at compile time, so a
    # release says exactly what each cog read; a newer version of the document is a change the policy owners review.
    doc_findings = []
    for r in rules:
        if r.get("kind") != "cog":
            continue
        src = r.get("source") or {}
        for field, ref in [("prompt_doc", r.get("prompt_from"))] + [("reference_docs", x) for x in r.get("references") or []]:
            if not ref:
                continue
            pin = resolve_document(ref) if resolve_document else None
            if pin is None:
                from .guards import Finding as _F

                doc_findings.append(_F("source-grounding", "error", f"cog {r['name']} links document {ref!r}, which does not exist",
                                       frame=src.get("frame", ""), line=int(src.get("line", 0) or 0)))
                continue
            if field == "prompt_doc":
                r["prompt_doc"] = pin
            else:
                r.setdefault("reference_docs", []).append(pin)
    # Later Frames override earlier ones: a rule with the same key stated twice keeps the last source.
    by_key: dict[str, dict] = {}
    for r in rules:
        by_key[rule_key(r)] = r
    rules = list(by_key.values())
    findings = []
    from .guards import Finding as _Finding
    findings += [_Finding("grammar", "error", n["note"] + ": " + n["text"], frame=n["frame"], line=n["line"])
                 for n in notes if n.get("severity") == "error"]
    findings += doc_findings
    findings += schema_guard(rules)
    findings += grounding_guard(rules, store.frames)
    findings += consistency_guard(rules, cog_ids, record_types)
    base = releases.current(now)
    old_rules = releases.rules(base["digest"]) if base else []
    changes = drift_guard(old_rules, rules)
    changes += text_changes(base.get("frames") if base else [], store.bill_of_materials(frames), rules)
    for c in changes:
        c["decisions"] = []
    proposal = {
        "id": uuid.uuid4().hex[:12],
        "created_at": now or utcnow(),
        "extractor": extractor.name,
        "scopes": scopes,
        "frames": store.bill_of_materials(frames),
        "config": config_digests or {},
        "record_types": record_types or {},
        "clauses": {f.name: list(f.frontmatter.get("clauses") or []) for f in frames},
        "rules": rules,
        "notes": notes,
        "findings": [f.as_dict() for f in findings],
        "changes": changes,
        "base": base["digest"] if base else None,
        # Policy owners are those of the release in force; a proposal cannot appoint its own reviewers.
        "reviewers": sorted(_reviewers(old_rules) if old_rules else _reviewers(rules)),
        "status": "open",
        "release": None,
    }
    return proposal


def text_changes(old_bom: list[dict], new_bom: list[dict], rules: list[dict]) -> list[dict]:
    """A change to a Frame's wording is a change the policy owners review, whether or not any rule moved: the SOP is
    what people follow and what cogs judge against. Two owners when a cog follows that Frame."""
    old = {f["name"]: f for f in old_bom or []}
    followed = {n for r in rules if r.get("kind") == "cog" for n in (r.get("follows") or []) + (r.get("reviews") or [])}
    out = []
    for f in new_bom or []:
        prior = old.get(f["name"])
        if prior and prior.get("sha256") == f.get("sha256"):
            continue
        what = (f"the text of {f['name']} changes (version {prior.get('version')} → {f.get('version')})" if prior
                else f"{f['name']} is new (version {f.get('version')})")
        if f["name"] in followed:
            what += "; a cog follows it, so it changes what the cog judges against"
        out.append({"id": f"text:{f['name']}:{str(f.get('sha256'))[:12]}", "op": "text", "frame": f["name"],
                    "rule": {"kind": "text", "frame": f["name"], "sha256": f.get("sha256"), "version": f.get("version")},
                    "widening": f["name"] in followed and prior is not None, "reason": what})
    return out


def blocking_findings(proposal: dict) -> list[dict]:
    return [f for f in proposal["findings"] if f["severity"] == "error"]


def decide_change(proposal: dict, change_id: str, verdict: str, by: str, meaning: str = "") -> dict:
    if proposal["status"] != "open":
        raise CompileError("proposal is closed")
    if by not in proposal["reviewers"]:
        raise CompileError(f"{by} is not a policy owner and cannot review changes")
    if verdict not in ("approve", "reject", "defer"):
        raise CompileError("verdict must be approve, reject or defer")
    for c in proposal["changes"]:
        if c["id"] == change_id:
            c["decisions"] = [d for d in c["decisions"] if d["by"] != by]
            c["decisions"].append({"by": by, "verdict": verdict, "at": utcnow(),
                                   "meaning": meaning or f"{verdict} change {change_id}",
                                   "change_digest": rule_key(c["rule"])})
            return c
    raise CompileError(f"no change {change_id!r}")


def change_state(change: dict) -> str:
    verdicts = [d["verdict"] for d in change["decisions"]]
    if "reject" in verdicts:
        return "rejected"
    approvers = {d["by"] for d in change["decisions"] if d["verdict"] == "approve"}
    need = 2 if change["widening"] else 1
    if len(approvers) >= need:
        return "approved"
    return "pending"


def readiness(proposal: dict) -> list[str]:
    problems = []
    for f in blocking_findings(proposal):
        problems.append(f"{f['guard']}: {f['detail']} ({f['frame']}:{f['line']})")
    for c in proposal["changes"]:
        if change_state(c) == "pending":
            need = 2 if c["widening"] else 1
            problems.append(f"change {c['id']} needs {need} approval(s): {c['reason']}")
    return problems


def finalize(proposal: dict, releases: ReleaseStore, *, by: str, effective_from: str | None = None,
             now: str | None = None, frame_texts: dict[str, str] | None = None) -> dict:
    if proposal.get("status") != "open":
        raise CompileError(f"proposal #{proposal.get('number') or proposal['id']} is {proposal.get('status')}"
                           + (f" (replaced by #{proposal['superseded_by_number']}: release that one)" if proposal.get("superseded_by_number") else ""))
    if by not in proposal["reviewers"]:
        raise CompileError(f"{by} is not a policy owner and cannot release (policy owners: {', '.join(proposal['reviewers']) or 'none'})")
    current = releases.current(now)
    if (current["digest"] if current else None) != proposal.get("base"):
        raise CompileError("the proposal is based on release "
                           f"{(proposal.get('base') or 'none')[:12]} but {(current['digest'] if current else 'none')[:12]} is in force; "
                           "compile again so the change is reviewed against the current policy")
    problems = readiness(proposal)
    if problems:
        raise CompileError("proposal is not ready:\n  " + "\n  ".join(problems))
    rejected = {c["id"] for c in proposal["changes"] if change_state(c) == "rejected"}
    rejected_keys = {rule_key(c["rule"]) for c in proposal["changes"] if c["id"] in rejected and c["op"] == "add"}
    kept_old = [c["rule"] for c in proposal["changes"] if c["id"] in rejected and c["op"] == "remove"]
    rules = [r for r in proposal["rules"] if rule_key(r) not in rejected_keys] + kept_old
    bundle = render(rules, proposal.get("record_types") or None)
    # A rejected wording change keeps the Frame as released before: its entry in the bill of materials, its text.
    rejected_text = {c["frame"] for c in proposal["changes"] if c["id"] in rejected and c.get("op") == "text"}
    old_bom = {f["name"]: f for f in (current or {}).get("frames") or []}
    bom = [old_bom[f["name"]] if f["name"] in rejected_text and f["name"] in old_bom else f for f in proposal["frames"]]
    meta = releases.save(bundle, effective_from=effective_from or (now or utcnow()), released_by=by,
                         proposal_id=proposal["id"], bom=bom, rules=rules,
                         parent=proposal["base"], now=now, config=proposal.get("config", {}),
                         clauses=proposal.get("clauses", {}), frame_texts=frame_texts)
    proposal["status"] = "released"
    proposal["release"] = meta["digest"]
    return meta


class ProposalStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, proposal: dict) -> Path:
        path = self.root / f"{proposal['id']}.json"
        path.write_text(json.dumps(proposal, indent=1, sort_keys=True))
        return path

    def load(self, pid: str) -> dict:
        matches = list(self.root.glob(f"{pid}*.json"))
        if len(matches) != 1:
            raise CompileError(f"proposal {pid!r}: {len(matches)} matches")
        return json.loads(matches[0].read_text())

    def list(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted(self.root.glob("*.json"))]
