"""The form editor: the Forms list, the drag-and-drop editor for document controllers and policy owners (read-only for
everyone else), drafts checked as they change (errors, warnings, the changes in words), a preview from the draft,
carrying the draft on a change order, and the layout coming into force when that change order is released."""

import copy
import json
import shutil
from urllib.parse import quote

import pytest

from conftest import ROOT, release_all
from hubproto import formlayout
from hubproto.app import Hub
from hubproto.shell import free_port, serve_in_thread
from test_shell_views import get, main_of, post


@pytest.fixture
def shell(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    port = free_port()
    server = serve_in_thread(hub, "127.0.0.1", port)
    yield {"hub": hub, "base": f"http://127.0.0.1:{port}"}
    server.shutdown()
    server.server_close()


def traceability(hub) -> dict:
    """The NCR form with a Traceability section holding the lot and a new custom field, and containment required
    when the source is a complaint."""
    lay = copy.deepcopy(formlayout.active_layout(hub, "ncr"))
    details = lay["sections"][0]
    lot = next(r for r in details["fields"] if r["field"] == "lot")
    details["fields"].remove(lot)
    lay["custom_fields"]["x_batch_code"] = {"type": "text", "label": "Batch code"}
    lay["sections"].append({"id": "traceability", "title": "Traceability", "fields": [lot, {"field": "x_batch_code", "label": "Batch code"}]})
    cont = next(r for r in details["fields"] if r["field"] == "containment")
    cont["required_when"] = {"field": "source", "op": "==", "value": "complaint"}
    return lay


def save(shell, who: str, lay: dict, path: str = "/forms-save"):
    status, url, body = post(shell, path, {"as": who, "type": "ncr", "layout": json.dumps(lay)})
    return status, json.loads(body)


def test_the_forms_page_lists_every_record_type(shell):
    status, text = get(shell, "/forms", **{"as": "carol"})
    body = main_of(text)
    assert status == 200 and "/forms/ncr" in body and "/forms/change-order" in body and "the hub's own" in body
    status, text = get(shell, "/settings", **{"as": "carol"})
    assert "/forms" in text, "Forms is in the Settings app"


def test_the_editor_is_for_document_controllers_and_read_only_for_staff(shell):
    status, text = get(shell, "/forms/ncr", **{"as": "carol"})
    body = main_of(text)
    assert status == 200 and "id='fe'" in body and "Save draft" in body and "New custom field" in body and "fe-data" in body
    data = json.loads(body.split("<script type='application/json' id='fe-data'>")[1].split("</script>")[0])
    assert data["editable"] and data["type"] == "ncr" and "lot" in data["fields"] and data["layout"]["sections"][0]["title"] == "Details"
    status, text = get(shell, "/forms/ncr", **{"as": "dave"})
    body = main_of(text)
    assert status == 200 and "Save draft" not in body and "You are reading the form in force" in body and "New custom field" not in body
    status, body = save(shell, "dave", formlayout.active_layout(shell["hub"], "ncr"))
    assert status == 403 and not body["ok"]


def test_a_draft_is_checked_and_saved_with_its_changes_in_words(shell):
    hub = shell["hub"]
    status, body = save(shell, "carol", traceability(hub))
    assert status == 200 and body["ok"] and body["errors"] == [], body
    changes = body["changes"]
    assert "new section “Traceability”" in changes
    assert "Lot moved from “Details” to “Traceability”" in changes
    assert any(c.startswith("Containment required when source is complaint") for c in changes), changes
    assert "new custom field Batch code (x_batch_code, text)" in changes
    assert body["words"]["containment"]["required_when"] == "source is complaint"
    assert hub.forms.drafts("carol")["ncr"]["sections"][-1]["id"] == "traceability"
    status, text = get(shell, "/forms", **{"as": "carol"})
    assert "draft with" in main_of(text)


def test_bad_custom_fields_and_retypes_are_refused(shell):
    hub = shell["hub"]
    lay = traceability(hub)
    lay["custom_fields"]["batch"] = {"type": "text", "label": "Batch"}
    status, body = save(shell, "carol", lay, "/forms-check")
    assert any("starts with x_" in e for e in body["errors"]), body["errors"]
    released = traceability(hub)
    hub.forms.set_active("ncr", {"version": 1, "layout": released, "released_with": "CO-TEST"})
    retyped = copy.deepcopy(released)
    retyped["custom_fields"]["x_batch_code"]["type"] = "number"
    status, body = save(shell, "carol", retyped, "/forms-check")
    assert any("type never changes" in e for e in body["errors"]), body["errors"]
    gone = copy.deepcopy(released)
    del gone["custom_fields"]["x_batch_code"]
    status, body = save(shell, "carol", gone, "/forms-check")
    assert any("retire it instead" in e for e in body["errors"]), body["errors"]


def test_the_preview_shows_the_draft(shell):
    hub = shell["hub"]
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "Burr on bracket", "description": "found at inspection", "lot": "L-42",
                                                               "source": "inspection"}, by="carol")
    save(shell, "carol", traceability(hub))
    status, text = get(shell, "/forms-preview/ncr", **{"as": "carol", "what": "sheet", "record": ncr["locator"]})
    html = json.loads(text)["html"]
    assert status == 200 and "Traceability" in html and "L-42" in html
    status, text = get(shell, "/forms-preview/ncr", **{"as": "carol", "what": "start"})
    assert status == 200 and 'name="title"' in json.loads(text)["html"]
    status, text = get(shell, "/record/" + quote(ncr["locator"], safe=":"), **{"as": "carol"})
    assert "Traceability" not in main_of(text), "a draft is not the form in force"


def test_a_carried_form_comes_into_force_when_its_change_order_is_released(shell):
    hub = shell["hub"]
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "Scratch", "description": "cosmetic", "lot": "L-7", "source": "inspection"}, by="carol")
    # bob, a policy owner, prepares the change order and the form: who made a change does not approve it (carol does).
    status, url, body = post(shell, "/co-new", {"as": "bob", "title": "NCR traceability", "description": "lot and batch on their own section"})
    assert status == 200, body[:300]
    co = next(r for r in hub.kernel.query(type_="change-order") if r["fields"]["title"] == "NCR traceability")
    save(shell, "bob", traceability(hub))
    status, url, body = post(shell, "/forms-carry", {"as": "bob", "type": "ncr", "co": co["locator"]})
    assert status == 200 and co["number"] in url, body[:300]
    co = hub.kernel.get(co["locator"])
    assert "ncr" in co["fields"]["form_changes"] and "new section “Traceability”" in co["fields"]["form_changes"]["ncr"]["changes"]
    page = main_of(get(shell, "/record/" + quote(co["locator"], safe=":"), **{"as": "alice"})[1])
    assert "Forms" in page and "new section “Traceability”" in page and "/forms/ncr" in page
    status, text = get(shell, "/change-orders", **{"as": "alice"})
    assert "ncr form" in main_of(text)
    # Reviewed, sent for approval by its requester, approved by the quality manager; the release automation applies it.
    status, url, body = post(shell, "/co-send", {"as": "bob", "co": co["locator"]})
    assert status == 400 and "request review" in body, body[:300]
    status, url, body = post(shell, "/co-review", {"as": "bob", "co": co["locator"]})
    assert status == 200, body[:300]
    status, url, body = post(shell, "/co-send", {"as": "bob", "co": co["locator"]})
    assert status == 200, body[:300]
    run = hub.runner.run_for_record(co["locator"])
    state = hub.runner.load(run["id"])
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["carol"], state.get("pending")
    hub.runner.decide(run["id"], "carol", "approve", meaning="approved", payload_digest=hub.runner.seen_digest(state))
    entry = hub.forms.active()["ncr"]
    assert entry["released_with"] == co["number"] and entry["version"] == 1
    assert hub.kernel.get(co["locator"])["state"] == "released"
    hub.kernel.set_fields(ncr["locator"], {"x_batch_code": "B-19"}, by="carol")
    page = main_of(get(shell, "/record/" + quote(ncr["locator"], safe=":"), **{"as": "carol"})[1])
    assert "Traceability" in page and "Batch code" in page and "B-19" in page and "L-7" in page
    status, text = get(shell, "/forms/ncr", **{"as": "dave"})
    body = main_of(text)
    assert "v1 in force" in body and co["number"] in body, "the history names the change order that released it"


def test_only_its_requester_or_a_document_controller_carries_and_removes_forms(shell):
    hub = shell["hub"]
    status, url, body = post(shell, "/co-new", {"as": "alice", "title": "Someone's", "description": "x"})
    co = next(r for r in hub.kernel.query(type_="change-order") if r["fields"]["title"] == "Someone's")
    status, url, body = post(shell, "/forms-carry", {"as": "dave", "type": "ncr", "co": co["locator"]})
    assert status == 403
    save(shell, "carol", traceability(hub))
    post(shell, "/forms-carry", {"as": "carol", "type": "ncr", "co": co["locator"]})
    assert "ncr" in hub.kernel.get(co["locator"])["fields"]["form_changes"]
    status, url, body = post(shell, "/forms-uncarry", {"as": "dave", "co": co["locator"], "type": "ncr"})
    assert status == 403
    status, url, body = post(shell, "/forms-uncarry", {"as": "alice", "co": co["locator"], "type": "ncr"})
    assert status == 200 and not hub.kernel.get(co["locator"])["fields"]["form_changes"]


def test_a_form_with_released_custom_fields_can_be_edited_again(tmp_path):
    import shutil

    from conftest import ROOT, release_all
    from hubproto import formlayout
    from hubproto.app import Hub

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    lay = formlayout.active_layout(hub, "ncr")
    lay["custom_fields"] = {"x_batch": {"type": "text", "label": "Batch code"}}
    lay["sections"][0]["fields"].append({"field": "x_batch", "visible_when": {"field": "severity", "op": "==", "value": "high"}})
    errors, _ = formlayout.validate(hub, "ncr", lay)
    assert errors == [], errors
    hub.forms.set_active("ncr", {"version": 1, "layout": lay, "released_with": "CO-TEST", "release": "", "by": "carol", "at": ""})
    formlayout.apply_custom_fields(hub)
    again = formlayout.active_layout(hub, "ncr")
    errors, _ = formlayout.validate(hub, "ncr", again)
    assert errors == [], "a released custom field is not a clash with the type the second time"
