"""Command line for the prototype hub."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .app import Hub
from .compile import CompileError, change_state
from .inbox import parse_eml
from .runner import RunError


def _hub(args) -> Hub:
    return Hub(Path(args.root))


def _print(obj) -> None:
    print(json.dumps(obj, indent=1, sort_keys=True, default=str))


def cmd_init(args):
    hub = _hub(args)
    hub.kernel.seed()
    print(f"kernel seeded at {hub.kernel.path}")


def cmd_frames(args):
    hub = _hub(args)
    for f in hub.frames.frames.values():
        print(f"{f.name:28s} v{f.version:8s} scope={f.scope:12s} inherits={','.join(f.inherits) or '-'}  {f.path.relative_to(hub.frames.root)}")


def cmd_compile(args):
    hub = _hub(args)
    proposal = hub.compile(args.scope or None)
    print(f"proposal {proposal['id']}  base={proposal['base'] or 'none'}  extractor={proposal['extractor']}")
    print(f"frames: " + ", ".join(f"{f['name']}@{f['version']}" for f in proposal["frames"]))
    print(f"rules: {len(proposal['rules'])}  changes: {len(proposal['changes'])}  "
          f"findings: {len(proposal['findings'])}  notes: {len(proposal['notes'])}")
    for f in proposal["findings"]:
        print(f"  [{f['severity']}] {f['guard']}: {f['detail']}  ({f['frame']}:{f['line']})")
    for n in proposal["notes"]:
        print(f"  [note] {n['frame']}:{n['line']}: {n['note']}: {n['text']}")
    for c in proposal["changes"]:
        flag = "WIDENING " if c["widening"] else ""
        print(f"  {c['id']:24s} {flag}{c['reason']}")
    print(f"reviewers: {', '.join(proposal['reviewers'])}")


def cmd_proposal(args):
    hub = _hub(args)
    p = hub.proposals.load(args.id)
    print(f"proposal {p['id']} status={p['status']} release={p['release']}")
    for c in p["changes"]:
        state = change_state(c)
        decided = ", ".join(f"{d['by']}:{d['verdict']}" for d in c["decisions"]) or "-"
        print(f"  {c['id']:24s} {state:9s} {'W ' if c['widening'] else '  '}{c['reason']}  [{decided}]")
    for line in hub.readiness(p["id"]):
        print("  needs: " + line)


def cmd_review(args):
    hub = _hub(args)
    decisions = {}
    for item in args.decide or []:
        cid, _, verdict = item.rpartition("=")
        decisions[cid] = verdict
    try:
        p = hub.review(args.id, args.as_user, decisions=decisions, approve_all=args.approve_all, meaning=args.meaning)
    except CompileError as e:
        sys.exit(f"error: {e}")
    print(f"recorded decisions by {args.as_user}; status={p['status']}")
    for line in hub.readiness(p["id"]):
        print("  needs: " + line)


def cmd_release(args):
    hub = _hub(args)
    try:
        meta = hub.release(args.id, args.as_user, effective_from=args.effective_from)
    except CompileError as e:
        sys.exit(f"error: {e}")
    print(f"release {meta['digest']}  effective_from={meta['effective_from']}  parent={meta['parent']}")


def cmd_releases(args):
    hub = _hub(args)
    cur = hub.releases.current()
    for m in hub.releases.list():
        mark = "*" if cur and cur["digest"] == m["digest"] else " "
        print(f"{mark} {m['digest'][:16]}  effective={m['effective_from']}  by={m['released_by']}  proposal={m['proposal_id']}  parent={(m['parent'] or '-')[:16]}")


def cmd_why(args):
    hub = _hub(args)
    d = hub.why(args.as_user, args.action, args.object, args.folder)
    print(("ALLOWED  " if d["allowed"] else "DENIED   ") + d["why"])


def cmd_run(args):
    hub = _hub(args)
    if args.record_file:
        record = json.loads(Path(args.record_file).read_text())
    else:
        record = json.loads(args.record_json or "{}")
    try:
        run_id = hub.runner.submit(args.process, {"record": record}, owner=args.as_user)
    except RunError as e:
        sys.exit(f"error: {e}")
    state = hub.runner.load(run_id)
    print(f"run {run_id} status={state['status']} release={state['release'][:16]}")
    if state["pending"]:
        print(f"  waiting for: {', '.join(state['pending']['assignees'])}  ({state['pending']['route_rule']['text']})")


def cmd_runs(args):
    hub = _hub(args)
    for s in hub.runner.list():
        pend = f" waiting:{','.join(s['pending']['assignees'])}" if s.get("pending") else ""
        print(f"{s['id']}  {s['process']:18s} {s['status']:10s} owner={s['owner']:12s} release={s['release'][:12]}{pend}")


def cmd_run_show(args):
    hub = _hub(args)
    s = hub.runner.load(args.id)
    print(f"run {s['id']} process={s['process']} owner={s['owner']} status={s['status']} release={s['release']}")
    for step in s["manifest"]["steps"]:
        rec = s["steps"].get(step["id"])
        if not rec:
            print(f"  {step['id']:36s} not-reached")
            continue
        env = rec["envelope"]
        line = f"  {step['id']:36s} {rec['status']:18s} cog={env['cog']['id']} ok={env['ok']} problems={len(env['problems'])}"
        if rec.get("decision"):
            d = rec["decision"]
            line += f"  decided by {d['by']} ({d['by_type']}): {d['verdict']}"
        print(line)
    if s.get("pending"):
        print(f"  pending: {s['pending']['title']} -> {', '.join(s['pending']['assignees'])}")
    if s.get("grant"):
        print(f"  grant: {s['grant']['id']} decided_by={s['grant']['decided_by']}")


def cmd_track(args):
    hub = _hub(args)
    for ev in hub.runner.track(args.id):
        extra = {k: v for k, v in ev.items() if k not in ("seq", "at", "type", "run_id", "record_digest")}
        print(f"{ev['seq']:3d} {ev['at']} {ev['type']:18s} {json.dumps(extra, sort_keys=True, default=str)[:220]}")


def cmd_decide(args):
    hub = _hub(args)
    try:
        s = hub.runner.decide(args.id, args.as_user, args.verdict, meaning=args.meaning)
    except RunError as e:
        sys.exit(f"error: {e}")
    print(f"run {s['id']} status={s['status']}")


def cmd_inbox(args):
    hub = _hub(args)
    for m in hub.inbox.for_principal(args.as_user):
        run = f" run={m['run_id']}" if m.get("run_id") else ""
        print(f"{m['at']} [{m['channel']}/{m['kind']}] from {m['from']}: {m['subject']}{run}\n    {m['body']}")


def cmd_msg(args):
    hub = _hub(args)
    attachments = []
    for path in args.attach or []:
        p = Path(path)
        try:
            content = json.loads(p.read_text())
        except json.JSONDecodeError:
            content = {"text": p.read_text()}
        attachments.append({"name": p.name, "content": content})
    reply = hub.send(args.from_user, args.to, args.subject, args.body, channel=args.channel, attachments=attachments)
    if reply.get("kind") == "reply":
        print(f"{reply['from']} replied: {reply['body']}")
    else:
        print(f"delivered to {reply['to']} via {reply['channel']}")


def cmd_mail_in(args):
    hub = _hub(args)
    msg = parse_eml(Path(args.file).read_text())
    reply = hub.send(msg["from"], msg["to"], msg["subject"], msg["body"], channel="email", attachments=msg["attachments"])
    if reply.get("kind") == "reply":
        print(f"{reply['from']} replied by email: {reply['body']}")
    else:
        print(f"delivered to {reply['to']}")


def cmd_ledger(args):
    hub = _hub(args)
    for j in hub.kernel.journals().values():
        print(f"{j['id']} {j['date']} {j['memo']}  prepared={j['prepared_by']} approved={j['approved_by']} run={j['run_id']} release={j['release'][:12]}")
        for line in j["lines"]:
            print(f"    {line['account']:28s} {line['debit']:10.2f} {line['credit']:10.2f}")


def cmd_bills(args):
    hub = _hub(args)
    for b in hub.kernel.bills().values():
        print(f"{b['id']} {b.get('supplier')} {b.get('amount')} {b.get('currency')} state={b['state']} folder={b['folder']} run={b['run_id']}")


def cmd_shell(args):
    from .shell import serve

    serve(_hub(args), host=args.host, port=args.port)


def cmd_tick(args):
    hub = _hub(args)
    for e in hub.runner.tick(args.today):
        print(f"escalated run {e['run']} ({e['record']}) to {', '.join(e['to'])}")
    print("tick done")


def cmd_bootstrap_release(args):
    """First release on a fresh hub: compile, approve with two policy owners, release."""
    hub = _hub(args)
    if hub.releases.current() and not args.force:
        print("a release is already in effect; use --force to add another")
        return
    proposal = hub.compile()
    errors = [f for f in proposal["findings"] if f["severity"] == "error"]
    for f in errors:
        print(f"  [error] {f['guard']}: {f['detail']}  ({f['frame']}:{f['line']})")
    if errors:
        sys.exit("bootstrap stopped: fix the findings above")
    for who in (args.as_user, args.second):
        if who:
            hub.review(proposal["id"], who, approve_all=True, meaning="bootstrap release")
    try:
        meta = hub.release(proposal["id"], args.as_user)
    except CompileError as e:
        sys.exit(f"error: {e}")
    print(f"release {meta['digest']} in effect (rules: {len(proposal['rules'])})")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hubproto", description="Frames -> policy release -> governed Ops")
    p.add_argument("--root", default=os.environ.get("HUBPROTO_ROOT", "."), help="hub directory (frames/, state/)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="seed the kernel").set_defaults(fn=cmd_init)
    sub.add_parser("frames", help="list Frames").set_defaults(fn=cmd_frames)
    s = sub.add_parser("compile", help="compile Frames into a proposal")
    s.add_argument("--scope", action="append")
    s.set_defaults(fn=cmd_compile)
    s = sub.add_parser("proposal", help="show a proposal")
    s.add_argument("id")
    s.set_defaults(fn=cmd_proposal)
    s = sub.add_parser("review", help="decide proposal changes at the Gate")
    s.add_argument("id")
    s.add_argument("--as", dest="as_user", required=True)
    s.add_argument("--approve-all", action="store_true")
    s.add_argument("--decide", action="append", metavar="CHANGE=verdict")
    s.add_argument("--meaning", default="")
    s.set_defaults(fn=cmd_review)
    s = sub.add_parser("release", help="render and store the release")
    s.add_argument("id")
    s.add_argument("--as", dest="as_user", required=True)
    s.add_argument("--effective-from")
    s.set_defaults(fn=cmd_release)
    sub.add_parser("releases").set_defaults(fn=cmd_releases)
    s = sub.add_parser("why", help="explain a permission")
    s.add_argument("--as", dest="as_user", required=True)
    s.add_argument("--action", required=True)
    s.add_argument("--object", required=True)
    s.add_argument("--folder", required=True)
    s.set_defaults(fn=cmd_why)
    s = sub.add_parser("run", help="start a process")
    s.add_argument("process")
    s.add_argument("--as", dest="as_user", required=True)
    s.add_argument("--record-json")
    s.add_argument("--record-file")
    s.set_defaults(fn=cmd_run)
    sub.add_parser("runs").set_defaults(fn=cmd_runs)
    s = sub.add_parser("run-show")
    s.add_argument("id")
    s.set_defaults(fn=cmd_run_show)
    s = sub.add_parser("track")
    s.add_argument("id")
    s.set_defaults(fn=cmd_track)
    s = sub.add_parser("decide", help="decide a pending Gate as a person")
    s.add_argument("id")
    s.add_argument("--as", dest="as_user", required=True)
    s.add_argument("--verdict", choices=["approve", "reject"], required=True)
    s.add_argument("--meaning", default="")
    s.set_defaults(fn=cmd_decide)
    s = sub.add_parser("inbox")
    s.add_argument("--as", dest="as_user", required=True)
    s.set_defaults(fn=cmd_inbox)
    s = sub.add_parser("msg", help="send a DM or email to a person or a cog")
    s.add_argument("--from", dest="from_user", required=True)
    s.add_argument("--to", required=True)
    s.add_argument("--subject", required=True)
    s.add_argument("--body", default="")
    s.add_argument("--channel", choices=["dm", "email"], default="dm")
    s.add_argument("--attach", action="append")
    s.set_defaults(fn=cmd_msg)
    s = sub.add_parser("mail-in", help="deliver an .eml file")
    s.add_argument("file")
    s.set_defaults(fn=cmd_mail_in)
    sub.add_parser("ledger").set_defaults(fn=cmd_ledger)
    sub.add_parser("bills").set_defaults(fn=cmd_bills)
    s = sub.add_parser("shell", help="serve the workspace shell")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    s.set_defaults(fn=cmd_shell)
    s = sub.add_parser("tick", help="escalate overdue items")
    s.add_argument("--today")
    s.set_defaults(fn=cmd_tick)
    s = sub.add_parser("bootstrap-release", help="compile, approve with two policy owners and release")
    s.add_argument("--as", dest="as_user", required=True)
    s.add_argument("--second")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_bootstrap_release)
    for modname in ("hubproto.profile", "hubproto.cli_records"):
        try:
            import importlib

            mod = importlib.import_module(modname)
        except ImportError:
            continue
        if hasattr(mod, "register"):
            mod.register(sub)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
