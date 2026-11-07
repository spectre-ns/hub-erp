"""Command line for controlled templates; ``hubproto.cli`` calls ``register(sub)``.

  hubproto templates import --as PERSON [--submit]   the Frames' Markdown templates into the library
  hubproto templates list                             each template: revision, state, the revision in force
"""

from __future__ import annotations

from .cli import _hub, require_member


def print_report(report: list[dict]) -> None:
    for r in report:
        print(f"{r['action']:9s} {r['number']:10s} {r['lineage']:40s} {r['why']}")
    counts: dict[str, int] = {}
    for r in report:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    print("templates: " + (", ".join(f"{n} {a}" for a, n in sorted(counts.items())) or "no sources under frames/*/templates/"))


def cmd_import(args) -> int:
    hub = _hub(args)
    require_member(hub, args.as_user)
    report = hub.import_templates(by=args.as_user, submit=args.submit)
    print_report(report)
    return 1 if args.submit and any("not sent for approval" in r["why"] for r in report) else 0


def cmd_list(args) -> int:
    from .templates import templates

    hub = _hub(args)
    groups = templates(hub.kernel)
    if not groups:
        print("no templates in the library; `hubproto templates import --as PERSON` brings them in")
        return 0
    print(f"{'template':44s} {'in force':16s} {'open revision':22s} lineage")
    for g in groups:
        eff = g["effective"]
        cur = g["open"]
        eff_s = f"{eff['number']} rev {eff['fields'].get('revision')}" if eff else "-"
        cur_s = f"{cur['number']} rev {cur['fields'].get('revision')} {cur['state']}" if cur else "-"
        print(f"{g['title'][:44]:44s} {eff_s:16s} {cur_s[:22]:22s} {g['lineage']}")
    return 0


def register(sub) -> None:
    s = sub.add_parser("templates", help="controlled templates: import the Frames' Markdown templates into the library, list them")
    t = s.add_subparsers(dest="templates_cmd", required=True, metavar="ACTION", help="import or list")
    i = t.add_parser("import", help="bring frames/*/templates/*.md into the library as controlled templates (.docx); idempotent")
    i.add_argument("--as", dest="as_user", required=True, metavar="PERSON", help="the author the templates are filed by")
    i.add_argument("--submit", action="store_true", help="send each new or changed template for approval (document-control)")
    i.set_defaults(fn=cmd_import)
    t.add_parser("list", help="each template with the revision in force and any open revision").set_defaults(fn=cmd_list)
