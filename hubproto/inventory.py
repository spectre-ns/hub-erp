"""Inventory after InvenTree, governed.

Parts (one part master for purchased components and made assemblies) sit in part categories (a tree); manufacturer
parts are the approved manufacturer list, supplier parts what can be ordered (SKU, pack size, price breaks). Stock is
held as stock items, one per lot (or one per serial number), in stock locations (a tree; a structural location only
groups others and holds nothing). A lot has a status (OK, ATTENTION, DAMAGED, DESTROYED, REJECTED, LOST, QUARANTINED,
RETURNED) and a state (in-stock, depleted, scrapped, returned); only an OK lot in stock, in a location that counts as
available, is available.

Every movement is one event on the lot in the kernel's hash-chained log, carrying the quantity, from, to, reason, run
and the lot's resulting quantity, location, status and state: the ledger is the only source of those numbers and it
cannot be switched off. What is free and what needs an approval is the release's inventory policy:

* free: receiving into a location, transfers within a site, splits, merges within one batch, counts within the count
  variance tolerance, moving stock into quarantine (containment never waits);
* through a process with an approval: adjustments (and counts outside the tolerance; over the adjustment approval
  threshold the controller signs), release from quarantine, and scrap, use-as-is, rework or return through the
  disposition of a nonconformance.

The functions here check the guards (structural locations, serial numbers, batches, quarantine) and write the events;
who may call them, and when an approval is needed, is checked by their callers (the shell, the cogs' tools, the
process steps).
"""

from __future__ import annotations

from datetime import date

STATUSES = ("OK", "ATTENTION", "DAMAGED", "DESTROYED", "REJECTED", "LOST", "QUARANTINED", "RETURNED")
LOCATION_KINDS = ("store", "receiving", "inspection", "quarantine", "mrb", "scrap", "production", "transit")
HOLD_KINDS = {"quarantine", "mrb"}
NOT_AVAILABLE_KINDS = {"quarantine", "mrb", "scrap", "transit", "inspection"}
LIVE = "in-stock"
STOCK_FOLDER = "Inventory/Stock"
EVENT_WORDS = {"stock.received": "received", "stock.moved": "moved", "stock.counted": "counted", "stock.adjusted": "adjusted",
               "stock.split": "split", "stock.merged": "merged", "stock.quarantined": "quarantined", "stock.released": "released",
               "stock.scrapped": "scrapped", "stock.returned": "returned to the supplier", "stock.reworked": "sent for rework",
               "stock.status": "status changed"}


class StockError(ValueError):
    """A movement the guards refuse; the message says why in words."""


class NeedsApproval(StockError):
    """A movement outside what is free (a count outside the tolerance): it goes through stock-adjustment."""

    def __init__(self, message: str, info: dict):
        super().__init__(message)
        self.info = info


# ---------------------------------------------------------------- records
def _get(kernel, ref, type_: str, what: str) -> dict:
    if isinstance(ref, dict):
        ref = ref.get("locator")
    rec = kernel.get(str(ref)) if ref else None
    if rec is None and ref and not str(ref).startswith("rec:"):
        rec = kernel.get(f"rec:{type_}:{ref}")
    if rec is None or rec["type"] != type_:
        raise StockError(f"there is no {what} {ref!r}")
    return rec


def part_of(kernel, ref) -> dict:
    return _get(kernel, ref, "part", "part")


def lot_of(kernel, ref) -> dict:
    return _get(kernel, ref, "stock-item", "stock item")


def location_of(kernel, ref) -> dict:
    return _get(kernel, ref, "stock-location", "stock location")


def part_active(part: dict | None) -> bool:
    """The part may be bought and stocked: it exists, is active and is not still waiting for its approval."""
    if not part:
        return False
    f = part["fields"]
    return f.get("active", True) is not False and part["state"] in ("active", "released") and f.get("purchaseable", True) is not False


# ---------------------------------------------------------------- locations
def location_chain(kernel, loc) -> list[dict]:
    """The location and its parents, outermost first."""
    out, cur, seen = [], location_of(kernel, loc), set()
    while cur and cur["locator"] not in seen:
        seen.add(cur["locator"])
        out.insert(0, cur)
        parent = cur["fields"].get("parent")
        cur = kernel.get(parent) if parent else None
    return out


def location_path(kernel, loc) -> str:
    return " / ".join(c["fields"].get("name") or c["number"] for c in location_chain(kernel, loc))


def site_of(kernel, loc) -> str:
    """The site a location belongs to: the nearest site name up the tree, else the outermost location's name."""
    chain = location_chain(kernel, loc)
    for c in reversed(chain):
        if c["fields"].get("site"):
            return c["fields"]["site"]
    return chain[0]["fields"].get("name") or chain[0]["number"]


def locations(kernel) -> list[dict]:
    return sorted(kernel.query(type_="stock-location"), key=lambda r: location_path(kernel, r["locator"]))


def location_of_kind(kernel, site: str, kind: str) -> dict | None:
    """The first location of this kind on the site that may hold stock."""
    for r in locations(kernel):
        if r["fields"].get("kind") == kind and not r["fields"].get("structural") and site_of(kernel, r["locator"]) == site:
            return r
    return None


def holds_stock(loc: dict) -> None:
    if loc["fields"].get("structural"):
        raise StockError(f"{loc['fields'].get('name') or loc['number']} is a structural location: it groups other locations and holds no stock")


def counts_as_available(loc: dict) -> bool:
    return loc["fields"].get("kind", "store") not in NOT_AVAILABLE_KINDS


# ---------------------------------------------------------------- lots and availability
def lots(kernel, part=None, location=None, live: bool = True) -> list[dict]:
    p = part_of(kernel, part)["locator"] if part else None
    loc = location_of(kernel, location)["locator"] if location else None
    out = []
    for r in kernel.query(type_="stock-item"):
        f = r["fields"]
        if p and f.get("part") != p:
            continue
        if loc and f.get("location") != loc:
            continue
        if live and r["state"] != LIVE:
            continue
        out.append(r)
    return out


def is_available(kernel, lot: dict) -> bool:
    if lot["state"] != LIVE or lot["fields"].get("status", "OK") != "OK" or float(lot["fields"].get("quantity") or 0) <= 0:
        return False
    if expired(lot):
        return False
    loc = kernel.get(lot["fields"].get("location") or "")
    return bool(loc) and counts_as_available(loc)


def expired(lot: dict, today: str | None = None) -> bool:
    exp = lot["fields"].get("expiry")
    return bool(exp) and str(exp) < (today or date.today().isoformat())


def available(kernel, part) -> float:
    """What can be used or allocated: OK lots, in stock, not expired, in locations that count as available."""
    return round(sum(float(r["fields"].get("quantity") or 0) for r in lots(kernel, part=part) if is_available(kernel, r)), 6)


def on_hand(kernel, part) -> list[dict]:
    """Every live lot of the part with where it is and whether it is available."""
    out = []
    for r in lots(kernel, part=part):
        loc = kernel.get(r["fields"].get("location") or "")
        out.append({"lot": r, "location": loc, "available": is_available(kernel, r)})
    return out


def thresholds(bundle: dict | None) -> dict:
    from .routing import threshold

    b = bundle or {}
    return {"count_tolerance_pct": float(threshold(b, "count-variance-tolerance", 2) or 2),
            "adjust_threshold": float(threshold(b, "adjustment-approval-threshold", 500) or 500),
            "over_receipt_pct": float(threshold(b, "over-receipt-tolerance", 0) or 0)}


def open_ncr_for(kernel, lot: dict) -> dict | None:
    """An open nonconformance that names the lot (in its stock or what it quarantined) or that it was held for (a lot
    split off held stock keeps the hold's NCR)."""
    loc = lot["locator"]
    own = kernel.get(str(lot["fields"].get("ncr") or "")) if lot["fields"].get("ncr") else None
    if own is not None and own["type"] == "ncr" and own["state"] not in ("closed", "rejected", "cancelled"):
        return own
    for n in kernel.query(type_="ncr"):
        if n["state"] in ("closed", "rejected", "cancelled"):
            continue
        f = n["fields"]
        named = [str(x.get("lot") if isinstance(x, dict) else x) for x in (f.get("stock_items") or [])] + [str(x) for x in (f.get("quarantined") or [])]
        if loc in named or lot["number"] in named:
            return n
    return None


def held_for(kernel, lot: dict) -> dict | None:
    """The open nonconformance a quarantined lot is held for: while there is one, its disposition decides what happens
    to the lot (it is not split, merged, marked or released on its own)."""
    return open_ncr_for(kernel, lot) if lot["fields"].get("status") == "QUARANTINED" else None


# ---------------------------------------------------------------- the ledger
def _event(kernel, lot: dict, ev_type: str, *, set_: dict, by: str, run_id: str = "", release: str = "", reason: str = "",
           state: str | None = None, **data) -> dict:
    body = {"set": set_, "reason": reason, **{k: v for k, v in data.items() if v is not None}}
    if state:
        body["state"] = state
    kernel.append(ev_type, lot["locator"], body, run_id=run_id, release=release, by=by)
    return kernel.get(lot["locator"])


def history(kernel, lot) -> list[dict]:
    """The lot's ledger: every stock event on it, oldest first."""
    lot = lot_of(kernel, lot)
    return [e for e in kernel.events(aggregate=lot["locator"]) if str(e["type"]).startswith("stock.")]


def ledger_consistent(kernel, lot) -> bool:
    """The lot's quantity is what its last ledger event left (nothing changed it any other way)."""
    lot = lot_of(kernel, lot)
    evs = [e for e in history(kernel, lot) if "quantity" in (e["data"].get("set") or {})]
    last = float(evs[-1]["data"]["set"]["quantity"]) if evs else 0.0
    return abs(last - float(lot["fields"].get("quantity") or 0)) < 1e-9


def _live(lot: dict, what: str) -> None:
    if lot["state"] != LIVE:
        raise StockError(f"{lot['number']} is {lot['state']}: it cannot be {what}")


def _qty(value, what: str = "quantity") -> float:
    try:
        q = float(value)
    except (TypeError, ValueError):
        raise StockError(f"the {what} must be a number, not {value!r}") from None
    if q <= 0:
        raise StockError(f"the {what} must be more than nothing")
    return q


def tracking_problem(kernel, part, quantity, *, batch: str = "", serials: list[str] | None = None) -> str | None:
    """Why receiving this quantity of the part as given would be refused (a serial number for each one received, none
    repeated or already in stock; a batch for a lot-tracked part), or None. The receipt's check and the receive form
    ask this before anything is filed, so a receipt never fails at booking, after the buyer confirmed it."""
    p = part_of(kernel, part)
    q = float(quantity)
    track = p["fields"].get("trackable") or "none"
    serials = [str(s).strip() for s in (serials or []) if str(s).strip()]
    if track == "serial":
        if q != int(q) or len(serials) != int(q):
            return f"{p['number']} is serial-tracked: give one serial number for each of the {q:g} received ({len(serials)} given)"
        if len(set(serials)) != len(serials):
            return "the serial numbers repeat"
        taken = {r["fields"].get("serial") for r in kernel.query(type_="stock-item") if r["fields"].get("part") == p["locator"]}
        clash = sorted(set(serials) & taken)
        if clash:
            return f"serial number(s) {', '.join(clash)} of {p['number']} already exist"
    elif serials:
        return f"{p['number']} is not serial-tracked: give a batch, not serial numbers"
    if track == "lot" and not batch:
        return f"{p['number']} is lot-tracked: give the batch"
    return None


def over_receipt_problem(kernel, po, part, quantity, *, bundle: dict | None, receipt: str | None = None) -> str | None:
    """Why receiving this quantity of the part against the order would be refused: it is not on the order, or it is
    more than is outstanding beyond the over-receipt tolerance. None when it may be received."""
    po = po if isinstance(po, dict) else kernel.get(str(po))
    p = part_of(kernel, part)
    ordered = sum(float(l.get("quantity") or 0) for l in po["fields"].get("lines") or [] if isinstance(l, dict) and l.get("part") == p["locator"])
    if not ordered:
        return f"{p['number']} is not on {po['number']}"
    got = sum(float(l.get("quantity") or 0) for r in kernel.query(type_="receipt", state="received")
              if r["fields"].get("po") == po["locator"] and r["locator"] != receipt
              for l in r["fields"].get("lines") or [] if isinstance(l, dict) and l.get("part") == p["locator"])
    outstanding = max(ordered - got, 0.0)
    tol = thresholds(bundle)["over_receipt_pct"]
    if float(quantity) > outstanding * (1 + tol / 100.0) + 1e-9:
        return (f"{float(quantity):g} of {p['number']} is more than the {outstanding:g} outstanding on {po['number']}"
                + (f" (the over-receipt tolerance is {tol:g}%)" if tol else ""))
    return None


def receive(kernel, *, part, quantity, location, by: str, batch: str = "", serials: list[str] | None = None, expiry: str | None = None,
            unit_cost=None, currency: str = "USD", supplier_part=None, po=None, receipt=None, status: str = "OK",
            folder: str = STOCK_FOLDER, run_id: str = "", release: str = "") -> list[dict]:
    """Stock received into a location: one lot, or one lot per serial number for serial-tracked parts."""
    p = part_of(kernel, part)
    loc = location_of(kernel, location)
    holds_stock(loc)
    q = _qty(quantity)
    track = p["fields"].get("trackable") or "none"
    serials = [str(s).strip() for s in (serials or []) if str(s).strip()]
    problem = tracking_problem(kernel, p, q, batch=batch, serials=serials)
    if problem:
        raise StockError(problem)
    if status not in STATUSES:
        raise StockError(f"unknown status {status!r}")
    base = {"part": p["locator"], "batch": batch or None, "location": loc["locator"], "status": status, "quantity": 0,
            "expiry": expiry or None, "unit_cost": float(unit_cost) if unit_cost not in (None, "") else None, "currency": currency or "USD",
            "supplier_part": (kernel.get(str(supplier_part)) or {}).get("locator") if supplier_part else None,
            "po": (kernel.get(str(po)) or {}).get("locator") if po else None,
            "receipt": (kernel.get(str(receipt)) or {}).get("locator") if receipt else None}
    made = []
    for serial in (serials if track == "serial" else [None]):
        lot = kernel.create("stock-item", folder, {**{k: v for k, v in base.items() if v is not None}, **({"serial": serial} if serial else {})},
                            by=by, run_id=run_id, release=release, state=LIVE)
        each = 1.0 if serial else q
        lot = _event(kernel, lot, "stock.received", set_={"quantity": each, "count_baseline": each}, by=by, run_id=run_id, release=release,
                     reason=f"received{(' with ' + kernel.get(base['receipt'])['number']) if base.get('receipt') and kernel.get(base['receipt']) else ''}",
                     quantity=each, to=loc["locator"], receipt=base.get("receipt"), po=base.get("po"))
        if base.get("receipt"):
            kernel.link(lot["locator"], "received-in", base["receipt"], by=by, run_id=run_id, release=release)
        made.append(lot)
    return made


def transfer(kernel, lot, to_location, *, by: str, quantity=None, reason: str = "", run_id: str = "", release: str = "") -> dict:
    """Move a lot (or part of it, split off first) to another location on the same site. Between sites, stock goes
    through a transit location (two moves). Quarantined stock moves only between hold locations."""
    lot = lot_of(kernel, lot)
    _live(lot, "moved")
    to = location_of(kernel, to_location)
    holds_stock(to)
    frm = location_of(kernel, lot["fields"].get("location"))
    if to["locator"] == frm["locator"]:
        raise StockError(f"{lot['number']} is already in {location_path(kernel, to['locator'])}")
    kinds = {frm["fields"].get("kind"), to["fields"].get("kind")}
    if site_of(kernel, frm["locator"]) != site_of(kernel, to["locator"]) and "transit" not in kinds:
        raise StockError(f"{location_path(kernel, to['locator'])} is on another site: move the stock to a transit location first")
    if lot["fields"].get("status") == "QUARANTINED" and to["fields"].get("kind") not in HOLD_KINDS:
        raise StockError(f"{lot['number']} is quarantined: it leaves quarantine only through a stock release")
    if lot["fields"].get("status") != "QUARANTINED" and to["fields"].get("kind") in HOLD_KINDS:
        raise StockError(f"{location_path(kernel, to['locator'])} holds quarantined stock: quarantine {lot['number']} instead, and it then leaves only through a release")
    if to["fields"].get("kind") == "scrap":
        raise StockError("stock goes to scrap only through the disposition of a nonconformance")
    if quantity not in (None, "") and _qty(quantity) < float(lot["fields"].get("quantity") or 0):
        lot = split(kernel, lot, quantity, by=by, reason=reason or "split to move part of it", run_id=run_id, release=release)
    return _event(kernel, lot, "stock.moved", set_={"location": to["locator"]}, by=by, run_id=run_id, release=release, reason=reason,
                  quantity=float(lot["fields"].get("quantity") or 0), **{"from": frm["locator"]}, to=to["locator"])


def variance_of(lot: dict, counted) -> dict:
    before = float(lot["fields"].get("quantity") or 0)
    after = float(counted)
    if after < 0:
        raise StockError("a count cannot be below nothing")
    variance = after - before
    pct = (abs(variance) / before * 100.0) if before else (0.0 if after == 0 else 100.0)
    value = abs(variance) * float(lot["fields"].get("unit_cost") or 0)
    return {"before": before, "after": after, "variance": round(variance, 6), "variance_pct": round(pct, 4), "value": round(value, 2),
            "currency": lot["fields"].get("currency") or "USD"}


def count_baseline(kernel, lot) -> float:
    """What a free count is measured against: the quantity the lot's receipt or its latest approved adjustment set,
    carried through splits and merges (``count_baseline`` on the lot, written by those ledger events). A run of counts
    shares one tolerance from there, so counts of 2% each never add up to a write-off nobody approved; nor does
    splitting stock off, counting it down and merging it back: a split takes its share of the baseline in proportion,
    so the lot split off keeps the drift already counted, and a merge adds the baselines, not the quantities.
    Lots booked before the baseline was kept: the quantity the latest event other than a count set."""
    lot = lot_of(kernel, lot)
    if lot["fields"].get("count_baseline") is not None:
        return float(lot["fields"]["count_baseline"])
    for ev in reversed(history(kernel, lot)):
        q = (ev["data"].get("set") or {}).get("quantity")
        if q is not None and ev["type"] != "stock.counted":
            return float(q)
    return float(lot["fields"].get("quantity") or 0)


def drift_of(baseline: float, after: float) -> float:
    """The variance of ``after`` from the baseline, in percent of the baseline."""
    return round(abs(after - baseline) / baseline * 100.0, 4) if baseline else (0.0 if after == 0 else 100.0)


def count(kernel, lot, counted, *, bundle: dict | None, by: str, run_id: str = "", release: str = "", reason: str = "") -> dict:
    """A stock count. Within the count variance tolerance of the baseline (``count_baseline``: the counts since the
    lot's quantity was last set otherwise share it) it is booked at once; outside it, NeedsApproval (the adjustment
    goes through stock-adjustment, and an approved adjustment is the new baseline)."""
    lot = lot_of(kernel, lot)
    _live(lot, "counted")
    if lot["fields"].get("serial") and float(counted) not in (0.0, 1.0):
        raise StockError(f"{lot['number']} is one serial number: it counts 1 or 0")
    v = variance_of(lot, counted)
    tol = thresholds(bundle)["count_tolerance_pct"]
    base = count_baseline(kernel, lot)
    drift = drift_of(base, v["after"])
    if drift > tol + 1e-9:
        since = "" if abs(base - v["before"]) < 1e-9 else f" (the counts since it was last set to {base:g} share one tolerance)"
        raise NeedsApproval(f"counted {v['after']:g} against {v['before']:g}: a variance of {drift:.1f}% from {base:g}{since} is outside the "
                            f"count variance tolerance of {tol:g}%, so the adjustment needs an approval",
                            {**v, "tolerance_pct": tol, "baseline": base, "cumulative_pct": drift, "cumulative_variance": round(v["after"] - base, 6)})
    return _event(kernel, lot, "stock.counted", set_={"quantity": v["after"]}, state=("depleted" if v["after"] == 0 else None), by=by,
                  run_id=run_id, release=release, reason=reason or "counted", quantity=v["variance"], counted=v["after"], before=v["before"],
                  variance_pct=v["variance_pct"], baseline=base, cumulative_pct=drift, within_tolerance=True)


def adjust(kernel, lot, new_quantity, *, by: str, reason: str, adjustment: str | None = None, run_id: str = "", release: str = "") -> dict:
    """An approved adjustment (the stock-adjustment process's last step)."""
    lot = lot_of(kernel, lot)
    _live(lot, "adjusted")
    v = variance_of(lot, new_quantity)
    return _event(kernel, lot, "stock.adjusted", set_={"quantity": v["after"], "count_baseline": v["after"]}, state=("depleted" if v["after"] == 0 else None), by=by,
                  run_id=run_id, release=release, reason=reason, quantity=v["variance"], before=v["before"], after=v["after"], adjustment=adjustment)


def split(kernel, lot, quantity, *, by: str, reason: str = "", run_id: str = "", release: str = "") -> dict:
    """Split part of a lot off as a new lot (same part, batch, status and place), linked split-from."""
    lot = lot_of(kernel, lot)
    _live(lot, "split")
    q = _qty(quantity)
    have = float(lot["fields"].get("quantity") or 0)
    if lot["fields"].get("serial"):
        raise StockError(f"{lot['number']} is one serial number: it cannot be split")
    held_by = held_for(kernel, lot)
    if held_by:
        raise StockError(f"{lot['number']} is held for {held_by['number']}, which is open: its disposition decides what happens to it")
    if q >= have:
        raise StockError(f"cannot split {q:g} off {lot['number']}, which holds {have:g}")
    base = count_baseline(kernel, lot)
    child_base = round(q * base / have, 6)  # its share of the baseline: the drift counted so far splits with the stock
    keep = {k: v for k, v in lot["fields"].items() if k not in ("quantity", "parent", "count_baseline") and v not in (None, "")}
    child = kernel.create("stock-item", lot["folder"], {**keep, "quantity": 0, "parent": lot["locator"]}, by=by, run_id=run_id,
                          release=release, state=LIVE)
    _event(kernel, lot, "stock.split", set_={"quantity": round(have - q, 6), "count_baseline": round(base - child_base, 6)}, by=by,
           run_id=run_id, release=release, reason=reason or "split", quantity=-q, into=child["locator"])
    child = _event(kernel, child, "stock.split", set_={"quantity": q, "count_baseline": child_base}, by=by, run_id=run_id, release=release,
                   reason=reason or "split", quantity=q, from_lot=lot["locator"])
    kernel.link(child["locator"], "split-from", lot["locator"], by=by, run_id=run_id, release=release)
    return child


def merge(kernel, sources: list, into, *, by: str, reason: str = "", run_id: str = "", release: str = "") -> dict:
    """Merge lots into one: only the same part and batch, with the same status, in the same place; never serials."""
    target = lot_of(kernel, into)
    _live(target, "merged into")
    total = float(target["fields"].get("quantity") or 0)
    srcs = [lot_of(kernel, s) for s in sources]
    for s in srcs:
        if s["locator"] == target["locator"]:
            raise StockError("a lot cannot be merged into itself")
        _live(s, "merged")
        for k, what in (("part", "part"), ("batch", "batch"), ("status", "status"), ("location", "location")):
            if (s["fields"].get(k) or None) != (target["fields"].get(k) or None):
                raise StockError(f"{s['number']} and {target['number']} differ in {what}: only one batch of one part, with one status, in one place merges")
        if s["fields"].get("serial") or target["fields"].get("serial"):
            raise StockError("serial-numbered stock is never merged")
        if not s["fields"].get("batch"):
            raise StockError(f"{s['number']} has no batch: lots without a batch are not merged (their history would be lost)")
    for x in [target, *srcs]:
        held_by = held_for(kernel, x)
        if held_by:
            raise StockError(f"{x['number']} is held for {held_by['number']}, which is open: its disposition decides what happens to it")
    base = count_baseline(kernel, target)
    for s in srcs:
        q = float(s["fields"].get("quantity") or 0)
        total += q
        base += count_baseline(kernel, s)  # the baselines add, so drift counted on either lot stays counted
        _event(kernel, s, "stock.merged", set_={"quantity": 0, "count_baseline": 0}, state="depleted", by=by, run_id=run_id,
               release=release, reason=reason or "merged", quantity=-q, into=target["locator"])
        kernel.link(s["locator"], "merged-into", target["locator"], by=by, run_id=run_id, release=release)
    return _event(kernel, target, "stock.merged", set_={"quantity": round(total, 6), "count_baseline": round(base, 6)}, by=by,
                  run_id=run_id, release=release,
                  reason=reason or "merged", quantity=round(total - float(target["fields"].get("quantity") or 0), 6),
                  from_lots=[s["locator"] for s in srcs])


def quarantine(kernel, lot, *, by: str, reason: str, quantity=None, ncr=None, location=None, run_id: str = "", release: str = "") -> dict:
    """Hold stock (or the affected part of a lot, split off first) in quarantine: free, so containment never waits."""
    lot = lot_of(kernel, lot)
    _live(lot, "quarantined")
    if lot["fields"].get("status") == "QUARANTINED":
        return lot
    frm = location_of(kernel, lot["fields"].get("location"))
    if location:
        to = location_of(kernel, location)
    else:
        site = site_of(kernel, frm["locator"])
        to = location_of_kind(kernel, site, "quarantine") or location_of_kind(kernel, site, "mrb")
        if to is None:
            raise StockError(f"there is no quarantine location on {site}: add one before quarantining stock")
    holds_stock(to)
    if quantity not in (None, "") and _qty(quantity) < float(lot["fields"].get("quantity") or 0):
        lot = split(kernel, lot, quantity, by=by, reason="split off the affected quantity", run_id=run_id, release=release)
    ncr_rec = kernel.get(str(ncr)) if ncr else None
    out = _event(kernel, lot, "stock.quarantined", set_={"status": "QUARANTINED", "location": to["locator"], "quarantined_from": frm["locator"],
                                                         **({"ncr": ncr_rec["locator"]} if ncr_rec else {})},
                 by=by, run_id=run_id, release=release, reason=reason, quantity=float(lot["fields"].get("quantity") or 0),
                 **{"from": frm["locator"]}, to=to["locator"], ncr=ncr_rec["locator"] if ncr_rec else None)
    if ncr_rec:
        kernel.link(out["locator"], "quarantined-by", ncr_rec["locator"], by=by, run_id=run_id, release=release)
    return out


def release_from_quarantine(kernel, lot, *, by: str, reason: str, to_location=None, ncr_ok: bool = False,
                            run_id: str = "", release: str = "") -> dict:
    """Back to OK stock (an approved stock release, or a use-as-is disposition): never while an open nonconformance
    names the lot (unless that disposition is the one releasing it), never expired stock."""
    lot = lot_of(kernel, lot)
    _live(lot, "released")
    if lot["fields"].get("status") not in ("QUARANTINED", "ATTENTION", "DAMAGED"):
        raise StockError(f"{lot['number']} is {lot['fields'].get('status')}: there is nothing to release")
    if not ncr_ok:
        n = open_ncr_for(kernel, lot)
        if n:
            raise StockError(f"{lot['number']} is named by {n['number']}, which is open: it is released by that nonconformance's disposition")
    if expired(lot):
        raise StockError(f"{lot['number']} expired on {lot['fields']['expiry']}: expired stock is not released")
    frm = location_of(kernel, lot["fields"].get("location"))
    target = to_location or lot["fields"].get("quarantined_from")
    if not target:
        site = site_of(kernel, frm["locator"])
        store = location_of_kind(kernel, site, "store")
        target = store["locator"] if store else None
    if not target:
        raise StockError("say where the released stock goes")
    to = location_of(kernel, target)
    holds_stock(to)
    if to["fields"].get("kind") in HOLD_KINDS or to["fields"].get("kind") == "scrap":
        raise StockError(f"{location_path(kernel, to['locator'])} is not a place for released stock")
    return _event(kernel, lot, "stock.released", set_={"status": "OK", "location": to["locator"]}, by=by, run_id=run_id, release=release,
                  reason=reason, quantity=float(lot["fields"].get("quantity") or 0), **{"from": frm["locator"]}, to=to["locator"])


def scrap(kernel, lot, *, by: str, reason: str, run_id: str = "", release: str = "") -> dict:
    """Destroyed and written off (a scrap disposition): the quantity goes to nothing, the lot is kept as record."""
    lot = lot_of(kernel, lot)
    _live(lot, "scrapped")
    frm = lot["fields"].get("location")
    site = site_of(kernel, frm) if frm else ""
    to = location_of_kind(kernel, site, "scrap") if site else None
    q = float(lot["fields"].get("quantity") or 0)
    return _event(kernel, lot, "stock.scrapped", set_={"status": "DESTROYED", "quantity": 0, "count_baseline": 0, **({"location": to["locator"]} if to else {})},
                  state="scrapped", by=by, run_id=run_id, release=release, reason=reason, quantity=-q, **{"from": frm}, to=to["locator"] if to else None)


def return_to_supplier(kernel, lot, *, by: str, reason: str, run_id: str = "", release: str = "") -> dict:
    lot = lot_of(kernel, lot)
    _live(lot, "returned")
    q = float(lot["fields"].get("quantity") or 0)
    return _event(kernel, lot, "stock.returned", set_={"status": "RETURNED", "quantity": 0, "count_baseline": 0}, state="returned", by=by, run_id=run_id,
                  release=release, reason=reason, quantity=-q)


def rework(kernel, lot, *, by: str, reason: str, run_id: str = "", release: str = "") -> dict:
    """Sent for rework: it needs attention, and it is not available until it is released again."""
    lot = lot_of(kernel, lot)
    _live(lot, "sent for rework")
    frm = lot["fields"].get("location")
    site = site_of(kernel, frm) if frm else ""
    to = location_of_kind(kernel, site, "production") if site else None
    return _event(kernel, lot, "stock.reworked", set_={"status": "ATTENTION", **({"location": to["locator"]} if to else {})}, by=by,
                  run_id=run_id, release=release, reason=reason, quantity=float(lot["fields"].get("quantity") or 0),
                  **{"from": frm}, to=to["locator"] if to else None)


def mark(kernel, lot, status: str, *, by: str, reason: str, run_id: str = "", release: str = "") -> dict:
    """A status that takes stock out of use without leaving its place (ATTENTION, DAMAGED): free, like quarantine.
    Back to OK only through a stock release; LOST, DESTROYED and the others only through their processes."""
    lot = lot_of(kernel, lot)
    _live(lot, "marked")
    if status not in ("ATTENTION", "DAMAGED"):
        raise StockError(f"a lot is marked {status} only through its process (an adjustment, a disposition)")
    if lot["fields"].get("status") == "QUARANTINED":
        raise StockError(f"{lot['number']} is quarantined: its status changes only through a stock release or its nonconformance's disposition")
    return _event(kernel, lot, "stock.status", set_={"status": status}, by=by, run_id=run_id, release=release, reason=reason,
                  status=status, was=lot["fields"].get("status"))


def genealogy(kernel, lot) -> dict:
    """Where a lot came from and went: split from or into, merged from or into, the receipt, the NCR."""
    lot = lot_of(kernel, lot)
    links = kernel.links_of(lot["locator"])
    pick = lambda rel, direction: [l["target"] for l in links if l["rel"] == rel and l["direction"] == direction]
    return {"split_from": pick("split-from", "out"), "split_into": pick("split-from", "in"), "merged_into": pick("merged-into", "out"),
            "merged_from": pick("merged-into", "in"), "received_in": pick("received-in", "out"), "quarantined_by": pick("quarantined-by", "out")}


# ---------------------------------------------------------------- parts
def manufacturer_parts(kernel, part) -> list[dict]:
    p = part_of(kernel, part)["locator"]
    return [r for r in kernel.query(type_="manufacturer-part") if r["fields"].get("part") == p]


def supplier_parts(kernel, part, active_only: bool = True) -> list[dict]:
    p = part_of(kernel, part)["locator"]
    out = [r for r in kernel.query(type_="supplier-part") if r["fields"].get("part") == p and (not active_only or r["fields"].get("active", True))]
    return sorted(out, key=lambda r: (not r["fields"].get("primary"), r["number"]))


def unit_price(supplier_part: dict, quantity: float) -> float | None:
    """The price break for this quantity (the highest break at or under it); None when the supplier part has none."""
    breaks = sorted((float(b.get("quantity") or 0), float(b.get("price") or 0)) for b in supplier_part["fields"].get("price_breaks") or []
                    if isinstance(b, dict) and b.get("price") not in (None, ""))
    if not breaks:
        return None
    price = breaks[0][1]
    for q, pr in breaks:
        if quantity + 1e-9 >= q:
            price = pr
    return price


def find_part_by_mpn(kernel, mpn: str, manufacturer: str = "") -> dict | None:
    """The part a manufacturer part number belongs to (the approved manufacturer list), matching the manufacturer when given."""
    mpn_l, man_l = str(mpn or "").strip().lower(), str(manufacturer or "").strip().lower()
    if not mpn_l:
        return None
    candidates = [r for r in kernel.query(type_="manufacturer-part") if str(r["fields"].get("mpn") or "").strip().lower() == mpn_l]
    for r in candidates:
        if not man_l or str(r["fields"].get("manufacturer") or "").strip().lower() in ("", man_l):
            p = kernel.get(r["fields"].get("part") or "")
            if p:
                return p
    return None


def create_part(kernel, *, title: str, by: str, folder: str = "Inventory/Parts", state: str = "active", mpn: str = "", manufacturer: str = "",
                approved_manufacturer: bool = True, supplier: str | None = None, sku: str = "", unit_cost=None, currency: str = "USD",
                run_id: str = "", release: str = "", **fields) -> dict:
    """A part with its manufacturer part (the MPN) and, when a supplier is named, its supplier part."""
    p = kernel.create("part", folder, {"title": title, "units": fields.pop("units", "ea"), "purchaseable": fields.pop("purchaseable", True),
                                       "trackable": fields.pop("trackable", "none"), "active": fields.pop("active", True), **fields},
                      by=by, run_id=run_id, release=release, state=state)
    mp = None
    if mpn:
        mp = kernel.create("manufacturer-part", folder, {"part": p["locator"], "mpn": mpn, "manufacturer": manufacturer or "",
                                                        "approved": approved_manufacturer}, by=by, run_id=run_id, release=release, state="active")
    if supplier:
        kernel.create("supplier-part", folder, {"part": p["locator"], "manufacturer_part": mp["locator"] if mp else None, "supplier": supplier,
                                               "sku": sku or (mpn or title), "pack_quantity": 1, "currency": currency, "primary": True, "active": True,
                                               "price_breaks": ([{"quantity": 1, "price": float(unit_cost)}] if unit_cost not in (None, "") else [])},
                      by=by, run_id=run_id, release=release, state="active")
    return kernel.get(p["locator"])


# ---------------------------------------------------------------- sample data
def seed(kernel) -> None:
    """The sample site: a location tree (stores, receiving, inspection, quarantine, scrap, production, transit) and a
    few part categories. Parts and stock come in through purchasing and receiving."""
    if kernel.query(type_="stock-location"):
        return
    root = kernel.create("stock-location", "Inventory/Locations", {"name": "Main site", "kind": "store", "structural": True, "site": "MAIN",
                                                                   "description": "The plant and its stores."}, by="seed", state="active")
    for name, kind in (("Stores", "store"), ("Receiving", "receiving"), ("Inspection", "inspection"), ("Quarantine", "quarantine"),
                       ("Scrap", "scrap"), ("Production", "production"), ("Transit", "transit")):
        kernel.create("stock-location", "Inventory/Locations", {"name": name, "kind": kind, "parent": root["locator"]}, by="seed", state="active")
    electronics = kernel.create("part-category", "Inventory/Parts", {"name": "Electronics", "structural": True}, by="seed", state="active")
    for name in ("Resistors", "Capacitors", "Connectors"):
        kernel.create("part-category", "Inventory/Parts", {"name": name, "parent": electronics["locator"]}, by="seed", state="active")
    kernel.create("part-category", "Inventory/Parts", {"name": "Mechanical"}, by="seed", state="active")
    kernel.create("part-category", "Inventory/Parts", {"name": "Assemblies"}, by="seed", state="active")


def default_location(kernel, part=None, kind: str = "store") -> dict | None:
    """Where stock of a part goes by default: the part's, its category's, else the first location of this kind."""
    if part:
        p = part_of(kernel, part)
        for ref in (p["fields"].get("default_location"), (kernel.get(p["fields"].get("category") or "") or {}).get("fields", {}).get("default_location")):
            loc = kernel.get(ref) if ref else None
            if loc and not loc["fields"].get("structural"):
                return loc
    for r in locations(kernel):
        if r["fields"].get("kind") == kind and not r["fields"].get("structural"):
            return r
    return None
