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
        if ext in ("odt", "ods", "odp") and "content.xml" in z.namelist():
            root = ElementTree.fromstring(z.read("content.xml"))
            paras = [_itertext(p) for p in root.iter() if p.tag in (TEXT + "p", TEXT + "h")]
            return "\n".join(p for p in paras if p.strip())[:limit]
        if ext == "xlsx" and "xl/sharedStrings.xml" in z.namelist():
            root = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            return "\n".join(_itertext(si) for si in root.iter(ns + "si"))[:limit]
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
            for c in root.iter(W + "comment"):
                text = "\n".join("".join(t.text or "" for t in p.iter(W + "t")) for p in c.iter(W + "p")).strip()
                out.append({"id": c.get(W + "id") or "", "author": c.get(W + "author") or "", "date": c.get(W + "date") or "", "text": text})
        elif ext in ("odt", "ods", "odp") and "content.xml" in z.namelist():
            root = ElementTree.fromstring(z.read("content.xml"))
            for i, a in enumerate(root.iter(OFFICE + "annotation")):
                creator = a.find(DC + "creator")
                date = a.find(DC + "date")
                text = "\n".join(_itertext(p) for p in a.iter(TEXT + "p")).strip()
                out.append({"id": a.get(OFFICE + "name") or str(i), "author": (creator.text if creator is not None else "") or "",
                            "date": (date.text if date is not None else "") or "", "text": text})
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
                    out.append({"id": f"{name}:{c.get('ref') or i}", "author": author, "date": "", "text": _itertext(c).strip()})
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
