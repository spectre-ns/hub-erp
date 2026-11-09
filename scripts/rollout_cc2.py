#!/usr/bin/env python3
"""Roll out change control v2 on a hub that ran the earlier code: one migration change order, end to end.

Run it on a hub whose state was made by the earlier code (restored from its backup, or the live state after the new
image is built), with the new code. It is idempotent: run it again and it carries on from where it stopped, doing
nothing twice. Every step is logged with its time; ``--dry-run`` reads everything, writes nothing to the hub and says
what each step would do.

    python scripts/rollout_cc2.py --root /hub --frames-from /hub/frames [--dry-run]

Steps (docs/rollout/cc2.md is the runbook):

1. health   release in force, run counts, every waiting run read by the new runner (its graph, its status, who may
            decide it and whether an approval resolves to a way forward), inbox cards and record pages drawn for each
            assignee, and a sample decided on a throwaway copy of the state.
2. co       the migration change order, prepared by alice (staff): a revision of every SOP, policy, cog-profile,
            persona and org-model document whose text differs from the repository's Frames, a new document for each
            Frame that has none, the retirement of each document whose Frame the repository dropped, and the
            record-type changes config/record-types.json needs. alice writes every text; bob opens each draft and
            retires the dropped ones (carol, who signs and decides, opens nothing).
3. review   a review round (compile with the extractor in HUBPROTO_EXTRACTOR, panel, reviews); every question and
            finding is logged verbatim; carol, a policy owner, answers the blocking unread-rule questions with
            "narrative, no rule" only where the paragraph was read as narrative (READ); the others wait for the
            product owner's decisions (--decisions).
4. validate a passing test record (the suite's result on the commit) for the high-risk items, and a test note for
            the medium-risk ones, through hubproto.validation.record_evidence.
5. decide   bob and carol decide every item (both, so widenings have their two owners), accepting each warning; the
            CO is sent for approval; carol (quality manager) signs it; the release automation runs.
6. verify   new release in force, revisions effective, record types activated, the waiting runs still waiting and
            decidable, a sample decided on a further copy, the next compile (new Frames in, retired out), the senior
            cogs unavailable with hosted models off and available with them on, signatures, chains, the delta record
            and status --check.

Exit codes: 0 done (or, with --dry-run, nothing stops it); 1 a check failed; 3 a step needs a code change (named in the
log with file and line); 4 a question needs the product owner's decision; 2 a mistake in the command.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

# The script works every task it queues itself, in this process: nothing waits for a shell's worker thread or a
# cog-users service (the container sets HUBPROTO_COG_QUEUE=worker for the shell; this is not the shell).
os.environ["HUBPROTO_COG_QUEUE"] = os.environ.get("ROLLOUT_COG_QUEUE", "inline")
os.environ.setdefault("HUBPROTO_EXTRACTOR", "convention")

TITLE = "Migration to change control v2"
MARK = "[rollout:cc2]"
PREPARER = "alice"          # staff, not a policy owner: bob and carol stay independent of everything the CO carries
OWNERS = ("bob", "carol")   # the policy owners decide every item (both, so a widening has its two)
QM = "carol"                # the quality manager signs the change order
NOTE = "migration to change control v2, carried by Claude at the product owner's instruction"
MEANING = ("Approved by Claude at the user's instruction: migration to change control v2 "
           "(rollout rehearsed on a copy of the live data)")
ANSWER = "Answered by Claude at the product owner's instruction: "
NARRATIVE = ANSWER + "narrative, no rule"
OPENER = "bob"     # opens each revision and new document for alice, and retires: never carol, who signs and decides
ANSWERER = "carol"  # a policy owner who contributed nothing answers the compile committee's blocking questions under v9
# Each unread-rule paragraph of the repository's texts, read one by one (key: Frame, first line, sha256 of the quoted
# words, first 16 hex). "narrative": it explains rules the sentence sections, the code or its guards already state, so
# "narrative, no rule" is true. "decide": it states an obligation the hub does not configure from it (a procedure for
# people, or a rule with no sentence); answering "no rule" would not be true, so the product owner decides (record it as a
# divergence, or have the text rewritten). A paragraph not in this table is never answered.
READ = {
    ("org-model", 14, "e847750e6236f814"): ("narrative", "cogs as members; what they may do comes from the role, group and name sentences"),
    ("org-model", 20, "cb8b16eadc055d3e"): ("narrative", "what groups and committees are; stated in ## Groups and ## Committee"),
    ("cog-profiles", 13, "d2f1066589e5d810"): ("narrative", "what a profile holds; a profile change is a widening by the compiler's guard"),
    ("cog-profiles", 21, "390da034acdab7cb"): ("narrative", "derived follows, autonomy, duties and criteria; a cog never signs its own work is a guard"),
    ("change-control-sop", 15, "39f53e3ecebcc5bc"): ("narrative", "what a CR, a CO and the delta are"),
    ("change-control-sop", 22, "16f537b856e5a572"): ("narrative", "the change-order loop, which the code runs; widenings by two owners is a guard"),
    ("change-control-sop", 80, "fb9e5ce03832fd58"): ("narrative", "says itself that it is not a rule the hub enforces"),
    ("document-control-sop", 20, "3dd418f055a722d2"): ("narrative", "restates the three 'when ... confirms' sentences and its criteria (lines 42-44, 55)"),
    ("it-sop", 15, "aa8da55d97b8e59d"): ("narrative", "what configuration is and what this SOP covers"),
    ("it-sop", 24, "88dc75451b78299f"): ("narrative", "closure is the code's; system administrators creating change orders is a sentence"),
    ("it-sop", 30, "bfd6c2ed50cf5a85"): ("narrative", "no approval sentence for the role; the it-admin deny and the SoD sentence state the rest"),
    ("it-sop", 37, "72a2cf53230b1a16"): ("decide", "the quarterly access review by the system administrators is a duty for people that no sentence configures (only the 90-day interval is a threshold)"),
    ("it-sop", 45, "b540cd7b837873c5"): ("narrative", "validation, stated in ## Validation and enforced by the code"),
    ("it-sop", 62, "dedfdecc068d207c"): ("decide", "hosted models: an installation switch recorded under the SOP and a supplier approval; procedures for people, not configured from this text"),
    ("it-sop", 76, "175b614cc9c8769c"): ("decide", "daily backups kept off the host, recorded restore tests, restores only by a system administrator on a task: procedures the hub does not configure"),
    ("it-sop", 82, "46520643a4ee0751"): ("decide", "incidents reported as tasks, the agents switch, an incident as a nonconformance: procedures the hub does not configure"),
    ("it-sop", 88, "dfe9e7fe288e5412"): ("decide", "the first sentence is in ## Segregation of duties, but 'an internal auditor does not audit system administration they perform' has no sentence"),
    ("ncr-sop", 15, "65b8e40eeed1554c"): ("narrative", "summarises the permissions, routing, criteria, SLA, duty and steps below"),
    ("training-sop", 15, "5d5f53d755d2420e"): ("narrative", "summarises the permissions, SLA, overdue routing and steps below"),
}
NEEDS_DECISION = 4
REVIEW = "the repository's change-control SOP (Review of changes): "
PANEL = (("it-admin", REVIEW + "a change that touches permissions, record types or forms is reviewed by the it-admin cog"),
         ("management", REVIEW + "a change to roles, limits or segregation of duties is reviewed by the management cog"),
         ("qa-manager", REVIEW + "a change to a quality process, a cog profile or a persona is reviewed by the qa-manager cog"),
         ("quality-assistant", REVIEW + "a change to a quality process is reviewed by the qa-manager cog and the quality-assistant cog"))
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
NEEDS_CODE = 3


class NeedsCode(Exception):
    """A step the code cannot do yet: the message names the file and line to change."""


class CheckFailed(Exception):
    pass


class NeedsDecision(Exception):
    """A question only the product owner can answer: the log lists each."""


# ---------------------------------------------------------------- logging
class Log:
    def __init__(self, path: Path | None):
        self.path = path
        self.fh = path.open("a", encoding="utf-8") if path else None
        self.timings: list[tuple[str, float, str]] = []
        self.report: dict = {"steps": {}, "problems": [], "needs_code": [], "notes": []}
        self.t0 = time.time()

    def line(self, text: str = "") -> None:
        stamp = time.strftime("%H:%M:%S")
        for ln in str(text).splitlines() or [""]:
            out = f"{stamp} {ln}"
            print(out, flush=True)
            if self.fh:
                self.fh.write(out + "\n")
                self.fh.flush()

    def problem(self, text: str) -> None:
        self.report["problems"].append(text)
        self.line("PROBLEM: " + text)

    def needs_code(self, text: str) -> None:
        self.report["needs_code"].append(text)
        self.line("NEEDS A CODE CHANGE: " + text)

    @contextmanager
    def step(self, name: str):
        self.line("")
        self.line(f"== {name}")
        t = time.time()
        status = "done"
        try:
            yield
        except NeedsCode as e:
            status = "stopped (needs a code change)"
            self.needs_code(str(e))
            raise
        except NeedsDecision as e:
            status = "stopped (needs the product owner's decision)"
            self.report.setdefault("needs_decision", []).append(str(e))
            self.line("NEEDS A DECISION: " + str(e))
            raise
        except CheckFailed as e:
            status = "failed"
            self.problem(str(e))
            raise
        except Exception as e:
            status = "error"
            self.problem(f"{name}: {type(e).__name__}: {e}")
            self.line(traceback.format_exc())
            raise
        finally:
            dt = time.time() - t
            self.timings.append((name, dt, status))
            self.report["steps"][name] = {"seconds": round(dt, 2), "status": status}
            self.line(f"-- {name}: {status} in {dt:.1f} s")

    def close(self) -> None:
        self.line("")
        self.line("== timings")
        for name, dt, status in self.timings:
            self.line(f"  {name:34s} {dt:8.1f} s  {status}")
        self.line(f"  {'total':34s} {time.time() - self.t0:8.1f} s")
        if self.fh:
            self.fh.close()


# ---------------------------------------------------------------- helpers
def hub_at(root: Path):
    from hubproto.app import Hub

    return Hub(root)


def version_of(hub, digest):
    from hubproto.cli import version_of as v

    return v(hub, digest)


def code_commit(args) -> str:
    if args.commit:
        return args.commit
    here = Path(__file__).resolve().parent.parent
    try:
        return subprocess.run(["git", "-C", str(here), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def find_co(hub) -> dict | None:
    """The migration change order this script made (by its mark), unless it was cancelled or rejected."""
    cos = [c for c in hub.kernel.query(type_="change-order")
           if MARK in (c["fields"].get("description") or "") and c["state"] not in ("cancelled", "rejected", "withdrawn")]
    return sorted(cos, key=lambda c: c.get("created_at") or "")[-1] if cos else None


def new_frames(args):
    from hubproto.frames import FrameStore

    return FrameStore(Path(args.frames_from))


def sop_docs(hub) -> dict[str, dict]:
    from hubproto import sopdocs

    return {d["fields"]["sop_name"]: d for d in hub.kernel.query(type_="document")
            if str(d["fields"].get("lineage", "")).startswith(sopdocs.LINEAGE) and d["state"] == "effective"}


def same_but_version(a: str, b: str) -> bool:
    """Equal texts, or equal but for the front matter's version line (the compile raises a version a changed Frame
    still carries: frames.bump_version)."""
    import re

    pat = re.compile(r'^version:.*$', re.M)
    return a == b or pat.sub("", a, count=1) == pat.sub("", b, count=1)


def header_of(frame) -> tuple[str, int]:
    """The Frame's header lines (front matter) and trailing newlines, as sopdocs.convert keeps them on a document."""
    head = "\n".join((frame.text or "").splitlines()[: max(0, frame.body_start - 1)])
    return head, max(1, len(frame.text) - len(frame.text.rstrip("\n")))


def plan(hub, args) -> dict:
    """What the migration change order must carry: {revise: [name], create: [name], retire: [name], same: [name],
    record_types: ([change], [skipped])}."""
    from hubproto import recordtypes, sopdocs

    store = new_frames(args)
    docs = sop_docs(hub)
    out = {"revise": [], "create": [], "retire": [], "same": []}
    for name, f in sorted(store.frames.items()):
        d = docs.get(name)
        if d is None:
            out["create"].append(name)
        elif not same_but_version(sopdocs.frame_text(hub, d), f.text):
            out["revise"].append(name)
        else:
            out["same"].append(name)
    out["retire"] = sorted(n for n in docs if n not in store.frames)
    try:
        out["record_types"] = recordtypes.from_file_changes(hub)
    except Exception as e:
        out["record_types"] = ([], [f"cannot read: {e}"])
    return out


# ---------------------------------------------------------------- step 1: waiting runs
def would_follow(hub, state: dict) -> tuple[dict, str]:
    """A copy of the run as it would stand at its next move (change control 5.1: it follows the release in force, unless
    that release cannot carry it), and the words for what happens. Nothing is saved."""
    from hubproto import process_graph as pg

    st = dict(state)  # shallow: _graph only sets top-level keys (a record may nest too deep to deepcopy)
    r = hub.runner
    cur = hub.releases.current()
    r._graph(st)
    if not cur or cur["digest"] == st["release"]:
        return st, f"on the release in force ({version_of(hub, st['release'])})"
    bundle = hub.releases.bundle(cur["digest"])
    wf = (bundle.get("workflows") or {}).get(st["process"])
    manifest = (bundle.get("ops") or {}).get(st["process"])
    if not wf or not manifest:
        return st, f"keeps {version_of(hub, st['release'])}: the release in force has no process {st['process']}"
    if not pg.is_graph_shape(wf):
        return st, f"keeps {version_of(hub, st['release'])}: the release in force predates workflow graphs"
    if not pg.status_of(wf, st.get("at")):
        return st, f"keeps {version_of(hub, st['release'])}: the release in force has no status {st.get('at')!r} (a state map moves it)"
    if st.get("moving"):
        return st, f"keeps {version_of(hub, st['release'])}: part-way through a move"
    st["release"], st["workflow"], st["manifest"] = cur["digest"], wf, manifest
    return st, f"follows {version_of(hub, cur['digest'])} from its next move"


def read_run(hub, run: dict) -> dict:
    """Everything the new code needs to show and decide a waiting run, and every reason it cannot."""
    from hubproto import authz
    from hubproto import process_graph as pg

    out = {"id": run["id"], "process": run.get("process"), "problems": [], "notes": []}
    try:
        state = hub.runner.load(run["id"])
    except Exception as e:
        out["problems"].append(f"the run cannot be read: {type(e).__name__}: {e}")
        return out
    pending = state.get("pending") or {}
    out.update({"record": state.get("record"), "release": state.get("release"), "assignees": list(pending.get("assignees") or []),
                "kind": pending.get("kind", "decision"), "title": pending.get("title"),
                "shape": "graph" if pg.is_graph_shape(state.get("workflow") or {}) else "earlier shape (numbered steps)"})
    if not pending:
        out["problems"].append("waiting, with nothing pending")
        return out
    if out["kind"] == "decision" and not state.get("seen_digest"):
        out["notes"].append("runner.load gives no seen_digest (its pending was saved without 'kind' by the earlier code, "
                            "hubproto/runner.py:108): `hubproto decide` without --seen quotes none and is refused (cli.py:855); "
                            "the shell computes it (shell/inbox.py:306)")
    try:
        hub.releases.get(state["release"])
    except Exception:
        out["problems"].append(f"its release {state['release'][:12]} is not in the release store")
        return out
    rec = hub.kernel.get(state["record"]) if state.get("record") else None
    if state.get("record") and rec is None:
        out["problems"].append(f"its record {state['record']} does not exist")
    if rec is not None:
        out["number"], out["state"] = rec["number"], rec["state"]
        if rec["type"] not in hub.kernel.types:
            out["problems"].append(f"its record type {rec['type']} is not a record type in force")
        if rec.get("hold"):
            out["notes"].append(f"on hold: {rec.get('hold_reason')}")
    try:
        st, follow = would_follow(hub, state)
    except Exception as e:
        out["problems"].append(f"the new runner cannot derive its graph: {type(e).__name__}: {e}")
        return out
    out["follow"], out["at"] = follow, st.get("at")
    wf = st.get("workflow") or {}
    if not pg.status_of(wf, st.get("at")):
        out["problems"].append(f"its status {st.get('at')!r} is not in its graph")
    if out["kind"] != "decision" or pending.get("committee"):
        out["notes"].append(f"pending {out['kind']}" + (" (committee)" if pending.get("committee") else ""))
        return out
    bundle = hub.releases.bundle(st["release"])
    obj = (st.get("manifest") or {}).get("object")
    folder = rec["folder"] if rec else (st.get("manifest") or {}).get("folder")
    deciders = []
    for a in out["assignees"]:
        m = (bundle.get("members") or {}).get(a)
        if m is None:
            out["problems"].append(f"assignee {a} is not a member of the release it would follow")
            continue
        if m.get("type") == "cog":
            out["notes"].append(f"waits for the {a} cog")
            continue
        allowed = authz.check(bundle, a, "approve", obj, folder)
        if not allowed["allowed"]:
            out["problems"].append(f"{a} may not approve it under the release it would follow: {allowed['why']}")
            continue
        excluded = hub.runner.excluded(bundle, st, pending.get("route") or "role:")
        if a in excluded:
            why = excluded[a] + (f" (its pending, saved by the earlier code, names no route; it was routed by “"
                                 f"{(pending.get('route_rule') or {}).get('text')}”)" if not pending.get("route") else "")
            out["excluded"] = out.get("excluded", []) + [f"{a}: {why}"]
            continue
        try:
            t = hub.runner._chosen(dict(st), pending, a, "approve", None)
        except Exception as e:
            out["problems"].append(f"an approval by {a} resolves to no way forward: {e}")
            continue
        try:
            missing = hub.runner._missing_for_person(st, t["to"])
        except Exception:
            missing = []
        if missing:
            out["notes"].append(f"{a} approving must supply {', '.join(missing)} (required in {t['to']})")
        deciders.append(a)
        out.setdefault("approve_to", t["to"])
    out["deciders"] = deciders
    if not deciders and not any(n.startswith("waits for the") for n in out["notes"]):
        out["problems"].append("nobody can approve it" + (": " + "; ".join(out.get("excluded") or []) if out.get("excluded") else ""))
    elif out.get("excluded"):
        out["notes"].append("an assignee is kept out: " + "; ".join(x.split(":", 1)[1].strip() for x in out["excluded"]))
    return out


def placeholder(spec: str, who: str):
    """A valid value of a field's type, for a decision on a throwaway copy that must supply it."""
    import datetime

    spec = str(spec or "text")
    if spec.startswith("enum:"):
        return spec[5:].split(",")[0]
    return {"date": datetime.date.today().isoformat(), "int": 1, "number": 1, "money": 1, "bool": True,
            "list": ["rollout rehearsal"], "principal": who}.get(spec, "rollout rehearsal")


def waiting_runs(hub) -> list[dict]:
    return [r for r in hub.runner.list() if r["status"] == "waiting"]


def draw_cards(hub, reads: list[dict], log: Log, sample_ids: set[str]) -> list[str]:
    """Each person assignee's inbox shows each of their waiting items; a sample's record page carries the digest a
    decision quotes. Served on 127.0.0.1, a free port, in this process."""
    from urllib.error import HTTPError
    from urllib.parse import quote
    from urllib.request import urlopen

    from hubproto.shell import serve_in_thread

    problems = []
    server = serve_in_thread(hub, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def get(path):
        try:
            with urlopen(base + path, timeout=60) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    try:
        by_person = collections.defaultdict(list)
        for r in reads:
            for a in r.get("deciders") or []:
                by_person[a].append(r)
        drawn = 0
        for person, rs in sorted(by_person.items()):
            from hubproto.shell.inbox import PAGE_SIZE

            html, code = "", 200
            for page in range(1, (len(rs) + PAGE_SIZE - 1) // PAGE_SIZE + 2):  # every page of the inbox
                code, part = get(f"/inbox?as={quote(person)}&page={page}")
                if code != 200:
                    break
                html += part
            if code != 200:
                problems.append(f"the inbox of {person} answers {code}")
                continue
            for r in rs:
                if r.get("number") and r["number"] not in html:
                    problems.append(f"{r['number']} ({r['id']}) is not on {person}'s inbox")
                else:
                    drawn += 1
        pages = 0
        for r in reads:
            if r["id"] not in sample_ids or not r.get("number") or not r.get("deciders"):
                continue
            person = r["deciders"][0]
            code, html = get(f"/record/{quote(r['number'])}?as={quote(person)}")
            seen = hub.runner.seen_digest(hub.runner.load(r["id"]))
            if code != 200:
                problems.append(f"the record page of {r['number']} answers {code} for {person}")
            elif seen and seen not in html:
                problems.append(f"the record page of {r['number']} for {person} carries no decision form with the current digest")
            else:
                pages += 1
        log.line(f"inbox cards: {drawn} card(s) found on {len(by_person)} inbox(es); record pages with a decision form: {pages}")
    finally:
        server.shutdown()
        server.server_close()
    return problems


def pick_sample(reads: list[dict], n: int) -> list[dict]:
    """One run per (process, status) with a person who may approve it, then more up to n."""
    seen, out = set(), []
    for r in reads:
        key = (r.get("process"), r.get("at"))
        if r.get("deciders") and not r["problems"] and key not in seen:
            seen.add(key)
            out.append(r)
    for r in reads:
        if len(out) >= n:
            break
        if r.get("deciders") and not r["problems"] and r not in out:
            out.append(r)
    return out


def sample_on_copy(args, log: Log, root: Path, ids: list[str], label: str) -> list[dict]:
    """Copy the hub (state and frames) to a throwaway directory and decide each sampled run there, in a child process
    whose cogs only queue (nothing calls a model). Returns one result per run."""
    if not ids:
        return []
    base = Path(args.scratch) if args.scratch else None
    if base:
        base.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"cc2-{label}-", dir=base))
    shutil.copytree(root / "state", tmp / "state", symlinks=True)
    if (root / "frames").exists():
        shutil.copytree(root / "frames", tmp / "frames", symlinks=True)
    env = {**os.environ, "HUBPROTO_COG_QUEUE": "service", "ROLLOUT_COG_QUEUE": "service"}
    out_file = tmp / "sample.json"
    cmd = [sys.executable, str(Path(__file__).resolve()), "--root", str(tmp), "--frames-from", str(args.frames_from),
           "--_sample-decide", ",".join(ids), "--_sample-out", str(out_file)]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=3600)
    try:
        results = json.loads(out_file.read_text())
    except Exception:
        results = [{"id": i, "ok": False, "why": f"the sample run stopped: {p.stderr[-800:]}"} for i in ids]
    log.line(f"decided {len(ids)} sampled run(s) on a throwaway copy ({tmp}):")
    for r in results:
        log.line(f"  {r.get('outcome', 'error'):8s} {r['id']} {r.get('process')} {r.get('number')}: {r.get('why')}")
    if not args.keep_copies:
        shutil.rmtree(tmp, ignore_errors=True)
    return results


def sample_decide_main(args) -> int:
    """Child process: decide each run (an approval by its first person decider) on the throwaway copy."""
    hub = hub_at(Path(args.root))
    results = []
    for rid in args._sample_decide.split(","):
        r = read_run(hub, {"id": rid})
        try:
            r["process"] = hub.runner.load(rid).get("process")
        except Exception:
            pass
        res = {"id": rid, "process": r.get("process"), "number": r.get("number"), "ok": False, "outcome": "error"}
        if not r.get("deciders"):
            res["why"] = "nobody to decide it: " + "; ".join(r["problems"] or r["notes"])
            results.append(res)
            continue
        who = r["deciders"][0]
        try:
            st = hub.runner.load(rid)
            fields = {}
            nxt = r.get("approve_to")
            missing = hub.runner._missing_for_person(st, nxt) if nxt else []
            if missing:
                res["why"] = f"needs {', '.join(missing)} from {who}; decided with placeholder values on the copy"
                rec = hub.kernel.get(st["record"])
                specs = hub.kernel.types.get(rec["type"], {}).get("fields", {})
                fields = {f: placeholder(specs.get(f), who) for f in missing}
            hub.runner.decide(rid, who, "approve", meaning="rollout rehearsal: approved on a throwaway copy of the hub",
                              payload_digest=hub.runner.seen_digest(st), fields=fields or None)
            after = hub.runner.load(rid)
            tr = hub.runner.track(rid)
            moved = [e for e in tr if e.get("type") == "run.release_changed"]
            # A failed action (a cog step refusing the item's data) still shows the decision path works; the before/after
            # comparison catches an outcome the rollout changed.
            res["ok"] = True
            res["outcome"] = "moved" if after["status"] != "failed" else "action-failed"
            res["why"] = (res.get("why", "") + "; " if res.get("why") else "") + (
                f"{who} approved; now {after['status']} at {after.get('at')}"
                + (f"; followed {version_of(hub, moved[-1]['to'])}" if moved else "; kept its release")
                + (f" (failed: {next((e.get('reason') for e in reversed(tr) if e.get('reason')), '')})" if after["status"] == "failed" else ""))
        except Exception as e:
            from hubproto.runner import RunError

            # A refusal by the item's own rules (a validator, segregation of duties, a field's type) shows the decision
            # path works; any other exception is the code failing on this item.
            res["outcome"] = "refused" if isinstance(e, RunError) else "error"
            res["ok"] = res["outcome"] == "refused"
            res["why"] = f"{who} could not approve it: {type(e).__name__}: {e}"
        results.append(res)
    Path(args._sample_out).write_text(json.dumps(results, indent=1, default=str))
    return 0


def health(hub, args, log: Log, root: Path, phase: str, sample_ids: list[str] | None = None) -> dict:
    cur = hub.releases.current()
    log.line(f"release in force: {version_of(hub, (cur or {}).get('digest'))}" + (f", released by {cur.get('released_by')}" if cur else ""))
    runs = hub.runner.list()
    counts = collections.Counter(r["status"] for r in runs)
    log.line(f"runs: {len(runs)} (" + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + ")")
    waiting = [r for r in runs if r["status"] == "waiting"]
    reads = [read_run(hub, r) for r in waiting]
    groups = collections.Counter((r.get("process"), r.get("at"), r.get("follow", "?")) for r in reads)
    log.line(f"waiting runs by process and status, read by the new runner ({len(reads)}):")
    for (proc, at, follow), n in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        log.line(f"  {n:4d}  {proc} at {at}: {follow}")
    shapes = collections.Counter(r.get("shape") for r in reads)
    log.line("workflow shapes saved on the waiting runs: " + ", ".join(f"{k} {v}" for k, v in shapes.items()))
    notes = collections.Counter(n for r in reads for n in r["notes"])
    for n, k in sorted(notes.items()):
        log.line(f"  note x{k}: {n}")
    bad = [r for r in reads if r["problems"]]
    for r in bad:
        log.problem(f"{phase}: run {r['id']} {r.get('process')} {r.get('number')}: " + "; ".join(r["problems"]))
    log.line(f"{len(reads) - len(bad)} of {len(reads)} waiting runs readable and decidable by someone; {len(bad)} with problems")
    by_id = {r["id"]: r for r in reads}
    sample = [by_id[i] for i in sample_ids if i in by_id] if sample_ids else pick_sample(reads, args.sample)
    ids = {r["id"] for r in sample}
    for p in draw_cards(hub, reads, log, ids):
        log.problem(f"{phase}: {p}")
    results = sample_on_copy(args, log, root, [r["id"] for r in sample], phase)
    for r in results:
        if not r["ok"]:
            log.problem(f"{phase}: sample {r['id']} {r.get('number')}: {r.get('why')}")
    out = {"release": (cur or {}).get("digest"), "counts": dict(counts), "waiting": sorted(r["id"] for r in waiting),
           "problems": len(bad), "sample": results}
    log.report["steps"].setdefault(f"health {phase}", {}).update({"release": out["release"], "counts": out["counts"]})
    return out


# ---------------------------------------------------------------- step 2: the change order
def capability(hub, p: dict) -> list[str]:
    """What the code must offer for this plan, and does not (each with where to change it)."""
    from hubproto import eco

    gaps = []
    if p["create"] and not hasattr(eco, "add_new_sop"):
        gaps.append("a change order cannot carry a new SOP or policy document (" + ", ".join(p["create"]) + "): "
                    "hubproto/eco.py:91-92 add_sop refuses a Frame with no document in force ('there is no SOP or policy ... in "
                    "force'), and hubproto/frames.py:194-195 FrameStore.with_overrides refuses a Frame the store lacks ('there is "
                    "no Frame ... to change'), which Hub.compile calls (hubproto/app.py:359 frames.get(name).version, app.py:361); the release writes each released Frame to self.frames.get(name).path (app.py:737), which a new Frame lacks. Needed: eco.add_new_sop(hub, co_ref, "
                    "name, text, *, by, author=None, relpath=None) -> draft (a controlled document with lineage sop:<name>, revision A, its "
                    "folder and doc type from sopdocs.kind_of, frame_header/frame_meta/frame_tail from the text, linked 'carries'), "
                    "Hub.compile adding such drafts to the Frame store (with_overrides accepting new names, relpath from the "
                    "Frame's scope), and the release making it effective (eco.publish_revisions, no predecessor).")
    if p["retire"] and not hasattr(eco, "retire_sop"):
        gaps.append("a change order cannot retire a document whose Frame the repository dropped (" + ", ".join(p["retire"]) + "); the "
                    "new grammar refuses those role and cog personas as errors (hubproto/extract.py:666-671, '## Persona: <name> "
                    "(role|cog)'), so on this hub every compile fails until they are retired. Needed: eco.retire_sop(hub, co_ref, "
                    "name, *, by, reason) (the requester or a document controller; a 'retires' link on the change order), "
                    "Hub.compile leaving a retired Frame out (hubproto/app.py:326-328), the delta listing its rules as removed, and "
                    "the release making the document obsolete and dropping the Frame from the release's texts (eco.publish_revisions).")
    return gaps


def carry_text(hub, draft: dict, frame, log: Log) -> bool:
    """Make the draft read as the Frame's text (front matter and body); False when it already does."""
    from hubproto import sopdocs

    if sopdocs.frame_text(hub, draft) == frame.text:
        return False
    head, tail = header_of(frame)
    hub.kernel.set_fields(draft["locator"], {"frame_header": head, "frame_meta": dict(frame.frontmatter), "frame_tail": tail,
                                             "description": frame.description}, by=PREPARER)
    hub.dms.add_version(draft["locator"], sopdocs.to_docx(frame.body), by=PREPARER, filename=f"{frame.name}.docx", mime=DOCX,
                        note=f"{frame.name} as in the repository's frames/{frame.relpath}: {NOTE}")
    d = hub.kernel.get(draft["locator"])
    if sopdocs.frame_text(hub, d) != frame.text:
        raise CheckFailed(f"{d['number']} ({frame.name}) does not read back as the repository's text")
    return True


WHERE = {"add_sop": "hubproto/eco.py:171-172", "add_new_sop": "hubproto/eco.py:76-77", "retire_sop": "hubproto/eco.py:114-115"}


def refused_needs_code(fn, what: str, who: str):
    """Run an eco call as ``who``; a refusal of who may ask is a code question, not a data one."""
    try:
        return fn()
    except ValueError as e:
        if "document controller" in str(e):
            raise NeedsCode(f"eco.{what} refuses {who}: “{e}” ({WHERE[what]}: only the change order's requester or a document "
                            f"controller may; under v9 {who} is neither, and carol, the only document controller, must not open "
                            "or retire anything because she signs the change order and is one of its two owners)") from None
        raise


def change_order(hub, args, log: Log) -> dict | None:
    from hubproto import authz, eco, recordtypes, sopdocs

    p = plan(hub, args)
    rt_changes, rt_skipped = p["record_types"]
    log.line(f"documents whose text differs from {args.frames_from}: {len(p['revise'])}: {', '.join(p['revise'])}")
    log.line(f"Frames with no document yet: {len(p['create'])}: {', '.join(p['create']) or 'none'}")
    log.line(f"documents whose Frame the repository dropped: {len(p['retire'])}: {', '.join(p['retire']) or 'none'}")
    log.line(f"documents already as the repository has them: {len(p['same'])}")
    log.line(f"record-type changes config/record-types.json needs: {len(rt_changes)}")
    for c in rt_changes:
        log.line(f"  {c.get('op')} {c.get('type')}.{c.get('field') or c.get('state') or ''} {c.get('spec') or ''}")
    for s in rt_skipped:
        log.problem(f"config/record-types.json says what no change order can carry: {s} (hubproto/recordtypes.py:695-698 "
                    "skips any key but fields, required_by_state, retention, sensitivity, defaults and archived; after "
                    "activation the drift check adopts the file's hash (recordtypes.py:760-763), so status --check is clean "
                    "while the record types in force still lack these)")
    log.line("form layouts: none in force and none shipped (state/forms has no layouts): nothing to carry")
    log.report["plan"] = {k: v for k, v in p.items() if k != "record_types"}
    log.report["plan"]["record_type_changes"] = len(rt_changes)
    log.report["plan"]["record_types_not_carried"] = rt_skipped
    gaps = capability(hub, p)
    if gaps:
        for g in gaps:
            log.needs_code(g)
        raise NeedsCode(f"{len(gaps)} code change(s) needed before the migration change order can be made; nothing was written")
    bundle = hub.current_bundle()
    if not authz.check(bundle, PREPARER, "create", "change-order", "Quality/Changes")["allowed"]:
        raise CheckFailed(f"{PREPARER} may not prepare change orders under the release in force")
    starter = OPENER
    if starter != PREPARER and "document-controller" not in authz.roles_of(bundle, starter) and not getattr(eco, "OPENERS_MAY_BE_OWNERS", False):
        # eco.add_sop, add_new_sop and retire_sop take the requester or a document controller (eco.py:171-172, 76-77,
        # 114-115). Checked before anything is written, so a refusal leaves no half-made change order. (A code change
        # that lets a policy owner open drafts for the requester should set eco.OPENERS_MAY_BE_OWNERS = True.)
        refused_needs_code(lambda: (_ for _ in ()).throw(ValueError(
            f"{starter} is neither {PREPARER} (the requester) nor a document controller: they or a document controller add SOPs to it")),
            "add_sop", starter)
    if args.dry_run:
        log.line(f"dry run: would prepare '{TITLE}' as {PREPARER}, carrying {len(p['revise'])} revision(s), {len(p['create'])} new "
                 f"document(s), {len(p['retire'])} retirement(s) and {len(rt_changes)} record-type change(s); each revision "
                 f"opened by {starter} with {PREPARER} as its author, the retirements by {OPENER}")
        return None
    co = find_co(hub)
    if co is None:
        co = hub.kernel.create("change-order", "Quality/Changes", {
            "title": TITLE, "requested_by": PREPARER, "implements": [],
            "description": f"Brings the SOPs, policies, cog profiles, personas, organization model and record types in line "
                           f"with change control v2 (the repository's frames/** and config/record-types.json). {NOTE}. {MARK}"},
            by=PREPARER)
        log.line(f"prepared {co['number']} as {PREPARER}")
    else:
        log.line(f"{co['number']} exists ({co['state']}, stage {co['fields'].get('stage') or 'preparing'}): carrying on")
    from hubproto import changecontrol as cc

    if cc.stage_of(co) not in cc.EDITABLE:
        log.line(f"{co['number']} is {cc.stage_of(co)}: what it carries is fixed")
        return co
    store = new_frames(args)
    carried = {c["name"]: c["draft"] for c in eco.carried(hub, co)}
    n_new = n_same = 0
    for name in p["revise"] + [n for n in carried if n not in p["revise"] and n not in p["create"]]:
        if name not in store.frames:
            continue
        draft = carried.get(name)
        if draft is None:
            # alice owns no SOP, so bob opens each revision for her; alice is its author and writes its only version.
            # bob counts as a contributor (started_by); carol, who signs and decides, opens nothing.
            draft = refused_needs_code(lambda: eco.add_sop(hub, co["number"], name, by=starter, author=PREPARER), "add_sop", starter)
        bom = {f["name"]: f for f in (hub.releases.current() or {}).get("frames") or []}
        if name not in bom and hasattr(sopdocs, "_relpath"):
            # A document no release compiles (the reference guide) has its Frame's path only on the document: the
            # revision must keep it, or the release writes a second file for the same Frame.
            rel = sopdocs._relpath(hub, name, hub.kernel.get(draft["locator"]), bom)
            if rel != store.get(name).relpath:
                raise NeedsCode(f"the revision {draft['number']} of {name} would be written to frames/{rel}, beside "
                                f"frames/{store.get(name).relpath}: eco.add_sop keeps no Frame path (hubproto/eco.py:189 copies "
                                "neither converted_from nor frame_relpath) and sopdocs._relpath falls back to documents/<name> "
                                "(hubproto/sopdocs.py:305-311). Rehearsed: the release then fails at 'Publish the revisions' "
                                "(duplicate Frame name) with the new release already in force and the change order approved but "
                                "not released. The change order is prepared but nothing is released; run again after the fix.")
        if carry_text(hub, hub.kernel.get(draft["locator"]), store.get(name), log):
            n_new += 1
            log.line(f"  {name}: {draft['number']} rev {draft['fields'].get('revision')} carries the repository's text")
        else:
            n_same += 1
    for name in p["create"]:
        draft = carried.get(name) or refused_needs_code(lambda: eco.add_new_sop(
            hub, co["number"], name, store.get(name).text, by=starter, author=PREPARER, relpath=store.get(name).relpath), "add_new_sop", starter)
        carry_text(hub, hub.kernel.get(draft["locator"]), store.get(name), log)
        log.line(f"  {name}: new document {draft['number']}")
    for name in p["retire"]:
        if name in eco.retirements(hub.kernel.get(co["locator"])):
            continue
        refused_needs_code(lambda: eco.retire_sop(hub, co["number"], name, by=OPENER,
                                                  reason=f"the repository dropped its Frame (change control v2): {NOTE}"), "retire_sop", OPENER)
        log.line(f"  {name}: retired with {co['number']}")
    log.line(f"revisions written: {n_new}; already carrying the repository's text: {n_same}")
    co = hub.kernel.get(co["locator"])
    if rt_changes:
        have = {(c.get("op"), c.get("type"), c.get("field") or c.get("state")) for c in recordtypes.changes_of(co)}
        if all((c.get("op"), c.get("type"), c.get("field") or c.get("state")) in have for c in rt_changes):
            log.line("record-type changes: carried already")
        else:
            got, skipped = recordtypes.carry_file_changes(hub, co["number"], by=PREPARER, reason=f"config/record-types.json of change control v2: {NOTE}")
            log.line(f"record-type changes carried: {len(got)}")
    return hub.kernel.get(co["locator"])


# ---------------------------------------------------------------- step 3: the review round
def dump_round(hub, co, log: Log) -> None:
    from hubproto import changecontrol as cc

    r = cc.current_round(hub.kernel.get(co["locator"])) or {}
    log.line(f"round {r.get('n')}: {r.get('status')}" + (f" ({r.get('error')})" if r.get("error") else ""))
    for j in hub.automation.of(co["locator"]):
        if j.get("id") == r.get("job"):
            for s in j["steps"]:
                log.line(f"  step {s['name']}: {s.get('status')}" + (f": {s.get('outcome')}" if s.get("outcome") else ""))
    for m in r.get("panel") or []:
        res = (r.get("results") or {}).get(m["who"]) or {}
        log.line(f"  panel: {m['who']} ({m.get('kind')}) - {m.get('reason')} -> {res.get('status')}"
                 + (f" [{res.get('error')}]" if res.get("error") else "") + (f" by {res['model']}" if res.get("model") else ""))
        if res.get("summary"):
            log.line(f"    summary: {res['summary']}")
        if "stand-in" in str(res.get("summary") or ""):
            log.line(f"    NOTE: {m['who']}'s review was written by the development stand-in (HUBPROTO_ALLOW_STAND_IN), not by "
                     f"{res.get('model') or 'its model'}: no model reviewed this change order here, although the result names the model")
    log.report.setdefault("review", {})["round"] = {"n": r.get("n"), "status": r.get("status"), "panel": r.get("panel")}
    qs = cc.questions(hub, co["number"])
    fs = cc.findings(hub, co["number"])
    ss = cc.suggestions(hub, co["number"])
    log.line(f"questions: {len(qs)}")
    for q in qs:
        log.line(f"  [{q.get('code') or q.get('raised_by') or '?'}] {q.get('status', 'open')} {q['id']}: {q.get('text')}"
                 + (f" ({q.get('frame')} line {q.get('line')})" if q.get("frame") else "") + (f" -> {q.get('answer')}" if q.get("answer") else ""))
    log.line(f"findings: {len(fs)}")
    for f in fs:
        log.line(f"  [{f.get('severity')}] {f.get('by') or f.get('who') or ''} {f.get('id')}: {f.get('text')}")
    log.line(f"suggestions: {len(ss)}")
    for s in ss:
        log.line(f"  {s.get('status')} {s.get('id')}: {s.get('reason') or s.get('text') or s}")
    log.report["review"].update({"questions": qs, "findings": fs, "suggestions": ss})


def review(hub, args, log: Log, co: dict) -> dict:
    from hubproto import changecontrol as cc

    co = cc.sync(hub, co["locator"])
    st = cc.stage_of(co)
    cmt = hub.compile_committee() or {}
    log.line(f"extractor: {os.environ.get('HUBPROTO_EXTRACTOR')}; compile committee: "
             + ", ".join(f"{n} ({m or 'unavailable'})" for n, m in cmt.get("members") or []) + f"; chair {cmt.get('chair') or 'none'}")
    for n, why in (cmt.get("unavailable") or {}).items():
        log.line(f"  {n} cannot read here: {why}")
    if st in cc.EDITABLE:
        reasons = cc.send_reasons(hub, co["number"])
        fresh = cc.current_round(co) and cc.current_round(co).get("status") not in ("failed", "compiling") and not any(
            "request review" in r or "compiled again" in r for r in reasons)
        if not fresh:
            r = cc.request_review(hub, co["number"], PREPARER)
            log.line(f"{PREPARER} requested review: round {r['n']}")
        co = hub.kernel.get(co["locator"])
    r = cc.current_round(co) or {}
    if r.get("status") == "failed":
        dump_round(hub, co, log)
        raise CheckFailed(f"round {r.get('n')} stopped: {r.get('error')}")
    if not r.get("panel") and cc.stage_of(co) in cc.EDITABLE:
        # The change-control SOP in force has no "Review of changes" section (it comes with this change order), so the
        # quality-assistant suggests nobody. The requester adds the reviewers the repository's section names for this
        # delta, where they are members already; the it-admin and management cogs arrive with this change order.
        log.line("the suggested panel is empty (the change-control SOP in force has no Review of changes section)")
        members = hub.current_bundle()["members"]
        for who, reason in PANEL:
            if who not in members:
                log.line(f"  {who}: not a member under the release in force (it arrives with this change order): not added")
                continue
            try:
                cc.add_reviewer(hub, co["number"], who, PREPARER, reason=reason)
                log.line(f"  {PREPARER} added {who}: {reason}")
            except ValueError as e:
                log.line(f"  {who} not added: {e}")
        co = hub.kernel.get(co["locator"])
    dump_round(hub, co, log)
    # The compile committee's blocking questions are answered by its chair (or a system administrator), never by
    # whoever made the change.
    from hubproto import authz

    bundle = hub.current_bundle()
    admins = [m for m in bundle["members"] if "system-administrator" in authz.roles_of(bundle, m)]
    import hashlib

    undecided = []
    for q in cc.questions(hub, co["number"]):
        if q.get("code") not in cc.COMMITTEE_CODES or q.get("status", "open") != "open":
            continue
        t = q.get("text") or ""
        quote = t[t.find("“") + 1:t.find("”")] if "“" in t else ""
        first = (q.get("lines") or [q.get("line")])[0]
        verdict, why = READ.get((q.get("frame"), first, hashlib.sha256(quote.encode()).hexdigest()[:16]), ("unread", "not read for this rollout"))
        po = (args.decisions or {}).get(f"{q.get('frame')}:{first}")
        if po and q.get("code") == "unread-rule" and po.get("answer") in ("narrative", "divergence"):
            # The product owner's decision on this paragraph (--decisions): its words, as the answer
            text = ANSWER + po["meaning"]
            cc.answer_question(hub, co["number"], q["id"], text, ANSWERER, record_divergence=po["answer"] == "divergence")
            log.line(f"  {ANSWERER} answered {q['id']} ({q.get('frame')} line {first}) as the product owner decided "
                     f"({po['answer']}): {text}")
            continue
        if q.get("code") != "unread-rule" or verdict != "narrative":
            undecided.append(f"[{q.get('code')}] {q.get('frame')} line {first} ({verdict}: {why}): {quote or t}")
            continue
        cc.answer_question(hub, co["number"], q["id"], NARRATIVE, ANSWERER)
        log.line(f"  {ANSWERER} answered {q['id']} ({q.get('frame')} line {first}): {NARRATIVE} [read: {why}]")
    if undecided:
        for u in undecided:
            log.line(f"  NEEDS THE PRODUCT OWNER'S DECISION: {u}")
        raise NeedsDecision(f"{len(undecided)} blocking question(s) are not narrative and stay open (each blocks approval): the product "
                            "owner decides each (record it as a divergence, or rewrite the text); then run the script again")
    return hub.kernel.get(co["locator"])


# ---------------------------------------------------------------- step 4: validation evidence
def validate(hub, args, log: Log, co: dict) -> None:
    from hubproto import validation

    its = validation.annotate(validation.items_of(hub, co), validation.policy(hub))
    by_risk = collections.Counter(i["risk"] for i in its)
    log.line(f"validation basis: {validation.basis(hub)}")
    log.line("items by risk: " + ", ".join(f"{k} {v}" for k, v in sorted(by_risk.items())))
    ev = validation.evidence(hub, co["locator"])
    high = [i["id"] for i in its if i["validation"] == "test-record"]
    medium = [i["id"] for i in its if i["validation"] == "test-note"]
    if high and not any(e["kind"] == "test-record" and e["result"] == "pass" and MARK in e["text"] for e in ev):
        validation.record_evidence(
            hub, co["locator"], by=PREPARER, kind="test-record", items=high,
            text=f"{MARK} the change control v2 suite (python -m pytest -q -n auto) on commit {code_commit(args)}; "
                 f"run by Claude at the product owner's instruction",
            expected="every test passes", actual=args.test_result)
        log.line(f"recorded a passing test record for {len(high)} high-risk item(s): {args.test_result}")
    if medium and not any(e["kind"] == "test-note" and MARK in e["text"] for e in ev):
        validation.record_evidence(
            hub, co["locator"], by=PREPARER, kind="test-note", items=medium,
            text=f"{MARK} rehearsed this migration end to end on fresh restores of the live backup before running it here: "
                 f"{args.rehearsal_note}")
        log.line(f"recorded a test note for {len(medium)} medium-risk item(s)")
    left = validation.requirements(hub, co["locator"])
    for r in left:
        log.problem(f"validation still needs: {r}")


# ---------------------------------------------------------------- step 5: decisions, approval, release
def decide(hub, args, log: Log, co: dict) -> dict:
    from hubproto import changecontrol as cc
    from hubproto import eco

    co = cc.sync(hub, co["locator"])
    if co["state"] in ("approved", "released"):
        log.line(f"{co['number']} is {co['state']}")
    else:
        items = [i for i in cc.delta(hub, co["number"]) if i["op"] != "text"]
        warnings = collections.Counter()
        n = 0
        for it in items:
            for w in it.get("warnings") or []:
                if not w.get("accepted"):
                    warnings[(w.get("code"), w.get("text"))] += 1
            for who in OWNERS:
                if who in (it.get("makers") or []):
                    log.line(f"  {who} made {it['id']}: does not decide it")
                    continue
                if any(d["by"] == who and d["verdict"] == "approve" and not d.get("stale") for d in it.get("decisions") or []):
                    continue
                cc.decide_item(hub, co["number"], it["id"], "approve", who, MEANING, accept_warnings=("all",))
                n += 1
        log.line(f"items: {len(items)} ({sum(1 for i in items if i.get('widening'))} widening(s)); decisions given now: {n}")
        log.line(f"warnings accepted on items ({sum(warnings.values())}):")
        for (code, text), k in sorted(warnings.items(), key=lambda kv: str(kv[0])):
            log.line(f"  x{k} [{code}] {text}")
        log.report["warnings_accepted"] = [{"code": c, "text": t, "count": k} for (c, t), k in warnings.items()]
        for w in cc.co_warnings(hub, co["number"]):
            if not w.get("accepted"):
                cc.accept_warning(hub, co["number"], w["ref"], QM, MEANING)
                log.line(f"  {QM} accepted on the change order: [{w.get('code')}] {w.get('text')}")
                log.report["warnings_accepted"].append({"code": w.get("code"), "text": w.get("text"), "count": 1, "on": "co"})
        co = hub.kernel.get(co["locator"])
        if cc.stage_of(co) in cc.EDITABLE:
            reasons = cc.send_reasons(hub, co["number"])
            if reasons:
                raise CheckFailed("it cannot be sent for approval: " + "; ".join(reasons))
            cc.send_for_approval(hub, co["number"], PREPARER)
            log.line(f"{PREPARER} sent {co['number']} for approval")
        co = hub.kernel.get(co["locator"])
        reasons = cc.approval_reasons(hub, co["number"])
        if reasons:
            raise CheckFailed("it cannot be signed: " + "; ".join(reasons))
        run = hub.runner.run_for_record(co["locator"])
        state = hub.runner.load(run["id"])
        if QM not in (state.get("pending") or {}).get("assignees", []):
            raise CheckFailed(f"the approval Gate waits for {state.get('pending', {}).get('assignees')}, not {QM}")
        hub.runner.decide(run["id"], QM, "approve", meaning=MEANING, payload_digest=hub.runner.seen_digest(state))
        log.line(f"{QM} signed {co['number']}")
    co = hub.kernel.get(co["locator"])
    jobs = [j for j in hub.automation.of(co["locator"]) if j["automation"] == "release-change-order"]
    if co["state"] == "approved" and (not jobs or jobs[-1]["status"] != "success"):
        eco.release(hub, co["number"], by=QM)
        jobs = [j for j in hub.automation.of(co["locator"]) if j["automation"] == "release-change-order"]
    for j in jobs[-1:]:
        log.line(f"release automation {j['id']}: {j['status']}" + (f" ({j.get('outcome')})" if j.get("outcome") else ""))
        for s in j["steps"]:
            log.line(f"  step {s['name']}: {s.get('status')}" + (f": {s.get('outcome')}" if s.get("outcome") else ""))
        if j["status"] != "success":
            raise CheckFailed(f"the release automation stopped: {j.get('outcome')}")
    return hub.kernel.get(co["locator"])


# ---------------------------------------------------------------- step 6: verify
def verify(hub, args, log: Log, root: Path, co: dict, before: dict) -> None:
    import hubproto
    from hubproto import changecontrol as cc
    from hubproto import eco, recordtypes, sopdocs

    fails = []
    cur = hub.releases.current()
    log.line(f"release in force: {version_of(hub, cur['digest'])} (change order {cur.get('change_order')})")
    if cur["digest"] == before.get("release") and not (co and co["fields"].get("released_release") == cur["digest"]):
        fails.append("the release in force is the one before the rollout")
    if co and cur.get("change_order") != co["number"]:
        fails.append(f"the release in force was not released by {co['number']}")
    if co:
        carried = eco.carried(hub, co)
        not_eff = [f"{c['name']} {c['draft']['number']} {c['draft']['state']}" for c in carried if c["draft"]["state"] != "effective"]
        log.line(f"carried revisions: {len(carried)}; effective: {len(carried) - len(not_eff)}")
        fails += [f"revision not effective: {x}" for x in not_eff]
        store = new_frames(args)
        texts = hub.releases.frame_texts(cur["digest"])
        bumped = [n for n, f in store.frames.items() if (texts.get(n) or "") != f.text and same_but_version(texts.get(n) or "", f.text)]
        # A Frame no compile scope reads (the reference guide) is never in a release's texts; its document is checked below.
        unreleased = [n for n in store.frames if n not in texts and n not in hub.releases.frame_texts(before.get("release") or cur["digest"])]
        off = [n for n, f in store.frames.items() if n not in unreleased and not same_but_version(texts.get(n) or "", f.text)]
        if unreleased:
            log.line(f"Frames no compile scope reads (not in any release's texts; their documents are checked): {', '.join(unreleased)}")
        log.line(f"Frames of the release in force: {len(store.frames) - len(off) - len(bumped)} exactly the repository's text, "
                 f"{len(bumped)} the repository's text with the version the compile raised (their version line was unchanged: "
                 f"{', '.join(bumped) or 'none'}), {len(off)} different" + (f" ({', '.join(off)})" if off else ""))
        fails += [f"the release's text of {n} is not the repository's" for n in off]
        docs = sop_docs(hub)
        for n, f in store.frames.items():
            if n not in docs:
                fails.append(f"{n} has no document in force")
            elif not same_but_version(sopdocs.frame_text(hub, docs[n]), f.text):
                fails.append(f"the document in force for {n} does not read as the repository's text")
        retired = [n for n in (co["fields"].get("retires") or {}) if n in docs or n in texts]
        fails += [f"{n} is retired by {co['number']} but still in force" for n in retired]
        rt = recordtypes.in_force(hub)
        log.line(f"record types in force: v{rt['version']} ({rt.get('source')})")
        if recordtypes.changes_of(co) and rt.get("released_with") != co["number"]:
            fails.append("the record-type changes are not in force")
        problems = cc.verify_delta(hub, co["number"])
        log.line(f"verify_delta: {len(problems)} problem(s)")
        fails += [f"verify_delta: {p}" for p in problems]
    if co:
        # The next compile (not saved) reads the documents in force: the new Frames are in it, the retired ones are not.
        nxt = hub.compile(save=False)
        names = {f["name"] for f in nxt.get("frames") or []}
        created = [c["name"] for c in eco.carried(hub, co) if c["draft"]["fields"].get("revision") == "A" and c["name"] not in names]
        back = sorted(set(eco.retirements(co)) & names)
        live = [c for c in nxt.get("changes") or [] if c.get("op") != "text"]
        log.line(f"next compile: {len(names)} Frames; new ones missing: {created or 'none'}; retired ones present: {back or 'none'}; "
                 f"changes against the release in force: {len(live)}")
        fails += [f"the next compile lacks the new Frame {n}" for n in created] + [f"the next compile still has the retired {n}" for n in back]
        fails += [f"the next compile changes something: {c.get('reason')}" for c in live[:5]]
        # The senior cogs: unavailable with hosted models off, available with them on and a key (a fake one: availability
        # is decided without calling the API).
        saved = {k: os.environ.get(k) for k in ("HUBPROTO_HOSTED_MODELS", "ANTHROPIC_API_KEY")}
        try:
            os.environ.pop("HUBPROTO_HOSTED_MODELS", None)
            off = hub.compile_committee() or {}
            os.environ["HUBPROTO_HOSTED_MODELS"], os.environ["ANTHROPIC_API_KEY"] = "on", "sk-ant-fake-rollout-check"
            on = hub.compile_committee() or {}
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        log.line(f"compile committee ({off.get('group')}, chair {off.get('chair')}): hosted models off: "
                 + ", ".join(f"{n} {'available' if m else 'unavailable'}" for n, m in off.get("members") or [])
                 + "; on with a key: " + ", ".join(f"{n} {m or 'unavailable'}" for n, m in on.get("members") or []))
        seniors = [n for n, _ in off.get("members") or []]
        if not seniors or any(m for _, m in off.get("members") or []) or not all(m for _, m in on.get("members") or []):
            fails.append("the senior cogs are not unavailable with hosted models off and available with them on")
    chain = [p for p in hub.kernel.verify_chain() if "not chained" not in p]
    log.line(f"kernel verify_chain: {len(chain)} problem(s)")
    fails += [f"verify_chain: {p}" for p in chain]
    sig = []
    for r in hub.runner.list():
        sig += [f"run {r['id']}: {p}" for p in hub.runner.verify_signatures(r["id"]) if "not chained" not in p]
    log.line(f"verify_signatures over {len(hub.runner.list())} runs: {len(sig)} problem(s)")
    fails += sig
    after = health(hub, args, log, root, "after", sample_ids=[r["id"] for r in before.get("sample") or []])
    was = {r["id"]: r for r in before.get("sample") or []}
    for r in after["sample"]:
        b = was.get(r["id"])
        if b and b.get("outcome") != r.get("outcome"):
            fails.append(f"{r['id']} {r.get('number')} was {b.get('outcome')} on the copy before the rollout and is "
                         f"{r.get('outcome')} after: {r.get('why')}")
    gone = sorted(set(before.get("waiting") or []) - set(after["waiting"]))
    if gone:
        fails.append(f"{len(gone)} run(s) waiting before are no longer waiting: {', '.join(gone[:10])}")
    log.line(f"waiting before: {len(before.get('waiting') or [])}; still waiting: {len(set(before.get('waiting') or []) & set(after['waiting']))}")
    if after["problems"]:
        fails.append(f"{after['problems']} waiting run(s) cannot be read or decided after the rollout")
    if any(not s["ok"] for s in after["sample"]):
        fails.append("a sampled run could not be decided after the rollout (see above)")
    p = subprocess.run([sys.executable, "-m", "hubproto", "--root", str(root), "status", "--check"], capture_output=True, text=True,
                       timeout=600, cwd=str(Path(hubproto.__file__).resolve().parent.parent))
    log.line(f"status --check: exit {p.returncode}")
    for ln in (p.stdout + p.stderr).splitlines():
        log.line("  " + ln)
    if p.returncode != 0:
        fails.append("status --check is not clean")
    for f in fails:
        log.problem(f)
    if fails:
        raise CheckFailed(f"{len(fails)} check(s) failed after the rollout")


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="the hub root (holds state/ and frames/)")
    ap.add_argument("--frames-from", required=True, help="the repository's frames/ of change control v2 (the revision texts)")
    ap.add_argument("--dry-run", action="store_true", help="read and report; write nothing to the hub")
    ap.add_argument("--steps", default="health,co,review,validate,decide,verify", help="comma-separated steps to run")
    ap.add_argument("--sample", type=int, default=15, help="waiting runs to decide on a throwaway copy (default 15)")
    ap.add_argument("--scratch", default="", help="where throwaway copies go (default: the system temporary directory)")
    ap.add_argument("--keep-copies", action="store_true", help="keep the throwaway copies (their path is logged)")
    ap.add_argument("--log", default="", help="log file (default <root>/state/rollout-cc2/<time>.log)")
    ap.add_argument("--report", default="", help="JSON report (default beside the log)")
    ap.add_argument("--commit", default="", help="the commit the code was built from (default: git rev-parse HEAD)")
    ap.add_argument("--test-result", default="", help="the suite's summary line on that commit, e.g. '1029 passed in 41.2s'")
    ap.add_argument("--rehearsal-note", default="see docs/rollout/cc2.md and the rehearsal logs",
                    help="what the rehearsals showed (the medium-risk items' test note)")
    ap.add_argument("--decisions", default="", help="JSON file of the product owner's decisions on the paragraphs this script does not "
                    "answer itself: {\"<frame>:<first line>\": {\"answer\": \"divergence\"|\"narrative\", \"meaning\": \"...\"}}")
    ap.add_argument("--_sample-decide", default="", help=argparse.SUPPRESS)
    ap.add_argument("--_sample-out", default="", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    args.decisions = json.loads(Path(args.decisions).read_text()) if args.decisions else {}
    root = Path(args.root).resolve()
    if not (root / "state" / "kernel.db").exists():
        print(f"error: no hub at {root}", file=sys.stderr)
        return 2
    if args._sample_decide:
        return sample_decide_main(args)
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    logp = Path(args.log) if args.log else root / "state" / "rollout-cc2" / f"{stamp}{'-dry-run' if args.dry_run else ''}.log"
    logp.parent.mkdir(parents=True, exist_ok=True)
    log = Log(logp)
    import hubproto

    log.line(f"rollout of change control v2 {'(DRY RUN: nothing is written to the hub)' if args.dry_run else ''}")
    log.line(f"hub: {root}; code: {Path(hubproto.__file__).resolve().parent} at {code_commit(args)}; frames from {args.frames_from}")
    log.line(f"HUBPROTO_EXTRACTOR={os.environ.get('HUBPROTO_EXTRACTOR')} HUBPROTO_COG_QUEUE={os.environ.get('HUBPROTO_COG_QUEUE')} "
             f"HUBPROTO_ALLOW_STAND_IN={os.environ.get('HUBPROTO_ALLOW_STAND_IN', '')} HUBPROTO_HOSTED_MODELS={os.environ.get('HUBPROTO_HOSTED_MODELS', '')} "
             f"HUBPROTO_OLLAMA_URL={'set' if os.environ.get('HUBPROTO_OLLAMA_URL') else 'unset'}")
    if not args.dry_run and "validate" in steps and not args.test_result:
        print("error: --test-result is required to record the validation evidence (the suite's summary line)", file=sys.stderr)
        return 2
    code = 0
    before: dict = {}
    co = None
    try:
        hub = hub_at(root)
        if "health" in steps:
            with log.step("1 health before"):
                before = health(hub, args, log, root, "before")
        co = find_co(hub)
        if "co" in steps:
            with log.step("2 the migration change order"):
                co = change_order(hub, args, log) or co
        if args.dry_run:
            log.line("dry run: steps 3 to 6 need the change order; not run")
        else:
            if co is None:
                raise CheckFailed("there is no migration change order")
            if "review" in steps:
                with log.step("3 review round"):
                    co = review(hub, args, log, co)
            if "validate" in steps:
                with log.step("4 validation evidence"):
                    validate(hub, args, log, co)
            if "decide" in steps:
                with log.step("5 decisions, approval and release"):
                    co = decide(hub, args, log, co)
            if "verify" in steps:
                with log.step("6 verify"):
                    if not before:
                        before = {"release": None, "waiting": []}
                    verify(hub, args, log, root, co, before)
    except NeedsCode:
        code = NEEDS_CODE
    except NeedsDecision:
        code = NEEDS_DECISION
    except CheckFailed:
        code = 1
    except Exception:
        code = 1
    if log.report["problems"] and code == 0:
        code = 1
    log.report["change_order"] = (co or {}).get("number")
    log.report["exit"] = code
    rep = Path(args.report) if args.report else logp.with_suffix(".json")
    rep.write_text(json.dumps(log.report, indent=1, default=str))
    log.line(f"exit {code}; log {logp}; report {rep}")
    log.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
