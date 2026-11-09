"""The workflow editor over HTTP (change control v2, section 10.5): the pages render (diagram, Text view, Changes) for
editors and read-only for everyone else; every edit works as a plain form (the Text view, no JavaScript) and as JSON (the
editor's script); one draft per person and process is saved as it changes, with undo and redo; a draft is carried on a
change order (``workflow_changes`` and ``state_map``), shown there as a visual diff, and its layout comes into force only
for an approved change order; Mermaid is gone."""

import json
import re
import shutil
from urllib.parse import quote

import pytest

from conftest import ROOT, release_all
from hubproto import workflow_model as wm, workflows as wfs
from hubproto.app import Hub
from hubproto.shell import serve_in_thread
from test_shell_views import get, main_of, post

NCR = "nonconformance"
AWAIT = "awaiting-disposition-the-nonconformance"


@pytest.fixture
def shell(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    server = serve_in_thread(hub, "127.0.0.1", 0)  # port 0: bound at once, no race with other test workers
    port = server.server_address[1]
    yield {"hub": hub, "base": f"http://127.0.0.1:{port}"}
    server.shutdown()
    server.server_close()


def op(shell, who: str, fields: dict, *, as_json: bool = False):
    data = {"as": who, "process": NCR, **fields}
    if as_json:
        data["fmt"] = "json"
    status, url, body = post(shell, "/workflow-op", data)
    return status, url, (json.loads(body) if as_json else body)


def jop(shell, who: str, operation: dict, **extra):
    return op(shell, who, {"op": "json", "op_json": json.dumps(operation), **extra}, as_json=True)


def doc_of(shell, who: str = "carol") -> dict:
    return wfs.draft_of(shell["hub"], who, NCR)["doc"]


def wf_data(html: str) -> dict:
    return json.loads(html.split("<script type='application/json' id='wf-data'>")[1].split("</script>")[0])


# ---------------------------------------------------------------- pages
def test_the_list_and_the_editor_render_for_editors_and_read_only_for_others(shell):
    status, text = get(shell, "/workflows", **{"as": "carol"})
    body = main_of(text)
    assert status == 200 and "/workflows/nonconformance" in body and "/workflows/purchase-to-pay" in body and "Edit" in body
    status, text = get(shell, "/workflows/" + NCR, **{"as": "carol"})
    body = main_of(text)
    assert status == 200 and "id='wf'" in body and "<svg class=\"wf wf-edit\"" in body and "Add status" in body and "Tidy" in body
    data = wf_data(body)
    assert data["editable"] and data["process"] == NCR and data["urls"]["op"] == "/workflow-op" and data["geo"]["W"] == wm.W
    # Every status and transition is a focusable, named element; each status has a connect handle.
    assert f'id="wfc-s-{AWAIT}"' in body and 'tabindex="0" role="button" aria-roledescription="status"' in body
    assert 'aria-label="Awaiting disposition the nonconformance, in progress, due within 5 days, 3 ways out, 0 items"' in body
    assert 'class="wf-handle" data-handle="open"' in body and 'id="wfc-t-disposition-the-nonconformance"' in body
    assert "id='live-say'" in body and "role='status' aria-live='polite'" in body
    assert "id='wf-checks'" in body and "id='wf-panel'" in body and "{side}" not in body, "the side panel is on the page"
    assert "<details class='wf-items'><summary>Items in each status" in body, "the items table folds away to leave room"
    assert "…" not in body.split("<svg class=\"wf wf-edit\"")[1].split("</svg>")[0], "no label is cut short"
    status, text = get(shell, "/workflows/" + NCR, **{"as": "dave"})
    body = main_of(text)
    assert status == 200 and "You are reading the workflow in force" in body and 'class="wf-handle"' not in body
    assert "<form method='post' action='/workflow-op'" not in body and not wf_data(body)["editable"]
    status, text = get(shell, "/workflows/nonexistent", **{"as": "carol"})
    assert status == 404


def test_the_text_view_shows_every_rule_as_a_sentence_and_every_edit_as_a_form(shell):
    status, text = get(shell, "/workflows/" + NCR, **{"as": "carol", "view": "text"})
    body = main_of(text)
    assert status == 200 and "id='add-status'" in body and "id='add-transition'" in body and f"id='s-{AWAIT}'" in body
    assert ("From “Awaiting disposition the nonconformance” to “Closed” (“Disposition the nonconformance”): the quality manager decides; "
            "after the move: apply the disposition, open corrective action, render the record.") in body
    assert "As the SOP will say it" in body and "Statuses: Open (to do), Awaiting disposition the nonconformance (in progress, due within 5 days)" in body
    for form in ("edit-status", "allow-all", "connect", "move", "delete-status", "edit-transition", "delete-transition", "add-status", "add-transition"):
        assert f"data-wf-form='{form}'" in body, form
    status, text = get(shell, "/workflows/" + NCR, **{"as": "dave", "view": "text"})
    body = main_of(text)
    assert status == 200 and "data-wf-op" not in body and "Disposition the nonconformance" in body


def test_mermaid_is_gone_and_the_record_page_draws_the_workflow_on_the_server(shell):
    hub = shell["hub"]
    rec = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "Burr", "description": "found at inspection", "source": "inspection"}, by="carol")
    hub.kernel.transition(rec["locator"], "rejected", by="carol", reason="test")
    status, text = get(shell, "/record/" + quote(rec["locator"], safe=":"), **{"as": "carol"})
    assert status == 200
    assert "mermaid" not in text.lower() and "cdnjs" not in text and "<script src=" not in text, "no CDN asset"
    body = main_of(text)
    assert '<svg class="wf wf-view"' in body and 'role="img"' in body and "The item is in Rejected." in body
    cur = re.search(r'<g class="([^"]*)" id="wfr-s-rejected"', body).group(1)
    assert "wf-cur" in cur, "the current status is marked"
    assert re.search(r'<g class="[^"]*wf-seen[^"]*" id="wfr-s-open"', body), "the path taken: it was open"
    assert 'id="wfr-s-closed"' in body and "Current" in body
    status, text = get(shell, "/settings/workflows", **{"as": "bob"})
    assert status == 200 and '<svg class="wf wf-view"' in text and "/workflows/nonconformance" in text


def test_the_panel_endpoint_gives_each_selection_and_the_dialogs(shell):
    status, text = get(shell, f"/workflow-panel/{NCR}", **{"as": "carol", "sel": f"s:{AWAIT}"})
    html = json.loads(text)["html"]
    assert status == 200 and "data-wf-form='edit-status'" in html and "Connect from here to" in html and "Ways out" in html
    status, text = get(shell, f"/workflow-panel/{NCR}", **{"as": "carol", "sel": "t:cancel"})
    assert "From any status except done ones to “Cancelled” (“Cancel”)" in json.loads(text)["html"]
    status, text = get(shell, f"/workflow-panel/{NCR}", **{"as": "carol", "sel": "add-transition"})
    html = json.loads(text)["html"]
    assert "From any status" in html and "name='from'" in html and "Who decides" in html
    status, text = get(shell, f"/workflow-panel/{NCR}", **{"as": "dave", "sel": "add-status"})
    assert status == 403
    status, text = get(shell, f"/workflow-panel/{NCR}", **{"as": "dave", "sel": "t:cancel"})
    assert status == 200 and "data-wf-op" not in json.loads(text)["html"]


# ---------------------------------------------------------------- edits without JavaScript
def test_every_edit_works_as_a_plain_form(shell):
    hub = shell["hub"]
    status, url, body = op(shell, "carol", {"op": "add_status", "name": "Awaiting verification", "category": "in_progress", "sla_days": "3", "back": "text"})
    assert status == 200 and "view=text" in url and "Added+status" in url or "Added%20status" in url, url
    g = doc_of(shell)["graph"]
    assert wm.status_map(g)["awaiting-verification"]["sla_days"] == 3
    op(shell, "carol", {"op": "edit_status", "id": "awaiting-verification", "name": "Verification", "category": "in_progress", "sla_days": ""})
    assert wm.status_map(doc_of(shell)["graph"])["awaiting-verification"] == {"id": "awaiting-verification", "name": "Verification", "category": "in_progress"}
    op(shell, "carol", {"op": "connect", "id": "awaiting-verification", "to": "closed", "name": "Verified"})
    op(shell, "carol", {"op": "edit_transition", "id": "disposition-the-nonconformance", "name": "Disposition", "from": [AWAIT], "to": "awaiting-verification",
                        "gate_kind": "person", "role": "quality-manager", "not_author": "1", "needs": "disposition, containment",
                        "condition": "no_open_ncr", "actions": "apply-the-disposition\nnotify:quality-manager"})
    t = wm.transition_map(doc_of(shell)["graph"])["disposition-the-nonconformance"]
    assert t["name"] == "Disposition" and t["to"] == "awaiting-verification" and t["gate"] == {"kind": "person", "role": "quality-manager", "not": ["author"]}
    assert t["validators"] == [{"fields_set": ["disposition", "containment"]}, {"condition": "no_open_ncr"}]
    assert t["actions"] == ["apply-the-disposition", {"notify": "role:quality-manager"}] and t["fields"] == ["disposition", "containment"]
    op(shell, "carol", {"op": "add_transition", "name": "Put on hold", "any": "1", "except_default": "1", "to": "awaiting-verification",
                        "gate_kind": "auto", "when": "risk_low"})
    hold = wm.transition_map(doc_of(shell)["graph"])["put-on-hold"]
    assert hold["type"] == "global" and "except" not in hold and hold["gate"] == {"kind": "auto", "when": "risk_low"}
    op(shell, "carol", {"op": "allow_all", "id": "rejected", "on": "1"})
    to_rejected = lambda: [t for t in doc_of(shell)["graph"]["transitions"] if t["to"] == "rejected" and t["type"] == "global"]
    assert [t["name"] for t in to_rejected()] == ["Rejected"]
    op(shell, "carol", {"op": "allow_all", "id": "rejected", "on": ""})
    assert not to_rejected()
    op(shell, "carol", {"op": "move", "id": "closed", "x": "805", "y": "403"})
    assert doc_of(shell)["layout"]["closed"] == [808, 400]
    op(shell, "carol", {"op": "delete_transition", "id": "put-on-hold"})
    op(shell, "carol", {"op": "tidy"})
    assert doc_of(shell)["layout"] == wm.tidy(doc_of(shell)["graph"])
    status, url, body = op(shell, "carol", {"op": "undo"})
    assert status == 200 and doc_of(shell)["layout"]["closed"] == [808, 400], "undo takes the tidy back"
    op(shell, "carol", {"op": "redo"})
    assert doc_of(shell)["layout"] == wm.tidy(doc_of(shell)["graph"])
    # Refused edits re-render the Text view with the reason.
    status, url, body = op(shell, "carol", {"op": "edit_status", "id": "open", "name": "Closed", "category": "todo", "back": "text"})
    assert status == 400 and "Another status is called “Closed”." in body and "id='add-status'" in body
    draft = wfs.draft_of(hub, "carol", NCR)
    assert len(draft["log"]) == draft["cursor"] == 10 and draft["log"][0]["said"] == "Added status “Awaiting verification”"


def test_deleting_a_status_with_items_asks_where_they_go(shell):
    hub = shell["hub"]
    for i in range(3):
        r = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": f"N{i}", "description": "x", "source": "inspection"}, by="carol")
        hub.kernel.transition(r["locator"], "rejected", by="carol", reason="test")
    status, text = get(shell, "/workflows/" + NCR, **{"as": "carol", "view": "text"})
    body = main_of(text)
    assert "3 items are in Rejected.</b> Move them to:" in body
    status, text = get(shell, f"/workflow-panel/{NCR}", **{"as": "carol", "sel": "delete:rejected"})
    html = json.loads(text)["html"]
    assert "Delete Rejected?" in html and "<option value='closed' selected>" in html, "prefilled with a done status of the same kind, or the first"
    status, url, out = jop(shell, "carol", {"op": "delete_status", "id": "rejected"})
    assert out["ok"] and any("say where they go" in x for x in re.findall(r"<li class='err'>(.*?)</li>", out["checks_html"])), out["checks_html"]
    op(shell, "carol", {"op": "undo"})
    op(shell, "carol", {"op": "delete_status", "id": "rejected", "move_to": "cancelled"})
    d = doc_of(shell)
    assert d["state_map"] == {"rejected": "cancelled"}
    an = wfs.analysis(hub, NCR, d, wfs.draft_of(hub, "carol", NCR)["base"])
    assert any(c["code"] == "items-move" and "they move to “Cancelled”" in c["text"] for c in an["checks"])
    parent = next(i for i in an["delta"] if i["key"] == "status:rejected")
    assert "3 items in it move to “Cancelled”" in parent["text"] and parent["children"]


# ---------------------------------------------------------------- the editor's script: JSON
def test_json_operations_answer_with_the_drawing_checks_and_changes(shell):
    status, url, out = jop(shell, "carol", {"op": "add_transition", "from": ["open"], "to": "closed"})
    assert status == 200 and out["ok"] and out["said"] == "Added transition “Closed” to “Closed”"
    assert out["sel"] == "t:closed-2" and "data-wf-form='edit-transition'" in out["panel"] and out["reveal"]
    assert '<g class="wf-edges">' in out["svg"] and 'data-t="closed-2"' in out["svg"] and len(out["vb"]) == 4
    assert "auto-overlap" not in out["checks_html"]
    assert "Transition “Closed” added" in out["delta_html"] and out["can_undo"] and not out["can_redo"]
    # The new way to Closed avoids the quality manager's disposition: a warning with the path.
    assert "Open → Closed → Closed avoids “Disposition the nonconformance”" in out["checks_html"]
    status, url, out = jop(shell, "carol", {"op": "move", "id": "open", "at": [33, 47]}, coalesce="1")
    status, url, out = jop(shell, "carol", {"op": "move", "id": "open", "at": [41, 47]}, coalesce="1")
    log = wfs.draft_of(shell["hub"], "carol", NCR)["log"]
    assert [e["op"] for e in log] == ["add_transition", "move"] and log[-1]["at"] == [40, 48], "nudges coalesce into one step"
    status, url, out = jop(shell, "carol", {"op": "rename_status", "id": "nope", "name": "X"})
    assert status == 400 and not out["ok"] and out["error"] == "There is no status 'nope'."
    status, url, out = op(shell, "carol", {"op": "json", "op_json": "{not json"}, as_json=True)
    assert status == 400 and "does not parse" in out["error"]


# ---------------------------------------------------------------- drafts
def test_one_draft_per_person_and_process_saved_as_it_changes(shell):
    hub = shell["hub"]
    assert wfs.draft_of(hub, "carol", NCR) is None
    jop(shell, "carol", {"op": "rename_status", "id": "closed", "name": "Closed out"})
    jop(shell, "bob", {"op": "rename_status", "id": "closed", "name": "Done"})
    assert wm.status_map(doc_of(shell, "carol")["graph"])["closed"]["name"] == "Closed out"
    assert wm.status_map(doc_of(shell, "bob")["graph"])["closed"]["name"] == "Done"
    assert wfs.draft_of(hub, "carol", "capa") is None
    status, text = get(shell, "/workflows/" + NCR, **{"as": "carol"})
    body = main_of(text)
    assert "Your draft, saved" in body and "Closed out" in body and "Carry on a change order" in body
    status, text = get(shell, "/workflows", **{"as": "carol"})
    assert "draft with 1 change" in main_of(text)
    status, url, body = post(shell, "/workflow-discard", {"as": "carol", "process": NCR})
    assert status == 200 and wfs.draft_of(hub, "carol", NCR) is None and wfs.draft_of(hub, "bob", NCR)
    status, text = get(shell, "/workflows/" + NCR, **{"as": "dave"})
    assert "Closed out" not in main_of(text), "a draft is not the workflow in force"


# ---------------------------------------------------------------- permissions
def test_only_document_controllers_and_policy_owners_edit(shell):
    status, url, out = jop(shell, "dave", {"op": "rename_status", "id": "closed", "name": "X"})
    assert status == 403 and "Only document controllers and policy owners change workflows" in out["error"]
    status, url, body = op(shell, "alice", {"op": "tidy"})
    assert status == 403
    status, url, body = post(shell, "/workflow-op", {"as": "ap-reviewer", "process": NCR, "op": "tidy"})
    assert status == 403, "nobody acts in a cog's name"
    status, text = get(shell, "/workflows/" + NCR, **{"as": "stranger"})
    assert status == 403
    assert wfs.draft_of(shell["hub"], "dave", NCR) is None


# ---------------------------------------------------------------- change orders
def new_co(shell, who: str = "alice", title: str = "NCR verification step") -> dict:
    status, url, body = post(shell, "/co-new", {"as": who, "title": title, "description": "add a verification step"})
    assert status == 200, body[:300]
    return next(r for r in shell["hub"].kernel.query(type_="change-order") if r["fields"]["title"] == title)


def test_carry_a_draft_on_a_change_order_and_see_the_visual_diff_there(shell):
    hub = shell["hub"]
    co = new_co(shell)
    r = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "N", "description": "x", "source": "inspection"}, by="carol")
    hub.kernel.transition(r["locator"], "rejected", by="carol", reason="test")
    jop(shell, "carol", {"op": "add_status", "name": "Awaiting verification", "id": "awaiting-verification", "at": [400, 400]})
    jop(shell, "carol", {"op": "reroute_transition", "id": "disposition-the-nonconformance", "to": "awaiting-verification"})
    jop(shell, "carol", {"op": "add_transition", "id": "verified", "name": "Verified", "from": ["awaiting-verification"], "to": "closed",
                         "gate": {"kind": "person", "role": "quality-manager"}})
    jop(shell, "carol", {"op": "delete_status", "id": "rejected", "move_to": "cancelled"})
    status, url, body = post(shell, "/workflow-carry", {"as": "dave", "process": NCR, "co": co["locator"]})
    assert status == 403
    status, url, body = post(shell, "/workflow-carry", {"as": "carol", "process": NCR, "co": co["locator"]})
    assert status == 200 and co["number"] in body and "is carried on" in url.replace("+", " ").replace("%20", " "), url
    co = hub.kernel.get(co["locator"])
    c = co["fields"]["workflow_changes"][NCR]
    assert c["by"] == "carol" and c["base"]["graph_digest"] and not c["layout_only"]
    assert {i["key"] for i in c["delta"]} >= {"status:awaiting-verification", "transition:verified", "status:rejected", "transition:disposition-the-nonconformance"}
    assert all(i["origin"] == {**i["origin"], "kind": "direct", "by": "carol"} for i in c["delta"])
    # What changes how items move is one direct change of the process on the change order (workstream 1's delta): the
    # process rule in the graph form, whose prose compiles back to the same graph; the state map is the change order's.
    from hubproto import changecontrol

    direct = [i for i in changecontrol._items(co) if i["id"] == c["direct_id"]]
    assert direct and direct[0]["op"] == "change" and direct[0]["origin"]["kind"] == "direct" and direct[0]["origin"]["by"] == "carol"
    rule = direct[0]["rule"]
    assert rule["kind"] == "process" and rule["name"] == NCR and not rule["steps"] and "awaiting-verification" in {s["id"] for s in rule["statuses"]}
    assert "failed" not in {s["id"] for s in rule["statuses"]} and "retry" not in {t["id"] for t in rule["transitions"]}, "the compile adds the hub's own"
    assert "disposition-applier" in wm.transition_map(rule)["disposition-the-nonconformance"]["actions"], "actions name cogs, as the prose does"
    assert co["fields"]["state_map"] == {NCR: {"rejected": "cancelled"}}
    items = wfs.delta_items(hub, co)
    assert items and all(not i["stale"] for i in items) and {i["process"] for i in items} == {NCR}
    page = main_of(get(shell, "/record/" + quote(co["locator"], safe=":"), **{"as": "alice"})[1])
    assert "id='co-workflows'" in page and "data-wf-diff" in page and "data-wf-view='before'" in page
    assert "<span class='wf-sign add'" in page and "Status “Awaiting verification” added" in page
    assert re.search(r'class="[^"]*m-del[^"]*" id="cowf-nonconformance-c-s-rejected"', page), "the removed status, dashed and struck, at its old place"
    assert "Items in removed statuses" in page and "Consequences" in page and "the waiting decision" not in page.split("Items in removed statuses")[1][:20]
    assert wfs.carried_reasons(hub, co) == []
    # Carried again after another edit: it replaces the first (one direct change for the process).
    jop(shell, "carol", {"op": "rename_status", "id": "awaiting-verification", "name": "Verification"})
    post(shell, "/workflow-carry", {"as": "carol", "process": NCR, "co": co["locator"]})
    co = hub.kernel.get(co["locator"])
    mine = [i for i in changecontrol._items(co) if (i.get("origin") or {}).get("process") == NCR]
    assert len(mine) == 1 and mine[0]["id"] == co["fields"]["workflow_changes"][NCR]["direct_id"]
    # Its requester (or a document controller) takes it off.
    status, url, body = post(shell, "/workflow-uncarry", {"as": "dave", "co": co["locator"], "process": NCR})
    assert status == 403
    status, url, body = post(shell, "/workflow-uncarry", {"as": "alice", "co": co["locator"], "process": NCR})
    co = hub.kernel.get(co["locator"])
    assert status == 200 and not co["fields"]["workflow_changes"] and not co["fields"]["state_map"]
    assert not [i for i in changecontrol._items(co) if (i.get("origin") or {}).get("process") == NCR], "its direct change goes too"


def test_a_draft_with_errors_is_not_carried(shell):
    co = new_co(shell)
    jop(shell, "carol", {"op": "add_status", "name": "Limbo", "id": "limbo", "at": [0, 0]})
    status, url, body = post(shell, "/workflow-carry", {"as": "carol", "process": NCR, "co": co["locator"]})
    assert status == 400 and "The draft has problems: no transition leads to “Limbo”" in body
    status, url, body = post(shell, "/workflow-carry", {"as": "carol", "process": NCR, "co": "new", "title": "Fresh"})
    assert status == 400


def test_a_layout_only_change_comes_into_force_with_an_approved_change_order(shell):
    from hubproto.closure import ChangeOrderRequired

    hub = shell["hub"]
    co = new_co(shell)
    jop(shell, "carol", {"op": "move", "id": "closed", "at": [1200, 600]})
    post(shell, "/workflow-carry", {"as": "carol", "process": NCR, "co": co["locator"]})
    co = hub.kernel.get(co["locator"])
    c = co["fields"]["workflow_changes"][NCR]
    assert c["layout_only"] and [i["text"] for i in c["delta"]] == ["Layout changed: no effect on how items move"]
    assert wfs.carried_reasons(hub, co) == [], "a layout needs no SOP prose"
    with pytest.raises(ChangeOrderRequired, match="only once it is approved"):
        wfs.activate(hub, co, "d1")
    assert wfs.layout_entry(hub, NCR) is None
    hub.kernel.transition(co["locator"], "approved", by="carol", reason="test")
    co = hub.kernel.get(co["locator"])
    assert wfs.activate(hub, co, "d1") == [f"{NCR} layout v1"]
    assert wfs.activate(hub, co, "d1") == [f"{NCR} layout v1"], "safe to repeat"
    entry = wfs.layout_entry(hub, NCR)
    assert entry["released_with"] == co["number"] and entry["layout"]["closed"] == [1200, 600]
    g = wfs.graph_in_force(hub, NCR)
    assert wfs.layout_in_force(hub, NCR, g)["closed"] == [1200, 600]
    status, text = get(shell, "/workflows/" + NCR, **{"as": "dave"})
    assert co["number"] in main_of(text), "the history names the change order"
    # A draft started before is now stale: carrying it again is refused.
    jop(shell, "bob", {"op": "tidy"})
    wfs.store(hub).set_layout(NCR, {**entry, "version": 2, "released_with": "CO-OTHER"})
    with pytest.raises(ValueError, match="changed since your draft was started"):
        wfs.carry(hub, new_co(shell, "bob", "Other")["locator"], "bob", NCR)
    assert wfs.delta_items(hub, co)[0]["stale"], "what the first change order carried is stale against the new layout"


def test_the_editor_offers_the_committees_of_the_release(shell):
    status, text = get(shell, f"/workflow-panel/{NCR}", **{"as": "carol", "sel": "t:disposition-the-nonconformance"})
    html = json.loads(text)["html"]
    assert "value='committee'" in html and "a committee (consensus)" in html and "<option value='compile-frame'>compile-frame committee (senior-cogs group)</option>" in html
    op(shell, "carol", {"op": "edit_transition", "id": "disposition-the-nonconformance", "name": "Disposition the nonconformance", "from": [AWAIT],
                        "to": "closed", "gate_kind": "committee", "committee": "compile-frame",
                        "actions": "apply-the-disposition\nopen-corrective-action\nrender-the-record"})
    g = doc_of(shell)["graph"]
    assert wm.transition_map(g)["disposition-the-nonconformance"]["gate"] == {"kind": "committee", "committee": "compile-frame"}
    status, text = get(shell, "/workflows/" + NCR, **{"as": "carol"})
    body = main_of(text)
    assert "compile-frame committee" in body and 'href="#wfc-i-committee"' in body, "the badge names the committee, with the group icon"
    assert "an objection has no way back" in body, "the committee needs a way back: the Reject goes to a done status"

