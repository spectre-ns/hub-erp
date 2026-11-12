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

from .frames import Frame, header_of

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


def to_docx(body: str, pictures: dict[str, dict] | None = None) -> bytes:
    """The SOP as a .docx: '#' lines as headings (Heading 1 to 3, the editor's styles), every other line as its own
    paragraph with its text exactly as written (indentation, list marks and table rows included), so the document
    reads back as the same Frame text. Formatting people add later in Collabora is theirs.

    A picture line (``office.picture_line``) is that picture, in a paragraph of its own: ``pictures`` holds them
    (``pictures_of`` the document the text was read from), and a picture line whose picture is not there is refused,
    never written as words (a revision would lose the picture and read back the same)."""
    from html import escape

    from . import office
    from .wopi import blank_document

    blank, _ = blank_document("docx")
    paras = []
    lines = body_lines(body)
    tables = markdown_tables(lines)
    writer = office.DocxPictureWriter(pictures)
    skip_to = -1
    for i, line in enumerate(lines):
        if i < skip_to:
            continue
        if i in tables:  # a Markdown table (the Roles and responsibilities table) as a Word table: it reads back as the same rows
            end = tables[i]
            paras.append(_docx_table([lines[i]] + lines[i + 2:end]))
            if end >= len(lines) or not any(x.strip() for x in lines[end:]):
                paras.append("<w:p/>")  # a body never ends with a table
            skip_to = end
            continue
        m = re.match(r"^(#{1,6}) (.*)$", line)
        pic = office.PICTURE_LINE.fullmatch(line)
        if office.is_reading_line(line):
            continue  # a picture's confirmed reading is the hub's (picture_reading), never words of the document
        if m:
            level = min(len(m.group(1)), 3) if len(m.group(1)) <= 3 else len(m.group(1))
            paras.append(f'<w:p><w:pPr><w:pStyle w:val="Heading{level}"/></w:pPr><w:r><w:t xml:space="preserve">{escape(m.group(2), quote=False)}</w:t></w:r></w:p>')
        elif pic:
            paras.append(writer.paragraph(i + 1, pic.group(1), pic.group(2)))
        elif line:
            paras.append(f'<w:p><w:r><w:t xml:space="preserve">{escape(office.unescape_text_line(line), quote=False)}</w:t></w:r></w:p>')
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
            z.writestr(item, writer.finish(item.filename, data))  # what the pictures need (nothing without pictures)
        for name, data in sorted(writer.media.items()):
            z.writestr(name, data)
    return out.getvalue()


_ROW = re.compile(r"^\|(.+)\|$")
_RULE = re.compile(r"^\|(?:---\|)+$")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip()[1:-1].split("|")]


def markdown_tables(lines: list[str]) -> dict[int, int]:
    """{first line: line after the table} for each Markdown table written as docx_lines reads a Word table back: a
    header row, the rule '|---|---|' (one '---|' per cell), then rows of as many cells, every cell non-empty. Anything
    else stays paragraphs, so it reads back exactly as written."""
    out, i = {}, 0
    while i < len(lines) - 1:
        head = lines[i]
        if _ROW.match(head) and not _RULE.match(head) and lines[i + 1] == "|" + "---|" * len(_cells(head)) \
                and all(_cells(head)) and head == "| " + " | ".join(_cells(head)) + " |":
            n, j = len(_cells(head)), i + 2
            while j < len(lines) and _ROW.match(lines[j]) and not _RULE.match(lines[j]) and len(_cells(lines[j])) == n \
                    and all(_cells(lines[j])) and lines[j] == "| " + " | ".join(_cells(lines[j])) + " |":
                j += 1
            out[i] = j
            i = j
            continue
        i += 1
    return out


def _docx_table(rows: list[str]) -> str:
    from html import escape

    border = "".join(f'<w:{b} w:val="single" w:sz="4" w:space="0" w:color="999999"/>' for b in ("top", "left", "bottom", "right", "insideH", "insideV"))
    n = len(_cells(rows[0]))
    out = [f'<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders>{border}</w:tblBorders></w:tblPr><w:tblGrid>'
           + "<w:gridCol/>" * n + "</w:tblGrid>"]
    for k, row in enumerate(rows):
        bold = "<w:rPr><w:b/></w:rPr>" if k == 0 else ""
        out.append("<w:tr>" + "".join(f'<w:tc><w:p><w:r>{bold}<w:t xml:space="preserve">{escape(c, quote=False)}</w:t></w:r></w:p></w:tc>'
                                      for c in _cells(row)) + "</w:tr>")
    return "".join(out) + "</w:tbl>"


def docx_lines(content: bytes, ext: str = "docx", pictures: bool = True) -> list[str]:
    """A document read back as Frame lines: headings as '#' lines, each paragraph as a line (its text as written;
    a Word bullet as '- '), tables as '| a | b |' rows, and each picture as a line of its own after the paragraph,
    heading or table it is in (``office.picture_line``: its description and its hash), with one more line at the end
    for the parts of the file with bytes that no picture line covers (``office.PictureReader``). A line of words
    written like a picture line reads with a backslash in front (``office.escape_text``).

    ``pictures=False`` reads as the hub did before pictures had lines (text only): what a release made then read."""
    from . import office
    from .edits import TextDoc

    doc = TextDoc(content, ext)
    per, tail = office.PictureReader(doc.files, ext).read(doc.blocks) if pictures else ([[] for _ in doc.blocks], [])
    words = office.escape_text if pictures else (lambda t: t)
    out: list[str] = []
    for b, pics in zip(doc.blocks, per):
        found = [office.picture_line(p["alt"], p["ref"]) for p in pics]
        if b.kind == "h":
            out.append(words("#" * max(1, min(b.level, 6)) + " " + b.text.strip()))
        elif b.kind == "tbl":
            rows = [r for r in b.text.split("\n") if r.strip()]
            for i, r in enumerate(rows):
                out.append(words("| " + r + " |"))
                if i == 0:
                    out.append("|" + "---|" * (r.count("|") + 1))
        elif b.kind in ("p", "other"):
            t = b.text.rstrip()
            if not t.strip():
                out.extend(found or [""])  # a paragraph that holds only a picture is the picture's line
                continue
            if t.lstrip().startswith("• "):
                t = "- " + t.lstrip()[2:].strip()
            elif "<w:numPr>" in (b.raw or "") and not re.match(r"^\s*(?:[-*]|\d+[.)]) ", t):
                t = "- " + t.strip()  # a Word list item someone added: a bullet line
            out.append(words(t))
        out.extend(found)
    out.extend(office.picture_line(p["alt"], p["ref"]) for p in tail)
    return out


def pictures_of(content: bytes, ext: str = "docx") -> dict[str, dict]:
    """{picture ref ("sha256:<hex>" or "link:<address>"): the picture} for every picture line of a document
    (``office.PictureReader``): what ``to_docx`` needs to write a revision's pictures back. A picture that appears twice
    is writable only if both places are."""
    from . import office
    from .edits import TextDoc

    if ext not in ("docx", "odt"):
        return {}
    doc = TextDoc(content, ext)
    per, tail = office.PictureReader(doc.files, ext).read(doc.blocks)
    out: dict[str, dict] = {}
    for p in [p for ps in per for p in ps] + tail:
        if p["ref"] in out:
            out[p["ref"]]["writable"] = out[p["ref"]]["writable"] and p["writable"]
        else:
            out[p["ref"]] = p
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


def frame_text(hub, rec: dict, version: int | None = None, pictures: bool = True) -> str:
    """A policy or SOP document read back as Frame text: its header from the document, its body from the file.
    ``pictures=False``: as the hub read it before pictures had lines (``docx_lines``)."""
    from .office import escape_text_line

    content, v = hub.dms.content(rec["locator"], version)
    ext = (v.get("filename") or "x.docx").rsplit(".", 1)[-1].lower()
    if ext in ("docx", "odt"):
        lines = docx_lines(content, ext, pictures=pictures)
    else:  # a text file holds no pictures: words written like a picture line are words
        lines = [escape_text_line(x) if pictures else x for x in body_lines(content.decode("utf-8", "replace"))]
    head = rec["fields"].get("frame_header") or header_text(dict(rec["fields"].get("frame_meta") or {}))
    while lines and not lines[-1].strip():
        lines.pop()
    return head + "\n" + "\n".join(lines) + "\n" * int(rec["fields"].get("frame_tail") or 1)


def in_force_text(hub, rec: dict, released: dict[str, str] | None) -> str:
    """The Frame text of a document in force that no change order carries. Read as the hub reads documents now, with the
    confirmed readings of its pictures in force (``picture_reading.with_readings``), unless the only difference from the
    release in force (``released``: {name: text}) is what the hub reads now that it did not when that release was made
    (its pictures' lines): then the release's text stands, so a new reading never changes an SOP no change order
    carries. The pictures come in with the next revision of the SOP (its draft is read in full, and a picture in it is a
    question for a person)."""
    from .picture_reading import readings_in, with_readings

    old = (released or {}).get(rec["fields"].get("sop_name"))
    text = with_readings(frame_text(hub, rec), readings_in(old or ""))
    if old is not None and text != old and frame_text(hub, rec, pictures=False) == old:
        return old
    return text


def draft_text(hub, rec: dict, released: dict[str, str] | None, readings: dict[str, list[str]] | None = None) -> str:
    """The Frame text of a draft a change order carries: the document's text, with the reading in force of each picture
    it keeps (an unchanged picture keeps its confirmed reading and asks nothing) and the readings this change order
    confirmed (``readings``: {picture ref: sentences}) after their pictures."""
    from .picture_reading import readings_in, with_readings

    old = (released or {}).get(rec["fields"].get("sop_name")) or ""
    return with_readings(frame_text(hub, rec), {**readings_in(old), **(readings or {})})


def picture_images(hub, name: str, ref: str) -> list[dict] | None:
    """The images of the picture ``ref`` ("sha256:<hex>") of the SOP ``name``, from its documents (the drafts and the one in
    force), as a model is sent them: [{"data", "media_type"}], only the kinds a model takes (``model.IMAGE_TYPES``, known by
    their bytes, not their name). [] when the picture is there but none of its images can be sent; None when no document
    of the SOP holds it (the document's own picture reader names it ``ref``, from the same bytes)."""
    for d in of_lineage(hub.kernel, name):
        try:
            content, v = hub.dms.content(d["locator"])
        except Exception:
            continue
        ext = (v.get("filename") or "x.docx").rsplit(".", 1)[-1].lower()
        try:
            pic = pictures_of(content, ext).get(ref)
        except Exception:
            continue
        if pic is None:
            continue
        images = []
        for m in pic.get("members") or []:
            data = m.get("data")
            kind = image_type(data) if data else None
            if kind:
                images.append({"data": data, "media_type": kind})
        return images
    return None


def image_type(data: bytes) -> str | None:
    """The media type of a picture a model can be sent, from its first bytes (PNG, JPEG, GIF, WebP); None otherwise."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _released_texts(hub) -> dict[str, str]:
    meta = hub.releases.current()
    if not meta:
        return {}
    texts = dict(hub.releases.frame_texts(meta["digest"]))
    for f in meta.get("frames") or []:
        if f.get("name") and f["name"] not in texts:
            t = hub.releases.text_by_sha(f.get("sha256"))
            if t is not None:
                texts[f["name"]] = t
    return texts


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
                  "frame_header": header_of(frame.text, frame.body_start),
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
                                     "frame_header": header_of(frame.text, frame.body_start),
                                     "frame_tail": max(1, len(frame.text) - len(frame.text.rstrip("\n"))),
                                     "revision": "A", "owner": frame.maintainer or by, "description": frame.description,
                                     "review_date": (date.today() + timedelta(days=365)).isoformat(),
                                     "converted_from": {"frame": frame.relpath, "version": frame.version,
                                                        "release": meta["digest"] if meta else ""}})
        rec = hub.kernel.transition(doc["locator"], "effective", by=by,
                                    reason=f"converted from the {name} Frame (version {frame.version}), in force under the release in effect")
        made.append(rec)
    if made:  # from now on configuration changes only through change orders (hubproto.closure)
        from . import closure

        closure.on_conversion(hub)
    return made


def source_texts(hub, drafts: dict[str, str] | None = None) -> dict[str, str]:
    """Every Frame's text from its document in force, or from a draft revision in ``drafts`` ({frame name: draft
    locator}, the SOP revisions an ECO carries)."""
    out = {}
    drafts = drafts or {}
    released = _released_texts(hub)
    names = {d["fields"]["sop_name"] for d in hub.kernel.query(type_="document") if str(d["fields"].get("lineage", "")).startswith(LINEAGE)}
    for name in sorted(names):
        if name in drafts and drafts[name] is None:  # retired by the change order: compiled as absent
            continue
        rec = hub.kernel.get(drafts[name]) if name in drafts else in_force(hub.kernel, name)
        if rec is not None:
            out[name] = draft_text(hub, rec, released) if name in drafts else in_force_text(hub, rec, released)
    return out


# ---------------------------------------------------------------- the Frames, once the SOPs are documents
def _store_of(hub, frames: dict[str, tuple[str, str]]):
    """A Frame store made of texts, never of the files on disk: {name: (text, path relative to the Frames root)}."""
    from pathlib import Path

    from .frames import FrameError, FrameStore, load_frame_text

    root = Path(hub._frames_dir)
    store = object.__new__(FrameStore)
    store.root = root
    store.frames = {}
    for name, (text, rel) in sorted(frames.items()):
        try:
            f = load_frame_text(text, root / rel)
        except FrameError:
            raise
        except Exception as e:
            raise FrameError(f"{rel}: {e}") from None
        if f.name != name:
            raise FrameError(f"the document of {name} reads as the Frame {f.name!r}; a Frame keeps its name")
        f.relpath = rel
        store.frames[name] = f
    return store


def _relpath(hub, name: str, rec: dict | None, bom: dict) -> str:
    """Where a Frame lives under frames/: as the release in force has it, as its document says, or a new place."""
    if name in bom and bom[name].get("path"):
        return bom[name]["path"]
    seen = set()
    while rec is not None and rec["locator"] not in seen:  # a revision's place is its own, or the one it revises has
        seen.add(rec["locator"])
        f = rec.get("fields") or {}
        rel = f.get("frame_relpath") or f.get("frame_path") or ((f.get("converted_from") or {}).get("frame") if isinstance(f.get("converted_from"), dict) else None)
        if rel:
            return rel
        rec = hub.kernel.get(f["revises"]) if f.get("revises") else None
    return f"documents/{name}.frame.md"


def released_store(hub, digest: str | None = None):
    """The Frames exactly as a release has them (its bill of materials and the texts it kept), for a hub whose SOPs
    are documents: the files on disk decide nothing. Documents in force that no release compiles (reference guides)
    are read from the document. None when the release kept no texts (an old release)."""
    meta = hub.releases.current() if digest is None else hub.releases.get(digest)
    if not meta:
        return None
    texts = hub.releases.frame_texts(meta["digest"])
    bom = {f["name"]: f for f in meta.get("frames") or []}
    frames = {}
    for name, entry in bom.items():
        text = texts.get(name) or hub.releases.text_by_sha(entry.get("sha256"))
        if text is None:
            return None
        frames[name] = (text, entry.get("path") or f"documents/{name}.frame.md")
    for d in hub.kernel.query(type_="document"):
        name = d["fields"].get("sop_name")
        if name and name not in frames and d["state"] == "effective" and str(d["fields"].get("lineage", "")).startswith(LINEAGE):
            frames[name] = (frame_text(hub, d), _relpath(hub, name, d, bom))
    return _store_of(hub, frames)


def document_store(hub, drafts: dict[str, str | None] | None = None, readings: dict[str, dict[str, list[str]]] | None = None):
    """The Frames a compile reads once the SOPs are documents: one per document in force, each read from it, with the
    drafts a change order carries in place of theirs (``eco.drafts_of``: {name: draft locator}; a draft of an SOP with no
    document in force, ``eco.add_new_sop``, adds that Frame; a name with None, ``eco.retire_sop``, takes it out).
    Never the disk listing. ``readings`` ({name: {picture ref: sentences}}): the pictures' readings the change order
    confirmed, after their pictures in its drafts' text (``draft_text``)."""
    drafts = dict(drafts or {})
    bom = {f["name"]: f for f in (hub.releases.current() or {}).get("frames") or []}
    frames = {}
    released: dict[str, str] | None = None
    names = {d["fields"]["sop_name"] for d in hub.kernel.query(type_="document")
             if str(d["fields"].get("lineage", "")).startswith(LINEAGE) and d["state"] == "effective"} | set(drafts)
    for name in sorted(names):
        if name in drafts and drafts[name] is None:
            continue  # retired by the change order
        rec = hub.kernel.get(drafts[name]) if name in drafts else in_force(hub.kernel, name)
        if rec is None:
            continue
        if released is None:
            released = _released_texts(hub)
        text = draft_text(hub, rec, released, (readings or {}).get(name)) if name in drafts else in_force_text(hub, rec, released)
        frames[name] = (text, _relpath(hub, name, rec, bom))
    return _store_of(hub, frames)


def roundtrip_problems(hub, frames=None) -> list[str]:
    """Frames whose text would not read back exactly from the document they become (nothing is written). Empty means
    converting changes no rule and no wording."""
    store = frames or hub.released_frames()
    problems = []
    for name in sorted(store.frames):
        frame = store.get(name)
        try:
            lines = docx_lines(to_docx(frame.body))
        except ValueError:  # a picture line with no picture to write
            problems.append(name)
            continue
        while lines and not lines[-1].strip():
            lines.pop()
        head = header_of(frame.text, frame.body_start)
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


# ---------------------------------------------------------------- the first release converts
FRAME_FILES_KEPT = "frame-files-kept.json"  # state/: the explicit opt-out below


def _kept_path(hub):
    from pathlib import Path

    return Path(hub.root) / "state" / FRAME_FILES_KEPT


def frame_files_kept(hub) -> dict | None:
    """{"by", "at", "reason"} when this hub was told to keep the Frame files as the source (no conversion after the
    first release); None otherwise. Only meaningful before conversion."""
    import json

    path = _kept_path(hub)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except ValueError:
        return {"by": "?", "at": "", "reason": f"{path.name} does not parse"}


def keep_frame_files(hub, *, by: str, reason: str) -> dict:
    """Opt out, explicitly and on the record, of converting the SOPs right after the first release: the Frame files
    stay the source and the policy owners release changes without change orders (the pre-conversion mode, for working
    on the Frames themselves and for the suites that test that workflow). ``status`` says so. Converting later
    (``sops convert``) ends it. Refused once the SOPs are documents: there is no way back."""
    import json

    from .release import utcnow

    if converted(hub.kernel):
        raise ValueError("the SOPs are documents already: configuration changes only through change orders")
    if not (reason or "").strip():
        raise ValueError("say why the Frame files stay the source (it is shown with the hub's status)")
    entry = {"by": by, "at": utcnow(), "reason": reason.strip()}
    path = _kept_path(hub)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, indent=1), encoding="utf-8")
    return entry


def convert_after_release(hub, *, by: str) -> dict:
    """Bootstrap: right after the first release, the SOPs become documents, so every later change needs a change order.
    Not when the hub keeps the Frame files (``keep_frame_files``). Returns {"made": [documents], "blockers": [why not],
    "kept": the opt-out or None}; a blocked conversion leaves the hub as it is, and ``status`` and ``bootstrap-release``
    say why until it is converted."""
    kept = frame_files_kept(hub)
    if kept or converted(hub.kernel):
        return {"made": [], "blockers": [], "kept": kept}
    blockers = conversion_blockers(hub)
    if blockers:
        return {"made": [], "blockers": blockers, "kept": None}
    return {"made": convert(hub, by=by), "blockers": [], "kept": None}


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

