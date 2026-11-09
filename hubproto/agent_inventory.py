"""Inventory tools for cogs: look up parts and stock; move, count and quarantine stock where that is free; ask for an
adjustment or a release from quarantine where it needs an approval.

Every write goes through hubproto.inventory's guards and is checked like the buttons in the hub: the cog and, when it
works for someone, that person too must hold the right in the policy release (``both_may``). A cog without custody of
stock (the purchasing assistant: it signs small write-offs, so it keeps no stock) is refused moves, counts and
adjustment requests, and says so; anyone on the staff, cog or person, may hold stock in quarantine. A count outside
the tolerance is never booked: the tool says so and the adjustment is asked for through the stock-adjustment process.
"""

from __future__ import annotations

from . import authz, inventory
from .agent import register


def _lot_intent(action: str, aliases=()):
    """What a stock tool would do, for the harness's decision: an action on stock items in the lot's folder ("move"
    and "count" are what the SOPs' "update stock items" covers)."""
    def intent(t, a: dict) -> dict:
        lot, err = _lot(t, a)
        if err:
            return err
        return {"action": action, "aliases": list(aliases), "object": "stock-item", "folder": lot["folder"], "checked": True}
    return intent


def _process_intent(process: str):
    def intent(t, a: dict) -> dict:
        lot, err = _lot(t, a)
        if err:
            return err
        proc = (t.bundle.get("processes") or {}).get(process) or {}
        return {"action": "create", "object": proc.get("object") or process, "folder": proc.get("folder") or lot["folder"], "checked": True}
    return intent

WORDS = {"update": "move or count", "quarantine": "quarantine"}


def _brief_lot(hub, r: dict) -> dict:
    f = r["fields"]
    loc = hub.kernel.get(f.get("location") or "")
    return {"lot": r["number"], "quantity": f.get("quantity"), "status": f.get("status"), "batch": f.get("batch"), "serial": f.get("serial"),
            "location": inventory.location_path(hub.kernel, loc["locator"]) if loc else None, "available": inventory.is_available(hub.kernel, r)}


def find_parts(t, a: dict) -> dict:
    text = str(a.get("query") or "").strip().lower()
    out = []
    for p in t.hub.kernel.query(type_="part"):
        if not t.may_read(p["folder"]):
            continue
        mps = inventory.manufacturer_parts(t.hub.kernel, p)
        hay = " ".join([p["number"], str(p["fields"].get("title") or ""), str(p["fields"].get("ipn") or ""), str(p["fields"].get("description") or "")]
                       + [f"{m['fields'].get('mpn')} {m['fields'].get('manufacturer')}" for m in mps]).lower()
        if text and not all(w in hay for w in text.split()):
            continue
        out.append({"part": p["number"], "title": p["fields"].get("title"), "state": p["state"], "units": p["fields"].get("units"),
                    "available": inventory.available(t.hub.kernel, p), "minimum_stock": p["fields"].get("minimum_stock"),
                    "mpns": [m["fields"].get("mpn") for m in mps]})
        if len(out) >= int(a.get("limit") or 15):
            break
    return {"count": len(out), "parts": out}


def stock_of(t, a: dict) -> dict:
    try:
        p = inventory.part_of(t.hub.kernel, a.get("part"))
    except inventory.StockError as e:
        return {"error": str(e)}
    if not t.may_read(p["folder"]):
        return {"error": f"no part {a.get('part')!r} that you may read"}
    lots = [_brief_lot(t.hub, r) for r in inventory.lots(t.hub.kernel, part=p) if t.may_read(r["folder"])]
    sps = [{"supplier_part": s["number"], "supplier": s["fields"].get("supplier"), "sku": s["fields"].get("sku"),
            "price_breaks": s["fields"].get("price_breaks") or []} for s in inventory.supplier_parts(t.hub.kernel, p)]
    return {"part": p["number"], "title": p["fields"].get("title"), "available": inventory.available(t.hub.kernel, p), "lots": lots, "supplier_parts": sps}


def _lot(t, a: dict):
    try:
        lot = inventory.lot_of(t.hub.kernel, a.get("lot"))
    except inventory.StockError as e:
        return None, {"error": str(e)}
    if not t.may_read(lot["folder"]):
        return None, {"error": f"no stock item {a.get('lot')!r} that you may read"}
    return lot, None


def _may(t, action: str, lot: dict) -> dict | None:
    mine = authz.check(t.bundle, t.cog, action, "stock-item", lot["folder"])
    if not mine["allowed"]:
        return {"error": f"{t.cog} keeps no stock: it may not {WORDS[action]} stock in {lot['folder']} ({mine['why']}). "
                         "A storekeeper does it on the lot's page in the hub.", "refused": True}
    allowed = t.both_may(action, "stock-item", lot["folder"])
    return None if allowed["allowed"] else {"error": allowed["why"], "refused": True}


def transfer_stock(t, a: dict) -> dict:
    lot, err = _lot(t, a)
    if err:
        return err
    refused = _may(t, "update", lot)
    if refused:
        return refused
    try:
        to = inventory.location_of(t.hub.kernel, a.get("to_location"))
        moved = inventory.transfer(t.hub.kernel, lot, to["locator"], quantity=a.get("quantity"), by=t.cog, reason=str(a.get("reason") or ""),
                                   run_id=f"agent-{t.run_id}")
    except inventory.StockError as e:
        return {"error": str(e)}
    return {"ok": True, **_brief_lot(t.hub, moved)}


def count_stock(t, a: dict) -> dict:
    lot, err = _lot(t, a)
    if err:
        return err
    refused = _may(t, "update", lot)
    if refused:
        return refused
    try:
        counted = inventory.count(t.hub.kernel, lot, a.get("counted"), bundle=t.bundle, by=t.cog, run_id=f"agent-{t.run_id}",
                                  reason=str(a.get("reason") or "counted"))
    except inventory.NeedsApproval as e:
        return {"error": str(e), "needs_approval": True, "next": "request_stock_adjustment with the counted quantity and a reason", **e.info}
    except inventory.StockError as e:
        return {"error": str(e)}
    return {"ok": True, **_brief_lot(t.hub, counted)}


def quarantine_stock(t, a: dict) -> dict:
    lot, err = _lot(t, a)
    if err:
        return err
    refused = _may(t, "quarantine", lot)
    if refused:
        return refused
    reason = str(a.get("reason") or "").strip()
    if not reason:
        return {"error": "say why the stock is held (reason)"}
    try:
        held = inventory.quarantine(t.hub.kernel, lot, quantity=a.get("quantity"), by=t.cog, reason=reason, run_id=f"agent-{t.run_id}")
    except inventory.StockError as e:
        return {"error": str(e)}
    return {"ok": True, **_brief_lot(t.hub, held)}


def _start(t, process: str, record: dict) -> dict:
    hub = t.hub
    owner = t.for_whom or t.cog
    why = hub.runner.may_start(process, owner)
    if why:
        return {"error": why}
    if t.for_whom:
        mine = hub.runner.may_start(process, t.cog)
        if mine:
            return {"error": f"{t.cog} cannot file a {process} for {t.for_whom}: {mine}. {t.for_whom} files it on the lot's page in the hub.",
                    "refused": True}
    try:
        run_id = hub.runner.submit(process, {"record": record}, owner=owner)
    except Exception as e:  # the runner says what is missing or refused
        return {"error": str(e)}
    st = hub.runner.load(run_id)
    rec = hub.kernel.get(st["record"]) if st.get("record") else None
    return {"ok": True, "process": process, "record": rec["number"] if rec else None, "status": st["status"],
            "waiting_for": (st.get("pending") or {}).get("assignees"), "reason": (st.get("pending") or {}).get("title")}


def request_stock_adjustment(t, a: dict) -> dict:
    lot, err = _lot(t, a)
    if err:
        return err
    if a.get("new_quantity") in (None, ""):
        return {"error": "say the quantity the lot should hold (new_quantity)"}
    reason = str(a.get("reason") or "").strip()
    if not reason:
        return {"error": "say why (reason)"}
    return _start(t, "stock-adjustment", {"lot": lot["locator"], "new_quantity": a["new_quantity"], "reason": reason})


def request_stock_release(t, a: dict) -> dict:
    lot, err = _lot(t, a)
    if err:
        return err
    reason = str(a.get("reason") or "").strip()
    if not reason:
        return {"error": "say why it may be released (reason)"}
    rec = {"lot": lot["locator"], "reason": reason}
    if a.get("to_location"):
        try:
            rec["to_location"] = inventory.location_of(t.hub.kernel, a["to_location"])["locator"]
        except inventory.StockError as e:
            return {"error": str(e)}
    return _start(t, "stock-release", rec)


register({"name": "find_parts", "description": "Find parts by words in their number, title, IPN or manufacturer part numbers; "
          "each with what is available and its minimum stock.",
          "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}}}, find_parts, write=False)
register({"name": "stock_of", "description": "A part's stock: every lot with its quantity, status, batch or serial and location; what is "
          "available; its supplier parts and price breaks.",
          "parameters": {"type": "object", "properties": {"part": {"type": "string"}}, "required": ["part"]}}, stock_of, write=False)
register({"name": "transfer_stock", "description": "Move a lot (or part of it) to another location on the same site. Free for storekeepers (you "
          "too need custody of the stock); quarantined stock moves only between hold locations; between sites go through a transit location.",
          "parameters": {"type": "object", "properties": {"lot": {"type": "string"}, "to_location": {"type": "string"},
                                                          "quantity": {"type": "number"}, "reason": {"type": "string"}},
                         "required": ["lot", "to_location"]}}, transfer_stock, write=True, intent=_lot_intent("move", ["update"]))
register({"name": "count_stock", "description": "Book a stock count. Within the count variance tolerance it is booked at once; outside it the "
          "tool refuses and you ask for an adjustment (request_stock_adjustment), which needs an approval.",
          "parameters": {"type": "object", "properties": {"lot": {"type": "string"}, "counted": {"type": "number"}, "reason": {"type": "string"}},
                         "required": ["lot", "counted"]}}, count_stock, write=True, intent=_lot_intent("count", ["update"]))
register({"name": "quarantine_stock", "description": "Hold a lot (or the affected quantity, split off) in quarantine. Free: containment never "
          "waits. It leaves quarantine only through a stock release or a nonconformance's disposition.",
          "parameters": {"type": "object", "properties": {"lot": {"type": "string"}, "quantity": {"type": "number"}, "reason": {"type": "string"}},
                         "required": ["lot", "reason"]}}, quarantine_stock, write=True, intent=_lot_intent("quarantine"))
register({"name": "request_stock_adjustment", "description": "Ask for a stock adjustment (a count outside tolerance, a loss, a correction): it "
          "goes through stock-adjustment, routed by value; the requester never approves it.",
          "parameters": {"type": "object", "properties": {"lot": {"type": "string"}, "new_quantity": {"type": "number"}, "reason": {"type": "string"}},
                         "required": ["lot", "new_quantity", "reason"]}}, request_stock_adjustment, write=True, intent=_process_intent("stock-adjustment"))
register({"name": "request_stock_release", "description": "Ask for a lot to be released from quarantine (stock-release, approved by the quality "
          "manager); refused while an open nonconformance names the lot.",
          "parameters": {"type": "object", "properties": {"lot": {"type": "string"}, "to_location": {"type": "string"}, "reason": {"type": "string"}},
                         "required": ["lot", "reason"]}}, request_stock_release, write=True, intent=_process_intent("stock-release"))
