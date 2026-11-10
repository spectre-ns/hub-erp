"""Reading office files: the text and the comments inside a docx, xlsx, odt or ods, so a review can read the
document and a comment written in the editor ("@quality-assistant can you review this?") reaches the chatter."""

from __future__ import annotations

import html
import io
import re
import zipfile
from xml.etree import ElementTree

from .rules import sha256_of

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
DC = "{http://purl.org/dc/elements/1.1/}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


MAX_MEMBER = 64 * 1024 * 1024     # one part of an office package, inflated
MAX_PACKAGE = 256 * 1024 * 1024   # all parts together, inflated
MAX_RATIO = 200                   # inflated size over compressed size, for packages above a few megabytes


class PackageTooLarge(ValueError):
    """An office package that would inflate far beyond its size (a zip bomb): it is not read."""


def _zip(content: bytes) -> zipfile.ZipFile | None:
    """The package, after checking what it would inflate to from its directory (nothing is inflated to check)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return None
    sizes = [i.file_size for i in z.infolist()]
    total = sum(sizes)
    if (sizes and max(sizes) > MAX_MEMBER) or total > MAX_PACKAGE or (total > 8 * 1024 * 1024 and total > MAX_RATIO * max(1, len(content))):
        raise PackageTooLarge(f"the file would inflate to {total / 1e6:,.0f} MB; it is not opened (a part over {MAX_MEMBER // 2**20} MB, "
                              f"over {MAX_PACKAGE // 2**20} MB in all, or compressed more than {MAX_RATIO} to 1)")
    return z


def _zip_safe(content: bytes) -> zipfile.ZipFile | None:
    """For readers: an oversized package is treated as unreadable (its text is not extracted), never inflated."""
    try:
        return _zip(content)
    except PackageTooLarge:
        return None


def _itertext(el) -> str:
    return "".join(el.itertext())


def _own_text(el, paragraph_tags: tuple, text_tag: str | None = None) -> str:
    """A paragraph's own text, leaving out paragraphs nested inside it (a textbox, a frame, a nested table, a note):
    those are paragraphs of their own and are read once, as themselves."""
    out = []

    def walk(e, top):
        if not top and e.tag in paragraph_tags:
            return
        if text_tag is None or e.tag == text_tag:
            out.append(e.text or "")
        for c in e:
            walk(c, False)
            if text_tag is None:
                out.append(c.tail or "")  # text after a child belongs to this element

    walk(el, True)
    return "".join(out)


def extract_text(content: bytes, extension: str, limit: int = 20000) -> str:
    """Plain text of the document body, paragraph per line; empty when the format is not one we read."""
    ext = (extension or "").lower()
    if ext in ("txt", "md", "csv"):
        return content.decode("utf-8", "replace")[:limit]
    z = _zip_safe(content)
    if z is None:
        return ""
    try:
        if ext == "docx" and "word/document.xml" in z.namelist():
            root = ElementTree.fromstring(z.read("word/document.xml"))
            paras = [_own_text(p, (W + "p",), W + "t") for p in root.iter(W + "p")]
            return "\n".join(p for p in paras if p.strip())[:limit]
        if ext == "ods" and "content.xml" in z.namelist():
            root = ElementTree.fromstring(z.read("content.xml"))
            TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
            lines = []
            OFF = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
            for row in root.iter(TABLE + "table-row"):
                cells = [(_itertext(c).strip() or c.get(OFF + "value") or "") for c in row.iter(TABLE + "table-cell")]
                if any(cells):
                    lines.append(" | ".join(cells).rstrip(" |"))
            return "\n".join(lines)[:limit]
        if ext in ("odt", "odp") and "content.xml" in z.namelist():
            root = ElementTree.fromstring(z.read("content.xml"))
            paras = [_own_text(p, (TEXT + "p", TEXT + "h")) for p in root.iter() if p.tag in (TEXT + "p", TEXT + "h")]
            return "\n".join(p for p in paras if p.strip())[:limit]
        if ext == "xlsx":
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                shared = [_itertext(si) for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).iter(ns + "si")]
            lines = []
            if "xl/worksheets/sheet1.xml" in z.namelist():
                root = ElementTree.fromstring(z.read("xl/worksheets/sheet1.xml"))
                for row in root.iter(ns + "row"):
                    cells = []
                    for c in row.iter(ns + "c"):
                        t = c.get("t")
                        v = c.find(ns + "v")
                        if t == "s" and v is not None and v.text and v.text.isdigit() and int(v.text) < len(shared):
                            cells.append(shared[int(v.text)])
                        elif t == "inlineStr":
                            cells.append(_itertext(c))
                        elif v is not None:
                            cells.append(v.text or "")
                    if any(x.strip() for x in cells):
                        lines.append(" | ".join(cells))
            return "\n".join(lines or shared)[:limit]
        if ext == "pptx":
            out = []
            for name in sorted(n for n in z.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")):
                root = ElementTree.fromstring(z.read(name))
                out.append(" ".join(t.text or "" for t in root.iter(A + "t")))
            return "\n".join(out)[:limit]
    except ElementTree.ParseError:
        return ""
    return ""


def extract_comments(content: bytes, extension: str) -> list[dict]:
    """Comments (annotations) in the document: [{id, author, date, text, digest}], in document order."""
    ext = (extension or "").lower()
    z = _zip_safe(content)
    if z is None:
        return []
    out: list[dict] = []
    try:
        if ext == "docx" and "word/comments.xml" in z.namelist():
            root = ElementTree.fromstring(z.read("word/comments.xml"))
            W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
            para_of: dict[str, str] = {}  # comment id -> its first paragraph id (threads refer to paragraph ids)
            for c in root.iter(W + "comment"):
                text = "\n".join("".join(t.text or "" for t in p.iter(W + "t")) for p in c.iter(W + "p")).strip()
                cid = c.get(W + "id") or ""
                first = next(iter(c.iter(W + "p")), None)
                if first is not None and first.get(W14 + "paraId"):
                    para_of[cid] = first.get(W14 + "paraId").upper()
                out.append({"id": cid, "author": c.get(W + "author") or "", "date": c.get(W + "date") or "", "text": text, "parent": None})
            if "word/commentsExtended.xml" in z.namelist():
                W15 = "{http://schemas.microsoft.com/office/word/2012/wordml}"
                ext_root = ElementTree.fromstring(z.read("word/commentsExtended.xml"))
                parent_para = {e.get(W15 + "paraId", "").upper(): (e.get(W15 + "paraIdParent") or "").upper() for e in ext_root.iter(W15 + "commentEx")}
                by_para = {v: k for k, v in para_of.items()}
                for c in out:
                    pp = parent_para.get(para_of.get(c["id"], ""), "")
                    if pp and pp in by_para:
                        c["parent"] = by_para[pp]
            # Without threading data, comments anchored on the same range are one thread (a reply the hub wrote).
            if "word/document.xml" in z.namelist():
                doc = z.read("word/document.xml").decode("utf-8", "replace")
                starts = re.findall(r'<w:commentRangeStart w:id="(\d+)"/>', doc)
                prev = None
                for cid in starts:
                    c = next((x for x in out if x["id"] == cid), None)
                    if c is not None and c["parent"] is None and prev is not None and doc.find(f'<w:commentRangeStart w:id="{cid}"/>') == doc.find(f'<w:commentRangeStart w:id="{prev}"/>') + len(f'<w:commentRangeStart w:id="{prev}"/>'):
                        c["parent"] = prev
                    prev = cid
        elif ext in ("odt", "ods", "odp") and "content.xml" in z.namelist():
            root = ElementTree.fromstring(z.read("content.xml"))
            LOEXT = "{urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0}"
            for i, a in enumerate(root.iter(OFFICE + "annotation")):
                creator = a.find(DC + "creator")
                date = a.find(DC + "date")
                text = "\n".join(_itertext(p) for p in a.iter(TEXT + "p")).strip()
                out.append({"id": a.get(OFFICE + "name") or str(i), "author": (creator.text if creator is not None else "") or "",
                            "date": (date.text if date is not None else "") or "", "text": text,
                            "parent": a.get(LOEXT + "parent-name") or None})
        elif ext == "xlsx":
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            for name in sorted(n for n in z.namelist() if n.startswith("xl/comments") and n.endswith(".xml")):
                root = ElementTree.fromstring(z.read(name))
                authors = [a.text or "" for a in root.iter(ns + "author")]
                for i, c in enumerate(root.iter(ns + "comment")):
                    try:
                        author = authors[int(c.get("authorId") or 0)]
                    except (ValueError, IndexError):
                        author = ""
                    out.append({"id": f"{name}:{c.get('ref') or i}", "author": author, "date": "", "text": _itertext(c).strip(), "parent": None})
    except ElementTree.ParseError:
        return []
    for c in out:
        c["digest"] = sha256_of({"author": c["author"], "text": c["text"]})[:16]
    return [c for c in out if c["text"]]


def mentions_in(text: str) -> bool:
    return bool(re.search(r"(?<![\w@])@[A-Za-z][\w-]*", text or ""))


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def add_reply(content: bytes, extension: str, parent_id: str, author: str, text: str, date: str = "") -> bytes | None:
    """Write a reply to a comment into the document: a new comment on the same anchor, authored by ``author``.
    docx and odt are supported; None when the format or the parent comment is not found."""
    ext = (extension or "").lower()
    z = _zip_safe(content)
    if z is None:
        return None
    names = z.namelist()
    files = {n: z.read(n) for n in names}
    when = date or "2026-01-01T00:00:00Z"
    if ext == "docx" and "word/comments.xml" in files and "word/document.xml" in files:
        comments = files["word/comments.xml"].decode("utf-8")
        document = files["word/document.xml"].decode("utf-8")
        if f'w:id="{parent_id}"' not in comments:
            return None
        ids = [int(x) for x in re.findall(r'<w:comment [^>]*w:id="(\d+)"', comments)]
        new_id = str(max(ids + [0]) + 1)
        paras = "".join(f'<w:p><w:r><w:t xml:space="preserve">{_escape(line)}</w:t></w:r></w:p>' for line in text.splitlines() or [""])
        reply = f'<w:comment w:id="{new_id}" w:author="{_escape(author)}" w:date="{when}" w:initials="{_escape(author[:2].upper())}">{paras}</w:comment>'
        comments = comments.replace("</w:comments>", reply + "</w:comments>")
        # Anchor the reply on the parent's range and reference.
        document = document.replace(f'<w:commentRangeStart w:id="{parent_id}"/>', f'<w:commentRangeStart w:id="{parent_id}"/><w:commentRangeStart w:id="{new_id}"/>', 1)
        document = document.replace(f'<w:commentRangeEnd w:id="{parent_id}"/>', f'<w:commentRangeEnd w:id="{parent_id}"/><w:commentRangeEnd w:id="{new_id}"/>', 1)
        ref = re.search(rf'<w:r[^>]*>(?:(?!</w:r>).)*<w:commentReference w:id="{parent_id}"/>\s*</w:r>', document, re.S)
        if ref:
            document = document[:ref.end()] + f'<w:r><w:commentReference w:id="{new_id}"/></w:r>' + document[ref.end():]
        else:
            document = document.replace("</w:body>", f'<w:p><w:r><w:commentReference w:id="{new_id}"/></w:r></w:p></w:body>', 1)
        # Threading (Word 2013+): the reply's paragraph id points at the parent's paragraph id when the part exists.
        if "word/commentsExtended.xml" in files:
            ext_xml = files["word/commentsExtended.xml"].decode("utf-8")
            parent_para = re.search(rf'<w:comment [^>]*w:id="{parent_id}"[^>]*>.*?<w:p [^>]*w14:paraId="([0-9A-F]+)"', comments, re.S)
            if parent_para:
                new_para = f"{int(parent_para.group(1), 16) + 1 + int(new_id):08X}"[-8:]
                comments = comments.replace(f'<w:comment w:id="{new_id}"', f'<w:comment w:id="{new_id}"', 1)
                comments = re.sub(rf'(<w:comment w:id="{new_id}"[^>]*>)<w:p>', rf'\1<w:p w14:paraId="{new_para}">', comments, count=1)
                ext_xml = ext_xml.replace("</w15:commentsEx>", f'<w15:commentEx w15:paraId="{new_para}" w15:paraIdParent="{parent_para.group(1)}" w15:done="0"/></w15:commentsEx>')
                files["word/commentsExtended.xml"] = ext_xml.encode("utf-8")
                if "xmlns:w14=" not in comments:
                    comments = comments.replace("<w:comments ", '<w:comments xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" ', 1)
        files["word/comments.xml"] = comments.encode("utf-8")
        files["word/document.xml"] = document.encode("utf-8")
    elif ext in ("odt", "ods", "odp") and "content.xml" in files:
        xml = files["content.xml"].decode("utf-8")
        m = re.search(rf'<office:annotation[^>]*office:name="{re.escape(parent_id)}"[^>]*>.*?</office:annotation>', xml, re.S)
        if not m:
            anns = list(re.finditer(r"<office:annotation[ >].*?</office:annotation>", xml, re.S))
            try:
                m = anns[int(parent_id)]
            except (ValueError, IndexError):
                return None
        paras = "".join(f"<text:p>{_escape(line)}</text:p>" for line in text.splitlines() or [""])
        reply = f'<office:annotation office:name="reply-{abs(hash(text)) % 10**8}"><dc:creator>{_escape(author)}</dc:creator><dc:date>{when}</dc:date>{paras}</office:annotation>'
        xml = xml[:m.end()] + reply + xml[m.end():]
        if "xmlns:dc=" not in xml:
            xml = xml.replace("<office:document-content ", '<office:document-content xmlns:dc="http://purl.org/dc/elements/1.1/" ', 1)
        files["content.xml"] = xml.encode("utf-8")
    else:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for n in names:
            if n == "mimetype":
                out.writestr(zipfile.ZipInfo(n), files[n], compress_type=zipfile.ZIP_STORED)
            else:
                out.writestr(n, files[n])
    return buf.getvalue()


def insert_paragraphs(content: bytes, extension: str, paragraphs: list[str], position: str = "start") -> bytes | None:
    """Write paragraphs into the document body at the start or the end. A line starting with '# ' is a heading.
    docx and odt are supported; None otherwise."""
    ext = (extension or "").lower()
    if is_sheet(ext):
        return insert_rows(content, ext, rows_from_lines(paragraphs))
    if ext in ("md", "txt"):
        text = content.decode("utf-8", "replace")
        block = "\n".join(paragraphs).strip() + "\n"
        if position == "replace":
            return block.encode("utf-8")
        return (block + "\n" + text).encode("utf-8") if position == "start" else (text.rstrip("\n") + "\n\n" + block).encode("utf-8")
    z = _zip_safe(content)
    if z is None or not paragraphs:
        return None
    names = z.namelist()
    files = {n: z.read(n) for n in names}
    if ext == "docx" and "word/document.xml" in files:
        xml = files["word/document.xml"].decode("utf-8")
        block = "".join(docx_blocks(paragraphs))
        if position == "replace":
            # Keep the section properties and the comment anchors (a paragraph holding the comment references), drop the rest.
            refs = "".join(f'<w:r><w:commentReference w:id="{i}"/></w:r>' for i in re.findall(r'<w:commentReference w:id="(\d+)"/>', xml))
            sect = re.search(r"<w:sectPr[ >/].*?</w:sectPr>|<w:sectPr/>", xml, re.S)
            tail = (f"<w:p>{refs}</w:p>" if refs else "") + (sect.group(0) if sect else "")
            xml = re.sub(r"(<w:body[^>]*>).*(</w:body>)", lambda m: m.group(1) + block + tail + m.group(2), xml, count=1, flags=re.S)
        elif position == "start":
            xml = re.sub(r"(<w:body[^>]*>)", lambda m: m.group(1) + block, xml, count=1)
        else:
            xml = re.sub(r"(<w:sectPr[ >/].*?</w:sectPr>|<w:sectPr/>)?\s*</w:body>", lambda m: block + (m.group(1) or "") + "</w:body>", xml, count=1, flags=re.S)
        files["word/document.xml"] = xml.encode("utf-8")
    elif ext == "odt" and "content.xml" in files:
        xml = files["content.xml"].decode("utf-8")
        out = []
        for line in paragraphs:
            if line.startswith("# "):
                out.append(f'<text:h text:outline-level="1">{_escape(line[2:].strip())}</text:h>')
            elif line.startswith("## "):
                out.append(f'<text:h text:outline-level="2">{_escape(line[3:].strip())}</text:h>')
            else:
                out.append(f"<text:p>{_escape(line)}</text:p>")
        block = "".join(out)
        if position == "replace":
            anns = "".join(re.findall(r"<office:annotation[ >].*?</office:annotation>", xml, re.S))
            xml = re.sub(r"(<office:text[^>]*>).*(</office:text>)", lambda m: m.group(1) + block + (f"<text:p>{anns}</text:p>" if anns else "") + m.group(2), xml, count=1, flags=re.S)
        elif position == "start":
            xml = re.sub(r"(<office:text[^>]*>)", lambda m: m.group(1) + block, xml, count=1)
        else:
            xml = xml.replace("</office:text>", block + "</office:text>", 1)
        files["content.xml"] = xml.encode("utf-8")
    else:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as outz:
        for n in names:
            if n == "mimetype":
                outz.writestr(zipfile.ZipInfo(n), files[n], compress_type=zipfile.ZIP_STORED)
            else:
                outz.writestr(n, files[n])
    return buf.getvalue()


def thread_of(comments: list[dict], comment_id: str) -> list[dict]:
    """Every comment in the same thread as the given one (the root and all its descendants), in document order."""
    by_id = {c["id"]: c for c in comments}
    root_id = comment_id
    seen = set()
    while by_id.get(root_id, {}).get("parent") and root_id not in seen:
        seen.add(root_id)
        root_id = by_id[root_id]["parent"]
    members = {root_id}
    changed = True
    while changed:
        changed = False
        for c in comments:
            if c["id"] not in members and c.get("parent") in members:
                members.add(c["id"])
                changed = True
    return [c for c in comments if c["id"] in members]


def _docx_runs(text: str, extra_rpr: str = "") -> str:
    """Runs for a line with **bold** and *italic* segments."""
    out = []
    for i, part in enumerate(re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text)):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out.append(f'<w:r><w:rPr><w:b/>{extra_rpr}</w:rPr><w:t xml:space="preserve">{_escape(part[2:-2])}</w:t></w:r>')
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            out.append(f'<w:r><w:rPr><w:i/>{extra_rpr}</w:rPr><w:t xml:space="preserve">{_escape(part[1:-1])}</w:t></w:r>')
        else:
            out.append(f'<w:r>{("<w:rPr>" + extra_rpr + "</w:rPr>") if extra_rpr else ""}<w:t xml:space="preserve">{_escape(part)}</w:t></w:r>')
    return "".join(out) or '<w:r><w:t xml:space="preserve"></w:t></w:r>'


def docx_blocks(lines: list[str]) -> list[str]:
    """Paragraphs and tables for lines written like Markdown: '# ' and '## ' headings, '- ' bullets, '**bold**',
    '*italic*', and '| a | b |' table rows (a '|---|' line is skipped)."""
    out: list[str] = []
    table: list[list[str]] = []

    def flush_table():
        if not table:
            return
        rows = ""
        for r, cells in enumerate(table):
            tcs = "".join(f'<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr><w:p>{_docx_runs(c.strip(), "<w:b/>" if r == 0 else "")}</w:p></w:tc>' for c in cells)
            rows += f"<w:tr>{tcs}</w:tr>"
        out.append('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/><w:tblBorders>'
                   + "".join(f'<w:{b} w:val="single" w:sz="4" w:space="0" w:color="808080"/>' for b in ("top", "left", "bottom", "right", "insideH", "insideV"))
                   + f'</w:tblBorders></w:tblPr><w:tblGrid>{"".join("<w:gridCol/>" for _ in table[0])}</w:tblGrid>{rows}</w:tbl>')
        table.clear()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            if re.fullmatch(r"\|[\s:|-]+\|", stripped):
                continue
            table.append(stripped.strip("|").split("|"))
            continue
        flush_table()
        if stripped == "---":
            out.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
            continue
        if stripped.startswith("# "):
            out.append(f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>{_docx_runs(stripped[2:].strip(), "<w:b/><w:sz w:val=\"32\"/>")}</w:p>')
        elif stripped.startswith("## "):
            out.append(f'<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>{_docx_runs(stripped[3:].strip(), "<w:b/><w:sz w:val=\"28\"/>")}</w:p>')
        elif stripped.startswith("### "):
            out.append(f'<w:p><w:pPr><w:pStyle w:val="Heading3"/></w:pPr>{_docx_runs(stripped[4:].strip(), "<w:b/>")}</w:p>')
        elif re.match(r"^[-*•] ", stripped):
            out.append(f'<w:p><w:pPr><w:pStyle w:val="ListParagraph"/><w:ind w:left="720" w:hanging="360"/></w:pPr>{_docx_runs("• " + stripped[2:].strip())}</w:p>')
        elif re.match(r"^\d+[.)] ", stripped):
            n, _, rest = stripped.partition(" ")
            out.append(f'<w:p><w:pPr><w:pStyle w:val="ListParagraph"/><w:ind w:left="720" w:hanging="360"/></w:pPr>{_docx_runs(n + " " + rest.strip())}</w:p>')
        else:
            out.append(f"<w:p>{_docx_runs(stripped)}</w:p>")
    flush_table()
    return out


# ---------------------------------------------------------------- spreadsheets
def _col(n: int) -> str:
    out = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def rows_from_lines(lines: list[str]) -> list[list[str]]:
    """Content lines as rows: '| a | b |' rows become cells, a '|---|' line is skipped, headings and plain lines
    become one-cell rows (heading marks and bullets stripped)."""
    rows: list[list[str]] = []
    for line in lines:
        t = line.strip()
        if not t:
            continue
        if t.startswith("|") and t.endswith("|"):
            if re.fullmatch(r"\|[\s:|-]+\|", t):
                continue
            cells = [c.strip().replace("**", "") for c in t.strip("|").split("|")]
            while len(cells) > 1 and not cells[0]:  # a row written as '|| a | b |' has no empty first cell
                cells.pop(0)
            rows.append(cells)
        else:
            rows.append([re.sub(r"^(#+\s*|[-*•]\s+|\d+[.)]\s+)", "", t).replace("**", "")])
    return rows


def sheet_rows_used(content: bytes, extension: str) -> int:
    """The last used row number of the first sheet (0 when empty)."""
    ext = (extension or "").lower()
    z = _zip_safe(content)
    if z is None:
        return 0
    if ext == "xlsx" and "xl/worksheets/sheet1.xml" in z.namelist():
        xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8", "replace")
        nums = [int(x) for x in re.findall(r'<row [^>]*r="(\d+)"', xml)]
        return max(nums) if nums else 0
    if ext == "ods" and "content.xml" in z.namelist():
        xml = z.read("content.xml").decode("utf-8", "replace")
        m = re.search(r"<table:table [^>]*>(.*?)</table:table>", xml, re.S)
        if not m:
            return 0
        count = last_used = 0
        for rm in re.finditer(r"<table:table-row\b([^>]*)>(.*?)</table:table-row>", m.group(1), re.S):
            rep = re.search(r'table:number-rows-repeated="(\d+)"', rm.group(1))
            n = int(rep.group(1)) if rep else 1
            if n > 1000:
                break
            count += n
            if "<text:p>" in rm.group(2) or "office:value" in rm.group(2):
                last_used = count
        return last_used
    return 0


def insert_rows(content: bytes, extension: str, rows: list[list[str]]) -> bytes | None:
    """Append rows of cells after the last used row of the first sheet (xlsx or ods); None otherwise."""
    ext = (extension or "").lower()
    z = _zip_safe(content)
    if z is None or not rows:
        return None
    names = z.namelist()
    files = {n: z.read(n) for n in names}
    if ext == "xlsx" and "xl/worksheets/sheet1.xml" in files:
        xml = files["xl/worksheets/sheet1.xml"].decode("utf-8")
        start = sheet_rows_used(content, ext) + 1
        new = ""
        for i, cells in enumerate(rows):
            r = start + i
            cs = ""
            for j, v in enumerate(cells):
                v = v.strip()
                ref = f"{_col(j)}{r}"
                if v.startswith("=") and len(v) > 1:
                    cs += f'<c r="{ref}"><f>{_escape(v[1:])}</f></c>'
                elif re.fullmatch(r"-?\d+(\.\d+)?", v):
                    cs += f'<c r="{ref}"><v>{v}</v></c>'
                else:
                    cs += f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_escape(v)}</t></is></c>'
            new += f'<row r="{r}">{cs}</row>'
        if "<sheetData/>" in xml:
            xml = xml.replace("<sheetData/>", f"<sheetData>{new}</sheetData>", 1)
        else:
            xml = xml.replace("</sheetData>", new + "</sheetData>", 1)
        xml = re.sub(r'<dimension ref="[^"]*"/>', "", xml, count=1)
        files["xl/worksheets/sheet1.xml"] = xml.encode("utf-8")
    elif ext == "ods" and "content.xml" in files:
        xml = files["content.xml"].decode("utf-8")
        width = max(len(r) for r in rows)
        new = ""
        for cells in rows:
            cs = ""
            for v in cells:
                v = v.strip()
                if v.startswith("=") and len(v) > 1:
                    cs += f'<table:table-cell table:formula="of:{_escape(v)}" office:value-type="float" office:value="0"><text:p>0</text:p></table:table-cell>'
                elif re.fullmatch(r"-?\d+(\.\d+)?", v):
                    cs += f'<table:table-cell office:value-type="float" office:value="{v}"><text:p>{v}</text:p></table:table-cell>'
                else:
                    cs += f'<table:table-cell office:value-type="string"><text:p>{_escape(v)}</text:p></table:table-cell>'
            cs += "<table:table-cell/>" * (width - len(cells))
            new += f"<table:table-row>{cs}</table:table-row>"
        m = re.search(r"<table:table [^>]*>.*?</table:table>", xml, re.S)
        if not m:
            return None
        table = m.group(0)
        # drop a trailing all-empty repeated row block so the new rows follow the used ones
        table2 = re.sub(r'(?:<table:table-row\b[^>]*>(?:\s*<table:table-cell\b[^>]*/>|\s*<table:table-cell\b[^>]*></table:table-cell>)*\s*</table:table-row>\s*)+</table:table>$',
                        new + "</table:table>", table, count=1, flags=re.S)
        if table2 == table:
            table2 = table.replace("</table:table>", new + "</table:table>", 1)
        xml = xml.replace(table, table2, 1)
        files["content.xml"] = xml.encode("utf-8")
    else:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for n in names:
            if n == "mimetype":
                out.writestr(zipfile.ZipInfo(n), files[n], compress_type=zipfile.ZIP_STORED)
            else:
                out.writestr(n, files[n])
    return buf.getvalue()


def is_sheet(extension: str) -> bool:
    return (extension or "").lower() in ("xlsx", "ods", "xls", "csv")


# ---------------------------------------------------------------- the writing interface
WRITING_INTERFACE = {
    "text": """OUTPUT FORMAT when you write into a text document (docx, odt): plain lines in this markup, nothing else.
- '# Title', '## Section', '### Subsection' become heading styles 1, 2 and 3.
- A plain line is a paragraph. A blank line separates paragraphs.
- '- item' is a bullet point; '1. item' is a numbered point.
- '**bold**' and '*italic*' inside a line.
- A table is consecutive rows '| Cell | Cell |'; put '|---|---|' after the first row to make it the header row.
- '---' alone on a line is a page break.
Not supported: images, merged cells, footnotes, fonts, colours. Write content only: no preamble, no explanation, no code fences.""",
    "sheet": """OUTPUT FORMAT when you write into a spreadsheet (xlsx, ods): one row per line, cells separated by '|', like '| Item | Limit | Result |'.
- The first row may be column headings.
- A cell that is a number (12, 2.5, -3) is stored as a number; text otherwise.
- A cell starting with '=' is a formula, e.g. '=SUM(B2:B5)'; use A1 references; rows you write start at the row the hub tells you.
- Keep one table; no headings, no bullets, no blank rows inside it.
Not supported: merged cells, formats, charts. Write rows only: no preamble, no explanation.""",
    "review": """OUTPUT FORMAT for a review: a short assessment in plain sentences, then any further findings as lines
'- [error] <what was checked>: "<the SOP sentence it breaks, quoted word for word>" <detail>' or
'- [warning] <what was checked>: <detail>', then exactly one line 'Verdict: pass' or 'Verdict: fail'.
An error is only something a procedure requires and the item does not do: quote that procedure's sentence. A suggestion,
a style point or something no procedure asks for is a warning. Judge the content in front of you, not how big the change is.
Fail only when there is at least one error.""",
    "chat": """HOW TO TALK HERE: a colleague has dropped by your desk for an informal chat in the hub's messenger.
Answer like a person would across a desk: plain, warm, first person, usually one to four sentences; more only if they ask for detail.
Plain text only: no Markdown, no links; write record numbers as they are (DOC-0432) and the messenger links them.
No headings, no review format, no verdict lines. Say what you know from the procedures, the item they are looking at and your queue.
You do not change anything from this chat: if they want a review, an edit or an approval, tell them to ask in the item's chatter or
comments, or to use the Ops buttons on the item, and that you will pick it up there. If you do not know, say so.
TOOLS: you can look up anything in the record space this person may read: search_records (find by words, type, state, folder,
assignee), get_record (one record by number: fields, state, decisions, links, documents, run), read_document (a document's text and
comments), waiting_for (a queue), record_history (who did what) and overview (counts by type and state). Look things up before you answer
a question about records; never guess a number or a state; mention the record numbers you looked at. If your tools do not
show something, say you cannot tell from what you can see; never claim that something did not happen.""",
    "chat-act": """HOW TO TALK HERE: a colleague is chatting with you in the hub's messenger.
Answer like a person would across a desk: plain, warm, first person, usually one to four sentences; more only if they ask for detail.
Plain text only: no Markdown, no links; write record numbers as they are (DOC-0432) and the messenger links them.
YOU CAN ACT FROM THIS CHAT, like a colleague with access: look things up (search_records, get_record, read_document, ...) and, when
they ask you to do something, do it with your write tools: move a record or document (move_record), change fields (update_fields),
edit a document (apply_document_ops, edit_document), start a process such as sending a document for approval (start_process),
take your turn at an approval you may sign (take_turn), comment, assign, link, create records, tasks and documents.
The hub checks every action against what you may do and what the person asking may do, and refuses anything either may not; it
never lets you act beyond that. When a tool refuses, tell them plainly what it said and who can do it instead. Never say you did
something a tool did not confirm. Look a record up before acting on it, act only on what they asked, and say what you did with the
record numbers.""",
    "answer": """OUTPUT FORMAT for an answer in a comment thread: a few plain sentences a colleague can act on; cite the procedure
by name when you rely on it. If the procedures you follow do not cover the question, say so, say which procedure would
(if you know), and that you do not follow it. No headings, no lists longer than three items.""",
}


def writing_interface(kind: str) -> str:
    return WRITING_INTERFACE.get(kind, "")


# ---------------------------------------------------------------- pictures in a text document
# A picture in an SOP has a place in the Frame text: a line of its own, ``![<its description>](sha256:<hash>)``, after
# the paragraph, heading or table it is in (``link:<address>`` for a picture linked from outside the file). The hash is
# the picture's bytes; for a picture with more than one image (an SVG with its PNG for older readers, a copy for old
# readers in mc:Fallback that differs) or with a crop, rotation or flip, it is the hash of all of them together. The
# line names what the picture is and what it holds, never where the file keeps it (an editor renumbers its media
# parts on save), so an unchanged picture reads the same and an added, removed or replaced one is a changed line.
_PICTURE = r"!\[[^\[\]\n]*\]\((?:sha256:[0-9a-f]{64}|link:[^\s()]+)\)"
PICTURE_LINE = re.compile(r"!\[([^\[\]\n]*)\]\((sha256:[0-9a-f]{64}|link:[^\s()]+)\)")
_LOOKS_LIKE_PICTURE = re.compile(rf"^(\s*)(\\*{_PICTURE})(\s*)$")
PICTURE_ALT = "picture"  # the description a picture with none reads as
DEFAULT_EXTENT = (2743200, 2057400)  # 3 x 2.25 inches, in EMU, for a picture whose size is not known
EMU = {"cm": 360000, "mm": 36000, "in": 914400, "pt": 12700, "pc": 152400, "px": 9525}
PICTURE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp",
                 "tif": "image/tiff", "tiff": "image/tiff", "svg": "image/svg+xml", "emf": "image/x-emf", "wmf": "image/x-wmf",
                 "webp": "image/webp"}
SVG_EXT_URI = "{96DAC541-7B7A-43D3-8B79-37D633B846F1}"  # the DrawingML extension that holds an SVG next to its PNG

_PKG_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
R_NS = ("http://schemas.openxmlformats.org/officeDocument/2006/relationships", "http://purl.oclc.org/ooxml/officeDocument/relationships")
W_NS = ("http://schemas.openxmlformats.org/wordprocessingml/2006/main", "http://purl.oclc.org/ooxml/wordprocessingml/main")
A_NS = ("http://schemas.openxmlformats.org/drawingml/2006/main", "http://purl.oclc.org/ooxml/drawingml/main")
WP_NS = ("http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing", "http://purl.oclc.org/ooxml/drawingml/wordprocessingDrawing")
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
O_NS = "urn:schemas-microsoft-com:office:office"
DRAW_NS = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
SVG_NS = "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
XLINK_NS = "http://www.w3.org/1999/xlink"
DOCX_PICTURE_NAMESPACES = {
    "wp": WP_NS[0], "a": A_NS[0], "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture", "r": R_NS[0],
}


def is_picture_line(line: str) -> bool:
    """True for a Frame line that stands for a picture in the document (``picture_line``)."""
    return bool(PICTURE_LINE.fullmatch((line or "").strip()))


def picture_line(alt: str, ref: str) -> str:
    alt = " ".join(re.sub(r"[\[\]]", " ", alt or "").split()) or PICTURE_ALT
    return f"![{alt}]({ref})"


def escape_text_line(text: str) -> str:
    """A paragraph whose words are written like a picture line reads with a backslash in front, so typed text never reads
    as a picture (and a picture the document does not hold never hides behind it). ``unescape_text_line`` undoes it."""
    m = _LOOKS_LIKE_PICTURE.match(text)
    return f"{m.group(1)}\\{m.group(2)}{m.group(3)}" if m else text


def unescape_text_line(line: str) -> str:
    m = _LOOKS_LIKE_PICTURE.match(line)
    return f"{m.group(1)}{m.group(2)[1:]}{m.group(3)}" if m and m.group(2).startswith("\\") else line


def _split(tag: str) -> tuple[str, str]:
    """(namespace, local name) of an ElementTree tag or attribute name."""
    if tag.startswith("{"):
        ns, _, local = tag[1:].partition("}")
        return ns, local
    return "", tag


def _length_emu(value: str) -> int | None:
    m = re.fullmatch(r"\s*([\d.]+)\s*(cm|mm|in|pt|pc|px)\s*", value or "")
    try:
        return int(float(m.group(1)) * EMU[m.group(2)]) if m else None
    except ValueError:
        return None


def _declarations(head: str) -> str:
    """The namespace declarations of the start tags before a document's body (they hold for every block of it)."""
    out: dict[str, str] = {}
    for m in re.finditer(r"""\sxmlns(?::([\w.\-]+))?\s*=\s*(["'])(.*?)\2""", head or "", re.S):
        out.setdefault(m.group(1) or "", m.group(3))
    return "".join(f' xmlns{":" + k if k else ""}="{_escape(html.unescape(v))}"' for k, v in out.items())


def part_extension(name: str) -> str:
    """The extension a picture is stored under: the source part's, when it is a plain one, else ``bin``."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in (name or "") else ""
    return ext if re.fullmatch(r"[a-z0-9]{1,8}", ext) else "bin"


class PictureReader:
    """The pictures of one docx or odt package, block by block of its body (``read``). Each block is parsed with the
    namespaces the document declares (so any prefix, any quoting), and its relationships resolved as the package format
    says (percent-encoded, case-insensitive part names). Only the body is read: a header's or footer's logo is not part
    of the SOP's text."""

    def __init__(self, files: dict[str, bytes], ext: str, head: str = ""):
        self.files, self.ext, self.decl = files, ext, _declarations(head)
        self.lower = {n.lower(): n for n in files}
        self.rels = self._docx_rels() if ext == "docx" else {}

    def _part(self, name: str) -> str | None:
        return self.lower.get(name.lower())

    def _docx_rels(self) -> dict[str, dict]:
        """{relationship id: {"target", "external", "image"}} of word/document.xml."""
        import posixpath
        from urllib.parse import unquote

        name = self._part("word/_rels/document.xml.rels")
        if not name:
            return {}
        try:
            root = ElementTree.fromstring(self.files[name])
        except ElementTree.ParseError:
            return {}
        out = {}
        for rel in root.iter("{%s}Relationship" % _PKG_RELS):
            target, external = rel.get("Target") or "", (rel.get("TargetMode") or "").lower() == "external"
            if not external:
                t = unquote(target)
                target = t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.join("word", t))
            out[rel.get("Id") or ""] = {"target": target, "external": external, "image": (rel.get("Type") or "").endswith("/image")}
        return out

    def _member(self, target: str, external: bool, role: str) -> dict:
        from hashlib import sha256

        part = None if external else self._part(target)
        data = self.files[part] if part else None
        return {"role": role, "part": part or target, "external": external, "data": data,
                "sha": sha256(data).hexdigest() if data is not None else None}

    def read(self, raw: str) -> list[dict]:
        """The pictures in one block of the body XML (a paragraph, a heading, a table), in order: each {"ref", "alt",
        "members": [{"role": "image"|"svg", "part", "external", "data", "sha"}], "crop", "xfrm", "cx", "cy",
        "writable" (``to_docx`` can write it again)}."""
        marks = ("drawing", "pict", "object", "blip", "imagedata", "AlternateContent") if self.ext == "docx" else ("draw:image", "image")
        if not raw or not any(m in raw for m in marks):
            return []
        try:
            root = ElementTree.fromstring(f"<hubblock{self.decl}>{raw}</hubblock>")
        except ElementTree.ParseError:
            return [self._unparsed(raw)]
        groups: list = []
        if self.ext == "docx":
            self._docx_groups(root, groups)
            return [p for p in (self._docx_picture(g) for g in groups) if p]
        self._odt_groups(root, groups)
        return [p for p in (self._odt_picture(g) for g in groups) if p]

    def _unparsed(self, raw: str) -> dict:
        """A block whose XML does not parse but looks like it holds a picture: a picture line on the block's own XML, so
        any change to it is a changed line (and the hub never writes it again)."""
        from hashlib import sha256

        return {"ref": "sha256:" + sha256(raw.encode("utf-8")).hexdigest(), "alt": "a picture the hub could not read",
                "members": [], "crop": [], "xfrm": [], "cx": None, "cy": None, "writable": False}

    # docx ------------------------------------------------------------
    def _image_rel(self, el) -> list[tuple[str, str]]:
        """(relationship id, role) of each attribute of ``el`` in the relationships namespace naming an image."""
        out = []
        for k, v in el.attrib.items():
            ns, _local = _split(k)
            if ns in R_NS and v in self.rels and self.rels[v]["image"]:
                out.append((v, "svg" if _split(el.tag)[1] == "svgBlip" else "image"))
        return out

    def _docx_groups(self, el, groups: list) -> None:
        ns, local = _split(el.tag)
        if (ns in W_NS and local in ("drawing", "pict", "object")) or (ns == MC_NS and local == "AlternateContent"):
            groups.append(el)  # one picture (or none): everything under it, mc:Choice and mc:Fallback alike
            return
        if self._image_rel(el):
            groups.append(el)
            return
        for c in el:
            self._docx_groups(c, groups)

    def _docx_picture(self, g) -> dict | None:
        members, seen = [], set()
        alt, cx, cy, crops, xfrms = "", None, None, [], []
        for el in g.iter():
            ns, local = _split(el.tag)
            for rid, role in self._image_rel(el):
                rel = self.rels[rid]
                key = (rel["target"].lower(), rel["external"], role)
                if key not in seen:
                    seen.add(key)
                    members.append(self._member(rel["target"], rel["external"], role))
            if ns in WP_NS and local == "docPr" and not alt:
                alt = el.get("descr") or el.get("title") or ""
            elif local == "shape" and not alt and el.get("alt"):
                alt = el.get("alt")
            elif local == "imagedata" and not alt and el.get("{%s}title" % O_NS):
                alt = el.get("{%s}title" % O_NS)
            elif ns in WP_NS and local == "extent" and cx is None:
                try:
                    cx, cy = int(el.get("cx")), int(el.get("cy"))
                except (TypeError, ValueError):
                    pass
            elif ns in A_NS and local == "srcRect":
                c = {k: v for k, v in sorted(el.attrib.items()) if k in ("l", "t", "r", "b") and v not in ("0", "")}
                if c:
                    crops.append(c)
            elif ns in A_NS and local == "xfrm":
                x = {k: v for k, v in sorted(el.attrib.items()) if (k == "rot" and v not in ("0", "")) or (k in ("flipH", "flipV") and v in ("1", "true"))}
                if x:
                    xfrms.append(x)
        if not members:
            return None
        roles = [m["role"] for m in members]
        writable = (all(m["data"] is not None for m in members) and roles in (["image"], ["image", "svg"])
                    and len(crops) <= 1 and len(xfrms) <= 1)
        return self._picture(members, alt, crops, xfrms, cx, cy, writable)

    # odt -------------------------------------------------------------
    def _odt_groups(self, el, groups: list) -> None:
        ns, local = _split(el.tag)
        if ns == DRAW_NS and local == "frame":
            groups.append(el)  # the outermost frame: every image under it is part of the picture
            return
        if ns == DRAW_NS and local == "image":
            groups.append(el)
            return
        for c in el:
            self._odt_groups(c, groups)

    def _odt_picture(self, g) -> dict | None:
        from urllib.parse import unquote

        members, seen, alt, xfrms = [], set(), "", []
        for el in g.iter():
            ns, local = _split(el.tag)
            if ns == DRAW_NS and local == "image":
                href = el.get("{%s}href" % XLINK_NS) or ""
                external = bool(re.match(r"^[a-z][a-z0-9+.\-]*:", href, re.I)) or href.startswith(("/", ".."))
                target = href if external else unquote(href).removeprefix("./")
                key = (target.lower(), external)
                if href and key not in seen:
                    seen.add(key)
                    members.append(self._member(target, external, "image"))
            elif ns == SVG_NS and local in ("desc", "title") and not alt:
                alt = "".join(el.itertext())
            if ns == DRAW_NS and local == "frame" and el.get("{%s}transform" % DRAW_NS):
                xfrms.append({"transform": el.get("{%s}transform" % DRAW_NS)})
        if not members:
            return None
        cx, cy = _length_emu(g.get("{%s}width" % SVG_NS)), _length_emu(g.get("{%s}height" % SVG_NS))
        writable = len(members) == 1 and members[0]["data"] is not None and not xfrms
        return self._picture(members, alt, [], xfrms, cx, cy, writable)

    @staticmethod
    def _picture(members, alt, crops, xfrms, cx, cy, writable) -> dict:
        import json
        from hashlib import sha256
        from urllib.parse import quote

        one = members[0] if len(members) == 1 and not crops and not xfrms else None
        if one is not None and one["external"]:
            ref = "link:" + quote(one["part"], safe=":/?#@!$&'*+,;=%~.-_")
        elif one is not None and one["data"] is not None:
            ref = "sha256:" + one["sha"]
        else:  # several images, a transform, or an image the package does not hold: all of it, together
            canon = {"images": [[m["role"], m["sha"] or (("link:" if m["external"] else "missing:") + m["part"])] for m in members],
                     "crop": crops, "xfrm": xfrms}
            ref = "sha256:" + sha256(json.dumps(canon, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return {"ref": ref, "alt": alt, "members": members, "crop": crops, "xfrm": xfrms, "cx": cx, "cy": cy, "writable": writable}


class DocxPictureWriter:
    """Pictures written into a .docx ``sopdocs.to_docx`` makes: each picture line becomes an inline DrawingML picture
    (its images, its crop, rotation and flip), with its parts, relationships and content types (``finish``)."""

    def __init__(self, pictures: dict[str, dict] | None):
        self.pictures = pictures or {}
        self.rels: dict[str, str] = {}  # relationship id -> its XML
        self.media: dict[str, bytes] = {}
        self.by_key: dict[str, str] = {}  # image sha or address -> relationship id
        self.types: set[str] = set()
        self.n = 0

    def _rid(self, member: dict | None, address: str = "") -> str:
        key = address or member["sha"]
        if key in self.by_key:
            return self.by_key[key]
        rid = f"rIdPicture{len(self.by_key) + 1}"
        self.by_key[key] = rid
        typ = f"{R_NS[0]}/image"
        if address:
            self.rels[rid] = f'<Relationship Id="{rid}" Type="{typ}" Target="{_escape(address)}" TargetMode="External"/>'
        else:
            ext = part_extension(member["part"])
            part = f"media/{member['sha'][:16]}.{ext}"  # named by what it holds
            self.media["word/" + part] = member["data"]
            self.types.add(ext)
            self.rels[rid] = f'<Relationship Id="{rid}" Type="{typ}" Target="{part}"/>'
        return rid

    def paragraph(self, line_no: int, alt: str, ref: str) -> str:
        p = self.pictures.get(ref)
        if ref.startswith("link:") and p is None:
            blip, crop, xfrm, cx, cy = f'<a:blip r:link="{self._rid(None, ref[5:])}"/>', {}, {}, None, None
        else:
            if p is None:
                raise ValueError(f"line {line_no} is a picture ({picture_line(alt, ref)}) that the document it was read from does not "
                                 "hold: the revision would lose it")
            if not p.get("writable"):
                raise ValueError(f"line {line_no} is a picture ({picture_line(alt, ref)}) the hub cannot write again (several images, "
                                 "or an image the document does not hold): change the document in the editor instead")
            if ref.startswith("link:"):
                blip = f'<a:blip r:link="{self._rid(None, ref[5:])}"/>'
            else:
                image = next(m for m in p["members"] if m["role"] == "image")
                svg = next((m for m in p["members"] if m["role"] == "svg"), None)
                ext = ('<a:extLst><a:ext uri="' + SVG_EXT_URI + '"><asvg:svgBlip xmlns:asvg="http://schemas.microsoft.com/office/'
                       f'drawing/2016/SVG/main" r:embed="{self._rid(svg)}"/></a:ext></a:extLst>') if svg else ""
                blip = f'<a:blip r:embed="{self._rid(image)}">{ext}</a:blip>' if ext else f'<a:blip r:embed="{self._rid(image)}"/>'
            crop, xfrm, cx, cy = (p["crop"] or [{}])[0], (p["xfrm"] or [{}])[0], p.get("cx"), p.get("cy")
        self.n += 1
        n, d = self.n, _escape(" ".join((alt or "").split()))
        cx, cy = cx or DEFAULT_EXTENT[0], cy or DEFAULT_EXTENT[1]
        src = ("<a:srcRect" + "".join(f' {k}="{_escape(v)}"' for k, v in sorted(crop.items())) + "/>") if crop else ""
        xf = "".join(f' {k}="{_escape(v)}"' for k, v in sorted(xfrm.items()))
        return (f'<w:p><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
                f'<wp:docPr id="{n}" name="Picture {n}" descr="{d}"/><wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/>'
                '</wp:cNvGraphicFramePr><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{n}" name="Picture {n}" descr="{d}"/><pic:cNvPicPr/></pic:nvPicPr>'
                f'<pic:blipFill>{blip}{src}<a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm{xf}><a:off x="0" y="0"/>'
                f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
                "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>")

    def finish(self, name: str, data: bytes) -> bytes:
        """A part of the package with what the pictures need (unchanged when there are none)."""
        if not self.rels:
            return data
        if name == "word/document.xml":
            xml = data.decode("utf-8")
            decl = "".join(f' xmlns:{k}="{v}"' for k, v in DOCX_PICTURE_NAMESPACES.items() if f"xmlns:{k}=" not in xml)
            return re.sub(r"<w:document\b", lambda m: m.group(0) + decl, xml, count=1).encode("utf-8")
        if name == "word/_rels/document.xml.rels":
            return data.decode("utf-8").replace("</Relationships>", "".join(self.rels.values()) + "</Relationships>").encode("utf-8")
        if name == "[Content_Types].xml":
            xml = data.decode("utf-8")
            add = "".join(f'<Default Extension="{e}" ContentType="{PICTURE_TYPES.get(e, "application/octet-stream")}"/>'
                          for e in sorted(self.types) if f'Extension="{e}"' not in xml)
            return xml.replace("</Types>", add + "</Types>").encode("utf-8")
        return data
