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

A citation names a revision of its own number: "DOC-0055 rev A" when DOC-0055 is revision B is a warning, not a quiet
reading as the lineage's revision A. An SOP citing its own lineage ("Revision history: DOC-0008 rev A") gets no warning,
no flag and no link to itself.

Not covered (on purpose, for now): a document made obsolete outside a release (retired by hand in document control) is
not flagged, as only a change order's release flags; a flag is never cleared, it goes with the citing revision when a
new one supersedes it; a compile warning names the state of the document cited ("DOC-0040 is obsolete"), which tells
the author that it exists and its state though they may not be allowed to read it; and a hub whose Frame files stay the
source gets links only for Frames that have a mirror document.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from .guards import Finding

GUARD = "document-reference"
LINK = "references"
CITES = "cites"  # field on a citing document: the targets its citations are linked to (only those links are managed)
FLAGS = "citation_flags"  # field on a citing document: citations a release made stale, for its owner
SOUND = ("in-force", "self")  # resolutions that are no finding: a document in force, or the SOP's own lineage

# A revision after a number: "rev B", "rev. B", "revision B", "(rev. B)", ", revision 2"; a single lower-case letter too
# ("rev a"), read as its capital.
_REV = r"(?:,?\s*(?P<paren>\()?(?i:rev(?:ision)?\.?)\s*(?P<rev>[A-Z]{1,2}|[a-z]\b|\d+)\b(?(paren)\)))?"
_URL = re.compile(r"https?://[^\s<>()\[\]\"'`]+", re.IGNORECASE)
_MD_LINK = re.compile(r"\[(?P<t>[^\]]*)\]\((?P<u>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")


def number_pattern(prefix: str = "DOC", yearly: bool = False) -> str:
    """A document number. This follows the ``document`` record type's numbering as ``hubproto.kernel.next_number`` makes
    it ("<prefix>-<n>", or "<prefix>-<year>-<n>" when yearly): a change to that numbering must change this pattern. A
    number runs to the end of the word: "DOC-0042-A2" is not DOC-0042."""
    return rf"{re.escape(prefix)}-" + (r"\d{4}-" if yearly else "") + r"\d+(?![\w-])"


def _patterns(prefix: str, yearly: bool = False) -> tuple[re.Pattern, re.Pattern, re.Pattern]:
    """(the path of a document's page, a relative link to one, a number with an optional revision)."""
    num = number_pattern(prefix, yearly)
    colon = r"(?::|%3[Aa])"
    path = re.compile(rf"/(?:document|doc|record)/(?:rec{colon}document{colon})?(?P<num>{num})")
    relative = re.compile(r"(?<![\w/.:%-])" + path.pattern)
    bare = re.compile(rf"(?<![\w-])(?P<num>{num})" + _REV)
    return path, relative, bare


def document_numbering(record_types: dict | None) -> tuple[str, bool]:
    """(prefix, yearly) of the ``document`` record type's numbers."""
    td = (record_types or {}).get("document") or {}
    return str(td.get("prefix") or "DOC"), bool(td.get("yearly"))


def hub_hosts() -> tuple[str, ...]:
    """The hosts (with their port) this hub is reached at: an absolute link to a document's page counts as a citation
    only on one of them. From ``HUBPROTO_PUBLIC_URL`` (the address people use) and ``HUBPROTO_WOPI_BASE`` (the address
    the editor uses); with neither set, only relative links ("/document/DOC-0040") count."""
    out = []
    for var in ("HUBPROTO_PUBLIC_URL", "HUBPROTO_WOPI_BASE"):
        host = urlsplit((os.environ.get(var) or "").strip()).netloc.lower()
        if host and host not in out:
            out.append(host)
    return tuple(out)


def _blank(s: str, a: int, b: int) -> str:
    return s[:a] + " " * (b - a) + s[b:]


def _site_link(target: str, path: re.Pattern, hosts) -> tuple[str, str] | None:
    """(number, the link as cited) when ``target`` is a link to a document's page on this hub, else None."""
    if re.match(r"https?://", target, re.IGNORECASE):
        parts = urlsplit(target)
        if parts.netloc.lower() not in hosts:
            return None  # another site's page: no citation of this hub's document
        m = path.match(parts.path)
        return (m.group("num"), target[:target.find(parts.path) + m.end()]) if m else None
    m = path.match(target)
    return (m.group("num"), m.group(0)) if m else None


def _line_citations(line: str, pats, hosts) -> list[tuple[int, str, str | None, str, str]]:
    """(position, number, revision, form, text) for each citation on one line of prose (inline code taken out)."""
    path, relative, bare = pats
    line = _INLINE_CODE.sub(lambda m: " " * len(m.group(0)), line)
    found = []
    # A Markdown link is one citation, its text and its target together ("[DOC-0048 rev A](/document/DOC-0048)").
    for m in _MD_LINK.finditer(line):
        hit = _site_link(m.group("u"), path, hosts)
        line = _blank(line, m.start("u"), m.end("u"))  # a link's target is no number cited in the prose
        if hit is None:
            continue
        num, text, rev = hit[0], hit[1], None
        t0 = m.start("t")
        for b in bare.finditer(m.group("t")):
            if b.group("num") == num:
                if rev is None and b.group("rev"):
                    rev, text = b.group("rev").upper(), b.group(0).strip()
                elif text == hit[1]:
                    text = b.group(0).strip()
                line = _blank(line, t0 + b.start(), t0 + b.end())
        found.append((m.start(), num, rev, "link", text))
    for m in _URL.finditer(line):
        hit = _site_link(m.group(0), path, hosts)
        line = _blank(line, m.start(), m.end())  # any absolute URL: the number in it is no citation, unless it is ours
        if hit is not None:
            found.append((m.start(), hit[0], None, "link", hit[1]))
    for m in relative.finditer(line):
        found.append((m.start(), m.group("num"), None, "link", m.group(0)))
        line = _blank(line, m.start(), m.end())
    for m in bare.finditer(line):
        found.append((m.start(), m.group("num"), (m.group("rev") or "").upper() or None, "number", m.group(0).strip()))
    return sorted(found)


def read_references(frame, prefix: str = "DOC", *, yearly: bool = False, hosts=None) -> list[dict]:
    """The citations of documents in a Frame's body: [{"frame", "line", "document", "revision", "form", "text"}], in
    line order, one per document (and revision) a line cites. ``form`` is "number" or "link" (a link to a document's
    page, relative or on one of ``hosts``, by default ``hub_hosts()``). Left out: cog sections (their "Prompt from:"
    and "References:" are pinned with the cog), code (fenced with ``` or ~~~, indented, or inline), and links to other
    sites."""
    pats = _patterns(prefix, yearly)
    hosts = tuple(h.lower() for h in (hub_hosts() if hosts is None else hosts))
    out: list[dict] = []
    in_cog = in_indented = in_list = False
    fence = ""
    prev_blank = True
    for n, raw in enumerate(frame.lines, start=frame.body_start):
        s = raw.strip()
        if fence:
            if s.startswith(fence) and not s.strip(fence[0]):
                fence = ""
            continue
        fm = _FENCE.match(raw)
        if fm:
            fence = fm.group(1)
            continue
        if not s:
            prev_blank = True
            continue
        expanded = raw.expandtabs(4)
        indent = len(expanded) - len(expanded.lstrip(" "))
        if indent >= 4 and (in_indented or (prev_blank and not in_list)):
            in_indented, prev_blank = True, False  # an indented code block
            continue
        in_indented = False
        if _LIST_ITEM.match(raw):
            in_list = True
        elif indent == 0 and prev_blank or s.startswith("#"):
            in_list = False
        prev_blank = False
        if s.startswith("## "):
            in_cog = s.startswith("## Cog:")
        if in_cog:
            continue
        seen: set[tuple] = set()
        for _, num, rev, form, text in _line_citations(raw, pats, hosts):
            if (num, rev) in seen:
                continue
            seen.add((num, rev))
            out.append({"frame": frame.name, "line": n, "document": num, "revision": rev, "form": form, "text": text})
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


def _is_self(rec: dict, citing: str | None) -> bool:
    """True when ``rec`` is a revision of the SOP ``citing`` itself (its lineage, or its mirror document)."""
    if not citing:
        return False
    from . import sopdocs

    f = rec.get("fields") or {}
    return _lineage(rec) == citing or (sopdocs.is_frame_doc(f) and f.get("frame_name") == citing)


def resolve(kernel, number: str, revision: str | None = None, *, drafts: dict | None = None, citing: str | None = None) -> dict:
    """What a citation of ``number`` (and ``revision``) names, as it will be once the compile it is in is released:
    {"status", "why", "record" (the record cited: the revision named, else the number's), "target" (the record a
    ``references`` link goes to), "lineage", "by_this_change"}. ``drafts`` are the SOP drafts the compile carries
    ({SOP name: draft locator, or None for one it retires}): a draft comes into force with it, a retired one does not.
    ``citing`` is the SOP the citation is in: a citation of its own lineage ("Revision history: DOC-0008 rev A") is
    status "self", with no target. Status: "in-force", "self", "unknown", "mismatch" (the number is another revision
    than the one named), "obsolete", "superseded", "withdrawn" or "not-in-force"."""
    drafts = drafts or {}
    rec = kernel.get(f"rec:document:{number}")
    if rec is None or rec.get("type") != "document":
        return {"status": "unknown", "why": f"{number} is not a document in this hub", "record": None, "target": None, "lineage": None,
                "by_this_change": False}
    name = _lineage(rec)
    out = {"record": rec, "target": rec, "lineage": name, "by_this_change": False}
    if _is_self(rec, citing):
        return {**out, "target": None, "status": "self", "why": f"{number} is a revision of {citing} itself"}
    if revision:
        what = f"{number} revision {revision}"
        has = str(rec["fields"].get("revision") or "")
        if has.upper() != str(revision).upper():  # each revision is its own number: "DOC-0055 rev A" when DOC-0055 is revision B names neither
            other = next((d for d in (_of_lineage(kernel, name) if name else [])
                          if str(d["fields"].get("revision") or "").upper() == str(revision).upper()), None)
            return {**out, "status": "mismatch",
                    "why": f"{number} is revision {has or '?'}, not revision {revision}"
                           + (f" (revision {revision} is {other['number']})" if other else "")}
        pinned = rec
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
        if r["status"] in SOUND:
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


def _bundle(hub) -> dict:
    meta = hub.releases.current()
    return hub.releases.bundle(meta["digest"]) if meta else {}


def _held(rec: dict | None) -> bool:
    return bool(rec and rec.get("hold"))


def sync_links(hub, *, by: str = "hub") -> dict:
    """Make each document in force cite by link what its SOP cites in the release in force (whichever job calls it, and
    however late: a retried job never puts back the citations of the release it started under): a ``references`` link
    from it to each document cited (to the revision in force for a citation of a number), and none to a document it no
    longer cites. A document no longer in force keeps no citation links. Only the links made here (the ``cites`` field)
    are ever taken off: a link a person made stays. A document on hold is left as it is. Idempotent. Returns
    {"linked": [(from, to)], "unlinked": [(from, to)], "held": [number]}."""
    k = hub.kernel
    meta = hub.releases.current() or {}
    digest = meta.get("digest") or ""
    refs = meta.get("references") or []
    managed = {d["locator"]: d for d in k.query(type_="document") if d["fields"].get(CITES)}
    out: dict = {"linked": [], "unlinked": [], "held": []}
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
            res = resolve(k, r["document"], r.get("revision"), citing=name)
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
        if _held(rec):
            if sorted(have) != sorted(targets):
                out["held"].append(rec["number"])
            continue
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


def _recipient(hub, doc: dict, frame: str, co: dict) -> str:
    """Who is told of a citing document's flags: its owner, else its SOP's maintainer, else the change order's requester
    (as for divergences, ``changecontrol.raise_divergence_requests``), the first that is a person of the release in
    force (an owner such as a cog, "operations" or an unfilled "{{MANAGEMENT_REPRESENTATIVE}}" is no one to tell)."""
    members = _bundle(hub).get("members") or {}

    def person(name) -> bool:
        m = members.get(name or "")
        return bool(m) and (m.get("type") or "person") == "person"

    owner = (doc.get("fields") or {}).get("owner")
    if person(owner):
        return owner
    try:
        maintainer = hub.released_frames().get(frame).maintainer
    except Exception:
        maintainer = None
    if person(maintainer):
        return maintainer
    return co["fields"].get("requested_by") or co.get("created_by") or ""


def _subject(doc: dict, co: dict) -> str:
    return f"{doc['number']} cites documents that {co['number']} took out of force"


def flag_citations(hub, co: dict) -> dict:
    """After a change order's release: each document in force whose SOP, in the release in force, still cites a
    document the change order made obsolete (a retirement) or a revision it superseded (a new revision of a citation
    naming that revision) is flagged: one flag per line on the citing document (``citation_flags``), and one message to
    its owner listing them. A citation of a number follows the new revision and is not flagged; one a later release
    took out is not flagged either. Idempotent, and safe to retry part-way: a message not yet sent for flags already
    kept is sent on the next run. A document on hold is not flagged. Returns {"flagged": [flag], "held": [number],
    "told": [{"citing", "frame", "to", "lines"}], "nobody": [number] (flagged, with no one to tell)}."""
    from . import eco
    from .release import utcnow

    k = hub.kernel
    meta = hub.releases.current() or {}
    digest = meta.get("digest") or ""
    refs = meta.get("references") or []
    out: dict = {"flagged": [], "held": [], "told": [], "nobody": []}
    if not refs:
        return out
    retired = eco.retirements(co)
    gone = {r["locator"] for r in retired.values() if r.get("locator")}
    for c in eco.carried(hub, co):
        old = k.get(f"rec:document:{c['draft']['fields'].get('revises')}") if c["draft"]["fields"].get("revises") else None
        if old is not None:
            gone.add(old["locator"])
    stale: dict[str, list[tuple[dict, dict]]] = {}  # citing document -> [(reference, resolution)]
    frames: dict[str, str] = {}
    for ref in refs:
        doc = _document_of(hub, ref["frame"])
        if doc is None:
            continue
        r = resolve(k, ref["document"], ref.get("revision"), citing=ref["frame"])
        rec = r.get("record")
        if r["status"] in SOUND or rec is None:
            continue
        if not (rec["locator"] in gone or (not ref.get("revision") and r.get("lineage") in retired)):
            continue
        stale.setdefault(doc["locator"], []).append((ref, r))
        frames[doc["locator"]] = ref["frame"]
    for loc in sorted(stale):
        doc = k.get(loc)
        if _held(doc):
            out["held"].append(doc["number"])
            continue
        flags = list(doc["fields"].get(FLAGS) or [])
        new = []
        for ref, r in stale[loc]:
            key = (ref["document"], ref.get("revision"), int(ref["line"]), co["number"])
            if any((f.get("document"), f.get("revision"), int(f.get("line") or 0), f.get("by")) == key for f in flags + new):
                continue
            new.append({"document": ref["document"], "revision": ref.get("revision"), "line": int(ref["line"]), "frame": ref["frame"],
                        "text": ref.get("text"), "status": r["status"], "why": r["why"], "by": co["number"], "release": digest,
                        "at": utcnow(), "cleared": None})
        if new:
            doc = k.set_fields(loc, {FLAGS: flags + new}, by="hub", release=digest)
        mine = sorted((f for f in doc["fields"].get(FLAGS) or [] if f.get("by") == co["number"]), key=lambda f: int(f.get("line") or 0))
        who = _recipient(hub, doc, frames[loc], co)
        for f in new:
            out["flagged"].append({**f, "citing": doc["number"], "owner": who})
        if mine and not who:
            out["nobody"].append(doc["number"])
        if not mine or not who or any(m.get("subject") == _subject(doc, co) for m in hub.inbox.on_record(loc)):
            continue  # told already (an earlier run), or no one to tell
        hub.inbox.notify(who, kind="task", subject=_subject(doc, co),
                         body=f"{co['number']} took out of force documents that {doc['number']} ({frames[loc]}) cites:\n"
                              + "".join(f"- line {f['line']} cites {f['text']}: {f['why']}\n" for f in mine)
                              + f"Revise {doc['number']} on a change order to cite documents in force, or take the citations out.",
                         record=loc)
        out["told"].append({"citing": doc["number"], "frame": frames[loc], "to": who, "lines": [(f["line"], f["text"]) for f in mine]})
    return out
