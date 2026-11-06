"""Command line for runs waiting on people, records and documents; ``hubproto.cli`` calls ``register(sub)``.

Commands: inbox-pending, retry, resubmit, cancel, records, record, link, snapshot, docs, doc-new, doc-versions,
doc-get, doc-checkout, doc-checkin, attach, upload. Errors follow ``hubproto.cli``: one sentence, exit 1 for a
refusal, 2 for a mistake; ``main`` there catches the core's exceptions.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

from .cli import _hub, clean, fail, require_member, resolve_record, resolve_run, report_run, short

SUMMARY_WIDTH = 80


def summary(rec: dict, width: int = SUMMARY_WIDTH) -> str:
    f = rec["fields"]
    for key in ("title", "statement", "memo", "description", "name"):
        if f.get(key):
            return clean(f[key], width)
    if f.get("supplier"):
        amount = f.get("amount")
        amount_s = f"{amount:,.2f}" if isinstance(amount, (int, float)) else clean(amount, 12)
        return clean(f"{f.get('supplier')} {amount_s} {f.get('currency', '')}".strip(), width)
    return ""


def parse_fields(pairs, specs: dict) -> dict:
    """``k=v`` pairs coerced by the record type's field specs (lists take JSON or a comma list, objects JSON,
    numbers are cast, enums checked); an unknown field is kept as text."""
    try:
        from .shell import ShellError, coerce
    except ImportError:  # the shell is being split into a package; its form helpers live in shell.forms
        from .shell.forms import ShellError, coerce

    out = {}
    for pair in pairs or []:
        k, sep, v = pair.partition("=")
        k = k.strip()
        if not sep or not k:
            fail(f"--field takes k=v, not {pair!r}", 2)
        try:
            value = coerce(k, specs.get(k), v)
        except ShellError as e:
            fail(str(e), 2)
        if value is not None:
            out[k] = value
    return out


def _doc_type(filename: str) -> str:
    return {".kicad_sch": "schematic", ".kicad_pcb": "board", ".fcstd": "model", ".pdf": "drawing"}.get(os.path.splitext(filename)[1].lower(), "document")


def _read(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as e:
        fail(f"cannot read {path}: {e.strerror or e}", 2)
    return b""


def _file_attachments(paths) -> list[dict]:
    return [{"name": Path(p).name, "content_b64": base64.b64encode(_read(p)).decode("ascii"), "doc_class": "controlled",
             "doc_type": _doc_type(Path(p).name)} for p in paths or []]


def _specs_for_run(hub, state: dict) -> dict:
    rec = hub.kernel.get(state["record"]) if state.get("record") else None
    return hub.kernel.types.get(rec["type"], {}).get("fields", {}) if rec else {}


def _known_states(hub) -> list[str]:
    states = {"open", "failed", "returned", "rejected", "cancelled", "closed"}
    bundle = hub.current_bundle()
    for wf in (bundle or {}).get("workflows", {}).values():
        states.update(wf.get("states") or [])
    states.update(r["state"] for r in hub.kernel.query(include_archived=True))
    return sorted(states)


# ------------------------------------------------------------------ runs waiting on people
def cmd_inbox_pending(args) -> int:
    hub = _hub(args)
    require_member(hub, args.as_user, people_only=False)
    runs = hub.runner.pending_for(args.as_user)
    failed = [s for s in hub.runner.list() if s["status"] == "failed" and s["owner"] == args.as_user]
    if not runs and not failed:
        print(f"nothing is waiting for {args.as_user}")
    for s in runs:
        p = s["pending"]
        kind = p.get("kind", "decision")
        due = f" due={p['due']}" if p.get("due") else ""
        rec = hub.kernel.get(s["record"]) if s.get("record") else None
        label = f"{rec['number']} {summary(rec)}".strip() if rec else (s.get("record") or "-")
        print(f"{s['id']} {kind:9s} {clean(p['title'], 40):40s} {s['process'][:22]:22s} {label}{due}")
        if kind == "decision":
            fields = hub.runner.allowed_decision_fields(s)
            print(f"    seen digest={s.get('seen_digest') or hub.runner.seen_digest(s)}  fields={', '.join(fields) or 'none'} (what this Gate accepts with a decision)")
            print(f"    decide with: hubproto decide {s['id']} --as {args.as_user} --verdict approve|reject --meaning '...'")
        else:
            print(f"    returned: {clean(p.get('reason', ''), 200)}  (rework from step {p.get('step')})")
            print(f"    resubmit with: hubproto resubmit {s['id']} --as {args.as_user} --field k=v --file PATH")
    for s in failed:
        ev = next((e for e in reversed(hub.runner.track(s["id"])) if e["type"] == "run.failed"), {})
        rec = hub.kernel.get(s["record"]) if s.get("record") else None
        label = rec["number"] if rec else (s.get("record") or "-")
        print(f"{s['id']} failed    {s['process'][:22]:22s} {label} at {ev.get('step')}: {clean(ev.get('reason', ''), 200)}  (retry or cancel)")
        print(f"    retry with: hubproto retry {s['id']} --as {args.as_user} --field k=v   or   hubproto cancel {s['id']} --as {args.as_user} --reason '...'")
    return 0


def cmd_resubmit(args) -> int:
    hub = _hub(args)
    require_member(hub, args.as_user)
    run_id = resolve_run(hub, args.run)
    state = hub.runner.load(run_id)
    fields = parse_fields(args.field, _specs_for_run(hub, state))
    attachments = _file_attachments(args.file)
    s = hub.runner.resubmit(run_id, args.as_user, fields=fields or None, attachments=attachments or None, note=args.note or "")
    return report_run(hub, s, "resubmitted")


def cmd_retry(args) -> int:
    hub = _hub(args)
    require_member(hub, args.as_user)
    run_id = resolve_run(hub, args.run)
    state = hub.runner.load(run_id)
    fields = parse_fields(args.field, _specs_for_run(hub, state))
    attachments = _file_attachments(args.file)
    s = hub.runner.retry(run_id, args.as_user, note=args.note or "", fields=fields or None, attachments=attachments or None)
    return report_run(hub, s, "retried")


def cmd_cancel(args) -> int:
    hub = _hub(args)
    require_member(hub, args.as_user)
    run_id = resolve_run(hub, args.run)
    s = hub.runner.cancel(run_id, args.as_user, reason=args.reason)
    rec = hub.kernel.get(s["record"]) if s.get("record") else None
    print(f"run {s['id']} cancelled; record {rec['number'] if rec else '-'} state={rec['state'] if rec else '-'}")
    return 0


# ------------------------------------------------------------------ records
def cmd_records(args) -> int:
    hub = _hub(args)
    if args.type and args.type not in hub.kernel.types:
        fail(f"--type {args.type}: unknown record type; types are: {', '.join(sorted(hub.kernel.types))}", 2)
    if args.state and args.state not in _known_states(hub):
        fail(f"--state {args.state}: no record is in that state and no workflow has it; states are: {', '.join(_known_states(hub))}", 2)
    recs = hub.kernel.query(type_=args.type, state=args.state, folder=args.folder, assignee=args.assignee, text=args.q)
    print(f"{'number':14s} {'type':12s} {'state':28s} {'created':19s} {'folder':24s} summary")
    for r in recs:
        if r["type"] == "document" and not args.type:
            continue
        due = f" due={r['due']}" if r["due"] else ""
        who = f" assignees={','.join(r['assignees'])}" if r["assignees"] else ""
        print(f"{r['number']:14s} {r['type']:12s} {r['state'][:28]:28s} {r['created_at'][:19]:19s} {clean(r['folder'], 24):24s} {summary(r)}{who}{due}")
    return 0


def cmd_record(args) -> int:
    hub = _hub(args)
    rec = resolve_record(hub, args.ref)
    print(f"{rec['number']} ({rec['type']}) state={rec['state']} folder={clean(rec['folder'])} locator={rec['locator']}")
    print(f"  created {rec['created_at']} by {rec['created_by']}; run={rec['run_id'] or '-'} release={short(rec['release'])}")
    print(f"  labels={clean(rec['labels'])} hold={rec['hold']} assignees={','.join(rec['assignees']) or '-'} due={rec['due'] or '-'}")
    if rec["type"] == "requirement":
        print(f"statement: {clean(rec['fields'].get('statement')) or '(none)'}")
    print("fields:")
    for k, v in sorted(rec["fields"].items()):
        if k in ("text", "body") or v in (None, "", [], {}):
            continue
        print(f"  {k:24s} {clean(v, 200)}")
    links = hub.kernel.links_of(rec["locator"])
    print("links:" if links else "links: none")
    for l in links:
        print(f"  {l['label']:20s} {l['target']} ({l['direction']})")
    docs = hub.dms.documents_of(rec["locator"])
    print("documents:" if docs else "documents: none")
    for d in docs:
        f = d["fields"]
        print(f"  {d['number']} {clean(f.get('title'), 60)!r} {f.get('doc_class')} rev {f.get('revision')}.{f.get('minor', 0)} state={d['state']}")
    decisions = rec["decisions"]  # the projection is the single source of signatures
    print("decisions:" if decisions else "decisions: none")
    for d in decisions:
        print(f"  {d.get('at')} {d.get('by')} ({d.get('by_type')}) {d.get('verdict')}: {clean(d.get('meaning'), 160)} sig={d.get('signature')}")
    print("transitions:" if rec["transitions"] else "transitions: none")
    for t in rec["transitions"]:
        print(f"  {t['at']} {t['from']} -> {t['to']} by {t['by']} run={t['run_id'] or '-'} {clean(t.get('reason', ''), 120)}")
    return 0


def cmd_link(args) -> int:
    hub = _hub(args)
    require_member(hub, args.as_user)
    vocab = sorted(k for k in hub.kernel.link_vocab if not k.startswith("$"))
    if args.rel not in vocab:
        fail(f"unknown link relation {args.rel!r}; the link-vocabulary (config/links.json) has: {', '.join(vocab)}", 2)
    rec = resolve_record(hub, args.record)
    target = resolve_record(hub, args.target)
    rec = hub.kernel.link(rec["locator"], args.rel, target["locator"], by=args.as_user)
    print(f"{rec['number']} {args.rel} {target['number']}")
    return 0


def cmd_snapshot(args) -> int:
    hub = _hub(args)
    require_member(hub, args.as_user)
    rec = resolve_record(hub, args.record)
    doc = hub.dms.render_snapshot(rec["locator"], by=args.as_user)
    print(f"{doc['number']} {clean(doc['fields'].get('title'), 80)!r} snapshot attached to {rec['number']}")
    return 0


# ------------------------------------------------------------------ documents
def cmd_docs(args) -> int:
    hub = _hub(args)
    print(f"{'number':10s} {'class':10s} {'rev':8s} {'state':10s} {'folder':30s} title")
    for d in hub.kernel.query(type_="document", folder=args.folder):
        f = d["fields"]
        lock = f" checked-out-by={f['checked_out_by']}" if f.get("checked_out_by") else ""
        rev = f"{f.get('revision', '')}.{f.get('minor', 0)}"
        print(f"{d['number']:10s} {f.get('doc_class', ''):10s} {rev:8s} {d['state']:10s} {clean(d['folder'], 30):30s} {clean(f.get('title', ''), 80)!r}{lock}")
    return 0


def cmd_doc_new(args) -> int:
    hub = _hub(args)
    require_member(hub, args.as_user)
    bundle = hub.current_bundle()
    if bundle and args.folder not in bundle["folders"]:
        fail(f"--folder {args.folder}: not a folder in the release in force; folders are: {', '.join(bundle['folders'])}", 2)
    content = _read(args.file)
    attach_to = resolve_record(hub, args.attach_to)["locator"] if args.attach_to else None
    doc = hub.dms.create(title=args.title, doc_class=args.doc_class, folder=args.folder, by=args.as_user,
                         content=content, filename=Path(args.file).name, doc_type=args.type or "", attach_to=attach_to)
    print(f"{doc['number']} {doc['locator']} state={doc['state']} rev {doc['fields'].get('revision')}.{doc['fields'].get('minor', 0)}")
    return 0


def cmd_doc_versions(args) -> int:
    hub = _hub(args)
    rec = resolve_record(hub, args.ref)
    for v in hub.dms.versions(rec["locator"]):
        print(f"{v['version']:3d} {v['at']} by {v['by']:12s} {clean(v.get('filename', ''), 30):30s} {v.get('size', 0):8d} {v['sha256'][:16]} {clean(v.get('note', ''), 80)}")
    return 0


def cmd_doc_get(args) -> int:
    hub = _hub(args)
    rec = resolve_record(hub, args.ref)
    versions = hub.dms.versions(rec["locator"])
    if args.version is not None and not any(v["version"] == args.version for v in versions):
        fail(f"{rec['number']} has no version {args.version}; versions are 1 to {len(versions)}", 2)
    content, version = hub.dms.content(rec["locator"], args.version)
    if args.out:
        Path(args.out).write_bytes(content)
        print(f"wrote version {version['version']} ({len(content)} bytes) to {args.out}")
    else:
        sys.stdout.buffer.write(content)
        sys.stdout.buffer.flush()
    return 0


def cmd_doc_checkout(args) -> int:
    hub = _hub(args)
    require_member(hub, args.as_user)
    rec = hub.dms.checkout(resolve_record(hub, args.ref)["locator"], by=args.as_user)
    print(f"{rec['number']} checked out by {args.as_user}")
    return 0


def cmd_doc_checkin(args) -> int:
    hub = _hub(args)
    require_member(hub, args.as_user)
    content = _read(args.file)
    rec = hub.dms.checkin(resolve_record(hub, args.ref)["locator"], content, by=args.as_user, filename=Path(args.file).name,
                          note=args.note or "check-in")
    print(f"{rec['number']} version {len(hub.dms.versions(rec['locator']))} rev {rec['fields'].get('revision')}.{rec['fields'].get('minor', 0)}")
    return 0


def cmd_attach(args) -> int:
    hub = _hub(args)
    require_member(hub, args.as_user)
    rec = resolve_record(hub, args.record)
    doc = resolve_record(hub, args.doc)
    hub.dms.attach(rec["locator"], doc["locator"], by=args.as_user)
    print(f"{doc['number']} attached to {rec['number']}")
    return 0


def cmd_upload(args) -> int:
    hub = _hub(args)
    require_member(hub, args.as_user)
    bundle = hub.current_bundle()
    if bundle and args.folder not in bundle["folders"]:
        fail(f"--folder {args.folder}: not a folder in the release in force; folders are: {', '.join(bundle['folders'])}", 2)
    content = _read(args.file)
    attach_to = resolve_record(hub, args.attach_to)["number"] if args.attach_to else None
    doc = hub.dms.intake(folder=args.folder, by=args.as_user, content=content, filename=Path(args.file).name, channel="cli",
                         sender=args.as_user, attach_to=attach_to)
    print(f"{doc['number']} {doc['locator']} filed in {args.folder}" + (f" attached to {attach_to}" if attach_to else ""))
    return 0


def register(sub) -> None:
    AS = dict(dest="as_user", required=True, metavar="PERSON")
    # --- runs waiting on people ---------------------------------------------
    s = sub.add_parser("inbox-pending", help="runs waiting for a person's decision or rework, with the seen digest and the fields the Gate accepts, and their failed runs")
    s.add_argument("--as", help="the person", **AS)
    s.set_defaults(fn=cmd_inbox_pending)
    s = sub.add_parser("retry", help="retry a failed run from the failed step, fixing fields and attaching files on the way")
    s.add_argument("run", help="run id prefix")
    s.add_argument("--as", help="the run's owner (or an approver of its object)", **AS)
    s.add_argument("--field", action="append", metavar="K=V", help="a record field to set first; repeatable (JSON for lists and objects)")
    s.add_argument("--file", action="append", metavar="PATH", help="a file to attach as a controlled document; repeatable")
    s.add_argument("--note", default="", metavar="TEXT", help="why, for the Track")
    s.set_defaults(fn=cmd_retry)
    s = sub.add_parser("resubmit", help="rework a returned item: set fields, attach files, restart the run from the return step")
    s.add_argument("run", help="run id prefix")
    s.add_argument("--as", help="the run's owner", **AS)
    s.add_argument("--field", action="append", metavar="K=V", help="a record field to set; repeatable (JSON for lists and objects)")
    s.add_argument("--file", action="append", metavar="PATH", help="a file to attach as a controlled document; repeatable")
    s.add_argument("--note", default="", metavar="TEXT", help="what changed, for the approver")
    s.set_defaults(fn=cmd_resubmit)
    s = sub.add_parser("cancel", help="cancel a run; its record closes as cancelled")
    s.add_argument("run", help="run id prefix")
    s.add_argument("--as", help="the run's owner (or an approver of its object)", **AS)
    s.add_argument("--reason", required=True, metavar="TEXT", help="why")
    s.set_defaults(fn=cmd_cancel)

    # --- records ---------------------------------------------------------
    s = sub.add_parser("records", help="list records (documents only with --type document); filters are checked against known values")
    s.add_argument("--type", metavar="TYPE", help="record type (bill, ncr, task, document, ...)")
    s.add_argument("--state", metavar="STATE", help="workflow state (open, awaiting-..., posted, closed, failed, ...)")
    s.add_argument("--folder", metavar="FOLDER", help="a folder and its subfolders")
    s.add_argument("--assignee", metavar="PERSON", help="records waiting on this person")
    s.add_argument("--q", metavar="TEXT", help="text search in number and fields")
    s.set_defaults(fn=cmd_records)
    s = sub.add_parser("record", help="show a record: fields, links, documents, decisions, transitions")
    s.add_argument("ref", help="record number (any case) or locator")
    s.set_defaults(fn=cmd_record)
    s = sub.add_parser("link", help="link two records with a relation from config/links.json")
    s.add_argument("record", help="record number or locator")
    s.add_argument("rel", metavar="RELATION", help="concerns, caused, fulfils, settles, supersedes, ... (listed on a mistake)")
    s.add_argument("target", help="record number or locator")
    s.add_argument("--as", help="the person linking", **AS)
    s.set_defaults(fn=cmd_link)
    s = sub.add_parser("snapshot", help="render a record to an evidence document attached to it")
    s.add_argument("record", help="record number or locator")
    s.add_argument("--as", help="the person asking", **AS)
    s.set_defaults(fn=cmd_snapshot)

    # --- documents --------------------------------------------------------
    s = sub.add_parser("docs", help="list documents")
    s.add_argument("--folder", metavar="FOLDER", help="a folder and its subfolders")
    s.set_defaults(fn=cmd_docs)
    s = sub.add_parser("doc-new", help="create a document from a file")
    s.add_argument("--title", required=True, metavar="TEXT", help="document title")
    s.add_argument("--class", dest="doc_class", required=True, choices=["controlled", "evidence", "external", "working", "template"], help="document class")
    s.add_argument("--folder", required=True, metavar="FOLDER", help="a folder from the org model")
    s.add_argument("--as", help="the author", **AS)
    s.add_argument("--file", required=True, metavar="PATH", help="the file holding the first version")
    s.add_argument("--attach-to", dest="attach_to", metavar="RECORD", help="attach it to this record (number or locator)")
    s.add_argument("--type", metavar="TYPE", help="document type (sop, work-instruction, drawing, model, ...)")
    s.set_defaults(fn=cmd_doc_new)
    s = sub.add_parser("doc-versions", help="list a document's versions")
    s.add_argument("ref", help="document number or locator")
    s.set_defaults(fn=cmd_doc_versions)
    s = sub.add_parser("doc-get", help="write a document's content to a file or stdout")
    s.add_argument("ref", help="document number or locator")
    s.add_argument("--out", metavar="PATH", help="file to write (default: stdout)")
    s.add_argument("--version", type=int, metavar="N", help="version number (default: latest)")
    s.set_defaults(fn=cmd_doc_get)
    s = sub.add_parser("doc-checkout", help="check a document out for editing")
    s.add_argument("ref", help="document number or locator")
    s.add_argument("--as", help="the editor", **AS)
    s.set_defaults(fn=cmd_doc_checkout)
    s = sub.add_parser("doc-checkin", help="check a new version in")
    s.add_argument("ref", help="document number or locator")
    s.add_argument("--as", help="the editor", **AS)
    s.add_argument("--file", required=True, metavar="PATH", help="the file holding the new version")
    s.add_argument("--note", metavar="TEXT", help="what changed")
    s.set_defaults(fn=cmd_doc_checkin)
    s = sub.add_parser("attach", help="attach a document to a record")
    s.add_argument("record", help="record number or locator")
    s.add_argument("doc", help="document number or locator")
    s.add_argument("--as", help="the person attaching", **AS)
    s.set_defaults(fn=cmd_attach)
    s = sub.add_parser("upload", help="file an external document into a folder")
    s.add_argument("--folder", required=True, metavar="FOLDER", help="a folder from the org model")
    s.add_argument("--as", help="the person filing", **AS)
    s.add_argument("--file", required=True, metavar="PATH", help="the file")
    s.add_argument("--attach-to", dest="attach_to", metavar="RECORD", help="attach it to this record (number or locator)")
    s.set_defaults(fn=cmd_upload)
