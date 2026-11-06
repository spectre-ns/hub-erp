"""WOPI host: the hub serves its documents to an online office editor (Collabora Online; SharePoint's Office
Online speaks the same protocol) and takes saves back as DMS versions under the check-out rules.

Endpoints (served by the shell, authenticated by an access token, never by ``as``):

- ``GET  /wopi/files/<number>``            CheckFileInfo
- ``GET  /wopi/files/<number>/contents``   GetFile
- ``POST /wopi/files/<number>/contents``   PutFile (``X-WOPI-Override: PUT``): a new version by the editing user
- ``POST /wopi/files/<number>``            Lock / Unlock / RefreshLock / GetLock, mapped to the check-out

Editing rules: opening the editor checks the document out to the user (if nobody else holds it); every save
is a minor version noted "saved in the editor"; "Finish editing" releases the check-out. Someone else's
check-out opens the document read-only. Controlled documents in effect are never editable (the DMS refuses).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import time
import zipfile
from urllib.parse import quote
from urllib.request import urlopen
from xml.etree import ElementTree

from .kernel import KernelGuardError

EDITABLE_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "application/msword": "doc", "application/vnd.ms-excel": "xls", "text/plain": "txt", "text/csv": "csv",
    "text/markdown": "md", "application/rtf": "rtf",
}
EDITABLE_EXTENSIONS = {v: k for k, v in EDITABLE_MIMES.items()}
VIEWABLE_EXTENSIONS = {"pdf": "application/pdf"}  # opened in the editor read-only
TOKEN_TTL = 10 * 60 * 60  # ten hours, the length of a working day

_SECRET = os.environ.get("HUBPROTO_SECRET") or secrets.token_hex(32)


def extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def is_editable(rec: dict) -> bool:
    f = rec.get("fields") or {}
    return extension_of(f.get("filename") or "") in EDITABLE_EXTENSIONS or (f.get("mime") or "") in EDITABLE_MIMES


def is_viewable(rec: dict) -> bool:
    return is_editable(rec) or extension_of((rec.get("fields") or {}).get("filename") or "") in VIEWABLE_EXTENSIONS


# ---------------------------------------------------------------- access tokens
def issue_token(user: str, number: str, *, can_write: bool, ttl: int = TOKEN_TTL) -> str:
    payload = json.dumps({"u": user, "f": number, "w": bool(can_write), "exp": int(time.time()) + ttl}, separators=(",", ":"))
    body = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig = hmac.new(_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def read_token(token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, hmac.new(_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, UnicodeDecodeError):
        return None
    if data.get("exp", 0) < time.time():
        return None
    return data


# ---------------------------------------------------------------- the host
class WopiHost:
    """Pure WOPI logic over the DMS; the shell wires the HTTP side."""

    def __init__(self, hub):
        self.hub = hub

    def document(self, number: str) -> dict | None:
        rec = self.hub.kernel.get(number if ":" in number else f"rec:document:{number}")
        return rec if rec and rec["type"] == "document" else None

    def can_write(self, rec: dict, user: str) -> tuple[bool, str]:
        """Whether this user may save versions right now: the DMS's check-out rules decide."""
        f = rec["fields"]
        if f.get("doc_class") in ("evidence", "external"):
            return False, f"{f.get('doc_class')} documents are never edited"
        if f.get("doc_class") in ("controlled", "template") and rec["state"] not in ("open", "draft", "returned", "failed"):
            return False, f"{rec['number']} is {rec['state']}; a controlled document changes only through a new revision"
        locked = f.get("checked_out_by")
        if locked and locked != user:
            return False, f"checked out by {locked}"
        return True, ""

    def check_file_info(self, rec: dict, claims: dict, *, post_message_origin: str = "") -> dict:
        content, version = self.hub.dms.content(rec["locator"])
        f = rec["fields"]
        writable = bool(claims.get("w")) and self.can_write(rec, claims["u"])[0]
        return {
            "BaseFileName": version.get("filename") or f.get("filename") or f"{rec['number']}.{extension_of(f.get('filename') or '') or 'bin'}",
            "Size": len(content),
            "Version": str(version.get("version")),
            "OwnerId": f.get("owner") or f.get("author") or rec.get("created_by") or "hub",
            "UserId": claims["u"],
            "UserFriendlyName": claims["u"],
            "UserCanWrite": writable,
            "ReadOnly": not writable,
            "UserCanNotWriteRelative": True,
            "SupportsUpdate": True,
            "SupportsLocks": True,
            "SupportsGetLock": True,
            "SupportsRename": False,
            "DisablePrint": False,
            "DisableExport": False,
            "DisableCopy": False,
            "HidePrintOption": False,
            "EnableOwnerTermination": False,
            "LastModifiedTime": version.get("at"),
            "PostMessageOrigin": post_message_origin,
            "HubNumber": rec["number"],
            "HubTitle": f.get("title"),
            "HubState": rec["state"],
        }

    def get_file(self, rec: dict) -> tuple[bytes, dict]:
        return self.hub.dms.content(rec["locator"])

    def put_file(self, rec: dict, claims: dict, content: bytes) -> dict:
        if not claims.get("w"):
            raise KernelGuardError("read-only", "this session opened the document read-only")
        ok, why = self.can_write(rec, claims["u"])
        if not ok:
            raise KernelGuardError("read-only", why)
        user = claims["u"]
        if not rec["fields"].get("checked_out_by"):
            self.hub.dms.checkout(rec["locator"], by=user)
        _, latest = self.hub.dms.content(rec["locator"])
        self.hub.dms.add_version(rec["locator"], content, by=user, filename=latest.get("filename") or rec["fields"].get("filename", ""),
                                 mime=latest.get("mime") or rec["fields"].get("mime", ""), note="saved in the editor")
        # A new version is a check-in for the DMS; the editing session keeps the document until "Finish editing".
        self.hub.dms.checkout(rec["locator"], by=user)
        _, version = self.hub.dms.content(rec["locator"])
        return {"LastModifiedTime": version.get("at")}

    def lock(self, rec: dict, claims: dict, lock_id: str) -> tuple[int, dict]:
        """Lock, RefreshLock and Unlock map onto the check-out; the lock id is remembered on the record."""
        user = claims["u"]
        ok, why = self.can_write(rec, user)
        if not ok:
            return 409, {"X-WOPI-Lock": rec["fields"].get("wopi_lock") or "", "X-WOPI-LockFailureReason": why}
        if not rec["fields"].get("checked_out_by"):
            self.hub.dms.checkout(rec["locator"], by=user)
        self.hub.kernel.set_fields(rec["locator"], {"wopi_lock": lock_id}, by=user)
        return 200, {}

    def unlock(self, rec: dict, claims: dict, lock_id: str) -> tuple[int, dict]:
        current = rec["fields"].get("wopi_lock") or ""
        if current and current != lock_id:
            return 409, {"X-WOPI-Lock": current}
        self.hub.kernel.set_fields(rec["locator"], {"wopi_lock": None}, by=claims["u"])
        return 200, {}

    def get_lock(self, rec: dict) -> tuple[int, dict]:
        return 200, {"X-WOPI-Lock": rec["fields"].get("wopi_lock") or ""}

    def finish(self, rec: dict, user: str) -> dict:
        """Release the check-out after editing; nothing changes if the user does not hold it."""
        if rec["fields"].get("checked_out_by") == user:
            self.hub.kernel.set_fields(rec["locator"], {"wopi_lock": None}, by=user)
            return self.hub.dms.undo_checkout(rec["locator"], by=user)
        return rec


# ---------------------------------------------------------------- the editor's location
_discovery_cache: dict[str, tuple[float, dict[str, str]]] = {}


def collabora_urls() -> tuple[str, str]:
    """(editor URL the browser reaches, hub URL the editor reaches) or ('', '') when no editor is configured."""
    browser = (os.environ.get("HUBPROTO_COLLABORA_URL") or "").rstrip("/")
    host = (os.environ.get("HUBPROTO_WOPI_BASE") or "").rstrip("/")
    return browser, host


def editor_urlsrc(extension: str, *, browser_url: str, discovery_url: str = "") -> str:
    """The editor's page for a file type, from its discovery document (cached), or the known default."""
    override = os.environ.get("HUBPROTO_COLLABORA_URLSRC")
    if override:
        return override
    key = discovery_url or browser_url
    now = time.time()
    cached = _discovery_cache.get(key)
    if cached is None or now - cached[0] > 3600:
        table: dict[str, str] = {}
        try:
            with urlopen((discovery_url or browser_url) + "/hosting/discovery", timeout=5) as r:
                root = ElementTree.fromstring(r.read())
            for app in root.iter("app"):
                for action in app.iter("action"):
                    ext = (action.get("ext") or "").lower()
                    if ext and action.get("name") in ("edit", "view") and ext not in table:
                        table[ext] = action.get("urlsrc") or ""
        except Exception:
            table = {}
        _discovery_cache[key] = (now, table)
        cached = _discovery_cache[key]
    url = cached[1].get(extension.lower())
    if url:
        # The discovery document names the editor by the server name it was given; the browser must use ours.
        path = url.split("//", 1)[1].split("/", 1)[1] if "//" in url else url
        return browser_url + "/" + path
    return browser_url + "/browser/dist/cool.html?"


def editor_frame_url(rec: dict, token: str, *, browser_url: str, host_url: str, discovery_url: str = "") -> str:
    ext = extension_of(rec["fields"].get("filename") or "") or "txt"
    src = editor_urlsrc(ext, browser_url=browser_url, discovery_url=discovery_url)
    wopi_src = f"{host_url}/wopi/files/{rec['number']}"
    sep = "&" if src.endswith("?") or "?" in src and not src.endswith("?") else "?"
    if src.endswith("?"):
        sep = ""
    return f"{src}{sep}WOPISrc={quote(wopi_src, safe='')}&access_token={quote(token, safe='')}"


# ---------------------------------------------------------------- blank documents
def blank_document(extension: str) -> tuple[bytes, str]:
    """A minimal, valid empty file for a new document: docx, xlsx, odt, ods, txt or md. Returns (bytes, mime)."""
    ext = extension.lower()
    if ext in ("txt", "md", "csv"):
        return b"", EDITABLE_EXTENSIONS[ext]
    buf = io.BytesIO()
    if ext == "docx":
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            z.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/><w:sectPr/></w:body></w:document>')
        return buf.getvalue(), EDITABLE_EXTENSIONS["docx"]
    if ext == "xlsx":
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
            z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
            z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr("xl/worksheets/sheet1.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>')
        return buf.getvalue(), EDITABLE_EXTENSIONS["xlsx"]
    if ext in ("odt", "ods"):
        mime = EDITABLE_EXTENSIONS[ext]
        body = ('<office:text><text:p/></office:text>' if ext == "odt"
                else '<office:spreadsheet><table:table table:name="Sheet1"><table:table-column/><table:table-row><table:table-cell/></table:table-row></table:table></office:spreadsheet>')
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(zipfile.ZipInfo("mimetype"), mime, compress_type=zipfile.ZIP_STORED)
            z.writestr("META-INF/manifest.xml", f'<?xml version="1.0" encoding="UTF-8"?><manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2"><manifest:file-entry manifest:full-path="/" manifest:media-type="{mime}"/><manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>')
            z.writestr("content.xml", f'<?xml version="1.0" encoding="UTF-8"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" office:version="1.2"><office:body>{body}</office:body></office:document-content>')
        return buf.getvalue(), mime
    raise KernelGuardError("format", f"cannot create a blank .{ext}; choose docx, xlsx, odt, ods, txt or md")
