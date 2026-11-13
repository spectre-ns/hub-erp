"""A permission given to a group or a role widens each of its members (issue #76): none of them reviews that change,
as a cog named directly does not."""

from hubproto import changecontrol as cc
from test_change_control import hub, new_co  # noqa: F401  (the converted hub fixture)


def grant(hub, co, subject, action="approve", obj="document", folder="Policy"):
    template = next(r for r in hub.settings_rules() if r["kind"] == "permission" and r.get("effect") == "allow")
    rule = {k: v for k, v in template.items() if k not in ("source", "key")}
    rule.update({"subject": subject, "action": action, "object": obj})
    for k in ("folder", "resource"):
        if k in rule:
            rule[k] = folder if k == "folder" else f"folder:{folder}"
    cc.add_direct_change(hub, co["number"], {"op": "add", "rule": rule}, "alice")
    cc.compile_delta(hub, co["number"])
    assert any(cc.widening(c) for c in cc.delta(hub, co["number"])), "the grant is a widening"


def test_a_grant_to_a_group_widens_its_members_and_keeps_them_off_its_panel(hub):
    co = new_co(hub, title="More for the junior cogs")
    grant(hub, co, "group:junior-cogs")
    widened = cc.widened_principals(hub, co["number"])
    assert {"doc-reviewer", "management", "it-admin", "qa-manager"} <= widened, widened
    cc.request_review(hub, co["number"], "alice", panel=["it-admin", "management", "carol"])
    rnd = cc.current_round(hub.kernel.get(co["locator"]))
    assert {x["who"] for x in rnd["left_off"]} == {"it-admin", "management"}, rnd["left_off"]
    assert "carol" in {e["who"] for e in rnd["panel"]} and not {"it-admin", "management"} & {e["who"] for e in rnd["panel"]}


def test_a_grant_to_a_role_widens_everyone_holding_it(hub):
    co = new_co(hub, title="More for authors")
    grant(hub, co, "role:author")
    widened = cc.widened_principals(hub, co["number"])
    assert {"qa-manager", "doc-reviewer", "alice"} <= widened, widened
