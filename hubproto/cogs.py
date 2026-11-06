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

import re
from dataclasses import dataclass, field

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
    extras: dict = field(default_factory=dict)


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
            lines = record["text"].splitlines()
            for n, line in enumerate(lines, start=1):
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


class BillFiler(Cog):
    id = "bill-filer"
    reaches = ["kernel:bills"]
    description = "Files the proposed bill as a record in the kernel under the run owner's admission."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        bill = dict(input.get("prior") or {})
        folder = ctx.extras.get("folder", "")
        bill_id = ctx.kernel.file_bill(bill, folder=folder, run_id=ctx.run_id, release=ctx.release, by=ctx.owner)
        bill["id"] = bill_id
        bill["folder"] = folder
        return self.ok("run", bill)


class ThreeWayMatcher(Cog):
    id = "three-way-matcher"
    reaches = ["kernel:read"]
    description = "Matches a bill to its purchase order and receipt within the release's match tolerance."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        bill = dict(input.get("prior") or {})
        tol = threshold(ctx.bundle, "match-tolerance", 0.0)
        pos = ctx.kernel.purchase_orders()
        po = pos.get(f"po:{bill.get('po_ref')}")
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
        ctx.kernel.mark_bill(bill["id"], "matched", {"match": match}, run_id=ctx.run_id, release=ctx.release, by=self.id)
        return self.ok("run", {**bill, "match": match}, problems)


class RiskScorer(Cog):
    id = "risk-scorer"
    description = "Deterministic risk tier and the facts approval criteria can test."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        rec = dict(input.get("prior") or {})
        reasons = []
        conditions = {}
        if "amount" in rec:
            match = rec.get("match", {})
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
        else:
            conditions["change_minor"] = rec.get("change_type") == "minor"
            tier = "low" if conditions["change_minor"] else "medium"
            if not conditions["change_minor"]:
                reasons.append("major change to a controlled document")
        conditions["risk_low"] = tier == "low"
        return self.ok("run", {**rec, "risk": tier, "risk_reasons": reasons, "conditions": conditions})


class LedgerPoster(Cog):
    id = "ledger-poster"
    reaches = ["kernel:journals"]
    description = "Posts the approved bill as a balanced journal. Runs only under a grant."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        refused = self._require_grant(ctx, "run")
        if refused:
            return refused
        bill = dict(input.get("prior") or {})
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
        ctx.kernel.mark_bill(bill["id"], "posted", {"journal": number}, run_id=ctx.run_id, release=ctx.release, by=self.id)
        return self.ok("run", {**bill, "journal": number})


class DocumentDrafter(Cog):
    id = "document-drafter"
    kind = "context"
    description = "Turns a request into a controlled-document draft (deterministic stand-in)."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        rec = input.get("record") or {}
        problems = []
        if not rec.get("title"):
            problems.append(problem("required-field", "document has no title"))
        change_type = rec.get("change_type", "major")
        if change_type not in ("minor", "major"):
            problems.append(problem("change-type", "change_type must be minor or major"))
        body = rec.get("body") or f"# {rec.get('title', '')}\n\n(draft body)"
        return self.ok("run", {"title": rec.get("title", ""), "body": body, "change_type": change_type,
                               "doc_type": rec.get("doc_type", "sop")}, problems)


class DocumentFiler(Cog):
    id = "document-filer"
    reaches = ["kernel:documents"]

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        doc = dict(input.get("prior") or {})
        folder = ctx.extras.get("folder", "")
        doc_id = ctx.kernel.file_document(doc, folder=folder, run_id=ctx.run_id, release=ctx.release, by=ctx.owner)
        return self.ok("run", {**doc, "id": doc_id, "folder": folder})


class DocumentPublisher(Cog):
    id = "document-publisher"
    reaches = ["kernel:documents"]

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        refused = self._require_grant(ctx, "run")
        if refused:
            return refused
        doc = dict(input.get("prior") or {})
        ctx.kernel.publish_document(doc["id"], run_id=ctx.run_id, release=ctx.release, grant=ctx.grant,
                                    approved_by=ctx.grant.get("decided_by", ""), by=self.id)
        return self.ok("run", {**doc, "state": "effective"})


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
                                "attachment": att.get("name")})
            reply = "Filed " + ", ".join(a["attachment"] or "attachment" for a in actions) + \
                    " as run " + ", ".join(a["run_id"] for a in actions) + "."
        elif "status" in text and ctx.runner is not None:
            waiting = [r["id"] for r in ctx.runner.list() if r["status"] == "waiting"]
            reply = f"{len(waiting)} run(s) are waiting at a Gate: {', '.join(waiting) or 'none'}."
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


class CogRegistry:
    def __init__(self):
        self.cogs: dict[str, Cog] = {}
        for cls in (InvoiceExtractor, BillFiler, ThreeWayMatcher, RiskScorer, LedgerPoster, DocumentDrafter,
                    DocumentFiler, DocumentPublisher, APReviewer, DocReviewer):
            self.register(cls())

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
