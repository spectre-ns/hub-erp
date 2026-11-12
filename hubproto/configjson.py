"""The configuration in force as one JSON document, and a JSON document proposed on a change order (issue #33).

``export(hub)`` gives everything in force: the rules of the policy release (roles, members, groups, committees,
folders, permissions, thresholds, routing, due dates, approval criteria, segregation of duties, duties, processes, cog
profiles, personas, validation classes; and, read only, the Roles and responsibilities rows and the scopes), the record
types, the form layouts, the link vocabulary and the retention classes. ``dumps`` writes it deterministically (sorted
keys, every list in a stable order, no timestamps), so two exports of one configuration are the same bytes. The format
is described in ``docs/design/config-json.md`` and, as a JSON Schema, in ``docs/design/config-json.schema.json``.

``propose(hub, doc, by=...)`` reads such a document as data (parsed, validated, guarded; nothing in it is run) and puts
its difference from what is in force on a change order through the paths that already exist, so it is the same change
order the same edit made on screen would give:

* rules, members, roles, groups, folders, cog profiles and personas: direct changes, as Settings records them
  (``changecontrol.rule_changes`` and ``add_direct_change``); the review round's cog writes the SOP prose back;
* record types: ``recordtypes.carry`` (non-destructive changes only, as ``record-types carry --from-file``);
* form layouts: the person's draft, carried (``formlayout.save_draft`` and ``carry``), as the forms editor does;
* links and retention: ``configdocs.carry``.

Guards, widenings, reviews and signatures are those of the change order. Anything that cannot be carried is refused
with a sentence, and then nothing is carried: every part is checked before any is put on the change order, and a
change order is put back as it was if carrying stops part-way. A section left out of the document is left as it is; a
section given says the whole of it (a rule, relation or class it leaves out is removed; a record type it leaves out is
refused: record types are archived, never dropped).
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
import unicodedata

from .rules import KINDS, canonical, rule_key, validate_rule

SCHEMA = "hub-erp/config/1"
# Policy sections: the rule kind each lists.
SECTIONS = {
    "roles": "role", "members": "member", "groups": "group", "committees": "committee", "folders": "folder",
    "permissions": "permission", "thresholds": "threshold", "routing": "routing", "due_dates": "sla",
    "approval_criteria": "approval_criteria", "segregation_of_duties": "sod", "duties": "duty", "processes": "process",
    "cogs": "cog", "personas": "persona", "validation_classes": "validation_class", "validation_needs": "validation_need",
    "responsibilities": "responsibility", "scopes": "scope",
}
SECTION_OF = {k: s for s, k in SECTIONS.items()}
# Read from the SOPs' structure, not set as data: a change to these is a revision of the SOP on a change order.
READ_ONLY = {
    "responsibilities": "the Roles and responsibilities rows are each SOP's table: revise the SOP on a change order to change them",
    "scopes": "the scopes are the scoping documents: revise the scope document on a change order to change them",
}
TOP = ("schema", "hub", "release", "versions", "policy", "record_types", "forms", "links", "retention")
# Fields a rule of a kind may carry besides those every one has (rules.KINDS) and those the rules in force carry.
OPTIONAL = {
    "approval_criteria": {"adversarial_reviews", "countersign", "currency", "max_amount", "also_currencies", "max_per_day", "max_amount_per_month"},
    "permission": {"confirmer"}, "cog": {"about", "persona", "prompt_from", "references"},
    "committee": {"chair", "group", "members", "purpose", "quorum", "agree"},
    "persona": {"tone", "stance", "lenses", "passes", "escalates", "behaviour"}, "sod": {"only", "rule"},
    "routing": set(), "group": set(), "member": set(),
}
MAX_TEXT = 20000
MAX_DEPTH = 24  # within one entry
MAX_PARSE_DEPTH = 64  # the whole document
MAX_BYTES = 16 * 1024 * 1024
MAX_NUMBER = 1e15


class Refused(ValueError):
    """A configuration document that cannot be carried: every reason, each a sentence. Nothing was carried."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


# ---------------------------------------------------------------- export
def _entry(rule: dict) -> dict:
    return {k: copy.deepcopy(v) for k, v in rule.items() if k not in ("source", "key", "kind")}


def _sorted_entries(entries) -> list[dict]:
    seen = {}
    for e in entries:
        seen.setdefault(canonical(e), e)
    return [seen[k] for k in sorted(seen)]


def policy_sections(rules: list[dict]) -> dict[str, list[dict]]:
    """The rules as the document's policy sections (each entry the rule without its kind and source), every section
    present, entries in a stable order, one entry for a rule stated in several SOPs."""
    out = {s: [] for s in SECTIONS}
    for r in rules:
        s = SECTION_OF.get(r.get("kind"))
        if s is not None:
            out[s].append(_entry(r))
    return {s: _sorted_entries(v) for s, v in out.items()}


def versions(hub) -> dict:
    """The versions in force of what a change order may change without a new policy release."""
    from . import configdocs, recordtypes

    forms = {t: int(e.get("version") or 0) for t, e in sorted(hub.forms.active().items()) if isinstance(e, dict)}
    return {"record_types": int(recordtypes.in_force(hub)["version"]), "links": int(configdocs.in_force(hub, "links")["version"]),
            "retention": int(configdocs.in_force(hub, "retention")["version"]), "forms": forms}


def export(hub) -> dict:
    """Everything in force, as one document (see the module docstring)."""
    from . import configdocs, formlayout, recordtypes

    meta = hub.releases.current()
    if not meta:
        raise ValueError("no policy release is in force yet: there is no configuration to export (release one first: hubproto bootstrap-release)")
    types = copy.deepcopy(recordtypes.in_force(hub)["types"])
    return {
        "schema": SCHEMA,
        "hub": {"id": hub_id(hub)},
        "release": {"digest": meta["digest"], "version": configdocs.release_number(hub, meta["digest"]), "effective_from": meta.get("effective_from")},
        "versions": versions(hub),
        "policy": policy_sections(hub.settings_rules()),
        "record_types": types,
        "forms": {t: _form(formlayout.active_layout(hub, t), t) for t in sorted(types) if isinstance(types[t], dict)},
        "links": configdocs.values(hub, "links"),
        "retention": configdocs.values(hub, "retention"),
    }


def _form(layout: dict, type_: str) -> dict:
    return {**{k: v for k, v in copy.deepcopy(layout).items() if k != "updated_at"}, "type": type_}


def dumps(doc: dict) -> str:
    """The document as text: sorted keys, two-space indent, a final newline; the same configuration, the same bytes."""
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def loads(text: str | bytes) -> dict:
    """Parse a document (data only). Raises Refused with a sentence when it is too big, too deeply nested, not JSON, has
    a key twice in one object or a number that is not finite (``1e400``, ``NaN``, ``Infinity``), or is not an object."""
    if isinstance(text, bytes):
        if len(text) > MAX_BYTES:
            raise Refused([f"the file is {len(text):,} bytes; a configuration document is at most {MAX_BYTES:,}"])
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            raise Refused(["the file is not UTF-8 text: a configuration document is JSON"]) from None
    if len(text) > MAX_BYTES:
        raise Refused([f"the file is {len(text):,} characters; a configuration document is at most {MAX_BYTES:,}"])
    deep = _nesting(text)
    if deep > MAX_PARSE_DEPTH:
        raise Refused([f"the file nests objects and lists {deep} deep; a configuration document nests at most {MAX_PARSE_DEPTH}"])
    try:
        doc = json.loads(text, parse_constant=_no_constant, parse_float=_finite_float, parse_int=_bounded_int,
                         object_pairs_hook=_no_duplicates)
    except _Unreadable as e:
        raise Refused([str(e)]) from None
    except RecursionError:
        raise Refused([f"the file nests objects and lists too deeply (at most {MAX_PARSE_DEPTH})"]) from None
    except ValueError as e:
        raise Refused([f"the file is not JSON ({e})"]) from None
    if not isinstance(doc, dict):
        raise Refused(["the file is not a configuration document: its top level is an object with a schema"])
    return doc


class _Unreadable(ValueError):
    """A value json would read, but the hub does not: a repeated key, a number that is not finite or too large."""


def _nesting(text: str) -> int:
    """How deep objects and lists nest in a JSON text (strings skipped), counted before parsing."""
    depth = deepest = 0
    in_str = esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
            deepest = max(deepest, depth)
        elif ch in "]}":
            depth -= 1
    return deepest


def _no_constant(name: str):
    raise _Unreadable(f"{name} is not a number the hub reads: give a finite number")


def _finite_float(text: str) -> float:
    v = float(text)
    if not math.isfinite(v) or abs(v) > MAX_NUMBER:
        raise _Unreadable(f"{text} is not a number the hub reads: give a finite number of at most {MAX_NUMBER:,.0f}")
    return v


def _bounded_int(text: str) -> int:
    if len(text.lstrip("-")) > 16:
        raise _Unreadable(f"{text[:24]}{'...' if len(text) > 24 else ''} is not a number the hub reads: at most {MAX_NUMBER:,.0f}")
    v = int(text)
    if abs(v) > MAX_NUMBER:
        raise _Unreadable(f"{text} is not a number the hub reads: at most {MAX_NUMBER:,.0f}")
    return v


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    out = {}
    for k, v in pairs:
        if k in out:
            raise _Unreadable(f"the key {k!r} is given twice in one object: say it once (a reader sees the first, JSON keeps the last)")
        out[k] = v
    return out


# ---------------------------------------------------------------- reading a document: data, checked
def _plain(value, where: str, depth: int = 0) -> list[str]:
    """Problems with a value read from a document: only JSON data, finite numbers, strings of printable text, bounded
    nesting."""
    if depth > MAX_DEPTH:
        return [f"{where} is nested too deeply"]
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or abs(value) > MAX_NUMBER:
            return [f"{where} is not a number the hub reads: give a finite number of at most {MAX_NUMBER:,.0f}"]
        return []
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            return [f"{where} is longer than {MAX_TEXT:,} characters"]
        if any(ord(ch) < 32 and ch not in "\n\t" or 127 <= ord(ch) < 160 for ch in value):
            return [f"{where} holds control characters"]
        return []
    if isinstance(value, list):
        out = []
        for i, v in enumerate(value):
            out += _plain(v, f"{where}[{i}]", depth + 1)
        return out
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            out += _plain(k, f"{where} key", depth + 1) + _plain(v, f"{where}.{k}", depth + 1)
        return out
    return [f"{where} is not JSON data"]


# ---------------------------------------------------------------- names: the shape Settings gives them, never a look-alike
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")  # what Settings' _slug produces: lowercase letters, digits, hyphens
FIELD = re.compile(r"^[a-z][a-z0-9_]{0,40}$")  # a record field, as recordtypes.FIELD_NAME
BIDI = set("؜‎‏‪‫‬‭‮⁦⁧⁨⁩")
# The fields of each kind that name someone or something (a member, role, group, committee, cog, persona, limit, ...);
# "x:" is a reference "<kind>:<name>" whose name part is held to the shape; "[]" a list of names.
NAMES = {
    "role": ("name",), "member": ("principal", "roles[]"), "group": ("name", "members[]", "roles[]"),
    "committee": ("name", "group", "chair", "members[]"), "cog": ("name", "persona"), "persona": ("name",),
    "threshold": ("name",), "sod": ("roles[]",), "duty": ("cog",), "permission": ("subject:", "confirmer"),
    "approval_criteria": ("approver:", "countersign"), "routing": ("assignee:",),
}
FOLDER_FIELDS = {"folder": "path", "permission": "resource"}


def _invisible(text: str) -> str:
    """The first invisible character in ``text`` (zero-width, a direction control, any format character), in words."""
    for ch in text:
        if ch in BIDI or unicodedata.category(ch) == "Cf":
            kind = "a direction control" if ch in BIDI else "an invisible character"
            return f"{kind} (U+{ord(ch):04X} {unicodedata.name(ch, 'FORMAT CHARACTER')})"
    return ""


def _scripts(text: str) -> set[str]:
    return {unicodedata.name(ch, "UNKNOWN").split(" ")[0] for ch in text if ch.isalpha()}


def name_problem(value, where: str) -> str:
    """Why a name is not one Settings would write ('' when it is): lowercase letters, digits and hyphens, nothing that
    only looks like them."""
    if not isinstance(value, str):
        return f"{where} is a name (text), not {type(value).__name__}"
    if SLUG.match(value):
        return ""
    hidden = _invisible(value)
    if hidden:
        return f"{where} '{value.encode('ascii', 'backslashreplace').decode()}' holds {hidden}: names are written plainly"
    odd = next((ch for ch in value if ord(ch) > 127), None)
    if odd is not None:
        return (f"{where} '{value.encode('ascii', 'backslashreplace').decode()}' is written with U+{ord(odd):04X} "
                f"{unicodedata.name(odd, 'a character')}, which only looks like a letter of a name: names are lowercase "
                "letters a to z, digits and hyphens")
    return f"{where} {value!r} is not a name: lowercase letters, digits and hyphens, as Settings writes it"


def folder_problem(value, where: str) -> str:
    """Why a folder path is not a plain one ('' when it is): segments of printable text, no invisible characters, in
    their normal form (NFKC), each in one script."""
    if not isinstance(value, str) or not value.strip():
        return f"{where} is a folder path such as Quality/Nonconformances"
    hidden = _invisible(value)
    if hidden:
        return f"{where} '{value.encode('ascii', 'backslashreplace').decode()}' holds {hidden}: folder names are written plainly"
    if unicodedata.normalize("NFKC", value) != value:
        return f"{where} {value!r} is not in its normal form (NFKC: {unicodedata.normalize('NFKC', value)!r}): write it that way"
    for seg in value.split("/"):
        if not seg or seg != seg.strip():
            return f"{where} {value!r}: each folder name in the path is given, with no spaces around it"
        if len(_scripts(seg)) > 1:
            return f"{where} {value!r} mixes letters of {' and '.join(sorted(_scripts(seg)))} in {seg!r}: a folder name is in one script"
    return ""


def _names_problems(rule: dict, where: str) -> list[str]:
    """Names and folder paths of a rule the document adds or changes, held to the shape Settings gives them; and no
    invisible characters in any text of it."""
    out = []
    for f in NAMES.get(rule["kind"], ()):
        key, many, ref = f.rstrip("[]:"), f.endswith("[]"), f.endswith(":")
        v = rule.get(key)
        if v in (None, ""):
            continue
        for i, one in enumerate(v if many and isinstance(v, list) else [v]):
            at = f"{where}.{key}" + (f"[{i}]" if many else "")
            prefix = ""
            if ref and isinstance(one, str) and ":" in one:
                prefix, _, one = one.partition(":")
            if prefix == "field" and isinstance(one, str) and FIELD.match(one):
                continue  # a record's field (the requester, the owner): a field name, as the record types write them
            why = name_problem(one, at)
            if why:
                out.append(why)
    fk = FOLDER_FIELDS.get(rule["kind"])
    if fk and rule.get(fk) not in (None, ""):
        v = rule[fk]
        why = folder_problem(v.partition(":")[2] if fk == "resource" and isinstance(v, str) else v, f"{where}.{fk}")
        if why:
            out.append(why)
    out += _text_problems(rule, where)
    return out


def _text_problems(value, where: str) -> list[str]:
    """Invisible characters (zero-width, direction controls) anywhere in a value the document adds or changes."""
    if isinstance(value, str):
        hidden = _invisible(value)
        return [f"{where} holds {hidden}: write it plainly"] if hidden else []
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in _text_problems(v, f"{where}[{i}]")]
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _text_problems(k, f"{where} key") + _text_problems(v, f"{where}.{k}")]
    return []


def _allowed(kind: str, base: list[dict]) -> set[str] | None:
    if kind == "process":
        return None  # a process is a whole workflow (steps or a graph): its schema is checked by validate_rule
    seen = set().union(*[set(r) for r in base if r.get("kind") == kind]) if base else set()
    return (set(KINDS.get(kind, ())) | seen | OPTIONAL.get(kind, set())) - {"source", "key", "kind"}


def _read_policy(policy, base: list[dict], by: str, processes: dict) -> tuple[list[dict] | None, list[str]]:
    """The rule set a document's policy sections give (the rules in force where it lists them as they are, its entries
    for the rest), or None when it gives no section; and the problems."""
    from .settings import stamp

    if policy is None:
        return None, []
    if not isinstance(policy, dict):
        return None, ["policy is an object of sections (permissions, thresholds, ...)"]
    problems = []
    unknown = sorted(set(policy) - set(SECTIONS))
    if unknown:
        problems.append(f"policy has no section {', '.join(unknown)} (sections: {', '.join(SECTIONS)})")
    given: dict[str, list[dict]] = {}
    for section, entries in policy.items():
        if section not in SECTIONS:
            continue
        kind = SECTIONS[section]
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            problems.append(f"policy.{section} is a list of objects")
            continue
        allowed = _allowed(kind, base)
        rules = []
        for i, e in enumerate(entries):
            where = f"policy.{section}[{i}]"
            if e.get("kind") not in (None, kind):
                problems.append(f"{where} says it is a {e.get('kind')}, in the {section} section (a {kind})")
                continue
            p = _plain(e, where)
            if p:
                problems += p
                continue
            extra = sorted(set(e) - {"kind", "source", "key"} - allowed) if allowed is not None else []
            if extra:
                problems.append(f"{where}: a {kind} has no field {', '.join(extra)} (it has {', '.join(sorted(allowed))})")
                continue
            rules.append({**{k: v for k, v in e.items() if k not in ("source", "key")}, "kind": kind})
        given[kind] = rules
    for section, why in READ_ONLY.items():
        kind = SECTIONS[section]
        if kind in given and {rule_key(r) for r in given[kind]} != {rule_key(r) for r in base if r.get("kind") == kind}:
            problems.append(f"policy.{section} differs from what is in force: {why}")
    if problems:
        return None, problems
    keys = {rule_key(r) for rs in given.values() for r in rs}
    base_keys = {rule_key(r) for r in base}
    new_rules = [r for r in base if r.get("kind") not in given or rule_key(r) in keys]
    seen = set()
    for kind, rules in given.items():
        section = SECTION_OF[kind]
        for r in rules:
            k = rule_key(r)
            if k in base_keys or k in seen:
                continue
            seen.add(k)
            names = _names_problems(r, f"policy.{section}")  # what it adds or changes: names as Settings writes them
            if names:
                problems += names
                continue
            why = validate_rule(stamp(r, by, processes))
            if why:
                problems.append(f"policy.{section}: " + "; ".join(why) + f" (in {canonical(_entry(r))[:160]})")
                continue
            new_rules.append(r)
    return (None if problems else new_rules), problems


def hub_id(hub) -> str:
    """This hub's identity in an export: a one-way HMAC of its signing secret (state/.secret), so it never reveals the
    secret. A backup restored, or the state moved to another machine, keeps the secret and so the identity; a new hub
    built from the same Frames has another."""
    secret = hub.runner.secret()
    key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return hmac.new(key, b"hub-erp/config-export/hub-id", hashlib.sha256).hexdigest()[:32]


VERSION_WORDS = {"record_types": "the record types", "links": "the link vocabulary", "retention": "the retention classes"}


def _check_versions(hub, doc: dict) -> list[str]:
    """Why the document is not one taken from what is in force on this hub (empty when it is): the hub it came from, the
    release, and the version of each part a change order changes without a new release. Every one is required, so a
    document (or a script's output) that leaves one out cannot undo what was released since."""
    meta = hub.releases.current() or {}
    again = "export this hub's configuration in force and make the change there, so nothing released since is undone"
    out = []
    who = doc.get("hub")
    if not isinstance(who, dict) or not isinstance(who.get("id"), str):
        out.append(f"say which hub the document was exported from (hub.id): {again}")
    elif not hmac.compare_digest(who["id"], hub_id(hub)):
        out.append(f"the document was exported from another hub (hub.id {who['id'][:12]}, this one is {hub_id(hub)[:12]}): {again}")
    rel = doc.get("release")
    if not isinstance(rel, dict) or not isinstance(rel.get("digest"), str):
        out.append(f"say which release the document was exported from (release.digest): {again}")
    elif rel["digest"] != meta.get("digest"):
        from .configdocs import release_number

        n = release_number(hub, rel["digest"])
        then = f"policy version {n}" if n else f"release {rel['digest'][:12]}"
        out.append(f"the document was exported from {then}, and policy version {release_number(hub, meta.get('digest'))} is in force now: {again}")
    ver = doc.get("versions")
    if not isinstance(ver, dict):
        return out + [f"say which versions the document was exported from (versions: record_types, links, retention, forms): {again}"]
    now = versions(hub)
    for k, w in VERSION_WORDS.items():
        if k not in ver:
            out.append(f"versions.{k} is missing: say which version of {w} the document was exported from ({again})")
        elif ver[k] != now[k]:
            out.append(f"{w} changed since the document was exported (version {ver[k]} then, {now[k]} now): {again}")
    forms = ver.get("forms")
    if not isinstance(forms, dict):
        out.append(f"versions.forms is missing: say which form layouts were in force when the document was exported ({again})")
    else:
        for t in sorted(set(forms) | set(now["forms"])):
            if not isinstance(forms.get(t, 0), int) or int(forms.get(t) or 0) != int(now["forms"].get(t) or 0):
                out.append(f"the {t.replace('-', ' ')} form changed since the document was exported: {again}")
    return out


def plan(hub, doc: dict, *, by: str) -> dict:
    """What a document would put on a change order, checked: {"rules": [direct changes], "record_types": [changes],
    "forms": {type: layout}, "config": [changes], "summary": [sentences]}. Raises Refused with every reason when any part
    cannot be carried."""
    from . import configdocs, formlayout, recordtypes

    if not isinstance(doc, dict):
        raise Refused(["the file is not a configuration document: its top level is an object with a schema"])
    if doc.get("schema") != SCHEMA:
        raise Refused([f"the file is not a hub configuration document (schema {SCHEMA}): it says schema {doc.get('schema')!r}"])
    problems = []
    unknown = sorted(set(doc) - set(TOP))
    if unknown:
        problems.append(f"the document has no part {', '.join(unknown)} (parts: {', '.join(TOP)})")
    stale = _check_versions(hub, doc)
    if stale:  # compared with what is in force now, an older document would undo what was released since
        raise Refused(problems + stale)
    from . import changecontrol

    out = {"rules": [], "record_types": [], "forms": {}, "config": [], "summary": []}
    # Rules, members, roles, groups, folders, cog profiles and personas: as Settings records them.
    base = hub.settings_rules()
    processes = (hub.current_bundle() or {}).get("processes") or {}
    new_rules, p = _read_policy(doc.get("policy"), base, by, processes)
    problems += p
    if new_rules is not None:
        out["rules"] = changecontrol.rule_changes(base, new_rules)
    # Links and retention: before the record types, which may be kept under a class the document adds.
    cfg_docs = {d: configdocs.values(hub, d) for d in configdocs.DOCS}
    for d in configdocs.DOCS:
        if d not in doc:
            continue
        val = doc[d]
        if not isinstance(val, dict):
            problems.append(f"{d} is an object ({'relation -> its inverse' if d == 'links' else 'class -> {years, evidence}'})")
            continue
        pp = _plain(val, d)
        if pp:
            problems += pp
            continue
        out["config"] += configdocs.changes_between(d, cfg_docs[d], val)
    rt_in_force = recordtypes.in_force(hub)["types"]
    if out["config"]:
        after, errors, _ = configdocs.check(hub, [{**c, "id": f"{c['doc']}.{c['key']}"} for c in out["config"]], cfg_docs,
                                            types=_planned_types(hub, doc, rt_in_force))
        problems += errors
        cfg_docs = after
    # Record types: non-destructive changes, checked as a change order's are.
    if "record_types" in doc:
        new = doc["record_types"]
        if not isinstance(new, dict) or not all(isinstance(v, dict) for v in new.values()):
            problems.append("record_types is an object of record type -> definition")
        elif _plain(new, "record_types"):
            problems += _plain(new, "record_types")
        else:
            problems += [x for t in sorted(new) if new[t] != rt_in_force.get(t) for x in _text_problems(new[t], f"record_types.{t}")]
            changes, skipped = recordtypes.changes_between(rt_in_force, new, where="the document")
            problems += skipped
            if changes:
                _, errors, _ = recordtypes.check(hub, [{**c, "id": f"{c['op']} {c['type']}"} for c in changes], rt_in_force,
                                                 retention=cfg_docs["retention"])
                problems += [f"record types: {e}" for e in errors]
            out["record_types"] = changes
    # Form layouts: a layout that differs from the one in force is carried as the forms editor carries a draft.
    if "forms" in doc:
        forms = doc["forms"]
        if not isinstance(forms, dict):
            problems.append("forms is an object of record type -> layout")
        else:
            for t in sorted(forms):
                lay = forms[t]
                where = f"forms.{t}"
                if t not in rt_in_force:
                    problems.append(f"{where}: there is no record type {t} in force (a form for a new record type is carried once the type is released)")
                    continue
                if not isinstance(lay, dict):
                    problems.append(f"{where} is a layout object (sections, start, custom_fields)")
                    continue
                if _plain(lay, where):
                    problems += _plain(lay, where)
                    continue
                if lay.get("type") not in (None, t):
                    problems.append(f"{where} says it is the layout of {lay.get('type')}")
                    continue
                lay = _form(lay, t)
                if canonical(lay) == canonical(_form(formlayout.active_layout(hub, t), t)):
                    continue
                hidden = _text_problems(lay, where)
                if hidden:
                    problems += hidden
                    continue
                errors, _ = formlayout.validate(hub, t, lay)
                if errors:
                    problems += [f"{where}: {e}" for e in errors]
                    continue
                out["forms"][t] = lay
    if problems:
        raise Refused(problems)
    out["summary"] = _summary(hub, out)
    return out


def _planned_types(hub, doc: dict, rt_in_force: dict) -> dict:
    """The record types to protect a retention class for: those in force, with what the document says of them."""
    new = doc.get("record_types")
    if not isinstance(new, dict):
        return rt_in_force
    out = copy.deepcopy(rt_in_force)
    for t, td in new.items():
        if isinstance(td, dict):
            out[t] = td
    return out


def _summary(hub, out: dict) -> list[str]:
    from . import changecontrol, configdocs, recordtypes

    lines = [changecontrol._item_reason(c) for c in out["rules"]]
    lines += [recordtypes.describe(c) for c in out["record_types"]]
    lines += [f"the {t.replace('-', ' ')} form layout" for t in sorted(out["forms"])]
    lines += [configdocs.describe(c) for c in out["config"]]
    return lines


def nothing(p: dict) -> bool:
    return not (p["rules"] or p["record_types"] or p["forms"] or p["config"])


# ---------------------------------------------------------------- proposing
def _person(hub, who: str) -> bool:
    meta = hub.releases.current()
    b = hub.releases.bundle(meta["digest"]) if meta else {}
    return ((b.get("members") or {}).get(who) or {}).get("type") == "person"


CO_FIELDS = ("direct_changes", "direct_base", "rebase_questions", "record_type_changes", "form_changes", "config_changes", "delta_seal", "contributor")


def propose(hub, doc: dict, *, by: str, co_ref=None, reason: str = "", name: str = "") -> dict:
    """Put what a document changes on a change order: ``co_ref``, or the person's open settings change order (one is
    started when they have none), as Settings does. Returns {"change_order", "locator", "changed", "summary", "carried"}.
    Raises Refused (nothing carried) when any part cannot be carried; with nothing to change, carries nothing and says
    so (``changed`` False)."""
    from . import changecontrol, configdocs, eco, formlayout, recordtypes

    if not _person(hub, by):
        raise Refused([f"{by} is not a person of the organization: a person proposes a configuration change"])
    co = None
    if co_ref is not None:
        try:
            co = changecontrol.sync(hub, co_ref)
        except (KeyError, ValueError) as e:
            raise Refused([str(e)]) from None
        why = changecontrol.may_edit(hub, co, by)
        st = changecontrol.stage_of(co)
        if why:
            raise Refused([why])
        if eco.finished(co) or st not in changecontrol.EDITABLE or co["state"].startswith("awaiting"):
            raise Refused([f"{co['number']} is {st.replace('-', ' ')}: its changes are fixed now; propose on another change order"])
    try:
        p = plan(hub, doc, by=by)
    except Refused:
        raise
    except (TypeError, KeyError, AttributeError, IndexError, OverflowError, RecursionError, ValueError) as e:
        # A shape the checks above did not foresee: a sentence, never a traceback, and nothing carried.
        raise Refused([f"the document cannot be read as a configuration ({type(e).__name__}: {str(e)[:200]}): export again and edit that"]) from None
    if nothing(p):
        return {"change_order": co["number"] if co else None, "locator": co["locator"] if co else None, "changed": False,
                "summary": ["the document says what is in force: nothing to propose"], "carried": {}}
    why = reason or (f"Configuration changes from {name}." if name else "Configuration changes from a JSON document.")
    created = co is None and changecontrol.settings_change_order(hub, by, create=False) is None
    co = co or changecontrol.settings_change_order(hub, by, why)
    before = {k: copy.deepcopy(co["fields"].get(k)) for k in CO_FIELDS}
    drafts_before = copy.deepcopy(hub.forms.drafts(by))
    carried = {"rules": [], "record_types": [], "forms": [], "config": []}
    try:
        if p["rules"]:
            known = {it["id"] for it in changecontrol._items(co)}
            items = changecontrol.add_direct_change(hub, co["locator"], p["rules"], by=by)
            carried["rules"] = [it["id"] for it in items if it["id"] not in known]
        if p["config"]:
            carried["config"] = [c["id"] for c in configdocs.carry(hub, co["locator"], p["config"], by=by, reason=why)]
        if p["record_types"]:
            carried["record_types"] = [c["id"] for c in recordtypes.carry(hub, co["locator"], p["record_types"], by=by, reason=why)]
        for t, lay in sorted(p["forms"].items()):
            formlayout.save_draft(hub, by, t, lay)
            formlayout.carry(hub, co["locator"], by, t)
            carried["forms"].append(t)
    except (ValueError, KeyError) as e:
        hub.kernel.set_fields(co["locator"], before, by=by)  # put the change order back as it was: nothing carried
        _restore_drafts(hub, by, drafts_before, p["forms"])
        raise Refused([f"nothing was carried: {e}" + (f" ({co['number']} was started for it and carries nothing)" if created else "")]) from None
    return {"change_order": co["number"], "locator": co["locator"], "changed": True, "summary": p["summary"], "carried": carried,
            "created": created}


def _restore_drafts(hub, user: str, before: dict, types) -> None:
    for t in types:
        if t in before:
            hub.forms.save_draft(user, t, {k: v for k, v in before[t].items() if k != "updated_at"})
        else:
            hub.forms.discard_draft(user, t)
