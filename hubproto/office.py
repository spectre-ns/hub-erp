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
