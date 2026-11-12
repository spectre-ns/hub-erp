"""Document references in SOP prose: read at compile, checked by a guard, linked at release, flagged when retired.

An SOP that says "see DOC-0040", "DOC-0040 rev B" or links to a document's page ("/document/DOC-0040") cites that
document. The compile reads every such citation with the Frame and line it is on (``read_references``) and records them
in the proposal and the release; ``reference_guard`` reports a citation of an unknown document, or of one not in force
(obsolete, superseded, withdrawn, not yet effective), as a warning with its frame and line. When a release puts an SOP
in force, ``sync_links`` makes its citations ``references`` links from its document to the cited ones (``referenced-by``
on theirs) and takes off the links of citations it no longer makes. After a change order's release, ``flag_citations``
flags each SOP in force still citing a document no longer in force (obsolete, or the revision cited superseded) to its
owner, once.

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
flagged only at the next change order's release; a flag is never cleared, it goes with the citing revision when a new
one supersedes it (a new revision that still cites the document is flagged again); a compile warning names the state of the document cited ("DOC-0040 is obsolete"), which tells
the author that it exists and its state though they may not be allowed to read it; and a hub whose Frame files stay the
source gets links only for Frames that have a mirror document.
"""

from __future__ import annotations

import os
import re
import sys
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
# A URL may hold balanced parentheses ("https://wiki.example/Gauge_(metrology)"), one level deep.
_URL_CHARS = r"(?:[^\s<>()\[\]\"'`]|\([^\s<>()\[\]\"'`]*\))+"
_URL = re.compile(r"https?://" + _URL_CHARS, re.IGNORECASE)
_MD_LINK = re.compile(r"\[(?P<t>[^\]]*)\]\((?P<u>" + _URL_CHARS + r")(?:\s+\"[^\"]*\")?\)")
_INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s")
OTHER_SITE = "other-site"  # the form of a link to a document's page on another host: no citation, a warning
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
    bare = re.compile(rf"(?<![\w/.:%-])(?P<num>{num})" + _REV)  # not inside a path or a host ("www.x.example/DOC-0040.pdf")
    return path, relative, bare


def document_numbering(record_types: dict | None) -> tuple[str, bool]:
    """(prefix, yearly) of the ``document`` record type's numbers."""
    td = (record_types or {}).get("document") or {}
    return str(td.get("prefix") or "DOC"), bool(td.get("yearly"))


_DEFAULT_PORTS = {"http": 80, "https": 443}
_told_bad: set[tuple[str, str]] = set()


def host_key(url: str) -> tuple[str, int] | None:
    """(host name, port) of an absolute http(s) URL, the scheme's default port filled in; None for anything else."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    return parts.hostname.lower(), port or _DEFAULT_PORTS[scheme]


def hub_hosts() -> tuple[tuple[str, int], ...]:
    """The (host, port) pairs this hub is reached at: an absolute link to a document's page counts as a citation only
    on one of them. From ``HUBPROTO_PUBLIC_URL`` (the address people use) and ``HUBPROTO_WOPI_BASE`` (the address the
    editor uses); with neither set, only relative links ("/document/DOC-0040") count. A value with no http(s) scheme
    ("hub.example:8080") names no host: it is left out, and said once on stderr."""
    out: list[tuple[str, int]] = []
    for var in ("HUBPROTO_PUBLIC_URL", "HUBPROTO_WOPI_BASE"):
        value = (os.environ.get(var) or "").strip()
        if not value:
            continue
        key = host_key(value)
        if key is None:
            if (var, value) not in _told_bad:
                _told_bad.add((var, value))
                sys.stderr.write(f"warning: {var}={value!r} is not an http(s) URL; links to this hub's pages in SOP prose are read only "
                                 "when relative\n")
            continue
        if key not in out:
            out.append(key)
    return tuple(out)


def _hosts(hosts) -> tuple[tuple[str, int], ...]:
    """Hosts as given (an URL, "host[:port]" for http, or a (host, port) pair) as (host, port) pairs."""
    out = []
    for h in hosts:
        if isinstance(h, tuple):
            out.append((str(h[0]).lower(), int(h[1])))
            continue
        key = host_key(h if "://" in h else "http://" + h)
        if key:
            out.append(key)
    return tuple(out)


def _blank(s: str, a: int, b: int) -> str:
    return s[:a] + " " * (b - a) + s[b:]


def _site_link(target: str, path: re.Pattern, hosts) -> tuple[str, str, bool] | None:
    """(number, the link as cited, on this hub) when ``target`` is a link to a document's page, else None. A link
    to such a page on another host is no citation (the third item is False): the guard warns about it."""
    if re.match(r"https?://", target, re.IGNORECASE):
        parts = urlsplit(target)
        m = path.match(parts.path)
        if not m:
            return None
        return m.group("num"), target[:target.find(parts.path) + m.end()], host_key(target) in hosts
    m = path.match(target)
    return (m.group("num"), m.group(0), True) if m else None


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
        if not hit[2]:
            found.append((m.start("u"), hit[0], None, OTHER_SITE, hit[1]))
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
            found.append((m.start(), hit[0], None, "link" if hit[2] else OTHER_SITE, hit[1]))
    for m in relative.finditer(line):
        found.append((m.start(), m.group("num"), None, "link", m.group(0)))
        line = _blank(line, m.start(), m.end())
    for m in bare.finditer(line):
        found.append((m.start(), m.group("num"), (m.group("rev") or "").upper() or None, "number", m.group(0).strip()))
    return sorted(found)


def read_references(frame, prefix: str = "DOC", *, yearly: bool = False, hosts=None) -> list[dict]:
    """The citations of documents in a Frame's body: [{"frame", "line", "document", "revision", "form", "text"}], in
    line order, one per document (and revision) a line cites. ``form`` is "number" or "link" (a link to a document's
    page, relative or on one of ``hosts``, by default ``hub_hosts()``), or ``OTHER_SITE``: a link to a document's page
    on another host, which is no citation (it is not recorded in the release nor linked) but a warning. Left out: cog
    sections (to the next heading of their level or higher; their "Prompt from:" and "References:" are pinned with the
    cog), code (fenced with ``` or ~~~, in a list too, indented, or inline), and other sites' URLs."""
    pats = _patterns(prefix, yearly)
    hosts = _hosts(hub_hosts() if hosts is None else hosts)
    out: list[dict] = []
    in_indented = in_list = False
    cog_level = 0  # the level of the "## Cog:" heading whose section this is, 0 outside one
    fence = ""
    prev_blank = True
    for n, raw in enumerate(frame.lines, start=frame.body_start):
        s = raw.strip()
        expanded = raw.expandtabs(4)
        indent = len(expanded) - len(expanded.lstrip(" "))
        if fence:
            if s.startswith(fence) and not s.strip(fence[0]):
                fence = ""
            continue
        fm = _FENCE.match(raw)
        if fm and (indent <= 3 or in_list):  # a fence (in a list item it is indented); else indented code, below
            fence = fm.group(1)
            continue
        if not s:
            prev_blank = True
            continue
        if indent >= 4 and (in_indented or (prev_blank and not in_list)):
            in_indented, prev_blank = True, False  # an indented code block
            continue
        in_indented = False
        if _LIST_ITEM.match(raw):
            in_list = True
        elif indent == 0 and prev_blank or s.startswith("#"):
            in_list = False
        prev_blank = False
        hm = _HEADING.match(raw)
        if hm:
            level = len(hm.group(1))
            if cog_level and level <= cog_level:
                cog_level = 0
            if s.startswith("## Cog:"):
                cog_level = level
        if cog_level:
            continue
        seen: set[tuple] = set()
        for _, num, rev, form, text in _line_citations(raw, pats, hosts):
            if (num, rev, form == OTHER_SITE) in seen:
                continue
            seen.add((num, rev, form == OTHER_SITE))
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
        if ref.get("form") == OTHER_SITE:
            why = f"{ref['text']} is a document's page on another site, not this hub: it is not read as a citation"
            ref["resolved"] = {"status": OTHER_SITE, "why": why, "lineage": None, "by_this_change": False, "record": None, "target": None}
            out.append(Finding(GUARD, "warning", f"links to {why}; link to this hub's page (a relative link, /document/{ref['document']}), "
                                                 "or to the other site's page without a number of this hub",
                               rule_key=f"reference:{ref['document']}@{OTHER_SITE}", frame=ref["frame"], line=int(ref["line"])))
            continue
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
    return [{k: r.get(k) for k in keys} for r in references or [] if r.get("form") != OTHER_SITE]


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
    are ever taken off: a link a person made stays, and is not taken over when the prose cites it too. ``cites`` is
    written before any link is made or taken off (with every target it is about to touch), so a crash part-way leaves
    no link of the hub's looking like a person's. A document on hold is left as it is. Idempotent. Returns
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
        # The hub's links once this is done: the ones it has, and the new ones not already there by a person's hand.
        keep = [t for t in targets if t in have or not _linked(rec, t)]
        drop = [t for t in have if t not in targets]
        touching = keep + [t for t in drop if t not in keep]
        if sorted(touching) != sorted(have):  # first say which links are the hub's, then make them
            rec = k.set_fields(loc, {CITES: touching}, by=by, release=digest)
        for t in keep:
            if not _linked(rec, t):
                rec = k.link(loc, LINK, t, by=by, release=digest)
                out["linked"].append((rec["number"], k.get(t)["number"]))
        for t in drop:
            if k.get(t) is not None and _linked(rec, t):
                rec = k.unlink(loc, LINK, t, by=by, release=digest)
                out["unlinked"].append((rec["number"], k.get(t)["number"]))
        if sorted(keep) != sorted(touching):
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
    requester = co["fields"].get("requested_by") or co.get("created_by") or ""
    return requester if person(requester) else ""


STALE = ("obsolete", "superseded")  # a document that was in force and is no longer: a citation of it is flagged


def _key(f: dict) -> tuple:
    return (f.get("document"), f.get("revision"), int(f.get("line") or 0))


def flag_citations(hub, co: dict) -> dict:
    """After a change order's release: each citation in the release in force of a document that was in force and no
    longer is (obsolete, or a revision superseded), in an SOP whose document is in force and that has no flag for it
    yet, is flagged: one flag per line on the citing document (``citation_flags``), and one message to its owner
    listing the lines not yet told. A citation of a number follows the new revision and is not flagged; one a later
    release took out is not flagged either. Whatever made it stale (this change order, an earlier one whose flag a hold
    held back, a document retired by hand) is caught at the first release after. A document on hold is not flagged,
    but its owner is told (a message writes nothing on the document) and the flag follows at a later release once the
    hold is lifted. An unknown document or a revision that is not the number's is the compile's warning, not a flag.
    Idempotent and safe to retry part-way: each message carries the citations it tells of, and its id follows from
    them, so a line is told once. Returns {"flagged": [flag], "held": [number], "told": [{"citing", "frame", "to",
    "lines"}], "nobody": [number] (with lines to tell and no one to tell them to)}."""
    import json

    from .release import utcnow
    from .rules import sha256_of

    k = hub.kernel
    meta = hub.releases.current() or {}
    digest = meta.get("digest") or ""
    refs = meta.get("references") or []
    out: dict = {"flagged": [], "held": [], "told": [], "nobody": []}
    stale: dict[str, list[tuple[dict, dict]]] = {}  # citing document -> [(reference, resolution)]
    frames: dict[str, str] = {}
    for ref in refs:
        doc = _document_of(hub, ref["frame"])
        if doc is None:
            continue
        r = resolve(k, ref["document"], ref.get("revision"), citing=ref["frame"])
        if r["status"] not in STALE or r.get("record") is None:
            continue
        stale.setdefault(doc["locator"], []).append((ref, r))
        frames[doc["locator"]] = ref["frame"]
    for loc in sorted(stale):
        doc = k.get(loc)
        flags = list(doc["fields"].get(FLAGS) or [])
        flagged = {_key(f) for f in flags}
        new = []
        for ref, r in stale[loc]:
            f = {"document": ref["document"], "revision": ref.get("revision"), "line": int(ref["line"]), "frame": ref["frame"],
                 "text": ref.get("text"), "status": r["status"], "why": r["why"], "by": co["number"], "release": digest,
                 "at": utcnow(), "cleared": None}
            if _key(f) not in flagged and _key(f) not in {_key(x) for x in new}:
                new.append(f)
        if _held(doc):
            if new:
                out["held"].append(doc["number"])
        elif new:
            doc = k.set_fields(loc, {FLAGS: flags + new}, by="hub", release=digest)
            out["flagged"] += [{**f, "citing": doc["number"]} for f in new]
        told = {tuple(c) for m in hub.inbox.on_record(loc) for c in m.get("citations") or []}
        tell = sorted((f for f in list(doc["fields"].get(FLAGS) or []) + new if _key(f) not in told), key=_key)
        tell = [f for i, f in enumerate(tell) if _key(f) not in {_key(x) for x in tell[:i]}]
        if not tell:
            continue
        who = _recipient(hub, doc, frames[loc], co)
        if not who:
            out["nobody"].append(doc["number"])
            continue
        keys = [list(_key(f)) for f in tell]
        hub.inbox.notify(who, kind="task", subject=f"{doc['number']} cites documents no longer in force ({co['number']})",
                         body=f"{doc['number']} ({frames[loc]}) cites documents no longer in force:\n"
                              + "".join(f"- line {f['line']} cites {f['text']}: {f['why']}\n" for f in tell)
                              + f"Revise {doc['number']} on a change order to cite documents in force, or take the citations out.",
                         record=loc, key="cite-" + sha256_of({"doc": loc, "co": co["number"], "citations": json.dumps(keys)})[:16],
                         data={"citations": keys})
        out["told"].append({"citing": doc["number"], "frame": frames[loc], "to": who, "lines": [(f["line"], f["text"]) for f in tell]})
    return out
