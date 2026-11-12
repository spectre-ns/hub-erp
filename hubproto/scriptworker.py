"""The script worker: a configuration script run behind the /invoke envelope seam (issue #34).

Runs in its own image (``docker/script-worker``), long-running, with no network (``network_mode: none``): the hub
reaches it over a Unix socket on a volume of its own, and it reaches nothing. No kernel, no records, no policy here:
the worker handles text only, like the CAD worker (``hubproto/worker.py``).

**One run at a time.** Runs are serialized (``_QUEUE``): one runs, at most ``MAX_QUEUED`` wait, any more are refused
at once ("busy"), so no run ever shares the machine with another and a flood costs neither memory nor threads (the
server also refuses connections past ``MAX_CONNECTIONS`` without starting a thread for them).

**Each run** is a fresh ``python -I -S -B`` subprocess (``hubproto/script_sandbox.py``) in a fresh private temporary
directory (mode 0700, removed after the run), with no environment but a minimal PATH. The child protects itself in the
kernel before any of the script is in it (rlimits; not dumpable; Landlock: read only the standard library, read and
write only its directory; seccomp: no execve, fork, clone, socket, kill, ptrace, prctl, ...) and says so in a
handshake. **The parent checks that itself** before sending the script: ``/proc/<child>/status`` shows ``Seccomp: 2``,
more filters than its own, ``NoNewPrivs: 1``, and ``/proc/<child>`` is no longer its user's (not dumpable); a child
that is not protected gets no script (fail closed). The protection in the run record is what the parent saw, never
what the run says. The answer comes on a control descriptor, as one length-prefixed frame after the script returns;
all of it (the result, what it printed) is the script's own output, and anything else it wrote is kept apart as such.

Request (``POST /invoke``)::

    {"tool": "run-script", "script": "<Python source>", "snapshot": "<the configuration export, JSON text>",
     "limits": {...optional, each at most LIMITS' value}}

Answer: an envelope (``hubproto.envelope``) whose payload is the run record (``run_local``'s answer). ``ok`` says the
invocation completed; the exit reason says whether the script did. A full queue is an error envelope, code ``busy``.

``run_local`` is also the hub's fallback when it has no worker and is told it may use one
(``HUBPROTO_SCRIPT_FALLBACK=1``: dev hubs and tests): the same subprocess, protection and checks, but no container
around it, so the hub labels it weaker isolation (``hubproto.scripts``).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import selectors
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer

from . import scriptcheck
from .envelope import make_envelope, problem

VERSION = "0.2.0"
SANDBOX = Path(__file__).with_name("script_sandbox.py")
# Defaults, and the most a request may ask for.
LIMITS = {"seconds": 30.0, "cpu_seconds": 20, "memory_bytes": 512 * 1024 * 1024, "file_bytes": 1024 * 1024, "open_files": 32,
          "output_bytes": 20 * 1024 * 1024, "excerpt_chars": 16000}
MIN_MEMORY = 64 * 1024 * 1024  # below this the interpreter cannot start
PATH = "/usr/local/bin:/usr/bin:/bin"
MAX_REQUEST = 40 * 1024 * 1024
HANDSHAKE_SECONDS = 15.0
MAX_QUEUED = 4  # runs waiting behind the one running; more are refused as busy
MAX_CONNECTIONS = 8  # connections the server serves at once (each a thread); more are refused without a thread
EXIT_TRIPPED, EXIT_UNPROTECTED = 3, 4
REASONS = {
    "ok": "the script finished and left a result",
    "refused": "the allow-list check refused the script; it was not run",
    "unprotected": "the run's process could not be protected, so the script was not run",
    "tripwire": "the script tried something a configuration script never does, and the run was stopped",
    "timeout": "the script ran longer than the time allowed and was stopped",
    "cpu-limit": "the script used more processor time than allowed and was stopped",
    "memory-limit": "the script used more memory than allowed",
    "output-limit": "the script wrote more than allowed and was stopped",
    "error": "the script stopped with an error",
    "no-result": "the script left no result",
    "bad-result": "the script's result is not JSON data",
    "crashed": "the run ended without an answer",
}


class Busy(ValueError):
    """The worker is running a script and enough wait behind it: try again shortly."""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def limits_for(asked: dict | None) -> dict:
    """The limits of a run: the defaults, lowered where ``asked`` lowers them (never raised)."""
    out = dict(LIMITS)
    for k, v in (asked or {}).items():
        if k in out and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            out[k] = type(LIMITS[k])(min(v, LIMITS[k]))
    out["memory_bytes"] = max(out["memory_bytes"], MIN_MEMORY)
    return out


# ---------------------------------------------------------------- one at a time
_QUEUE = threading.Lock()
_RUN = threading.Lock()
_waiting = 0


@contextlib.contextmanager
def _one_at_a_time():
    global _waiting
    with _QUEUE:
        if _waiting > MAX_QUEUED:
            raise Busy(f"the script worker is running a script and {MAX_QUEUED} more wait: try again in a minute")
        _waiting += 1
    try:
        with _RUN:  # each run is bounded by its own timeout, so the wait is too
            yield
    finally:
        with _QUEUE:
            _waiting -= 1


# ---------------------------------------------------------------- frames and the parent's own checks
def _frame(obj: dict) -> bytes:
    data = json.dumps(obj).encode("utf-8")
    return struct.pack(">Q", len(data)) + data


def _frames(buf: bytes) -> tuple[list, bytes]:
    """The whole frames in ``buf`` and what is left over."""
    out = []
    while len(buf) >= 8:
        (n,) = struct.unpack(">Q", buf[:8])
        if len(buf) < 8 + n:
            break
        try:
            out.append(json.loads(buf[8:8 + n].decode("utf-8")))
        except (ValueError, UnicodeDecodeError):
            out.append(None)
        buf = buf[8 + n:]
    return out, buf


def _status(pid) -> dict:
    try:
        with open(f"/proc/{pid}/status", encoding="ascii", errors="replace") as fh:
            return dict(line.rstrip("\n").split(":\t", 1) for line in fh if ":\t" in line)
    except OSError:
        return {}


def verify(pid: int, hello: dict) -> tuple[dict, list[str]]:
    """What the parent sees of the child's protection, and what is missing (empty: protected). ``hello`` is the
    handshake, written before the script was sent; the kernel's own state is read from /proc."""
    said = hello.get("protection") if isinstance(hello.get("protection"), dict) else {}
    me, it = _status("self"), _status(pid)
    mine = int((me.get("Seccomp_filters") or "0").strip() or 0)
    seen = {"seccomp_mode": int((it.get("Seccomp") or "0").strip() or 0),
            "seccomp_filters": int((it.get("Seccomp_filters") or "0").strip() or 0),
            "no_new_privs": (it.get("NoNewPrivs") or "").strip() == "1"}
    try:  # a process that is not dumpable has its /proc entries (fd, environ, mem) owned by root
        owner = os.stat(f"/proc/{pid}/environ").st_uid
        seen["dumpable"] = not (owner == 0 and os.getuid() != 0)
    except OSError:
        seen["dumpable"] = True
    seen["landlock_abi"] = int(said.get("landlock_abi") or 0) if said.get("landlock") == "installed" else 0
    seen["rlimits"] = said.get("rlimits") == "set"
    missing = []
    if seen["seccomp_mode"] != 2 or seen["seccomp_filters"] <= mine:
        missing.append(f"no seccomp filter of its own (Seccomp {seen['seccomp_mode']}, {seen['seccomp_filters']} filters)")
    if not seen["no_new_privs"]:
        missing.append("no_new_privs is not set")
    if seen["dumpable"] and os.getuid() != 0:
        missing.append("it is still dumpable (its /proc entries are its user's)")
    if not seen["landlock_abi"]:
        missing.append(f"no Landlock ruleset ({said.get('landlock') or 'not said'})")
    if not seen["rlimits"]:
        missing.append(f"no limits ({said.get('rlimits') or 'not said'})")
    if not hello.get("ready"):
        said_no = [f"{k} {v}" for k, v in said.items() if k != "landlock_abi" and v not in ("set", "off", "installed")]
        missing.append("it did not say it was ready" + (f" ({'; '.join(said_no)[:300]})" if said_no else ""))
    return seen, missing


# ---------------------------------------------------------------- one run
def run_local(script: str, snapshot: str, limits: dict | None = None, *, check: bool = True, tripwire: bool = True,
              python: str | None = None, _skip=(), _claim: bool = False) -> dict:
    """Run one script in a fresh subprocess, after the one before it (``_one_at_a_time``), and return the run record.
    Raises Busy when enough runs wait. ``check=False`` skips the allow-list check and ``tripwire=False`` the audit hook;
    ``_skip`` leaves a kernel layer out (``_claim``: and has the run say it is there): only tests use them, to show that
    the kernel's restrictions hold when the check and the hook are got past, and that a run without them is refused
    (never offered over /invoke)."""
    with _one_at_a_time():
        return _run_unqueued(script, snapshot, limits, check=check, tripwire=tripwire, python=python, _skip=_skip, _claim=_claim)


def _run_unqueued(script: str, snapshot: str, limits: dict | None = None, *, check: bool = True, tripwire: bool = True,
                  python: str | None = None, _skip=(), _claim: bool = False) -> dict:
    lim = limits_for(limits)
    record = {"script_sha256": sha256(script), "snapshot_sha256": sha256(snapshot), "limits": lim, "worker_version": VERSION,
              "python": "", "protection": {}, "tripped": None, "result": None, "stdout": "", "stderr": "", "raw_output": "",
              "stdout_dropped": 0, "stderr_dropped": 0, "seconds": 0.0}
    if check:
        refusals = scriptcheck.check(script)
        if refusals:
            return {**record, "exit": "refused", "detail": "; ".join(refusals), "refusals": refusals}
    setup = {"allowed": list(scriptcheck.ALLOWED_IMPORTS), "allowed_submodules": list(scriptcheck.ALLOWED_SUBMODULES),
             "names": {k: list(v) for k, v in scriptcheck.ALLOWED_NAMES.items()}, "limits": lim, "tripwire": bool(tripwire),
             **({"test_skip": list(_skip), "test_claim": bool(_claim)} if _skip else {})}
    exe = python or sys.executable
    record["python"] = f"{sys.version_info.major}.{sys.version_info.minor}" if exe == sys.executable else exe
    started = time.monotonic()
    tmp = tempfile.mkdtemp(prefix="script-run-")
    ctl_r, ctl_w = os.pipe()
    proc = None
    try:
        os.chmod(tmp, 0o700)
        proc = subprocess.Popen([exe, "-I", "-S", "-B", str(SANDBOX), str(ctl_w)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, cwd=tmp, env={"PATH": PATH}, close_fds=True, pass_fds=(ctl_w,),
                                start_new_session=True)
        os.close(ctl_w)
        ctl_w = -1
        out = _Collector(proc, ctl_r, lim)
        try:
            proc.stdin.write(_frame(setup))
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        hello, why = out.first_frame(HANDSHAKE_SECONDS)
        seen, missing = verify(proc.pid, hello or {}) if hello is not None else ({}, [why or "it sent no handshake"])
        record["protection"] = {**seen, "checked_by": "", "missing": missing}
        if missing:
            _kill(proc)
            out.finish(0)
            record["seconds"] = round(time.monotonic() - started, 3)
            record["raw_output"] = out.raw()[-lim["excerpt_chars"]:]
            return {**record, "exit": "unprotected",
                    "detail": "the run's process could not be protected, so the script was not run: " + "; ".join(missing)}
        out.feed(_frame({"script": script, "snapshot": snapshot}))
        stopped = out.finish(float(lim["seconds"]))
        record["seconds"] = round(time.monotonic() - started, 3)
    finally:
        if ctl_w >= 0:
            os.close(ctl_w)
        if proc is not None and proc.poll() is None:
            _kill(proc)
            proc.wait()
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            os.close(ctl_r)
        except OSError:
            pass
    return _conclude(record, proc, out, stopped, lim)


def _conclude(record: dict, proc, out: "_Collector", stopped: str, lim: dict) -> dict:
    """The exit reason, from what the parent saw (how the process ended, the time, the output) first, then the answer."""
    raw = out.raw()
    record["raw_output"] = raw[-lim["excerpt_chars"]:]
    answers, rest = _frames(out.answer)
    code = proc.returncode
    if stopped:
        reason = stopped
        detail = {"timeout": f"the script ran longer than {lim['seconds']:g} seconds and was stopped",
                  "output-limit": f"the script wrote more than {lim['output_bytes']:,} bytes and was stopped"}[stopped]
    elif code == -signal.SIGXCPU or (code == -signal.SIGKILL and record["seconds"] >= 0.9 * lim["cpu_seconds"]):
        reason, detail = "cpu-limit", f"the script used more than {lim['cpu_seconds']} seconds of processor time and was stopped"
    elif code == -signal.SIGKILL:  # not by the worker, not for processor time: the container's memory limit
        reason, detail = "memory-limit", "the run was killed from outside, most likely at the container's memory limit"
    elif len(answers) != 1 or rest or answers[0] is None or not isinstance(answers[0], dict) or code not in (0, EXIT_TRIPPED):
        if "MemoryError" in raw or "Cannot allocate memory" in raw:
            reason, detail = "memory-limit", f"the script ran out of memory (the limit is {lim['memory_bytes'] // (1024 * 1024)} MB)"
        else:
            n = len(answers)
            reason, detail = "crashed", (f"the run ended without one readable answer ({n} answer{'s' if n != 1 else ''}, exit status {code}); "
                                         "what it wrote is kept as its own output")
    else:
        a = answers[0]  # written in the run's process: the script's own claims about itself
        reason = str(a.get("exit") or "crashed")
        detail = str(a.get("detail") or "")
        for k in ("result", "stdout", "stderr", "stdout_dropped", "stderr_dropped", "tripped"):
            if k in a:
                record[k] = a[k]
        if code == EXIT_TRIPPED:  # only the tripwire exits 3
            reason, detail = "tripwire", detail or "the run was stopped by the tripwire"
        elif reason in ("tripwire", "unprotected", "refused", "timeout", "cpu-limit", "output-limit"):
            reason, detail = "crashed", f"the run claimed {reason[:20]!r} but ended normally: not believed"
    if reason not in REASONS:
        reason, detail = "crashed", f"the run gave an exit reason the worker does not know ({reason[:40]!r})"
    if reason == "ok" and not isinstance(record["result"], str):
        reason, detail = "no-result", REASONS["no-result"]
    if reason != "ok":
        record["result"] = None
    return {**record, "exit": reason, "detail": detail or REASONS[reason]}


class _Collector:
    """Reads the child's control descriptor (frames) and its raw output (anything it wrote to fd 1 or 2) together,
    against the time limit and the output cap."""

    def __init__(self, proc, ctl_r: int, lim: dict):
        self.proc, self.lim = proc, lim
        self.sel = selectors.DefaultSelector()
        self.sel.register(ctl_r, selectors.EVENT_READ, "ctl")
        self.sel.register(proc.stdout, selectors.EVENT_READ, "raw")
        self.ctl, self.raw_buf, self.answer = bytearray(), bytearray(), b""
        self.handshake_end = None
        self.feeder = None

    def raw(self) -> str:
        return bytes(self.raw_buf).decode("utf-8", "replace")

    def _pump(self, deadline: float, until=None) -> str:
        while self.sel.get_map():
            if until is not None and until():
                return ""
            left = deadline - time.monotonic()
            if left <= 0:
                return "timeout"
            for key, _ in self.sel.select(timeout=min(left, 0.5)):
                fd = key.fd if key.data == "ctl" else self.proc.stdout.fileno()
                chunk = os.read(fd, 65536)
                if not chunk:
                    self.sel.unregister(key.fileobj)
                    continue
                (self.ctl if key.data == "ctl" else self.raw_buf).extend(chunk)
                if len(self.ctl) + len(self.raw_buf) > self.lim["output_bytes"]:
                    return "output-limit"
        return ""

    def first_frame(self, seconds: float) -> tuple[dict | None, str]:
        """The handshake: the first frame on the control descriptor, written before the script was sent."""
        def have():
            return len(self.ctl) >= 8 and len(self.ctl) >= 8 + struct.unpack(">Q", bytes(self.ctl[:8]))[0]

        why = self._pump(time.monotonic() + seconds, until=have)
        if not have():
            return None, ("it sent no handshake" + (f" ({why})" if why else "") + (f": {self.raw()[-300:]}" if self.raw_buf else ""))
        frames, rest = _frames(bytes(self.ctl))
        self.ctl = bytearray(bytes(self.ctl)[8 + struct.unpack(">Q", bytes(self.ctl[:8]))[0]:])
        first = frames[0] if frames and isinstance(frames[0], dict) else None
        return first, "" if first is not None else "its handshake was not readable"

    def feed(self, data: bytes) -> None:
        def run():
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    self.proc.stdin.close()
                except OSError:
                    pass

        self.feeder = threading.Thread(target=run, daemon=True)
        self.feeder.start()

    def finish(self, seconds: float) -> str:
        stopped = self._pump(time.monotonic() + seconds) if seconds else ""
        self.sel.close()
        if stopped:
            _kill(self.proc)
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill(self.proc)
            self.proc.wait()
            stopped = stopped or "timeout"
        try:
            self.proc.stdout.close()
        except OSError:
            pass
        if self.feeder is not None:
            self.feeder.join(timeout=1)
        self.answer = bytes(self.ctl)
        return stopped


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


# ---------------------------------------------------------------- the /invoke seam
def invoke(request: dict) -> dict:
    """Answer one /invoke request with an envelope (never raises)."""
    tool = request.get("tool") if isinstance(request, dict) else None
    if tool != "run-script":
        return make_envelope("script-worker", VERSION, str(tool), error={"code": "invalid-input", "detail": f"unknown tool {tool!r}: this worker runs run-script"})
    script, snapshot = request.get("script"), request.get("snapshot")
    if not isinstance(script, str) or not isinstance(snapshot, str):
        return make_envelope("script-worker", VERSION, tool, error={"code": "invalid-input", "detail": "give the script and the snapshot as text"})
    started = time.perf_counter()
    try:
        rec = run_local(script, snapshot, request.get("limits") if isinstance(request.get("limits"), dict) else None)
    except Busy as e:
        return make_envelope("script-worker", VERSION, tool, error={"code": "busy", "detail": str(e)})
    except Exception as e:  # keep the seam: always an envelope
        return make_envelope("script-worker", VERSION, tool, error={"code": "invocation-failed", "detail": f"{type(e).__name__}: {e}"})
    rec["protection"]["checked_by"] = "the script worker"
    probs = [] if rec["exit"] == "ok" else [problem("script-run", rec["detail"])]
    return make_envelope("script-worker", VERSION, tool, payload=rec, problems=probs, timing=round(time.perf_counter() - started, 3))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def address_string(self):
        return "local"

    def _send(self, code: int, obj) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            return self._send(200, {"ok": True, "version": VERSION, "tool": "run-script"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/invoke":
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_REQUEST:
            return self._send(413, make_envelope("script-worker", VERSION, "?", error={"code": "invalid-input", "detail": "the request is too large"}))
        try:
            request = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._send(400, make_envelope("script-worker", VERSION, "?", error={"code": "invalid-input", "detail": "bad JSON"}))
        self._send(200, invoke(request))



def _busy_bytes() -> bytes:
    body = json.dumps(make_envelope("script-worker", VERSION, "?", error={
        "code": "busy", "detail": "the script worker has as many requests as it serves at once: try again in a minute"})).encode()
    return (b"HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n" + body)


class _Bounded:
    """At most MAX_CONNECTIONS requests served at once; past that, a busy answer from the accepting thread itself."""

    def _bounded_init(self):
        self._active = 0
        self._active_lock = threading.Lock()

    def process_request(self, request, client_address):
        with self._active_lock:
            full = self._active >= MAX_CONNECTIONS
            if not full:
                self._active += 1
        if full:
            try:
                request.sendall(_busy_bytes())
            except OSError:
                pass
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._active_lock:
                self._active -= 1


class UnixServer(_Bounded, ThreadingMixIn, UnixStreamServer):
    daemon_threads = True

    def __init__(self, *a, **k):
        self._bounded_init()
        super().__init__(*a, **k)

    def get_request(self):
        conn, _ = super().get_request()
        return conn, ("local", 0)


class TcpServer(_Bounded, ThreadingHTTPServer):
    def __init__(self, *a, **k):
        self._bounded_init()
        super().__init__(*a, **k)


def unix_server(path: str) -> UnixServer:
    """Listen on a Unix socket at ``path`` (any stale one removed), connectable by the hub's user."""
    p = Path(path)
    if p.exists() or p.is_symlink():
        p.unlink()
    server = UnixServer(str(p), Handler)
    os.chmod(p, 0o666)
    return server


def watch_socket(path: str, on_lost, every: float = 1.0) -> threading.Thread:
    """Call ``on_lost(why)`` once if the socket at ``path`` is removed or replaced (a run cannot: Landlock gives it
    nothing outside its own folder; this is for anything else). ``serve`` exits then, so the restart policy brings a
    worker back with a fresh socket."""
    st = os.lstat(path)
    ident = (st.st_dev, st.st_ino)

    def loop():
        while True:
            time.sleep(every)
            try:
                now = os.lstat(path)
                if (now.st_dev, now.st_ino) != ident:
                    return on_lost(f"the socket {path} was replaced")
            except OSError:
                return on_lost(f"the socket {path} is gone")

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def _not_dumpable_self() -> None:
    """The worker itself is not dumpable either: no run can open its /proc entries."""
    try:
        import ctypes

        ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0)
    except (ImportError, OSError, AttributeError):
        pass


def _exit_lost(why: str) -> None:
    print(f"script worker: {why}; exiting so the restart policy starts a fresh one", flush=True)
    os._exit(75)


def serve(socket_path: str = "", host: str = "127.0.0.1", port: int = 9100):
    _not_dumpable_self()
    server = unix_server(socket_path) if socket_path else TcpServer((host, port), Handler)
    where = f"unix://{socket_path}" if socket_path else f"http://{host}:{port}"
    if socket_path:
        watch_socket(socket_path, _exit_lost)
    print(f"script worker at {where} (version {VERSION})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def serve_in_thread(host: str = "127.0.0.1", port: int = 0, socket_path: str = "", on_lost=None):
    server = unix_server(socket_path) if socket_path else TcpServer((host, port), Handler)
    if socket_path and on_lost is not None:
        watch_socket(socket_path, on_lost, every=0.2)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="the script worker (issue #34)")
    ap.add_argument("--socket", default=os.environ.get("HUBPROTO_SCRIPT_SOCKET", ""), help="listen on this Unix socket")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9100)
    a = ap.parse_args()
    serve(a.socket, a.host, a.port)
