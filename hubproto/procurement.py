"""Procurement: from a released design's shortages to a purchase order with an
approved supplier, then goods receipt into stock.

Code Cogs only. Supplier approval comes from the kernel: a seeded supplier
aggregate marked approved, or a supplier-evaluation record in state
``approved`` (the quality module's process). The purchasing assistant cog is
a member that may approve small requests within the criteria sentence in the
purchasing Frame; everything else waits for the buyer or the controller.
"""

from __future__ import annotations

from datetime import date, timedelta

from .cogs import Cog, ReviewerCog, RunContext
from .envelope import make_envelope, problem


def supplier_approved(kernel, supplier: str | None) -> tuple[bool, str]:
    if not supplier:
        return False, "no supplier named"
    seeded = kernel.suppliers().get(f"supplier:{supplier}")
    if seeded and seeded.get("approved"):
        return True, f"approved supplier {seeded.get('name', supplier)}"
    for ev in kernel.query(type_="supplier-evaluation", state="approved"):
        if ev["fields"].get("supplier") == supplier:
            return True, f"approved by supplier evaluation {ev['number']}"
    if seeded:
        return False, f"supplier {seeded.get('name', supplier)} is registered but not approved"
    return False, f"unknown supplier {supplier!r}"


class SupplierChecker(Cog):
    id = "supplier-checker"
    reaches = ["kernel:read"]
    description = "Checks the request's supplier against the approved list and computes the amount."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        prior = self._prior(input, ctx)
        problems = []
        item = ctx.kernel.get(prior["item"]) if prior.get("item") else None
        if item is None:
            problems.append(problem("item", "the request names no known inventory item", "warning"))
        supplier = prior.get("supplier") or (item["fields"].get("supplier") if item else None)
        ok, why = supplier_approved(ctx.kernel, supplier)
        if not ok:
            problems.append(problem("supplier", why, "warning"))
        qty = float(prior.get("quantity") or 0)
        unit_cost = prior.get("unit_cost")
        if unit_cost in (None, "") and item is not None:
            unit_cost = item["fields"].get("unit_cost")
        if unit_cost in (None, ""):
            problems.append(problem("unit-cost", "no unit cost on the request or the item; amount is 0 until a buyer sets it", "warning"))
            unit_cost = 0
        amount = round(qty * float(unit_cost), 2)
        currency = prior.get("currency") or (item["fields"].get("currency") if item else None) or "USD"
        conditions = dict(prior.get("conditions") or {})
        conditions.update({"supplier_approved": ok, "item_known": item is not None})
        fields = {"supplier": supplier, "supplier_approved": ok, "supplier_note": why, "unit_cost": float(unit_cost),
                  "amount": amount, "currency": currency, "conditions": conditions,
                  "mpn": prior.get("mpn") or (item["fields"].get("mpn") if item else ""),
                  "manufacturer": prior.get("manufacturer") or (item["fields"].get("manufacturer") if item else "")}
        if ctx.record:
            ctx.kernel.set_fields(ctx.record, fields, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, **fields}, problems)


class POIssuer(Cog):
    id = "po-issuer"
    reaches = ["kernel:records"]
    description = "After approval: creates the purchase order record and the PO aggregate that bill matching reads."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        refused = self._require_grant(ctx, "run")
        if refused:
            return refused
        prior = self._prior(input, ctx)
        rec = ctx.rec()
        if prior.get("unit_cost") not in (None, "") and prior.get("quantity") not in (None, ""):
            prior["amount"] = round(float(prior["quantity"]) * float(prior["unit_cost"]), 2)
        line = {"item": prior.get("item"), "mpn": prior.get("mpn"), "quantity": prior.get("quantity"), "unit": prior.get("unit", "ea"),
                "unit_cost": prior.get("unit_cost"), "amount": prior.get("amount")}
        po = ctx.kernel.create("purchase-order", "Purchasing/Orders",
                               {"supplier": prior.get("supplier"), "lines": [line], "amount": prior.get("amount"),
                                "currency": prior.get("currency", "USD"), "description": prior.get("title", ""),
                                "requests": [rec["locator"]], "issued_by": self.id, "approved_by": ctx.grant.get("decided_by")},
                               by=self.id, run_id=ctx.run_id, release=ctx.release, state="issued")
        # The PO aggregate the three-way matcher reads (purchase-to-pay), keyed by the PO number.
        ctx.kernel.append("po.issued", f"po:{po['number']}", {"supplier": prior.get("supplier"), "amount": prior.get("amount"),
                                                             "currency": prior.get("currency", "USD"),
                                                             "description": prior.get("title", ""), "received": False,
                                                             "record": po["locator"]},
                          run_id=ctx.run_id, release=ctx.release, by=self.id)
        ctx.kernel.link(po["locator"], "fulfils", rec["locator"], by=self.id, run_id=ctx.run_id, release=ctx.release)
        if prior.get("item") and ctx.kernel.get(prior["item"]):
            ctx.kernel.link(po["locator"], "concerns", prior["item"], by=self.id, run_id=ctx.run_id, release=ctx.release)
        if prior.get("design") and ctx.kernel.get(prior["design"]):
            ctx.kernel.link(po["locator"], "concerns", prior["design"], by=self.id, run_id=ctx.run_id, release=ctx.release)
        ctx.kernel.set_fields(rec["locator"], {"po": po["locator"], "amount": prior.get("amount"), "unit_cost": prior.get("unit_cost")},
                              by=self.id, run_id=ctx.run_id, release=ctx.release)
        if ctx.inbox is not None:
            ctx.inbox.notify(rec["fields"].get("requested_by", ctx.owner), kind="update",
                             subject=f"{po['number']} issued for {rec['number']}",
                             body=f"Ordered {line['quantity']} x {line.get('mpn') or line.get('item')} from {prior.get('supplier')} ({prior.get('amount')} {prior.get('currency', 'USD')}).",
                             run_id=ctx.run_id)
        return self.ok("run", {**prior, "po": po["locator"], "po_number": po["number"]})


class ShortageRequester(Cog):
    id = "shortage-requester"
    reaches = ["kernel:records"]
    description = ("After a design is released: creates inventory items for the approved proposals and raises a purchase "
                   "request for every BOM line short of stock, as child runs under the design's grant.")

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        refused = self._require_grant(ctx, "run")
        if refused:
            return refused
        prior = self._prior(input, ctx)
        rec = ctx.rec()
        created_items, requests = [], []
        lines = list(prior.get("bom_resolved") or [])
        for u in prior.get("bom_unresolved") or []:
            f = dict(u.get("proposal", {}).get("fields", {}))
            f.setdefault("stock", 0)
            item = ctx.kernel.create("item", "Inventory/Items", f, by=ctx.grant.get("decided_by", ctx.owner), run_id=ctx.run_id,
                                     release=ctx.release, state="open")
            created_items.append(item["locator"])
            lines.append({**u, "item": item["number"], "stock": 0, "short": int(u.get("quantity", 1))})
        if ctx.runner is None or "purchase-request" not in ctx.bundle.get("ops", {}):
            return self.ok("run", {**prior, "items_created": created_items, "purchase_requests": []},
                           [problem("purchasing", "no purchase-request process in the release", "warning")])
        for line in lines:
            short = int(line.get("short") or 0)
            if short <= 0:
                continue
            item = ctx.kernel.get(f"item:{line['item']}")
            run_id = ctx.runner.submit("purchase-request", {"record": {
                "title": f"{short} x {line.get('mpn') or line.get('value')} for {rec['number']}",
                "item": item["locator"] if item else None, "mpn": line.get("mpn"), "manufacturer": line.get("manufacturer"),
                "quantity": short, "supplier": (item["fields"].get("supplier") if item else None),
                "unit_cost": (item["fields"].get("unit_cost") if item else None), "design": rec["locator"],
                "reason": f"shortage for released design {rec['number']}", "requested_by": ctx.owner,
                "needed_by": (date.today() + timedelta(days=14)).isoformat()}},
                owner=ctx.owner, parent_run=ctx.run_id)
            pr = ctx.runner.load(run_id)
            ctx.kernel.link(pr["record"], "concerns", rec["locator"], by=self.id, run_id=ctx.run_id, release=ctx.release)
            requests.append({"run_id": run_id, "record": pr["record"], "status": pr["status"]})
        fields = {"items_created": created_items, "purchase_requests": [r["record"] for r in requests]}
        ctx.kernel.set_fields(rec["locator"], fields, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, **fields, "purchase_request_runs": requests})


class ReceiptChecker(Cog):
    id = "receipt-checker"
    reaches = ["kernel:read"]
    description = "Checks a goods receipt against its purchase order: lines, quantities, supplier."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        prior = self._prior(input, ctx)
        problems = []
        po_ref = prior.get("po")
        po = ctx.kernel.get(po_ref) if po_ref else None
        if po is None:
            return make_envelope(self.id, self.version, "run", error={"code": "po-missing", "detail": f"no purchase order {po_ref!r}"})
        ordered = {str(l.get("item")): l for l in po["fields"].get("lines", [])}
        lines = prior.get("lines") or [{"item": l.get("item"), "quantity": l.get("quantity")} for l in po["fields"].get("lines", [])]
        for l in lines:
            o = ordered.get(str(l.get("item")))
            if o is None:
                problems.append(problem("receipt-line", f"line {l.get('item')} is not on {po['number']}"))
            elif float(l.get("quantity") or 0) > float(o.get("quantity") or 0):
                problems.append(problem("over-receipt", f"received {l.get('quantity')} of {l.get('item')}, ordered {o.get('quantity')}", "warning"))
        fields = {"lines": lines, "supplier": po["fields"].get("supplier"), "po_number": po["number"]}
        if ctx.record:
            ctx.kernel.set_fields(ctx.record, fields, by=self.id, run_id=ctx.run_id, release=ctx.release)
            ctx.kernel.link(ctx.record, "fulfils", po["locator"], by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, **fields}, problems)


class StockBooker(Cog):
    id = "stock-booker"
    reaches = ["kernel:records"]
    description = "Books received quantities onto the items and marks the purchase order received (for bill matching)."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        refused = self._require_grant(ctx, "run")
        if refused:
            return refused
        prior = self._prior(input, ctx)
        booked = []
        for l in prior.get("lines") or []:
            item = ctx.kernel.get(l["item"]) if l.get("item") else None
            if item is None:
                continue
            new_stock = float(item["fields"].get("stock") or 0) + float(l.get("quantity") or 0)
            ctx.kernel.set_fields(item["locator"], {"stock": new_stock}, by=self.id, run_id=ctx.run_id, release=ctx.release)
            ctx.kernel.append("stock.received", item["locator"], {"quantity": l.get("quantity"), "po": prior.get("po"), "receipt": ctx.record},
                              run_id=ctx.run_id, release=ctx.release, by=self.id)
            booked.append({"item": item["number"], "quantity": l.get("quantity"), "stock": new_stock})
        po = ctx.kernel.get(prior["po"]) if prior.get("po") else None
        if po is not None:
            ctx.kernel.set_fields(po["locator"], {"received": True}, by=self.id, run_id=ctx.run_id, release=ctx.release)
            ctx.kernel.transition(po["locator"], "received", by=self.id, run_id=ctx.run_id, release=ctx.release)
            ctx.kernel.append("po.received", f"po:{po['number']}", {"received": True, "receipt": ctx.record},
                              run_id=ctx.run_id, release=ctx.release, by=self.id)
        return self.ok("run", {**prior, "booked": booked})


class PurchasingAssistant(ReviewerCog):
    id = "purchasing-assistant"
    files_process = "purchase-request"
    description = "Purchasing team member: raises requests from messages and approves small orders from approved suppliers."


def register(registry) -> None:
    for cls in (SupplierChecker, POIssuer, ShortageRequester, ReceiptChecker, StockBooker, PurchasingAssistant):
        registry.register(cls())
