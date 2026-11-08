"""Form layouts as data: how each record type's form looks, edited in the UI and released through change control.

The record type (config/record-types.json) says what a record holds: its fields and their types, which fields each
state needs. A form layout says how people see and fill it in:

* ``sections``: titled groups of field rows on the record's Details tab, in order. Each row names a field, with an
  optional label, help text, placeholder and widget (``textarea``, ``lines``), and conditions:
  ``visible_when``, ``required_when``, ``readonly_when``.
* ``start``: the fields on the form that starts the record, in order, with their widgets.
* ``custom_fields``: fields the organization adds (names start with ``x_``; typed; never renamed or retyped). A custom
  field that is no longer wanted is retired: hidden and closed to new values, its data kept.

A condition is data, never code: ``{"field": "severity", "op": "==", "value": "high"}``, ``{"state": "in", "value":
["open", "returned"]}``, combined with ``{"all": [...]}``, ``{"any": [...]}`` and ``{"not": {...}}``. The hub evaluates
them on the server: ``required_when`` is enforced when a record is created or moves on, ``readonly_when`` when someone
changes a field, not only hidden in the browser.

The layout in force for a type is the base layout (derived from the hub's own forms, so nothing looks different until
someone changes it) or the last released one, reconciled with the record type: a field the type no longer has is
dropped, a field it gained appears at the end of the last section. People edit a draft (one per person and type);
a draft is carried on a change order, checked with the rest of it, and the release automation applies it.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .release import utcnow

WIDGETS = ("textarea", "lines", "lines:locator,qty", "lines:lot,qty")
OPS = ("==", "!=", "in", "not in", ">", "<", ">=", "<=", "set", "empty")
CUSTOM_TYPES = ("text", "number", "money", "int", "date", "bool", "list", "principal")
CUSTOM_NAME = re.compile(r"^x_[a-z][a-z0-9_]{1,40}$")
PROPS = ("label", "help", "placeholder", "widget", "visible_when", "required_when", "readonly_when")
LEAD = ["title", "description", "statement", "kind", "severity", "supplier", "amount", "currency", "quantity", "unit_cost", "item", "po", "date", "due"]
# The inventory types lead with what identifies them (a part's number and units, a lot's part and place).
TYPE_LEAD = {"part": ["title", "ipn", "revision", "description", "category", "units", "trackable", "purchaseable", "assembly", "component", "active",
                      "default_location", "minimum_stock"],
             "stock-item": ["part", "batch", "serial", "expiry", "notes"], "stock-location": ["name", "parent", "kind", "structural", "site"],
             "part-category": ["name", "parent", "structural", "default_location"],
             "supplier-part": ["part", "supplier", "sku", "manufacturer_part", "pack_quantity", "price_breaks", "currency", "lead_time_days", "primary"],
             "manufacturer-part": ["part", "manufacturer", "mpn", "approved"], "stock-adjustment": ["lot", "new_quantity", "reason"],
             "stock-release": ["lot", "to_location", "reason"], "receipt": ["po", "location", "lines", "packing_slip"]}
NOT_ON_FORMS = {"text", "office_comments", "checked_out_by", "checked_out_via", "editors", "wopi_lock", "content_sha", "reviews", "attachments"}


# ---------------------------------------------------------------- storage
class FormStore:
    """state/forms/: active.json (the layout in force per type, with its version), history/<type>.jsonl, drafts/<person>.json."""

    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "drafts").mkdir(parents=True, exist_ok=True)
        (self.root / "history").mkdir(parents=True, exist_ok=True)

    def _read(self, p: Path, default):
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
        except ValueError:
            return default

    def _write(self, p: Path, data) -> None:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        tmp.replace(p)

    def active(self) -> dict:
        return self._read(self.root / "active.json", {})

    def set_active(self, type_: str, entry: dict) -> None:
        data = self.active()
        data[type_] = entry
        self._write(self.root / "active.json", data)
        with (self.root / "history" / f"{type_}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def history(self, type_: str) -> list[dict]:
        p = self.root / "history" / f"{type_}.jsonl"
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def drafts(self, user: str) -> dict:
        return self._read(self.root / "drafts" / f"{user}.json", {})

    def save_draft(self, user: str, type_: str, layout: dict) -> None:
        data = self.drafts(user)
        data[type_] = {**layout, "updated_at": utcnow()}
        self._write(self.root / "drafts" / f"{user}.json", data)

    def discard_draft(self, user: str, type_: str) -> None:
        data = self.drafts(user)
        data.pop(type_, None)
        self._write(self.root / "drafts" / f"{user}.json", data)


# ---------------------------------------------------------------- the base layout and the one in force
def type_fields(hub, type_: str, layout: dict | None = None) -> dict[str, str]:
    """The fields a form can show: the record type's own (not system or file fields) and the layout's custom fields
    that are not retired."""
    td = hub.kernel.types.get(type_) or {}
    system = set(td.get("system_fields") or [])
    out = {k: v for k, v in (td.get("fields") or {}).items() if k not in system and k not in NOT_ON_FORMS}
    for name, cf in ((layout or {}).get("custom_fields") or {}).items():
        if not cf.get("retired"):
            out[name] = cf["type"]
    return out


def base_layout(hub, type_: str) -> dict:
    """The layout the hub's own forms have: one Details section in the usual order, and the usual start form."""
    from .shell.forms import DEFAULT_START_INPUTS, START_INPUTS, TEXTAREA_FIELDS

    fields = type_fields(hub, type_)
    lead = TYPE_LEAD.get(type_, LEAD)
    order = [k for k in lead if k in fields] + [k for k in sorted(fields) if k not in lead]
    rows = [{"field": k, **({"widget": "textarea"} if k in TEXTAREA_FIELDS else {})} for k in order]
    start = []
    for entry in START_INPUTS.get(type_, DEFAULT_START_INPUTS):
        row = {"field": entry[0]}
        if len(entry) > 1 and entry[1]:
            row["widget"] = entry[1]
        if len(entry) > 2 and entry[2]:
            row["spec"] = entry[2]
        if row["field"] not in fields:
            row["input"] = True  # read by the process's first step (a document's body), not stored as a field
        start.append(row)
    return {"type": type_, "sections": [{"id": "details", "title": "Details", "fields": rows}], "start": start, "custom_fields": {}}


def reconcile(hub, type_: str, layout: dict) -> tuple[dict, list[str]]:
    """The layout made to fit the record type as it is now: rows for fields it no longer has are dropped, fields it
    gained are added to the end of the last section. Returns (layout, notes)."""
    lay = copy.deepcopy(layout)
    fields = type_fields(hub, type_, lay)
    notes = []
    seen = set()
    for s in lay.get("sections") or []:
        kept = []
        for r in s.get("fields") or []:
            if r.get("field") in fields and r["field"] not in seen:
                kept.append(r)
                seen.add(r["field"])
            elif r.get("field") not in fields:
                notes.append(f"{r.get('field')} is no longer a field of {type_}: left off the form")
        s["fields"] = kept
    hidden = set(lay.get("hidden") or [])
    new = [k for k in fields if k not in seen and k not in hidden]
    if new:
        if not lay.get("sections"):
            lay["sections"] = [{"id": "details", "title": "Details", "fields": []}]
        lay["sections"][-1]["fields"] += [{"field": k} for k in new]
        notes.append(f"new field(s) shown at the end: {', '.join(new)}")
    inputs = {r["field"] for r in base_layout(hub, type_).get("start") or [] if r.get("input")}
    lay["start"] = [r for r in lay.get("start") or [] if r.get("field") in fields or r.get("spec") or r.get("field") in inputs]
    return lay, notes


def active_entry(hub, type_: str) -> dict | None:
    return hub.forms.active().get(type_)


def active_layout(hub, type_: str) -> dict:
    """The layout in force for a type (the base layout until one is released), reconciled with the type."""
    entry = active_entry(hub, type_)
    lay = entry["layout"] if entry else base_layout(hub, type_)
    return reconcile(hub, type_, lay)[0]


def draft_layout(hub, user: str, type_: str) -> dict:
    d = hub.forms.drafts(user).get(type_)
    return reconcile(hub, type_, d)[0] if d else active_layout(hub, type_)


# ---------------------------------------------------------------- conditions
def fields_in(cond) -> set[str]:
    if not cond:
        return set()
    if isinstance(cond, dict):
        if "all" in cond or "any" in cond:
            return set().union(*[fields_in(c) for c in cond.get("all") or cond.get("any") or []])
        if "not" in cond:
            return fields_in(cond["not"])
        return {cond["field"]} if cond.get("field") else set()
    return set()


def evaluate(cond, fields: dict, state: str) -> bool:
    """A condition against a record's fields and state. An empty condition is false (no condition: never applies)."""
    if not cond:
        return False
    if "all" in cond:
        return all(evaluate(c, fields, state) for c in cond["all"])
    if "any" in cond:
        return any(evaluate(c, fields, state) for c in cond["any"])
    if "not" in cond:
        return not evaluate(cond["not"], fields, state)
    if "state" in cond:
        op, v = cond.get("state"), cond.get("value")
        if op in ("in", "not in"):
            vals = v if isinstance(v, list) else [v]
            return (state in vals) == (op == "in")
        return (state == v) == (op in ("==", True))
    have = fields.get(cond.get("field"))
    op, v = cond.get("op"), cond.get("value")
    if op == "set":
        return have not in (None, "", [], {})
    if op == "empty":
        return have in (None, "", [], {})
    if op in ("in", "not in"):
        vals = v if isinstance(v, list) else [v]
        return (have in vals) == (op == "in")
    if op in (">", "<", ">=", "<="):
        try:
            a, b = float(have), float(v)
        except (TypeError, ValueError):
            return False
        return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]
    if op == "!=":
        return str(have) != str(v)
    return str(have) == str(v)


def describe(cond) -> str:
    """A condition in words: 'when severity is high and the state is open'."""
    from .shell.text import field_label

    if not cond:
        return ""
    if "all" in cond:
        return " and ".join(describe(c) for c in cond["all"])
    if "any" in cond:
        return "(" + " or ".join(describe(c) for c in cond["any"]) + ")"
    if "not" in cond:
        return "not " + describe(cond["not"])
    if "state" in cond:
        v = cond.get("value")
        vals = ", ".join(v) if isinstance(v, list) else str(v)
        return f"the state is {'not ' if cond['state'] == 'not in' else ''}{'one of ' if isinstance(v, list) and len(v) > 1 else ''}{vals.replace('-', ' ')}"
    name = field_label(cond.get("field") or "").lower()
    op, v = cond.get("op"), cond.get("value")
    if op == "set":
        return f"{name} is filled in"
    if op == "empty":
        return f"{name} is empty"
    words = {"==": "is", "!=": "is not", "in": "is one of", "not in": "is none of", ">": "is over", "<": "is under", ">=": "is at least", "<=": "is at most"}
    vals = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
    return f"{name} {words.get(op, op)} {vals}"


def _check_cond(cond, known: set[str], where: str) -> list[str]:
    errors = []
    if not cond:
        return errors
    if not isinstance(cond, dict):
        return [f"{where}: a condition must be a mapping"]
    if "all" in cond or "any" in cond:
        parts = cond.get("all") if "all" in cond else cond.get("any")
        if not isinstance(parts, list) or not parts:
            return [f"{where}: 'all' and 'any' take a list of conditions"]
        for c in parts:
            errors += _check_cond(c, known, where)
        return errors
    if "not" in cond:
        return _check_cond(cond["not"], known, where)
    if "state" in cond:
        if cond["state"] not in ("in", "not in", "==", "!="):
            errors.append(f"{where}: a state condition is 'in', 'not in', '==' or '!='")
        return errors
    if cond.get("field") not in known:
        errors.append(f"{where}: no field {cond.get('field')!r} to test")
    if cond.get("op") not in OPS:
        errors.append(f"{where}: unknown test {cond.get('op')!r} (one of {', '.join(OPS)})")
    return errors


# ---------------------------------------------------------------- checking a layout
def validate(hub, type_: str, layout: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings) for a layout: what it refers to exists, conditions are well formed, custom fields are named
    and typed properly and never renamed or retyped, no field a state requires is hidden when it is required."""
    errors, warnings = [], []
    td = hub.kernel.types.get(type_)
    if td is None:
        return [f"there is no record type {type_!r}"], []
    in_force = (active_entry(hub, type_) or {}).get("layout", {}).get("custom_fields") or {}
    for name, cf in (layout.get("custom_fields") or {}).items():
        if not CUSTOM_NAME.match(name):
            errors.append(f"custom field {name!r}: the name starts with x_ and has only lower-case letters, digits and _")
        if name in (td.get("fields") or {}) and name not in in_force:  # released custom fields are merged into the type
            errors.append(f"custom field {name!r}: the record type already has that field")
        if cf.get("type") not in CUSTOM_TYPES and not str(cf.get("type", "")).startswith("enum:"):
            errors.append(f"custom field {name!r}: unknown type {cf.get('type')!r}")
        if name in in_force and in_force[name].get("type") != cf.get("type"):
            errors.append(f"custom field {name!r} is a {in_force[name].get('type')}: a field's type never changes (add a new field instead)")
        old = in_force.get(name, {})
        if str(old.get("type", "")).startswith("enum:") and str(cf.get("type", "")).startswith("enum:"):
            gone = set(old["type"][5:].split(",")) - set(cf["type"][5:].split(","))
            if gone:
                errors.append(f"custom field {name!r}: options are added, never removed or renamed ({', '.join(sorted(gone))} would be lost)")
    for name in in_force:
        if name not in (layout.get("custom_fields") or {}):
            errors.append(f"custom field {name!r} is in use: retire it instead of removing it (its data is kept)")
    known = set(type_fields(hub, type_, layout)) | set((layout.get("custom_fields") or {}).keys())
    testable = known | set((td.get("fields") or {}).keys()) | set(td.get("system_fields") or [])  # fields the system sets too (an NCR's severity)
    seen, ids = set(), set()
    for s in layout.get("sections") or []:
        if not s.get("id") or s["id"] in ids:
            errors.append(f"section {s.get('title') or s.get('id')!r}: needs an id of its own")
        ids.add(s.get("id"))
        if not str(s.get("title") or "").strip():
            errors.append(f"section {s.get('id')!r}: give it a title")
        for r in s.get("fields") or []:
            f = r.get("field")
            if f not in known:
                errors.append(f"{s.get('title')}: no field {f!r}")
                continue
            if f in seen:
                errors.append(f"{f} is on the form twice")
            seen.add(f)
            if r.get("widget") and r["widget"] not in WIDGETS:
                errors.append(f"{f}: unknown widget {r['widget']!r}")
            for k in ("visible_when", "required_when", "readonly_when"):
                errors += _check_cond(r.get(k), testable, f"{f} {k.replace('_', ' ')}")
    required = {f for fs in (td.get("required_by_state") or {}).values() for f in fs}
    for s in layout.get("sections") or []:
        for r in s.get("fields") or []:
            if r.get("field") in required and r.get("visible_when"):
                states = [st for st, fs in (td.get("required_by_state") or {}).items() if r["field"] in fs]
                warnings.append(f"{r['field']} is required in {', '.join(states)}: make sure it is visible then")
    for f in sorted(required - seen):
        if f in type_fields(hub, type_, layout):
            errors.append(f"{f} is required in some states but is not on the form")
    start_fields = [r.get("field") for r in layout.get("start") or []]
    inputs = {r["field"] for r in base_layout(hub, type_).get("start") or [] if r.get("input") or r.get("spec")}
    for f in [x for x in start_fields if x not in known and x not in inputs]:
        errors.append(f"start form: no field {f!r}")
    from .shell.forms import REQUIRED_EXTRA

    for f in REQUIRED_EXTRA.get(type_, []):
        if f not in start_fields:
            errors.append(f"start form: {f} is needed to start a {type_.replace('-', ' ')}; keep it on the start form")
    return errors, warnings


def changes(old: dict, new: dict) -> list[str]:
    """What a layout changes, in words, for the change order and its reviewers."""
    from .shell.text import field_label

    out = []
    where_old = {r["field"]: (s.get("title"), i, r) for s in old.get("sections") or [] for i, r in enumerate(s.get("fields") or [])}
    where_new = {r["field"]: (s.get("title"), i, r) for s in new.get("sections") or [] for i, r in enumerate(s.get("fields") or [])}
    old_titles = [s.get("title") for s in old.get("sections") or []]
    new_titles = [s.get("title") for s in new.get("sections") or []]
    for t in new_titles:
        if t not in old_titles:
            out.append(f"new section “{t}”")
    for t in old_titles:
        if t not in new_titles:
            out.append(f"section “{t}” removed")
    for f, (sec, i, r) in where_new.items():
        lab = field_label(f)
        if f not in where_old:
            out.append(f"{lab} shown in “{sec}”")
            continue
        osec, oi, orow = where_old[f]
        if osec != sec:
            out.append(f"{lab} moved from “{osec}” to “{sec}”")
        elif oi != i and [x for x in where_old if where_old[x][0] == sec] != [x for x in where_new if where_new[x][0] == sec]:
            pass  # order within a section: summarised once below
        for k in PROPS:
            if (orow.get(k) or None) != (r.get(k) or None):
                if k.endswith("_when"):
                    out.append(f"{lab} {k.split('_')[0]} " + (f"when {describe(r[k])}" if r.get(k) else "always" if k == "visible_when" else "never"))
                else:
                    out.append(f"{lab}: {k} “{r.get(k) or ''}”" + (f" (was “{orow.get(k)}”)" if orow.get(k) else ""))
    for f in where_old:
        if f not in where_new:
            out.append(f"{field_label(f)} taken off the form (its values are kept)")
    for sec in set(new_titles) & set(old_titles):
        a = [x for x in where_old if where_old[x][0] == sec and x in where_new and where_new[x][0] == sec]
        b = [x for x in where_new if where_new[x][0] == sec and x in where_old and where_old[x][0] == sec]
        if a != b:
            out.append(f"fields reordered in “{sec}”")
    if [r.get("field") for r in old.get("start") or []] != [r.get("field") for r in new.get("start") or []]:
        out.append("start form: " + ", ".join(field_label(r["field"]) for r in new.get("start") or []))
    oc, nc = old.get("custom_fields") or {}, new.get("custom_fields") or {}
    for n, cf in nc.items():
        if n not in oc:
            out.append(f"new custom field {cf.get('label') or n} ({n}, {cf.get('type')})")
        elif cf.get("retired") and not oc[n].get("retired"):
            out.append(f"custom field {cf.get('label') or n} retired (hidden; its values are kept)")
        elif cf.get("type") != oc[n].get("type"):
            out.append(f"custom field {n}: options {cf.get('type')}")
    return out


# ---------------------------------------------------------------- what applies to a record now
def visible_rows(layout: dict, fields: dict, state: str) -> list[tuple[dict, list[dict]]]:
    """[(section, rows shown)] for a record: rows with a visible_when that does not hold are left out."""
    out = []
    for s in layout.get("sections") or []:
        rows = [r for r in s.get("fields") or [] if not r.get("visible_when") or evaluate(r["visible_when"], fields, state)]
        out.append((s, rows))
    return out


def required_now(layout: dict, fields: dict, state: str) -> list[str]:
    return [r["field"] for s in layout.get("sections") or [] for r in s.get("fields") or []
            if r.get("required_when") and evaluate(r["required_when"], fields, state) and fields.get(r["field"]) in (None, "", [], {})]


def readonly_now(layout: dict, fields: dict, state: str) -> set[str]:
    return {r["field"] for s in layout.get("sections") or [] for r in s.get("fields") or []
            if r.get("readonly_when") and evaluate(r["readonly_when"], fields, state)}


def row_of(layout: dict, field: str) -> dict:
    return next((r for s in layout.get("sections") or [] for r in s.get("fields") or [] if r.get("field") == field), {})


def check_change(hub, rec: dict, changed: dict, *, to_state: str | None = None) -> list[str]:
    """Why these field changes (or this move to another state) are refused by the form in force: a field read-only
    now, or a field required now and still empty."""
    lay = active_layout(hub, rec["type"])
    fields = {**rec["fields"], **(changed or {})}
    state = to_state or rec["state"]
    problems = []
    for f in sorted(readonly_now(lay, rec["fields"], rec["state"]) & set(changed or {})):
        if rec["fields"].get(f) != changed[f]:
            problems.append(f"{f} is read-only {describe(row_of(lay, f).get('readonly_when'))}")
    for f in required_now(lay, fields, state):
        problems.append(f"{f} is required when {describe(row_of(lay, f).get('required_when'))}")
    return problems


# ---------------------------------------------------------------- custom fields in the record types
def apply_custom_fields(hub) -> None:
    """The released custom fields as fields of their record types (retired ones stay readable, never offered)."""
    for type_, entry in hub.forms.active().items():
        td = hub.kernel.types.get(type_)
        if td is None:
            continue
        for name, cf in ((entry.get("layout") or {}).get("custom_fields") or {}).items():
            td.setdefault("fields", {})[name] = cf["type"]


# ---------------------------------------------------------------- drafts and change orders
def save_draft(hub, user: str, type_: str, layout: dict) -> dict:
    lay = {**copy.deepcopy(layout), "type": type_}
    errors, warnings = validate(hub, type_, lay)
    hub.forms.save_draft(user, type_, lay)
    return {"layout": lay, "errors": errors, "warnings": warnings, "changes": changes(active_layout(hub, type_), lay)}


def carry(hub, co_ref: str, user: str, type_: str) -> dict:
    """Put the person's draft layout for a type on a change order (a snapshot: a later edit is carried again)."""
    from . import eco

    co = eco._co(hub, co_ref)
    if eco.finished(co) or co["state"].startswith("awaiting"):
        raise ValueError(f"{co['number']} is {co['state']}: change what it carries while it is being prepared")
    lay = hub.forms.drafts(user).get(type_)
    if not lay:
        raise ValueError(f"you have no draft of the {type_.replace('-', ' ')} form")
    errors, _ = validate(hub, type_, lay)
    if errors:
        raise ValueError("the draft has problems: " + "; ".join(errors[:3]))
    carried = dict(co["fields"].get("form_changes") or {})
    base = active_entry(hub, type_)
    carried[type_] = {"layout": {k: v for k, v in lay.items() if k != "updated_at"}, "by": user, "at": utcnow(),
                      "base_version": (base or {}).get("version", 0), "changes": changes(active_layout(hub, type_), lay)}
    hub.kernel.set_fields(co["locator"], {"form_changes": carried}, by=user)
    return carried[type_]


def uncarry(hub, co_ref: str, type_: str, *, by: str) -> None:
    from . import eco

    co = eco._co(hub, co_ref)
    carried = dict(co["fields"].get("form_changes") or {})
    carried.pop(type_, None)
    hub.kernel.set_fields(co["locator"], {"form_changes": carried}, by=by)


def carried_reasons(hub, co: dict) -> list[str]:
    """Why the forms a change order carries cannot be released as they stand."""
    out = []
    for type_, c in (co["fields"].get("form_changes") or {}).items():
        errors, _ = validate(hub, type_, c["layout"])
        out += [f"{type_.replace('-', ' ')} form: {e}" for e in errors]
        now = (active_entry(hub, type_) or {}).get("version", 0)
        if now != c.get("base_version", 0):
            out.append(f"{type_.replace('-', ' ')} form: another layout was released since this one was carried; carry the draft again")
    return out


def activate(hub, co: dict, digest: str) -> list[str]:
    """The release automation's step: each carried layout comes into force (a new version), with its custom fields."""
    done = []
    for type_, c in (co["fields"].get("form_changes") or {}).items():
        now = active_entry(hub, type_) or {}
        if now.get("released_with") == co["number"]:
            done.append(f"{type_} v{now['version']}")
            continue
        entry = {"version": int(now.get("version", 0)) + 1, "layout": c["layout"], "released_with": co["number"], "release": digest,
                 "by": c.get("by"), "at": utcnow(), "changes": c.get("changes") or []}
        hub.forms.set_active(type_, entry)
        done.append(f"{type_} v{entry['version']}")
    apply_custom_fields(hub)
    return done
