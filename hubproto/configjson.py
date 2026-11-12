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
import json

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
TOP = ("schema", "release", "versions", "policy", "record_types", "forms", "links", "retention")
# Fields a rule of a kind may carry besides those every one has (rules.KINDS) and those the rules in force carry.
OPTIONAL = {
    "approval_criteria": {"adversarial_reviews", "countersign", "currency", "max_amount", "also_currencies", "max_per_day", "max_amount_per_month"},
    "permission": {"confirmer"}, "cog": {"about", "persona", "prompt_from", "references"},
    "committee": {"chair", "group", "members", "purpose", "quorum", "agree"},
    "persona": {"tone", "stance", "lenses", "passes", "escalates", "behaviour"}, "sod": {"only", "rule"},
    "routing": set(), "group": set(), "member": set(),
}
MAX_TEXT = 20000
MAX_DEPTH = 24


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
    """Parse a document (data only). Raises Refused with a sentence when it is not JSON or not an object."""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            raise Refused(["the file is not UTF-8 text: a configuration document is JSON"]) from None
    try:
        doc = json.loads(text, parse_constant=_no_constant)
    except ValueError as e:
        raise Refused([f"the file is not JSON ({e})"]) from None
    if not isinstance(doc, dict):
        raise Refused(["the file is not a configuration document: its top level is an object with a schema"])
    return doc


def _no_constant(name: str):
    raise ValueError(f"{name} is not a number JSON allows")


# ---------------------------------------------------------------- reading a document: data, checked
def _plain(value, where: str, depth: int = 0) -> list[str]:
    """Problems with a value read from a document: only JSON data, strings of printable text, bounded nesting."""
    if depth > MAX_DEPTH:
        return [f"{where} is nested too deeply"]
    if value is None or isinstance(value, (bool, int, float)):
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
            why = validate_rule(stamp(r, by, processes))
            if why:
                problems.append(f"policy.{section}: " + "; ".join(why) + f" (in {canonical(_entry(r))[:160]})")
                continue
            new_rules.append(r)
    return (None if problems else new_rules), problems


def _check_versions(hub, doc: dict) -> list[str]:
    meta = hub.releases.current() or {}
    rel = doc.get("release")
    if not isinstance(rel, dict) or not isinstance(rel.get("digest"), str):
        return ["say which release the document was exported from (release.digest): export the configuration in force and edit that"]
    out = []
    if rel["digest"] != meta.get("digest"):
        from .configdocs import release_number

        n = release_number(hub, rel["digest"])
        then = f"policy version {n}" if n else f"release {rel['digest'][:12]}"
        out.append(f"the document was exported from {then}, and policy version {release_number(hub, meta.get('digest'))} is in force now: "
                   "export again and make the change there, so nothing released since is undone")
    ver = doc.get("versions")
    if ver is not None:
        if not isinstance(ver, dict):
            return out + ["versions is an object (record_types, links, retention, forms)"]
        now = versions(hub)
        words = {"record_types": "the record types", "links": "the link vocabulary", "retention": "the retention classes"}
        for k, w in words.items():
            if k in ver and ver[k] != now[k]:
                out.append(f"{w} changed since the document was exported (version {ver[k]} then, {now[k]} now): export again")
        forms = ver.get("forms")
        if isinstance(forms, dict):
            for t in sorted(set(forms) | set(now["forms"])):
                if int(forms.get(t) or 0) != int(now["forms"].get(t) or 0):
                    out.append(f"the {t.replace('-', ' ')} form changed since the document was exported: export again")
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
    p = plan(hub, doc, by=by)
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
