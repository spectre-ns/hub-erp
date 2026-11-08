"""Inventory in the shell, after InvenTree: the parts with what is available, the location tree, and on the record
pages of parts, lots, locations, purchase orders, receipts and nonconformances what inventory knows about them.

The free movements (transfer within a site, count within the tolerance, split, merge within a batch, quarantine, mark
ATTENTION or DAMAGED) are forms on a lot's page; the gated ones are started as processes: an adjustment, a release from
quarantine, and receiving an order (goods-receipt, which the buyer confirms before the stock is booked). Every action
is checked like the cogs' tools and the rest of the shell: the person needs the right in the release in force (update
or quarantine stock items in the lot's folder, or the right to start the process), and the inventory module's guards
say what the stock allows. A count outside the tolerance is never booked: the page offers the adjustment instead.
"""

from __future__ import annotations

import math
from datetime import date
from urllib.parse import quote

from .. import authz, inventory
from ..kernel import KernelGuardError
from ..runner import RunError
from .forms import ShellError, text_input, widget
from .handler import details, hidden_as, href, kv, log_line, table
from .icons import icon
from .text import chip, day, esc, money, qty, short, when

STATUS_CLS = {"OK": "ok", "ATTENTION": "warn", "QUARANTINED": "warn", "RETURNED": "warn", "DAMAGED": "crit", "DESTROYED": "crit",
              "REJECTED": "crit", "LOST": "crit"}
MARKABLE = ("ATTENTION", "DAMAGED")
RELEASABLE = ("QUARANTINED", "ATTENTION", "DAMAGED")
ACTION_WORDS = {"update": "move or count", "quarantine": "quarantine"}


def _number(ref) -> str:
    return str(ref or "").rsplit(":", 1)[-1]


class InventoryMixin:
    # ------------------------------------------------------------ routing
    def inventory_route(self, method: str, r: str):
        """The inventory pages and actions (handler._route asks here for what it does not know)."""
        if method == "GET":
            return {"/inventory": self.page_inventory, "/inventory/locations": self.page_inventory_locations}.get(r)
        return {"/stock-transfer": self.post_stock_transfer, "/stock-count": self.post_stock_count, "/stock-split": self.post_stock_split,
                "/stock-merge": self.post_stock_merge, "/stock-quarantine": self.post_stock_quarantine, "/stock-mark": self.post_stock_mark,
                "/po-receive": self.post_po_receive}.get(r)

    # ------------------------------------------------------------ small pieces
    def _stock_chip(self, lot: dict) -> str:
        s = lot["fields"].get("status") or "OK"
        out = chip(s, STATUS_CLS.get(s, ""))
        if lot["state"] != inventory.LIVE:
            out += " " + chip(lot["state"], "")
        if inventory.expired(lot):
            out += " " + chip("expired", "crit", title=f"expired on {day(lot['fields'].get('expiry'))}")
        return out

    def _loc_link(self, ref) -> str:
        loc = self.hub.kernel.get(str(ref)) if ref else None
        if loc is None or loc["type"] != "stock-location":
            return esc(_number(ref)) if ref else "<span class='muted'>nowhere</span>"
        return f'<a href="{href("/inventory/locations", self.user, loc=loc["number"])}">{esc(inventory.location_path(self.hub.kernel, loc["locator"]))}</a>'

    def _units(self, part: dict | None) -> str:
        return (part or {}).get("fields", {}).get("units") or ""

    def _stock_may(self, action: str, lot: dict) -> dict:
        return authz.check(self._need_release(), self.user, action, "stock-item", lot["folder"])

    def _may_start(self, process: str) -> str:
        """Empty when the person may start the process; otherwise why not."""
        if process not in (self.bundle or {}).get("ops", {}):
            return f"the release in force has no process {process}"
        return self.hub.runner.may_start(process, self.user)

    def _category_path(self, ref) -> str:
        names, cur, seen = [], self.hub.kernel.get(str(ref)) if ref else None, set()
        while cur and cur["locator"] not in seen:
            seen.add(cur["locator"])
            names.insert(0, cur["fields"].get("name") or cur["number"])
            cur = self.hub.kernel.get(cur["fields"].get("parent") or "") if cur["fields"].get("parent") else None
        return " / ".join(names)

    def _lots_table(self, lots: list[dict], *, part: bool = True, location: bool = True, empty: str = "No stock.") -> str:
        k = self.hub.kernel
        parts: dict[str, dict | None] = {}
        rows = []
        for r in lots:
            f = r["fields"]
            if f.get("part") not in parts:
                parts[f.get("part")] = k.get(f.get("part") or "")
            p = parts[f.get("part")]
            row = [self._record_link(r["locator"], with_summary=False)]
            if part:
                row.append(self._record_link(f.get("part"), with_summary=True) if p else esc(_number(f.get("part"))))
            row += [f'<span class="num">{esc(qty(f.get("quantity"), self._units(p)))}</span>', self._stock_chip(r),
                    esc(f.get("batch") or (f"SN {f['serial']}" if f.get("serial") else ""))]
            if location:
                row.append(self._loc_link(f.get("location")))
            row.append(esc(day(f.get("expiry"))))
            rows.append(row)
        heads = ["Lot"] + (["Part"] if part else []) + ["Quantity", "Status", "Batch or serial"] + (["Location"] if location else []) + ["Expiry"]
        return table(heads, rows, empty)

    def _refused_here(self, *routes: str) -> bool:
        return bool(self.err) and self.route in routes

    # ------------------------------------------------------------ the parts list
    def page_inventory(self, status: int = 200):
        user = self.user
        self._need_release()
        k = self.hub.kernel
        text = (self.q.get("q", [""])[0] or "").strip().lower()
        cat = self.q.get("category", [""])[0]
        low_only = self.q.get("low", [""])[0] == "1"
        live = [r for r in k.query(type_="stock-item") if r["state"] == inventory.LIVE]
        avail: dict[str, float] = {}
        on_hand: dict[str, float] = {}
        for r in live:
            p, q = r["fields"].get("part"), float(r["fields"].get("quantity") or 0)
            on_hand[p] = on_hand.get(p, 0.0) + q
            if inventory.is_available(k, r):
                avail[p] = avail.get(p, 0.0) + q
        mpns: dict[str, list[str]] = {}
        for m in k.query(type_="manufacturer-part"):
            mpns.setdefault(m["fields"].get("part"), []).append(f"{m['fields'].get('manufacturer') or ''} {m['fields'].get('mpn') or ''}".strip())
        cats = sorted(((c["locator"], self._category_path(c["locator"])) for c in k.query(type_="part-category")), key=lambda x: x[1])
        under = lambda ref: ref == cat or self._category_path(ref).startswith(self._category_path(cat) + " / ")
        rows, low, shown = [], 0, 0
        parts = [p for p in k.query(type_="part") if self._readable(p)]
        for p in sorted(parts, key=lambda r: r["number"]):
            f = p["fields"]
            a, h = round(avail.get(p["locator"], 0.0), 6), round(on_hand.get(p["locator"], 0.0), 6)
            minimum = float(f.get("minimum_stock") or 0)
            is_low = bool(minimum) and a < minimum
            low += is_low
            hay = " ".join([p["number"], str(f.get("title") or ""), str(f.get("ipn") or ""), str(f.get("description") or ""),
                            str(f.get("keywords") or "")] + mpns.get(p["locator"], [])).lower()
            if text and not all(w in hay for w in text.split()):
                continue
            if cat and not (f.get("category") and under(f["category"])):
                continue
            if low_only and not is_low:
                continue
            shown += 1
            state = chip(p["state"]) + ("" if f.get("active", True) is not False else " " + chip("inactive", "crit"))
            rows.append([self._record_link(p["locator"], with_summary=False), esc(short(f.get("title") or "", 80)),
                         esc(self._category_path(f.get("category"))) if f.get("category") else "", esc(f.get("ipn") or ""),
                         f'<span class="num"><b>{esc(qty(a, f.get("units")))}</b></span>' + (" " + chip("low", "crit") if is_low else ""),
                         f'<span class="num">{esc(qty(h, f.get("units")))}</span>', esc(qty(minimum, f.get("units"))) if minimum else "", state])
        held = sum(1 for r in live if r["fields"].get("status") == "QUARANTINED")
        kpis = (f'<div class="kpis"><a class="kpi" href="{href("/items/part", user)}"><span class="kpi-h">{icon("box")}Parts</span><b>{len(parts)}</b></a>'
                f'<a class="kpi" href="{href("/items/stock-item", user)}"><span class="kpi-h">{icon("package")}Lots in stock</span><b>{len(live)}</b></a>'
                f'<a class="kpi{" crit" if held else ""}" href="{href("/inventory/locations", user)}"><span class="kpi-h">{icon("alert")}Quarantined lots</span><b>{held}</b></a>'
                f'<a class="kpi{" crit" if low else ""}" href="{href("/inventory", user, low="1")}"><span class="kpi-h">{icon("arrow-down")}Below minimum</span><b>{low}</b></a></div>')
        opts = "".join(f'<option value="{esc(c)}"{" selected" if c == cat else ""}>{esc(name)}</option>' for c, name in cats)
        filters = (f'<form method="get" action="/inventory" class="filterbar">{hidden_as(user)}'
                   f'<label class="f"><span>Find</span><input name="q" value="{esc(text)}" placeholder="number, title, IPN or MPN"></label>'
                   f'<label class="f"><span>Category</span><select name="category"><option value="">any</option>{opts}</select></label>'
                   f'<label><input type="checkbox" name="low" value="1"{" checked" if low_only else ""}> below minimum only</label>'
                   f'<button class="secondary">{icon("filter")}Filter</button></form>')
        starts = ""
        for process, verb in (("part-request", "Request a part"), ("purchase-request", "Raise a purchase request")):
            if not self._may_start(process):
                starts += f'<a class="button ghost" href="{href("/", user, start=process)}#start-{process}">{icon("plus")}{esc(verb)}</a>'
        body = (f"<h1>{icon('box', cls='lg')} Parts and stock</h1><p class='why'>Available counts OK lots in stock, not expired, in locations "
                "that count as available (not quarantine, inspection, scrap or transit). Open a part for its stock by location and its "
                f"suppliers, or <a href='{href('/inventory/locations', user)}'>browse the locations</a>.</p>{kpis}"
                f"<div class='toolbar'>{starts}</div>{filters}"
                + table(["Part", "Title", "Category", "IPN", "Available", "On hand", "Minimum", "State"], rows,
                        "No parts match." if (text or cat or low_only) else "No parts yet: they come from released designs and part requests.")
                + (f"<p class='why'>{shown} of {len(parts)} parts shown.</p>" if shown != len(parts) else ""))
        self._page("Parts and stock", body, status=status, crumbs=[("Inventory", href("/app/inventory", user)), ("Parts and stock", None)])

    # ------------------------------------------------------------ the location tree
    def page_inventory_locations(self, status: int = 200):
        user = self.user
        self._need_release()
        k = self.hub.kernel
        locs = [r for r in inventory.locations(k) if self._readable(r)]
        children: dict[str, list[dict]] = {}
        for r in locs:
            children.setdefault(r["fields"].get("parent") or "", []).append(r)
        live = [r for r in k.query(type_="stock-item") if r["state"] == inventory.LIVE and self._readable(r)]
        here: dict[str, list[dict]] = {}
        for r in live:
            here.setdefault(r["fields"].get("location") or "", []).append(r)
        sel = self.q.get("loc", [""])[0]
        current = None
        if sel:
            try:
                current = inventory.location_of(k, sel)
            except inventory.StockError:
                raise ShellError(f"There is no stock location {sel!r}.", 404) from None
            self._require_read(current["folder"])
        elif locs:
            current = (children.get("") or locs)[0]

        def under(loc: dict) -> list[dict]:
            out, stack = [], [loc]
            while stack:
                cur = stack.pop()
                out += here.get(cur["locator"], [])
                stack += children.get(cur["locator"], [])
            return out

        def branch(parent: str) -> str:
            items = ""
            for r in children.get(parent, []):
                f = r["fields"]
                n = len(under(r))
                cur = ' aria-current="page"' if current and r["locator"] == current["locator"] else ""
                ic = "layers" if f.get("structural") else ("alert" if f.get("kind") in inventory.HOLD_KINDS else "map-pin")
                items += (f'<li><a{cur} href="{href("/inventory/locations", user, loc=r["number"])}">{icon(ic)}{esc(f.get("name") or r["number"])}'
                          f'{(" <span class=muted>(" + str(n) + ")</span>") if n else ""}</a>{branch(r["locator"])}</li>')
            return f"<ul>{items}</ul>" if items else ""

        tree = f'<nav class="ftree" aria-label="Locations">{branch("") or "<p class=muted>No locations.</p>"}</nav>'
        if current is None:
            main = "<p class='muted'>There are no stock locations yet.</p>"
            title = "Locations"
        else:
            f = current["fields"]
            title = inventory.location_path(k, current["locator"])
            facts = kv([("Kind", esc(f.get("kind") or "")), ("Site", esc(inventory.site_of(k, current["locator"]))),
                        ("Holds stock", "no: it is structural, it groups the locations under it" if f.get("structural") else "yes"),
                        ("Counts as available", chip("yes") if inventory.counts_as_available(current) else chip("no", "warn",
                                                                                                                title="stock here is not available")),
                        ("Record", self._record_link(current["locator"], with_summary=False)), ("Description", esc(f.get("description") or ""))])
            subs = children.get(current["locator"], [])
            sub_rows = [[self._loc_link(s["locator"]), esc(s["fields"].get("kind") or ""), str(len(here.get(s["locator"], []))), str(len(under(s)))] for s in subs]
            lots_here = here.get(current["locator"], [])
            main = (f"<h2 style='margin-top:0'>{icon('map-pin')}{esc(title)}</h2>{facts}"
                    + (("<h3>Locations in it</h3>" + table(["Location", "Kind", "Lots here", "Lots in and under it"], sub_rows)) if subs else "")
                    + (f"<h3>Stock here</h3>{self._lots_table(lots_here, location=False, empty='Nothing is held here.')}" if not f.get("structural") else
                       f"<h3>Stock under it</h3>{self._lots_table(under(current), empty='Nothing is held under it.')}"))
        body = (f"<h1>{icon('map-pin', cls='lg')} Locations</h1><p class='why'>Stock locations form a tree. A structural location only groups "
                "others and holds no stock; quarantine and MRB hold stock that is not available until it is released.</p>"
                f"<div class='library'>{tree}<div class='card'>{main}</div></div>")
        self._page("Locations", body, status=status, crumbs=[("Inventory", href("/app/inventory", user)), ("Parts and stock", href("/inventory", user)),
                                                             ("Locations", None)])

    # ------------------------------------------------------------ panels on record pages
    def _inventory_panel(self, rec: dict) -> str:
        """What inventory knows about a record, shown on its page (pages.page_record)."""
        if not self.bundle:
            return ""
        t = rec["type"]
        try:
            if t == "part":
                return self._part_panel(rec)
            if t == "stock-item":
                return self._lot_panel(rec)
            if t == "stock-location":
                return self._location_panel(rec)
            if t == "purchase-order":
                return self._po_receive_panel(rec)
            if t == "receipt":
                lots = [self.hub.kernel.get(x) for x in rec["fields"].get("lots") or []]
                lots = [x for x in lots if x]
                return (f"<section class='card' id='stock'><h2 style='margin-top:0'>{icon('package')}Lots booked</h2>"
                        + self._lots_table(lots, empty="Nothing is booked yet: the stock is booked once the receipt is confirmed.") + "</section>")
            if t == "ncr":
                html = self._ncr_stock_html(rec["fields"])
                return f"<section class='card' id='stock'>{html}</section>" if html else ""
        except inventory.StockError as e:  # a record that points at something gone: say so, never break the page
            return f"<div class='card warn' role='status'>{esc(str(e))}</div>"
        return ""

    def _ncr_stock_html(self, f: dict) -> str:
        k = self.hub.kernel
        held = [k.get(str(x)) for x in f.get("quarantined") or []]
        held = [x for x in held if x]
        out = ""
        if held:
            out += f"<h4>{icon('alert')}Quarantined stock</h4>" + self._lots_table(held)
        else:
            named = [(x.get("lot") if isinstance(x, dict) else x, x.get("quantity") if isinstance(x, dict) else None) for x in f.get("stock_items") or []]
            if named:
                out += "<h4>Stock affected</h4>" + table(["Lot", "Quantity affected"], [[self._record_link(str(l) if str(l).startswith("rec:") else f"rec:stock-item:{l}"),
                                                                                         esc(qty(q)) if q else "all of it"] for l, q in named])
        applied = [x for x in f.get("disposition_applied") or [] if isinstance(x, dict)]
        if applied:
            out += "<h4>Disposition applied</h4>" + table(["Lot", "Now", "Location"], [[self._record_link(x.get("lot")), chip(x.get("status"), STATUS_CLS.get(str(x.get("status")), "")),
                                                                                      self._loc_link(x.get("location"))] for x in applied])
        return out

    def _part_panel(self, part: dict) -> str:
        user, k = self.user, self.hub.kernel
        f = part["fields"]
        units = f.get("units") or ""
        lots = [r for r in inventory.lots(k, part=part) if self._readable(r)]
        avail = inventory.available(k, part)
        minimum = float(f.get("minimum_stock") or 0)
        by_loc: dict[str, list[dict]] = {}
        for r in lots:
            by_loc.setdefault(r["fields"].get("location") or "", []).append(r)
        loc_rows = [[self._loc_link(loc), str(len(rs)), f'<span class="num">{esc(qty(sum(float(x["fields"].get("quantity") or 0) for x in rs), units))}</span>',
                     f'<span class="num">{esc(qty(sum(float(x["fields"].get("quantity") or 0) for x in rs if inventory.is_available(k, x)), units))}</span>']
                    for loc, rs in sorted(by_loc.items(), key=lambda kv_: self._loc_path_safe(kv_[0]))]
        sps = inventory.supplier_parts(k, part, active_only=False)
        sp_rows = [[self._record_link(s["locator"], with_summary=False), esc(self._supplier_name(s["fields"].get("supplier")) if s["fields"].get("supplier") else ""),
                    esc(s["fields"].get("sku") or ""), esc(qty(s["fields"].get("pack_quantity") or 1)), self._price_breaks(s),
                    esc(f"{s['fields']['lead_time_days']} days") if s["fields"].get("lead_time_days") not in (None, "") else "",
                    (chip("primary", "ok") + " " if s["fields"].get("primary") else "") + ("" if s["fields"].get("active", True) else chip("inactive", "crit"))]
                   for s in sps]
        mps = inventory.manufacturer_parts(k, part)
        mp_rows = [[self._record_link(m["locator"], with_summary=False), esc(m["fields"].get("manufacturer") or ""), esc(m["fields"].get("mpn") or ""),
                    chip("approved") if m["fields"].get("approved", True) else chip("not approved", "crit")] for m in mps]
        low = bool(minimum) and avail < minimum
        head = (f"<div class='kpis'><div class='kpi'><span class='kpi-h'>{icon('package')}Available</span><b>{esc(qty(avail, units))}</b></div>"
                f"<div class='kpi'><span class='kpi-h'>{icon('box')}On hand</span><b>{esc(qty(sum(float(r['fields'].get('quantity') or 0) for r in lots), units))}</b></div>"
                f"<div class='kpi{' crit' if low else ''}'><span class='kpi-h'>{icon('arrow-down')}Minimum stock</span><b>{esc(qty(minimum, units)) if minimum else '-'}</b></div></div>")
        notes = []
        if not inventory.part_active(part):
            notes.append(f"{part['number']} is {part['state']}" + ("" if f.get("active", True) is not False else ", inactive")
                         + ("" if f.get("purchaseable", True) is not False else ", not purchaseable") + ": it is not ordered within the cogs' criteria.")
        if low:
            notes.append(f"Below its minimum stock by {qty(minimum - avail, units)}.")
        out = (f"<section class='card' id='stock'><h2 style='margin-top:0'>{icon('package')}Stock</h2>{head}"
               + "".join(f"<p class='why'>{esc(n)}</p>" for n in notes)
               + "<h3>By location</h3>" + table(["Location", "Lots", "On hand", "Available"], loc_rows, "No stock of this part.")
               + details(f"Every lot ({len(lots)})", self._lots_table(lots, part=False), open_=len(lots) <= 8)
               + f"<h3>{icon('truck')}Suppliers</h3>" + table(["Supplier part", "Supplier", "SKU", "Pack", "Price breaks", "Lead time", ""], sp_rows,
                                                          "No supplier part: the part cannot be ordered until one is added.")
               + f"<h3>{icon('cpu')}Manufacturers</h3>" + table(["Manufacturer part", "Manufacturer", "MPN", "Approved"], mp_rows, "No manufacturer part.")
               + self._purchase_request_form(part, sps, avail, minimum) + "</section>")
        return out

    def _loc_path_safe(self, ref: str) -> str:
        try:
            return inventory.location_path(self.hub.kernel, ref) if ref else ""
        except inventory.StockError:
            return ref

    def _price_breaks(self, sp: dict) -> str:
        cur = sp["fields"].get("currency") or "USD"
        breaks = sorted((b for b in sp["fields"].get("price_breaks") or [] if isinstance(b, dict)), key=lambda b: float(b.get("quantity") or 0))
        return "; ".join(f"{esc(qty(b.get('quantity') or 1))}+: {esc(money(b.get('price'), cur))}" for b in breaks) or "<span class='muted'>none</span>"

    def _purchase_request_form(self, part: dict, sps: list[dict], avail: float, minimum: float) -> str:
        if self._viewing_cog() or self._may_start("purchase-request") or not inventory.part_active(part):
            return ""
        user = self.user
        active = [s for s in sps if s["fields"].get("active", True)]
        need = max(minimum - avail, 0) or float((active[0]["fields"].get("pack_quantity") if active else 1) or 1)
        opts = [(s["locator"], f"{s['number']} {s['fields'].get('sku') or ''} from {self._supplier_name(s['fields'].get('supplier'))}") for s in active]
        return details(f"{icon('cart')}Raise a purchase request",
                       f"""<form method="post" action="/start">{hidden_as(user)}{self._once()}<input type="hidden" name="process" value="purchase-request">
<input type="hidden" name="part" value="{esc(part['locator'])}"><input type="hidden" name="title" value="{esc(part['fields'].get('title') or part['number'])}">
<div class="fields">{widget("supplier_part", "ref", active[0]["locator"] if active else "", options=opts, label="Supplier part")}
{widget("quantity", "number", qty(need).replace(",", ""), required=True, label="Quantity")}
{widget("needed_by", "date", "", label="Needed by")}{text_input("reason", "Why", "below minimum stock" if avail < minimum else "")}</div>
<div class="actions"><button>{icon('cart')}Raise the request</button><span class="why">It goes through purchase-request: the supplier check, the risk score and the approval its value needs.</span></div></form>""",
                       cls="card quiet")

    def _location_panel(self, loc: dict) -> str:
        k = self.hub.kernel
        lots = [r for r in inventory.lots(k, location=loc) if self._readable(r)]
        chain = inventory.location_chain(k, loc["locator"])
        path = " / ".join(self._loc_link(c["locator"]) for c in chain)
        subs = [r for r in inventory.locations(k) if r["fields"].get("parent") == loc["locator"]]
        return (f"<section class='card' id='stock'><h2 style='margin-top:0'>{icon('map-pin')}Stock here</h2><p>{path}</p>"
                + (f"<p class='why'>Locations in it: {', '.join(self._loc_link(s['locator']) for s in subs)}</p>" if subs else "")
                + ("<p class='why'>A structural location holds no stock; it groups the locations under it.</p>" if loc["fields"].get("structural") else
                   self._lots_table(lots, location=False, empty="Nothing is held here."))
                + "</section>")

    # ------------------------------------------------------------ a lot's page
    def _lot_panel(self, lot: dict) -> str:
        k, user = self.hub.kernel, self.user
        f = lot["fields"]
        part = k.get(f.get("part") or "")
        units = self._units(part)
        ncr = inventory.open_ncr_for(k, lot)
        avail = inventory.is_available(k, lot)
        why_not = ""
        if not avail:
            loc = k.get(f.get("location") or "")
            why_not = ("it is " + lot["state"] if lot["state"] != inventory.LIVE else f"its status is {f.get('status')}" if f.get("status", "OK") != "OK"
                       else "it has expired" if inventory.expired(lot) else "nothing is left" if not float(f.get("quantity") or 0)
                       else f"{loc['fields'].get('name') if loc else 'its location'} does not count as available")
        facts = kv([("Part", self._record_link(f.get("part")) if part else esc(_number(f.get("part")))),
                    ("Quantity", f"<b>{esc(qty(f.get('quantity'), units))}</b>"), ("Status", self._stock_chip(lot)),
                    ("Location", self._loc_link(f.get("location"))), ("Batch", esc(f.get("batch") or "")), ("Serial number", esc(f.get("serial") or "")),
                    ("Expiry", esc(day(f.get("expiry")))), ("Available", chip("yes") if avail else chip("no", "warn") + f" <span class='why'>{esc(why_not)}</span>"),
                    ("Unit cost", esc(money(f.get("unit_cost"), f.get("currency"))) if f.get("unit_cost") not in (None, "") else ""),
                    ("Purchase order", self._record_link(f.get("po")) if f.get("po") else ""), ("Goods receipt", self._record_link(f.get("receipt")) if f.get("receipt") else ""),
                    ("Quarantined from", self._loc_link(f.get("quarantined_from")) if f.get("quarantined_from") else ""),
                    ("Nonconformance", self._record_link(ncr["locator"]) + " " + chip(ncr["state"]) if ncr else (self._record_link(f.get("ncr")) if f.get("ncr") else ""))])
        out = f"<section class='card' id='stock'><h2 style='margin-top:0'>{icon('package')}Stock lot</h2>{facts}"
        if getattr(self, "_needs_adjustment", None):
            out += self._needs_adjustment_card(lot)
        out += self._lot_actions(lot, part, ncr)
        out += f"<h3>{icon('activity')}Ledger</h3>" + self._ledger_html(lot, units)
        out += f"<h3>{icon('git-merge')}Genealogy</h3>" + self._genealogy_html(lot)
        return out + "</section>"

    def _ledger_html(self, lot: dict, units: str) -> str:
        rows = []
        for ev in inventory.history(self.hub.kernel, lot):
            d = ev.get("data") or {}
            q = d.get("quantity")
            signed = (f"+{qty(q)}" if isinstance(q, (int, float)) and q > 0 and ev["type"] not in ("stock.moved", "stock.quarantined", "stock.released", "stock.reworked")
                      else qty(q) if q is not None else "")
            after = (d.get("set") or {}).get("quantity")
            where = " → ".join(x for x in (self._loc_link(d.get("from")) if d.get("from") else "", self._loc_link(d.get("to")) if d.get("to") else "") if x)
            run = d.get("run_id") or ev.get("run_id") or ""
            rows.append([esc(when(ev.get("at"))), esc(inventory.EVENT_WORDS.get(ev["type"], ev["type"].split(".", 1)[-1])),
                         f'<span class="num">{esc(signed)}</span>', f'<span class="num">{esc(qty(after, units)) if after is not None else ""}</span>', where,
                         self._avatar(ev.get("by") or "", size="sm", named=True) if ev.get("by") else "",
                         esc(short(d.get("reason") or "", 120)) + (" " + self._run_link(run) if run and not str(run).startswith("agent-") else "")])
        return table(["When", "What", "Quantity", "Leaves", "From → to", "By", "Why"], rows, "No movements yet.")

    def _genealogy_html(self, lot: dict) -> str:
        g = inventory.genealogy(self.hub.kernel, lot)
        links = lambda refs: ", ".join(self._record_link(r, with_summary=False) for r in refs)
        items = [("Split from", links(g["split_from"])), ("Split into", links(g["split_into"])), ("Merged from", links(g["merged_from"])),
                 ("Merged into", links(g["merged_into"])), ("Received in", links(g["received_in"])), ("Quarantined by", links(g["quarantined_by"]))]
        shown = [(a, b) for a, b in items if b]
        return kv(shown) if shown else "<p class='muted'>Received as it is: no splits, merges or holds.</p>"

    def _needs_adjustment_card(self, lot: dict) -> str:
        info = self._needs_adjustment or {}
        user = self.user
        form = ""
        why = self._may_start("stock-adjustment")
        if not why:
            form = f"""<form method="post" action="/start">{hidden_as(user)}{self._once()}<input type="hidden" name="process" value="stock-adjustment">
<input type="hidden" name="lot" value="{esc(lot['locator'])}"><input type="hidden" name="new_quantity" value="{esc(qty(info.get('after')).replace(',', ''))}">
<div class="fields">{text_input("reason", "Why the stock differs", f"count found {qty(info.get('after'))} against {qty(info.get('before'))}", required=True)}</div>
<div class="actions"><button>{icon('hash')}Request the adjustment to {esc(qty(info.get('after')))}</button></div></form>"""
        else:
            form = f"<p class='why'>You may not request it yourself ({esc(why)}); ask a storekeeper.</p>"
        return (f"<div class='card warn' role='alert' id='needs-approval'><b>Not booked: the count needs an approval.</b> "
                f"<p>{esc(info.get('message') or '')}</p>{form}</div>")

    def _lot_actions(self, lot: dict, part: dict | None, ncr: dict | None) -> str:
        if self._viewing_cog():
            return ""
        user, k = self.user, self.hub.kernel
        f = lot["fields"]
        if lot["state"] != inventory.LIVE:
            return f"<p class='why'>No movements: {esc(lot['number'])} is {esc(lot['state'])}. Its ledger and genealogy stay on file.</p>"
        may_move, may_hold = self._stock_may("update", lot), self._stock_may("quarantine", lot)
        status = f.get("status") or "OK"
        here = k.get(f.get("location") or "")
        site = inventory.site_of(k, here["locator"]) if here else ""
        held = status == "QUARANTINED"
        targets = []
        for r in inventory.locations(k):
            rf = r["fields"]
            if rf.get("structural") or r["locator"] == f.get("location") or rf.get("kind") == "scrap":
                continue
            other_site = inventory.site_of(k, r["locator"]) != site
            if other_site and rf.get("kind") != "transit" and (here or {}).get("fields", {}).get("kind") != "transit":
                continue
            if held != (rf.get("kind") in inventory.HOLD_KINDS):  # held stock stays in hold locations; holding stock is a quarantine
                continue
            targets.append((r["locator"], inventory.location_path(k, r["locator"])))
        vals = lambda route: dict(self.vals) if self._refused_here(route) else {}
        err = lambda route, names: self._form_error(names) if self._refused_here(route) else ""
        opened = lambda route: self._refused_here(route)
        lot_in = f'<input type="hidden" name="lot" value="{esc(lot["locator"])}">'
        forms = []
        if may_move["allowed"]:
            v = vals("/stock-transfer")
            forms.append(details(f"{icon('move')}Transfer", f"""{err("/stock-transfer", ["to_location", "quantity", "reason"])}<form method="post" action="/stock-transfer">{hidden_as(user)}{self._once()}{lot_in}
<div class="fields">{widget("to_location", "ref", v.get("to_location", ""), required=True, options=targets, label="To", error=self._error_for("to_location") if opened("/stock-transfer") else None)}
{widget("quantity", "number", v.get("quantity", ""), label="Quantity", hint=f"leave empty to move all {qty(f.get('quantity'))}", error=self._error_for("quantity") if opened("/stock-transfer") else None)}
{text_input("reason", "Why", v.get("reason", ""))}</div>
<div class="actions"><button>{icon('move')}Move it</button><span class="why">Free within a site; between sites through a transit location.{' Quarantined stock moves only between hold locations.' if held else ''}</span></div></form>""",
                                 open_=opened("/stock-transfer"), cls="card quiet"))
            v = vals("/stock-count")
            tol = inventory.thresholds(self.bundle)["count_tolerance_pct"]
            forms.append(details(f"{icon('hash')}Count", f"""{err("/stock-count", ["counted", "reason"])}<form method="post" action="/stock-count">{hidden_as(user)}{self._once()}{lot_in}
<div class="fields">{widget("counted", "number", v.get("counted", ""), required=True, label="Counted", error=self._error_for("counted") if opened("/stock-count") else None)}
{text_input("reason", "Note", v.get("reason", ""))}</div>
<div class="actions"><button>{icon('check')}Book the count</button><span class="why">Within {tol:g}% of {esc(qty(f.get('quantity')))} it is booked at once; outside it, it needs an approved adjustment.</span></div></form>""",
                                 open_=opened("/stock-count"), cls="card quiet"))
            if not f.get("serial") and float(f.get("quantity") or 0) > 1 and not (held and ncr):
                v = vals("/stock-split")
                forms.append(details(f"{icon('scissors')}Split", f"""{err("/stock-split", ["quantity", "reason"])}<form method="post" action="/stock-split">{hidden_as(user)}{self._once()}{lot_in}
<div class="fields">{widget("quantity", "number", v.get("quantity", ""), required=True, label="Quantity to split off", error=self._error_for("quantity") if opened("/stock-split") else None)}
{text_input("reason", "Why", v.get("reason", ""))}</div><div class="actions"><button>{icon('scissors')}Split it off</button></div></form>""",
                                     open_=opened("/stock-split"), cls="card quiet"))
            mergeable = [r for r in inventory.lots(k, part=f.get("part"), location=f.get("location")) if r["locator"] != lot["locator"] and f.get("batch")
                         and not f.get("serial") and not (held and ncr) and r["fields"].get("batch") == f.get("batch")
                         and (r["fields"].get("status") or "OK") == status and not inventory.held_for(k, r)]
            if mergeable:
                opts = "".join(f'<label><input type="checkbox" name="sources" value="{esc(r["locator"])}"> {esc(r["number"])} '
                               f'({esc(qty(r["fields"].get("quantity"), self._units(part)))})</label> ' for r in mergeable)
                forms.append(details(f"{icon('git-merge')}Merge", f"""{err("/stock-merge", ["sources", "reason"])}<form method="post" action="/stock-merge">{hidden_as(user)}{self._once()}{lot_in}
<p class="why">Lots of batch {esc(f.get('batch'))} in the same place, with the same status, merge into this one.</p><div class="fields"><div>{opts}</div>{text_input("reason", "Why", "")}</div>
<div class="actions"><button>{icon('git-merge')}Merge into {esc(lot['number'])}</button></div></form>""", open_=opened("/stock-merge"), cls="card quiet"))
            if status == "OK" or status in MARKABLE:
                v = vals("/stock-mark")
                choices = [(s, s) for s in MARKABLE if s != status]
                forms.append(details(f"{icon('flag')}Mark", f"""{err("/stock-mark", ["status", "reason"])}<form method="post" action="/stock-mark">{hidden_as(user)}{self._once()}{lot_in}
<div class="fields">{widget("status", "text", v.get("status", ""), required=True, options=choices, label="Status")}{text_input("reason", "Why", v.get("reason", ""), required=True)}</div>
<div class="actions"><button>{icon('flag')}Mark it</button><span class="why">Takes it out of use where it is; back to OK only through a stock release.</span></div></form>""",
                                     open_=opened("/stock-mark"), cls="card quiet"))
        if may_hold["allowed"] and not held:
            v = vals("/stock-quarantine")
            forms.append(details(f"{icon('alert')}Quarantine", f"""{err("/stock-quarantine", ["quantity", "reason"])}<form method="post" action="/stock-quarantine">{hidden_as(user)}{self._once()}{lot_in}
<div class="fields">{widget("quantity", "number", v.get("quantity", ""), label="Quantity affected", hint="leave empty for all of it",
                            error=self._error_for("quantity") if opened("/stock-quarantine") else None)}
{text_input("reason", "Why it is held", v.get("reason", ""), required=True, error=self._error_for("reason") if opened("/stock-quarantine") else None)}</div>
<div class="actions"><button class="secondary">{icon('alert')}Hold it in quarantine</button><span class="why">Free: containment never waits. To report the problem, open a nonconformance naming the lot.</span></div></form>""",
                                 open_=opened("/stock-quarantine"), cls="card quiet"))
        if not self._may_start("stock-adjustment"):
            forms.append(details(f"{icon('hash')}Request an adjustment", f"""<form method="post" action="/start">{hidden_as(user)}{self._once()}<input type="hidden" name="process" value="stock-adjustment">{lot_in}
<div class="fields">{widget("new_quantity", "number", "", required=True, label="The quantity it should hold")}{text_input("reason", "Why", "", required=True)}</div>
<div class="actions"><button>{icon('send')}Request it</button><span class="why">stock-adjustment: routed by value; whoever asks, or approved the order the stock came from, never signs it.</span></div></form>""",
                                 cls="card quiet"))
        if status in RELEASABLE:
            if ncr:
                forms.append(f"<p class='why'>{esc(lot['number'])} is named by {self._record_link(ncr['locator'], with_summary=False)}, which is open: "
                             "it is released, scrapped, reworked or returned by that nonconformance's disposition.</p>")
            elif not self._may_start("stock-release"):
                back = [(r["locator"], inventory.location_path(k, r["locator"])) for r in inventory.locations(k)
                        if not r["fields"].get("structural") and r["fields"].get("kind") not in (*inventory.HOLD_KINDS, "scrap", "transit")
                        and inventory.site_of(k, r["locator"]) == site]
                forms.append(details(f"{icon('shield')}Request a release", f"""<form method="post" action="/start">{hidden_as(user)}{self._once()}<input type="hidden" name="process" value="stock-release">{lot_in}
<div class="fields">{widget("to_location", "ref", f.get("quarantined_from") or "", options=back, label="Back to", hint="where it was held from, unless you choose")}
{text_input("reason", "Why it may be released", "", required=True)}</div>
<div class="actions"><button>{icon('send')}Request the release</button><span class="why">stock-release: the quality manager approves; never the requester.</span></div></form>""",
                                     cls="card quiet"))
        if not forms:
            return (f"<p class='why'>You may not move, count or hold this stock: {self._why_html(may_move, 'update', 'stock-item', lot['folder'])}</p>")
        return f"<h3>{icon('tool')}Actions</h3>" + "".join(forms)

    # ------------------------------------------------------------ the free movements
    def _stock_lot(self) -> dict:
        try:
            lot = inventory.lot_of(self.hub.kernel, self._f("lot"))
        except inventory.StockError as e:
            raise ShellError(f"{str(e)[:1].upper()}{str(e)[1:]}.", 404) from None
        self._require_read(lot["folder"])
        return lot

    def _stock_action(self, action: str, verb, refusal_fields: tuple[str, ...] = ()):
        """A free movement: the right in the release, the guards, one event on the ledger; refused, the lot's page again
        with the values kept and the message by the form."""
        user = self.user
        bundle = self._need_release()
        lot = self._stock_lot()
        done = self._once_done()
        if done:
            return self._redirect(done)
        try:
            d = authz.check(bundle, user, action, "stock-item", lot["folder"])
            if not d["allowed"]:
                raise ShellError(f"You may not {ACTION_WORDS[action]} stock in {lot['folder']}. " + self._why_text(d, action, "stock-item", lot["folder"]), 403)
            msg = verb(lot)
        except inventory.NeedsApproval as e:
            self._needs_adjustment = {**e.info, "message": str(e)}
            return self._stock_refused(lot, f"Not booked: {e}.", None, 409, show=False)
        except (inventory.StockError, KernelGuardError) as e:
            text = str(e)
            return self._stock_refused(lot, f"{text[:1].upper()}{text[1:]}.", self._refusal_field(text, refusal_fields), 400)
        except ShellError as e:
            return self._stock_refused(lot, str(e), e.field, e.status)
        log_line(f"stock {self.route[7:]} lot={lot['locator']} by={user}: {msg}")
        self._redirect(self._once_finish(href("/record/" + quote(lot["locator"], safe=":"), user, done=msg) + "#stock"))

    def _refusal_field(self, text: str, fields: tuple[str, ...]) -> str | None:
        t = text.lower()
        for name in fields:
            if name.replace("_", " ") in t or (name == "to_location" and ("site" in t or "location" in t or "quarantine" in t or "scrap" in t)):
                return name
        return fields[0] if fields and ("quantity" in t or "count" in t or "number" in t) and fields[0] in ("quantity", "counted") else None

    def _stock_refused(self, lot: dict, msg: str, field: str | None, status: int, show: bool = True):
        self._force_status = status
        if show:
            self.vals = dict(self.form)
            self.err = (msg, field)
        # else: the card on the lot's page says it and offers the next step (a count that needs an approval)
        self.arg = lot["locator"]
        return self.page_record(status=status)

    def _number_in(self, name: str, required: bool = True) -> float | None:
        raw = self._f(name)
        if not raw:
            if required:
                raise ShellError(f"Say the {name.replace('_', ' ')}.", field=name)
            return None
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            raise ShellError(f"The {name.replace('_', ' ')} must be a number, not {raw!r}.", field=name) from None
        if not math.isfinite(v) or v < 0:
            raise ShellError(f"The {name.replace('_', ' ')} must be a number of nothing or more.", field=name)
        return v

    def post_stock_transfer(self):
        k = self.hub.kernel

        def go(lot):
            to = self._f("to_location")
            if not to:
                raise ShellError("Choose where it goes.", field="to_location")
            moved = inventory.transfer(k, lot, to, quantity=self._number_in("quantity", required=False), by=self.user, reason=self._f("reason"),
                                       release=self.meta["digest"])
            path = inventory.location_path(k, moved["fields"]["location"])
            if moved["locator"] != lot["locator"]:
                return f"Moved {qty(moved['fields']['quantity'])} of {lot['number']} to {path} as {moved['number']}."
            return f"Moved {lot['number']} to {path}."
        return self._stock_action("update", go, ("to_location", "quantity"))

    def post_stock_count(self):
        k = self.hub.kernel

        def go(lot):
            counted = inventory.count(k, lot, self._number_in("counted"), bundle=self.bundle, by=self.user, reason=self._f("reason") or "counted",
                                      release=self.meta["digest"])
            before = float(lot["fields"].get("quantity") or 0)
            after = float(counted["fields"]["quantity"])
            return (f"Counted {lot['number']}: {qty(after)}" + (f" ({'+' if after > before else ''}{qty(after - before)}, within the tolerance)." if after != before
                                                                  else ", as booked."))
        return self._stock_action("update", go, ("counted",))

    def post_stock_split(self):
        k = self.hub.kernel

        def go(lot):
            child = inventory.split(k, lot, self._number_in("quantity"), by=self.user, reason=self._f("reason"), release=self.meta["digest"])
            return f"Split {qty(child['fields']['quantity'])} off {lot['number']} as {child['number']}."
        return self._stock_action("update", go, ("quantity",))

    def post_stock_merge(self):
        k = self.hub.kernel

        def go(lot):
            sources = [s for s in self.multi.get("sources", []) if s]
            if not sources:
                raise ShellError("Choose the lots to merge into this one.", field="sources")
            merged = inventory.merge(k, sources, lot, by=self.user, reason=self._f("reason"), release=self.meta["digest"])
            return f"Merged {len(sources)} lot(s) into {lot['number']}: it holds {qty(merged['fields']['quantity'])}."
        return self._stock_action("update", go, ("sources",))

    def post_stock_quarantine(self):
        k = self.hub.kernel

        def go(lot):
            reason = self._f("reason")
            if not reason:
                raise ShellError("Say why the stock is held.", field="reason")
            held = inventory.quarantine(k, lot, quantity=self._number_in("quantity", required=False), by=self.user, reason=reason,
                                        release=self.meta["digest"])
            where = inventory.location_path(k, held["fields"]["location"])
            if held["locator"] != lot["locator"]:
                return f"Held {qty(held['fields']['quantity'])} of {lot['number']} in {where} as {held['number']}."
            return f"Held {lot['number']} in {where}."
        return self._stock_action("quarantine", go, ("quantity", "reason"))

    def post_stock_mark(self):
        k = self.hub.kernel

        def go(lot):
            status, reason = self._f("status").upper(), self._f("reason")
            if not reason:
                raise ShellError("Say why.", field="reason")
            inventory.mark(k, lot, status, by=self.user, reason=reason, release=self.meta["digest"])
            return f"Marked {lot['number']} {status}."
        return self._stock_action("update", go, ("status",))

    # ------------------------------------------------------------ receiving an order
    def _po_receive_panel(self, po: dict) -> str:
        k, user = self.hub.kernel, self.user
        lines = [l for l in po["fields"].get("lines") or [] if isinstance(l, dict)]
        receipts = [r for r in k.query(type_="receipt") if r["fields"].get("po") == po["locator"]]
        got: dict[str, float] = {}
        for r in receipts:
            if r["state"] == "received":
                for l in r["fields"].get("lines") or []:
                    if isinstance(l, dict):
                        got[l.get("part")] = got.get(l.get("part"), 0.0) + float(l.get("quantity") or 0)
        open_ = po["state"] == "issued" and any(l.get("part") and float(l.get("quantity") or 0) - got.get(l.get("part"), 0.0) > 1e-9 for l in lines)
        if lines and not any(l.get("part") for l in lines):
            why = "its lines name items, which are archived: nothing on it is received into stock"
        elif not open_:
            why = f"the order is {po['state']}" if po["state"] != "issued" else "everything on it has been received"
        else:
            why = self._may_start("goods-receipt")
        mine = self._refused_here("/po-receive")
        v = dict(self.vals) if mine else {}
        rows = []
        for i, l in enumerate(lines):
            part = k.get(l.get("part") or "")
            ordered = float(l.get("quantity") or 0)
            left = max(ordered - got.get(l.get("part"), 0.0), 0.0)
            track = (part or {}).get("fields", {}).get("trackable") or "none"
            if not l.get("part"):  # an order placed before inventory names items (archived): nothing to receive into stock
                row = [f"{self._record_link(l.get('item')) if l.get('item') else ''} <span class='why'>an item (archived): not received into stock</span>",
                       esc(qty(ordered)), "", ""] + (["", "", ""] if open_ and not why else [])
                rows.append(row)
                continue
            row = [self._record_link(l.get("part")) if part else esc(_number(l.get("part"))), esc(qty(ordered, self._units(part))),
                   esc(qty(got.get(l.get("part"), 0.0))), f"<b>{esc(qty(left))}</b>"]
            if open_ and not why:
                row.append(f'<input type="number" step="any" min="0" name="qty-{i}" value="{esc(v.get(f"qty-{i}", qty(left).replace(",", "") if left else ""))}" aria-label="Receiving now, line {i + 1}">')
                row.append(f'<input name="serials-{i}" value="{esc(v.get(f"serials-{i}", ""))}" placeholder="S1, S2, …" aria-label="Serial numbers, line {i + 1}">' if track == "serial"
                           else f'<input name="batch-{i}" value="{esc(v.get(f"batch-{i}", ""))}"{" required" if track == "lot" else ""} aria-label="Batch, line {i + 1}">')
                row.append(f'<input type="date" name="expiry-{i}" value="{esc(v.get(f"expiry-{i}", ""))}" aria-label="Expiry, line {i + 1}">')
            rows.append(row)
        heads = ["Part", "Ordered", "Received", "Outstanding"] + (["Receiving now", "Batch or serials", "Expiry"] if open_ and not why else [])
        out = f"<section class='card' id='receive'><h2 style='margin-top:0'>{icon('truck')}Receiving</h2>"
        if open_ and not why:
            locs = [(r["locator"], inventory.location_path(k, r["locator"])) for r in inventory.locations(k)
                    if not r["fields"].get("structural") and r["fields"].get("kind") not in ("scrap", "transit", *inventory.HOLD_KINDS)]
            default = (inventory.default_location(k, kind="receiving") or {}).get("locator", "")
            out += (f"""{self._form_error(["location"]) if mine else ""}<form method="post" action="/po-receive">{hidden_as(user)}{self._once()}
<input type="hidden" name="po" value="{esc(po['locator'])}">{table(heads, rows, "No lines.")}
<div class="fields">{widget("location", "ref", v.get("location", default), required=True, options=locs, label="Into", error=self._error_for("location") if mine else None)}
{widget("packing_slip", "text", v.get("packing_slip", ""), label="Packing slip")}{text_input("notes", "Notes", v.get("notes", ""))}</div>
<div class="actions"><button>{icon('truck')}Record the receipt</button><span class="why">goods-receipt: checked against the order, confirmed by the buyer, then booked into lots.</span></div></form>""")
        else:
            out += table(heads, rows, "No lines.") + (f"<p class='why'>Receiving is closed: {esc(why)}.</p>" if why else "")
        if receipts:
            out += "<h3>Receipts</h3>" + table(["Receipt", "State", "Into", "Lots"], [[self._record_link(r["locator"], with_summary=False), chip(r["state"]),
                                                                                    self._loc_link(r["fields"].get("location")),
                                                                                    ", ".join(self._record_link(x, with_summary=False) for x in r["fields"].get("lots") or [])]
                                                                                   for r in receipts])
        return out + "</section>"

    def post_po_receive(self):
        user = self.user
        self._need_release()
        k = self.hub.kernel
        po = k.get(self._f("po"))
        if po is None or po["type"] != "purchase-order":
            raise ShellError(f"There is no purchase order {self._f('po')!r}.", 404)
        self._require_read(po["folder"])
        done = self._once_done()
        if done:
            return self._redirect(done)
        try:
            why = self._may_start("goods-receipt")
            if why:
                raise ShellError(f"You may not record a goods receipt: {why}.", 403)
            if not self._f("location"):
                raise ShellError("Choose where the goods go.", field="location")
            loc = inventory.location_of(k, self._f("location"))
            inventory.holds_stock(loc)
            lines = []
            for i, l in enumerate(x for x in po["fields"].get("lines") or [] if isinstance(x, dict)):
                q = self._number_in(f"qty-{i}", required=False)
                if not q or not l.get("part"):  # an item line (archived) is never received into stock
                    continue
                row = {"part": l.get("part"), "quantity": q}
                if self._f(f"batch-{i}"):
                    row["batch"] = self._f(f"batch-{i}")
                serials = [s.strip() for s in self._f(f"serials-{i}").replace("\n", ",").split(",") if s.strip()]
                if serials:
                    row["serials"] = serials
                expiry = self._f(f"expiry-{i}")
                if expiry:
                    try:
                        date.fromisoformat(expiry)
                    except ValueError:
                        raise ShellError(f"The expiry on line {i + 1} must be a date written YYYY-MM-DD.", field=f"expiry-{i}") from None
                    row["expiry"] = expiry
                lines.append(row)
            if not lines:
                raise ShellError("Say how many were received on at least one line.", field="qty-0")
            record = {"po": po["locator"], "location": loc["locator"], "lines": lines}
            for name in ("packing_slip", "notes"):
                if self._f(name):
                    record[name] = self._f(name)
            run_id = self.hub.runner.submit("goods-receipt", {"record": record}, owner=user)
        except (inventory.StockError, KernelGuardError, RunError) as e:
            text = str(e)
            return self._po_refused(po, f"{text[:1].upper()}{text[1:]}.", "location" if "location" in text.lower() else None, 400)
        except ShellError as e:
            return self._po_refused(po, str(e), e.field, e.status)
        st = self.hub.runner.load(run_id)
        rec = k.get(st["record"]) if st.get("record") else None
        log_line(f"po-receive po={po['locator']} receipt={st.get('record')} by={user} lines={len(lines)} status={st['status']}")
        msg = self._outcome(f"Recorded {rec['number'] if rec else 'the receipt'} against {po['number']}", st)
        target = href("/record/" + quote(rec["locator"], safe=":"), user, done=msg) if rec else href("/run/" + run_id, user, done=msg)
        self._redirect(self._once_finish(target))

    def _po_refused(self, po: dict, msg: str, field: str | None, status: int):
        self.vals = dict(self.form)
        self.err = (msg, field)
        self._force_status = status
        self.arg = po["locator"]
        return self.page_record(status=status)

    # ------------------------------------------------------------ the decision card
    def _inventory_brief_rows(self, rec: dict, f: dict) -> list[tuple[str, str]]:
        """Key facts for whoever decides on an adjustment, a release or a part request (inbox._brief)."""
        k = self.hub.kernel
        rows: list[tuple[str, str]] = []
        add = lambda label, html: rows.append((label, html)) if html not in (None, "") else None
        t = rec["type"]
        if t in ("stock-adjustment", "stock-release"):
            lot = k.get(str(f.get("lot") or ""))
            part = k.get(str(f.get("part") or (lot or {}).get("fields", {}).get("part") or ""))
            units = self._units(part)
            add("Lot", self._record_link(lot["locator"]) if lot else esc(_number(f.get("lot"))))
            add("Part", self._record_link(part["locator"]) if part else "")
            if lot:
                add("Now", f"{esc(qty(lot['fields'].get('quantity'), units))} {self._stock_chip(lot)} in {self._loc_link(lot['fields'].get('location'))}")
            if t == "stock-adjustment":
                if f.get("quantity_before") is not None and f.get("new_quantity") is not None:
                    add("Adjustment", f"{esc(qty(f['quantity_before'], units))} → <b>{esc(qty(f['new_quantity'], units))}</b>"
                        + (f" ({esc(qty(f.get('variance')))}, {esc(f.get('variance_pct'))}%)" if f.get("variance") is not None else ""))
                add("Value", esc(money(f.get("amount"), f.get("currency"))) if f.get("amount") is not None else "")
                cond = f.get("conditions") or {}
                if "variance_within_tolerance" in cond:
                    add("Count tolerance", chip("within tolerance" if cond["variance_within_tolerance"] else "outside tolerance"))
                if f.get("baseline") is not None and f.get("quantity_before") is not None and abs(float(f["baseline"]) - float(f["quantity_before"])) > 1e-9:
                    add("Since it was last set", f"{esc(qty(f['baseline'], units))} → {esc(qty(f.get('new_quantity'), units))} ({esc(f.get('cumulative_pct'))}%, "
                        "with the counts booked since: they share one tolerance)")
                add("Ordered (approved) by", esc(f.get("ordered_by") or ""))
            else:
                add("Release to", self._loc_link(f.get("to_location") or (lot or {}).get("fields", {}).get("quarantined_from")))
                n = inventory.open_ncr_for(k, lot) if lot else None
                add("Open nonconformance", self._record_link(n["locator"]) + " " + chip("blocks the release", "crit") if n else "")
            add("Reason", esc(f.get("reason") or ""))
            add("Requested by", esc(f.get("requested_by") or ""))
        elif t == "part":
            add("Title", esc(f.get("title") or ""))
            add("IPN", esc(f.get("ipn") or ""))
            add("Category", esc(self._category_path(f.get("category"))) if f.get("category") else "")
            add("Units", esc(f.get("units") or ""))
            add("Tracked by", esc(f.get("trackable") or "none"))
            add("Purchaseable", chip("yes" if f.get("purchaseable", True) else "no"))
            try:
                mps = inventory.manufacturer_parts(k, rec)
                sps = inventory.supplier_parts(k, rec, active_only=False)
            except inventory.StockError:
                mps, sps = [], []
            add("Manufacturer parts", ", ".join(f"{esc(m['fields'].get('manufacturer') or '')} {esc(m['fields'].get('mpn') or '')}" for m in mps))
            add("Supplier parts", ", ".join(f"{esc(s['fields'].get('sku') or s['number'])} from {esc(self._supplier_name(s['fields'].get('supplier')))}" for s in sps))
            add("Description", esc(short(f.get("description") or "", 400)))
        return rows
