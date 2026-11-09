"""Command line for SOPs and policies as documents; ``hubproto.cli`` calls ``register(sub)``.

  hubproto sops convert --as PERSON   each Frame becomes a policy, SOP or work-instruction document, in force
  hubproto sops status                each SOP: its document in force, any revision under way and its change order
"""

from __future__ import annotations

from .cli import _hub, fail, require_member


def cmd_convert(args) -> int:
    from . import sopdocs

    hub = _hub(args)
    require_member(hub, args.as_user)
    bundle = hub.releases.bundle(hub.releases.current()["digest"])
    from . import authz

    if not {"policy-owner", "document-controller"} & set(authz.roles_of(bundle, args.as_user)):
        print(f"{args.as_user} is neither a policy owner nor a document controller: they do not convert the policy")
        return 2
    if hub.sop_source():
        print("the SOPs are documents already; nothing to do (`hubproto sops status`)")
        return 0
    blockers = sopdocs.conversion_blockers(hub)
    if blockers:  # converting must change no rule, and leave a way (the change-order process) to change them after
        fail("not converted: " + "; ".join(blockers), 1)
    made = sopdocs.convert(hub, by=args.as_user)
    for r in made:
        print(f"{r['number']:10s} {r['fields']['doc_type']:7s} {r['folder']:30s} {r['fields']['sop_name']}")
    changes = hub.compile(save=False)["changes"]
    print(f"converted {len(made)} Frame(s); compiling from the documents now changes {len(changes)} rule(s)"
          + (" (the documents read back exactly as the release in force)" if not changes else ": check them before going on"))
    return 0 if not changes else 1


def cmd_status(args) -> int:
    from . import sopdocs

    hub = _hub(args)
    if not hub.sop_source():
        print("the SOPs are not documents yet; `hubproto sops convert --as PERSON` converts them")
        return 0
    names = sorted({d["fields"]["sop_name"] for d in hub.kernel.query(type_="document") if str(d["fields"].get("lineage", "")).startswith(sopdocs.LINEAGE)})
    print(f"{'SOP':34s} {'in force':16s} {'under way':30s}")
    for n in names:
        cur = sopdocs.in_force(hub.kernel, n)
        open_ = [d for d in sopdocs.of_lineage(hub.kernel, n) if d["state"] in ("draft", "open", "approved") or d["state"].startswith("awaiting")]
        way = ", ".join(f"{d['number']} {d['state']}" + (f" ({d['fields'].get('change_order')})" if d["fields"].get("change_order") else "") for d in open_)
        print(f"{n:34s} {(cur['number'] + ' rev ' + str(cur['fields'].get('revision'))) if cur else '-':16s} {way or '-'}")
    return 0


def register(sub) -> None:
    s = sub.add_parser("sops", help="SOPs and policies as documents the Frame is compiled from: convert the Frames, show where each stands")
    t = s.add_subparsers(dest="sops_cmd", required=True, metavar="ACTION", help="convert or status")
    c = t.add_parser("convert", help="make each Frame a policy, SOP or work-instruction document in force (once; idempotent)")
    c.add_argument("--as", dest="as_user", required=True, metavar="PERSON", help="a policy owner or document controller")
    c.set_defaults(fn=cmd_convert)
    t.add_parser("status", help="each SOP's document in force and any revision under way").set_defaults(fn=cmd_status)
