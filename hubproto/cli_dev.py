"""Development hubs on localhost: ``hubproto dev serve|list|stop|clean``; ``hubproto.cli`` calls ``register(sub)``.

  hubproto dev serve [--worktree DIR] [--port auto|N] [--seed demo|empty] [--name NAME] [--keep] [--detach]
                                       a fresh hub from a worktree's code and Frames, released, served on a free port
  hubproto dev list [--json]           the instances running now (dead ones are pruned from the registry)
  hubproto dev stop NAME|--all         stop instances (their state is kept)
  hubproto dev clean NAME|--all        remove the state directories of stopped instances

Development only, never a deployed hub: the cogs answer with the deterministic stand-in (``HUBPROTO_ALLOW_STAND_IN=1``)
unless ``--models ollama``. Everything lives under ``$HUBPROTO_DEV_HOME`` (default ``~/.cache/hubproto-dev``): one
directory per instance (the hub root: ``frames/``, ``state/``, ``hub.log``, ``hub.pid`` and the ``dev-instance.json``
marker; ``clean`` removes only directories that carry it) and ``registry.json``, read and written under an exclusive
lock on ``registry.lock`` so concurrent callers never get the same name or port. Ports are scanned from 8100 and never
8080 (the hub in Docker), 9980 (Collabora) or 11434 (Ollama).

The served hub runs the worktree's own code: each step is a child ``python -c`` with ``PYTHONPATH`` set to the
worktree (its ``.venv`` interpreter when it has one), so the worktree need not contain this module; the demo data is
``hubproto/dev_demo.py``'s text, run the same way.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .cli import clean, fail

FORBIDDEN_PORTS = frozenset({8080, 9980, 11434})  # the Docker hub, Collabora, Ollama
FIRST_PORT, LAST_PORT = 8100, 8999
MARKER = "dev-instance.json"
CLI_MAIN = "import sys; from hubproto.cli import main; sys.exit(main())"
STOP_WAIT = 10.0


# ------------------------------------------------------------------ the registry
def dev_home() -> Path:
    return Path(os.environ.get("HUBPROTO_DEV_HOME") or Path.home() / ".cache" / "hubproto-dev").expanduser()


def _empty() -> dict:
    return {"instances": {}, "stopped": {}}


def read_registry(home: Path | None = None) -> dict:
    path = (home or dev_home()) / "registry.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    return {"instances": dict(data.get("instances") or {}), "stopped": dict(data.get("stopped") or {})}


@contextmanager
def locked_registry(home: Path | None = None):
    """The registry, held under an exclusive lock for the block and written back (atomically) when it ends cleanly."""
    import fcntl

    home = home or dev_home()
    home.mkdir(parents=True, exist_ok=True)
    with open(home / "registry.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = read_registry(home)
            yield data
            tmp = home / f"registry.json.{os.getpid()}.tmp"
            tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
            os.replace(tmp, home / "registry.json")
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _proc_stat(pid: int) -> list[str] | None:
    """Fields of /proc/<pid>/stat from the state on (field 3), or None where there is no /proc."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return text.rsplit(")", 1)[1].split()


def start_time(pid: int) -> str | None:
    """The process's start time (clock ticks since boot), so a reused pid is not taken for the instance."""
    stat = _proc_stat(pid)
    return stat[19] if stat and len(stat) > 19 else None


def alive(entry: dict) -> bool:
    pid = entry.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    stat = _proc_stat(pid)
    if stat is not None:
        if stat[0] in ("Z", "X"):
            return False
        if entry.get("pid_start") and stat[19] != entry["pid_start"]:
            return False
    elif not Path("/proc").is_dir():
        return True
    else:
        return False  # /proc exists but not this pid: it is gone
    return True


def prune(reg: dict) -> list[str]:
    """Move instances whose process is gone to ``stopped`` (their state directories stay for ``clean``)."""
    gone = [n for n, e in reg["instances"].items() if not alive(e)]
    for n in gone:
        e = reg["instances"].pop(n)
        reg["stopped"][n] = {**e, "status": "stopped", "stopped": now()}
    return gone


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------ names and ports
def safe_name(text: str) -> str:
    name = re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-.")[:40]
    return name or "hub"


def choose_name(reg: dict, wanted: str | None, worktree: Path) -> str:
    if wanted:
        name = safe_name(wanted)
        if name != wanted:
            fail(f"--name {wanted!r}: use lower-case letters, digits, '.', '_' and '-' (for example {name})", 2)
        if name in reg["instances"]:
            e = reg["instances"][name]
            fail(f"an instance named {name} is already running at {e.get('url')} (pid {e.get('pid')}); "
                 f"`hubproto dev stop {name}` first, or choose another --name", 2)
        return name
    base = safe_name(worktree.name)
    name, n = base, 1
    while name in reg["instances"]:
        n += 1
        name = f"{base}-{n}"
    return name


def bindable(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as http.server binds: a TIME_WAIT port is free
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def choose_port(reg: dict, wanted: str = "auto") -> int:
    """A port no registered instance holds and nothing listens on: ``wanted`` when it is a number, else the first free
    one from 8100. Never 8080, 9980 or 11434."""
    taken = {e.get("port") for e in reg["instances"].values()}
    if wanted and wanted != "auto":
        try:
            port = int(wanted)
        except ValueError:
            fail(f"--port {wanted!r}: give a port number or auto", 2)
        if port in FORBIDDEN_PORTS:
            fail(f"--port {port}: {port} is reserved (8080 the Docker hub, 9980 Collabora, 11434 Ollama); "
                 f"use auto or a port from {FIRST_PORT}", 2)
        if not 1024 <= port <= 65535:
            fail(f"--port {port}: give a port from 1024 to 65535", 2)
        if port in taken:
            fail(f"--port {port}: instance {_holder(reg, port)} has it; `hubproto dev list` shows the instances", 2)
        if not bindable(port):
            fail(f"--port {port}: something already listens on it; use auto", 2)
        return port
    for port in range(FIRST_PORT, LAST_PORT + 1):
        if port not in FORBIDDEN_PORTS and port not in taken and bindable(port):
            return port
    fail(f"no free port from {FIRST_PORT} to {LAST_PORT}; `hubproto dev stop --all` frees the instances' ports", 1)
    return 0


def _holder(reg: dict, port: int) -> str:
    return next((n for n, e in reg["instances"].items() if e.get("port") == port), "?")


# ------------------------------------------------------------------ the worktree
def find_worktree(given: str | None) -> Path:
    def ok(p: Path) -> bool:
        return (p / "hubproto" / "__init__.py").exists() and (p / "frames").is_dir()

    if given:
        path = Path(given).expanduser().resolve()
        if not ok(path):
            fail(f"--worktree {given}: no hub code there (it needs hubproto/ and frames/)", 2)
        return path
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        top = ""
    for cand in ([Path(top)] if top else []) + [Path(__file__).resolve().parent.parent]:
        if ok(cand):
            return cand
    fail("not in a hub worktree; pass --worktree DIR (a checkout with hubproto/ and frames/)", 2)
    return Path()


def git_info(worktree: Path) -> dict:
    def git(*argv) -> str:
        try:
            r = subprocess.run(["git", "-C", str(worktree), *argv], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return ""
        return r.stdout.strip() if r.returncode == 0 else ""

    commit = git("rev-parse", "--short=12", "HEAD")
    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    return {"branch": git("rev-parse", "--abbrev-ref", "HEAD") or "-", "commit": (commit + ("+dirty" if dirty else "")) or "-"}


def python_for(worktree: Path) -> str:
    venv = worktree / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def child_env(worktree: Path, models: str) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(worktree)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("HUBPROTO_ROOT", None)  # every step gets --root
    if models == "ollama":
        env.pop("HUBPROTO_ALLOW_STAND_IN", None)
        env.setdefault("HUBPROTO_OLLAMA_URL", "http://localhost:11434")
    else:
        env["HUBPROTO_ALLOW_STAND_IN"] = "1"
    return env


# ------------------------------------------------------------------ building the hub
class BuildFailed(Exception):
    pass


def _tail(path: Path, lines: int = 25) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def _step(what: str, cmd: list[str], root: Path, env: dict, log: Path, required: bool = True) -> str:
    with open(log, "a") as out:
        out.write(f"\n$ {what}\n")
        out.flush()
        r = subprocess.run(cmd, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True)
        out.write(r.stdout)
    if r.returncode and required:
        raise BuildFailed(f"{what} exited {r.returncode}:\n{r.stdout.strip()[-3000:]}")
    if r.returncode:
        print(f"  {what}: exited {r.returncode} (see {log}); carrying on")
    return r.stdout


def prepare_dir(root: Path, keep: bool, home: Path) -> bool:
    """Make ``root`` ready; True when a hub must be built in it. Only a directory carrying the marker (or an empty
    one) is ever wiped."""
    if keep and (root / "state" / "kernel.db").exists():
        return False
    if root.exists() and any(root.iterdir()):
        if not (root / MARKER).exists():
            fail(f"{root} is not empty and is not a dev instance's directory (no {MARKER}); choose another --state-dir", 2)
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    return True


def build(worktree: Path, root: Path, env: dict, seed: str, log: Path) -> dict:
    """A fresh hub in ``root`` from the worktree's Frames, as the container's first start makes one: init, the ISO 9001
    profile, the first release by bob and carol (which converts the SOPs to documents), then the demo data."""
    py = python_for(worktree)
    where = _step("which hubproto", [py, "-c", "import hubproto; print(hubproto.__file__)"], root, env, log).strip()
    if not Path(where).is_relative_to(worktree):
        raise BuildFailed(f"the child imports hubproto from {where}, not from {worktree}")
    shutil.copytree(worktree / "frames", root / "frames")
    cli = [py, "-c", CLI_MAIN, "--root", str(root)]
    _step("hubproto init", cli + ["init"], root, env, log)
    _step("hubproto seed iso9001", cli + ["seed", "iso9001"], root, env, log, required=False)
    out = _step("hubproto bootstrap-release --as bob --second carol", cli + ["bootstrap-release", "--as", "bob", "--second", "carol"],
                root, env, log)
    release = next((ln for ln in out.splitlines() if ln.startswith("release ")), "").split(" in effect")[0]
    made: dict = {}
    if seed == "demo":
        demo = (Path(__file__).resolve().parent / "dev_demo.py").read_text()
        out = _step("demo data", [py, "-c", demo, str(root)], root, env, log, required=False)
        line = next((ln for ln in reversed(out.splitlines()) if ln.startswith("demo: ")), "")
        try:
            made = json.loads(line[len("demo: "):]) if line else {}
        except ValueError:
            made = {}
        for ln in out.splitlines():
            if ln.startswith("skipped the demo"):
                print("  " + clean(ln))
    return {"release": release, "demo": made}


def wait_healthy(url: str, proc: subprocess.Popen, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url + "healthz", timeout=2) as r:
                if r.status == 200 and r.read().startswith(b"ok"):
                    return True
        except OSError:
            pass
        time.sleep(0.2)
    return False


def _forget(name: str, pid: int | None = None) -> None:
    """Move an instance to stopped (only while it is still the one with ``pid``, when given)."""
    with locked_registry() as reg:
        e = reg["instances"].get(name)
        if e is not None and (pid is None or e.get("pid") == pid):
            reg["instances"].pop(name)
            reg["stopped"][name] = {**e, "status": "stopped", "stopped": now()}


def _stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(STOP_WAIT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def say(*parts, **kw) -> None:
    print(*parts, **kw, flush=True)  # before a child writes to the same terminal or file


class _Terminated(Exception):
    pass


def _on_term(signum, frame):
    raise _Terminated()


# ------------------------------------------------------------------ verbs
def cmd_serve(args) -> int:
    worktree = find_worktree(args.worktree)
    info = git_info(worktree)
    home = dev_home()
    with locked_registry(home) as reg:
        prune(reg)
        name = choose_name(reg, args.name, worktree)
        root = Path(args.state_dir).expanduser().resolve() if args.state_dir else (home / name).resolve()
        clash = next((n for n, e in reg["instances"].items() if Path(e.get("state_dir", "")) == root), None)
        if clash:
            fail(f"{root} is the state of running instance {clash}; stop it first or choose another --state-dir", 2)
        if root == worktree or worktree.is_relative_to(root) or root == home:
            fail(f"--state-dir {root}: the state directory must be a directory of its own", 2)
        port = choose_port(reg, str(args.port))
        url = f"http://127.0.0.1:{port}/"
        entry = {"name": name, "worktree": str(worktree), "branch": info["branch"], "commit": info["commit"], "port": port,
                 "pid": os.getpid(), "pid_start": start_time(os.getpid()), "state_dir": str(root), "started": now(), "url": url,
                 "log": str(root / "hub.log"), "seed": args.seed, "models": args.models, "status": "starting",
                 "detached": bool(args.detach)}
        reg["instances"][name] = entry
        reg["stopped"].pop(name, None)

    old_term = None
    try:
        old_term = signal.signal(signal.SIGTERM, _on_term)  # `dev stop` while building: clean up, do not leave a reservation
    except ValueError:
        pass  # not the main thread (a test calling main from a thread)
    proc = None
    try:
        fresh = prepare_dir(root, args.keep, home)
        log = root / "hub.log"
        env = child_env(worktree, args.models)
        (root / MARKER).write_text(json.dumps({k: entry[k] for k in ("name", "worktree", "branch", "commit", "seed", "models")}
                                              | {"created": entry["started"]}, indent=1))
        say(f"{name}: {'building a fresh hub' if fresh else 'reusing the hub'} in {root} from {worktree} "
              f"({info['branch']} {info['commit']})")
        if fresh:
            t0 = time.monotonic()
            out = build(worktree, root, env, args.seed, log)
            demo = ", ".join(f"{v}" for v in out["demo"].values())
            say(f"  {out['release'] or 'released'}" + (f"; demo: {demo}" if demo else "") + f" ({time.monotonic() - t0:.1f}s)")
        cmd = [python_for(worktree), "-c", CLI_MAIN, "--root", str(root), "shell", "--host", "127.0.0.1", "--port", str(port)]
        if args.detach:
            with open(log, "ab") as out_f:
                proc = subprocess.Popen(cmd, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=out_f, stderr=subprocess.STDOUT,
                                        start_new_session=True)
        else:
            proc = subprocess.Popen(cmd, cwd=root, env=env, stdin=subprocess.DEVNULL)
        (root / "hub.pid").write_text(f"{proc.pid}\n")
        with locked_registry(home) as reg:
            reg["instances"][name] = {**entry, "pid": proc.pid, "pid_start": start_time(proc.pid), "status": "running"}
        if not wait_healthy(url, proc, args.timeout):
            if proc.returncode in (-signal.SIGTERM, -signal.SIGINT):  # `dev stop` (or Ctrl-C) came first
                _forget(name)
                say(f"{name}: stopped")
                return 0
            _stop_process(proc)
            _forget(name)
            print(_tail(log), file=sys.stderr)
            fail(f"{name}: the shell did not answer {url}healthz within {args.timeout:g}s (log: {log})", 1)
        say(f"{name}: serving {url}  (as a person: {url}?as=alice)")
        if args.detach:
            say(f"  pid {proc.pid}, log {log}; stop it with `hubproto dev stop {name}`")
            return 0
        say("  Ctrl-C stops it")
        try:
            rc = proc.wait()
        except (KeyboardInterrupt, _Terminated):
            _stop_process(proc)
            rc = 0
        _forget(name)
        return 0 if rc in (0, -signal.SIGTERM, -signal.SIGINT) else 1
    except BuildFailed as e:
        _forget(name)
        fail(f"{name}: the hub could not be built: {e} (log: {root / 'hub.log'})", 1)
    except (KeyboardInterrupt, _Terminated):
        if proc is not None:
            _stop_process(proc)
        _forget(name)
        say(f"{name}: stopped")
        return 1
    except BaseException:
        if proc is not None:
            _stop_process(proc)
        _forget(name)
        raise
    finally:
        if old_term is not None:
            signal.signal(signal.SIGTERM, old_term)
    return 0


def cmd_list(args) -> int:
    with locked_registry() as reg:
        prune(reg)
        live = sorted(reg["instances"].values(), key=lambda e: e.get("started") or "")
        stopped = len(reg["stopped"])
    if args.json:
        print(json.dumps(live, indent=1, sort_keys=True))
        return 0
    if not live:
        print("no dev hubs running; `hubproto dev serve --detach --name NAME` starts one")
    else:
        print(f"{'name':20s} {'port':5s} {'pid':>7s} {'status':8s} {'branch':24s} {'commit':18s} {'started':16s} url")
        for e in live:
            print(f"{clean(e.get('name'), 20):20s} {e.get('port')!s:5s} {e.get('pid')!s:>7s} {clean(e.get('status'), 8):8s} "
                  f"{clean(e.get('branch'), 24):24s} {clean(e.get('commit'), 18):18s} {clean((e.get('started') or '')[:16].replace('T', ' ')):16s} {clean(e.get('url'))}")
            print(f"{'':20s} worktree {clean(e.get('worktree'))}  state {clean(e.get('state_dir'))}")
    if stopped:
        print(f"{stopped} stopped instance(s) kept their state; `hubproto dev clean --all` removes it")
    return 0


def _wait_gone(entry: dict, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive(entry):
            return True
        time.sleep(0.1)
    return not alive(entry)


def cmd_stop(args) -> int:
    if bool(args.name) == bool(args.all):
        fail("name one instance or pass --all", 2)
    with locked_registry() as reg:
        prune(reg)
        if args.all:
            targets = list(reg["instances"].values())
        elif args.name in reg["instances"]:
            targets = [reg["instances"][args.name]]
        elif args.name in reg["stopped"]:
            print(f"{args.name} is not running (stopped {reg['stopped'][args.name].get('stopped')})")
            return 0
        else:
            fail(f"no instance named {args.name}; `hubproto dev list` shows the running ones", 2)
    if not targets:
        print("no dev hubs running")
    rc = 0
    for e in targets:
        try:
            os.kill(e["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError as err:
            print(f"error: cannot stop {e['name']} (pid {e['pid']}): {err.strerror}", file=sys.stderr)
            rc = 1
            continue
        if not _wait_gone(e, STOP_WAIT):
            try:
                os.kill(e["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
            _wait_gone(e, 2)
        _forget(e["name"], e["pid"])
        print(f"stopped {e['name']} (was {e.get('url')}); its state stays in {e.get('state_dir')}")
    return rc


def cmd_clean(args) -> int:
    if bool(args.name) == bool(args.all):
        fail("name one instance or pass --all", 2)
    home = dev_home()
    with locked_registry(home) as reg:
        prune(reg)
        live_dirs = {Path(e.get("state_dir", "")) for e in reg["instances"].values()}
        if args.all:
            dirs = {n: Path(e.get("state_dir", "")) for n, e in reg["stopped"].items()}
            for d in sorted(home.iterdir()) if home.is_dir() else []:
                if d.is_dir() and (d / MARKER).exists() and d not in live_dirs and d not in dirs.values():
                    dirs.setdefault(d.name, d)
        elif args.name in reg["instances"]:
            fail(f"{args.name} is running ({reg['instances'][args.name].get('url')}); `hubproto dev stop {args.name}` first", 1)
        else:
            stopped = reg["stopped"].get(args.name)
            d = Path(stopped["state_dir"]) if stopped and stopped.get("state_dir") else home / args.name
            if not stopped and not d.exists():
                fail(f"no stopped instance named {args.name}", 2)
            dirs = {args.name: d}
        removed = 0
        for n, d in dirs.items():
            if d in live_dirs:
                continue
            if d.exists():
                if not (d / MARKER).exists():
                    print(f"kept {d}: it is not a dev instance's directory (no {MARKER})")
                    reg["stopped"].pop(n, None)  # forget the instance; the directory is not ours to remove
                    continue
                shutil.rmtree(d)
                removed += 1
                print(f"removed {n}: {d}")
            reg["stopped"].pop(n, None)
    if not removed:
        print("nothing to remove")
    return 0


def register(sub) -> None:
    s = sub.add_parser("dev", help="development hubs on localhost: serve a worktree's hub on a free port, list, stop, clean "
                                   "(stand-in models; never a deployed hub)")
    t = s.add_subparsers(dest="dev_cmd", required=True, metavar="ACTION", help="serve, list, stop or clean")
    c = t.add_parser("serve", help="build a fresh hub from a worktree's code and Frames (released, SOPs as documents, demo data) "
                                   "and serve the shell on a free port")
    c.add_argument("--worktree", metavar="DIR", help="the checkout whose code and Frames to serve (default: the git checkout "
                                                     "you are in)")
    c.add_argument("--port", default="auto", metavar="auto|N", help=f"auto (default: the first free port from {FIRST_PORT}) or a "
                                                                    "number; never 8080, 9980 or 11434")
    c.add_argument("--seed", choices=["demo", "empty"], default="demo",
                   help="demo (default): also an open NCR waiting for carol, a bill under the ap-reviewer limit and an open "
                        "change order alice prepared with one revision; empty: the released example company only")
    c.add_argument("--name", metavar="NAME", help="the instance's name (default: the worktree's directory name, with -2, -3 ... "
                                                  "when that is running)")
    c.add_argument("--state-dir", metavar="DIR", help="the hub directory (default: $HUBPROTO_DEV_HOME/<name>, "
                                                      "~/.cache/hubproto-dev/<name>)")
    c.add_argument("--keep", action="store_true", help="reuse the hub already in the state directory (no rebuild, no seeding); "
                                                       "default: build it fresh")
    c.add_argument("--detach", action="store_true", help="run in the background (pidfile hub.pid, log hub.log in the state "
                                                         "directory); default: in the foreground until Ctrl-C")
    c.add_argument("--models", choices=["stand-in", "ollama"], default="stand-in",
                   help="stand-in (default): the deterministic stand-in answers for every cog; ollama: the local Ollama "
                        "($HUBPROTO_OLLAMA_URL, default http://localhost:11434)")
    c.add_argument("--timeout", type=float, default=60.0, metavar="SECONDS", help="how long to wait for /healthz (default 60)")
    c.set_defaults(fn=cmd_serve)
    c = t.add_parser("list", help="the dev hubs running now: name, port, pid, branch, commit, started, url, worktree, state")
    c.add_argument("--json", action="store_true", help="as JSON (one object per instance)")
    c.set_defaults(fn=cmd_list)
    c = t.add_parser("stop", help="stop a dev hub (SIGTERM, then SIGKILL after 10s); its state is kept")
    c.add_argument("name", nargs="?", help="the instance (`hubproto dev list`)")
    c.add_argument("--all", action="store_true", help="every running instance")
    c.set_defaults(fn=cmd_stop)
    c = t.add_parser("clean", help="remove the state directories of stopped dev hubs")
    c.add_argument("name", nargs="?", help="a stopped instance")
    c.add_argument("--all", action="store_true", help="every stopped instance")
    c.set_defaults(fn=cmd_clean)
