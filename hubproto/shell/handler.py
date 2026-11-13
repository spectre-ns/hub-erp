"""Request plumbing for the workspace shell: identity, body reading, error handling that re-renders the form the
error came from, the page frame (skip link, breadcrumbs, role-trimmed rail), one-use form tokens and the
lookups every page shares.

Request handling: ``/healthz`` is answered without the hub lock; a POST body is validated (``Content-Length``
present, non-negative, at most ``HUBPROTO_MAX_UPLOAD`` bytes) and read before the lock is taken, so one stalled or
malformed client never freezes the shell for everyone. An oversize body is drained up to the limit and answered
with a readable 413; a stalled body is answered with a 408. One log line per request and one per decision go to
stdout.
"""

from __future__ import annotations

import email.policy
import os
import socket
import sys
import threading
import time
import traceback
import uuid
from datetime import date
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from .. import authz
from ..app import Hub
from ..frames import FrameError, FrameStore
from ..kernel import KernelGuardError
from ..runner import RunError
from .apps import app_for_route, app_map, appbar_html, avatar, launcher_html
from .css import CSS
from .forms import ShellError, error_field
from .icons import icon
from .text import clean, esc, friendly, humanize, short, summary_of_record, tag, type_name, version_label, why_sentence

DEFAULT_MAX_UPLOAD = 25 * 1024 * 1024
RAIL_MAIN = [("Home", "/", "home"), ("Inbox", "/inbox", "inbox"), ("Documents", "/files", "folder"), ("Boards", "/boards", "kanban"), ("People", "/people", "users"),
             ("Why?", "/why", "help")]
RAIL_POLICY = [("Runs", "/runs", "activity"), ("Policy versions", "/releases", "layers"), ("Policies", "/frames", "book"),
               ("Conformance", "/conformance", "check-circle"), ("People and roles", "/admin", "users")]
# Routes the identity switcher may stay on (anything else goes home).
STAY_ROUTES = ("/", "/inbox", "/files", "/documents", "/templates", "/change-orders", "/record-types", "/boards", "/runs", "/releases", "/frames", "/why", "/admin", "/conformance", "/people", "/suppliers",
               "/settings", "/inventory", "/inventory/locations", "/workflows")
STAY_PREFIXES = ("/record/", "/document/", "/run/", "/frame/", "/release/", "/items/", "/app/", "/user/", "/settings/", "/workflows/")
# The small script: tabs show one panel at a time (every panel is visible without it) and the print button prints.
SCRIPT = """document.documentElement.classList.add('js');
addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('[data-theme-toggle]');if(!b)return;var r=document.documentElement;var cur=r.getAttribute('data-theme');if(cur==='auto')cur=getComputedStyle(r).colorScheme==='dark'?'dark':'light';var t=cur==='dark'?'light':'dark';r.setAttribute('data-theme',t);try{localStorage.setItem('hub-theme',t)}catch(_){}});
addEventListener('DOMContentLoaded',function(){var al=document.querySelector('main [role=alert], main .err');if(al&&!location.hash){al.scrollIntoView({block:'center'})}
var mx=function(){var t=document.querySelector('.mx-toggle');if(t)t.classList.toggle('mini',scrollY>40)};addEventListener('scroll',mx,{passive:true});mx()});
addEventListener('DOMContentLoaded',function(){var tabs=document.querySelectorAll('.tabs a[data-tab]');
function show(id){tabs.forEach(function(a){var on=a.getAttribute('data-tab')===id;a.setAttribute('aria-selected',on?'true':'false');
var p=document.getElementById(a.getAttribute('data-tab'));if(p){p.classList.toggle('active',on)}})}
function go(){if(!tabs.length)return;var h=location.hash.replace('#','');var t=h&&document.getElementById(h);var p=t&&(t.classList.contains('tab-panel')?t:t.closest('.tab-panel'));
show(p?p.id:tabs[0].getAttribute('data-tab'));if(t&&t!==p){setTimeout(function(){t.scrollIntoView()},0)}}
go();addEventListener('hashchange',go);
var all=document.querySelector('input[data-select-all]');if(all){all.addEventListener('change',function(){document.querySelectorAll('input[name=sel]').forEach(function(c){c.checked=all.checked})})}
addEventListener('DOMContentLoaded',function(){document.querySelectorAll('.statusbar').forEach(function(b){var c=b.querySelector('[aria-current]');if(c&&b.scrollWidth>b.clientWidth)b.scrollLeft=c.offsetLeft-b.clientWidth/2+c.clientWidth/2})});function hotkeysOff(){try{return localStorage.getItem('hub.hotkeys')==='off'}catch(e){return false}}var leader=false,leaderTimer=null;function armLeader(on){leader=on;document.body.classList.toggle('leader',on);clearTimeout(leaderTimer);if(on)leaderTimer=setTimeout(function(){armLeader(false)},2000)}
addEventListener('keydown',function(ev){if(ev.ctrlKey||ev.metaKey||ev.altKey)return;var t=ev.target;if(t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA'||t.tagName==='SELECT'||t.isContentEditable))return;
var k=ev.key.toLowerCase();if(!leader){if(k==='o'&&document.querySelector('[data-hotkey]')&&!hotkeysOff()){armLeader(true);ev.preventDefault()}return}
armLeader(false);var el=null;document.querySelectorAll('[data-hotkey]').forEach(function(b){if(!el&&b.getAttribute('data-hotkey').toLowerCase()===k)el=b});if(!el)return;ev.preventDefault();
if(el.tagName==='A'&&el.getAttribute('href')&&el.getAttribute('href').charAt(0)==='#'){location.hash=el.getAttribute('href');var h=document.getElementById(el.getAttribute('href').slice(1));if(h){var f=h.querySelector('input,textarea,select,button');if(f)f.focus()}}else{el.focus();el.classList.add('hk-armed');var st=document.getElementById('hk-status');if(st)st.textContent='Press Enter to '+(el.textContent||'').trim()+', or Escape.';setTimeout(function(){el.classList.remove('hk-armed')},4000)}});document.querySelectorAll('[data-hotkeys-toggle]').forEach(function(b){var paint=function(){b.textContent=hotkeysOff()?'Keyboard shortcuts: off':'Keyboard shortcuts: on';b.setAttribute('aria-pressed',hotkeysOff()?'false':'true')};paint();b.addEventListener('click',function(){try{localStorage.setItem('hub.hotkeys',hotkeysOff()?'on':'off')}catch(e){}paint()})});});"""
ONCE_KEEP = 2000  # one-use tokens remembered (the oldest are forgotten first)


def log_line(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def href(path: str, user: str, **params) -> str:
    q = {"as": user}
    q.update({k: v for k, v in params.items() if v not in (None, "")})
    return f"{path}?{urlencode(q)}"


def hidden_as(user: str) -> str:
    return f'<input type="hidden" name="as" value="{esc(user)}">'


def table(headers: list[str], rows: list[list[str]], empty: str = "Nothing here.", row_header: bool = False) -> str:
    """A data table in an overflow container, with column headers scoped and, when asked, the first cell a row header."""
    if not rows:
        return f'<p class="muted">{esc(empty)}</p>'
    head = "".join(f'<th scope="col">{esc(h)}</th>' for h in headers)
    body = ""
    for row in rows:
        cells = "".join((f'<th scope="row">{c}</th>' if (row_header and i == 0) else f"<td>{c}</td>") for i, c in enumerate(row))
        body += f"<tr>{cells}</tr>"
    return f'<div class="tbl"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def kv(items: list[tuple[str, str]]) -> str:
    if not items:
        return ""
    return '<dl class="kv">' + "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in items if v not in (None, "")) + "</dl>"


def details(summary: str, body: str, open_: bool = False, cls: str = "") -> str:
    attrs = (" open" if open_ else "") + (f' class="{cls}"' if cls else "")
    return f"<details{attrs}><summary>{summary}</summary>{body}</details>"


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
        cur = ' aria-current="page"' if path == current else ""
        out += f'<li><a{cur} href="{href("/files", user, folder=path)}">{esc(name)}</a>'
        out += (render_tree(child, path, user, current) if child else "") + "</li>"
    return out + "</ul>"


class BaseHandler(BaseHTTPRequestHandler):
    hub: Hub = None  # set by make_server()
    lock = threading.RLock()
    server_version = "hubproto-shell/0.2"
    max_upload = DEFAULT_MAX_UPLOAD
    timeout = 30  # a stalled client gives up its thread (and gets a 408), never the lock
    _once_used: dict[str, str] = {}  # one-use form tokens -> where their first submission landed
    _once_lock = threading.Lock()
    _frames_stamp: float = -1.0

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
        self.multi: dict[str, list[str]] = {}  # every value of a repeated field (the list view's selection)
        self.files: list[tuple[str, str, bytes]] = []
        self.user = clean(self.q.get("as", [""])[0] or "").strip()
        self.route = url.path.rstrip("/") or "/"
        self.arg = ""
        self.meta = None
        self.bundle = None
        self.err: tuple[str, str | None] | None = None  # (message, field) when re-rendering a form after a refusal
        self.vals: dict[str, str] = {}  # the posted values to keep on that form
        self.today = date.today().isoformat()
        self._force_status = 0
        self._err_shown = False
        try:
            if self.route == "/healthz":
                q = getattr(self.hub, "queue", None)
                return self._text("ok; cog queue stalled" if q is not None and q.health().get("stalled") else "ok")
            if self.route == "/mcp":
                self.raw_body = self._read_raw() if method == "POST" else b""
                with self.lock:
                    self.meta = self.hub.releases.current()
                    self.bundle = self.hub.releases.bundle(self.meta["digest"]) if self.meta else None
                    return self.handle_mcp(method)
            if self.route.startswith("/wopi/files/"):
                self.raw_body = self._read_raw() if method == "POST" else b""
                with self.lock:
                    self.meta = self.hub.releases.current()
                    self.bundle = self.hub.releases.bundle(self.meta["digest"]) if self.meta else None
                    return self.handle_wopi(method)
            if method == "POST":
                self._read_body()  # validated and read outside the lock
                self.user = clean(self.form.get("as") or self.q.get("as", [""])[0] or "").strip()
                if not self.user:
                    raise ShellError("Say who you are: choose your name from 'Acting as' before sending a form.", 400)
            with self.lock:
                self._refresh_frames()
                self.meta = self.hub.releases.current()
                self.bundle = self.hub.releases.bundle(self.meta["digest"]) if self.meta else None
                if method == "GET" and not self.user and self.route not in ("/download", "/api/config"):
                    return self.page_whoami()
                if method == "POST" and self._viewing_cog():
                    raise ShellError(f"You are watching {self.user}, a cog: it decides, files and answers on its own within the policy, "
                                     f"and nobody acts in its name from here. Choose your own name under 'Acting as' to act.", 403)
                self._dispatch(method)
        except ShellError as e:
            self._error_page(str(e), e.status)
        finally:
            ms = int((time.monotonic() - t0) * 1000)
            log_line(f"request method={method} path={url.path} as={self.user} status={self._status} ms={ms}")

    def _dispatch(self, method: str) -> None:
        try:
            handler = self._route(method)
            if handler is None:
                raise ShellError("There is no such page." if method == "GET" else "There is no such action.", 404)
            return handler()
        except (RunError, KernelGuardError, ShellError) as e:
            if method == "POST":
                return self._after_post_error(e)
            if isinstance(e, ShellError):
                raise
            return self._error_page(friendly(str(e)), 400)
        except Exception as e:  # readable, never a blank 500; the traceback goes to the log, not the browser
            err_id = uuid.uuid4().hex[:8]
            log_line(f"error id={err_id} {type(e).__name__}: {e}\n{traceback.format_exc()}")
            detail = f"<pre>{esc(traceback.format_exc())}</pre>" if os.environ.get("HUBPROTO_DEBUG") else ""
            return self._error_page(f"Something went wrong on the server (error id {err_id}; the log has the details).", 500, extra=detail)

    def _route(self, method: str):
        r = self.route
        if method == "GET":
            fixed = {"/chat-thread": self.page_chat_thread, "/queue-status": self.page_queue_status, "/queue": self.page_queue, "/proposals": self.page_proposals, "/": self.page_home, "/files": self.page_files, "/documents": self.page_files, "/boards": self.page_boards, "/inbox": self.page_inbox,
                     "/runs": self.page_runs, "/releases": self.page_releases, "/frames": self.page_frames, "/why": self.page_why,
                     "/admin": self.page_admin, "/conformance": self.page_conformance, "/download": self.page_download,
                     "/start": self.page_start, "/people": self.page_people, "/suppliers": self.page_suppliers, "/settings": self.page_settings, "/templates": self.page_templates,
                     "/change-orders": self.page_change_orders, "/automation": self.page_automation,
                     "/automation-status": self.page_automation_status, "/scopes": self.page_scopes, "/forms": self.page_forms,
                     "/workflows": self.page_workflows, "/record-types": self.page_record_types, "/api/config": self.page_api_config,
                     "/download-marked": self.page_download_marked}
            if r in fixed:
                return fixed[r]
            for prefix, fn in (("/record/", self.page_record), ("/document/", self.page_document), ("/doc/", self.page_document),
                               ("/run/", self.page_run), ("/frame/", self.page_frame), ("/release/", self.page_release),
                               ("/edit/", self.page_edit), ("/compare/", self.page_compare), ("/editor-replies/", self.page_editor_replies), ("/cog-seat/", self.page_cog_seat), ("/frame-doc/", self.page_frame_doc),
                               ("/text-edit/", self.page_text_edit), ("/frame-check/", self.page_frame_check), ("/proposal/", self.page_proposal),
                               ("/agent-run/", self.page_agent_run), ("/items/", self.page_items), ("/app/", self.page_app), ("/user/", self.page_user),
                               ("/settings/", self.page_settings), ("/automation/", self.page_automation), ("/board/", self.page_cog_board),
                               ("/forms/", self.page_forms), ("/forms-preview/", self.page_forms_preview),
                               ("/workflows/", self.page_workflows), ("/workflow-panel/", self.page_workflow_panel)):
                if r.startswith(prefix) and len(r) > len(prefix):
                    self.arg = r[len(prefix):]
                    return fn
            return self.inventory_route(method, r)
        posts = {"/sops-convert": self.post_sops_convert, "/folder-new": self.post_folder_new, "/folder-remove": self.post_folder_remove, "/forms-check": self.post_forms_check, "/forms-save": self.post_forms_save, "/forms-discard": self.post_forms_discard,
                 "/forms-carry": self.post_forms_carry, "/forms-uncarry": self.post_forms_uncarry,
                 "/workflow-op": self.post_workflow_op, "/workflow-discard": self.post_workflow_discard, "/workflow-carry": self.post_workflow_carry,
                 "/workflow-uncarry": self.post_workflow_uncarry,
                 "/withdraw-record": self.post_withdraw_record, "/automation-retry": self.post_automation_retry, "/automation-start": self.post_automation_start, "/start": self.post_start, "/decide": self.post_decide, "/msg": self.post_msg, "/attach": self.post_attach,
                 "/finish-edit": self.post_finish_edit, "/new-document": self.post_new_document, "/working-copy": self.post_working_copy, "/apply-replies": self.post_apply_replies, "/start-on": self.post_start_on, "/chat": self.post_chat, "/chat-clear": self.post_chat_clear, "/frame-draft": self.post_frame_draft, "/text-save": self.post_text_save,
                 "/frame-propose": self.post_frame_propose, "/frame-withdraw": self.post_frame_withdraw, "/proposal-review": self.post_proposal_review, "/proposal-release": self.post_proposal_release,
                 "/agent-task": self.post_agent_task, "/settings-edit": self.post_settings_edit, "/settings-check": self.post_settings_check,
                 "/settings-propose": self.post_settings_propose, "/settings-discard": self.post_settings_discard, "/queue-withdraw": self.post_queue_withdraw, "/template-revise": self.post_template_revise,
                 "/co-new": self.post_co_new, "/co-add-sop": self.post_co_add_sop, "/co-remove-sop": self.post_co_remove_sop,
                 "/co-implements": self.post_co_implements, "/co-send": self.post_co_send, "/co-details": self.post_co_details, "/co-review": self.post_co_review, "/sop-revise": self.post_sop_revise,
                 "/co-reviewer-add": self.post_co_reviewer_add, "/co-reviewer-remove": self.post_co_reviewer_remove,
                 "/co-submit-review": self.post_co_submit_review, "/co-suggestion": self.post_co_suggestion, "/co-answer": self.post_co_answer,
                 "/co-decide": self.post_co_decide, "/co-accept-warning": self.post_co_accept_warning, "/co-state-map": self.post_co_state_map,
                 "/co-dismiss-related": self.post_co_dismiss_related, "/co-direct-remove": self.post_co_direct_remove,
                 "/co-rt-uncarry": self.post_co_rt_uncarry, "/record-types-carry": self.post_record_types_carry,
                 "/co-config-upload": self.post_co_config_upload, "/co-cfg-uncarry": self.post_co_cfg_uncarry, "/co-script-run": self.post_co_script_run,
                 "/co-intervene-review": self.post_co_intervene_review, "/co-report-unavailable": self.post_co_report_unavailable,
                 "/co-answer-all": self.post_co_answer_all, "/co-accept-all": self.post_co_accept_all, "/co-ask-sop": self.post_co_ask_sop, "/co-evidence": self.post_co_evidence, "/co-evidence-file": self.post_co_evidence_file, "/intervene": self.post_intervene, "/assign": self.post_assign, "/agent-confirm": self.post_agent_confirm, "/new-person": self.post_new_person, "/new-cog": self.post_new_cog, "/cog-model": self.post_cog_model, "/restart": self.post_restart, "/mark-applied": self.post_mark_applied, "/verify-edit": self.post_verify_edit, "/editor-debug": self.post_editor_debug,
                 "/upload": self.post_upload, "/checkout": self.post_checkout, "/checkin": self.post_checkin, "/link": self.post_link,
                 "/snapshot": self.post_snapshot, "/hold": self.post_hold, "/resubmit": self.post_resubmit, "/retry": self.post_retry,
                 "/cancel": self.post_cancel, "/undo-checkout": self.post_undo_checkout, "/bulk-hold": self.post_bulk_hold}
        return posts.get(r) or self.inventory_route(method, r) or self.audit_route(method, r)

    def _read_raw(self) -> bytes:
        """A raw request body (the editor's saves), bounded like an upload."""
        raw = self.headers.get("Content-Length")
        try:
            length = int(raw or "0")
        except ValueError:
            raise ShellError("Content-Length is not a number.", 400) from None
        if length < 0 or length > self.max_upload:
            self.close_connection = True
            raise ShellError(f"The body is {length:,} bytes; the limit is {self.max_upload:,}.", 413)
        try:
            return self.rfile.read(length) if length else b""
        except (socket.timeout, TimeoutError):
            self.close_connection = True
            raise ShellError("The body stopped before it was complete.", 408) from None

    def _read_body(self) -> None:
        raw = self.headers.get("Content-Length")
        if raw is None:
            self.close_connection = True
            raise ShellError("The form could not be read: the request had no Content-Length header. Go back and send it again.", 411)
        try:
            length = int(raw)
        except ValueError:
            self.close_connection = True
            raise ShellError(f"Content-Length {raw!r} is not a number.", 400) from None
        if length < 0:
            self.close_connection = True
            raise ShellError("Content-Length cannot be negative.", 400)
        if length > self.max_upload:
            self._drain(length)
            self.close_connection = True
            raise ShellError(f"That upload is too big: {length:,} bytes, and the limit is {self.max_upload:,} bytes "
                             f"({self.max_upload // (1024 * 1024)} MB, HUBPROTO_MAX_UPLOAD). Choose a smaller file.", 413)
        try:
            body = self.rfile.read(length) if length else b""
        except (socket.timeout, TimeoutError):
            self.close_connection = True
            raise ShellError("The upload stopped before it was complete (the connection stalled). Go back and send the form again.", 408) from None
        ctype = self.headers.get("Content-Type", "") or ""
        if ctype.startswith("multipart/form-data"):
            msg = BytesParser(policy=email.policy.default).parsebytes(b"Content-Type: " + ctype.encode("latin-1") + b"\r\n\r\n" + body)
            parts = msg.iter_parts() if msg.is_multipart() else []
            for part in parts:
                name = part.get_param("name", header="content-disposition") or ""
                filename = part.get_filename()
                payload = part.get_payload(decode=True) or b""
                if filename:
                    self.files.append((name, clean(os.path.basename(filename)), payload))
                else:
                    text = clean(payload.decode("utf-8", "replace"))
                    self.form.setdefault(name, text)
                    self.multi.setdefault(name, []).append(text)
        else:
            for k, v in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items():
                self.form[k] = clean(v[0])
                self.multi[k] = [clean(x) for x in v]

    def _drain(self, length: int) -> None:
        """Read and discard an oversize body (up to the limit) so the client sees the 413 instead of a reset."""
        left = min(length, self.max_upload)
        try:
            self.connection.settimeout(5)
            while left > 0:
                chunk = self.rfile.read(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
        except (OSError, socket.timeout):
            pass

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
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _back_link(self) -> str:
        ref = self.headers.get("Referer") or ""
        scheme = urlparse(ref).scheme
        if ref and scheme in ("", "http", "https"):
            return f' &middot; <a href="{esc(ref)}">Back to the previous page</a>'
        return ""

    # --- one-use form tokens ------------------------------------------------
    def _once(self) -> str:
        return f'<input type="hidden" name="once" value="{uuid.uuid4().hex}">'

    def _once_done(self) -> str | None:
        """Where the first submission with this token landed, if it was already used."""
        token = (self.form.get("once") or "").strip()
        if not token:
            return None
        with self._once_lock:
            return self._once_used.get(token)

    def _once_finish(self, location: str) -> str:
        token = (self.form.get("once") or "").strip()
        if token:
            with self._once_lock:
                if len(self._once_used) >= ONCE_KEEP:
                    for k in list(self._once_used)[: ONCE_KEEP // 4]:
                        del self._once_used[k]
                self._once_used[token] = location
        return location

    # --- errors ---------------------------------------------------------------
    def _error_page(self, message: str, status: int = 400, extra: str = "") -> None:
        user = self.user
        first = message.split(". ")[0].rstrip(".")
        links = f'<a href="{href("/", user)}">Home</a> &middot; <a href="{href("/inbox", user)}">Inbox</a>' if user else f'<a href="/">Choose who you are</a>'
        self._page(f"Not done: {short(first, 60)}", f'<h1>Not done</h1><div class="card crit" role="alert"><p>{esc(message)}</p></div>'
                   f"<p>{links}{self._back_link()}</p>{extra}", status=status)

    def _after_post_error(self, e: Exception) -> None:
        """A refused POST re-renders the form it came from with the typed values kept and the message beside the field."""
        msg = str(e) if isinstance(e, ShellError) else friendly(str(e))
        status = e.status if isinstance(e, ShellError) else 400
        field = getattr(e, "field", None)
        self.vals = dict(self.form)
        self.err = (msg, field)
        self._force_status = status
        raw = str(e)
        try:
            if self.route == "/start":
                return self.page_home(status=status)
            if self.route == "/intervene":  # a refused intervention: the card it came from, what was typed kept
                return self.page_inbox(status=status)
            if self.route == "/decide":
                stale = isinstance(e, RunError) and ("not waiting for a" in raw or "changed since you opened it" in raw)
                return self.page_inbox(status=status, stale=stale)
            if self.route in ("/resubmit", "/retry", "/cancel"):
                run = self.hub.runner.load(self.form.get("run", "")) if self.form.get("run") else None
                if run and run.get("record"):
                    self.arg = run["record"]
                    return self.page_record(status=status)
                return self.page_inbox(status=status)
            if self.route in ("/attach", "/link", "/hold", "/snapshot", "/assign", "/restart"):
                self.arg = self.form.get("record", "")
                rec = self.hub.kernel.get(self.arg)
                return self.page_document(status=status) if rec and rec["type"] == "document" else self.page_record(status=status)
            if self.route == "/bulk-hold":
                self.arg = self.form.get("type", "")
                return self.page_items(status=status)
            if self.route == "/msg":
                back = self.form.get("return", "")
                if back.startswith(("/record/", "/document/")):  # sent from a chatter: the composer is re-rendered there
                    self.arg = unquote(back.split("?")[0].split("/", 2)[2])
                    return self.page_record(status=status) if back.startswith("/record/") else self.page_document(status=status)
                return self.page_inbox(status=status)
            if self.route == "/upload":
                self.q["folder"] = [self.form.get("folder", "")]
                return self.page_files(status=status)
            if self.route in ("/checkout", "/checkin", "/undo-checkout"):
                self.arg = self.form.get("doc", "")
                return self.page_document(status=status)
            if self.route == "/co-new":
                return self.page_change_orders(status=status)
            if self.route.startswith("/co-") and self.form.get("co"):  # a refused change-order action: its page, the form's values kept
                self.arg = self.form.get("co", "")
                return self.page_record(status=status)
            if self.route == "/record-types-carry":
                return self.page_record_types(status=status)
            if self.route.startswith("/settings-"):  # a refused settings change: the section it came from, values kept
                self.arg = self.form.get("section", "") if self.route == "/settings-edit" else "overview"
                return self.page_settings(status=status)
        except (RunError, KernelGuardError, ShellError):
            pass
        except Exception as inner:  # the re-render failed: fall back to the plain error page, never a 500
            log_line(f"rerender failed after {type(e).__name__}: {inner}\n{traceback.format_exc()}")
        return self._error_page(msg, status)

    def _error_for(self, *fields: str) -> str | None:
        """The pending error message when it concerns one of these fields (or none of them, for a form-level message)."""
        if not self.err:
            return None
        msg, field = self.err
        return msg if field in fields else None

    def _form_error(self, candidates=()) -> str:
        """The pending error as an alert above a form, unless a control carries it."""
        if not self.err:
            return ""
        msg, field = self.err
        if field is None:
            field = error_field(msg, candidates)
            self.err = (msg, field)
        if field and field in candidates and field not in ("confirm", "file", "files"):  # those controls show no inline message
            self._err_shown = True  # the control carries it
            return ""
        self._err_shown = True
        return f'<p class="err" role="alert">{esc(msg)}</p>'

    def _field_error(self, name: str) -> str | None:
        if not self.err:
            return None
        msg, field = self.err
        if field == name:
            self._err_shown = True
            return msg
        return None

    def _val(self, name: str, default="") -> str:
        return self.vals.get(name, default) if self.vals else default

    # --- page frame --------------------------------------------------------
    def _rail(self) -> str:
        user, bundle = self.user, self.bundle
        members = bundle["members"] if bundle else {}

        def item(n, p, ic):
            cur = ' aria-current="page"' if (self.route == p or (p != "/" and self.route.startswith(p))) else ""
            return f'<li><a{cur} href="{href(p, user)}">{icon(ic)}<span>{esc(n)}</span></a></li>'

        main = list(RAIL_MAIN)
        if bundle and (members.get(user) or {}).get("type") == "person":  # people who may edit Settings find it here
            main.append(("Settings", "/settings", "sliders"))
        out = '<nav class="rail" aria-label="Workspace"><ul class="main">' + "".join(item(n, p, ic) for n, p, ic in main) + "</ul>"
        if self._policy_owner():
            out += '<h2 id="rail-policy">Policy</h2><ul class="main" aria-labelledby="rail-policy">' + "".join(item(n, p, ic) for n, p, ic in RAIL_POLICY) + "</ul>"
        if bundle and user in members and (self.route in ("/files", "/documents") or self.route.startswith(("/document/", "/doc/", "/edit/"))):
            cur_folder = self.q.get("folder", [""])[0]
            out += '<h2 id="rail-folders">Folders</h2><div class="tree" aria-labelledby="rail-folders">' + render_tree(folder_tree(bundle["folders"]), "", user, cur_folder) + "</div>"
        return out + "</nav>"

    def _viewing_cog(self) -> bool:
        """The chosen identity is a cog: the shell shows what it sees and does, and refuses to act in its name."""
        return bool(self.bundle) and (self.bundle["members"].get(self.user) or {}).get("type") == "cog"

    def _apps(self) -> list[dict]:
        if getattr(self, "_apps_cache", None) is None:
            self._apps_cache = app_map(self.bundle, self.hub.kernel.types, policy_owner=self._policy_owner())
        return self._apps_cache

    def _avatar(self, name: str, **kw) -> str:
        return avatar(name, self.bundle, href=lambda p: href(p, self.user), **kw)

    def _identity_options(self) -> str:
        bundle = self.bundle
        user = self.user
        members = dict(bundle["members"]) if bundle else {}
        people = sorted(m for m in members if members[m]["type"] == "person")
        cogs = sorted(m for m in members if members[m]["type"] == "cog")
        options = "".join(f'<option value="{esc(m)}"{" selected" if m == user else ""}>{esc(m)}</option>' for m in people)
        if user and user not in people and user not in cogs:
            options = f'<option value="{esc(user)}" selected>{esc(user)} (not a member)</option>' + options
        if not user:
            options = '<option value="" selected>choose…</option>' + options
        if cogs:
            options += '<optgroup label="See what a cog is doing (read-only)">' + "".join(
                f'<option value="{esc(m)}"{" selected" if m == user else ""}>{esc(m)} (cog)</option>' for m in cogs) + "</optgroup>"
        return options

    def _page(self, title: str, body: str, status: int = 200, head: str = "", crumbs: list[tuple[str, str | None]] | None = None,
              app_type: str = "") -> None:
        user = self.user
        keep = "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(v[0])}">' for k, v in self.q.items() if k not in ("as", "done") and v)
        action = self.route if self.route in STAY_ROUTES or self.route.startswith(STAY_PREFIXES) else "/"
        crumb_items = [("Hub", href("/", user) if user else "/")] + list(crumbs or [(title, None)])
        crumb = "".join(f'<li><a href="{esc(h)}">{esc(n)}</a></li>' if h else f"<li>{esc(n)}</li>" for n, h in crumb_items)
        done = self.q.get("done", [""])[0]
        status_line = f'<p class="status" role="status">{esc(short(done, 300))}</p>' if done else ""
        if self.err and not getattr(self, "_err_shown", False):  # the form that was refused is not on this page: say it up front
            status_line += f'<div class="card crit" role="alert"><p>{esc(self.err[0])}</p></div>'
        if user and self._viewing_cog():
            status_line += (f'<div class="card quiet" role="status">{icon("eye")} You are watching <b>{esc(user)}</b>, a cog. It decides within fixed '
                            f'criteria on its own; nothing here can be sent in its name. <a href="{href("/user/" + user, user)}">What it is doing</a></div>')
        full_title = (f"{short(done, 40)} - " if done else "") + title + (f" ({user})" if user else "") + " - Hub"
        apps = self._apps() if user else []
        rt = app_type or (self.arg.split("?")[0] if self.route.startswith("/items/") else "")
        app = app_for_route(apps, self.route, rt) if apps else None
        launcher = launcher_html(apps, user, href, app["key"] if app else None) if apps and self._is_member() else ""
        appbar = appbar_html(app, user, href, self.route, rt) if app and self._is_member() else ""
        me = (f'<span class="who">{self._avatar(user, size="sm")} {esc(user)}</span>' if user else "")
        page = f"""<!doctype html><html lang="en" data-theme="auto"><head><meta charset="utf-8"><script>try{{var t=localStorage.getItem("hub-theme");if(t==="dark"||t==="light")document.documentElement.setAttribute("data-theme",t)}}catch(e){{}}</script><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(full_title)}</title><style>{CSS}</style><script>{SCRIPT}</script>{head}</head>
<body><a class="skip" href="#main">Skip to content</a><div class="top">{launcher}<a class="brand" href="{href("/", user) if user else "/"}">{icon("home")}Hub</a><ol class="crumb" aria-label="You are here">{crumb}</ol>
<form method="get" action="{esc(action)}"><button type="button" class="theme" data-theme-toggle aria-label="Switch between light and dark" title="Light or dark"><span class="i-light">{icon("moon")}</span><span class="i-dark">{icon("sun")}</span></button>{keep}{me}<label><span class="who">Acting as</span> <select name="as">{self._identity_options()}</select></label> <button class="secondary">Go</button></form></div>{appbar}
<div class="layout">{self._rail()}<main id="main">{status_line}{body}</main></div>{self._messenger() if hasattr(self, "_messenger") else ""}</body></html>"""
        data = page.encode("utf-8")
        if status == 200 and getattr(self, "_force_status", 0):
            status = self._force_status  # a refused POST re-rendered its form: the page keeps the refusal's status
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def page_whoami(self) -> None:
        """No principal on the request: ask who is here instead of assuming anyone."""
        members = (self.bundle or {"members": {}})["members"]
        people = sorted(p for p, m in members.items() if m["type"] == "person")
        cogs = sorted(p for p, m in members.items() if m["type"] == "cog")
        target = self.route if self.route != "/healthz" else "/"
        query = {k: v[0] for k, v in self.q.items() if k != "as" and v}
        cards = "".join(f'<div class="card">{avatar(p, self.bundle, size="lg")}<div><a href="{esc(target)}?{urlencode({"as": p, **query})}">{esc(p)}</a><div class="why">'
                        f'{esc(", ".join(r.replace("-", " ") for r in members[p]["roles"]))}</div></div></div>' for p in people)
        body = (f"<h1>Who are you?</h1><p>This prototype has no sign-in: choose your name to act as that person. Every signature, "
                f"message and change is recorded under the name you choose.</p><div class='who'>{cards or '<p class=muted>No policy release is in effect yet.</p>'}</div>")
        if cogs:
            cog_cards = "".join(f'<div class="card">{avatar(p, self.bundle, size="lg")}<div><a href="/user/{esc(p)}?{urlencode({"as": p})}">See what {esc(p)} is doing</a>'
                                f'<div class="why">{esc(", ".join(r.replace("-", " ") for r in members[p]["roles"]))} &middot; read-only</div></div></div>' for p in cogs)
            body += (f"<h2>Cogs</h2><p class='why'>Cogs act on their own within the policy; nobody signs in as one, but you can watch "
                     f"what each is doing.</p><div class='who'>{cog_cards}</div>")
        self._page("Who are you?", body, status=200)

    # --- shared lookups -------------------------------------------------------
    def _refresh_frames(self) -> None:
        """Re-read the Frames when a file changed on disk, so /frame pages and citations show what is there now."""
        try:
            root = getattr(self.hub, "_frames_dir", None) or self.hub.frames.root
            stamp = max((p.stat().st_mtime for p in root.rglob("*.frame.md")), default=0.0)
        except (OSError, FrameError):
            return
        if stamp != type(self)._frames_stamp:
            try:
                self.hub.frames = FrameStore(root)
            except FrameError as e:
                log_line(f"frames not reloaded: {e}")
            type(self)._frames_stamp = stamp

    def _frame_names(self) -> set[str]:
        try:
            return set(self.hub.frames.frames)
        except FrameError:
            return set()

    def _need_release(self) -> dict:
        if not self.bundle:
            raise ShellError("No policy release is in effect. Compile and release Frames first (hubproto bootstrap-release).", 409)
        return self.bundle

    def _is_member(self) -> bool:
        return bool(self.bundle) and self.user in self.bundle["members"]

    def _policy_owner(self) -> bool:
        return bool(self.bundle) and "policy-owner" in authz.roles_of(self.bundle, self.user)

    def _people(self) -> list[str]:
        return sorted(p for p, m in (self.bundle or {"members": {}})["members"].items() if m["type"] == "person")

    def _can_read(self, folder: str) -> dict:
        return authz.check(self._need_release(), self.user, "read", "*", folder)

    def _can_comment(self, rec: dict) -> dict:
        """May the user comment on (post in the chatter of) this item? Whoever may read it may, unless a sentence about
        commenting there says otherwise (``authz.check``)."""
        td = self.hub.kernel.types.get(rec.get("type")) or {}
        return authz.check(self._need_release(), self.user, "comment", td.get("object") or rec.get("type") or "*", rec["folder"])

    def _require_read(self, folder: str) -> dict:
        d = self._can_read(folder)
        if not d["allowed"]:
            raise ShellError(f"You cannot read {folder}. " + self._why_text(d, "read", "*", folder), 403)
        return d

    def _why_text(self, decision: dict, action: str, obj: str, folder: str) -> str:
        """The permission sentence as plain text (no links) for an error message."""
        import re

        return re.sub(r"<[^>]+>", "", why_sentence(self.bundle, decision, self.user, action, obj, folder)).replace("&quot;", '"').replace("&#x27;", "'")

    def _why_html(self, decision: dict, action: str, obj: str, folder: str, principal: str | None = None) -> str:
        return why_sentence(self.bundle, decision, principal or self.user, action, obj, folder, link_rule=self._rule_link)

    def _rule_link(self, src: dict | None) -> str:
        src = src or {}
        frame, line = src.get("frame", ""), src.get("line", 0)
        if frame and not frame.startswith("(") and frame in self._frame_names():
            return f'<a href="{href("/frame/" + frame, self.user)}#L{line}">{esc(frame)} line {esc(line)}</a>'
        return esc(f"{frame} line {line}" if line else frame)

    def _rule_html(self, rule: dict | None) -> str:
        rule = rule or {}
        return f'{self._rule_link(rule)}: “{esc(rule.get("text", ""))}”'

    def _record_link(self, ref, with_summary: bool = True) -> str:
        if not ref or not isinstance(ref, str):
            return esc(str(ref)) if ref else ""
        rec = self.hub.kernel.get(ref)
        if rec is None:
            return esc(ref.rsplit(":", 1)[-1])
        if self.user and self.bundle and not self._can_read(rec["folder"])["allowed"]:
            return esc(rec["number"])  # a number without the title or a link: the item is in a folder this person may not read
        page = "/document/" if rec["type"] == "document" else "/record/"
        link = f'<a href="{href(page + quote(rec["locator"], safe=":"), self.user)}">{esc(rec["number"])}</a>'
        summary = summary_of_record(rec)
        return link + (f' <span class="muted">{esc(summary)}</span>' if with_summary and summary != rec["number"] else "")

    def _number_link(self, locator: str, number: str) -> str:
        return self._record_link(locator, with_summary=False) or esc(number)

    def _human(self, text) -> str:
        return humanize(str(text or ""), self._number_link)

    def _run_link(self, run: dict | str, text: str | None = None) -> str:
        rid = run if isinstance(run, str) else run["id"]
        return f'<a href="{href("/run/" + rid, self.user)}">{esc(text or ("run " + rid[:8]))}</a>'

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

    def _waiting_on_others(self, user: str) -> list[dict]:
        return [r for r in self.hub.runner.list() if r["status"] == "waiting" and r["owner"] == user and r.get("pending")
                and user not in (r["pending"].get("assignees") or [])]

    def _failure_of(self, run_id: str) -> dict:
        for ev in reversed(self.hub.runner.track(run_id)):
            if ev["type"] == "run.failed":
                return ev
        return {}

    ORG_STOPS = ("no eligible approver", "can countersign", "no routing rule", "no person or cog holds")

    def _org_stop(self, ev: dict) -> bool:
        """A stop no field can fix: nobody may take the item (routing, roles, segregation of duties, a countersignature)."""
        if ev.get("kind"):
            return ev["kind"] == "organisation"
        reason = str(ev.get("reason") or "").lower()
        return any(x in reason for x in self.ORG_STOPS)

    def _stop_reasons(self, run_id: str, ev: dict) -> tuple[str, list[str]]:
        """A failure's headline and its reasons in words. The runner writes the chain into the reason ("Nobody can take
        “X”: a declined: … | b: …"); an older run without it is explained from its log."""
        reason = str(ev.get("reason") or "")
        head, sep, rest = reason.partition(": ")
        if sep and (" | " in rest or head.startswith("Nobody can take")):
            return head, [x.strip() for x in rest.split(" | ") if x.strip()]
        return reason, ([] if not self._org_stop(ev) else self._stop_explanation(run_id))

    def _stop_explanation(self, run_id: str) -> list[str]:
        """Why each route passed the item by, from the run's own record: what a cog declined and why, who was skipped."""
        evs = self.hub.runner.track(run_id)
        start = max((i for i, e in enumerate(evs) if e["type"] in ("run.started", "run.retried", "run.resubmitted")), default=0)
        out = []
        for e in evs[start:]:
            if e["type"] == "gate.declined":
                out.append(f"{e.get('by')} could not decide it: " + "; ".join(str(x) for x in e.get("reasons") or ["its rules do not cover it"]) + ".")
            elif e["type"] == "gate.skipped":
                who = str(e.get("candidate") or "").partition(":")[2].replace("-", " ") or str(e.get("candidate"))
                out.append(f"The route to {who} was passed by: {e.get('reason')}.")
        seen, uniq = set(), []
        for x in out:
            if x not in seen:
                seen.add(x)
                uniq.append(x)
        return uniq

    def _record_type_of(self, run: dict) -> str:
        wf = run.get("workflow") or {}
        if wf.get("record_type"):
            return wf["record_type"]
        op = (self.bundle or {}).get("ops", {}).get(run.get("process", ""), {})
        return op.get("record_type", "")

    def _process_name(self, process: str) -> str:
        """A process named by what it produces: 'Supplier bill' for purchase-to-pay."""
        op = (self.bundle or {}).get("ops", {}).get(process, {})
        rt = op.get("record_type") or ""
        return type_name(rt) if rt else process.replace("-", " ").capitalize()

    def _version_label(self, digest: str | None) -> str:
        if not digest:
            return "no policy version"
        try:
            meta = self.hub.releases.get(digest)
        except KeyError:
            return f"policy version {digest[:12]}"
        return version_label(meta, self.hub.releases.list())

    def _version_html(self, digest: str | None) -> str:
        if not digest:
            return "no policy version"
        label = self._version_label(digest)
        return f'<a href="{href("/release/" + digest[:16], self.user)}">{esc(label)}</a>'

    def _type_chip(self, rt: str) -> str:
        return tag(type_name(rt))

    def _state_chip(self, state: str, wf: dict | None = None) -> str:
        from .text import chip_class, state_name

        return f'<span class="chip {chip_class(state)}" data-state="{esc(state)}">{esc(state_name(state, wf))}</span>'

    def _due_html(self, due: str | None) -> str:
        from .text import due_chip

        return due_chip(due, self.today)

    def _supplier_options(self) -> list[tuple[str, str]]:
        out = []
        for key, s in sorted(self.hub.kernel.suppliers().items()):
            out.append((s.get("id") or key.split(":", 1)[1], f"{s.get('name') or s.get('id')}{'' if s.get('approved') else ' (not approved)'}"))
        if "supplier" in self.hub.kernel.types:
            for r in self.hub.kernel.query(type_="supplier"):
                out.append((r["locator"], f"{r['number']} {summary_of_record(r)}"))
        return out

    def _record_options(self, type_: str, key: str = "locator") -> list[tuple[str, str]]:
        if type_ not in self.hub.kernel.types:
            return []
        readable: dict[str, bool] = {}

        def ok(folder: str) -> bool:
            if folder not in readable:
                readable[folder] = self._can_read(folder)["allowed"]
            return readable[folder]
        return [(r[key], f"{r['number']} {summary_of_record(r)}") for r in self.hub.kernel.query(type_=type_)
                if r["state"] not in ("cancelled", "rejected", "obsolete") and ok(r["folder"])]

    def _supplier_name(self, ref) -> str:
        """A supplier's name for its id ('Acme Metals' for acme-metals); the id when unknown."""
        if not ref:
            return ""
        s = self.hub.kernel.suppliers().get(f"supplier:{ref}")
        if s and s.get("name"):
            return str(s["name"])
        rec = self.hub.kernel.get(str(ref)) if isinstance(ref, str) and ref.startswith("rec:") else None
        return summary_of_record(rec) if rec else str(ref)

    def _po_options(self) -> list[tuple[str, str]]:
        from .text import money

        out = [(po.get("id") or key.split(":", 1)[1], f"{po.get('id')}: {self._supplier_name(po.get('supplier'))}, {money(po.get('amount'), po.get('currency'))}"
                + ("" if po.get("received") else ", not yet received"))
               for key, po in sorted(self.hub.kernel.purchase_orders().items())]
        out += self._record_options("purchase-order")
        return out

    def _picker_options(self, name: str, spec: str | None) -> list[tuple[str, str]] | None:
        """Pickers for references people would otherwise have to look up: suppliers, items, purchase orders, people, assets..."""
        if name == "supplier":
            return self._supplier_options()
        if name == "po_ref":  # a bill is matched against the finance system's purchase orders
            return self._po_options()
        if name == "po":  # a receipt is checked against a purchase-order record
            return self._record_options("purchase-order")
        if name == "part":
            return self._record_options("part")
        if name == "supplier_part":
            return self._record_options("supplier-part")
        if name in ("location", "to_location", "default_location") and "stock-location" in self.hub.kernel.types:
            return self._record_options("stock-location")
        if name == "lot":
            return self._record_options("stock-item")
        if name == "category" and "part-category" in self.hub.kernel.types:
            return self._record_options("part-category")
        if name == "design":
            return self._record_options("design")
        if name == "journal":
            return self._record_options("journal")
        if name == "asset":
            return self._record_options("asset")
        if name == "customer" and "customer" in self.hub.kernel.types:
            return self._record_options("customer")
        if name == "procedure":
            return self._record_options("document")
        if name == "ncrs":
            return self._record_options("ncr")
        if spec == "ref" and name in self.hub.kernel.types:
            return self._record_options(name)
        return None
