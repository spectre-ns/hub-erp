"""The automation engine: Ops the hub runs on its own, each run a job you can watch step by step.

An automation has a trigger, a subject and named steps. When its trigger fires (a revision a change order carries is
approved; a change order is approved), the hub starts a job: it goes in the queue (lane "hub-automation", one job at
a time, oldest first), and each step is recorded as it runs, with its outcome in words. A job ends as success, some
errors (it stopped part way: retry it after fixing what it says), no actions (nothing to do) or failed. The jobs and
their steps are the audit trail: who or what started each one, on which approval it acts, what each step did.

An automation never decides anything a person has not: it acts only on what was approved (a revision approved through
document control, a change order approved by its approver, widenings signed by both policy owners), and it refuses
when that approval is missing or out of date.

Two automations come with the hub:

* ``compile-frame``: compiles the Frame the approved revisions of a change order make, against the release in force,
  checks its guards and stages it on the change order. Triggered by the approval of a revision the change order
  carries; started again when another release comes into force.
* ``release-change-order``: brings an approved change order's compiled Frame into force and publishes its revisions,
  together; assigns training; restages the other open change orders. Triggered by the change order's approval.
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
        "trigger": "a revision carried by a change order is approved, or another release comes into force",
        "steps": ["Collect the approved revisions", "Compile the Frame", "Check the guards", "Stage it on the change order",
                  "Tell the people involved"],
    },
    "release-change-order": {
        "title": "Release the change order",
        "trigger": "a change order is approved",
        "steps": ["Check the change order is ready", "Bring the Frame into force", "Publish the revisions", "Assign training",
                  "Restage the other open change orders", "Tell the people involved"],
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


# ---------------------------------------------------------------- compile-frame
def _approved(hub, co: dict) -> dict[str, dict]:
    from . import eco

    return {c["name"]: c["draft"] for c in eco.carried(hub, co)
            if c["draft"]["state"] == "approved" and c["draft"]["fields"].get("held_for") == co["number"]}


def c_collect(hub, job, memo):
    co = hub.kernel.get(job["subject"])
    if co is None:
        raise StepError("the change order is gone")
    approved = _approved(hub, co)
    if not approved:
        raise NoActions("no revision on it is approved yet: nothing to compile")
    from . import eco

    waiting = [c["draft"]["number"] for c in eco.carried(hub, co) if c["name"] not in approved]
    return (f"{len(approved)} approved: " + ", ".join(f"{d['number']} ({n})" for n, d in sorted(approved.items()))
            + (f"; still to approve: {', '.join(waiting)}" if waiting else "")), {"approved": {n: d["locator"] for n, d in approved.items()}}


def c_compile(hub, job, memo):
    from . import eco

    fields = eco.stage(hub, job["subject"], drafts=memo["approved"])
    if fields.get("staged_errors") and not fields.get("staged_proposal"):
        raise StepError("the Frame does not compile: " + "; ".join(fields["staged_errors"][:3]))
    return (f"compiled as proposal #{fields.get('staged_number')}: {fields.get('staged_changes')} change(s), "
            f"{fields.get('staged_widenings')} widening(s)"), {"proposal": fields.get("staged_proposal")}


def c_guards(hub, job, memo):
    co = hub.kernel.get(job["subject"])
    errors = co["fields"].get("staged_errors") or []
    if errors:
        raise StepError("the compiled Frame has problems that stop it: " + "; ".join(errors[:3]))
    w = int(co["fields"].get("staged_widenings") or 0)
    return ("no guard stops it" + (f"; {w} widening(s) need both policy owners on the proposal" if w else "")), {}


def c_stage(hub, job, memo):
    co = hub.kernel.get(job["subject"])
    return f"staged on {co['number']} against release {str(co['fields'].get('staged_base') or '')[:12]}", {}


def c_tell(hub, job, memo):
    co = hub.kernel.get(job["subject"])
    f = co["fields"]
    who = f.get("requested_by")
    told = []
    if who:
        hub.inbox.notify(who, kind="automation", subject=f"The Frame for {co['number']} is compiled",
                         body=f"{f.get('staged_changes')} change(s). The change order can go for approval once every revision on it is approved.",
                         record=co["locator"])
        told.append(who)
    if int(f.get("staged_widenings") or 0):
        bundle = hub.releases.bundle(hub.releases.current()["digest"])
        from . import authz

        for owner in sorted(p for p in bundle["members"] if "policy-owner" in authz.roles_of(bundle, p)):
            hub.inbox.notify(owner, kind="proposal", subject=f"{co['number']} widens authority: your approval is needed",
                             body=f"Proposal #{f.get('staged_number')} staged by {co['number']} has {f.get('staged_widenings')} widening(s).",
                             record=co["locator"])
            told.append(owner)
    return ("told " + ", ".join(sorted(set(told)))) if told else "nobody to tell", {}


# ---------------------------------------------------------------- release-change-order
def r_check(hub, job, memo):
    from . import eco

    blocked = eco.check_approval(hub, job["subject"])
    if blocked:
        raise StepError("not ready: " + "; ".join(blocked))
    co = hub.kernel.get(job["subject"])
    return f"{co['number']} is approved and its compiled Frame is ready", {}


def r_frame(hub, job, memo):
    from . import eco

    meta = eco.activate_frame(hub, job["subject"])
    return f"release {meta['digest'][:12]} is in force", {"release": meta["digest"]}


def r_publish(hub, job, memo):
    from . import eco

    done = eco.publish_revisions(hub, job["subject"], memo["release"])
    return "published " + ", ".join(done), {"published": done}


def r_training(hub, job, memo):
    from . import eco

    n, problems = eco.assign_revision_training(hub, job["subject"], memo["release"])
    if problems:
        raise StepError("training was not assigned for " + "; ".join(problems))
    return f"{n} training task(s) assigned", {}


def r_restage(hub, job, memo):
    from . import eco

    others = [r for r in hub.kernel.query(type_="change-order") if r["locator"] != job["subject"] and r["state"] in eco.OPEN_STATES + ("approved",)
              and r["fields"].get("staged_proposal")]
    for r in others:
        start(hub, "compile-frame", subject=r["locator"], trigger=f"{job['subject_number']} brought a new release into force", by="hub")
    return (f"compiling again for {', '.join(r['number'] for r in others)}" if others else "no other change order to restage"), {}


def r_tell(hub, job, memo):
    co = hub.kernel.get(job["subject"])
    if co["state"] != "released":
        co = hub.kernel.transition(co["locator"], "released", by="hub", release=memo.get("release", ""),
                                   reason="released: its Frame and revisions are in force")
    who = co["fields"].get("requested_by")
    if who:
        hub.inbox.notify(who, kind="automation", subject=f"{co['number']} is released", body="The revisions it carries are in force, with the Frame they make.",
                         record=co["locator"])
    return f"{co['number']} released" + (f"; told {who}" if who else ""), {}


STEPS = {"compile-frame": [c_collect, c_compile, c_guards, c_stage, c_tell],
         "release-change-order": [r_check, r_frame, r_publish, r_training, r_restage, r_tell]}


# ---------------------------------------------------------------- triggers
def on_run_completed(hub, state: dict, rec: dict | None) -> None:
    """Start the automations a completed run triggers."""
    if rec is None:
        return
    held = rec["fields"].get("held_for")
    if rec["type"] == "document" and held and rec["state"] == "approved":
        co = next((r for r in hub.kernel.query(type_="change-order") if r["number"] == held), None)
        if co is not None:
            start(hub, "compile-frame", subject=co["locator"], trigger=f"{rec['number']} was approved", by="hub",
                  acting_on=f"{rec['number']} approved in run {state['id']}")
    if rec["type"] == "change-order" and rec["state"] == "approved":
        decided = next((e for e in reversed(hub.runner.track(state["id"])) if e["type"] == "gate.decided"), {})
        start(hub, "release-change-order", subject=rec["locator"], trigger=f"{rec['number']} was approved", by="hub",
              acting_on=f"approved by {decided.get('by') or 'its approver'} in run {state['id']}")
