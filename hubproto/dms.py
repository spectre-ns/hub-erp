"""The document management system on top of the record system.

A document is a record of type ``document``. Its content lives in a
content-addressed blob store with a version history; the record carries the
metadata, class, revision and lifecycle state. Documents attach to any record
by a ``has-document`` link, once stored, however many records refer to them.

The file is the one source of a document's words: nothing keeps a copy of its
text on the record. ``DMS.text_of`` reads them from the version's file (cached
by the file's hash, so a cached reading can never be of other content), and
search, previews, reviews and the cogs all read through it.

Classes: controlled (SOPs, forms, drawings), evidence (signed forms,
certificates, snapshots), external (received files, immutable), working
(drafts), template.
"""

from __future__ import annotations

import hashlib
import os
import json
import mimetypes
import threading
from collections import OrderedDict
from datetime import date
from pathlib import Path

from .kernel import Kernel, KernelGuardError
from .release import utcnow

CLASSES = ("controlled", "evidence", "external", "working", "template")
IMMUTABLE_CLASSES = ("evidence", "external")
TEXT_MIMES = ("text/", "application/json", "application/x-yaml", "application/xml")


class BlobError(OSError):
    """A stored blob does not match its hash (torn by a crash or changed on disk)."""


class BlobStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes) -> str:
        """Store content under its hash. Written to a temporary file, flushed to disk and renamed into place, so a
        crash never leaves a half-written blob under the final name; a blob already there that does not match (torn
        by an older crash) is rewritten."""
        sha = hashlib.sha256(content).hexdigest()
        path = self.root / sha
        if path.exists():
            try:
                if path.stat().st_size == len(content) and hashlib.sha256(path.read_bytes()).hexdigest() == sha:
                    return sha
            except OSError:
                pass
        tmp = self.root / f".{sha}.{os.getpid()}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return sha

    def get(self, sha: str) -> bytes:
        data = (self.root / sha).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise BlobError(f"stored content {sha[:12]} is damaged (its bytes no longer match its hash); restore it from a backup")
        return data

    def exists(self, sha: str) -> bool:
        return (self.root / sha).exists()


# The most a zip may inflate to (or inflate by) before its text is not extracted: a 600 KB "zip bomb" otherwise
# takes gigabytes of memory under the hub lock.
MAX_INFLATED = int(os.environ.get("HUBPROTO_MAX_INFLATED") or 200 * 1024 * 1024)
MAX_RATIO = 200


def zip_too_big(content: bytes) -> str:
    """Why a zip package is not safe to open (empty when it is): what it would inflate to, by member and in total."""
    import io
    import zipfile

    try:
        z = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return ""
    total = sum(max(0, i.file_size) for i in z.infolist())
    if total > MAX_INFLATED:
        return f"it would inflate to {total:,} bytes (the limit is {MAX_INFLATED:,})"
    if content and total / max(1, len(content)) > MAX_RATIO and total > 10 * 1024 * 1024:
        return f"it inflates {total // max(1, len(content))} times its size"
    return ""


def extract_text(content: bytes, mime: str, filename: str = "") -> str:
    """Plain-text extraction for search and for the cogs: text files as they are; office files (docx, xlsx, odt,
    ods, pptx) through the office reader. Production uses Tika and OCR."""
    if any(mime.startswith(t) for t in TEXT_MIMES):
        try:
            return content.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover
            return ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if not ext:
        ext = {"application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
               "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
               "application/vnd.oasis.opendocument.text": "odt", "application/vnd.oasis.opendocument.spreadsheet": "ods"}.get(mime, "")
    if ext in ("docx", "xlsx", "pptx", "odt", "ods", "odp"):
        from . import office

        if zip_too_big(content):
            return ""
        try:
            return office.extract_text(content, ext, limit=None)  # the whole document: a reading is never cut short
        except Exception:
            return ""
    return ""


def next_revision(rev: str | None) -> str:
    if not rev:
        return "A"
    if rev.isalpha():
        if rev == "Z":
            return "AA"
        return chr(ord(rev[-1]) + 1) if len(rev) == 1 else rev[:-1] + chr(ord(rev[-1]) + 1)
    return str(int(rev) + 1)


class _Reader:
    """What ``sopdocs.frame_text`` needs of a hub: its documents."""

    def __init__(self, dms: "DMS"):
        self.dms = dms


class _TextCache:
    """Readings of files, least recently used first, bounded by the characters they hold (not by their number, so a
    search over every document does not evict each reading before it is used again). One lock: the shell's handler
    threads and the queue worker share it."""

    def __init__(self, max_chars: int = 64_000_000):
        self.max_chars = max_chars
        self.items: OrderedDict[tuple, str] = OrderedDict()
        self.chars = 0
        self.lock = threading.Lock()

    def get(self, key: tuple) -> str | None:
        with self.lock:
            text = self.items.get(key)
            if text is not None:
                self.items.move_to_end(key)
            return text

    def put(self, key: tuple, text: str) -> str:
        with self.lock:
            old = self.items.pop(key, None)
            self.chars -= len(old) if old is not None else 0
            self.items[key] = text
            self.chars += len(text)
            while self.chars > self.max_chars and len(self.items) > 1:
                _, gone = self.items.popitem(last=False)
                self.chars -= len(gone)
        return text


class DMS:
    def __init__(self, kernel: Kernel, blobs: BlobStore):
        self.kernel = kernel
        self.blobs = blobs
        self._texts = _TextCache()

    # ------------------------------------------------------------------ text
    def text_of(self, ref: str | dict, version: int | None = None) -> str:
        """A document's words, read from the file of a version (the latest by default): "" when it has no file, or
        none a reader takes words from (a PDF, an image). The record keeps no copy, so this is the one reading of a
        document's text. An SOP or policy document reads as the compile reads it (``sopdocs.frame_text``: its header,
        and its body with a line per picture), so a reviewing cog, the preview and the compile see the same lines.
        Cached by the file's hash, mime and name (and an SOP's header), so it is never a reading of other content."""
        loc = ref.get("locator") if isinstance(ref, dict) else ref
        rec = self.kernel.get(loc) if loc else None  # always the record as it is now, not a copy the caller held
        if not rec or rec.get("type") != "document":
            return ""
        f = rec["fields"]
        if version is None and f.get("content_sha"):
            sha, mime, filename = f["content_sha"], f.get("mime") or "", f.get("filename") or ""
        else:
            vs = self.versions(rec["locator"])
            v = (vs[-1] if vs else None) if version is None else next((x for x in vs if x["version"] == version), None)
            if v is None:
                return ""
            sha, mime, filename = v["sha256"], v.get("mime") or "", v.get("filename") or ""
        from . import sopdocs

        sop = str(f.get("lineage") or "").startswith(sopdocs.LINEAGE)
        key = (sha, mime, filename) + ((f.get("frame_header") or json.dumps(f.get("frame_meta") or {}, sort_keys=True),
                                         f.get("frame_tail") or 1) if sop else ())
        hit = self._texts.get(key)
        if hit is not None:
            return hit
        try:
            if sop:
                return self._texts.put(key, sopdocs.frame_text(_Reader(self), rec, version))
            content = self.blobs.get(sha)
        except (OSError, KeyError, ValueError, StopIteration):
            return ""
        return self._texts.put(key, extract_text(content, mime, filename))

    def edit_text(self, ref: str | dict, version: int | None = None) -> str:
        """A document's paragraphs as the edit operations find and check them (``edits.applied_text``: a paragraph per
        line, a heading or a table cell as its words alone, no SOP header): what an edit a cog asks for is compared with,
        before and after. Not what people and cogs read (``text_of`` reads an SOP as the compile does, with '#' headings
        and '|' rows, which an edit never matches). "" when there is no file."""
        from .edits import applied_text

        loc = ref.get("locator") if isinstance(ref, dict) else ref
        rec = self.kernel.get(loc) if loc else None
        if not rec or rec.get("type") != "document":
            return ""
        try:
            content, v = self.content(rec["locator"], version)
        except (OSError, KeyError, ValueError, StopIteration, KernelGuardError):
            return ""
        name = v.get("filename") or rec["fields"].get("filename") or ""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        key = ("edit", v["sha256"], ext)
        hit = self._texts.get(key)
        if hit is not None:
            return hit
        return self._texts.put(key, applied_text(content, ext))

    # ---------------------------------------------------------------- create
    def create(self, *, title: str, doc_class: str, folder: str, by: str, content: bytes | None = None,
               filename: str = "", mime: str = "", doc_type: str = "", fields: dict | None = None,
               run_id: str = "", release: str = "", state: str | None = None, provenance: dict | None = None,
               attach_to: str | None = None) -> dict:
        if doc_class not in CLASSES:
            raise KernelGuardError("document-class", f"unknown document class {doc_class!r}")
        f = {"title": title, "doc_class": doc_class, "doc_type": doc_type, "owner": by, "author": by,
             "revision": "A" if doc_class in ("controlled", "template") else "1", "minor": 0,
             "language": "en", "provenance": provenance or {}}
        f.update(fields or {})
        default_state = {"controlled": "draft", "template": "draft", "evidence": "filed", "external": "received",
                         "working": "open"}[doc_class]
        rec = self.kernel.create("document", folder, f, by=by, run_id=run_id, release=release, state=state or default_state)
        if content is not None:
            self.add_version(rec["locator"], content, by=by, filename=filename, mime=mime, run_id=run_id,
                             release=release, note="initial")
        if attach_to:
            self.attach(attach_to, rec["locator"], by=by, run_id=run_id, release=release)
        return self.kernel.get(rec["locator"])

    # -------------------------------------------------------------- versions
    def add_version(self, ref: str, content: bytes, *, by: str, filename: str = "", mime: str = "", note: str = "",
                    run_id: str = "", release: str = "", system: bool = False) -> dict:
        rec = self.kernel.require(ref)
        if rec["type"] != "document":
            raise KernelGuardError("document", f"{rec['number']} is not a document")
        if rec["fields"].get("doc_class") in IMMUTABLE_CLASSES and self.versions(ref):
            raise KernelGuardError("immutable", f"{rec['fields']['doc_class']} documents cannot be changed; file a new one that supersedes it")
        if not system and rec["fields"].get("doc_class") in ("controlled", "template") and rec["state"] not in ("open", "draft", "returned", "failed"):
            raise KernelGuardError("controlled", f"{rec['number']} is {rec['state']}; a controlled document changes only through a new revision (start document-control with the same title)")
        locked_by = rec["fields"].get("checked_out_by")
        in_session = rec["fields"].get("checked_out_via") == "editor" and by in (rec["fields"].get("editors") or [])
        if locked_by and locked_by != by and not in_session and by != "hub":
            raise KernelGuardError("checked-out", f"{rec['number']} is checked out by {locked_by}")
        mime = mime or (mimetypes.guess_type(filename)[0] if filename else "") or (rec["fields"].get("mime") if self.versions(ref) else "") or "application/octet-stream"
        sha = self.blobs.put(content)
        n = len(self.versions(ref)) + 1
        text = extract_text(content, mime, filename)
        self.kernel.append("document.version_added", rec["locator"],
                           {"version": n, "sha256": sha, "filename": filename, "mime": mime, "size": len(content),
                            "note": note, "text_excerpt": text[:2000]}, run_id=run_id, release=release, by=by)
        self.kernel.set_fields(rec["locator"], {"content_sha": sha, "filename": filename, "mime": mime, "size": len(content),
                                                "checked_out_by": None, "minor": int(rec["fields"].get("minor") or 0) + (1 if n > 1 else 0)},
                               by=by, run_id=run_id, release=release)
        return self.kernel.get(rec["locator"])

    def versions(self, ref: str) -> list[dict]:
        rec = self.kernel.require(ref)
        return [{**ev["data"], "at": ev["at"], "by": ev["by"], "run_id": ev["run_id"]}
                for ev in self.kernel.events(aggregate=rec["locator"]) if ev["type"] == "document.version_added"]

    def content(self, ref: str, version: int | None = None) -> tuple[bytes, dict]:
        vs = self.versions(ref)
        if not vs:
            raise KernelGuardError("no-content", f"{ref} has no content")
        v = vs[-1] if version is None else next(x for x in vs if x["version"] == version)
        return self.blobs.get(v["sha256"]), v

    def checkout(self, ref: str, *, by: str, run_id: str = "", release: str = "", via: str = "manual") -> dict:
        """Take the document to work on it. ``via="editor"`` opens an editing session that others may join
        (``join_editing``); a manual check-out is one person's alone."""
        rec = self.kernel.require(ref)
        if rec["fields"].get("doc_class") in IMMUTABLE_CLASSES:
            raise KernelGuardError("immutable", "evidence and external documents are never edited")
        if rec["fields"].get("doc_class") in ("controlled", "template") and rec["state"] not in ("open", "draft", "returned", "failed"):
            raise KernelGuardError("controlled", f"{rec['number']} is {rec['state']} and cannot be checked out; revise it through document-control")
        locked_by = rec["fields"].get("checked_out_by")
        if locked_by and locked_by != by:
            if via == "editor" and rec["fields"].get("checked_out_via") == "editor":
                return self.join_editing(ref, by=by, run_id=run_id, release=release)
            raise KernelGuardError("checked-out", f"{rec['number']} is checked out by {locked_by}")
        editors = sorted(set(rec["fields"].get("editors") or []) | {by}) if via == "editor" else []
        self.kernel.append("document.checked_out", rec["locator"], {"by": by, "via": via}, run_id=run_id, release=release, by=by)
        return self.kernel.set_fields(rec["locator"], {"checked_out_by": by, "checked_out_via": via, "editors": editors}, by=by,
                                      run_id=run_id, release=release)

    machine_names = staticmethod(lambda: set())  # set by the Hub: cog ids, which never hold a document's lock

    def join_editing(self, ref: str, *, by: str, run_id: str = "", release: str = "") -> dict:
        """Join an open editing session: the editor merges everyone's changes; the session's lock stays with whoever opened it."""
        rec = self.kernel.require(ref)
        if rec["fields"].get("checked_out_via") != "editor" or not rec["fields"].get("checked_out_by"):
            raise KernelGuardError("not-editing", f"{rec['number']} is not in an editing session")
        editors = sorted(set(rec["fields"].get("editors") or [rec["fields"]["checked_out_by"]]) | {by})
        self.kernel.append("document.editing_joined", rec["locator"], {"by": by}, run_id=run_id, release=release, by=by)
        return self.kernel.set_fields(rec["locator"], {"editors": editors}, by=by, run_id=run_id, release=release)

    def leave_editing(self, ref: str, *, by: str, run_id: str = "", release: str = "") -> dict:
        """Finish editing: the last one out releases the session; otherwise the lock passes to someone still in it."""
        rec = self.kernel.require(ref)
        f = rec["fields"]
        editors = [e for e in (f.get("editors") or []) if e != by]
        people = [e for e in editors if e not in self.machine_names()]
        if not people or f.get("checked_out_via") != "editor":
            self.kernel.append("document.checkout_undone", rec["locator"], {"by": by}, run_id=run_id, release=release, by=by)
            return self.kernel.set_fields(rec["locator"], {"checked_out_by": None, "checked_out_via": None, "editors": [], "wopi_lock": None},
                                          by=by, run_id=run_id, release=release)
        holder = f.get("checked_out_by") if f.get("checked_out_by") in people else people[0]
        self.kernel.append("document.editing_left", rec["locator"], {"by": by, "holder": holder}, run_id=run_id, release=release, by=by)
        return self.kernel.set_fields(rec["locator"], {"checked_out_by": holder, "editors": editors}, by=by, run_id=run_id, release=release)

    def undo_checkout(self, ref: str, *, by: str, run_id: str = "", release: str = "") -> dict:
        """Release a check-out without a new version; only the holder may."""
        rec = self.kernel.require(ref)
        locked_by = rec["fields"].get("checked_out_by")
        if not locked_by:
            raise KernelGuardError("not-checked-out", f"{rec['number']} is not checked out")
        if locked_by != by:
            raise KernelGuardError("checked-out", f"{rec['number']} is checked out by {locked_by}; only they can undo it")
        self.kernel.append("document.checkout_undone", rec["locator"], {"by": by}, run_id=run_id, release=release, by=by)
        return self.kernel.set_fields(rec["locator"], {"checked_out_by": None, "checked_out_via": None, "editors": []}, by=by, run_id=run_id, release=release)

    def checkin(self, ref: str, content: bytes, *, by: str, filename: str = "", mime: str = "", note: str = "",
                run_id: str = "", release: str = "") -> dict:
        rec = self.kernel.require(ref)
        locked_by = rec["fields"].get("checked_out_by")
        if locked_by and locked_by != by:
            raise KernelGuardError("checked-out", f"{rec['number']} is checked out by {locked_by}")
        return self.add_version(ref, content, by=by, filename=filename or rec["fields"].get("filename", ""),
                                mime=mime or rec["fields"].get("mime", ""), note=note or "check-in", run_id=run_id,
                                release=release)

    def release_revision(self, ref: str, *, by: str, run_id: str = "", release: str = "") -> dict:
        """Promote the working minor versions to the next major revision (used when a controlled document becomes effective)."""
        rec = self.kernel.require(ref)
        rev = rec["fields"].get("revision") or "A"
        if rec["transitions"] and any(t["to"] == "effective" for t in rec["transitions"]):
            rev = next_revision(rev)
        return self.kernel.set_fields(rec["locator"], {"revision": rev, "minor": 0}, by=by, run_id=run_id, release=release)

    # ----------------------------------------------------------- attachments
    def attach(self, record_ref: str, doc_ref: str, *, by: str, run_id: str = "", release: str = "") -> None:
        self.kernel.link(record_ref, "has-document", doc_ref, by=by, run_id=run_id, release=release)

    def documents_of(self, record_ref: str) -> list[dict]:
        rec = self.kernel.require(record_ref)
        out = []
        for l in rec["links"]:
            if l["rel"] == "has-document":
                d = self.kernel.get(l["target"])
                if d:
                    out.append(d)
        return out

    # ------------------------------------------------------------ rendering
    def render_snapshot(self, record_ref: str, *, by: str, run_id: str = "", release: str = "", title: str = "",
                        members: dict | None = None, body: str | None = None) -> dict:
        """Render a record to Markdown and file it as an evidence document attached to the record. ``members`` (the
        release's) names and roles each signer; ``body`` replaces the generic fields section (a change order's record,
        ``hubproto.co_record``)."""
        rec = self.kernel.require(record_ref)
        md = render_markdown(self.kernel, rec, members=members, body=body)
        doc = self.create(title=title or f"{rec['number']} snapshot at {rec['state']}", doc_class="evidence",
                          folder=rec["folder"], by=by, content=md.encode("utf-8"), filename=f"{rec['number']}-{rec['state']}.md",
                          mime="text/markdown", doc_type="snapshot", run_id=run_id, release=release,
                          provenance={"record": rec["locator"], "state": rec["state"], "rendered_at": utcnow()},
                          attach_to=rec["locator"])
        return doc

    def intake(self, *, folder: str, by: str, content: bytes, filename: str, channel: str, sender: str,
               attach_to: str | None = None, run_id: str = "", release: str = "", title: str = "",
               doc_class: str = "external") -> dict:
        """File a received file: as an external document (immutable, with provenance) by default, or as a
        working document the uploader goes on editing (``doc_class="working"``)."""
        return self.create(title=title or filename, doc_class=doc_class, folder=folder, by=by, content=content,
                           filename=filename, doc_type="received" if doc_class == "external" else "document", run_id=run_id,
                           release=release, provenance={"channel": channel, "sender": sender, "received_at": utcnow()},
                           attach_to=attach_to)


def render_markdown(kernel: Kernel, rec: dict, members: dict | None = None, body: str | None = None) -> str:
    from .co_record import signature_lines, when_utc

    lines = [f"# {rec['number']} ({rec['type']})", "", f"State: **{rec['state']}**  ", f"Folder: {rec['folder']}  ",
             f"Created {when_utc(rec['created_at'])} by {rec['created_by']}  ",
             f"Policy release: `{(rec.get('release') or '')[:16]}`  Run: `{rec.get('run_id') or ''}`", ""]
    if body is not None:
        lines += [body, ""]
    else:
        lines += ["## Fields", ""]
        system = set((kernel.types.get(rec["type"]) or {}).get("system_fields") or [])
        for k, v in sorted(rec["fields"].items()):
            if k in ("text",) or v in (None, "", [], {}):
                continue
            if isinstance(v, (dict, list)):
                if k in system:
                    continue  # the system's own bookkeeping: not part of the record as people read it
                v = json.dumps(v, sort_keys=True, default=str)
            lines.append(f"- **{k}**: {v}")
    links = kernel.links_of(rec["locator"])
    reqs = [kernel.get(l["target"]) for l in links if l["rel"] == "fulfils" and l["direction"] == "out"]
    reqs = [r for r in reqs if r and r["type"] == "requirement"]
    if reqs:
        lines += ["", "## Requirements", ""]
        for r in reqs:
            f = r["fields"]
            lines.append(f"- {r['number']} [{f.get('kind')}] {f.get('statement')} (state {r['state']}; acceptance: {f.get('acceptance_criteria') or 'n/a'}; "
                         f"verification: {f.get('verification_method') or 'n/a'}; verified: {bool(f.get('verified'))})")
    if links:
        lines += ["", "## Links", ""] + [f"- {l['label']}: {l['target']}" for l in links]
    if rec["transitions"]:
        lines += ["", "## Transitions", ""] + [f"- {when_utc(t['at'])}: {t['from']} -> {t['to']} by {t['by']} (run {t['run_id']})" for t in rec["transitions"]]
    if rec["decisions"] and body is None:  # a change order's record prints its own signatures
        lines += [""] + signature_lines(rec["decisions"], members)
    lines += ["", f"Rendered {utcnow()}. Printed copies are uncontrolled.", ""]
    return "\n".join(lines)


def doc_summary(rec: dict) -> str:
    f = rec["fields"]
    return f"{f.get('title', '')} [{f.get('doc_class', '')}/{f.get('doc_type', '')} rev {f.get('revision', '')}.{f.get('minor', 0)}]"


def today() -> str:
    return date.today().isoformat()
