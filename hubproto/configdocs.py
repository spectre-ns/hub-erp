"""The link vocabulary and the retention classes under change control (issue #33), as the record types are.

``config/links.json`` (relation -> the label its inverse shows on the other record) and ``config/retention.json``
(class -> how many years a record is kept and the evidence its Track must hold) are where these came from, as the
``.frame.md`` files were for the SOPs. Once the SOPs are documents, what is in force is kept by the hub in
``state/config/<doc>/`` (``doc`` is ``links`` or ``retention``):

    the file as it was when the hub first kept it (version 1: at the conversion, or the first start of a hub converted
    before this module existed), plus every change a released change order carried, in order (version 2, 3, ...).

From then on the file is read only to see whether it still says the same: a file that differs is reported (``hubproto
status --check``) and not used. Before conversion nothing changes: the files are used, as they always were.

A change order carries these changes in its ``config_changes`` field, a list of items:

    {"id": "cfg-1", "doc": "links", "op": "set", "key": "inspires", "value": "inspired-by",
     "summary": "link vocabulary: new relation inspires (inverse inspired-by)", "by": "carol", "at": "<iso>",
     "reason": "...", "base_version": 1, "origin": {"kind": "direct", "by": "carol", "at": "<iso>"}}

Ops: ``set`` (a new entry, or a new value for one) and ``remove``. Refused, with why: a relation a record link uses, or
one the hub's own code links with (the shipped file lists it), is never removed; a retention class a record type in
force (or on the change order) uses, the default class ``standard``, or a class records are labelled with, is never
removed. A retention class kept for fewer years than before is a warning, shown with the change on the change order.

The change-order core calls, as it calls ``hubproto.recordtypes``:

    carried_reasons(hub, co) -> [str]   why the changes on ``co`` cannot be released (empty: they can)
    activate(hub, co, digest) -> [str]  release step: bring them into force (idempotent; ``co`` approved), what it did
    delta_items(hub, co) -> [dict]      each change as a delta item (approved with the change order, not one by one)
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .release import utcnow
from .rules import sha256_of

DOCS = {"links": "links.json", "retention": "retention.json"}
WORDS = {"links": "link vocabulary", "retention": "retention classes"}
FIELD = "config_changes"
OPS = ("set", "remove")
NAME = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
EVIDENCE = ("release_digest", "manifest_digest", "decisions", "grants", "cog_versions", "inputs_digest", "guard_findings", "documents")
MAX_YEARS = 100


# ---------------------------------------------------------------- storage
class DocStore:
    """state/config/<doc>/: active.json (what is in force, with its version) and history.jsonl."""

    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def active_path(self) -> Path:
        return self.root / "active.json"

    def active(self) -> dict | None:
        if not self.active_path.exists():
            return None
        try:
            return json.loads(self.active_path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise ValueError(f"{self.active_path} does not parse ({e}): what is in force is unknown; restore it from a backup "
                             "(hubproto restore)") from None

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
            f.write(json.dumps({k: v for k, v in entry.items() if k != "values"}, sort_keys=True, default=str) + "\n")

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


def store(hub, doc: str) -> DocStore:
    stores = getattr(hub, "_config_doc_stores", None)
    if stores is None:
        stores = hub._config_doc_stores = {}
    if doc not in stores:
        stores[doc] = DocStore(Path(hub.root) / "state" / "config" / doc)
    return stores[doc]


# ---------------------------------------------------------------- the files, and what is in force
def file_path(hub, doc: str) -> Path:
    return Path(hub.kernel.config_dir) / DOCS[doc]


def file_values(hub, doc: str) -> dict:
    data = json.loads(file_path(hub, doc).read_text(encoding="utf-8"))
    data.pop("$comment", None)
    return data


def file_sha(hub, doc: str) -> str:
    try:
        return sha256_of(file_values(hub, doc))
    except (OSError, ValueError):
        return ""


def governed(hub, doc: str) -> bool:
    return store(hub, doc).active() is not None


def _converted(hub) -> bool:
    from .recordtypes import _converted as converted

    return converted(hub)


def _ensure_one(hub, doc: str, by: str) -> dict:
    s = store(hub, doc)
    entry = s.active()
    if entry is not None:
        return entry
    values = file_values(hub, doc)
    sha = sha256_of(values)
    meta = hub.releases.current() if getattr(hub, "releases", None) else None
    source = "the file"
    if meta:
        want = (meta.get("config") or {}).get(DOCS[doc])
        version = _release_label(hub, meta["digest"])
        source = (f"the file as of release {version}" if not want or want == sha else
                  f"the file as it was when the hub first kept it (it differs from what release {version} recorded)")
    entry = {"version": 1, "values": values, "digest": sha, "file_sha": sha, "source": source, "release": (meta or {}).get("digest"),
             "released_with": None, "by": by, "at": utcnow(), "changes": []}
    s.set_active(entry)
    return entry


def release_number(hub, digest: str | None) -> int | None:
    """The release's version number (v1 is the first to take effect), as the command line counts them."""
    ordered = [m for _, m in sorted(enumerate(hub.releases.list()), key=lambda im: (im[1]["effective_from"], im[1]["created_at"], im[0]))]
    digests = [m["digest"] for m in ordered]
    return digests.index(digest) + 1 if digest in digests else None


def _release_label(hub, digest: str) -> str:
    n = release_number(hub, digest)
    return f"v{n} ({digest[:12]})" if n else digest[:12]


def ensure(hub, by: str = "hub") -> dict[str, dict]:
    """What is in force, kept from now on (version 1 when nothing was kept yet: the file as it is)."""
    out = {doc: _ensure_one(hub, doc, by) for doc in DOCS}
    install(hub)
    return out


def in_force(hub, doc: str) -> dict:
    """{"version", "values", "digest", ...}: what is in force (the file, version 0, while it is still the source)."""
    entry = store(hub, doc).active()
    if entry is not None:
        return entry
    if _converted(hub):
        return ensure(hub)[doc]
    values = file_values(hub, doc)
    return {"version": 0, "values": values, "digest": sha256_of(values), "file_sha": sha256_of(values), "source": "the file",
            "released_with": None}


def values(hub, doc: str) -> dict:
    return copy.deepcopy(in_force(hub, doc)["values"])


def install(hub) -> None:
    """Make what is in force the kernel's link vocabulary and retention classes, when the hub keeps them. Before that the
    kernel uses the files, as it always did. Called when the hub starts and whenever another process changed them."""
    stamps = {}
    for doc, name in DOCS.items():
        s = store(hub, doc)
        entry = s.active()
        if entry is None and _converted(hub):
            entry = _ensure_one(hub, doc, "hub")
        stamps[doc] = s.stamp()
        if entry is None:
            continue
        if doc == "links":
            hub.kernel.link_vocab = copy.deepcopy(entry["values"])
        else:
            hub.kernel.retention = copy.deepcopy(entry["values"])
        hub.kernel.config_digests = {**hub.kernel.config_digests, name: entry["digest"]}
    hub._config_doc_stamps = stamps


def refresh(hub) -> None:
    """Install again when another process (or this one) changed what is in force: cheap, a stat per document."""
    now = {doc: store(hub, doc).stamp() for doc in DOCS}
    if now != getattr(hub, "_config_doc_stamps", None):
        hub._config_doc_stamps = now
        if any(v is not None for v in now.values()):
            install(hub)


def differences(doc: str, old: dict, new: dict) -> list[str]:
    """What ``new`` says that ``old`` does not, and the other way round, in words."""
    out = []
    for k in sorted(set(new) - set(old)):
        out.append(f"{k}: {_value_words(doc, new[k])}")
    for k in sorted(set(old) - set(new)):
        out.append(f"no {k}")
    for k in sorted(set(old) & set(new)):
        if old[k] != new[k]:
            out.append(f"{k}: {_value_words(doc, new[k])} (not {_value_words(doc, old[k])})")
    return out


def drift(hub, doc: str) -> dict | None:
    """How the file differs from what is in force, once the hub keeps it; None when the file still says what version 1
    was made from (changes released by change orders since are not drift). {"version", "file", "in_file", "in_force"}."""
    entry = store(hub, doc).active()
    if entry is None:
        if not _converted(hub):
            return None
        entry = ensure(hub)[doc]
    try:
        now = file_values(hub, doc)
    except (OSError, ValueError) as e:
        return {"version": entry["version"], "file": str(file_path(hub, doc)), "in_file": [f"the file cannot be read: {e}"], "in_force": []}
    if sha256_of(now) in (entry.get("file_sha"), entry.get("digest")):
        return None
    return {"version": entry["version"], "file": str(file_path(hub, doc)), "in_file": differences(doc, entry["values"], now),
            "in_force": differences(doc, now, entry["values"])}


# ---------------------------------------------------------------- checking and applying changes
def _value_words(doc: str, v) -> str:
    if doc == "links":
        return f"inverse {v}"
    if isinstance(v, dict):
        return f"{v.get('years')} year(s), evidence {', '.join(v.get('evidence') or []) or 'none'}"
    return json.dumps(v, sort_keys=True)


def changes_between(doc: str, old: dict, new: dict) -> list[dict]:
    """The changes that make ``old`` say what ``new`` says (sets first, then removals), each checked when carried."""
    out = [{"doc": doc, "op": "set", "key": k, "value": copy.deepcopy(new[k])} for k in sorted(new) if old.get(k) != new[k] or k not in old]
    out += [{"doc": doc, "op": "remove", "key": k} for k in sorted(set(old) - set(new))]
    return out


def _value_problem(doc: str, value) -> str:
    if doc == "links":
        if not isinstance(value, str) or not NAME.match(value):
            return "the inverse is a lowercase name (letters, digits and hyphens), such as concerned-by"
        return ""
    if not isinstance(value, dict) or set(value) - {"years", "evidence"} or not {"years", "evidence"} <= set(value):
        return 'a retention class is {"years": <whole years>, "evidence": [<what its Track must hold>]}, nothing else'
    y = value["years"]
    if isinstance(y, bool) or not isinstance(y, int) or not 0 <= y <= MAX_YEARS:
        return f"years is a whole number from 0 to {MAX_YEARS}"
    ev = value["evidence"]
    if not isinstance(ev, list) or not all(isinstance(e, str) for e in ev) or len(set(ev)) != len(ev):
        return "evidence is a list of names, each once"
    unknown = [e for e in ev if e not in EVIDENCE]
    if unknown:
        return f"the hub does not know the evidence {', '.join(unknown)} (it knows {', '.join(EVIDENCE)})"
    return ""


def _shipped(hub, doc: str) -> dict:
    """What the software ships (the repository's file): its relations are ones the hub's own code links with."""
    from . import kernel

    try:
        data = json.loads((Path(kernel.CONFIG_DIR) / DOCS[doc]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    data.pop("$comment", None)
    return data


def _events_with(hub, pattern: str, type_: str | None = None) -> int:
    sql = "SELECT COUNT(*) FROM events WHERE data LIKE ?" + (" AND type = ?" if type_ else "")
    args = (pattern,) + ((type_,) if type_ else ())
    return int(hub.kernel.db.execute(sql, args).fetchone()[0])


def _types_using(types: dict, cls: str) -> list[str]:
    return sorted(n for n, td in types.items() if isinstance(td, dict) and (td.get("retention") or "standard") == cls)


def check_one(hub, docs: dict[str, dict], c: dict, types: dict | None = None) -> tuple[list[str], list[str]]:
    """(errors, warnings) for one change against ``docs`` ({doc: values}) as they stand before it. ``types``: the record
    types to protect (in force, with those the change order carries)."""
    errors, warnings = [], []
    doc, op, key = c.get("doc"), c.get("op"), c.get("key")
    if doc not in DOCS:
        return [f"{doc!r} is not a configuration document the hub keeps (links or retention)"], []
    if op not in OPS:
        return [f"{op!r} is not a change the hub makes to the {WORDS[doc]} (set or remove)"], []
    if not isinstance(key, str) or not NAME.match(key):
        return [f"{key!r} is not a name: a lowercase name of letters, digits and hyphens"], []
    cur = docs[doc]
    if op == "set":
        why = _value_problem(doc, c.get("value"))
        if why:
            return [f"{key}: {why}"], []
        if cur.get(key) == c["value"]:
            errors.append(f"{key} already says so")
        elif doc == "retention" and key in cur and c["value"]["years"] < int(cur[key].get("years") or 0):
            who = _types_using(types if types is not None else {}, key)
            warnings.append(f"records of the {key} class are kept {cur[key]['years'] - c['value']['years']} year(s) less"
                            + (f" ({', '.join(who)})" if who else ""))
        return errors, warnings
    if key not in cur:
        return [f"the {WORDS[doc]} has no {key}"], []
    if doc == "links":
        n = _events_with(hub, f'%"rel": "{key}"%', "record.linked")
        if n:
            errors.append(f"{key} is never removed: {n} link(s) between records use it")
        elif key in _shipped(hub, doc):
            errors.append(f"{key} is never removed: the hub's own steps link records with it")
    else:
        who = _types_using(types if types is not None else {}, key)
        if key == "standard":
            errors.append("standard is never removed: it is the class a record type without one is kept under")
        elif who:
            errors.append(f"{key} is never removed while a record type is kept under it ({', '.join(who)})")
        elif _events_with(hub, f'%"retention": "{key}"%'):
            errors.append(f"{key} is never removed: records are labelled with it")
    return errors, warnings


def apply_one(docs: dict[str, dict], c: dict) -> dict[str, dict]:
    out = copy.deepcopy(docs)
    if c["op"] == "set":
        out[c["doc"]][c["key"]] = copy.deepcopy(c["value"])
    else:
        out[c["doc"]].pop(c["key"], None)
    return out


def _types_for(hub, co: dict | None) -> dict:
    """The record types a change must not leave without their retention class: in force, plus those ``co`` carries."""
    from . import recordtypes

    types = recordtypes.in_force(hub)["types"]
    if co is not None and recordtypes.changes_of(co):
        try:
            types, _, _ = recordtypes.check(hub, recordtypes.changes_of(co), types, retention=retention_with(hub, co))
        except (ValueError, KeyError, TypeError):  # malformed carried changes: their own check says so
            pass
    return types


def check(hub, changes: list[dict], base: dict[str, dict] | None = None, types: dict | None = None) -> tuple[dict, list[str], list[str]]:
    """Apply ``changes`` in order to ``base`` (what is in force): (the documents after, errors, warnings). Each error and
    warning starts with the change's id."""
    docs = copy.deepcopy(base) if base is not None else {doc: values(hub, doc) for doc in DOCS}
    if types is None:
        from . import recordtypes

        types = recordtypes.in_force(hub)["types"]
    errors, warnings = [], []
    for c in changes:
        e, w = check_one(hub, docs, c, types)
        errors += [f"{c.get('id', '?')}: {x}" for x in e]
        warnings += [f"{c.get('id', '?')}: {x}" for x in w]
        if not e:
            docs = apply_one(docs, c)
    return docs, errors, warnings


def describe(c: dict) -> str:
    doc, op, key = c.get("doc"), c.get("op"), c.get("key")
    what = WORDS.get(doc, str(doc))
    if op == "remove":
        return f"{what}: remove {key}"
    if doc == "links":
        return f"{what}: {key} (inverse {c.get('value')})"
    v = c.get("value") or {}
    return f"{what}: {key} kept {v.get('years')} year(s), evidence {', '.join(v.get('evidence') or []) or 'none'}"


# ---------------------------------------------------------------- change orders
def changes_of(co: dict) -> list[dict]:
    return list(co["fields"].get(FIELD) or [])


def retention_with(hub, co: dict | None) -> dict:
    """The retention classes in force with those ``co`` carries applied (a change order may add a class and a record
    type kept under it together)."""
    docs = {doc: values(hub, doc) for doc in DOCS}
    for c in changes_of(co) if co else []:
        e, _ = check_one(hub, docs, c, {})
        if not e:
            docs = apply_one(docs, c)
    return docs["retention"]


def _may_carry(hub, co: dict, by: str) -> str:
    from .recordtypes import _may_carry as may_carry

    return may_carry(hub, co, by)


def carry(hub, co_ref: str, changes: list[dict], *, by: str, reason: str = "", origin: str = "direct") -> list[dict]:
    """Put link-vocabulary and retention changes on a change order. Every change is checked, with the ones it already
    carries, against what is in force; a refused change is refused with why and nothing is carried. Returns the items
    added."""
    from . import eco

    co = eco._co(hub, co_ref)
    if eco.finished(co) or co["state"].startswith("awaiting"):
        raise ValueError(f"{co['number']} is {co['state'].replace('-', ' ')}: change what it carries while it is being prepared")
    why = _may_carry(hub, co, by)
    if why:
        raise ValueError(why)
    if not changes:
        raise ValueError("give at least one link-vocabulary or retention change")
    existing = changes_of(co)
    n = max([int(str(c.get("id", "cfg-0")).rsplit("-", 1)[-1] or 0) for c in existing if str(c.get("id", "")).startswith("cfg-")] or [0])
    at = utcnow()
    new = []
    for raw in changes:
        if not isinstance(raw, dict):
            raise ValueError("each change is a mapping with a doc, an op and a key")
        n += 1
        doc = raw.get("doc")
        item = {k: copy.deepcopy(raw[k]) for k in ("doc", "op", "key", "value") if k in raw}
        item.update({"id": f"cfg-{n}", "by": by, "at": at, "reason": str(raw.get("reason") or reason),
                     "base_version": in_force(hub, doc)["version"] if doc in DOCS else None, "origin": {"kind": origin, "by": by, "at": at}})
        item["summary"] = describe(item)
        new.append(item)
    _, errors, _ = check(hub, existing + new, types=_types_for(hub, co))
    mine = [e for e in errors if any(e.startswith(f"{c['id']}:") for c in new)]
    if mine:
        raise ValueError("refused, nothing carried: " + "; ".join(mine))
    hub.kernel.set_fields(co["locator"], {FIELD: existing + new}, by=by)
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
        raise ValueError(f"{co['number']} carries no link-vocabulary or retention change {change_id}")
    hub.kernel.set_fields(co["locator"], {FIELD: rest}, by=by)


def carried_reasons(hub, co: dict) -> list[str]:
    """Why the changes a change order carries cannot be released as they stand (empty when they can)."""
    _, changes = _pending(hub, co)  # what it already brought into force (a release that stopped part-way) is not stale
    if not changes:
        return []
    _, errors, _ = check(hub, changes, types=_types_for(hub, co))
    out = [f"links and retention: {e}" for e in errors]
    for c in changes:
        if c.get("doc") in DOCS and c.get("base_version") != in_force(hub, c["doc"])["version"]:
            out.append(f"links and retention: {c['id']}: the {WORDS[c['doc']]} changed since it was carried; carry it again")
    return out


def delta_items(hub, co: dict) -> list[dict]:
    """Each change on the change order as a delta item (shown, approved with the change order)."""
    changes = changes_of(co)
    if not changes:
        return []
    docs = {doc: values(hub, doc) for doc in DOCS}
    types = _types_for(hub, co)
    out = []
    for c in changes:
        errors, warnings = check_one(hub, docs, c, types)
        if not errors:
            docs = apply_one(docs, c)
        out.append({"id": f"config:{c['id']}", "op": c.get("op"), "kind": "links" if c.get("doc") == "links" else "retention",
                    "change": {k: c[k] for k in ("doc", "key", "value") if k in c}, "reason": c.get("summary") or describe(c),
                    "origin": c.get("origin") or {"kind": "direct", "by": c.get("by"), "at": c.get("at")},
                    "warnings": [{"code": c.get("doc"), "text": w, "ref": c["id"], "accepted": None} for w in warnings],
                    "errors": errors, "stale": c.get("doc") in DOCS and c.get("base_version") != in_force(hub, c["doc"])["version"],
                    "decisions": []})
    return out


def released_by(hub, doc: str, co: dict) -> dict | None:
    """The version of ``doc`` this change order brought into force, if it did (the one in force, or one in its history):
    a release that stopped part-way sees what it already did."""
    active = store(hub, doc).active() or {}
    if active.get("released_with") == co["number"]:
        return active
    done = [e for e in store(hub, doc).history() if e.get("released_with") == co["number"]]
    return done[-1] if done else None


def _pending(hub, co: dict) -> tuple[dict[str, dict], list[dict]]:
    """({doc: the version this change order already brought into force}, the changes of the documents it has not yet)."""
    changes = changes_of(co)
    done = {doc: e for doc in sorted({c.get("doc") for c in changes if c.get("doc") in DOCS}) if (e := released_by(hub, doc, co))}
    return done, [c for c in changes if c.get("doc") not in done]


def later_changes(hub, co: dict) -> list[str]:
    """The documents with changes this change order has not brought into force whose version in force is no longer the
    one they were carried against (another change order released one since), in words."""
    from .closure import now_in_force

    _, todo = _pending(hub, co)
    return [now_in_force(f"the {WORDS[doc]}", in_force(hub, doc)) for doc in sorted({c.get("doc") for c in todo if c.get("doc") in DOCS})
            if any(c.get("base_version") != in_force(hub, doc)["version"] for c in todo if c.get("doc") == doc)]


def activate(hub, co: dict, digest: str) -> list[str]:
    """The release step (before the record types, which may be kept under a class this change order adds): what the
    approved change order carries comes into force, as the next version of each document it changes. Safe to repeat,
    and to retry after a stop part-way: a document this change order already brought into force is left as it is, and
    only the changes of the others are checked and applied, against what is in force for them. A document counts as
    already in force only when it was made from the changes signed (``closure.check_in_force_as_signed``). Refuses a
    change order that is not approved, changes carried against a version no longer in force (another change order
    released one since: ``later_changes``), and changes that no longer check (nothing changes then)."""
    from . import closure

    changes = changes_of(co)
    if not changes:
        return []
    docs_changed = sorted({c.get("doc") for c in changes if c.get("doc") in DOCS})
    done, todo = _pending(hub, co)
    for doc, e in done.items():  # already in force with this change order: only when made from what is signed (issue #53)
        mine = [c.get("summary") or describe(c) for c in changes if c.get("doc") == doc]
        closure.check_in_force_as_signed(co, "config", co["fields"][FIELD], e, f"{WORDS[doc]} v{e['version']}",
                                         same_content=list(e.get("changes") or []) == mine)
    already = {doc: f"{WORDS[doc]} v{done[doc]['version']} (already in force with {co['number']})" for doc in done}
    if not todo:
        return [already[doc] for doc in docs_changed]
    closure.check_co_approved(co, "its link-vocabulary and retention changes")
    stale = later_changes(hub, co)
    if stale:  # another change order released a version since these were carried: never undo it
        raise ValueError(closure.later_change_refusal(co, stale))
    base = ensure(hub)
    after, errors, _ = check(hub, todo, {doc: copy.deepcopy(base[doc]["values"]) for doc in DOCS}, types=_types_for(hub, co))
    if errors:
        raise ValueError(f"{co['number']}'s link-vocabulary and retention changes cannot come into force: " + "; ".join(errors))
    out = []
    for doc in docs_changed:
        if doc in done:
            out.append(already[doc])
            continue
        mine = [c.get("summary") or describe(c) for c in todo if c.get("doc") == doc]
        entry = {"version": int(base[doc]["version"]) + 1, "values": after[doc], "digest": sha256_of(after[doc]),
                 "file_sha": base[doc].get("file_sha"), "source": f"released with {co['number']}", "release": digest,
                 "released_with": co["number"], "by": f"change-order:{co['number']}", "at": utcnow(), "changes": mine,
                 "signed_digest": closure.part_digest(co["fields"][FIELD])}
        store(hub, doc).set_active(entry)
        out.append(f"{WORDS[doc]} v{entry['version']}: " + "; ".join(mine))
    install(hub)
    return out


def release_problems(hub, co: dict) -> list[str]:
    """What would stop this part of the release, checked before anything comes into force (what this change order already
    brought into force is not checked again)."""
    _, todo = _pending(hub, co)
    if not todo:
        return []
    _, errors, _ = check(hub, todo, types=_types_for(hub, co))
    return [f"links and retention: {e}" for e in errors]
