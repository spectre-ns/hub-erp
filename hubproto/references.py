"""Document references in SOP prose: read at compile, checked by a guard, linked at release, flagged when retired.

An SOP that says "see DOC-0040", "DOC-0040 rev B" or links to a document's page ("/document/DOC-0040") cites that
document. The compile reads every such citation with the Frame and line it is on (``read_references``) and records them
in the proposal and the release; ``reference_guard`` reports a citation of an unknown document, or of one not in force
(obsolete, superseded, withdrawn, not yet effective), as a warning with its frame and line. When a release puts an SOP
in force, ``sync_links`` makes its citations ``references`` links from its document to the cited ones (``referenced-by``
on theirs) and takes off the links of citations it no longer makes. When a change order's release makes a document
obsolete or supersedes the revision cited, ``flag_citations`` flags each SOP still citing it to its owner.

What a citation names. Each revision of an SOP is its own document record (DOC-0040 revision A, DOC-0055 revision B
that supersedes it), so a bare number could mean either "this record" or "this document, in its current revision". A
bare number, or a site link, names the **lineage**: the document in its revision in force (an SOP's lineage, or the
chain of records that supersede it), which is what "see DOC-0040" means in a procedure. A new revision therefore
leaves the citation sound, and it is linked to the revision in force. A citation that names a revision ("DOC-0040 rev
A", "DOC-0040, revision A") names that **revision**: it is flagged once another revision supersedes it.

A citation is not a rule. It configures nothing (no permission, route or step changes with it), so it is no item of
the delta and needs no decision of its own: adding or removing one is a change to the SOP's wording, which the policy
owners (or the change order's approval) already review.
"""

from __future__ import annotations

import re

from .guards import Finding

GUARD = "document-reference"
LINK = "references"
CITES = "cites"  # field on a citing document: the targets its citations are linked to (only those links are managed)
FLAGS = "citation_flags"  # field on a citing document: citations a release made stale, for its owner

_REV = r"(?:,?\s*(?P<paren>\()?(?i:rev(?:ision)?\.?)\s*(?P<rev>[A-Z]{1,2}|\d+)\b(?(paren)\)))?"


def _patterns(prefix: str) -> tuple[re.Pattern, re.Pattern]:
    """(site link, number): a link to a document's page (absolute or relative, by number or locator), and a number
    with an optional revision ("DOC-0040", "DOC-0040 rev B", "DOC-0040, revision B", "DOC-0040 (rev. B)")."""
    num = rf"{re.escape(prefix)}-\d+"
    colon = r"(?::|%3[Aa])"
    link = re.compile(rf"(?:https?://[^\s<>()\[\]\"']+?)?/(?:document|doc|record)/(?:rec{colon}document{colon})?(?P<num>{num})\b"
                      r"(?P<rev>)")
    bare = re.compile(rf"(?<![\w-])(?P<num>{num})\b" + _REV)
    return link, bare


def document_prefix(record_types: dict | None) -> str:
    return str(((record_types or {}).get("document") or {}).get("prefix") or "DOC")


def read_references(frame, prefix: str = "DOC") -> list[dict]:
    """The citations of documents in a Frame's body: [{"frame", "line", "document", "revision", "form", "text"}], in
    line order, one per document (and revision) a line cites. ``form`` is "number" or "link". Cog sections are left out
    (their "Prompt from:" and "References:" are pinned with the cog), and so are fenced code blocks."""
    link, bare = _patterns(prefix)
    out: list[dict] = []
    in_cog = in_code = False
    for n, raw in enumerate(frame.lines, start=frame.body_start):
        s = raw.strip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if s.startswith("## "):
            in_cog = s.startswith("## Cog:")
        if in_cog:
            continue
        seen: set[tuple] = set()
        spans = []
        for form, pat in (("link", link), ("number", bare)):
            for m in pat.finditer(raw):
                if any(a < m.end() and m.start() < b for a, b in spans):
                    continue  # the number inside a link is that link
                spans.append((m.start(), m.end()))
                key = (m.group("num"), m.group("rev") or None)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"frame": frame.name, "line": n, "document": key[0], "revision": key[1], "form": form,
                            "text": m.group(0).strip()})
    return out


# ---------------------------------------------------------------- what a citation resolves to
def _lineage(rec: dict) -> str | None:
    lin = str((rec.get("fields") or {}).get("lineage") or "")
    return lin[len("sop:"):] if lin.startswith("sop:") else None


def _of_lineage(kernel, name: str) -> list[dict]:
    return [d for d in kernel.query(type_="document") if (d["fields"].get("lineage") or "") == "sop:" + name]


def _superseded_by(kernel, rec: dict) -> dict | None:
    """The record that supersedes this one (its next revision), if any."""
    for lk in kernel.links_of(rec["locator"]):
        if lk["rel"] == "supersedes" and lk["direction"] == "in":
            return kernel.get(lk["target"])
    name = _lineage(rec)
    if name:
        return next((d for d in _of_lineage(kernel, name) if d["fields"].get("revises") == rec["number"] and d["state"] == "effective"), None)
    return None


def _current(kernel, rec: dict) -> dict | None:
    """The revision in force of the document this record is a revision of (itself when it is in force)."""
    name = _lineage(rec)
    if name:
        return next((d for d in _of_lineage(kernel, name) if d["state"] == "effective"), None)
    seen, cur = set(), rec
    while cur is not None and cur["locator"] not in seen:
        if cur["state"] == "effective":
            return cur
        seen.add(cur["locator"])
        cur = _superseded_by(kernel, cur)
    return None


def _not_in_force(kernel, rec: dict, what: str) -> tuple[str, str]:
    st = rec["state"]
    if st == "obsolete":
        newer = _superseded_by(kernel, rec)
        if newer is not None:
            return "superseded", f"{what} is superseded by {newer['number']} (revision {newer['fields'].get('revision') or '?'})"
        return "obsolete", f"{what} is obsolete (retired)"
    if st == "withdrawn":
        return "withdrawn", f"{what} was withdrawn"
    return "not-in-force", f"{what} is {st.replace('-', ' ')}, not in force"


def resolve(kernel, number: str, revision: str | None = None, *, drafts: dict | None = None) -> dict:
    """What a citation of ``number`` (and ``revision``) names, as it will be once the compile it is in is released:
    {"status", "why", "record" (the record cited: the revision named, else the number's), "target" (the record a
    ``references`` link goes to), "lineage", "by_this_change"}. ``drafts`` are the SOP drafts the compile carries
    ({SOP name: draft locator, or None for one it retires}): a draft comes into force with it, a retired one does not.
    Status: "in-force", "unknown", "obsolete", "superseded", "withdrawn" or "not-in-force"."""
    drafts = drafts or {}
    rec = kernel.get(f"rec:document:{number}")
    if rec is None or rec.get("type") != "document":
        return {"status": "unknown", "why": f"{number} is not a document in this hub", "record": None, "target": None, "lineage": None,
                "by_this_change": False}
    name = _lineage(rec)
    out = {"record": rec, "target": rec, "lineage": name, "by_this_change": False}
    if revision:
        what = f"{number} revision {revision}"
        pinned = rec if (rec["fields"].get("revision") or "") == revision else next(
            (d for d in (_of_lineage(kernel, name) if name else []) if (d["fields"].get("revision") or "") == revision), None)
        if pinned is None:
            return {**out, "status": "unknown" if name else "not-in-force",
                    "why": f"{number} has no revision {revision}" if name else
                           f"{number} is at revision {rec['fields'].get('revision') or '?'}; revision {revision} is not in force"}
        out.update(record=pinned, target=pinned)
        if name and name in drafts:
            loc = drafts[name]
            if loc is None:
                return {**out, "status": "obsolete", "by_this_change": True, "why": f"{what} is retired by this change: obsolete once it is released"}
            if loc == pinned["locator"]:
                return {**out, "status": "in-force", "why": f"{what} comes into force with this change"}
            if pinned["state"] == "effective":
                newer = kernel.get(loc)
                return {**out, "status": "superseded", "by_this_change": True,
                        "why": f"{what} is superseded by {newer['number'] if newer else 'a new revision'} in this change"}
        if pinned["state"] == "effective":
            return {**out, "status": "in-force", "why": f"{what} is in force"}
        status, why = _not_in_force(kernel, pinned, what)
        return {**out, "status": status, "why": why}
    if name and name in drafts:
        loc = drafts[name]
        if loc is None:
            return {**out, "status": "obsolete", "by_this_change": True, "why": f"{number} is retired by this change: obsolete once it is released"}
        draft = kernel.get(loc)
        return {**out, "target": draft or rec, "status": "in-force",
                "why": f"{number} is in force with this change" + (f" as {draft['number']}" if draft and draft["number"] != number else "")}
    cur = _current(kernel, rec)
    if cur is not None:
        return {**out, "target": cur, "status": "in-force",
                "why": f"{number} is in force" + (f" as {cur['number']} (revision {cur['fields'].get('revision') or '?'})" if cur["locator"] != rec["locator"] else "")}
    if name and rec["state"] in ("effective", "obsolete"):  # every revision of the SOP is out of force: it was retired
        return {**out, "status": "obsolete", "why": f"{number} is obsolete: {name} has no revision in force (retired)"}
    status, why = _not_in_force(kernel, rec, number)
    return {**out, "status": status, "why": why}


def summary(r: dict) -> dict:
    """A resolution as the proposal records it (no records)."""
    return {"status": r["status"], "why": r["why"], "lineage": r.get("lineage"), "by_this_change": bool(r.get("by_this_change")),
            "record": (r.get("record") or {}).get("number"), "target": (r.get("target") or {}).get("number")}


def reference_guard(references: list[dict], resolve_one) -> list[Finding]:
    """A citation of an unknown document, or of one that is not in force, is a warning at its frame and line. Each
    reference gets its resolution (``resolved``) recorded on it."""
    out = []
    for ref in references:
        r = resolve_one(ref)
        ref["resolved"] = summary(r)
        if r["status"] == "in-force":
            continue
        out.append(Finding(GUARD, "warning",
                           f"cites {ref['text']}: {r['why']}; cite a document in force (its number follows its current revision)",
                           rule_key=f"reference:{ref['document']}" + (f"@{ref['revision']}" if ref.get("revision") else ""),
                           frame=ref["frame"], line=int(ref["line"])))
    return out


def recorded(references: list[dict]) -> list[dict]:
    """The citations as a release records them: what the prose says, not what it resolved to then."""
    keys = ("frame", "line", "document", "revision", "form", "text")
    return [{k: r.get(k) for k in keys} for r in references or []]


# ---------------------------------------------------------------- at release
def _document_of(hub, name: str) -> dict | None:
    """The document in force that carries a Frame: its SOP document, or (before the SOPs are documents) its mirror."""
    from . import sopdocs

    d = sopdocs.in_force(hub.kernel, name)
    if d is not None:
        return d
    return next((x for x in hub.kernel.query(type_="document")
                 if sopdocs.is_frame_doc(x["fields"]) and x["fields"].get("frame_name") == name and x["state"] == "effective"), None)


def _linked(rec: dict, target: str) -> bool:
    return any(lk["rel"] == LINK and lk["target"] == target for lk in rec.get("links") or [])


def sync_links(hub, digest: str, *, by: str = "hub") -> dict:
    """Make each document in force cite by link what its SOP cites in release ``digest``: a ``references`` link from it
    to each document cited (to the revision in force for a citation of a number), and none to a document it no longer
    cites. A document no longer in force keeps no citation links. Only the links made here (the ``cites`` field) are
    ever taken off. Idempotent. Returns {"linked": [(from, to)], "unlinked": [(from, to)]}."""
    k = hub.kernel
    meta = hub.releases.get(digest)
    refs = meta.get("references") or []
    managed = {d["locator"]: d for d in k.query(type_="document") if d["fields"].get(CITES)}
    out: dict = {"linked": [], "unlinked": []}
    if not refs and not managed:
        return out
    by_frame: dict[str, list[dict]] = {}
    for r in refs:
        by_frame.setdefault(r["frame"], []).append(r)
    want: dict[str, list[str]] = {}
    for name in sorted(by_frame):
        doc = _document_of(hub, name)
        if doc is None:
            continue
        managed.setdefault(doc["locator"], doc)
        for r in by_frame[name]:
            res = resolve(k, r["document"], r.get("revision"))
            t = res.get("target")
            if t is None or t["locator"] == doc["locator"]:
                continue
            want.setdefault(doc["locator"], [])
            if t["locator"] not in want[doc["locator"]]:
                want[doc["locator"]].append(t["locator"])
    for loc in sorted(managed):
        rec = k.get(loc)
        have = list(rec["fields"].get(CITES) or [])
        targets = want.get(loc, [])
        keep = []
        for t in targets:
            if t in have:
                keep.append(t)
            elif not _linked(rec, t):
                k.link(loc, LINK, t, by=by, release=digest)
                out["linked"].append((rec["number"], k.get(t)["number"]))
                keep.append(t)
            # a link someone made by hand stays theirs: it is not added to the managed ones
        for t in have:
            if t not in targets and k.get(t) is not None and _linked(k.get(loc), t):
                k.unlink(loc, LINK, t, by=by, release=digest)
                out["unlinked"].append((rec["number"], k.get(t)["number"]))
        if sorted(keep) != sorted(have):
            k.set_fields(loc, {CITES: keep}, by=by, release=digest)
    return out


def flag_citations(hub, co: dict, digest: str) -> list[dict]:
    """After a change order's release: each document in force whose SOP still cites a document the release made
    obsolete (a retirement) or a revision it superseded (a new revision of a citation naming that revision) is flagged
    to its owner: the flag is kept on the citing document (``citation_flags``) and the owner is told. A citation of a
    number follows the new revision and is not flagged. Idempotent. Returns the new flags."""
    from . import eco
    from .release import utcnow

    k = hub.kernel
    refs = hub.releases.get(digest).get("references") or []
    if not refs:
        return []
    retired = eco.retirements(co)
    gone = {r["locator"] for r in retired.values() if r.get("locator")}
    for c in eco.carried(hub, co):
        old = k.get(f"rec:document:{c['draft']['fields'].get('revises')}") if c["draft"]["fields"].get("revises") else None
        if old is not None:
            gone.add(old["locator"])
    new_flags = []
    for ref in refs:
        doc = _document_of(hub, ref["frame"])
        if doc is None:
            continue
        r = resolve(k, ref["document"], ref.get("revision"))
        rec = r.get("record")
        if r["status"] == "in-force" or rec is None:
            continue
        if not (rec["locator"] in gone or (not ref.get("revision") and r.get("lineage") in retired)):
            continue
        doc = k.get(doc["locator"])
        flags = list(doc["fields"].get(FLAGS) or [])
        key = (ref["document"], ref.get("revision"), int(ref["line"]), co["number"])
        if any((f.get("document"), f.get("revision"), int(f.get("line") or 0), f.get("by")) == key for f in flags):
            continue
        flag = {"document": ref["document"], "revision": ref.get("revision"), "line": int(ref["line"]), "frame": ref["frame"],
                "text": ref.get("text"), "status": r["status"], "why": r["why"], "by": co["number"], "release": digest, "at": utcnow(),
                "cleared": None}
        k.set_fields(doc["locator"], {FLAGS: flags + [flag]}, by="hub", release=digest)
        owner = doc["fields"].get("owner")
        if owner:
            hub.inbox.notify(owner, kind="task", subject=f"{doc['number']} cites {ref['text']}, which {co['number']} made {r['status']}",
                             body=f"{ref['frame']} line {ref['line']} cites {ref['text']}: {r['why']}. Revise {doc['number']} on a change order "
                                  "to cite a document in force, or take the citation out.", record=doc["locator"])
        new_flags.append({**flag, "citing": doc["number"], "owner": owner})
    return new_flags
