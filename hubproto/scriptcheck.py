"""The allow-list check of a configuration script (issue #34), made in the hub before anything is sent to the worker.

No model takes part: the script is parsed with ``ast`` (never compiled or run here) and every node is held to a short
list. It is not the boundary (the script worker's operating system is: see ``hubproto/scriptworker.py``); it refuses
most mistakes and attacks with a sentence naming the line before anything runs, and it keeps scripts simple enough
for a reviewer to read on the change order.

``check(text)`` returns the refusals, each a plain sentence naming its line; empty means the script may be run.

Refused:

* ``import`` of anything but ``ALLOWED_IMPORTS`` (and a relative import);
* ``eval``, ``exec``, ``compile``, ``__import__``, ``open``, ``globals``, ``locals``, ``vars``, ``breakpoint``,
  ``input``, named anywhere (called or not);
* ``getattr``, ``setattr``, ``delattr`` other than called with a literal, non-dunder name;
* any dunder name or attribute (``__class__``, ``__subclasses__``, ``__globals__``, ``__builtins__``, ...);
* the introspection attributes that reach frames and code (``gi_frame``, ``f_globals``, ``tb_frame``, ``mro``, ...)
  and ``string.Formatter`` (its ``get_field`` reaches attributes by a name in a string).
"""

from __future__ import annotations

import ast

ALLOWED_IMPORTS = ("json", "math", "re", "datetime", "decimal", "collections", "itertools", "functools", "statistics",
                   "string", "typing")
# A submodule of an allowed module that a script may name in an import.
ALLOWED_SUBMODULES = ("collections.abc",)
FORBIDDEN_NAMES = {
    "eval": "it runs text as code", "exec": "it runs text as code", "compile": "it makes code from text",
    "__import__": "it imports a module by a name in a string", "open": "a script reads the configuration it is given and opens no files",
    "globals": "it reaches the script's namespace by name", "locals": "it reaches the script's namespace by name",
    "vars": "it reaches an object's namespace by name", "breakpoint": "it starts a debugger", "input": "a script asks nobody anything",
}
NAMED_ATTR = ("getattr", "setattr", "delattr")
INTROSPECTION = {
    "mro", "gi_frame", "gi_code", "gi_yieldfrom", "cr_frame", "cr_code", "cr_await", "ag_frame", "ag_code", "f_globals",
    "f_locals", "f_builtins", "f_back", "f_code", "tb_frame", "tb_next", "co_code", "get_field", "Formatter",
}
MAX_CHARS = 200_000
MAX_LINES = 5_000


def _dunder(name: str) -> bool:
    return name.startswith("__")


def _literal_name(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


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
    calls_named = set()  # getattr(x, "literal") calls: their Name node is allowed
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in NAMED_ATTR:
            name = _literal_name(node.args[1]) if len(node.args) >= 2 else None
            if name is None:
                out.append(f"Line {node.lineno}: {node.func.id} is given a computed name: write the attribute's name as text "
                           f"(as in {node.func.id}(x, \"name\")) or use x.name")
            elif _dunder(name):
                out.append(f"Line {node.lineno}: {node.func.id} names {name}, a special attribute: a script does not reach "
                           "Python's internals")
            calls_named.add(id(node.func))
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
            for a in node.names:
                if a.name != "*" and (_dunder(a.name) or a.name in INTROSPECTION):
                    out.append(f"{at}: importing {a.name} from {node.module} is not allowed: a script does not reach Python's internals")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                out.append(f"{at}: {node.id} is not allowed: {FORBIDDEN_NAMES[node.id]}")
            elif node.id in NAMED_ATTR and id(node) not in calls_named:
                out.append(f"{at}: {node.id} is used other than called with a literal name: call it as {node.id}(x, \"name\")")
            elif _dunder(node.id):
                out.append(f"{at}: {node.id} is a special name: a script does not reach Python's internals")
            elif node.id in INTROSPECTION:
                out.append(f"{at}: {node.id} is not allowed: it reaches attributes by a name in a string")
        elif isinstance(node, ast.Attribute):
            if _dunder(node.attr):
                out.append(f"{at}: .{node.attr} is a special attribute: a script does not reach Python's internals")
            elif node.attr in INTROSPECTION:
                out.append(f"{at}: .{node.attr} is not allowed: it reaches frames, code or attributes by name")
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
    return list(dict.fromkeys(sorted(out, key=_line_of)))


def _line_of(sentence: str) -> tuple[int, str]:
    if sentence.startswith("Line "):
        head = sentence[5:].split(":", 1)[0]
        if head.isdigit():
            return int(head), sentence
    return 0, sentence
