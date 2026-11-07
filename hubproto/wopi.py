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
import re
import os
import pathlib
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
    "application/rtf": "rtf",
}
# markdown is the hub's own text format: shown and edited by the hub, not opened in the office editor
HUB_TEXT_EXTENSIONS = {"md"}
EDITABLE_EXTENSIONS = {v: k for k, v in EDITABLE_MIMES.items()}
VIEWABLE_EXTENSIONS = {"pdf": "application/pdf"}  # opened in the editor read-only
TOKEN_TTL = 10 * 60 * 60  # ten hours, the length of a working day

_SECRET_CACHE: dict[str, str] = {}


def secret(root: str | os.PathLike | None = None) -> str:
    """The signing secret: HUBPROTO_SECRET, else one generated once into <root>/state/.secret so every process on
    the same hub (shell, CLI, worker) accepts the same tokens."""
    env = os.environ.get("HUBPROTO_SECRET")
    if env:
        return env
    base = str(root or os.environ.get("HUBPROTO_ROOT") or ".")
    if base in _SECRET_CACHE:
        return _SECRET_CACHE[base]
    path = pathlib.Path(base) / "state" / ".secret"
    try:
        value = path.read_text().strip()
    except OSError:
        value = ""
    if not value:
        value = secrets.token_hex(32)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
            os.chmod(path, 0o600)
        except OSError:
            pass
    _SECRET_CACHE[base] = value
    return value


def wopi_time(iso: str | None, version: int | None = None) -> str:
    """WOPI clients parse one shape only: 2026-11-06T17:20:35.000000Z. With a version number, its microseconds are the
    version, so two saves in the same second still have different stamps (the stale-save check depends on it)."""
    if not iso:
        return ""
    base = iso.replace("Z", "").split("+", 1)[0]
    if version is not None:
        base = base.split(".", 1)[0] + f".{int(version) % 1_000_000:06d}"
    elif "." not in base:
        base += ".000000"
    return base + "Z"


def version_stamp(version: dict) -> str:
    """The LastModifiedTime of a document version, unique per version."""
    return wopi_time(version.get("at"), version.get("version"))


def is_stale_save(latest: dict, seen_stamp: str, machines: set[str]) -> bool:
    """A save from an editor session based on an older version, when the newer version was written by the hub or a
    cog (their edits must not be silently overwritten): the client is told to reload instead."""
    return bool(seen_stamp) and seen_stamp != version_stamp(latest) and (latest.get("by") in machines or latest.get("by") == "hub")


def extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def is_editable(rec: dict) -> bool:
    f = rec.get("fields") or {}
    return extension_of(f.get("filename") or "") in EDITABLE_EXTENSIONS or (f.get("mime") or "") in EDITABLE_MIMES


def is_viewable(rec: dict) -> bool:
    return is_editable(rec) or extension_of((rec.get("fields") or {}).get("filename") or "") in VIEWABLE_EXTENSIONS


# ---------------------------------------------------------------- access tokens
def issue_token(user: str, number: str, *, can_write: bool, ttl: int = TOKEN_TTL, root=None) -> str:
    payload = json.dumps({"u": user, "f": number, "w": bool(can_write), "exp": int(time.time()) + ttl}, separators=(",", ":"))
    body = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig = hmac.new(secret(root).encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def read_token(token: str, root=None) -> dict | None:
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, hmac.new(secret(root).encode(), body.encode(), hashlib.sha256).hexdigest()[:32]):
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
            if f.get("checked_out_via") == "editor":
                return True, ""  # an editing session: the editor merges everyone's changes
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
            "LastModifiedTime": version_stamp(version),
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
        f = rec["fields"]
        holder = f.get("checked_out_by") or user
        editors = sorted(set(f.get("editors") or []) | {holder, user})
        if not f.get("checked_out_by"):
            self.hub.dms.checkout(rec["locator"], by=user, via="editor")
        elif f.get("checked_out_via") == "editor" and user not in (f.get("editors") or []):
            self.hub.dms.join_editing(rec["locator"], by=user)
        _, latest = self.hub.dms.content(rec["locator"])
        self.hub.dms.add_version(rec["locator"], content, by=user, filename=latest.get("filename") or f.get("filename", ""),
                                 mime=latest.get("mime") or f.get("mime", ""), note="saved in the editor")
        # A new version is a check-in for the DMS; the editing session keeps the document until the last editor finishes.
        self.hub.dms.checkout(rec["locator"], by=holder, via="editor")
        self.hub.kernel.set_fields(rec["locator"], {"editors": editors}, by=user)
        _, version = self.hub.dms.content(rec["locator"])
        self.index(rec, content, user)
        return {"LastModifiedTime": version_stamp(version)}

    def index(self, rec: dict, content: bytes, user: str) -> list[dict]:
        """After a save: the document's text goes on the record (so reviews read it) and a comment written in the
        editor that mentions someone becomes a message on the item's chatter from the person who saved it."""
        from . import office

        ext = extension_of(rec["fields"].get("filename") or "")
        text = office.extract_text(content, ext)
        comments = office.extract_comments(content, ext)
        seen = {c.get("digest") for c in (rec["fields"].get("office_comments") or [])}
        fields = {"office_comments": [{k: c.get(k) for k in ("id", "author", "date", "text", "digest", "parent")} for c in comments]}
        if text:
            fields["text"] = text
        try:
            self.hub.kernel.set_fields(rec["locator"], fields, by=user)
        except KernelGuardError:
            pass
        posted = []
        members = (self.hub.releases.bundle(self.hub.releases.current()["digest"]) if self.hub.releases.current() else {}).get("members", {})
        cog_names = {n for n, m in members.items() if m.get("type") == "cog"}
        for c in comments:
            if c["digest"] in seen:
                continue
            # Never take identity from the file: a new comment is by the person who saved it, unless the name in the file is
            # another member in this live editing session (co-editors' comments are saved by whoever saves next).
            named = (c["author"] or "").strip().lower()
            session = set(rec["fields"].get("editors") or []) | {rec["fields"].get("checked_out_by")}
            author = named if (named in members and (named == user or (named in session and rec["fields"].get("checked_out_via") == "editor"))) else user
            if author in cog_names:
                continue  # a cog's own words never trigger a cog
            subject = f"Comment in {rec['number']}"
            if not office.mentions_in(c["text"]):
                # A reply in a thread where a cog has spoken is addressed to that cog, mention or not.
                thread = office.thread_of(comments, c["id"])
                spoken = [t["author"].strip().lower() for t in thread if t["author"].strip().lower() in cog_names and t["id"] != c["id"]]
                if not spoken or len(thread) < 2:
                    continue
                subject = f"@{spoken[-1]} reply in a thread in {rec['number']}"
            try:
                reply = self.hub.send(author, author, subject, c["text"], record=rec["locator"], comment_id=c["id"])
                posted.append(reply)
            except Exception as e:  # a comment that cannot be routed never breaks the save
                posted.append({"error": str(e)})
        return posted

    def pending_replies(self, rec: dict) -> list[dict]:
        """Chatter replies to comments in this document that are not yet written into it."""
        msgs = self.hub.inbox.on_record(rec["locator"])
        by_id = {m["id"]: m for m in msgs}
        comments = {str(c.get("id")): c for c in (rec["fields"].get("office_comments") or [])}
        out = []
        for m in msgs:
            if m.get("kind") != "reply" or m.get("applied_in"):
                continue
            parent = by_id.get(m.get("in_reply_to") or "")
            if parent and (parent.get("comment_id") not in (None, "") or m.get("edit")):
                cid = parent.get("comment_id")
                c = comments.get(str(cid)) if cid not in (None, "") else None
                out.append({**m, "comment_id": cid, "comment_text": (c or {}).get("text") or parent.get("body") or "",
                            "comment_author": (c or {}).get("author") or parent.get("from") or ""})
        return out

    def _applied_by_version(self, rec: dict) -> dict[str, int]:
        """Replies already written into a version (the version note names them): a crash between saving the version and
        marking the reply must not write the same edit twice."""
        out = {}
        for v in self.hub.dms.versions(rec["locator"]):
            for rid in re.findall(r"\breply ([0-9a-f]{8})\b", v.get("note") or ""):
                out[rid] = v.get("version")
        return out

    @staticmethod
    def writer(rec: dict, cog: str) -> str:
        """Who a version is credited to: the cog, unless a person holds the check-out outside a live editing session (the
        DMS then admits only the hub; the version note still names the cog and who asked)."""
        f = rec.get("fields") or {}
        locked = f.get("checked_out_by")
        if not locked or locked == cog or (f.get("checked_out_via") == "editor" and cog in (f.get("editors") or [])):
            return cog
        return "hub"

    @staticmethod
    def done_text(report: list[dict], stand_in: bool = False) -> str:
        """The reply as the outcome, not the plan: 'Done: ...' and 'Could not: ...' from the apply report."""
        ok = [f"{r['op']} ({r['detail']})" for r in report if r.get("ok")]
        bad = [f"{r['op']} ({r['detail']})" for r in report if not r.get("ok")]
        parts = []
        if ok:
            parts.append("Done: " + "; ".join(ok) + ".")
        if bad:
            parts.append("Could not: " + "; ".join(bad) + ".")
        return " ".join(parts) + (" (drafted with the stand-in: configure a model for real wording)" if stand_in else "")

    def apply_replies(self, rec: dict, by: str) -> list[dict]:
        """Write every pending reply into the document as a reply comment by its author (a new version), so the
        thread in the editor carries the answer. An edit is written only if the person who asked for it may still
        change the document; the reply then says what was done and what could not be."""
        from . import edits, office

        pending = self.pending_replies(rec)
        if not pending:
            return []
        already = self._applied_by_version(rec)
        for m in [m for m in pending if m["id"] in already]:
            self.hub.inbox.mark(m["id"], applied_in=already[m["id"]])
        pending = [m for m in pending if m["id"] not in already]
        if not pending:
            return []
        content, latest = self.hub.dms.content(rec["locator"])
        ext = extension_of(latest.get("filename") or rec["fields"].get("filename") or "")
        applied = []
        reports: dict[str, list[dict]] = {}
        for m in pending:
            new = content
            edit = m.get("edit") or {}
            body = m.get("body") or ""
            if edit.get("ops"):
                asker = edit.get("asked_by")
                ok, why = self.may_edit(self.hub.kernel.get(rec["locator"]), asker) if asker else (True, "")
                if not ok:
                    report = [{"op": edits.describe(o), "ok": False, "detail": why} for o in edit["ops"]]
                else:
                    changed, report = edits.apply_ops(new, ext, edit["ops"])
                    new = changed or new
                reports[m["id"]] = report
                body = self.done_text(report, edit.get("stand_in"))
            elif edit.get("paragraphs"):
                new = office.insert_paragraphs(new, ext, edit["paragraphs"], edit.get("position") or "start") or new
            if m.get("comment_id") not in (None, ""):
                new = office.add_reply(new, ext, str(m["comment_id"]), m["from"], body, date=m.get("at") or "") or new
            if new is content:
                if m["id"] in reports:  # nothing could be done: the reply says so, and it is not offered again
                    self.hub.inbox.mark(m["id"], applied_in="none", edit_report=reports[m["id"]], body=body)
                continue
            content = new
            m["_body"] = body
            applied.append(m)
        if not applied:
            return []
        holder = rec["fields"].get("checked_out_by") or by
        cogs = sorted({m["from"] for m in applied})
        askers = sorted({(m.get("edit") or {}).get("asked_by") for m in applied if (m.get("edit") or {}).get("asked_by")})
        cur = self.hub.kernel.get(rec["locator"])
        self.hub.dms.add_version(rec["locator"], content, by=self.writer(cur, cogs[0]) if len(cogs) == 1 else "hub", filename=latest.get("filename") or "",
                                 mime=latest.get("mime") or "",
                                 note=("edits and replies" if any(m.get("edit") for m in applied) else "replies") + " from " + ", ".join(cogs)
                                      + (f", asked by {', '.join(askers)}" if askers else "") + " written into the document ("
                                      + ", ".join(f"reply {m['id']}" for m in applied) + ")")
        if rec["fields"].get("checked_out_via") == "editor" or rec["fields"].get("checked_out_by"):
            self.hub.dms.checkout(rec["locator"], by=holder, via="editor")
            self.hub.kernel.set_fields(rec["locator"], {"editors": sorted(set(rec["fields"].get("editors") or []) | {holder})}, by=by)
        _, version = self.hub.dms.content(rec["locator"])
        for m in applied:
            extra = {"edit_report": reports[m["id"]], "body": m["_body"]} if m["id"] in reports else {}
            self.hub.inbox.mark(m["id"], applied_in=version.get("version"), **extra)
        comments = office.extract_comments(content, ext)
        self.hub.kernel.set_fields(rec["locator"], {"office_comments": [{k: c.get(k) for k in ("id", "author", "date", "text", "digest", "parent")} for c in comments],
                                                    "text": office.extract_text(content, ext)}, by=by)
        return applied

    def verify_edit(self, rec: dict, msg_id: str, by: str) -> dict:
        """After the cog's seat edited live and the session saved: check each operation in the saved file, do in the
        file whatever did not take, and mark the reply applied. Returns {"fixed", "failed", "version"}. Idempotent: a
        second call, or a call after a crash between the fix and the mark, does nothing twice."""
        from . import edits

        m = self.hub.inbox.find(msg_id)
        if m is None or m.get("record") != rec["locator"] or not (m.get("edit") or {}).get("ops"):
            raise KernelGuardError("edit", "no such edit on this document")
        already = self._applied_by_version(rec)
        if m.get("applied_in") or msg_id in already:
            if not m.get("applied_in"):
                self.hub.inbox.mark(msg_id, applied_in=already[msg_id])
            _, v = self.hub.dms.content(rec["locator"])
            return {"fixed": 0, "failed": [], "version": v.get("version"), "already": True}
        content, latest = self.hub.dms.content(rec["locator"])
        ext = extension_of(latest.get("filename") or rec["fields"].get("filename") or "")
        ops = m["edit"]["ops"]
        text = edits.applied_text(content, ext)
        grid = None
        if m["edit"].get("kind") == "sheet":
            try:
                grid = edits.Sheet(content, ext).grid()
            except Exception:
                grid = {}
        missing = [o for o in ops if not edits.op_applied(o, text, grid)]
        report = [{"op": edits.describe(o), "ok": True, "detail": "done live in the editor"} for o in ops if o not in missing]
        fixed = 0
        asker = m["edit"].get("asked_by")
        ok, why = self.may_edit(self.hub.kernel.get(rec["locator"]), asker) if asker else (True, "")
        if missing and not ok:
            report += [{"op": edits.describe(o), "ok": False, "detail": why} for o in missing]
            missing = []
        if missing:
            # An insert that landed in the wrong place live: take that copy out before the hub puts it where it belongs.
            cleanup = []
            if m["edit"].get("before_text") is not None:
                before = {edits.squash(x) for x in m["edit"]["before_text"].splitlines() if x.strip()}
                now = {edits.squash(x) for x in text.splitlines() if x.strip()}
                for o in missing:
                    for ln in (o.get("lines") or []) if o["op"] == "insert" else []:
                        pl = edits.plain_line(ln)
                        if pl and edits.squash(pl) in now and edits.squash(pl) not in before:
                            cleanup.append({"op": "delete_paragraph", "target": pl})
            if cleanup:
                content = edits.apply_ops(content, ext, cleanup)[0] or content
            new, rep = edits.apply_ops(content, ext, missing)
            report += [dict(r, detail=r["detail"] + " (by the hub, in the file)") for r in rep]
            if new is not None:
                holder = rec["fields"].get("checked_out_by") or by
                self.hub.dms.add_version(rec["locator"], new, by=self.writer(self.hub.kernel.get(rec["locator"]), m["from"]),
                                         filename=latest.get("filename") or "", mime=latest.get("mime") or "",
                                         note=f"edits from {m['from']}" + (f", asked by {asker}" if asker else "")
                                              + f" finished in the file ({sum(1 for r in rep if r['ok'])} of {len(missing)}; reply {m['id']})")
                if rec["fields"].get("checked_out_via") == "editor" or rec["fields"].get("checked_out_by"):
                    self.hub.dms.checkout(rec["locator"], by=holder, via="editor")
                fixed = sum(1 for r in rep if r["ok"])
                self.index(self.hub.kernel.get(rec["locator"]), new, m["from"])
        _, version = self.hub.dms.content(rec["locator"])
        self.hub.inbox.mark(m["id"], applied_in=version.get("version"), edit_report=report, body=self.done_text(report, m["edit"].get("stand_in")))
        return {"fixed": fixed, "failed": [r for r in report if not r["ok"]], "version": version.get("version")}

    def seat_token(self, rec: dict, cog_id: str, requester: str) -> str:
        """A seat in the live editing session for a cog that has something to say on this document: the cog's own
        token, so what it writes is attributed to it. Only someone in the session may seat a cog, and only a cog
        with a pending answer here."""
        f = rec["fields"]
        in_session = f.get("checked_out_via") == "editor" and (requester == f.get("checked_out_by") or requester in (f.get("editors") or []))
        if not in_session:
            raise KernelGuardError("seat", f"{requester} is not editing {rec['number']}")
        if cog_id not in {m["from"] for m in self.pending_replies(rec)}:
            raise KernelGuardError("seat", f"{cog_id} has nothing pending on {rec['number']}")
        self.hub.dms.join_editing(rec["locator"], by=cog_id)
        return issue_token(cog_id, rec["number"], can_write=True, ttl=2 * 3600, root=self.hub.root)

    def lock(self, rec: dict, claims: dict, lock_id: str) -> tuple[int, dict]:
        """Lock, RefreshLock and Unlock map onto the check-out; the lock id is remembered on the record."""
        user = claims["u"]
        if not claims.get("w"):  # a view-only session never locks (Collabora does not ask); a hand-made request may
            return 409, {"X-WOPI-Lock": rec["fields"].get("wopi_lock") or "", "X-WOPI-LockFailureReason": "this session is read-only"}
        ok, why = self.may_edit(rec, user)
        if ok:
            ok, why = self.can_write(rec, user)
        if not ok:
            return 409, {"X-WOPI-Lock": rec["fields"].get("wopi_lock") or "", "X-WOPI-LockFailureReason": why}
        if not rec["fields"].get("checked_out_by"):
            self.hub.dms.checkout(rec["locator"], by=user)
        self.hub.kernel.set_fields(rec["locator"], {"wopi_lock": lock_id}, by=user)
        return 200, {}

    def may_edit(self, rec: dict, user: str) -> tuple[bool, str]:
        from .tools import may_edit_document

        meta = self.hub.releases.current()
        bundle = self.hub.releases.bundle(meta["digest"]) if meta else {"members": {}}
        return may_edit_document(bundle, user, rec)

    def unlock(self, rec: dict, claims: dict, lock_id: str) -> tuple[int, dict]:
        if not claims.get("w"):
            return 409, {"X-WOPI-Lock": rec["fields"].get("wopi_lock") or "", "X-WOPI-LockFailureReason": "this session is read-only"}
        current = rec["fields"].get("wopi_lock") or ""
        if current and current != lock_id:
            return 409, {"X-WOPI-Lock": current}
        self.hub.kernel.set_fields(rec["locator"], {"wopi_lock": None}, by=claims["u"])
        return 200, {}

    def get_lock(self, rec: dict) -> tuple[int, dict]:
        return 200, {"X-WOPI-Lock": rec["fields"].get("wopi_lock") or ""}

    def finish(self, rec: dict, user: str) -> dict:
        """Leave the editing session: the last one out releases the document; nothing changes for a bystander."""
        f = rec["fields"]
        if f.get("checked_out_via") == "editor" and (user in (f.get("editors") or []) or f.get("checked_out_by") == user):
            return self.hub.dms.leave_editing(rec["locator"], by=user)
        if f.get("checked_out_by") == user:
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
# The paragraph styles the writing markup uses (headings, list paragraphs, a bordered table): without a styles part an
# editor maps 'Heading1' to the default style and headings lose their look.
DOCX_STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/>'
               '<w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="22"/></w:rPr></w:style>'
               + "".join(f'<w:style w:type="paragraph" w:styleId="Heading{n}"><w:name w:val="heading {n}"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
                         f'<w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="{360 - 80 * n}" w:after="120"/><w:outlineLvl w:val="{n - 1}"/></w:pPr>'
                         f'<w:rPr><w:b/><w:sz w:val="{36 - 4 * n}"/></w:rPr></w:style>' for n in (1, 2, 3))
               + '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/><w:rPr><w:b/><w:sz w:val="40"/></w:rPr></w:style>'
               '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="720"/></w:pPr></w:style>'
               '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders>'
               + "".join(f'<w:{b} w:val="single" w:sz="4" w:space="0" w:color="808080"/>' for b in ("top", "left", "bottom", "right", "insideH", "insideV"))
               + '</w:tblBorders></w:tblPr></w:style></w:styles>')


def blank_document(extension: str) -> tuple[bytes, str]:
    """A minimal, valid empty file for a new document: docx, xlsx, odt, ods, txt or md. Returns (bytes, mime)."""
    ext = extension.lower()
    if ext in ("txt", "md", "csv"):
        return b"", {"txt": "text/plain", "md": "text/markdown", "csv": "text/csv"}[ext]
    buf = io.BytesIO()
    if ext == "docx":
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
            z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            z.writestr("word/_rels/document.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
            z.writestr("word/styles.xml", DOCX_STYLES)
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
