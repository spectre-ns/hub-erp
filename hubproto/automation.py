"""The automation engine: Ops the hub runs on its own, each run a job you can watch step by step.

An automation has a trigger, a subject and named steps. When its trigger fires (a change order's requester asks for
review; a change order is approved; another release comes into force), the hub starts a job: it goes in the queue
(lane "hub-automation", one job at a time, oldest first), and each step is recorded as it runs, with its outcome in
words. A job ends as success, some errors (it stopped part way: retry it after fixing what it says), no actions
(nothing to do) or failed. The jobs and their steps are the audit trail: who or what started each one, on which
approval it acts, what each step did.

An automation never decides anything a person has not: it acts only on what was approved (a change order approved by
its approver, every item of its delta decided by the approvers, widenings signed by both policy owners), and it
refuses when that approval is missing or out of date.

Two automations come with the hub (change control v2, docs/design/change-control.md; hubproto.changecontrol):

* ``compile-frame``: the Op that compiles a change order's delta. When review is requested it carries the review
  round: collects the carried drafts and direct changes, compiles the Frame from the drafts as they stand (the
  compile-frame committee's cogs read the diff, each with its own bundled model), drafts the prose for direct changes
  (the qa-manager cog) and checks their round trip, checks the guards, stages the delta on the change order, suggests
  the panel (the quality-assistant cog) and queues the panel's reviews. When another release comes into force it
  compiles every open change order again (no round: no prose drafted, no panel).
* ``release-change-order``: brings an approved change order into force: the revisions it carries are approved with
  it, the decided delta comes into force with the revisions, forms and record types; training is assigned;
  divergences are recorded (a change request for each new one); related change orders and change requests are sent
  back; every other open change order is compiled again. Triggered by the change order's approval.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .release import utcnow

LANE = "hub-automation"
LOOP_LIMIT, LOOP_WINDOW_NS = 10, 10 * 60 * 10**9  # the same automation on the same item, again and again: a loop
FINAL = ("success", "some-errors", "no-actions", "failed")

AUTOMATIONS = {
    "compile-frame": {
        "title": "Compile the Frame",
        "committee": "compile-frame",  # its member cogs read the SOPs' prose into rules, each on its own; the quorum decides
        "trigger": "review requested on a change order (a review round), or another release came into force (every open change order)",
        "steps": ["Collect the carried drafts and direct changes", "Compile the Frame", "Draft prose for direct changes", "Check the guards",
                  "Stage it on the change order", "Suggest the panel", "Queue the panel's reviews", "Tell the people involved"],
    },
    "release-change-order": {
        "title": "Release the change order",
        "trigger": "a change order is approved",
        "steps": ["Check the change order is ready", "Approve the carried revisions", "Bring the Frame into force", "Move items by the state map", "Publish the revisions",
                  "Apply the form layouts", "Apply the record-type changes", "Assign training", "Record divergences",
                  "Send related changes back", "Compile every open change order", "Tell the people involved"],
    },
}


class StepError(Exception):
    """A step that cannot do its part: the job stops there, with this reason."""


class NoActions(Exception):
    """Nothing to do: the job ends as no-actions."""


class JobStore:
    """One JSON file per job under state/automation/."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def save(self, job: dict) -> dict:
        job["updated_at"] = utcnow()
        tmp = self.path(job["id"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(job, indent=1, default=str), encoding="utf-8")
        tmp.replace(self.path(job["id"]))
        return job

    def load(self, job_id: str) -> dict | None:
        p = self.path(job_id)
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except ValueError:
            return None

    def all(self) -> list[dict]:
        out = []
        for p in self.root.glob("*.json"):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return sorted(out, key=lambda j: j.get("created_ns") or 0, reverse=True)

    def of(self, subject: str) -> list[dict]:
        return [j for j in self.all() if j.get("subject") == subject or j.get("subject_number") == subject]


def start(hub, automation: str, *, subject: str, trigger: str, by: str = "hub", acting_on: str = "") -> dict:
    """Start a job (queued; worked at once when the queue is inline). A job of the same automation on the same subject
    already waiting is not started twice."""
    if automation not in AUTOMATIONS:
        raise ValueError(f"no automation {automation!r}")
    recent = 0
    for j in hub.automation.of(subject):
        if j["automation"] == automation and j["status"] == "queued":
            return j
        if j["automation"] == automation and time.time_ns() - int(j.get("created_ns") or 0) < LOOP_WINDOW_NS:
            recent += 1
    if recent >= LOOP_LIMIT:
        raise ValueError(f"{AUTOMATIONS[automation]['title']} ran {recent} times on this item in the last 10 minutes: stopped as a loop. "
                         "Look at the last job, fix what it says, then retry it.")
    rec = hub.kernel.get(subject)
    meta = hub.releases.current()
    job = {"id": uuid.uuid4().hex[:10], "automation": automation, "title": AUTOMATIONS[automation]["title"], "subject": subject,
           "subject_number": rec["number"] if rec else subject, "trigger": trigger, "started_by": by, "acting_on": acting_on,
           "status": "queued", "created_at": utcnow(), "created_ns": time.time_ns(),
           "release": meta["digest"] if meta else "",  # the policy in force when it started: what it acts under
           "steps": [{"name": n, "status": "waiting"} for n in AUTOMATIONS[automation]["steps"]], "attempt": 0}
    hub.automation.save(job)
    hub.queue.submit(LANE, "automation", by=by, title=f"{job['title']}: {job['subject_number']}", record=subject if rec else None,
                     payload={"job": job["id"]}, key=f"automation:{job['id']}")
    return hub.automation.load(job["id"])


def retry(hub, job_id: str, *, by: str) -> dict:
    job = hub.automation.load(job_id)
    if job is None:
        raise ValueError(f"no automation job {job_id}")
    if job["status"] not in ("some-errors", "failed"):
        raise ValueError(f"the job is {job['status']}; only a job that stopped is retried")
    job["status"] = "queued"
    job["retried_by"] = by
    for s in job["steps"]:
        if s["status"] in ("failed", "skipped"):
            s.update({"status": "waiting", "detail": ""})
    hub.automation.save(job)
    hub.queue.submit(LANE, "automation", by=by, title=f"{job['title']} (retry): {job['subject_number']}", record=job["subject"],
                     payload={"job": job["id"]}, key=f"automation:{job['id']}:{job.get('attempt', 0) + 1}")
    return hub.automation.load(job_id)


def run(hub, job_id: str) -> dict:
    """Work a job: its steps in order, each recorded as it starts and ends. Steps already done (a retry) are skipped."""
    job = hub.automation.load(job_id)
    if job is None:
        raise ValueError(f"no automation job {job_id}")
    if job["status"] in ("success", "no-actions"):
        return job
    job.update({"status": "running", "attempt": int(job.get("attempt") or 0) + 1, "started_at": job.get("started_at") or utcnow()})
    hub.automation.save(job)
    steps = STEPS[job["automation"]]
    memo: dict = {}
    done_any = False
    for i, s in enumerate(job["steps"]):
        if s["status"] == "done":
            memo.update(s.get("memo") or {})
            done_any = True
            continue
        s.update({"status": "running", "started_at": utcnow(), "detail": ""})
        hub.automation.save(job)
        t0 = time.time()
        try:
            detail, keep = steps[i](hub, job, memo)
            memo.update(keep or {})
            s.update({"status": "done", "detail": detail, "memo": keep or {}, "ms": int((time.time() - t0) * 1000), "finished_at": utcnow()})
            done_any = True
        except NoActions as e:
            s.update({"status": "done", "detail": str(e), "ms": int((time.time() - t0) * 1000), "finished_at": utcnow()})
            for rest in job["steps"][i + 1:]:
                rest.update({"status": "skipped", "detail": "nothing to do"})
            job.update({"status": "no-actions", "finished_at": utcnow(), "outcome": str(e)})
            return hub.automation.save(job)
        except Exception as e:  # the step could not do its part: the job stops here and says why
            s.update({"status": "failed", "detail": str(e) if isinstance(e, StepError) else f"{type(e).__name__}: {e}",
                      "ms": int((time.time() - t0) * 1000), "finished_at": utcnow()})
            for rest in job["steps"][i + 1:]:
                rest.update({"status": "skipped", "detail": "not run: an earlier step stopped"})
            job.update({"status": "some-errors" if done_any and i > 0 else "failed", "finished_at": utcnow(), "outcome": s["detail"]})
            hub.automation.save(job)
            _tell_failure(hub, job)
            return job
        hub.automation.save(job)
    job.update({"status": "success", "finished_at": utcnow(), "outcome": job["steps"][-1].get("detail") or "done"})
    return hub.automation.save(job)


def _tell_failure(hub, job: dict) -> None:
    rec = hub.kernel.get(job["subject"])
    who = (rec or {}).get("fields", {}).get("requested_by") or job.get("started_by")
    if who and who != "hub":
        hub.inbox.notify(who, kind="failure", subject=f"{job['title']} stopped on {job['subject_number']}",
                         body=f"{job.get('outcome') or 'it stopped'}. See the automation job; retry it once that is fixed.", record=job["subject"])


# ---------------------------------------------------------------- compile-frame (hubproto.changecontrol carries the round)
def _cc(fn):
    """A compile-frame step. A refusal (ValueError) stops the job with its sentence; whatever stops it, the review
    round it carries is marked as stopped (the job can be retried, or review requested again)."""
    def step(hub, job, memo):
        from . import changecontrol

        n = memo.get("round")
        if n:  # a retry picks the round up again
            co = hub.kernel.get(job["subject"])
            rnd = next((r for r in changecontrol.rounds(co) if r["n"] == n), None) if co else None
            if rnd and rnd.get("status") == "failed" and rnd.get("job") == job["id"]:
                changecontrol._update_round(hub, co, n, lambda r: r.update({"status": "compiling", "error": None}), stage="in-review")
        try:
            return fn(hub, job, memo)
        except (StepError, NoActions):
            raise
        except Exception as e:
            co = hub.kernel.get(job["subject"])
            if n and co:
                rnd = next((r for r in changecontrol.rounds(co) if r["n"] == n), None)
                if rnd and rnd.get("status") == "compiling":
                    changecontrol._round_failed(hub, co, n, str(e))
            if isinstance(e, ValueError):
                raise StepError(str(e)) from None
            raise
    return step


def _readers(hub) -> str:
    committee = hub.compile_committee()
    if committee:
        ready = [n for n, m in committee["members"] if m]
        if len(ready) >= committee["quorum"]:
            return f"; prose read by the compile-frame committee ({', '.join(ready)}; by consensus, every member agreeing)"
        missing = [n for n, m in committee["members"] if not m]
        return (f"; sentence shapes only: the compile-frame committee cannot reach consensus without {', '.join(missing)} "
                f"(not available here); " + (f"its chair, {committee['chair']}, intervenes" if committee.get("chair") else "a person intervenes"))
    model = hub.compiler_model()
    return f"; prose read by the organization cog ({model})" if model else "; sentence shapes only (no local model)"


@_cc
def c_collect(hub, job, memo):
    from . import changecontrol, eco

    co = hub.kernel.get(job["subject"])
    if co is None:
        raise StepError("the change order is gone")
    carried = eco.carried(hub, co)
    items = co["fields"].get("direct_changes") or []
    what = ((f"{len(carried)} revision(s) as they stand ({', '.join(c['draft']['number'] + ' (' + c['name'] + ')' for c in carried)})" if carried
             else "no revision") + (f"; {len(items)} direct change(s)" if items else ""))
    rnd = changecontrol.current_round(co)
    if rnd and rnd.get("status") == "compiling" and rnd.get("job") in (None, job["id"]):
        # This job carries the review round the requester asked for.
        changecontrol._update_round(hub, co, rnd["n"], lambda r: r.update({"job": job["id"]}))
        return f"review round {rnd['n']}: {what}", {"round": rnd["n"]}
    changed = [c for c in carried if c["in_force"] is None
               or str(c["draft"]["fields"].get("content_sha") or "") != str(c["in_force"]["fields"].get("content_sha") or "")]
    if not changed and not items:
        raise NoActions("nothing to compile: it carries no changed revision and no direct change" if not carried
                        else "nothing changed: the drafts it carries are the SOPs in force")
    return f"compiled again, no review round: {what}", {"round": None}


@_cc
def c_compile(hub, job, memo):
    from . import changecontrol

    # The background job may ask the committee's models to read the SOPs' prose (pages never wait on it).
    if memo.get("round"):
        return changecontrol.round_compile(hub, job["subject"], memo["round"], extractor=hub.extractor(calls=True)) + _readers(hub), {}
    out = changecontrol.compile_delta(hub, job["subject"], extractor=hub.extractor(calls=True))
    if out["proposal"] is None:
        if out["errors"]:
            raise StepError("the Frame does not compile: " + "; ".join(out["errors"][:3]))
        raise NoActions("nothing to compile")
    p = out["proposal"]
    live = [c for c in p["changes"] if c.get("op") != "text"]
    return (f"compiled as proposal #{p.get('number')}: {len(live)} change(s), {sum(1 for c in live if c.get('widening'))} widening(s)"
            + _readers(hub)), {"proposal": p["id"]}


@_cc
def c_draft(hub, job, memo):
    from . import changecontrol

    if not memo.get("round"):
        return "not a review round: prose for direct changes is drafted when review is requested", {}
    by = job.get("started_by") or "hub"
    return changecontrol.round_draft(hub, job["subject"], memo["round"], by, extractor=hub.extractor(calls=True)), {}


@_cc
def c_guards(hub, job, memo):
    from . import changecontrol

    if memo.get("round"):
        return changecontrol.round_check(hub, job["subject"], memo["round"]), {}
    co = hub.kernel.get(job["subject"])
    errors = co["fields"].get("staged_errors") or []
    if errors:
        raise StepError("the compiled Frame has problems that stop it: " + "; ".join(errors[:3]))
    w = int(co["fields"].get("staged_widenings") or 0)
    return ("no guard stops it" + (f"; {w} widening(s) need both policy owners" if w else "")), {}


@_cc
def c_stage(hub, job, memo):
    co = hub.kernel.get(job["subject"])
    if not co["fields"].get("staged_proposal"):
        return f"nothing staged on {co['number']}: it carries forms or record types only", {}
    return f"staged on {co['number']} against release {str(co['fields'].get('staged_base') or '')[:12]}", {}


@_cc
def c_panel(hub, job, memo):
    from . import changecontrol

    if not memo.get("round"):
        return "not a review round: no panel", {}
    return changecontrol.round_panel(hub, job["subject"], memo["round"]), {}


@_cc
def c_queue(hub, job, memo):
    from . import changecontrol

    if not memo.get("round"):
        return "not a review round: nobody asked", {}
    return changecontrol.round_queue(hub, job["subject"], memo["round"], job.get("started_by") or "hub"), {}


@_cc
def c_tell(hub, job, memo):
    from . import changecontrol

    if memo.get("round"):
        return changecontrol.round_tell(hub, job["subject"], memo["round"]), {}
    co = hub.kernel.get(job["subject"])
    f = co["fields"]
    who = f.get("requested_by")
    if who:
        hub.inbox.notify(who, kind="automation", subject=f"The delta of {co['number']} is compiled again",
                         body=f"{job.get('trigger') or ''}: {f.get('staged_changes')} item(s) against the release in force. Decisions on items that "
                              "did not change are kept.", record=co["locator"])
    return (f"told {who}" if who else "nobody to tell"), {}


# ---------------------------------------------------------------- release-change-order
def r_check(hub, job, memo):
    from . import eco

    co = hub.kernel.get(job["subject"])
    if co["state"] != "approved" and not co["fields"].get("released_release"):
        raise StepError(f"not ready: {co['number']} is {co['state'].replace('-', ' ')}, not approved (it went back to revising, or was never signed)")
    signer = (co["fields"].get("signed") or {})
    if signer.get("by_type") not in (None, "person"):
        raise StepError(f"{co['number']} was signed by {signer.get('by')}, a {signer.get('by_type')}: a person signs a change order")
    blocked = eco.check_approval(hub, job["subject"])
    if blocked:
        raise StepError("not ready: " + "; ".join(blocked))
    return f"{co['number']} is approved and its delta is decided; what it carries is what was signed", {}


def r_revisions(hub, job, memo):
    from . import changecontrol

    try:
        done = changecontrol.approve_revisions(hub, job["subject"])
    except ValueError as e:
        raise StepError(str(e)) from None
    return ("approved with the change order: " + ", ".join(done)) if done else "no revision to approve", {}


def r_frame(hub, job, memo):
    from . import changecontrol, eco

    co = hub.kernel.get(job["subject"])
    applied = changecontrol.apply_delta(hub, job["subject"])
    meta = eco.activate_frame(hub, job["subject"])
    if not co["fields"].get("staged_proposal") and not eco.carried(hub, co) and not eco.retirements(co):
        return f"no Frame change: release {meta['digest'][:12]} stays in force", {"release": meta["digest"]}
    rec = changecontrol.record_release(hub, job["subject"], meta["digest"])
    extra = [f"{applied['overrides']} override(s)" if applied["overrides"] else "", f"{applied['direct']} direct change(s) the prose does not state"
             if applied["direct"] else "", f"{len(rec['new'])} new divergence(s)" if rec["new"] else "",
             f"{len(rec['resolved'])} divergence(s) resolved" if rec["resolved"] else ""]
    extra = [x for x in extra if x]
    return f"release {meta['digest'][:12]} is in force" + (f" ({'; '.join(extra)})" if extra else ""), {"release": meta["digest"]}


def r_state_maps(hub, job, memo):
    """Items in statuses the release removed move where the change order's state map says (design note 5.2), once the
    new release is in force: no validator or action runs, waiting Gates are withdrawn, each move is on the item's Track."""
    from .runner import apply_state_map

    co = hub.kernel.get(job["subject"])
    signed = co["fields"].get("state_map_signed")
    smap = signed if signed is not None else (co["fields"].get("state_map") or {})
    if signed is not None and (co["fields"].get("state_map") or {}) != signed:
        raise StepError(f"the state map of {co['number']} changed after it was signed: only the signed map is applied, and it no longer matches")
    moved = []
    for process, mapping in sorted(smap.items()):
        if mapping:
            moved += apply_state_map(hub, process, mapping, "hub", co["number"])
    if not moved:
        return "no item to move", {}
    return f"moved {len(moved)} item(s): " + ", ".join(f"{m.get('record')} {m.get('from')} → {m.get('to')}" for m in moved[:5]), {"moved": len(moved)}


def r_publish(hub, job, memo):
    from . import eco

    done = eco.publish_revisions(hub, job["subject"], memo["release"])
    return ("published " + ", ".join(done)) if done else "no revision to publish", {"published": done}


def r_forms(hub, job, memo):
    from . import formlayout

    from . import workflows

    done = formlayout.activate(hub, hub.kernel.get(job["subject"]), memo["release"])
    done += workflows.activate(hub, hub.kernel.get(job["subject"]), memo["release"])  # workflow layouts, like form layouts
    return ("in force: " + ", ".join(done)) if done else "no form change", {}


def r_record_types(hub, job, memo):
    from . import recordtypes

    try:
        done = recordtypes.activate(hub, hub.kernel.get(job["subject"]), memo["release"])
    except ValueError as e:
        raise StepError(str(e)) from None
    return ("in force: " + ", ".join(done)) if done else "no record-type change", {}


def r_training(hub, job, memo):
    from . import eco

    n, problems = eco.assign_revision_training(hub, job["subject"], memo["release"])
    if problems:
        raise StepError("training was not assigned for " + "; ".join(problems))
    from . import training  # the training matrix, from the Roles and responsibilities tables in force (workstream 3)

    matrix = training.publish_matrix(hub, memo["release"])
    return f"{n} training task(s) assigned" + (f"; {matrix['number']} {training.MATRIX_TITLE} rev {matrix['fields']['revision']} published"
                                                if matrix else ""), {}


def r_divergences(hub, job, memo):
    from . import changecontrol

    crs = changecontrol.raise_divergence_requests(hub, job["subject"], memo["release"])
    return (f"change request(s) raised to bring the SOPs in line: {', '.join(crs)}" if crs else "no new divergence"), {}


def r_related(hub, job, memo):
    from . import changecontrol

    found = changecontrol.find_related(hub, job["subject"])
    parts = [f"{x['number']} ({'by definition' if x['kind'] == 'definition' else 'by meaning'})" for x in found["cos"]]
    parts += [f"{x['number']} flagged ({x['cog']})" for x in found["crs"]]
    errors = found.get("errors") or []
    return ((("sent back: " + ", ".join(parts)) if parts else "no related change")
            + (f"; could not look for related changes by meaning: {'; '.join(errors)}" if errors else "")), {}


def r_restage(hub, job, memo):
    from . import changecontrol

    others = changecontrol.open_change_orders(hub, exclude=job["subject"])
    started, problems = [], []
    for r in others:
        try:
            if r["state"] == "approved":  # signed against the release before: it cannot come into force as signed
                r = changecontrol._back_to_revising(hub, r, f"{job['subject_number']} was released before it: it is compiled again against "
                                                            "the new release, then decided and signed again")
            j = start(hub, "compile-frame", subject=r["locator"], trigger=f"{job['subject_number']} brought a new release into force", by="hub")
            started.append(f"{r['number']} ({j['status']})")
        except Exception as e:  # one change order that cannot be restaged does not stop the others, nor the release
            problems.append(f"{r['number']}: {e}")
    return ((f"compiling again: {', '.join(started)}" if started else "no other open change order")
            + (f"; not restaged: {'; '.join(problems)}" if problems else "")), {}


def r_tell(hub, job, memo):
    co = hub.kernel.get(job["subject"])
    if co["state"] != "released":
        co = hub.kernel.transition(co["locator"], "released", by="hub", release=memo.get("release", ""),
                                   reason="released: its Frame and revisions are in force")
    co = hub.kernel.set_fields(co["locator"], {"stage": "released"}, by="hub")
    who = co["fields"].get("requested_by")
    if who:
        hub.inbox.notify(who, kind="automation", subject=f"{co['number']} is released", body="The revisions it carries are in force, with the Frame they make.",
                         record=co["locator"])
    return f"{co['number']} released" + (f"; told {who}" if who else ""), {}


STEPS = {"compile-frame": [c_collect, c_compile, c_draft, c_guards, c_stage, c_panel, c_queue, c_tell],
         "release-change-order": [r_check, r_revisions, r_frame, r_state_maps, r_publish, r_forms, r_record_types, r_training, r_divergences, r_related,
                                  r_restage, r_tell]}


# ---------------------------------------------------------------- triggers
def on_run_completed(hub, state: dict, rec: dict | None) -> None:
    """Start the automations a completed run triggers: a change order's approval releases it. (Approving a revision a
    change order carries no longer compiles anything: the delta is compiled in its review rounds.)"""
    if rec is None:
        return
    if rec["type"] == "change-order" and rec["state"] == "approved":
        from . import changecontrol

        changecontrol.on_approved(hub, rec["locator"])
        decided = next((e for e in reversed(hub.runner.track(state["id"])) if e["type"] == "gate.decided"), {})
        start(hub, "release-change-order", subject=rec["locator"], trigger=f"{rec['number']} was approved", by="hub",
              acting_on=f"approved by {decided.get('by') or 'its approver'} in run {state['id']}")
