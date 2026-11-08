"""SOPs and policies as documents: the source the Frame is compiled from.

Each policy, SOP and work instruction is a controlled document in the library (.docx, edited in Collabora like any
other): policies in Policy, SOPs and work instructions in Quality/Controlled Documents. The Frame the hub enforces is
derived from them: each document's text, read back as Frame text (its headings, lines and tables, under the Frame's
header kept on the document), is compiled into the release. The organization model and the cog profiles are policy
documents too. The .frame.md files are where the documents came from; once converted they are no longer edited.

``convert`` makes the documents (once; idempotent by lineage) from the Frames as released; ``frame_text`` reads a
document back as Frame text; ``source_texts`` gives every Frame's text from the documents in force, or from the drafts
an ECO carries, for a staged compile.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .frames import Frame

LINEAGE = "sop:"  # lineage key prefix: sop:<frame name>
POLICY_FOLDER = "Policy"
QUALITY_FOLDER = "Quality/Controlled Documents"


def is_frame_doc(fields: dict) -> bool:
    """The document that carries a Frame's text before the SOPs are the source (once "frame"; now filed as the quality
    document it is: an SOP, a work instruction, a policy)."""
    return fields.get("doc_type") == "frame" or bool(fields.get("frame_mirror"))


def kind_of(frame: Frame) -> tuple[str, str]:
    """(doc_type, folder) for a Frame: policies in Policy, SOPs and work instructions as quality documents."""
    n = frame.name
    if n.startswith("wi-"):
        return "wi", QUALITY_FOLDER
    if n.endswith("-sop"):
        return "sop", QUALITY_FOLDER
    if frame.scope == "reference":
        return "guide", POLICY_FOLDER
    if frame.scope == "persona":
        return "persona", POLICY_FOLDER
    return "policy", POLICY_FOLDER  # finance-policy, purchasing-policy, quality-manual, org-model, cogs


def title_of(frame: Frame) -> str:
    m = re.search(r"^#\s+(.+)$", frame.body, re.M)
    return (m.group(1).strip() if m else frame.name.replace("-", " ").capitalize())


def body_lines(body: str) -> list[str]:
    """One document paragraph per Frame line, blank lines included: each rule sentence stays its own paragraph and
    keeps its line number, so a citation ("document-control-sop line 16") points at the same sentence."""
    return [ln.rstrip() for ln in (body or "").splitlines()]


def to_docx(body: str) -> bytes:
    """The SOP as a .docx: '#' lines as headings (Heading 1 to 3, the editor's styles), every other line as its own
    paragraph with its text exactly as written (indentation, list marks and table rows included), so the document
    reads back as the same Frame text. Formatting people add later in Collabora is theirs."""
    from html import escape

    from .wopi import blank_document

    blank, _ = blank_document("docx")
    paras = []
    for line in body_lines(body):
        m = re.match(r"^(#{1,6}) (.*)$", line)
        if m:
            level = min(len(m.group(1)), 3) if len(m.group(1)) <= 3 else len(m.group(1))
            paras.append(f'<w:p><w:pPr><w:pStyle w:val="Heading{level}"/></w:pPr><w:r><w:t xml:space="preserve">{escape(m.group(2), quote=False)}</w:t></w:r></w:p>')
        elif line:
            paras.append(f'<w:p><w:r><w:t xml:space="preserve">{escape(line, quote=False)}</w:t></w:r></w:p>')
        else:
            paras.append("<w:p/>")
    import io
    import zipfile

    zin = zipfile.ZipFile(io.BytesIO(blank))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml":
                xml = data.decode("utf-8")
                sect = re.search(r"<w:sectPr[ >/].*?</w:sectPr>|<w:sectPr/>", xml, re.S)
                xml = re.sub(r"(<w:body[^>]*>).*(</w:body>)", lambda mm: mm.group(1) + "".join(paras) + (sect.group(0) if sect else "") + mm.group(2),
                             xml, count=1, flags=re.S)
                data = xml.encode("utf-8")
            z.writestr(item, data)
    return out.getvalue()


def docx_lines(content: bytes, ext: str = "docx") -> list[str]:
    """A document read back as Frame lines: headings as '#' lines, each paragraph as a line (its text as written;
    a Word bullet as '- '), tables as '| a | b |' rows."""
    from .edits import TextDoc

    doc = TextDoc(content, ext)
    out: list[str] = []
    for b in doc.blocks:
        if b.kind == "h":
            out.append("#" * max(1, min(b.level, 6)) + " " + b.text.strip())
        elif b.kind == "tbl":
            rows = [r for r in b.text.split("\n") if r.strip()]
            for i, r in enumerate(rows):
                out.append("| " + r + " |")
                if i == 0:
                    out.append("|" + "---|" * (r.count("|") + 1))
        elif b.kind in ("p", "other"):
            t = b.text.rstrip()
            if not t.strip():
                out.append("")
                continue
            if t.lstrip().startswith("• "):
                t = "- " + t.lstrip()[2:].strip()
            elif "<w:numPr>" in (b.raw or "") and not re.match(r"^\s*(?:[-*]|\d+[.)]) ", t):
                t = "- " + t.strip()  # a Word list item someone added: a bullet line
            out.append(t)
    return out


def header_text(meta: dict) -> str:
    """The Frame's header (frontmatter) kept on the document, written back in its usual order."""
    import json

    order = ["type", "name", "description", "visibility", "version", "scope", "maintainer", "inherits", "clauses", "compile"]
    keys = [k for k in order if k in meta] + [k for k in meta if k not in order]
    lines = ["---"]
    for k in keys:
        lines.append(f"{k}: {json.dumps(meta[k], ensure_ascii=False, default=str)}")  # JSON is YAML: every value stays exact
    lines.append("---")
    return "\n".join(lines)


def frame_text(hub, rec: dict, version: int | None = None) -> str:
    """A policy or SOP document read back as Frame text: its header from the document, its body from the file."""
    content, v = hub.dms.content(rec["locator"], version)
    ext = (v.get("filename") or "x.docx").rsplit(".", 1)[-1].lower()
    lines = docx_lines(content, ext) if ext in ("docx", "odt") else body_lines(content.decode("utf-8", "replace"))
    head = rec["fields"].get("frame_header") or header_text(dict(rec["fields"].get("frame_meta") or {}))
    while lines and not lines[-1].strip():
        lines.pop()
    return head + "\n" + "\n".join(lines) + "\n" * int(rec["fields"].get("frame_tail") or 1)


def of_lineage(kernel, name: str) -> list[dict]:
    return [d for d in kernel.query(type_="document") if d["fields"].get("lineage") == LINEAGE + name]


def in_force(kernel, name: str) -> dict | None:
    return next((d for d in of_lineage(kernel, name) if d["state"] == "effective"), None)


def converted(kernel) -> bool:
    """True once the SOP documents are the source (any Frame has a document in force)."""
    return any(d["fields"].get("lineage", "").startswith(LINEAGE) and d["state"] == "effective" for d in kernel.query(type_="document"))


def convert(hub, *, by: str, frames=None) -> list[dict]:
    """Make each Frame a policy, SOP or work-instruction document, revision A, in force (the text is the release in
    force, already approved as a Frame). Idempotent: a Frame that already has its document is left alone."""
    store = frames or hub.released_frames()
    made = []
    meta = hub.releases.current()
    for name in sorted(store.frames):
        if of_lineage(hub.kernel, name):
            continue
        frame = store.get(name)
        doc_type, folder = kind_of(frame)
        content = to_docx(frame.body)
        fields = {"lineage": LINEAGE + name, "sop_name": name, "frame_meta": dict(frame.frontmatter),
                  "frame_header": "\n".join((frame.text or "").splitlines()[: max(0, frame.body_start - 1)]),
                  "frame_tail": max(1, len(frame.text) - len(frame.text.rstrip("\n"))),
                  "revision": "A", "doc_type": doc_type, "frame_mirror": False, "description": frame.description,
                  "converted_from": {"frame": frame.relpath, "version": frame.version, "release": meta["digest"] if meta else "",
                                     "approved": "released by the policy owners in that release"}}
        mirror = next((d for d in hub.kernel.query(type_="document")
                       if is_frame_doc(d["fields"]) and d["fields"].get("frame_name") == name and d["state"] == "effective"), None)
        if mirror is not None:
            # The document that carried this Frame becomes the SOP itself: same number and history, now a .docx source.
            hub.dms.add_version(mirror["locator"], content, by=by, filename=f"{name}.docx",
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                note="the SOP as the document the Frame is compiled from", system=True)
            if mirror["folder"] != folder:
                hub.kernel.move(mirror["locator"], folder, by=by, reason="an SOP is a quality document")
            hub.kernel.set_fields(mirror["locator"], {**fields, "title": title_of(frame), "owner": mirror["fields"].get("owner") or frame.maintainer or by},
                                  by=by)
            made.append(hub.kernel.get(mirror["locator"]))
            continue
        doc = hub.dms.create(title=title_of(frame), doc_class="controlled", folder=folder, by=by, content=content,
                             filename=f"{name}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                             doc_type=doc_type,
                             fields={"lineage": LINEAGE + name, "sop_name": name, "frame_meta": dict(frame.frontmatter),
                                     "frame_header": "\n".join((frame.text or "").splitlines()[: max(0, frame.body_start - 1)]),
                                     "frame_tail": max(1, len(frame.text) - len(frame.text.rstrip("\n"))),
                                     "revision": "A", "owner": frame.maintainer or by, "description": frame.description,
                                     "review_date": (date.today() + timedelta(days=365)).isoformat(),
                                     "converted_from": {"frame": frame.relpath, "version": frame.version,
                                                        "release": meta["digest"] if meta else ""}})
        rec = hub.kernel.transition(doc["locator"], "effective", by=by,
                                    reason=f"converted from the {name} Frame (version {frame.version}), in force under the release in effect")
        made.append(rec)
    return made


def source_texts(hub, drafts: dict[str, str] | None = None) -> dict[str, str]:
    """Every Frame's text from its document in force, or from a draft revision in ``drafts`` ({frame name: draft
    locator}, the SOP revisions an ECO carries)."""
    out = {}
    drafts = drafts or {}
    names = {d["fields"]["sop_name"] for d in hub.kernel.query(type_="document") if str(d["fields"].get("lineage", "")).startswith(LINEAGE)}
    for name in sorted(names):
        rec = hub.kernel.get(drafts[name]) if name in drafts else in_force(hub.kernel, name)
        if rec is not None:
            out[name] = frame_text(hub, rec)
    return out


def roundtrip_problems(hub, frames=None) -> list[str]:
    """Frames whose text would not read back exactly from the document they become (nothing is written). Empty means
    converting changes no rule and no wording."""
    store = frames or hub.released_frames()
    problems = []
    for name in sorted(store.frames):
        frame = store.get(name)
        lines = docx_lines(to_docx(frame.body))
        while lines and not lines[-1].strip():
            lines.pop()
        head = "\n".join((frame.text or "").splitlines()[: max(0, frame.body_start - 1)])
        tail = max(1, len(frame.text) - len(frame.text.rstrip("\n")))
        if head + "\n" + "\n".join(lines) + "\n" * tail != frame.text:
            problems.append(name)
    return problems


def conversion_blockers(hub) -> list[str]:
    """Why the SOPs cannot be converted now (empty when they can)."""
    meta = hub.releases.current()
    if not meta:
        return ["no policy release is in force"]
    bundle = hub.releases.bundle(meta["digest"])
    out = []
    if converted(hub.kernel):
        out.append("the SOPs are documents already")
    if "change-order" not in (bundle.get("ops") or {}):
        out.append("the release in force has no change-order process: without it no SOP could change after converting. "
                   "Release the policy that adds it first (change-control-sop with the change-order process)")
    bad = roundtrip_problems(hub)
    if bad:
        out.append("these Frames would not read back exactly from their documents: " + ", ".join(bad))
    return out


def convert_from_ui(hub, *, by: str) -> dict:
    """Convert, for a policy owner, after the checks; the other policy owners are told. Returns {"made", "changes"}."""
    from . import authz

    bundle = hub.releases.bundle(hub.releases.current()["digest"])
    if "policy-owner" not in authz.roles_of(bundle, by):
        raise ValueError("only a policy owner converts the SOPs to documents")
    blockers = conversion_blockers(hub)
    if blockers:
        raise ValueError("; ".join(blockers))
    made = convert(hub, by=by)
    changes = hub.compile(save=False)["changes"]
    for owner in sorted(p for p in bundle.get("members") or {} if "policy-owner" in authz.roles_of(bundle, p) and p != by):
        hub.inbox.notify(owner, kind="policy", subject="The SOPs are documents now",
                         body=f"{by} converted {len(made)} Frames into policy, SOP and work-instruction documents; the Frame is compiled from them. "
                              "Rules now change through change orders that carry SOP revisions.")
    return {"made": made, "changes": len(changes)}

