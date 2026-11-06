"""Pages for everyone: home (the front door), the Start forms, files, boards, runs, records and documents."""

from __future__ import annotations

import json
from urllib.parse import quote

from .. import authz
from ..runner import FIELD_ROLES
from .forms import DEFAULT_START_INPUTS, REQUIRED_EXTRA, START_INPUTS, ShellError, file_input, text_input, widget
from .handler import details, hidden_as, href, kv, table
from .text import (BLOB_FIELDS, chip, day, esc, event_sentence, field_label, fmt, money, owner_of, short, state_name,
                   summary_of_record, tag, track_sentence, type_name, value_text, verb_for, when)

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
        runs = [r for r in self.hub.runner.list() if r["owner"] == user]
        waiting = self._pending(user, "decision")
        returned = self._pending(user, "resubmit")
        failed = self._failed_for(user)
        others = self._waiting_on_others(user)
        roles = " ".join(tag(r.replace("-", " ")) for r in m["roles"]) or "no role"
        body = f"""<h1>Welcome, {esc(user)}</h1><p>You are {roles}.</p>
<div class="counts"><div class="card"><b>{len(waiting)}</b><a href="{href('/inbox', user)}">waiting for my approval</a></div>
<div class="card"><b>{len(returned) + len(failed)}</b><a href="{href('/inbox', user)}#mine">of mine returned or stopped</a></div>
<div class="card"><b>{len(others)}</b><a href="{href('/inbox', user)}#others">of mine waiting on others</a></div>
<div class="card"><b>{len(runs)}</b><a href="{href('/runs', user, owner='me')}">items I started</a></div></div>
<h2>Start something</h2><p class="why">Fields marked * are required. Each entry opens its form.</p>"""
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
        for name, op, d in startable:
            body += self._start_entry(name, op, d, open_=(name == open_process))
        if unavailable:
            rows = [[esc(verb_for(op.get("record_type"), n)), esc(op["folder"]), f'<span class="why">{w}</span>'] for n, op, w in unavailable]
            body += details("Not available to you", table(["Process", "Filed in", "Why"], rows), cls="card")
        vlabel = self._version_html(meta["digest"])
        body += f"<p class='why'>Policy in effect: {vlabel}, released by {esc(meta['released_by'])}.</p>"
        self._page("Home", body, status=status, crumbs=[("Home", None)])

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

    def _required_at_start(self, process: str, rt: str) -> list[str]:
        req = list(self.hub.runner.required_at_start(process))
        for f in REQUIRED_EXTRA.get(rt, []):
            if f not in req and f in {n for n, _, _ in self._start_spec(rt)}:
                req.append(f)
        return req

    def _start_spec(self, rt: str) -> list[tuple[str, str | None, str | None]]:
        """(field, widget kind, type spec) for the Start form of a record type."""
        td = self.hub.kernel.types.get(rt, {}).get("fields", {})
        out = []
        for entry in START_INPUTS.get(rt, DEFAULT_START_INPUTS):
            name = entry[0]
            kind = entry[1] if len(entry) > 1 else None
            spec = entry[2] if len(entry) > 2 and entry[2] else td.get(name)
            out.append((name, kind, spec))
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
            out += widget(name, spec, values.get(name, ""), required=name in req, kind=kind, people=self._people(),
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
        if not folder:
            roots = [f for f in bundle["folders"] if "/" not in f]
            rows = []
            for f in roots:
                d = self._can_read(f)
                rows.append([f'<a href="{href("/files", user, folder=f)}">{esc(f)}</a>', chip("readable") if d["allowed"] else chip("no access"),
                             f'<span class="why">{self._why_html(d, "read", "*", f)}</span>'])
            return self._page("Files", "<h1>Files</h1><p class='why'>Folders you may open, and why.</p>" + table(["Folder", "Access", "Why"], rows), crumbs=[("Files", None)])
        d = self._can_read(folder)
        parts = folder.split("/")
        crumbs = [("Files", href("/files", user))] + [(p, href("/files", user, folder="/".join(parts[: i + 1]))) for i, p in enumerate(parts[:-1])] + [(parts[-1], None)]
        body = f"<h1>{esc(parts[-1])} <span class='why'>{esc(folder)}</span></h1>"
        if not d["allowed"]:
            return self._page(folder, body + f"<div class='card crit' role='alert'>You cannot read this folder.<div class='why'>{self._why_html(d, 'read', '*', folder)}</div></div>", status=403, crumbs=crumbs)
        subs = [f for f in bundle["folders"] if f.startswith(folder + "/") and "/" not in f[len(folder) + 1:]]
        if subs:
            body += "<h2>Folders</h2><ul>" + "".join(f'<li><a href="{href("/files", user, folder=s)}">{esc(s.split("/")[-1])}</a></li>' for s in subs) + "</ul>"
        text = self.q.get("text", [""])[0]
        recs = [r for r in self.hub.kernel.query(folder=folder, text=text or None) if r["folder"] == folder]
        records = list(reversed([r for r in recs if r["type"] != "document"]))
        docs = list(reversed([r for r in recs if r["type"] == "document"]))
        body += f"""<form method="get" action="/files" class="card">{hidden_as(user)}<input type="hidden" name="folder" value="{esc(folder)}">
<div class="fields">{widget("text", "text", text, label="Search this folder", hint="number or words in the title")}</div><div class="actions"><button class="secondary">Search</button></div></form>"""
        body += "<h2>Items</h2><p class='why'>Newest first.</p>" + table(["Number", "Type", "State", "Title", "Owner", "With", "Due", "Updated"],
                                                                          [[self._record_link(r["locator"], with_summary=False), tag(type_name(r["type"])), self._state_chip(r["state"]),
                                                                            esc(summary_of_record(r)), esc(owner_of(r)), esc(", ".join(r["assignees"])),
                                                                            self._due_html(r["due"] or r["fields"].get("due")), esc(when(r.get("updated_at") or r.get("created_at")))] for r in records],
                                                                          "No items in this folder.")
        body += "<h2>Documents</h2>" + table(["Number", "Title", "Class", "Rev", "State", "Owner", "Size", "Updated"],
                                              [[self._record_link(r["locator"], with_summary=False), esc(r["fields"].get("title")),
                                                esc(r["fields"].get("doc_class")), esc(f"{r['fields'].get('revision', '')}.{r['fields'].get('minor', 0)}"), self._state_chip(r["state"]),
                                                esc(owner_of(r)), esc(r["fields"].get("size", "")), esc(when(r.get("updated_at") or r.get("created_at")))] for r in docs], "No documents in this folder.")
        f = may_file(bundle, user, folder)
        if f["allowed"]:
            err = self._form_error(["file", "title"]) if self.err and self.route == "/upload" else ""
            body += f"""<h2>Upload a file</h2><div class="card"><p class="why">Files a received file as an external document (kept as received, with provenance).</p>{err}
<form method="post" action="/upload" enctype="multipart/form-data">{hidden_as(user)}{self._once()}<input type="hidden" name="folder" value="{esc(folder)}">
<div class="fields">{file_input("file", "File", required=True, hint=f"up to {self.max_upload // (1024 * 1024)} MB")}{text_input("title", "Title", self._val("title"), hint="defaults to the file name", error=self._field_error("title"))}</div>
<div class="actions"><button>Upload</button></div></form></div>"""
        else:
            body += f"<p class='why'>You cannot file documents here: {self._why_html(f, 'create', 'document', folder)}</p>"
        self._page(parts[-1], body, status=status, crumbs=crumbs)

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
            wf_for_type = None
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
        body = f"""<h1>{esc(heading)}</h1><p class="why">{esc(intro)}</p>
<form method="get" action="/boards" class="card">{hidden_as(user)}<div class="fields">
<label class="f"><span>Type</span><select name="type"><option value="">my work</option>{types}</select></label>
{f'<label class="f"><span>State</span><select name="state"><option value="">any</option>{states}</select></label>' if states else ''}
<label class="f"><span>Folder</span><select name="folder"><option value="">any</option>{folders}</select></label>
<label class="f"><span>Assigned to</span><select name="assignee"><option value="">anyone</option>{people}</select></label>
{widget("text", "text", text, label="Search", hint="number or words")}</div>
<div class="actions"><button>Filter</button> <a href="{saved}">Link to this filter</a></div></form><div class="board">"""
        total = 0
        for col, items in grouped.items():
            total += len(items)
            body += f"<div class='col{' empty' if not items else ''}'><h3>{esc(col)} <span class='muted'>{len(items)}</span></h3>"
            for r in reversed(items):
                due = r["due"] or r["fields"].get("due")
                body += f"""<div class="kcard">{self._record_link(r['locator'])}<div class="why">{tag(type_name(r['type']))} {esc(r['folder'])} &middot; {esc(owner_of(r))}{' &middot; with ' + esc(', '.join(r['assignees'])) if r['assignees'] else ''} {self._due_html(due) if due else ''}</div></div>"""
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
            body += f"<p class='why'>Approval granted by {esc(g['decided_by'])} ({'automatic reviewer' if g['by_type'] == 'cog' else 'person'}) on {esc(when(g['issued_at']))}.</p>"
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
                dec = f"{chip(d['verdict'])} by {esc(d['by'])} ({'automatic reviewer' if d['by_type'] == 'cog' else 'person'})<div class='why'>{esc(d['meaning'])}</div>"
                if d.get("delegating_rule"):
                    dec += f"<div class='why'>under {self._rule_html(d['delegating_rule'])}</div>"
            result = {"passed": "passed", "failed": "stopped", "pass-with-problems": "passed with warnings", "gate-pending": "waiting for approval"}.get(srec["status"], srec["status"])
            rows.append([esc(step["title"]), f"{esc(env['cog']['id'])} {esc(env['cog']['version'])}", chip(result), probs, dec])
        body += "<h2>Steps</h2>" + table(["Step", "Automatic reviewer", "Result", "Problems", "Decision"], rows)
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
        if run and run["owner"] == user:
            can.append("retry or cancel its run when it stops")
        body = f"""<h1>{esc(rec['number'])} {tag(tname)} {self._state_chip(rec['state'], wf)}{hold}</h1>
<div class="card"><b>{esc(short(headline, 300))}</b><div class="why">{esc(tname)} in <a href="{href('/files', user, folder=rec['folder'])}">{esc(rec['folder'])}</a> &middot; owner {esc(owner_of(rec))}
&middot; with {esc(', '.join(rec['assignees']) or '-')}{' &middot; due ' + esc(day(typed_due)) if typed_due else ''}{' &middot; approval due ' + self._due_html(approval_due) if approval_due else ''}
&middot; {esc(self._process_name(process)) if process else 'no process'} &middot; {self._run_link(run, 'run ' + run['id'][:8]) + ' ' + self._state_chip(run['status']) if run else 'no run'} &middot; {labels}</div>
{('<div class=why>You may ' + esc(' and '.join(can)) + '.</div>') if can else ''}</div>"""
        if rec["hold"]:
            body += f"<div class='card crit' role='status'><b>On hold:</b> {esc(short(rec.get('hold_reason') or 'no reason given', 400))}. Nothing can be decided until an approver releases the hold.</div>"
        # Run controls: stopped runs go back to their owner; returned items are reworked and resubmitted; a waiting assignee decides here.
        if run and run["status"] == "failed" and rec["state"] == "failed":
            body += self._failed_card(run, rec)
        if run and pending and pending.get("kind") == "resubmit":
            if user in pending["assignees"]:
                body += f"<div class='card warn' id='rework'><h2 style='margin-top:0;border:0'>Returned to you for rework</h2><p><b>Reason:</b> {esc(pending.get('reason') or '-')}</p>{self._resubmit_form(run, rec)}{self._cancel_form(run, rec)}</div>"
            else:
                body += f"<div class='card warn'><b>Returned for rework</b>: {esc(pending.get('reason') or '')} <span class='why'>Waiting for {esc(', '.join(pending['assignees']))} to resubmit.</span></div>"
        if run and pending and pending.get("kind", "decision") == "decision" and run["status"] == "waiting":
            if user in pending["assignees"]:
                body += self._decision_card(run, standalone=True)
            else:
                body += f"<div class='card warn'>Waiting for {esc(', '.join(pending['assignees']))} at <b>{esc(pending['title'])}</b>{' (approval due ' + esc(day(pending['due'])) + ')' if pending.get('due') else ''}.</div>"
        tabs = "".join(f'<a href="#{t.lower()}">{t}</a>' for t in ("Details", "Workflow", "Documents", "Links", "History", "Decisions"))
        body += f'<div class="tabs">{tabs}</div>'
        rows = [[esc(field_label(k)), self._field_html(k, v, rec["fields"])] for k, v in sorted(rec["fields"].items()) if k not in BLOB_FIELDS and v not in (None, "", [], {})]
        body += '<h2 id="details">Details</h2>' + table(["Field", "Value"], rows, "No fields.", row_header=True)
        err_hold = self._field_error("reason") if self.route == "/hold" else None
        err_form = self._form_error(["reason"]) if self.route in ("/hold", "/snapshot") else ""
        body += f"""<div class="card">{err_form}<form method="post" action="/snapshot" class="inline">{hidden_as(user)}{self._once()}<input type="hidden" name="record" value="{esc(loc)}"><button class="secondary">Save a snapshot</button> <span class="why">files a copy of this item as an evidence document</span></form>
<form method="post" action="/hold">{hidden_as(user)}<input type="hidden" name="record" value="{esc(loc)}"><input type="hidden" name="on" value="{'0' if rec['hold'] else '1'}">
<div class="fields">{text_input("reason", "Reason for the hold" if not rec["hold"] else "Reason for releasing the hold", self._val("reason") if self.route == "/hold" else "", required=True, error=err_hold)}</div>
<div class="actions"><button class="secondary"{'' if may_approve['allowed'] else ' disabled'}>{'Release the hold' if rec['hold'] else 'Put on hold'}</button>
{'' if may_approve['allowed'] else '<span class="why">Only approvers of this item may hold it: ' + self._why_html(may_approve, 'approve', obj, rec['folder']) + '</span>'}</div></form></div>"""
        body += '<h2 id="workflow">Workflow</h2>'
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
        docs = self.hub.dms.documents_of(loc)
        body += '<h2 id="documents">Documents</h2>' + table(["Number", "Title", "Class", "Type", "Rev", "State", "Size"],
                                                               [[self._record_link(d["locator"], with_summary=False), esc(d["fields"].get("title")),
                                                                 esc(d["fields"].get("doc_class")), esc(d["fields"].get("doc_type")), esc(f"{d['fields'].get('revision', '')}.{d['fields'].get('minor', 0)}"), self._state_chip(d["state"]),
                                                                 esc(d["fields"].get("size", ""))] for d in docs], "No documents attached.")
        f = may_file(bundle, user, rec["folder"])
        if f["allowed"]:
            in_folder = [d for d in self.hub.kernel.query(type_="document", folder=rec["folder"]) if d["folder"] == rec["folder"]
                         and d["locator"] not in {x["locator"] for x in docs}]
            opts = [(d["locator"], f"{d['number']} {d['fields'].get('title')}") for d in in_folder]
            err = self._form_error(["file", "title", "doc"]) if self.route == "/attach" else ""
            body += f"""<div class="card">{err}<form method="post" action="/attach" enctype="multipart/form-data">{hidden_as(user)}{self._once()}<input type="hidden" name="record" value="{esc(loc)}">
<div class="fields">{file_input("file", "Upload and attach a file")}{text_input("title", "Title for the file", hint="defaults to the file name")}
{widget("doc", "ref", "", label="Or attach an existing document", options=opts, blank="none", hint="") if opts else ''}</div>
<div class="actions"><button>Attach</button></div></form></div>"""
        else:
            body += f"<p class='why'>You cannot attach documents here: {self._why_html(f, 'create', 'document', rec['folder'])}</p>"
        links = self.hub.kernel.links_of(loc)
        body += '<h2 id="links">Links</h2>' + table(["Relation", "Item", "Direction"], [[esc(l["label"].replace("-", " ")), self._record_link(l["target"]), esc(l["direction"])] for l in links], "No links.")
        if f["allowed"]:
            rels = [(r, r.replace("-", " ")) for r in self.hub.kernel.link_vocab]
            err = self._form_error(["rel", "target"]) if self.route == "/link" else ""
            body += f"""<div class="card">{err}<form method="post" action="/link">{hidden_as(user)}{self._once()}<input type="hidden" name="record" value="{esc(loc)}">
<div class="fields">{widget("rel", "text", self._val("rel") if self.route == "/link" else "", label="Link as", options=rels, required=True, hint="", error=self._field_error("rel") if self.route == "/link" else None)}
{text_input("target", "To item", self._val("target") if self.route == "/link" else "", required=True, hint="number, e.g. PO-1002", error=self._field_error("target") if self.route == "/link" else None)}</div>
<div class="actions"><button>Add link</button></div></form></div>"""
        body += '<h2 id="history">History</h2>' + self._history_html(loc, run["id"] if run else None)
        decisions = list(rec["decisions"])
        body += '<h2 id="decisions">Decisions</h2>' + table(["When", "By", "Verdict", "Meaning", "Step", "Under"],
                                                             [[esc(when(d.get("at", ""))), f"{esc(d.get('by'))} ({'automatic reviewer' if d.get('by_type') == 'cog' else 'person'})", chip(d.get("verdict")),
                                                               esc(short(d.get("meaning"), 600)) + details("signature", f"<code>{esc(d.get('signature'))}</code> on payload <code>{esc(str(d.get('payload_digest', ''))[:12])}</code>"),
                                                               esc((d.get("step") or "").replace("-", " ")),
                                                               self._rule_html(d["delegating_rule"]) if d.get("delegating_rule") else ""]
                                                              for d in decisions], "No signatures yet.")
        head = (f'<script src="{MERMAID_URL}"></script><script>if(window.mermaid){{mermaid.initialize({{startOnLoad:true,theme:matchMedia("(prefers-color-scheme: dark)").matches?"dark":"default"}})}}'
                f'else{{addEventListener("load",function(){{var d=document.getElementById("wf-diagram");if(d&&!window.mermaid){{d.hidden=true}}}})}}</script>')
        parts = rec["folder"].split("/")
        crumbs = [("Files", href("/files", user))] + [(p, href("/files", user, folder="/".join(parts[: i + 1]))) for i, p in enumerate(parts)]
        if process:
            crumbs.append((self._process_name(process), href("/boards", user, type=rec["type"])))
        crumbs.append((rec["number"], None))
        self._page(rec["number"], body, status=status, head=head, crumbs=crumbs)

    def _field_html(self, name: str, v, fields: dict) -> str:
        if name == "supplier" and isinstance(v, str):
            return esc(self._supplier_name(v)) + (f" <span class='muted'>({esc(v)})</span>" if self._supplier_name(v) != v else "")
        if isinstance(v, str) and (v.startswith("rec:") or self.hub.kernel.get(v) is not None) and name not in ("title", "description"):
            return self._record_link(v)
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
                ("Revision", esc(f"{f.get('revision', '')}.{f.get('minor', 0)}")), ("State", self._state_chip(rec["state"])),
                ("Folder", f'<a href="{href("/files", user, folder=rec["folder"])}">{esc(rec["folder"])}</a>'), ("Owner", esc(f.get("owner"))),
                ("Author", esc(f.get("author"))), ("Checked out by", esc(locked or "")), ("File", esc(f.get("filename"))), ("Format", esc(f.get("mime"))),
                ("Labels", " ".join(tag(f"{k}: {v}") for k, v in rec["labels"].items())),
                ("Provenance", esc(fmt(f.get("provenance")))), ("Effective from", esc(day(f.get("effective_from")))), ("Review date", esc(day(f.get("review_date")))),
                ("Approved by", esc(f.get("approved_by")))]
        body = f"<h1>{esc(rec['number'])} {tag('Document')} {self._state_chip(rec['state'])}</h1><div class='card'><b>{esc(f.get('title'))}</b>{kv([(k, v) for k, v in meta if v])}</div>"
        body += "<h2>Versions</h2>" + table(["Version", "When", "By", "File", "Size", "Note", "Download"],
                                             [[esc(v["version"]), esc(when(v["at"])), esc(v["by"]), esc(v.get("filename")), esc(v.get("size")), esc(v.get("note")),
                                               f'<a href="{href("/download", user, doc=loc, v=v["version"])}">Download version {esc(v["version"])}</a>'] for v in versions], "No content yet.")
        if f.get("text"):
            body += f"<h2>Text</h2><pre>{esc(short(f['text'], 20000))}</pre>"
        body += "<h2>Check-out and check-in</h2><div class='card'>"
        err = self._form_error(["file", "note"]) if self.route in ("/checkout", "/checkin", "/undo-checkout") else ""
        body += err
        if not can:
            body += f"<span class='why'>You cannot change this document: {why_not}</span>"
        elif immutable:
            body += f"<span class='why'>{esc(f.get('doc_class'))} documents are never edited; file a new one that supersedes it.</span>"
        elif rec["state"] not in EDITABLE_DOC_STATES:
            body += (f"<span class='why'>{esc(rec['number'])} is {esc(rec['state'])}; a controlled document changes only through a new revision "
                     f"(propose a document change with the same title).</span>")
        else:
            if locked and locked != user:
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
        attached = [l for l in self.hub.kernel.links_of(loc) if l["direction"] == "in" and l["rel"] == "has-document"]
        body += "<h2>Attached to</h2>" + table(["Item"], [[self._record_link(l["target"])] for l in attached], "Not attached to any item.")
        other = [l for l in self.hub.kernel.links_of(loc) if not (l["direction"] == "in" and l["rel"] == "has-document")]
        if other:
            body += "<h2>Links</h2>" + table(["Relation", "Item", "Direction"], [[esc(l["label"]), self._record_link(l["target"]), esc(l["direction"])] for l in other])
        body += "<h2>History</h2>" + self._history_html(loc, None)
        parts = rec["folder"].split("/")
        crumbs = [("Files", href("/files", user))] + [(p, href("/files", user, folder="/".join(parts[: i + 1]))) for i, p in enumerate(parts)] + [(rec["number"], None)]
        self._page(rec["number"], body, status=status, crumbs=crumbs)

    def _may_edit_document(self, rec: dict) -> tuple[bool, str]:
        """Who may check a document out or in: its author or owner, a document controller, or anyone who may create documents there."""
        f = rec["fields"]
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
