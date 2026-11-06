"""The Cog catalog.

A Cog declares its id, version, kind (``code`` has no model in the loop,
``context`` does), what it reaches outside the run, and its tasks. Every
task returns a result envelope. Reaching Cogs check their grant themselves.

Reviewer Cogs are also principals: they appear in the org model with roles,
receive routed approvals and inbox messages, file records on request, and
approve only within the fixed criteria the release gives them.

The context Cogs here are deterministic stand-ins so the prototype runs
offline. Their interfaces are the ones a model-backed version would keep.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .envelope import Timer, make_envelope, problem, synthesized_failure
from .kernel import KernelGuardError
from .routing import criteria_for, evaluate_criteria, threshold


@dataclass
class RunContext:
    run_id: str
    release: str
    bundle: dict
    owner: str
    kernel: object
    grant: dict | None = None
    runner: object = None
    inbox: object = None
    record: str | None = None
    dms: object = None
    extras: dict = field(default_factory=dict)

    def rec(self) -> dict | None:
        return self.kernel.get(self.record) if self.record else None


class Cog:
    id = "base"
    version = "0.1.0"
    kind = "code"
    reaches: list[str] = []
    tasks: list[str] = ["run"]
    description = ""

    def invoke(self, task: str, input: dict, ctx: RunContext) -> dict:
        if task not in self.tasks:
            return synthesized_failure(self.id, self.version, task, "invalid-input", f"no task {task!r}")
        handler = getattr(self, "task_" + task.replace("-", "_"))
        with Timer() as t:
            try:
                env = handler(input, ctx)
            except KernelGuardError as e:
                env = make_envelope(self.id, self.version, task, error={"code": "guard-refused", "detail": str(e),
                                                                        "guard": e.guard})
            except Exception as e:  # a Cog that crashes still yields an envelope
                env = synthesized_failure(self.id, self.version, task, "invocation-failed", f"{type(e).__name__}: {e}")
        env["timing"] = t.seconds
        return env

    def ok(self, task: str, payload: dict, problems=()) -> dict:
        return make_envelope(self.id, self.version, task, payload=payload, problems=problems)

    def _require_grant(self, ctx: RunContext, task: str) -> dict | None:
        if not ctx.grant or ctx.grant.get("run_id") != ctx.run_id:
            return make_envelope(self.id, self.version, task,
                                 error={"code": "no-grant", "detail": f"{self.id} reaches {self.reaches} and has no grant"})
        return None

    @staticmethod
    def _prior(input: dict, ctx: RunContext) -> dict:
        prior = dict(input.get("prior") or {})
        rec = ctx.rec()
        if rec:
            prior.setdefault("id", rec["number"])
            prior.setdefault("folder", rec["folder"])
            for k, v in rec["fields"].items():
                prior.setdefault(k, v)
        return prior


# ---------------------------------------------------------------- generic code Cogs

class RecordFiler(Cog):
    id = "record-filer"
    reaches = ["kernel:records"]
    description = "Writes the step's proposed fields onto the run's record under the owner's admission."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        prior = dict(input.get("prior") or input.get("record") or {})
        rec = ctx.rec()
        if rec is None:
            return make_envelope(self.id, self.version, "run", error={"code": "no-record", "detail": "run has no record"})
        attachments = prior.pop("attachments", None) or (input.get("record") or {}).get("attachments") or []
        fields = {k: v for k, v in prior.items() if k not in ("id", "folder", "state", "locator", "citations", "attachments")}
        fields["filed_by"] = ctx.owner
        ctx.kernel.set_fields(rec["locator"], fields, by=ctx.owner, run_id=ctx.run_id, release=ctx.release)
        filed = []
        if attachments and ctx.dms is not None:
            import base64

            for att in attachments:
                raw = att.get("content")
                content = base64.b64decode(att["content_b64"]) if att.get("content_b64") else (
                    raw.encode("utf-8") if isinstance(raw, str) else json.dumps(raw).encode("utf-8") if raw is not None else b"")
                doc = ctx.dms.create(title=att.get("title") or att.get("name", "attachment"), doc_class=att.get("doc_class", "controlled"),
                                     folder=rec["folder"], by=ctx.owner, content=content, filename=att.get("name", ""),
                                     doc_type=att.get("doc_type", ""), run_id=ctx.run_id, release=ctx.release,
                                     attach_to=rec["locator"])
                filed.append(doc["locator"])
            ctx.kernel.set_fields(rec["locator"], {"attachments_filed": filed}, by=ctx.owner, run_id=ctx.run_id, release=ctx.release)
            if "attachments" in rec["fields"]:
                ctx.kernel.set_fields(rec["locator"], {"attachments": None}, by=ctx.owner, run_id=ctx.run_id, release=ctx.release)
        if prior.get("citations"):
            ctx.kernel.set_fields(rec["locator"], {"citations": prior["citations"]}, by=ctx.owner, run_id=ctx.run_id,
                                  release=ctx.release)
        return self.ok("run", {**prior, "id": rec["number"], "folder": rec["folder"], "locator": rec["locator"]})


class BillFiler(RecordFiler):
    id = "bill-filer"


class DocumentFiler(RecordFiler):
    id = "document-filer"


class Linker(Cog):
    id = "record-linker"
    reaches = ["kernel:links"]
    description = "Links the run's record to the records its payload names (links: [{rel, target}])."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        prior = self._prior(input, ctx)
        made = []
        for l in prior.get("links") or []:
            if ctx.kernel.get(l["target"]):
                ctx.kernel.link(ctx.record, l["rel"], l["target"], by=self.id, run_id=ctx.run_id, release=ctx.release)
                made.append(l)
        return self.ok("run", {**prior, "links_made": made})


class Notifier(Cog):
    id = "notifier"
    reaches = ["inbox"]
    description = "Tells the record owner and the people in the payload's notify list what happened."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        prior = self._prior(input, ctx)
        rec = ctx.rec() or {}
        people = {ctx.owner, *(prior.get("notify") or [])} - {"hub"}
        if ctx.inbox is not None:
            for p in sorted(people):
                ctx.inbox.notify(p, kind="update", subject=f"{rec.get('number', ctx.record)}: {rec.get('state', '')}",
                                 body=prior.get("message") or f"{ctx.extras.get('process')} progressed on {rec.get('number')}",
                                 run_id=ctx.run_id)
        return self.ok("run", {**prior, "notified": sorted(people)})


class DocumentRenderer(Cog):
    id = "document-renderer"
    reaches = ["kernel:documents"]
    description = "Renders the record to Markdown and files it as an evidence document attached to the record."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        prior = self._prior(input, ctx)
        if ctx.dms is None or ctx.record is None:
            return self.ok("run", prior, [problem("renderer", "no DMS or record in this run", "warning")])
        doc = ctx.dms.render_snapshot(ctx.record, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, "snapshot": doc["number"]})


class RiskScorer(Cog):
    id = "risk-scorer"
    description = "Deterministic risk tier and the facts approval criteria can test."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        rec = self._prior(input, ctx)
        reasons = []
        conditions = {}
        if rec.get("amount") not in (None, ""):
            match = rec.get("match", {}) or {}
            conditions["match_within_tolerance"] = bool(match.get("within_tolerance") and match.get("received"))
            conditions["supplier_approved"] = bool(match.get("supplier_approved"))
            amount = float(rec["amount"])
            if not conditions["match_within_tolerance"]:
                reasons.append("three-way match failed")
            if not conditions["supplier_approved"]:
                reasons.append("supplier not approved")
            if amount >= 10000:
                reasons.append("amount at or above 10,000")
            tier = "high" if (reasons and amount >= 10000) or "three-way match failed" in reasons else (
                "medium" if reasons or amount >= 2000 else "low")
        elif rec.get("change_type") or rec.get("doc_class"):
            conditions["change_minor"] = rec.get("change_type") == "minor"
            tier = "low" if conditions["change_minor"] else "medium"
            if not conditions["change_minor"]:
                reasons.append("major change to a controlled document")
        else:
            sev = (rec.get("severity") or rec.get("source") or "").lower()
            open_ncr = any(r["state"] not in ("closed", "rejected") for r in ctx.kernel.query(type_="ncr")
                           if r["locator"] != ctx.record) if ctx.record else False
            conditions["no_open_ncr"] = not open_ncr
            tier = "high" if sev in ("high", "complaint", "audit") else ("medium" if sev else "low")
            if tier != "low":
                reasons.append(f"source or severity {sev!r}")
        conditions["risk_low"] = tier == "low"
        conditions["risk_not_high"] = tier != "high"
        if ctx.record:
            ctx.kernel.set_fields(ctx.record, {"risk": tier, "conditions": conditions}, by=self.id, run_id=ctx.run_id,
                                  release=ctx.release)
        return self.ok("run", {**rec, "risk": tier, "risk_reasons": reasons, "conditions": conditions})


# ---------------------------------------------------------------- finance

_INVOICE_FIELDS = ("supplier", "amount", "currency", "date", "po_ref", "description")


class InvoiceExtractor(Cog):
    id = "invoice-extractor"
    kind = "context"
    description = "Reads a supplier invoice and proposes a bill, citing where each field came from."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        record = input.get("record") or {}
        problems = []
        bill: dict = {"citations": {}}
        if "text" in record:
            for n, line in enumerate(record["text"].splitlines(), start=1):
                m = re.match(r"^(?P<k>[A-Za-z _]+):\s*(?P<v>.+)$", line.strip())
                if not m:
                    continue
                key = m.group("k").strip().lower().replace(" ", "_")
                if key in _INVOICE_FIELDS:
                    bill[key] = m.group("v").strip()
                    bill["citations"][key] = f"line {n}"
        for key in _INVOICE_FIELDS:
            if key in record and key not in bill:
                bill[key] = record[key]
                bill["citations"][key] = f"field {key}"
        for key in ("supplier", "amount", "currency", "date"):
            if key not in bill:
                problems.append(problem("required-field", f"invoice has no {key}"))
        if "amount" in bill:
            try:
                bill["amount"] = float(str(bill["amount"]).replace(",", ""))
            except ValueError:
                problems.append(problem("amount-format", f"amount {bill['amount']!r} is not a number"))
        if "po_ref" not in bill:
            problems.append(problem("po-reference", "invoice names no purchase order", "warning"))
        return self.ok("run", bill, problems)


class ThreeWayMatcher(Cog):
    id = "three-way-matcher"
    reaches = ["kernel:read"]
    description = "Matches a bill to its purchase order and receipt within the release's match tolerance."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        bill = self._prior(input, ctx)
        tol = threshold(ctx.bundle, "match-tolerance", 0.0)
        po = ctx.kernel.purchase_orders().get(f"po:{bill.get('po_ref')}")
        match = {"po_found": bool(po), "received": bool(po and po.get("received")), "within_tolerance": False,
                 "tolerance_percent": tol, "variance_percent": None}
        problems = []
        if not po:
            problems.append(problem("po-match", f"no purchase order {bill.get('po_ref')!r}"))
        else:
            if po.get("supplier") != bill.get("supplier"):
                problems.append(problem("po-supplier", "purchase order is for a different supplier"))
            if po.get("currency") != bill.get("currency"):
                problems.append(problem("po-currency", "purchase order currency differs"))
            base = float(po.get("amount") or 0)
            variance = abs(float(bill.get("amount", 0)) - base) / base * 100 if base else 100.0
            match["variance_percent"] = round(variance, 3)
            match["within_tolerance"] = variance <= tol
            if not match["within_tolerance"]:
                problems.append(problem("price-variance", f"variance {variance:.2f}% exceeds tolerance {tol}%", "warning"))
            if not po.get("received"):
                problems.append(problem("receipt", "goods have not been received", "warning"))
        supplier = ctx.kernel.suppliers().get(f"supplier:{bill.get('supplier')}")
        match["supplier_approved"] = bool(supplier and supplier.get("approved"))
        if not match["supplier_approved"]:
            problems.append(problem("supplier", "supplier is not on the approved list", "warning"))
        if ctx.record:
            ctx.kernel.set_fields(ctx.record, {"match": match}, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**bill, "match": match}, problems)


class LedgerPoster(Cog):
    id = "ledger-poster"
    reaches = ["kernel:journals"]
    description = "Posts the approved bill as a balanced journal. Runs only under a grant."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        refused = self._require_grant(ctx, "run")
        if refused:
            return refused
        bill = self._prior(input, ctx)
        amount = round(float(bill["amount"]), 2)
        journal = {
            "date": str(bill.get("date")),
            "currency": bill.get("currency"),
            "memo": f"Bill {bill.get('id')} from {bill.get('supplier')}: {bill.get('description', '')}".strip(),
            "folder": bill.get("folder", "Finance"),
            "bill": bill.get("id"),
            "lines": [
                {"account": "5000 Purchases", "debit": amount, "credit": 0.0},
                {"account": "2000 Accounts payable", "debit": 0.0, "credit": amount},
            ],
        }
        number = ctx.kernel.post_journal(journal, run_id=ctx.run_id, release=ctx.release, grant=ctx.grant,
                                         prepared_by=ctx.owner, approved_by=ctx.grant.get("decided_by", ""), by=self.id)
        if ctx.record:
            ctx.kernel.set_fields(ctx.record, {"journal": number}, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**bill, "journal": number})


# ---------------------------------------------------------------- quality

class DocumentDrafter(Cog):
    id = "document-drafter"
    kind = "context"
    description = "Turns a request into a controlled-document draft (deterministic stand-in)."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        rec = dict(input.get("record") or {})
        problems = []
        if not rec.get("title"):
            problems.append(problem("required-field", "document has no title"))
        change_type = rec.get("change_type", "major")
        if change_type not in ("minor", "major"):
            problems.append(problem("change-type", "change_type must be minor or major"))
        body = rec.get("body") or f"# {rec.get('title', '')}\n\n(draft body)"
        out = {"title": rec.get("title", ""), "body": body, "change_type": change_type,
               "doc_type": rec.get("doc_type", "sop"), "doc_class": "controlled",
               "training_roles": rec.get("training_roles", [])}
        if ctx.dms is not None and ctx.record:
            ctx.dms.add_version(ctx.record, body.encode("utf-8"), by=ctx.owner, filename=f"{rec.get('title', 'draft')}.md",
                                mime="text/markdown", note="draft", run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", out, problems)


class DocumentPublisher(Cog):
    id = "document-publisher"
    reaches = ["kernel:documents"]
    description = "Makes the approved revision effective, withdraws the superseded one, assigns training."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        refused = self._require_grant(ctx, "run")
        if refused:
            return refused
        doc = self._prior(input, ctx)
        ctx.kernel.publish_document(doc["id"], run_id=ctx.run_id, release=ctx.release, grant=ctx.grant,
                                    approved_by=ctx.grant.get("decided_by", ""), by=self.id)
        if ctx.dms is not None:
            ctx.dms.release_revision(ctx.record, by=self.id, run_id=ctx.run_id, release=ctx.release)
        assigned = []
        roles = doc.get("training_roles") or []
        if roles and ctx.runner is not None and "training-acknowledgement" in ctx.bundle.get("ops", {}):
            rec = ctx.rec()
            for person, m in ctx.bundle["members"].items():
                if m["type"] == "person" and set(m["roles"]) & set(roles):
                    rid = ctx.runner.submit("training-acknowledgement",
                                            {"record": {"person": person, "document": rec["locator"], "revision": rec["fields"].get("revision"),
                                                        "assigned": date.today().isoformat(), "assignee": person,
                                                        "title": f"Read {rec['fields'].get('title')} rev {rec['fields'].get('revision')}"}},
                                            owner=ctx.grant.get("decided_by", ctx.owner), parent_run=ctx.run_id)
                    ctx.kernel.link(ctx.runner.load(rid)["record"], "trains-on", rec["locator"], by=self.id,
                                    run_id=ctx.run_id, release=ctx.release)
                    assigned.append({"person": person, "run_id": rid})
        return self.ok("run", {**doc, "state": "effective", "training_assigned": assigned})


class NCRClassifier(Cog):
    id = "ncr-classifier"
    kind = "context"
    description = "Proposes source, severity and containment wording for a nonconformance (stand-in)."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        rec = dict(input.get("record") or {})
        text = (rec.get("description") or "").lower()
        source = rec.get("source") or ("complaint" if "customer" in text else "inspection")
        sev = "high" if any(w in text for w in ("safety", "recall", "field", "injury")) else ("medium" if "lot" in text else "low")
        problems = [] if rec.get("description") else [problem("required-field", "nonconformance has no description")]
        out = {**rec, "source": source, "severity": sev,
               "containment": rec.get("containment") or "Quarantine affected items and notify the process owner."}
        if sev == "low":
            out.setdefault("disposition", "rework")
            out.setdefault("disposition_rationale", "Proposed by the classifier for a low-risk nonconformance; confirmed at the Gate.")
        return self.ok("run", out, problems)


class CAPAOpener(Cog):
    id = "capa-opener"
    reaches = ["kernel:records"]
    description = "Opens a CAPA linked to this nonconformance when the disposition requires one."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        refused = self._require_grant(ctx, "run")
        if refused:
            return refused
        prior = self._prior(input, ctx)
        rec = ctx.rec()
        needs_capa = prior.get("risk") in ("medium", "high") or prior.get("disposition") in ("scrap", "return")
        if not needs_capa or ctx.runner is None or "capa" not in ctx.bundle.get("ops", {}):
            return self.ok("run", {**prior, "capa": None})
        run_id = ctx.runner.submit("capa", {"record": {"title": f"CAPA for {rec['number']}: {rec['fields'].get('title', '')}",
                                                        "ncrs": [rec["locator"]], "owner": ctx.grant.get("decided_by", ctx.owner)}},
                                   owner=ctx.grant.get("decided_by", ctx.owner), parent_run=ctx.run_id)
        capa = ctx.runner.load(run_id)["record"]
        ctx.kernel.link(capa, "caused", rec["locator"], by=self.id, run_id=ctx.run_id, release=ctx.release)
        ctx.kernel.set_fields(rec["locator"], {"capa": capa}, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, "capa": capa, "capa_run": run_id})


class NCROpener(Cog):
    id = "ncr-opener"
    reaches = ["kernel:records"]
    description = "Opens a nonconformance from a complaint or an out-of-tolerance calibration, under the parent's grant."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        refused = self._require_grant(ctx, "run")
        if refused:
            return refused
        prior = self._prior(input, ctx)
        rec = ctx.rec()
        trigger = prior.get("result") == "out-of-tolerance" or prior.get("severity") in ("medium", "high") \
            or prior.get("risk") in ("medium", "high")
        if not trigger or ctx.runner is None or "nonconformance" not in ctx.bundle.get("ops", {}):
            return self.ok("run", {**prior, "ncr": None})
        desc = prior.get("description") or f"Raised from {rec['number']} ({rec['type']})"
        run_id = ctx.runner.submit("nonconformance", {"record": {"title": f"From {rec['number']}: {prior.get('title', rec['type'])}",
                                                                  "description": desc, "source": "calibration" if rec["type"] == "calibration" else "complaint"}},
                                   owner=ctx.grant.get("decided_by", ctx.owner), parent_run=ctx.run_id)
        ncr = ctx.runner.load(run_id)["record"]
        ctx.kernel.link(ncr, "caused-by" if "caused-by" in ctx.kernel.link_vocab else "concerns", rec["locator"],
                        by=self.id, run_id=ctx.run_id, release=ctx.release)
        ctx.kernel.set_fields(rec["locator"], {"ncr": ncr}, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, "ncr": ncr, "ncr_run": run_id})


class CAPADrafter(Cog):
    id = "capa-drafter"
    kind = "context"
    description = "Drafts a root-cause skeleton and action list for the owner to complete (stand-in)."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        rec = self._prior(input, ctx)
        out = {**rec, "root_cause_method": rec.get("root_cause_method") or "5-whys",
               "actions": rec.get("actions") or [{"action": "Contain and investigate", "owner": rec.get("owner"), "status": "open"}],
               "due": rec.get("due") or (date.today() + timedelta(days=30)).isoformat()}
        return self.ok("run", out, [] if rec.get("root_cause") else [problem("root-cause", "root cause not yet stated", "warning")])


class EffectivenessScheduler(Cog):
    id = "effectiveness-scheduler"
    reaches = ["kernel:records"]
    description = "Schedules the effectiveness check after the interval the finance or quality Frame sets."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        prior = self._prior(input, ctx)
        days = int(threshold(ctx.bundle, "effectiveness-check-interval", 90) or 90)
        when = (date.today() + timedelta(days=days)).isoformat()
        fields = {"effectiveness_check_date": when,
                  "effectiveness_criteria": prior.get("effectiveness_criteria") or "No recurrence of the nonconformance in the interval."}
        if ctx.record:
            ctx.kernel.set_fields(ctx.record, fields, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, **fields})


class AuditChecklistDrafter(Cog):
    id = "audit-checklist-drafter"
    kind = "context"
    description = "Builds an audit checklist from the processes in the policy release (stand-in)."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        rec = self._prior(input, ctx)
        wanted = set(rec.get("processes") or ctx.bundle.get("processes", {}).keys())
        checklist = []
        for name, proc in ctx.bundle.get("processes", {}).items():
            if name not in wanted:
                continue
            for step in proc["steps"]:
                checklist.append({"process": name, "step": step["title"], "role": step["role"],
                                  "question": f"Is '{step['title']}' performed by a {step['role']} as the SOP states, with records?",
                                  "result": None})
        return self.ok("run", {**rec, "checklist": checklist, "processes": sorted(wanted)})


class DocumentClassifier(Cog):
    id = "document-classifier"
    kind = "context"
    description = "Proposes a document type from its name and text (stand-in)."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        rec = self._prior(input, ctx)
        name = (rec.get("filename") or rec.get("title") or "").lower()
        text = (rec.get("text") or "").lower()
        if "invoice" in name or "invoice" in text:
            kind = "invoice"
        elif any(w in name for w in ("drawing", ".dwg", ".step")):
            kind = "drawing"
        elif "sop" in name or "procedure" in text:
            kind = "sop"
        elif "cert" in name:
            kind = "certificate"
        else:
            kind = "received"
        return self.ok("run", {**rec, "doc_type": kind})


# ---------------------------------------------------------------- reviewer cogs (team members)

class ReviewerCog(Cog):
    """A Cog that is also a team member: reviews, approves within criteria,
    answers its inbox, and files records it is asked to file."""

    kind = "context"
    tasks = ["review", "inbox"]
    files_process = ""

    def task_review(self, input: dict, ctx: RunContext) -> dict:
        payload = input.get("payload") or {}
        process = input.get("process", "")
        routing_ctx = input.get("routing_ctx") or {}
        crits = criteria_for(ctx.bundle, f"cog:{self.id}", process)
        if not crits:
            return self.ok("review", {"verdict": "escalate", "reasons": ["no approval criteria for this process"]})
        reasons: list[str] = []
        for crit in crits:
            within, reasons = evaluate_criteria(crit, routing_ctx)
            if within:
                checks = [f"{c} holds" for c in crit.get("conditions", [])]
                if crit.get("max_amount") is not None:
                    checks.append(f"amount {routing_ctx.get('amount')} under {crit['max_amount']} {crit['currency']}")
                return self.ok("review", {"verdict": "approve", "reasons": checks,
                                          "delegating_rule": crit["source"], "risk": payload.get("risk")})
        return self.ok("review", {"verdict": "escalate", "reasons": reasons})

    def task_inbox(self, input: dict, ctx: RunContext) -> dict:
        msg = input.get("message") or {}
        attachments = msg.get("attachments") or []
        text = (msg.get("body") or "").lower()
        actions = []
        if self.files_process and attachments and ("file" in text or "please" in text or "process" in text):
            for att in attachments:
                run_id = ctx.runner.submit(self.files_process, {"record": att.get("content", {})}, owner=self.id)
                actions.append({"action": "filed", "process": self.files_process, "run_id": run_id,
                                "attachment": att.get("name"), "record": ctx.runner.load(run_id)["record"]})
            reply = "Filed " + ", ".join(a["attachment"] or "attachment" for a in actions) + \
                    " as " + ", ".join(f"{a['record']} (run {a['run_id']})" for a in actions) + "."
        elif "status" in text and ctx.runner is not None:
            waiting = [f"{r.get('record')} ({r['process']})" for r in ctx.runner.list() if r["status"] == "waiting"]
            reply = f"{len(waiting)} item(s) are waiting at a Gate: {', '.join(waiting) or 'none'}."
        else:
            reply = ("I can file an attached record into a process, or report status. "
                     "I approve only within the criteria in the current policy release.")
        return self.ok("inbox", {"reply": reply, "actions": actions})


class APReviewer(ReviewerCog):
    id = "ap-reviewer"
    files_process = "purchase-to-pay"
    description = "Payables reviewer team member: files invoices from its inbox and approves small matched bills."


class DocReviewer(ReviewerCog):
    id = "doc-reviewer"
    files_process = "document-control"
    description = "Document reviewer team member: approves minor revisions of controlled documents."


class QualityAssistant(ReviewerCog):
    id = "quality-assistant"
    files_process = "nonconformance"
    description = "Quality team member: files nonconformances from messages and approves low-risk dispositions within criteria."


class CogRegistry:
    def __init__(self):
        self.cogs: dict[str, Cog] = {}
        for cls in (RecordFiler, BillFiler, DocumentFiler, Linker, Notifier, DocumentRenderer, RiskScorer,
                    InvoiceExtractor, ThreeWayMatcher, LedgerPoster, DocumentDrafter, DocumentPublisher,
                    NCRClassifier, CAPAOpener, NCROpener, CAPADrafter, EffectivenessScheduler, AuditChecklistDrafter,
                    DocumentClassifier, APReviewer, DocReviewer, QualityAssistant):
            self.register(cls())
        from . import cad  # CAD and ECAD cogs live in their own module

        cad.register(self)

    def register(self, cog: Cog) -> None:
        self.cogs[cog.id] = cog

    def get(self, cog_id: str) -> Cog:
        if cog_id not in self.cogs:
            raise KeyError(f"cog {cog_id!r} is not installed")
        return self.cogs[cog_id]

    def ids(self) -> set[str]:
        return set(self.cogs)

    def cards(self) -> list[dict]:
        return [{"id": c.id, "version": c.version, "kind": c.kind, "reaches": c.reaches, "tasks": c.tasks,
                 "description": c.description} for c in self.cogs.values()]
