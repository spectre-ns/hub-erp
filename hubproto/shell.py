"""The workspace shell: a Nextcloud/SharePoint-style web UI over the record
system, the DMS and the workflow engine.

Standard library only (``http.server``), no JavaScript frameworks; the one
optional script is mermaid for the workflow diagram, and every page degrades
to plain HTML without it. Identity is chosen with ``?as=<principal>``
because the prototype has no SSO; a Nebari deployment puts Keycloak in
front. Every page is permission-trimmed with the release in effect and shows
the policy sentence ("why") behind a denial.

Request handling: ``/healthz`` is answered without the hub lock; a POST body
is validated (``Content-Length`` present, non-negative, at most
``HUBPROTO_MAX_UPLOAD`` bytes) and read before the lock is taken, so one
stalled or malformed client never freezes the shell for everyone. One log
line per request and one per decision go to stdout.
"""

from __future__ import annotations

import base64
import email.policy
import html
import json
import os
import socket
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from datetime import date
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse

from . import authz
from .app import Hub
from .dms import CLASSES
from .kernel import KernelGuardError
from .runner import FIELD_ROLES, RunError

MERMAID_URL = "https://cdnjs.cloudflare.com/ajax/libs/mermaid/11.4.1/mermaid.min.js"
DEFAULT_USER = "alice"
DEFAULT_MAX_UPLOAD = 25 * 1024 * 1024
BLOB_FIELDS = ("text", "body", "citations", "text_excerpt")
RAIL = [("Home", "/"), ("Inbox", "/inbox"), ("Boards", "/boards"), ("Files", "/files"), ("Runs", "/runs"),
        ("Releases", "/releases"), ("Frames", "/frames"), ("Why", "/why"), ("Conformance", "/conformance"),
        ("Admin", "/admin")]
RESERVED_KEYS = {"as", "process", "json", "run", "verdict", "meaning", "digest", "note", "reason", "record", "doc", "folder",
                 "title_", "files"}
EDITABLE_DOC_STATES = ("draft", "open", "returned", "failed")
NOTIFICATION_KINDS = ("escalation", "failure", "returned", "rejection")
TEXTAREA_FIELDS = {"description", "statement", "body", "rationale", "root_cause", "disposition_rationale", "effectiveness_result",
                   "summary", "impact", "plan", "verification", "response", "acceptance_criteria", "containment", "reason", "notes",
                   "effectiveness_criteria", "scope"}
DOC_TYPES = {".kicad_sch": "schematic", ".kicad_pcb": "board", ".fcstd": "model", ".pdf": "drawing"}
MAX_SHOWN = 300  # characters of a value shown on a card or a table cell

# Start-form inputs per record type: (field, widget kind, type spec override). The widget kind is None (from the
# field's type spec), "textarea", "lines" (one list element per line) or "lines:locator,qty" (one {item, quantity}
# per line). Anything else goes in the JSON textarea. Fields the first Gate's state requires are marked required, the rest
# submission; the first waiting state's needs are checked by the runner.
START_INPUTS: dict[str, list[tuple]] = {
    "bill": [("supplier",), ("amount",), ("currency",), ("date",), ("po_ref",), ("description", "textarea")],
    "design": [("title",), ("description", "textarea"), ("kind",), ("change_class",), ("user_needs", "lines")],
    "purchase-request": [("title",), ("item",), ("mpn",), ("quantity",), ("unit_cost",), ("supplier",), ("needed_by",), ("reason", "textarea")],
    "receipt": [("po",), ("lines", "lines:locator,qty"), ("location",), ("notes", "textarea")],
    "ncr": [("title",), ("description", "textarea"), ("severity", None, "enum:low,medium,high"), ("source",), ("lot",), ("quantity",),
            ("containment", "textarea")],
    "calibration": [("asset",), ("result",), ("standard",), ("procedure",)],
    "document": [("title",), ("doc_type",), ("change_type",), ("training_roles",), ("body", "textarea")],
    "task": [("title",), ("description", "textarea"), ("assignee",), ("due",), ("priority",)],
    "requirement": [("statement", "textarea"), ("kind",), ("source",), ("priority",)],
    "journal-reversal": [("journal",), ("reason", "textarea")],
    "item": [("name",), ("mpn",), ("manufacturer",), ("supplier",), ("unit_cost",), ("currency",), ("stock",), ("lead_time_days",)],
    "capa": [("title",), ("ncrs",), ("root_cause_method",)],
    "complaint": [("title",), ("customer",), ("description", "textarea"), ("product",), ("severity",)],
    "change-request": [("title",), ("description", "textarea"), ("impact", "textarea"), ("affected_documents",)],
    "audit": [("title",), ("scope_clauses",), ("processes",), ("planned",)],
    "management-review": [("title",), ("date",), ("inputs",)],
    "supplier-evaluation": [("supplier",), ("scope", "textarea")],
}
DEFAULT_START_INPUTS: list[tuple] = [("title",), ("description", "textarea")]

_CSS = """
:root{--bg:#f3f5f7;--panel:#fff;--line:#d9dee3;--fg:#1c2730;--muted:#5f6f7c;--accent:#0b5f8a;--accent-fg:#fff;
--ok:#1f7a4d;--ok-bg:#dcefe4;--warn:#9a6400;--warn-bg:#f7ebcf;--crit:#a3302a;--crit-bg:#f6dcda;--chip:#e4e9ee;--th:#e9eef2}
@media (prefers-color-scheme: dark){:root{--bg:#141a20;--panel:#1d252d;--line:#34404a;--fg:#e6ebef;--muted:#9fb0bd;--accent:#2f8fc4;
--ok:#7fd3a3;--ok-bg:#1d3b2c;--warn:#f0c674;--warn-bg:#4a3a12;--crit:#f09a93;--crit-bg:#4d1f1b;--chip:#2b353f;--th:#242e37}}
*{box-sizing:border-box}body{margin:0;font:14.5px/1.45 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
a{color:var(--accent)}.top{display:flex;align-items:center;gap:16px;padding:8px 18px;background:var(--accent);color:var(--accent-fg)}
.top a{color:var(--accent-fg);text-decoration:none;font-weight:600}.top .crumb{opacity:.9}.top form{margin-left:auto}
.layout{display:grid;grid-template-columns:230px 1fr;min-height:calc(100vh - 44px)}
nav{background:var(--panel);border-right:1px solid var(--line);padding:12px}
nav a{display:block;padding:5px 10px;border-radius:6px;color:var(--fg);text-decoration:none}nav a:hover,nav a.cur{background:var(--bg)}
nav h3{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:16px 0 4px 10px}
nav ul{list-style:none;margin:0;padding-left:14px}nav>ul{padding-left:0}
main{padding:18px 26px;max-width:1240px;min-width:0}
h1{font-size:21px;margin:0 0 12px}h2{font-size:16px;margin:22px 0 8px;border-bottom:1px solid var(--line);padding-bottom:3px}h3{font-size:14px;margin:14px 0 6px}
table{border-collapse:collapse;width:100%;background:var(--panel);border:1px solid var(--line);font-size:13.5px}
th,td{text-align:left;padding:6px 9px;border-bottom:1px solid var(--line);vertical-align:top}th{background:var(--th);font-size:12.5px}
.chip{display:inline-block;padding:0 8px;border-radius:999px;font-size:12px;font-weight:600;background:var(--chip);white-space:nowrap}
.ok{background:var(--ok-bg);color:var(--ok)}.warn{background:var(--warn-bg);color:var(--warn)}.crit{background:var(--crit-bg);color:var(--crit)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:10px 0}
.card.crit{border-color:var(--crit)}.card.warn{border-color:var(--warn)}.why{font-size:12.5px;color:var(--muted)}code{font-size:12.5px;background:var(--th);padding:1px 4px;border-radius:4px}
pre{background:var(--th);padding:10px;border-radius:6px;font-size:12.5px;overflow:auto;white-space:pre-wrap}
button{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:5px 12px;cursor:pointer}
button.secondary{background:#6c7a86}button.danger{background:var(--crit)}input,select,textarea{padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--fg);font:inherit}
form.inline{display:inline}label.f{display:inline-block;margin:3px 10px 3px 0;font-size:13px;vertical-align:top}label.f span{display:block;color:var(--muted);font-size:12px}
label.f span b{color:var(--crit)}
.kv{display:grid;grid-template-columns:max-content 1fr;gap:3px 14px;font-size:13.5px}.kv dt{color:var(--muted)}.kv dd{margin:0;word-break:break-word}
.board{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(210px,1fr);gap:12px;overflow-x:auto;padding-bottom:8px}
.col{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px;min-height:120px}.col h3{margin:2px 0 8px}
.kcard{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 8px;margin:6px 0;font-size:13px}
.tabs{display:flex;gap:4px;flex-wrap:wrap;margin:8px 0 14px}.tabs a{padding:3px 10px;border:1px solid var(--line);border-radius:999px;text-decoration:none;color:var(--fg);font-size:13px}
.counts{display:flex;gap:12px;flex-wrap:wrap}.counts .card{min-width:160px;margin:0}.counts b{font-size:22px;display:block}
.muted{color:var(--muted)}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:900px){.layout{grid-template-columns:1fr}nav{border-right:0;border-bottom:1px solid var(--line)}.grid2{grid-template-columns:1fr}}
.brief table{margin:6px 0}.brief h4{margin:10px 0 2px;font-size:13px}details summary{cursor:pointer;color:var(--muted);font-size:12.5px}
"""


# ---------------------------------------------------------------- helpers
def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def href(path: str, user: str, **params) -> str:
    q = {"as": user}
    q.update({k: v for k, v in params.items() if v not in (None, "")})
    return f"{path}?{urlencode(q)}"


def hidden_as(user: str) -> str:
    return f'<input type="hidden" name="as" value="{esc(user)}">'


def chip(state: str) -> str:
    s = str(state or "")
    cls = ""
    if s.startswith("awaiting") or s in ("waiting", "draft", "open", "in-review", "returned"):
        cls = "warn"
    if s in ("posted", "effective", "closed", "approved", "acknowledged", "completed", "passed", "received", "filed", "released",
             "active", "ordered", "done", "accepted", "allowed", "readable"):
        cls = "ok"
    if s in ("failed", "rejected", "obsolete", "fail", "cancelled", "overdue", "denied", "no access"):
        cls = "crit"
    return f'<span class="chip {cls}">{esc(s)}</span>'


def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, default=str)
    return str(v)


def short(v, n: int = MAX_SHOWN) -> str:
    s = fmt(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def summary_of(rec: dict) -> str:
    f = rec.get("fields", rec)
    for key in ("title", "statement", "memo", "description", "name"):
        if f.get(key):
            return short(f[key], 120)
    if f.get("supplier"):
        return f"{f.get('supplier')} {f.get('amount', '')} {f.get('currency', '')}".strip()
    return rec.get("number", "")


def owner_of(rec: dict) -> str:
    f = rec.get("fields", {})
    return str(f.get("owner") or f.get("author") or f.get("filed_by") or rec.get("created_by") or "")


def updated_of(rec: dict) -> str:
    return str(rec.get("updated_at") or rec.get("created_at") or "")[:19].replace("T", " ")


def table(headers: list[str], rows: list[list[str]], empty: str = "Nothing here.") -> str:
    if not rows:
        return f'<p class="muted">{esc(empty)}</p>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def kv(items: list[tuple[str, str]]) -> str:
    if not items:
        return ""
    return '<dl class="kv">' + "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in items) + "</dl>"


def folder_tree(folders: list[str]) -> dict:
    tree: dict = {}
    for f in folders:
        node = tree
        for part in f.split("/"):
            node = node.setdefault(part, {})
    return tree


def render_tree(node: dict, prefix: str, user: str, current: str) -> str:
    out = "<ul>"
    for name, child in node.items():
        path = f"{prefix}/{name}" if prefix else name
        cls = ' class="cur"' if path == current else ""
        out += f'<li><a{cls} href="{href("/files", user, folder=path)}">{esc(name)}</a>'
        out += (render_tree(child, path, user, current) if child else "") + "</li>"
    return out + "</ul>"


class ShellError(Exception):
    """A user-facing error with an HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def coerce(name: str, spec: str | None, value: str, kind: str | None = None):
    """A form string to the record-type field type. Lists take JSON when the text starts with ``[``, else a comma
    list; objects take JSON; numbers are cast; enums are checked. A value that does not fit is a readable error."""
    value = value.strip()
    if value == "":
        return None
    if kind == "lines":
        return [line.strip() for line in value.splitlines() if line.strip()]
    if kind == "lines:locator,qty":
        out = []
        for line in value.splitlines():
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) != 2 or not parts[0]:
                raise ShellError(f"{name}: each line is 'locator,quantity', not {line.strip()!r}.")
            try:
                qty = float(parts[1])
            except ValueError:
                raise ShellError(f"{name}: the quantity in {line.strip()!r} is not a number.") from None
            out.append({"item": parts[0], "quantity": qty})
        return out
    spec = spec or "text"
    if spec.startswith("enum:"):
        opts = spec[5:].split(",")
        if value not in opts:
            raise ShellError(f"{name} must be one of {', '.join(opts)}, not {value!r}.")
        return value
    try:
        if spec == "int":
            return int(float(value))
        if spec in ("number", "money"):
            return float(value)
    except ValueError:
        raise ShellError(f"{name} must be a number, not {value!r}.") from None
    if spec == "bool":
        return value.lower() in ("1", "true", "yes", "on")
    if spec == "list":
        if value.startswith("["):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as e:
                raise ShellError(f"{name} looks like JSON but does not parse: {e}.") from None
            if not isinstance(parsed, list):
                raise ShellError(f"{name} must be a JSON list.")
            return parsed
        return [x.strip() for x in value.split(",") if x.strip()]
    if spec == "object":
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as e:
            raise ShellError(f"{name} must be a JSON object: {e}.") from None
        if not isinstance(parsed, dict):
            raise ShellError(f"{name} must be a JSON object.")
        return parsed
    return value


def prefill(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if all(isinstance(x, str) for x in v) else json.dumps(v, default=str)
    if isinstance(v, dict):
        return json.dumps(v, sort_keys=True, default=str)
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def widget(name: str, spec: str | None, value="", required: bool = False, kind: str | None = None, label: str | None = None,
           people: list[str] | None = None) -> str:
    """One labelled form control for a record field. ``required`` only when the server requires it."""
    req = " required" if required else ""
    spec = spec or "text"
    lab = f"<span>{esc(label or name)}{' <b>*</b>' if required else ''}</span>"
    val = prefill(value)
    if kind == "lines":
        return f'<label class="f">{lab}<textarea name="{esc(name)}" rows="3" cols="50" placeholder="one per line"{req}>{esc(val if isinstance(value, str) else chr(10).join(map(str, value or [])))}</textarea></label>'
    if kind == "lines:locator,qty":
        shown = "\n".join(f"{l.get('item', '')},{l.get('quantity', '')}" for l in (value or []) if isinstance(l, dict)) if isinstance(value, list) else val
        return f'<label class="f">{lab}<textarea name="{esc(name)}" rows="3" cols="50" placeholder="locator,quantity per line, e.g. rec:item:ITEM-00001,4"{req}>{esc(shown)}</textarea></label>'
    if spec.startswith("enum:"):
        opts = "".join(f'<option value="{esc(o)}"{" selected" if o == val else ""}>{esc(o)}</option>' for o in spec[5:].split(","))
        return f'<label class="f">{lab}<select name="{esc(name)}"{req}><option value=""></option>{opts}</select></label>'
    if spec == "principal" and people:
        opts = "".join(f'<option value="{esc(p)}"{" selected" if p == val else ""}>{esc(p)}</option>' for p in people)
        return f'<label class="f">{lab}<select name="{esc(name)}"{req}><option value=""></option>{opts}</select></label>'
    if spec == "date":
        return f'<label class="f">{lab}<input type="date" name="{esc(name)}" value="{esc(val)}"{req}></label>'
    if spec in ("int", "number", "money"):
        return f'<label class="f">{lab}<input type="number" step="any" name="{esc(name)}" value="{esc(val)}"{req}></label>'
    if spec == "bool":
        return (f'<label class="f">{lab}<select name="{esc(name)}"><option value="">-</option><option value="true"{" selected" if val == "true" else ""}>true</option>'
                f'<option value="false"{" selected" if val == "false" else ""}>false</option></select></label>')
    if spec == "object":
        return f'<label class="f">{lab} <span class="why">JSON object</span><textarea name="{esc(name)}" rows="2" cols="50"{req}>{esc(val)}</textarea></label>'
    if kind == "textarea" or (spec == "text" and name in TEXTAREA_FIELDS):
        return f'<label class="f">{lab}<textarea name="{esc(name)}" rows="2" cols="50"{req}>{esc(val)}</textarea></label>'
    hint = " (comma list or JSON)" if spec == "list" else (" (record number or locator)" if spec == "ref" else "")
    return f'<label class="f"><span>{esc(label or name)}{" <b>*</b>" if required else ""}{esc(hint)}</span><input name="{esc(name)}" value="{esc(val)}" size="24"{req}></label>'


def doc_type_for(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return DOC_TYPES.get(ext, "document")


def mermaid_for(wf: dict, current: str | None) -> str:
    states = list(wf["states"])
    if current and current not in states:
        states.append(current)
    ids = {s: f"s{i}" for i, s in enumerate(states)}
    lines = ["stateDiagram-v2"]
    for s, i in ids.items():
        lines.append(f'    state "{s}" as {i}')
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
        lines.append("    classDef current fill:#f7ebcf,stroke:#9a6400,stroke-width:2px,color:#1c2730")
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


def render_conformance(result) -> str:
    rows = result
    if isinstance(result, dict):
        for key in ("items", "checks", "rows", "results", "clauses"):
            if isinstance(result.get(key), list):
                rows = result[key]
                break
        else:
            rows = [{"item": k, "value": v} for k, v in result.items()]
    if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
        cols: list[str] = []
        for r in rows:
            for k in r:
                if k not in cols:
                    cols.append(k)
        return table(cols, [[chip(r.get(c)) if c in ("status", "state", "result") else esc(fmt(r.get(c))) for c in cols] for r in rows])
    return f"<pre>{esc(json.dumps(result, indent=1, default=str))}</pre>"


def log_line(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    hub: Hub = None  # set by serve()
    lock = threading.RLock()
    server_version = "hubproto-shell/0.1"
    max_upload = DEFAULT_MAX_UPLOAD
    timeout = 120  # a stalled client gives up its thread, never the lock

    def version_string(self) -> str:  # no Python version in the Server header
        return self.server_version

    def log_message(self, *a):  # the request log is written by _handle
        pass

    def send_response(self, code, message=None):
        self._status = code
        super().send_response(code, message)

    # --- request plumbing --------------------------------------------------
    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method: str) -> None:
        t0 = time.monotonic()
        self._status = 0
        url = urlparse(self.path)
        self.q = parse_qs(url.query, keep_blank_values=True)
        self.form: dict[str, str] = {}
        self.files: list[tuple[str, str, bytes]] = []
        self.user = (self.q.get("as", [""])[0] or "").strip() or DEFAULT_USER
        self.route = url.path.rstrip("/") or "/"
        self.arg = ""
        self.meta = None
        self.bundle = None
        try:
            if self.route == "/healthz":
                return self._text("ok")
            if method == "POST":
                self._read_body()  # validated and read outside the lock
                self.user = (self.form.get("as") or self.q.get("as", [""])[0] or "").strip()
                if not self.user:
                    raise ShellError("Say who you are: a POST needs the 'as' field.", 400)
            with self.lock:
                self.meta = self.hub.releases.current()
                self.bundle = self.hub.releases.bundle(self.meta["digest"]) if self.meta else None
                self._dispatch(method)
        except ShellError as e:
            self._page("Not done", f'<div class="card crit"><b>Not done.</b> {esc(e)}</div>' + self._back_link(), status=e.status)
        finally:
            ms = int((time.monotonic() - t0) * 1000)
            log_line(f"request method={method} path={url.path} as={self.user} status={self._status} ms={ms}")

    def _dispatch(self, method: str) -> None:
        try:
            handler = self._route(method)
            if handler is None:
                raise ShellError("No such page." if method == "GET" else "No such action.", 404)
            return handler()
        except (RunError, KernelGuardError) as e:
            return self._page("Not done", f'<div class="card crit"><b>Not done.</b> {esc(e)}<div class="why">{esc(type(e).__name__)}</div></div>'
                              + self._back_link(), status=400)
        except ShellError:
            raise
        except Exception as e:  # readable, never a blank 500; the traceback goes to the log, not the browser
            err_id = uuid.uuid4().hex[:8]
            log_line(f"error id={err_id} {type(e).__name__}: {e}\n{traceback.format_exc()}")
            detail = f"<pre>{esc(traceback.format_exc())}</pre>" if os.environ.get("HUBPROTO_DEBUG") else ""
            return self._page("Error", f'<div class="card crit"><b>{esc(type(e).__name__)}:</b> {esc(e)}<div class="why">error id {err_id} (in the server log)</div></div>{detail}',
                              status=500)

    def _route(self, method: str):
        r = self.route
        if method == "GET":
            fixed = {"/": self.page_home, "/files": self.page_files, "/boards": self.page_boards, "/inbox": self.page_inbox,
                     "/runs": self.page_runs, "/releases": self.page_releases, "/frames": self.page_frames, "/why": self.page_why,
                     "/admin": self.page_admin, "/conformance": self.page_conformance, "/download": self.page_download}
            if r in fixed:
                return fixed[r]
            for prefix, fn in (("/record/", self.page_record), ("/document/", self.page_document), ("/run/", self.page_run),
                               ("/frame/", self.page_frame)):
                if r.startswith(prefix) and len(r) > len(prefix):
                    self.arg = r[len(prefix):]
                    return fn
            return None
        posts = {"/start": self.post_start, "/decide": self.post_decide, "/msg": self.post_msg, "/attach": self.post_attach,
                 "/upload": self.post_upload, "/checkout": self.post_checkout, "/checkin": self.post_checkin, "/link": self.post_link,
                 "/snapshot": self.post_snapshot, "/hold": self.post_hold, "/resubmit": self.post_resubmit, "/retry": self.post_retry,
                 "/cancel": self.post_cancel}
        return posts.get(r)

    def _read_body(self) -> None:
        raw = self.headers.get("Content-Length")
        if raw is None:
            self.close_connection = True
            raise ShellError("A POST needs a Content-Length header.", 411)
        try:
            length = int(raw)
        except ValueError:
            self.close_connection = True
            raise ShellError(f"Content-Length {raw!r} is not a number.", 400) from None
        if length < 0:
            self.close_connection = True
            raise ShellError("Content-Length cannot be negative.", 400)
        if length > self.max_upload:
            self.close_connection = True
            raise ShellError(f"The request body ({length:,} bytes) is over the limit of {self.max_upload:,} bytes (HUBPROTO_MAX_UPLOAD).", 413)
        body = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type", "") or ""
        if ctype.startswith("multipart/form-data"):
            msg = BytesParser(policy=email.policy.default).parsebytes(b"Content-Type: " + ctype.encode("latin-1") + b"\r\n\r\n" + body)
            parts = msg.iter_parts() if msg.is_multipart() else []
            for part in parts:
                name = part.get_param("name", header="content-disposition") or ""
                filename = part.get_filename()
                payload = part.get_payload(decode=True) or b""
                if filename:
                    self.files.append((name, os.path.basename(filename), payload))
                else:
                    self.form[name] = payload.decode("utf-8", "replace")
        else:
            for k, v in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items():
                self.form[k] = v[0]

    def _text(self, text: str, status: int = 200) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _back_link(self) -> str:
        ref = self.headers.get("Referer") or ""
        scheme = urlparse(ref).scheme
        if ref and scheme in ("", "http", "https"):
            return f'<p><a href="{esc(ref)}">Back</a></p>'
        return ""

    # --- page frame --------------------------------------------------------
    def _page(self, title: str, body: str, status: int = 200, head: str = "") -> None:
        user = self.user
        bundle = self.bundle
        members = dict(bundle["members"]) if bundle else {}
        options = "".join(f'<option value="{esc(m)}"{" selected" if m == user else ""}>{esc(m)}{" (cog)" if members[m]["type"] == "cog" else ""}</option>'
                          for m in sorted(members))
        if user not in members:
            options = f'<option value="{esc(user)}" selected>{esc(user)} (not a member)</option>' + options
        keep = "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(v[0])}">' for k, v in self.q.items() if k != "as" and v)
        rail = "".join(f'<a{" class=cur" if self.route == p or (p != "/" and self.route.startswith(p)) else ""} href="{href(p, user)}">{esc(n)}</a>' for n, p in RAIL)
        cur_folder = self.q.get("folder", [""])[0]
        tree = render_tree(folder_tree(bundle["folders"]), "", user, cur_folder) if bundle and user in members else '<p class="muted">no folders</p>'
        page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} - Hub</title><style>{_CSS}</style>{head}</head>
<body><div class="top"><a href="{href("/", user)}">Hub</a><span class="crumb">{esc(title)}</span>
<form method="get" action="{esc(self.route)}">{keep}<label>Acting as <select name="as" onchange="this.form.submit()">{options}</select></label> <button class="secondary">Go</button></form></div>
<div class="layout"><nav>{rail}<h3>Folders</h3>{tree}</nav><main>{body}</main></div></body></html>"""
        data = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _need_release(self) -> dict:
        if not self.bundle:
            raise ShellError("No policy release is in effect. Compile and release Frames first (hubproto bootstrap-release).", 409)
        return self.bundle

    def _is_member(self) -> bool:
        return bool(self.bundle) and self.user in self.bundle["members"]

    def _people(self) -> list[str]:
        return sorted(p for p, m in (self.bundle or {"members": {}})["members"].items() if m["type"] == "person")

    def _can_read(self, folder: str) -> dict:
        return authz.check(self._need_release(), self.user, "read", "*", folder)

    def _require_read(self, folder: str) -> dict:
        d = self._can_read(folder)
        if not d["allowed"]:
            raise ShellError(f"You cannot read {folder}: {d['why']}", 403)
        return d

    def _record_link(self, ref) -> str:
        if not ref or not isinstance(ref, str):
            return esc(fmt(ref))
        rec = self.hub.kernel.get(ref)
        if rec is None:
            return f"<code>{esc(ref)}</code>"
        page = "/document/" if rec["type"] == "document" else "/record/"
        return f'<a href="{href(page + quote(rec["locator"], safe=":"), self.user)}">{esc(rec["number"])}</a> <span class="muted">{esc(summary_of(rec))}</span>'

    def _run_visible(self, run: dict) -> bool:
        """A run is shown to a member who may read its record's folder, owns it, or is asked to act on it."""
        if not self._is_member():
            return False
        folder = (run.get("manifest") or {}).get("folder", "")
        if folder and self._can_read(folder)["allowed"]:
            return True
        if run.get("owner") == self.user:
            return True
        pending = run.get("pending") or {}
        return self.user in (pending.get("assignees") or [])

    def _visible_runs(self) -> list[dict]:
        if not self._is_member():
            return []
        cache: dict[str, bool] = {}
        out = []
        for r in self.hub.runner.list():
            folder = (r.get("manifest") or {}).get("folder", "")
            if folder not in cache:
                cache[folder] = bool(folder) and self._can_read(folder)["allowed"]
            if cache[folder] or r.get("owner") == self.user or self.user in ((r.get("pending") or {}).get("assignees") or []):
                out.append(r)
        return out

    def _pending(self, user: str, kind: str | None = None) -> list[dict]:
        runs = self.hub.runner.pending_for(user)
        return [r for r in runs if kind is None or r["pending"].get("kind", "decision") == kind]

    def _failed_for(self, user: str) -> list[dict]:
        return [r for r in self.hub.runner.list() if r["status"] == "failed" and r["owner"] == user]

    def _failure_of(self, run_id: str) -> dict:
        for ev in reversed(self.hub.runner.track(run_id)):
            if ev["type"] == "run.failed":
                return ev
        return {}

    # --- pages --------------------------------------------------------------
    def page_home(self):
        user = self.user
        if not self.bundle:
            return self._page("Home", "<div class='card'>No policy release is in effect. Compile and release Frames first "
                                      "(<code>hubproto bootstrap-release --as bob --second carol</code>).</div>")
        bundle, meta = self.bundle, self.meta
        if not self._is_member():
            return self._page("Home", f"<h1>Welcome, {esc(user)}</h1><div class='card crit'><b>{esc(user)} is not a member of the organization</b> under the "
                                      "policy release in effect, so nothing here is available. Ask a policy owner to add you to the org model.</div>")
        m = bundle["members"][user]
        runs = [r for r in self.hub.runner.list() if r["owner"] == user]
        waiting = self._pending(user, "decision")
        returned = self._pending(user, "resubmit")
        failed = self._failed_for(user)
        roles = ", ".join(f'<span class="chip">{esc(r)}</span>' for r in m["roles"]) or "none"
        body = f"""<h1>Welcome, {esc(user)} <span class="chip">{esc(m['type'])}</span></h1>
<div class="card">Roles: {roles}<br><span class="why">Policy release in effect: <code>{esc(meta['digest'][:16])}</code> effective from {esc(meta['effective_from'])},
released by {esc(meta['released_by'])} (<a href="{href('/releases', user)}">releases</a>)</span></div>
<div class="counts"><div class="card"><b>{len(waiting)}</b><a href="{href('/inbox', user)}">approvals waiting for me</a></div>
<div class="card"><b>{len(returned) + len(failed)}</b><a href="{href('/inbox', user)}">of mine returned or failed</a></div>
<div class="card"><b>{len([r for r in runs if r['status'] == 'waiting'])}</b>of my runs waiting</div>
<div class="card"><b>{len(runs)}</b><a href="{href('/runs', user)}">runs I started</a></div></div>
<h2>Processes you can start</h2>"""
        startable, others = [], []
        for name, op in bundle["ops"].items():
            d = authz.check(bundle, user, "create", op["object"], op["folder"])
            first_role = op["steps"][0]["role"]
            holds = first_role in ("system", *FIELD_ROLES) or first_role in authz.roles_of(bundle, user)
            if d["allowed"] and holds:
                startable.append((name, op, d))
            else:
                why = d["why"] if not d["allowed"] else f"you do not hold the role {first_role!r} that starts it"
                others.append((name, op, why))
        if not startable:
            body += "<p class='muted'>None under the release in effect.</p>"
        for name, op, d in startable:
            rt = op.get("record_type", "")
            body += f"""<div class="card"><b>{esc(name)}</b> <span class="chip">{esc(rt)}</span> filed in {esc(op['folder'])}
<div class="why">{esc(d['why'])}</div>
<form method="post" action="/start" enctype="multipart/form-data">{hidden_as(user)}<input type="hidden" name="process" value="{esc(name)}">
<div>{self._start_inputs(rt, required=self.hub.runner.required_at_start(name))}</div>
<label class="f"><span>files to attach</span><input type="file" name="files" multiple></label>
<label class="f"><span>other fields (JSON object)</span><textarea name="json" rows="2" cols="50" placeholder='{{"lot": "L-42"}}'></textarea></label>
<div><button>Start {esc(name)}</button></div></form></div>"""
        if others:
            body += "<h3>Not available to you</h3>" + table(["Process", "Filed in", "Why"], [[esc(n), esc(op["folder"]), f'<span class="why">{esc(w)}</span>'] for n, op, w in others])
        body += "<h2>Frames behind this release</h2><ul>"
        for f in meta["frames"]:
            body += f"<li><a href='{href('/frame/' + f['name'], user)}'>{esc(f['name'])}</a> v{esc(f['version'])} ({esc(f['scope'])}, maintained by {esc(f['maintainer'])})</li>"
        self._page("Home", body + "</ul>")

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

    def _start_inputs(self, rt: str, values: dict | None = None, required: list[str] | None = None) -> str:
        values = values or {}
        req = set(required or [])
        return "".join(widget(name, spec, values.get(name, ""), required=name in req, kind=kind, people=self._people())
                       for name, kind, spec in self._start_spec(rt))

    def page_files(self):
        user = self.user
        bundle = self._need_release()
        folder = self.q.get("folder", [""])[0].strip("/")
        if not folder:
            roots = [f for f in bundle["folders"] if "/" not in f]
            rows = []
            for f in roots:
                d = self._can_read(f)
                rows.append([f'<a href="{href("/files", user, folder=f)}">{esc(f)}</a>', chip("readable") if d["allowed"] else chip("no access"), f'<span class="why">{esc(d["why"])}</span>'])
            return self._page("Files", "<h1>Files</h1>" + table(["Folder", "Access", "Why"], rows))
        d = self._can_read(folder)
        body = f"<h1>{esc(folder)}</h1>"
        if not d["allowed"]:
            return self._page(folder, body + f"<div class='card crit'>You cannot read this folder.<div class='why'>{esc(d['why'])}</div></div>", status=403)
        body += f"<div class='why'>{esc(d['why'])}</div>"
        subs = [f for f in bundle["folders"] if f.startswith(folder + "/") and "/" not in f[len(folder) + 1:]]
        if subs:
            body += "<h2>Folders</h2><ul>" + "".join(f'<li><a href="{href("/files", user, folder=s)}">{esc(s.split("/")[-1])}</a></li>' for s in subs) + "</ul>"
        recs = [r for r in self.hub.kernel.query(folder=folder) if r["folder"] == folder]
        records = [r for r in recs if r["type"] != "document"]
        docs = [r for r in recs if r["type"] == "document"]
        body += "<h2>Records</h2>" + table(["Number", "Type", "State", "Title / summary", "Owner", "Assignees", "Due", "Updated", "Run"],
                                            [[f'<a href="{href("/record/" + quote(r["locator"], safe=":"), user)}">{esc(r["number"])}</a>', esc(r["type"]), chip(r["state"]),
                                              esc(summary_of(r)), esc(owner_of(r)), esc(", ".join(r["assignees"])), esc(r["due"]), esc(updated_of(r)),
                                              f'<a href="{href("/run/" + r["run_id"], user)}">{esc(r["run_id"][:8])}</a>' if r.get("run_id") else ""] for r in records],
                                            "No records in this folder.")
        body += "<h2>Documents</h2>" + table(["Number", "Title", "Class", "Rev", "State", "Owner", "Size", "Updated"],
                                              [[f'<a href="{href("/document/" + quote(r["locator"], safe=":"), user)}">{esc(r["number"])}</a>', esc(r["fields"].get("title")),
                                                esc(r["fields"].get("doc_class")), esc(f"{r['fields'].get('revision', '')}.{r['fields'].get('minor', 0)}"), chip(r["state"]),
                                                esc(owner_of(r)), esc(r["fields"].get("size", "")), esc(updated_of(r))] for r in docs], "No documents in this folder.")
        f = may_file(bundle, user, folder)
        if f["allowed"]:
            body += f"""<h2>Upload a file</h2><div class="card"><div class="why">Files a received file as an external document (immutable, with provenance). {esc(f['why'])}</div>
<form method="post" action="/upload" enctype="multipart/form-data">{hidden_as(user)}<input type="hidden" name="folder" value="{esc(folder)}">
<input type="file" name="file" required> <input name="title" placeholder="title (defaults to the file name)" size="30"> <button>Upload</button></form></div>"""
        else:
            body += f"<p class='why'>You cannot file documents here: {esc(f['why'])}</p>"
        self._page(folder, body)

    def page_boards(self):
        user = self.user
        bundle = self._need_release()
        workflows = bundle.get("workflows", {})
        default_type = next(iter(workflows.values()))["record_type"] if workflows else "task"
        type_ = self.q.get("type", [default_type])[0] or default_type
        if type_ not in self.hub.kernel.types:
            raise ShellError(f"No record type {type_!r}.", 404)
        state, folder, assignee, text = (self.q.get(k, [""])[0] for k in ("state", "folder", "assignee", "text"))
        columns: list[str] = []
        for name, wf in workflows.items():
            if wf["record_type"] == type_:
                for s in wf["states"]:
                    if s not in columns:
                        columns.append(s)
        if not columns:
            columns.append("open")
        for s in ("returned", "failed", "rejected", "cancelled"):
            if s not in columns:
                columns.append(s)
        recs = self.hub.kernel.query(type_=type_, folder=folder or None, state=state or None, assignee=assignee or None, text=text or None)
        readable: dict[str, bool] = {}
        for r in recs:
            if r["folder"] not in readable:
                readable[r["folder"]] = self._can_read(r["folder"])["allowed"]
        recs = [r for r in recs if readable[r["folder"]]]
        grouped: dict[str, list[dict]] = {c: [] for c in columns}
        for r in recs:
            grouped.setdefault(r["state"], []).append(r)
        today = date.today().isoformat()
        types = "".join(f'<option value="{esc(t)}"{" selected" if t == type_ else ""}>{esc(t)}</option>' for t in self.hub.kernel.types)
        folders = "".join(f'<option value="{esc(f)}"{" selected" if f == folder else ""}>{esc(f)}</option>' for f in bundle["folders"])
        people = "".join(f'<option value="{esc(p)}"{" selected" if p == assignee else ""}>{esc(p)}</option>' for p in bundle["members"])
        states = "".join(f'<option value="{esc(s)}"{" selected" if s == state else ""}>{esc(s)}</option>' for s in columns)
        saved = href("/boards", user, type=type_, state=state, folder=folder, assignee=assignee, text=text)
        body = f"""<h1>Boards <span class="chip">{esc(type_)}</span></h1>
<form method="get" action="/boards" class="card">{hidden_as(user)}
<label class="f"><span>type</span><select name="type">{types}</select></label>
<label class="f"><span>state</span><select name="state"><option value="">any</option>{states}</select></label>
<label class="f"><span>folder</span><select name="folder"><option value="">any</option>{folders}</select></label>
<label class="f"><span>assignee</span><select name="assignee"><option value="">any</option>{people}</select></label>
<label class="f"><span>text</span><input name="text" value="{esc(text)}" size="18"></label>
<button>Filter</button> <span class="why">Saved filter: <a href="{saved}">{esc(saved)}</a></span></form><div class="board">"""
        for col, items in grouped.items():
            body += f"<div class='col'><h3>{chip(col)} <span class='muted'>{len(items)}</span></h3>"
            for r in items:
                overdue = " " + chip("overdue") if r["due"] and r["due"] < today and r["state"].startswith("awaiting") else ""
                body += f"""<div class="kcard"><a href="{href('/record/' + quote(r['locator'], safe=':'), user)}">{esc(r['number'])}</a> {esc(summary_of(r))}{overdue}
<div class="why">{esc(r['folder'])} &middot; {esc(owner_of(r))}{' &middot; ' + esc(', '.join(r['assignees'])) if r['assignees'] else ''}{' &middot; due ' + esc(r['due']) if r['due'] else ''}</div></div>"""
            body += "</div>"
        self._page("Boards", body + "</div>")

    # --- inbox cards ---------------------------------------------------------
    def _payload_of(self, run: dict) -> dict:
        step = run["steps"].get((run.get("pending") or {}).get("step", ""), {})
        payload = (step.get("envelope") or {}).get("payload") or {}
        return {k: v for k, v in payload.items() if k not in BLOB_FIELDS}

    def _guard_problems(self, run: dict) -> list[str]:
        out = []
        for sid, rec in run["steps"].items():
            env = rec.get("envelope") or {}
            out += [f"{sid}: [{p.get('severity', '')}] {p.get('check', '')}: {p.get('detail', '')}" for p in env.get("problems", [])]
            if env.get("error"):
                out.append(f"{sid}: error {env['error'].get('code', '')}: {env['error'].get('detail', '')}")
        return out

    def _brief(self, rec: dict, payload: dict) -> str:
        """A readable summary of a record for the person deciding on it: key fields by type, small tables for the rest."""
        f = {k: v for k, v in payload.items() if k not in ("id", "folder", "locator", "state")}
        f.update(rec["fields"])
        t = rec["type"]
        rows: list[tuple[str, str]] = []
        extra = ""

        def add(label, key=None, value=None, link=False):
            v = f.get(key) if key else value
            if v in (None, "", [], {}):
                return
            rows.append((label, self._record_link(v) if link else esc(short(v))))

        def risk():
            if f.get("risk"):
                reasons = f.get("risk_reasons") or []
                rows.append(("risk", chip(f["risk"]) + (" " + esc("; ".join(map(str, reasons))) if reasons else "")))

        if t == "bill":
            add("supplier", "supplier")
            add("amount", value=f"{f.get('amount', '')} {f.get('currency', '')}".strip() if f.get("amount") is not None else None)
            add("date", "date")
            add("purchase order", "po_ref")
            add("description", "description")
            m = f.get("match") or {}
            if m:
                verdict = "within tolerance" if m.get("within_tolerance") else "outside tolerance"
                parts = [verdict, f"variance {m.get('variance_percent')}%" if m.get("variance_percent") is not None else "",
                         "PO found" if m.get("po_found") else "no PO", "received" if m.get("received") else "not received",
                         "supplier approved" if m.get("supplier_approved") else "supplier not approved"]
                rows.append(("match result", chip(verdict) + " " + esc(", ".join(p for p in parts[1:] if p))))
                add("billed to date", value=f"{m.get('billed_to_date', 0):.2f} {f.get('currency', '')}" if m.get("billed_to_date") is not None else None)
            risk()
        elif t == "purchase-request":
            add("title", "title")
            add("item", "item", link=True)
            add("mpn", "mpn")
            add("quantity", value=f"{f.get('quantity', '')} {f.get('unit', '')}".strip() if f.get("quantity") is not None else None)
            add("unit cost", "unit_cost")
            add("amount", value=f"{f.get('amount', '')} {f.get('currency', '')}".strip() if f.get("amount") is not None else None)
            add("supplier", "supplier")
            if "supplier_approved" in f:
                rows.append(("supplier approved", chip("yes" if f.get("supplier_approved") else "no") + " " + esc(f.get("supplier_note") or "")))
            add("needed by", "needed_by")
            add("reason", "reason")
            add("design", "design", link=True)
            risk()
        elif t == "design":
            add("title", "title")
            add("kind", "kind")
            add("change class", "change_class")
            add("description", "description")
            add("designer", "designer")
            risk()
            trace = f.get("requirement_trace") or []
            if trace:
                extra += "<h4>Requirements</h4>" + table(["Requirement", "Kind", "Statement", "State"],
                                                         [[self._record_link(x.get("requirement")), esc(x.get("kind")), esc(short(x.get("statement"))), chip(x.get("state"))]
                                                          for x in trace])
            elif f.get("user_needs"):
                extra += "<h4>User needs</h4><ul>" + "".join(f"<li>{esc(short(n))}</li>" for n in f["user_needs"]) + "</ul>"
            if any(k in f for k in ("bom_resolved", "bom_unresolved", "bom_lines")):
                rows.append(("BOM", esc(f"{len(f.get('bom_lines') or [])} lines: {len(f.get('bom_resolved') or [])} resolved, "
                                        f"{len(f.get('bom_unresolved') or [])} unresolved, {len(f.get('bom_unapproved') or [])} from unapproved manufacturers")))
            checks = f.get("checks") or {}
            if checks:
                rows.append(("checks", " ".join(f"{esc(name.upper())} " + (chip(f"{c.get('errors')} errors") if c.get("ran") else chip("not run"))
                                                 + (f" {esc(c.get('warnings'))} warnings" if c.get("ran") and c.get("warnings") else "") for name, c in checks.items())))
            exports = f.get("exports") or []
            if exports:
                extra += "<h4>Exports</h4><ul>" + "".join(f"<li>{esc(x.get('doc_type'))}: {self._record_link(x.get('document'))}</li>" for x in exports) + "</ul>"
            if f.get("part"):
                add("part", "part", link=True)
        elif t == "requirement":
            rows.append(("statement", f"<b>{esc(short(f.get('statement') or '', 600))}</b>"))
            add("kind", "kind")
            add("source", "source")
            add("priority", "priority")
            add("design", "design", link=True)
            add("acceptance criteria", "acceptance_criteria")
            add("verification method", "verification_method")
            risk()
        elif t == "ncr":
            add("title", "title")
            add("description", "description")
            add("source", "source")
            add("severity", "severity")
            add("containment", "containment")
            add("lot", "lot")
            add("quantity", "quantity")
            add("part", "part", link=True)
            risk()
        elif t == "receipt":
            add("purchase order", "po", link=True)
            add("supplier", "supplier")
            lines = f.get("lines") or []
            if lines:
                extra += "<h4>Lines</h4>" + table(["Item", "Quantity"], [[self._record_link(l.get("item")), esc(l.get("quantity"))] for l in lines if isinstance(l, dict)])
            add("location", "location")
            add("notes", "notes")
        elif t == "document":
            add("title", "title")
            add("type", "doc_type")
            add("change type", "change_type")
            add("revision", value=f"{f.get('revision', '')}.{f.get('minor', 0)}")
            rows.append(("document", self._record_link(rec["locator"])))
            add("training roles", "training_roles")
            risk()
        else:
            for k, v in sorted(f.items()):
                if k in BLOB_FIELDS or k in ("conditions",) or isinstance(v, (dict, list)) or v in (None, ""):
                    continue
                rows.append((k, esc(short(v))))
            risk()
        return f'<div class="brief">{kv(rows)}{extra}</div>'

    def _decision_inputs(self, run: dict, rec: dict | None) -> tuple[str, list[str], list[str]]:
        """Form controls for a decision: the next state's required-but-missing fields (required) and the type's
        other decision fields (optional); never a system field."""
        if rec is None:
            return "", [], []
        allowed = self.hub.runner.allowed_decision_fields(run)
        td = self.hub.kernel.types.get(rec["type"], {})
        system = set(td.get("system_fields") or [])
        nxt = next_state_for(run.get("workflow"), run["pending"]["checkpoint"])
        missing = [x for x in self.hub.kernel.required_missing(rec, nxt) if x not in system] if nxt else []
        optional = [x for x in allowed if x not in missing]
        people = self._people()
        html_ = "".join(widget(x, td.get("fields", {}).get(x), required=True, people=people) for x in missing)
        html_ += "".join(widget(x, td.get("fields", {}).get(x), rec["fields"].get(x, ""), people=people) for x in optional)
        return html_, missing, optional

    def _rule_html(self, rule: dict | None) -> str:
        rule = rule or {}
        frame, line, text = rule.get("frame", ""), rule.get("line", 0), rule.get("text", "")
        if frame and not frame.startswith("(") and frame in self.hub.frames.frames:
            return f'<a href="{href("/frame/" + frame, self.user)}#L{line}">{esc(frame)} line {esc(line)}</a>: "{esc(text)}"'
        return f'{esc(frame)}{" line " + esc(line) if line else ""}: "{esc(text)}"'

    def _decision_card(self, r: dict, today: str) -> str:
        user = self.user
        p = r["pending"]
        rec = self.hub.kernel.get(r["record"]) if r.get("record") else None
        nxt = next_state_for(r.get("workflow"), p["checkpoint"])
        inputs, missing, optional = self._decision_inputs(r, rec)
        problems = self._guard_problems(r)
        brief = self._brief(rec, self._payload_of(r)) if rec else "<span class='muted'>no record</span>"
        overdue = p.get("due") and p["due"] < today
        due = (f" &middot; due <b>{esc(p['due'])}</b> " + (chip("overdue") if overdue else "")) if p.get("due") else ""
        esc_note = f" &middot; <span class='chip warn'>escalated {esc(p['escalated'])}</span>" if p.get("escalated") else ""
        needs = ""
        if missing:
            needs = f'<div class="why">{esc(nxt)} needs: {esc(", ".join(missing))}</div>'
        elif optional:
            needs = '<div class="why">You may also set: ' + esc(", ".join(optional)) + "</div>"
        hold = f"<div class='chip crit'>on hold: {esc(rec.get('hold_reason', ''))}; lift the hold first</div>" if rec and rec["hold"] else ""
        return f"""<div class="card{' crit' if overdue else ''}"><b>{esc(p['title'])}</b> &middot; {esc(r['process'])} &middot; {self._record_link(r['record']) if rec else ''} &middot;
run <a href="{href('/run/' + r['id'], user)}">{esc(r['id'])}</a> started by {esc(r['owner'])}{due}{esc_note}
<div class="why">Routed to you by {self._rule_html(p.get('route_rule'))}{' &middot; approval leads to ' + chip(nxt) if nxt else ''}</div>
{brief}
{'<div class="why"><b>Guard findings:</b><br>' + '<br>'.join(esc(x) for x in problems) + '</div>' if problems else ''}
{hold}
<form method="post" action="/decide">{hidden_as(user)}<input type="hidden" name="run" value="{esc(r['id'])}"><input type="hidden" name="digest" value="{esc(r.get('seen_digest') or self.hub.runner.seen_digest(r))}">
{needs}<div>{inputs}</div>
<div><label class="f"><span>meaning of your signature <b>*</b></span><input name="meaning" placeholder="e.g. approved for posting" size="40" required></label>
<button name="verdict" value="approve">Approve</button> <button class="secondary" name="verdict" value="reject">Reject</button></div></form></div>"""

    def _resubmit_form(self, r: dict, rec: dict | None) -> str:
        user = self.user
        p = r["pending"]
        rt = rec["type"] if rec else r["workflow"]["record_type"]
        values = rec["fields"] if rec else {}
        return f"""<form method="post" action="/resubmit" enctype="multipart/form-data">{hidden_as(user)}<input type="hidden" name="run" value="{esc(r['id'])}">
<div class="why">Rework from step <b>{esc(p.get('step'))}</b>: change what needs changing (unchanged fields are left alone), add files, and resubmit.</div>
<div>{self._start_inputs(rt, values)}</div>
<label class="f"><span>files to attach</span><input type="file" name="files" multiple></label>
<label class="f"><span>other fields (JSON object)</span><textarea name="json" rows="2" cols="50"></textarea></label>
<div><label class="f"><span>note</span><input name="note" size="40" placeholder="what changed"></label> <button>Resubmit</button></div></form>"""

    def _retry_cancel_forms(self, r: dict) -> str:
        user = self.user
        return f"""<form method="post" action="/retry" class="inline">{hidden_as(user)}<input type="hidden" name="run" value="{esc(r['id'])}">
<input name="note" placeholder="what was fixed" size="28"> <button>Retry</button></form>
<form method="post" action="/cancel" class="inline">{hidden_as(user)}<input type="hidden" name="run" value="{esc(r['id'])}">
<input name="reason" placeholder="reason" size="20"> <button class="danger">Cancel the run</button></form>"""

    def page_inbox(self):
        user = self.user
        bundle = self._need_release()
        body = "<h1>Inbox</h1>"
        if not self._is_member():
            return self._page("Inbox", body + f"<div class='card'><b>{esc(user)} is not a member</b> of the organization under the release in effect; "
                                              "there is nothing in this inbox. Ask a policy owner to add you to the org model.</div>")
        today = date.today().isoformat()
        waiting = self._pending(user, "decision")
        waiting.sort(key=lambda r: (0 if (r["pending"].get("due") and r["pending"]["due"] < today) else 1, r["pending"].get("due") or "9999", r["created_at"]))
        body += f"<h2>Waiting for your decision <span class='muted'>{len(waiting)}</span></h2>"
        if not waiting:
            body += "<p class='muted'>Nothing is waiting for you.</p>"
        for r in waiting:
            body += self._decision_card(r, today)
        returned = self._pending(user, "resubmit")
        if returned:
            body += f"<h2>Returned to you for rework <span class='muted'>{len(returned)}</span></h2>"
            for r in returned:
                p = r["pending"]
                rec = self.hub.kernel.get(r["record"]) if r.get("record") else None
                body += f"""<div class="card warn"><b>{esc(p['title'])}</b> &middot; {esc(r['process'])} &middot; {self._record_link(r['record']) if rec else ''} &middot;
run <a href="{href('/run/' + r['id'], user)}">{esc(r['id'])}</a><div class="why">{esc(p.get('route_rule', {}).get('text', ''))}</div>
<div><b>Reason:</b> {esc(p.get('reason') or '-')}</div>{self._resubmit_form(r, rec)}
<form method="post" action="/cancel" class="inline">{hidden_as(user)}<input type="hidden" name="run" value="{esc(r['id'])}"><input name="reason" placeholder="reason" size="20"> <button class="danger">Withdraw</button></form></div>"""
        failed = self._failed_for(user)
        if failed:
            body += f"<h2>Failed runs of yours <span class='muted'>{len(failed)}</span></h2>"
            for r in failed:
                ev = self._failure_of(r["id"])
                rec = self.hub.kernel.get(r["record"]) if r.get("record") else None
                body += f"""<div class="card crit"><b>{esc(r['process'])}</b> &middot; {self._record_link(r['record']) if rec else ''} &middot; run <a href="{href('/run/' + r['id'], user)}">{esc(r['id'])}</a>
failed at <b>{esc(ev.get('step', '?'))}</b>: {esc(ev.get('reason', 'no reason recorded'))}
<div class="why">Fix the record (its page has the details) and retry, or cancel the run.</div>{self._retry_cancel_forms(r)}</div>"""
        msgs = list(reversed(self.hub.inbox.for_principal(user)))
        still_pending = {r["id"] for r in waiting} | {r["id"] for r in returned}
        notes = [m for m in msgs if m["kind"] in NOTIFICATION_KINDS]
        others = [m for m in msgs if m["kind"] not in NOTIFICATION_KINDS and (m["kind"] != "approval" or m.get("run_id") in still_pending)]
        body += f"<h2>Notifications <span class='muted'>{len(notes)}</span></h2>"
        if not notes:
            body += "<p class='muted'>No escalations, failures, returns or rejections.</p>"
        for m in notes:
            cls = "crit" if m["kind"] in ("escalation", "failure") else "warn"
            body += f"<div class='card {cls}'><span class='chip {cls}'>{esc(m['kind'])}</span> <b>{esc(m['subject'])}</b> at {esc(m['at'][:19])}<br>{esc(short(m['body'], 600))}"
            body += self._message_links(m) + "</div>"
        body += f"<h2>Messages <span class='muted'>{len(others)}</span></h2>"
        if not others:
            body += "<p class='muted'>No messages.</p>"
        for m in others:
            body += f"<div class='card'><span class='chip'>{esc(m['channel'])}/{esc(m['kind'])}</span> <b>{esc(m['subject'])}</b> from {esc(m['from'])} at {esc(m['at'][:19])}<br>{esc(short(m['body'], 600))}"
            body += self._message_links(m)
            if m.get("attachments"):
                body += f"<div class='why'>attachments: {esc(', '.join(a.get('name', '') for a in m['attachments']))}</div>"
            body += "</div>"
        options = "".join(f"<option value='{esc(p)}'>{esc(p)}{' (cog)' if m['type'] == 'cog' else ''}</option>" for p, m in bundle["members"].items() if p != user)
        body += f"""<h2>Send a message</h2><form method="post" action="/msg" class="card">{hidden_as(user)}
<label class="f"><span>to</span><select name="to">{options}</select></label> <label class="f"><span>via</span><select name="channel"><option value="dm">DM</option><option value="email">email</option></select></label>
<label class="f"><span>subject</span><input name="subject" size="30" required></label><br><label class="f"><span>message</span><textarea name="body" rows="2" cols="60"></textarea></label><br><button>Send</button></form>"""
        self._page("Inbox", body)

    def _message_links(self, m: dict) -> str:
        out = ""
        if m.get("run_id"):
            out += f" <a href='{href('/run/' + m['run_id'], self.user)}'>open run</a>"
            try:
                run = self.hub.runner.load(m["run_id"])
            except RunError:
                run = None
            if run and run.get("record") and self.hub.kernel.get(run["record"]):
                out += f" &middot; <a href='{href('/record/' + quote(run['record'], safe=':'), self.user)}'>open record</a>"
        return out

    # --- record and document pages --------------------------------------------
    def page_record(self):
        user = self.user
        bundle = self._need_release()
        rec = self.hub.kernel.get(self.arg)
        if rec is None:
            raise ShellError(f"No record {self.arg!r}.", 404)
        if rec["type"] == "document":
            return self._redirect(href("/document/" + quote(rec["locator"], safe=":"), user))
        self._require_read(rec["folder"])
        loc = rec["locator"]
        run = self.hub.runner.run_for_record(loc)
        process, wf = workflow_for(bundle, rec, run)
        obj = self.hub.kernel.types.get(rec["type"], {}).get("object", rec["type"])
        why = self.hub.why(user, "approve", obj, rec["folder"])
        labels = " ".join(f'<span class="chip">{esc(k)}: {esc(v)}</span>' for k, v in rec["labels"].items())
        hold = f' <span class="chip crit">on hold: {esc(rec.get("hold_reason", ""))}</span>' if rec["hold"] else ""
        today = date.today().isoformat()
        overdue = " " + chip("overdue") if rec["due"] and rec["due"] < today and rec["state"].startswith("awaiting") else ""
        tabs = "".join(f'<a href="#{t.lower()}">{t}</a>' for t in ("Details", "Workflow", "Documents", "Links", "Activity", "Decisions", "Track"))
        headline = rec["fields"].get("statement") if rec["type"] == "requirement" else summary_of(rec)
        body = f"""<h1>{esc(rec['number'])} <span class="chip">{esc(rec['type'])}</span> {chip(rec['state'])}{hold}{overdue}</h1>
<div class="card"><b>{esc(headline)}</b><div class="why">folder <a href="{href('/files', user, folder=rec['folder'])}">{esc(rec['folder'])}</a> &middot; {labels} &middot; owner {esc(owner_of(rec))}
&middot; assignees {esc(', '.join(rec['assignees']) or '-')} &middot; due {esc(rec['due'] or '-')} &middot; process {esc(process or '-')}
&middot; run {f'<a href="{href("/run/" + run["id"], user)}">{esc(run["id"])}</a> ' + chip(run["status"]) if run else '-'}</div>
<div class="why"><b>Why:</b> may {esc(user)} approve {esc(obj)} in {esc(rec['folder'])}? {chip('allowed' if why['allowed'] else 'denied')} {esc(why['why'])}</div></div>
<div class="tabs">{tabs}</div>"""
        # Run controls: failed runs go back to their owner; returned items are reworked and resubmitted.
        if run and run["status"] == "failed" and rec["state"] == "failed":
            ev = self._failure_of(run["id"])
            if user == run["owner"] or why["allowed"]:
                body += f"""<div class="card crit"><b>The run failed at {esc(ev.get('step', '?'))}:</b> {esc(ev.get('reason', ''))}
<div class="why">Fix the record and retry the run from the failed step, or cancel it.</div>{self._retry_cancel_forms(run)}</div>"""
            else:
                body += f"<div class='card crit'><b>The run failed at {esc(ev.get('step', '?'))}:</b> {esc(ev.get('reason', ''))} <span class='why'>Its owner {esc(run['owner'])} may retry or cancel it.</span></div>"
        if run and run.get("pending") and run["pending"].get("kind") == "resubmit":
            p = run["pending"]
            if user in p["assignees"]:
                body += f"<div class='card warn'><b>{esc(p['title'])}</b>: {esc(p.get('reason') or '')}{self._resubmit_form(run, rec)}</div>"
            else:
                body += f"<div class='card warn'><b>{esc(p['title'])}</b>: {esc(p.get('reason') or '')} <span class='why'>Waiting for {esc(', '.join(p['assignees']))} to resubmit.</span></div>"
        # Details
        rows = [[esc(k), esc(short(v, 600))] for k, v in sorted(rec["fields"].items()) if k not in BLOB_FIELDS and v not in (None, "", [], {})]
        body += '<h2 id="details">Details</h2>' + table(["Field", "Value"], rows, "No fields.")
        can_control = why["allowed"]
        body += f"""<div class="card"><form method="post" action="/snapshot" class="inline">{hidden_as(user)}<input type="hidden" name="record" value="{esc(loc)}"><button class="secondary">Render snapshot</button></form>
<form method="post" action="/hold" class="inline">{hidden_as(user)}<input type="hidden" name="record" value="{esc(loc)}"><input type="hidden" name="on" value="{'0' if rec['hold'] else '1'}">
<input name="reason" placeholder="reason" size="24"> <button class="secondary"{'' if can_control else ' disabled'}>{'Release hold' if rec['hold'] else 'Put on hold'}</button></form>
{'' if can_control else '<span class="why">Holds need an approve permission here.</span>'}</div>"""
        # Workflow
        body += '<h2 id="workflow">Workflow</h2>'
        if wf:
            diagram = mermaid_for(wf, rec["state"])
            states = list(wf["states"]) + [s for s in ("returned", "failed", "cancelled") if s not in wf["states"]]
            body += f'<div class="card"><pre class="mermaid" id="wf-diagram">{esc(diagram)}</pre><p class="why">States: ' + " &middot; ".join(chip(s) + (" (current)" if s == rec["state"] else "") for s in states) \
                + "<br>returned, failed and cancelled are side exits: a rejection that returns the item to its owner, a failed step, a cancelled run.</p>"
            trows = []
            for t in wf["transitions"]:
                if t["kind"] == "automatic":
                    trows.append([esc(t["from"]), esc(t["to"]), "automatic", esc(", ".join(t.get("steps") or [])), ""])
                else:
                    cp = next((c for c in wf["checkpoints"] if c["id"] == t["step"]), {})
                    who = ""
                    if rec["state"] == t["from"]:
                        assignees = (run or {}).get("pending", {}) or {}
                        people = assignees.get("assignees") or rec["assignees"]
                        eligible = [p for p, m in bundle["members"].items() if cp.get("role") in m["roles"]
                                    and authz.check(bundle, p, "approve", obj, rec["folder"])["allowed"]]
                        who = f"<b>pending</b>: assigned to {esc(', '.join(people) or '-')}; role {esc(cp.get('role'))} held by {esc(', '.join(eligible) or 'nobody')}"
                    trows.append([esc(t["from"]), esc(t["to"]), f"human: {esc(t['title'])}" + (f" (SLA {t['sla_days']} days)" if t.get("sla_days") else ""),
                                  f"reject &rarr; {esc(t.get('on_reject'))}", who])
            body += table(["From", "To", "Kind", "Steps / on reject", "Who may take it"], trows) + "</div>"
        else:
            body += "<p class='muted'>No workflow in the release covers this record type.</p>"
        # Documents
        docs = self.hub.dms.documents_of(loc)
        body += '<h2 id="documents">Documents</h2>' + table(["Number", "Title", "Class", "Type", "Rev", "State", "Size"],
                                                               [[f'<a href="{href("/document/" + quote(d["locator"], safe=":"), user)}">{esc(d["number"])}</a>', esc(d["fields"].get("title")),
                                                                 esc(d["fields"].get("doc_class")), esc(d["fields"].get("doc_type")), esc(f"{d['fields'].get('revision', '')}.{d['fields'].get('minor', 0)}"), chip(d["state"]),
                                                                 esc(d["fields"].get("size", ""))] for d in docs], "No documents attached.")
        f = may_file(bundle, user, rec["folder"])
        if f["allowed"]:
            in_folder = [d for d in self.hub.kernel.query(type_="document", folder=rec["folder"]) if d["folder"] == rec["folder"]
                         and d["locator"] not in {x["locator"] for x in docs}]
            opts = "".join(f'<option value="{esc(d["locator"])}">{esc(d["number"])} {esc(d["fields"].get("title"))}</option>' for d in in_folder)
            body += f"""<div class="card"><form method="post" action="/attach" enctype="multipart/form-data">{hidden_as(user)}<input type="hidden" name="record" value="{esc(loc)}">
Upload and attach <input type="file" name="file"> <input name="title" placeholder="title" size="20">{' or attach existing <select name="doc"><option value=""></option>' + opts + '</select>' if opts else ''} <button>Attach</button></form></div>"""
        else:
            body += f"<p class='why'>You cannot attach documents here: {esc(f['why'])}</p>"
        # Links
        links = self.hub.kernel.links_of(loc)
        body += '<h2 id="links">Links</h2>' + table(["Relation", "Record", "Direction"], [[esc(l["label"]), self._record_link(l["target"]), esc(l["direction"])] for l in links], "No links.")
        if f["allowed"]:
            rels = "".join(f'<option value="{esc(r)}">{esc(r)}</option>' for r in self.hub.kernel.link_vocab)
            body += f"""<div class="card"><form method="post" action="/link">{hidden_as(user)}<input type="hidden" name="record" value="{esc(loc)}">
Link as <select name="rel">{rels}</select> to <input name="target" placeholder="record number or locator" size="26" required> <button>Add link</button></form></div>"""
        # Activity
        evs = self.hub.kernel.events(aggregate=loc)
        body += '<h2 id="activity">Activity</h2>' + table(["#", "At", "Event", "By", "Run", "Detail"],
                                                           [[esc(e["seq"]), esc(e["at"][:19]), f"<code>{esc(e['type'])}</code>", esc(e["by"]),
                                                             f'<a href="{href("/run/" + e["run_id"], user)}">{esc(e["run_id"][:8])}</a>' if e["run_id"] else "",
                                                             esc(fmt({k: v for k, v in e["data"].items() if k not in BLOB_FIELDS}))[:300]] for e in evs])
        # Decisions: the record's projection is the single source of signatures.
        decisions = list(rec["decisions"])
        body += '<h2 id="decisions">Decisions</h2>' + table(["At", "By", "Verdict", "Meaning", "Step", "Signature", "Payload", "Delegating rule"],
                                                             [[esc(d.get("at", "")[:19]), f"{esc(d.get('by'))} ({esc(d.get('by_type'))})", chip(d.get("verdict")), esc(d.get("meaning")),
                                                               esc(d.get("step")), f"<code>{esc(d.get('signature'))}</code>", f"<code>{esc(str(d.get('payload_digest', ''))[:12])}</code>",
                                                               esc(f"{d['delegating_rule'].get('frame')} line {d['delegating_rule'].get('line')}: {d['delegating_rule'].get('text')}") if d.get("delegating_rule") else ""]
                                                              for d in decisions], "No signatures yet.")
        # Track
        body += '<h2 id="track">Track</h2>'
        body += self._track_table(run["id"]) if run else "<p class='muted'>No run for this record.</p>"
        head = (f'<script src="{MERMAID_URL}"></script><script>if(window.mermaid){{mermaid.initialize({{startOnLoad:true,theme:matchMedia("(prefers-color-scheme: dark)").matches?"dark":"default"}})}}'
                f'else{{addEventListener("load",function(){{var d=document.getElementById("wf-diagram");if(d&&!window.mermaid){{d.hidden=true}}}})}}</script>')
        self._page(rec["number"], body, head=head)

    def _track_table(self, run_id: str) -> str:
        rows = []
        for ev in self.hub.runner.track(run_id):
            detail = {k: v for k, v in ev.items() if k not in ("seq", "at", "type", "run_id", "record_digest")}
            rows.append([esc(ev["seq"]), esc(ev["at"][:19]), f"<code>{esc(ev['type'])}</code>", f"<pre style='margin:0'>{esc(short(json.dumps(detail, indent=1, default=str), 4000))}</pre>"])
        return table(["#", "At", "Event", "Detail"], rows)

    def page_document(self):
        user = self.user
        bundle = self._need_release()
        rec = self.hub.kernel.get(self.arg)
        if rec is None or rec["type"] != "document":
            raise ShellError(f"No document {self.arg!r}.", 404)
        self._require_read(rec["folder"])
        loc, f = rec["locator"], rec["fields"]
        versions = self.hub.dms.versions(loc)
        locked = f.get("checked_out_by")
        immutable = f.get("doc_class") in ("evidence", "external")
        can = authz.check(bundle, user, "create", "document", rec["folder"])
        meta = [("title", esc(f.get("title"))), ("class", esc(f.get("doc_class"))), ("type", esc(f.get("doc_type"))),
                ("revision", esc(f"{f.get('revision', '')}.{f.get('minor', 0)}")), ("state", chip(rec["state"])),
                ("folder", f'<a href="{href("/files", user, folder=rec["folder"])}">{esc(rec["folder"])}</a>'), ("owner", esc(f.get("owner"))),
                ("author", esc(f.get("author"))), ("checked out by", esc(locked or "-")), ("file", esc(f.get("filename"))), ("mime", esc(f.get("mime"))),
                ("sha256", f"<code>{esc(f.get('content_sha'))}</code>"), ("labels", " ".join(f'<span class="chip">{esc(k)}: {esc(v)}</span>' for k, v in rec["labels"].items())),
                ("provenance", esc(fmt(f.get("provenance")))), ("effective from", esc(f.get("effective_from"))), ("review date", esc(f.get("review_date"))),
                ("approved by", esc(f.get("approved_by")))]
        body = f"<h1>{esc(rec['number'])} <span class='chip'>document</span> {chip(rec['state'])}</h1><div class='card'>{kv([(k, v) for k, v in meta if v])}</div>"
        body += "<h2>Versions</h2>" + table(["Version", "At", "By", "File", "Size", "SHA-256", "Note", ""],
                                             [[esc(v["version"]), esc(v["at"][:19]), esc(v["by"]), esc(v.get("filename")), esc(v.get("size")), f"<code>{esc(v['sha256'][:16])}</code>", esc(v.get("note")),
                                               f'<a href="{href("/download", user, doc=loc, v=v["version"])}">download</a>'] for v in versions], "No content yet.")
        if f.get("text"):
            body += f"<h2>Text</h2><pre>{esc(short(f['text'], 20000))}</pre>"
        body += "<h2>Check-out / check-in</h2><div class='card'>"
        if not can["allowed"]:
            body += f"<span class='why'>You cannot change documents here: {esc(can['why'])}</span>"
        elif immutable:
            body += f"<span class='why'>{esc(f.get('doc_class'))} documents are never edited; file a new one that supersedes it.</span>"
        elif rec["state"] not in EDITABLE_DOC_STATES:
            body += (f"<span class='why'>{esc(rec['number'])} is {esc(rec['state'])}; a controlled document changes only through a new revision "
                     f"(start document-control with the same title).</span>")
        else:
            if locked and locked != user:
                body += f"<span class='chip warn'>checked out by {esc(locked)}</span> "
            elif not locked:
                body += f"<form method='post' action='/checkout' class='inline'>{hidden_as(user)}<input type='hidden' name='doc' value='{esc(loc)}'><button>Check out</button></form> "
            body += f"""<form method="post" action="/checkin" enctype="multipart/form-data" class="inline">{hidden_as(user)}<input type="hidden" name="doc" value="{esc(loc)}">
<input type="file" name="file" required> <input name="note" placeholder="note" size="20"> <button>{'Check in' if locked == user else 'Upload a new version'}</button></form>"""
        body += "</div>"
        attached = [l for l in self.hub.kernel.links_of(loc) if l["direction"] == "in" and l["rel"] == "has-document"]
        body += "<h2>Attached to</h2>" + table(["Record"], [[self._record_link(l["target"])] for l in attached], "Not attached to any record.")
        other = [l for l in self.hub.kernel.links_of(loc) if not (l["direction"] == "in" and l["rel"] == "has-document")]
        if other:
            body += "<h2>Links</h2>" + table(["Relation", "Record", "Direction"], [[esc(l["label"]), self._record_link(l["target"]), esc(l["direction"])] for l in other])
        evs = self.hub.kernel.events(aggregate=loc)
        body += "<h2>Activity</h2>" + table(["#", "At", "Event", "By", "Detail"],
                                             [[esc(e["seq"]), esc(e["at"][:19]), f"<code>{esc(e['type'])}</code>", esc(e["by"]),
                                               esc(fmt({k: v for k, v in e["data"].items() if k not in BLOB_FIELDS}))[:300]] for e in evs])
        self._page(rec["number"], body)

    def page_download(self):
        self._need_release()
        ref = self.q.get("doc", [""])[0]
        rec = self.hub.kernel.get(ref)
        if rec is None or rec["type"] != "document":
            raise ShellError(f"No document {ref!r}.", 404)
        self._require_read(rec["folder"])
        v = self.q.get("v", [""])[0]
        content, version = self.hub.dms.content(rec["locator"], int(v) if v else None)
        self.send_response(200)
        self.send_header("Content-Type", version.get("mime") or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Disposition", f'attachment; filename="{quote(version.get("filename") or rec["number"])}"')
        self.end_headers()
        self.wfile.write(content)

    def page_runs(self):
        user = self.user
        self._need_release()
        if not self._is_member():
            return self._page("Runs", f"<h1>Runs</h1><div class='card'><b>{esc(user)} is not a member</b> of the organization; no runs are visible.</div>")
        rows = []
        for r in reversed(self._visible_runs()):
            if r.get("pending"):
                waiting = esc(", ".join(r["pending"]["assignees"])) + (f" (due {esc(r['pending']['due'])})" if r["pending"].get("due") else "")
                if r["pending"].get("kind") == "resubmit":
                    waiting = "rework by " + waiting
            elif r["status"] == "failed":
                waiting = f"<span class='why'>failed: {esc(short(self._failure_of(r['id']).get('reason', ''), 120))}</span>"
            else:
                waiting = ""
            rows.append([f'<a href="{href("/run/" + r["id"], user)}">{esc(r["id"])}</a>', esc(r["process"]), self._record_link(r["record"]) if r.get("record") else "",
                         esc(r["owner"]), chip(r["status"]), f"<code>{esc(r['release'][:12])}</code>", waiting])
        self._page("Runs", "<h1>Runs</h1><p class='why'>Runs whose record folder you may read, plus your own and those waiting for you.</p>"
                   + table(["Run", "Process", "Record", "Owner", "Status", "Release", "Waiting for"], rows, "No runs visible to you."))

    def page_run(self):
        user = self.user
        self._need_release()
        s = self.hub.runner.load(self.arg)
        if not self._run_visible(s):
            folder = (s.get("manifest") or {}).get("folder", "")
            why = self._can_read(folder)["why"] if folder else "no folder"
            raise ShellError(f"You cannot read run {s['id']}: {why}", 403)
        body = f"""<h1>Run {esc(s['id'])} {chip(s['status'])}</h1><div class="card">{esc(s['process'])} started by {esc(s['owner'])} at {esc(s['created_at'])};
record {self._record_link(s['record']) if s.get('record') else '-'}; pinned to release <code>{esc(s['release'])}</code>{'; parent run ' + esc(s['parent_run']) if s.get('parent_run') else ''}</div>"""
        if s.get("pending"):
            p = s["pending"]
            what = "Waiting for rework by" if p.get("kind") == "resubmit" else f"Waiting at <b>{esc(p['title'])}</b> for"
            body += f"<div class='card warn'>{what} {esc(', '.join(p['assignees']))}{' until ' + esc(p['due']) if p.get('due') else ''}<div class='why'>{esc(p['route_rule']['text'])}</div></div>"
        if s["status"] == "failed":
            ev = self._failure_of(s["id"])
            body += f"<div class='card crit'><b>Failed at {esc(ev.get('step', '?'))}:</b> {esc(ev.get('reason', ''))}"
            if user == s["owner"]:
                body += self._retry_cancel_forms(s)
            body += "</div>"
        if s.get("grant"):
            g = s["grant"]
            body += f"<div class='card'>Grant <code>{esc(g['id'])}</code> decided by {esc(g['decided_by'])} ({esc(g['by_type'])}) at {esc(g['issued_at'])}</div>"
        rows = []
        for step in s["manifest"]["steps"]:
            rec = s["steps"].get(step["id"])
            if not rec:
                rows.append([esc(step["title"]), esc(step["cog"]["id"]), chip("not reached"), "", ""])
                continue
            env = rec["envelope"]
            probs = "<br>".join(f"<span class='chip {'crit' if p['severity'] == 'error' else 'warn'}'>{esc(p['check'])}</span> {esc(p['detail'])}" for p in env["problems"])
            if env.get("error"):
                probs += f"<span class='chip crit'>{esc(env['error']['code'])}</span> {esc(env['error']['detail'])}"
            dec = ""
            if rec.get("decision"):
                d = rec["decision"]
                dec = f"{chip(d['verdict'])} by {esc(d['by'])} ({esc(d['by_type'])})<div class='why'>{esc(d['meaning'])}</div>"
                if d.get("delegating_rule"):
                    dec += f"<div class='why'>under {esc(d['delegating_rule']['frame'])} line {esc(d['delegating_rule']['line'])}: \"{esc(d['delegating_rule']['text'])}\"</div>"
            rows.append([esc(step["title"]), f"{esc(env['cog']['id'])} {esc(env['cog']['version'])}", chip(rec["status"]), probs, dec])
        body += "<h2>Steps</h2>" + table(["Step", "Cog", "Gate", "Problems", "Decision"], rows)
        body += "<h2>Track</h2>" + self._track_table(s["id"])
        self._page(f"Run {s['id']}", body)

    def page_releases(self):
        user = self.user
        cur = self.meta
        rows = []
        for m in reversed(self.hub.releases.list()):
            mark = "&#9679;" if cur and cur["digest"] == m["digest"] else ""
            frames = ", ".join(f"{f['name']}@{f['version']}" for f in m["frames"])
            rows.append([mark, f"<code>{esc(m['digest'][:20])}</code>", esc(m["effective_from"]), esc(m["released_by"]), f"<code>{esc((m['parent'] or '-')[:12])}</code>", esc(frames)])
        if not self._is_member():
            return self._page("Releases", f"<h1>Policy releases</h1><div class='card'><b>{esc(user)} is not a member</b> of the organization; the release is not shown.</div>")
        body = "<h1>Policy releases</h1>" + table(["", "Digest", "Effective from", "Released by", "Parent", "Frames"], rows, "No release yet.")
        if self.bundle:
            bundle = self.bundle
            rrows = []
            for r in bundle["routing"]:
                c = r["condition"]
                cond = "default" if not c else (f"{c['field']} {c['op']} {c['value']} {c.get('currency', '')}" if "field" in c else fmt(c))
                rrows.append([esc(r["process"]), esc(r["step"]), esc(cond), esc(r["assignee"]),
                              f"<a class='why' href='{href('/frame/' + r['source']['frame'], user)}#L{r['source']['line']}'>{esc(r['source']['frame'])}:{esc(r['source']['line'])}</a>"])
            body += "<h2>Routing in effect</h2>" + table(["Process", "Checkpoint", "Condition", "Goes to", "Source"], rrows)
            body += "<h2>Cog approval criteria</h2>" + table(["Cog", "Process", "Up to", "Conditions", "Source"],
                                                              [[esc(c["approver"]), esc(c["process"]), f"{esc(c.get('max_amount') or '-')} {esc(c.get('currency') or '')}", esc(", ".join(c["conditions"])),
                                                                f"<span class='why'>{esc(c['source']['frame'])}:{esc(c['source']['line'])}</span>"] for c in bundle["approval_criteria"]])
            body += "<h2>Workflows</h2>"
            for name, wf in bundle["workflows"].items():
                cps = "; ".join(f"{c['title']} [{c['role']}] -> {c['state']}" + (f", SLA {c['sla_days']} days" if c.get("sla_days") else "") for c in wf["checkpoints"]) or "no checkpoints"
                body += f"<div class='card'><b>{esc(name)}</b> <span class='chip'>{esc(wf['record_type'])}</span> closes as {chip(wf['terminal'])}<br>" \
                        + " &rarr; ".join(chip(s) for s in wf["states"]) + f"<div class='why'>Checkpoints: {esc(cps)}</div></div>"
            body += "<h2>Thresholds</h2>" + table(["Name", "Value", "Unit"], [[esc(k), esc(v["value"]), esc(v["unit"])] for k, v in bundle["thresholds"].items()])
        self._page("Releases", body)

    def page_frames(self):
        user = self.user
        rows = [[f"<a href='{href('/frame/' + f.name, user)}'>{esc(f.name)}</a>", esc(f.version), esc(f.scope), esc(f.maintainer), esc(", ".join(f.inherits) or "-"), esc(f.description)]
                for f in self.hub.frames.frames.values()]
        self._page("Frames", "<h1>Frames</h1>" + table(["Frame", "Version", "Scope", "Maintainer", "Inherits", "Description"], rows))

    def page_frame(self):
        try:
            frame = self.hub.frames.get(self.arg)
        except Exception:
            raise ShellError(f"No Frame {self.arg!r}.", 404) from None
        lines = "".join(f"<tr id='L{i}'><td class='why'><a href='#L{i}'>{i}</a></td><td><pre style='margin:0;background:none;padding:0'>{esc(line)}</pre></td></tr>"
                        for i, line in enumerate(frame.file_lines, start=1))
        body = f"<h1>{esc(frame.name)} <span class='chip'>v{esc(frame.version)}</span></h1><div class='card'>{esc(frame.description)}<br><span class='why'>scope {esc(frame.scope)}; maintained by {esc(frame.maintainer)}; visibility {esc(frame.visibility)}; inherits {esc(', '.join(frame.inherits) or 'nothing')}</span></div><table>{lines}</table>"
        self._page(frame.name, body)

    def page_why(self):
        user = self.user
        principal = (self.q.get("principal", [""])[0] or "").strip() or user
        action = self.q.get("action", ["create"])[0] or "create"
        obj = self.q.get("object", ["bill"])[0] or "bill"
        folder = self.q.get("folder", ["Finance/Payables"])[0] or "Finance/Payables"
        d = self.hub.why(principal, action, obj, folder)
        objects = sorted({t.get("object", n) for n, t in self.hub.kernel.types.items()} | {"*"})
        folders = self.bundle["folders"] if self.bundle else [folder]
        members = sorted(self.bundle["members"]) if self.bundle else []
        if principal not in members:
            members = [principal] + members
        body = f"""<h1>Why?</h1><form method="get" action="/why" class="card">{hidden_as(user)}
May <select name="principal">{''.join(f'<option value="{esc(m)}"{" selected" if m == principal else ""}>{esc(m)}</option>' for m in members)}</select>
<select name="action">{''.join(f'<option{" selected" if a == action else ""}>{a}</option>' for a in ("create", "read", "approve", "update", "delete"))}</select>
<select name="object">{''.join(f'<option value="{esc(o)}"{" selected" if o == obj else ""}>{esc(o)}</option>' for o in objects)}</select> in
<select name="folder">{''.join(f'<option value="{esc(f)}"{" selected" if f == folder else ""}>{esc(f)}</option>' for f in folders)}</select> <button>Ask</button></form>
<div class="card">May <b>{esc(principal)}</b> {esc(action)} {esc(obj)} in {esc(folder)}? {chip('allowed' if d['allowed'] else 'denied')} {esc(d['why'])}</div>"""
        if d.get("rule"):
            src = d["rule"]["source"]
            body += f"<p><a href='{href('/frame/' + src['frame'], user)}#L{src['line']}'>Open {esc(src['frame'])} at line {src['line']}</a></p>"
        self._page("Why", body)

    def page_admin(self):
        bundle = self.bundle or {"members": {}}
        body = "<h1>Admin</h1><h2>Cog catalog</h2>" + table(["Cog", "Version", "Kind", "Reaches", "Tasks", "Description"],
                                                            [[esc(c["id"]), esc(c["version"]), esc(c["kind"]), esc(", ".join(c["reaches"])), esc(", ".join(c["tasks"])), esc(c["description"])]
                                                             for c in self.hub.registry.cards()])
        body += "<h2>Members</h2>" + table(["Principal", "Type", "Roles", "Source"],
                                            [[esc(p), esc(m["type"]), " ".join(f'<span class="chip">{esc(r)}</span>' for r in m["roles"]),
                                              f"<span class='why'>{esc(m.get('source', {}).get('frame', ''))}:{esc(m.get('source', {}).get('line', ''))}</span>"] for p, m in bundle["members"].items()],
                                            "No release in effect.")
        body += "<h2>Record types</h2>" + table(["Type", "Prefix", "Object", "Fields", "Required by state", "System fields", "Decision fields", "Retention"],
                                                 [[esc(n), esc(t["prefix"]) + ("-YYYY" if t.get("yearly") else ""), esc(t["object"]), esc(", ".join(t["fields"])),
                                                   esc("; ".join(f"{s}: {', '.join(v)}" for s, v in t.get("required_by_state", {}).items()) or "-"),
                                                   esc(", ".join(t.get("system_fields") or []) or "-"), esc(", ".join(t.get("decision_fields") or []) or "-"), esc(t.get("retention"))]
                                                  for n, t in self.hub.kernel.types.items()])
        body += "<h2>Retention classes</h2>" + table(["Class", "Years", "Evidence a Track must hold"], [[esc(n), esc(r["years"]), esc(", ".join(r["evidence"]))] for n, r in self.hub.kernel.retention.items()])
        body += "<h2>Link vocabulary</h2>" + table(["Relation", "Inverse"], [[esc(k), esc(v)] for k, v in self.hub.kernel.link_vocab.items()])
        body += "<h2>Document classes</h2><p>" + " ".join(f'<span class="chip">{esc(c)}</span>' for c in CLASSES) + "</p>"
        self._page("Admin", body)

    def page_conformance(self):
        try:
            from . import profile  # type: ignore
        except ImportError:
            profile = None
        if profile is None or not hasattr(profile, "conformance"):
            return self._page("Conformance", "<h1>Conformance</h1><div class='card'>The profile module (<code>hubproto.profile</code>) is not installed; no conformance checklist is available.</div>")
        result = profile.conformance(self.hub)
        self._page("Conformance", "<h1>Conformance</h1>" + render_conformance(result))

    # --- actions ------------------------------------------------------------
    def _f(self, name: str, default: str = "") -> str:
        return (self.form.get(name) or default).strip()

    def _file(self) -> tuple[str, bytes]:
        for _, filename, content in self.files:
            if filename:
                return filename, content
        return "", b""

    def _attachments(self) -> list[dict]:
        """Every uploaded file as a submission attachment: a controlled document typed by its extension."""
        return [{"name": filename, "content_b64": base64.b64encode(content).decode("ascii"), "doc_class": "controlled",
                 "doc_type": doc_type_for(filename)} for _, filename, content in self.files if filename]

    def _json_fields(self) -> dict:
        raw = self._f("json")
        if not raw:
            return {}
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ShellError(f"The JSON fields do not parse: {e}") from None
        if not isinstance(record, dict):
            raise ShellError("The JSON fields must be an object.")
        return record

    def _collect_fields(self, rt: str, current: dict | None = None) -> dict:
        """The record fields from a Start or resubmit form: the type's inputs, any other declared field, the JSON box.
        Unknown and system-owned fields are refused with the list of what is accepted."""
        td = self.hub.kernel.types.get(rt, {})
        specs = td.get("fields", {})
        system = set(td.get("system_fields") or [])
        declared = {name: (kind, spec) for name, kind, spec in self._start_spec(rt)}
        record: dict = {}
        for k, v in self._json_fields().items():
            if k == "attachments":
                record[k] = v
                continue
            if k in system and k not in declared:
                raise ShellError(f"{k} is set by the system, not at submission; leave it out of the JSON.")
            if k not in specs and k not in declared:
                raise ShellError(f"{rt} has no field {k!r}; its fields are {', '.join(specs)}.")
            record[k] = v
        for k, v in self.form.items():
            if k in RESERVED_KEYS or not v.strip():
                continue
            if k in declared:
                kind, spec = declared[k]
            elif k in specs and k not in system:
                kind, spec = None, specs[k]
            elif k in system:
                raise ShellError(f"{k} is set by the system, not at submission.")
            else:
                raise ShellError(f"{rt} has no field {k!r}; its fields are {', '.join(specs)}.")
            value = coerce(k, spec, v, kind)
            if value is None:
                continue
            if current is not None and current.get(k) == value:
                continue  # unchanged on a resubmit
            record[k] = value
        return record

    def post_start(self):
        user = self.user
        bundle = self._need_release()
        process = self._f("process")
        if process not in bundle["ops"]:
            raise ShellError(f"No process {process!r} in the release.", 404)
        rt = bundle["ops"][process].get("record_type", "")
        record = self._collect_fields(rt)
        attachments = list(record.get("attachments") or []) + self._attachments()
        if attachments:
            record["attachments"] = attachments
        run_id = self.hub.runner.submit(process, {"record": record}, owner=user)
        state = self.hub.runner.load(run_id)
        log_line(f"run started run={run_id} process={process} by={user} record={state.get('record')} status={state['status']}")
        self._redirect(href("/run/" + run_id, user))

    def post_decide(self):
        user = self.user
        self._need_release()
        run_id = self._f("run")
        verdict = self._f("verdict")
        meaning = self._f("meaning")
        state = self.hub.runner.load(run_id)
        fields: dict = {}
        reserved = {"as", "run", "verdict", "meaning", "digest"}
        given = {k: v for k, v in self.form.items() if k not in reserved and v.strip()}
        if given and state.get("pending") and state["pending"].get("kind", "decision") == "decision" and state.get("record"):
            rec = self.hub.kernel.get(state["record"])
            td = self.hub.kernel.types.get(rec["type"], {}).get("fields", {}) if rec else {}
            allowed = self.hub.runner.allowed_decision_fields(state)
            refused = sorted(k for k in given if k not in allowed)
            if refused:
                raise ShellError(f"A decision may not set {', '.join(refused)}; this Gate accepts {', '.join(allowed) or 'no fields'}.")
            for k, v in given.items():
                value = coerce(k, td.get(k), v)
                if value is not None:
                    fields[k] = value
        result = self.hub.runner.decide(run_id, user, verdict, meaning=meaning, payload_digest=self._f("digest") or None,
                                        fields=fields or None)
        log_line(f"decision run={run_id} record={state.get('record')} by={user} verdict={verdict} fields={sorted(fields)} "
                 f"meaning={meaning!r} status={result['status']}")
        rec_loc = state.get("record")
        self._redirect(href("/record/" + quote(rec_loc, safe=":"), user) if rec_loc else href("/run/" + run_id, user))

    def post_resubmit(self):
        user = self.user
        self._need_release()
        run_id = self._f("run")
        state = self.hub.runner.load(run_id)
        rec = self.hub.kernel.get(state["record"]) if state.get("record") else None
        rt = rec["type"] if rec else state["workflow"]["record_type"]
        fields = self._collect_fields(rt, current=rec["fields"] if rec else {})
        attachments = list(fields.pop("attachments", None) or []) + self._attachments()
        note = self._f("note")
        result = self.hub.runner.resubmit(run_id, user, fields=fields or None, attachments=attachments or None, note=note)
        log_line(f"resubmit run={run_id} record={state.get('record')} by={user} fields={sorted(fields)} attachments={len(attachments)} status={result['status']}")
        self._redirect(href("/record/" + quote(state["record"], safe=":"), user) if state.get("record") else href("/run/" + run_id, user))

    def post_retry(self):
        user = self.user
        self._need_release()
        run_id = self._f("run")
        result = self.hub.runner.retry(run_id, user, note=self._f("note"))
        log_line(f"retry run={run_id} record={result.get('record')} by={user} status={result['status']}")
        self._redirect(href("/record/" + quote(result["record"], safe=":"), user) if result.get("record") else href("/run/" + run_id, user))

    def post_cancel(self):
        user = self.user
        self._need_release()
        run_id = self._f("run")
        result = self.hub.runner.cancel(run_id, user, reason=self._f("reason"))
        log_line(f"cancel run={run_id} record={result.get('record')} by={user} reason={self._f('reason')!r}")
        self._redirect(href("/record/" + quote(result["record"], safe=":"), user) if result.get("record") else href("/run/" + run_id, user))

    def post_msg(self):
        user = self.user
        bundle = self._need_release()
        to = self._f("to")
        if not to:
            raise ShellError("Choose a recipient.")
        if user not in bundle["members"]:
            raise ShellError(f"{user} is not a member and cannot send messages.", 403)
        if to not in bundle["members"]:
            raise ShellError(f"{to} is not a member; messages go to members only.", 404)
        self.hub.send(user, to, self._f("subject") or "(no subject)", self._f("body"), channel=self._f("channel", "dm") or "dm")
        self._redirect(href("/inbox", user))

    def post_upload(self):
        user = self.user
        bundle = self._need_release()
        folder = self._f("folder")
        if folder not in bundle["folders"]:
            raise ShellError(f"No folder {folder!r} in the release.", 404)
        d = may_file(bundle, user, folder)
        if not d["allowed"]:
            raise ShellError(f"You cannot file documents in {folder}: {d['why']}", 403)
        filename, content = self._file()
        if not filename:
            raise ShellError("Choose a file to upload.")
        attach_to = self._f("attach_to") or None
        self.hub.dms.intake(folder=folder, by=user, content=content, filename=filename, channel="shell", sender=user,
                            attach_to=attach_to, title=self._f("title"))
        self._redirect(href("/files", user, folder=folder))

    def post_attach(self):
        user = self.user
        bundle = self._need_release()
        rec = self.hub.kernel.require(self._f("record"))
        d = may_file(bundle, user, rec["folder"])
        if not d["allowed"]:
            raise ShellError(f"You cannot attach documents in {rec['folder']}: {d['why']}", 403)
        filename, content = self._file()
        if filename:
            self.hub.dms.intake(folder=rec["folder"], by=user, content=content, filename=filename, channel="shell", sender=user,
                                attach_to=rec["locator"], title=self._f("title"))
        elif self._f("doc"):
            doc = self.hub.kernel.require(self._f("doc"))
            self._require_read(doc["folder"])
            self.hub.dms.attach(rec["locator"], doc["locator"], by=user)
        else:
            raise ShellError("Choose a file to upload or an existing document to attach.")
        self._redirect(href("/record/" + quote(rec["locator"], safe=":"), user) + "#documents")

    def _doc_for_change(self) -> dict:
        bundle = self._need_release()
        rec = self.hub.kernel.require(self._f("doc"))
        if rec["type"] != "document":
            raise ShellError(f"{rec['number']} is not a document.")
        d = authz.check(bundle, self.user, "create", "document", rec["folder"])
        if not d["allowed"]:
            raise ShellError(f"You cannot change documents in {rec['folder']}: {d['why']}", 403)
        return rec

    def post_checkout(self):
        rec = self._doc_for_change()
        self.hub.dms.checkout(rec["locator"], by=self.user)
        self._redirect(href("/document/" + quote(rec["locator"], safe=":"), self.user))

    def post_checkin(self):
        rec = self._doc_for_change()
        filename, content = self._file()
        if not filename:
            raise ShellError("Choose a file to check in.")
        self.hub.dms.checkin(rec["locator"], content, by=self.user, filename=filename, note=self._f("note") or "check-in")
        self._redirect(href("/document/" + quote(rec["locator"], safe=":"), self.user))

    def post_link(self):
        user = self.user
        bundle = self._need_release()
        rec = self.hub.kernel.require(self._f("record"))
        d = may_file(bundle, user, rec["folder"])
        if not d["allowed"]:
            raise ShellError(f"You cannot link records in {rec['folder']}: {d['why']}", 403)
        self.hub.kernel.link(rec["locator"], self._f("rel"), self._f("target"), by=user)
        self._redirect(href("/record/" + quote(rec["locator"], safe=":"), user) + "#links")

    def post_snapshot(self):
        user = self.user
        self._need_release()
        rec = self.hub.kernel.require(self._f("record"))
        self._require_read(rec["folder"])
        doc = self.hub.dms.render_snapshot(rec["locator"], by=user)
        self._redirect(href("/document/" + quote(doc["locator"], safe=":"), user))

    def post_hold(self):
        user = self.user
        bundle = self._need_release()
        rec = self.hub.kernel.require(self._f("record"))
        obj = self.hub.kernel.types.get(rec["type"], {}).get("object", rec["type"])
        d = authz.check(bundle, user, "approve", obj, rec["folder"])
        if not d["allowed"]:
            raise ShellError(f"A hold needs an approve permission: {d['why']}", 403)
        on = self._f("on", "1") in ("1", "true", "on")
        self.hub.kernel.hold(rec["locator"], on, self._f("reason"), by=user)
        log_line(f"hold record={rec['locator']} on={on} by={user} reason={self._f('reason')!r}")
        self._redirect(href("/record/" + quote(rec["locator"], safe=":"), user))


# ---------------------------------------------------------------- serving
def _prepare(hub: Hub) -> None:
    """Handlers run on server threads: reopen the kernel's SQLite connection so any thread may use it
    (hub access is serialized by Handler.lock; body reads and /healthz happen outside it)."""
    try:
        hub.kernel.db.close()
    except Exception:
        pass
    hub.kernel.db = sqlite3.connect(str(hub.kernel.path), check_same_thread=False)
    hub.kernel._cache = None


def max_upload_from_env() -> int:
    try:
        return int(os.environ.get("HUBPROTO_MAX_UPLOAD") or DEFAULT_MAX_UPLOAD)
    except ValueError:
        return DEFAULT_MAX_UPLOAD


def make_server(hub: Hub, host: str = "127.0.0.1", port: int = 8080, max_upload: int | None = None) -> ThreadingHTTPServer:
    _prepare(hub)
    handler = type("HubHandler", (Handler,), {"hub": hub, "max_upload": max_upload or max_upload_from_env()})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve_in_thread(hub: Hub, host: str = "127.0.0.1", port: int = 0, max_upload: int | None = None) -> ThreadingHTTPServer:
    """Start the shell on a daemon thread (port 0 picks a free port); returns the server (``server.server_address``)."""
    server = make_server(hub, host, port, max_upload=max_upload)
    thread = threading.Thread(target=server.serve_forever, name="hub-shell", daemon=True)
    thread.start()
    return server


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def serve(hub: Hub, host: str = "127.0.0.1", port: int = 8080) -> None:
    server = make_server(hub, host, port)
    log_line(f"workspace shell at http://{host}:{server.server_address[1]}/?as={DEFAULT_USER} (max upload {server.RequestHandlerClass.max_upload:,} bytes)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
