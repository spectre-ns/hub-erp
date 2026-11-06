"""The inbox as a queue: one summary row per item, one card open at a time, the decision first on the card."""

from __future__ import annotations

import re

from .. import authz
from ..runner import RunError
from .forms import file_input, text_input, widget
from .handler import details, hidden_as, href, kv, table
from .pages import next_state_for
from .text import (BLOB_FIELDS, MEANING_EXAMPLES, NOTIFICATION_KINDS, chip, day, esc, join_words, money, notification_sentence,
                   qty, short, state_name, summary_of_record, tag, type_name, value_text, when)

PAGE_SIZE = 25


class InboxMixin:
    # --------------------------------------------------------------- lookups
    def _payload_of(self, run: dict) -> dict:
        step = run["steps"].get((run.get("pending") or {}).get("step", ""), {})
        payload = (step.get("envelope") or {}).get("payload") or {}
        return {k: v for k, v in payload.items() if k not in BLOB_FIELDS}

    def _guard_problems(self, run: dict, only_step: str | None = None) -> list[tuple[str, str, str]]:
        """(severity, check, detail) from every step's envelope (or one step's)."""
        out = []
        for sid, rec in run["steps"].items():
            if only_step and sid != only_step:
                continue
            env = rec.get("envelope") or {}
            out += [(p.get("severity", ""), p.get("check", ""), p.get("detail", "")) for p in env.get("problems", [])]
            if env.get("error"):
                out.append(("error", env["error"].get("code", ""), env["error"].get("detail", "")))
        return out

    def _key_fact(self, rec: dict | None, payload: dict) -> str:
        """The one number or sentence an approver scans for: the amount, the statement, the severity."""
        if rec is None:
            return ""
        f = {**payload, **rec["fields"]}
        if f.get("amount") is not None:
            return money(f["amount"], f.get("currency")) + (f" from {self._supplier_name(f['supplier'])}" if f.get("supplier") else "")
        if rec["type"] == "requirement":
            return short(f.get("statement") or "", 100)
        if rec["type"] == "ncr":
            return f"severity {f.get('severity')}" if f.get("severity") else ""
        if rec["type"] == "receipt":
            lines = f.get("lines") or []
            return f"{len(lines)} line{'s' if len(lines) != 1 else ''}"
        if rec["type"] == "design":
            return f"change class {f.get('change_class')}" if f.get("change_class") else ""
        if rec["type"] == "document":
            return f"{f.get('change_type') or ''} revision {f.get('revision', '')}.{f.get('minor', 0)}".strip()
        return ""

    def _dup_key(self, rec: dict | None) -> tuple | None:
        if rec is None:
            return None
        f = rec["fields"]

        def text(v) -> str:  # fields may hold anything a form or the JSON box put there
            return " ".join(str(x) for x in v) if isinstance(v, (list, tuple)) else ("" if v is None else str(v))

        if f.get("amount") not in (None, ""):
            try:
                return (rec["type"], text(f.get("supplier")), float(f["amount"]))
            except (TypeError, ValueError):
                return None
        title = text(f.get("title") or f.get("statement")).strip().lower()
        return (rec["type"], title, text(f.get("lot"))) if title else None

    def _duplicates(self, waiting: list[dict]) -> dict[str, list[str]]:
        """Run id -> numbers of other waiting items with the same type, supplier and amount (or title)."""
        keys: dict[tuple, list[tuple[str, str]]] = {}
        recs: dict[str, dict | None] = {}
        for r in waiting:
            recs[r["id"]] = self.hub.kernel.get(r["record"]) if r.get("record") else None
            k = self._dup_key(recs[r["id"]])
            if k:
                keys.setdefault(k, []).append((r["id"], recs[r["id"]]["number"]))
        out: dict[str, list[str]] = {}
        for members in keys.values():
            if len(members) > 1:
                for rid, _ in members:
                    out[rid] = [n for i, n in members if i != rid]
        return out

    def _blocked(self, run: dict, rec: dict | None) -> str:
        """Why Approve cannot work right now (HTML), or '' when it can."""
        if rec is None:
            return ""
        if rec["hold"]:
            return f"On hold: {esc(short(rec.get('hold_reason') or 'no reason given', 300))}. An approver must release the hold on the item's page first."
        if rec["type"] == "design":
            trace = rec["fields"].get("requirement_trace") or []
            open_reqs = [t for t in trace if t.get("state") != "accepted"]
            if open_reqs:
                links = ", ".join(self._record_link(f"rec:requirement:{t['requirement']}", with_summary=False) for t in open_reqs)
                return f"Blocked: accept {links} first (requirements not yet accepted by the design authority)."
            if not trace and rec["fields"].get("requirements_accepted") is False:
                return "Blocked: the design traces to no requirement; add user needs or link accepted requirements first."
        return ""

    def _policy_line(self, run: dict, rec: dict | None) -> str:
        """The routing sentence, the threshold band around this item, and what the person is asked to judge, in one line."""
        bundle = self.bundle or {}
        p = run["pending"]
        parts = [f"Routed to you by {self._rule_html(p.get('route_rule'))}"]
        f = rec["fields"] if rec else {}
        amount = f.get("amount")
        cur = f.get("currency") or "USD"
        bands = []
        for r in bundle.get("routing", []):
            c = r.get("condition") or {}
            if r["process"] == run["process"] and r["step"] == p.get("checkpoint") and c.get("field") == "amount":
                who = r["assignee"].partition(":")[2].replace("-", " ")
                bands.append(f"{'at or under' if c['op'] == '<=' else 'under' if c['op'] == '<' else 'over' if c['op'] == '>' else 'at or over'} "
                             f"{money(c['value'], c.get('currency') or cur)} to {'the ' + who + ' cog' if r['assignee'].startswith('cog:') else 'the ' + who}")
        crit = [c for c in bundle.get("approval_criteria", []) if c["process"] == run["process"]]
        for c in crit:
            cog = c["approver"].partition(":")[2]
            limit = f" up to {money(c['max_amount'], c.get('currency') or cur)}" if c.get("max_amount") is not None else ""
            conds = [k for k in c.get("conditions", [])]
            met = f.get("conditions") or {}
            failed = [k for k in conds if met.get(k) is False]
            words = {"match_within_tolerance": "the three-way match is within tolerance", "supplier_approved": "the supplier is approved",
                     "risk_low": "the risk is low", "change_minor": "the change is minor", "item_known": "the item is known",
                     "risk_not_high": "the risk is not high", "requirements_accepted": "every requirement is accepted",
                     "bom_resolved": "the bill of materials is resolved", "design_checks_clean": "the design checks are clean",
                     "no_open_ncr": "no open nonconformance exists"}
            s = f"the {cog} cog may approve{limit} when {join_words(words.get(k, k) for k in conds)} ({self._rule_link(c.get('source'))})"
            if failed:
                s += f"; here, not met: {esc(join_words(words.get(k, k) for k in failed))}"
            parts.append(s)
        if bands:
            parts.append("amounts " + esc("; ".join(bands)))
        if amount is not None:
            parts.append(f"this item is <b>{esc(money(amount, cur))}</b>")
        ask = {"bill": "you are asked whether the bill may be posted as it stands", "purchase-request": "you are asked whether this purchase may go ahead at this price and supplier",
               "ncr": "you are asked to disposition the nonconformance", "design": "you are asked whether the design may be released",
               "document": "you are asked whether the document may become effective", "training": "you confirm you have read the document",
               "task": "you confirm the task is done", "receipt": "you confirm what was received", "requirement": "you are asked whether the requirement is accepted",
               "capa": "you verify the actions taken", "audit": "you accept or record the findings"}.get(rec["type"] if rec else "", "")
        if ask:
            parts.append(ask)
        return "; ".join(parts) + "."

    def _escalation_html(self, run: dict) -> str:
        p = run["pending"]
        if not p.get("escalated"):
            return ""
        ev = next((e for e in reversed(self.hub.runner.track(run["id"])) if e["type"] in ("gate.escalated", "gate.overdue")), None)
        rule = (ev or {}).get("route_rule")
        to = ", ".join((ev or {}).get("assignees") or []) or "the same people"
        return (f"<p class='why'>{chip('escalated', 'warn')} to {esc(to)} on {esc(day(p['escalated']))}"
                + (f" by {self._rule_html(rule)}" if rule else "") + f"; originally routed by {self._rule_html(p.get('route_rule'))}.</p>")

    def _decision_inputs(self, run: dict, rec: dict | None, values: dict | None = None) -> tuple[str, list[str], list[str]]:
        """Form controls for a decision: the next state's required-but-missing fields (required) and the type's
        other decision fields (optional); never a system field."""
        if rec is None:
            return "", [], []
        allowed = self.hub.runner.allowed_decision_fields(run)
        td = self.hub.kernel.types.get(rec["type"], {})
        system = set(td.get("system_fields") or [])
        nxt = next_state_for(run.get("workflow"), run["pending"]["checkpoint"])
        missing = [x for x in self.hub.kernel.required_missing(rec, nxt) if x not in system] if nxt else []
        optional = [x for x in allowed if x not in missing]
        people = self._people()
        values = values or {}
        html_ = "".join(widget(x, td.get("fields", {}).get(x), values.get(x, ""), required=True, people=people, options=self._picker_options(x, td.get("fields", {}).get(x)),
                               error=self._field_error(x) if values else None) for x in missing)
        opt = "".join(widget(x, td.get("fields", {}).get(x), values.get(x, rec["fields"].get(x, "")), people=people, options=self._picker_options(x, td.get("fields", {}).get(x)),
                             error=self._field_error(x) if values else None) for x in optional)
        if opt:
            html_ += details(f"Also set: {esc(', '.join(optional))}", f"<div class='fields'>{opt}</div>", open_=bool(values and any(self._field_error(x) for x in optional)))
        return html_, missing, optional

    # ---------------------------------------------------------------- cards
    def _card_title(self, run: dict, rec: dict | None) -> str:
        p = run["pending"]
        return f"{p['title']}: {rec['number'] + ' ' + summary_of_record(rec) if rec else run['process']}"

    def _queue_row(self, run: dict, dups: dict[str, list[str]], open_: bool) -> str:
        user = self.user
        p = run["pending"]
        rec = self.hub.kernel.get(run["record"]) if run.get("record") else None
        if open_:
            return f'<li class="open" id="r-{esc(run["id"])}">{self._decision_card(run, dups=dups)}</li>'
        fact = self._key_fact(rec, self._payload_of(run))
        f = rec["fields"] if rec else {}
        risk = chip(f["risk"], title="risk") if f.get("risk") else ""
        others = [a for a in p["assignees"] if a != user]
        meta = [tag(type_name(rec["type"]) if rec else run["process"]), esc(fact) if fact else "", risk, self._due_html(p.get("due")),
                chip("escalated", "warn") if p.get("escalated") else "", chip("on hold", "crit") if rec and rec["hold"] else "",
                f"also with {esc(', '.join(others))}" if others else "",
                f"<span class='chip warn'>duplicate of {esc(', '.join(dups[run['id']]))}?</span>" if run["id"] in dups else ""]
        link = href("/inbox", user, open=run["id"], type=self.q.get("type", [""])[0], page=self.q.get("page", [""])[0]) + f"#r-{run['id']}"
        return (f'<li id="r-{esc(run["id"])}"><h3><a href="{link}">{esc(self._card_title(run, rec))}</a></h3>'
                f'<span class="meta">{" ".join(m for m in meta if m)}</span></li>')

    def _decision_card(self, r: dict, standalone: bool = False, dups: dict | None = None) -> str:
        user = self.user
        p = r["pending"]
        rec = self.hub.kernel.get(r["record"]) if r.get("record") else None
        rt = rec["type"] if rec else self._record_type_of(r)
        nxt = next_state_for(r.get("workflow"), p["checkpoint"])
        on_reject = next((t.get("on_reject") for t in (r.get("workflow") or {}).get("transitions", []) if t["kind"] == "human" and t["step"] == p["checkpoint"]), "rejected")
        mine = self.err is not None and self.vals.get("run") == r["id"] and self.route == "/decide"
        values = dict(self.vals) if mine else {}
        candidates = ["meaning", "reason"] + (self.hub.runner.allowed_decision_fields(r) if rec else [])
        err = self._form_error(candidates) if mine else ""  # attributes the message to its field before the controls render
        inputs, missing, optional = self._decision_inputs(r, rec, values)
        problems = self._guard_problems(r)
        brief = self._brief(rec, self._payload_of(r)) if rec else "<span class='muted'>no item</span>"
        blocked = self._blocked(r, rec)
        f = rec["fields"] if rec else {}
        facts = []
        if f.get("amount") is not None:
            facts.append(f"<b>{esc(money(f['amount'], f.get('currency')))}</b>")
        if f.get("risk"):
            facts.append(f"risk {chip(f['risk'])}")
        if "supplier_approved" in f:
            facts.append(f"supplier approved {chip('yes' if f['supplier_approved'] else 'no')}")
        m = f.get("match") or {}
        if m:
            facts.append(chip("within tolerance" if m.get("within_tolerance") else "outside tolerance") + (" " + chip("not received") if not m.get("received") else ""))
        if p.get("due"):
            facts.append(self._due_html(p["due"]))
        others = [a for a in p["assignees"] if a != user]
        if others:
            facts.append(f"<span class='why'>also with {esc(', '.join(others))}</span>")
        if dups and r["id"] in dups:
            facts.append(f"<span class='chip warn'>duplicate of {esc(', '.join(dups[r['id']]))}?</span>")
        head = f"<h3 id='h-{esc(r['id'])}'>{esc(self._card_title(r, rec))}</h3>" if not standalone else f"<h2 id='h-{esc(r['id'])}' style='margin-top:0;border:0'>{esc(p['title'])}: waiting for you</h2>"
        meta = f"<p class='why'>{tag(type_name(rt))} started by {esc(r['owner'])} on {esc(when(r['created_at']))} &middot; {self._run_link(r)}</p>"
        policy = f"<p class='why'>{self._policy_line(r, rec)}</p>" + self._escalation_html(r)
        checks = ""
        if problems:
            checks = "<div class='card quiet'><b>Checks:</b><ul>" + "".join(f"<li>{chip(sev if sev != 'error' else 'error')} {esc(check)}: {esc(detail)}</li>" for sev, check, detail in problems) + "</ul></div>"
        blocked_html = f"<div class='card crit' role='status'><b>{blocked}</b> Approve is disabled until then.</div>" if blocked else ""
        needs = f"<p class='why'>To approve, also give: {esc(', '.join(missing))}.</p>" if missing else ""
        examples = MEANING_EXAMPLES.get(rt) or ("reviewed and approved as presented", "approved with the stated condition")
        meaning_hint = (f"Say in your own words what you are confirming. It is stored with your signature and shown to {esc(r['owner'])}. "
                        f"For example: “{esc(examples[0])}” or “{esc(examples[1])}”.")
        digest = r.get("seen_digest") or self.hub.runner.seen_digest(r)
        leads = f"Approval moves it to {self._state_chip(nxt, r.get('workflow'))}" if nxt else "Approval completes it"
        rej = (f"Reject returns it to {esc(r['owner'])} for rework" if on_reject == "returned" else
               f"Reject rewinds it to {esc(state_name(on_reject, r.get('workflow')))}" if on_reject and on_reject.startswith("awaiting") else
               "Reject closes it as rejected; nothing more can be done with it")
        approve_form = f"""{err}<form method="post" action="/decide" aria-labelledby="h-{esc(r['id'])}">{hidden_as(user)}<input type="hidden" name="run" value="{esc(r['id'])}"><input type="hidden" name="digest" value="{esc(digest)}"><input type="hidden" name="verdict" value="approve">
{needs}<div class="fields">{inputs}</div>
<div class="fields">{text_input("meaning", "Meaning of your signature", values.get("meaning", ""), required=True, textarea=True, hint="", error=self._field_error("meaning") if mine else None)}</div>
<p class="why">{meaning_hint}</p>
<div class="actions"><label><input type="checkbox" name="confirm" value="1" required{' checked' if values.get('confirm') else ''}> I have read the item and the checks above</label>
<button{' disabled' if blocked else ''}>Approve {esc(rec['number'] if rec else '')}</button> <span class="why">{leads}.</span></div></form>"""
        reject_form = f"""<div class="card quiet"><h4>Reject instead</h4><p class="why">{rej}. {esc(r['owner'])} will see your reason.</p>
<form method="post" action="/decide" aria-label="Reject {esc(rec['number'] if rec else '')}">{hidden_as(user)}<input type="hidden" name="run" value="{esc(r['id'])}"><input type="hidden" name="digest" value="{esc(digest)}"><input type="hidden" name="verdict" value="reject">
<div class="fields">{text_input("reason", "Reason for rejecting", values.get("reason", "") if values.get("verdict") == "reject" else "", required=True, textarea=True, hint="", error=self._field_error("reason") if mine else None)}</div>
<div class="actions"><button class="reject" formnovalidate>Reject {esc(rec['number'] if rec else '')}</button></div></form></div>"""
        return (f"<article class='card decide' aria-labelledby='h-{esc(r['id'])}'>{head}<div class='facts'>{' '.join(facts)}</div>{meta}{policy}{blocked_html}{checks}"
                f"{approve_form}<h4>The item</h4>{brief}{reject_form}</article>")

    def _brief(self, rec: dict, payload: dict) -> str:
        """A readable summary of a record for the person deciding on it: key fields by type, small tables for the rest."""
        f = {k: v for k, v in payload.items() if k not in ("id", "folder", "locator", "state")}
        f.update(rec["fields"])
        t = rec["type"]
        rows: list[tuple[str, str]] = []
        extra = ""

        def add(label, key=None, value=None, link=False):
            v = f.get(key) if key else value
            if v in (None, "", [], {}):
                return
            rows.append((label, self._record_link(v) if link else esc(value_text(key or label, v, f) if key else v)))

        def risk():
            if f.get("risk"):
                reasons = f.get("risk_reasons") or []
                rows.append(("Risk", chip(f["risk"]) + (" " + esc("; ".join(map(str, reasons))) if reasons else "")))

        if t == "bill":
            add("Supplier", value=self._supplier_name(f.get("supplier")) if f.get("supplier") else None)
            add("Amount", value=money(f["amount"], f.get("currency")) if f.get("amount") is not None else None)
            add("Invoice date", "date")
            add("Purchase order", "po_ref")
            add("Description", "description")
            m = f.get("match") or {}
            if m:
                verdict = "within tolerance" if m.get("within_tolerance") else "outside tolerance"
                parts = [f"variance {m.get('variance_percent')}%" if m.get("variance_percent") is not None else "",
                         "PO found" if m.get("po_found") else "no PO", "goods received" if m.get("received") else "goods not received",
                         "supplier approved" if m.get("supplier_approved") else "supplier not approved"]
                rows.append(("Match result", chip(verdict) + " " + esc(", ".join(p for p in parts if p))))
                add("Billed to date", value=money(m.get("billed_to_date"), f.get("currency")) if m.get("billed_to_date") is not None else None)
            risk()
        elif t == "purchase-request":
            add("Title", "title")
            add("Item", "item", link=True)
            add("Manufacturer part number", "mpn")
            add("Quantity", value=qty(f.get("quantity"), f.get("unit")) if f.get("quantity") is not None else None)
            add("Unit cost", value=money(f.get("unit_cost"), f.get("currency")) if f.get("unit_cost") is not None else None)
            add("Amount", value=money(f.get("amount"), f.get("currency")) if f.get("amount") is not None else None)
            add("Supplier", value=self._supplier_name(f.get("supplier")) if f.get("supplier") else None)
            if "supplier_approved" in f:
                rows.append(("Supplier approved", chip("yes" if f.get("supplier_approved") else "no") + " " + esc(f.get("supplier_note") or "")))
            add("Needed by", "needed_by")
            add("Reason", "reason")
            add("Design", "design", link=True)
            risk()
        elif t == "design":
            add("Title", "title")
            add("Kind", "kind")
            add("Change class", "change_class")
            add("Description", "description")
            add("Designer", "designer")
            risk()
            trace = f.get("requirement_trace") or []
            if trace:
                extra += "<h4>Requirements</h4>" + table(["Requirement", "Kind", "Statement", "State"],
                                                         [[self._record_link(f"rec:requirement:{x.get('requirement')}", with_summary=False), esc(x.get("kind")), esc(short(x.get("statement"))), chip(x.get("state"))]
                                                          for x in trace])
            elif f.get("user_needs"):
                extra += "<h4>User needs</h4><ul>" + "".join(f"<li>{esc(short(n))}</li>" for n in f["user_needs"]) + "</ul>"
            if any(k in f for k in ("bom_resolved", "bom_unresolved", "bom_lines")):
                rows.append(("Bill of materials", esc(f"{len(f.get('bom_lines') or [])} lines: {len(f.get('bom_resolved') or [])} resolved, "
                                                      f"{len(f.get('bom_unresolved') or [])} unresolved, {len(f.get('bom_unapproved') or [])} from unapproved manufacturers")))
            checks = f.get("checks") or {}
            if checks:
                rows.append(("Design checks", " ".join(f"{esc(name.upper())} " + (chip(f"{c.get('errors')} errors", "crit" if c.get("errors") else "ok") if c.get("ran") else chip("not run"))
                                                       + (f" {esc(c.get('warnings'))} warnings" if c.get("ran") and c.get("warnings") else "") for name, c in checks.items())))
            exports = f.get("exports") or []
            if exports:
                extra += "<h4>Exports</h4><ul>" + "".join(f"<li>{esc(x.get('doc_type'))}: {self._record_link(x.get('document'))}</li>" for x in exports) + "</ul>"
            if f.get("part"):
                add("Part", "part", link=True)
        elif t == "requirement":
            rows.append(("Statement", f"<b>{esc(short(f.get('statement') or '', 600))}</b>"))
            add("Kind", "kind")
            add("Source", "source")
            add("Priority", "priority")
            add("Design", "design", link=True)
            add("Acceptance criteria", "acceptance_criteria")
            add("Verification method", "verification_method")
            risk()
        elif t == "ncr":
            add("Title", "title")
            add("Description", "description")
            add("Source", "source")
            add("Severity", "severity")
            add("Containment", "containment")
            add("Lot", "lot")
            add("Quantity", "quantity")
            add("Part", "part", link=True)
            risk()
        elif t == "receipt":
            add("Purchase order", "po", link=True)
            add("Supplier", "supplier")
            lines = f.get("lines") or []
            if lines:
                extra += "<h4>Lines</h4>" + table(["Item", "Quantity received"], [[self._record_link(l.get("item")), esc(qty(l.get("quantity")))] for l in lines if isinstance(l, dict)])
            add("Location", "location")
            add("Notes", "notes")
        elif t == "document":
            add("Title", "title")
            add("Type", "doc_type")
            add("Change type", "change_type")
            add("Revision", value=f"{f.get('revision', '')}.{f.get('minor', 0)}")
            rows.append(("Document", self._record_link(rec["locator"])))
            add("Roles that must read it", "training_roles")
            risk()
        elif t == "training":
            add("Person", "person")
            add("Document", "document", link=True)
            risk()
        else:
            for k, v in sorted(f.items()):
                if k in BLOB_FIELDS or k in ("conditions",) or isinstance(v, (dict, list)) or v in (None, ""):
                    continue
                rows.append((k.replace("_", " ").capitalize(), esc(value_text(k, v, f))))
            risk()
        docs = self.hub.dms.documents_of(rec["locator"])
        if docs:
            rows.append(("Documents", ", ".join(self._record_link(d["locator"], with_summary=False) + " " + esc(d["fields"].get("title", "")) for d in docs)))
        return f'<div class="brief">{kv(rows)}{extra}</div>'

    # ----------------------------------------------------- rework and cancel
    def _failing_fields(self, run: dict, rt: str) -> dict[str, str]:
        """Field -> what the stopped step said about it, for marking the rework form."""
        from .forms import error_field

        names = [n for n, _, _ in self._start_spec(rt)]
        ev = self._failure_of(run["id"])
        out: dict[str, str] = {}
        texts = [ev.get("reason", "")] + [f"{check}: {detail}" for sev, check, detail in self._guard_problems(run, ev.get("step")) if sev == "error"]
        for text in texts:
            for piece in re.split(r";\s*", text):
                field = error_field(piece, names)
                if field and field not in out:
                    out[field] = piece.strip()
        return out

    def _failed_card(self, run: dict, rec: dict | None) -> str:
        """A stopped run: what the step said, the fields it concerns, and the rework form that retries with fixes."""
        user = self.user
        ev = self._failure_of(run["id"])
        rt = rec["type"] if rec else self._record_type_of(run)
        problems = [(sev, check, detail) for sev, check, detail in self._guard_problems(run, ev.get("step"))]
        plist = "".join(f"<li>{chip(sev if sev != 'error' else 'error')} {esc(check)}: {esc(detail)}</li>" for sev, check, detail in problems)
        head = f"<h2 style='margin-top:0;border:0'>Stopped at {esc((ev.get('step') or '?').replace('-', ' '))}</h2><p><b>{esc(ev.get('reason', 'no reason recorded'))}</b></p>" + (f"<ul>{plist}</ul>" if plist else "")
        bundle = self.bundle
        may = user == run["owner"] or (bundle and authz.check(bundle, user, "approve", run["manifest"]["object"], run["manifest"]["folder"])["allowed"])
        if not may or rec is None:
            return f"<div class='card crit' id='stopped'>{head}<p class='why'>Its owner {esc(run['owner'])}, or an approver of this item, may fix it and retry, or cancel it.</p></div>"
        failing = self._failing_fields(run, rt)
        mine = self.err is not None and self.route == "/retry" and self.vals.get("run") == run["id"]
        values = dict(self.vals) if mine else {}
        base = {k: values.get(k, rec["fields"].get(k, "")) for k, _, _ in self._start_spec(rt)}
        err = self._form_error([n for n, _, _ in self._start_spec(rt)] + ["note", "json"]) if mine else ""
        errors = {k: v for k, v in failing.items()}
        if mine and self.err and self.err[1]:
            errors[self.err[1]] = self.err[0]
        form = f"""<h3>Fix and retry</h3><p class="why">Change what the step complained about (marked in red), add files if needed, and retry from that step. You, as the owner, or an approver of this item may do this.</p>{err}
<form method="post" action="/retry" enctype="multipart/form-data">{hidden_as(user)}{self._once()}<input type="hidden" name="run" value="{esc(run['id'])}">
<div class="fields">{self._start_inputs(rt, base, required=self._required_at_start(run['process'], rt), errors=errors)}</div>
{file_input("files", "Files to attach", multiple=True)}{self._advanced_json(rt, values.get("json", ""))}
<div class="fields">{text_input("note", "What you fixed", values.get("note", ""), hint="goes into the history", error=self._field_error("note") if mine else None)}</div>
<div class="actions"><button>Fix and retry</button></div></form>"""
        return f"<div class='card crit' id='stopped'>{head}{form}{self._cancel_form(run, rec)}</div>"

    def _cancel_form(self, run: dict, rec: dict | None) -> str:
        user = self.user
        word = type_name(rec["type"]).lower() if rec else "run"
        mine = self.err is not None and self.route == "/cancel" and self.vals.get("run") == run["id"]
        err = self._form_error(["reason", "confirm"]) if mine else ""
        return f"""<details class="cancel"><summary>Cancel this {esc(word)} instead</summary>{err}<form method="post" action="/cancel">{hidden_as(user)}<input type="hidden" name="run" value="{esc(run['id'])}">
<p class="why">Cancelling ends the run; {esc(rec['number'] if rec else 'the item')} stays on file as cancelled and cannot be reopened.</p>
<div class="fields">{text_input("reason", "Reason for cancelling", self._val("reason") if mine else "", required=True, hint="", error=self._field_error("reason") if mine else None)}</div>
<div class="actions"><label><input type="checkbox" name="confirm" value="1" required> I want to cancel this {esc(word)}</label> <button class="danger">Cancel this {esc(word)}</button></div></form></details>"""

    def _resubmit_form(self, r: dict, rec: dict | None) -> str:
        user = self.user
        p = r["pending"]
        rt = rec["type"] if rec else r["workflow"]["record_type"]
        mine = self.err is not None and self.route == "/resubmit" and self.vals.get("run") == r["id"]
        values = dict(self.vals) if mine else {}
        base = {k: values.get(k, (rec["fields"] if rec else {}).get(k, "")) for k, _, _ in self._start_spec(rt)}
        err = self._form_error([n for n, _, _ in self._start_spec(rt)] + ["note", "json"]) if mine else ""
        return f"""{err}<form method="post" action="/resubmit" enctype="multipart/form-data">{hidden_as(user)}{self._once()}<input type="hidden" name="run" value="{esc(r['id'])}">
<p class="why">Rework from step <b>{esc((p.get('step') or '').replace('-', ' '))}</b>: change what needs changing (a field left as it is stays as it is; fields marked * are required), add files, and resubmit.</p>
<div class="fields">{self._start_inputs(rt, base, required=self._required_at_start(r['process'], rt), with_errors=mine)}</div>
{file_input("files", "Files to attach", multiple=True)}{self._advanced_json(rt, values.get("json", ""))}
<div class="fields">{text_input("note", "What changed", values.get("note", ""), hint="goes to the reviewer", error=self._field_error("note") if mine else None)}</div>
<div class="actions"><button>Resubmit</button></div></form>"""

    # ----------------------------------------------------------------- inbox
    def page_inbox(self, status: int = 200, stale: bool = False):
        user = self.user
        bundle = self._need_release()
        if not self._is_member():
            return self._page("Inbox", f"<h1>Inbox</h1><div class='card' role='alert'><b>{esc(user)} is not a member</b> of the organization under the release in effect; "
                                       "there is nothing in this inbox. Ask a policy owner (bob or carol) to add you to the org model.</div>")
        today = self.today
        open_id = self.q.get("open", [""])[0] or (self.vals.get("run", "") if self.err and self.route == "/decide" else "")
        type_filter = self.q.get("type", [""])[0]
        try:
            page = max(1, int(self.q.get("page", ["1"])[0] or 1))
        except ValueError:
            page = 1
        waiting = self._pending(user, "decision")
        waiting.sort(key=lambda r: (0 if (r["pending"].get("due") and r["pending"]["due"] < today) else 1, r["pending"].get("due") or "9999", r["created_at"]))
        types = sorted({self._record_type_of(r) for r in waiting})
        if type_filter:
            waiting = [r for r in waiting if self._record_type_of(r) == type_filter]
        dups = self._duplicates(waiting)
        body = "<h1>Inbox</h1>"
        if stale:
            body += self._stale_card(open_id)
        body += f"<h2 id='forme'>Waiting for me <span class='muted'>{len(waiting)}</span></h2>"
        if types and len(types) > 1 or type_filter:
            opts = "".join(f'<option value="{esc(t)}"{" selected" if t == type_filter else ""}>{esc(type_name(t))}</option>' for t in types)
            body += f"""<form method="get" action="/inbox" class="card">{hidden_as(user)}<div class="fields"><label class="f"><span>Show only</span><select name="type"><option value="">every type</option>{opts}</select></label></div><div class="actions"><button class="secondary">Filter</button></div></form>"""
        if not waiting:
            body += "<p class='muted'>Nothing is waiting for you.</p>"
        else:
            if open_id and open_id not in {r["id"] for r in waiting[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]}:
                ids = [r["id"] for r in waiting]
                if open_id in ids:
                    page = ids.index(open_id) // PAGE_SIZE + 1
            total_pages = (len(waiting) + PAGE_SIZE - 1) // PAGE_SIZE
            shown = waiting[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
            body += "<p class='why'>Overdue and soonest-due first. Open one item at a time; the decision comes first on the open card.</p><ol class='queue'>"
            body += "".join(self._queue_row(r, dups, open_=(r["id"] == open_id)) for r in shown) + "</ol>"
            if total_pages > 1:
                pages = " ".join(f"<b>{n}</b>" if n == page else f'<a href="{href("/inbox", user, page=n, type=type_filter)}#forme">{n}</a>' for n in range(1, total_pages + 1))
                body += f"<p class='why'>Page {page} of {total_pages} ({PAGE_SIZE} per page): {pages}</p>"
        returned = self._pending(user, "resubmit")
        failed = self._failed_for(user)
        body += f"<h2 id='mine'>Returned or stopped, mine <span class='muted'>{len(returned) + len(failed)}</span></h2>"
        if not returned and not failed:
            body += "<p class='muted'>Nothing of yours needs rework.</p>"
        for r in returned:
            p = r["pending"]
            rec = self.hub.kernel.get(r["record"]) if r.get("record") else None
            body += f"""<article class="card warn" aria-labelledby="ret-{esc(r['id'])}"><h3 id="ret-{esc(r['id'])}">Returned for rework: {self._record_link(r['record']) if rec else esc(r['process'])}</h3>
<p><b>Reason:</b> {esc(p.get('reason') or '-')} <span class="why">&middot; {self._run_link(r)}</span></p>{self._resubmit_form(r, rec)}{self._cancel_form(r, rec)}</article>"""
        for r in sorted(failed, key=lambda x: x["created_at"], reverse=True):
            rec = self.hub.kernel.get(r["record"]) if r.get("record") else None
            ev = self._failure_of(r["id"])
            body += f"""<article class="card crit" aria-labelledby="f-{esc(r['id'])}"><h3 id="f-{esc(r['id'])}">Stopped: {self._record_link(r['record']) if rec else esc(r['process'])}</h3>
<p class="why">{tag(type_name(rec['type']) if rec else r['process'])} stopped on {esc(when(ev.get('at', r['created_at'])))} at {esc((ev.get('step') or '?').replace('-', ' '))} &middot; {self._run_link(r)}</p>
{details("Fix and retry", self._failed_card(r, rec), open_=bool(self.err and self.vals.get("run") == r["id"]))}</article>"""
        others = self._waiting_on_others(user)
        body += f"<h2 id='others'>Waiting on others (mine) <span class='muted'>{len(others)}</span></h2>"
        if not others:
            body += "<p class='muted'>Nothing of yours is waiting on someone else.</p>"
        else:
            rows = []
            for r in sorted(others, key=lambda x: x["pending"].get("due") or "9999"):
                p = r["pending"]
                rows.append([self._record_link(r["record"]) if r.get("record") else esc(r["process"]), esc(p["title"]), esc(", ".join(p["assignees"])),
                             self._due_html(p.get("due")), self._run_link(r)])
            body += table(["Item", "Waiting at", "With", "Due", "Run"], rows)
        msgs = list(reversed(self.hub.inbox.for_principal(user)))
        notes = [m for m in msgs if m["kind"] in NOTIFICATION_KINDS]
        messages = [m for m in msgs if m["kind"] not in NOTIFICATION_KINDS and m["kind"] != "approval"]
        body += f"<h2 id='notifications'>Notifications <span class='muted'>{len(notes)}</span></h2>"
        if not notes:
            body += "<p class='muted'>No escalations, stops, returns or automatic decisions.</p>"
        else:
            items = "".join(f"<li>{notification_sentence(m, self._number_link)} <span class='muted'>{esc(when(m['at']))}</span>{self._message_links(m)}</li>" for m in notes[:10])
            more = "".join(f"<li>{notification_sentence(m, self._number_link)} <span class='muted'>{esc(when(m['at']))}</span>{self._message_links(m)}</li>" for m in notes[10:])
            body += f"<ul class='notes'>{items}</ul>" + (details(f"{len(notes) - 10} older notifications", f"<ul class='notes'>{more}</ul>") if more else "")
        body += f"<h2 id='messages'>Messages <span class='muted'>{len(messages)}</span></h2>"
        if not messages:
            body += "<p class='muted'>No messages.</p>"
        for m in messages[:50]:
            sender = esc(m["from"]) + (" (automatic)" if bundle["members"].get(m["from"], {}).get("type") == "cog" else "")
            body += (f"<article class='card quiet'><h3>{esc(short(m['subject'], 160))}</h3><p class='why'>from {sender} on {esc(when(m['at']))}"
                     f"{' by email' if m['channel'] == 'email' else ''}</p><p class='msg'>{self._human(short(m['body'], 1200))}</p>{self._message_links(m)}")
            if m.get("attachments"):
                body += f"<div class='why'>attachments: {esc(', '.join(a.get('name', '') for a in m['attachments']))}</div>"
            body += "</article>"
        sent = [m for m in reversed(self.hub.inbox.all()) if m["from"] == user][:20]
        if sent:
            body += details("Sent by me", "<ul>" + "".join(f"<li>to {esc(m['to'])}: {esc(short(m['subject'], 120))} <span class='muted'>{esc(when(m['at']))}</span></li>" for m in sent) + "</ul>")
        body += self._message_form()
        self._page("Inbox", body, status=status, crumbs=[("Inbox", None)])

    def _stale_card(self, run_id: str) -> str:
        """The card an approver pressed was stale: say what happened to the item instead of an error page."""
        msg = self.err[0] if self.err else ""
        try:
            run = self.hub.runner.load(run_id)
        except RunError:
            return f"<div class='card crit' role='alert'><p>{esc(msg)}</p></div>"
        rec = self.hub.kernel.get(run["record"]) if run.get("record") else None
        link = self._record_link(run["record"]) if rec else self._run_link(run)
        if run["status"] == "waiting":
            self.err = None  # the current card is shown below, nothing else is wrong with it
            return f"<div class='card warn' role='alert'><p><b>The item changed after you opened it.</b> Here is the current card for {link}; decide on what it is now.</p></div>"
        people = [d for d in (rec["decisions"] if rec else []) if d.get("by_type") == "person"]
        if people:
            d = people[-1]
            text = f"<b>Already decided by {esc(d.get('by'))} at {esc(when(d.get('at')))}:</b> {esc(d.get('verdict'))}d, “{esc(d.get('meaning', ''))}”."
        elif run["status"] == "cancelled":
            ev = next((e for e in reversed(self.hub.runner.track(run["id"])) if e["type"] == "run.cancelled"), {})
            text = f"<b>Already cancelled by {esc(ev.get('by', '?'))} at {esc(when(ev.get('at')))}</b>" + (f": {esc(ev.get('reason'))}." if ev.get("reason") else ".")
        elif run["status"] == "failed":
            ev = self._failure_of(run["id"])
            text = f"<b>No longer waiting:</b> the run stopped at {esc((ev.get('step') or '?').replace('-', ' '))} ({esc(ev.get('reason', ''))})."
        else:
            text = f"<b>No longer waiting:</b> the run is {esc(run['status'])}."
        self.err = None
        return f"<div class='card warn' role='alert'><p>{text} The item is now {self._state_chip(rec['state']) if rec else esc(run['status'])}: {link}.</p></div>"

    def _message_links(self, m: dict) -> str:
        if not m.get("run_id"):
            return ""
        try:
            run = self.hub.runner.load(m["run_id"])
        except RunError:
            return ""
        if run.get("record") and self.hub.kernel.get(run["record"]):
            rec = self.hub.kernel.get(run["record"])
            return f" <a href='{href('/record/' + run['record'], self.user)}'>Open {esc(rec['number'])}</a>"
        return " " + self._run_link(run, f"Open run {run['id'][:8]}")

    def _message_form(self) -> str:
        user = self.user
        bundle = self.bundle
        people = [(p, p) for p in self._people() if p != user]
        cogs = [(p, f"{p} (automatic reviewer)") for p, m in sorted(bundle["members"].items()) if m["type"] == "cog"]
        mine = self.err is not None and self.route == "/msg"
        err = self._form_error(["to", "subject", "body", "channel"]) if mine else ""
        to = self._val("to") if mine else (people[0][0] if people else "")
        return f"""<h2 id="send">Send a message</h2><form method="post" action="/msg" class="card">{hidden_as(user)}{self._once()}{err}
<div class="fields">{widget("to", "text", to, label="To", required=True, options=people + cogs, hint="people first; automatic reviewers answer with what they filed", error=self._field_error("to") if mine else None)}
{widget("channel", "enum:dm,email", self._val("channel") if mine else "dm", label="Via", hint="", blank="dm", error=self._field_error("channel") if mine else None)}
{text_input("subject", "Subject", self._val("subject") if mine else "", required=True, error=self._field_error("subject") if mine else None)}
{text_input("body", "Message", self._val("body") if mine else "", required=True, textarea=True, error=self._field_error("body") if mine else None)}</div>
<div class="actions"><button>Send</button></div></form>"""
