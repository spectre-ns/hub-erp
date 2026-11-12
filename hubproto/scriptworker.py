"""The script worker: a configuration script run behind the /invoke envelope seam (issue #34).

Runs in its own image (``docker/script-worker``), long-running, with no network (``network_mode: none``): the hub
reaches it over a Unix socket on a volume of its own, and it reaches nothing. Each run is a fresh ``python -I -S -B``
subprocess (``hubproto/script_sandbox.py``) in a fresh, empty temporary directory, with no environment but a minimal
PATH, under hard limits (CPU, address space, file size, open files, no processes), a wall-clock timeout and a cap on
what it may write back; inside it, a kernel filter (seccomp) and an audit-hook tripwire are installed before the script
runs. No kernel, no records, no policy here: the worker handles text only, like the CAD worker (``hubproto/worker.py``).

Request (``POST /invoke``)::

    {"tool": "run-script", "script": "<Python source>", "snapshot": "<the configuration export, JSON text>",
     "limits": {...optional, each at most LIMITS' value}}

Answer: an envelope (``hubproto.envelope``) whose payload is the run record (``run_local``'s answer): the exit reason
and its sentence, the script's ``result`` as JSON text (the whole configuration or a part, in the format of
``docs/design/config-json.md``), stdout and stderr excerpts, the limits, what the tripwire saw, and the digests of the
script and the snapshot. ``ok`` says the invocation completed; the exit reason says whether the script did.

``run_local`` is also the hub's fallback when it has no worker (dev hubs, tests): the same subprocess with the same
limits and hooks, but no container around it, so the hub labels it weaker isolation (``hubproto.scripts``).
"""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import shutil
import signal
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

VERSION = "0.1.0"
SANDBOX = Path(__file__).with_name("script_sandbox.py")
# Defaults, and the most a request may ask for.
LIMITS = {"seconds": 30.0, "cpu_seconds": 20, "memory_bytes": 512 * 1024 * 1024, "file_bytes": 1024 * 1024, "open_files": 32,
          "output_bytes": 20 * 1024 * 1024, "excerpt_chars": 16000}
MIN_MEMORY = 64 * 1024 * 1024  # below this the interpreter cannot start
PATH = "/usr/local/bin:/usr/bin:/bin"
MAX_REQUEST = 40 * 1024 * 1024
REASONS = {
    "ok": "the script finished and left a result",
    "refused": "the allow-list check refused the script; it was not run",
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
_RUNS = threading.BoundedSemaphore(2)  # at most two runs at once in one worker


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


def run_local(script: str, snapshot: str, limits: dict | None = None, *, check: bool = True, kernel_filter: bool = True,
              python: str | None = None) -> dict:
    """Run one script in a fresh subprocess and return the run record. ``check=False`` skips the allow-list check: only
    tests use it, to show that the tripwire and the limits hold when the check is got past (never offered over /invoke)."""
    lim = limits_for(limits)
    record = {"script_sha256": sha256(script), "snapshot_sha256": sha256(snapshot), "limits": lim, "worker_version": VERSION,
              "python": "", "kernel_filter": "", "tripped": None, "result": None, "stdout": "", "stderr": "",
              "stdout_dropped": 0, "stderr_dropped": 0, "seconds": 0.0}
    if check:
        refusals = scriptcheck.check(script)
        if refusals:
            return {**record, "exit": "refused", "detail": "; ".join(refusals), "refusals": refusals}
    request = json.dumps({"script": script, "snapshot": snapshot, "allowed": list(scriptcheck.ALLOWED_IMPORTS),
                          "allowed_submodules": list(scriptcheck.ALLOWED_SUBMODULES),
                          "limits": {**lim, "kernel_filter": kernel_filter}}).encode("utf-8")
    exe = python or sys.executable
    started = time.monotonic()
    tmp = tempfile.mkdtemp(prefix="script-run-")
    try:
        os.chmod(tmp, 0o700)
        proc = subprocess.Popen([exe, "-I", "-S", "-B", str(SANDBOX)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd=tmp, env={"PATH": PATH}, close_fds=True, start_new_session=True)
        out, err, why = _collect(proc, request, lim)
        record["seconds"] = round(time.monotonic() - started, 3)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    record["python"] = f"{sys.version_info.major}.{sys.version_info.minor}" if exe == sys.executable else exe
    answer = _last_json(out)
    stray = err.decode("utf-8", "replace")
    if why:  # stopped from outside: what the script printed so far is not known (it was captured inside)
        reason = why
        detail = {"timeout": f"the script ran longer than {lim['seconds']:g} seconds and was stopped",
                  "output-limit": f"the script wrote more than {lim['output_bytes']:,} bytes and was stopped"}[why]
    elif answer is not None and proc.returncode in (0, 3):
        reason, detail = str(answer.get("exit") or "crashed"), str(answer.get("detail") or "")
        for k in ("result", "stdout", "stderr", "stdout_dropped", "stderr_dropped", "kernel_filter", "tripped"):
            if k in answer:
                record[k] = answer[k]
        if proc.returncode == 3 and reason != "tripwire":  # only the tripwire exits 3: whatever the line says
            reason, detail = "tripwire", detail or "the run was stopped by the tripwire"
    elif proc.returncode == -signal.SIGXCPU or (proc.returncode == -signal.SIGKILL and record["seconds"] >= 0.9 * lim["cpu_seconds"]):
        reason, detail = "cpu-limit", f"the script used more than {lim['cpu_seconds']} seconds of processor time and was stopped"
    elif proc.returncode == -signal.SIGKILL:  # not by the worker, not for processor time: the container's memory limit
        reason, detail = "memory-limit", "the run was killed from outside, most likely at the container's memory limit"
    elif "MemoryError" in stray or "Cannot allocate memory" in stray:
        reason, detail = "memory-limit", f"the script ran out of memory (the limit is {lim['memory_bytes'] // (1024 * 1024)} MB)"
    else:
        reason, detail = "crashed", f"the run ended without an answer (exit status {proc.returncode})"
    if stray and not record["stderr"]:
        record["stderr"] = stray[-lim["excerpt_chars"]:]
    if reason not in REASONS:
        reason, detail = "crashed", f"the run gave an exit reason the worker does not know ({reason[:40]!r})"
    if reason == "ok" and not isinstance(record["result"], str):
        reason, detail = "no-result", REASONS["no-result"]
    if reason != "ok":
        record["result"] = None
    return {**record, "exit": reason, "detail": detail or REASONS[reason]}


def _collect(proc: subprocess.Popen, request: bytes, lim: dict) -> tuple[bytes, bytes, str]:
    """Feed the request, read stdout and stderr until the child ends, the time is up or it writes too much."""
    def feed():
        try:
            proc.stdin.write(request)
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    t = threading.Thread(target=feed, daemon=True)
    t.start()
    sel = selectors.DefaultSelector()
    bufs = {proc.stdout: bytearray(), proc.stderr: bytearray()}
    for f in bufs:
        sel.register(f, selectors.EVENT_READ)
    deadline = time.monotonic() + float(lim["seconds"])
    why = ""
    try:
        while sel.get_map():
            left = deadline - time.monotonic()
            if left <= 0:
                why = "timeout"
                break
            for key, _ in sel.select(timeout=min(left, 0.5)):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    sel.unregister(key.fileobj)
                    continue
                bufs[key.fileobj] += chunk
                if sum(len(b) for b in bufs.values()) > lim["output_bytes"]:
                    why = "output-limit"
                    break
            if why:
                break
    finally:
        sel.close()
        if why:
            _kill(proc)
        try:
            proc.wait(timeout=max(1.0, deadline - time.monotonic()) if not why else 5)
        except subprocess.TimeoutExpired:
            _kill(proc)
            proc.wait()
            why = why or "timeout"
        for f in (proc.stdout, proc.stderr):
            f.close()
        t.join(timeout=1)
    return bytes(bufs[proc.stdout]), bytes(bufs[proc.stderr]), why


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _last_json(out: bytes) -> dict | None:
    for line in reversed(out.decode("utf-8", "replace").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                v = json.loads(line)
            except ValueError:
                return None
            return v if isinstance(v, dict) else None
    return None


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
    with _RUNS:
        try:
            rec = run_local(script, snapshot, request.get("limits") if isinstance(request.get("limits"), dict) else None)
        except Exception as e:  # keep the seam: always an envelope
            return make_envelope("script-worker", VERSION, tool, error={"code": "invocation-failed", "detail": f"{type(e).__name__}: {e}"})
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


class UnixServer(ThreadingMixIn, UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        conn, _ = super().get_request()
        return conn, ("local", 0)


def unix_server(path: str) -> UnixServer:
    """Listen on a Unix socket at ``path`` (any stale one removed), connectable by the hub's user."""
    p = Path(path)
    if p.exists() or p.is_symlink():
        p.unlink()
    server = UnixServer(str(p), Handler)
    os.chmod(p, 0o666)
    return server


def serve(socket_path: str = "", host: str = "127.0.0.1", port: int = 9100):
    server = unix_server(socket_path) if socket_path else ThreadingHTTPServer((host, port), Handler)
    where = f"unix://{socket_path}" if socket_path else f"http://{host}:{port}"
    print(f"script worker at {where} (version {VERSION})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def serve_in_thread(host: str = "127.0.0.1", port: int = 0, socket_path: str = ""):
    server = unix_server(socket_path) if socket_path else ThreadingHTTPServer((host, port), Handler)
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
