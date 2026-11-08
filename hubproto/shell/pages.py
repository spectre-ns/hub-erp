"""Pages for everyone: the dashboard (home), the Start forms, the document library, boards, runs, records as Odoo-style forms, and documents."""

from __future__ import annotations

import json
from urllib.parse import quote

from datetime import date, timedelta

from .. import authz, wopi
from .apps import FOLDER_APPS, avatars, plural
from .charts import day_bars, hbars, last_days, median, per_day, sparkline, stacked_bars
from .editor import new_document_form
from ..runner import FIELD_ROLES
from .forms import DEFAULT_START_INPUTS, REQUIRED_EXTRA, START_INPUTS, ShellError, file_input, text_input, widget
from .handler import details, folder_tree, hidden_as, href, kv, table
from .icons import TYPE_ICONS, file_icon, icon, type_icon
from .text import (type_name, BLOB_FIELDS, chip, day, days_from, esc, event_sentence, field_label, fmt, join_words, owner_of, short, state_name,
                   summary_of_record, tag, track_sentence, type_name, value_text, verb_for, when)
from .views import BUCKET_WORDS, EXIT_STATES, bucket_of

MERMAID_URL = "https://cdnjs.cloudflare.com/ajax/libs/mermaid/11.4.1/mermaid.min.js"
EDITABLE_DOC_STATES = ("draft", "open", "returned", "failed")


def mermaid_for(wf: dict, current: str | None) -> str:
    states = list(wf["states"])
    if current and current not in states:
        states.append(current)
    ids = {s: f"s{i}" for i, s in enumerate(states)}
    lines = ["stateDiagram-v2"]
    for s, i in ids.items():
        lines.append(f'    state "{state_name(s, wf)}" as {i}')
    lines.append(f"    [*] --> {ids[states[0]]}")
    for t in wf["transitions"]:
        if t["from"] not in ids or t["to"] not in ids:
            continue
        label = ", ".join(t.get("steps") or []) or "automatic" if t["kind"] == "automatic" else f"{t['title']} (approve)"
        lines.append(f"    {ids[t['from']]} --> {ids[t['to']]} : {label}")
        if t["kind"] == "human" and t.get("on_reject") in ids:
            lines.append(f"    {ids[t['from']]} --> {ids[t['on_reject']]} : reject")
    if wf["terminal"] in ids:
        lines.append(f"    {ids[wf['terminal']]} --> [*]")
    if current in ids:
        lines.append("    classDef current stroke:#0b5f8a,stroke-width:3px")  # a stroke only, so it reads in both themes
        lines.append(f"    class {ids[current]} current")
    return "\n".join(lines)


def workflow_for(bundle: dict, rec: dict, run: dict | None) -> tuple[str | None, dict | None]:
    """The process and workflow a record follows: the run's, else the first whose record type matches."""
    if run and run.get("workflow"):
        return run["process"], run["workflow"]
    if run and run.get("process") in bundle.get("workflows", {}):
        return run["process"], bundle["workflows"][run["process"]]
    for name, wf in bundle.get("workflows", {}).items():
        if wf["record_type"] == rec["type"]:
            return name, wf
    return None, None


def next_state_for(wf: dict | None, checkpoint: str) -> str | None:
    for t in (wf or {}).get("transitions", []):
        if t["kind"] == "human" and t["step"] == checkpoint:
            return t["to"]
    return None


def may_file(bundle: dict, user: str, folder: str) -> dict:
    """Filing a document into a folder: a create-document permission, or any create permission there."""
    d = authz.check(bundle, user, "create", "document", folder)
    if d["allowed"]:
        return d
    for obj in sorted({p["object"] for p in bundle["processes"].values()}):
        d2 = authz.check(bundle, user, "create", obj, folder)
        if d2["allowed"]:
            return d2
    return d


def _plain_preview(text: str) -> str:
    """Markdown source as the words a person reads: no heading marks, emphasis stars, backticks or digests."""
    import re as _re

    out = []
    for line in str(text or "").splitlines():
        t = _re.sub(r"^#{1,6}\s*", "", line).replace("**", "").replace("`", "")
        t = _re.sub(r"^\s*[-*]\s+", "• ", t)
        t = _re.sub(r"\b[0-9a-f]{12,64}\b", "…", t)
        t = _re.sub(r"rec:[a-z-]+:([A-Z]+-[0-9A-Z-]+)", r"\1", t)
        t = _re.sub(r"(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z)?", r"\1", t)
        if t.strip():
            out.append(t.strip())
    return "\n".join(out)


class PagesMixin:
    # ----------------------------------------------------------------- home
    def page_home(self, status: int = 200):
        user = self.user
        if not self.bundle:
            return self._page("Home", "<h1>Home</h1><div class='card'>No policy release is in effect. Compile and release Frames first "
                                      "(<code>hubproto bootstrap-release --as bob --second carol</code>).</div>")
        bundle, meta = self.bundle, self.meta
        if not self._is_member():
            return self._page("Home", f"<h1>Welcome, {esc(user)}</h1><div class='card crit' role='alert'><b>{esc(user)} is not a member of the organization</b> under the "
                                      "policy release in effect, so nothing here is available. Ask a policy owner (bob or carol) to add you to the org model.</div>")
        m = bundle["members"][user]
        roles = join_words(tag(r.replace("-", " ")) for r in m["roles"]) or "no role"
        body = f"""<h1>Welcome, {esc(user)}</h1><p>You are {roles}.</p>""" + self._dashboard()
        body += f"<h2 id='start'>{icon('play')} Start something</h2><p class='why'>Fields marked * are required. Each entry opens its form.</p>"
        open_process = self.q.get("start", [""])[0] or (self.vals.get("process") if self.err else "")
        startable, unavailable = [], []
        for name, op in bundle["ops"].items():
            d = authz.check(bundle, user, "create", op["object"], op["folder"])
            first_role = op["steps"][0]["role"]
            holds = first_role in ("system", *FIELD_ROLES) or first_role in authz.roles_of(bundle, user)
            if d["allowed"] and holds:
                startable.append((name, op, d))
            else:
                why = self._why_html(d, "create", op["object"], op["folder"]) if not d["allowed"] else esc(f"you do not hold the role '{first_role}' that starts it")
                unavailable.append((name, op, why))
        if not startable:
            body += "<p class='muted'>Nothing under the release in effect.</p>"
        else:
            from .text import PREPARED_FIRST

            body += '<div class="quick" aria-label="Quick actions">' + "".join(
                f'<a href="{(href(PREPARED_FIRST[name], user) + "#new") if name in PREPARED_FIRST else (href("/", user, start=name) + "#start-" + esc(name))}">'
                f'{type_icon(op.get("record_type"))}{esc(verb_for(op.get("record_type"), name))}</a>'
                for name, op, _ in startable) + "</div>"
        for name, op, d in startable:
            if name not in PREPARED_FIRST:
                body += self._start_entry(name, op, d, open_=(name == open_process))
        if unavailable:
            rows = [[esc(verb_for(op.get("record_type"), n)), esc(op["folder"]), f'<span class="why">{w}</span>'] for n, op, w in unavailable]
            body += details("Not available to you", table(["Process", "Filed in", "Why"], rows), cls="card")
        vlabel = self._version_html(meta["digest"])
        body += f"<p class='why'>Policy in effect: {vlabel}, released by {esc(meta['released_by'])}.</p>"
        self._page("Home", body, status=status, crumbs=[("Home", None)])

    def _dashboard(self) -> str:
        """KPI tiles with sparklines and four charts, every number a link to the list it counts."""
        user, bundle, today = self.user, self.bundle, self.today
        days14 = last_days(14, today)
        days30 = last_days(30, today)
        runs = self.hub.runner.list()
        waiting = self._pending(user, "decision")
        overdue = [r for r in waiting if r["pending"].get("due") and r["pending"]["due"] < today]
        returned = self._pending(user, "resubmit") + self._failed_for(user)
        mine = [r for r in runs if r["owner"] == user]
        ncrs = [r for r in self._records("ncr") if bucket_of(r, self._wf_for_type("ncr")) in ("open", "waiting", "returned")]
        pos = [r for r in self._records("purchase-order") if not r["fields"].get("received") and bucket_of(r, self._wf_for_type("purchase-order")) != "rejected"]
        bills = [r for r in self._records("bill") if r["state"].startswith("awaiting")]
        checked_out = [r for r in self._records("document") if r["fields"].get("checked_out_by")]

        def spark(items, key=lambda x: x.get("created_at", "")):
            return sparkline([n for _, n in per_day([key(x) for x in items], days14)])

        tiles = [("Waiting for me", len(waiting), "inbox", href("/inbox", user), spark(waiting), False),
                 ("Overdue", len(overdue), "alert", href("/items/all", user, filter="overdue"), spark(overdue, lambda r: r["pending"].get("due", "")), bool(overdue)),
                 ("Returned or stopped", len(returned), "retry", href("/inbox", user) + "#mine", spark(returned), bool(returned)),
                 ("My runs", len(mine), "activity", href("/runs", user, owner="me"), spark(mine), False),
                 ("Open NCRs", len(ncrs), "alert", href("/items/ncr", user, filter="open"), spark(ncrs), False),
                 ("POs awaiting receipt", len(pos), "truck", href("/items/purchase-order", user, filter="open"), spark(pos), False),
                 ("Bills to approve", len(bills), "coins", href("/items/bill", user, bucket="waiting"), spark(bills), False),
                 ("Documents checked out", len(checked_out), "lock", href("/items/document", user, text="checked"), spark(checked_out, lambda r: r.get("updated_at", "")), False)]
        kpis = "".join(f'<a class="kpi{" crit" if crit and n else ""}" href="{h}"><span class="kpi-h">{icon(ic)}{esc(label)}</span><b>{n}</b>{sp}</a>'
                       for label, n, ic, h, sp, crit in tiles)
        # items by stage per app
        legend = [(BUCKET_WORDS[b], b) for b in ("open", "waiting", "done", "returned", "rejected")]
        app_root = {v: k for k, v in FOLDER_APPS.items()}
        rows = []
        for app in self._apps():
            if not app["types"]:
                continue
            counts = {b: 0 for b, _ in legend}
            for rt in app["types"]:
                wf = self._wf_for_type(rt)
                for r in self._records(rt):
                    counts[BUCKET_WORDS[bucket_of(r, wf)]] += 1
            if not sum(counts.values()):
                continue
            root = app_root.get(app["key"])
            if app["key"] == "documents":
                link = lambda key: href("/items/document", user, bucket=key)
            elif root:
                link = lambda key, root=root: href("/items/all", user, bucket=key, folder=root)
            else:
                link = lambda key, rt=app["types"][0]: href("/items/" + rt, user, bucket=key)
            rows.append((app["name"], [(b, counts[b], link(key)) for b, key in legend]))
        by_app = stacked_bars(rows, legend=legend, label="Items by stage per app")
        # decisions per day
        stamps = [d.get("at", "") for r in self._records() for d in r["decisions"]]
        decisions = day_bars(per_day(stamps, days30), href_for=lambda d: href("/items/all", user, decided=d), label="Decisions per day, last 30 days")
        # cycle time by process: median days from the record's creation to its terminal state
        cycles: dict[str, list[float]] = {}
        for r in self._records():
            wf = self._wf_for_type(r["type"])
            if not wf or r["state"] != wf["terminal"] or not r["transitions"]:
                continue
            try:
                t0 = date.fromisoformat(r["created_at"][:10])
                t1 = date.fromisoformat(r["transitions"][-1]["at"][:10])
            except (ValueError, KeyError):
                continue
            cycles.setdefault(r["type"], []).append((t1 - t0).days)
        cycle_rows = sorted(((plural(rt), median(v) or 0.0, href("/items/" + rt, user, bucket="done"), f"{median(v):g} d (n={len(v)})") for rt, v in cycles.items()),
                            key=lambda x: -x[1])
        cycle = hbars(cycle_rows[:8], swatch="sw", unit="days", label="Median days from submission to close, by process")
        # overdue by assignee
        late: dict[str, int] = {}
        for r in runs:
            p = r.get("pending") or {}
            if r["status"] == "waiting" and p.get("due") and p["due"] < today and self._run_visible(r):
                for a in p.get("assignees") or []:
                    late[a] = late.get(a, 0) + 1
        late_rows = sorted(((a, float(n), href("/items/all", user, assignee=a, filter="overdue"), f"{n}") for a, n in late.items()), key=lambda x: -x[1])
        late_chart = hbars(late_rows[:8], swatch="sw-crit", label="Overdue items by assignee")
        return (f'<div class="kpis">{kpis}</div><div class="charts">'
                f'<div class="chart"><h2>{icon("chart")}Items by stage, per app</h2>{by_app}</div>'
                f'<div class="chart"><h2>{icon("check-circle")}Decisions per day, last 30 days</h2>{decisions}</div>'
                f'<div class="chart"><h2>{icon("clock")}Cycle time by process <span class="muted">median days, submit to close</span></h2>{cycle}</div>'
                f'<div class="chart"><h2>{icon("alert")}Overdue by assignee</h2>{late_chart}</div></div>')

    def _start_entry(self, name: str, op: dict, d: dict, open_: bool = False) -> str:
        user = self.user
        rt = op.get("record_type", "")
        verb = verb_for(rt, name)
        values = dict(self.vals) if (self.err and self.vals.get("process") == name) else {}
        err = self._form_error(self._start_field_names(rt)) if values else ""
        return f"""<details class="card start" id="start-{esc(name)}"{' open' if open_ else ''}><summary>{esc(verb)} {tag(type_name(rt))} <span class="why">filed in {esc(op['folder'])}</span></summary>
{err}<form method="post" action="/start" enctype="multipart/form-data">{hidden_as(user)}{self._once()}<input type="hidden" name="process" value="{esc(name)}">
<div class="fields">{self._start_inputs(rt, values, required=self._required_at_start(name, rt), with_errors=bool(values))}</div>
{file_input("files", "Files to attach", multiple=True, hint=f"up to {self.max_upload // (1024 * 1024)} MB each")}
{self._advanced_json(rt, values.get("json", ""))}
<div class="actions"><button>{esc(verb)}</button></div></form>
{details("Why can I do this?", f"<div class='why'>{self._why_html(d, 'create', op['object'], op['folder'])}</div>")}</details>"""

    def _new_folder_control(self, bundle: dict, folder: str) -> str:
        """The New folder button: inside a folder the person may file in, a name; at the top of the library, a choice of
        the folders they may file in; where they may not, why and who may."""
        user = self.user
        name_input = '<label class="f"><span>Name</span><input name="name" required maxlength="60" placeholder="for example Work instructions"></label>'
        note = ('<p class="why">A new folder has the same rights as the folder it is in: whoever may read or file there may read or file in it. '
                'A folder with rights of its own is a policy folder: a policy owner adds it in Settings.</p>')
        if folder:
            d = may_file(bundle, user, folder)
            if d["allowed"]:
                inner = (f'<form method="post" action="/folder-new">{hidden_as(user)}<input type="hidden" name="parent" value="{esc(folder)}">'
                         f'{name_input}<div class="actions"><button>{icon("plus")}Add to {esc(folder)}</button></div></form>{note}')
            else:
                inner = (f'<p>You may not file documents in {esc(folder)}, so you cannot add folders to it.</p>'
                         f'<p class="why">{esc(d.get("why") or "")}. People who may file here, or a document controller, add folders in it.</p>')
        else:
            mine = [x for x in bundle.get("folders") or [] if may_file(bundle, user, x)["allowed"]]
            if mine:
                opts = "".join(f'<option value="{esc(x)}">{esc(x)}</option>' for x in mine)
                inner = (f'<form method="post" action="/folder-new">{hidden_as(user)}<label class="f"><span>In</span><select name="parent">{opts}</select></label>'
                         f'{name_input}<div class="actions"><button>{icon("plus")}Add the folder</button></div></form>{note}')
            else:
                inner = '<p>You may not file documents in any folder, so you cannot add folders.</p>' + note
        return (f'<details class="tb-menu" id="new-folder"><summary class="button secondary">{icon("folder")}New folder</summary>'
                f'<div class="tb-pop card">{inner}</div></details>')

    def _run_cards(self, rec: dict, run: dict | None) -> str:
        """The run's controls on an item's page (a record or a document): a stopped run goes back to its owner, a
        returned item is reworked and resubmitted, and whoever a decision waits for decides here; others see who it
        waits for."""
        user = self.user
        pending = (run or {}).get("pending") or {}
        out = ""
        if run and run["status"] == "failed" and rec["state"] == "failed":
            out += self._failed_card(run, rec)
        if run and pending and pending.get("kind") == "resubmit":
            if user in pending["assignees"]:
                out += f"<div class='card warn' id='rework'><h2 style='margin-top:0;border:0'>Returned to you for rework</h2><p><b>Reason:</b> {esc(pending.get('reason') or '-')}</p>{self._resubmit_form(run, rec)}{self._cancel_form(run, rec)}</div>"
            else:
                out += f"<div class='card warn'><b>Returned for rework</b>: {esc(pending.get('reason') or '')} <span class='why'>Waiting for {esc(', '.join(pending['assignees']))} to resubmit.</span></div>"
        if run and pending and pending.get("kind", "decision") == "decision" and run["status"] == "waiting":
            if user in pending["assignees"]:
                out += f"<div id='decide'>{self._decision_card(run, standalone=True)}</div>"
            else:
                out += f"<div class='card warn'>Waiting for {' '.join(self._avatar(a, size='sm', named=True) for a in pending['assignees'])} at <b>{esc(pending['title'])}</b>{' (approval due ' + esc(day(pending['due'])) + ')' if pending.get('due') else ''}.</div>"
        return out

    def _layout_sheet(self, rec: dict, layout: dict | None = None) -> str:
        """A record's fields as its form layout shows them (the one in force, or ``layout`` for a preview): each section
        a titled block of field rows, a row's visible_when tested on the server, its label and help. Values the layout
        leaves off stay on the record and are listed under "Not on the form"."""
        from .. import formlayout

        lay = layout or formlayout.active_layout(self.hub, rec["type"])
        f = rec["fields"]
        filled = lambda k: k not in BLOB_FIELDS and k != "reviews" and f.get(k) not in (None, "", [], {})
        blocks, placed = [], set()
        sections = formlayout.visible_rows(lay, f, rec["state"])
        for i, (sec, rows) in enumerate(sections):
            items = ""
            for r in rows:
                k = r["field"]
                placed.add(k)
                if not filled(k):
                    continue
                label = r.get("label") or ((lay.get("custom_fields") or {}).get(k) or {}).get("label") or field_label(k)
                hint = f'<span class="why">{esc(r["help"])}</span>' if r.get("help") else ""
                ro = " <span class='chip' title='read-only now'>read-only</span>" if r.get("readonly_when") and formlayout.evaluate(r["readonly_when"], f, rec["state"]) else ""
                items += f'<div class="fld" data-field="{esc(k)}"><dt>{esc(label)}{ro}</dt><dd>{self._field_html(k, f[k], f)}{hint}</dd></div>'
            if not items:
                continue
            head = f"<h3 class='sec'>{esc(sec.get('title') or '')}</h3>" if len(sections) > 1 or (sec.get("title") or "Details") != "Details" else ""
            blocks.append(f'<div class="fsec" id="sec-{esc(sec.get("id") or i)}">{head}<dl class="sheetfields">{items}</dl></div>')
        td = self.hub.kernel.types.get(rec["type"], {})
        system = set(td.get("system_fields") or [])
        hidden_rows = [s for s, _ in sections for r in s.get("fields") or [] if False]  # (kept for clarity: hidden rows are not listed)
        rest = [k for k in sorted(f) if k not in placed and k not in system and filled(k) and k not in formlayout.NOT_ON_FORMS
                and not any(r.get("field") == k for s in lay.get("sections") or [] for r in s.get("fields") or [])]
        if rest:
            items = "".join(f'<div class="fld"><dt>{esc(field_label(k))}</dt><dd>{self._field_html(k, f[k], f)}</dd></div>' for k in rest)
            blocks.append(details("Not on the form", f'<dl class="sheetfields">{items}</dl>'))
        return "".join(blocks) or "<p class='muted'>No fields.</p>"

    def _required_at_start(self, process: str, rt: str) -> list[str]:
        req = list(self.hub.runner.required_at_start(process))
        for f in REQUIRED_EXTRA.get(rt, []):
            if f not in req and f in {n for n, _, _ in self._start_spec(rt)}:
                req.append(f)
        return req

    def _start_spec(self, rt: str) -> list[tuple[str, str | None, str | None]]:
        """(field, widget kind, type spec) for the Start form of a record type, from the form layout in force."""
        from .. import formlayout

        td = self.hub.kernel.types.get(rt, {}).get("fields", {})
        if rt not in self.hub.kernel.types:
            return [(e[0], e[1] if len(e) > 1 else None, (e[2] if len(e) > 2 else None) or td.get(e[0])) for e in START_INPUTS.get(rt, DEFAULT_START_INPUTS)]
        out = []
        for r in formlayout.active_layout(self.hub, rt).get("start") or []:
            out.append((r["field"], r.get("widget"), r.get("spec") or td.get(r["field"])))
        return out

    def _start_field_names(self, rt: str) -> list[str]:
        return [n for n, _, _ in self._start_spec(rt)] + ["json"]

    def _start_inputs(self, rt: str, values: dict | None = None, required: list[str] | None = None, with_errors: bool = False,
                      errors: dict[str, str] | None = None) -> str:
        values = values or {}
        req = set(required or [])
        errors = dict(errors or {})
        out = ""
        for name, kind, spec in self._start_spec(rt):
            error = errors.get(name) or (self._field_error(name) if with_errors else None)
            cogs = sorted(n for n, m in (self.bundle or {"members": {}})["members"].items() if m["type"] == "cog") if name == "assignee" else []
            out += widget(name, spec, values.get(name, ""), required=name in req, kind=kind, people=self._people() + cogs,
                          options=self._picker_options(name, spec), error=error)
        return out

    def _advanced_json(self, rt: str, value: str = "") -> str:
        """The JSON box for fields the form does not show, with an example that works for this type; none when there is nothing to add."""
        td = self.hub.kernel.types.get(rt, {})
        shown = {n for n, _, _ in self._start_spec(rt)}
        system = set(td.get("system_fields") or [])
        extra = [f for f in td.get("fields", {}) if f not in shown and f not in system and f not in BLOB_FIELDS and f not in ("attachments",)]
        if not extra:
            return ""
        first = extra[0]
        spec = td["fields"][first]
        sample = {"text": "example", "number": 1, "money": 10.0, "int": 1, "date": self.today, "list": ["a", "b"], "bool": True, "ref": "ITEM-00001"}.get(
            spec.split(":")[0], spec.split(":")[1].split(",")[0] if spec.startswith("enum:") else "example")
        example = json.dumps({first: sample})
        error = self._field_error("json")
        box = text_input("json", "Other fields as JSON", value, textarea=True, error=error, hint=f"also settable: {', '.join(extra)}; for example {example}")
        return details("Advanced: other fields", box, open_=bool(error or value))

    def page_start(self):
        process = self.q.get("process", [""])[0]
        return self._redirect(href("/", self.user, start=process))

    # ----------------------------------------------------------------- files
    def page_files(self, status: int = 200):
        user = self.user
        bundle = self._need_release()
        folder = self.q.get("folder", [""])[0].strip("/")
        if folder.startswith("Policy"):
            self.hub.frame_documents()  # every Frame has its document in Policy/Frames
        sort = self.q.get("sort", [""])[0] or "date"
        tree = self._library_tree(folder)
        if not folder:
            roots = [f for f in bundle["folders"] if "/" not in f]
            rows = []
            for f in roots:
                d = self._can_read(f)
                rows.append([f'{icon("folder")} <a href="{href("/files", user, folder=f)}">{esc(f)}</a>', chip("readable") if d["allowed"] else chip("no access"),
                             f'<span class="why">{self._why_html(d, "read", "*", f)}</span>'])
            recent = sorted(self._records("document"), key=lambda r: r.get("updated_at") or r["created_at"], reverse=True)[:24]
            body = (f"<h1>{icon('folder', cls='lg')} Documents</h1><div class='library'>{tree}<div>"
                    f"<div class='toolbar'>{self._new_folder_control(bundle, '')}</div>"
                    f"<p class='why'>Folders you may open, and why. Recent documents across them below.</p>" + table(["Folder", "Access", "Why"], rows)
                    + "<h2>Recent documents</h2>" + self._tiles(recent) + "</div></div>")
            return self._page("Files", body, crumbs=[("Files", None)])
        d = self._can_read(folder)
        parts = folder.split("/")
        crumbs = [("Files", href("/files", user))] + [(p, href("/files", user, folder="/".join(parts[: i + 1]))) for i, p in enumerate(parts[:-1])] + [(parts[-1], None)]
        body = f"<h1>{icon('folder-open', cls='lg')} {esc(parts[-1])} <span class='why'>{esc(folder)}</span></h1>"
        if not d["allowed"]:
            return self._page(folder, body + f"<div class='card crit' role='alert'>You cannot read this folder.<div class='why'>{self._why_html(d, 'read', '*', folder)}</div></div>", status=403, crumbs=crumbs)
        subs = [f for f in bundle["folders"] if f.startswith(folder + "/") and "/" not in f[len(folder) + 1:]]
        text = self.q.get("text", [""])[0]
        recs = [r for r in self.hub.kernel.query(folder=folder, text=text or None) if r["folder"] == folder]
        records = list(reversed([r for r in recs if r["type"] != "document"]))
        docs = [r for r in recs if r["type"] == "document"]
        keyf = {"name": lambda r: str(r["fields"].get("title") or "").lower(), "revision": lambda r: (str(r["fields"].get("revision") or ""), int(r["fields"].get("minor") or 0)),
                "date": lambda r: (r.get("updated_at") or r["created_at"], r["events"][-1])}.get(sort, lambda r: (r.get("updated_at") or r["created_at"], r["events"][-1]))
        docs.sort(key=keyf, reverse=(sort == "date"))
        f = may_file(bundle, user, folder)
        toolbar = '<div class="toolbar">'
        if f["allowed"]:
            toolbar += f'<a class="button" href="#new-document">{icon("plus")}New document</a><a class="button secondary" href="#upload">{icon("upload")}Upload</a>'

        sorts = "".join(f'<option value="{k}"{" selected" if sort == k else ""}>{v}</option>' for k, v in (("date", "newest first"), ("name", "name"), ("revision", "revision")))
        toolbar += (f'<form method="get" action="/files" class="sort">{hidden_as(user)}<input type="hidden" name="folder" value="{esc(folder)}">'
                    f'<label class="f" style="width:auto"><span class="vh">Search this folder</span><input type="search" name="text" value="{esc(text)}" aria-label="Search this folder" title="number or words in the title"></label>'
                    f'<label class="f" style="width:auto"><span>Sort by</span><select name="sort" class="small">{sorts}</select></label><button class="ghost">{icon("search")}Search</button></form>'
                    + self._new_folder_control(bundle, folder) + '</div>')
        body += f"<div class='library'>{tree}<div>{toolbar}"
        lib = self.hub.library.all()
        if subs:
            body += "<h2>Folders</h2><ul class='folders'>" + "".join(
                f'<li>{icon("folder")} <a href="{href("/files", user, folder=s)}">{esc(s.split("/")[-1])}</a>'
                + (f' <span class="why">added by {esc(lib[s].get("by"))}; the same rights as {esc(folder)}</span>' if s in lib else "") + "</li>" for s in subs) + "</ul>"
        if folder in lib and not docs and not any(x.startswith(folder + "/") for x in lib):
            body += (f'<form method="post" action="/folder-remove" class="inline">{hidden_as(user)}<input type="hidden" name="path" value="{esc(folder)}">'
                     f'<button class="ghost">{icon("x")}Remove this empty folder</button></form>')
        body += f"<h2>Documents <span class='muted'>{len(docs)}</span></h2>" + self._tiles(docs)
        body += "<h2>Items</h2><p class='why'>Newest first.</p>" + table(["Number", "Type", "State", "Title", "Owner", "With", "Due", "Updated"],
                                                                          [[f"{type_icon(r['type'])} {self._record_link(r['locator'], with_summary=False)}", tag(type_name(r["type"])), self._state_chip(r["state"]),
                                                                            esc(summary_of_record(r)), esc(owner_of(r)), avatars(r["assignees"], bundle),
                                                                            self._due_html(r["due"] or r["fields"].get("due")), esc(when(r.get("updated_at") or r.get("created_at")))] for r in records],
                                                                          "No items in this folder.")
        if f["allowed"]:
            err = self._form_error(["file", "title"]) if self.err and self.route == "/upload" else ""
            body += f"""<h2 id="upload">{icon('upload')} Upload a file</h2><div class="card"><p class="why">An office or text file becomes a working document you can edit online; anything else, or a file you tick to keep as received, is filed as an external document (kept as-is, with provenance).</p>{err}
<form method="post" action="/upload" enctype="multipart/form-data">{hidden_as(user)}{self._once()}<input type="hidden" name="folder" value="{esc(folder)}">
<div class="fields">{file_input("file", "File", required=True, hint=f"up to {self.max_upload // (1024 * 1024)} MB")}{text_input("title", "Title", self._val("title"), hint="defaults to the file name", error=self._field_error("title"))}
<label class="f check"><input type="checkbox" name="keep" value="1"> <span>Keep as received (evidence; not editable)</span></label></div>
<div class="actions"><button>{icon('upload')}Upload</button></div></form></div>"""
            body += new_document_form(user, folder=folder, once=self._once(), error=self._form_error(["title", "kind"]) if self.route == "/new-document" else "")
        else:
            body += f"<p class='why'>You cannot file documents here: {self._why_html(f, 'create', 'document', folder)}</p>"
        body += "</div></div>"
        self._page(parts[-1], body, status=status, crumbs=crumbs)

    def _library_tree(self, current: str) -> str:
        """The folder tree of the Documents app with icons; folders the user cannot read are greyed and say so."""
        user, bundle = self.user, self.bundle

        def render(node: dict, prefix: str) -> str:
            out = "<ul>"
            for name, child in node.items():
                path = f"{prefix}/{name}" if prefix else name
                ok = self._can_read(path)["allowed"]
                cur = ' aria-current="page"' if path == current else ""
                cls = "" if ok else ' class="denied"'
                out += f'<li><a{cur}{cls} href="{href("/files", user, folder=path)}" title="{esc(path)}{"" if ok else " (no access)"}">{icon("folder-open" if current.startswith(path) and cur == "" else "folder")}{esc(name)}</a>'
                out += (render(child, path) if child else "") + "</li>"
            return out + "</ul>"

        return f'<nav class="ftree" aria-label="Folders">{render(folder_tree(bundle["folders"]), "")}</nav>'

    def _tiles(self, docs: list[dict]) -> str:
        """Document tiles: a type badge or a text preview, the title, revision and state, and the open and download links."""
        user = self.user
        if not docs:
            return "<p class='muted'>No documents here.</p>"
        tiles = ""
        for d in docs:
            f = d["fields"]
            name = f.get("filename") or ""
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext in ("md", "txt") and f.get("text"):
                thumb = f"<pre aria-hidden='true'>{esc(short(_plain_preview(f['text']), 220))}</pre>"
            else:
                thumb = file_icon(name, label=False)
            page = href("/document/" + quote(d["locator"], safe=":"), user)
            links = f'<a href="{page}">{icon("file-text")}Open</a>'
            if wopi.is_viewable(d):
                links += f'<a href="{href("/edit/" + quote(d["locator"], safe=":"), user)}">{icon("edit" if wopi.is_editable(d) else "eye")}{"Edit online" if wopi.is_editable(d) else "View"}</a>'
            if f.get("content_sha"):
                links += f'<a href="{href("/download", user, doc=d["locator"])}">{icon("download")}Download</a>'
            locked = f' {chip("checked out", "warn")}' if f.get("checked_out_by") else ""
            tiles += (f'<div class="tile"><div class="thumb">{thumb}</div><div class="name">{file_icon(name)} <a href="{page}">{esc(f.get("title") or d["number"])}</a></div>'
                      f'<div class="tmeta">{esc(d["number"])} &middot; rev {esc(f.get("revision", ""))}.{esc(f.get("minor", 0))} {self._state_chip(d["state"])}{locked}</div>'
                      f'<div class="tmeta">{self._avatar(owner_of(d), size="sm", named=True) if owner_of(d) else ""} <span>{esc(when(d.get("updated_at") or d["created_at"]))}</span></div>'
                      f'<div class="tlinks">{links}</div></div>')
        return f'<div class="tiles">{tiles}</div>'

    # ---------------------------------------------------------------- boards
    def page_boards(self):
        user = self.user
        bundle = self._need_release()
        workflows = bundle.get("workflows", {})
        type_ = self.q.get("type", [""])[0]
        if type_ and type_ not in self.hub.kernel.types:
            raise ShellError(f"There is no item type '{type_}'. Choose one from the list on the Boards page.", 404)
        state, folder, assignee, text = (self.q.get(k, [""])[0] for k in ("state", "folder", "assignee", "text"))
        readable: dict[str, bool] = {}

        def can(r):
            if r["folder"] not in readable:
                readable[r["folder"]] = self._can_read(r["folder"])["allowed"]
            return readable[r["folder"]]

        types = "".join(f'<option value="{esc(t)}"{" selected" if t == type_ else ""}>{esc(type_name(t))}</option>' for t in self.hub.kernel.types if t != "$comment")
        folders = "".join(f'<option value="{esc(f)}"{" selected" if f == folder else ""}>{esc(f)}</option>' for f in bundle["folders"])
        people = "".join(f'<option value="{esc(p)}"{" selected" if p == assignee else ""}>{esc(p)}</option>' for p in self._people())
        wf_for_type = None
        if not type_:
            # The user's own work, in columns named by what the person has to do.
            mine = [r for r in self.hub.kernel.query(folder=folder or None, assignee=assignee or None, text=text or None)
                    if r["type"] != "document" and (owner_of(r) == user or user in r["assignees"] or r["created_by"] == user) and can(r)]
            cols = {"Waiting for me": [], "Waiting on others": [], "Returned or stopped": [], "Done": [], "Cancelled or rejected": []}
            for r in mine:
                s = r["state"]
                if s in ("cancelled", "rejected", "obsolete"):
                    cols["Cancelled or rejected"].append(r)
                elif s in ("returned", "failed"):
                    cols["Returned or stopped"].append(r)
                elif s.startswith("awaiting") or s in ("open", "draft", "in-review"):
                    cols["Waiting for me" if user in r["assignees"] else "Waiting on others"].append(r)
                else:
                    cols["Done"].append(r)
            grouped = cols
            heading = "My work"
            states = ""
            intro = "Your items, by what happens next. Choose a type to see every item of that kind by state."
        else:
            columns: list[str] = []
            for name, wf in workflows.items():
                if wf["record_type"] == type_:
                    wf_for_type = wf_for_type or wf
                    for s in wf["states"]:
                        if s not in columns:
                            columns.append(s)
            if not columns:
                columns.append("open")
            for s in ("returned", "failed", "rejected", "cancelled"):
                if s not in columns:
                    columns.append(s)
            recs = [r for r in self.hub.kernel.query(type_=type_, folder=folder or None, state=state or None, assignee=assignee or None, text=text or None) if can(r)]
            grouped = {state_name(c, wf_for_type): [] for c in columns}
            for r in recs:
                grouped.setdefault(state_name(r["state"], wf_for_type), []).append(r)
            heading = type_name(type_) + " board"
            states = "".join(f'<option value="{esc(s)}"{" selected" if s == state else ""}>{esc(state_name(s, wf_for_type))}</option>' for s in columns)
            intro = f"Every {type_name(type_).lower()} you may read, by state."
        saved = href("/boards", user, type=type_, state=state, folder=folder, assignee=assignee, text=text)
        cog_boards = " ".join(f"<a href='{href('/board/' + c, user)}'>{icon('robot')}{esc(c)}</a>"
                              for c in sorted(n for n, m in bundle["members"].items() if m["type"] == "cog"))
        body = f"""<h1>{esc(heading)}</h1><p class="why">{esc(intro)}</p>{('<div class="chips" role="group" aria-label="Cog boards"><span class="lbl">Cog boards</span>' + cog_boards + '</div>') if cog_boards else ''}
<form method="get" action="/boards" class="card">{hidden_as(user)}<div class="fields">
<label class="f"><span>Type</span><select name="type"><option value="">my work</option>{types}</select></label>
{f'<label class="f"><span>State</span><select name="state"><option value="">any</option>{states}</select></label>' if states else ''}
<label class="f"><span>Folder</span><select name="folder"><option value="">any</option>{folders}</select></label>
<label class="f"><span>Assigned to</span><select name="assignee"><option value="">anyone</option>{people}</select></label>
{widget("text", "text", text, label="Search", hint="number or words")}</div>
<div class="actions"><button>{icon("filter")}Filter</button> <a href="{saved}">Link to this filter</a>{(' <a class="button ghost" href="' + href("/items/" + type_, user, view="kanban") + '">' + icon("kanban") + 'Open as the ' + esc(type_name(type_).lower()) + ' kanban</a>') if type_ else ''}</div></form><div class="board">"""
        total = 0
        for col, items in grouped.items():
            total += len(items)
            body += f"<div class='col{' empty' if not items else ''}'><h3>{esc(col)}<span class='n'>{len(items)}</span></h3>"
            for r in reversed(items):
                body += self._kanban_card(r, wf_for_type if type_ else None, show_type=not type_)
            body += "</div>"
        body += "</div>"
        if not total:
            body += f"<p class='muted'>No {esc(type_name(type_).lower() + 's') if type_ else 'items of yours'} here yet.</p>"
        self._page("Boards", body, crumbs=[("Boards", None)])

    # ------------------------------------------------------------------ runs
    def page_runs(self):
        user = self.user
        self._need_release()
        if not self._is_member():
            return self._page("Runs", f"<h1>Runs</h1><div class='card'><b>{esc(user)} is not a member</b> of the organization; no runs are visible.</div>")
        text = self.q.get("text", [""])[0].lower()
        only_mine = self.q.get("owner", [""])[0] == "me"
        rows = []
        for r in reversed(self._visible_runs()):
            if only_mine and r["owner"] != user:
                continue
            rec = self.hub.kernel.get(r["record"]) if r.get("record") else None
            if text and text not in (r["id"] + " " + (rec["number"] + " " + summary_of_record(rec) if rec else "") + " " + r["process"]).lower():
                continue
            if r.get("pending"):
                waiting = esc(", ".join(r["pending"]["assignees"])) + (f" ({self._due_html(r['pending']['due'])})" if r["pending"].get("due") else "")
                if r["pending"].get("kind") == "resubmit":
                    waiting = "rework by " + waiting
            elif r["status"] == "failed":
                waiting = f"<span class='why'>stopped: {esc(short(self._failure_of(r['id']).get('reason', ''), 120))}</span>"
            else:
                waiting = ""
            rows.append([self._run_link(r, f"Run of {rec['number']}" if rec else f"Run {r['id'][:8]}"), esc(self._process_name(r["process"])),
                         self._record_link(r["record"]) if r.get("record") else "", esc(r["owner"]), self._state_chip(r["status"]),
                         esc(when(r["created_at"])), waiting])
        form = f"""<form method="get" action="/runs" class="card">{hidden_as(user)}<div class="fields">{widget("text", "text", text, label="Search", hint="number, title or process")}
<label class="f"><span>Whose</span><select name="owner"><option value="">everyone I may see</option><option value="me"{' selected' if only_mine else ''}>mine</option></select></label></div>
<div class="actions"><button>Filter</button></div></form>"""
        self._page("Runs", "<h1>Runs</h1><p class='why'>Newest first: runs whose item folder you may read, plus your own and those waiting for you.</p>" + form
                   + table(["Run", "Process", "Item", "Started by", "Status", "Started", "Waiting for"], rows, "No runs visible to you."), crumbs=[("Runs", None)])

    def page_run(self):
        user = self.user
        self._need_release()
        s = self.hub.runner.load(self.arg)
        if not self._run_visible(s):
            folder = (s.get("manifest") or {}).get("folder", "")
            why = self._why_text(self._can_read(folder), "read", "*", folder) if folder else "it has no folder"
            raise ShellError(f"You cannot read this run: {why}", 403)
        rec = self.hub.kernel.get(s["record"]) if s.get("record") else None
        title = f"Run of {rec['number']}" if rec else f"Run {s['id'][:8]}"
        body = f"""<h1>{esc(title)} {self._state_chip(s['status'])}</h1><div class="card">{esc(self._process_name(s['process']))} started by {esc(s['owner'])} on {esc(when(s['created_at']))};
item {self._record_link(s['record']) if s.get('record') else '-'}; {self._version_html(s['release'])}{'; parent run ' + self._run_link(s['parent_run']) if s.get('parent_run') else ''}</div>"""
        if s.get("pending"):
            p = s["pending"]
            what = "Waiting for rework by" if p.get("kind") == "resubmit" else f"Waiting at <b>{esc(p['title'])}</b> for"
            body += f"<div class='card warn'>{what} {esc(', '.join(p['assignees']))}{' (approval due ' + esc(day(p['due'])) + ')' if p.get('due') else ''}<div class='why'>{self._rule_html(p.get('route_rule'))}</div></div>"
            if user in p["assignees"] and p.get("kind", "decision") == "decision":
                body += self._decision_card(s, standalone=True)
        if s["status"] == "failed":
            body += self._failed_card(s, rec)
        if s.get("grant"):
            g = s["grant"]
            body += f"<p class='why'>Approval granted by {esc(g['decided_by'])} ({'cog' if g['by_type'] == 'cog' else 'person'}) on {esc(when(g['issued_at']))}.</p>"
        rows = []
        for step in s["manifest"]["steps"]:
            srec = s["steps"].get(step["id"])
            if not srec:
                rows.append([esc(step["title"]), esc(step["cog"]["id"]), chip("not reached", ""), "", ""])
                continue
            env = srec["envelope"]
            probs = "<br>".join(f"<span class='chip {'crit' if p['severity'] == 'error' else 'warn'}'>{esc(p['check'])}</span> {esc(p['detail'])}" for p in env["problems"])
            if env.get("error"):
                probs += f"<span class='chip crit'>{esc(env['error']['code'])}</span> {esc(env['error']['detail'])}"
            dec = ""
            if srec.get("decision"):
                d = srec["decision"]
                dec = f"{chip(d['verdict'])} by {esc(d['by'])} ({'cog' if d['by_type'] == 'cog' else 'person'})<div class='why'>{esc(d['meaning'])}</div>"
                if d.get("delegating_rule"):
                    dec += f"<div class='why'>under {self._rule_html(d['delegating_rule'])}</div>"
            result = {"passed": "passed", "failed": "stopped", "pass-with-problems": "passed with warnings", "gate-pending": "passed"}.get(srec["status"], srec["status"])
            gate = step.get("gate") or {}
            if gate.get("type") == "human":
                # The step's own work, then its approval as a row of its own (the approval is what waits, not the step).
                rows.append([esc(step["title"]), f"{esc(env['cog']['id'])} {esc(env['cog']['version'])}", chip(result), probs, ""])
                pend = s.get("pending") or {}
                if srec["status"] == "gate-pending" and pend.get("step") == step["id"]:
                    who = ", ".join(pend.get("assignees") or [])
                    rows.append([esc(gate.get("title") or "Approval"), esc(who), chip("waiting"), "", f"<span class='why'>waiting for {esc(who)}</span>"])
                elif dec:
                    rows.append([esc(gate.get("title") or "Approval"), esc(srec["decision"]["by"]), chip(srec["decision"]["verdict"] + "d"), "", dec])
                else:
                    rows.append([esc(gate.get("title") or "Approval"), "", chip("not reached", ""), "", ""])
                continue
            rows.append([esc(step["title"]), f"{esc(env['cog']['id'])} {esc(env['cog']['version'])}", chip(result), probs, dec])
        body += "<h2>Steps</h2>" + table(["Step", "Done by", "Result", "Problems", "Decision"], rows)
        body += "<h2>History</h2>" + self._track_html(s["id"])
        crumbs = [("Runs", href("/runs", user))] + ([(rec["number"], href("/record/" + quote(rec["locator"], safe=":"), user))] if rec else []) + [(title, None)]
        self._page(title, body, crumbs=crumbs)

    def _track_html(self, run_id: str) -> str:
        items = ""
        for ev in self.hub.runner.track(run_id):
            detail = {k: v for k, v in ev.items() if k not in ("seq", "at", "type", "run_id", "record_digest")}
            items += (f"<li><span class='muted'>{esc(when(ev['at']))}</span> {track_sentence(ev, self._number_link)} "
                      f"{details('details', f'<pre class=cell>{esc(short(json.dumps(detail, indent=1, default=str), 4000))}</pre>')}</li>")
        return f"<ol class='history'>{items}</ol>" if items else "<p class='muted'>Nothing yet.</p>"

    # ---------------------------------------------------------------- record
    def page_record(self, status: int = 200):
        user = self.user
        bundle = self._need_release()
        rec = self.hub.kernel.get(self.arg)
        if rec is None:
            raise ShellError(f"There is no item '{self.arg}'. Check the number, or find it under Files.", 404)
        if rec["type"] == "document":
            return self._redirect(href("/document/" + quote(rec["locator"], safe=":"), user))
        self._require_read(rec["folder"])
        loc = rec["locator"]
        run = self.hub.runner.run_for_record(loc)
        process, wf = workflow_for(bundle, rec, run)
        obj = self.hub.kernel.types.get(rec["type"], {}).get("object", rec["type"])
        may_approve = authz.check(bundle, user, "approve", obj, rec["folder"])
        labels = " ".join(tag(f"{k}: {v}") for k, v in rec["labels"].items())
        hold = f' <span class="chip crit">on hold</span>' if rec["hold"] else ""
        tname = type_name(rec["type"])
        headline = rec["fields"].get("statement") if rec["type"] == "requirement" else summary_of_record(rec)
        typed_due = rec["fields"].get("due")
        pending = (run or {}).get("pending") or {}
        approval_due = pending.get("due") if pending.get("kind", "decision") == "decision" else None
        if not approval_due and rec["state"].startswith("awaiting"):
            approval_due = rec["due"]
        can = []
        if may_approve["allowed"]:
            can.append("approve here" if user in rec["assignees"] else "hold or release this item")
        if run and run["owner"] == user and run["status"] in ("failed", "waiting"):
            can.append("retry or cancel its run" if run["status"] == "failed" else "cancel its run")
        docs = self.hub.dms.documents_of(loc)
        links = self.hub.kernel.links_of(loc)
        f_may = may_file(bundle, user, rec["folder"])
        decisions = list(rec["decisions"])
        reviews = [r for r in (rec["fields"].get("reviews") or []) if isinstance(r, dict)]
        runs_of = [r for r in self.hub.runner.list() if r.get("record") == loc]
        # --- the form head: action buttons on the left, the status bar pipeline on the right
        btns = ""
        decides_here = run and pending and pending.get("kind", "decision") == "decision" and run["status"] == "waiting" and user in pending["assignees"]
        if decides_here:
            btns += f'<a class="button" href="#decide" data-hotkey="a">{icon("check")}Approve<kbd class="hk">a</kbd></a><a class="button reject" href="#reject-{esc(run["id"])}" data-hotkey="x">{icon("x")}Reject<kbd class="hk">x</kbd></a>'
        if run and pending and pending.get("kind") == "resubmit" and user in pending["assignees"]:
            btns += f'<a class="button" href="#rework">{icon("retry")}Rework and resubmit</a>'
        if run and run["status"] == "failed" and rec["state"] == "failed":
            btns += f'<a class="button" href="#stopped">{icon("retry")}Fix and retry</a>'
        if may_approve["allowed"]:
            btns += f'<a class="button ghost" href="#hold">{icon("pause")}{"Release the hold" if rec["hold"] else "Put on hold"}</a>'
        if f_may["allowed"]:
            btns += f'<a class="button ghost" href="#attach">{icon("paperclip")}Attach</a><a class="button ghost" href="#new-document">{icon("plus")}New document</a>'
        startable = [(n, op) for n, op in bundle["ops"].items() if authz.check(bundle, user, "create", op["object"], op["folder"])["allowed"]
                     and (op["steps"][0]["role"] in ("system", *FIELD_ROLES) or op["steps"][0]["role"] in authz.roles_of(bundle, user))]
        if startable:
            btns += f'<a class="button ghost" href="{href("/", user)}#start">{icon("play")}Start a process</a>'
        btns += self._why_not_me(rec) + self._assign_form(rec) + self._restart_form(rec)
        btns += f'<form method="post" action="/snapshot" class="inline">{hidden_as(user)}{self._once()}<input type="hidden" name="record" value="{esc(loc)}"><button class="ghost" title="files a copy of this item as an evidence document">{icon("camera")}Render snapshot</button></form>'
        btns += f'<button type="button" class="ghost" onclick="print()">{icon("printer")}Print</button>'
        body = f'<div class="formhead"><div class="btns">{btns}</div>{self._statusbar(wf, rec)}</div>' + self._ops_row(rec, run)
        body += self._smart_buttons([("Documents", len(docs), "paperclip", "documents"), ("Links", len([l for l in links if l["rel"] != "has-document"]), "link", "links"),
                                     ("Runs", len(runs_of), "activity", "workflow"), ("Decisions", len(decisions), "check-circle", "decisions")]
                                    + ([("Reviews", len(reviews), "eye", "reviews")] if reviews else []))
        # --- the sheet
        body += f"""<div class="sheet" id="sheet" data-live><h1 class="title">{esc(rec['number'])} {tag(tname)} {self._state_chip(rec['state'], wf)}{hold}</h1>
<div class="subtitle"><b>{esc(short(headline, 300))}</b><div class="why">{' &middot; '.join(x for x in [
    f"{esc(tname)} in <a href=\"{href('/files', user, folder=rec['folder'])}\">{esc(rec['folder'])}</a>",
    ("owner " + self._avatar(owner_of(rec), size='sm', named=True)) if owner_of(rec) else "",
    ("with " + " ".join(self._avatar(a, size='sm', named=True) for a in rec['assignees'])) if rec['assignees'] else "",
    ("due " + esc(day(typed_due))) if typed_due else "",
    ("approval " + self._due_html(approval_due)) if approval_due else "",
    esc(self._process_name(process)) if process and self._process_name(process) != tname else "",
    (self._run_link(run, 'run ' + run['id'][:8]) + ' ' + self._state_chip(run['status'])) if run else "",
    labels] if x)}</div>
{('<div class=why>You may ' + esc(' and '.join(can)) + '.</div>') if can else ''}</div>"""
        if rec["hold"]:
            body += f"<div class='card crit' role='status'><b>On hold:</b> {esc(short(rec.get('hold_reason') or 'no reason given', 400))}. Nothing can be decided until an approver releases the hold.</div>"
        body += self._run_cards(rec, run)
        if rec["type"] == "change-order":
            body += self._eco_panel(rec)
        tabs = [("details", "Details", "form"), ("documents", "Documents", "paperclip"), ("links", "Links", "link")] + \
               ([("reviews", "Reviews", "eye")] if reviews else []) + [("history", "History", "activity"), ("workflow", "Workflow and decisions", "workflow")]
        body += '<nav class="tabs" role="tablist" aria-label="Sections">' + "".join(
            f'<a href="#{t}" data-tab="tab-{t}" role="tab" aria-selected="{"true" if i == 0 else "false"}">{icon(ic)}{label}</a>' for i, (t, label, ic) in enumerate(tabs)) + "</nav>"
        # Details: the sections of the form layout in force, then the hold form.
        td = self.hub.kernel.types.get(rec["type"], {})
        system = set(td.get("system_fields") or [])
        shown = lambda k, v: k not in BLOB_FIELDS and k != "reviews" and v not in (None, "", [], {})
        row = lambda k: f'<div class="fld"><dt>{esc(field_label(k))}</dt><dd>{self._field_html(k, rec["fields"][k], rec["fields"])}</dd></div>'
        sys_rows = "".join(row(k) for k in sorted(system) if k in rec["fields"] and shown(k, rec["fields"][k]))
        body += '<section class="tab-panel active" id="tab-details"><h2 id="details">Details</h2>' + self._layout_sheet(rec)
        if sys_rows:
            body += details("Set by the system", f'<dl class="sheetfields">{sys_rows}</dl>')
        err_hold = self._field_error("reason") if self.route == "/hold" else None
        err_form = self._form_error(["reason"]) if self.route in ("/hold", "/snapshot") else ""
        body += f"""<div class="card" id="hold">{err_form}<form method="post" action="/hold">{hidden_as(user)}<input type="hidden" name="record" value="{esc(loc)}"><input type="hidden" name="on" value="{'0' if rec['hold'] else '1'}">
<div class="fields">{text_input("reason", "Reason for the hold" if not rec["hold"] else "Reason for releasing the hold", self._val("reason") if self.route == "/hold" else "", required=True, error=err_hold)}</div>
<div class="actions"><button class="secondary"{'' if may_approve['allowed'] else ' disabled'}>{icon('pause')}{'Release the hold' if rec['hold'] else 'Put on hold'}</button>
{'' if may_approve['allowed'] else '<span class="why">Only approvers of this item may hold it: ' + self._why_html(may_approve, 'approve', obj, rec['folder']) + '</span>'}</div></form></div>"""
        if not self._may_withdraw(rec):
            body += f"""<details class="card quiet" id="withdraw"><summary>Withdraw this draft</summary><form method="post" action="/withdraw-record">{hidden_as(user)}
<input type="hidden" name="record" value="{esc(loc)}"><div class="fields">{text_input("reason", "Why it is withdrawn", "", required=True)}</div>
<div class="actions"><button class="reject">{icon('x')}Withdraw</button><span class="why">It stays in the history, closed; nothing else changes.</span></div></form></details>"""
        body += "</section>"
        # Documents
        body += '<section class="tab-panel" id="tab-documents"><h2 id="documents">Documents</h2>' + table(["Number", "Title", "Class", "Type", "Rev", "State", "Size", "Open"],
                                                                                        [[f"{file_icon(d['fields'].get('filename'))} " + self._record_link(d["locator"], with_summary=False), esc(d["fields"].get("title")),
                                                                                          esc(d["fields"].get("doc_class")), esc(d["fields"].get("doc_type")), esc(f"{d['fields'].get('revision', '')}.{d['fields'].get('minor', 0)}"), self._state_chip(d["state"]),
                                                                                          esc(d["fields"].get("size", "")),
                                                                                          (f'<a href="{href("/edit/" + quote(d["locator"], safe=":"), user)}">{"Edit" if wopi.is_editable(d) else "View"}</a>' if wopi.is_viewable(d) else "")] for d in docs], "No documents attached.")
        if f_may["allowed"]:
            body += new_document_form(user, attach_to=loc, once=self._once(), error=self._form_error(["title", "kind"]) if self.route == "/new-document" else "")
            in_folder = [d for d in self.hub.kernel.query(type_="document", folder=rec["folder"]) if d["folder"] == rec["folder"]
                         and d["locator"] not in {x["locator"] for x in docs}]
            opts = [(d["locator"], f"{d['number']} {d['fields'].get('title')}") for d in in_folder]
            err = self._form_error(["file", "title", "doc"]) if self.route == "/attach" else ""
            body += f"""<div class="card" id="attach">{err}<form method="post" action="/attach" enctype="multipart/form-data">{hidden_as(user)}{self._once()}<input type="hidden" name="record" value="{esc(loc)}">
<div class="fields">{file_input("file", "Upload and attach a file")}{text_input("title", "Title for the file", hint="defaults to the file name")}
{widget("doc", "ref", "", label="Or attach an existing document", options=opts, blank="none", hint="") if opts else ''}</div>
<div class="actions"><button>{icon('paperclip')}Attach</button></div></form></div>"""
        else:
            body += f"<p class='why'>You cannot attach documents here: {self._why_html(f_may, 'create', 'document', rec['folder'])}</p>"
        body += "</section>"
        # Links
        body += '<section class="tab-panel" id="tab-links"><h2 id="links">Links</h2>' + table(["Relation", "Item", "Direction"], [[esc(l["label"].replace("-", " ")), self._record_link(l["target"]), esc(l["direction"])] for l in links], "No links.")
        if f_may["allowed"]:
            rels = [(r, r.replace("-", " ")) for r in self.hub.kernel.link_vocab]
            err = self._form_error(["rel", "target"]) if self.route == "/link" else ""
            body += f"""<div class="card">{err}<form method="post" action="/link">{hidden_as(user)}{self._once()}<input type="hidden" name="record" value="{esc(loc)}">
<div class="fields">{widget("rel", "text", self._val("rel") if self.route == "/link" else "", label="Link as", options=rels, required=True, hint="", error=self._field_error("rel") if self.route == "/link" else None)}
{text_input("target", "To item", self._val("target") if self.route == "/link" else "", required=True, hint="number, e.g. PO-1002", error=self._field_error("target") if self.route == "/link" else None)}</div>
<div class="actions"><button>{icon('link')}Add link</button></div></form></div>"""
        body += "</section>"
        # Reviews by cogs (swarm steps and @mentions): one row per cog, with the evidence document holding the full text
        if reviews:
            evidence = [d for d in docs if d["fields"].get("doc_type") == "review"]
            rrows = []
            for rv in reviews:
                ev = next((d for d in reversed(evidence) if rv.get("cog", "") in (d["fields"].get("title") or "")), evidence[-1] if evidence else None)
                rrows.append([self._avatar(rv.get("cog", ""), size="sm", named=True), chip(rv.get("verdict")), esc(short(rv.get("summary"), 400)),
                              esc(f"{rv.get('model') or '-'}{' (stand-in)' if rv.get('stand_in') else ''}"), esc(", ".join(rv.get("sops") or [])),
                              esc(str((rv.get("findings") if not isinstance(rv.get("findings"), list) else len(rv["findings"])) or 0)),
                              self._record_link(ev["locator"], with_summary=False) if ev else ""])
            verdict = rec["fields"].get("review_verdict")
            body += (f'<section class="tab-panel" id="tab-reviews"><h2 id="reviews">Reviews {chip(verdict) if verdict else ""}</h2>'
                     f"<p class='why'>Cogs reviewed this item from the procedures they follow; a model drafted the words, the full text is filed as evidence, and nobody signed.</p>"
                     + table(["Cog", "Verdict", "Summary", "Model", "Procedures", "Findings", "Evidence"], rrows) + "</section>")
        # History
        body += '<section class="tab-panel" id="tab-history"><h2 id="history">History</h2>' + self._history_html(loc, run["id"] if run else None) + "</section>"
        # Workflow and decisions
        body += '<section class="tab-panel" id="tab-workflow"><h2 id="workflow">Workflow</h2>'
        if wf:
            diagram = mermaid_for(wf, rec["state"])
            states = list(wf["states"]) + [s for s in ("returned", "failed", "cancelled") if s not in wf["states"]]
            body += f'<div class="card"><pre class="mermaid" id="wf-diagram">{esc(diagram)}</pre><p class="why">States: ' + " &middot; ".join(self._state_chip(s, wf) + (" (current)" if s == rec["state"] else "") for s in states) \
                + "<br>Returned, failed and cancelled are side exits: a rejection that returns the item to its owner, a stopped step, a cancelled run.</p>"
            trows = []
            for t in wf["transitions"]:
                if t["kind"] == "automatic":
                    trows.append([esc(state_name(t["from"], wf)), esc(state_name(t["to"], wf)), "automatic", esc(", ".join(t.get("steps") or [])), ""])
                else:
                    cp = next((c for c in wf["checkpoints"] if c["id"] == t["step"]), {})
                    who = ""
                    if rec["state"] == t["from"]:
                        people = pending.get("assignees") or rec["assignees"]
                        eligible = [p for p, m in bundle["members"].items() if cp.get("role") in m["roles"]
                                    and authz.check(bundle, p, "approve", obj, rec["folder"])["allowed"]]
                        who = f"<b>pending</b>: assigned to {esc(', '.join(people) or '-')}; role {esc(cp.get('role', '').replace('-', ' '))} held by {esc(', '.join(eligible) or 'nobody')}"
                    trows.append([esc(state_name(t["from"], wf)), esc(state_name(t["to"], wf)), f"approval: {esc(t['title'])}" + (f" (due within {t['sla_days']} days)" if t.get("sla_days") else ""),
                                  f"reject &rarr; {esc(state_name(t.get('on_reject'), wf))}", who])
            body += table(["From", "To", "How", "Steps / on reject", "Who may take it"], trows) + "</div>"
        else:
            body += "<p class='muted'>No workflow in the release covers this item type.</p>"
        if runs_of:
            body += "<h2>Runs</h2>" + table(["Run", "Started by", "Status", "Started", "Waiting for"],
                                             [[self._run_link(r), self._avatar(r["owner"], size="sm", named=True), self._state_chip(r["status"]), esc(when(r["created_at"])),
                                               esc(", ".join((r.get("pending") or {}).get("assignees") or []))] for r in reversed(runs_of)])
        body += '<h2 id="decisions">Decisions</h2>' + table(["When", "By", "Verdict", "Meaning", "Step", "Under"],
                                                             [[esc(when(d.get("at", ""))), f"{self._avatar(d.get('by', ''), size='sm', named=True)} ({'cog' if d.get('by_type') == 'cog' else 'person'})", chip(d.get("verdict")),
                                                               esc(short(d.get("meaning"), 600)) + details("signature", f"<code>{esc(d.get('signature'))}</code> on payload <code>{esc(str(d.get('payload_digest', ''))[:12])}</code>"),
                                                               esc((d.get("step") or "").replace("-", " ")),
                                                               self._rule_html(d["delegating_rule"]) if d.get("delegating_rule") else ""]
                                                              for d in decisions], "No signatures yet.") + "</section>"
        body += self._chatter(rec, run)
        head = (f'<script src="{MERMAID_URL}"></script><script>if(window.mermaid){{mermaid.initialize({{startOnLoad:true,theme:(document.documentElement.getAttribute("data-theme")==="dark"||(document.documentElement.getAttribute("data-theme")==="auto"&&matchMedia("(prefers-color-scheme: dark)").matches))?"dark":"default"}})}}'
                f'else{{addEventListener("load",function(){{var d=document.getElementById("wf-diagram");if(d&&!window.mermaid){{d.hidden=true}}}})}}</script>')
        parts = rec["folder"].split("/")
        crumbs = [("Files", href("/files", user))] + [(p, href("/files", user, folder="/".join(parts[: i + 1]))) for i, p in enumerate(parts)]
        if process:
            crumbs.append((self._process_name(process), href("/items/" + rec["type"], user)))
        crumbs.append((rec["number"], None))
        self._page(rec["number"], body, status=status, head=head, crumbs=crumbs, app_type=rec["type"])

    OPS_ON_RECORD = {"document-control": "Send for approval", "design-release": "Submit for release", "requirements-capture": "Submit for acceptance",
                     "purchase-request": "Request the purchase", "goods-receipt": "Record the receipt", "nonconformance": "Open the nonconformance",
                     "capa": "Open the corrective action", "purchase-to-pay": "Send the bill for approval", "item-request": "Request the item",
                     "journal-reversal": "Request the reversal", "change-control": "Send for change control", "calibration": "Record the calibration",
                     "internal-audit": "Plan the audit", "training-acknowledgement": "Send for acknowledgement", "complaint": "Open the complaint",
                     "management-review": "Hold the review", "task": "Assign the task", "supplier-evaluation": "Start the evaluation"}

    def _why_not_me(self, rec: dict) -> str:
        run = self.hub.runner.run_for_record(rec["locator"])
        if not run or run["status"] != "waiting" or self.user in ((run.get("pending") or {}).get("assignees") or []) or self._viewing_cog():
            return ""
        return f'<a class="button ghost" href="{href("/why", self.user, record=rec["locator"])}">{icon("help")}Why not me?</a>'

    FINISHED_STATES = ("closed", "posted", "effective", "obsolete", "cancelled", "rejected", "superseded", "completed", "verified",
                       "released", "ordered", "acknowledged", "active", "accepted")

    def _ops_row(self, rec: dict, run: dict | None) -> str:
        """One-click Ops on this item: the processes that apply to it (started on it, not on a copy) and the cogs that
        can be asked about it, each with a key: press the key on the page to fire it."""
        user, bundle, loc = self.user, self.bundle, rec["locator"]
        live = run is not None and run["status"] in ("waiting", "submitted", "running")
        keys = iter("123456789")
        letters = iter("qwertyuiop")
        items = []
        finished = rec["state"] in self.FINISHED_STATES
        if not live and not finished:
            for name, wf in sorted((bundle.get("workflows") or {}).items()):
                if wf.get("record_type") != rec["type"] or (rec["fields"].get("doc_type") in ("frame", "frame-draft")):
                    continue
                op = bundle["ops"].get(name) or {}
                d = authz.check(bundle, user, "create", op.get("object", rec["type"]), op.get("folder") or rec["folder"])
                first = op["steps"][0]["role"] if op.get("steps") else "system"
                if not d["allowed"] or not (first in ("system", *FIELD_ROLES) or first in authz.roles_of(bundle, user)):
                    continue
                k = next(keys, "")
                label = self.OPS_ON_RECORD.get(name) or ("Start " + name.replace("-", " "))
                where = "" if op.get("folder") in ("", rec["folder"]) else f"; files it in {op.get('folder')}"
                ask_change = rec["type"] == "document" and rec["fields"].get("doc_class") != "evidence"
                change_pick = (f'<label class="inline"><span class="vh">Change type</span><select name="change_type" required title="minor: wording, no change to what people do">'
                               f'<option value="">minor or major?</option><option value="minor">minor change</option><option value="major">major change</option></select></label>'
                               if ask_change else "")
                items.append(f'<form method="post" action="/start-on" class="inline">{hidden_as(user)}{self._once()}<input type="hidden" name="process" value="{esc(name)}"><input type="hidden" name="record" value="{esc(loc)}">{change_pick}'
                             f'<button class="button" data-hotkey="{k}" title="starts {esc(name)} on this item{esc(where)}">{icon("play")}{esc(label)}<kbd class="hk">{k}</kbd></button></form>')
        # Only cogs that follow a procedure for this kind of item are offered (as only trained people are asked).
        sops = {((p.get("source") or {}).get("frame")) for p in (bundle.get("processes") or {}).values()
                if (p.get("record_type") or p.get("object")) == rec["type"]}
        cogs = [(n, m) for n, m in sorted((bundle.get("members") or {}).items()) if m.get("type") == "cog" and n in self.hub.registry.ids()
                and sops & set(((bundle.get("cogs") or {}).get(n) or {}).get("follows") or [])]
        for n, m in cogs:
            cog = self.hub.registry.get(n)
            if "review-document" not in getattr(cog, "tasks", []):
                continue
            k = next(letters, "")
            body = f"@{n} please review this {type_name(rec['type']).lower()}"
            items.append(f'<form method="post" action="/msg" class="inline">{hidden_as(user)}{self._once()}<input type="hidden" name="to" value="{esc(n)}"><input type="hidden" name="record" value="{esc(loc)}">'
                         f'<input type="hidden" name="subject" value="Review of {esc(rec["number"])}"><input type="hidden" name="body" value="{esc(body)}"><input type="hidden" name="return" value="{esc(self._self_url())}">'
                         f'<button class="button ghost" data-hotkey="{k}" title="asks {esc(n)} for a review in the chatter">{self._avatar(n, size="sm")} Ask {esc(n)} to review<kbd class="hk">{k}</kbd></button></form>')
        if not items:
            return ""
        return (f'<div class="ops" role="group" aria-label="Ops on this item"><span class="lbl">{icon("zap")}Ops</span>{"".join(items)}'
                f'<span class="lbl hk-hint">press <kbd class="hk" style="margin:0"></kbd> then the key; Enter confirms</span>'
                f'<button type="button" class="ghost hk-hint" data-hotkeys-toggle aria-pressed="true">Keyboard shortcuts: on</button>'
                f'<span id="hk-status" class="vh" role="status" aria-live="polite"></span></div>')

    def _self_url(self) -> str:
        return self.path.split("?", 1)[0] + "?as=" + quote(self.user)

    def _field_html(self, name: str, v, fields: dict) -> str:
        if name == "supplier" and isinstance(v, str):
            return esc(self._supplier_name(v)) + (f" <span class='muted'>({esc(v)})</span>" if self._supplier_name(v) != v else "")
        if isinstance(v, str) and (v.startswith("rec:") or self.hub.kernel.get(v) is not None) and name not in ("title", "description"):
            return self._record_link(v)
        if isinstance(v, list) and v and all(isinstance(x, str) and x.startswith("rec:") for x in v):
            return ", ".join(self._record_link(x, with_summary=False) for x in v[:12]) + (f" <span class='muted'>and {len(v) - 12} more</span>" if len(v) > 12 else "")
        if isinstance(v, (dict, list)) and not (isinstance(v, list) and all(isinstance(x, (str, int, float)) for x in v)):
            return details("show", f"<pre class='cell'>{esc(short(json.dumps(v, indent=1, default=str), 4000))}</pre>")
        return esc(value_text(name, v, fields))

    def _history_html(self, loc: str, run_id: str | None) -> str:
        rec = self.hub.kernel.get(loc)
        word = type_name(rec["type"]).lower() if rec else "item"
        items = ""
        for e in self.hub.kernel.events(aggregate=loc):
            data = {k: v for k, v in e["data"].items() if k not in BLOB_FIELDS}
            run = f" {self._run_link(e['run_id'], 'run ' + e['run_id'][:8])}" if e["run_id"] else ""
            items += (f"<li><span class='muted'>{esc(when(e['at']))}</span> {event_sentence(e, self._number_link, word)}{run} "
                      f"{details('details', f'<pre class=cell>{esc(short(json.dumps(data, indent=1, default=str), 4000))}</pre>')}</li>")
        out = f"<ol class='history'>{items}</ol>" if items else "<p class='muted'>Nothing yet.</p>"
        if run_id:
            out += details("Run steps and checks", self._track_html(run_id))
        return out

    # -------------------------------------------------------------- document
    def page_document(self, status: int = 200):
        user = self.user
        bundle = self._need_release()
        rec = self.hub.kernel.get(self.arg)
        if rec is None or rec["type"] != "document":
            raise ShellError(f"There is no document '{self.arg}'. Check the number, or find it under Files.", 404)
        self._require_read(rec["folder"])
        loc, f = rec["locator"], rec["fields"]
        versions = self.hub.dms.versions(loc)
        locked = f.get("checked_out_by")
        immutable = f.get("doc_class") in ("evidence", "external")
        can, why_not = self._may_edit_document(rec)
        meta = [("Title", esc(f.get("title"))), ("Class", esc(f.get("doc_class"))), ("Type", esc(f.get("doc_type"))),
                ("Revision", esc(f"version {f.get('frame_version')}" if f.get("doc_type") in ("frame", "frame-draft") and f.get("frame_version")
                                 else f"{f.get('revision', '')}.{f.get('minor', 0)}")), ("State", self._state_chip(rec["state"])),
                ("Folder", f'<a href="{href("/files", user, folder=rec["folder"])}">{esc(rec["folder"])}</a>'), ("Owner", self._avatar(f["owner"], size="sm", named=True) if f.get("owner") else ""),
                ("Author", self._avatar(f["author"], size="sm", named=True) if f.get("author") else ""), ("Checked out by", (self._avatar(locked, size="sm", named=True) if locked else "")), ("File", esc(f.get("filename"))), ("Format", esc(f.get("mime"))),
                ("Labels", " ".join(tag(f"{k}: {v}") for k, v in rec["labels"].items())),
                ("Provenance", esc(", ".join(f"{k}: {v}" for k, v in (f.get("provenance") or {}).items()) if isinstance(f.get("provenance"), dict) else fmt(f.get("provenance")))), ("Effective from", esc(day(f.get("effective_from")))), ("Review date", esc(day(f.get("review_date")))),
                ("Approved by", esc(f.get("approved_by")))]
        wf = self._wf_for_type("document")
        attached = [l for l in self.hub.kernel.links_of(loc) if l["direction"] == "in" and l["rel"] == "has-document"]
        other = [l for l in self.hub.kernel.links_of(loc) if not (l["direction"] == "in" and l["rel"] == "has-document")]
        btns = ""
        session = f.get("checked_out_via") == "editor"
        editable_now = wopi.is_viewable(rec) and versions and can and not immutable and rec["state"] in EDITABLE_DOC_STATES and wopi.is_editable(rec) and (not locked or locked == user or session)
        if wopi.is_viewable(rec) and versions:
            label = "Edit online" if editable_now and not (session and locked != user) else ("Join the editing" if editable_now else "Open in the viewer")
            btns += f'<a class="button" href="{href("/edit/" + quote(loc, safe=":"), user)}">{icon("edit" if editable_now else "eye")}{label}</a>'
        if versions and wopi.is_editable(rec) and (immutable or rec["state"] not in EDITABLE_DOC_STATES) and may_file(bundle, user, rec["folder"])["allowed"]:
            btns += f"""<form method="post" action="/working-copy" class="inline">{hidden_as(user)}{self._once()}<input type="hidden" name="doc" value="{esc(loc)}"><button class="ghost" title="A new working document with this content, attached to the same items, that you can edit online">{icon("plus")}Make a working copy</button></form>"""
        btns = self._policy_doc_buttons(rec) + btns + self._why_not_me(rec) + self._assign_form(rec) + self._restart_form(rec)
        if versions:
            btns += f'<a class="button ghost" href="{href("/download", user, doc=loc)}">{icon("download")}Download</a>'
        btns += f'<a class="button ghost" href="#checkout">{icon("lock")}Check-out and check-in</a><button type="button" class="ghost" onclick="print()">{icon("printer")}Print</button>'
        body = f'<div class="formhead"><div class="btns">{btns}</div>{self._statusbar(wf, rec)}</div>' + self._ops_row(rec, self.hub.runner.run_for_record(loc))
        body += self._smart_buttons([("Versions", len(versions), "layers", "versions"), ("Attached to", len(attached), "paperclip", "attached"), ("Links", len(other), "link", "links")])
        doc_run = self.hub.runner.run_for_record(loc)
        if doc_run:
            doc_run = self.hub.runner.load(doc_run["id"])
        body += self._run_cards(rec, doc_run)
        body += (f"<div class='sheet' id='sheet' data-live><h1 class='title'>{esc(rec['number'])} {tag('Document')} {self._state_chip(rec['state'])}</h1>"
                 f"<div class='subtitle'><b>{file_icon(f.get('filename'))} {esc(f.get('title'))}</b></div><dl class='sheetfields'>"
                 + "".join(f"<div class='fld'><dt>{esc(k)}</dt><dd>{v}</dd></div>" for k, v in meta if v) + "</dl></div>")
        if wopi.is_viewable(rec) and versions:
            editable = can and not immutable and rec["state"] in EDITABLE_DOC_STATES and wopi.is_editable(rec) and (not locked or locked == user)
            label = "Edit online" if editable else "Open in the viewer"
            body += f'<p><a class="button" href="{href("/edit/" + quote(loc, safe=":"), user)}">{label}</a> <span class="why">{"every save becomes a version; finish editing to release the document" if editable else "read-only"}</span></p>'
        body += "<h2 id='versions'>Versions</h2>" + table(["Version", "When", "By", "File", "Size", "Note", "Download", "Change"],
                                             [[esc(v["version"]), esc(when(v["at"])), esc(v["by"]), esc(v.get("filename")), esc(v.get("size")), esc(v.get("note")),
                                               f'<a href="{href("/download", user, doc=loc, v=v["version"])}">Download version {esc(v["version"])}</a>',
                                               (f'<a href="{href("/compare/" + quote(loc, safe=":"), user, a=v["version"] - 1, b=v["version"])}">what changed</a>'
                                                if v["version"] > 1 else "")] for v in versions], "No content yet.")
        if f.get("text"):
            body += f"<h2>Text</h2><pre>{esc(short(f['text'], 20000))}</pre>"
        body += self._sop_doc_html(rec)
        body += "<h2 id='checkout'>Check-out and check-in</h2><div class='card'>"
        err = self._form_error(["file", "note"]) if self.route in ("/checkout", "/checkin", "/undo-checkout") else ""
        body += err
        if not can:
            body += f"<span class='why'>You cannot change this document: {why_not}</span>"
        elif immutable:
            body += f"<span class='why'>{esc(f.get('doc_class'))} documents are never edited; file a new one that supersedes it.</span>"
        elif rec["state"] not in EDITABLE_DOC_STATES and f.get("doc_class") == "template":
            body += self._template_revision_html(rec)
        elif rec["state"] not in EDITABLE_DOC_STATES and str(f.get("lineage", "")).startswith("sop:"):
            body += (f"<span class='why'>{esc(rec['number'])} is {esc(state_name(rec['state']).lower())}; it changes only through a new revision "
                     "carried by a change order (above).</span>")
        elif rec["state"] not in EDITABLE_DOC_STATES:
            body += (f"<span class='why'>{esc(rec['number'])} is {esc(state_name(rec['state']).lower())}; a controlled document changes only through a new revision "
                     f"(propose a document change with the same title).</span>")
        else:
            if locked and locked != user and f.get("checked_out_via") == "editor":
                body += f"<span class='chip ok'>being edited by {esc(', '.join(f.get('editors') or [locked]))}</span> <span class='why'>Join the editing above; everyone's changes merge in the editor, and the last to finish releases the document.</span>"
            elif locked and locked != user:
                body += f"<span class='chip warn'>checked out by {esc(locked)}</span> <span class='why'>Only {esc(locked)} can check it in or undo the check-out.</span>"
            elif not locked:
                body += f"<p class='why'>Check the document out to work on it; nobody else can check in a version while you hold it.</p><form method='post' action='/checkout' class='inline'>{hidden_as(user)}<input type='hidden' name='doc' value='{esc(loc)}'><button>Check out</button></form>"
            else:
                body += f"""<p class='why'>You have this document checked out. Check in a new version, or undo the check-out to release it unchanged.</p>
<form method="post" action="/checkin" enctype="multipart/form-data">{hidden_as(user)}{self._once()}<input type="hidden" name="doc" value="{esc(loc)}">
<div class="fields">{file_input("file", "New version", required=True)}{text_input("note", "What changed", self._val("note") if self.route == "/checkin" else "")}</div>
<div class="actions"><button>Check in</button></div></form>
<form method="post" action="/undo-checkout" class="inline">{hidden_as(user)}<input type="hidden" name="doc" value="{esc(loc)}"><button class="secondary">Undo the check-out</button></form>"""
        body += "</div>"
        body += "<h2 id='attached'>Attached to</h2>" + table(["Item"], [[self._record_link(l["target"])] for l in attached], "Not attached to any item.")
        if other:
            body += "<h2 id='links'>Links</h2>" + table(["Relation", "Item", "Direction"], [[esc(l["label"]), self._record_link(l["target"]), esc(l["direction"])] for l in other])
        body += "<h2>History</h2>" + self._history_html(loc, None)
        body += self._chatter(rec, self.hub.runner.run_for_record(loc))
        parts = rec["folder"].split("/")
        crumbs = [("Files", href("/files", user))] + [(p, href("/files", user, folder="/".join(parts[: i + 1]))) for i, p in enumerate(parts)] + [(rec["number"], None)]
        self._page(rec["number"], body, status=status, crumbs=crumbs, app_type="document")

    def page_compare(self):
        """/compare/<document>?a=<version>&b=<version>: what changed between two versions, as text."""
        from .. import office
        from ..wopi import extension_of
        from .policy_docs import diff_html

        user = self.user
        self._need_release()
        rec = self.hub.kernel.get(self.arg)
        if rec is None or rec["type"] != "document":
            raise ShellError(f"There is no document '{self.arg}'.", 404)
        self._require_read(rec["folder"])
        versions = self.hub.dms.versions(rec["locator"])
        if not versions:
            raise ShellError(f"{rec['number']} has no versions yet.", 404)
        try:
            b = int(self.q.get("b", [versions[-1]["version"]])[0])
            a = int(self.q.get("a", [b - 1])[0])
        except ValueError:
            raise ShellError("Versions are numbers, e.g. a=2&b=3.", 400) from None
        nums = {v["version"]: v for v in versions}
        if a not in nums or b not in nums:
            raise ShellError(f"{rec['number']} has versions {min(nums)} to {max(nums)}.", 404)

        def text_of(n: int) -> str:
            content, v = self.hub.dms.content(rec["locator"], n)
            ext = extension_of(v.get("filename") or rec["fields"].get("filename") or "")
            if ext in ("md", "txt", "csv"):
                return content.decode("utf-8", "replace")
            return office.extract_text(content, ext, limit=200000)
        va, vb = nums[a], nums[b]
        body = (f"<h1>{esc(rec['number'])}: what changed from version {a} to {b}</h1>"
                f"<p class='why'>Version {a}: {esc(va.get('by'))} on {esc(when(va.get('at')))} ({esc(va.get('note') or '-')}). "
                f"Version {b}: {esc(vb.get('by'))} on {esc(when(vb.get('at')))} ({esc(vb.get('note') or '-')}). Text only; formatting is not compared.</p>"
                + diff_html(text_of(a), text_of(b), rec["number"]).replace("(in force)", f"(version {a})").replace("(draft)", f"(version {b})")
                + f"<p><a href='{href('/document/' + quote(rec['locator'], safe=':'), user)}#versions'>Back to the versions</a></p>")
        self._page(f"{rec['number']} changes", body, crumbs=[("Files", href("/files", user)), (rec["number"], href("/document/" + quote(rec["locator"], safe=":"), user)),
                                                           ("What changed", None)])

    def _may_edit_document(self, rec: dict) -> tuple[bool, str]:
        """Who may check a document out or in: its author or owner, a document controller, or anyone who may create documents there."""
        f = rec["fields"]
        if f.get("doc_type") == "frame-draft":
            # A draft of a policy is its author's: only they (and the policy owners, who review it) change its text.
            if self.user == f.get("owner") or "policy-owner" in authz.roles_of(self.bundle, self.user):
                return True, ""
            return False, f"this is {esc(f.get('owner') or 'someone')}'s draft of a policy; only they or a policy owner change it. Start your own draft from the policy's page."
        if self.user in (f.get("author"), f.get("owner")) or "document-controller" in authz.roles_of(self.bundle, self.user):
            return True, ""
        d = authz.check(self.bundle, self.user, "create", "document", rec["folder"])
        if d["allowed"]:
            return True, ""
        return False, "only its author or owner, a document controller, or someone who may create documents in " + esc(rec["folder"]) + " may. " \
            + self._why_html(d, "create", "document", rec["folder"])

    def page_download(self):
        self._need_release()
        if not self.user:
            raise ShellError("Choose who you are before downloading.", 400)
        ref = self.q.get("doc", [""])[0]
        rec = self.hub.kernel.get(ref)
        if rec is None or rec["type"] != "document":
            raise ShellError(f"There is no document '{ref}'.", 404)
        self._require_read(rec["folder"])
        v = self.q.get("v", [""])[0]
        versions = self.hub.dms.versions(rec["locator"])
        number = None
        if v:
            try:
                number = int(v)
            except ValueError:
                raise ShellError(f"'{v}' is not a version number; {rec['number']} has versions 1 to {len(versions)}.", 404) from None
            if not any(x["version"] == number for x in versions):
                raise ShellError(f"{rec['number']} has no version {number}; it has versions 1 to {len(versions)}.", 404)
        content, version = self.hub.dms.content(rec["locator"], number)
        name = version.get("filename") or rec["number"]
        ascii_name = "".join(c if 32 < ord(c) < 127 and c not in '"\\' else "_" for c in name) or rec["number"]
        self.send_response(200)
        self.send_header("Content-Type", version.get("mime") or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Disposition", f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name)}')
        self.end_headers()
        self.wfile.write(content)
