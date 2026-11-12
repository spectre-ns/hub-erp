"""An internal audit on its record page: its findings (clause, what was expected, what was found, the evidence, the
grade, the proposed action), the quality manager's verdict on each, the actions raised from the accepted ones, and,
while it waits for the quality manager at "Accept the findings", the form that accepts or rejects each finding and
signs once (``hubproto.audit.accept_findings``)."""

from __future__ import annotations

from urllib.parse import quote

from .. import audit
from ..runner import RunError
from .forms import ShellError, text_input
from .handler import hidden_as, href, log_line, table
from .icons import icon
from .text import chip, esc, short

GRADE_CLS = {"major": "crit", "minor": "warn", "observation": "", "opportunity": "ok"}


class AuditMixin:
    def audit_route(self, method: str, r: str):
        return {"/audit-accept": self.post_audit_accept}.get(r) if method == "POST" else None

    def _evidence_html(self, refs) -> str:
        out = []
        ev = audit.Evidence(self.hub.kernel)
        for r in refs or []:
            hit = ev.resolve(r)
            if hit and hit["kind"] == "record":
                out.append(f'<a href="{href("/record/" + quote(hit["locator"], safe=":"), self.user)}">{esc(hit["ref"])}</a>')
            elif hit and hit["kind"] == "track":
                out.append(f'<a href="{href("/run/" + hit["run"], self.user)}">{esc(r)}</a>')
            else:
                out.append(f"<code>{esc(r)}</code>" + ("" if hit else " <span class='why'>(not found)</span>"))
        return ", ".join(out)

    def _audit_panel(self, rec: dict) -> str:
        if rec["type"] != "audit":
            return ""
        f = rec["fields"]
        findings = [x for x in f.get("findings") or [] if isinstance(x, dict)]
        if not findings:
            return ""
        verdicts = {d.get("finding"): d for d in f.get("finding_decisions") or [] if isinstance(d, dict)}
        raised = {r.get("finding"): r for r in f.get("actions_raised") or [] if isinstance(r, dict)}
        run = self.hub.runner.run_for_record(rec["locator"])
        pending = (run or {}).get("pending") or {}
        deciding = bool(run and run["status"] == "waiting" and pending.get("checkpoint") == audit.ACCEPT_STEP
                        and self.user in (pending.get("assignees") or []))
        criteria = audit.load_clauses(self.hub.kernel.config_dir)
        rows = []
        for i, x in enumerate(findings, 1):
            fid = x.get("id") or f"#{i}"
            v = verdicts.get(fid) or {}
            made = raised.get(fid)
            verdict = chip(v["verdict"] + "ed", "ok" if v.get("verdict") == "accept" else "crit") if v.get("verdict") else "<span class='muted'>to decide</span>"
            if made:
                verdict += " &rarr; " + self._record_link(made["record"], with_summary=False)
            if deciding and x.get("id"):
                verdict = (f"<fieldset class='inline'><legend class='sr'>{esc(fid)}</legend>"
                           f"<label><input type='radio' name='v-{esc(fid)}' value='accept' required> accept</label> "
                           f"<label><input type='radio' name='v-{esc(fid)}' value='reject'> reject</label>"
                           f"{text_input('r-' + fid, 'Reason (for a rejection)', '')}</fieldset>")
            rows.append([esc(fid), chip(x.get("grade") or x.get("severity") or "-", GRADE_CLS.get(x.get("grade") or "", "")),
                         esc(audit.clause_title(criteria, x.get("clause", "")) or x.get("clause", "")),
                         esc(short(x.get("criterion") or "", 400)), esc(short(x.get("found") or x.get("statement") or "", 600)),
                         self._evidence_html(x.get("evidence")), esc(x.get("action") or "none"), verdict])
        head = ["Finding", "Grade", "Clause", "Expected", "Found", "Evidence", "Action", "Verdict"]
        out = (f"<section class='card' id='findings'><h2 style='margin-top:0'>{icon('flag')}Findings</h2>"
               "<p class='why'>Recorded by the auditor against the organization's SOPs and the clauses they cite (an internal audit, not a "
               "certification). The quality manager accepts or rejects each one; an accepted finding with an action becomes a "
               "nonconformance or a CAPA.</p>")
        if not deciding:
            return out + table(head, rows) + "</section>"
        return out + (f"<form method='post' action='/audit-accept'>{hidden_as(self.user)}{self._once()}"
                      f"<input type='hidden' name='record' value='{esc(rec['locator'])}'>"
                      f"<input type='hidden' name='digest' value='{esc(self.hub.runner.seen_digest(run))}'>" + table(head, rows)
                      + "<div class='fields'>" + text_input("summary", "Summary of the audit", "", required=True)
                      + text_input("meaning", "What your signature means", "findings reviewed; verdicts as marked", required=True)
                      + f"</div><div class='actions'><button>{icon('check')}Sign the verdicts</button></div></form></section>")

    def post_audit_accept(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        rec = self.hub.kernel.require(self._f("record"))
        self.arg = rec["locator"]
        self._require_read(rec["folder"])
        ids = [x["id"] for x in rec["fields"].get("findings") or [] if isinstance(x, dict) and x.get("id")]
        verdicts = {fid: {"verdict": self._f(f"v-{fid}"), "reason": self._f(f"r-{fid}")} for fid in ids}
        try:
            state = audit.accept_findings(self.hub, rec["locator"], self.user, verdicts, summary=self._f("summary"),
                                          meaning=self._f("meaning"), payload_digest=self._f("digest"))
        except (ValueError, RunError) as e:
            raise ShellError(f"{rec['number']}: {e}.") from None
        log_line(f"audit-accept record={rec['number']} by={self.user} verdicts={ {k: v['verdict'] for k, v in verdicts.items()} }")
        now = self.hub.kernel.get(rec["locator"])
        raised = now["fields"].get("actions_raised") or []
        msg = (f"Signed the verdicts on {rec['number']}; it is now {now['state']}"
               + (f", and {len(raised)} action(s) were raised." if raised else "."))
        self._redirect(href("/record/" + quote(rec["locator"], safe=":"), self.user, done=msg) if state else "/")
