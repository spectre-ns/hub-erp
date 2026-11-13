"""Command line for the prototype hub.

Conventions every verb follows:

* ``main`` returns the exit code: 0 when the verb did what was asked, 1 when the hub refused or the outcome is a
  failure (error findings, ``DENIED``, a failed run), 2 for a mistake in the command (unknown id, bad JSON, a
  missing file, the wrong ``--root``). A refusal or a mistake prints one ``error:`` sentence on stderr and raises
  ``CliExit`` (a ``SystemExit`` whose ``code`` is the exit code and whose text is the sentence); the console script
  and ``python -m`` turn either into the process exit code. Nothing prints a traceback.
* ``--as``, ``--second``, ``--to`` and the sender of a message must be members of the release in force (before
  the first release: of the Frames being bootstrapped), and an unknown name is refused with the member list.
* Every verb but ``init`` and ``restore`` needs ``<root>/state/kernel.db``; a wrong root is refused, never
  silently filled with an empty hub.
* Anything printed from a record field goes through ``clean`` (no control characters, no ANSI escapes), listings
  truncate with an ellipsis, and digests show 12 characters everywhere.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .app import Hub
from .compile import CompileError, change_state
from .frames import FrameError
from .inbox import parse_eml
from .kernel import KernelGuardError
from .release import strip_sources, utcnow
from .closure import CO_ROUTE
from .rules import rule_key, sha256_of, CONDITION_VOCABULARY
from .runner import RunError

DIGEST = 12
POLICY_OWNER_ROLE = "policy-owner"
BACKUP_PARTS = ("runs", "releases", "proposals", "blobs", "inbox", "agents", "settings", "forms", "record-types", "config")
SQLITE_PARTS = ("queue.db",)  # copied with the SQLite backup API (a consistent snapshot while the hub runs)
NEEDS_FRAMES = {"seed", "compile", "frames", "bootstrap-release", "conformance", "status"}
NO_HUB_NEEDED = {"init", "restore", "dev"}  # dev makes its own hubs (hubproto.cli_dev)


# ------------------------------------------------------------------ errors and output hygiene
class CliExit(SystemExit):
    """A refusal or a mistake: ``str(e)`` is the sentence printed, ``e.code`` the exit code."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.message = message
        self.code = code


def fail(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise CliExit(message, code)


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]|\x9b[0-?]*[ -/]*[@-~]")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def clean(value, width: int | None = None) -> str:
    """Record text fit for a terminal: ANSI escapes and C0/C1 controls removed, line breaks and tabs made spaces,
    and, with ``width``, cut with an ellipsis."""
    if isinstance(value, (dict, list)):
        s = json.dumps(value, sort_keys=True, default=str)
    else:
        s = "" if value is None else str(value)
    s = _ANSI.sub("", s)
    s = s.replace("\r\n", "\n").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    s = _CTRL.sub("", s)
    if width and len(s) > width:
        s = s[: width - 1] + "…"
    return s


def short(digest: str | None) -> str:
    return (digest or "")[:DIGEST] or "-"


def iso_date(text: str) -> str:
    """``YYYY-MM-DD`` only (what the escalation tick compares due dates with)."""
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be a date written YYYY-MM-DD, not {text!r}") from None
    return text


def iso_timestamp(text: str) -> str:
    """``YYYY-MM-DD`` or a full ISO timestamp; a bare date means midnight UTC."""
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat()
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be YYYY-MM-DD or a full ISO timestamp such as 2026-11-30T09:00:00+00:00, not {text!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ------------------------------------------------------------------ the hub and its identifiers


def _hub(args) -> Hub:
    """The hub at ``--root``. A malformed Frame only stops the verbs that read Frames; the others carry on with a
    warning, so an operator can still list runs and records while a colleague's editor has a Frame half saved."""
    root = Path(args.root)
    hub = Hub(root)  # Frames load on first use
    if getattr(args, "cmd", None) in NEEDS_FRAMES:
        try:
            hub.frames
        except FrameError as e:
            fail(f"{e}; fix the Frame and run again", 2)
    return hub


def frame_file(hub: Hub, name: str) -> str:
    """``frames/finance/finance-policy.frame.md`` for a Frame name, so a citation opens in an editor."""
    frame = hub.frames.frames.get(name)
    if frame is None:
        return name
    try:
        return "frames/" + str(frame.path.relative_to(hub.frames.root))
    except ValueError:
        return str(frame.path)


def cite(hub: Hub, frame: str, line) -> str:
    if not frame:
        return "release-wide"
    return f"{frame_file(hub, frame)}:{line}" if line else frame_file(hub, frame)


def _members_from_frames(hub: Hub) -> dict[str, dict]:
    from .app import default_scopes
    from .extract import get_extractor

    members: dict[str, dict] = {}
    try:
        frames = hub.frames.resolve(default_scopes(hub.frames))
    except FrameError:
        return members
    extractor = get_extractor()
    for frame in frames:
        for r in extractor.extract(frame).rules:
            if r["kind"] == "member":
                cur = members.setdefault(r["principal"], {"type": r["principal_type"], "roles": []})
                cur["roles"] = sorted(set(cur["roles"]) | set(r["roles"]))
    return members


def members_of(hub: Hub, proposal: dict | None = None) -> dict[str, dict]:
    """Who exists: the members of the release in force, else of the proposal (or Frames) being bootstrapped."""
    bundle = hub.current_bundle()
    if bundle:
        return bundle["members"]
    if proposal:
        out: dict[str, dict] = {}
        for r in proposal["rules"]:
            if r["kind"] == "member":
                cur = out.setdefault(r["principal"], {"type": r["principal_type"], "roles": []})
                cur["roles"] = sorted(set(cur["roles"]) | set(r["roles"]))
        return out
    return _members_from_frames(hub)


def require_member(hub: Hub, name: str, flag: str = "--as", people_only: bool = True, members: dict | None = None,
                   proposal: dict | None = None) -> dict:
    """Refuse a name that is not a member; say who is. Cogs count only where ``people_only`` is off (recipients)."""
    members = members if members is not None else members_of(hub, proposal)
    if not members:
        return {}  # no release and no Frames to read: the verb's own "no policy release is in effect" follows
    if name in members and not (people_only and members[name]["type"] == "cog"):
        return members[name]
    people = sorted(p for p, m in members.items() if m["type"] == "person")
    cogs = sorted(p for p, m in members.items() if m["type"] == "cog")
    if name in members:
        fail(f"{flag} {name}: {name} is a cog; only a person may do this (people: {', '.join(people)})", 2)
    where = "release in force" if hub.current_bundle() else "Frames being released"
    extra = f"; cogs: {', '.join(cogs)}" if cogs and not people_only else ""
    fail(f"{flag} {name}: {name} is not a member of the organization under the {where}; members are: {', '.join(people)}{extra}", 2)
    return {}


def policy_owners(hub: Hub, proposal: dict | None = None) -> list[str]:
    return sorted(p for p, m in members_of(hub, proposal).items() if POLICY_OWNER_ROLE in m["roles"] and m["type"] == "person")


def resolve_run(hub: Hub, ref: str) -> str:
    runs = sorted(p.name for p in hub.runner.root.iterdir() if p.is_dir() and (p / "state.json").exists())
    matches = [r for r in runs if r.startswith(ref)]
    if not matches:
        fail(f"no run starts with {ref!r}; `hubproto runs` lists them", 2)
    if len(matches) > 1:
        fail(f"{ref!r} matches {len(matches)} runs: {', '.join(matches)}; give more characters", 2)
    return matches[0]


def proposal_numbers(hub: Hub) -> dict[str, int]:
    """Proposal id -> number. The core numbers proposals at compile time; proposals from before that are numbered
    once, in creation order, and the assignment is kept in state/proposal-numbers.json so a number never moves."""
    path = hub.root / "state" / "proposal-numbers.json"
    try:
        numbers = {k: int(v) for k, v in json.loads(path.read_text()).items()}
    except (OSError, ValueError):
        numbers = {}
    proposals = sorted(hub.proposals.list(), key=lambda p: (p.get("created_at") or "", p["id"]))
    for p in proposals:
        if p.get("number"):
            numbers[p["id"]] = int(p["number"])
    fresh = [p for p in proposals if p["id"] not in numbers]
    if fresh:
        n = max(numbers.values(), default=0)
        for p in fresh:
            n += 1
            numbers[p["id"]] = n
        try:
            path.write_text(json.dumps(numbers, indent=1, sort_keys=True))
        except OSError:
            pass
    return numbers


def proposals_in_order(hub: Hub) -> list[dict]:
    numbers = proposal_numbers(hub)
    return sorted(hub.proposals.list(), key=lambda p: numbers.get(p["id"], 0))


def resolve_proposal(hub: Hub, ref: str) -> dict:
    """``#3`` or ``3`` is the third proposal as ``proposals`` numbers them; anything else is an id prefix."""
    ordered = proposals_in_order(hub)
    if not ordered:
        fail("no proposals yet; run `hubproto compile` first", 2)
    text = ref.lstrip("#")
    if text.isdigit() and (ref.startswith("#") or len(text) < 4):
        n = int(text)
        numbers = proposal_numbers(hub)
        match = next((p for p in ordered if numbers.get(p["id"]) == n), None)
        if match is None:
            fail(f"there is no proposal #{n}; `hubproto proposals` lists {len(ordered)}", 2)
        return match
    matches = [p for p in ordered if p["id"].startswith(text)]
    if not matches:
        fail(f"no proposal starts with {ref!r}; `hubproto proposals` lists them", 2)
    if len(matches) > 1:
        fail(f"{ref!r} matches {len(matches)} proposals: {', '.join(p['id'] for p in matches)}; use the number from `hubproto proposals`", 2)
    return matches[0]


def proposal_number(hub: Hub, pid: str) -> int:
    return proposal_numbers(hub).get(pid, 0)


def releases_in_order(hub: Hub) -> list[dict]:
    """Releases as versions: v1 is the first to take effect."""
    # Timestamps are second-resolution; two releases in one second keep the order they were released in.
    return [m for _, m in sorted(enumerate(hub.releases.list()), key=lambda im: (im[1]["effective_from"], im[1]["created_at"], im[0]))]



def version_of(hub: Hub, digest: str | None) -> str:
    if not digest:
        return "none"
    for i, m in enumerate(releases_in_order(hub), start=1):
        if m["digest"] == digest:
            return f"v{i} ({short(digest)})"
    return short(digest)


def resolve_release(hub: Hub, ref: str) -> dict:
    ordered = releases_in_order(hub)
    if not ordered:
        fail("no release yet; compile, review and release first", 2)
    m = re.fullmatch(r"v?(\d+)", ref)
    if m and (ref.startswith("v") or len(ref) < 4):
        n = int(m.group(1))
        if not 1 <= n <= len(ordered):
            fail(f"there is no release v{n}; `hubproto releases` lists {len(ordered)}", 2)
        return ordered[n - 1]
    matches = [x for x in ordered if x["digest"].startswith(ref)]
    if not matches:
        fail(f"no release digest starts with {ref!r}; `hubproto releases` lists them", 2)
    if len(matches) > 1:
        fail(f"{ref!r} matches {len(matches)} releases; give more characters or the version (v2)", 2)
    return matches[0]


def resolve_record(hub: Hub, ref: str) -> dict:
    """A record by number (any case) or locator."""
    rec = hub.kernel.get(ref) or hub.kernel.get(ref.upper()) or hub.kernel.get(ref.lower())
    if rec is None:
        fail(f"no record {ref!r}; `hubproto records` lists them by number", 2)
    return rec


def load_json_record(text: str | None, path: str | None) -> dict:
    def constant(name):
        fail(f"the record JSON contains {name}, which is not a number the hub stores", 2)

    if path:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as e:
            fail(f"cannot read {path}: {e.strerror or e}", 2)
    text = text or "{}"
    try:
        record = json.loads(text, parse_constant=constant)
    except json.JSONDecodeError as e:
        fail(f"the record JSON does not parse at line {e.lineno} column {e.colno}: {e.msg}", 2)
    if not isinstance(record, dict):
        fail(f"the record JSON must be an object of field: value pairs, not {type(record).__name__}", 2)
    return record


# ------------------------------------------------------------------ describing changes and failures
def _condition_words(code: str) -> str:
    for words, c in CONDITION_VOCABULARY.items():
        if c == code:
            return words
    return code


def describe_change(change: dict) -> str:
    """The change in words, never ``up to None``; the drift guard's wording for the kinds it states well."""
    rule, op = change["rule"], change["op"]
    kind = rule["kind"]
    if kind == "approval_criteria":
        amount = rule.get("max_amount")
        limit = f"up to {amount:,.0f} {rule.get('currency') or ''}".strip() if amount is not None else "with no amount limit"
        conds = " and ".join(_condition_words(c) for c in rule.get("conditions") or []) or "always"
        return f"{op} {rule['approver']} may approve {rule['process']} {limit} when {conds}"
    if kind == "member":
        return f"{op} member {rule['principal']} ({rule['principal_type']}) with roles {', '.join(rule['roles'])}"
    if kind == "sod":
        if rule.get("only"):
            return f"{op} segregation of duties on {rule['object']}: whoever {rule['roles'][0].replace('-', ' ')} is not its only approver"
        return f"{op} segregation of duties on {rule['object']}: {' and '.join(rule['roles'])} must differ"
    if kind in ("role", "folder"):
        return f"{op} {kind} {rule.get('name') or rule.get('path')}"
    if kind == "sla":
        return f"{op} SLA {rule['process']}/{rule['step']} due within {rule['days']} days"
    return change["reason"]


def collapse_changes(changes: list[dict]) -> list[dict]:
    """Group an add and a remove of the same rule whose only difference is where it sits in the file (a process whose
    section was re-rendered): one entry with both ids. Everything else is returned as it is."""
    bodies: dict[str, list[dict]] = {}
    for c in changes:
        key = json.dumps(strip_sources({k: v for k, v in c["rule"].items() if k != "source"}), sort_keys=True)
        bodies.setdefault(key, []).append(c)
    out: list[dict] = []
    seen: set[str] = set()
    by_id = {c["id"]: c for c in changes}
    for c in changes:
        if c["id"] in seen:
            continue
        if c["op"] == "add" and c.get("changes_from") is not None:
            old_id = "remove:" + rule_key(c["changes_from"])
            if old_id in by_id:
                seen.update({c["id"], old_id})
                out.append({"changed": True, "ids": [c["id"], old_id], "changes": [c, by_id[old_id]], "kind": c["rule"]["kind"],
                            "name": c["rule"].get("name") or c["rule"]["kind"], "widening": c["widening"], "reason": c["reason"]})
                continue
        key = json.dumps(strip_sources({k: v for k, v in c["rule"].items() if k != "source"}), sort_keys=True)
        group = bodies[key]
        adds = [x for x in group if x["op"] == "add"]
        removes = [x for x in group if x["op"] == "remove"]
        if adds and removes:
            pair = [adds[0], removes[0]]
            seen.update(x["id"] for x in pair)
            name = c["rule"].get("name") or c["rule"]["kind"]
            out.append({"rerendered": True, "ids": [x["id"] for x in pair], "changes": pair, "kind": c["rule"]["kind"],
                        "name": name, "widening": any(x["widening"] for x in pair)})
        else:
            seen.add(c["id"])
            out.append(c)
    return out


def _decisions_text(change: dict) -> str:
    return ", ".join(f"{d['by']}:{d['verdict']}" for d in change.get("decisions", [])) or "-"


def print_moved(proposal: dict) -> None:
    """Rules that only moved in their file (compile.collapse_moves): shown, never decided."""
    for m in proposal.get("moved") or []:
        print(f"  {m['kind']} {m['name']}: re-rendered, no semantic change (moved from line {m.get('from_line')} to "
              f"{m.get('to_line')}; nothing to decide)")


def print_changes(hub: Hub, changes: list[dict], with_decisions: bool = False) -> None:
    for entry in collapse_changes(changes):
        if entry.get("rerendered"):
            states = {change_state(x) for x in entry["changes"]}
            state = "approved" if states == {"approved"} else ("rejected" if "rejected" in states else "pending")
            print(f"  {entry['kind']} {entry['name']}: re-rendered, no semantic change  [{state}; ids {', '.join(entry['ids'])}]")
            if with_decisions:
                for x in entry["changes"]:
                    for d in x.get("decisions", []):
                        print(f"      {d['by']} {d['verdict']} {d['at']}: {clean(d.get('meaning'), 80)}")
            continue
        if entry.get("changed"):
            states = {change_state(x) for x in entry["changes"]}
            state = "approved" if states == {"approved"} else ("rejected" if "rejected" in states else "pending")
            flag = "WIDENING " if entry["widening"] else ""
            new, old = entry["changes"]
            src = new["rule"].get("source") or {}
            print(f"  {flag}{entry['reason']}  — \"{clean(src.get('text'), 100)}\" ({src.get('path') or src.get('frame')}:{src.get('line')})  [{state}; ids {', '.join(entry['ids'])}]")
            if with_decisions:
                for x in entry["changes"]:
                    for d in x.get("decisions", []):
                        print(f"      {d['by']} {d['verdict']} {d['at']}: {clean(d.get('meaning'), 80)}")
            continue
        c = entry
        flag = "WIDENING " if c["widening"] else ""
        src = c["rule"].get("source") or {}
        where = f'  — "{clean(src.get("text"), 100)}" ({cite(hub, src.get("frame", ""), src.get("line"))})' if src.get("text") else ""
        state = f"{change_state(c):9s}" if with_decisions else ""
        print(f"  {c['id']:24s} {state}{flag}{describe_change(c)}{where}")
        if with_decisions:
            for d in c.get("decisions", []):
                print(f"      {d['by']} {d['verdict']} {d['at']}: {clean(d.get('meaning'), 80)}")


def print_findings(hub: Hub, findings: list[dict], notes: list[dict] | None = None) -> int:
    """Errors first, each once per distinct problem with every place it occurs; returns the number of errors."""
    order = {"error": 0, "warning": 1, "note": 2}
    grouped: dict[tuple, list[str]] = {}
    for f in sorted(findings, key=lambda f: (order.get(f["severity"], 3), f["guard"], f["frame"], f["line"])):
        grouped.setdefault((f["severity"], f["guard"], f["detail"]), []).append(cite(hub, f["frame"], f["line"]))
    for (severity, guard, detail), places in grouped.items():
        places = sorted(set(places))
        tail = f"  (also {', '.join(places[1:])})" if len(places) > 1 else ""
        print(f"  [{severity}] {places[0]}: {guard}: {detail}{tail}")
    for n in notes or []:
        if n.get("severity") == "error":
            continue  # already listed as a grammar error finding
        print(f"  [note] {cite(hub, n['frame'], n['line'])}: {n['note']}: {clean(n.get('text'), 100)}")
    return sum(1 for f in findings if f["severity"] == "error")


def failure_lines(hub: Hub, state: dict) -> list[str]:
    """Why a run failed, from its Track: the step, the reason, the step's problems, and what to do next."""
    ev = next((e for e in reversed(hub.runner.track(state["id"])) if e["type"] == "run.failed"), {})
    step = ev.get("step") or "?"
    lines = [f"run {state['id']} failed at {step}: {clean(ev.get('reason'), 300)}"]
    env = ((state.get("steps") or {}).get(step) or {}).get("envelope") or {}
    for p in env.get("problems") or []:
        lines.append(f"    - [{p.get('severity', 'error')}] {clean(p.get('detail'), 200)}")
    if env.get("error") and env["error"].get("detail") and env["error"]["detail"] != ev.get("reason"):
        lines.append(f"    - {clean(env['error'].get('detail'), 200)}")
    owner = state.get("owner", "<owner>")
    lines.append(f"next: hubproto retry {state['id']} --as {owner} --field k=v ...   or   hubproto cancel {state['id']} --as {owner} --reason ...")
    return lines


def report_run(hub: Hub, state: dict, verb: str) -> int:
    """Print the run's outcome after a verb moved it; exit 1 when it failed."""
    line = f"run {state['id']} {verb} status={state['status']} release={short(state['release'])}"
    rec = hub.kernel.get(state["record"]) if state.get("record") else None
    if rec:
        line += f" record={rec['number']} state={rec['state']}"
    print(line)
    if state.get("pending"):
        p = state["pending"]
        rule = (p.get("route_rule") or {}).get("text")
        print(f"  waiting for: {', '.join(p['assignees'])}  ({p.get('title', '')}" + (f"; {clean(rule, 120)})" if rule else ")"))
    if state["status"] == "failed":
        for text in failure_lines(hub, state):
            print(text)
        return 1
    return 0


# ------------------------------------------------------------------ policy verbs
def cmd_init(args) -> int:
    root = Path(args.root)
    hub = Hub(root, frames_dir=root / "frames") if (root / "frames").exists() else None
    if hub is None:
        (root / "frames").mkdir(parents=True, exist_ok=True)
        hub = Hub(root)
    if hub.kernel.events(prefix="supplier:"):
        # Every start runs this (docker/entrypoint.sh): a hub seeded before inventory existed gets the sample stock
        # locations and part categories once; anything already there is left alone.
        had = bool(hub.kernel.query(type_="stock-location"))
        hub.kernel.seed()
        print(f"kernel already seeded at {hub.kernel.path}; " + ("nothing to do" if had else "added the sample stock locations and part categories"))
        return 0
    hub.kernel.seed()
    print(f"kernel seeded at {hub.kernel.path}")
    print("next: hubproto seed iso9001 (optional), then hubproto compile")
    return 0


def cmd_frames(args) -> int:
    hub = _hub(args)
    print(f"{'name':28s} {'version':9s} {'scope':12s} {'inherits':20s} file")
    for f in hub.frames.frames.values():
        print(f"{f.name:28s} v{f.version:8s} {f.scope:12s} {','.join(f.inherits) or '-':20s} {frame_file(hub, f.name)}")
    return 0


def _check_scopes(hub: Hub, scopes: list[str] | None) -> None:
    for s in scopes or []:
        if s not in hub.frames.frames:
            fail(f"--scope {s}: no Frame is named {s!r}; Frames are: {', '.join(sorted(hub.frames.frames))}", 2)


def cmd_withdraw_draft(args) -> int:
    hub = _hub(args)
    try:
        out = hub.withdraw_frame_draft(args.draft if args.draft.startswith("rec:") else f"rec:document:{args.draft}", args.as_, args.reason or "")
    except (ValueError, KeyError) as e:
        print(f"refused: {e}")
        return 1
    print(f"withdrew {out['draft']}" + (f"; proposal(s) withdrawn: {', '.join('#' + str(n) for n in out['proposals_withdrawn'])}" if out["proposals_withdrawn"] else ""))
    return 0


def cmd_compile(args) -> int:
    hub = _hub(args)
    _check_scopes(hub, args.scope)
    proposal = hub.compile(args.scope or None, extractor=hub.extractor(calls=True))  # the command line may wait on the local model
    number = proposal_number(hub, proposal["id"])
    errors = [f for f in proposal["findings"] if f["severity"] == "error"]
    warnings = [f for f in proposal["findings"] if f["severity"] != "error"]
    print(f"proposal #{number} {proposal['id']}  base={version_of(hub, proposal['base'])}  extractor={proposal['extractor']}")
    print("frames: " + ", ".join(f"{f['name']}@{f['version']}" for f in proposal["frames"]))
    print(f"rules: {len(proposal['rules'])}  changes: {len(proposal['changes'])}  "
          f"findings: {len(errors)} error(s), {len(warnings)} warning(s)  notes: {len(proposal['notes'])}")
    print_findings(hub, proposal["findings"], proposal["notes"])
    if proposal["changes"]:
        print("changes:")
        print_changes(hub, proposal["changes"])
        print_moved(proposal)
    else:
        print(f"no changes against the release in force ({version_of(hub, proposal['base'])}); nothing to review")
    print(f"reviewers: {', '.join(proposal['reviewers']) or 'none (no policy owner in the org model)'}")
    if errors:
        files = sorted({frame_file(hub, f["frame"]) for f in errors})
        print(f"not releasable: fix the {len(errors)} error(s) in {', '.join(files)} and compile again")
        return 1
    unread = [q for q in proposal.get("questions") or [] if q.get("unread_note") and q.get("status", "open") == "open"]
    if unread:
        for q in unread:
            print(f"[question] {frame_file(hub, q['frame'])}:{q['line']}: not read: {q.get('quote')}")
        print(f"not releasable yet: no reader read {len(unread)} sentence(s); a person answers each question "
              f"on the change order that carries it, or the author rewrites the sentence and compiles again")
        return 1
    if proposal["changes"] and proposal["reviewers"]:
        first = proposal["reviewers"][0]
        widen = any(c["widening"] for c in proposal["changes"])
        second = f", then another owner for the widenings ({', '.join(proposal['reviewers'][1:]) or 'none available'})" if widen else ""
        print(f"next: hubproto review #{number} --as {first} --approve-all{second}; then hubproto release #{number} --as {first}")
    return 0


def _proposal_status(hub: Hub, p: dict, ordered: list[dict], current: str | None) -> str:
    if p["status"] != "open":
        return p["status"]
    if p.get("base") != current:
        return f"stale (based on {version_of(hub, p.get('base'))}; {version_of(hub, current)} in force; compile again)"
    newer = [q for q in ordered if q["status"] == "open" and q.get("base") == p.get("base")
             and (q.get("created_at"), q["id"]) > (p.get("created_at"), p["id"])]
    if newer:
        return f"open, superseded by #{proposal_number(hub, newer[-1]['id'])}"
    return "open"


def cmd_proposals(args) -> int:
    hub = _hub(args)
    ordered = proposals_in_order(hub)
    cur = hub.releases.current()
    current = cur["digest"] if cur else None
    if not ordered:
        print("no proposals; run `hubproto compile`")
        return 0
    print(f"{'#':>3s}  {'id':12s}  {'created':25s}  {'base':20s}  {'changes':>7s}  {'decided':>7s}  {'errors':>6s}  status")
    numbers = proposal_numbers(hub)
    for p in ordered:
        i = numbers.get(p["id"], 0)
        if args.open and p["status"] != "open":
            continue
        decided = sum(1 for c in p["changes"] if change_state(c) != "pending")
        errors = sum(1 for f in p["findings"] if f["severity"] == "error")
        who = sorted({d["by"] for c in p["changes"] for d in c.get("decisions", [])})
        status = _proposal_status(hub, p, ordered, current)
        if p["status"] == "released":
            status += f" as {version_of(hub, p.get('release'))}"
        tail = f"  decisions by {', '.join(who)}" if who else ""
        print(f"{i:3d}  {p['id']:12s}  {p.get('created_at', '')[:25]:25s}  {version_of(hub, p.get('base')):20s}  {len(p['changes']):7d}  {decided:7d}  {errors:6d}  {status}{tail}")
    return 0


def cmd_proposal(args) -> int:
    hub = _hub(args)
    p = resolve_proposal(hub, args.id)
    ordered = proposals_in_order(hub)
    cur = hub.releases.current()
    print(f"proposal #{proposal_number(hub, p['id'])} {p['id']}  status={_proposal_status(hub, p, ordered, cur['digest'] if cur else None)}  "
          f"base={version_of(hub, p.get('base'))}  release={version_of(hub, p.get('release'))}  created={p.get('created_at', '')}")
    print(f"reviewers: {', '.join(p['reviewers']) or 'none'}")
    errors = print_findings(hub, p["findings"], p["notes"])
    if p["changes"]:
        print("changes:")
        print_changes(hub, p["changes"], with_decisions=True)
        print_moved(p)
    else:
        print("no changes")
    for line in hub.readiness(p["id"]):
        print("  needs: " + line)
    from .closure import release_blocker

    closed = release_blocker(hub, p) if p["status"] == "open" else ""
    if closed:  # the SOPs are documents: only an approved change order brings it into force
        print("  not released this way: " + closed)
    elif p["status"] == "open" and not errors and not hub.readiness(p["id"]):
        print(f"ready: hubproto release #{proposal_number(hub, p['id'])} --as {p['reviewers'][0] if p['reviewers'] else '<policy owner>'}")
    return 0


def cmd_review(args) -> int:
    hub = _hub(args)
    p = resolve_proposal(hub, args.id)
    require_member(hub, args.as_user, proposal=p)
    number = proposal_number(hub, p["id"])
    if p["status"] != "open":
        fail(f"proposal #{number} is {p['status']}; nothing to review (compile a new one)", 1)
    if args.as_user not in p["reviewers"]:
        fail(f"{args.as_user} is not a policy owner and cannot review changes (policy owners: {', '.join(p['reviewers']) or 'none'})", 1)
    if not p["changes"]:
        fail(f"proposal #{number} has no changes to review; it is already the release in force", 1)
    decisions = {}
    for item in args.decide or []:
        cid, sep, verdict = item.rpartition("=")
        if not sep or verdict not in ("approve", "reject", "defer"):
            fail(f"--decide takes CHANGE=approve|reject|defer, not {item!r}", 2)
        matches = [c["id"] for c in p["changes"] if c["id"].startswith(cid)]
        if len(matches) != 1:
            fail(f"--decide {cid}: {'no change' if not matches else str(len(matches)) + ' changes'} in proposal #{proposal_number(hub, p['id'])} "
                 f"start with {cid!r}; `hubproto proposal {p['id'][:4]}` lists them", 2)
        decisions[matches[0]] = verdict
    if not decisions and not args.approve_all:
        fail("say what you decide: --approve-all, or --decide CHANGE=approve|reject|defer (one per change)", 2)
    p = hub.review(p["id"], args.as_user, decisions=decisions, approve_all=args.approve_all, meaning=args.meaning)
    print(f"recorded {len(decisions) or len(p['changes'])} decision(s) by {args.as_user} on proposal #{proposal_number(hub, p['id'])}; status={p['status']}")
    needs = hub.readiness(p["id"])
    for line in needs:
        print("  needs: " + line)
    if p.get("eco"):
        print(f"it comes into force with {p['eco']} when that change order is approved and released")
    elif not needs:
        print(f"ready: hubproto release #{proposal_number(hub, p['id'])} --as {args.as_user}")
    return 0


def cmd_release(args) -> int:
    hub = _hub(args)
    p = resolve_proposal(hub, args.id)
    require_member(hub, args.as_user, proposal=p)
    meta = hub.release(p["id"], args.as_user, effective_from=args.effective_from)
    now = utcnow()
    when = f"in effect since {meta['effective_from']}" if meta["effective_from"] <= now else f"scheduled; takes effect {meta['effective_from']}"
    print(f"release {version_of(hub, meta['digest'])}  {when}  parent={version_of(hub, meta['parent'])}  released by {meta['released_by']}")
    return 0


def cmd_releases(args) -> int:
    hub = _hub(args)
    cur = hub.releases.current()
    ordered = releases_in_order(hub)
    if not ordered:
        print("no release yet; compile, review and release first")
        return 0
    now = utcnow()
    print(f"  {'version':8s} {'digest':12s}  {'effective from':25s}  {'by':8s}  {'proposal':16s}  parent")
    for i, m in enumerate(ordered, start=1):
        mark = "*" if cur and cur["digest"] == m["digest"] else " "
        state = "  (scheduled)" if m["effective_from"] > now else ""
        pnum = proposal_number(hub, m["proposal_id"])
        prop = f"#{pnum} {short(m['proposal_id'])}" if pnum else short(m["proposal_id"])
        print(f"{mark} v{i:<7d} {short(m['digest']):12s}  {m['effective_from'][:25]:25s}  {m['released_by']:8s}  {prop:16s}  {version_of(hub, m['parent'])}{state}")
    print("* in force; `hubproto release-diff v<n>` shows what a version changed")
    return 0


def _sentences(rules: list[dict]) -> dict[str, dict[str, int]]:
    """{frame: {sentence: file line}} of the sentences the rules cite."""
    out: dict[str, dict[str, int]] = {}
    for r in rules:
        src = r.get("source") or {}
        if src.get("text"):
            out.setdefault(src["frame"], {})[src["text"]] = int(src.get("line") or 0)
        for step in r.get("steps") or []:  # process steps carry their own lines
            if step.get("title"):
                out.setdefault(src.get("frame", ""), {})[f"{step.get('n', '')}. [{step.get('role', '')}] {step['title']}" + (f" ({step['cog']})" if step.get("cog") else "")] = int(step.get("line") or 0)
    return out


def release_diff(hub: Hub, meta: dict) -> dict:
    """Sentences added, removed and changed by a release against its parent, by Frame."""
    new = _sentences(hub.releases.rules(meta["digest"]))
    old = _sentences(hub.releases.rules(meta["parent"])) if meta.get("parent") else {}
    paths = {f["name"]: "frames/" + f["path"] for f in meta.get("frames", []) if f.get("path")}
    out = {"frames": {}, "added": 0, "removed": 0, "changed": 0}
    for frame in sorted(set(new) | set(old)):
        added = {t: n for t, n in new.get(frame, {}).items() if t not in old.get(frame, {})}
        removed = {t: n for t, n in old.get(frame, {}).items() if t not in new.get(frame, {})}
        changed = []
        for old_text, old_line in list(removed.items()):
            same_line = [t for t, n in added.items() if n == old_line]
            close = difflib.get_close_matches(old_text, list(added), n=1, cutoff=0.6)
            pick = same_line[0] if same_line else (close[0] if close else None)
            if pick:
                changed.append((old_line, old_text, added[pick], pick))
                removed.pop(old_text)
                added.pop(pick)
        if added or removed or changed:
            out["frames"][frame] = {"path": paths.get(frame, frame), "added": sorted(added.items(), key=lambda x: x[1]),
                                    "removed": sorted(removed.items(), key=lambda x: x[1]), "changed": sorted(changed)}
            out["added"] += len(added)
            out["removed"] += len(removed)
            out["changed"] += len(changed)
    return out


def cmd_release_diff(args) -> int:
    hub = _hub(args)
    meta = resolve_release(hub, args.ref)
    diff = release_diff(hub, meta)
    parent = version_of(hub, meta.get("parent"))
    pnum = proposal_number(hub, meta["proposal_id"])
    print(f"release {version_of(hub, meta['digest'])} against {parent}: {diff['added']} sentence(s) added, {diff['removed']} removed, "
          f"{diff['changed']} changed; released by {meta['released_by']} from proposal {'#' + str(pnum) + ' ' if pnum else ''}{short(meta['proposal_id'])}")
    if not meta.get("parent"):
        print("(first release: every sentence is new)")
    if not diff["frames"]:
        print("no sentence differs from the parent (the release re-rendered the same rules)")
    for frame, d in diff["frames"].items():
        print(d["path"])
        for text, line in d["added"]:
            print(f"  + L{line}: {clean(text, 140)}")
        for text, line in d["removed"]:
            print(f"  - L{line}: {clean(text, 140)}")
        for old_line, old_text, new_line, new_text in d["changed"]:
            print(f"  ~ L{old_line} -> L{new_line}: {clean(old_text, 100)}")
            print(f"      now: {clean(new_text, 120)}")
    # Decisions on the proposal that produced it.
    try:
        p = hub.proposals.load(meta["proposal_id"])
    except CompileError:
        p = None
    if p:
        decisions = [(d["by"], d["verdict"], d["at"], d.get("meaning", ""), c["id"]) for c in p["changes"] for d in c.get("decisions", [])]
        if decisions:
            by = {}
            for who, verdict, at, meaning, cid in decisions:
                by.setdefault((who, verdict), []).append((at, meaning))
            print("decided by:")
            for (who, verdict), items in sorted(by.items()):
                last = max(items)
                print(f"  {who} {verdict} x{len(items)}, last {last[0]}: {clean(last[1], 80)}")
    return 0


def cmd_why(args) -> int:
    hub = _hub(args)
    bundle = hub.current_bundle()
    if bundle is None:
        print("DENIED   no policy release is in effect")
        return 1
    require_member(hub, args.as_user)
    from .extract import singular_role

    objects = sorted({p["object"] for p in bundle["processes"].values()} | {t.get("object", n) for n, t in hub.kernel.types.items() if isinstance(t, dict)})
    obj = singular_role(args.object) if args.object != "*" else "*"
    if obj != "*" and obj not in objects:
        fail(f"--object {args.object}: no record type or process has that object; objects are: {', '.join(objects)}", 2)
    if args.folder not in bundle["folders"]:
        fail(f"--folder {args.folder}: not a folder in the release in force; folders are: {', '.join(bundle['folders'])}", 2)
    d = hub.why(args.as_user, args.action, args.object, args.folder)
    print(("ALLOWED  " if d["allowed"] else "DENIED   ") + d["why"])
    return 0 if d["allowed"] else 1


# ------------------------------------------------------------------ runs
def cmd_run(args) -> int:
    hub = _hub(args)
    require_member(hub, args.as_user)
    record = load_json_record(args.record_json, args.record_file)
    run_id = hub.runner.submit(args.process, {"record": record}, owner=args.as_user)
    return report_run(hub, hub.runner.load(run_id), "started")


def cmd_runs(args) -> int:
    hub = _hub(args)
    print(f"{'run':10s}  {'created':25s}  {'process':22s}  {'status':10s}  {'owner':10s}  {'record':16s}  {'release':12s}  waiting for")
    for s in hub.runner.list():
        if args.status and s["status"] != args.status:
            continue
        pend = ",".join(s["pending"]["assignees"]) if s.get("pending") else ""
        rec = (s.get("record") or "").rpartition(":")[2] or "-"
        print(f"{s['id']:10s}  {s.get('created_at', '')[:25]:25s}  {s['process'][:22]:22s}  {s['status']:10s}  {s['owner'][:10]:10s}  {rec[:16]:16s}  {short(s['release']):12s}  {pend}")
    return 0


def cmd_run_show(args) -> int:
    hub = _hub(args)
    s = hub.runner.load(resolve_run(hub, args.id))
    rec = hub.kernel.get(s["record"]) if s.get("record") else None
    print(f"run {s['id']} process={s['process']} owner={s['owner']} status={s['status']} release={short(s['release'])} "
          f"record={rec['number'] if rec else (s.get('record') or '-')} created={s.get('created_at', '')}")
    for step in s["manifest"]["steps"]:
        rec_ = s["steps"].get(step["id"])
        if not rec_:
            print(f"  {step['id']:36s} not-reached")
            continue
        env = rec_["envelope"]
        line = f"  {step['id']:36s} {rec_['status']:18s} cog={env['cog']['id']} ok={env['ok']} problems={len(env['problems'])}"
        if rec_.get("decision"):
            d = rec_["decision"]
            line += f"  decided by {d['by']} ({d['by_type']}): {d['verdict']}"
        print(line)
        for p in env.get("problems") or []:
            print(f"      - [{p.get('severity', 'error')}] {clean(p.get('detail'), 160)}")
    if s.get("pending"):
        print(f"  pending: {clean(s['pending']['title'], 60)} -> {', '.join(s['pending']['assignees'])}")
    if s.get("grant"):
        print(f"  grant: {s['grant']['id']} decided_by={s['grant']['decided_by']}")
    if s["status"] == "failed":
        for text in failure_lines(hub, s):
            print("  " + text)
    return 0


def cmd_track(args) -> int:
    hub = _hub(args)
    for ev in hub.runner.track(resolve_run(hub, args.id)):
        extra = {k: v for k, v in ev.items() if k not in ("seq", "at", "type", "run_id", "record_digest")}
        print(f"{ev['seq']:3d} {ev['at']} {ev['type']:18s} {clean(extra, 220)}")
    return 0


def cmd_decide(args) -> int:
    from .cli_records import parse_fields

    hub = _hub(args)
    require_member(hub, args.as_user)
    run_id = resolve_run(hub, args.id)
    state = hub.runner.load(run_id)
    seen = args.seen or state.get("seen_digest")
    fields = {}
    if args.field:
        rec = hub.kernel.get(state["record"]) if state.get("record") else None
        specs = hub.kernel.types.get(rec["type"], {}).get("fields", {}) if rec else {}
        fields = parse_fields(args.field, specs)
    if not (args.meaning or "").strip():
        # Let the core say why a signature needs its meaning (nothing is changed first: no fields are passed).
        hub.runner.decide(run_id, args.as_user, args.verdict, meaning="", payload_digest=seen)
    produced = [x for v in (args.produced or []) for x in v.replace(",", " ").split()]
    s = hub.runner.decide(run_id, args.as_user, args.verdict, meaning=args.meaning, payload_digest=seen, fields=fields or None,
                          produced=produced or None)
    return report_run(hub, s, f"{args.verdict}d by {args.as_user}")


def cmd_inbox(args) -> int:
    hub = _hub(args)
    require_member(hub, args.as_user, people_only=False)
    msgs = hub.inbox.for_principal(args.as_user)
    if not msgs:
        print(f"no messages for {args.as_user}")
    for m in msgs:
        run = f" run={m['run_id']}" if m.get("run_id") else ""
        print(f"{m['at']} [{m['channel']}/{m['kind']}] from {clean(m['from'], 20)}: {clean(m['subject'], 100)}{run}\n    {clean(m['body'], 300)}")
    return 0


def _attachments(paths: list[str] | None) -> list[dict]:
    attachments = []
    for path in paths or []:
        p = Path(path)
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as e:
            fail(f"cannot read attachment {path}: {e.strerror or e}", 2)
        try:
            content = json.loads(text)
        except json.JSONDecodeError:
            content = {"text": text}
        attachments.append({"name": p.name, "content": content})
    return attachments


def cmd_msg(args) -> int:
    hub = _hub(args)
    members = members_of(hub)
    if not members:
        fail("no policy release is in effect, so there is nobody to write to; compile and release first", 1)
    require_member(hub, args.from_user, flag="--as", members=members)
    require_member(hub, args.to, flag="--to", people_only=False, members=members)
    record = None
    if getattr(args, "record", None):
        rec = resolve_record(hub, args.record)
        record = rec["locator"]
    reply = hub.send(args.from_user, args.to, args.subject, args.body, channel=args.channel, attachments=_attachments(args.attach), record=record)
    for r in (reply.get("replies") or [reply]):
        if r.get("kind") == "reply":
            print(f"{r['from']} replied: {clean(r['body'], 600)}")
        elif r.get("kind") in ("request", "mention", "review"):
            print(f"{r['to']} was told: {clean(r['subject'], 200)}")
        else:
            print(f"delivered to {r['to']} via {r['channel']}")
    return 0


def cmd_mail_in(args) -> int:
    hub = _hub(args)
    try:
        msg = parse_eml(Path(args.file).read_text(encoding="utf-8"))
    except OSError as e:
        fail(f"cannot read {args.file}: {e.strerror or e}", 2)
    if not msg["to"]:
        fail(f"{args.file} has no To: header", 2)
    members = members_of(hub)
    if not members:
        fail("no policy release is in effect, so there is nobody to deliver to; compile and release first", 1)
    require_member(hub, msg["to"], flag="To:", people_only=False, members=members)
    reply = hub.send(msg["from"], msg["to"], msg["subject"], msg["body"], channel="email", attachments=msg["attachments"])
    if reply.get("kind") == "reply":
        print(f"{reply['from']} replied by email: {clean(reply['body'], 400)}")
    else:
        print(f"delivered to {reply['to']}")
    return 0


def cmd_ledger(args) -> int:
    hub = _hub(args)
    for j in hub.kernel.journals().values():
        print(f"{j['id']} {j['date']} {clean(j['memo'], 60)}  prepared={j['prepared_by']} approved={j['approved_by']} run={j['run_id']} release={short(j['release'])}")
        for line in j["lines"]:
            print(f"    {clean(line['account'], 28):28s} {line['debit']:10.2f} {line['credit']:10.2f}")
    return 0


def cmd_bills(args) -> int:
    hub = _hub(args)
    print(f"{'number':10s} {'supplier':16s} {'amount':>12s} {'cur':4s} {'state':28s} {'folder':20s} run")
    for b in hub.kernel.bills().values():
        amount = b.get("amount")
        amount_s = f"{amount:,.2f}" if isinstance(amount, (int, float)) else clean(amount, 12)
        print(f"{b['id']:10s} {clean(b.get('supplier'), 16):16s} {amount_s:>12s} {clean(b.get('currency'), 4):4s} {b['state']:28s} {clean(b['folder'], 20):20s} {b['run_id']}")
    return 0


def cmd_shell(args) -> int:
    from .shell import serve

    serve(_hub(args), host=args.host, port=args.port)
    return 0


def cmd_tick(args) -> int:
    hub = _hub(args)
    n = 0
    for e in hub.runner.tick(args.today):
        n += 1
        print(f"escalated run {e['run']} ({e['record']}) to {', '.join(e['to']) or 'nobody (reported to ' + ', '.join(e.get('informed') or []) + ')'}")
    print(f"tick done: {n} overdue item(s) escalated as of {args.today or datetime.now().date().isoformat()}")
    return 0


def cmd_models(args) -> int:
    from .model import OFF_MACHINE, available_models, hosted_allowed, resolve_model

    hub = _hub(args)
    av = available_models(refresh=True)
    print("installed in Ollama:" if av["ollama"] else f"Ollama: {av['ollama_error'] or 'no models installed'}")
    for m in av["ollama"]:
        if m.get("cloud"):  # nothing of it is on this machine: no size, and where it runs instead
            print(f"  ollama/{m['name']:28s} cloud: {OFF_MACHINE}" + ("" if hosted_allowed() else " (not available: hosted models are off)"))
        else:
            print(f"  ollama/{m['name']:28s} {m['params']:>7s} {m['family']:10s} {m['quant']:8s} {m['size_gb']} GB")
    print("Claude:", "Anthropic key set" if av["anthropic_key"] else "no Anthropic key: a cog bundled with a claude-* model is not available here")
    meta = hub.releases.current()
    cogs = (hub.releases.bundle(meta["digest"]).get("cogs") or {}) if meta else {}
    print("cogs:")
    for name, prof in sorted(cogs.items()):
        r = resolve_model(prof.get("model") or "")
        where = f"{r['backend']}: {OFF_MACHINE}" if r["backend"] == "ollama-cloud" else r["backend"]
        print(f"  {name:22s} {prof.get('model') or '-':22s} " + (f"-> {r['served']} ({where})" if r["available"] else f"NOT AVAILABLE: {r['note']}"))
    return 0


def cmd_agent(args) -> int:
    from .agent import Agent

    hub = _hub(args)
    require_member(hub, args.as_user, flag="--as", members=members_of(hub))
    agent = Agent(hub, args.cog)
    if args.confirm:
        from . import authz
        from .agent import AgentStore

        evs = AgentStore(hub.root / "state" / "agents").load(args.confirm)
        if not evs:
            fail(f"no agent run {args.confirm}", 2)
        started = evs[0]
        if started.get("cog") != args.cog:
            fail(f"run {args.confirm} is {started.get('cog')}'s; confirm it with --cog {started.get('cog')}", 2)
        meta = hub.releases.current()
        bundle = hub.releases.bundle(meta["digest"]) if meta else {"members": {}}
        if args.as_user != started.get("by") and "policy-owner" not in authz.roles_of(bundle, args.as_user):
            fail(f"only {started.get('by')} (who gave the task) or a policy owner confirms its actions", 1)
        try:
            out = agent.confirm(args.confirm, args.as_user)
        except ValueError as e:
            fail(str(e), 1)
        for r in out["results"]:
            print(f"  {r['tool']}: {clean(json.dumps(r['result'], default=str), 200)}")
        return 0
    if not args.goal:
        fail("give the cog a --goal", 2)
    try:
        res = agent.run(args.goal, by=args.as_user, max_actions=args.actions)
    except ValueError as e:
        fail(str(e), 1)
    print(f"run {res['run']}: {res['status']}, {res['actions']} action(s)")
    print(clean(res["summary"], 1200))
    for p in res["proposed"]:
        print(f"  proposed {p['tool']}: {clean(json.dumps(p['arguments'], default=str), 200)}")
    if res["proposed"]:
        print(f"confirm with: hubproto agent --cog {args.cog} --as {args.as_user} --confirm {res['run']}")
    return 0


def cmd_agent_duties(args) -> int:
    from .agent import run_duties

    hub = _hub(args)
    while True:
        done = run_duties(hub, args.cog)
        for d in done:
            print(f"{d['cog']} took up a duty ({d['status']}, {d['actions']} action(s)): {clean(d['duty'], 80)}\n  {clean(d['summary'], 300)}")
        if not args.loop:
            print(f"agent-duties done: {len(done)} duty run(s)")
            return 0
        time.sleep(args.loop)


def cmd_cog_users(args) -> int:
    """Cogs as users, as a service: work the cogs' queues (mentions, messages, Gates, tasks) in this process. Items
    waiting for a cog that have no task yet are put in its queue first. The shell works the queues itself unless
    HUBPROTO_COG_QUEUE=service, in which case this verb (or the cog-users service) does."""
    hub = _hub(args)
    hub.queue.mode = "service"  # tasks asked while working are queued and taken in turn below
    if args.loop:
        n = hub.queue.recover()
        if n:
            print(f"{n} task(s) left working by a stopped worker went back in the queue")
    while True:
        for state in hub.runner.list():
            pending = state.get("pending") or {}
            if state["status"] == "waiting" and pending.get("assignee_type") == "cog" and pending.get("kind", "decision") == "decision" \
                    and (not args.cogs or pending["assignees"][0] in args.cogs):
                hub.runner.give_to_cog(state)
        done = hub.queue.drain(cogs=args.cogs)
        taken = 0
        for t in done:
            res = t.get("result") or {}
            if t["kind"] == "gate" and res.get("outcome"):
                taken += 1
                print(f"{t['cog']} {res['outcome']} run {res['run']} ({t.get('record') or ''}): now {res['status']}")
            else:
                print(f"{t['cog']} {t['kind']} {t['status']}: {t['title'][:80]}" + (f" ({t['error']})" if t.get("error") else ""))
        if not args.loop:
            print(f"cog-users done: {len(done)} task(s) worked, {taken} item(s) taken")
            return 0
        time.sleep(args.loop)


def _conversion_report(out: dict | None) -> int:
    """What the bootstrap's conversion did, in words: 0 when the hub is under change control (or explicitly keeps the
    Frame files), 1 when the SOPs could not be converted."""
    if out is None:
        return 0
    if out.get("kept"):
        k = out["kept"]
        print(f"the Frame files stay the source (kept by {clean(k.get('by'))}: {clean(k.get('reason'))}); the policy owners release "
              "changes without change orders until `hubproto sops convert`")
        return 0
    if out.get("blockers"):
        print("the SOPs were not converted to documents, so changes are not under change control yet: " + clean("; ".join(out["blockers"])))
        print("fix that and run this command again (or `hubproto sops convert --as <policy owner>`)")
        return 1
    if out.get("made"):
        print(f"the SOPs are documents now ({len(out['made'])}): from here on {CO_ROUTE[0].lower() + CO_ROUTE[1:]}")
    return 0


def cmd_bootstrap_release(args) -> int:
    """First release on a fresh hub: compile, approve with two policy owners, release; then the SOPs become documents,
    so every later change needs a change order (unless --keep-frame-files says, on the record, why not)."""
    from . import sopdocs

    hub = _hub(args)
    cur = hub.releases.current()
    members = members_of(hub)  # before the first release: read from the Frames being bootstrapped
    owners = sorted(p for p, m in members.items() if POLICY_OWNER_ROLE in m["roles"] and m["type"] == "person")
    for who, flag in ((args.as_user, "--as"), (args.second, "--second")):
        if who:
            require_member(hub, who, flag=flag, members=members)
            if who not in owners:
                fail(f"{flag} {who}: {who} is not a policy owner; policy owners in the org model are: {', '.join(owners) or 'none (add the policy-owner role to a member)'}", 2)
    if args.keep_frame_files is not None:
        try:
            sopdocs.keep_frame_files(hub, by=args.as_user, reason=args.keep_frame_files)
        except ValueError as e:
            fail(str(e), 1)
    if cur and not args.force:
        if not hub.sop_source() and not sopdocs.frame_files_kept(hub):
            # Released, but the bootstrap stopped before the SOPs became documents: finish it.
            print(f"a release is already in effect ({version_of(hub, cur['digest'])}); converting the SOPs to documents")
            return _conversion_report(sopdocs.convert_after_release(hub, by=args.as_user))
        print(f"a release is already in effect ({version_of(hub, cur['digest'])}); " +
              ("nothing to do" if args.if_needed else "use --force to compile and release the Frames again"))
        return 0 if args.if_needed else 1
    proposal = hub.compile()
    number = proposal_number(hub, proposal["id"])
    errors = print_findings(hub, proposal["findings"], proposal["notes"])
    if errors:
        files = sorted({frame_file(hub, f["frame"]) for f in proposal["findings"] if f["severity"] == "error"})
        print(f"bootstrap stopped: fix the {errors} error(s) above in {', '.join(files)}, then run this command again")
        return 1
    for who in (args.as_user, args.second):
        if who:
            hub.review(proposal["id"], who, approve_all=True, meaning="bootstrap release")
    meta = hub.release(proposal["id"], args.as_user)  # the first release converts the SOPs (closure.after_release)
    print(f"release {version_of(hub, meta['digest'])} in effect (rules: {len(proposal['rules'])}, proposal #{number} {proposal['id']})")
    out = getattr(hub, "last_conversion", None) or sopdocs.convert_after_release(hub, by=args.as_user)
    return _conversion_report(out)


# ------------------------------------------------------------------ operations: status, backup, restore
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frames_drift(hub: Hub, meta: dict) -> dict:
    """Frames on disk against the release's bill of materials: changed, missing and new files."""
    root = hub.frames.root
    changed, missing = [], []
    known = set()
    for f in meta.get("frames", []):
        path = root / f.get("path", "")
        known.add(f.get("path", ""))
        if not path.exists():
            missing.append("frames/" + f.get("path", f["name"]))
        elif _sha256(path) != f.get("sha256"):
            changed.append("frames/" + f["path"])
    new = []
    for path in sorted(root.rglob("*.frame.md")):
        rel = str(path.relative_to(root))
        if any(part.startswith(".") for part in Path(rel).parts):
            continue
        if rel not in known:
            frame = hub.frames.frames.get(next((n for n, fr in hub.frames.frames.items() if fr.path == path), ""))
            if frame is not None and frame.scope == "reference":
                continue
            new.append("frames/" + rel)
    return {"changed": changed, "missing": missing, "new": new}


def config_drift(hub: Hub, cur: dict) -> bool:
    """Once configuration changes only through change orders: the record types in force and how the configuration
    files differ from what is in force. Prints it; True when a file differs."""
    from . import recordtypes

    entry = recordtypes.in_force(hub)
    print(f"record types in force: v{entry['version']} ({clean(entry.get('source'))})")
    differs = False
    rt = recordtypes.drift(hub)
    if rt:
        differs = True
        print(f"config/{recordtypes.FILE} differs from the record types in force (v{rt['version']}); it is not used:")
        for line in rt["in_file"][:20]:
            print(f"  in the file, not in force: {clean(line)}")
        if len(rt["in_file"]) > 20:
            print(f"  ... and {len(rt['in_file']) - 20} more")
        print(f"to bring what it adds into force: hubproto record-types carry <change order> --from-file --as <person>, "
              "then approve and release the change order")
    from . import configdocs

    for doc, name in sorted(configdocs.DOCS.items()):
        entry = configdocs.in_force(hub, doc)
        print(f"{configdocs.WORDS[doc]} in force: v{entry['version']} ({clean(entry.get('source'))})")
        d = configdocs.drift(hub, doc)
        if not d:
            continue
        differs = True
        print(f"config/{name} differs from the {configdocs.WORDS[doc]} in force (v{d['version']}); it is not used:")
        for line in d["in_file"][:20]:
            print(f"  in the file, not in force: {clean(line)}")
        for line in d["in_force"][:20]:
            print(f"  in force, not in the file: {clean(line)}")
        print(f"to bring a change into force: put it on a change order (hubproto config propose FILE.json --as <person>), then "
              "approve and release the change order")
    recorded = cur.get("config") or {}  # any other configuration file (the audit's standard clauses): reported only
    governed = {recordtypes.FILE, *configdocs.DOCS.values()}
    for name in sorted(hub.kernel.config_digests):
        if name in governed or name not in recorded:
            continue
        current = _config_sha(Path(hub.kernel.config_dir) / name)
        if current != recorded[name]:
            differs = True
            print(f"config/{name} differs from what the release in force, {version_of(hub, cur['digest'])}, recorded: it is used as it "
                  "is (it is not under change control)")
    return differs


def _config_sha(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    data.pop("$comment", None)
    return sha256_of(data)


def cmd_status(args) -> int:
    hub = _hub(args)
    root = Path(args.root).resolve()
    cur = hub.releases.current()
    print(f"hub: {root}")
    if cur is None:
        print("release in force: none; compile, review and release (or bootstrap-release) first")
        rc = 1 if args.check else 0
    else:
        print(f"release in force: {version_of(hub, cur['digest'])}  effective {cur['effective_from']}  released by {cur['released_by']}")
        upcoming = [m for m in hub.releases.list() if m["effective_from"] > utcnow()]
        for m in upcoming:
            print(f"scheduled: {version_of(hub, m['digest'])} takes effect {m['effective_from']}")
        rc = 0
        closed = hub.sop_source()
        if closed:
            print("configuration changes only through change orders (the SOPs are documents; `hubproto sops status`)")
        else:
            from . import sopdocs

            kept = sopdocs.frame_files_kept(hub)
            if kept:
                print(f"the Frame files are the source (kept by {clean(kept.get('by'))}: {clean(kept.get('reason'))}): the policy owners "
                      "release changes without change orders until `hubproto sops convert`")
            else:
                print("the SOPs are not documents yet, so changes are released without change orders: "
                      + clean("; ".join(sopdocs.conversion_blockers(hub)) or "convert them: hubproto sops convert --as <policy owner>"))
                rc = 1 if args.check else 0
        drift = frames_drift(hub, cur)
        if any(drift.values()) and closed:
            print(f"Frames on disk differ from the release in force ({version_of(hub, cur['digest'])}):")
            for kind in ("changed", "missing", "new"):
                for path in drift[kind]:
                    print(f"  {kind}: {path}")
            print("they are not used: the Frame is compiled from the SOP documents. " + CO_ROUTE)
            rc = 1 if args.check else 0
        elif any(drift.values()):
            owners = policy_owners(hub) or ["<policy owner>"]
            first, second = owners[0], (owners[1] if len(owners) > 1 else owners[0])
            print(f"Frames on disk differ from the release in force ({version_of(hub, cur['digest'])}):")
            for kind in ("changed", "missing", "new"):
                for path in drift[kind]:
                    print(f"  {kind}: {path}")
            print("to put them in force:")
            print(f"  hubproto compile && hubproto review <proposal> --as {first} --approve-all && hubproto review <proposal> --as {second} --approve-all && hubproto release <proposal> --as {first}")
            print(f"until then the shell and every run apply {version_of(hub, cur['digest'])}, the Frames as released")
            rc = 1 if args.check else 0
        else:
            print("Frames on disk match the release in force")
        if closed and config_drift(hub, cur) and args.check:
            rc = 1
    open_props = [p for p in proposals_in_order(hub) if p["status"] == "open"]
    if open_props:
        print(f"open proposals: {', '.join('#' + str(proposal_number(hub, p['id'])) for p in open_props)} (`hubproto proposals`)")
    counts: dict[str, int] = {}
    for s in hub.runner.list():
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    print("runs: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none"))
    return rc


def cmd_verify(args) -> int:
    """Check the evidence: the kernel's event chain, every run's Track chain and signatures, and every stored blob
    against its hash. Exit 1 when anything was changed after it was written."""
    import hashlib

    hub = _hub(args)
    problems = [p for p in hub.kernel.verify_chain() if "not chained" not in p]
    notes = [p for p in hub.kernel.verify_chain() if "not chained" in p]
    for st in hub.runner.list():
        for p in hub.runner.verify_signatures(st["id"]):
            (notes if "not chained" in p else problems).append(f"run {st['id']} ({st['process']}): {p}")
    blobs = Path(args.root) / "state" / "blobs"
    damaged = 0
    for b in blobs.iterdir() if blobs.exists() else []:
        if b.is_file() and not b.name.startswith(".") and hashlib.sha256(b.read_bytes()).hexdigest() != b.name:
            problems.append(f"blob {b.name[:12]} is damaged")
            damaged += 1
    for n in notes:
        print(f"note: {n}")
    for p in problems:
        print(f"PROBLEM: {p}")
    print(f"verify: {len(problems)} problem(s); {len(hub.runner.list())} run(s), kernel chain checked, blobs checked")
    return 1 if problems else 0


def cmd_backup(args) -> int:
    hub = _hub(args)
    state = Path(args.root) / "state"
    dest = Path(args.dir)
    if dest.exists() and any(dest.iterdir()):
        fail(f"{dest} exists and is not empty; back up into a new or empty directory", 2)
    dest.mkdir(parents=True, exist_ok=True)
    # The SQLite backup API copies a consistent snapshot even while the shell holds the file open.
    src = sqlite3.connect(str(hub.kernel.path))
    dst = sqlite3.connect(str(dest / "kernel.db"))
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    parts = {}
    for part in BACKUP_PARTS:
        source = state / part
        if source.exists():
            shutil.copytree(source, dest / part, symlinks=False)
            parts[part] = sum(1 for _ in (dest / part).rglob("*") if _.is_file())
        else:
            (dest / part).mkdir()
            parts[part] = 0
    for name in SQLITE_PARTS:
        if (state / name).exists():
            s_db = sqlite3.connect(str(state / name))
            d_db = sqlite3.connect(str(dest / name))
            with d_db:
                s_db.backup(d_db)
            d_db.close()
            s_db.close()
            parts[name] = 1
    if (state / ".secret").exists():  # signatures and editor tokens verify against it
        shutil.copyfile(state / ".secret", dest / ".secret")
        parts[".secret"] = 1
    frames = Path(args.root) / "frames"
    # The hub's Frames (in Docker its frames volume): every release writes the released texts there. They are copied
    # after the kernel snapshot, with no lock: a release during the backup can leave Frames newer than the state, which
    # a restore then reports as drift. Stop the hub for a backup consistent across all parts.
    if frames.is_dir():
        shutil.copytree(frames, dest / "frames", symlinks=False, ignore=_not_set_aside(frames))
        parts["frames"] = sum(1 for _ in (dest / "frames").rglob("*") if _.is_file())
        if not _frame_files(dest / "frames"):
            print(f"WARNING: {frames} holds no Frame files; a restore of this backup leaves the Frames where it restores "
                  "as they are")
    cur = hub.releases.current()
    manifest = {"created_at": utcnow(), "root": str(Path(args.root).resolve()), "kernel": "kernel.db", "parts": parts,
                "release": cur["digest"] if cur else None}
    for name in ("proposal-numbers.json", "frame-files-kept.json"):  # the second: the hub keeps its Frame files (sopdocs)
        if (state / name).exists():
            shutil.copyfile(state / name, dest / name)
    (dest / "backup.json").write_text(json.dumps(manifest, indent=1))
    print(f"backup written to {dest}: kernel.db, " + ", ".join(f"{k} ({v} files)" for k, v in parts.items()))
    print("restore with: hubproto --root <new root> restore " + str(dest))
    return 0


def _not_set_aside(frames: Path):
    """For ``shutil.copytree``: leave out the Frames an earlier restore set aside (frames/.before-restore-<time>/)."""
    top = frames.resolve()
    return lambda d, names: [n for n in names if Path(d).resolve() == top and n.startswith(".before-restore-")]


def _set_aside_frames(frames: Path, stamp: str) -> Path:
    """Move the Frames being replaced into frames/.before-restore-<time>/ (in the container frames/ is a volume's
    mount point, so it is never renamed); the Frame store and status skip dot-directories."""
    aside, n = frames / f".before-restore-{stamp}", 1
    while aside.exists():  # two restores in the same second
        n += 1
        aside = frames / f".before-restore-{stamp}-{n}"
    aside.mkdir()
    for child in list(frames.iterdir()):
        if not child.name.startswith(".before-restore-"):
            child.rename(aside / child.name)
    return aside


def _set_aside(state: Path, src: Path, stamp: str) -> Path:
    """Move the state being replaced out of the way: beside it (state.before-restore-<time>) when it can be renamed;
    inside it (state/.before-restore-<time>) when it cannot, as in the container where /hub/state is the volume's mount
    point, or when the backup being restored lies inside it (README: /hub/state/backups/...): then the folder that holds
    the backup stays where it is."""
    src_res, st_res = src.resolve(), state.resolve()
    inside = st_res in src_res.parents
    if not inside and not os.path.ismount(state):
        beside = state.parent / f"state.before-restore-{stamp}"
        try:
            state.rename(beside)
            return beside
        except OSError:  # busy (a mount point the check missed) or another file system: set it aside in place
            pass
    aside = state / f".before-restore-{stamp}"
    aside.mkdir()
    for child in list(state.iterdir()):
        if child.name.startswith(".before-restore-") or (inside and (child.resolve() == src_res or child.resolve() in src_res.parents)):
            continue
        child.rename(aside / child.name)
    return aside


def cmd_restore(args) -> int:
    src = Path(args.dir)
    if not src.exists():
        fail(f"no backup at {src}", 2)
    try:
        listed = list((json.loads((src / "backup.json").read_text()).get("parts") or {}).keys())
    except (OSError, json.JSONDecodeError):
        listed = []
    # Every part the backup says it holds must be there (older backups list fewer parts).
    missing = [name for name in ("backup.json", "kernel.db", *dict.fromkeys(listed or BACKUP_PARTS[:5])) if not (src / name).exists()]
    if missing:
        fail(f"{src} is not a complete backup: missing {', '.join(missing)}; a partial restore would leave records pointing at runs or releases that are not there", 2)
    try:
        manifest = json.loads((src / "backup.json").read_text())
    except (OSError, json.JSONDecodeError) as e:
        fail(f"{src / 'backup.json'} does not parse: {e}", 2)
    root = Path(args.root)
    state = root / "state"
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    if state.exists() and any(state.iterdir()):
        if not args.force:
            fail(f"{state} already holds a hub; stop the shell and pass --force to replace it (the old state is kept beside it)", 2)
        moved = _set_aside(state, src, stamp)
        print(f"previous state moved to {moved}")
    state.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src / "kernel.db", state / "kernel.db")
    for part in BACKUP_PARTS:
        if (src / part).exists():
            shutil.copytree(src / part, state / part, symlinks=False)
    for name in (*SQLITE_PARTS, ".secret"):
        if (src / name).exists():
            shutil.copyfile(src / name, state / name)
    for name in ("proposal-numbers.json", "frame-files-kept.json"):
        if (src / name).exists():
            shutil.copyfile(src / name, state / name)
    frames = root / "frames"
    with_frames = (src / "frames").is_dir()  # the Frames as the hub had them when it was backed up (older backups hold none)
    if with_frames and not _frame_files(src / "frames"):
        # Putting them in place would set the hub's Frames aside and leave none: every Frame of the release missing.
        print(f"WARNING: {src / 'frames'} holds no Frame files (the hub had none when it was backed up), so the Frames in "
              f"{frames} are left as they are, not replaced by an empty folder")
        with_frames = False
    elif with_frames:
        if frames.is_dir() and any(frames.iterdir()):
            print(f"previous Frames moved to {_set_aside_frames(frames, stamp)}")
        frames.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src / "frames", frames, symlinks=False, dirs_exist_ok=True)
    elif frames.is_dir():
        print(f"this backup holds no Frames (it was made before backups copied them): {frames} is left as it is")
    hub = Hub(root, frames_dir=root / "frames") if (root / "frames").exists() else None
    cur = hub.releases.current() if hub else None
    print(f"restored {src} (taken {manifest.get('created_at', '?')}) into {state}")
    if hub:
        print(f"release in force: {version_of(hub, cur['digest']) if cur else 'none'}; runs: {len(hub.runner.list())}; records: {len(hub.kernel.query())}")
        drift = frames_drift(hub, cur) if cur else {}
        if any(drift.values()):  # what `status --check` will report
            print(f"Frames on disk differ from the release in force ({version_of(hub, cur['digest'])}):")
            for kind in ("changed", "missing", "new"):
                for path in drift[kind]:
                    print(f"  {kind}: {path}")
            if with_frames:
                print("if the backup was taken while the hub ran, its Frames and its state may be from different moments "
                      "(stop the hub for a consistent backup); `hubproto status` says how to bring them into line")
            else:
                print("this backup holds no Frames, so the Frames here may not be the ones the restored release was made "
                      "from: put the hub's Frames from that time in place, or see `hubproto status`")
        elif cur:
            print("Frames on disk match the release in force")
    else:
        print("no frames/ directory beside the restored state yet; copy the Frames in before compiling")
    return 0


def _frame_files(frames: Path) -> list[Path]:
    """The Frame files the Frame store reads (dot-directories such as .retired and .replaced left out)."""
    return [p for p in frames.rglob("*.frame.md")
            if not any(part.startswith(".") for part in p.relative_to(frames).parts)]


# ------------------------------------------------------------------ parser
EPILOG = """\
the policy path, in the order an operator uses it:
  init           create the hub's state under --root; on an existing hub, add what it lacks
  seed           copy a seed profile's Frames in (iso9001)
  compile        read the Frames into a proposal; error findings block release
  proposals      what is waiting for a policy owner; proposal <id> shows one
  review         approve or reject each change (a widening needs two owners)
  release        put an approved proposal in force
  releases, release-diff, status, why, frames, conformance    then look at what is in force
  shell          serve the workspace; records verbs read and change the kernel
  dev            development hubs: dev serve --detach --name NAME, dev list, dev stop, dev clean
ids: records by number (BILL-0001, any case) or locator; runs by id prefix; proposals by
  number (#3) or id prefix; releases by version (v2) or digest prefix; digests show 12 characters.
exit codes: 0 done; 1 refused by the hub or a failed outcome (error findings, DENIED, a failed run);
  2 a mistake in the command (unknown id, bad JSON, missing file, wrong --root).
"""


def _conformance_parser(sub) -> None:
    """The profile module's ``conformance`` takes both --profile and a positional that silently wins; one spelling."""
    from . import profile

    if "conformance" in sub.choices:
        sub.choices.pop("conformance", None)
        sub._choices_actions = [a for a in sub._choices_actions if a.dest != "conformance"]
    s = sub.add_parser("conformance", help="print a profile's clause coverage from the release in force (or the Frames)")
    s.add_argument("--profile", default="iso9001", help="profile name under profiles/ (default iso9001)")
    s.set_defaults(fn=profile.cmd_conformance, profile_pos=None)


def _frames_are_the_source(fn, what: str):
    """A verb that only makes sense while the Frame files are the source: refused once the SOPs are documents."""
    def run(args) -> int:
        if Hub(Path(args.root)).sop_source():
            fail(f"the SOPs are documents, so {what}: the Frame is compiled from the documents. {CO_ROUTE}", 1)
        return fn(args)
    return run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hubproto", formatter_class=argparse.RawDescriptionHelpFormatter,
                                description="Frames (policy prose) compile into a policy release; records and runs are governed by it.",
                                epilog=EPILOG)
    p.add_argument("--root", default=os.environ.get("HUBPROTO_ROOT", "."),
                   help="the hub directory holding frames/ and state/ (default: $HUBPROTO_ROOT, else the current directory)")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="VERB", title="verbs")
    AS = dict(dest="as_user", required=True, metavar="PERSON")

    # --- policy -----------------------------------------------------------
    sub.add_parser("init", help="create the hub's state (kernel, suppliers, purchase orders, periods, stock locations) under --root; "
                                "on an existing hub, add only what it lacks (stock locations, part categories)").set_defaults(fn=cmd_init)
    from . import profile

    profile.register(sub)  # seed, conformance
    sub.choices["seed"].set_defaults(fn=_frames_are_the_source(profile.cmd_seed, "a seed profile's Frames would not be used"))
    sub.choices["seed"].description = "Copy a seed profile's Frames (SOPs, work instructions, templates) into frames/<profile>/; compile afterwards."
    s = sub.add_parser("compile", help="read the Frames into a proposal of changes against the release in force; exit 1 on error findings")
    s.add_argument("--scope", action="append", metavar="FRAME", help="compile only this Frame (and what it inherits); repeatable")
    s.set_defaults(fn=cmd_compile)
    s = sub.add_parser("verify", help="check the evidence: event chain, run Tracks and signatures, stored files; exit 1 on tampering")
    s.set_defaults(fn=cmd_verify)
    s = sub.add_parser("withdraw-draft", help="withdraw a Frame draft: it leaves open proposals and is no longer compiled")
    s.add_argument("draft", help="the draft document's number (DOC-0431) or locator")
    s.add_argument("--as", dest="as_", required=True, help="the draft's author or a policy owner")
    s.add_argument("--reason", default="", help="why it is withdrawn (kept on the draft and the proposal)")
    s.set_defaults(fn=cmd_withdraw_draft)
    s = sub.add_parser("proposals", help="list proposals: number, status, base release, changes, decisions so far")
    s.add_argument("--open", action="store_true", help="only proposals that are still open")
    s.set_defaults(fn=cmd_proposals)
    s = sub.add_parser("proposal", help="show one proposal: findings, each change with its decisions, what it still needs")
    s.add_argument("id", help="proposal number (#3) or id prefix")
    s.set_defaults(fn=cmd_proposal)
    s = sub.add_parser("review", help="record a policy owner's decisions on a proposal's changes")
    s.add_argument("id", help="proposal number (#3) or id prefix")
    s.add_argument("--as", help="the policy owner deciding", **AS)
    s.add_argument("--approve-all", action="store_true", help="approve every change in the proposal")
    s.add_argument("--decide", action="append", metavar="CHANGE=verdict", help="one change (id prefix) with approve, reject or defer; repeatable")
    s.add_argument("--meaning", default="", metavar="TEXT", help="what the approval means, in your words (recorded with the decision)")
    s.set_defaults(fn=cmd_review)
    s = sub.add_parser("release", help="put an approved proposal in force as a new policy release")
    s.add_argument("id", help="proposal number (#3) or id prefix")
    s.add_argument("--as", help="the policy owner releasing (must be one of the proposal's reviewers)", **AS)
    s.add_argument("--effective-from", type=iso_timestamp, metavar="WHEN", help="YYYY-MM-DD or an ISO timestamp; default now (a future time schedules it)")
    s.set_defaults(fn=cmd_release)
    sub.add_parser("releases", help="list releases as versions (v1, v2, ...) with digest, effective date, releaser and parent").set_defaults(fn=cmd_releases)
    s = sub.add_parser("release-diff", help="sentences a release added, removed or changed against its parent, by Frame and file line, with the decisions")
    s.add_argument("ref", help="version (v2) or digest prefix")
    s.set_defaults(fn=cmd_release_diff)
    s = sub.add_parser("status", help="the release in force, whether the Frames on disk differ from it, open proposals and run counts")
    s.add_argument("--check", action="store_true", help="exit 1 when no release is in force or the Frames on disk differ from it")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("why", help="may a person do an action on an object in a folder? ALLOWED or DENIED with the sentence; exit 1 on DENIED")
    s.add_argument("--as", help="the person asked about", **AS)
    s.add_argument("--action", required=True, metavar="ACTION", help="create, read or approve")
    s.add_argument("--object", required=True, metavar="OBJECT", help="the record object as the sentences say it: bill or bills, document, ...")
    s.add_argument("--folder", required=True, metavar="FOLDER", help="a folder from the org model, for example Finance/Payables")
    s.set_defaults(fn=cmd_why)
    sub.add_parser("frames", help="list the Frames on disk with version, scope, inheritance and file").set_defaults(fn=cmd_frames)
    _conformance_parser(sub)

    # --- runs ------------------------------------------------------------
    s = sub.add_parser("run", help="start a process for a person with a record; prints the failure reason and exits 1 if it fails")
    s.add_argument("process", help="process name from the release (hubproto status, or the shell's home page)")
    s.add_argument("--as", help="the person starting it (must hold the first step's role)", **AS)
    s.add_argument("--record-json", metavar="JSON", help="the record's fields as a JSON object")
    s.add_argument("--record-file", metavar="PATH", help="a file holding the record's fields as a JSON object")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("runs", help="list runs with created time, status, owner, record and who they wait for")
    s.add_argument("--status", choices=["submitted", "running", "waiting", "failed", "rejected", "completed", "cancelled"], help="only runs in this status")
    s.set_defaults(fn=cmd_runs)
    s = sub.add_parser("run-show", help="one run: its steps, cog results, decisions, pending Gate and failure reason")
    s.add_argument("id", help="run id prefix")
    s.set_defaults(fn=cmd_run_show)
    s = sub.add_parser("track", help="a run's Track: every event in order")
    s.add_argument("id", help="run id prefix")
    s.set_defaults(fn=cmd_track)
    s = sub.add_parser("decide", help="sign a pending Gate as a person: approve or reject, with the meaning of the signature")
    s.add_argument("id", help="run id prefix")
    s.add_argument("--as", help="the assignee deciding", **AS)
    s.add_argument("--verdict", choices=["approve", "reject"], required=True, help="approve or reject")
    s.add_argument("--meaning", default="", metavar="TEXT", help="what you are confirming (or why you reject); required")
    s.add_argument("--field", action="append", metavar="K=V", help="a field the Gate accepts (inbox-pending lists them); repeatable")
    s.add_argument("--produced", action="append", metavar="NUMBER", help="a record your work on the item produced, linked to it when it "
                   "completes (when its process says so); repeatable, or several separated by commas")
    s.add_argument("--seen", metavar="DIGEST", help="the seen digest from inbox-pending; default: the item as it is now")
    s.set_defaults(fn=cmd_decide)
    s = sub.add_parser("tick", help="escalate overdue Gates as the routing sentences say")
    s.add_argument("--today", type=iso_date, metavar="YYYY-MM-DD", help="treat this date as today (default: today)")
    s.set_defaults(fn=cmd_tick)
    s = sub.add_parser("models", help="the models a cog can use (installed in Ollama, Claude with a key) and who serves each cog now")
    s.set_defaults(fn=cmd_models)
    s = sub.add_parser("agent", help="give a cog a task: it plans and acts on its own where the SOPs let it (each action: act, propose or refuse)")
    s.add_argument("--cog", required=True, metavar="COG", help="the cog to give the task to")
    s.add_argument("--goal", metavar="TEXT", help="what it should do")
    s.add_argument("--as", dest="as_user", required=True, metavar="PERSON", help="who hands over the task (is told the outcome)")
    s.add_argument("--actions", type=int, metavar="N", help="at most N actions (default 5)")
    s.add_argument("--confirm", metavar="RUN", help="confirm a run's proposed actions instead of starting one")
    s.set_defaults(fn=cmd_agent)
    s = sub.add_parser("agent-duties", help="let the cogs take up the duties the SOPs give them (every day, week or month)")
    s.add_argument("--cog", metavar="COG", help="only this cog")
    s.add_argument("--loop", type=int, metavar="SECONDS", help="keep checking every SECONDS")
    s.set_defaults(fn=cmd_agent_duties)
    s = sub.add_parser("cog-users", help="work the cogs' queues: mentions, messages, Gates and tasks (service mode: HUBPROTO_COG_QUEUE=service)")
    s.add_argument("--as", dest="cogs", action="append", metavar="COG", help="only this cog (repeatable); default: every cog")
    s.add_argument("--loop", type=int, metavar="SECONDS", help="keep working every SECONDS until stopped")
    s.set_defaults(fn=cmd_cog_users)
    from . import cli_config, cli_records, cli_recordtypes, cli_sops, cli_templates

    cli_templates.register(sub)  # templates import, templates list
    cli_sops.register(sub)  # sops convert, sops status
    cli_recordtypes.register(sub)  # record-types status, record-types carry
    cli_config.register(sub)  # config export, config propose (the configuration as JSON)

    cli_records.register(sub)  # inbox-pending, retry, resubmit, cancel, records, record, link, snapshot, documents
    from . import cli_dev

    cli_dev.register(sub)  # dev serve, dev list, dev stop, dev clean (development hubs on localhost)

    # --- messages ----------------------------------------------------------
    s = sub.add_parser("inbox", help="a person's messages and notifications")
    s.add_argument("--as", help="whose inbox", **AS)
    s.set_defaults(fn=cmd_inbox)
    s = sub.add_parser("msg", help="send a DM or email from a person to a person or a cog")
    s.add_argument("--as", "--from", dest="from_user", required=True, metavar="PERSON", help="the sender (a person)")
    s.add_argument("--to", required=True, metavar="MEMBER", help="the recipient: a person or a cog of the release in force")
    s.add_argument("--subject", required=True, metavar="TEXT", help="subject line")
    s.add_argument("--body", default="", metavar="TEXT", help="message body")
    s.add_argument("--channel", choices=["dm", "email"], default="dm", help="dm (default) or email (also written to state/inbox/outbox)")
    s.add_argument("--attach", action="append", metavar="PATH", help="a file to attach (JSON or text); repeatable")
    s.add_argument("--record", metavar="NUMBER", help="the item this message is about: it goes on the item's chatter and its @mentions act (a cog reviews, a person is asked, a committee fans out)")
    s.set_defaults(fn=cmd_msg)
    s = sub.add_parser("mail-in", help="deliver an .eml file as email to its To: member")
    s.add_argument("file", help="path of the .eml file")
    s.set_defaults(fn=cmd_mail_in)

    # --- finance and operations ---------------------------------------------
    sub.add_parser("ledger", help="journals posted by runs, with their lines").set_defaults(fn=cmd_ledger)
    sub.add_parser("bills", help="supplier bills with amount, state and run").set_defaults(fn=cmd_bills)
    s = sub.add_parser("shell", help="serve the workspace shell over HTTP")
    s.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    s.add_argument("--port", type=int, default=8080, help="port (default 8080)")
    s.set_defaults(fn=cmd_shell)
    s = sub.add_parser("bootstrap-release", help="first release on a fresh hub: compile, approve everything with two policy owners, release, then make the SOPs documents (change orders from then on)")
    s.add_argument("--as", help="a policy owner (releases it)", **AS)
    s.add_argument("--second", metavar="PERSON", help="a second policy owner (widenings need two)")
    s.add_argument("--force", action="store_true", help="compile and release again although a release is in effect")
    s.add_argument("--if-needed", action="store_true", help="exit 0 and do nothing when a release is already in effect "
                                                            "(and the SOPs are documents: otherwise it converts them)")
    s.add_argument("--keep-frame-files", metavar="REASON", help="do not convert the SOPs to documents after the release: the Frame files "
                                                               "stay the source and changes are released without change orders (recorded, "
                                                               "shown by status; for work on the Frames themselves)")
    s.set_defaults(fn=cmd_bootstrap_release)
    s = sub.add_parser("backup", help="copy the hub's state (kernel via the SQLite backup API, runs, releases, proposals, blobs, inbox) and its Frames into a directory; stop the hub first for a copy consistent across all parts")
    s.add_argument("dir", help="a new or empty directory to write the backup into")
    s.set_defaults(fn=cmd_backup)
    s = sub.add_parser("restore", help="restore a complete backup into --root/state, and the Frames it holds into --root/frames; refuses a partial set")
    s.add_argument("dir", help="the directory a backup was written into")
    s.add_argument("--force", action="store_true", help="replace an existing state (it is moved beside the new one); stop the shell first")
    s.set_defaults(fn=cmd_restore)
    return p


def main(argv=None) -> int:
    """Run one verb; returns the exit code (see the module docstring for the codes)."""
    from .profile import ProfileError

    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root)
    if args.cmd not in NO_HUB_NEEDED and not (root / "state" / "kernel.db").exists():
        fail(f"no hub at {root.resolve()} (no state/kernel.db); run `hubproto --root {root} init` or set HUBPROTO_ROOT to the hub directory", 2)
    try:
        rc = args.fn(args)
    except CliExit:
        raise
    except CompileError as e:
        fail(" ".join(str(e).split()), 1)
    except RunError as e:
        fail(str(e), 1)
    except KernelGuardError as e:
        if e.guard == "exists":
            fail(f"{e.detail}; `hubproto records` lists records by number", 2)
        fail(e.detail, 1)
    except FrameError as e:
        fail(f"{e}; fix the Frame and run again", 2)
    except ProfileError as e:
        fail(str(e), 2)
    except json.JSONDecodeError as e:
        fail(f"JSON does not parse at line {e.lineno} column {e.colno}: {e.msg}", 2)
    except PermissionError as e:
        fail(f"no permission for {e.filename or 'a file under the hub'}: {e.strerror}", 2)
    except FileNotFoundError as e:
        fail(f"missing file {e.filename or ''}: {e.strerror}".replace("  ", " "), 2)
    except IsADirectoryError as e:
        fail(f"{e.filename} is a directory, not a file", 2)
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
