"""A permission given to a group or a role widens each of its members (issue #76): none of them reviews that change,
as a cog named directly does not. That holds for a group's members through its roles, and for a group the same
change order creates; removing a route widens nobody."""

from hubproto import changecontrol as cc
from hubproto.routing import principals_for
from test_change_control import hub, new_co  # noqa: F401  (the converted hub fixture)


def add(hub, co, rule, op="add"):
    cc.add_direct_change(hub, co["number"], {"op": op, "rule": rule}, "alice")
    cc.compile_delta(hub, co["number"])


def permission(hub, subject, action="approve", obj="document", folder="Policy"):
    template = next(r for r in hub.settings_rules() if r["kind"] == "permission" and r.get("effect") == "allow")
    rule = {k: v for k, v in template.items() if k not in ("source", "key")}
    rule.update({"subject": subject, "action": action, "object": obj})
    for k in ("folder", "resource"):
        if k in rule:
            rule[k] = folder if k == "folder" else f"folder:{folder}"
    return rule


def group(hub, name, members=(), roles=()):
    template = next(r for r in hub.settings_rules() if r["kind"] == "group")
    rule = {k: v for k, v in template.items() if k not in ("source", "key")}
    rule.update({"name": name, "members": list(members), "roles": list(roles)})
    return rule


def everyone(hub, ref):
    return set(principals_for(hub.current_bundle(), ref))


def test_a_grant_to_a_group_widens_exactly_its_members_and_keeps_them_off_its_panel(hub):
    co = new_co(hub, title="More for the junior cogs")
    add(hub, co, permission(hub, "group:junior-cogs"))
    assert cc.widened_principals(hub, co["number"]) == everyone(hub, "group:junior-cogs")
    cc.request_review(hub, co["number"], "alice", panel=["it-admin", "management", "carol"])
    rnd = cc.current_round(hub.kernel.get(co["locator"]))
    assert {x["who"] for x in rnd["left_off"]} == {"it-admin", "management"}, rnd["left_off"]
    assert "carol" in {e["who"] for e in rnd["panel"]} and not {"it-admin", "management"} & {e["who"] for e in rnd["panel"]}


def test_a_grant_to_a_role_widens_exactly_everyone_holding_it(hub):
    co = new_co(hub, title="More for authors")
    add(hub, co, permission(hub, "role:author"))
    widened = cc.widened_principals(hub, co["number"])
    assert widened == everyone(hub, "role:author") and {"qa-manager", "doc-reviewer", "alice"} <= widened


def test_a_group_given_a_role_widens_everyone_holding_it(hub):
    """senior-cogs gains the author role: alice, doc-reviewer and qa-manager join it (and its committee)."""
    co = new_co(hub, title="Authors on the senior cogs")
    old = next(r for r in hub.settings_rules() if r["kind"] == "group" and r["name"] == "senior-cogs")
    add(hub, co, {**{k: v for k, v in old.items() if k not in ("source", "key")}, "roles": ["author"]})
    assert everyone(hub, "role:author") <= cc.widened_principals(hub, co["number"])


def test_a_group_created_and_given_a_permission_in_one_change_order_widens_its_members(hub):
    co = new_co(hub, title="A new group of authors and dave")
    add(hub, co, group(hub, "plus", members=["dave"], roles=["author"]))
    add(hub, co, permission(hub, "group:plus"))
    widened = cc.widened_principals(hub, co["number"])
    assert widened >= everyone(hub, "role:author") | {"dave"}, widened


def test_removing_a_route_widens_nobody_it_named(hub):
    route = next((r for r in hub.settings_rules() if r["kind"] == "routing" and str(r.get("assignee") or "").startswith("role:")), None)
    co = new_co(hub, title="Drop a route")
    add(hub, co, {k: v for k, v in route.items() if k != "key"}, op="remove")
    named = everyone(hub, route["assignee"])
    assert named and not named & cc.widened_principals(hub, co["number"]), "its assignees gain no authority"


def test_removing_a_group_that_carries_a_deny_widens_its_members(tmp_path):
    """Its members lose the "may not" it carried (guards._flag_lost_denies): none of them reviews its removal."""
    import shutil

    from conftest import ROOT, release_all
    from hubproto import sopdocs
    from hubproto.app import Hub

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    org = tmp_path / "frames" / "company" / "org-model.frame.md"
    text = org.read_text()
    assert "## Permissions\n" in text
    org.write_text(text.replace("## Permissions\n", "## Permissions\nThe junior-cogs group may not approve documents in Policy.\n", 1))
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    sopdocs.convert(h, by="carol")
    co = new_co(h, title="Drop the junior cogs group")
    old = next(r for r in h.settings_rules() if r["kind"] == "group" and r["name"] == "junior-cogs")
    add(h, co, {k: v for k, v in old.items() if k != "key"}, op="remove")
    assert any(cc.widening(c) for c in cc.delta(h, co["number"])), "its members lose a deny: a widening"
    assert everyone(h, "group:junior-cogs") <= cc.widened_principals(h, co["number"])
