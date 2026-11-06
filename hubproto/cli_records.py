"""Command line for records and documents; ``hubproto.cli`` calls ``register(sub)``.

Commands: records, record, docs, doc-new, doc-versions, doc-get, doc-checkout,
doc-checkin, attach, link, snapshot, upload, inbox-pending, resubmit, retry, cancel.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

from .app import Hub
from .kernel import KernelGuardError
from .runner import RunError


def _hub(args) -> Hub:
    return Hub(Path(args.root))


def _summary(rec: dict) -> str:
    f = rec["fields"]
    for key in ("title", "statement", "memo", "description", "name"):
        if f.get(key):
            return str(f[key])[:120]
    if f.get("supplier"):
        return f"{f.get('supplier')} {f.get('amount', '')} {f.get('currency', '')}".strip()
    return ""


def _fmt(v) -> str:
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, default=str)
    return "" if v is None else str(v)


def _guarded(fn):
    def wrapper(args):
        try:
            return fn(args)
        except (KernelGuardError, RunError) as e:
            sys.exit(f"error: {e}")

    wrapper.__name__ = fn.__name__
    return wrapper


def cmd_records(args):
    hub = _hub(args)
    recs = hub.kernel.query(type_=args.type, state=args.state, folder=args.folder, assignee=args.assignee, text=args.q)
    for r in recs:
        if r["type"] == "document" and not args.type:
            continue
        due = f" due={r['due']}" if r["due"] else ""
        who = f" assignees={','.join(r['assignees'])}" if r["assignees"] else ""
        print(f"{r['number']:14s} {r['type']:12s} {r['state']:28s} {r['folder']:30s} {_summary(r)}{who}{due}")


@_guarded
def cmd_record(args):
    hub = _hub(args)
    rec = hub.kernel.require(args.ref)
    print(f"{rec['number']} ({rec['type']}) state={rec['state']} folder={rec['folder']} locator={rec['locator']}")
    print(f"  created {rec['created_at']} by {rec['created_by']}; run={rec['run_id'] or '-'} release={(rec['release'] or '')[:12] or '-'}")
    print(f"  labels={_fmt(rec['labels'])} hold={rec['hold']} assignees={','.join(rec['assignees']) or '-'} due={rec['due'] or '-'}")
    if rec["type"] == "requirement":
        print(f"statement: {rec['fields'].get('statement') or '(none)'}")
    print("fields:")
    for k, v in sorted(rec["fields"].items()):
        if k in ("text", "body") or v in (None, "", [], {}):
            continue
        print(f"  {k:24s} {_fmt(v)[:200]}")
    links = hub.kernel.links_of(rec["locator"])
    print("links:" if links else "links: none")
    for l in links:
        print(f"  {l['label']:20s} {l['target']} ({l['direction']})")
    docs = hub.dms.documents_of(rec["locator"])
    print("documents:" if docs else "documents: none")
    for d in docs:
        f = d["fields"]
        print(f"  {d['number']} {f.get('title')!r} {f.get('doc_class')} rev {f.get('revision')}.{f.get('minor', 0)} state={d['state']}")
    decisions = rec["decisions"]  # the projection is the single source of signatures
    print("decisions:" if decisions else "decisions: none")
    for d in decisions:
        print(f"  {d.get('at')} {d.get('by')} ({d.get('by_type')}) {d.get('verdict')}: {d.get('meaning')} sig={d.get('signature')}")
    print("transitions:" if rec["transitions"] else "transitions: none")
    for t in rec["transitions"]:
        print(f"  {t['at']} {t['from']} -> {t['to']} by {t['by']} run={t['run_id'] or '-'} {t.get('reason', '')}")


def cmd_docs(args):
    hub = _hub(args)
    for d in hub.kernel.query(type_="document", folder=args.folder):
        f = d["fields"]
        lock = f" checked-out-by={f['checked_out_by']}" if f.get("checked_out_by") else ""
        print(f"{d['number']:10s} {f.get('doc_class', ''):10s} rev {f.get('revision', '')}.{f.get('minor', 0):<3} {d['state']:10s} {d['folder']:30s} {f.get('title', '')!r}{lock}")


@_guarded
def cmd_doc_new(args):
    hub = _hub(args)
    path = Path(args.file)
    doc = hub.dms.create(title=args.title, doc_class=args.doc_class, folder=args.folder, by=args.as_user,
                         content=path.read_bytes(), filename=path.name, doc_type=args.type or "", attach_to=args.attach_to)
    print(f"{doc['number']} {doc['locator']} state={doc['state']} rev {doc['fields'].get('revision')}.{doc['fields'].get('minor', 0)}")


@_guarded
def cmd_doc_versions(args):
    hub = _hub(args)
    rec = hub.kernel.require(args.ref)
    for v in hub.dms.versions(rec["locator"]):
        print(f"{v['version']:3d} {v['at']} by {v['by']:12s} {v.get('filename', ''):30s} {v.get('size', 0):8d} {v['sha256'][:16]} {v.get('note', '')}")


@_guarded
def cmd_doc_get(args):
    hub = _hub(args)
    rec = hub.kernel.require(args.ref)
    content, version = hub.dms.content(rec["locator"], args.version)
    if args.out:
        Path(args.out).write_bytes(content)
        print(f"wrote version {version['version']} ({len(content)} bytes) to {args.out}")
    else:
        sys.stdout.buffer.write(content)
        sys.stdout.buffer.flush()


@_guarded
def cmd_doc_checkout(args):
    hub = _hub(args)
    rec = hub.dms.checkout(args.ref, by=args.as_user)
    print(f"{rec['number']} checked out by {args.as_user}")


@_guarded
def cmd_doc_checkin(args):
    hub = _hub(args)
    path = Path(args.file)
    rec = hub.dms.checkin(args.ref, path.read_bytes(), by=args.as_user, filename=path.name, note=args.note or "check-in")
    print(f"{rec['number']} version {len(hub.dms.versions(rec['locator']))} rev {rec['fields'].get('revision')}.{rec['fields'].get('minor', 0)}")


@_guarded
def cmd_attach(args):
    hub = _hub(args)
    rec = hub.kernel.require(args.record)
    doc = hub.kernel.require(args.doc)
    hub.dms.attach(rec["locator"], doc["locator"], by=args.as_user)
    print(f"{doc['number']} attached to {rec['number']}")


@_guarded
def cmd_link(args):
    hub = _hub(args)
    rec = hub.kernel.link(args.record, args.rel, args.target, by=args.as_user)
    print(f"{rec['number']} {args.rel} {args.target}")


@_guarded
def cmd_snapshot(args):
    hub = _hub(args)
    doc = hub.dms.render_snapshot(args.record, by=args.as_user)
    print(f"{doc['number']} {doc['fields'].get('title')!r} attached to {args.record}")


@_guarded
def cmd_upload(args):
    hub = _hub(args)
    path = Path(args.file)
    doc = hub.dms.intake(folder=args.folder, by=args.as_user, content=path.read_bytes(), filename=path.name, channel="cli",
                         sender=args.as_user, attach_to=args.attach_to)
    print(f"{doc['number']} {doc['locator']} filed in {args.folder}" + (f" attached to {args.attach_to}" if args.attach_to else ""))


def _parse_fields(pairs, specs: dict) -> dict:
    """``k=v`` pairs coerced by the record type's field specs (lists take JSON or a comma list, objects JSON,
    numbers are cast, enums checked); an unknown field is kept as text."""
    from .shell import ShellError, coerce

    out = {}
    for pair in pairs or []:
        k, sep, v = pair.partition("=")
        k = k.strip()
        if not sep or not k:
            sys.exit(f"error: --field takes k=v, not {pair!r}")
        try:
            value = coerce(k, specs.get(k), v)
        except ShellError as e:
            sys.exit(f"error: {e}")
        if value is not None:
            out[k] = value
    return out


def _doc_type(filename: str) -> str:
    return {".kicad_sch": "schematic", ".kicad_pcb": "board", ".fcstd": "model", ".pdf": "drawing"}.get(os.path.splitext(filename)[1].lower(), "document")


@_guarded
def cmd_inbox_pending(args):
    hub = _hub(args)
    runs = hub.runner.pending_for(args.as_user)
    if not runs:
        print(f"nothing is waiting for {args.as_user}")
    for s in runs:
        p = s["pending"]
        kind = p.get("kind", "decision")
        due = f" due={p['due']}" if p.get("due") else ""
        rec = hub.kernel.get(s["record"]) if s.get("record") else None
        label = f"{rec['number']} {_summary(rec)}".strip() if rec else (s.get("record") or "-")
        print(f"{s['id']} {kind:9s} {p['title']:40s} {s['process']:22s} {label}{due}")
        if kind == "decision":
            print(f"    digest={p['payload_digest']} fields={','.join(hub.runner.allowed_decision_fields(s)) or '-'}")
        else:
            print(f"    reason={p.get('reason', '')!r} rework from step {p.get('step')}")
    failed = [s for s in hub.runner.list() if s["status"] == "failed" and s["owner"] == args.as_user]
    for s in failed:
        ev = next((e for e in reversed(hub.runner.track(s["id"])) if e["type"] == "run.failed"), {})
        print(f"{s['id']} failed    {s['process']:22s} {s.get('record') or '-'} at {ev.get('step')}: {ev.get('reason', '')}  (retry or cancel)")


@_guarded
def cmd_resubmit(args):
    hub = _hub(args)
    attachments = []
    for path in args.file or []:
        p = Path(path)
        attachments.append({"name": p.name, "content_b64": base64.b64encode(p.read_bytes()).decode("ascii"),
                            "doc_class": "controlled", "doc_type": _doc_type(p.name)})
    state = hub.runner.load(args.run)
    rec = hub.kernel.get(state["record"]) if state.get("record") else None
    specs = hub.kernel.types.get(rec["type"], {}).get("fields", {}) if rec else {}
    fields = _parse_fields(args.field, specs)
    s = hub.runner.resubmit(args.run, args.as_user, fields=fields or None, attachments=attachments or None, note=args.note or "")
    print(f"run {s['id']} resubmitted status={s['status']}" + (f" pending={s['pending']['title']} -> {','.join(s['pending']['assignees'])}" if s.get("pending") else ""))


@_guarded
def cmd_retry(args):
    hub = _hub(args)
    s = hub.runner.retry(args.run, args.as_user, note=args.note or "")
    print(f"run {s['id']} retried status={s['status']}" + (f" pending={s['pending']['title']} -> {','.join(s['pending']['assignees'])}" if s.get("pending") else ""))


@_guarded
def cmd_cancel(args):
    hub = _hub(args)
    s = hub.runner.cancel(args.run, args.as_user, reason=args.reason)
    print(f"run {s['id']} cancelled; record {s.get('record') or '-'} state={hub.kernel.get(s['record'])['state'] if s.get('record') and hub.kernel.get(s['record']) else '-'}")


def _replace(sub, name: str):
    """argparse refuses a duplicate subparser name; drop an earlier one so this module's wins."""
    if name in sub.choices:
        sub.choices.pop(name, None)
        sub._choices_actions = [a for a in sub._choices_actions if a.dest != name]


def register(sub) -> None:
    s = sub.add_parser("records", help="list records")
    s.add_argument("--type")
    s.add_argument("--state")
    s.add_argument("--folder")
    s.add_argument("--assignee")
    s.add_argument("--q", help="text search")
    s.set_defaults(fn=cmd_records)
    s = sub.add_parser("record", help="show a record: fields, links, documents, decisions, transitions")
    s.add_argument("ref")
    s.set_defaults(fn=cmd_record)
    _replace(sub, "docs")
    s = sub.add_parser("docs", help="list documents")
    s.add_argument("--folder")
    s.set_defaults(fn=cmd_docs)
    s = sub.add_parser("doc-new", help="create a document from a file")
    s.add_argument("--title", required=True)
    s.add_argument("--class", dest="doc_class", required=True, choices=["controlled", "evidence", "external", "working", "template"])
    s.add_argument("--folder", required=True)
    s.add_argument("--as", dest="as_user", required=True)
    s.add_argument("--file", required=True)
    s.add_argument("--attach-to", dest="attach_to")
    s.add_argument("--type")
    s.set_defaults(fn=cmd_doc_new)
    s = sub.add_parser("doc-versions", help="list a document's versions")
    s.add_argument("ref")
    s.set_defaults(fn=cmd_doc_versions)
    s = sub.add_parser("doc-get", help="write a document's content")
    s.add_argument("ref")
    s.add_argument("--out")
    s.add_argument("--version", type=int)
    s.set_defaults(fn=cmd_doc_get)
    s = sub.add_parser("doc-checkout", help="check a document out")
    s.add_argument("ref")
    s.add_argument("--as", dest="as_user", required=True)
    s.set_defaults(fn=cmd_doc_checkout)
    s = sub.add_parser("doc-checkin", help="check a new version in")
    s.add_argument("ref")
    s.add_argument("--as", dest="as_user", required=True)
    s.add_argument("--file", required=True)
    s.add_argument("--note")
    s.set_defaults(fn=cmd_doc_checkin)
    s = sub.add_parser("attach", help="attach a document to a record")
    s.add_argument("record")
    s.add_argument("doc")
    s.add_argument("--as", dest="as_user", required=True)
    s.set_defaults(fn=cmd_attach)
    s = sub.add_parser("link", help="link two records")
    s.add_argument("record")
    s.add_argument("rel")
    s.add_argument("target")
    s.add_argument("--as", dest="as_user", required=True)
    s.set_defaults(fn=cmd_link)
    s = sub.add_parser("snapshot", help="render a record to an evidence document")
    s.add_argument("record")
    s.add_argument("--as", dest="as_user", required=True)
    s.set_defaults(fn=cmd_snapshot)
    s = sub.add_parser("upload", help="file an external document")
    s.add_argument("--folder", required=True)
    s.add_argument("--as", dest="as_user", required=True)
    s.add_argument("--file", required=True)
    s.add_argument("--attach-to", dest="attach_to")
    s.set_defaults(fn=cmd_upload)
    s = sub.add_parser("inbox-pending", help="runs waiting for a decision or a resubmission by a person, and their failed runs")
    s.add_argument("--as", dest="as_user", required=True)
    s.set_defaults(fn=cmd_inbox_pending)
    s = sub.add_parser("resubmit", help="rework a returned item: set fields, attach files, restart the run")
    s.add_argument("run")
    s.add_argument("--as", dest="as_user", required=True)
    s.add_argument("--field", action="append", metavar="K=V", help="a field to set (JSON values accepted)")
    s.add_argument("--file", action="append", metavar="PATH", help="a file to attach as a controlled document")
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_resubmit)
    s = sub.add_parser("retry", help="retry a failed run from the failed step")
    s.add_argument("run")
    s.add_argument("--as", dest="as_user", required=True)
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_retry)
    s = sub.add_parser("cancel", help="cancel a run; its record closes as cancelled")
    s.add_argument("run")
    s.add_argument("--as", dest="as_user", required=True)
    s.add_argument("--reason", required=True)
    s.set_defaults(fn=cmd_cancel)
