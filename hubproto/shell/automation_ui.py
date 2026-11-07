"""The Automation pages: the hub's own jobs (compiling a change order's Frame, releasing a change order), each step
as it runs, with its outcome in words, live while a job runs. Retry a job that stopped; compile a change order's
Frame again by hand when the release it was compiled against is no longer in force."""

from __future__ import annotations

from urllib.parse import quote

from .. import automation as auto
from .. import authz
from .handler import ShellError, href, log_line
from .icons import icon
from .queue import LIVE_JS
from .text import esc, when

STATUS = {"queued": ("waiting in the queue", "info"), "running": ("running", "work"), "success": ("succeeded", "ok"),
          "some-errors": ("stopped part way", "warn"), "no-actions": ("nothing to do", ""), "failed": ("failed", "crit")}
STEP = {"waiting": ("·", "muted"), "running": ("…", "work"), "done": ("✓", "ok"), "failed": ("✗", "crit"), "skipped": ("–", "muted")}


def _chip(status: str) -> str:
    words, cls = STATUS.get(status, (status, ""))
    return f"<span class='chip {cls}'>{esc(words)}</span>"


def _took(job: dict) -> str:
    ms = sum(int(s.get("ms") or 0) for s in job.get("steps") or [])
    return f"{ms / 1000:.1f} s" if ms >= 1000 else (f"{ms} ms" if ms else "")


class AutomationMixin:
    def _automation_member(self) -> None:
        self._need_release()
        if not self._is_member():
            raise ShellError("Only members of the organization see the hub's automations.", 403)

    def _automation_may_run(self, job_or_subject) -> bool:
        """Who may retry a job or start a compile by hand: the change order's requester, a document controller or a policy owner."""
        subject = job_or_subject["subject"] if isinstance(job_or_subject, dict) else job_or_subject
        rec = self.hub.kernel.get(subject) if subject else None
        roles = authz.roles_of(self.bundle, self.user) if self.bundle else []
        return bool(rec) and (self.user == rec["fields"].get("requested_by") or "document-controller" in roles or "policy-owner" in roles)

    def _automation_readable(self, job: dict) -> bool:
        rec = self.hub.kernel.get(job.get("subject") or "")
        return rec is None or self._can_read(rec["folder"])["allowed"]

    def _automation_live(self, jobs: list[dict]) -> str:
        url = href("/automation-status", self.user, job=",".join(j["id"] for j in jobs[:50]))
        stamp = "|".join(f"{j['id']}:{j.get('updated_at', '')}" for j in jobs[:50])
        n_open = sum(1 for j in jobs if j["status"] in ("queued", "running"))
        return (f"<span id='live' hidden data-url='{esc(url)}' data-stamp='{esc(stamp)}' data-open='{n_open}'></span>"
                f"<span id='live-say' class='vh' role='status' aria-live='polite'></span><script>{LIVE_JS}</script>")

    def _automation_line(self, rec: dict) -> str:
        """On a change order: its latest automation job, live, with Retry or Compile again."""
        jobs = self.hub.automation.of(rec["locator"])
        out = ""
        if jobs:
            j = jobs[0]
            cur = next((s for s in j["steps"] if s["status"] == "running"), None)
            n = next((i for i, s in enumerate(j["steps"], 1) if s is cur), 0)
            out = (f"<p class='automation-line' id='automation-{esc(rec['number'])}' data-live>{icon('activity')}<a href='{href('/automation/' + j['id'], self.user)}'>"
                   f"{esc(j['title'])}</a> {_chip(j['status'])}"
                   + (f" <span class='why'>step {n} of {len(j['steps'])}: {esc(cur['name'])}</span>" if cur else "")
                   + (f" <span class='why'>{esc(j.get('outcome') or '')}</span>" if j["status"] in ("some-errors", "failed", "no-actions") else "")
                   + (f" <span class='why'>{esc(when(j.get('finished_at') or j['created_at']))}</span>" if j["status"] not in ("queued", "running") else ""))
            if j["status"] in ("some-errors", "failed") and self._automation_may_run(j):
                out += (f" <form class='inline' method='post' action='/automation-retry'><input type='hidden' name='as' value='{esc(self.user)}'>"
                        f"<input type='hidden' name='job' value='{esc(j['id'])}'><button class='ghost'>{icon('retry')}Retry</button></form>")
            out += "</p>"
            out += self._automation_live(jobs[:1])
        from .. import eco

        if rec["type"] == "change-order" and not eco.finished(rec) and self._automation_may_run(rec["locator"]):
            why = eco.frame_reasons(self.hub, rec)
            again = [r for r in why if "compile it again" in r or "not been compiled" in r]
            if again and eco.approved_of(self.hub, rec):
                out += (f"<form class='inline' method='post' action='/automation-start'><input type='hidden' name='as' value='{esc(self.user)}'>"
                        f"<input type='hidden' name='automation' value='compile-frame'><input type='hidden' name='subject' value='{esc(rec['locator'])}'>"
                        f"<button class='secondary'>{icon('retry')}Compile the Frame again</button></form> <span class='why'>{esc(again[0])}</span>")
        return out

    # ------------------------------------------------------------ pages
    def page_automation(self):
        self._automation_member()
        user = self.user
        if self.arg:
            return self._automation_job(self.arg)
        status = self.q.get("status", [""])[0]
        jobs = [j for j in self.hub.automation.all() if self._automation_readable(j)]
        shown = [j for j in jobs if not status or j["status"] == status][:100]
        rows = "".join(
            f"<tr><td><a href='{href('/automation/' + j['id'], user)}'>{esc(j['title'])}</a></td>"
            f"<td><a href='{href('/record/' + quote(j['subject']), user)}'>{esc(j.get('subject_number') or '')}</a></td>"
            f"<td>{_chip(j['status'])}" + (lambda cur: f" <span class='why'>{esc(cur['name'])}</span>" if cur else "")(next((s for s in j['steps'] if s['status'] == 'running'), None)) + "</td>"
            f"<td class='why'>{esc(j.get('trigger') or '')}</td><td class='num'>{esc(when(j['created_at']))}</td><td class='num'>{esc(_took(j))}</td></tr>"
            for j in shown)
        chips = "".join(f"<a href='{href('/automation', user, **({'status': k} if k else {}))}'{' aria-current=\"true\"' if status == k else ''}>{esc(v)}</a>"
                        for k, v in (("", "all"), ("running", "running"), ("queued", "waiting"), ("some-errors", "stopped part way"), ("failed", "failed"), ("success", "succeeded")))
        defs = "".join(f"<li><b>{esc(a['title'])}</b> <span class='why'>when {esc(a['trigger'])}: {esc(' → '.join(a['steps']))}</span></li>"
                       for a in auto.AUTOMATIONS.values())
        body = (f"<h1>{icon('activity')}Automation</h1><p class='why'>The hub's own jobs, each step as it runs. An automation acts only on what "
                f"people approved, and stops (and says why) when an approval is missing or out of date.</p><ul class='notes'>{defs}</ul>"
                f"<div class='chips' role='group' aria-label='Show'>{chips}</div><section id='jobs' data-live><div class='tbl'><table><thead><tr><th>Job</th><th>On</th>"
                f"<th>Status</th><th>Why it ran</th><th>Started</th><th>Took</th></tr></thead><tbody>"
                + (rows or "<tr><td colspan='6' class='muted'>No jobs yet.</td></tr>") + "</tbody></table></div></section>" + self._automation_live(jobs))
        self._page("Automation", body, crumbs=[("Automation", None)])

    def _automation_job(self, job_id: str):
        job = self.hub.automation.load(job_id)
        if job is None or not self._automation_readable(job):
            raise ShellError("There is no such automation job, or it is about something you may not read.", 404)
        user = self.user
        steps = ""
        for i, s in enumerate(job["steps"], 1):
            mark, cls = STEP.get(s["status"], ("·", ""))
            steps += (f"<li class='step {esc(s['status'])}'><span class='mark {cls}' aria-hidden='true'>{mark}</span><div><b>{i}. {esc(s['name'])}</b> "
                      f"<span class='why'>{esc(s['status'])}" + (f", {int(s['ms'])} ms" if s.get("ms") else "") + "</span>"
                      + (f"<div class='msg'>{esc(s['detail'])}</div>" if s.get("detail") else "") + "</div></li>")
        retry = ""
        if job["status"] in ("some-errors", "failed") and self._automation_may_run(job):
            retry = (f"<form method='post' action='/automation-retry'><input type='hidden' name='as' value='{esc(user)}'><input type='hidden' name='job' value='{esc(job['id'])}'>"
                     f"<button>{icon('retry')}Retry from the step that stopped</button></form>")
        facts = (f"<dl class='kv'><dt>On</dt><dd><a href='{href('/record/' + quote(job['subject']), user)}'>{esc(job.get('subject_number') or '')}</a></dd>"
                 f"<dt>Why it ran</dt><dd>{esc(job.get('trigger') or '')}</dd>"
                 + (f"<dt>Acts on</dt><dd>{esc(job['acting_on'])}</dd>" if job.get("acting_on") else "")
                 + f"<dt>Started by</dt><dd>{esc(job.get('started_by') or 'hub')}" + (f"; retried by {esc(job['retried_by'])}" if job.get("retried_by") else "") + "</dd>"
                 f"<dt>Queued</dt><dd>{esc(when(job['created_at']))}</dd>" + (f"<dt>Finished</dt><dd>{esc(when(job['finished_at']))}</dd>" if job.get("finished_at") else "")
                 + f"<dt>Attempts</dt><dd>{int(job.get('attempt') or 0)}</dd>"
                 + (f"<dt>Under policy</dt><dd><a href='{href('/release/' + job['release'], user)}'>release {esc(job['release'][:12])}</a></dd>" if job.get("release") else "")
                 + "</dl>")
        body = (f"<h1>{icon('activity')}{esc(job['title'])} {_chip(job['status'])}</h1><section id='job-{esc(job['id'])}' data-live>{facts}"
                + (f"<p class='status{' crit' if job['status'] in ('failed', 'some-errors') else ''}'>{esc(job['outcome'])}</p>" if job.get("outcome") else "")
                + f"<ol class='job-steps'>{steps}</ol>{retry}</section>" + self._automation_live([job]))
        self._page(job["title"], body, crumbs=[("Automation", href("/automation", user)), (job.get("subject_number") or job["id"], None)])

    def page_automation_status(self):
        self._automation_member()
        ids = [x for x in (self.q.get("job", [""])[0] or "").split(",") if x]
        jobs = [j for j in (self.hub.automation.load(i) for i in ids) if j and self._automation_readable(j)] if ids else \
            [j for j in self.hub.automation.all() if self._automation_readable(j)]
        return self._json({"stamp": "|".join(f"{j['id']}:{j.get('updated_at', '')}" for j in jobs[:50]),
                           "open": sum(1 for j in jobs if j["status"] in ("queued", "running"))})

    # ------------------------------------------------------------ actions
    def post_automation_retry(self):
        self._automation_member()
        job = self.hub.automation.load(self._f("job"))
        if job is None:
            raise ShellError("There is no such automation job.", 404)
        if not self._automation_may_run(job):
            raise ShellError("Only the change order's requester, a document controller or a policy owner retries its automation.", 403)
        try:
            job = auto.retry(self.hub, job["id"], by=self.user)
        except ValueError as e:
            raise ShellError(str(e)) from None
        log_line(f"automation retry {job['id']} by={self.user}")
        self._redirect(href("/automation/" + job["id"], self.user, done=f"{job['title']} is running again from the step that stopped."))

    def post_automation_start(self):
        self._automation_member()
        name, subject = self._f("automation"), self._f("subject")
        if name != "compile-frame":
            raise ShellError("Only compiling a change order's Frame is started by hand; a release starts when the change order is approved.", 400)
        rec = self.hub.kernel.get(subject)
        if rec is None or rec["type"] != "change-order":
            raise ShellError("That is not a change order.", 404)
        if not self._automation_may_run(subject):
            raise ShellError("Only the change order's requester, a document controller or a policy owner compiles its Frame.", 403)
        job = auto.start(self.hub, "compile-frame", subject=subject, trigger=f"{self.user} asked to compile it again", by=self.user)
        log_line(f"automation start compile-frame {rec['number']} by={self.user}")
        self._redirect(href("/automation/" + job["id"], self.user, done="Compiling the Frame."))
