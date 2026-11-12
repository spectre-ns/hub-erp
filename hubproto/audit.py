"""The internal audit, run by the auditor cog (issue #25): exact checks, structured findings guarded against their
evidence, the quality manager's verdict on each finding, and the actions raised from the accepted ones.

The auditor cog holds the internal-auditor role and nothing more. It runs the ``internal-audit`` process of the audit
SOP like a person in that seat: it plans the audit (``plan_audit``), the item waits for it at "Record the findings",
it does that work and completes the step with the findings (``submit_findings``); the quality manager accepts or
rejects each finding with one signed decision (``accept_findings``); the "Raise the actions" step then files a
nonconformance or a CAPA for each accepted finding that needs one, under the quality manager's grant. The cog signs
nothing that gives authority: its step is its own assigned work, and the only decision on the findings is a person's.

What is deterministic here (no model in any of it):

- ``audit_checks``: findings computed exactly from the records and the Track: items overdue against their due date,
  approvals by whoever prepared the item, decisions by someone the item was not routed to, training not assigned when
  a document took effect, the hash chains (kernel events and every run's Track, with its signatures), standard
  clauses no Frame cites (``config/standard-clauses.json``: clause numbers and short titles only), and audits whose
  auditor owns a process they audit.
- ``validate_finding``: the cog's findings are data. Each is parsed into a fixed shape (clause, criterion, found,
  evidence, grade, action) and every evidence reference must resolve to something that exists (a record, a Track
  entry, a kernel event, a release); a finding that cites nothing real is refused, and refused again at the Gate
  (``decision_guard``, before the fields are set; ``start_guard`` when an audit is filed) whatever path it came by.
- ``programme``: the processes in the release in force, the one audited longest ago first, never one the auditor
  owns (the audit SOP: "Auditors do not audit processes they own").

A model takes part only in the cog's agent run (reading samples against the SOP text and wording findings); its
output goes through ``validate_finding`` before anything is kept.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

from . import authz
from .kernel import KernelGuardError

CLAUSES_FILE = "standard-clauses.json"
GRADES = ("major", "minor", "observation", "opportunity")
ACTIONS = ("none", "ncr", "capa")
VERDICTS = ("accept", "reject")
PROCESS = "internal-audit"
RECORD_STEP = "record-the-findings"
ACCEPT_STEP = "accept-the-findings"
LIMITS = {"criterion": 600, "found": 1500, "evidence": 12, "title": 160}

# A run of a cog in the internal auditor's seat (an audit) gets a larger budget than other agent runs (5 actions, 10
# model rounds by default): an audit records a finding per action. It stays bounded: the environment may move it, never
# past the ceiling.
AUDIT_BUDGET = {"actions": 30, "rounds": 40, "max_tokens": 2000}
AUDIT_CEILING = {"actions": 80, "rounds": 100}
AUDIT_ROLE = "internal-auditor"


def audit_budget() -> dict:
    """The budget of an audit run: HUBPROTO_AUDIT_ACTIONS and HUBPROTO_AUDIT_ROUNDS, else the defaults, never past the
    ceiling and never below one."""
    out = dict(AUDIT_BUDGET)
    for key, env in (("actions", "HUBPROTO_AUDIT_ACTIONS"), ("rounds", "HUBPROTO_AUDIT_ROUNDS")):
        try:
            v = int(os.environ.get(env) or out[key])
        except ValueError:
            v = out[key]
        out[key] = max(1, min(v, AUDIT_CEILING[key]))
    return out


def is_audit_run(bundle: dict, cog: str) -> bool:
    """A cog holding the internal auditor's role is auditing whenever it works on its own."""
    m = (bundle.get("members") or {}).get(cog) or {}
    return m.get("type") == "cog" and AUDIT_ROLE in (m.get("roles") or [])


# ---------------------------------------------------------------- the criteria: clause numbers and titles
def load_clauses(config_dir: Path) -> dict:
    """{"default": key, "standards": {key: {"title", "clauses": {number: short title}}}}; empty when the file is missing."""
    path = Path(config_dir) / CLAUSES_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"default": "iso9001", "standards": {}}
    data.pop("$comment", None)
    return data


def parse_clause(ref: str, default: str = "iso9001") -> tuple[str, str]:
    """"9.2" -> ("iso9001", "9.2"); "iso13485:8.2.4" -> ("iso13485", "8.2.4")."""
    ref = str(ref or "").strip()
    std, sep, num = ref.partition(":")
    return (std.strip().lower(), num.strip()) if sep else (default, ref)


def clause_title(criteria: dict, ref: str) -> str | None:
    std, num = parse_clause(ref, criteria.get("default") or "iso9001")
    s = (criteria.get("standards") or {}).get(std) or {}
    t = (s.get("clauses") or {}).get(num)
    return f"{s.get('title', std)} {num} {t}" if t else None


def clause_key(num: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in str(num).split("."))


def uncovered_clauses(criteria: dict, claimed: dict[str, list[str]]) -> dict[str, list[str]]:
    """{standard: [clause numbers no Frame cites]}. A Frame that cites a clause covers the clauses under it (7.5 covers
    7.5.1), as the profile's conformance checklist counts them."""
    default = criteria.get("default") or "iso9001"
    cited: dict[str, set[str]] = {}
    for clauses in claimed.values():
        for c in clauses:
            std, num = parse_clause(c, default)
            cited.setdefault(std, set()).add(num)
    out = {}
    for std, s in (criteria.get("standards") or {}).items():
        mine = cited.get(std, set())
        out[std] = [n for n in sorted(s.get("clauses") or {}, key=clause_key)
                    if not any(n == c or n.startswith(c + ".") for c in mine)]
    return out


# ---------------------------------------------------------------- evidence: what a finding may cite
class Evidence:
    """Resolves evidence references against a hub's state: a record ("NCR-2026-0001" or its locator), a Track entry
    ("track:<run id>#<seq>"), a kernel event ("event:<seq>") or a release ("release:<digest, 8 or more characters>").
    Built from the kernel alone (its state directory), so the Gate's guard can use it too. A record, a Track entry and
    a kernel event carry the folder of the record they are about (``folder``), so a reader can be held to it."""

    TRACK = re.compile(r"^track:(?P<run>[0-9a-zA-Z_-]{4,64})#(?P<seq>\d{1,7})$")
    EVENT = re.compile(r"^event:(?P<seq>\d{1,9})$")
    RELEASE = re.compile(r"^release:(?P<digest>[0-9a-f]{8,64})$")
    NUMBER = re.compile(r"^[A-Za-z]{2,12}-[0-9A-Za-z-]{1,24}$")

    def __init__(self, kernel):
        self.kernel = kernel
        self.state = Path(kernel.path).parent

    def _folder_of(self, locator) -> str | None:
        rec = self.kernel.get(str(locator)) if locator else None
        return rec["folder"] if rec else None

    def resolve(self, ref) -> dict | None:
        """{"kind", "ref", "folder"?, ...what it is} or None when it does not exist."""
        ref = str(ref or "").strip()
        if not ref or len(ref) > 120:
            return None
        m = self.TRACK.match(ref)
        if m:
            run_dir = self.state / "runs" / m["run"]
            path = run_dir / "track.jsonl"
            if not path.exists():
                return None
            seq = int(m["seq"])
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("seq") == seq:
                    try:
                        record = json.loads((run_dir / "state.json").read_text(encoding="utf-8")).get("record")
                    except (OSError, ValueError):
                        record = None
                    return {"kind": "track", "ref": ref, "type": ev.get("type"), "run": m["run"], "seq": seq, "by": ev.get("by"),
                            "step": ev.get("step"), "at": ev.get("at"), "folder": self._folder_of(record)}
            return None
        m = self.EVENT.match(ref)
        if m:
            row = self.kernel.db.execute("SELECT seq, type, aggregate, by, at FROM events WHERE seq = ?", (int(m["seq"]),)).fetchone()
            return {"kind": "event", "ref": ref, "type": row[1], "record": row[2], "by": row[3], "at": row[4],
                    "folder": self._folder_of(row[2])} if row else None
        m = self.RELEASE.match(ref)
        if m:
            index = self.state / "releases" / "index.json"
            try:
                metas = json.loads(index.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                metas = []
            hit = [x for x in metas if isinstance(x, dict) and str(x.get("digest", "")).startswith(m["digest"])]
            return {"kind": "release", "ref": ref, "digest": hit[0]["digest"]} if len(hit) == 1 else None
        if not (ref.startswith("rec:") or self.NUMBER.match(ref)):
            return None
        rec = self.kernel.get(ref if ref.startswith("rec:") else ref.upper())
        if rec is None:
            return None
        return {"kind": "record", "ref": rec["number"], "locator": rec["locator"], "type": rec["type"], "folder": rec["folder"],
                "state": rec["state"]}

    def missing(self, refs) -> list[str]:
        return [str(r) for r in refs if self.resolve(r) is None]


def reader_for(bundle: dict, member: str):
    """fn(folder) -> bool: what this member may read, under the release in force."""
    cache: dict[str, bool] = {}

    def may_read(folder: str) -> bool:
        if folder not in cache:
            cache[folder] = authz.check(bundle, member, "read", "*", folder)["allowed"]
        return cache[folder]
    return may_read


# ---------------------------------------------------------------- findings: the cog's output is data
FINDING_ID = re.compile(r"^F-\d{1,3}$")


def validate_finding(raw, *, kernel, criteria: dict, reader=None) -> dict:
    """A finding in its fixed shape, or ValueError with a sentence saying what is wrong. Nothing in it is executed:
    text is kept as text, the clause must be one of the criteria, the grade and the action are from closed lists,
    and every evidence reference must exist and, when ``reader`` (fn(folder) -> bool) is given, be about something
    it may read: a record, a Track entry or a kernel event is held to its record's folder."""
    if not isinstance(raw, dict):
        raise ValueError("a finding is an object with clause, criterion, found, evidence and grade")
    unknown = sorted(set(raw) - {"clause", "criterion", "found", "evidence", "grade", "action", "process", "source", "title"})
    if unknown:
        raise ValueError(f"a finding has no field {', '.join(unknown)}; it has clause, criterion, found, evidence, grade, action, process and title")
    clause = str(raw.get("clause") or "").strip()
    if not clause_title(criteria, clause):
        raise ValueError(f"{clause or 'no clause'} is not a clause of the audit criteria (ISO 9001:2015 by number, e.g. 8.7; others as "
                         "iso13485:8.2.4, 21cfr820:820.35 or 21cfr11:11.10)")
    out = {"clause": clause}
    for key in ("criterion", "found"):
        v = raw.get(key)
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"a finding needs {key}: {'what was expected, quoting the SOP or naming the clause' if key == 'criterion' else 'what was found'}")
        out[key] = " ".join(v.split())[: LIMITS[key]]
    grade = str(raw.get("grade") or "").strip().lower()
    if grade not in GRADES:
        raise ValueError(f"the grade is one of {', '.join(GRADES)}, not {grade or 'nothing'}")
    out["grade"] = grade
    action = str(raw.get("action") or "none").strip().lower()
    if action not in ACTIONS:
        raise ValueError(f"the action is one of {', '.join(ACTIONS)} (a proposed nonconformance or CAPA), not {action}")
    out["action"] = action
    ev = raw.get("evidence")
    if isinstance(ev, str):
        ev = [ev]
    if not isinstance(ev, list) or not ev or not all(isinstance(x, (str, int)) for x in ev):
        raise ValueError("a finding cites its evidence: a list of record numbers and Track entries (track:<run>#<seq>)")
    ev = list(dict.fromkeys(str(x).strip() for x in ev))[: LIMITS["evidence"]]
    resolver = Evidence(kernel)
    missing, refs = [], []
    for r in ev:
        hit = resolver.resolve(r)
        if hit is None:
            missing.append(r)
        elif reader is not None and hit.get("folder") and not reader(hit["folder"]):
            missing.append(r)
        else:
            refs.append(hit["ref"] if hit["kind"] == "record" else r)
    if missing:
        raise ValueError(f"the evidence {', '.join(missing)} does not exist (or may not be read): cite only records and Track entries "
                         "the tools returned")
    out["evidence"] = refs
    for key in ("process", "title"):
        if raw.get(key):
            out[key] = " ".join(str(raw[key]).split())[: LIMITS["title"]]
    src = str(raw.get("source") or "sample").strip().lower()
    out["source"] = src if src in ("check", "sample") else "sample"
    return out


def check_findings(kernel, findings, *, by: str | None, reader=None, criteria: dict | None = None) -> list[dict]:
    """Every finding on an audit in its fixed shape, with an id (F-1, F-2 ...) and its recorder: ValueError otherwise.
    ``by`` is who records them now (their ``by`` is set to it); None checks findings already on the record as they are."""
    criteria = criteria or load_clauses(kernel.config_dir)
    if not isinstance(findings, list):
        raise ValueError("findings are a list, one entry per finding")
    out, seen = [], set()
    for i, f in enumerate(findings, 1):
        fid = str((f or {}).get("id") or "") if isinstance(f, dict) else ""
        if not FINDING_ID.match(fid):
            raise ValueError(f"finding {i} has no id: every finding on an audit is numbered (F-1, F-2 ...) and has the fixed shape "
                             "(clause, criterion, found, evidence, grade, action)")
        if fid in seen:
            raise ValueError(f"two findings are numbered {fid}")
        seen.add(fid)
        if by is not None and f.get("by") not in (None, "", by):
            raise ValueError(f"{fid} says it was recorded by {f['by']}; the findings are recorded by {by}, who records them now")
        try:
            clean = validate_finding({k: v for k, v in f.items() if k not in ("id", "by")}, kernel=kernel, criteria=criteria, reader=reader)
        except ValueError as e:
            raise ValueError(f"{fid}: {e}") from None
        out.append({"id": fid, **clean, "by": by if by is not None else f.get("by")})
    return out


def check_independence(bundle: dict, auditor: str, processes) -> None:
    """The audit SOP: "Auditors do not audit processes they own." KernelGuardError naming the processes otherwise. An
    audit naming no process audits every one."""
    procs = list(processes or []) or sorted((bundle.get("processes") or {}))
    mine = sorted(set(procs) & owned_processes(bundle, auditor))
    if mine:
        raise KernelGuardError("audit-independence", f"{auditor} owns {', '.join(mine)} (they decide a step in it, or a Routing sentence sends one to them) and does not "
                                                     f"audit {'it' if len(mine) == 1 else 'them'} (audit-sop: Auditors do not audit processes "
                                                     "they own); name the processes to audit, and another auditor for these")


NOT_AT_START = ("findings", "finding_decisions", "actions_raised")


def start_guard(kernel, bundle: dict, fields: dict, owner: str) -> None:
    """An audit as it is filed, whoever files it and by whatever path: no findings, verdicts or actions yet (they are
    recorded at their own steps), and an auditor who owns none of the processes it audits."""
    given = [k for k in NOT_AT_START if fields.get(k) not in (None, "", [], {})]
    if given:
        raise KernelGuardError("audit-start", f"an audit starts without {', '.join(given)}: the auditor records the findings at Record the "
                                              "findings, and the quality manager gives the verdicts at Accept the findings")
    check_independence(bundle, authz.principal_name(str(fields.get("auditor") or owner)), fields.get("processes"))


def decision_guard(kernel, rec: dict, verdict: str, *, by: str, fields: dict, bundle: dict) -> dict:
    """The Gate's guard on an audit, before the decision's fields are set; returns the fields to set. At "Record the
    findings": the auditor owns none of the processes, every finding has an id and the fixed shape and cites evidence
    that exists and the auditor may read, and no verdict is given. At "Accept the findings": the findings stand as
    recorded, a person who is not the auditor gives a verdict on each (a rejection with its reason) and is recorded as
    the one who gave it. Verdicts are given nowhere else."""
    fields = dict(fields or {})
    f = rec.get("fields") or {}
    state = rec.get("state") or ""
    at_record, at_accept = state == f"awaiting-{RECORD_STEP}", state == f"awaiting-{ACCEPT_STEP}"
    if "finding_decisions" in fields and not at_accept:
        raise KernelGuardError("audit-verdicts", "the verdicts on the findings are the quality manager's, given at Accept the findings")
    if "findings" in fields and not at_record and fields["findings"] != f.get("findings"):
        raise KernelGuardError("audit-findings", "the findings stand as the auditor recorded them at Record the findings and are not "
                                                 "changed here: give your verdict on each (reject a finding that is wrong, saying why), "
                                                 "or reject the audit, which ends it as rejected, and file a new one")
    criteria = load_clauses(kernel.config_dir)
    try:
        if at_record:
            check_independence(bundle, authz.principal_name(str(f.get("auditor") or by)), f.get("processes"))
            if f.get("finding_decisions"):
                raise ValueError("the audit carries verdicts before its findings are recorded; a new audit is needed")
            if verdict == "approve":
                if "findings" in fields:
                    fields["findings"] = check_findings(kernel, fields["findings"], by=by, reader=reader_for(bundle, by), criteria=criteria)
                else:
                    check_findings(kernel, f.get("findings") or [], by=None, criteria=criteria)
        elif at_accept and verdict == "approve":
            if (bundle.get("members", {}).get(by) or {}).get("type") != "person":
                raise ValueError("a person accepts or rejects the findings of an audit")
            if by == authz.principal_name(str(f.get("auditor") or "")):
                raise ValueError(f"{by} is the auditor of {rec.get('number')} and does not decide their own findings")
            check_findings(kernel, f.get("findings") or [], by=None, criteria=criteria)
            ids = [x["id"] for x in f.get("findings") or []]
            given = fields.get("finding_decisions") or []
            if not isinstance(given, list):
                raise ValueError("the verdicts are a list, one per finding")
            out, seen = [], set()
            for d in given:
                if not isinstance(d, dict) or d.get("finding") not in ids:
                    raise ValueError(f"{(d or {}).get('finding') if isinstance(d, dict) else d!r} is not a finding of {rec.get('number')}")
                if d["finding"] in seen:
                    raise ValueError(f"{d['finding']} has two verdicts")
                seen.add(d["finding"])
                v = str(d.get("verdict") or "").lower()
                if v not in VERDICTS:
                    raise ValueError(f"the verdict on {d['finding']} is accept or reject")
                reason = " ".join(str(d.get("reason") or "").split())[:600]
                if v == "reject" and not reason:
                    raise ValueError(f"say why {d['finding']} is rejected")
                out.append({"finding": d["finding"], "verdict": v, "reason": reason, "by": by})
            open_ = [i for i in ids if i not in seen]
            if open_:
                raise ValueError(f"accept or reject each finding first, under Findings on the audit's page: {', '.join(open_)} "
                                 f"{'has' if len(open_) == 1 else 'have'} no verdict")
            fields["finding_decisions"] = out
    except ValueError as e:
        raise KernelGuardError("audit", str(e)) from None
    return fields


# ---------------------------------------------------------------- who owns what; the programme
def owned_processes(bundle: dict, member: str) -> set[str]:
    """The processes a member owns, which an auditor audits none of:

    - one of its roles decides a step in it (a checkpoint, a person's Gate on a transition); starting one (as staff file
      a nonconformance) is not owning it;
    - a Routing sentence sends a step's decision to it, to one of its roles or to a group it is in, at any threshold
      ("Approvals in purchase-request over 10,000 USD go to the controller." makes the controller an owner of
      purchase-request).

    Overdue escalations ("Overdue items in X go to the management representative.") are NOT counted: every process has
    one, and counting them would make whoever receives them own everything. Whether an escalation is ownership is the
    maintainer's call (issue #25). A route to a field of the record (the auditor, the requester) names no one in advance
    and is not counted either."""
    roles = authz.roles_of(bundle, member)
    name_ = authz.principal_name(member)
    groups = authz.groups_of(bundle, name_)
    out = set()
    for name, proc in (bundle.get("processes") or {}).items():
        if any(s.get("kind") == "checkpoint" and s.get("role") in roles for s in proc.get("steps") or []):
            out.add(name)
    for name, wf in (bundle.get("workflows") or {}).items():
        for t in wf.get("transitions") or []:
            g = t.get("gate") or {}
            if t.get("type") != "initial" and g.get("kind") == "person" and g.get("role") in roles:
                out.add(name)
    for r in bundle.get("routing") or []:
        cond = r.get("condition") if isinstance(r.get("condition"), dict) else {}
        if r.get("mode", "assign") != "assign" or cond.get("field") == "overdue" or not r.get("process"):
            continue
        kind, _, who = str(r.get("assignee") or "").partition(":")
        if (kind == "role" and who in roles) or (kind in ("person", "cog") and who == name_) or (kind == "group" and who in groups):
            out.add(r["process"])
    # A change order's items are decided by the policy owners in change control (``changecontrol._owners``), not by a
    # runner step or a Routing sentence, so the loops above cannot see it: a policy owner owns the change-order process.
    if "policy-owner" in roles and "change-order" in (bundle.get("processes") or {}):
        out.add("change-order")
    return out


def programme(hub, auditor: str) -> list[dict]:
    """The audit programme for this auditor: every process in the release in force, the one audited longest ago (never
    audited first) at the top; a process the auditor owns is listed with ``owned`` and never comes first."""
    bundle = hub.current_bundle() or {}
    owned = owned_processes(bundle, auditor)
    last: dict[str, list[tuple[str, str]]] = {}
    for a in hub.kernel.query(type_="audit"):
        if a["state"] in ("cancelled", "rejected", "failed"):
            continue
        for p in a["fields"].get("processes") or []:
            last.setdefault(p, []).append((a["created_at"], a["number"]))
    rows = []
    for name, proc in sorted((bundle.get("processes") or {}).items()):
        audits = sorted(last.get(name) or [])
        rows.append({"process": name, "frame": ((proc.get("source") or {}).get("frame")), "owned": name in owned,
                     "last_audited": audits[-1][0][:10] if audits else None, "audits": [n for _, n in audits][-3:]})
    rows.sort(key=lambda r: (r["owned"], r["last_audited"] or "", r["process"]))
    return rows


def frame_clauses(hub, frame: str) -> list[str]:
    meta = hub.releases.current() or {}
    return [str(c) for c in (meta.get("clauses") or {}).get(frame) or []]


# ---------------------------------------------------------------- the exact checks
CHECKS = {
    "overdue": ("iso9001:9.1.1", "Items are decided within the due date their SOP sets (an SLA sentence)."),
    "self-approval": ("iso9001:5.3", "Whoever prepared an item is never the one who approves it (segregation of duties)."),
    "skipped-routing": ("21cfr11:11.10", "Every decision is taken by someone the item was routed to, as the Track records."),
    "training": ("iso9001:7.2", "When a document takes effect, everyone its Roles and responsibilities table names is assigned its training."),
    "chain": ("21cfr11:11.10", "The record of every change (kernel events, each run's Track and its signatures) verifies: nothing was edited or removed."),
    "clauses": ("iso9001:4.4", "Every clause of the audit criteria is cited by a Frame, or excluded with a reason in the quality manual."),
    "independence": ("iso9001:9.2", "Auditors do not audit processes they own."),
}


def _process_frame_clause(bundle: dict, hub, process: str | None, fallback: str) -> str:
    if not process:
        return fallback
    frame = (((bundle.get("processes") or {}).get(process) or {}).get("source") or {}).get("frame")
    cl = frame_clauses(hub, frame) if frame else []
    return cl[0] if cl else fallback


def _routed(track: list[dict], upto: int) -> set[str]:
    """Everyone the item was put before for the decision at Track entry ``upto``: routed, escalated, handed back,
    stepped in, intervening, countersigning, since the decision before it (a run waits at one Gate at a time)."""
    start = next((j + 1 for j in range(upto - 1, -1, -1) if track[j].get("type") == "gate.decided"), 0)
    out: set[str] = set()
    for ev in track[start:upto]:
        for k in ("assignees", "informed"):
            out |= {x for x in ev.get(k) or [] if isinstance(x, str)}
        if ev.get("type") == "gate.handed_back":
            out.add(ev.get("to") or "")
        if ev.get("type") == "gate.stepped_in":
            out.add(ev.get("cog") or "")
        if ev.get("type") in ("gate.intervened", "gate.signed"):
            out.add(ev.get("by") or "")
    return out


# What the checks parsed and verified of each run's Track, kept while the Track's bytes are unchanged (keyed on their
# hash: a cache must never change what the chain check answers). The kernel's chain is verified afresh every time.
_TRACKS: dict[str, tuple[str, dict]] = {}
CACHE_LIMIT = 50000


def track_view(hub, run_id: str) -> dict:
    """{"events", "problems"} of one run's Track: its entries, and what is wrong with its chain or its signatures. The
    cache is keyed on a hash of the file's bytes, so any change to the content (an edit of the same length with its time
    put back included) is read and verified afresh; only the parsing and verifying of an unchanged Track is saved."""
    import hashlib

    from .track import Track, verify_signature

    path = hub.runner._dir(run_id) / "track.jsonl"
    try:
        raw = path.read_bytes()
    except OSError:
        return {"events": [], "problems": []}
    head = hashlib.sha256(raw).hexdigest()
    hit = _TRACKS.get(str(path))
    if hit and hit[0] == head:
        return hit[1]
    track = Track(path.parent)
    events = []  # parse the very bytes that were hashed (as Track.read does), never a second read of the file
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a torn last line from a crash: that entry never happened
    problems = list(track.verify(events))
    secret = hub.runner.secret()
    for ev in events:
        if ev["type"] in ("gate.decided", "gate.signed", "gate.vote") and verify_signature(secret, ev) == "forged":
            problems.append(f"the signature by {ev.get('by')} at {ev.get('step')} does not verify")
    view = {"events": events, "problems": problems}
    if len(_TRACKS) > CACHE_LIMIT:
        _TRACKS.clear()
    _TRACKS[str(path)] = (head, view)
    return view


def audit_checks(hub, process: str | None = None, today: str | None = None, reader=None) -> dict:
    """Every exact check, for one process (its runs and records) or the whole hub; the clause and chain checks are
    always hub-wide. {"process", "today", "release", "checks": {name: {"clause", "criterion", "findings": [{"found",
    "evidence"}]}}}. Read-only: it changes nothing. ``reader`` (fn(folder) -> bool): only what it may read is looked
    at, so the checks show an auditor no more than the auditor's role reads."""
    today = today or date.today().isoformat()
    may = reader or (lambda folder: True)
    bundle = hub.current_bundle() or {}
    meta = hub.releases.current() or {}
    wf = (bundle.get("workflows") or {}).get(process or "") or {}
    rtype = wf.get("record_type") or ((bundle.get("processes") or {}).get(process or "") or {}).get("record_type")
    out: dict[str, list[dict]] = {k: [] for k in CHECKS}
    runs = []
    for s in hub.runner.list():
        if process and s.get("process") != process:
            continue
        rec = hub.kernel.get(s["record"]) if s.get("record") else None
        if rec is not None and not may(rec["folder"]):
            continue
        runs.append((s, rec))

    # Overdue: an item waiting past the due date of its step.
    seen = set()
    for s, rec in runs:
        p = s.get("pending") or {}
        if s.get("status") == "waiting" and p.get("due") and p["due"] < today:
            events = track_view(hub, s["id"])["events"]
            seq = next((ev.get("seq") for ev in reversed(events) if ev.get("type") == "gate.pending"), None) or (events[-1]["seq"] if events else 1)
            seen.add((rec or {}).get("locator"))
            out["overdue"].append({"found": f"{(rec or {}).get('number') or s['id']} has waited at {p.get('title') or p.get('step')} since its due date "
                                            f"{p['due']} (for {', '.join(p.get('assignees') or []) or 'nobody'})",
                                   "evidence": ([rec["number"]] if rec else []) + [f"track:{s['id']}#{seq}"]})
    for rec in hub.kernel.query(overdue_at=today):
        if rec["locator"] in seen or (rtype and rec["type"] != rtype) or (process and not rtype) or not may(rec["folder"]):
            continue
        out["overdue"].append({"found": f"{rec['number']} is {rec['state']} past its due date {rec['due']}", "evidence": [rec["number"]]})

    # Decisions: by whoever prepared the item; by someone it was not routed to.
    members = bundle.get("members") or {}
    for s, rec in runs:
        track = track_view(hub, s["id"])["events"]
        f = (rec or {}).get("fields") or {}
        preparers = {authz.principal_name(s.get("owner") or "")}
        if rec and not s.get("parent_run") and rec.get("created_by") not in ("hub", "system", "seed"):
            preparers.add(rec.get("created_by"))
        co_blocked = None
        if hub.runner.is_change_order(s) and rec is not None:
            # A change order: a contributor signs alongside an independent approver (the change-control SOP); only who
            # may not sign it (changecontrol.may_not_sign) signing it is a finding.
            try:
                from . import changecontrol

                co_blocked = set(changecontrol.may_not_sign(hub, rec["locator"]))
            except Exception:
                co_blocked = None
        for i, ev in enumerate(track):
            if ev.get("type") != "gate.decided" or not ev.get("by") or str(ev["by"]).startswith("committee:"):
                continue
            who = ev["by"]
            ref = f"track:{s['id']}#{ev.get('seq')}"
            named = str(ev.get("route") or "").startswith(("field:", "person:"))
            if co_blocked is not None:
                selfish = ev.get("verdict") == "approve" and who in co_blocked
            else:
                selfish = ev.get("verdict") == "approve" and not named and (who in preparers or (who == f.get("author") and who in members))
            if selfish:
                out["self-approval"].append({"found": f"{who} approved {ev.get('step')} on {(rec or {}).get('number') or s['id']}, which they prepared",
                                             "evidence": [x for x in [(rec or {}).get("number"), ref] if x]})
            routed = _routed(track, i)
            if who not in routed:
                out["skipped-routing"].append({"found": f"{who} decided {ev.get('step')} on {(rec or {}).get('number') or s['id']}; it was routed to "
                                                        f"{', '.join(sorted(x for x in routed if x)) or 'nobody'}",
                                               "evidence": [x for x in [(rec or {}).get("number"), ref] if x]})

    # Training: everyone a document in force names, assigned its training.
    if not process:
        from . import training
        from .groups import group_members

        for frame in sorted({r.get("frame") for r in bundle.get("responsibilities") or [] if r.get("frame")}):
            doc = training.document_in_force(hub, frame)
            if doc is None or not may(doc["folder"]):
                continue
            roles = set(training.roles_to_train(bundle, doc))
            people = {p for p, m in members.items() if m.get("type") == "person" and roles & set(m.get("roles") or [])}
            for g in training.groups_to_train(bundle, doc):
                people |= {p for p in group_members(bundle, g) if (members.get(p) or {}).get("type") == "person"}
            missing = [p for p in sorted(people) if training.reading_status(hub, p, frame, today)["status"] == "not assigned"]
            if missing:
                out["training"].append({"found": f"{doc['number']} ({frame}, revision {doc['fields'].get('revision')}) took effect; "
                                                 f"no training was assigned to {', '.join(missing)}", "evidence": [doc["number"]]})

    # The chains.
    if not process:
        for p in hub.kernel.verify_chain():  # never cached: an in-place change shows at once
            if "not chained" not in p:
                m = re.match(r"event (\d+)", p)
                out["chain"].append({"found": f"kernel: {p}", "evidence": [f"event:{m[1]}"] if m else []})
    for s, rec in runs:
        for p in track_view(hub, s["id"])["problems"]:
            if "not chained" in p:
                continue
            m = re.match(r"entry (\d+)", p)
            out["chain"].append({"found": f"run {s['id']} ({s['process']}): {p}",
                                 "evidence": [f"track:{s['id']}#{m[1]}"] if m else ([rec["number"]] if rec else [])})

    # Clauses no Frame cites.
    criteria = load_clauses(hub.kernel.config_dir)
    from .profile import claimed_clauses

    claimed, _ = claimed_clauses(hub)
    for std, nums in uncovered_clauses(criteria, claimed).items():
        if nums:
            title = (criteria["standards"][std] or {}).get("title", std)
            out["clauses"].append({"found": f"no Frame cites {len(nums)} clause(s) of {title}: "
                                            + ", ".join(f"{n} {criteria['standards'][std]['clauses'][n]}" for n in nums),
                                   "standard": std, "clauses": nums,
                                   "evidence": [f"release:{meta['digest'][:16]}"] if meta.get("digest") else []})

    # Independence: an audit of a process its auditor owns (the guards refuse one; this finds any that got through).
    for a in hub.kernel.query(type_="audit"):
        who = a["fields"].get("auditor")
        if not who or who not in members or not may(a["folder"]):
            continue
        procs = a["fields"].get("processes") or sorted(bundle.get("processes") or {})
        mine = sorted(set(procs) & owned_processes(bundle, who))
        if mine and (not process or process in mine or process == PROCESS):
            out["independence"].append({"found": f"{a['number']} audits {', '.join(mine)}, which its auditor {who} owns", "evidence": [a["number"]]})

    fallback = {k: v[0] for k, v in CHECKS.items()}
    checks = {}
    for name, (clause, criterion) in CHECKS.items():
        cl = _process_frame_clause(bundle, hub, process, clause) if name == "overdue" else fallback[name]
        checks[name] = {"clause": cl, "criterion": criterion, "count": len(out[name]), "findings": out[name]}
    return {"process": process, "today": today, "release": meta.get("digest", "")[:16], "checks": checks}


# ---------------------------------------------------------------- sampling the records of a process
def sample(hub, process: str, n: int = 5, reader=None) -> dict:
    """A deterministic sample of a process's items (spread over its history, newest last): each with its state, run
    and the Track entries of its decisions, so a finding can cite them."""
    bundle = hub.current_bundle() or {}
    if process not in (bundle.get("processes") or {}):
        raise ValueError(f"there is no process {process!r} in the policy release")
    n = max(1, min(int(n or 5), 10))
    runs = [s for s in hub.runner.list() if s.get("process") == process and s.get("record")]
    if len(runs) > n:
        step = (len(runs) - 1) / (n - 1) if n > 1 else 0
        runs = [runs[round(i * step)] for i in range(n)] if n > 1 else [runs[-1]]
    items = []
    for s in runs:
        rec = hub.kernel.get(s["record"])
        if rec is None or (reader is not None and not reader(rec["folder"])):
            continue
        decisions = [{"track": f"track:{s['id']}#{ev.get('seq')}", "step": ev.get("step"), "by": ev.get("by"), "verdict": ev.get("verdict"),
                      "meaning": str(ev.get("meaning") or "")[:160], "routed_by": ((ev.get("route") or ""))}
                     for ev in hub.runner.track(s["id"]) if ev.get("type") == "gate.decided"]
        items.append({"number": rec["number"], "title": str(rec["fields"].get("title") or "")[:120], "state": rec["state"], "run": s["id"],
                      "status": s.get("status"), "owner": s.get("owner"), "created": rec["created_at"][:10], "due": rec.get("due"),
                      "decisions": decisions})
    frame = (((bundle.get("processes") or {}).get(process) or {}).get("source") or {}).get("frame")
    return {"process": process, "sop": frame, "count": len(items), "items": items,
            "note": "read the SOP section of the process (read_sop_section) and the records (get_record); cite numbers and track entries as given"}


# ---------------------------------------------------------------- planning, recording, accepting
def plan_audit(hub, auditor: str, process: str, *, planned: str | None = None, title: str = "") -> dict:
    """Start the internal-audit process for one process, as the auditor (its owner and auditor). Refused for a process
    the auditor owns."""
    bundle = hub.current_bundle() or {}
    if process not in (bundle.get("processes") or {}):
        raise ValueError(f"there is no process {process!r} in the policy release")
    try:
        check_independence(bundle, auditor, [process])
    except KernelGuardError as e:
        raise ValueError(f"{e.detail}; take another from the programme") from None
    frame = (((bundle.get("processes") or {}).get(process) or {}).get("source") or {}).get("frame")
    run_id = hub.runner.submit(PROCESS, {"record": {"title": title or f"Internal audit of {process}", "processes": [process],
                                                    "scope_clauses": frame_clauses(hub, frame) if frame else [],
                                                    "planned": planned or date.today().isoformat(), "auditor": auditor}}, owner=auditor)
    state = hub.runner.load(run_id)
    rec = hub.kernel.get(state["record"]) if state.get("record") else None
    return {"run": run_id, "audit": (rec or {}).get("number"), "status": state["status"],
            "waiting_for": (state.get("pending") or {}).get("assignees"), "state": (rec or {}).get("state")}


def audit_record(hub, number: str) -> dict:
    rec = hub.kernel.get(str(number) if str(number).startswith("rec:") else str(number).strip().upper())
    if rec is None or rec["type"] != "audit":
        raise ValueError(f"there is no audit {number!r}")
    return rec


def waiting_audit(hub, number: str, who: str, step: str) -> tuple[dict, dict]:
    """The audit and its run, waiting at ``step`` for ``who``; ValueError otherwise."""
    rec = audit_record(hub, number)
    run = hub.runner.run_for_record(rec["locator"])
    pending = (run or {}).get("pending") or {}
    at = pending.get("checkpoint") or pending.get("transition") or pending.get("step")
    if not run or run["status"] != "waiting" or at != step or who not in (pending.get("assignees") or []):
        where = (pending.get("title") or run.get("status")) if run else "no run"
        raise ValueError(f"{rec['number']} is not waiting for {who} at {step.replace('-', ' ')} (it is at {where})")
    return rec, run


def number_findings(findings: list[dict], by: str) -> list[dict]:
    return [{"id": f"F-{i}", **f, "by": by} for i, f in enumerate(findings, 1)]


def submit_findings(hub, number: str, cog: str, findings: list[dict], summary: str) -> dict:
    """The auditor cog completes "Record the findings" with its findings (validated again here and at the Gate)."""
    rec, run = waiting_audit(hub, number, cog, RECORD_STEP)
    if not findings:
        raise ValueError("record at least one finding (record_finding) before completing the step; a process that conforms is an observation")
    criteria = load_clauses(hub.kernel.config_dir)
    reader = reader_for(hub.current_bundle() or {}, cog)
    clean = [validate_finding({k: v for k, v in f.items() if k not in ("id", "by")}, kernel=hub.kernel, criteria=criteria, reader=reader)
             for f in findings]
    return hub.runner.cog_complete(run["id"], cog, summary or f"{len(clean)} finding(s) recorded",
                                   fields={"findings": number_findings(clean, cog)})


def accept_findings(hub, number: str, by: str, verdicts: dict[str, dict], *, summary: str, meaning: str, payload_digest: str) -> dict:
    """The quality manager's decision at "Accept the findings": a verdict (accept or reject, a reason for a rejection)
    on every finding, the summary, one signature. ``verdicts`` = {finding id: {"verdict", "reason"}}."""
    rec, run = waiting_audit(hub, number, by, ACCEPT_STEP)
    bundle = hub.current_bundle() or {}
    if (bundle.get("members", {}).get(by) or {}).get("type") != "person":
        raise ValueError("a person accepts or rejects the findings of an audit")
    ids = [f["id"] for f in rec["fields"].get("findings") or [] if isinstance(f, dict) and f.get("id")]
    decisions = []
    for fid in ids:
        v = verdicts.get(fid) or {}
        verdict = str(v.get("verdict") or "").lower()
        if verdict not in VERDICTS:
            raise ValueError(f"accept or reject {fid}")
        reason = " ".join(str(v.get("reason") or "").split())[:600]
        if verdict == "reject" and not reason:
            raise ValueError(f"say why {fid} is rejected")
        decisions.append({"finding": fid, "verdict": verdict, "reason": reason, "by": by})
    extra = sorted(set(verdicts) - set(ids))
    if extra:
        raise ValueError(f"{', '.join(extra)} {'is' if len(extra) == 1 else 'are'} not a finding of {rec['number']}")
    return hub.runner.decide(run["id"], by, "approve", meaning=meaning, payload_digest=payload_digest,
                             fields={"finding_decisions": decisions, "summary": summary})


def accepted_actions(rec: dict) -> list[dict]:
    """The accepted findings that need a nonconformance or a CAPA."""
    f = rec.get("fields") or {}
    ok = {d["finding"] for d in f.get("finding_decisions") or [] if isinstance(d, dict) and d.get("verdict") == "accept"}
    return [x for x in f.get("findings") or [] if isinstance(x, dict) and x.get("id") in ok and x.get("action") in ("ncr", "capa")]


# ---------------------------------------------------------------- the report the cog drafts
def report_markdown(hub, rec: dict, findings: list[dict], checks: dict | None = None) -> str:
    criteria = load_clauses(hub.kernel.config_dir)
    f = rec["fields"]
    lines = [f"# Internal audit report: {rec['number']}", "", f"Title: {f.get('title') or ''}  ",
             f"Processes: {', '.join(f.get('processes') or [])}  ", f"Auditor: {f.get('auditor') or ''}  ",
             f"Policy release: `{(hub.releases.current() or {}).get('digest', '')[:16]}`", "",
             "This is an internal audit against the organization's own SOPs and the clauses they cite; it is not a certification. "
             "The criteria are clause numbers and titles only. The quality manager accepts or rejects each finding.", "",
             "## Findings", ""]
    if not findings:
        lines.append("No findings were recorded.")
    for i, x in enumerate(findings, 1):
        fid = x.get("id") or f"F-{i}"
        lines += [f"### {fid}: {x.get('grade', '').capitalize()}, {clause_title(criteria, x.get('clause', '')) or x.get('clause', '')}", "",
                  f"- Expected: {x.get('criterion', '')}", f"- Found: {x.get('found', '')}",
                  f"- Evidence: {', '.join(x.get('evidence') or [])}", f"- Proposed action: {x.get('action', 'none')}", ""]
    if checks:
        lines += ["## Exact checks", "", "| Check | Clause | Count |", "|---|---|---|"]
        for name, c in checks.get("checks", {}).items():
            lines.append(f"| {name} | {c.get('clause')} | {c.get('count')} |")
    return "\n".join(lines) + "\n"


def draft_report(hub, number: str, by: str, findings: list[dict], checks: dict | None = None) -> dict:
    """The audit report as a controlled draft document attached to the audit, authored by the auditor; a person sends
    it through document control. Its content is the findings as recorded: nothing in it is executed."""
    rec = audit_record(hub, number)
    if rec["fields"].get("auditor") != by:
        raise ValueError(f"{by} is not the auditor of {rec['number']}")
    allowed = authz.check(hub.current_bundle() or {}, by, "create", "document", rec["folder"])
    if not allowed["allowed"]:
        raise ValueError(allowed["why"])
    procs = rec["fields"].get("processes") or []
    if checks is None:
        checks = audit_checks(hub, procs[0] if len(procs) == 1 else None, reader=reader_for(hub.current_bundle() or {}, by))
    md = report_markdown(hub, rec, findings, checks)
    title = f"Audit report {rec['number']}"
    existing = next((d for d in hub.dms.documents_of(rec["locator"]) if d["fields"].get("doc_type") == "audit-report"
                     and d["state"] in ("draft", "open", "returned")), None)
    if existing is not None:
        hub.dms.add_version(existing["locator"], md.encode("utf-8"), by=by, filename=f"{rec['number']}-report.md", mime="text/markdown",
                            note=f"redrafted by {by}")
        return hub.kernel.get(existing["locator"])
    return hub.dms.create(title=title, doc_class="controlled", folder=rec["folder"], by=by, content=md.encode("utf-8"),
                          filename=f"{rec['number']}-report.md", mime="text/markdown", doc_type="audit-report",
                          provenance={"audit": rec["locator"], "drafted_by": by}, attach_to=rec["locator"])


# ---------------------------------------------------------------- the step that raises the actions
def register(registry) -> None:
    from . import runner as _runner
    from .cogs import Cog, ReviewerCog

    class AuditActionRaiser(Cog):
        id = "audit-action-raiser"
        reaches = ["kernel:records"]
        description = ("Opens a nonconformance or a CAPA for each audit finding the quality manager accepted that needs one, "
                       "under the quality manager's grant, and links it to the audit.")

        def task_run(self, input: dict, ctx) -> dict:
            refused = self._require_grant(ctx, "run")
            if refused:
                return refused
            prior = self._prior(input, ctx)
            rec = ctx.rec()
            if rec is None or ctx.runner is None:
                return self.ok("run", prior)
            done = {r.get("finding") for r in rec["fields"].get("actions_raised") or [] if isinstance(r, dict)}
            owner = ctx.grant.get("decided_by", ctx.owner)
            raised = list(rec["fields"].get("actions_raised") or [])
            for x in accepted_actions(rec):
                if x["id"] in done:
                    continue
                process = "nonconformance" if x["action"] == "ncr" else "capa"
                if process not in (ctx.bundle.get("ops") or {}):
                    continue
                head = f"{rec['number']} {x['id']}: {x.get('title') or x['found'][:80]}"
                fields = ({"title": head, "description": f"{x['found']} Expected: {x['criterion']} Evidence: {', '.join(x['evidence'])}.",
                           "source": "audit"} if process == "nonconformance" else {"title": head, "owner": owner})
                run_id = ctx.runner.submit(process, {"record": fields}, owner=owner, parent_run=ctx.run_id)
                made = ctx.runner.load(run_id)["record"]
                ctx.kernel.link(made, "concerns", rec["locator"], by=self.id, run_id=ctx.run_id, release=ctx.release)
                raised.append({"finding": x["id"], "record": made, "process": process})
            if len(raised) != len(rec["fields"].get("actions_raised") or []):
                ctx.kernel.set_fields(rec["locator"], {"actions_raised": raised}, by=self.id, run_id=ctx.run_id, release=ctx.release)
            return self.ok("run", {**prior, "actions_raised": raised})

    class Auditor(ReviewerCog):
        id = "auditor"
        description = ("Internal auditor team member (a senior cog): runs the internal audit each month, records findings that cite "
                       "their evidence and drafts the report; it signs nothing, and the quality manager accepts or rejects each finding.")

    registry.register(AuditActionRaiser())
    registry.register(Auditor())
    _runner.DECISION_FIELD_GUARDS["audit"] = decision_guard
    _runner.START_GUARDS["audit"] = start_guard
