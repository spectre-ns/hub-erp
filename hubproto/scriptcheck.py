"""The allow-list check of a configuration script (issue #34), made in the hub before anything is sent to the worker.

No model takes part: the script is parsed with ``ast`` (never compiled or run here) and every node is held to a short
list. It is not the boundary (the run's kernel restrictions are: Landlock, seccomp and rlimits in
``hubproto/script_sandbox.py``, inside the script worker's container); it refuses most mistakes and attacks with a
sentence naming the line before anything runs, and it keeps scripts simple enough for a reviewer to read on the change
order. The run enforces the same names again (``ALLOWED_NAMES``: an imported module is a namespace of those names only).

``check(text)`` returns the refusals, each a plain sentence naming its line; empty means the script may be run.

Refused:

* ``import`` of anything but ``ALLOWED_IMPORTS`` (and a relative import); a name of an allowed module that is not in
  its list (``statistics.sys``, ``collections._sys``, ``collections.namedtuple``: the lists name only plain computing
  helpers); a module used other than as ``module.name``; ``typing`` (its ``TypeVar``, ``ForwardRef`` and
  ``get_type_hints`` evaluate text);
* ``eval``, ``exec``, ``compile``, ``__import__``, ``open``, ``getattr``, ``setattr``, ``delattr``, ``globals``,
  ``locals``, ``vars``, ``breakpoint``, ``input``, named anywhere (called or not);
* any dunder name or attribute (``__class__``, ``__subclasses__``, ``__globals__``, ``__builtins__``, ...), text that
  holds ``__`` or begins or ends with ``_`` (joined, ``"_" + "_"`` spells one), and ``chr``;
* the introspection attributes that reach frames and code (``gi_frame``, ``f_globals``, ``tb_frame``, ``mro``, ...);
* a class pattern in ``match`` (``case object(__class__=t)`` reads attributes by a name), and an annotation written as
  text (it can be evaluated);
* the names that evaluate text (``evaluate``, ``evaluate_bound``, ``ForwardRef``, ``TypeVar``, ``get_type_hints``).
"""

from __future__ import annotations

import ast

ALLOWED_IMPORTS = ("json", "math", "re", "datetime", "decimal", "collections", "itertools", "functools", "statistics",
                   "string")
# A submodule of an allowed module that a script may name in an import.
ALLOWED_SUBMODULES = ("collections.abc",)
# The names a script may use of each module (and of collections.abc, reached as collections.abc). Nothing else: no
# module a module imports (sys, os, operator), nothing that evaluates text (typing is not allowed at all: TypeVar's
# evaluate_bound, ForwardRef and get_type_hints run eval; collections.namedtuple builds its class with eval), no
# string.Formatter (get_field reaches attributes by a name in a string). The run also ends at any compile or exec
# once the script has started (the tripwire), so anything this list misses that evaluates text stops there.
ALLOWED_NAMES = {
    "json": ("JSONDecodeError", "JSONDecoder", "JSONEncoder", "dump", "dumps", "load", "loads"),
    "math": ("acos", "acosh", "asin", "asinh", "atan", "atan2", "atanh", "cbrt", "ceil", "comb", "copysign", "cos", "cosh",
             "degrees", "dist", "e", "erf", "erfc", "exp", "exp2", "expm1", "fabs", "factorial", "floor", "fma", "fmod",
             "frexp", "fsum", "gamma", "gcd", "hypot", "inf", "isclose", "isfinite", "isinf", "isnan", "isqrt", "lcm",
             "ldexp", "lgamma", "log", "log10", "log1p", "log2", "modf", "nan", "nextafter", "perm", "pi", "pow", "prod",
             "radians", "remainder", "sin", "sinh", "sqrt", "sumprod", "tan", "tanh", "tau", "trunc", "ulp"),
    "re": ("A", "ASCII", "DOTALL", "I", "IGNORECASE", "L", "LOCALE", "M", "MULTILINE", "NOFLAG", "PatternError", "RegexFlag",
           "S", "U", "UNICODE", "VERBOSE", "X", "compile", "error", "escape", "findall", "finditer", "fullmatch", "match",
           "search", "split", "sub", "subn"),
    "datetime": ("MAXYEAR", "MINYEAR", "UTC", "date", "datetime", "time", "timedelta", "timezone", "tzinfo"),
    "decimal": ("Clamped", "Context", "ConversionSyntax", "Decimal", "DecimalException", "DecimalTuple", "DivisionByZero",
                "DivisionImpossible", "DivisionUndefined", "FloatOperation", "Inexact", "InvalidContext", "InvalidOperation",
                "Overflow", "ROUND_05UP", "ROUND_CEILING", "ROUND_DOWN", "ROUND_FLOOR", "ROUND_HALF_DOWN", "ROUND_HALF_EVEN",
                "ROUND_HALF_UP", "ROUND_UP", "Rounded", "Subnormal", "Underflow", "getcontext", "localcontext"),
    "collections": ("ChainMap", "Counter", "OrderedDict", "UserDict", "UserList", "UserString", "abc", "defaultdict", "deque"),
    "collections.abc": ("Callable", "Collection", "Container", "Generator", "Hashable", "ItemsView", "Iterable", "Iterator",
                        "KeysView", "Mapping", "MappingView", "MutableMapping", "MutableSequence", "MutableSet", "Reversible",
                        "Sequence", "Set", "Sized", "ValuesView"),
    "itertools": ("accumulate", "batched", "chain", "combinations", "combinations_with_replacement", "compress", "count",
                  "cycle", "dropwhile", "filterfalse", "groupby", "islice", "pairwise", "permutations", "product", "repeat",
                  "starmap", "takewhile", "tee", "zip_longest"),
    "functools": ("cache", "cmp_to_key", "lru_cache", "partial", "reduce", "total_ordering"),
    "statistics": ("NormalDist", "StatisticsError", "correlation", "covariance", "fmean", "geometric_mean", "harmonic_mean",
                   "linear_regression", "mean", "median", "median_grouped", "median_high", "median_low", "mode", "multimode",
                   "pstdev", "pvariance", "quantiles", "stdev", "variance"),
    "string": ("Template", "ascii_letters", "ascii_lowercase", "ascii_uppercase", "capwords", "digits", "hexdigits",
               "octdigits", "printable", "punctuation", "whitespace"),
}
FORBIDDEN_NAMES = {
    "eval": "it runs text as code", "exec": "it runs text as code", "compile": "it makes code from text",
    "__import__": "it imports a module by a name in a string", "open": "a script reads the configuration it is given and opens no files",
    "getattr": "it reaches an attribute by a name: write x.name", "setattr": "it sets an attribute by a name: write x.name = value",
    "delattr": "it deletes an attribute by a name: write del x.name",
    "globals": "it reaches the script's namespace by name", "locals": "it reaches the script's namespace by name",
    "vars": "it reaches an object's namespace by name", "breakpoint": "it starts a debugger", "input": "a script asks nobody anything",
    "chr": "it builds text from numbers, which can spell a special name: write the text",
}
INTROSPECTION = {
    "mro", "gi_frame", "gi_code", "gi_yieldfrom", "cr_frame", "cr_code", "cr_await", "ag_frame", "ag_code", "f_globals",
    "f_locals", "f_builtins", "f_back", "f_code", "tb_frame", "tb_next", "co_code", "get_field", "Formatter",
}
# str.format and format_map read attributes and items by names written in the text ("{0.gi_frame}"): use an f-string,
# whose fields the check sees. (Text can still be built to spell anything: "%c" % 95, json.loads('"\\u005f"'), and
# more. That is not refused; the run's catch-all and its kernel restrictions are the boundary.)
FORMATS = {"format", "format_map"}
EVALUATES = {"evaluate", "evaluate_bound", "evaluate_constraints", "evaluate_default", "_evaluate", "TypeVar", "ForwardRef",
             "get_type_hints", "namedtuple"}
MAX_CHARS = 200_000
MAX_LINES = 5_000


def _dunder(name: str) -> bool:
    return name.startswith("__")


def _imported(name: str) -> bool:
    return name in ALLOWED_IMPORTS or name in ALLOWED_SUBMODULES


def _allowed_words() -> str:
    return ", ".join(ALLOWED_IMPORTS)


def check(text: str) -> list[str]:
    """Why the script may not be run, each a sentence naming its line (empty: it may)."""
    if not isinstance(text, str):
        return ["a script is Python source text"]
    if not text.strip():
        return ["the script is empty: it reads the configuration in force as `config` and leaves what it proposes in `result`"]
    if len(text) > MAX_CHARS:
        return [f"the script is {len(text):,} characters; a configuration script is at most {MAX_CHARS:,}"]
    if text.count("\n") + 1 > MAX_LINES:
        return [f"the script has more than {MAX_LINES:,} lines; split it, or make it simpler"]
    if "\x00" in text:
        return ["the script holds a NUL character: it is not Python source text"]
    try:
        tree = ast.parse(text, filename="<script>", mode="exec")
    except SyntaxError as e:
        return [f"Line {e.lineno or 1}: the script is not Python the hub can read ({e.msg})"]
    except (ValueError, RecursionError, MemoryError) as e:
        return [f"the script cannot be read as Python ({type(e).__name__})"]
    out: list[str] = []
    modules: dict[str, str] = {}  # a name the script binds -> the allowed module it is
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if _imported(a.name):
                    modules[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and not node.level and _imported(node.module or ""):
            for a in node.names:
                full = f"{node.module}.{a.name}"
                if full in ALLOWED_NAMES:
                    modules[a.asname or a.name] = full
    as_value = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute)}

    def module_of(node) -> str | None:
        """The allowed module an expression is (``json``, ``collections.abc``), or None."""
        if isinstance(node, ast.Name):
            return modules.get(node.id)
        if isinstance(node, ast.Attribute):
            base = module_of(node.value)
            if base and f"{base}.{node.attr}" in ALLOWED_NAMES and node.attr in ALLOWED_NAMES.get(base, ()):
                return f"{base}.{node.attr}"
        return None

    for node in ast.walk(tree):
        line = getattr(node, "lineno", None)
        at = f"Line {line}" if line else "The script"
        if isinstance(node, ast.Import):
            for a in node.names:
                if not _imported(a.name):
                    out.append(f"{at}: import {a.name} is not allowed: a script may import only {_allowed_words()}")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                out.append(f"{at}: a relative import is not allowed: a script may import only {_allowed_words()}")
            elif not _imported(node.module or ""):
                out.append(f"{at}: from {node.module} import ... is not allowed: a script may import only {_allowed_words()}")
            else:
                for a in node.names:
                    if a.name == "*":
                        out.append(f"{at}: from {node.module} import * is not allowed: name what the script uses")
                    elif a.name not in ALLOWED_NAMES.get(node.module, ()):
                        out.append(f"{at}: {node.module}.{a.name} is not allowed: {_names_words(node.module)}")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                out.append(f"{at}: {node.id} is not allowed: {FORBIDDEN_NAMES[node.id]}")
            elif _dunder(node.id):
                out.append(f"{at}: {node.id} is a special name: a script does not reach Python's internals")
            elif node.id in INTROSPECTION:
                out.append(f"{at}: {node.id} is not allowed: it reaches attributes by a name in a string")
            elif node.id in EVALUATES:
                out.append(f"{at}: {node.id} is not allowed: it evaluates text as code")
            elif node.id in modules and (id(node) not in as_value or not isinstance(node.ctx, ast.Load)):
                out.append(f"{at}: {node.id} is a module: use it only as {node.id}.name (a name of its list), never as a value")
        elif isinstance(node, ast.Attribute):
            base = module_of(node.value)
            if base is not None and node.attr not in ALLOWED_NAMES.get(base, ()):
                out.append(f"{at}: {base}.{node.attr} is not allowed: {_names_words(base)}")
            elif _dunder(node.attr):
                out.append(f"{at}: .{node.attr} is a special attribute: a script does not reach Python's internals")
            elif node.attr in INTROSPECTION:
                out.append(f"{at}: .{node.attr} is not allowed: it reaches frames, code or attributes by name")
            elif node.attr in EVALUATES:
                out.append(f"{at}: .{node.attr} is not allowed: it evaluates text as code")
            elif node.attr in FORMATS:
                out.append(f"{at}: .{node.attr} is not allowed: it reads attributes by names written in the text; use an f-string")
            elif base is not None and f"{base}.{node.attr}" in ALLOWED_NAMES and id(node) not in as_value:
                out.append(f"{at}: {base}.{node.attr} is a module: use it only as {base}.{node.attr}.name, never as a value")
        elif isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)) and _underscored(node.value):
            out.append(f"{at}: the text {node.value[:40]!r} holds two underscores together, or begins or ends with one: joined, "
                       "it can spell a special name (__class__); write names without them")
        elif isinstance(node, ast.MatchClass):
            out.append(f"{at}: a class pattern in match is not allowed: it reads attributes by name (match on values, lists "
                       "or dictionaries instead)")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and _dunder(node.name) and node.name not in (
                "__init__", "__repr__", "__str__", "__eq__", "__hash__", "__lt__", "__le__", "__gt__", "__ge__", "__len__",
                "__iter__", "__contains__", "__getitem__", "__post_init__"):
            out.append(f"{at}: defining {node.name} is not allowed: a script does not reach Python's internals")
        elif isinstance(node, ast.arg) and _dunder(node.arg):
            out.append(f"{at}: {node.arg} is a special name: a script does not reach Python's internals")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bad = [n for n in node.names if _dunder(n)]
            if bad:
                out.append(f"{at}: {', '.join(bad)} is a special name: a script does not reach Python's internals")
        for ann in _annotations(node):
            if any(isinstance(n, ast.Constant) and isinstance(n.value, str) for n in ast.walk(ann)):
                out.append(f"Line {ann.lineno}: an annotation written as text is not allowed: it can be evaluated as code")
    return list(dict.fromkeys(sorted(out, key=_line_of)))


def _underscored(v) -> bool:
    """Text that names a dunder, or could make one joined to other text: it holds "__", or begins or ends with "_"."""
    u = "_" if isinstance(v, str) else b"_"
    return (u + u) in v or v.startswith(u) or v.endswith(u)


def _annotations(node) -> list:
    if isinstance(node, ast.arg) and node.annotation is not None:
        return [node.annotation]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
        return [node.returns]
    if isinstance(node, ast.AnnAssign):
        return [node.annotation]
    return []


def _names_words(module: str) -> str:
    names = ALLOWED_NAMES.get(module, ())
    shown = ", ".join(names[:12]) + (", ..." if len(names) > 12 else "")
    return f"a script uses only the names of {module} on its list ({shown})"


def _line_of(sentence: str) -> tuple[int, str]:
    if sentence.startswith("Line "):
        head = sentence[5:].split(":", 1)[0]
        if head.isdigit():
            return int(head), sentence
    return 0, sentence
