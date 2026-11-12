"""The child program a configuration script runs in (issue #34): ``python -I -S -B script_sandbox.py``.

Started by ``hubproto.scriptworker.run_local`` for one run, in an empty temporary directory, with no environment but a
minimal PATH. It imports nothing from the hub (the worker image ships it on its own) and uses the standard library
only. It reads one JSON request on standard input, then, in this order:

1. imports the allowed modules (and what they import lazily), before any limit, so the script finds them loaded;
2. sets hard limits (``resource.setrlimit``, soft and hard alike, so they cannot be raised again): CPU seconds,
   address space, file size, open files, processes (none: no fork) and core files;
3. installs a kernel filter (seccomp, Linux): from here on ``execve``/``execveat``, ``fork``/``vfork``/``clone``,
   ``socket``/``connect``/``bind``/..., ``kill``, ``ptrace``, ``mount``, ``unshare``, ``setns``, ``bpf`` and the
   other calls of ``BLOCKED`` fail with "operation not permitted", whatever Python does. This is the "no execve after
   start" that a container's seccomp profile cannot say (the container needs execve to start this very program);
4. installs an audit hook (``sys.addaudithook``, PEP 578) as a tripwire: ``os.system``, ``subprocess``, ``os.exec*``,
   spawn and fork, ``socket``, ``ctypes``, an ``open`` for writing outside the temporary directory (or for reading
   outside it and the standard library), an import of a module off the allow-list, frame introspection and the
   others of ``TRIP_*`` end the run at once and say why. PEP 578 says it is not a sandbox: it turns an attempt into a
   recorded failure; the boundary is the kernel's;
5. runs the script with ``config`` (the snapshot, parsed) in its namespace and builtins without ``open``, ``eval``,
   ``exec``, ``compile``, ``input`` and ``breakpoint``, and an ``__import__`` that refuses a module off the list;
6. writes one JSON object on standard output: the exit reason, the script's ``result`` as JSON text, and excerpts of
   what it printed (capped).

Nothing here is the hub: the hub never runs this code in its own process.
"""

import io
import json
import os
import sys

TRIP_EXACT = {
    "os.system", "os.fork", "os.forkpty", "os.posix_spawn", "os.posix_spawnp", "os.startfile", "os.kill", "os.killpg",
    "os.putenv", "os.unsetenv", "pty.spawn", "sys._getframe", "sys._current_frames", "sys._current_exceptions",
    "sys.settrace", "sys.setprofile", "sys.addaudithook", "gc.get_objects", "gc.get_referrers", "gc.get_referents",
    "marshal.load", "marshal.loads", "resource.setrlimit", "resource.prlimit", "mmap.__new__", "code.__new__",
    "function.__new__", "sys.remote_exec", "os.setuid", "os.setgid", "os.chroot", "os.mkfifo", "os.mknod",
}
TRIP_PREFIX = ("socket.", "ctypes.", "subprocess.", "os.exec", "os.spawn", "_posixsubprocess.", "winreg.", "_winapi.")
PATH_EVENTS = {  # event -> the argument positions that are paths the script changes (each must be in the temp dir)
    "os.remove": (0,), "os.rename": (0, 1), "os.rmdir": (0,), "os.mkdir": (0,), "os.chmod": (0,), "os.chown": (0,),
    "os.link": (0, 1), "os.symlink": (0, 1), "os.truncate": (0,), "os.utime": (0,), "shutil.rmtree": (0,),
    "shutil.copyfile": (1,), "shutil.copytree": (1,), "shutil.move": (0, 1), "os.chflags": (0,), "os.lchmod": (0,),
}
LIST_EVENTS = {"os.listdir", "os.scandir"}
# Python modules the allowed modules import lazily (on first use, not at import): loaded before the tripwire.
LAZY = ("_strptime", "traceback", "linecache", "copy", "fractions", "random", "numbers", "operator", "keyword",
        "reprlib", "heapq", "bisect", "contextlib", "enum", "types", "weakref", "abc", "textwrap", "warnings", "_colorize",
        "tokenize", "token", "codecs", "unicodedata", "locale", "calendar")
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
EXIT_TRIPPED = 3

# Calls the kernel filter refuses (x86_64 numbers, aarch64 numbers). Fork and clone included: the run starts nothing.
BLOCKED = {
    "execve": (59, 221), "execveat": (322, 281), "fork": (57, None), "vfork": (58, None), "clone": (56, 220),
    "clone3": (435, 435), "socket": (41, 198), "socketpair": (53, 199), "connect": (42, 203), "bind": (49, 200),
    "listen": (50, 201), "accept": (43, 202), "accept4": (288, 242), "ptrace": (101, 117), "mount": (165, 40),
    "umount2": (166, 39), "unshare": (272, 97), "setns": (308, 268), "bpf": (321, 280), "process_vm_readv": (310, 270),
    "process_vm_writev": (311, 271), "kexec_load": (246, 104), "init_module": (175, 105), "finit_module": (313, 273),
    "pivot_root": (155, 41), "chroot": (161, 51), "perf_event_open": (298, 241), "keyctl": (250, 219),
    "add_key": (248, 217), "request_key": (249, 218), "userfaultfd": (323, 282), "io_uring_setup": (425, 425),
    "open_tree": (428, 428), "move_mount": (429, 429), "fsopen": (430, 430), "fsmount": (432, 432),
    "mount_setattr": (442, 442), "personality": (135, 92), "kill": (62, 129), "tkill": (200, 130), "tgkill": (234, 131),
    "rt_sigqueueinfo": (129, 138), "rt_tgsigqueueinfo": (297, 240), "pidfd_open": (434, 434),
    "pidfd_send_signal": (424, 424), "pidfd_getfd": (438, 438),
}
AUDIT_ARCH = {"x86_64": (0xC000003E, 0), "aarch64": (0xC00000B7, 1)}


def install_kernel_filter() -> str:
    """Install the seccomp filter (``BLOCKED`` fails with EPERM from now on, in this process and anything it could
    start). Returns "installed", or why not ("unavailable: ..."). Uses ctypes once, then drops it from sys.modules."""
    machine = os.uname().machine
    if sys.platform != "linux" or machine not in AUDIT_ARCH:
        return f"unavailable: no seccomp filter for {sys.platform}/{machine}"
    try:
        import ctypes
    except ImportError as e:
        return f"unavailable: ctypes cannot be loaded ({e})"
    try:
        arch, col = AUDIT_ARCH[machine]
        ld_abs, jeq, jge, ret = 0x20, 0x15, 0x35, 0x06
        allow, errno_eperm, kill = 0x7FFF0000, 0x00050000 | 1, 0x80000000

        class Filter(ctypes.Structure):
            _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]

        class Prog(ctypes.Structure):
            _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

        prog = [(ld_abs, 0, 0, 4), (jeq, 1, 0, arch), (ret, 0, 0, kill), (ld_abs, 0, 0, 0)]
        if machine == "x86_64":  # the x32 ABI: the same calls with bit 30 set; refuse them all
            prog += [(jge, 0, 1, 0x40000000), (ret, 0, 0, errno_eperm)]
        for nums in BLOCKED.values():
            nr = nums[col]
            if nr is not None:
                prog += [(jeq, 0, 1, nr), (ret, 0, 0, errno_eperm)]
        prog += [(ret, 0, 0, allow)]
        arr = (Filter * len(prog))(*[Filter(*p) for p in prog])
        fprog = Prog(len(prog), arr)
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            return f"unavailable: no_new_privs refused (errno {ctypes.get_errno()})"
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
        if libc.prctl(22, 2, ctypes.addressof(fprog), 0, 0) != 0:  # PR_SET_SECCOMP, SECCOMP_MODE_FILTER
            return f"unavailable: the kernel refused the filter (errno {ctypes.get_errno()})"
        return "installed"
    except (OSError, AttributeError, TypeError, ValueError) as e:
        return f"unavailable: {type(e).__name__}: {e}"
    finally:
        for name in [m for m in sys.modules if m == "ctypes" or m.startswith(("ctypes.", "_ctypes"))]:
            sys.modules.pop(name, None)


class Capped(io.TextIOBase):
    """What the script prints: the first ``cap`` characters kept, the rest counted and dropped."""

    def __init__(self, cap: int):
        self.cap, self.parts, self.size, self.dropped = cap, [], 0, 0

    def writable(self):
        return True

    def write(self, s):
        s = str(s)
        room = self.cap - self.size
        if room > 0:
            self.parts.append(s[:room])
            self.size += min(len(s), room)
        self.dropped += max(0, len(s) - max(room, 0))
        return len(s)

    def text(self) -> str:
        return "".join(self.parts)


def _emit(obj: dict) -> None:
    data = (json.dumps(obj, ensure_ascii=True) + "\n").encode("ascii")
    while data:
        n = os.write(1, data)
        data = data[n:]


def main() -> None:
    raw = sys.stdin.buffer.read()
    req = json.loads(raw.decode("utf-8"))
    del raw
    script, snapshot_text = req["script"], req["snapshot"]
    allowed = frozenset(req["allowed"]) | frozenset(req.get("allowed_submodules") or ())
    allowed_top = frozenset(a.split(".")[0] for a in allowed)
    lim = req["limits"]
    tmp = os.path.realpath(os.getcwd())
    excerpt = int(lim.get("excerpt_chars") or 16000)

    import importlib

    for name in sorted(allowed) + list(LAZY):
        try:
            importlib.import_module(name)
        except ImportError:
            pass
    import traceback

    # What a run may read: the standard library (sys.path, under -I -S nothing else) and this file (a traceback's lines).
    roots = tuple(sorted({os.path.realpath(p) for p in sys.path if p and os.path.isabs(p)} | {os.path.realpath(__file__)}))
    code = compile(script, "<script>", "exec", dont_inherit=True)
    config = json.loads(snapshot_text)

    import resource

    def hard(which, value):
        resource.setrlimit(which, (int(value), int(value)))

    hard(resource.RLIMIT_CORE, 0)
    # CPU: SIGXCPU (which ends the run) at the limit, SIGKILL a second later should the script catch it.
    resource.setrlimit(resource.RLIMIT_CPU, (int(lim["cpu_seconds"]), int(lim["cpu_seconds"]) + 1))
    hard(resource.RLIMIT_FSIZE, lim["file_bytes"])
    hard(resource.RLIMIT_NOFILE, lim["open_files"])
    if hasattr(resource, "RLIMIT_NPROC"):
        hard(resource.RLIMIT_NPROC, 0)
    try:
        hard(resource.RLIMIT_AS, lim["memory_bytes"])
    except (ValueError, OSError):  # some systems refuse an address-space limit; the data limit then
        hard(resource.RLIMIT_DATA, lim["memory_bytes"])
    kernel = install_kernel_filter() if lim.get("kernel_filter", True) else "not asked for"

    out, err = Capped(excerpt), Capped(excerpt)

    def excerpts() -> dict:
        return {"stdout": out.text(), "stderr": err.text(), "stdout_dropped": out.dropped, "stderr_dropped": err.dropped}

    def under(path, bases) -> bool:
        try:
            if isinstance(path, bytes):
                path = os.fsdecode(path)
            p = os.path.realpath(os.fspath(path))
        except (TypeError, ValueError, OSError):
            return False
        return any(p == b or p.startswith(b.rstrip(os.sep) + os.sep) for b in bases)

    state = {"busy": False}

    def trip(event, args, why):
        state["busy"] = True
        try:
            shown = repr(args)[:300]
        except Exception:
            shown = "(arguments not shown)"
        _emit({"exit": "tripwire", "detail": f"the script tried {event} {shown}: {why}; the run was stopped",
               "tripped": {"event": event, "args": shown, "why": why}, "kernel_filter": kernel, **excerpts()})
        os._exit(EXIT_TRIPPED)

    def hook(event, args):
        if state["busy"]:
            return
        if event in TRIP_EXACT or event.startswith(TRIP_PREFIX):
            trip(event, args, "a configuration script computes; it starts nothing, opens no connection and reaches no internals")
        if event == "import":
            name = str(args[0]) if args else ""
            if name not in allowed and name.split(".")[0] not in allowed_top:
                trip(event, args[:1], f"{name} is not a module a script may import")
        elif event == "open":
            path, mode, flags = (list(args) + [None, None, None])[:3]
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                mode is None and isinstance(flags, int) and flags & WRITE_FLAGS)
            if isinstance(path, int):
                if writing:
                    trip(event, args, "a script writes no file descriptor; it leaves its answer in result")
            elif writing and not under(path, (tmp,)):
                trip(event, args, "a script writes only in its own temporary folder")
            elif not writing and not under(path, (tmp,) + roots):
                trip(event, args, "a script reads only the configuration it is given")
        elif event in LIST_EVENTS:
            path = args[0] if args else None
            if path is not None and not isinstance(path, int) and not under(path, (tmp,) + roots):
                trip(event, args, "a script reads only the configuration it is given")
        elif event in PATH_EVENTS:
            for i in PATH_EVENTS[event]:
                if i < len(args) and not isinstance(args[i], int) and not under(args[i], (tmp,)):
                    trip(event, args, "a script changes only its own temporary folder")

    import builtins

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or (name not in allowed and name.split(".")[0] not in allowed_top):
            raise ImportError(f"{name} is not a module a script may import")
        return real_import(name, globals, locals, fromlist, level)

    names = {k: v for k, v in vars(builtins).items() if k not in (
        "open", "eval", "exec", "compile", "input", "breakpoint", "help", "exit", "quit", "copyright", "credits", "license")}
    names["__import__"] = guarded_import
    ns = {"__name__": "__script__", "__builtins__": names, "config": config}

    sys.stdout, sys.stderr, sys.stdin = out, err, io.StringIO("")
    sys.addaudithook(hook)
    reason, detail = "ok", "the script finished"
    try:
        # The one exec of the hub's code base: the script's own, in this child process, which the hub never imports.
        exec(code, ns)
    except SystemExit:
        pass
    except MemoryError:
        ns.clear()
        reason, detail = "memory-limit", f"the script ran out of memory (the limit is {lim['memory_bytes'] // (1024 * 1024)} MB)"
    except RecursionError:
        reason, detail = "error", "the script recursed too deeply"
    except BaseException as e:  # the script's own error: its traceback is what the person reads
        reason = "error"
        tb = traceback.format_exception(type(e), e, e.__traceback__)
        lines = [ln for ln in tb if 'File "<script>"' in ln or not ln.startswith("  File ")]
        detail = f"the script stopped with {type(e).__name__}: {str(e)[:500]}"
        err.write("".join(lines)[-4000:])
    result_text = None
    if reason == "ok":
        if "result" not in ns:
            reason, detail = "no-result", "the script left no result: set result to the configuration it proposes (the whole document or a part)"
        else:
            try:
                result_text = json.dumps(ns["result"], ensure_ascii=False, allow_nan=False, sort_keys=True)
            except (TypeError, ValueError, RecursionError) as e:
                reason, detail = "bad-result", f"the script's result is not JSON data ({type(e).__name__}: {str(e)[:200]})"
            except MemoryError:
                reason, detail = "memory-limit", "the script's result is too large to write"
    state["busy"] = True
    _emit({"exit": reason, "detail": detail, "result": result_text, "kernel_filter": kernel, **excerpts()})
    os._exit(0)


if __name__ == "__main__":
    main()
