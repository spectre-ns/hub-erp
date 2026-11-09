"""Record types under change control (change control v2: principle 3, sections 2 and 8).

``config/record-types.json`` is where the record types came from, as the ``.frame.md`` files were for the SOPs. Once
the SOPs are documents (or once a change order has brought a record-type change into force), the record types in
force are kept by the hub in ``state/record-types/``:

    the file as it was at the release in force when the SOPs became documents (version 1),
    plus every record-type change a released change order carried, in order (version 2, 3, ...).

From then on the file is read only to see whether it still says the same. A file that differs is reported (``hubproto
status --check``, ``hubproto record-types status``) and not used; to bring what it adds into force, carry it on a
change order (``hubproto record-types carry CO-... --from-file``). Before conversion nothing changes: the file is used.

A change order carries record-type changes in its ``record_type_changes`` field, a list of items (the CO field shape):

    {"id": "rt-1", "op": "add_field", "type": "ncr", "field": "batch", "spec": "text",       # the op and its parameters
     "summary": "ncr: new field batch (text)", "by": "carol", "at": "<iso>", "reason": "...",
     "base_version": 1,                                         # the record types in force when it was carried
     "origin": {"kind": "direct", "by": "carol", "at": "<iso>"},
     "from_file": "<sha256 of config/record-types.json>"}       # only when carried from the file

Ops, all of them non-destructive (``OPS``):

    add_type {type, definition}            a new record type (prefix, digits, fields, object, retention, ...)
    add_field {type, field, spec}          a new field (custom ``x_`` fields belong to form layouts)
    add_enum_values {type, field, values}  more options for an enum field
    add_state {type, state, fields}        a state the type knows, with the fields a record needs to enter it
    require_fields {type, state, fields}   more fields a record needs to enter a state
    set_retention {type, value}            another retention class (config/retention.json)
    set_sensitivity {type, value}          the sensitivity new records are labelled with
    set_default {type, field, value}       a field's default ($owner, $today or a value)
    archive_type {type}                    the type stays readable; nothing new is filed (not while a process files it)
    remove_field {type, field}             only while no record holds a value in it
    remove_type {type}                     only while no record of it exists and no process files it

Refused, with why (``DESTRUCTIVE``): ``rename_type``, ``rename_field``, ``retype_field``, ``remove_enum_values``; and
``remove_field`` or ``remove_type`` when records hold data in them.

The change-order core (workstream 1) calls, like it calls ``hubproto.formlayout``:

    carried_reasons(hub, co) -> [str]   why the record-type changes on ``co`` cannot be released (empty: they can)
    activate(hub, co, digest) -> [str]  release step: bring them into force (idempotent; ``co`` approved), what it did
    delta_items(hub, co) -> [dict]      each change as a delta item (section 8 shape) for the change order's delta
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .release import utcnow
from .rules import sha256_of

FILE = "record-types.json"
TYPE_NAME = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
STATE_NAME = re.compile(r"^[a-z][a-z0-9-]{0,80}$")
PREFIX = re.compile(r"^[A-Z][A-Z0-9]{0,7}$")
ENUM_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,40}$")
SPECS = ("text", "int", "number", "money", "date", "bool", "list", "object", "ref", "principal")
SENSITIVITY = ("public", "internal", "confidential", "restricted")
TYPE_KEYS = ("prefix", "digits", "yearly", "object", "fields", "required_by_state", "defaults", "retention", "sensitivity", "gapless",
             "system_fields", "system_states", "decision_fields", "description", "folder_hint")
OPS = ("add_type", "add_field", "add_enum_values", "add_state", "require_fields", "set_retention", "set_sensitivity", "set_default",
       "archive_type", "remove_field", "remove_type")
DESTRUCTIVE = {
    "rename_type": "a record type is never renamed: its records keep their type and numbers. Add a new type and archive this one",
    "rename_field": "a field is never renamed: records keep their values under its name. Add a new field and take this one off the form",
    "retype_field": "a field's type never changes (add a new field instead)",
    "remove_enum_values": "options are added, never removed or renamed: records may hold them",
}
PARAMS = {"add_type": ("type", "definition"), "add_field": ("type", "field", "spec"), "add_enum_values": ("type", "field", "values"),
          "add_state": ("type", "state", "fields"), "require_fields": ("type", "state", "fields"), "set_retention": ("type", "value"),
          "set_sensitivity": ("type", "value"), "set_default": ("type", "field", "value"), "archive_type": ("type",),
          "remove_field": ("type", "field"), "remove_type": ("type",)}


# ---------------------------------------------------------------- storage
class RecordTypeStore:
    """state/record-types/: active.json (the record types in force, with their version) and history.jsonl."""

    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def active_path(self) -> Path:
        return self.root / "active.json"

    def active(self) -> dict | None:
        try:
            return json.loads(self.active_path.read_text(encoding="utf-8")) if self.active_path.exists() else None
        except ValueError:
            return None

    def stamp(self) -> int | None:
        try:
            return self.active_path.stat().st_mtime_ns
        except OSError:
            return None

    def set_active(self, entry: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.active_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry, indent=1, sort_keys=True, default=str), encoding="utf-8")
        tmp.replace(self.active_path)
        with (self.root / "history.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({k: v for k, v in entry.items() if k != "types"}, sort_keys=True, default=str) + "\n")

    def history(self) -> list[dict]:
        p = self.root / "history.jsonl"
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


def store(hub) -> RecordTypeStore:
    s = getattr(hub, "_record_types_store", None)
    if s is None:
        s = hub._record_types_store = RecordTypeStore(Path(hub.root) / "state" / "record-types")
    return s


# ---------------------------------------------------------------- the file, and the record types in force
def file_path(hub) -> Path:
    return Path(hub.kernel.config_dir) / FILE


def file_types(hub) -> dict:
    """The record types the file on disk says (unused once the record types are governed)."""
    data = json.loads(file_path(hub).read_text(encoding="utf-8"))
    data.pop("$comment", None)
    return data


def file_sha(hub) -> str:
    try:
        return sha256_of(file_types(hub))
    except (OSError, ValueError):
        return ""


def _without_custom(types: dict) -> dict:
    """Record types without the custom fields form layouts add (``x_``: they live in the layouts, not the type)."""
    out = copy.deepcopy(types)
    for td in out.values():
        if isinstance(td, dict) and isinstance(td.get("fields"), dict):
            td["fields"] = {k: v for k, v in td["fields"].items() if not k.startswith("x_")}
    out.pop("$comment", None)
    return out


def governed(hub) -> bool:
    """True once the hub keeps the record types in force (from the conversion, or a change order's record-type change)."""
    return store(hub).active() is not None


def _converted(hub) -> bool:
    from . import sopdocs

    return sopdocs.converted(hub.kernel)


def ensure(hub, by: str = "hub") -> dict:
    """The record types in force, kept from now on (version 1 when none were kept yet): the file as of the release in
    force. When the file changed since that release, the record types that release was compiled with."""
    s = store(hub)
    entry = s.active()
    if entry is not None:
        return entry
    now_sha = file_sha(hub)
    meta = hub.releases.current() if getattr(hub, "releases", None) else None
    types, source, base_sha = None, "the file", now_sha
    if meta:
        want = (meta.get("config") or {}).get(FILE)
        if want and want != now_sha:
            try:
                kept = hub.proposals.load(meta["proposal_id"]).get("record_types")
            except Exception:
                kept = None
            if kept:
                types, source, base_sha = _without_custom(kept), f"the record types release {meta['digest'][:12]} was compiled with", want
        if types is None:
            source = f"the file as of release {meta['digest'][:12]}"
    if types is None:
        types = file_types(hub)
    entry = {"version": 1, "types": types, "digest": sha256_of(types), "file_sha": base_sha, "source": source,
             "release": (meta or {}).get("digest"), "released_with": None, "by": by, "at": utcnow(), "changes": []}
    s.set_active(entry)
    install(hub)
    return entry


def in_force(hub) -> dict:
    """{"version", "types", ...}: the record types in force (the file, version 0, while it is still the source)."""
    entry = store(hub).active()
    if entry is not None:
        return entry
    if _converted(hub):
        return ensure(hub)
    types = file_types(hub)
    return {"version": 0, "types": types, "digest": sha256_of(types), "file_sha": sha256_of(types), "source": "the file",
            "released_with": None}


def install(hub) -> None:
    """Make the record types in force the kernel's (with the released custom fields), when the hub keeps them. Before
    that the kernel uses the file, as it always did. Called when the hub starts and whenever another process changes
    the record types in force (``refresh``)."""
    s = store(hub)
    entry = s.active()
    if entry is None:
        if not _converted(hub):
            hub._record_types_stamp = None
            return
        entry = ensure(hub)  # installs
        return
    hub._record_types_stamp = s.stamp()  # before the custom fields are applied: they read kernel.types (refresh)
    hub.kernel.types = copy.deepcopy(entry["types"])
    hub.kernel.config_digests = {**hub.kernel.config_digests, FILE: entry["digest"]}
    if getattr(hub, "forms", None) is not None:
        from . import formlayout

        formlayout.apply_custom_fields(hub)


def refresh(hub) -> None:
    """Install the record types in force again when they changed since (in this process or another): cheap, a stat."""
    s = store(hub)
    if s.stamp() != getattr(hub, "_record_types_stamp", None):
        hub._record_types_stamp = s.stamp()
        if s.active() is not None:
            install(hub)


# ---------------------------------------------------------------- the file against the record types in force
def differences(old: dict, new: dict) -> list[str]:
    """What ``new`` says that ``old`` does not, and the other way round, in words."""
    out = []
    for t in sorted(set(new) - set(old)):
        out.append(f"record type {t} (prefix {new[t].get('prefix')})")
    for t in sorted(set(old) - set(new)):
        out.append(f"no record type {t}")
    for t in sorted(set(old) & set(new)):
        a, b = old[t], new[t]
        fa, fb = a.get("fields") or {}, b.get("fields") or {}
        for f in sorted(set(fb) - set(fa)):
            out.append(f"{t}.{f} ({fb[f]})")
        for f in sorted(set(fa) - set(fb)):
            out.append(f"no field {t}.{f}")
        for f in sorted(set(fa) & set(fb)):
            if fa[f] != fb[f]:
                out.append(f"{t}.{f} is {fb[f]} (not {fa[f]})")
        for k in sorted((set(a) | set(b)) - {"fields"}):
            if a.get(k) != b.get(k):
                out.append(f"{t} {k.replace('_', ' ')}: {json.dumps(b.get(k), sort_keys=True)}")
    return out


def drift(hub) -> dict | None:
    """How the file differs from the record types in force, once the hub keeps them; None when the file still says what
    the record types in force were made from (changes released by change orders since are not drift).
    {"version", "file", "in_file", "in_force"}: what the file says that is not in force (not used), and what is in
    force that the file does not say."""
    entry = store(hub).active()
    if entry is None:
        if not _converted(hub):
            return None
        entry = ensure(hub)
    try:
        types = file_types(hub)
    except (OSError, ValueError) as e:
        return {"version": entry["version"], "file": str(file_path(hub)), "in_file": [f"the file cannot be read: {e}"], "in_force": []}
    if sha256_of(types) in (entry.get("file_sha"), entry.get("digest")):
        return None
    return {"version": entry["version"], "file": str(file_path(hub)), "in_file": differences(entry["types"], types),
            "in_force": differences(types, entry["types"])}


# ---------------------------------------------------------------- checking and applying changes
def _valid_spec(spec) -> bool:
    if not isinstance(spec, str):
        return False
    if spec.startswith("enum:"):
        vals = spec[5:].split(",")
        return bool(vals) and all(ENUM_VALUE.match(v) for v in vals) and len(set(vals)) == len(vals)
    return spec in SPECS


def _value_problem(spec: str, value) -> str:
    """Why a value does not fit a field type (empty when it does), as the kernel would check it."""
    from .kernel import _DATE_RE

    if spec.startswith("enum:"):
        return "" if str(value) in spec[5:].split(",") else f"must be one of {spec[5:]}"
    if spec in ("money", "number", "int"):
        try:
            num = float(value)
        except (TypeError, ValueError):
            return "must be a number"
        if num < 0 or num != num or num in (float("inf"), float("-inf")) or (spec == "int" and num != int(num)):
            return "must be a finite number, not negative" + (", whole" if spec == "int" else "")
    if spec == "date" and not _DATE_RE.match(str(value)):
        return "must be a date written YYYY-MM-DD"
    if spec == "bool" and not isinstance(value, bool):
        return "must be true or false"
    return ""


def _with_data(hub, type_: str, field: str | None = None) -> int:
    recs = hub.kernel.query(type_=type_, include_archived=True)
    if field is None:
        return len(recs)
    return sum(1 for r in recs if r["fields"].get(field) not in (None, "", [], {}))


def _filed_by(hub, type_: str) -> list[str]:
    """The processes of the release in force whose records are of this type."""
    b = hub.current_bundle() or {}
    return sorted(p for p, w in (b.get("workflows") or {}).items() if w.get("record_type") == type_)


def _check_definition(hub, types: dict, name: str, td) -> list[str]:
    errors = []
    if not isinstance(td, dict):
        return [f"{name}: the definition is a mapping of prefix, digits, fields, ..."]
    for k in td:
        if k not in TYPE_KEYS:
            errors.append(f"{name}: unknown key {k!r} (known: {', '.join(TYPE_KEYS)})")
    prefix = td.get("prefix")
    if not isinstance(prefix, str) or not PREFIX.match(prefix):
        errors.append(f"{name}: the prefix is 1 to 8 capital letters or digits, starting with a letter")
    elif any(isinstance(o, dict) and o.get("prefix") == prefix for t, o in types.items() if t != name):
        errors.append(f"{name}: prefix {prefix} is taken by {next(t for t, o in types.items() if isinstance(o, dict) and o.get('prefix') == prefix)}")
    if not isinstance(td.get("digits", 4), int) or not 1 <= td.get("digits", 4) <= 8:
        errors.append(f"{name}: digits is a whole number from 1 to 8")
    fields = td.get("fields")
    if not isinstance(fields, dict) or not fields:
        errors.append(f"{name}: give it fields (name -> type)")
        fields = {}
    for f, spec in fields.items():
        if not FIELD_NAME.match(str(f)) or str(f).startswith("x_"):
            errors.append(f"{name}.{f}: a field name is lower-case letters, digits and _ (x_ names are custom fields of form layouts)")
        if not _valid_spec(spec):
            errors.append(f"{name}.{f}: unknown field type {spec!r} (one of {', '.join(SPECS)}, or enum:a,b)")
    for st, req in (td.get("required_by_state") or {}).items():
        if not STATE_NAME.match(str(st)):
            errors.append(f"{name}: state {st!r} is lower-case words joined by -")
        for f in req if isinstance(req, list) else [req]:
            if f not in fields:
                errors.append(f"{name}: state {st} requires {f}, which is not a field")
    for f in (td.get("defaults") or {}):
        if f not in fields:
            errors.append(f"{name}: a default for {f}, which is not a field")
    for k in ("system_fields", "decision_fields"):
        for f in td.get(k) or []:
            if f not in fields:
                errors.append(f"{name}: {k.replace('_', ' ')} names {f}, which is not a field")
    if td.get("retention", "standard") not in hub.kernel.retention:
        errors.append(f"{name}: no retention class {td.get('retention')!r} (classes: {', '.join(sorted(hub.kernel.retention))})")
    if td.get("sensitivity", "internal") not in SENSITIVITY:
        errors.append(f"{name}: sensitivity is one of {', '.join(SENSITIVITY)}")
    obj = td.get("object", name)
    if not isinstance(obj, str) or not TYPE_NAME.match(obj):
        errors.append(f"{name}: the object is the noun the SOPs use, in lower case")
    elif any(isinstance(o, dict) and o.get("object", t) == obj for t, o in types.items() if t != name):
        errors.append(f"{name}: another record type already has the object {obj!r}")
    return errors


def check_one(hub, types: dict, c: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings) for one change against ``types`` (the record types it would apply to)."""
    op = c.get("op")
    if op in DESTRUCTIVE:
        return [f"{op.replace('_', ' ')} refused: {DESTRUCTIVE[op]}"], []
    if op not in OPS:
        return [f"unknown change {op!r} (one of {', '.join(OPS)})"], []
    missing = [p for p in PARAMS[op] if c.get(p) in (None, "") and not (p == "fields" and op == "add_state")]
    if missing:
        return [f"{op.replace('_', ' ')}: give {', '.join(missing)}"], []
    t = c["type"]
    errors: list[str] = []
    warnings: list[str] = []
    if op == "add_type":
        if not TYPE_NAME.match(str(t)):
            return [f"{t!r}: a record type's name is lower-case letters, digits and -"], []
        if t in types:
            return [f"there is already a record type {t}" + (" (archived)" if types[t].get("archived") else "")], []
        return _check_definition(hub, types, t, c["definition"]), []
    td = types.get(t)
    if not isinstance(td, dict):
        return [f"there is no record type {t}"], []
    fields = td.get("fields") or {}
    if td.get("archived") and op not in ("set_retention",):
        return [f"{t} is archived: it stays readable and does not change"], []
    f = c.get("field")
    if op == "add_field":
        if not FIELD_NAME.match(str(f)):
            errors.append(f"{t}.{f}: a field name is lower-case letters, digits and _")
        elif str(f).startswith("x_"):
            errors.append(f"{t}.{f}: x_ fields are custom fields; add them on the {t} form layout")
        elif f in fields:
            errors.append(f"{t} already has a field {f} ({fields[f]})")
        if not _valid_spec(c["spec"]):
            errors.append(f"{t}.{f}: unknown field type {c['spec']!r} (one of {', '.join(SPECS)}, or enum:a,b)")
    elif op == "add_enum_values":
        spec = fields.get(f)
        if spec is None:
            errors.append(f"{t} has no field {f}")
        elif not str(spec).startswith("enum:"):
            errors.append(f"{t}.{f} is {spec}, not a list of options")
        else:
            vals = c["values"] if isinstance(c["values"], list) else [c["values"]]
            bad = [v for v in vals if not ENUM_VALUE.match(str(v))]
            if bad:
                errors.append(f"{t}.{f}: an option is letters, digits, spaces and . _ - ({', '.join(map(str, bad))})")
            if not set(map(str, vals)) - set(spec[5:].split(",")):
                errors.append(f"{t}.{f} already has {', '.join(map(str, vals))}")
    elif op in ("add_state", "require_fields"):
        st = c["state"]
        req = td.get("required_by_state") or {}
        want = c.get("fields") or []
        want = want if isinstance(want, list) else [want]
        if not STATE_NAME.match(str(st)):
            errors.append(f"{t}: state {st!r} is lower-case words joined by -")
        if op == "add_state" and st in req:
            errors.append(f"{t} already knows state {st}; require more fields there instead")
        if op == "require_fields":
            if st not in req:
                errors.append(f"{t} has no state {st} with required fields; add the state")
            elif not set(want) - set(req[st]):
                errors.append(f"{t}: {st} already requires {', '.join(want)}")
            if not want:
                errors.append(f"{t}: name the fields {st} requires")
        for x in want:
            if x not in fields:
                errors.append(f"{t}: {x} is not a field")
        if not errors and op == "require_fields" and getattr(hub, "kernel", None) is not None:
            short = sum(1 for r in hub.kernel.query(type_=t, state=st) if any(r["fields"].get(x) in (None, "", [], {}) for x in want))
            if short:
                warnings.append(f"{short} {t} record(s) in {st} lack {', '.join(want)}: they stay there; the rule applies to the next ones")
    elif op == "set_retention":
        if c["value"] not in hub.kernel.retention:
            errors.append(f"no retention class {c['value']!r} (classes: {', '.join(sorted(hub.kernel.retention))})")
        elif td.get("retention", "standard") == c["value"]:
            errors.append(f"{t} is already kept as {c['value']}")
        else:
            old = (hub.kernel.retention.get(td.get("retention", "standard")) or {}).get("years", 0)
            new = (hub.kernel.retention.get(c["value"]) or {}).get("years", 0)
            if new < old:
                warnings.append(f"{t} records are kept {new} year(s) instead of {old}")
    elif op == "set_sensitivity":
        if c["value"] not in SENSITIVITY:
            errors.append(f"sensitivity is one of {', '.join(SENSITIVITY)}")
        elif td.get("sensitivity", "internal") == c["value"]:
            errors.append(f"{t} is already {c['value']}")
        elif SENSITIVITY.index(c["value"]) < SENSITIVITY.index(td.get("sensitivity", "internal")):
            warnings.append(f"new {t} records are labelled {c['value']}, lower than {td.get('sensitivity', 'internal')}; existing ones keep their label")
    elif op == "set_default":
        if f not in fields:
            errors.append(f"{t} has no field {f}")
        elif c["value"] not in ("$owner", "$today"):
            why = _value_problem(fields[f], c["value"])
            if why:
                errors.append(f"{t}.{f}: the default {why}")
    elif op == "archive_type":
        procs = _filed_by(hub, t)
        if procs:
            errors.append(f"{', '.join(procs)} file(s) {t} records: change the process first, then archive the type")
    elif op == "remove_field":
        if f not in fields:
            errors.append(f"{t} has no field {f}")
        else:
            n = _with_data(hub, t, f)
            if n:
                errors.append(f"{t}.{f} holds data in {n} record(s): a field with data is never removed (its values are part of "
                              "the records); take it off the form instead")
            used = [st for st, req in (td.get("required_by_state") or {}).items() if f in req]
            used += [k.replace("_", " ") for k in ("defaults", "system_fields", "decision_fields") if f in (td.get(k) or {})]
            if used:
                errors.append(f"{t}.{f} is used ({', '.join(used)}): it cannot be removed")
    elif op == "remove_type":
        n = _with_data(hub, t)
        if n:
            errors.append(f"{t} has {n} record(s): a record type with records is never removed; archive it instead")
        procs = _filed_by(hub, t)
        if procs:
            errors.append(f"{', '.join(procs)} file(s) {t} records: it cannot be removed")
    return errors, warnings


def apply_one(types: dict, c: dict) -> dict:
    """``types`` with one (checked) change made; ``types`` is not changed."""
    out = copy.deepcopy(types)
    op, t = c["op"], c["type"]
    if op == "add_type":
        out[t] = copy.deepcopy(c["definition"])
        return out
    td = out[t]
    if op == "add_field":
        td.setdefault("fields", {})[c["field"]] = c["spec"]
    elif op == "add_enum_values":
        have = td["fields"][c["field"]][5:].split(",")
        vals = c["values"] if isinstance(c["values"], list) else [c["values"]]
        td["fields"][c["field"]] = "enum:" + ",".join(have + [str(v) for v in vals if str(v) not in have])
    elif op in ("add_state", "require_fields"):
        req = td.setdefault("required_by_state", {})
        want = c.get("fields") or []
        req[c["state"]] = list(req.get(c["state"], [])) + [x for x in (want if isinstance(want, list) else [want]) if x not in req.get(c["state"], [])]
    elif op == "set_retention":
        td["retention"] = c["value"]
    elif op == "set_sensitivity":
        td["sensitivity"] = c["value"]
    elif op == "set_default":
        td.setdefault("defaults", {})[c["field"]] = c["value"]
    elif op == "archive_type":
        td["archived"] = True
    elif op == "remove_field":
        td["fields"].pop(c["field"], None)
    elif op == "remove_type":
        out.pop(t, None)
    return out


def check(hub, changes: list[dict], types: dict | None = None) -> tuple[dict, list[str], list[str]]:
    """(types after the changes, errors, warnings), the changes applied in order on ``types`` (the record types in force
    by default). A change with errors is not applied, so the next ones are checked against what would be in force."""
    types = copy.deepcopy(in_force(hub)["types"] if types is None else types)
    errors, warnings = [], []
    for c in changes:
        e, w = check_one(hub, types, c)
        label = c.get("id") or c.get("op")
        errors += [f"{label}: {x}" for x in e]
        warnings += [f"{label}: {x}" for x in w]
        if not e:
            types = apply_one(types, c)
    return types, errors, warnings


def describe(c: dict) -> str:
    """A change in words, for the change order, its reviewers and the release."""
    op, t = c.get("op"), c.get("type")
    if op == "add_type":
        d = c.get("definition") or {}
        return f"new record type {t} (prefix {d.get('prefix')}, {len(d.get('fields') or {})} field(s), kept as {d.get('retention', 'standard')})"
    if op == "add_field":
        return f"{t}: new field {c.get('field')} ({c.get('spec')})"
    if op == "add_enum_values":
        vals = c.get("values") if isinstance(c.get("values"), list) else [c.get("values")]
        return f"{t}.{c.get('field')}: new option(s) {', '.join(map(str, vals))}"
    if op == "add_state":
        return f"{t}: state {c.get('state')}" + (f", which needs {', '.join(c.get('fields') or [])}" if c.get("fields") else "")
    if op == "require_fields":
        return f"{t}: entering {c.get('state')} also needs {', '.join(c.get('fields') or [])}"
    if op == "set_retention":
        return f"{t}: kept as {c.get('value')}"
    if op == "set_sensitivity":
        return f"{t}: new records labelled {c.get('value')}"
    if op == "set_default":
        return f"{t}.{c.get('field')}: default {c.get('value')}"
    if op == "archive_type":
        return f"{t}: archived (readable; nothing new is filed)"
    if op == "remove_field":
        return f"{t}: field {c.get('field')} removed (it holds no data)"
    if op == "remove_type":
        return f"record type {t} removed (it has no records)"
    return f"{t}: {str(op).replace('_', ' ')}"


# ---------------------------------------------------------------- change orders
def changes_of(co: dict) -> list[dict]:
    return list(co["fields"].get("record_type_changes") or [])


def _may_carry(hub, co: dict, by: str) -> str:
    from . import authz

    meta = hub.releases.current()
    bundle = hub.releases.bundle(meta["digest"]) if meta else {"members": {}}
    if by in (co["fields"].get("requested_by"), co.get("created_by")) or "document-controller" in authz.roles_of(bundle, by):
        return ""
    return (f"{co['number']} is {co['fields'].get('requested_by') or co.get('created_by')}'s change order: they or a document "
            "controller change what it carries")


def carry(hub, co_ref: str, changes: list[dict], *, by: str, reason: str = "", origin: str = "direct",
          from_file: str | None = None) -> list[dict]:
    """Put record-type changes on a change order (each a delta item with its origin). Every change is checked, with
    the ones it already carries, against the record types in force; a destructive or invalid change is refused and
    nothing is carried. Returns the items added."""
    from . import eco

    co = eco._co(hub, co_ref)
    if eco.finished(co) or co["state"].startswith("awaiting"):
        raise ValueError(f"{co['number']} is {co['state'].replace('-', ' ')}: change what it carries while it is being prepared")
    why = _may_carry(hub, co, by)
    if why:
        raise ValueError(why)
    if not changes:
        raise ValueError("give at least one record-type change")
    existing = changes_of(co)
    base = in_force(hub)
    n = max([int(str(c.get("id", "rt-0")).rsplit("-", 1)[-1] or 0) for c in existing if str(c.get("id", "")).startswith("rt-")] or [0])
    at = utcnow()
    new = []
    for raw in changes:
        if not isinstance(raw, dict):
            raise ValueError("each record-type change is a mapping with an op")
        n += 1
        item = {k: copy.deepcopy(v) for k, v in raw.items() if k in ("op",) + PARAMS.get(raw.get("op"), ("type",))}
        item.update({"id": f"rt-{n}", "by": by, "at": at, "reason": str(raw.get("reason") or reason), "base_version": base["version"],
                     "origin": {"kind": origin, "by": by, "at": at}})
        if from_file:
            item["from_file"] = from_file
        item["summary"] = describe(item)
        new.append(item)
    _, errors, _ = check(hub, existing + new, base["types"])
    mine = [e for e in errors if any(e.startswith(f"{c['id']}:") for c in new)]
    if mine:
        raise ValueError("refused, nothing carried: " + "; ".join(mine))
    hub.kernel.set_fields(co["locator"], {"record_type_changes": existing + new}, by=by)
    return new


def uncarry(hub, co_ref: str, change_id: str, *, by: str) -> None:
    from . import eco

    co = eco._co(hub, co_ref)
    if eco.finished(co) or co["state"].startswith("awaiting"):
        raise ValueError(f"{co['number']} is {co['state'].replace('-', ' ')}: change what it carries while it is being prepared")
    why = _may_carry(hub, co, by)
    if why:
        raise ValueError(why)
    rest = [c for c in changes_of(co) if c.get("id") != change_id]
    if len(rest) == len(changes_of(co)):
        raise ValueError(f"{co['number']} carries no record-type change {change_id}")
    hub.kernel.set_fields(co["locator"], {"record_type_changes": rest}, by=by)


def from_file_changes(hub) -> tuple[list[dict], list[str]]:
    """The changes that would bring what the file adds into force (non-destructive ones), and what the file says that
    no change order can make (removals, renames, retypes): ([change], [skipped, in words])."""
    base = in_force(hub)["types"]
    new = file_types(hub)
    out, skipped = [], []
    for t in sorted(set(new) - set(base)):
        out.append({"op": "add_type", "type": t, "definition": new[t]})
    for t in sorted(set(base) - set(new)):
        skipped.append(f"the file has no record type {t}: a record type is archived, never dropped")
    for t in sorted(set(base) & set(new)):
        a, b = base[t], new[t]
        fa, fb = a.get("fields") or {}, b.get("fields") or {}
        for f in sorted(set(fb) - set(fa)):
            out.append({"op": "add_field", "type": t, "field": f, "spec": fb[f]})
        for f in sorted(set(fa) - set(fb)):
            skipped.append(f"the file has no field {t}.{f}: a field is not removed this way")
        for f in sorted(set(fa) & set(fb)):
            if fa[f] == fb[f]:
                continue
            if str(fa[f]).startswith("enum:") and str(fb[f]).startswith("enum:"):
                old, now = fa[f][5:].split(","), fb[f][5:].split(",")
                if set(old) <= set(now):
                    out.append({"op": "add_enum_values", "type": t, "field": f, "values": [v for v in now if v not in old]})
                    continue
            skipped.append(f"{t}.{f} is {fb[f]} in the file (in force: {fa[f]}): {DESTRUCTIVE['retype_field']}")
        ra, rb = a.get("required_by_state") or {}, b.get("required_by_state") or {}
        for st in sorted(rb):
            if st not in ra:
                out.append({"op": "add_state", "type": t, "state": st, "fields": list(rb[st])})
            elif set(rb[st]) - set(ra[st]):
                out.append({"op": "require_fields", "type": t, "state": st, "fields": [x for x in rb[st] if x not in ra[st]]})
        for k, op in (("retention", "set_retention"), ("sensitivity", "set_sensitivity")):
            if b.get(k) is not None and a.get(k) != b.get(k):
                out.append({"op": op, "type": t, "value": b[k]})
        for f, v in (b.get("defaults") or {}).items():
            if (a.get("defaults") or {}).get(f) != v:
                out.append({"op": "set_default", "type": t, "field": f, "value": v})
        if b.get("archived") and not a.get("archived"):
            out.append({"op": "archive_type", "type": t})
        others = sorted(k for k in (set(a) | set(b)) - {"fields", "required_by_state", "retention", "sensitivity", "defaults", "archived"}
                        if a.get(k) != b.get(k))
        for k in others:
            skipped.append(f"{t} {k.replace('_', ' ')} differs in the file: not a change a change order makes")
    return out, skipped


def carry_file_changes(hub, co_ref: str, *, by: str, reason: str = "") -> tuple[list[dict], list[str]]:
    """Carry what the file adds to the record types in force on a change order (a software update that ships new record
    types or fields comes into force this way). Returns (items carried, what the file says that cannot be carried)."""
    changes, skipped = from_file_changes(hub)
    if not changes:
        raise ValueError("the file adds nothing to the record types in force" + (f" ({'; '.join(skipped)})" if skipped else ""))
    sha = file_sha(hub)
    return carry(hub, co_ref, changes, by=by, reason=reason or f"{FILE} as shipped ({sha[:12]})", origin="direct", from_file=sha), skipped


def carried_reasons(hub, co: dict) -> list[str]:
    """Why the record-type changes a change order carries cannot be released as they stand (empty when they can)."""
    changes = changes_of(co)
    if not changes:
        return []
    _, errors, _ = check(hub, changes)
    return [f"record types: {e}" for e in errors]


def delta_items(hub, co: dict) -> list[dict]:
    """Each record-type change on the change order as a delta item (change control v2, section 8)."""
    changes = changes_of(co)
    if not changes:
        return []
    base = in_force(hub)
    types = copy.deepcopy(base["types"])
    out = []
    for c in changes:
        errors, warnings = check_one(hub, types, c)
        if not errors:
            types = apply_one(types, c)
        out.append({"id": f"record-type:{c['id']}", "op": c["op"], "kind": "record-type", "record_type": c.get("type"),
                    "change": {k: c[k] for k in PARAMS.get(c["op"], ()) if k in c}, "reason": c.get("summary") or describe(c),
                    "origin": c.get("origin") or {"kind": "direct", "by": c.get("by"), "at": c.get("at")},
                    "warnings": [{"code": "record-type", "text": w, "ref": c["id"], "accepted": None} for w in warnings],
                    "errors": errors, "stale": c.get("base_version") != base["version"], "decisions": []})
    return out


def activate(hub, co: dict, digest: str) -> list[str]:
    """The release step (workstream 1's release automation calls it, after the Frame and the form layouts): the record
    types the approved change order carries come into force, as the next version of the record types in force. Safe to
    repeat (a change order already in force returns what it did). Refuses a change order that is not approved, and
    changes that no longer check against the record types in force (nothing changes then). Returns what it did."""
    from . import closure

    changes = changes_of(co)
    if not changes:
        return []
    s = store(hub)
    done = [e for e in s.history() if e.get("released_with") == co["number"]]
    if done:
        return [f"record types v{done[-1]['version']} (already in force with {co['number']})"]
    closure.check_co_approved(co, "its record-type changes")
    base = ensure(hub)
    types, errors, _ = check(hub, changes, base["types"])
    if errors:
        raise ValueError(f"{co['number']}'s record-type changes cannot come into force: " + "; ".join(errors))
    now_sha = file_sha(hub)
    adopted = now_sha if any(c.get("from_file") == now_sha for c in changes) else None
    entry = {"version": int(base["version"]) + 1, "types": types, "digest": sha256_of(types),
             "file_sha": adopted or base.get("file_sha"), "source": f"released with {co['number']}", "release": digest,
             "released_with": co["number"], "by": f"change-order:{co['number']}", "at": utcnow(),
             "changes": [c.get("summary") or describe(c) for c in changes]}
    s.set_active(entry)
    install(hub)
    return [f"record types v{entry['version']}: " + "; ".join(entry["changes"])]
