"""The workspace shell: a Nextcloud/SharePoint-style web UI over the record system, the DMS and the workflow engine.

Standard library only (``http.server``), no JavaScript frameworks; the one optional script is mermaid for the
workflow diagram, and every page degrades to plain HTML without it. Identity is chosen with ``?as=<principal>``
because the prototype has no SSO (a page without it asks who you are; nobody is assumed); a Nebari deployment
puts Keycloak in front. Every page is permission-trimmed with the release in effect and explains a denial as
"who may, you are, ask".

Modules: ``text`` (names, formatters, sentences), ``css``, ``forms`` (controls and coercion), ``handler`` (request
plumbing, errors, page frame), ``pages`` (home, files, boards, runs, records, documents), ``inbox`` (the queue and
the decision card), ``policy`` (versions, policies, why, people and roles, conformance), ``actions`` (POSTs).
"""

from __future__ import annotations

import os
import socket
import sqlite3
import threading
from http.server import ThreadingHTTPServer

from ..app import Hub
from .actions import ActionsMixin
from .css import CONTRAST_PAIRS, DARK, LIGHT  # noqa: F401  (public: the contrast test)
from .forms import START_INPUTS, ShellError, coerce, prefill, widget  # noqa: F401  (public: cli_records, tests)
from .handler import DEFAULT_MAX_UPLOAD, BaseHandler, href, log_line, table  # noqa: F401
from .inbox import InboxMixin
from .pages import PagesMixin
from .policy import PolicyMixin, sentence_diff  # noqa: F401
from .text import TYPE_NAMES, VERBS, chip, esc, friendly, money, day, when, why_sentence  # noqa: F401

__all__ = ["Handler", "ShellError", "coerce", "make_server", "serve", "serve_in_thread", "free_port", "max_upload_from_env",
           "DEFAULT_MAX_UPLOAD", "LIGHT", "DARK", "CONTRAST_PAIRS", "TYPE_NAMES", "VERBS", "START_INPUTS", "friendly", "money", "day",
           "when", "why_sentence", "sentence_diff", "widget", "prefill", "chip", "esc", "href", "table"]


class Handler(PagesMixin, InboxMixin, PolicyMixin, ActionsMixin, BaseHandler):
    """The request handler: the plumbing in BaseHandler, the pages and actions in the mixins."""


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
    handler = type("HubHandler", (Handler,), {"hub": hub, "max_upload": max_upload or max_upload_from_env(), "_once_used": {}, "_frames_stamp": -1.0})
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
    log_line(f"workspace shell at http://{host}:{server.server_address[1]}/ (choose who you are on the first page; max upload {server.RequestHandlerClass.max_upload:,} bytes)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
