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
import re
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
    """The policy owners who review a proposal: people only (a cog never decides configuration)."""
    return {r["principal"] for r in rules if r["kind"] == "member" and POLICY_OWNER_ROLE in r["roles"] and r.get("principal_type", "person") == "person"}


# ---------------------------------------------------------------- questions (change control v2, section 8)
# Raised by compiling: a recompile that no longer raises one closes it.
COMPILE_QUESTION_CODES = ("ambiguous", "unread", "disagreement", "member-unavailable", "outside-diff", "unread-rule")
_NEGATION = re.compile(r"\b(?:not|never|no|none|nobody|nothing|unless|except|without|cannot|neither|nor)\b|n't\b", re.I)


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def question(frame: str, line: int | None, text: str, *, raised_by: str, code: str = "ambiguous", **extra) -> dict:
    """A question on a proposal: where the prose is ambiguous ("ambiguous", a reader's), where a reading in force lost its
    line ("unread"), where the committee's members read a line differently ("disagreement"), where a member cannot read
    ("member-unavailable"), or where drafted prose does not read back as its direct change ("round-trip").

    Its id is ``q-`` and a hash of the SOP, the line and the words of the question normalised (lower case, letters and
    digits only), so a recompile that asks the same finds the same question. When members' questions are merged
    (``merge_questions``) the merged one keeps the id of the first phrasing (the first member's, in committee order)."""
    from .rules import sha256_of

    line = int(line or 0)
    return {"id": "q-" + sha256_of({"frame": frame, "line": line, "text": _words(text)})[:12], "text": text, "frame": frame, "line": line,
            "raised_by": raised_by, "status": "open", "answer": None, "by": None, "code": code, **extra}


def _raisers(q: dict) -> list[str]:
    return [x.strip() for x in str(q.get("raised_by") or "").split(",") if x.strip()]


def _overlap(a: str, b: str) -> float:
    x = {w for w in _words(a).split() if len(w) > 3}
    y = {w for w in _words(b).split() if len(w) > 3}
    return len(x & y) / len(x | y) if x | y else 0.0


def merge_questions(questions: list[dict]) -> list[dict]:
    """One question per ambiguity. Readers' questions (code "ambiguous") from different members about the same SOP and
    line (or quoting the same words) are merged into the first one asked: ``raised_by`` lists every member who asked and
    the other phrasings are kept under ``also_asked`` ({"by", "text"}). Of one member's questions about a line, each joins
    the earlier question it shares the most words with; a member's own questions about one line stay apart (it asked two
    things). The same question twice (same id) is one. Other codes are only de-duplicated by id."""
    out: list[dict] = []
    for q in questions:
        q = dict(q)
        same = next((p for p in out if p["id"] == q["id"]), None)
        if same is not None:
            same["raised_by"] = ", ".join(dict.fromkeys(_raisers(same) + _raisers(q)))
            continue
        if q.get("code", "ambiguous") == "ambiguous":
            mine = set(_raisers(q))
            near = [p for p in out if p.get("code", "ambiguous") == "ambiguous" and p.get("frame") == q.get("frame")
                    and (p.get("line") == q.get("line") or (_words(p.get("quote")) and _words(p.get("quote")) == _words(q.get("quote"))))
                    and not set(_raisers(p)) & mine]
            if near:
                best = max(near, key=lambda p: _overlap(p["text"], q["text"]))
                best["raised_by"] = ", ".join(dict.fromkeys(_raisers(best) + _raisers(q)))
                best.setdefault("also_asked", []).append({"by": q.get("raised_by"), "text": q["text"]})
                continue
        out.append(q)
    return out


def carry_questions(previous: list[dict], current: list[dict], codes=COMPILE_QUESTION_CODES) -> list[dict]:
    """The questions of a recompile, given the earlier proposal's. A question asked again keeps a person's answer; an open
    one of ``codes`` that is not asked again is closed ("no longer arises at line n"), and comes back open if it is asked
    again later; questions of other codes, and closed ones, are carried as they were. Idempotent."""
    prev = {q.get("id"): q for q in previous or []}
    now = {q.get("id") for q in current or []}
    out = []
    for q in current or []:
        old = prev.get(q.get("id"))
        if old and old.get("status") in ("answered", "overridden") and old.get("by") not in (None, "", "hub"):
            q = {**q, "status": old["status"], "answer": old.get("answer"), "by": old.get("by")}
        out.append(q)
    for old in previous or []:
        if old.get("id") in now:
            continue
        if old.get("status") == "open" and (codes is None or old.get("code", "ambiguous") in codes):
            old = {**old, "status": "answered", "answer": f"no longer arises at line {old.get('line')}", "by": "hub"}
        out.append(old)
    return out


def negated(text: str | None) -> bool:
    """Whether words say "not" in any way (not, never, no, unless, except, without, ...n't)."""
    return bool(_NEGATION.search(text or ""))


def stamp_origins(changes: list[dict], *, kind: str = "prose", by: str | None = None, at: str | None = None,
                  processes: dict | None = None) -> list[dict]:
    """Each change (delta item) says where it came from (section 8): ``origin`` (for prose, the SOP, line and words it was
    read from and who read them: ``read_by`` for a model's reading), a ``warnings`` list and ``stale: False``. A change
    that already has an origin (direct, suggested, override: change-order core) keeps it.

    Explain-back: a rule a model read is written back as the sentence the hub's own grammar would state it in
    (``origin.explained``), so reviewers see the quote and its meaning side by side; when exactly one of the two says
    "not" (or never, no, unless, except, without), the change carries a ``negation`` warning: the reading may have
    inverted the prose."""
    from .settings import sentence

    for c in changes:
        src = (c.get("rule") or {}).get("source") or {}
        if c.get("op") == "text":
            where = {"frame": c.get("frame"), "line": None, "quote": None, "read_by": None}
        else:
            where = {"frame": src.get("frame"), "line": src.get("line"), "quote": src.get("text"), "read_by": src.get("read_by") or None}
        origin = c.setdefault("origin", {"kind": kind, "by": by if by is not None else src.get("by"), **where, "at": at or src.get("at")})
        c.setdefault("warnings", [])
        c.setdefault("stale", False)
        if c.get("op") == "text" or not src.get("read_by"):
            continue
        try:
            origin.setdefault("explained", sentence({k: v for k, v in c["rule"].items() if k != "source"}, processes or {}))
        except Exception:
            continue
        quote, explained = src.get("text") or "", origin.get("explained") or ""
        if c.get("op") == "add" and negated(quote) != negated(explained) and not any(w.get("code") == "negation" for w in c["warnings"]):
            said, read = ("says", "does not") if negated(quote) else ("does not say", "does")
            w = {"code": "negation", "ref": c["id"], "accepted": None,
                 "text": f"the prose {said} “not” (or never, no, unless, except, without) and the rule as read {read}: "
                         f"“{quote}” was read as “{explained}”. Check the reading did not invert it."}
            c["warnings"].append(w)
            c.setdefault("read_warnings", []).append(dict(w))  # the compiler's own: kept when a change order rebuilds warnings
    return changes


def collapse_moves(changes: list[dict]) -> tuple[list[dict], list[dict]]:
    """A rule removed and added again that differs only in where it sits (a process whose steps moved down because lines
    were inserted above) is no change: the pair is taken out of the changes, so nobody is asked to decide noise, and
    listed as moved. Returns (changes, moved)."""
    from .release import strip_sources

    def body(rule: dict) -> str:
        return json.dumps(strip_sources({k: v for k, v in rule.items() if k != "source"}), sort_keys=True, default=str)

    removes: dict[str, list[dict]] = {}
    for c in changes:
        if c.get("op") == "remove" and not c.get("changed_to"):
            removes.setdefault(body(c["rule"]), []).append(c)
    gone: set[str] = set()
    moved = []
    by_id = {c["id"]: c for c in changes}
    for c in changes:
        if c.get("op") != "add":
            continue
        if c.get("changes_from"):  # paired as one change (a process by its name): moved only when the body is the same
            prior = by_id.get("remove:" + __import__("hubproto.rules", fromlist=["rule_key"]).rule_key(c["changes_from"]))
            pair = prior if prior is not None and body(prior["rule"]) == body(c["rule"]) and prior["id"] not in gone else None
        else:
            pair = next((r for r in removes.get(body(c["rule"]), []) if r["id"] not in gone), None)
        if pair is None:
            continue
        gone |= {c["id"], pair["id"]}
        moved.append({"kind": c["rule"].get("kind"), "name": c["rule"].get("name") or c["rule"].get("kind"), "ids": [c["id"], pair["id"]],
                      "frame": (c["rule"].get("source") or {}).get("frame"), "from_line": (pair["rule"].get("source") or {}).get("line"),
                      "to_line": (c["rule"].get("source") or {}).get("line"), "reason": "moved in the file, no semantic change"})
    return [c for c in changes if c["id"] not in gone], moved


def compile_proposal(store: FrameStore, scopes: list[str], cog_ids: set[str], releases: ReleaseStore,
                     extractor=None, now: str | None = None, record_types: dict | None = None,
                     config_digests: dict | None = None, resolve_document=None, resolve_reference=None) -> dict:
    """``resolve_reference`` resolves a citation of a document in the prose ({"document", "revision", ...}) to
    ``hubproto.references.resolve``'s answer; without it the citations are read and recorded but not checked."""
    extractor = extractor or ConventionExtractor()
    frames = store.resolve(scopes)
    rules: list[dict] = []
    notes: list[dict] = []
    questions: dict[str, dict] = {}
    for frame in frames:
        ex = extractor.extract(frame)
        rules.extend(ex.rules)
        notes.extend(ex.notes)
        for q in getattr(ex, "questions", None) or []:  # where the prose is ambiguous (a diff reading, hubproto.llm_extract)
            questions.setdefault(q["id"], q)
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
    # "Administrators may ..." where administrators is a group of the org model: the permission is the group's.
    from .groups import resolve_group_refs

    resolve_group_refs(rules)
    # Later Frames override earlier ones: a rule with the same key stated twice keeps the last source.
    by_key: dict[str, dict] = {}
    for r in rules:
        by_key[rule_key(r)] = r
    rules = list(by_key.values())
    # A sentence the grammar could not read is not an error: the grammar is the fast path, and the compile-frame
    # committee (the senior cogs) reads the diff. One that no reader read is a blocking question for a person (its
    # chair): it states no rule, it is recorded as a divergence, or the author rewrites it.
    cited = {((r.get("source") or {}).get("frame"), (r.get("source") or {}).get("line")) for r in rules}
    asked = {(q.get("frame"), ln): q for q in questions.values() if q.get("code") == "unread-rule" for ln in (q.get("lines") or [q.get("line")])}
    for n in notes:
        if n.get("severity") != "unread" or (n.get("frame"), n.get("line")) in cited:
            continue
        if (n.get("frame"), n.get("line")) in asked:  # the diff reading asked about this sentence already
            asked[(n["frame"], n["line"])]["unread_note"] = True
            continue
        q = question(n["frame"], n["line"], f"Line {n['line']}, “{n.get('text')}”, was not read: the sentence grammar does not know "
                     f"it ({n.get('note')}) and no reader read a rule from it, so nothing would be configured for it. A person "
                     "decides: it states no rule, it is recorded as a divergence, or the author rewrites it.",
                     raised_by="hub", code="unread-rule", quote=n.get("text") or "", blocking=True, lines=[n["line"]], unread_note=True)
        questions.setdefault(q["id"], q)
        asked[(n["frame"], n["line"])] = q
    findings = []
    from .guards import Finding as _Finding
    findings += [_Finding("grammar", "error", n["note"] + ": " + n["text"], frame=n["frame"], line=n["line"])
                 for n in notes if n.get("severity") == "error"]
    findings += [_Finding("model", "warning", n["note"] + (f": {n['text']}" if n.get("text") else ""), frame=n["frame"], line=n["line"])
                 for n in notes if n.get("severity") == "warning"]
    # Every rule a model read is shown to the reviewers, with the words it read it from.
    findings += [_Finding("model", "warning", f"read by {r['source']['read_by']}: “{r['source'].get('text')}” → {r['kind']}",
                          frame=r["source"].get("frame"), line=r["source"].get("line") or 0)
                 for r in rules if (r.get("source") or {}).get("read_by")]
    findings += doc_findings
    # Citations of documents in the prose (issue #8): recorded with their line; one of a document not in force is a
    # warning. A citation is no rule: it is part of the SOP's wording, reviewed with it, never an item of its own.
    from .references import document_prefix, read_references, reference_guard

    references = [ref for frame in frames for ref in read_references(frame, document_prefix(record_types))]
    if resolve_reference is not None:
        findings += reference_guard(references, resolve_reference)
    findings += schema_guard(rules)
    findings += grounding_guard(rules, store.frames)
    findings += consistency_guard(rules, cog_ids, record_types)
    from .guards import scope_guard

    findings += scope_guard(rules, set(store.frames), {n: f.scope for n, f in store.frames.items()})
    base = releases.current(now)
    old_rules = releases.rules(base["digest"]) if base else []
    from .changecontrol import keep_divergences  # an open divergence keeps its rule in force until a change order resolves it

    rules, kept = keep_divergences(rules, base)
    findings += kept
    changes, moved = collapse_moves(drift_guard(old_rules, rules))
    changes += text_changes(base.get("frames") if base else [], store.bill_of_materials(frames), rules)
    for c in changes:
        c["decisions"] = []
        src = (c.get("rule") or {}).get("source") or {}
        if src.get("read_by"):
            c["read_by"] = src["read_by"]
            c["reason"] = f"{c['reason']} (read by {src['read_by']} from “{src.get('text')}”)"
    created = now or utcnow()
    stamp_origins(changes, at=created, processes={r["name"]: r for r in rules if r.get("kind") == "process"})
    proposal = {
        "id": uuid.uuid4().hex[:12],
        "created_at": created,
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
        "questions": merge_questions(list(questions.values())),
        "moved": moved,  # rules that only moved in their file: no change to decide
        "references": references,  # citations of documents in the prose, each with what it resolved to
        "base": base["digest"] if base else None,
        # Policy owners are those of the release in force; a proposal cannot appoint its own reviewers.
        "reviewers": sorted(_reviewers(old_rules) if old_rules else _reviewers(rules)),
        "status": "open",
        "release": None,
    }
    return proposal


def _released_references(proposal: dict, rejected_text: set, current: dict | None) -> list[dict]:
    """The citations the release records: the proposal's, except for a Frame whose wording change was rejected, which
    keeps the citations of the release before."""
    from .references import recorded

    refs = [r for r in proposal.get("references") or [] if r.get("frame") not in rejected_text]
    refs += [r for r in (current or {}).get("references") or [] if r.get("frame") in rejected_text]
    return recorded(refs)


def text_changes(old_bom: list[dict], new_bom: list[dict], rules: list[dict]) -> list[dict]:
    """A change to a Frame's wording is a change the policy owners review, whether or not any rule moved: the SOP is
    what people follow and what cogs judge against. Two owners when a cog follows that Frame."""
    old = {f["name"]: f for f in old_bom or []}
    from .release import followed_by_cogs

    followed = followed_by_cogs(rules)  # what the cogs follow (derived from the SOPs that name them) and the personas they behave by
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
    """approved, rejected or pending. Stale decisions (change control: made before the baseline changed) do not count;
    an approved override's widening is what was approved; a wording change of a change order is covered by its
    signature (one entry by ``change-order:<CO>``)."""
    live = [d for d in change["decisions"] if not d.get("stale")]
    verdicts = [d["verdict"] for d in live]
    if "reject" in verdicts:
        return "rejected"
    approvers = {d["by"] for d in live if d["verdict"] == "approve"}
    if change.get("op") == "text" and any(str(a).startswith("change-order:") for a in approvers):
        return "approved"
    widening = (change.get("override") or {}).get("widening", change.get("widening")) if change.get("override") else change.get("widening")
    need = 2 if widening else 1
    if len(approvers) >= need:
        return "approved"
    return "pending"


def readiness(proposal: dict) -> list[str]:
    problems = []
    for f in blocking_findings(proposal):
        problems.append(f"{f['guard']}: {f['detail']} ({f['frame']}:{f['line']})")
    for q in proposal.get("questions") or []:  # a sentence nobody read stops a release made without a change order too
        if q.get("unread_note") and q.get("blocking") and q.get("status", "open") == "open":
            problems.append(f"question ({q.get('code')}): {q.get('text')}")
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
    if by not in proposal["reviewers"] and not (proposal.get("eco") and by == f"change-order:{proposal['eco']}"):
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
                         clauses=proposal.get("clauses", {}), frame_texts=frame_texts,
                         references=_released_references(proposal, rejected_text, current))
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
