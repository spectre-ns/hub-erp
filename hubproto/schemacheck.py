"""A configuration document held to its JSON Schema (``docs/design/config-json.schema.json``), standard library only.

Reads the keywords that schema uses (``type``, ``required``, ``properties``, ``additionalProperties``, ``const``,
``enum``, ``items``, ``minimum``, ``maximum``, ``pattern``, ``uniqueItems`` and local ``$ref``); a keyword it does
not know is ignored, as JSON Schema says of unknown keywords. ``problems(doc)`` gives a sentence per place the document
does not match, empty when it matches. A script's output (issue #34) is held to it before ``configjson.propose``,
which then makes the hub's own checks (versions, names, rules, record types, forms, links, retention).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "docs" / "design" / "config-json.schema.json"
MAX_PROBLEMS = 20
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _is(value, t: str) -> bool:
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, TYPES.get(t, object))


def _ref(root: dict, ref: str) -> dict:
    if not ref.startswith("#/"):
        return {}
    node = root
    for part in ref[2:].split("/"):
        node = node.get(part, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def _check(value, s: dict, where: str, root: dict, out: list[str], depth: int = 0) -> None:
    if len(out) >= MAX_PROBLEMS or depth > 80 or not isinstance(s, dict):
        return
    if "$ref" in s:
        _check(value, _ref(root, s["$ref"]), where, root, out, depth + 1)
    w = where or "the document"
    t = s.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_is(value, x) for x in ts):
            out.append(f"{w} is {_kind(value)}; the format says {' or '.join(ts)}")
            return
    if "const" in s and value != s["const"]:
        out.append(f"{w} is {json.dumps(value)[:60]}; the format says {json.dumps(s['const'])}")
    if "enum" in s and value not in s["enum"]:
        out.append(f"{w} is {json.dumps(value)[:60]}; the format says one of {', '.join(json.dumps(x) for x in s['enum'])}")
    if isinstance(value, str) and "pattern" in s and not re.search(s["pattern"], value):
        out.append(f"{w} {json.dumps(value)[:60]} is not written as the format says ({s['pattern']})")
    if _is(value, "number"):
        if "minimum" in s and value < s["minimum"]:
            out.append(f"{w} is {value}; the format says at least {s['minimum']}")
        if "maximum" in s and value > s["maximum"]:
            out.append(f"{w} is {value}; the format says at most {s['maximum']}")
    if isinstance(value, dict):
        for k in s.get("required") or []:
            if k not in value:
                out.append(f"{w} has no {k}; the format requires it")
        props = s.get("properties") or {}
        extra = s.get("additionalProperties", True)
        for k, v in value.items():
            at = f"{where}.{k}" if where else k
            if k in props:
                _check(v, props[k], at, root, out, depth + 1)
            elif extra is False:
                out.append(f"{w} has no part {k} in the format (it has {', '.join(props)})")
            elif isinstance(extra, dict):
                _check(v, extra, at, root, out, depth + 1)
    if isinstance(value, list):
        if isinstance(s.get("items"), dict):
            for i, v in enumerate(value):
                _check(v, s["items"], f"{where}[{i}]", root, out, depth + 1)
        if s.get("uniqueItems"):
            seen = set()
            for v in value:
                k = json.dumps(v, sort_keys=True)
                if k in seen:
                    out.append(f"{w} lists {k[:60]} twice; the format lists each once")
                    break
                seen.add(k)


def _kind(value) -> str:
    for name, t in (("null", type(None)), ("true or false", bool), ("a number", (int, float)), ("text", str),
                    ("a list", list), ("an object", dict)):
        if isinstance(value, t):
            return name
    return type(value).__name__


def problems(doc, s: dict | None = None) -> list[str]:
    """Where ``doc`` does not match the configuration document's schema, each a sentence (at most MAX_PROBLEMS)."""
    root = s or schema()
    out: list[str] = []
    _check(doc, root, "", root, out)
    return out
