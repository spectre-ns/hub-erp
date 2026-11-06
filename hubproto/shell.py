"""A small workspace shell: folders, records as documents, an approvals inbox,
runs with their Tracks, releases, and "Why?" for any permission.

Standard library only. Identity is chosen with ``?as=<principal>`` because
the prototype has no SSO; a Nebari deployment puts Keycloak in front.
"""

from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import authz
from .app import Hub
from .runner import RunError

_CSS = """
:root{--bg:#f3f5f7;--panel:#fff;--line:#d9dee3;--fg:#1c2730;--muted:#5f6f7c;--accent:#0b5f8a;--ok:#1f7a4d;--warn:#9a6400;--crit:#a3302a}
body{margin:0;font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
.top{display:flex;align-items:center;gap:16px;padding:10px 18px;background:var(--accent);color:#fff}
.top a{color:#fff;text-decoration:none;font-weight:600}.top form{margin-left:auto}
.layout{display:grid;grid-template-columns:220px 1fr;min-height:calc(100vh - 46px)}
nav{background:var(--panel);border-right:1px solid var(--line);padding:14px}
nav a{display:block;padding:6px 10px;border-radius:6px;color:var(--fg);text-decoration:none}nav a:hover{background:var(--bg)}
nav .sub{padding-left:22px;font-size:14px;color:var(--muted)}
main{padding:18px 26px;max-width:1100px}
h1{font-size:22px;margin:0 0 12px}h2{font-size:17px;margin:22px 0 8px}
table{border-collapse:collapse;width:100%;background:var(--panel);border:1px solid var(--line);font-size:14px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{background:#e9eef2;font-size:13px}
.chip{display:inline-block;padding:0 8px;border-radius:999px;font-size:12px;font-weight:600;background:#e4e9ee}
.ok{background:#dcefe4;color:var(--ok)}.warn{background:#f7ebcf;color:var(--warn)}.crit{background:#f6dcda;color:var(--crit)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:10px 0}
.why{font-size:13px;color:var(--muted)}code{font-size:13px;background:#e9eef2;padding:1px 4px;border-radius:4px}
pre{background:#e9eef2;padding:10px;border-radius:6px;font-size:12.5px;overflow:auto}
button{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:5px 12px;cursor:pointer}
button.secondary{background:#6c7a86}input,select{padding:5px 8px;border:1px solid var(--line);border-radius:6px}
"""


def esc(x) -> str:
    return html.escape(str(x))


class Handler(BaseHTTPRequestHandler):
    hub: Hub = None  # set by serve()

    def log_message(self, *a):  # quiet
        pass

    # --- helpers ----------------------------------------------------------
    def _user(self, q) -> str:
        return (q.get("as") or ["alice"])[0]

    def _page(self, title: str, body: str, user: str, status: int = 200):
        meta = self.hub.releases.current()
        bundle = self.hub.releases.bundle(meta["digest"]) if meta else None
        members = sorted(bundle["members"]) if bundle else [user]
        options = "".join(f'<option value="{esc(m)}" {"selected" if m == user else ""}>{esc(m)}'
                          f'{" (cog)" if bundle and bundle["members"][m]["type"] == "cog" else ""}</option>' for m in members)
        folders = ""
        if bundle:
            for f in bundle["folders"]:
                depth = f.count("/")
                folders += f'<a class="{"sub" if depth else ""}" href="/files?folder={esc(f)}&as={esc(user)}">{esc(f.split("/")[-1])}</a>'
        page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{esc(title)}</title><style>{_CSS}</style></head>
<body><div class="top"><a href="/?as={esc(user)}">Hub</a><span>{esc(title)}</span>
<form method="get" action="{esc(self.path.split('?')[0])}"><label>Acting as <select name="as" onchange="this.form.submit()">{options}</select></label></form></div>
<div class="layout"><nav><a href="/?as={esc(user)}">Home</a><a href="/inbox?as={esc(user)}">Approvals and inbox</a>
<a href="/runs?as={esc(user)}">Runs</a><a href="/releases?as={esc(user)}">Policy releases</a><a href="/why?as={esc(user)}">Why?</a>
<h2 style="font-size:13px;color:var(--muted);margin:14px 0 4px">Files</h2>{folders}</nav><main>{body}</main></div></body></html>"""
        data = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # --- routes -----------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        user = self._user(q)
        route = url.path.rstrip("/") or "/"
        try:
            if route == "/":
                return self.page_home(user)
            if route == "/files":
                return self.page_files(user, (q.get("folder") or [""])[0])
            if route == "/inbox":
                return self.page_inbox(user)
            if route == "/runs":
                return self.page_runs(user)
            if route.startswith("/run/"):
                return self.page_run(user, route[5:])
            if route == "/releases":
                return self.page_releases(user)
            if route == "/why":
                return self.page_why(user, q)
            if route.startswith("/frame/"):
                return self.page_frame(user, route[7:])
        except Exception as e:  # show the error rather than a blank page
            return self._page("Error", f"<pre>{esc(type(e).__name__)}: {esc(e)}</pre>", user, 500)
        self._page("Not found", "<p>No such page.</p>", user, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        url = urlparse(self.path)
        user = (form.get("as") or ["alice"])[0]
        if url.path == "/decide":
            run_id = form["run"][0]
            try:
                self.hub.runner.decide(run_id, user, form["verdict"][0], meaning=(form.get("meaning") or [""])[0],
                                       payload_digest=(form.get("digest") or [None])[0])
                msg = f"Recorded your {form['verdict'][0]} on run {run_id}."
            except RunError as e:
                msg = f"Not recorded: {e}"
            return self._page("Decision", f'<div class="card">{esc(msg)}</div><p><a href="/run/{esc(run_id)}?as={esc(user)}">Open the run</a></p>', user)
        if url.path == "/msg":
            reply = self.hub.send(user, form["to"][0], form["subject"][0], form.get("body", [""])[0],
                                  channel=form.get("channel", ["dm"])[0])
            body = f"<div class=\"card\">Sent to {esc(reply['to'] if reply.get('kind') != 'reply' else reply['from'])}.</div>"
            if reply.get("kind") == "reply":
                body += f"<div class=\"card\"><b>{esc(reply['from'])} replied:</b> {esc(reply['body'])}</div>"
            return self._page("Message", body + f'<p><a href="/inbox?as={esc(user)}">Back to inbox</a></p>', user)
        self._page("Not found", "<p>No such action.</p>", user, 404)

    # --- pages ------------------------------------------------------------
    def page_home(self, user):
        meta = self.hub.releases.current()
        if not meta:
            return self._page("Home", "<div class='card'>No policy release is in effect. Compile and release Frames first.</div>", user)
        bundle = self.hub.releases.bundle(meta["digest"])
        m = bundle["members"].get(user, {"type": "?", "roles": []})
        runs = [r for r in self.hub.runner.list() if r["owner"] == user]
        waiting = [r for r in self.hub.runner.list() if r.get("pending") and user in r["pending"]["assignees"]]
        body = f"""<h1>Welcome, {esc(user)} <span class="chip">{esc(m['type'])}</span></h1>
<div class="card">Roles: {', '.join(f'<span class="chip">{esc(r)}</span>' for r in m['roles']) or 'none'}<br>
Policy release in effect: <code>{esc(meta['digest'][:16])}</code> effective from {esc(meta['effective_from'])}, released by {esc(meta['released_by'])}</div>
<div class="card"><b>{len(waiting)}</b> approval(s) waiting for you &middot; <b>{len(runs)}</b> run(s) you started</div>
<h2>Processes you can start</h2><table><tr><th>Process</th><th>Filed in</th><th>Can you start it?</th></tr>"""
        for name, op in bundle["ops"].items():
            d = authz.check(bundle, user, "create", op["object"], op["folder"])
            chip = '<span class="chip ok">yes</span>' if d["allowed"] else '<span class="chip crit">no</span>'
            body += f"<tr><td>{esc(name)}</td><td>{esc(op['folder'])}</td><td>{chip} <span class='why'>{esc(d['why'])}</span></td></tr>"
        body += "</table><h2>Frames behind this release</h2><ul>"
        for f in meta["frames"]:
            body += f"<li><a href='/frame/{esc(f['name'])}?as={esc(user)}'>{esc(f['name'])}</a> v{esc(f['version'])} ({esc(f['scope'])}, maintained by {esc(f['maintainer'])})</li>"
        body += "</ul>"
        self._page("Home", body, user)

    def page_files(self, user, folder):
        meta = self.hub.releases.current()
        bundle = self.hub.releases.bundle(meta["digest"])
        can_read = authz.check(bundle, user, "read", "*", folder)
        body = f"<h1>{esc(folder or 'Files')}</h1>"
        if not can_read["allowed"]:
            body += f"<div class='card crit'>You cannot read this folder.<div class='why'>{esc(can_read['why'])}</div></div>"
            return self._page(folder, body, user)
        body += f"<div class='why'>{esc(can_read['why'])}</div><table><tr><th>Record</th><th>Kind</th><th>State</th><th>Summary</th><th>Run</th><th>Release</th></tr>"
        for rec in self.hub.kernel.records_in_folder(folder):
            summary = rec.get("memo") or rec.get("title") or f"{rec.get('supplier', '')} {rec.get('amount', '')} {rec.get('currency', '')}"
            body += (f"<tr><td>{esc(rec['id'])}</td><td>{esc(rec['kind'])}</td><td><span class='chip'>{esc(rec.get('state', rec.get('last_event', '')))}</span></td>"
                     f"<td>{esc(summary)}</td><td><a href='/run/{esc(rec.get('run_id', ''))}?as={esc(user)}'>{esc(rec.get('run_id', ''))}</a></td><td><code>{esc(rec.get('release', '')[:12])}</code></td></tr>")
        body += "</table>"
        self._page(folder, body, user)

    def page_inbox(self, user):
        body = "<h1>Approvals and inbox</h1><h2>Waiting for your decision</h2>"
        waiting = [r for r in self.hub.runner.list() if r.get("pending") and user in r["pending"]["assignees"]]
        if not waiting:
            body += "<div class='card'>Nothing is waiting for you.</div>"
        for r in waiting:
            gated = r["steps"][r["pending"]["step"]]["envelope"]["payload"] or {}
            problems = []
            for sid, rec in r["steps"].items():
                problems += [f"{sid}: {p['detail']}" for p in rec["envelope"]["problems"]]
            body += f"""<div class="card"><b>{esc(r['pending']['title'])}</b> &middot; {esc(r['process'])} run <a href="/run/{esc(r['id'])}?as={esc(user)}">{esc(r['id'])}</a> started by {esc(r['owner'])}
<div class="why">Routed to you by {esc(r['pending']['route_rule']['frame'])} line {esc(r['pending']['route_rule']['line'])}: "{esc(r['pending']['route_rule']['text'])}"</div>
<pre>{esc(json.dumps({k: v for k, v in gated.items() if k not in ('body', 'citations')}, indent=1))}</pre>
{'<div class="why">Guard findings: ' + esc('; '.join(problems)) + '</div>' if problems else ''}
<form method="post" action="/decide"><input type="hidden" name="as" value="{esc(user)}"><input type="hidden" name="run" value="{esc(r['id'])}">
<input type="hidden" name="digest" value="{esc(r['pending']['payload_digest'])}"><input name="meaning" placeholder="meaning of your signature" size="40">
<button name="verdict" value="approve">Approve</button> <button class="secondary" name="verdict" value="reject">Reject</button></form></div>"""
        body += "<h2>Messages</h2>"
        for m in reversed(self.hub.inbox.for_principal(user)):
            body += f"<div class='card'><span class='chip'>{esc(m['channel'])}/{esc(m['kind'])}</span> <b>{esc(m['subject'])}</b> from {esc(m['from'])} at {esc(m['at'])}<br>{esc(m['body'])}"
            if m.get("run_id"):
                body += f" <a href='/run/{esc(m['run_id'])}?as={esc(user)}'>open run</a>"
            body += "</div>"
        meta = self.hub.releases.current()
        bundle = self.hub.releases.bundle(meta["digest"]) if meta else {"members": {}}
        options = "".join(f"<option value='{esc(p)}'>{esc(p)}{' (cog)' if m['type'] == 'cog' else ''}</option>" for p, m in bundle["members"].items() if p != user)
        body += f"""<h2>Send a message</h2><form method="post" action="/msg" class="card"><input type="hidden" name="as" value="{esc(user)}">
To <select name="to">{options}</select> via <select name="channel"><option value="dm">DM</option><option value="email">email</option></select><br>
<input name="subject" placeholder="subject" size="40" required><br><input name="body" placeholder="message" size="60"><br><button>Send</button></form>"""
        self._page("Inbox", body, user)

    def page_runs(self, user):
        body = "<h1>Runs</h1><table><tr><th>Run</th><th>Process</th><th>Owner</th><th>Status</th><th>Release</th><th>Waiting for</th></tr>"
        for r in reversed(self.hub.runner.list()):
            chip = {"completed": "ok", "waiting": "warn", "failed": "crit", "rejected": "crit"}.get(r["status"], "")
            body += (f"<tr><td><a href='/run/{esc(r['id'])}?as={esc(user)}'>{esc(r['id'])}</a></td><td>{esc(r['process'])}</td><td>{esc(r['owner'])}</td>"
                     f"<td><span class='chip {chip}'>{esc(r['status'])}</span></td><td><code>{esc(r['release'][:12])}</code></td>"
                     f"<td>{esc(', '.join(r['pending']['assignees'])) if r.get('pending') else ''}</td></tr>")
        self._page("Runs", body + "</table>", user)

    def page_run(self, user, run_id):
        s = self.hub.runner.load(run_id)
        body = f"<h1>Run {esc(s['id'])} <span class='chip'>{esc(s['status'])}</span></h1><div class='card'>{esc(s['process'])} started by {esc(s['owner'])} at {esc(s['created_at'])}; pinned to release <code>{esc(s['release'])}</code></div><h2>Steps</h2><table><tr><th>Step</th><th>Cog</th><th>Gate</th><th>Problems</th><th>Decision</th></tr>"
        for step in s["manifest"]["steps"]:
            rec = s["steps"].get(step["id"])
            if not rec:
                body += f"<tr><td>{esc(step['title'])}</td><td>{esc(step['cog']['id'])}</td><td>not reached</td><td></td><td></td></tr>"
                continue
            env = rec["envelope"]
            probs = "<br>".join(f"<span class='chip {'crit' if p['severity'] == 'error' else 'warn'}'>{esc(p['check'])}</span> {esc(p['detail'])}" for p in env["problems"])
            if env["error"]:
                probs += f"<span class='chip crit'>{esc(env['error']['code'])}</span> {esc(env['error']['detail'])}"
            dec = ""
            if rec.get("decision"):
                d = rec["decision"]
                dec = f"{esc(d['verdict'])} by {esc(d['by'])} ({esc(d['by_type'])})<div class='why'>{esc(d['meaning'])}</div>"
                if d.get("delegating_rule"):
                    dec += f"<div class='why'>under {esc(d['delegating_rule']['frame'])} line {esc(d['delegating_rule']['line'])}: \"{esc(d['delegating_rule']['text'])}\"</div>"
            body += f"<tr><td>{esc(step['title'])}</td><td>{esc(env['cog']['id'])} {esc(env['cog']['version'])}</td><td>{esc(rec['status'])}</td><td>{probs}</td><td>{dec}</td></tr>"
        body += "</table><h2>Track</h2><table><tr><th>#</th><th>At</th><th>Event</th><th>Detail</th></tr>"
        for ev in self.hub.runner.track(run_id):
            detail = {k: v for k, v in ev.items() if k not in ("seq", "at", "type", "run_id", "record_digest")}
            body += f"<tr><td>{ev['seq']}</td><td>{esc(ev['at'])}</td><td><code>{esc(ev['type'])}</code></td><td><pre style='margin:0'>{esc(json.dumps(detail, indent=1, default=str))}</pre></td></tr>"
        self._page(f"Run {run_id}", body + "</table>", user)

    def page_releases(self, user):
        cur = self.hub.releases.current()
        body = "<h1>Policy releases</h1><table><tr><th></th><th>Digest</th><th>Effective from</th><th>Released by</th><th>Parent</th><th>Frames</th></tr>"
        for m in reversed(self.hub.releases.list()):
            mark = "&#9679;" if cur and cur["digest"] == m["digest"] else ""
            frames = ", ".join(f"{f['name']}@{f['version']}" for f in m["frames"])
            body += f"<tr><td>{mark}</td><td><code>{esc(m['digest'][:20])}</code></td><td>{esc(m['effective_from'])}</td><td>{esc(m['released_by'])}</td><td><code>{esc((m['parent'] or '-')[:12])}</code></td><td>{esc(frames)}</td></tr>"
        body += "</table>"
        if cur:
            bundle = self.hub.releases.bundle(cur["digest"])
            body += "<h2>Routing in effect</h2><table><tr><th>Process</th><th>Condition</th><th>Goes to</th><th>Source</th></tr>"
            for r in bundle["routing"]:
                c = r["condition"]
                cond = "default" if not c else f"{c['field']} {c['op']} {c['value']} {c.get('currency', '')}"
                body += f"<tr><td>{esc(r['process'])}</td><td>{esc(cond)}</td><td>{esc(r['assignee'])}</td><td class='why'>{esc(r['source']['frame'])}:{esc(r['source']['line'])}</td></tr>"
            body += "</table><h2>Cog approval criteria</h2><table><tr><th>Cog</th><th>Process</th><th>Up to</th><th>Conditions</th><th>Source</th></tr>"
            for c in bundle["approval_criteria"]:
                body += f"<tr><td>{esc(c['approver'])}</td><td>{esc(c['process'])}</td><td>{esc(c.get('max_amount') or '-')} {esc(c.get('currency') or '')}</td><td>{esc(', '.join(c['conditions']))}</td><td class='why'>{esc(c['source']['frame'])}:{esc(c['source']['line'])}</td></tr>"
            body += "</table>"
        self._page("Releases", body, user)

    def page_why(self, user, q):
        action = (q.get("action") or ["create"])[0]
        obj = (q.get("object") or ["bill"])[0]
        folder = (q.get("folder") or ["Finance/Payables"])[0]
        d = self.hub.why(user, action, obj, folder)
        chip = "<span class='chip ok'>allowed</span>" if d["allowed"] else "<span class='chip crit'>denied</span>"
        body = f"""<h1>Why?</h1><form method="get" class="card"><input type="hidden" name="as" value="{esc(user)}">
May <b>{esc(user)}</b> <input name="action" value="{esc(action)}" size="8"> <input name="object" value="{esc(obj)}" size="10"> in <input name="folder" value="{esc(folder)}" size="28"> <button>Ask</button></form>
<div class="card">{chip} {esc(d['why'])}</div>"""
        if d.get("rule"):
            src = d["rule"]["source"]
            body += f"<p><a href='/frame/{esc(src['frame'])}?as={esc(user)}#L{src['line']}'>Open {esc(src['frame'])} at line {src['line']}</a></p>"
        self._page("Why", body, user)

    def page_frame(self, user, name):
        frame = self.hub.frames.get(name)
        lines = "".join(f"<tr id='L{i}'><td class='why'>{i}</td><td><pre style='margin:0;background:none;padding:0'>{esc(line)}</pre></td></tr>"
                        for i, line in enumerate(frame.lines, start=1))
        body = f"<h1>{esc(frame.name)} <span class='chip'>v{esc(frame.version)}</span></h1><div class='card'>{esc(frame.description)}<br><span class='why'>scope {esc(frame.scope)}; maintained by {esc(frame.maintainer)}; visibility {esc(frame.visibility)}; inherits {esc(', '.join(frame.inherits) or 'nothing')}</span></div><table>{lines}</table>"
        self._page(name, body, user)


def serve(hub: Hub, port: int = 8080) -> None:
    Handler.hub = hub
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"workspace shell at http://127.0.0.1:{port}/?as=alice")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
