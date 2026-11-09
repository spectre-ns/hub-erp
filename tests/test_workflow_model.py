"""Workflow graphs as data (change control v2, section 10): today's workflows in the 10.1 shape, the shape checks and the
section 10.3 checks (including a mandatory gate bypassed), the change in words (10.4), tidy's layout, every editor
operation, and the geometry the drawing and the editor's script share."""

import copy

import pytest

from conftest import release_all
from hubproto import workflow_model as wm


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    import shutil

    from conftest import ROOT
    from hubproto.app import Hub

    root = tmp_path_factory.mktemp("wfm")
    shutil.copytree(ROOT / "frames", root / "frames")
    hub = Hub(root)
    hub.kernel.seed()
    release_all(hub)
    b = hub.releases.bundle(hub.releases.current()["digest"])
    return {"bundle": b, "types": hub.kernel.types}


AWAIT = "awaiting-disposition-the-nonconformance"


def ncr(bundle) -> dict:
    return wm.graph_of(bundle["bundle"], "nonconformance", bundle["types"])


def small() -> dict:
    """Open -> Review -> Done, with a Reject back to Open and Cancel from anywhere."""
    return {"process": "p", "record_type": "x", "statuses": [
        {"id": "open", "name": "Open", "category": "todo"}, {"id": "review", "name": "Review", "category": "in_progress", "sla_days": 3},
        {"id": "done", "name": "Done", "category": "done", "outcome": "completed"},
        {"id": "cancelled", "name": "Cancelled", "category": "done", "outcome": "cancelled"}],
        "transitions": [
            {"id": "create", "type": "initial", "to": "open", "name": "Create", "gate": {"kind": "person", "role": "staff"}},
            {"id": "submit", "type": "directed", "from": ["open"], "to": "review", "name": "Submit", "gate": {"kind": "auto", "when": "actions-succeed"},
             "actions": ["file-it"]},
            {"id": "approve", "type": "directed", "from": ["review"], "to": "done", "name": "Approve",
             "gate": {"kind": "person", "role": "quality-manager", "not": ["author"]}, "validators": [{"fields_set": ["verdict"]}],
             "actions": ["apply-it"], "fields": ["verdict"]},
            {"id": "back", "type": "directed", "from": ["review"], "to": "open", "name": "Return", "gate": {"kind": "person", "role": "quality-manager"}},
            {"id": "cancel", "type": "global", "to": "cancelled", "name": "Cancel", "gate": {"kind": "person", "role": "owner"}}]}


def codes(problems, level=None) -> set:
    return {p["code"] for p in problems if level is None or p["level"] == level}


# ---------------------------------------------------------------- today's workflows in the 10.1 shape
def test_every_process_in_the_release_converts_and_checks_clean(bundle):
    b = bundle["bundle"]
    assert len(b["workflows"]) > 10
    for name in b["workflows"]:
        g = wm.graph_of(b, name, bundle["types"])
        assert wm.checks(g, bundle=b) == [], name
        assert {s["id"] for s in g["statuses"]} >= set(b["workflows"][name]["states"]), "every state of today is a status"
        assert sum(1 for t in g["transitions"] if t["type"] == "initial") == 1


def test_the_graph_is_the_compiled_one_without_the_earlier_view(bundle):
    """graph_of gives workstream 7's compiled graph (hubproto.process_graph) without the derived keys of the earlier
    state-machine shape; the hub's own failed status and Retry loop are marked system."""
    b = bundle["bundle"]
    g = ncr(bundle)
    assert not set(g) & {"object", "states", "checkpoints", "terminal", "reject_to", "sla"}
    assert {k: v for k, v in b["workflows"]["nonconformance"].items() if k in g} == g
    sm, tm = wm.status_map(g), wm.transition_map(g)
    assert sm[AWAIT]["category"] == "in_progress" and sm[AWAIT]["sla_days"] == 5
    assert wm.is_system(sm["failed"]) and wm.is_system(tm["retry"]) and not wm.is_system(sm["closed"])
    assert tm["disposition-the-nonconformance"]["from"] == [AWAIT] and tm["cancel"]["type"] == "global"
    with pytest.raises(ValueError, match="the hub keeps"):
        wm.apply_op(g, {"op": "delete_status", "id": "failed"})
    with pytest.raises(ValueError, match="the hub keeps"):
        wm.apply_op(g, {"op": "delete_transition", "id": "retry"})


def test_a_release_from_before_graphs_is_compiled_the_same_way(bundle):
    b = bundle["bundle"]
    old_shape = {k: v for k, v in b["workflows"]["nonconformance"].items() if k not in ("statuses", "transitions", "source", "form", "process")}
    earlier = {**b, "workflows": {"nonconformance": old_shape}}
    assert wm.graph_of(earlier, "nonconformance") == {k: v for k, v in ncr(bundle).items()}
    with pytest.raises(KeyError):
        wm.graph_of({"workflows": {"x": {"states": []}}, "processes": {}}, "x")


def test_actions_as_the_editor_writes_them_and_as_the_release_carries_them():
    assert wm.action_from_text("notify:quality-manager") == {"notify": "role:quality-manager"}
    assert wm.action_from_text("notify:owner") == {"notify": "field:owner"}
    assert wm.action_from_text("set:priority=high") == {"set": {"priority": "high"}}
    assert wm.action_from_text("set:locked=true") == {"set": {"locked": True}}
    assert wm.action_from_text(" capa-opener ") == "capa-opener"
    for a in ({"notify": "role:quality-manager"}, {"set": {"priority": "high"}}, "capa-opener"):
        assert wm.action_from_text(wm.action_text(a)) == a
    assert wm.action_words({"notify": "role:quality-manager"}) == "tells the quality manager"


def test_a_bundle_already_in_the_graph_shape_is_returned_as_it_is():
    g = small()
    assert wm.graph_of({"workflows": {"p": g}}, "p") == g
    with pytest.raises(KeyError):
        wm.graph_of({"workflows": {}}, "nope")


# ---------------------------------------------------------------- shape
def test_validate_names_each_bad_element():
    g = small()
    g["statuses"].append({"id": "open", "name": "Again", "category": "todo"})
    g["statuses"][1]["outcome"] = "completed"
    g["transitions"][1]["from"] = ["nowhere"]
    g["transitions"][2]["gate"] = {"kind": "boss"}
    g["transitions"][3]["validators"] = [{"fields_set": ["a"], "condition": "risk_low"}, {"condition": "the moon is full"}]
    g["transitions"][4]["gate"] = {"kind": "auto", "when": "the moon is full"}
    found = {(p["code"], p.get("id")) for p in wm.validate(g)}
    assert ("duplicate", "open") in found and ("outcome", "review") in found and ("reference", "submit") in found
    assert ("gate", "approve") in found and ("validator", "back") in found and ("gate", "cancel") in found
    assert all(p["level"] == "error" for p in wm.validate(g))
    assert wm.validate({"statuses": "x"})[0]["code"] == "shape"
    assert wm.validate(small()) == []


# ---------------------------------------------------------------- checks (10.3)
def test_the_structural_errors(bundle):
    g = small()
    assert wm.checks(g) == []
    no_start = copy.deepcopy(g)
    no_start["transitions"] = [t for t in no_start["transitions"] if t["type"] != "initial"]
    assert "no-initial" in codes(wm.checks(no_start), "error")
    lonely = wm.apply_op(g, {"op": "add_status", "name": "Limbo", "category": "in_progress", "id": "limbo", "at": [0, 0]})
    found = {(p["code"], p["id"]) for p in wm.checks(lonely)}
    assert ("unreachable", "limbo") in found and ("dead-end", "limbo") in found
    reach = wm.apply_op(lonely, {"op": "add_transition", "id": "go", "name": "Go", "from": ["review"], "to": "limbo"})
    found = {(p["code"], p["id"]) for p in wm.checks(reach)}
    assert ("unreachable", "limbo") not in found and ("dead-end", "limbo") in found
    roles = copy.deepcopy(g)
    wm.transition_map(roles)["approve"]["gate"] = {"kind": "person", "role": "wizard"}
    b = bundle["bundle"]
    assert ("role", "approve") in {(p["code"], p["id"]) for p in wm.checks(roles, bundle=b)}
    cog = copy.deepcopy(g)
    wm.transition_map(cog)["approve"]["gate"] = {"kind": "cog", "cog": "ap-reviewer", "fallback": "quality-manager"}
    assert ("cog-criteria", "approve") in {(p["code"], p["id"]) for p in wm.checks(cog, bundle=b)}, "ap-reviewer has no criteria for p"
    cog["process"] = "purchase-to-pay"
    assert "cog-criteria" not in codes(wm.checks(cog, bundle=b))
    wm.transition_map(cog)["approve"]["gate"]["cog"] = "nobody-cog"
    assert "cog" in codes(wm.checks(cog, bundle=b))


def test_automatic_transitions_that_overlap_or_leave_a_gap():
    g = small()
    # Tried in order: an unconditional one first means a later one can never fire.
    two = wm.apply_op(g, {"op": "add_transition", "id": "skip", "name": "Skip", "from": ["open"], "to": "done", "gate": {"kind": "auto"}})
    p = [x for x in wm.checks(two) if x["code"] == "auto-overlap"]
    assert p and p[0]["id"] == "open" and "“Skip” can never fire" in p[0]["text"] and set(p[0]["transitions"]) == {"submit", "skip"}
    g2 = copy.deepcopy(g)
    wm.transition_map(g2)["submit"]["gate"] = {"kind": "auto", "when": "risk_low"}
    assert "auto-gap" in codes(wm.checks(g2)), "when the risk is not low the item is stuck in Open"
    g3 = wm.apply_op(g2, {"op": "add_transition", "id": "slow", "name": "Slow", "from": ["open"], "to": "review",
                          "gate": {"kind": "auto", "when": "not risk_low"}})
    assert not codes(wm.checks(g3)) & {"auto-gap", "auto-overlap"}, "a condition and its negation cover every item once"
    g4 = wm.apply_op(g3, {"op": "add_transition", "id": "also", "name": "Also", "from": ["open"], "to": "done", "gate": {"kind": "auto", "when": "risk_low"}})
    assert "auto-overlap" in codes(wm.checks(g4))
    # A condition first and an unconditional "otherwise" after it is how the graph grammar writes a fast lane.
    lane = wm.apply_op(g2, {"op": "add_transition", "id": "else", "name": "Otherwise", "from": ["open"], "to": "review", "gate": {"kind": "auto"}})
    assert not codes(wm.checks(lane)) & {"auto-gap", "auto-overlap"}


def test_widenings_against_the_workflow_in_force():
    old = small()
    new = copy.deepcopy(old)
    tm = wm.transition_map(new)
    tm["approve"]["gate"] = {"kind": "auto", "when": "actions-succeed"}
    tm["approve"]["validators"] = []
    found = [p for p in wm.checks(new, old=old) if p["code"] == "widening"]
    assert any(p["id"] == "approve" and "weaker gate" in p["text"] for p in found)
    assert any(p["id"] == "approve" and "segregation of duties removed" in p["text"] for p in found)
    assert any(p["id"] == "approve" and "no longer checks that verdict is filled in" in p["text"] for p in found)
    glob = wm.apply_op(old, {"op": "add_transition", "id": "hold", "name": "Hold", "from": "any", "to": "review"})
    assert any(p["id"] == "hold" and "any status" in p["text"] for p in wm.checks(glob, old=old) if p["code"] == "widening")
    big = copy.deepcopy(old)
    for i in range(23):
        big = wm.apply_op(big, {"op": "add_status", "name": f"S{i}", "id": f"s{i}", "at": [0, 0], "category": "done"})
    assert "size" in codes(wm.checks(big), "warning")


def test_a_mandatory_gate_bypassed_is_a_warning_with_the_path():
    old = small()
    assert wm.mandatory_gates(old) == {"approve": True, "back": False, "cancel": False}
    new = wm.apply_op(old, {"op": "add_transition", "id": "fast", "name": "Fast close", "from": ["open"], "to": "done",
                            "gate": {"kind": "person", "role": "staff"}})
    bypass = [p for p in wm.checks(new, old=old) if p["code"] == "bypass"]
    assert bypass and bypass[0]["id"] == "approve" and bypass[0]["path"] == ["open", "fast", "done"]
    assert bypass[0]["text"].startswith("Open → Fast close → Done avoids “Approve”") and bypass[0]["level"] == "warning"
    # Removing the gate (the decision becomes automatic) is a bypass too, through the now ungated transition.
    auto = copy.deepcopy(old)
    wm.transition_map(auto)["approve"]["gate"] = {"kind": "auto"}
    path = next(p for p in wm.checks(auto, old=old) if p["code"] == "bypass")["path"]
    assert path == ["open", "submit", "review", "approve", "done"]
    # A gate that was never mandatory (there was another way) raises nothing.
    assert not [p for p in wm.checks(wm.apply_op(new, {"op": "rename_transition", "id": "fast", "name": "Quick"}), old=new) if p["code"] == "bypass"]


def test_removing_a_status_with_items_needs_a_place_for_them():
    old = small()
    new = wm.apply_op(old, {"op": "delete_status", "id": "review"})
    p = [x for x in wm.checks(new, old=old, items={"review": 4}) if x["code"] == "state-map"]
    assert p and p[0]["level"] == "error" and p[0]["text"].startswith("4 items are in “Review”") and p[0]["count"] == 4
    doc = wm.apply_op({"graph": old, "layout": wm.tidy(old), "state_map": {}}, {"op": "delete_status", "id": "review", "move_to": "open"})
    found = [x for x in wm.checks(doc["graph"], old=old, items={"review": 4}, state_map=doc["state_map"]) if x["code"] == "items-move"]
    assert found and found[0]["level"] == "warning" and "they move to “Open”" in found[0]["text"]
    assert not [x for x in wm.checks(new, old=old, items={"review": 0}) if x["code"] in ("state-map", "items-move")]


# ---------------------------------------------------------------- the change in words (10.4)
def test_diff_in_words_keyed_by_element(bundle):
    old = small()
    d = {"graph": old, "layout": wm.tidy(old), "state_map": {}}
    for op in ({"op": "rename_status", "id": "open", "name": "New"}, {"op": "recategorise_status", "id": "review", "category": "todo"},
               {"op": "set_sla", "id": "review", "days": 5}, {"op": "add_status", "id": "extra", "name": "Extra", "at": [0, 0]},
               {"op": "reroute_transition", "id": "back", "to": "extra"}, {"op": "rename_transition", "id": "submit", "name": "Send"},
               {"op": "set_gate", "id": "approve", "gate": {"kind": "cog", "cog": "qa-manager", "fallback": "quality-manager"}},
               {"op": "set_validators", "id": "approve", "validators": []}, {"op": "set_actions", "id": "approve", "actions": ["notify:quality-manager"]},
               {"op": "set_fields", "id": "approve", "fields": []}, {"op": "add_transition", "id": "t9", "name": "Again", "from": ["extra"], "to": "review"}):
        d = wm.apply_op(d, op)
    items = wm.diff(old, d["graph"], by="carol", at="2026-11-09T10:00:00+00:00")
    by = {(i["key"], i["change"]): i for i in items}
    assert by[("status:open", "renamed")]["text"] == "Status “Open” renamed “New” (the same status: no item moves)"
    assert "is now to do, was in progress" in by[("status:review", "recategorised")]["text"]
    assert "due within 5 days (was 3 days)" in by[("status:review", "due")]["text"]
    assert by[("status:extra", "added")]["op"] == "add"
    assert "now goes from “Review” to “Extra”" in by[("transition:back", "rerouted")]["text"]
    gate = by[("transition:approve", "gate")]
    assert gate["text"] == ("“Approve”: the qa-manager cog decides within its approval criteria, otherwise the quality manager, instead of "
                            "the quality manager decides, not the item's author") and gate["widening"]
    assert by[("transition:approve", "validators")]["widening"] and "checks nothing (was verdict is filled in)" in by[("transition:approve", "validators")]["text"]
    assert "then tells the quality manager (was apply it)" in by[("transition:approve", "actions")]["text"]
    assert ("transition:approve", "fields") in by and ("transition:t9", "added") in by and ("transition:submit", "renamed") in by
    for i in items:  # the section 8 delta item keys
        assert i["origin"]["kind"] == "direct" and i["origin"]["by"] == "carol" and i["warnings"] == [] and i["stale"] is False
        assert i["id"].startswith("workflow:p:") and i["kind"] == "workflow"
    assert len({i["id"] for i in items}) == len(items)


def test_a_global_whose_exceptions_still_mean_the_done_statuses_has_not_changed():
    old = small()
    wm.transition_map(old)["cancel"]["except"] = ["done", "cancelled"]  # written out, as the compile writes it
    new = wm.apply_op(old, {"op": "add_status", "id": "void", "name": "Void", "category": "done", "outcome": "cancelled", "at": [0, 0]})
    new = wm.apply_op(new, {"op": "add_transition", "id": "void-it", "name": "Void it", "from": ["review"], "to": "void"})
    assert [i["change"] for i in wm.diff(old, new) if i["key"] == "transition:cancel"] == ["rerouted"], "Void is done but not excepted"
    trimmed = wm.apply_op(old, {"op": "delete_status", "id": "done"})
    assert not [i for i in wm.diff(old, trimmed) if i["key"] == "transition:cancel"], "the done statuses, one fewer: the same meaning"


def test_a_removed_status_is_one_item_with_its_transitions_as_children():
    old = small()
    doc = wm.apply_op({"graph": old, "layout": {}, "state_map": {}}, {"op": "delete_status", "id": "review", "move_to": "open"})
    items = wm.diff(old, doc["graph"], state_map=doc["state_map"], items={"review": 2})
    assert [i["key"] for i in items] == ["status:review"]
    parent = items[0]
    assert parent["op"] == "remove" and "2 items in it move to “Open”" in parent["text"]
    assert sorted(c["element_id"] for c in parent["children"]) == ["approve", "back", "submit"]


def test_a_layout_change_is_one_item_with_no_effect():
    g = small()
    lay = wm.tidy(g)
    moved = wm.apply_op({"graph": g, "layout": lay, "state_map": {}}, {"op": "move", "id": "done", "at": [800, 400]})
    moved = wm.apply_op(moved, {"op": "move_label", "id": "approve", "offset": [16, -8]})
    items = wm.diff(g, moved["graph"], old_layout=lay, new_layout=moved["layout"])
    assert len(items) == 1 and items[0]["key"] == "layout" and items[0]["no_effect"] and items[0]["text"] == "Layout changed: no effect on how items move"
    assert wm.layout_only(items)
    assert wm.diff(g, g, old_layout=lay, new_layout=dict(lay)) == []


# ---------------------------------------------------------------- tidy
def test_tidy_is_deterministic_on_the_grid_with_done_statuses_last(bundle):
    for name in ("nonconformance", "capa", "design-release", "document-control"):
        g = wm.graph_of(bundle["bundle"], name, bundle["types"])
        a, b = wm.tidy(g), wm.tidy(copy.deepcopy(g))
        assert a == b and list(a) == [s["id"] for s in g["statuses"]]
        assert all(isinstance(v, int) and v % 8 == 0 for xy in a.values() for v in xy)
        done_x = {a[s["id"]][0] for s in g["statuses"] if s["category"] == "done"}
        assert len(done_x) == 1 and max(x for x, _ in a.values()) == done_x.pop()
        boxes = list(a.values())
        assert len({tuple(b) for b in boxes}) == len(boxes), "no two statuses in one place"
    g = ncr(bundle)
    lay = wm.tidy(g)
    assert lay["open"][1] == lay["awaiting-disposition-the-nonconformance"][1] == lay["closed"][1], "the main path runs straight"
    assert lay["closed"][1] < lay["rejected"][1], "the completed outcome first"
    sz = wm.sizes(g)
    op = lay["open"]
    assert abs((lay["failed"][0] + sz["failed"][0] / 2) - (op[0] + sz["open"][0] / 2)) <= wm.GRID, "Failed is tucked under the start of the flow"
    assert lay["failed"][1] - (op[1] + sz["open"][1]) == wm.VGAP


def test_tidy_lays_a_cycle_out_by_the_forward_path():
    g = small()
    lay = wm.tidy(g)
    assert lay["open"][0] < lay["review"][0] < lay["done"][0], "the Return loop back to Open does not count"


# ---------------------------------------------------------------- operations
def test_every_operation_applies_and_leaves_its_input_alone():
    g = small()
    doc = {"graph": g, "layout": wm.tidy(g), "state_map": {}}
    before = copy.deepcopy(doc)
    steps = [
        ({"op": "add_status", "name": "Awaiting verification", "category": "in_progress"}, lambda d: "awaiting-verification" in wm.status_map(d["graph"])),
        ({"op": "add_status", "name": "On hold", "global": True, "id": "hold", "at": [10, 10]},
         lambda d: d["layout"]["hold"] == [8, 8] and wm.transition_map(d["graph"])["to-hold"]["type"] == "global"),
        ({"op": "rename_status", "id": "review", "name": "Under review"}, lambda d: wm.status_map(d["graph"])["review"]["name"] == "Under review"),
        ({"op": "recategorise_status", "id": "hold", "category": "done", "outcome": "superseded"},
         lambda d: wm.status_map(d["graph"])["hold"]["outcome"] == "superseded"),
        ({"op": "recategorise_status", "id": "hold", "category": "todo"}, lambda d: "outcome" not in wm.status_map(d["graph"])["hold"]),
        ({"op": "set_sla", "id": "review", "days": 7}, lambda d: wm.status_map(d["graph"])["review"]["sla_days"] == 7),
        ({"op": "set_sla", "id": "review", "days": None}, lambda d: "sla_days" not in wm.status_map(d["graph"])["review"]),
        ({"op": "add_transition", "name": "Verify", "from": ["review"], "to": "awaiting-verification"},
         lambda d: wm.transition_map(d["graph"])["verify"]["gate"] == wm.DEFAULT_GATE),
        ({"op": "add_transition", "name": "Park", "from": "any", "to": "hold", "except": ["done"]},
         lambda d: wm.transition_map(d["graph"])["park"]["except"] == ["done"]),
        ({"op": "rename_transition", "id": "verify", "name": "Send to verification"}, lambda d: wm.transition_map(d["graph"])["verify"]["name"] == "Send to verification"),
        ({"op": "reroute_transition", "id": "verify", "from": ["open", "review"]}, lambda d: wm.transition_map(d["graph"])["verify"]["from"] == ["open", "review"]),
        ({"op": "set_gate", "id": "verify", "gate": {"kind": "cog", "cog": "qa-manager", "fallback": "quality-manager", "junk": 1}},
         lambda d: wm.transition_map(d["graph"])["verify"]["gate"] == {"kind": "cog", "cog": "qa-manager", "fallback": "quality-manager"}),
        ({"op": "set_validators", "id": "verify", "validators": [{"status_visited": "review"}]},
         lambda d: wm.transition_map(d["graph"])["verify"]["validators"] == [{"status_visited": "review"}]),
        ({"op": "set_actions", "id": "verify", "actions": ["notify:quality-manager", " ", "set:priority=high"]},
         lambda d: wm.transition_map(d["graph"])["verify"]["actions"] == ["notify:quality-manager", "set:priority=high"]),
        ({"op": "set_fields", "id": "verify", "fields": ["a", "b", "a"]}, lambda d: wm.transition_map(d["graph"])["verify"]["fields"] == ["a", "b"]),
        ({"op": "set_global", "id": "verify", "global": True}, lambda d: wm.transition_map(d["graph"])["verify"]["type"] == "global"
         and "from" not in wm.transition_map(d["graph"])["verify"]),
        ({"op": "set_except", "id": "verify", "except": ["open"]}, lambda d: wm.transition_map(d["graph"])["verify"]["except"] == ["open"]),
        ({"op": "set_except", "id": "verify", "except": None}, lambda d: "except" not in wm.transition_map(d["graph"])["verify"]),
        ({"op": "set_global", "id": "verify", "global": False, "from": ["review"]}, lambda d: wm.transition_map(d["graph"])["verify"]["from"] == ["review"]),
        ({"op": "move", "id": "review", "at": [403, 197]}, lambda d: d["layout"]["review"] == [400, 200]),
        ({"op": "move_label", "id": "approve", "offset": [13, -3]}, lambda d: d["layout"]["label:approve"] == [16, 0]),
        ({"op": "move_label", "id": "approve", "offset": None}, lambda d: "label:approve" not in d["layout"]),
        ({"op": "delete_transition", "id": "park"}, lambda d: "park" not in wm.transition_map(d["graph"])),
        ({"op": "delete_status", "id": "hold"}, lambda d: "hold" not in wm.status_map(d["graph"]) and "to-hold" not in wm.transition_map(d["graph"])),
        ({"op": "delete_status", "id": "review", "move_to": "open"},
         lambda d: d["state_map"] == {"review": "open"} and "approve" not in wm.transition_map(d["graph"]) and "review" not in d["layout"]),
        ({"op": "tidy"}, lambda d: d["layout"] == wm.tidy(d["graph"])),
        ({"op": "batch", "ops": [{"op": "rename_status", "id": "open", "name": "Start"}, {"op": "set_sla", "id": "open", "days": 2}]},
         lambda d: wm.status_map(d["graph"])["open"] == {"id": "open", "name": "Start", "category": "todo", "sla_days": 2}),
    ]
    assert {s[0]["op"] for s in steps} == set(wm.OPS), "every operation is exercised"
    for op, ok in steps:
        prepared = wm.prepare_op(doc, op)
        new = wm.apply_op(doc, prepared)
        assert ok(new), op
        assert wm.apply_op(doc, prepared) == new, "the same operation gives the same document"
        assert wm.op_words(doc, prepared)
        doc = new
    assert before == {"graph": g, "layout": wm.tidy(g), "state_map": {}}, "apply_op is pure"
    assert wm.validate(doc["graph"]) == []


def test_operations_refuse_in_words():
    g = small()
    bad = [({"op": "rename_status", "id": "nope", "name": "X"}, "there is no status"), ({"op": "rename_status", "id": "open", "name": "Review"}, "another status"),
           ({"op": "add_status", "name": ""}, "give the status a name"), ({"op": "recategorise_status", "id": "open", "category": "later"}, "category"),
           ({"op": "set_sla", "id": "open", "days": "soon"}, "whole number"), ({"op": "add_transition", "name": "X", "from": [], "to": "done"}, "at least one"),
           ({"op": "add_transition", "name": "X", "to": "open", "type": "initial"}, "a start already"),
           ({"op": "reroute_transition", "id": "cancel", "from": ["open"]}, "make it directed first"),
           ({"op": "set_gate", "id": "approve", "gate": {"kind": "boss"}}, "a gate is"), ({"op": "delete_status", "id": "review", "move_to": "review"}, "a status that stays"),
           ({"op": "set_global", "id": "create", "global": True}, "the start"), ({"op": "fly"}, "unknown operation")]
    for op, words in bad:
        with pytest.raises(ValueError, match=words):
            wm.apply_op(g, op)


def test_new_ids_come_from_names_and_stay_unique():
    g = small()
    a = wm.prepare_op(g, {"op": "add_status", "name": "Review again"})
    assert a["id"] == "review-again" and a["at"][0] % 8 == 0
    b = wm.prepare_op(g, {"op": "add_transition", "name": "Approve", "from": ["open"], "to": "done"})
    assert b["id"] == "approve-2"
    c = wm.prepare_op(g, {"op": "add_transition", "from": ["open"], "to": "done"})
    assert c["name"] == "Done" and c["id"] == "done-2", "a transition is named after its target unless named"


def test_default_move_to():
    g = small()
    assert wm.default_move_to(g, "review") == "open", "the status the work came from"
    assert wm.default_move_to(g, "cancelled") == "done"


# ---------------------------------------------------------------- geometry shared with the editor's script
def test_ports_face_the_other_status():
    b = (100.0, 100.0, float(wm.W), float(wm.H))
    assert wm.port(b, (1000, 130)) == (100 + wm.W, 132)
    assert wm.port(b, (-500, 130)) == (100, 132)
    assert wm.port(b, (190, 900)) == (192, 100 + wm.H)
    assert wm.port(b, (190, -900)) == (192, 100)


def test_opposite_transitions_get_separate_curves_and_loops_arcs():
    g = small()
    bends = wm.bends(g)
    # The same bend on both: each bows to the left of its own direction, so they part.
    assert bends["submit"] == bends["back"] == wm.PAIR_GAP / 2 and bends["approve"] == 0
    lay = wm.tidy(g)
    a = wm.transition_geometry(g, lay, wm.transition_map(g)["submit"], bend=bends["submit"])
    b = wm.transition_geometry(g, lay, wm.transition_map(g)["back"], bend=bends["back"])
    assert a["kind"] == b["kind"] == "edge" and a["parts"][0][1] != b["parts"][0][1]
    assert abs(a["label"][1] - b["label"][1]) >= 32, "their labels sit apart"
    # Pinned: the editor's script computes the same curve (hubproto.shell.workflow_ui WF_JS curve()).
    assert wm.curve((0, 0), (100, 0), 40) == {"d": "M0,8 Q50,48 100,8", "label": (50.0, 28.0)}
    assert wm.curve((0, 0), (100, 0), 0)["d"] == "M0,0 Q50,0 100,0"
    loop = wm.loop_geometry((0, 0, wm.W, wm.H), 0)
    assert loop["d"] == "M74,64 C52,108 132,108 110,64" and loop["label"] == (92.0, 64 + 44 * 0.75 + 16)
    g2 = wm.apply_op(g, {"op": "add_transition", "id": "again", "name": "Again", "from": ["review"], "to": "review"})
    assert wm.transition_geometry(g2, lay, wm.transition_map(g2)["again"])["kind"] == "loop"
    glob = wm.transition_geometry(g, lay, wm.transition_map(g)["cancel"])
    sz = wm.sizes(g)
    glob = wm.transition_geometry(g, lay, wm.transition_map(g)["cancel"], sz=sz)
    lw = wm.label_size(wm.transition_map(g)["cancel"])[0]
    assert glob["kind"] == "global" and glob["label"] == (lay["cancelled"][0] - wm.GLOBAL_DX - lw / 2, lay["cancelled"][1] + sz["cancelled"][1] / 2), \
        "an any-status marker sits just left of its target"
    start = wm.transition_geometry(g, lay, wm.transition_map(g)["create"])
    assert start["kind"] == "initial" and start["start"] == (lay["open"][0] - wm.START_DX, lay["open"][1] + wm.H / 2)
    shared = wm.apply_op(g, {"op": "reroute_transition", "id": "cancel", "to": "cancelled"})
    shared = wm.apply_op(shared, {"op": "set_global", "id": "cancel", "global": False, "from": ["open", "review"]})
    geo = wm.transition_geometry(shared, lay, wm.transition_map(shared)["cancel"])
    assert geo["kind"] == "shared" and [p[0] for p in geo["parts"]] == ["arm:open", "arm:review", "line"]


def test_a_lone_transition_bows_round_a_status_in_its_way():
    g = small()
    lay = wm.tidy(g)
    g2 = wm.apply_op(g, {"op": "add_transition", "id": "skip", "name": "Skip", "from": ["open"], "to": "done"})
    assert lay["open"][1] == lay["review"][1] == lay["done"][1]
    assert wm.bends(g2).get("skip", 0) == 0 and wm.bends(g2, lay)["skip"] != 0, "the straight line would cross Review"


# ---------------------------------------------------------------- committee gates (workstream 9)
def committee_graph() -> dict:
    """Review decided by a committee: Approve to Done, and Return to Open (the committee's way back for an objection)."""
    g = small()
    tm = wm.transition_map(g)
    tm["approve"]["gate"] = {"kind": "committee", "committee": "compile-frame"}
    tm["back"]["gate"] = {"kind": "committee", "committee": "compile-frame"}
    return g


def test_a_committee_gate_is_a_decision_in_words_badges_and_checks(bundle):
    g = committee_graph()
    t = wm.transition_map(g)["approve"]
    assert wm.gate_words(g, t["gate"]) == "the compile-frame committee decides (consensus)"
    assert wm.gate_badge(t["gate"]) == ("committee", "compile-frame committee") and wm.gated(t)
    assert wm.checks(g, bundle=bundle["bundle"]) == [], "a defined committee with a way back for an objection"
    assert wm.mandatory_gates(g)["approve"], "the bypass search counts a committee's decision"
    unknown = copy.deepcopy(g)
    wm.transition_map(unknown)["approve"]["gate"]["committee"] = "tea"
    assert ("committee", "approve") in {(p["code"], p["id"]) for p in wm.checks(unknown, bundle=bundle["bundle"])}
    no_way_back = copy.deepcopy(g)
    wm.transition_map(no_way_back)["back"]["gate"] = {"kind": "person", "role": "quality-manager"}
    found = [p for p in wm.checks(no_way_back, bundle=bundle["bundle"]) if p["code"] == "committee-return"]
    assert found and found[0]["id"] == "approve" and "an objection has no way back" in found[0]["text"]
    assert not [p for p in wm.checks(no_way_back) if p["code"].startswith("committee")], \
        "without the release (the compile's call) the guard of hubproto.guards says it, not this too"
    bad = copy.deepcopy(g)
    wm.transition_map(bad)["approve"]["gate"] = {"kind": "committee"}
    assert ("gate", "approve") in {(p["code"], p["id"]) for p in wm.validate(bad)}


def test_from_a_committee_to_one_person_is_a_widening_and_set_gate_keeps_only_the_committee():
    old = committee_graph()
    new = wm.apply_op(old, {"op": "set_gate", "id": "approve", "gate": {"kind": "person", "role": "quality-manager"}})
    assert any(p["id"] == "approve" and "weaker gate" in p["text"] for p in wm.checks(new, old=old) if p["code"] == "widening")
    item = next(i for i in wm.diff(old, new) if i["key"] == "transition:approve" and i["change"] == "gate")
    assert item["widening"] and "instead of the compile-frame committee decides (consensus)" in item["text"]
    back = wm.apply_op(new, {"op": "set_gate", "id": "approve", "gate": {"kind": "committee", "committee": "compile-frame", "role": "x", "when": "y"}})
    assert wm.transition_map(back)["approve"]["gate"] == {"kind": "committee", "committee": "compile-frame"}
    assert not [p for p in wm.checks(back, old=new) if p["code"] == "widening" and p["id"] == "approve"], "a committee is stronger"


def test_a_process_rule_change_in_words_and_a_short_responsibility():
    old = {"kind": "process", "name": "p", "folder": "F", "record_type": "x", "steps": [], "terminal": "done", "reject_to": None,
           "statuses": small()["statuses"], "transitions": [t for t in small()["transitions"]]}
    new = copy.deepcopy(old)
    new["transitions"].append({"id": "verified", "type": "directed", "from": ["review"], "to": "done", "name": "Verified",
                               "gate": {"kind": "person", "role": "quality-manager"}})
    words = wm.process_change_words(old, new)
    assert words == ["Transition “Verified” added: from “Review” to “Done”, the quality manager decides"]
    reason = wm.process_change_reason(old, new)
    assert reason == "the p workflow (1 change): Transition “Verified” added: from “Review” to “Done”, the quality manager decides"
    assert wm.process_change_reason(old, copy.deepcopy(old)) == "the p workflow, restated (no change of meaning)"
    assert wm.responsibility_words(new, "role:quality-manager") == "Decides “Approve”, “Return” and “Verified” in the p workflow"
