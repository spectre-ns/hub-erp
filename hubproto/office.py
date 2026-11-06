"""Reading office files: the text and the comments inside a docx, xlsx, odt or ods, so a review can read the
document and a comment written in the editor ("@quality-assistant can you review this?") reaches the chatter."""

from __future__ import annotations

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


def _zip(content: bytes) -> zipfile.ZipFile | None:
    try:
        return zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return None


def _itertext(el) -> str:
    return "".join(el.itertext())


def extract_text(content: bytes, extension: str, limit: int = 20000) -> str:
    """Plain text of the document body, paragraph per line; empty when the format is not one we read."""
    ext = (extension or "").lower()
    if ext in ("txt", "md", "csv"):
        return content.decode("utf-8", "replace")[:limit]
    z = _zip(content)
    if z is None:
        return ""
    try:
        if ext == "docx" and "word/document.xml" in z.namelist():
            root = ElementTree.fromstring(z.read("word/document.xml"))
            paras = ["".join(t.text or "" for t in p.iter(W + "t")) for p in root.iter(W + "p")]
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
            paras = [_itertext(p) for p in root.iter() if p.tag in (TEXT + "p", TEXT + "h")]
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
    z = _zip(content)
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
    z = _zip(content)
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
    z = _zip(content)
    if z is None or not paragraphs:
        return None
    names = z.namelist()
    files = {n: z.read(n) for n in names}
    if ext == "docx" and "word/document.xml" in files:
        xml = files["word/document.xml"].decode("utf-8")
        block = "".join(docx_blocks(paragraphs))
        if position == "start":
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
        if position == "start":
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
    z = _zip(content)
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
    z = _zip(content)
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
'- [error] check: detail' or '- [warning] check: detail', then exactly one line 'Verdict: pass' or 'Verdict: fail'.""",
    "answer": """OUTPUT FORMAT for an answer in a comment thread: a few plain sentences a colleague can act on; cite the procedure
by name when you rely on it. No headings, no lists longer than three items.""",
}


def writing_interface(kind: str) -> str:
    return WRITING_INTERFACE.get(kind, "")
