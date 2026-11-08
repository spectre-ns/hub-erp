"""Procurement: from a released design's shortages to a purchase order with an
approved supplier, then goods receipt into a stock location as lots.

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
from .routing import threshold


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
    reaches = ["kernel:records"]
    description = ("Resolves the request's part and supplier part (named, or the part's primary active supplier part), checks the "
                   "supplier against the approved list, prices it from the price breaks and computes the amount.")

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        from . import inventory

        prior = self._prior(input, ctx)
        problems = []
        part = ctx.kernel.get(str(prior["part"])) if prior.get("part") else None
        if part is None and prior.get("part"):
            part = ctx.kernel.get(f"rec:part:{prior['part']}")
        if part is not None and part["type"] != "part":
            part = None
        if part is None:
            problems.append(problem("part", "the request names no known part", "warning"))
        sp = ctx.kernel.get(str(prior["supplier_part"])) if prior.get("supplier_part") else None
        if sp is None and part is not None:
            candidates = inventory.supplier_parts(ctx.kernel, part)
            named = prior.get("supplier")
            sp = next((c for c in candidates if not named or c["fields"].get("supplier") == named), None)
        if sp is not None and part is not None and sp["fields"].get("part") != part["locator"]:
            problems.append(problem("supplier-part", f"{sp['number']} is not a supplier part of {part['number']}"))
            sp = None
        supplier = prior.get("supplier") or (sp["fields"].get("supplier") if sp else None)
        ok, why = supplier_approved(ctx.kernel, supplier)
        if not ok:
            problems.append(problem("supplier", why, "warning"))
        qty = float(prior.get("quantity") or 0)
        if qty <= 0:
            problems.append(problem("quantity", f"quantity must be positive, not {qty:g}"))
        if sp is not None and qty > 0:
            pack = float(sp["fields"].get("pack_quantity") or 1)
            if pack > 1 and abs(qty / pack - round(qty / pack)) > 1e-9:
                problems.append(problem("pack", f"{sp['number']} comes in packs of {pack:g}; {qty:g} is not a whole number of packs", "warning"))
        unit_cost = prior.get("unit_cost")
        if unit_cost in (None, "") and sp is not None:
            unit_cost = inventory.unit_price(sp, qty)
        priced = unit_cost not in (None, "")
        if not priced:
            problems.append(problem("unit-cost", "no unit cost on the request or its supplier part; amount is 0 until a buyer sets it", "warning"))
            unit_cost = 0
        amount = round(qty * float(unit_cost), 2)
        currency = prior.get("currency") or (sp["fields"].get("currency") if sp else None) or "USD"
        active = inventory.part_active(part)
        if part is not None and not active:
            problems.append(problem("part", f"{part['number']} is {part['state']}{'' if part['fields'].get('active', True) else ', inactive'}: not yet orderable within criteria", "warning"))
        mp = ctx.kernel.get(sp["fields"].get("manufacturer_part") or "") if sp else None
        if mp is None and part is not None:
            mps = inventory.manufacturer_parts(ctx.kernel, part)
            mp = mps[0] if mps else None
        conditions = dict(prior.get("conditions") or {})
        conditions.update({"supplier_approved": ok, "part_active": active})
        fields = {"supplier": supplier, "supplier_part": sp["locator"] if sp else None, "supplier_approved": ok, "supplier_note": why,
                  "unit_cost": float(unit_cost) if priced else None,  # left empty so the buyer may price it at the Gate
                  "amount": amount, "currency": currency, "conditions": conditions,
                  "mpn": prior.get("mpn") or (mp["fields"].get("mpn") if mp else ""),
                  "manufacturer": prior.get("manufacturer") or (mp["fields"].get("manufacturer") if mp else ""),
                  **({"part": part["locator"]} if part else {})}
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
        # The Gate may have named the supplier or the price: re-check both before anything is issued.
        ok, why = supplier_approved(ctx.kernel, prior.get("supplier"))
        if not ok:
            return make_envelope(self.id, self.version, "run", error={"code": "supplier-not-approved", "detail": f"no purchase order: {why}"})
        if float(prior.get("amount") or 0) <= 0 or float(prior.get("quantity") or 0) <= 0:
            return make_envelope(self.id, self.version, "run", error={"code": "amount", "detail": "no purchase order: the request has no positive quantity and price"})
        big = float(threshold(ctx.bundle, "purchase-approval-threshold", 10000) or 10000)
        decider = ctx.grant.get("decided_by", "")
        if float(prior["amount"]) > big and ctx.grant.get("by_type") == "cog":
            return make_envelope(self.id, self.version, "run", error={"code": "threshold", "detail": f"a cog may not approve {prior['amount']} above {big:g}"})
        sp = ctx.kernel.get(prior.get("supplier_part") or "") if prior.get("supplier_part") else None
        line = {"line": 1, "part": prior.get("part"), "supplier_part": prior.get("supplier_part"), "sku": (sp or {}).get("fields", {}).get("sku"),
                "mpn": prior.get("mpn"), "quantity": prior.get("quantity"), "unit": prior.get("unit", "ea"),
                "unit_cost": prior.get("unit_cost"), "amount": prior.get("amount"), "received": 0}
        if not prior.get("part") and prior.get("item"):  # a request raised before inventory: its line names the (archived) item
            line["item"] = prior["item"]
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
        if prior.get("part") and ctx.kernel.get(prior["part"]):
            ctx.kernel.link(po["locator"], "concerns", prior["part"], by=self.id, run_id=ctx.run_id, release=ctx.release)
        if prior.get("design") and ctx.kernel.get(prior["design"]):
            ctx.kernel.link(po["locator"], "concerns", prior["design"], by=self.id, run_id=ctx.run_id, release=ctx.release)
        ctx.kernel.set_fields(rec["locator"], {"po": po["locator"], "amount": prior.get("amount"), "unit_cost": prior.get("unit_cost")},
                              by=self.id, run_id=ctx.run_id, release=ctx.release)
        if ctx.inbox is not None:
            ctx.inbox.notify(rec["fields"].get("requested_by", ctx.owner), kind="update",
                             subject=f"{po['number']} issued for {rec['number']}",
                             body=f"Ordered {line['quantity']} x {line.get('mpn') or line.get('part')} from {prior.get('supplier')} ({prior.get('amount')} {prior.get('currency', 'USD')}).",
                             run_id=ctx.run_id)
        return self.ok("run", {**prior, "po": po["locator"], "po_number": po["number"]})


class ShortageRequester(Cog):
    id = "shortage-requester"
    reaches = ["kernel:records"]
    description = ("After a design is released: creates the parts (with their manufacturer parts) for the BOM lines no part matched, and "
                   "raises a purchase request for every line short of available stock, as child runs under the design's grant.")

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        from . import inventory

        refused = self._require_grant(ctx, "run")
        if refused:
            return refused
        prior = self._prior(input, ctx)
        rec = ctx.rec()
        created_parts, requests = [], []
        lines = list(prior.get("bom_resolved") or [])
        for u in prior.get("bom_unresolved") or []:
            f = dict(u.get("proposal", {}).get("fields", {}))
            # A new part waits for the buyer's approval (part-request): it is not yet orderable within a cog's criteria.
            part = inventory.create_part(ctx.kernel, title=f.get("title") or f.get("name") or f.get("mpn") or "part", by=ctx.grant.get("decided_by", ctx.owner),
                                         state="open", mpn=f.get("mpn") or "", manufacturer=f.get("manufacturer") or "", footprint=f.get("footprint"),
                                         design=rec["locator"], run_id=ctx.run_id, release=ctx.release)
            created_parts.append(part["locator"])
            lines.append({**u, "part": part["number"], "available": 0, "short": int(u.get("quantity", 1))})
        if ctx.runner is None or "purchase-request" not in ctx.bundle.get("ops", {}):
            return self.ok("run", {**prior, "parts_created": created_parts, "purchase_requests": []},
                           [problem("purchasing", "no purchase-request process in the release", "warning")])
        remaining: dict[str, float] = {}
        for line in lines:
            part = ctx.kernel.get(f"rec:part:{line['part']}") if line.get("part") else None
            key = part["locator"] if part else str(line.get("part"))
            remaining.setdefault(key, inventory.available(ctx.kernel, part) if part else 0.0)
            need = float(line.get("quantity") or 1)
            take = min(need, max(0.0, remaining[key]))
            remaining[key] -= take
            short = int(round(need - take))
            line["short"] = short
            if short <= 0:
                continue
            sps = inventory.supplier_parts(ctx.kernel, part) if part else []
            sp = sps[0] if sps else None
            run_id = ctx.runner.submit("purchase-request", {"record": {
                "title": f"{short} x {line.get('mpn') or line.get('value')} for {rec['number']}",
                "part": part["locator"] if part else None, "supplier_part": sp["locator"] if sp else None,
                "mpn": line.get("mpn"), "manufacturer": line.get("manufacturer"),
                "quantity": short, "supplier": (sp["fields"].get("supplier") if sp else None), "design": rec["locator"],
                "reason": f"shortage for released design {rec['number']}", "requested_by": ctx.owner,
                "needed_by": (date.today() + timedelta(days=14)).isoformat()}},
                owner=ctx.owner, parent_run=ctx.run_id)
            pr = ctx.runner.load(run_id)
            ctx.kernel.link(pr["record"], "concerns", rec["locator"], by=self.id, run_id=ctx.run_id, release=ctx.release)
            requests.append({"run_id": run_id, "record": pr["record"], "status": pr["status"]})
        fields = {"parts_created": created_parts, "purchase_requests": [r["record"] for r in requests]}
        ctx.kernel.set_fields(rec["locator"], fields, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, **fields, "purchase_request_runs": requests})


class ReceiptChecker(Cog):
    id = "receipt-checker"
    reaches = ["kernel:records"]
    description = ("Checks a goods receipt against its purchase order: the location holds stock, each line is a part on the order, and no "
                   "line is received beyond the order and the over-receipt tolerance.")

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        from . import inventory

        prior = self._prior(input, ctx)
        problems = []
        po_ref = prior.get("po")
        po = ctx.kernel.get(str(po_ref)) if po_ref else None
        if po is None and po_ref:
            po = ctx.kernel.get(f"rec:purchase-order:{po_ref}")
        if po is None:
            return make_envelope(self.id, self.version, "run", error={"code": "po-missing", "detail": f"no purchase order {po_ref!r}"})
        loc = None
        try:
            loc = inventory.location_of(ctx.kernel, prior.get("location")) if prior.get("location") else inventory.default_location(ctx.kernel, kind="receiving")
            if loc is None:
                raise inventory.StockError("there is no stock location to receive into: add one first")
            inventory.holds_stock(loc)
        except inventory.StockError as e:
            return make_envelope(self.id, self.version, "run", error={"code": "location", "detail": str(e)})

        def norm(ref):
            r = ctx.kernel.get(str(ref)) if ref else None
            if r is None and ref and not str(ref).startswith("rec:"):
                r = ctx.kernel.get(f"rec:part:{ref}")
            return r["locator"] if r else str(ref)
        ordered: dict[str, float] = {}
        for l in po["fields"].get("lines", []):
            if isinstance(l, dict) and l.get("part"):
                ordered[norm(l["part"])] = ordered.get(norm(l["part"]), 0.0) + float(l.get("quantity") or 0)
        if not ordered:  # an order placed before inventory: its lines name items (archived), which hold no stock
            return make_envelope(self.id, self.version, "run", error={
                "code": "no-part-lines", "detail": f"{po['number']} orders no parts (its lines name items, which are archived): it cannot be "
                                                  "received into stock; cancel this receipt, and re-order the parts if they are still wanted"})
        received_before: dict[str, float] = {k: 0.0 for k in ordered}
        for other in ctx.kernel.query(type_="receipt", state="received"):
            if other["fields"].get("po") == po["locator"] and other["locator"] != ctx.record:
                for l in other["fields"].get("lines", []):
                    received_before[norm(l.get("part"))] = received_before.get(norm(l.get("part")), 0.0) + float(l.get("quantity") or 0)
        if po["state"] == "received":
            problems.append(problem("po-received", f"{po['number']} is already fully received"))
        tol = inventory.thresholds(ctx.bundle)["over_receipt_pct"]
        lines = prior.get("lines") or [{"part": k, "quantity": ordered[k] - received_before.get(k, 0.0)} for k in ordered]
        for l in lines:
            if not l.get("part"):
                problems.append(problem("receipt-line", "each line names a part: " + (f"{l['item']} is an item, which is archived" if l.get("item") else "a line names none")))
                continue
            l["part"] = norm(l.get("part"))
            qty = float(l.get("quantity") or 0)
            name = l["part"].rsplit(":", 1)[-1]
            if qty <= 0:
                problems.append(problem("quantity", f"received quantity for {name} must be positive, not {qty:g}"))
                continue
            if l["part"] not in ordered:
                problems.append(problem("receipt-line", f"line {name} is not on {po['number']}"))
                continue
            try:
                tp = inventory.tracking_problem(ctx.kernel, l["part"], qty, batch=l.get("batch") or "", serials=l.get("serials"))
            except inventory.StockError as e:
                tp = str(e)
            if tp:  # refused here, before the buyer confirms, not at booking after
                problems.append(problem("tracking", tp))
                continue
            outstanding = ordered[l["part"]] - received_before.get(l["part"], 0.0)
            if qty > outstanding * (1 + tol / 100.0) + 1e-9:
                problems.append(problem("over-receipt", f"received {qty:g} of {name}, outstanding {outstanding:g} on {po['number']}"
                                                        + (f" (over-receipt tolerance {tol:g}%)" if tol else "")))
        outstanding_after = {k: max(0.0, ordered[k] - received_before.get(k, 0.0) - sum(float(l.get("quantity") or 0) for l in lines if l.get("part") == k))
                             for k in ordered}
        fields = {"lines": lines, "supplier": po["fields"].get("supplier"), "po_number": po["number"], "location": loc["locator"],
                  "outstanding_after": outstanding_after, "completes_order": all(v <= 1e-9 for v in outstanding_after.values())}
        if ctx.record:
            ctx.kernel.set_fields(ctx.record, fields, by=self.id, run_id=ctx.run_id, release=ctx.release)
            ctx.kernel.link(ctx.record, "fulfils", po["locator"], by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, **fields}, problems)


class StockBooker(Cog):
    id = "stock-booker"
    reaches = ["kernel:records"]
    description = ("Receives the confirmed lines into the receipt's location as lots (one per serial number for serial-tracked parts), "
                   "and marks the purchase order received (for bill matching).")

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        from . import inventory

        refused = self._require_grant(ctx, "run")
        if refused:
            return refused
        prior = self._prior(input, ctx)
        po = ctx.kernel.get(prior["po"]) if prior.get("po") else None
        po_lines = {l.get("part"): l for l in (po or {}).get("fields", {}).get("lines", []) if isinstance(l, dict)}
        unbookable = [l for l in prior.get("lines") or [] if not l.get("part")]
        if unbookable or not prior.get("lines"):
            # A receipt checked before inventory names items (archived), which hold no stock: never mark the order
            # received with nothing booked.
            return make_envelope(self.id, self.version, "run", error={
                "code": "no-part-lines", "detail": "nothing booked: " + ("a line names an item, which is archived, not a part" if unbookable else "the receipt has no lines")
                + "; cancel this receipt and receive the order's parts on a new one"})
        booked, made = [], []
        for l in prior.get("lines") or []:
            part = ctx.kernel.get(l["part"]) if l.get("part") else None
            if part is None or float(l.get("quantity") or 0) <= 0:
                continue
            ol = po_lines.get(part["locator"]) or {}
            try:
                lots = inventory.receive(ctx.kernel, part=part, quantity=l.get("quantity"), location=prior.get("location"), by=self.id,
                                         batch=l.get("batch") or "", serials=l.get("serials") or None, expiry=l.get("expiry") or None,
                                         unit_cost=ol.get("unit_cost"), currency=(po or {}).get("fields", {}).get("currency") or "USD",
                                         supplier_part=ol.get("supplier_part"), po=(po or {}).get("locator"), receipt=ctx.record,
                                         run_id=ctx.run_id, release=ctx.release)
            except inventory.StockError as e:
                return make_envelope(self.id, self.version, "run", error={"code": "receive", "detail": f"{part['number']}: {e}"})
            made += [x["locator"] for x in lots]
            booked.append({"part": part["number"], "quantity": l.get("quantity"), "lots": [x["number"] for x in lots],
                           "available": inventory.available(ctx.kernel, part)})
        if ctx.record:
            ctx.kernel.set_fields(ctx.record, {"booked": booked, "lots": made}, by=self.id, run_id=ctx.run_id, release=ctx.release)
        if po is not None and prior.get("completes_order", True):
            ctx.kernel.set_fields(po["locator"], {"received": True}, by=self.id, run_id=ctx.run_id, release=ctx.release)
            ctx.kernel.transition(po["locator"], "received", by=self.id, run_id=ctx.run_id, release=ctx.release)
            ctx.kernel.append("po.received", f"po:{po['number']}", {"received": True, "receipt": ctx.record},
                              run_id=ctx.run_id, release=ctx.release, by=self.id)
        elif po is not None:
            ctx.kernel.append("po.partially_received", f"po:{po['number']}", {"receipt": ctx.record, "outstanding": prior.get("outstanding_after")},
                              run_id=ctx.run_id, release=ctx.release, by=self.id)
        return self.ok("run", {**prior, "booked": booked, "lots": made})


class PurchasingAssistant(ReviewerCog):
    id = "purchasing-assistant"
    files_process = "purchase-request"
    description = "Purchasing team member: raises requests from messages and approves small orders from approved suppliers."


def register(registry) -> None:
    for cls in (SupplierChecker, POIssuer, ShortageRequester, ReceiptChecker, StockBooker, PurchasingAssistant):
        registry.register(cls())
