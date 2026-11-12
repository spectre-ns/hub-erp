"""Command line for the configuration as JSON (issue #33); ``hubproto.cli`` calls ``register(sub)``.

  hubproto config export [--out FILE]              everything in force as one JSON document (hubproto.configjson)
  hubproto config propose FILE.json --as PERSON [--co CO] [--reason TEXT]
                                                   put what the document changes on a change order (the person's settings
                                                   change order unless --co names one); refused, with nothing carried,
                                                   when any part cannot be carried
  hubproto config propose SCRIPT.py --as PERSON [--co CO] [--reason TEXT]   (or --script FILE)
                                                   a configuration script (issue #34): checked, run in the script worker
                                                   (or, on a hub not deployed, a local subprocess labelled weaker
                                                   isolation), and its output carried as a document is; the run is
                                                   recorded on the change order (hubproto.scripts)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .cli import _hub, clean, fail, require_member


def cmd_export(args) -> int:
    from . import configjson

    hub = _hub(args)
    try:
        text = configjson.dumps(configjson.export(hub))
    except ValueError as e:
        fail(str(e), 1)
    if not args.out or args.out == "-":
        print(text, end="")
        return 0
    out = Path(args.out)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, out)
    print(f"wrote the configuration in force to {out}")
    return 0


def cmd_propose(args) -> int:
    from . import configjson, eco

    hub = _hub(args)
    require_member(hub, args.as_user)  # a person: a cog is refused here (exit 2), and by scripts.propose
    path = Path(args.file)
    try:
        raw = path.read_bytes()  # a missing file is a mistake in the command (main says so, exit 2)
    except OSError as e:
        fail(f"{path} cannot be read: {e.strerror or e}", 2)
    co = None
    if args.co:
        try:
            co = eco._co(hub, args.co if args.co.startswith("rec:") else args.co.upper())
        except ValueError as e:
            fail(f"{e}; `hubproto records --type change-order` lists them", 2)
    if args.script or path.suffix == ".py":
        return _propose_script(hub, args, path, raw, co)
    try:
        doc = configjson.loads(raw)
    except configjson.Refused as e:  # not a document the hub reads: refused, with the sentence
        print(f"error: {path.name} was not proposed; nothing was carried:", file=sys.stderr)
        for r in e.reasons:
            print(f"  - {clean(r)}", file=sys.stderr)
        return 1
    try:
        out = configjson.propose(hub, doc, by=args.as_user, co_ref=co["locator"] if co else None, reason=args.reason or "", name=path.name)
    except configjson.Refused as e:
        print(f"error: {path.name} was not proposed; nothing was carried:", file=sys.stderr)
        for r in e.reasons:
            print(f"  - {clean(r)}", file=sys.stderr)
        return 1
    if not out["changed"]:
        print(f"{path.name} says what is in force: nothing to propose")
        return 0
    for line in out["summary"]:
        print(f"  {clean(line)}")
    print(f"{out['change_order']} carries what {path.name} changes"
          + (" (started for it)" if out.get("created") else "")
          + "; nothing changes until it is reviewed, approved and released (request review next)")
    return 0


def _propose_script(hub, args, path: Path, raw: bytes, co: dict | None) -> int:
    from . import configjson, scripts

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        fail(f"{path.name} is not UTF-8 text: a configuration script is Python source", 2)
    try:
        out = scripts.propose(hub, text, by=args.as_user, co_ref=co["locator"] if co else None, reason=args.reason or "", name=path.name)
    except configjson.Refused as e:
        print(f"error: {path.name} was not proposed; nothing was carried:", file=sys.stderr)
        for r in e.reasons:
            print(f"  - {clean(r)}", file=sys.stderr)
        return 1
    run = out["script"]
    print(f"{path.name} ran in {'the script worker' if run['isolation'] == 'container' else 'a local subprocess (weaker isolation: no script worker)'}"
          f" in {run['seconds']:g} s: {run['detail']}")
    if not out["changed"]:
        print("its output says what is in force: nothing to propose")
        return 0
    for line in out["summary"]:
        print(f"  {clean(line)}")
    print(f"{out['change_order']} carries what {path.name}'s output changes, with the script and its output ({run['id']})"
          + (" (started for it)" if out.get("created") else "")
          + "; nothing changes until it is reviewed, approved and released (request review next)")
    return 0


def register(sub) -> None:
    s = sub.add_parser("config", help="the configuration in force as JSON; a JSON document proposed on a change order")
    t = s.add_subparsers(dest="config_cmd", required=True, metavar="ACTION", help="export or propose")
    c = t.add_parser("export", help="write everything in force as one JSON document (deterministic: sorted keys, stable order)")
    c.add_argument("--out", metavar="FILE", help="write it to FILE (default: standard output)")
    c.set_defaults(fn=cmd_export)
    c = t.add_parser("propose", help="put what a JSON document changes on a change order; nothing changes until it is released")
    c.add_argument("file", metavar="FILE", help="a document as `config export` writes it, edited (FILE.json); or a configuration script (FILE.py)")
    c.add_argument("--script", action="store_true", help="FILE is a configuration script, whatever its name (a .py file always is)")
    c.add_argument("--as", dest="as_user", required=True, metavar="PERSON", help="the person proposing it")
    c.add_argument("--co", metavar="CO", help="the change order to put it on (default: your open settings change order, started when you have none)")
    c.add_argument("--reason", default="", metavar="TEXT", help="why (kept on the changes)")
    c.set_defaults(fn=cmd_propose)
