"""The document management system on top of the record system.

A document is a record of type ``document``. Its content lives in a
content-addressed blob store with a version history; the record carries the
metadata, class, revision and lifecycle state. Documents attach to any record
by a ``has-document`` link, once stored, however many records refer to them.

Classes: controlled (SOPs, forms, drawings), evidence (signed forms,
certificates, snapshots), external (received files, immutable), working
(drafts), template.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
from datetime import date
from pathlib import Path

from .kernel import Kernel, KernelGuardError
from .release import utcnow

CLASSES = ("controlled", "evidence", "external", "working", "template")
IMMUTABLE_CLASSES = ("evidence", "external")
TEXT_MIMES = ("text/", "application/json", "application/x-yaml", "application/xml")


class BlobStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes) -> str:
        sha = hashlib.sha256(content).hexdigest()
        path = self.root / sha
        if not path.exists():
            path.write_bytes(content)
        return sha

    def get(self, sha: str) -> bytes:
        return (self.root / sha).read_bytes()

    def exists(self, sha: str) -> bool:
        return (self.root / sha).exists()


def extract_text(content: bytes, mime: str) -> str:
    """Plain-text extraction for search. Production uses Tika and OCR."""
    if any(mime.startswith(t) for t in TEXT_MIMES):
        try:
            return content.decode("utf-8", errors="replace")[:20000]
        except Exception:  # pragma: no cover
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


class DMS:
    def __init__(self, kernel: Kernel, blobs: BlobStore):
        self.kernel = kernel
        self.blobs = blobs

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
                    run_id: str = "", release: str = "") -> dict:
        rec = self.kernel.require(ref)
        if rec["type"] != "document":
            raise KernelGuardError("document", f"{rec['number']} is not a document")
        if rec["fields"].get("doc_class") in IMMUTABLE_CLASSES and self.versions(ref):
            raise KernelGuardError("immutable", f"{rec['fields']['doc_class']} documents cannot be changed; file a new one that supersedes it")
        if rec["fields"].get("doc_class") in ("controlled", "template") and rec["state"] not in ("open", "draft", "returned", "failed"):
            raise KernelGuardError("controlled", f"{rec['number']} is {rec['state']}; a controlled document changes only through a new revision (start document-control with the same title)")
        locked_by = rec["fields"].get("checked_out_by")
        if locked_by and locked_by != by:
            raise KernelGuardError("checked-out", f"{rec['number']} is checked out by {locked_by}")
        mime = mime or (mimetypes.guess_type(filename)[0] if filename else "") or (rec["fields"].get("mime") if self.versions(ref) else "") or "application/octet-stream"
        sha = self.blobs.put(content)
        n = len(self.versions(ref)) + 1
        text = extract_text(content, mime)
        self.kernel.append("document.version_added", rec["locator"],
                           {"version": n, "sha256": sha, "filename": filename, "mime": mime, "size": len(content),
                            "note": note, "text_excerpt": text[:2000]}, run_id=run_id, release=release, by=by)
        self.kernel.set_fields(rec["locator"], {"content_sha": sha, "filename": filename, "mime": mime, "size": len(content),
                                                "text": text, "checked_out_by": None, "minor": int(rec["fields"].get("minor") or 0) + (1 if n > 1 else 0)},
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

    def checkout(self, ref: str, *, by: str, run_id: str = "", release: str = "") -> dict:
        rec = self.kernel.require(ref)
        if rec["fields"].get("doc_class") in IMMUTABLE_CLASSES:
            raise KernelGuardError("immutable", "evidence and external documents are never edited")
        if rec["fields"].get("doc_class") in ("controlled", "template") and rec["state"] not in ("open", "draft", "returned", "failed"):
            raise KernelGuardError("controlled", f"{rec['number']} is {rec['state']} and cannot be checked out; revise it through document-control")
        locked_by = rec["fields"].get("checked_out_by")
        if locked_by and locked_by != by:
            raise KernelGuardError("checked-out", f"{rec['number']} is checked out by {locked_by}")
        self.kernel.append("document.checked_out", rec["locator"], {"by": by}, run_id=run_id, release=release, by=by)
        return self.kernel.set_fields(rec["locator"], {"checked_out_by": by}, by=by, run_id=run_id, release=release)

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
    def render_snapshot(self, record_ref: str, *, by: str, run_id: str = "", release: str = "", title: str = "") -> dict:
        """Render a record to Markdown and file it as an evidence document attached to the record."""
        rec = self.kernel.require(record_ref)
        md = render_markdown(self.kernel, rec)
        doc = self.create(title=title or f"{rec['number']} snapshot at {rec['state']}", doc_class="evidence",
                          folder=rec["folder"], by=by, content=md.encode("utf-8"), filename=f"{rec['number']}-{rec['state']}.md",
                          mime="text/markdown", doc_type="snapshot", run_id=run_id, release=release,
                          provenance={"record": rec["locator"], "state": rec["state"], "rendered_at": utcnow()},
                          attach_to=rec["locator"])
        return doc

    def intake(self, *, folder: str, by: str, content: bytes, filename: str, channel: str, sender: str,
               attach_to: str | None = None, run_id: str = "", release: str = "", title: str = "") -> dict:
        """File a received file as an external document (immutable, with provenance)."""
        return self.create(title=title or filename, doc_class="external", folder=folder, by=by, content=content,
                           filename=filename, doc_type="received", run_id=run_id, release=release,
                           provenance={"channel": channel, "sender": sender, "received_at": utcnow()},
                           attach_to=attach_to)


def render_markdown(kernel: Kernel, rec: dict) -> str:
    lines = [f"# {rec['number']} ({rec['type']})", "", f"State: **{rec['state']}**  ", f"Folder: {rec['folder']}  ",
             f"Created {rec['created_at']} by {rec['created_by']}  ",
             f"Policy release: `{(rec.get('release') or '')[:16]}`  Run: `{rec.get('run_id') or ''}`", "", "## Fields", ""]
    for k, v in sorted(rec["fields"].items()):
        if k in ("text",) or v in (None, "", [], {}):
            continue
        if isinstance(v, (dict, list)):
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
        lines += ["", "## Transitions", ""] + [f"- {t['at']}: {t['from']} -> {t['to']} by {t['by']} (run {t['run_id']})" for t in rec["transitions"]]
    if rec["decisions"]:
        lines += ["", "## Signatures", ""]
        for d in rec["decisions"]:
            lines.append(f"- {d.get('by')} ({d.get('by_type')}), {d.get('at')}: **{d.get('meaning')}**; "
                         f"decision {d.get('signature', '')}; payload {str(d.get('payload_digest', ''))[:16]}")
    lines += ["", f"Rendered {utcnow()}. Printed copies are uncontrolled.", ""]
    return "\n".join(lines)


def doc_summary(rec: dict) -> str:
    f = rec["fields"]
    return f"{f.get('title', '')} [{f.get('doc_class', '')}/{f.get('doc_type', '')} rev {f.get('revision', '')}.{f.get('minor', 0)}]"


def today() -> str:
    return date.today().isoformat()
