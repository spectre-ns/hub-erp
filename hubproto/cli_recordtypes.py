"""Command line for the record types under change control; ``hubproto.cli`` calls ``register(sub)``.

  hubproto record-types status                     the record types in force (version, where from), and the file's drift
  hubproto record-types show TYPE                  one record type in force, as JSON
  hubproto record-types carry CO --as PERSON (--changes JSON | --changes-file PATH | --from-file) [--reason TEXT]
                                                   put record-type changes on a change order (see hubproto.recordtypes)
"""

from __future__ import annotations

import json
from pathlib import Path

from .cli import _hub, clean, fail, require_member


def cmd_status(args) -> int:
    from . import recordtypes

    hub = _hub(args)
    entry = recordtypes.in_force(hub)
    governed = recordtypes.governed(hub)
    print(f"record types in force: v{entry['version']}, {len(entry['types'])} type(s), from {clean(entry.get('source'))}"
          + ("" if governed else f" (config/{recordtypes.FILE} is the source until the SOPs are documents)"))
    for e in recordtypes.store(hub).history():
        print(f"  v{e['version']:<3d} {str(e.get('at') or '')[:19]:19s}  {clean(e.get('released_with') or e.get('source'), 40):40s}  "
              + clean("; ".join(e.get("changes") or []), 90))
    drift = recordtypes.drift(hub)
    if drift:
        print(f"config/{recordtypes.FILE} differs from the record types in force (v{drift['version']}); it is not used:")
        for line in drift["in_file"]:
            print(f"  in the file, not in force: {clean(line)}")
        for line in drift["in_force"]:
            print(f"  in force, not in the file: {clean(line)}")
        print("to bring what the file adds into force: hubproto record-types carry <change order> --from-file --as <person>")
    elif governed:
        print(f"config/{recordtypes.FILE} says what the record types in force were made from")
    return 0


def cmd_show(args) -> int:
    from . import recordtypes

    hub = _hub(args)
    types = recordtypes.in_force(hub)["types"]
    if args.type not in types:
        fail(f"no record type {args.type!r}; record types are: {', '.join(sorted(types))}", 2)
    print(json.dumps(types[args.type], indent=1, sort_keys=True))
    return 0


def cmd_carry(args) -> int:
    from . import eco, recordtypes

    hub = _hub(args)
    require_member(hub, args.as_user)
    try:
        co = eco._co(hub, args.co if args.co.startswith("rec:") else args.co.upper())
    except ValueError as e:
        fail(f"{e}; `hubproto records --type change-order` lists them", 2)
    given = [x for x in (args.changes, args.changes_file, args.from_file or None) if x]
    if len(given) != 1:
        fail("give the changes one way: --changes JSON, --changes-file PATH or --from-file", 2)
    skipped: list[str] = []
    changes = None
    if not args.from_file:  # bad JSON or a missing file is a mistake in the command (main says so, exit 2)
        changes = json.loads(args.changes if args.changes else Path(args.changes_file).read_text(encoding="utf-8"))
        if isinstance(changes, dict):
            changes = [changes]
        if not isinstance(changes, list):
            fail("the changes are a JSON list of objects, each with an op (add_field, add_type, ...)", 2)
    try:
        if args.from_file:
            items, skipped = recordtypes.carry_file_changes(hub, co["number"], by=args.as_user, reason=args.reason or "")
        else:
            items = recordtypes.carry(hub, co["number"], changes, by=args.as_user, reason=args.reason or "")
    except ValueError as e:  # the hub refused: destructive, invalid, or not theirs to change
        fail(str(e), 1)
    for item in items:
        print(f"{item['id']:6s} {clean(item['summary'])}")
    for line in skipped:
        print(f"not carried: {clean(line)}")
    print(f"{co['number']} carries {len(recordtypes.changes_of(hub.kernel.get(co['locator'])))} record-type change(s); "
          "they come into force when it is approved and released")
    return 0


def register(sub) -> None:
    s = sub.add_parser("record-types", help="the record types in force (under change control once the SOPs are documents); "
                                            "carry record-type changes on a change order")
    t = s.add_subparsers(dest="rt_cmd", required=True, metavar="ACTION", help="status, show or carry")
    t.add_parser("status", help="the record types in force, their versions, and whether the file on disk differs").set_defaults(fn=cmd_status)
    c = t.add_parser("show", help="one record type in force, as JSON")
    c.add_argument("type", help="the record type, e.g. ncr")
    c.set_defaults(fn=cmd_show)
    c = t.add_parser("carry", help="put record-type changes on a change order; refused when one is destructive or invalid")
    c.add_argument("co", help="the change order's number (CO-2026-0001)")
    c.add_argument("--as", dest="as_user", required=True, metavar="PERSON", help="its requester or a document controller")
    c.add_argument("--changes", metavar="JSON", help='a JSON list, e.g. [{"op": "add_field", "type": "ncr", "field": "batch", "spec": "text"}]')
    c.add_argument("--changes-file", metavar="PATH", help="a file holding that JSON list")
    c.add_argument("--from-file", action="store_true", help="what config/record-types.json adds to the record types in force")
    c.add_argument("--reason", default="", metavar="TEXT", help="why (kept on each change)")
    c.set_defaults(fn=cmd_carry)
