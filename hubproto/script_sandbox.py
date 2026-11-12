"""The child program a configuration script runs in (issue #34): ``python -I -S -B script_sandbox.py CTL_FD``.

Started by ``hubproto.scriptworker.run_local`` for one run, in a fresh private temporary directory, with no
environment but a minimal PATH. It imports nothing from the hub (the worker image ships it on its own) and uses the
standard library only. The protocol, every message a frame (8-byte big-endian length, then JSON):

1. it reads the **setup** frame on standard input (the allowed modules and names, the limits);
2. it imports the allowed modules (and what they import lazily), before any restriction;
3. it **protects itself**, in the kernel, before any of the script is in the process. If any step fails it says so and
   exits, and the run is refused (fail closed):
   * hard limits (``resource.setrlimit``, soft and hard alike): CPU seconds, address space, file size, open files,
     processes (none) and core files (the allow-list below then refuses setrlimit and prlimit64 altogether);
   * ``PR_SET_DUMPABLE`` 0: another process of the same user cannot open its ``/proc/<pid>/fd``, ``mem`` or
     ``environ``;
   * **Landlock** (the highest ABI the kernel has): from here on it may read only the standard library, the
     interpreter and this file, and read and write only its own temporary directory. Nothing else: not ``/proc``, not
     the hub's state, not the worker's socket. Also no TCP and, from ABI 6, no signals or abstract sockets outside it;
   * **seccomp, as an allow-list** (``ALLOWED_CALLS``): every call not on it fails with EPERM, so nothing reaches
     another process (``kill``, ``prlimit64``, ``setpriority``, ``fcntl(F_SETOWN)``), starts one, opens a connection,
     changes metadata, or undoes the protection (``prctl``, ``seccomp``, ``setrlimit``); ``fcntl`` and ``ioctl`` only
     for a few commands;
4. it writes the **handshake** frame on the control descriptor (CTL_FD) and waits: the parent checks the protection
   itself (``/proc/<pid>/status``: Seccomp, filters, NoNewPrivs, and that ``/proc/<pid>`` is no longer the user's)
   before it sends anything of the script. What the handshake says was written before the script existed;
5. it reads the **job** frame (the script and the snapshot), installs the audit-hook tripwire (PEP 578: not a
   sandbox, a tripwire that turns an attempt into a recorded failure), and runs the script with ``config`` (the
   snapshot, parsed) in its namespace, builtins without ``open``, ``eval``, ``exec``, ``compile``, ``getattr``,
   ``setattr``, ``delattr``, ``vars``, ``globals``, ``locals``, ``input`` and ``breakpoint``, and an ``__import__``
   that gives each allowed module as a namespace of its allowed names only;
6. it writes the **answer** frame on the control descriptor: the script's result as JSON text and excerpts of what it
   printed. The parent treats all of it as the script's own output (it ran in the same process); the protection it
   records is what it checked in step 4.

Nothing here is the hub: the hub never runs this code in its own process.
"""

import io
import json
import os
import struct
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
# Where an event's dir_fd arguments are (-1 when none was given).
DIR_FD_AT = {"os.chmod": (2,), "os.chown": (3,), "os.utime": (3,), "os.remove": (1,), "os.rename": (2, 3), "os.rmdir": (1,),
             "os.mkdir": (2,), "os.link": (2, 3), "os.symlink": (2,)}
LIST_EVENTS = {"os.listdir", "os.scandir"}
# Python modules the allowed modules import lazily (on first use, not at import): loaded before the restrictions.
LAZY = ("_strptime", "traceback", "linecache", "copy", "fractions", "random", "numbers", "operator", "keyword",
        "reprlib", "heapq", "bisect", "contextlib", "enum", "types", "weakref", "abc", "textwrap", "warnings", "_colorize",
        "tokenize", "token", "codecs", "unicodedata", "locale", "calendar", "encodings.idna", "_pydatetime", "_pydecimal", "_suggestions", "typing",
        # text codecs, which bytes.decode and str.encode load on first use (another codec is refused: its import trips)
        "encodings.utf_8", "encodings.utf_8_sig", "encodings.latin_1", "encodings.ascii", "encodings.utf_16",
        "encodings.utf_16_le", "encodings.utf_16_be", "encodings.utf_32", "encodings.cp1252", "encodings.cp437",
        "encodings.iso8859_15", "encodings.unicode_escape", "encodings.raw_unicode_escape", "encodings.hex_codec")
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
EXIT_TRIPPED, EXIT_UNPROTECTED = 3, 4
MAX_FRAME = 64 * 1024 * 1024

# The run's own seccomp filter is an ALLOW-LIST: every call not named here fails with EPERM (and another architecture,
# or the x32 ABI, kills the process). What is here is what CPython needs, once the script has been sent, to run a
# pure computation with the allowed modules, use its own folder and write its answer, measured by tracing the run
# (strace -f, tests/test_config_scripts.py's representative scripts, the worker image), plus a small margin of calls
# that touch nothing but the process itself. Nothing here acts on another process (no kill, prlimit64, setpriority,
# sched_set*, ioprio_set, ptrace, process_vm_*), starts one (execve, fork, clone), reaches out (socket, connect), or
# changes metadata (chmod, chown, utime, xattrs, truncate), and nothing changes the process's own protection (prctl,
# seccomp, setrlimit). (x86_64 number, aarch64 number); None where the architecture has no such call.
ALLOWED_CALLS = {
    # reading: the standard library, for a module an allowed one imports lazily (open and openat only read-only, below)
    "read": (0, 63), "pread64": (17, 67), "readv": (19, 65), "close": (3, 57), "lseek": (8, 62), "fstat": (5, 80),
    "stat": (4, None), "lstat": (6, None), "newfstatat": (262, 79), "statx": (332, 291), "getdents64": (217, 61),
    "getcwd": (79, 17), "readlink": (89, None), "readlinkat": (267, 78), "access": (21, None), "faccessat": (269, 48),
    "faccessat2": (439, 439),
    # writing: only to descriptors it already holds (its answer, its standard output)
    "write": (1, 64), "writev": (20, 66),
    # memory
    "mmap": (9, 222), "munmap": (11, 215), "mremap": (25, 216), "mprotect": (10, 226), "madvise": (28, 233),
    "brk": (12, 214),
    # its own signals, time, identity, randomness, the end
    "rt_sigaction": (13, 134), "rt_sigprocmask": (14, 135), "rt_sigreturn": (15, 139), "sigaltstack": (131, 132),
    "futex": (202, 98), "sched_yield": (24, 124), "clock_gettime": (228, 113), "clock_getres": (229, 114),
    "gettimeofday": (96, 169), "time": (201, None), "nanosleep": (35, 101), "clock_nanosleep": (230, 115),
    "getpid": (39, 172), "getppid": (110, 173), "gettid": (186, 178), "getuid": (102, 174), "geteuid": (107, 175),
    "getgid": (104, 176), "getegid": (108, 177), "uname": (63, 160), "getrandom": (318, 278), "getrusage": (98, 165),
    "times": (100, 153), "restart_syscall": (219, 128), "exit": (60, 93), "exit_group": (231, 94),
}
# open and openat only to read: no O_WRONLY, O_RDWR, O_CREAT, O_TRUNC or O_APPEND (args[1] for open, args[2] for openat).
OPEN_READ_ONLY = {"open": ((2, None), 1), "openat": ((257, 56), 2)}
OPEN_WRITE_FLAGS = 0o1 | 0o2 | 0o100 | 0o1000 | 0o2000
# fcntl and ioctl are allowed only for these commands (args[1]); F_SETFL only without O_ASYNC (args[2]), so no
# F_SETOWN, F_SETOWN_EX, F_SETSIG, F_SETLEASE, F_NOTIFY, FIOSETOWN, SIOCSPGRP or FIOASYNC: no signal to anyone.
FCNTL = {"fcntl": (72, 25)}
FCNTL_ALLOWED = {"F_GETFD": 1, "F_SETFD": 2, "F_GETFL": 3, "F_DUPFD_CLOEXEC": 1030}
F_SETFL, O_ASYNC = 4, 0o20000
IOCTL = {"ioctl": (16, 29)}
IOCTL_ALLOWED = {"TCGETS": 0x5401, "FIOCLEX": 0x5451, "FIONCLEX": 0x5450}  # isatty; set_inheritable
LANDLOCK_MIN_ABI = 3  # ABI 3 is the first that controls truncate; below it a run is refused (fail closed)
AUDIT_ARCH = {"x86_64": (0xC000003E, 0), "aarch64": (0xC00000B7, 1)}
# Landlock (the same numbers on x86_64 and aarch64): landlock_create_ruleset, landlock_add_rule, landlock_restrict_self.
LL_CREATE, LL_ADD, LL_RESTRICT = 444, 445, 446
LL_FS_BITS_BY_ABI = {1: 13, 2: 14, 3: 15, 4: 15}  # how many file-system rights an ABI knows (2 adds REFER, 3 TRUNCATE, 5 IOCTL_DEV)
LL_READ = (1 << 0) | (1 << 2) | (1 << 3)  # EXECUTE, READ_FILE, READ_DIR
LL_FILE_ONLY = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 14) | (1 << 15)  # the rights a rule on a file (not a directory) may name


def _libc():
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return ctypes, libc


def _drop_ctypes() -> None:
    for name in [m for m in sys.modules if m == "ctypes" or m.startswith(("ctypes.", "_ctypes"))]:
        sys.modules.pop(name, None)


def not_dumpable(dumpable_too: bool = True) -> str:
    """no_new_privs, then PR_SET_DUMPABLE 0: this process's /proc entries become root's, closed to every other process
    of its user. Returns "off" or why not."""
    if sys.platform != "linux":
        return f"unavailable: no prctl on {sys.platform}"
    ctypes, libc = _libc()
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS (Landlock and seccomp both need it)
        return f"refused: no_new_privs (errno {ctypes.get_errno()})"
    if not dumpable_too:
        return "left out"
    if libc.prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE
        return f"refused: dumpable (errno {ctypes.get_errno()})"
    if libc.prctl(3, 0, 0, 0, 0) != 0:  # PR_GET_DUMPABLE
        return "refused: the process is still dumpable"
    return "off"


def install_landlock(read_dirs, read_files, write_dirs, cap: int = 0) -> tuple[int, str]:
    """Restrict this process's file access in the kernel: read (and execute) only under ``read_dirs`` and the files
    ``read_files``; everything only under ``write_dirs``; nothing else. Returns (ABI, "installed") or (0, why not)."""
    if sys.platform != "linux":
        return 0, f"unavailable: no Landlock on {sys.platform}"
    try:
        ctypes, libc = _libc()
    except (ImportError, OSError) as e:
        return 0, f"unavailable: ctypes cannot be loaded ({e})"

    class RulesetAttr(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64), ("scoped", ctypes.c_uint64)]

    abi = libc.syscall(ctypes.c_long(LL_CREATE), None, ctypes.c_size_t(0), ctypes.c_uint32(1))  # LANDLOCK_CREATE_RULESET_VERSION
    if abi < 1:
        return 0, f"unavailable: the kernel has no Landlock (errno {ctypes.get_errno()})"
    if cap:  # tests only: behave as a kernel of an older ABI would (the rights it knows, and its number)
        abi = min(abi, cap)
    if abi < LANDLOCK_MIN_ABI:
        return abi, (f"refused: Landlock ABI {abi} does not control truncate; a run needs ABI {LANDLOCK_MIN_ABI} or later "
                     "(Linux 6.2)")
    every = (1 << LL_FS_BITS_BY_ABI.get(abi, 16)) - 1
    # net (ABI 4): BIND_TCP, CONNECT_TCP; scoped (ABI 6): abstract Unix sockets and signals outside the domain.
    attr = RulesetAttr(every, 3 if abi >= 4 else 0, 3 if abi >= 6 else 0)
    size = 24 if abi >= 6 else (16 if abi >= 4 else 8)
    rfd = libc.syscall(ctypes.c_long(LL_CREATE), ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0))
    if rfd < 0:
        return 0, f"refused: landlock_create_ruleset (errno {ctypes.get_errno()})"
    try:
        rules = [(p, LL_READ & every) for p in read_dirs] + [(p, LL_READ & every & LL_FILE_ONLY) for p in read_files]
        rules += [(p, every) for p in write_dirs]
        for path, access in rules:
            try:
                fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            except OSError:
                continue  # a sys.path entry that does not exist (the stdlib zip)
            try:
                rule = ctypes.create_string_buffer(struct.pack("=Qi", access, fd), 12)  # struct landlock_path_beneath_attr (packed)
                if libc.syscall(ctypes.c_long(LL_ADD), ctypes.c_int(rfd), ctypes.c_int(1), rule, ctypes.c_uint32(0)) != 0:
                    return 0, f"refused: landlock_add_rule {path} (errno {ctypes.get_errno()})"
            finally:
                os.close(fd)
        if libc.syscall(ctypes.c_long(LL_RESTRICT), ctypes.c_int(rfd), ctypes.c_uint32(0)) != 0:
            return 0, f"refused: landlock_restrict_self (errno {ctypes.get_errno()})"
    finally:
        os.close(rfd)
    return abi, "installed"


def filter_program(machine: str) -> list[tuple[int, int, int, int]]:
    """The allow-list as a classic BPF program (code, jt, jf, k) for ``machine`` ("x86_64" or "aarch64")."""
    arch, col = AUDIT_ARCH[machine]
    ld_abs, jeq, jge, jset, ret = 0x20, 0x15, 0x35, 0x45, 0x06
    allow, deny, kill = 0x7FFF0000, 0x00050000 | 1, 0x80000000  # SECCOMP_RET_ALLOW, ERRNO(EPERM), KILL_PROCESS
    lo, hi = (lambda i: 16 + 8 * i), (lambda i: 20 + 8 * i)  # struct seccomp_data: args[i], little-endian halves
    prog = [(ld_abs, 0, 0, 4), (jeq, 1, 0, arch), (ret, 0, 0, kill), (ld_abs, 0, 0, 0)]
    if machine == "x86_64":  # the x32 ABI: the same numbers with bit 30 set
        prog += [(jge, 0, 1, 0x40000000), (ret, 0, 0, kill)]
    for nums in ALLOWED_CALLS.values():
        if nums[col] is not None:
            prog += [(jeq, 0, 1, nums[col]), (ret, 0, 0, allow)]

    def by_command(nr: int, allowed: list[int], setfl: bool) -> list:
        # args[1] (the command) must fit in 32 bits and be one of ``allowed``; F_SETFL only without O_ASYNC in args[2].
        body = [(ld_abs, 0, 0, hi(1)), None, (ld_abs, 0, 0, lo(1))]
        for c in allowed:
            body += [(jeq, 0, 1, c), (ret, 0, 0, allow)]
        if setfl:
            # == F_SETFL: load args[2]; O_ASYNC set -> deny, else allow. != F_SETFL: on to the final deny.
            body += [(jeq, 0, 4, F_SETFL), (ld_abs, 0, 0, lo(2)), (jset, 0, 1, O_ASYNC), (ret, 0, 0, deny), (ret, 0, 0, allow)]
        body += [(ret, 0, 0, deny)]
        body[1] = (jeq, 0, len(body) - 3, 0)  # high half not 0: to the block's final deny
        return [(jeq, 0, len(body), nr)] + body  # not this call: skip the block

    for (nums, flags_at) in OPEN_READ_ONLY.values():  # any write flag: deny; else allow
        if nums[col] is not None:
            prog += [(jeq, 0, 4, nums[col]), (ld_abs, 0, 0, lo(flags_at)), (jset, 0, 1, OPEN_WRITE_FLAGS), (ret, 0, 0, deny),
                     (ret, 0, 0, allow)]
    prog += by_command(FCNTL["fcntl"][col], sorted(FCNTL_ALLOWED.values()), True)
    prog += by_command(IOCTL["ioctl"][col], sorted(IOCTL_ALLOWED.values()), False)
    prog += [(ret, 0, 0, deny)]
    return prog


def install_kernel_filter() -> str:
    """Install the seccomp allow-list (``filter_program``): from now on every call not on it fails with EPERM, in this
    process and anything it could start. Returns "installed", or why not. Needs no_new_privs (``not_dumpable`` sets it)."""
    machine = os.uname().machine
    if sys.platform != "linux" or machine not in AUDIT_ARCH:
        return f"unavailable: no seccomp filter for {sys.platform}/{machine}"
    try:
        ctypes, libc = _libc()
    except (ImportError, OSError) as e:
        return f"unavailable: ctypes cannot be loaded ({e})"
    try:
        prog = filter_program(machine)

        class Filter(ctypes.Structure):
            _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]

        class Prog(ctypes.Structure):
            _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

        arr = (Filter * len(prog))(*[Filter(*p) for p in prog])
        fprog = Prog(len(prog), arr)
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
        if libc.prctl(22, 2, ctypes.addressof(fprog), 0, 0) != 0:  # PR_SET_SECCOMP, SECCOMP_MODE_FILTER
            return f"refused: the kernel refused the filter (errno {ctypes.get_errno()})"
        return "installed"
    except (OSError, AttributeError, TypeError, ValueError) as e:
        return f"refused: {type(e).__name__}: {e}"


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


def write_frame(fd: int, obj: dict) -> None:
    data = json.dumps(obj, ensure_ascii=True).encode("ascii")
    data = struct.pack(">Q", len(data)) + data
    while data:
        n = os.write(fd, data)
        data = data[n:]


def read_frame(fd: int) -> dict:
    def exactly(n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = os.read(fd, min(n - len(buf), 1 << 20))
            if not chunk:
                raise EOFError("the frame ended early")
            buf += chunk
        return buf

    (n,) = struct.unpack(">Q", exactly(8))
    if n > MAX_FRAME:
        raise ValueError("the frame is too large")
    return json.loads(exactly(n).decode("utf-8"))


def _proxies(names: dict) -> dict:
    """Each allowed module as a namespace of its allowed names only (``typing.sys`` is not there)."""
    import types

    out = {}
    for mod in sorted(names, key=lambda m: -m.count(".")):  # collections.abc before collections
        real = sys.modules.get(mod)
        if real is None:
            continue
        ns = types.SimpleNamespace()
        for n in names[mod]:
            if f"{mod}.{n}" in out:
                setattr(ns, n, out[f"{mod}.{n}"])
            elif hasattr(real, n):
                setattr(ns, n, getattr(real, n))
        out[mod] = ns
    return out


def _make_hook(tmp: str, roots: tuple, allowed: frozenset, allowed_top: frozenset, ctl: int, excerpts, script_code):
    """The tripwire, built in a scope of its own: no frame the script can reach holds its state. Once installed it
    allows one exec, the script's own (``script_code``); any other compile or exec ends the run, so text evaluated
    through any API (``eval``, ``ForwardRef.evaluate``, ``namedtuple``) stops there, whatever the lists miss."""
    tripping = []
    ran = []

    def under(path, bases) -> bool:
        try:
            if isinstance(path, bytes):
                path = os.fsdecode(path)
            p = os.path.realpath(os.fspath(path))
        except (TypeError, ValueError, OSError):
            return False
        return any(p == b or p.startswith(b.rstrip(os.sep) + os.sep) for b in bases)

    def trip(event, args, why):
        tripping.append(event)
        try:
            shown = repr(args)[:300]
        except Exception:
            shown = "(arguments not shown)"
        write_frame(ctl, {"exit": "tripwire", "detail": f"the script tried {event} {shown}: {why}; the run was stopped",
                          "tripped": {"event": event, "args": shown, "why": why}, **excerpts()})
        os._exit(EXIT_TRIPPED)

    def hook(event, args):
        if tripping:
            return
        if event == "exec" and not ran and args and args[0] is script_code:
            ran.append(True)  # the script's own exec, the one allowed
            return
        if event in ("compile", "exec", "cpython.run_command", "cpython.run_file", "cpython.run_module"):
            trip(event, args[1:2] if event == "compile" else (), "the script evaluated text as code; a configuration script never does")
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
            # A path relative to a dir_fd is resolved from that descriptor, not from the cwd: refuse any dir_fd.
            if any(i < len(args) and isinstance(args[i], int) and args[i] not in (-1, -100) for i in DIR_FD_AT.get(event, ())):
                trip(event, args, "a script changes only its own temporary folder, by name, never relative to a descriptor")
            for i in PATH_EVENTS[event]:
                if i < len(args) and not isinstance(args[i], int) and not under(args[i], (tmp,)):
                    trip(event, args, "a script changes only its own temporary folder")

    return hook


def _said(e: BaseException) -> str:
    try:
        return str(e)
    except Exception:
        return "(its message cannot be shown)"


def _where(e: BaseException, script: str) -> str:
    """The script's lines the error passed through, without the traceback module (which compiles source to point at
    the error, and any compile ends the run)."""
    src = script.split("\n")
    out, t = ["Traceback (the script's lines):"], e.__traceback__
    while t is not None:
        if t.tb_frame.f_code.co_filename == "<script>":
            n = t.tb_lineno or 0
            out.append(f"  line {n}: {src[n - 1].strip() if 0 < n <= len(src) else ''}")
        t = t.tb_next
    out.append(f"{type(e).__name__}: {_said(e)}")
    return "\n".join(out) + "\n"


def protect(lim: dict, read_dirs, read_files, tmp: str, skip=(), claim: bool = False, abi_cap: int = 0) -> dict:
    """Every kernel restriction, in order; what each gave. ``skip`` and ``claim`` are for tests only (a layer left out,
    and said to be there anyway), to show the run is refused and the parent's own check catches the claim."""
    import resource

    def hard(which, value):
        resource.setrlimit(which, (int(value), int(value)))

    protection = {}
    if os.geteuid() == 0:
        protection["user"] = "refused: a script never runs as root"
    try:
        hard(resource.RLIMIT_CORE, 0)
        # CPU: SIGXCPU (which ends the run) at the limit, SIGKILL a second later should the script catch it.
        resource.setrlimit(resource.RLIMIT_CPU, (int(lim["cpu_seconds"]), int(lim["cpu_seconds"]) + 1))
        hard(resource.RLIMIT_FSIZE, lim["file_bytes"])
        hard(resource.RLIMIT_NOFILE, lim["open_files"])
        hard(resource.RLIMIT_NPROC, 0)
        try:
            hard(resource.RLIMIT_AS, lim["memory_bytes"])
        except (ValueError, OSError):  # some systems refuse an address-space limit; the data limit then
            hard(resource.RLIMIT_DATA, lim["memory_bytes"])
        protection["rlimits"] = "set"
    except (ValueError, OSError, AttributeError) as e:
        protection["rlimits"] = f"refused: {e}"
    left_out = "installed" if claim else "left out"
    try:
        protection["dumpable"] = not_dumpable("dumpable" not in skip)
        if "dumpable" in skip and claim:
            protection["dumpable"] = "off"
        abi, why = install_landlock(read_dirs, read_files, [tmp], abi_cap) if "landlock" not in skip else ((7 if claim else 0), left_out)
        protection["landlock"], protection["landlock_abi"] = why, abi
        protection["seccomp"] = install_kernel_filter() if "seccomp" not in skip else left_out
    except (ImportError, OSError) as e:
        protection.setdefault("landlock", f"refused: {e}")
        protection.setdefault("seccomp", f"refused: {e}")
    finally:
        _drop_ctypes()
    return protection


def protected(p: dict) -> bool:
    return (p.get("rlimits") == "set" and p.get("dumpable") == "off" and p.get("landlock") == "installed"
            and p.get("seccomp") == "installed" and "user" not in p)


def main() -> None:
    ctl = int(sys.argv[1])
    setup = read_frame(0)
    names = {k: tuple(v) for k, v in setup["names"].items()}
    allowed = frozenset(setup["allowed"]) | frozenset(setup.get("allowed_submodules") or ())
    allowed_top = frozenset(a.split(".")[0] for a in allowed)
    lim = setup["limits"]
    tmp = os.path.realpath(os.getcwd())
    excerpt = int(lim.get("excerpt_chars") or 16000)

    import importlib
    import time

    for name in sorted(allowed) + list(LAZY):
        try:
            importlib.import_module(name)
        except ImportError:
            pass
    import builtins

    time.localtime()  # the time zone is read now: after Landlock nothing outside the run's folder is
    proxies = _proxies(names)
    read_dirs = sorted({os.path.realpath(p) for p in sys.path if p and os.path.isabs(p) and os.path.isdir(p)})
    read_files = sorted({os.path.realpath(__file__), os.path.realpath(sys.executable)})
    roots = tuple(read_dirs) + tuple(read_files)

    protection = protect(lim, read_dirs, read_files, tmp, skip=tuple(setup.get("test_skip") or ()), claim=bool(setup.get("test_claim")),
                         abi_cap=int(setup.get("test_abi_cap") or 0))
    ok = protected(protection)
    write_frame(ctl, {"ready": ok, "protection": protection})
    if not ok:  # fail closed: no script runs in a process that is not protected
        os._exit(EXIT_UNPROTECTED)

    job = read_frame(0)
    script, snapshot_text = job["script"], job["snapshot"]
    del job
    out, err = Capped(excerpt), Capped(excerpt)

    def excerpts() -> dict:
        return {"stdout": out.text(), "stderr": err.text(), "stdout_dropped": out.dropped, "stderr_dropped": err.dropped}

    real_import = builtins.__import__

    class Refused:
        """What an import of a module off the list gives the script: nothing to use. (C code that imports through the
        script's builtins, such as datetime.strptime loading _strptime, takes the module from sys.modules itself.)"""

        def __init__(self, name):
            self._name = name

        def __getattr__(self, attr):
            raise ImportError(f"{object.__getattribute__(self, '_name')} is not a module a script may import")

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or (name not in allowed and name.split(".")[0] not in allowed_top):
            if not level and name in sys.modules and not fromlist:
                return Refused(name)
            raise ImportError(f"{name} is not a module a script may import")
        real_import(name, None, None, fromlist, 0)
        key = name if fromlist else name.split(".")[0]
        if key not in proxies:
            raise ImportError(f"{name} is not a module a script may import")
        return proxies[key]

    names_ = {k: v for k, v in vars(builtins).items() if k not in (
        "open", "eval", "exec", "compile", "input", "breakpoint", "help", "exit", "quit", "copyright", "credits", "license",
        "getattr", "setattr", "delattr", "vars", "globals", "locals")}
    names_["__import__"] = guarded_import
    reason, detail = "ok", "the script finished"
    try:
        code = compile(script, "<script>", "exec", dont_inherit=True)
        config = json.loads(snapshot_text)
    except (SyntaxError, ValueError) as e:
        write_frame(ctl, {"exit": "error", "detail": f"the script or the snapshot cannot be read ({type(e).__name__})", **excerpts()})
        os._exit(0)
    ns = {"__name__": "__script__", "__builtins__": names_, "config": config}
    del config, snapshot_text
    if setup.get("tripwire", True):
        sys.addaudithook(_make_hook(tmp, roots, allowed, allowed_top, ctl, excerpts, code))
    sys.stdout, sys.stderr, sys.stdin = out, err, io.StringIO("")
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
    except BaseException as e:  # the script's own error: where it stopped in the script is what the person reads
        reason = "error"
        detail = f"the script stopped with {type(e).__name__}: {_said(e)[:500]}"
        err.write(_where(e, script)[-4000:])
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
    write_frame(ctl, {"exit": reason, "detail": detail, "result": result_text, **excerpts()})
    os._exit(0)


if __name__ == "__main__":
    main()
