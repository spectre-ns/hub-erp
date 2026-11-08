"""The inventory process steps (code cogs).

* ``stock-quarantiner``: a nonconformance's affected stock goes into quarantine as soon as the NCR is filed, with no
  Gate (containment never waits). The reporter must be allowed to quarantine stock where it is.
* ``disposition-applier``: after the disposition Gate, under its grant: scrap, use as is (release), rework or return
  the lots the NCR quarantined.
* ``adjustment-checker`` / ``adjustment-applier``: a stock adjustment's variance, value and who ordered the stock
  (segregation of duties: whoever approved the order never signs the adjustment of its stock), then, approved, the
  adjustment itself.
* ``release-checker`` / ``stock-releaser``: release from quarantine only for a quarantined lot no open nonconformance
  names, not expired; then, approved, the release.
* ``stores``: the stores cog, a team member with custody of the stock and no approval right over it.
"""

from __future__ import annotations

from . import authz, inventory
from .cogs import Cog, ReviewerCog, RunContext
from .envelope import make_envelope, problem


def _lot_entries(fields: dict) -> list[tuple[str, float | None]]:
    out = []
    for x in fields.get("stock_items") or []:
        if isinstance(x, dict):
            out.append((str(x.get("lot") or x.get("stock_item") or ""), x.get("quantity")))
        elif x:
            out.append((str(x), None))
    return [(lot, q) for lot, q in out if lot]


class StockQuarantiner(Cog):
    id = "stock-quarantiner"
    reaches = ["kernel:records"]
    description = "Quarantines the stock a nonconformance names, at once and without a Gate, splitting off the affected quantity."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        prior = self._prior(input, ctx)
        rec = ctx.rec()
        if rec is None:
            return self.ok("run", prior)
        entries = _lot_entries({**rec["fields"], **{k: v for k, v in prior.items() if k == "stock_items"}})
        if not entries:
            return self.ok("run", {**prior, "quarantined": []})
        kernel = ctx.runner.kernel if ctx.runner is not None else ctx.kernel  # containment is free: the permission is checked below
        who = ctx.owner
        done, problems = [], []
        for ref, qty in entries:
            try:
                lot = inventory.lot_of(kernel, ref)
                allowed = authz.check(ctx.bundle, who, "quarantine", "stock-item", lot["folder"])
                if not allowed["allowed"]:
                    problems.append(problem("quarantine", f"{who} may not quarantine {lot['number']}: {allowed['why']}", "warning"))
                    continue
                held = inventory.quarantine(kernel, lot, by=self.id, quantity=qty, ncr=rec["locator"], run_id=ctx.run_id, release=ctx.release,
                                            reason=f"held for {rec['number']}")
                done.append(held["locator"])
            except inventory.StockError as e:
                problems.append(problem("quarantine", str(e), "warning"))
        if done:
            ctx.kernel.set_fields(rec["locator"], {"quarantined": done}, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, "quarantined": done}, problems)


class DispositionApplier(Cog):
    id = "disposition-applier"
    reaches = ["kernel:records"]
    description = "After the disposition Gate: scraps, releases (use as is), sends for rework or returns the stock the nonconformance holds."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        refused = self._require_grant(ctx, "run")
        if refused:
            return refused
        prior = self._prior(input, ctx)
        rec = ctx.rec()
        held = list((rec or {}).get("fields", {}).get("quarantined") or [])
        disposition = str(prior.get("disposition") or (rec or {}).get("fields", {}).get("disposition") or "")
        if not held:
            return self.ok("run", {**prior, "disposition_applied": []})
        why = f"{disposition} by the disposition of {rec['number']}"
        done, problems = [], []
        for ref in held:
            try:
                lot = inventory.lot_of(ctx.kernel, ref)
                if disposition == "scrap":
                    lot = inventory.scrap(ctx.kernel, lot, by=self.id, reason=why, run_id=ctx.run_id, release=ctx.release)
                elif disposition == "use-as-is":
                    lot = inventory.release_from_quarantine(ctx.kernel, lot, by=self.id, reason=why, ncr_ok=True, run_id=ctx.run_id, release=ctx.release)
                elif disposition == "rework":
                    lot = inventory.rework(ctx.kernel, lot, by=self.id, reason=why, run_id=ctx.run_id, release=ctx.release)
                elif disposition == "return":
                    lot = inventory.return_to_supplier(ctx.kernel, lot, by=self.id, reason=why, run_id=ctx.run_id, release=ctx.release)
                else:
                    problems.append(problem("disposition", f"no disposition to apply to {lot['number']}", "warning"))
                    continue
                done.append({"lot": lot["number"], "status": lot["fields"].get("status"), "state": lot["state"]})
            except inventory.StockError as e:
                problems.append(problem("disposition", str(e)))
        ctx.kernel.set_fields(rec["locator"], {"disposition_applied": done}, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, "disposition_applied": done}, problems)


class AdjustmentChecker(Cog):
    id = "adjustment-checker"
    reaches = ["kernel:records"]
    description = "Works out a stock adjustment's variance, its value and who ordered the stock, for its routing and its approval."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        prior = self._prior(input, ctx)
        try:
            lot = inventory.lot_of(ctx.kernel, prior.get("lot"))
        except inventory.StockError as e:
            return make_envelope(self.id, self.version, "run", error={"code": "lot", "detail": str(e)})
        if lot["state"] != inventory.LIVE:
            return make_envelope(self.id, self.version, "run", error={"code": "lot", "detail": f"{lot['number']} is {lot['state']}"})
        if prior.get("new_quantity") in (None, ""):
            return make_envelope(self.id, self.version, "run", error={"code": "quantity", "detail": "say the quantity the lot should hold"})
        v = inventory.variance_of(lot, prior["new_quantity"])
        tol = inventory.thresholds(ctx.bundle)["count_tolerance_pct"]
        # Within tolerance is measured like a free count, from the baseline: the free counts since the lot was last set
        # and this adjustment share one tolerance, so the cog's rail never extends a run of small counts.
        base = inventory.count_baseline(ctx.kernel, lot)
        drift = inventory.drift_of(base, v["after"])
        po = ctx.kernel.get(lot["fields"].get("po") or "")
        ordered_by = (po or {}).get("fields", {}).get("approved_by")
        fields = {"part": lot["fields"].get("part"), "quantity_before": v["before"], "baseline": base, "cumulative_pct": drift,
                  "variance": v["variance"], "variance_pct": v["variance_pct"], "amount": v["value"], "currency": v["currency"], "ordered_by": ordered_by,
                  "conditions": {**(prior.get("conditions") or {}), "variance_within_tolerance": drift <= tol + 1e-9}}
        if ctx.record:
            ctx.kernel.set_fields(ctx.record, {k: v_ for k, v_ in fields.items() if v_ is not None}, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, **fields})


class AdjustmentApplier(Cog):
    id = "adjustment-applier"
    reaches = ["kernel:records"]
    description = ("Applies an approved stock adjustment to its lot, on the ledger; never when the lot changed after the adjustment "
                   "was checked (the approval was given on the change the check worked out).")

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        refused = self._require_grant(ctx, "run")
        if refused:
            return refused
        prior = self._prior(input, ctx)
        rec = ctx.rec()
        try:
            current = inventory.lot_of(ctx.kernel, prior.get("lot"))
        except inventory.StockError as e:
            return make_envelope(self.id, self.version, "run", error={"code": "adjust", "detail": str(e)})
        checked = rec["fields"].get("quantity_before", prior.get("quantity_before"))
        now = float(current["fields"].get("quantity") or 0)
        if checked is None or abs(now - float(checked)) > 1e-9:
            # A split, a count or a move since the check: the absolute quantity approved would book another change than
            # the one approved (1000 -> 800 approved, 500 left after a split: +300). Check it again and approve it again.
            was = "an unchecked quantity" if checked is None else f"{float(checked):g}"
            return make_envelope(self.id, self.version, "run", error={
                "code": "stale", "recheck": "adjustment-checker",
                "detail": (f"{current['number']} changed since the adjustment was checked: it holds {now:g} now, not {was}; nothing was "
                           f"booked. Retry it and it is checked again and approved again on what the lot holds now")})
        try:
            lot = inventory.adjust(ctx.kernel, prior.get("lot"), prior.get("new_quantity"), by=self.id,
                                   reason=f"{rec['number']}: {prior.get('reason') or 'adjustment'} (approved by {ctx.grant.get('decided_by')})",
                                   adjustment=rec["locator"], run_id=ctx.run_id, release=ctx.release)
        except inventory.StockError as e:
            return make_envelope(self.id, self.version, "run", error={"code": "adjust", "detail": str(e)})
        ctx.kernel.set_fields(rec["locator"], {"applied": True}, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, "applied": True, "quantity_after": lot["fields"].get("quantity")})


class ReleaseChecker(Cog):
    id = "release-checker"
    reaches = ["kernel:records"]
    description = "Checks a lot may be released from quarantine: quarantined, named by no open nonconformance, not expired."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        prior = self._prior(input, ctx)
        try:
            lot = inventory.lot_of(ctx.kernel, prior.get("lot"))
        except inventory.StockError as e:
            return make_envelope(self.id, self.version, "run", error={"code": "lot", "detail": str(e)})
        if lot["fields"].get("status") not in ("QUARANTINED", "ATTENTION", "DAMAGED"):
            return make_envelope(self.id, self.version, "run", error={"code": "not-held", "detail": f"{lot['number']} is {lot['fields'].get('status')}: nothing to release"})
        n = inventory.open_ncr_for(ctx.kernel, lot)
        if n:
            return make_envelope(self.id, self.version, "run", error={"code": "open-ncr", "detail": f"{lot['number']} is named by {n['number']}, which is open: its disposition releases it"})
        if inventory.expired(lot):
            return make_envelope(self.id, self.version, "run", error={"code": "expired", "detail": f"{lot['number']} expired on {lot['fields']['expiry']}"})
        fields = {"part": lot["fields"].get("part"), "ncr": lot["fields"].get("ncr")}
        if ctx.record:
            ctx.kernel.set_fields(ctx.record, {k: v for k, v in fields.items() if v}, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, **fields})


class StockReleaser(Cog):
    id = "stock-releaser"
    reaches = ["kernel:records"]
    description = "Releases an approved lot from quarantine back to stock."

    def task_run(self, input: dict, ctx: RunContext) -> dict:
        refused = self._require_grant(ctx, "run")
        if refused:
            return refused
        prior = self._prior(input, ctx)
        rec = ctx.rec()
        try:
            lot = inventory.release_from_quarantine(ctx.kernel, prior.get("lot"), by=self.id, to_location=prior.get("to_location") or None,
                                                    reason=f"{rec['number']}: {prior.get('reason') or 'released'} (approved by {ctx.grant.get('decided_by')})",
                                                    run_id=ctx.run_id, release=ctx.release)
        except inventory.StockError as e:
            return make_envelope(self.id, self.version, "run", error={"code": "release", "detail": str(e)})
        ctx.kernel.set_fields(rec["locator"], {"released": True}, by=self.id, run_id=ctx.run_id, release=ctx.release)
        return self.ok("run", {**prior, "released": True, "location": lot["fields"].get("location")})


class StoresCog(ReviewerCog):
    """The stores cog: a team member that keeps stock as a storekeeper does. It moves, counts, splits, merges and
    quarantines lots where the person who asks may too (its tools check both), and asks for adjustments; it holds no
    approval right over stock, so it never signs a write-off or a release."""

    id = "stores"
    files_process = "stock-adjustment"
    description = ("Stores team member: keeps the stock, moving, counting and quarantining lots where the person who asks may too, "
                   "and asks for adjustments; it never approves a write-off or a release.")


def register(registry) -> None:
    for cls in (StockQuarantiner, DispositionApplier, AdjustmentChecker, AdjustmentApplier, ReleaseChecker, StockReleaser, StoresCog):
        registry.register(cls())
