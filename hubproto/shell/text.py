"""Words: one display name per thing, formatters for money, dates and quantities, sentences for permissions,
errors, notifications and history. Nothing here touches the hub; every function takes plain data."""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime

MAX_SHOWN = 300  # characters of a value shown on a card or a table cell
BLOB_FIELDS = ("text", "body", "citations", "text_excerpt")

# One display name per record type, used in every heading, select, column and sentence.
TYPE_NAMES = {
    "bill": "Supplier bill", "purchase-request": "Purchase request", "purchase-order": "Purchase order", "ncr": "Nonconformance",
    "capa": "Corrective action (CAPA)", "design": "Design", "document": "Document", "task": "Task", "requirement": "Requirement",
    "receipt": "Goods receipt", "calibration": "Calibration", "complaint": "Complaint", "change-request": "Change request", "change-order": "Change order",
    "audit": "Internal audit", "management-review": "Management review", "supplier-evaluation": "Supplier evaluation",
    "item": "Item (archived)", "part": "Part", "asset": "Asset", "journal": "Journal", "journal-reversal": "Journal reversal",
    "training": "Training sign-off", "stock-item": "Stock lot", "stock-location": "Stock location", "part-category": "Part category",
    "supplier-part": "Supplier part", "manufacturer-part": "Manufacturer part", "stock-adjustment": "Stock adjustment",
    "stock-release": "Stock release",
}
# What a person does when they start the process that creates a record of this type (the button and the entry title).
VERBS = {
    "bill": "File a supplier bill", "purchase-request": "Raise a purchase request", "ncr": "Report a nonconformance",
    "capa": "Open a corrective action", "design": "Submit a design for release", "document": "New controlled document",
    "task": "Create a task", "requirement": "Capture a requirement", "receipt": "Record a goods receipt",
    "calibration": "Record a calibration", "complaint": "Log a complaint", "change-request": "Raise a change request", "change-order": "Prepare a change order",
    "audit": "Plan an internal audit", "management-review": "Prepare a management review",
    "supplier-evaluation": "Evaluate a supplier", "part": "Request a part", "journal-reversal": "Request a journal reversal",
    "training": "Assign a training sign-off", "stock-adjustment": "Request a stock adjustment",
    "stock-release": "Request a release from quarantine",
}
# Processes whose item is prepared before the process starts (a change order gathers its revisions first): their
# "new" link opens that page instead of a start form.
PREPARED_FIRST = {"change-order": "/change-orders"}
# Field labels in words; anything missing is the field name with underscores as spaces, capitalised.
FIELD_LABELS = {
    "po_ref": "Purchase order", "po": "Purchase order", "mpn": "Manufacturer part number", "doc_type": "Document type",
    "change_type": "Change type", "change_class": "Change class", "training_roles": "Roles that must read it", "needed_by": "Needed by",
    "unit_cost": "Unit cost", "user_needs": "User needs", "affected_documents": "Affected documents", "scope_clauses": "Clauses in scope",
    "root_cause": "Root cause", "root_cause_method": "Root cause method", "disposition_rationale": "Reason for the disposition",
    "effectiveness_result": "Effectiveness result", "acceptance_criteria": "Acceptance criteria", "verification_method": "Verification method",
    "lead_time_days": "Lead time (days)", "reevaluation_date": "Re-evaluation date", "ncrs": "Nonconformances", "date": "Invoice date",
    "due": "Due", "amount": "Amount", "currency": "Currency", "supplier": "Supplier", "item": "Item", "quantity": "Quantity",
    "description": "Description", "title": "Title", "statement": "Statement", "body": "Text", "lines": "Lines received",
    "assignee": "Assignee", "priority": "Priority", "severity": "Severity", "source": "Source", "lot": "Lot", "kind": "Kind",
    "containment": "Containment", "asset": "Asset", "result": "Result", "standard": "Standard", "procedure": "Procedure",
    "journal": "Journal", "reason": "Reason", "name": "Name", "manufacturer": "Manufacturer", "stock": "Stock", "customer": "Customer",
    "product": "Product", "impact": "Impact", "processes": "Processes", "planned": "Planned date", "inputs": "Inputs", "scope": "Scope",
    "location": "Location", "notes": "Notes", "disposition": "Disposition", "actions": "Actions", "findings": "Findings",
    "summary": "Summary", "assessed_by": "Assessed by", "score": "Score", "criteria": "Criteria", "approved": "Approved",
    "verification": "Verification", "plan": "Plan", "effectiveness_check_date": "Effectiveness check date",
    "part": "Part", "supplier_part": "Supplier part", "manufacturer_part": "Manufacturer part", "ipn": "Internal part number (IPN)",
    "units": "Units", "trackable": "Tracked by", "purchaseable": "Purchaseable", "assembly": "Assembly", "component": "Component",
    "active": "Active", "default_location": "Default location", "minimum_stock": "Minimum stock", "parameters": "Parameters",
    "category": "Category", "batch": "Batch", "serial": "Serial number", "status": "Stock status", "expiry": "Expiry date",
    "to_location": "To location", "new_quantity": "New quantity", "quantity_before": "Quantity before", "variance_pct": "Variance (%)",
    "pack_quantity": "Pack quantity", "price_breaks": "Price breaks", "sku": "Supplier SKU", "structural": "Structural (holds no stock)",
    "site": "Site", "stock_items": "Stock affected", "quarantined": "Quarantined lots", "ordered_by": "Ordered (approved) by",
    "quarantined_from": "Quarantined from", "packing_slip": "Packing slip", "lots": "Lots booked",
}
# Examples of what the meaning of a signature says, per record type (shown once on the open card).
MEANING_EXAMPLES = {
    "bill": ("invoice matches PO-1002 and the goods were received", "approved; the 3% variance is accepted"),
    "purchase-request": ("needed for line 2, price checked against the quote", "approved at 12,000 USD from Acme Metals"),
    "ncr": ("scrap: the housing cannot be reworked", "use as is: cosmetic, within drawing tolerance"),
    "design": ("design reviewed against the requirements; release", "drawings complete, DRC clean"),
    "document": ("reviewed revision B; the change is correct", "approved for release to the floor"),
    "training": ("I have read rev A and understand it", "read and understood; questions asked to the author"),
    "task": ("done: label stock ordered, PO-0031", "completed as described"),
    "receipt": ("received in full, no damage", "received; 3 of 5 units, the rest is back-ordered"),
    "requirement": ("accepted: testable and traced to the user need", "accepted with the stated verification method"),
    "capa": ("actions verified on the line", "effective: no recurrence in 90 days"),
    "audit": ("findings recorded as observed", "findings accepted; CAPA to follow"),
    "change-request": ("approved; impact on WI-7 understood", "verified on the first batch"),
    "stock-adjustment": ("recounted twice: 40 are missing, written off", "approved; the shrinkage is booked"),
    "stock-release": ("certificate of conformance received; release", "re-inspected 20 of 200, all within drawing"),
    "part": ("approved: datasheet checked, two sources", "approved as a purchased component"),
}
NOTIFICATION_KINDS = ("escalation", "failure", "returned", "rejection", "auto-decided", "request", "review", "mention", "agent-run", "withdrawn", "moved",
                      "intervention", "automation", "task")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_LOCATOR = re.compile(r"rec:[a-z-]+:([A-Z]+-[0-9A-Z-]+)")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z)?")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


# ---------------------------------------------------------------- escaping and truncation
def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def clean(s: str) -> str:
    """Control characters out of anything a person typed before it is shown or stored."""
    return _CONTROL.sub("", s or "")


def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, default=str)
    return str(v)


def short(v, n: int = MAX_SHOWN) -> str:
    s = fmt(v)
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------- names
def type_name(t: str | None) -> str:
    t = t or ""
    return TYPE_NAMES.get(t) or t.replace("-", " ").capitalize()


def verb_for(rt: str | None, process: str = "") -> str:
    return VERBS.get(rt or "") or ("Start " + (process or rt or "").replace("-", " "))


def field_label(name: str) -> str:
    return FIELD_LABELS.get(name) or name.replace("_", " ").capitalize()


def role_words(role: str) -> str:
    return role.replace("-", " ")


def roles_plural(roles) -> str:
    """'controllers and payables reviewers' from role slugs."""
    words = []
    for r in roles:
        w = role_words(r)
        words.append(w if w.endswith("s") else w + "s")
    return join_words(words)


def join_words(items, last: str = "and") -> str:
    items = [str(x) for x in items if x not in (None, "")]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" {last} " + items[-1]


def state_name(state: str | None, wf: dict | None = None) -> str:
    """A workflow state in words: 'awaiting-approve-the-bill' is 'Awaiting: Approve the bill'."""
    s = state or ""
    if s.startswith("awaiting-"):
        cp = s[len("awaiting-"):]
        for c in (wf or {}).get("checkpoints", []):
            if c.get("id") == cp:
                return "Awaiting: " + c.get("title", cp)
        return "Awaiting: " + cp.replace("-", " ").capitalize()
    return s.replace("-", " ").capitalize()


def step_words(step_id: str | None) -> str:
    return (step_id or "?").replace("-", " ")


def chip_class(value) -> str:
    """Colour by meaning: status words and risk, match and yes/no values; anything else is neutral."""
    s = str(value or "").lower()
    if s.startswith("awaiting") or s in ("waiting", "draft", "open", "in-review", "returned", "medium", "warning", "pending", "not run",
                                          "gate-pending", "escalated", "on hold", "submitted", "running"):
        return "warn"
    if s in ("posted", "effective", "closed", "approved", "acknowledged", "completed", "passed", "received", "filed", "released",
             "active", "ordered", "done", "accepted", "allowed", "readable", "low", "yes", "within tolerance", "ok", "in effect", "pass", "review pass"):
        return "ok"
    if s in ("failed", "rejected", "obsolete", "fail", "cancelled", "overdue", "denied", "no access", "high", "no", "outside tolerance",
             "error", "stopped", "blocked", "not received", "supplier not approved", "crit", "review fail"):
        return "crit"
    return ""


def chip(value, cls: str | None = None, title: str | None = None) -> str:
    s = str(value or "")
    c = chip_class(s) if cls is None else cls
    t = f' title="{esc(title)}"' if title else ""
    return f'<span class="chip {c}"{t}>{esc(short(s, 80))}</span>'


def tag(value) -> str:
    """A quiet outlined label for a category (type, role, label), never a status."""
    return f'<span class="tag">{esc(short(value, 80))}</span>'


# ---------------------------------------------------------------- numbers and dates
def money(v, currency: str | None = "USD") -> str:
    try:
        num = float(v)
    except (TypeError, ValueError):
        return f"{fmt(v)} {currency or ''}".strip()
    if num != num or num in (float("inf"), float("-inf")):
        return f"{v} {currency or ''}".strip()
    return f"{num:,.2f} {currency or 'USD'}".strip()


def qty(v, unit: str | None = None) -> str:
    try:
        num = float(v)
    except (TypeError, ValueError):
        return f"{fmt(v)} {unit or ''}".strip()
    text = f"{int(num):,}" if num == int(num) else f"{num:,.3f}".rstrip("0").rstrip(".")
    return f"{text} {unit or ''}".strip()


def day(s) -> str:
    """'2026-11-06' as '6 Nov 2026'; anything else is shown as typed."""
    if not s:
        return ""
    try:
        d = date.fromisoformat(str(s)[:10])
    except ValueError:
        return str(s)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def when(ts) -> str:
    """An ISO timestamp as '6 Nov 2026 14:38'."""
    if not ts:
        return ""
    s = str(ts)
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return day(s) if len(s) >= 10 else s
    return f"{d.day} {MONTHS[d.month - 1]} {d.year} {d:%H:%M}"


def days_from(due: str | None, today: str) -> int | None:
    try:
        return (date.fromisoformat(str(due)[:10]) - date.fromisoformat(today)).days
    except (TypeError, ValueError):
        return None


def due_chip(due: str | None, today: str) -> str:
    """The due date in words, red only when overdue, amber when due within three days."""
    if not due:
        return ""
    n = days_from(due, today)
    if n is None:
        return esc(due)
    if n < 0:
        return chip(f"overdue {-n} day{'s' if n != -1 else ''}", "crit", title=day(due))
    if n == 0:
        return chip("due today", "warn", title=day(due))
    if n <= 3:
        return chip(f"due in {n} day{'s' if n != 1 else ''}", "warn", title=day(due))
    return f'<span class="muted">due {esc(day(due))}</span>'


def value_text(name: str, v, fields: dict | None = None) -> str:
    """A field value for a person: money with its currency, dates in words, lists joined, yes/no."""
    f = fields or {}
    if v in (None, "", [], {}):
        return ""
    if name in ("amount", "unit_cost", "billed_to_date"):
        return money(v, f.get("currency") or "USD")
    if name in ("quantity", "stock", "lead_time_days"):
        return qty(v, f.get("unit") if name != "lead_time_days" else None)
    if name in ("date", "due", "needed_by", "planned", "review_date", "effective_from", "reevaluation_date", "effectiveness_check_date"):
        return day(v)
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, list) and all(isinstance(x, (str, int, float)) for x in v):
        return ", ".join(str(x) for x in v)
    return short(v, 600)


# ---------------------------------------------------------------- sentences
_DATE_ONLY = re.compile(r"(?<![\d-])(\d{4}-\d{2}-\d{2})(?![\dT:-])")
_STATE_SLUG = re.compile(r"\bawaiting-([a-z][a-z-]*[a-z])\b")
_PY_LIST = re.compile(r"\[((?:'[^'\]]*'(?:,\s*)?)+)\]")
_FLOAT_MONEY = re.compile(r"\b(\d+)\.0\b(?=\s*(?:USD|EUR|GBP|percent|days)\b)")
_CONDITION_KEY = None


def _condition_words(m) -> str:
    from ..rules import CONDITION_VOCABULARY

    names = {}
    for phrase, key in CONDITION_VOCABULARY.items():
        names.setdefault(key, phrase)
    return names.get(m.group(0), m.group(0))


def machine_words(s: str) -> str:
    """Engine text in words: date-only ISO dates, 'awaiting-<step>' states, Python lists, '1000.0 USD', condition keys."""
    global _CONDITION_KEY
    if _CONDITION_KEY is None:
        from ..rules import CONDITION_VOCABULARY

        _CONDITION_KEY = re.compile(r"\b(" + "|".join(sorted(set(CONDITION_VOCABULARY.values()), key=len, reverse=True)) + r")\b")
    s = _DATE_ONLY.sub(lambda m: day(m.group(1)), s)
    s = _STATE_SLUG.sub(lambda m: "waiting at " + m.group(1).replace("-", " ").capitalize(), s)
    s = _PY_LIST.sub(lambda m: ", ".join(x.strip().strip("'") for x in m.group(1).split(",") if x.strip()), s)
    s = _FLOAT_MONEY.sub(lambda m: f"{int(m.group(1)):,}", s)
    return _CONDITION_KEY.sub(_condition_words, s)


def humanize(text: str, link_number=None) -> str:
    """Locators, ISO timestamps and other machine text out of a sentence written by the engine; escaped HTML."""
    out = []
    pos = 0
    s = machine_words(clean(str(text or "")))
    for m in _LOCATOR.finditer(s):
        out.append(esc(_TIMESTAMP.sub(lambda t: when(t.group(0)), s[pos:m.start()])))
        number = m.group(1)
        out.append(link_number(m.group(0), number) if link_number else esc(number))
        pos = m.end()
    out.append(esc(_TIMESTAMP.sub(lambda t: when(t.group(0)), s[pos:])))
    return "".join(out)


def friendly(message: str) -> str:
    """An engine message as a sentence for the person who hit it: no class names, no match counts, a next step."""
    m = str(message or "").strip()
    g = re.match(r"^proposal (\w+) is (superseded|released|withdrawn); compile a new one$", m)
    if g:
        return {"superseded": "This proposal was replaced by a newer one; open the newer proposal from the Proposals page.",
                "released": "This proposal is already released.",
                "withdrawn": "This proposal was withdrawn; propose the draft again to review it."}[g.group(2)]
    g = re.match(r"^proposal #?(\w+) is stale: (.*)$", m)
    if g:
        return f"Proposal #{g.group(1)} is out of date: {g.group(2)}."
    g = re.match(r"^(\S+) is (\w[\w-]*)'s draft: only its author or a policy owner proposes it$", m)
    if g:
        return f"{g.group(1)} is {g.group(2)}'s draft; only {g.group(2)} or a policy owner proposes it."
    g = re.match(r"^proposal #?(\w+) is superseded \(replaced by #(\d+): release that one\)$", m)
    if g:
        return f"Proposal #{g.group(1)} was replaced by a newer one, #{g.group(2)}: review and release that one."
    if m.startswith("the proposal is based on release"):
        return ("The policy in force changed after this proposal was made, so releasing it would undo that change. Propose the drafts "
                "again so they are compared with the policy in force.")
    g = re.match(r"^run '(.*)': (\d+) matches$", m)
    if g:
        ident, n = g.group(1), int(g.group(2))
        if not ident:
            return "No run was named. Go back to your inbox and open the item again."
        return f"There is no run starting with '{ident}'." if n == 0 else f"'{ident}' matches {n} runs; give more of the id."
    g = re.match(r"^release '(.*)': (\d+) matches$", m)
    if g:
        return f"There is no policy version starting with '{g.group(1)}'." if g.group(2) == "0" else f"'{g.group(1)}' matches several policy versions."
    g = re.match(r"^exists: no record '(.*)'$", m)
    if g:
        return "Choose a record." if not g.group(1) else f"There is no record '{g.group(1)}'."
    g = re.match(r"^link-vocabulary: unknown link relation '(.*)'$", m)
    if g:
        return f"'{g.group(1)}' is not a link relation; choose one from the list."
    g = re.match(r"^run (\S+) is not waiting for a (\w+) \(status (\w+)\)$", m)
    if g:
        return f"This item is no longer waiting for a {g.group(2)}: its run is {g.group(3)}. Open it to see what happened."
    g = re.match(r"^run (\S+) is not failed \(status (\w+)\)$", m)
    if g:
        return f"Only a stopped run can be retried; this one is {g.group(2)}."
    g = re.match(r"^run (\S+) is (\w+) and cannot be cancelled$", m)
    if g:
        return f"This run is already {g.group(1 + 1)}; there is nothing to cancel."
    if m.startswith("the item changed since you opened it"):
        return "The item changed after you opened it (its fields, links or documents differ). Here is the current card; decide on what it is now."
    g = re.match(r"^a decision may not set (.*); this Gate accepts (.*)$", m)
    if g:
        return f"You may not set {g.group(1)} here; this approval accepts {g.group(2)}."
    g = re.match(r"^(\S+) needs (.*) before approval; supply them with the decision$", m)
    if g:
        return f"Fill in {g.group(2)} before approving."
    g = re.match(r"^([\w.-]+)\.(\w+) (must be|cannot be) (.*)$", m)  # kernel field checks: 'bill.amount must be ...'
    if g:
        return f"{field_label(g.group(2))} {g.group(3)} {g.group(4)}"
    g = re.match(r"^field-(?:number|date|enum): (.*)$", m)
    if g:
        return friendly(g.group(1))
    g = re.match(r"^(\w[\w-]*): (.*)$", m)  # 'guard: detail' from the kernel
    if g and g.group(1) in ("hold", "immutable", "controlled", "checked-out", "record-type", "requirements", "period-open", "exists"):
        return g.group(2)[0].upper() + g.group(2)[1:] + ("" if g.group(2).endswith(".") else ".")
    g = re.match(r"^no rule in the release lets any of \[(.*)\] (\w+) (\S+) in (.*)$", m)
    if g:
        roles = [r.strip(" '") for r in g.group(1).split(",")]
        return f"No rule lets {join_words(role_words(r) for r in roles)} {g.group(2)} {g.group(3)} in {g.group(4)}."
    g = re.match(r"^(\S+) is not an assignee of this decision \(assigned: (.*)\)$", m)
    if g:
        return f"This item is not assigned to you; it is with {g.group(2)}."
    if m and not m.endswith("."):
        m += "."
    return m[0].upper() + m[1:] if m else "Something went wrong."


def who_may(bundle: dict, action: str, obj: str, folder: str) -> list[str]:
    """Roles the release lets act this way here (allows minus denies)."""
    def folder_matches(rule_folder: str) -> bool:
        return folder == rule_folder or folder.startswith(rule_folder + "/")

    allowed, denied = [], set()
    for rule in bundle.get("permissions", []):
        if rule["action"] != action or rule["object"] not in ("*", obj) or not folder_matches(rule["resource"].partition(":")[2]):
            continue
        role = rule["subject"].partition(":")[2]
        if rule["effect"] == "deny":
            denied.add(role)
        elif role not in allowed:
            allowed.append(role)
    return [r for r in allowed if r not in denied]


def why_sentence(bundle: dict | None, decision: dict, principal: str, action: str, obj: str, folder: str, link_rule=None) -> str:
    """'Who may: ...; you are: ...; ask: ...' for a denial; 'Allowed by <rule>' for a grant. Escaped HTML."""
    rule = decision.get("rule") or {}
    src = rule.get("source") or {}
    cite = link_rule(src) if (link_rule and src) else (esc(f"{src.get('frame')} line {src.get('line')}") if src else "")
    if decision.get("allowed"):
        return f"Allowed by {cite}: “{esc(src.get('text', ''))}”" if src else esc(decision.get("why", "allowed"))
    bundle = bundle or {"members": {}, "permissions": []}
    member = bundle["members"].get(principal)
    if member is None:
        return f"{esc(principal)} is not a member of the organization; ask a policy owner to add you to the org model."
    roles = who_may(bundle, action, obj, folder)
    owners = sorted(p for p, m in bundle["members"].items() if "policy-owner" in m.get("roles", []) and m.get("type") == "person")
    yours = join_words(role_words(r) for r in member.get("roles", [])) or "no role"
    who = roles_plural(roles) if roles else "nobody under the release in force"
    out = f"Who may: {esc(who)}"
    if src and rule.get("effect") == "deny":
        out += f" (you are denied by {cite}: “{esc(src.get('text', ''))}”)"
    out += f"; you are: {esc(yours)}; ask: {esc(join_words(owners, 'or') or 'a policy owner')} to change the policy."
    return out


def event_sentence(ev: dict, link_number=None, type_word: str = "item") -> str:
    """One record event as a sentence (escaped HTML). The JSON stays available behind a disclosure."""
    t, d, by = ev.get("type", ""), ev.get("data") or {}, esc(ev.get("by", ""))
    link = (lambda loc: link_number(loc, loc.rsplit(":", 1)[-1]) if link_number else esc(loc.rsplit(":", 1)[-1]))
    if t == "record.created":
        f = d.get("fields") or {}
        facts = [f"{field_label(k).lower()} {value_text(k, v, f)}" for k, v in f.items() if k in ("supplier", "amount", "title", "quantity", "severity", "kind")]
        return f"{by} created the {esc(type_word)}" + (f" ({esc('; '.join(facts))})" if facts else "")
    if t == "record.fields_set":
        return f"{by} set {esc(join_words(field_label(k).lower() for k in (d.get('fields') or {})))}"
    if t == "record.transitioned":
        dec = d.get("decision") or {}
        s = f"{by} moved it from {esc(state_name(d.get('from')))} to {esc(state_name(d.get('to')))}"
        if dec:
            s = f"{esc(dec.get('by'))} {esc(dec.get('verdict'))}d it: “{esc(dec.get('meaning', ''))}”; now {esc(state_name(d.get('to')))}"
        if d.get("reason"):
            s += f" ({esc(short(d['reason'], 160))})"
        if d.get("assignees"):
            s += f"; with {esc(', '.join(d['assignees']))}" + (f", due {esc(day(d.get('due')))}" if d.get("due") else "")
        return s
    if t == "record.decision":
        return f"{esc(d.get('by'))} {esc(d.get('verdict'))}d it: “{esc(d.get('meaning', ''))}”"
    if t == "record.assigned":
        return f"{by} assigned it to {esc(', '.join(d.get('assignees') or []) or 'nobody')}" + (f", due {esc(day(d.get('due')))}" if d.get("due") else "")
    if t == "record.linked":
        return f"{by} linked it ({esc(d.get('rel'))}) to {link(str(d.get('target', '')))}"
    if t == "record.unlinked":
        return f"{by} removed the link ({esc(d.get('rel'))}) to {link(str(d.get('target', '')))}"
    if t == "record.labelled":
        return f"{by} labelled it {esc(', '.join(f'{k}: {v}' for k, v in (d.get('labels') or {}).items()))}"
    if t == "record.held":
        return f"{by} {'put it on hold' if d.get('on') else 'released the hold'}" + (f": {esc(short(d.get('reason'), 160))}" if d.get("reason") else "")
    if t == "record.archived":
        return f"{by} archived it"
    if t == "document.checked_out":
        return f"{by} checked it out"
    if t.startswith("document."):
        return f"{by}: {esc(t.split('.', 1)[1].replace('_', ' '))}" + (f" version {esc(d.get('version'))}" if d.get("version") else "")
    if t == "cog.worked":  # what produced a cog's decision, review or drafted text (its model and competence record)
        what = {"decision": "decided at its turn", "review": "reviewed it", "draft": "wrote it"}.get(d.get("kind"), "worked on it")
        stand = " (stand-in)" if d.get("stand_in") else ""
        rel = f"release v{d['release_n']}" if d.get("release_n") else (f"release {esc(str(d.get('release'))[:12])}" if d.get("release") else "no release")
        return (f"{by} {what}" + (f": {esc(d.get('detail'))}" if d.get("detail") else "")
                + f" &middot; model {esc(d.get('model'))}{stand}, persona {esc(d.get('persona') or '-')} {esc(d.get('persona_version') or '')}, "
                  f"trained by {rel}")
    return f"{by}: {esc(t)}"


def track_sentence(ev: dict, link_number=None) -> str:
    """One run event as a sentence (escaped HTML)."""
    t = ev.get("type", "")
    step = esc(step_words(ev.get("step")))
    by = esc(ev.get("by", ""))
    if t == "run.started":
        return f"{esc(ev.get('owner', ''))} started the run" + (f" on policy version {esc(str(ev.get('release', ''))[:12])}" if ev.get("release") else "")
    if t == "step.started":
        return f"step {step} started ({esc((ev.get('cog') or {}).get('id') if isinstance(ev.get('cog'), dict) else ev.get('cog', ''))})"
    if t == "step.result":
        gate = ev.get("gate")
        probs = ev.get("problems") or []
        words = {"pass": "passed", "fail": "stopped", "pass-with-problems": "passed with warnings", "gate-pending": "waiting for approval"}
        s = f"step {step}: {esc(words.get(gate, gate or ('ok' if ev.get('ok') else 'error')))}"
        if probs:
            s += " (" + esc("; ".join(f"{p.get('check', '')}: {p.get('detail', '')}" for p in probs if isinstance(p, dict))[:300]) + ")"
        if ev.get("error"):
            e = ev["error"]
            s += f": {esc(e.get('detail') if isinstance(e, dict) else e)}"
        return s
    if t == "gate.pending":
        return f"waiting for {esc(', '.join(ev.get('assignees') or []))} at {step}" + (f", due {esc(day(ev.get('due')))}" if ev.get("due") else "")
    if t == "gate.cog_review":
        return f"the {esc(ev.get('cog'))} cog reviewed it: {esc(ev.get('verdict'))}" + (f" ({esc('; '.join(ev.get('reasons') or []))})" if ev.get("reasons") else "")
    if t == "gate.voided":
        return (f"{esc(ev.get('was_decided_by') or 'the')}'s approval at {step} no longer matches: {by} changed "
                f"{esc(', '.join(ev.get('fields') or []))} after it, so it is approved again")
    if t == "gate.reviewing":
        return (f"the {esc(ev.get('cog'))} cog started its adversarial reviews: " + esc(", ".join(ev.get("lenses") or []))
                + "; it signs only when all of them pass")
    if t == "gate.lens":
        i, n = (ev.get("of") or [1, 1])[:2]
        s = f"the {esc(ev.get('cog'))} cog's {esc(ev.get('lens'))} review ({i} of {n}): " + ("<b>passed</b>" if ev.get("passes") else "<b>did not pass</b>")
        return s + (f" ({esc((ev.get('findings') or [''])[0][:240])})" if ev.get("findings") and not ev.get("passes") else "")
    if t == "gate.skipped":
        return f"route {esc(ev.get('candidate'))} skipped: {esc(ev.get('reason'))}"
    if t == "gate.escalated":
        return f"escalated to {esc(', '.join(ev.get('assignees') or []))}: due {esc(day(ev.get('due')))}"
    if t == "gate.overdue":
        return f"overdue (due {esc(day(ev.get('due')))}); {esc(', '.join(ev.get('informed') or []))} informed"
    if t == "run.failed":
        return f"stopped at {step}: {esc(ev.get('reason', ''))}"
    if t == "run.retried":
        return f"{by} retried " + (f"from {step}" if ev.get("step") else "the run") + (f": {esc(ev.get('note'))}" if ev.get("note") else "")
    if t == "run.cancelled":
        return f"{by} cancelled the run" + (f": {esc(ev.get('reason'))}" if ev.get("reason") else "")
    if t == "run.resubmitted":
        return f"{by} resubmitted it" + (f": {esc(ev.get('note'))}" if ev.get("note") else "") + (f" (changed {esc(', '.join(ev.get('fields') or []))})" if ev.get("fields") else "")
    if t == "run.completed":
        return "the run completed"
    if t == "run.rejected":
        return f"{by} rejected it at {step}"
    if t == "record.fields_set":
        return f"{by} set {esc(join_words(field_label(k).lower() for k in (ev.get('fields') or [])))}"
    if t == "record.decision" or t == "gate.decided":
        return f"{by} decided: {esc(ev.get('verdict'))} “{esc(ev.get('meaning', ''))}”"
    if t == "grant.issued":
        return f"grant issued by {esc(ev.get('decided_by', by))}"
    return esc(t.replace(".", " ").replace("_", " "))


def notification_sentence(m: dict, link_number=None) -> str:
    """A notification as one sentence with the item named by its number (escaped HTML)."""
    kind = m.get("kind", "")
    subj = humanize(m.get("subject", ""), link_number)
    full = str(m.get("body", "") or "")
    head = full
    if len(full) > 400:  # cut at a word, never mid-word; the rest is one click away
        cut = full.rfind(" ", 0, 400)
        head = full[: cut if cut > 200 else 400].rstrip(",;: ") + "…"
    body = humanize(head, link_number)
    rest = humanize(full, link_number) if head != full else ""
    lead = {"failure": "Stopped", "returned": "Returned to you", "rejection": "Rejected", "escalation": "Escalated",
            "auto-decided": "Decided by a cog", "approval": "Waiting for you", "request": "Asked of you", "review": "Review posted",
            "mention": "Mention", "agent-run": "A cog finished a task", "cog-task": "A cog could not do a task", "cog-done": "A cog finished",
            "handed-back": "Handed back to you", "proposal": "Policy proposal", "automation": "Change order", "task": "Task for you",
            "intervention": "A committee needs you", "withdrawn": "Withdrawn", "moved": "Moved"}.get(kind, kind.replace("-", " ").capitalize())
    if kind == "review" and str(m.get("subject", "")).startswith("Review "):
        lead = "Asked to review"
    out = f"<b>{lead}:</b> {subj}" + (f" <span class='muted'>{body}</span>" if body and body != subj else "")
    if rest:
        out += f"<details class='more'><summary>more</summary><span class='muted'>{rest}</span></details>"
    return out


def version_label(meta: dict, releases: list[dict]) -> str:
    """'policy version 3 (6 Nov 2026)' for a release; the digest stays behind a disclosure."""
    digests = [m["digest"] for m in releases]
    n = digests.index(meta["digest"]) + 1 if meta["digest"] in digests else len(digests) + 1
    return f"policy version {n} ({day(str(meta.get('effective_from', ''))[:10])})"


def prefill_text(v) -> str:
    """A stored value back into a form control."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if all(isinstance(x, str) for x in v) else json.dumps(v, default=str)
    if isinstance(v, dict):
        return json.dumps(v, sort_keys=True, default=str)
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def summary_of_record(rec: dict) -> str:
    f = rec.get("fields", rec)
    t = rec.get("type")
    number = lambda ref: str(ref or "").rsplit(":", 1)[-1]
    if t == "stock-item":  # a lot: how many of which part, its batch or serial, its status
        return " · ".join(b for b in (f"{qty(f.get('quantity'))} × {number(f.get('part'))}", f"batch {f['batch']}" if f.get("batch") else "",
                                      f"SN {f['serial']}" if f.get("serial") else "", str(f.get("status") or "")) if b)
    if t == "manufacturer-part":
        return f"{f.get('manufacturer') or ''} {f.get('mpn') or ''}".strip() or rec.get("number", "")
    if t == "supplier-part":
        return f"{f.get('sku') or number(f.get('part'))} from {f.get('supplier') or 'no supplier'}"
    if t in ("stock-adjustment", "stock-release") and f.get("lot"):
        return short(f"{number(f['lot'])}: {f.get('reason') or ''}".rstrip(": "), 120)
    for key in ("title", "statement", "memo", "description", "name"):
        if f.get(key):
            return short(f[key], 120)
    if f.get("supplier"):
        return f"{f.get('supplier')} {money(f.get('amount'), f.get('currency')) if f.get('amount') is not None else ''}".strip()
    return rec.get("number", "")


def owner_of(rec: dict) -> str:
    f = rec.get("fields", {})
    return str(f.get("owner") or f.get("author") or f.get("filed_by") or rec.get("created_by") or "")
