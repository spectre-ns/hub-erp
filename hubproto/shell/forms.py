"""Form controls and the coercion of what people type into record fields."""

from __future__ import annotations

import json
import math
import re
from datetime import date

from .text import esc, field_label, prefill_text

TEXTAREA_FIELDS = {"description", "statement", "body", "rationale", "root_cause", "disposition_rationale", "effectiveness_result",
                   "summary", "impact", "plan", "verification", "response", "acceptance_criteria", "containment", "reason", "notes",
                   "effectiveness_criteria", "scope"}
RESERVED_KEYS = {"as", "process", "json", "run", "verdict", "meaning", "digest", "note", "reason", "record", "doc", "folder",
                 "title_", "files", "once", "confirm", "return", "open", "page", "type"}
CURRENCIES = ("USD", "EUR", "GBP", "CAD")
DOC_TYPES = {".kicad_sch": "schematic", ".kicad_pcb": "board", ".fcstd": "model", ".pdf": "drawing"}

# Start-form inputs per record type: (field, widget kind, type spec override). The widget kind is None (from the
# field's type spec), "textarea", "lines" (one list element per line), "lines:locator,qty" (one {part, quantity[, batch]}
# per line) or "lines:lot,qty" (one {lot, quantity}
# per line). Anything else goes in the advanced JSON box.
START_INPUTS: dict[str, list[tuple]] = {
    "bill": [("supplier",), ("amount",), ("currency",), ("date",), ("po_ref",), ("description", "textarea")],
    "design": [("title",), ("description", "textarea"), ("kind",), ("change_class",), ("user_needs", "lines")],
    "purchase-request": [("title",), ("part",), ("supplier_part",), ("quantity",), ("unit_cost",), ("supplier",), ("needed_by",), ("reason", "textarea")],
    "receipt": [("po",), ("location",), ("lines", "lines:locator,qty"), ("packing_slip",), ("notes", "textarea")],
    "ncr": [("title",), ("description", "textarea"), ("severity", None, "enum:low,medium,high"), ("source",), ("stock_items", "lines:lot,qty"),
            ("containment", "textarea")],
    "calibration": [("asset",), ("result",), ("standard",), ("procedure",)],
    "document": [("title",), ("doc_type",), ("change_type",), ("training_roles",), ("body", "textarea")],
    "task": [("title",), ("description", "textarea"), ("assignee",), ("due",), ("priority",)],
    "requirement": [("statement", "textarea"), ("kind",), ("source",), ("priority",)],
    "journal-reversal": [("journal",), ("reason", "textarea")],
    "part": [("title",), ("category",), ("units",), ("trackable",), ("purchaseable",), ("minimum_stock",), ("description", "textarea")],
    "stock-adjustment": [("lot",), ("new_quantity",), ("reason", "textarea")],
    "stock-release": [("lot",), ("to_location",), ("reason", "textarea")],
    "capa": [("title",), ("ncrs",), ("root_cause_method",)],
    "complaint": [("title",), ("customer",), ("description", "textarea"), ("product",), ("severity",)],
    "change-request": [("title",), ("description", "textarea"), ("impact", "textarea"), ("affected_documents",)],
    "audit": [("title",), ("scope_clauses",), ("processes",), ("planned",)],
    "management-review": [("title",), ("date",), ("inputs",)],
    "supplier-evaluation": [("supplier",), ("scope", "textarea")],
}
DEFAULT_START_INPUTS: list[tuple] = [("title",), ("description", "textarea")]
# Fields a submission needs before the first automatic step can do its work, when the type's own required-by-state
# list says nothing about the states before the first approval (the invoice extractor refuses a bill without them).
REQUIRED_EXTRA = {"bill": ["supplier", "amount", "currency", "date"], "purchase-request": ["title", "supplier"], "task": ["title"],
                  "ncr": ["title", "description"], "design": ["title", "kind"], "requirement": ["statement"], "document": ["title"],
                  "receipt": ["po", "lines"], "complaint": ["title", "description"], "change-request": ["title", "description"],
                  "audit": ["title"], "management-review": ["title"], "calibration": ["asset", "result"], "capa": ["title"],
                  "part": ["title"], "stock-adjustment": ["lot", "new_quantity", "reason"], "stock-release": ["lot", "reason"],
                  "journal-reversal": ["journal", "reason"], "supplier-evaluation": ["supplier"]}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ShellError(Exception):
    """A user-facing error with an HTTP status and, when known, the form field it is about."""

    def __init__(self, message: str, status: int = 400, field: str | None = None):
        super().__init__(message)
        self.status = status
        self.field = field


def coerce(name: str, spec: str | None, value: str, kind: str | None = None):
    """A form string to the record-type field type. Lists take JSON when the text starts with ``[``, else a comma
    list; objects take JSON; numbers are cast and must be finite; dates are YYYY-MM-DD; enums are checked. A value
    that does not fit is a readable error naming the field."""
    value = value.strip()
    label = field_label(name)
    if value == "":
        return None
    if kind == "lines":
        return [line.strip() for line in value.splitlines() if line.strip()]
    if kind in ("lines:locator,qty", "lines:lot,qty"):
        key, example = ("lot", "LOT-000001, 4") if kind == "lines:lot,qty" else ("part", "PN-00001, 4, BATCH-24")
        out = []
        for line in value.splitlines():
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            if key == "lot" and len(parts) == 1 and parts[0]:  # a lot alone: all of it
                out.append({"lot": parts[0]})
                continue
            if len(parts) not in (2, 3) or not parts[0] or (len(parts) == 3 and key == "lot"):
                raise ShellError(f"{label}: each line is '{key} number, quantity'" + (", batch" if key == "part" else " (or the lot alone for all of it)")
                                 + f" (for example {example}), not {line.strip()!r}.", field=name)
            try:
                qty = float(parts[1])
            except ValueError:
                raise ShellError(f"{label}: the quantity in {line.strip()!r} is not a number.", field=name) from None
            if not math.isfinite(qty) or qty <= 0:
                raise ShellError(f"{label}: the quantity in {line.strip()!r} must be a positive number.", field=name)
            row = {key: parts[0], "quantity": qty}
            if len(parts) == 3 and parts[2]:
                row["batch"] = parts[2]
            out.append(row)
        return out
    spec = spec or "text"
    if spec.startswith("list") and not value.startswith("[") and ";" in value:
        # One row per line: 'action; owner; due date; status' (the CAPA actions editor), never JSON for a person.
        rows = []
        for line in value.splitlines():
            parts = [p.strip() for p in line.split(";")]
            if not parts or not parts[0]:
                continue
            row = {"action": parts[0]}
            for key, v in zip(("owner", "due", "status"), parts[1:]):
                if v:
                    row[key] = v
            row.setdefault("status", "open")
            rows.append(row)
        return rows
    if spec.startswith("enum:"):
        opts = spec[5:].split(",")
        if value not in opts:
            raise ShellError(f"{label} must be one of {', '.join(opts)}, not {value!r}.", field=name)
        return value
    if spec in ("int", "number", "money"):
        try:
            num = float(value.replace(",", "")) if spec == "money" else float(value)
        except ValueError:
            raise ShellError(f"{label} must be a number, not {value!r}.", field=name) from None
        if not math.isfinite(num):
            raise ShellError(f"{label} must be a finite number, not {value!r}.", field=name)
        if num < 0:
            raise ShellError(f"{label} cannot be negative ({value!r}).", field=name)
        if spec == "int":
            if num != int(num):
                raise ShellError(f"{label} must be a whole number, not {value!r}.", field=name)
            return int(num)
        return num
    if spec == "date":
        if not _DATE_RE.match(value):
            raise ShellError(f"{label} must be a date written YYYY-MM-DD, not {value!r}.", field=name)
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ShellError(f"{label}: {value!r} is not a real date.", field=name) from None
        return value
    if spec == "bool":
        return value.lower() in ("1", "true", "yes", "on")
    if spec == "list":
        if value.startswith("["):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as e:
                raise ShellError(f"{label} looks like JSON but does not parse: {e}.", field=name) from None
            if not isinstance(parsed, list):
                raise ShellError(f"{label} must be a JSON list.", field=name)
            return parsed
        return [x.strip() for x in value.split(",") if x.strip()]
    if spec == "object":
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as e:
            raise ShellError(f"{label} must be a JSON object: {e}.", field=name) from None
        if not isinstance(parsed, dict):
            raise ShellError(f"{label} must be a JSON object.", field=name)
        return parsed
    return value


def prefill(v) -> str:
    return prefill_text(v)


def format_hint(spec: str | None, kind: str | None, name: str) -> str:
    if kind == "lines":
        return "one per line"
    if kind == "lines:locator,qty":
        return "one per line: part number, quantity, batch if it has one"
    if kind == "lines:lot,qty":
        return "one per line: lot number, quantity affected (or the lot alone for all of it)"
    spec = spec or "text"
    if spec == "date":
        return "YYYY-MM-DD"
    if spec == "money":
        return "amount, e.g. 1500.00"
    if spec in ("int", "number"):
        return "number"
    if spec == "list":
        return "comma-separated"
    if spec == "ref":
        return "record number"
    return ""


def widget(name: str, spec: str | None, value="", required: bool = False, kind: str | None = None, label: str | None = None,
           people: list[str] | None = None, options: list[tuple[str, str]] | None = None, error: str | None = None,
           hint: str | None = None, blank: str = "choose…") -> str:
    """One labelled form control for a record field. ``required`` only when the server requires it; ``options``
    turns it into a picker (any posted value is still accepted, so a typed number works too); ``error`` marks it."""
    req = " required" if required else ""
    spec = spec or "text"
    text = label or field_label(name)
    h = hint if hint is not None else ("" if options is not None else format_hint(spec, kind, name))
    star = ' <b aria-hidden="true">*</b>' if required else ""
    lab = f"<span>{esc(text)}{star}{(' <i>(' + esc(h) + ')</i>') if h else ''}</span>"
    eid = f"err-{re.sub(r'[^a-z0-9_-]', '', name.lower())}"
    aria = f' aria-invalid="true" aria-describedby="{eid}"' if error else ""
    cls = ' class="f bad"' if error else ' class="f"'
    tail = f'<span class="err" id="{eid}">{esc(error)}</span>' if error else ""
    if kind not in ("lines:locator,qty", "lines:lot,qty") and isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
        # A list of rows (e.g. CAPA actions) as editable lines, not JSON.
        keys = ("action", "owner", "due", "status")
        shown = "\n".join("; ".join(str(x.get(k, "") or "") for k in keys).rstrip("; ") for x in value)
        lab = f"<span>{esc(text)}{star} <i>(one per line: action; owner; due date; status)</i></span>"
        return f'<label{cls}>{lab}<textarea name="{esc(name)}" rows="{min(8, len(value) + 2)}"{req}{aria}>{esc(shown)}</textarea>{tail}</label>'
    val = prefill(value)
    if kind == "lines":
        shown = val if isinstance(value, str) else "\n".join(map(str, value or []))
        return f'<label{cls}>{lab}<textarea name="{esc(name)}" rows="3"{req}{aria}>{esc(shown)}</textarea>{tail}</label>'
    if kind in ("lines:locator,qty", "lines:lot,qty"):
        key = "lot" if kind == "lines:lot,qty" else "part"
        number = lambda ref: str(ref or "").rsplit(":", 1)[-1]
        shown = "\n".join(", ".join(str(x) for x in (number(l.get(key) or l.get("item")), l.get("quantity"), l.get("batch")) if x not in (None, ""))
                          for l in (value or []) if isinstance(l, dict)) if isinstance(value, list) else val
        return f'<label{cls}>{lab}<textarea name="{esc(name)}" rows="3"{req}{aria}>{esc(shown)}</textarea>{tail}</label>'
    if options is not None:
        opts = list(options)
        if val and val not in {o[0] for o in opts}:
            opts.insert(0, (val, val))
        body = "".join(f'<option value="{esc(o)}"{" selected" if o == val else ""}>{esc(t)}</option>' for o, t in opts)
        return f'<label{cls}>{lab}<select name="{esc(name)}"{req}{aria}><option value="">{esc(blank)}</option>{body}</select>{tail}</label>'
    if spec.startswith("enum:"):
        opts = "".join(f'<option value="{esc(o)}"{" selected" if o == val else ""}>{esc(o)}</option>' for o in spec[5:].split(","))
        return f'<label{cls}>{lab}<select name="{esc(name)}"{req}{aria}><option value="">{esc(blank)}</option>{opts}</select>{tail}</label>'
    if spec == "principal" and people:
        opts = "".join(f'<option value="{esc(p)}"{" selected" if p == val else ""}>{esc(p)}</option>' for p in people)
        return f'<label{cls}>{lab}<select name="{esc(name)}"{req}{aria}><option value="">{esc(blank)}</option>{opts}</select>{tail}</label>'
    if name == "currency" and spec == "text":
        cur = val or "USD"
        opts = "".join(f'<option value="{c}"{" selected" if c == cur else ""}>{c}</option>' for c in (CURRENCIES if cur in CURRENCIES else (cur, *CURRENCIES)))
        return f'<label{cls}>{lab}<select name="{esc(name)}"{req}{aria}>{opts}</select>{tail}</label>'
    if spec == "date":
        return f'<label{cls}>{lab}<input type="date" name="{esc(name)}" value="{esc(val)}"{req}{aria}>{tail}</label>'
    if spec in ("int", "number", "money"):
        step = "1" if spec == "int" else "any"
        return f'<label{cls}>{lab}<input type="number" step="{step}" min="0" name="{esc(name)}" value="{esc(val)}"{req}{aria}>{tail}</label>'
    if spec == "bool":
        return (f'<label{cls}>{lab}<select name="{esc(name)}"{aria}><option value="">leave unset</option><option value="true"{" selected" if val == "true" else ""}>yes</option>'
                f'<option value="false"{" selected" if val == "false" else ""}>no</option></select>{tail}</label>')
    if spec == "object":
        return f'<label{cls}>{lab}<textarea name="{esc(name)}" rows="2"{req}{aria}>{esc(val)}</textarea>{tail}</label>'
    if kind == "textarea" or (spec == "text" and name in TEXTAREA_FIELDS):
        return f'<label{cls}>{lab}<textarea name="{esc(name)}" rows="2"{req}{aria}>{esc(val)}</textarea>{tail}</label>'
    return f'<label{cls}>{lab}<input name="{esc(name)}" value="{esc(val)}"{req}{aria}>{tail}</label>'


def text_input(name: str, label: str, value: str = "", required: bool = False, error: str | None = None, textarea: bool = False,
               hint: str = "", rows: int = 2, type_: str = "text") -> str:
    """A labelled free-text control for notes, reasons, subjects and titles (never a placeholder as the only label)."""
    return widget(name, "text", value, required=required, kind="textarea" if textarea else None, label=label, error=error, hint=hint)


def file_input(name: str, label: str, multiple: bool = False, required: bool = False, hint: str = "") -> str:
    h = f" <i>({esc(hint)})</i>" if hint else ""
    star = ' <b aria-hidden="true">*</b>' if required else ""
    return (f'<label class="f"><span>{esc(label)}{star}{h}</span>'
            f'<input type="file" name="{esc(name)}"{" multiple" if multiple else ""}{" required" if required else ""}></label>')


def error_field(message: str, candidates) -> str | None:
    """The form field an engine message is about, when it names one: 'invoice has no supplier' -> supplier."""
    text = " " + re.sub(r"[^a-z0-9_ ]", " ", str(message).lower()) + " "
    for name in candidates:
        if f" {name.lower()} " in text or f" {name.lower().replace('_', ' ')} " in text or f" {field_label(name).lower()} " in text:
            return name
    if "purchase order" in text or " po " in text:
        for name in candidates:
            if name in ("po_ref", "po"):
                return name
    if "period" in text:
        for name in candidates:
            if name == "date":
                return name
    return None


def doc_type_for(filename: str) -> str:
    import os

    ext = os.path.splitext(filename)[1].lower()
    return DOC_TYPES.get(ext, "document")
