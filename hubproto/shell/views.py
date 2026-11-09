"""The three views of a record type (list, kanban, form parts), the app pages, people and cog profiles, suppliers,
and the bulk hold. Everything reads the kernel through the same permission trimming as the rest of the shell."""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import quote

from .. import authz
from ..runner import RunError
from .apps import app_for_type, avatar, avatars, plural
from .charts import stacked_bars
from .forms import ShellError, text_input, widget
from .handler import details, hidden_as, href, kv, table
from .icons import icon, type_icon
from .text import (NOTIFICATION_KINDS, chip, chip_class, day, days_from, esc, event_sentence, field_label, join_words, money, qty, role_words,
                   short, state_name, summary_of_record, tag, track_sentence, type_name, value_text, verb_for, when)

PAGE = 25
EXIT_STATES = ("returned", "failed", "cancelled", "rejected", "obsolete")
FILTERS = [("mine", "My items", "user"), ("open", "Open", "play"), ("waiting", "Waiting for me", "inbox"), ("overdue", "Overdue", "alert"),
           ("week", "This week", "calendar")]
GROUPS = [("", "No grouping"), ("state", "State"), ("assignee", "Assignee"), ("supplier", "Supplier"), ("type", "Type"), ("folder", "Folder")]
SORTS = {"number": lambda r: r["number"], "title": lambda r: summary_of_record(r).lower(), "state": lambda r: r["state"],
         "owner": lambda r: _owner(r), "due": lambda r: r["due"] or r["fields"].get("due") or "9999", "updated": lambda r: r.get("updated_at") or "",
         "amount": lambda r: _num(r["fields"].get("amount")), "type": lambda r: r["type"]}
BUCKET_WORDS = {"open": "Open", "waiting": "Waiting", "done": "Done", "returned": "Returned or stopped", "rejected": "Rejected or cancelled"}


def _owner(r: dict) -> str:
    f = r.get("fields", {})
    return str(f.get("owner") or f.get("author") or f.get("filed_by") or r.get("created_by") or "")


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return -1.0


def bucket_of(rec: dict, wf: dict | None) -> str:
    """Where an item stands: open, waiting (for a person), done (its terminal state), returned or stopped, rejected or cancelled."""
    s = rec["state"]
    if s in ("cancelled", "rejected", "obsolete"):
        return "rejected"
    if s in ("returned", "failed"):
        return "returned"
    if wf and s == wf.get("terminal"):
        return "done"
    if s.startswith("awaiting") or s == "in-review":
        return "waiting"
    if s in ("open", "draft", "active", "in-service"):
        return "open" if s in ("open", "draft") else "done"
    if s in ("posted", "effective", "closed", "approved", "acknowledged", "completed", "released", "received", "ordered", "done", "accepted", "issued"):
        return "done"
    return "open"


def fact_of(rec: dict, supplier_name=None) -> str:
    """The one number or phrase a card shows: amount, severity, quantity, revision, change class."""
    f = rec["fields"]
    t = rec["type"]
    if f.get("amount") is not None:
        s = money(f["amount"], f.get("currency"))
        return s + (f" · {supplier_name(f['supplier'])}" if supplier_name and f.get("supplier") else "")
    if t == "ncr" and f.get("severity"):
        return f"severity {f['severity']}"
    if t in ("receipt", "purchase-request", "item") and f.get("quantity") is not None:
        return qty(f.get("quantity"), f.get("unit"))
    if t == "stock-item":
        return " · ".join(x for x in (qty(f.get("quantity")), str(f.get("status") or ""), f"batch {f['batch']}" if f.get("batch") else "",
                                      f"SN {f['serial']}" if f.get("serial") else "") if x)
    if t == "part":
        return " · ".join(x for x in (f"rev {f['revision']}" if f.get("revision") else "", f.get("ipn") or "", f"min {qty(f['minimum_stock'], f.get('units'))}"
                                      if f.get("minimum_stock") else "") if x)
    if t == "stock-location":
        return str(f.get("kind") or "") + (" · structural" if f.get("structural") else "")
    if t == "supplier-part" and f.get("sku"):
        return f"SKU {f['sku']}" + (f" · pack of {qty(f['pack_quantity'])}" if f.get("pack_quantity") not in (None, 1, 1.0) else "")
    if t == "document":
        return f"rev {f.get('revision', '')}.{f.get('minor', 0)}" + (f" · {f['doc_class']}" if f.get("doc_class") else "")
    if t == "design" and f.get("change_class"):
        return f"change class {f['change_class']}"
    if t == "requirement" and f.get("priority"):
        return f"priority {f['priority']}"
    if t == "task" and f.get("priority"):
        return f"priority {f['priority']}"
    if t == "item" and f.get("stock") is not None:
        return f"stock {qty(f['stock'], f.get('unit'))}"
    if t == "capa" and f.get("root_cause_method"):
        return str(f["root_cause_method"])
    return ""


class ViewsMixin:
    # ------------------------------------------------------------ lookups
    def _wf_for_type(self, rt: str) -> dict | None:
        for wf in (self.bundle or {}).get("workflows", {}).values():
            if wf["record_type"] == rt:
                return wf
        return None

    def _msg_visible(self, m: dict) -> bool:
        """A message is shown to its sender and recipient, and to whoever may read the item it is about."""
        if self.user in (m.get("from"), m.get("to")):
            return True
        loc = m.get("record")
        rec = self.hub.kernel.get(loc) if loc else None
        return bool(rec) and self._readable(rec)

    def _readable(self, rec: dict) -> bool:
        cache = getattr(self, "_read_cache", None)
        if cache is None:
            cache = self._read_cache = {}
        f = rec["folder"]
        if f not in cache:
            cache[f] = self._can_read(f)["allowed"]
        return cache[f]

    def _records(self, rt: str | None = None, **q) -> list[dict]:
        """Records of a type (or every type) the user may read, newest first."""
        if not self._is_member():
            return []
        recs = self.hub.kernel.query(type_=rt if rt and rt != "all" else None, **q)
        return [r for r in reversed(recs) if self._readable(r)]

    def _pending_index(self) -> dict[str, dict]:
        """record locator -> the run waiting on it (for 'waiting for me' and overdue approvals)."""
        idx = getattr(self, "_pending_idx", None)
        if idx is None:
            idx = self._pending_idx = {}
            for r in self.hub.runner.list():
                if r.get("record") and r.get("pending") and r["status"] == "waiting":
                    idx[r["record"]] = r
        return idx

    def _counts_for(self, loc: str) -> dict:
        docs = len(self.hub.dms.documents_of(loc))
        links = len([l for l in self.hub.kernel.links_of(loc) if l["rel"] != "has-document"])
        return {"documents": docs, "links": links}

    # ------------------------------------------------------------ cards and controls
    def _kanban_card(self, r: dict, wf: dict | None = None, show_type: bool = False) -> str:
        f = r["fields"]
        risk = str(f.get("risk") or f.get("severity") or "").lower()
        cls = f" risk-{risk}" if risk in ("high", "medium", "low") else ""
        due = r["due"] or f.get("due")
        pend = self._pending_index().get(r["locator"])
        people = list(r["assignees"]) or ([_owner(r)] if _owner(r) else [])
        counts = self._counts_for(r["locator"])
        chips = [chip(f["risk"], title="risk") if f.get("risk") else "", chip(f"review {f['review_verdict']}", title="review by cogs") if f.get("review_verdict") else "",
                 self._due_html(due) if due else "",
                 chip("on hold", "crit") if r["hold"] else "", chip("escalated", "warn") if pend and pend["pending"].get("escalated") else ""]
        fact = fact_of(r, self._supplier_name)
        meta = f'<div class="kc-meta">{(esc(fact) + " ") if fact else ""}{esc(r["folder"])} &middot; {esc(_owner(r))}' \
               f'{" &middot; with " + esc(", ".join(r["assignees"])) if r["assignees"] else ""} {" ".join(c for c in chips if c)}</div>'
        head = f'<div class="kc-head">{type_icon(r["type"], cls="ty")}<div class="kc-main">{self._record_link(r["locator"], with_summary=False)}' \
               f'{" " + tag(type_name(r["type"])) if show_type else ""}<span class="kc-title">{esc(short(summary_of_record(r), 90))}</span>{meta}</div></div>'
        foot = (f'<div class="kc-foot"><span class="cnt" title="documents">{icon("paperclip")}{counts["documents"]}</span>'
                f'<span class="cnt" title="links">{icon("link")}{counts["links"]}</span>{avatars(people, self.bundle)}</div>')
        return f'<div class="kcard{cls}">{head}{foot}</div>'

    def _views_switch(self, rt: str, view: str, first: dict | None = None, **params) -> str:
        def link(v, ic, text):
            if v == view:
                return f'<span aria-current="page">{icon(ic)}{text}</span>'
            if v == "form":
                if not first:
                    return f'<span class="muted" title="no item to open">{icon(ic)}{text}</span>'
                return f'<a href="{href("/record/" + quote(first["locator"], safe=":"), self.user)}">{icon(ic)}{text}</a>'
            return f'<a href="{href("/items/" + rt, self.user, view=v if v != "list" else None, **params)}">{icon(ic)}{text}</a>'

        return f'<span class="views" role="group" aria-label="View">{link("list", "list", "List")}{link("kanban", "kanban", "Kanban")}{link("form", "form", "Form")}</span>'

    # ------------------------------------------------------------ list and kanban
    def page_items(self, status: int = 200):
        user = self.user
        bundle = self._need_release()
        rt = self.arg.split("?")[0]
        if rt != "all" and rt not in self.hub.kernel.types:
            raise ShellError(f"There is no item type '{rt}'. Choose one from the app menu.", 404)
        if not self._is_member():
            return self._page(plural(rt) if rt != "all" else "Items", f"<h1>{esc(plural(rt) if rt != 'all' else 'Items')}</h1><div class='card'><b>{esc(user)} is not a member</b> of the organization; nothing is listed.</div>")
        q = {k: self.q.get(k, [""])[0] for k in ("text", "filter", "group", "sort", "dir", "state", "bucket", "assignee", "folder", "decided", "view", "page")}
        view = "kanban" if q["view"] == "kanban" else "list"
        wf = self._wf_for_type(rt) if rt != "all" else None
        today = self.today
        week_ago = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
        recs = self._records(rt, text=q["text"] or None, folder=q["folder"] or None, assignee=q["assignee"] or None)
        if rt == "all":
            recs = [r for r in recs if r["type"] != "document"] if not q["text"] else recs
        pend = self._pending_index()
        if q["filter"] == "controlled":
            recs = [r for r in recs if r["fields"].get("doc_class") == "controlled"]
        elif q["filter"] == "mine":
            recs = [r for r in recs if _owner(r) == user or r["created_by"] == user or user in r["assignees"]]
        elif q["filter"] == "open":
            recs = [r for r in recs if bucket_of(r, wf or self._wf_for_type(r["type"])) in ("open", "waiting", "returned")]
        elif q["filter"] == "waiting":
            recs = [r for r in recs if user in r["assignees"] and r["locator"] in pend]
        elif q["filter"] == "overdue":
            recs = [r for r in recs if self._overdue(r, pend.get(r["locator"]))]
        elif q["filter"] == "week":
            recs = [r for r in recs if (r.get("updated_at") or r["created_at"])[:10] >= week_ago]
        if q["state"]:
            recs = [r for r in recs if r["state"] == q["state"]]
        if q["bucket"]:
            recs = [r for r in recs if bucket_of(r, wf or self._wf_for_type(r["type"])) == q["bucket"]]
        if q["decided"]:
            recs = [r for r in recs if any(str(d.get("at", ""))[:10] == q["decided"] for d in r["decisions"])]
        sort = q["sort"] if q["sort"] in SORTS else ""
        if sort:
            recs.sort(key=SORTS[sort], reverse=(q["dir"] == "desc"))
        name = plural(rt) if rt != "all" else "Items"
        crumbs = [(name, None)]
        app = app_for_type(self._apps(), rt) if rt != "all" else None
        if app:
            crumbs = [(app["name"], href("/app/" + app["key"], user)), (name, None)]
        params = {k: v for k, v in q.items() if v and k not in ("view", "page")}
        body = f"<h1>{type_icon(rt) if rt != 'all' else icon('grid')} {esc(name)} <span class='muted'>{len(recs)}</span></h1>"
        body += self._control_panel(rt, view, q, recs[0] if recs else None, params)
        err = self._form_error(["reason", "sel"]) if (self.err and self.route == "/bulk-hold") else ""
        if view == "kanban":
            body += self._kanban(rt, recs, wf)
        else:
            body += err + self._list(rt, recs, q, params, wf)
        self._page(name, body, status=status, crumbs=crumbs, app_type=rt if rt != "all" else "")

    def _overdue(self, r: dict, run: dict | None) -> bool:
        due = (run or {}).get("pending", {}).get("due") if run else None
        due = due or r["due"] or r["fields"].get("due")
        n = days_from(due, self.today) if due else None
        return n is not None and n < 0 and bucket_of(r, self._wf_for_type(r["type"])) in ("open", "waiting", "returned")

    def _control_panel(self, rt: str, view: str, q: dict, first: dict | None, params: dict) -> str:
        user = self.user
        keep = "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">' for k, v in params.items() if k not in ("text", "page") and v)
        keep += f'<input type="hidden" name="view" value="{esc(view)}">' if view != "list" else ""
        chips = '<span class="chips"><span class="lbl">' + icon("filter") + "Filter</span>"
        for key, label, ic in FILTERS:
            cur = ' aria-current="true"' if q["filter"] == key else ""
            p = {**params, "filter": "" if q["filter"] == key else key}
            chips += f'<a{cur} href="{href("/items/" + rt, user, view=view if view != "list" else None, **{k: v for k, v in p.items() if v})}">{icon(ic)}{label}</a>'
        if q["filter"] == "controlled":
            chips += f'<a aria-current="true" href="{href("/items/" + rt, user, **{k: v for k, v in params.items() if k != "filter"})}">{icon("file-text")}Controlled</a>'
        for key, label in (("state", "state"), ("bucket", "stage"), ("assignee", "assignee"), ("folder", "folder"), ("decided", "decided on")):
            if q.get(key):
                chips += f'<a aria-current="true" href="{href("/items/" + rt, user, **{k: v for k, v in params.items() if k != key})}">{esc(label)}: {esc(state_name(q[key]) if key == "state" else BUCKET_WORDS.get(q[key], q[key]) if key == "bucket" else q[key])} {icon("x")}</a>'
        chips += "</span>"
        groups = "".join(f'<option value="{g}"{" selected" if q["group"] == g else ""}>{esc(l)}</option>' for g, l in GROUPS)
        return (f'<div class="cp"><form method="get" action="/items/{esc(rt)}">{hidden_as(user)}{keep}'
                f'<span class="search"><label class="f"><span class="vh">Search {esc(plural(rt).lower())}</span>'
                f'<input type="search" name="text" value="{esc(q["text"])}" aria-label="Search" title="number or words"></label>'
                f'<button class="ghost" title="Search">{icon("search")}<span class="vh">Search</span></button></span>'
                f'<label class="f" style="width:auto"><span class="vh">Group by</span><select name="group" class="small" aria-label="Group by">{groups}</select></label>'
                f'<button class="ghost">{icon("group")}Group</button></form>{chips}{self._views_switch(rt, view, first, **params)}</div>')

    def _list(self, rt: str, recs: list[dict], q: dict, params: dict, wf: dict | None) -> str:
        user = self.user
        try:
            page = max(1, int(q["page"] or 1))
        except ValueError:
            page = 1
        total_pages = max(1, (len(recs) + PAGE - 1) // PAGE)
        page = min(page, total_pages)
        shown = recs[(page - 1) * PAGE: page * PAGE]
        group = q["group"] if q["group"] in dict(GROUPS) else ""
        all_types = rt == "all"
        cols = [("number", "Number"), ("title", "Title")] + ([("type", "Type")] if all_types else []) + \
               [("state", "State"), ("amount", "Key fact"), ("owner", "Owner"), ("", "With"), ("due", "Due"), ("updated", "Updated")]

        def head(key, label):
            if not key:
                return f'<th scope="col">{esc(label)}</th>'
            cur = q["sort"] == key
            d = "desc" if (cur and q["dir"] != "desc") else "asc"
            arrow = icon("arrow-down" if (cur and q["dir"] == "desc") else "arrow-up") if cur else ""
            aria = f' aria-sort="{"descending" if q["dir"] == "desc" else "ascending"}"' if cur else ""
            return f'<th scope="col"><a{aria} href="{href("/items/" + rt, user, **{**params, "sort": key, "dir": d})}">{esc(label)}{arrow}</a></th>'

        header = '<th scope="col"><label><input type="checkbox" data-select-all aria-label="Select all"><span class="vh">Select all</span></label></th>' + "".join(head(k, l) for k, l in cols)
        rows = ""
        last_group = object()

        def group_key(r):
            if group == "state":
                return state_name(r["state"], wf or self._wf_for_type(r["type"]))
            if group == "assignee":
                return ", ".join(r["assignees"]) or "nobody"
            if group == "supplier":
                return self._supplier_name(r["fields"].get("supplier")) or "no supplier"
            if group == "type":
                return type_name(r["type"])
            if group == "folder":
                return r["folder"]
            return None

        if group:
            shown = sorted(shown, key=lambda r: str(group_key(r)))
        for r in shown:
            g = group_key(r)
            if group and g != last_group:
                n = sum(1 for x in shown if group_key(x) == g)
                rows += f'<tr class="groupname"><th scope="row" colspan="{len(cols) + 1}">{esc(g)} <span class="muted">({n})</span></th></tr>'
                last_group = g
            due = r["due"] or r["fields"].get("due")
            cells = [f'<td class="sel"><label><input type="checkbox" name="sel" value="{esc(r["locator"])}"><span class="vh">Select {esc(r["number"])}</span></label></td>',
                     f"<td>{type_icon(r['type'])} {self._record_link(r['locator'], with_summary=False)}</td>",
                     f"<td>{esc(short(summary_of_record(r), 100))}{' ' + chip('on hold', 'crit') if r['hold'] else ''}</td>"]
            if all_types:
                cells.append(f"<td>{tag(type_name(r['type']))}</td>")
            cells += [f"<td>{self._state_chip(r['state'], wf or self._wf_for_type(r['type']))}</td>", f"<td>{esc(fact_of(r, self._supplier_name))}</td>",
                      f"<td>{self._avatar(_owner(r), size='sm', named=True) if _owner(r) else ''}</td>", f"<td>{avatars(r['assignees'], self.bundle)}</td>",
                      f"<td class='num'>{self._due_html(due) if due else ''}</td>", f"<td class='num'>{esc(when(r.get('updated_at') or r.get('created_at')))}</td>"]
            rows += "<tr>" + "".join(cells) + "</tr>"
        if not recs:
            tbl = f"<p class='muted'>No {esc(plural(rt).lower() if rt != 'all' else 'items')} match.</p>"
        else:
            tbl = f'<div class="tbl"><table><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table></div>'
        pager = ""
        if total_pages > 1:
            pages = " ".join(f"<b>{n}</b>" if n == page else f'<a href="{href("/items/" + rt, user, **{**params, "page": n})}">{n}</a>' for n in range(1, total_pages + 1))
            pager = f'<p class="pager">Page {page} of {total_pages} ({PAGE} per page): {pages}</p>'
        if not recs:
            return tbl + pager
        err = self._field_error("reason") if self.route == "/bulk-hold" else None
        bulk = (f'<div class="bulk"><span class="muted">{icon("pause")}With the selected:</span>'
                f'{text_input("reason", "Reason for the hold", self._val("reason") if self.route == "/bulk-hold" else "", required=True, hint="", error=err)}'
                f'<button class="secondary">Put selected on hold</button><span class="why">Only approvers of an item may hold it.</span></div>')
        return (f'<form method="post" action="/bulk-hold">{hidden_as(user)}<input type="hidden" name="type" value="{esc(rt)}">'
                f'<input type="hidden" name="return" value="{esc(href("/items/" + rt, user, **params))}">{tbl}{pager}{bulk}</form>')

    def _kanban(self, rt: str, recs: list[dict], wf: dict | None) -> str:
        if rt == "all":
            columns = ["open", "waiting", "done", "returned", "rejected"]
            grouped = {BUCKET_WORDS[c]: [] for c in columns}
            for r in recs:
                grouped[BUCKET_WORDS[bucket_of(r, self._wf_for_type(r["type"]))]].append(r)
        else:
            columns = list(wf["states"]) if wf else ["open"]
            present = {r["state"] for r in recs}
            columns += [s for s in EXIT_STATES if s not in columns and s in present]
            grouped = {state_name(c, wf): [] for c in columns}
            for r in recs:
                grouped.setdefault(state_name(r["state"], wf), []).append(r)
        body = '<div class="board">'
        for col, items in grouped.items():
            body += f"<div class='col{' empty' if not items else ''}'><h3>{esc(col)}<span class='n'>{len(items)}</span></h3>"
            body += "".join(self._kanban_card(r, wf, show_type=(rt == "all")) for r in items)
            body += "</div>"
        return body + "</div>" + ("" if recs else "<p class='muted'>Nothing here yet.</p>")

    # ------------------------------------------------------------ bulk hold
    def post_bulk_hold(self):
        user = self.user
        bundle = self._need_release()
        sel = [s for s in self.multi.get("sel", []) if s]
        reason = self._f("reason")
        rt = self._f("type") or "all"
        if not sel:
            raise ShellError("Tick at least one item to put on hold.", field="sel")
        if not reason:
            raise ShellError("Say why you are putting these on hold; the reason stays in each item's history.", field="reason")
        done, refused = [], []
        for ref in sel:
            rec = self.hub.kernel.get(ref)
            if rec is None:
                refused.append(f"{ref} (no such item)")
                continue
            obj = self.hub.kernel.types.get(rec["type"], {}).get("object", rec["type"])
            if not authz.check(bundle, user, "approve", obj, rec["folder"])["allowed"]:
                refused.append(f"{rec['number']} (you are not its approver)")
                continue
            if rec["hold"]:
                refused.append(f"{rec['number']} (already on hold)")
                continue
            self.hub.kernel.hold(rec["locator"], True, reason, by=user)
            done.append(rec["number"])
        msg = (f"Put on hold: {', '.join(done)}." if done else "Nothing was put on hold.") + (f" Not held: {'; '.join(refused)}." if refused else "")
        back = self._f("return")
        target = back if back.startswith("/items/") else href("/items/" + rt, user)
        self._redirect(target + ("&" if "?" in target else "?") + "done=" + quote(msg))

    # ------------------------------------------------------------ app pages
    def page_app(self):
        user = self.user
        bundle = self._need_release()
        apps = self._apps()
        app = next((a for a in apps if a["key"] == self.arg), None)
        if app is None:
            raise ShellError(f"There is no app '{self.arg}'. Open the app menu (the grid icon) to choose one.", 404)
        if not self._is_member():
            return self._page(app["name"], f"<h1>{esc(app['name'])}</h1><div class='card'><b>{esc(user)} is not a member</b> of the organization.</div>")
        if app["key"] == "inbox":
            return self._redirect(href("/inbox", user))
        if app["key"] == "documents":
            return self._redirect(href("/files", user))
        if app["key"] == "people":
            return self._redirect(href("/people", user))
        body = f"<h1>{icon(app['icon'], cls='lg')} {esc(app['name'])}</h1>"
        cards = ""
        rows = []
        legend = [(BUCKET_WORDS[b], b) for b in ("open", "waiting", "done", "returned", "rejected")]
        for rt in app["types"]:
            recs = self._records(rt)
            wf = self._wf_for_type(rt)
            buckets = {b: 0 for b, _ in legend}
            for r in recs:
                buckets[BUCKET_WORDS[bucket_of(r, wf)]] += 1
            rows.append((plural(rt), [(b, buckets[b], href("/items/" + rt, user, bucket=key)) for b, key in legend]))
            open_n = buckets["Open"] + buckets["Waiting"] + buckets["Returned or stopped"]
            process = next((p for p, t in app["processes"] if t == rt), None)
            from .text import PREPARED_FIRST

            target = (href(PREPARED_FIRST[process], user) + "#new") if process in PREPARED_FIRST else (href("/", user, start=process) + "#start-" + esc(process))
            start = f'<a class="button ghost" href="{target}">{icon("plus")}{esc(verb_for(rt, process))}</a>' if process else ""
            cards += (f'<div class="card"><h3>{type_icon(rt)} <a href="{href("/items/" + rt, user)}">{esc(plural(rt))}</a></h3>'
                      f'<b class="n">{open_n}</b> <span class="muted">open of {len(recs)}</span>'
                      f'<div class="actions"><a class="button ghost" href="{href("/items/" + rt, user)}">{icon("list")}List</a>'
                      f'<a class="button ghost" href="{href("/items/" + rt, user, view="kanban")}">{icon("kanban")}Kanban</a>{start}</div></div>')
        for label, path, ic in app["pages"]:
            base = path.split("?")[0]
            params = dict(p.split("=") for p in path.split("?")[1].split("&")) if "?" in path else {}
            cards += f'<div class="card"><h3>{icon(ic)} <a href="{href(base, user, **params)}">{esc(label)}</a></h3></div>'
        body += f'<div class="apphome">{cards}</div>'
        if rows:
            body += f'<div class="chart" style="margin-top:var(--sp-4)"><h2>{icon("chart")}Items by stage</h2>{stacked_bars(rows, legend=legend, label=app["name"] + " items by stage")}</div>'
        self._page(app["name"], body, crumbs=[(app["name"], None)])

    # ------------------------------------------------------------ people and cogs
    def _decisions_by(self, name: str, limit: int = 10) -> list[tuple[dict, dict]]:
        out = []
        for r in self.hub.kernel.query():
            for d in r["decisions"]:
                if d.get("by") == name:
                    out.append((d, r))
        out.sort(key=lambda x: x[0].get("at", ""), reverse=True)
        return out[:limit]

    def _may_approve_words(self, name: str) -> list[str]:
        """What a member may approve: for a cog its criteria with limits; for a person the checkpoints its roles hold."""
        bundle = self.bundle
        m = bundle["members"].get(name) or {}
        out = []
        if m.get("type") == "cog":
            for c in bundle.get("approval_criteria", []):
                if c["approver"] == f"cog:{name}":
                    limit = f" up to {money(c['max_amount'], c.get('currency'))}" if c.get("max_amount") is not None else ""
                    out.append(f"{self._process_name(c['process'])}{limit}: {c['source']['text']}")
            return out or ["nothing on its own"]
        roles = set(m.get("roles", []))
        for pname, wf in bundle.get("workflows", {}).items():
            op = bundle["ops"].get(pname, {})
            if not authz.check(bundle, name, "approve", op.get("object", ""), op.get("folder", ""))["allowed"]:
                continue
            for cp in wf["checkpoints"]:
                routes = [r for r in bundle.get("routing", []) if r["process"] == pname and r["step"] == cp["id"] and r["assignee"].partition(":")[2] in roles
                          and r["assignee"].startswith("role:") and r.get("mode", "assign") == "assign"]
                if cp["role"] in roles and not routes:
                    out.append(f"{cp['title']} ({self._process_name(pname)})" + (f", due within {cp['sla_days']} days" if cp.get("sla_days") else ""))
                for r in routes:
                    c = r.get("condition") or {}
                    when_ = ""
                    if c.get("field") == "amount":
                        word = {"<=": "at or under", "<": "under", ">": "over", ">=": "at or over"}.get(c.get("op"), c.get("op", ""))
                        when_ = f" for amounts {word} {money(c['value'], c.get('currency'))}"
                    out.append(f"{cp['title']} ({self._process_name(pname)}){when_}" + (f", due within {cp['sla_days']} days" if cp.get("sla_days") else ""))
        return out or ["nothing: no approval is routed to the roles held"]

    def page_people(self):
        user = self.user
        bundle = self._need_release()
        if not self._is_member():
            return self._page("People", f"<h1>People</h1><div class='card'><b>{esc(user)} is not a member</b> of the organization.</div>")
        members = bundle["members"]
        pend_counts: dict[str, int] = {}
        for r in self.hub.runner.list():
            if r.get("pending") and r["status"] == "waiting":
                for a in r["pending"].get("assignees") or []:
                    pend_counts[a] = pend_counts.get(a, 0) + 1
        month = (date.fromisoformat(self.today) - timedelta(days=30)).isoformat()
        dec_counts: dict[str, int] = {}
        for r in self.hub.kernel.query():
            for d in r["decisions"]:
                if str(d.get("at", ""))[:10] >= month:
                    dec_counts[d.get("by", "")] = dec_counts.get(d.get("by", ""), 0) + 1

        qcounts = self.hub.queue.counts()

        def card(name: str, m: dict) -> str:
            roles = " ".join(tag(role_words(r)) for r in m["roles"]) or "<span class='muted'>no role</span>"
            kind = "cog" if m["type"] == "cog" else "person"
            prof = (bundle.get("cogs") or {}).get(name) or {}
            extra = ""
            if m["type"] == "cog":
                qn = qcounts.get(name, {})
                busy = (f"<span class='chip work'>{icon('activity')}working</span> " if qn.get("working") else "<span class='chip ok'>free</span> ")
                busy += f"<a href='{href('/user/' + name, user)}#cog-work'>{qn.get('queued', 0)} in its queue</a>"
                extra = (f"<div class='why'>{busy}</div><div class='why'>{icon('cpu')} {esc(prof.get('model') or 'stand-in')} &middot; follows "
                         + (", ".join(f'<a href="{href("/frame/" + fr, user)}">{esc(fr)}</a>' for fr in prof.get("follows") or []) or "no procedure") + "</div>")
            return (f'<div class="card">{avatar(name, bundle, size="lg")}<div><h3><a href="{href("/user/" + name, user)}">{esc(name)}</a> <span class="muted">{kind}</span></h3>'
                    f'<div>{roles}</div>{extra}<div class="why">{pend_counts.get(name, 0)} waiting &middot; {dec_counts.get(name, 0)} decision{"" if dec_counts.get(name, 0) == 1 else "s"} in 30 days</div></div></div>')

        people = sorted(n for n, m in members.items() if m["type"] == "person")
        cogs = sorted(n for n, m in members.items() if m["type"] == "cog")
        body = f"<h1>{icon('users', cls='lg')} People</h1><p class='why'>Members of the organization under the policy in effect: people and cogs, with what waits for each and what each decided lately.</p>"
        body += f"<h2>People <span class='muted'>{len(people)}</span></h2><div class='people'>{''.join(card(n, members[n]) for n in people)}</div>"
        body += (f"<h2>Cogs <span class='muted'>{len(cogs)}</span></h2><p class='why'>Cogs are team members that review, approve within fixed criteria, answer messages and file records. "
                 f"Each works from its own queue, one task at a time: <a href='{href('/queue', user)}'>see all the cogs' queues</a>.</p>"
                 f"<div class='people' id='cog-cards' data-live>{''.join(card(n, members[n]) for n in cogs)}</div>{self._live_meta()}")
        body += self._new_member_forms()
        self._page("People", body, crumbs=[("People", None)])

    def _user_reading(self, person: str) -> str:
        m = ((self.bundle or {}).get("members") or {}).get(person) or {}
        return self._required_reading_html(person) if m.get("type") == "person" else self._cog_training_html(person)

    def page_user(self):
        user = self.user
        bundle = self._need_release()
        name = self.arg
        m = bundle["members"].get(name)
        if m is None:
            raise ShellError(f"There is no member '{name}'. The list is under People.", 404)
        if not self._is_member():
            raise ShellError(f"{user} is not a member of the organization; profiles are shown to members.", 403)
        is_cog = m["type"] == "cog"
        roles = " ".join(tag(role_words(r)) for r in m["roles"]) or "no role"
        body = (f'<div class="profile-head">{avatar(name, bundle, size="lg")}<div><h1>{esc(name)} <span class="muted">{"cog" if is_cog else "person"}</span></h1>'
                f'<div class="roles">{roles}</div></div></div>')
        if is_cog:
            prof = (bundle.get("cogs") or {}).get(name) or {}
            follows = prof.get("follows") or []
            calls = [c for c in getattr(getattr(self.hub, "model", None), "calls", []) if c.get("by") == name]
            try:
                cog = self.hub.registry.get(name)
                about = [("Kind", esc(cog.kind)), ("Version", esc(cog.version)), ("Reaches", esc(", ".join(cog.reaches) or "nothing beyond its own run")),
                         ("Tasks", esc(", ".join(cog.tasks))), ("Files records through", esc(getattr(cog, "files_process", "") or "-"))]
                desc = cog.description or ""
            except KeyError:
                about, desc = [], ""
                body += "<div class='card crit'>This cog is a member of the organization but is not installed in this hub.</div>"
            from ..model import available_models, resolve_model

            res = resolve_model(prof.get("model") or "")
            if not res["available"]:
                body += (f"<div class='card crit' role='status'><b>{esc(name)} is not available.</b> It is bundled with {esc(prof.get('model') or 'no model')}, "
                         f"which this hub cannot use: {esc(res['note'])}. It takes no work until it can; choose an installed model below to propose a change.</div>")
            avail = available_models()
            current = prof.get("model") or ""
            info = {f"ollama/{m['name']}": m for m in avail["ollama"]}
            cur = info.get(current)
            what = (f"{cur['params']} {cur['family']}, local via Ollama, {cur['size_gb']} GB" if cur else
                    ("Anthropic" if res["backend"] == "anthropic" else res["note"]))
            served = (f"<b>{esc(current or 'none')}</b> <span class='chip {'ok' if res['available'] else 'crit'}'>{'answers' if res['available'] else 'not available'}</span> "
                      f"<span class='muted'>{esc(what)}</span>")
            can_change = self._is_member() and (self.bundle["members"].get(user) or {}).get("type") == "person"
            rows = []
            for c in sorted(avail["choices"], key=lambda c: c["value"] != current):  # its current model first, then largest first
                mi = info.get(c["value"])
                detail = f"{mi['params']} {mi['family']} · {mi['quant']} · {mi['size_gb']} GB · installed" if mi else ("Anthropic" if c["available"] else "needs an Anthropic key")
                name_ = mi["name"] if mi else c["value"]
                is_cur = c["value"] == current
                rows.append(f"<label class='mopt{' off' if not c['available'] else ''}{' cur' if is_cur else ''}'><input type='radio' name='model' value='{esc(c['value'])}'"
                            f"{' checked' if is_cur else ''}{'' if c['available'] else ' disabled'}><span class='mname'>{esc(name_)}</span>"
                            f"<span class='mdetail'>{esc(detail)}{' · current' if is_cur else ''}</span></label>")
            servable = [c for c in avail["choices"] if c["available"] and c["value"] != current]
            picker = ""
            if can_change and not servable:
                picker = (f"<p class='why'>No other model can be reached from this hub"
                          + (f" ({esc(avail['ollama_error'])})" if avail["ollama_error"] else "") + ", so there is nothing to change to.</p>")
                can_change = False
            picker = picker or ("" if not can_change else
                      f"<details class='modelpick'><summary>Change model</summary><form method='post' action='/cog-model'>{hidden_as(user)}{self._once()}"
                      f"<input type='hidden' name='cog' value='{esc(name)}'><fieldset><legend class='vh'>Model for {esc(name)}</legend>{''.join(rows)}</fieldset>"
                      + (f"<p class='why'>{esc(avail['ollama_error'])}</p>" if avail["ollama_error"] else "")
                      + f"<div class='actions'><button>{icon('send')}Propose the change</button><span class='why'>A cog comes with its model: the policy owners approve a change.</span></div></form></details>")
            from .. import scopes as _sc

            persona = (bundle.get("personas") or {}).get(prof.get("persona") or "") or {}
            pframe = (persona.get("source") or {}).get("frame")
            because = {f["frame"]: [x.partition(":")[2] or x for x in f["because"] if x != "company"] for f in _sc.following(bundle, name)}
            follows_html = ", ".join(f'<a href="{href("/frame/" + fr, user)}">{esc(fr)}</a>'
                                     + (f" <span class='why'>({esc(', '.join(because[fr]))})</span>" if because.get(fr) else "") for fr in follows)
            confirms = [r for r in bundle.get("permissions") or [] if r.get("effect") == "propose" and r.get("subject") in
                        ({"cog:" + name} | {"role:" + x for x in m.get("roles") or []})]
            acts = ("<p>Derived action by action from the SOPs: it <b>acts</b> where a permission for it or its roles allows it, or the step is "
                    "its own; it <b>proposes</b> where an SOP says a person confirms; anything else it only reads, and says what should be done.</p>"
                    + ("<ul>" + "".join(f"<li>{esc(r['source']['text'])} <span class='why'>{self._rule_link(r['source'])}</span></li>" for r in confirms) + "</ul>"
                       if confirms else ""))
            profile = [("About", esc(prof.get("about") or "-")),
                       ("Model", served + picker),
                       ("Persona", (f"<a href='{href('/frame/' + pframe, user)}'>{esc(prof.get('persona'))}</a> "
                                    f"<span class='why'>{esc(persona.get('stance') or '')}, {esc(persona.get('tone') or '')}; "
                                    f"lenses {esc(', '.join(persona.get('lenses') or []) or '-')}</span>") if pframe else esc(prof.get("persona") or "-")),
                       ("How it behaves", esc(prof.get("prompt") or "-")),
                       ("Follows", (follows_html or "<span class='muted'>no procedure: it is routed no work</span>")
                        + "<div class='why'>Derived: the company scope and every document whose Roles and responsibilities table lists it or one of "
                          "its roles (in brackets).</div>"),
                       ("What it may do", acts + self._member_permissions_html(name)),
                       ("Written in", self._rule_link(prof.get("source")) if prof.get("source") else "")]
            def pin_html(pin):
                d = self.hub.kernel.get(pin["locator"])
                latest = (self.hub.dms.versions(pin["locator"]) or [{}])[-1].get("version") if d else None
                newer = f" <span class='chip warn'>version {latest} exists: a recompile pins it, as a reviewed change</span>" if latest and latest != pin.get("version") else ""
                return f"<a href='{href('/document/' + quote(pin['locator'], safe=':'), user)}'>{esc(pin['number'])}</a> {esc(pin.get('title') or '')} (version {esc(pin.get('version'))}){newer}"
            if prof.get("prompt_doc"):
                profile.insert(1, ("Prompt from", pin_html(prof["prompt_doc"])))
            if prof.get("reference_docs"):
                profile.insert(4, ("References", "<br>".join(pin_html(x) for x in prof["reference_docs"])))
            src_frame = (prof.get("source") or {}).get("frame")
            if src_frame:
                profile.append(("Change it", f"<a class='button ghost' href='{href('/frame-doc/' + src_frame, user)}'>{icon('edit')}Edit this cog's profile</a> "
                                             "<span class='why'>what it does, its model and persona; its roles are in "
                                             f"<a href='{href('/frame-doc/org-model', user)}'>the organization model</a>, and what it may do and "
                                             "follows in the SOPs that name it or its roles</span>"))
            models = (f"{len(calls)} model call{'s' if len(calls) != 1 else ''} since the hub started"
                      + (f" ({sum(1 for c in calls if c.get('stand_in'))} answered by the stand-in, no model key)" if calls else ""))
            body += (f"<div class='card'><h2 style='margin-top:0;border:0'>{icon('robot')} About this cog</h2><p>{esc(desc)}</p>"
                     f"<p class='why'>A cog is routed work only from the procedures it follows; a model drafts its words and never signs.</p>"
                     + kv(profile) + (details("Technical details", kv(about + [("Model calls", esc(models))])) if about else "") + "</div>")
        if m.get("type") == "cog" and name in self.hub.registry.ids():
            body += self._cog_queue_section(name) + self._live_meta(cog=name)
            body += self._agent_section(name, prof if 'prof' in locals() else ((self.bundle.get("cogs") or {}).get(name) or {}))
        body += f"<h2>{icon('check-circle')} May approve</h2><ul>" + "".join(f"<li>{esc(s)}</li>" for s in self._may_approve_words(name)) + "</ul>"
        waiting = [r for r in self.hub.runner.pending_for(name)]
        body += f"<h2>{icon('inbox')} Waiting for {esc(name)} <span class='muted'>{len(waiting)}</span></h2>"
        rows = []
        for r in sorted(waiting, key=lambda x: x["pending"].get("due") or "9999"):
            rec = self.hub.kernel.get(r["record"]) if r.get("record") else None
            if rec and not self._readable(rec):
                rows.append([esc(type_name(rec["type"])), "<span class='muted'>an item you may not read</span>", esc(r["pending"]["title"]), self._due_html(r["pending"].get("due")), ""])
                continue
            rows.append([esc(type_name(rec["type"]) if rec else r["process"]), self._record_link(r["record"]) if rec else esc(r["process"]), esc(r["pending"]["title"]),
                         self._due_html(r["pending"].get("due")), self._run_link(r) if self._run_visible(r) else ""])
        body += table(["Type", "Item", "Waiting at", "Due", "Run"], rows, f"Nothing is waiting for {name}.")
        decisions = self._decisions_by(name)
        body += f"<h2>{icon('activity')} Recent decisions</h2>"
        rows = []
        for d, rec in decisions:
            if not self._readable(rec):
                continue
            rows.append([esc(when(d.get("at"))), self._record_link(rec["locator"]), chip(d.get("verdict")), esc(short(d.get("meaning"), 300)),
                         self._rule_html(d["delegating_rule"]) if d.get("delegating_rule") else ""])
        body += table(["When", "Item", "Verdict", "Reason", "Under"], rows, "No decisions yet.")
        msgs = [x for x in reversed(self.hub.inbox.all()) if x["from"] == name and x["kind"] not in NOTIFICATION_KINDS and x["kind"] != "chat"
                and self._msg_visible(x)][:10]
        body += f"<h2>{icon('message')} Recent messages {'answered' if is_cog else 'sent'}</h2>"
        body += table(["When", "To", "Subject", "Text"], [[esc(when(x["at"])), self._avatar(x["to"], size="sm", named=True), esc(short(x["subject"], 120)),
                                                          esc(short(x["body"], 200))] for x in msgs], "No messages.")
        if name != user and not self._viewing_cog():
            body += (f'<h2 id="send">{icon("send")} Send {esc(name)} a message</h2><form method="post" action="/msg" class="card">{hidden_as(user)}{self._once()}'
                     f'<input type="hidden" name="to" value="{esc(name)}"><input type="hidden" name="return" value="{esc(href("/user/" + name, user))}">'
                     f'<div class="fields">{text_input("subject", "Subject", required=True)}{text_input("body", "Message", required=True, textarea=True)}</div>'
                     f'<div class="actions"><button>{icon("send")}Send</button>{(" <span class=why>" + esc(name) + " answers with what it filed or found.</span>") if is_cog else ""}</div></form>')
        body += self._user_reading(name)
        self._page(name, body, crumbs=[("People", href("/people", user)), (name, None)])

    # ------------------------------------------------------------ suppliers
    def page_suppliers(self):
        user = self.user
        self._need_release()
        if not self._is_member():
            return self._page("Suppliers", f"<h1>Suppliers</h1><div class='card'><b>{esc(user)} is not a member</b> of the organization.</div>")
        bills = self._records("bill")
        pos = self._records("purchase-order")
        evals = self._records("supplier-evaluation")
        rows = []
        for key, s in sorted(self.hub.kernel.suppliers().items()):
            sid = s.get("id") or key.split(":", 1)[1]
            nb = [b for b in bills if b["fields"].get("supplier") == sid]
            npo = [p for p in pos if p["fields"].get("supplier") == sid]
            ev = [e for e in evals if e["fields"].get("supplier") == sid]
            rows.append([f"{icon('truck')} {esc(s.get('name') or sid)}", chip("approved" if s.get("approved") else "not approved"),
                         f'<a href="{href("/items/bill", user, text=sid)}">{len(nb)}</a>', f'<a href="{href("/items/purchase-order", user, text=sid)}">{len(npo)}</a>',
                         ", ".join(self._record_link(e["locator"], with_summary=False) for e in ev) or "<span class='muted'>none</span>"])
        body = f"<h1>{icon('truck', cls='lg')} Suppliers</h1><p class='why'>Suppliers known to the finance system, with their bills, purchase orders and evaluations.</p>"
        body += table(["Supplier", "Status", "Bills", "Purchase orders", "Evaluations"], rows, "No suppliers.", row_header=True)
        self._page("Suppliers", body, crumbs=[("Purchasing", href("/app/purchasing", user)), ("Suppliers", None)])

    def _mentions(self, text: str) -> str:
        """A message body with @names linked to profiles (escaped HTML; locators become numbers)."""
        import re

        html_ = self._human(text)
        members = (self.bundle or {}).get("members") or {}
        return re.sub(r"@([A-Za-z][\w-]*)", lambda m: f'<a href="{href("/user/" + m.group(1), self.user)}">@{esc(m.group(1))}</a>' if m.group(1) in members else f"<b>@{esc(m.group(1))}</b>", html_)

    # ------------------------------------------------------------ form view parts
    def _statusbar(self, wf: dict | None, rec: dict) -> str:
        """The workflow pipeline across the top of a form: every state in order, the current one highlighted."""
        if not wf:
            return f'<ol class="statusbar" aria-label="Workflow"><li class="current" data-state="{esc(rec["state"])}" aria-current="step">{esc(state_name(rec["state"]))}</li></ol>'
        states = list(wf["states"])
        cur = rec["state"]
        if cur not in states:
            states.append(cur)
        idx = states.index(cur)
        items = ""
        for i, s in enumerate(states):
            cls = "current" if i == idx else ("done" if i < idx else "todo")
            if s in EXIT_STATES:
                cls += " exit"
            aria = ' aria-current="step"' if i == idx else ""
            items += f'<li class="{cls}" data-state="{esc(s)}"{aria}>{esc(state_name(s, wf))}</li>'
        return f'<ol class="statusbar" id="statusbar" data-live aria-label="Workflow">{items}</ol>'

    def _smart_buttons(self, counts: list[tuple[str, int, str, str]]) -> str:
        """(label, count, icon, tab id) as the strip under the form head; each jumps to its tab."""
        out = "".join(f'<a href="#{esc(tab)}">{icon(ic)}<span><b>{n}</b>{esc(label)}</span></a>' for label, n, ic, tab in counts)
        return f'<div class="smart" aria-label="Related">{out}</div>'

    def _chatter(self, rec: dict, run: dict | None, with_composer: bool = True) -> str:
        """Messages, notifications, decisions and history as one timeline, newest first, with a composer."""
        user = self.user
        bundle = self.bundle
        loc = rec["locator"]
        word = type_name(rec["type"]).lower()
        entries: list[tuple[str, str, str, str]] = []  # (at, who, kind, html)
        for e in self.hub.kernel.events(aggregate=loc):
            d = e["data"] or {}
            if e["type"] == "record.transitioned" and d.get("decision"):
                dec = d["decision"]
                entries.append((e["at"], dec.get("by", ""), "decision",
                                f"{esc(dec.get('verdict'))}d it: “{esc(short(dec.get('meaning', ''), 400))}” &middot; now {self._state_chip(d.get('to'), None)}"))
            elif e["type"] in ("record.created", "record.transitioned", "record.held", "record.assigned", "record.linked", "record.unlinked", "record.labelled", "document.checked_out", "document.version_added", "document.checked_in"):
                entries.append((e["at"], e.get("by", ""), "history", event_sentence(e, self._number_link, word)))
        if run:
            try:
                for ev in self.hub.runner.track(run["id"]):
                    if ev["type"] in ("gate.cog_review", "gate.reviewing", "gate.lens", "gate.voided", "gate.declined", "gate.skipped", "gate.escalated", "gate.overdue", "run.failed", "run.retried", "run.resubmitted", "run.cancelled"):
                        who = ev.get("cog") or ev.get("by") or "hub"
                        kind = "decision" if ev["type"] in ("gate.cog_review", "gate.lens") else "history"
                        entries.append((ev["at"], who, kind, track_sentence(ev, self._number_link)))
            except RunError:
                pass
        run_id = run["id"] if run else rec.get("run_id") or ""
        seen_ids = set()
        for m in self.hub.inbox.on_record(loc) + [x for x in self.hub.inbox.all() if not x.get("record") and run_id and x.get("run_id") == run_id]:
            if m["id"] in seen_ids:
                continue
            seen_ids.add(m["id"])
            if m["from"] == "hub":
                entries.append((m["at"], "hub", "notification", f"to {self._avatar(m['to'], size='sm', named=True)}: <b>{self._human(m['subject'])}</b><div class='why'>{self._human(short(m['body'], 600))}</div>"))
            elif user in (m["from"], m["to"]) or m.get("record") or bundle["members"].get(m["to"], {}).get("type") == "cog" or bundle["members"].get(m["from"], {}).get("type") == "cog":
                kind = "note" if (m["from"] == m["to"]) else ("reply" if m.get("kind") == "reply" else "message")
                to = "" if kind == "note" else f"to {self._avatar(m['to'], size='sm', named=True)}: "
                see = ""
                applied = m.get("applied_in")
                if (m.get("edit") or {}) and isinstance(applied, int) and applied > 1 and rec["type"] == "document":
                    see = f" <a href='{href('/compare/' + quote(loc, safe=':'), user, a=applied - 1, b=applied)}'>see what changed (version {applied})</a>"
                elif (m.get("edit") or {}) and applied == "live":
                    see = f" <a href='{href('/compare/' + quote(loc, safe=':'), user)}'>see the latest change</a>"
                report = [r for r in (m.get("edit_report") or []) if not r.get("ok")]
                if report:
                    see += "<div class='why'>Not done: " + esc("; ".join(f"{r.get('op')} ({r.get('detail')})" for r in report)) + "</div>"
                entries.append((m["at"], m["from"], kind, f"{to}<b>{self._human(m['subject'])}</b><div class='msg'>{self._mentions(short(m['body'], 2000))}</div>{see}"))
        entries.sort(key=lambda x: x[0], reverse=True)
        kinds = {"decision": ("check-circle", "decision"), "history": ("activity", "history"), "notification": ("alert", "notification"), "message": ("message", "message"),
                 "note": ("note", "note"), "reply": ("robot", "reply")}
        items = ""
        for at, who, kind, html_ in entries:
            ic, label = kinds[kind]
            av = f'<span class="avatar sm" style="background:var(--muted)" title="the hub">{icon("cpu")}</span>' if who == "hub" else avatar(who, bundle)
            name = "the hub" if who == "hub" else who
            name_html = esc(name) if who == "hub" else f'<a href="{href("/user/" + who, user)}">{esc(who)}</a>' if who in bundle["members"] else esc(who)
            items += (f'<li class="kind-{kind}">{av}<div><div class="tl-head"><b>{name_html}</b><span class="kind-chip">{icon(ic)}{label}</span>'
                      f'<time datetime="{esc(at)}">{esc(when(at))}</time></div><div class="tl-body">{html_}</div></div></li>')
        composer = ""
        if with_composer and self._is_member() and not self._viewing_cog():
            people = [(p, p) for p in self._people() if p != user]
            cogs = [(p, f"{p} (cog)") for p, mm in sorted(bundle["members"].items()) if mm["type"] == "cog"]
            owner = _owner(rec)
            default_to = owner if owner and owner != user and owner in bundle["members"] else (people[0][0] if people else "")
            back = href("/record/" + quote(loc, safe=":"), user) if rec["type"] != "document" else href("/document/" + quote(loc, safe=":"), user)
            mine = self.err is not None and self.route == "/msg" and self.vals.get("return") == back
            err = self._form_error(["to", "subject", "body"]) if mine else ""
            names = sorted(set(bundle["members"]) | set(bundle.get("roles") or []))
            datalist = '<datalist id="mentions">' + "".join(f'<option value="@{esc(n)}">{esc(n)}{" (cog)" if bundle["members"].get(n, {}).get("type") == "cog" else " (role)" if n not in bundle["members"] else ""}</option>' for n in names) + "</datalist>"
            composer = (f'<div class="composer">{err}<form method="post" action="/msg">{hidden_as(user)}{self._once()}<input type="hidden" name="return" value="{esc(back)}"><input type="hidden" name="record" value="{esc(loc)}">'
                        f'<div class="fields">{widget("to", "text", self._val("to") if mine else default_to, label="To", required=True, options=people + cogs, hint="", error=self._field_error("to") if mine else None)}'
                        f'{text_input("subject", "Subject", self._val("subject") if mine else "About " + rec["number"], required=True, error=self._field_error("subject") if mine else None)}'
                        f'<label class="f{" bad" if mine and self._field_error("body") else ""}"><span>Message <i>(@name asks a person or a cog; @role asks a committee; "@cog please review" gets a review; "@name please approve" starts or advances the approval)</i></span>'
                        f'<textarea name="body" rows="3" required{" aria-invalid=true aria-describedby=err-body" if mine and self._field_error("body") else ""}>{esc(self._val("body") if mine else "")}</textarea>'
                        f'{("<span class=err id=err-body>" + esc(self._field_error("body")) + "</span>") if mine and self._field_error("body") else ""}</label>'
                        f'<label class="f"><span>Who to mention <i>(pick a name, then write it in the message)</i></span><input name="mention" list="mentions" style="max-width:16em"></label>{datalist}</div>'
                        f'<div class="actions"><button>{icon("send")}Send message</button><span class="why">A cog answers in this chatter with what it reviewed, filed or found.</span></div></form>'
                        f'<details><summary>{icon("note")} Log a note (kept with the item, sent to nobody else)</summary><form method="post" action="/msg">{hidden_as(user)}{self._once()}'
                        f'<input type="hidden" name="return" value="{esc(back)}"><input type="hidden" name="record" value="{esc(loc)}"><input type="hidden" name="to" value="{esc(user)}"><input type="hidden" name="subject" value="Note on {esc(rec["number"])}">'
                        f'<div class="fields">{text_input("body", "Note", "", required=True, textarea=True)}</div><div class="actions"><button class="secondary">{icon("note")}Log note</button></div></form></details></div>')
        page_back = href(("/document/" if rec["type"] == "document" else "/record/") + quote(loc, safe=":"), user)
        working = self._queue_entries(loc, page_back) if hasattr(self, "_queue_entries") else ""
        return (f'<section class="chatter" id="chatter" aria-labelledby="chatter-h"><h2 id="chatter-h">{icon("message")} Chatter <span class="muted" id="chatter-count" data-live>{len(entries)}</span></h2>{composer}'
                f'<div id="chatter-log" data-live><ol class="timeline">{working}{items}</ol>{"" if entries or working else "<p class=muted>Nothing yet.</p>"}</div>'
                f'{self._live_meta(record=loc) if hasattr(self, "_live_meta") else ""}</section>')
