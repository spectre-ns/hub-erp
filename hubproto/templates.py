"""Controlled document templates: the profile's Markdown templates as documents in the library.

A template (an NCR form, a CAPA form, a change request...) is a document of class ``template``: it lives in the
library beside the SOPs, is edited in the online editor, and goes through document-control like any controlled
document (draft, approval, effective). Effective templates are read-only; a change is a new revision that
supersedes the earlier revision of the same template when it becomes effective.

The sources are the Markdown files under ``frames/<profile>/templates/``. Each becomes a .docx (headings, lists,
tables and the ``{{placeholders}}`` kept as plain text) keyed by its lineage, ``<profile>/<file name>``, never by
its title. Importing is idempotent: a source already imported with the same content does nothing; a changed source
updates the template's open draft, or, when the template is in force, is offered as a new revision (a draft that
revises it). Nothing is ever duplicated.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .dms import next_revision
from .office import insert_paragraphs
from .wopi import blank_document

FOLDER = "Quality/Controlled Documents"  # where document-control files controlled documents
DOC_TYPE = "template"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
EDITABLE = ("open", "draft", "returned", "failed")
GONE = ("obsolete", "withdrawn", "cancelled", "rejected")
PLACEHOLDER = re.compile(r"\{\{\s*([\w.-]+)\s*\}\}")


# ---------------------------------------------------------------- sources
def sources(frames_root: Path) -> list[Path]:
    """The template sources the Frames carry: frames/<profile>/templates/*.md."""
    return sorted(Path(frames_root).glob("*/templates/*.md"))


def lineage_of(path: Path) -> str:
    """A template's identity across revisions: its profile and file name (iso9001/ncr.md)."""
    path = Path(path)
    return f"{path.parent.parent.name}/{path.name}"


def title_of(md: str, fallback: str) -> str:
    m = re.search(r"(?m)^#\s+(.+?)\s*$", md)
    return m.group(1).strip() if m else fallback


def record_type_of(md: str) -> str:
    """The record type a template is the form for, when it says so ("Record type `ncr`")."""
    m = re.search(r"[Rr]ecord type `([\w-]+)`", md)
    return m.group(1) if m else ""


def placeholders(text: str) -> list[str]:
    seen: list[str] = []
    for name in PLACEHOLDER.findall(text or ""):
        if name not in seen:
            seen.append(name)
    return seen


def markdown_lines(md: str) -> list[str]:
    """The lines the docx writer takes: one per heading, list item, table row or paragraph. Soft-wrapped lines of a
    paragraph are joined, blank lines dropped, inline code marks removed (the text stays)."""
    out: list[str] = []
    para: list[str] = []

    def flush():
        if para:
            out.append(" ".join(para))
            para.clear()

    for raw in (md or "").splitlines():
        line = raw.rstrip().replace("`", "")
        s = line.strip()
        if not s:
            flush()
            continue
        block = s.startswith(("#", "|", "- ", "* ", "• ")) or re.match(r"^\d+[.)] ", s) or s == "---"
        if block:
            flush()
            out.append(s)
        else:
            para.append(s)
    flush()
    return out


def to_docx(md: str) -> bytes:
    """A .docx of the Markdown template, with the editor's heading, list and table styles."""
    blank, _ = blank_document("docx")
    content = insert_paragraphs(blank, "docx", markdown_lines(md), "replace")
    if content is None:
        raise ValueError("could not write the template as a .docx")
    return content


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- the library
def of_lineage(kernel, lineage: str) -> list[dict]:
    """Every document of a template's lineage, oldest first (all revisions, in force or not)."""
    docs = [d for d in kernel.query(type_="document") if (d["fields"].get("template_source") == lineage)]
    return sorted(docs, key=lambda d: (d.get("created_at") or "", d["number"]))


def templates(kernel) -> list[dict]:
    """The template documents in the library, grouped by lineage: [{lineage, title, effective, open, revisions}]."""
    groups: dict[str, list[dict]] = {}
    for d in kernel.query(type_="document"):
        f = d["fields"]
        if f.get("doc_class") != "template":
            continue
        groups.setdefault(f.get("template_source") or d["number"], []).append(d)
    out = []
    for lineage, docs in groups.items():
        docs.sort(key=lambda d: (d.get("created_at") or "", d["number"]))
        effective = next((d for d in reversed(docs) if d["state"] == "effective"), None)
        current = next((d for d in reversed(docs) if d["state"] not in GONE and d is not effective and d["state"] != "effective"), None)
        latest = current or effective or docs[-1]
        out.append({"lineage": lineage, "title": latest["fields"].get("title") or lineage, "effective": effective, "open": current,
                    "revisions": docs, "for": latest["fields"].get("template_for") or ""})
    return sorted(out, key=lambda g: g["title"].lower())


def import_templates(hub, *, by: str, paths: list[Path] | None = None, folder: str = FOLDER, submit: bool = False) -> list[dict]:
    """Bring the template sources into the library as controlled templates. Returns one line per source:
    {"lineage", "action", "number", "why"} where action is created, revised (a new revision of the one in force),
    updated (a new version of its open draft), unchanged, or held (in approval now: offered once it is decided).
    With ``submit`` each new or changed draft is sent through document-control by ``by`` (who must hold the role
    that starts it)."""
    report = []
    for path in paths if paths is not None else sources(hub.frames.root):
        path = Path(path)
        md = path.read_text(encoding="utf-8")
        lineage, sha = lineage_of(path), _sha(md)
        title = title_of(md, path.stem.replace("-", " ").capitalize())
        meta = {"template_source": lineage, "template_source_sha": sha, "template_for": record_type_of(md),
                "placeholders": placeholders(md)}
        docs = [d for d in of_lineage(hub.kernel, lineage) if d["state"] not in GONE]
        live = [d for d in docs if d["state"] != "effective"]
        effective = next((d for d in reversed(docs) if d["state"] == "effective"), None)
        line = {"lineage": lineage, "title": title}
        filename = path.with_suffix(".docx").name
        if any(d["fields"].get("template_source_sha") == sha for d in (live or ([effective] if effective else []))):
            report.append({**line, "action": "unchanged", "number": (live or [effective])[-1]["number"], "why": "already imported"})
            continue
        if live:
            draft = live[-1]
            if draft["state"] not in EDITABLE:
                report.append({**line, "action": "held", "number": draft["number"],
                               "why": f"{draft['number']} is {draft['state']}; import again once it is decided"})
                continue
            hub.dms.add_version(draft["locator"], to_docx(md), by=by, filename=filename, mime=DOCX_MIME,
                                note=f"template source {lineage} changed")
            hub.kernel.set_fields(draft["locator"], meta, by=by)
            rec = hub.kernel.get(draft["locator"])
            report.append({**line, "action": "updated", "number": rec["number"], "why": "the source changed: a new version of the draft"})
        else:
            fields = dict(meta)
            if effective:
                fields |= {"revises": effective["number"], "revision": next_revision(effective["fields"].get("revision") or "A")}
            rec = hub.dms.create(title=title, doc_class="template", folder=folder, by=by, content=to_docx(md), filename=filename,
                                 mime=DOCX_MIME, doc_type=DOC_TYPE, fields=fields,
                                 provenance={"source": f"frames/{lineage}", "imported_by": by})
            report.append({**line, "action": "revised" if effective else "created", "number": rec["number"],
                           "why": f"a new revision of {effective['number']}" if effective else "new template"})
        if submit:
            try:
                hub.runner.submit("document-control", {"record": {"change_type": "major"}}, owner=by, record=rec["locator"])
                report[-1]["why"] += "; sent for approval"
            except Exception as e:  # the import stands; the person sends it for approval from the library
                report[-1]["why"] += f"; not sent for approval: {e}"
    return report


def may_revise(bundle: dict, rec: dict, by: str) -> str:
    """Why ``by`` may not start a new revision of a template (empty when they may): its owner or author, or a
    document controller, who may also file documents in its folder."""
    from . import authz

    f = rec["fields"]
    if f.get("doc_class") != "template":
        return f"{rec['number']} is not a template"
    if by not in (f.get("owner"), f.get("author")) and "document-controller" not in authz.roles_of(bundle, by):
        return f"only its owner ({f.get('owner') or f.get('author')}) or a document controller starts a new revision of {rec['number']}"
    if not authz.check(bundle, by, "create", "document", rec["folder"])["allowed"]:
        return f"{by} may not file documents in {rec['folder']}"
    return ""


def new_revision(hub, ref: str, *, by: str, bundle: dict) -> dict:
    """A new revision of a template in force: a draft with the effective version's file, the next revision letter,
    the same lineage and ``revises`` naming it, ready to edit. An open revision of the lineage is returned instead of
    a second one. Raises ValueError with the reason when it may not."""
    rec = hub.kernel.require(ref)
    if rec["state"] != "effective":
        raise ValueError(f"{rec['number']} is {rec['state']}: edit it as it is; a new revision is made from the template in force")
    why = may_revise(bundle, rec, by)
    if why:
        raise ValueError(why)
    f = rec["fields"]
    lineage = f.get("template_source") or f"library/{rec['number']}"
    open_ = [d for d in of_lineage(hub.kernel, lineage) if d["state"] not in GONE and d["state"] != "effective"]
    if open_:
        return open_[-1]
    content, version = hub.dms.content(rec["locator"])
    fields = {"template_source": lineage, "template_source_sha": f.get("template_source_sha"), "template_for": f.get("template_for") or "",
              "placeholders": placeholders(hub.dms.text_of(rec)), "revises": rec["number"],
              "revision": next_revision(f.get("revision") or "A")}
    if not f.get("template_source"):
        hub.kernel.set_fields(rec["locator"], {"template_source": lineage}, by=by)
    return hub.dms.create(title=f.get("title") or rec["number"], doc_class="template", folder=rec["folder"], by=by, content=content,
                          filename=version.get("filename") or f.get("filename") or f"{rec['number']}.docx",
                          mime=version.get("mime") or f.get("mime") or DOCX_MIME, doc_type=f.get("doc_type") or DOC_TYPE, fields=fields,
                          provenance={"revision_of": rec["number"], "by": by})
