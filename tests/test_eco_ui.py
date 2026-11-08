"""The change-order pages: prepare a change order, carry SOP and policy revisions on it, see the staged Frame and what
blocks it, send it for approval; SOP and policy pages point to change orders, and the Frame pages say the Frame is
derived from the documents."""

import shutil
from urllib.parse import quote

import pytest

from conftest import ROOT, release_all
from hubproto import eco, sopdocs
from hubproto.app import Hub
from hubproto.shell import free_port, serve_in_thread
from test_shell_views import get, main_of, post

OLD = "The ap-reviewer cog may approve purchase-to-pay bills under 2,000 USD when the three-way match is within tolerance and the supplier is approved."
NEW = OLD.replace("2,000", "1,500")


@pytest.fixture
def shell(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    sopdocs.convert(hub, by="carol")
    policy = sopdocs.in_force(hub.kernel, "finance-policy")
    hub.kernel.set_fields(policy["locator"], {"owner": "alice"}, by="setup")  # alice, an author, owns the policy here
    port = free_port()
    server = serve_in_thread(hub, "127.0.0.1", port)
    yield {"hub": hub, "base": f"http://127.0.0.1:{port}"}
    server.shutdown()
    server.server_close()


def page(shell, rec, who):
    path = ("/document/" if rec["type"] == "document" else "/record/") + quote(rec["locator"], safe=":")
    status, text = get(shell, path, **{"as": who})
    assert status == 200, text[:400]
    return main_of(text)


def new_co(shell, who="alice", title="Lower the AP cog limit"):
    hub = shell["hub"]
    status, url, body = post(shell, "/co-new", {"as": who, "title": title, "description": "fewer cog approvals"})
    assert status == 200, body[:400]
    return next(r for r in hub.kernel.query(type_="change-order") if r["fields"]["title"] == title)


def write(hub, draft, old, new, by):
    body = sopdocs.frame_text(hub, draft).split("\n---\n", 1)[1]
    hub.dms.add_version(draft["locator"], sopdocs.to_docx(body.replace(old, new)), by=by, filename=f"{draft['fields']['sop_name']}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def test_change_orders_sit_in_the_quality_app_and_are_prepared_first(shell):
    status, text = get(shell, "/app/quality", **{"as": "alice"})
    assert status == 200 and "/change-orders" in text and "Change orders" in text
    status, text = get(shell, "/change-orders", **{"as": "alice"})
    body = main_of(text)
    assert status == 200 and "New change order" in body and "No change orders yet." in body
    co = new_co(shell)
    assert co["state"] == "open" and co["fields"]["requested_by"] == "alice" and shell["hub"].runner.run_for_record(co["locator"]) is None
    assert co["number"] in main_of(get(shell, "/change-orders", **{"as": "alice"})[1])
    status, text = get(shell, "/", **{"as": "alice"})
    assert "/change-orders" in text and "start-change-order" not in text, "a change order is prepared, not started from a form"


def test_carrying_an_sop_stages_the_frame_and_shows_what_blocks_approval(shell):
    hub = shell["hub"]
    co = new_co(shell)
    html = page(shell, co, "alice")
    assert "Carries" in html and "Add an SOP or policy" in html and "finance-policy" in html
    status, url, body = post(shell, "/co-add-sop", {"as": "alice", "co": co["locator"], "sop": "finance-policy"})
    assert status == 200, body[:400]
    [c] = eco.carried(hub, hub.kernel.get(co["locator"]))
    draft = c["draft"]
    html = page(shell, co, "alice")
    assert draft["number"] in html and "being written" in html and "Staged Frame" in html
    write(hub, draft, OLD, NEW, "alice")
    html = page(shell, co, "alice")
    assert "1,500" in html and "was:" in html and "2,000" in html, "the changed rule as a sentence, with what it was"
    assert "must be approved through document control first" in html and "Send for approval" not in html and "Preview from the drafts" in html
    assert "compare with the version in force" in html
    status, url, body = post(shell, "/co-remove-sop", {"as": "alice", "co": co["locator"], "sop": "finance-policy"})
    assert status == 200 and eco.carried(hub, hub.kernel.get(co["locator"])) == []
    assert hub.kernel.get(draft["locator"])["state"] == "withdrawn"


def test_someone_else_cannot_change_a_change_order(shell):
    co = new_co(shell)
    status, url, body = post(shell, "/co-add-sop", {"as": "dave", "co": co["locator"], "sop": "finance-policy"})
    assert status == 403
    assert "Add an SOP or policy" not in page(shell, co, "dave")


def test_an_sop_page_offers_a_revision_on_a_change_order(shell):
    hub = shell["hub"]
    policy = sopdocs.in_force(hub.kernel, "finance-policy")
    html = page(shell, policy, "alice")
    assert "Compiled into the Frame" in html and "Revise on a change order" in html
    status, url, body = post(shell, "/sop-revise", {"as": "alice", "doc": policy["locator"], "co": ""})
    assert status == 200, body[:400]
    co = next(r for r in hub.kernel.query(type_="change-order") if r["fields"]["title"].startswith("Revise"))
    [c] = eco.carried(hub, co)
    html = page(shell, c["draft"], "alice")
    assert f"carried by change order" in html and co["number"] in html and "comes into force only when that change order is released" in html
    assert "only alice, its author, a document controller" in page(shell, policy, "dave") or "Revise on a change order" not in page(shell, policy, "dave")


def test_send_for_approval_once_every_revision_is_approved(shell):
    hub = shell["hub"]
    co = new_co(shell)
    post(shell, "/co-add-sop", {"as": "alice", "co": co["locator"], "sop": "finance-policy"})
    [c] = eco.carried(hub, hub.kernel.get(co["locator"]))
    write(hub, c["draft"], OLD, NEW, "alice")
    run = hub.runner.submit("document-control", {"record": {"change_type": "minor"}}, owner="alice", record=c["draft"]["locator"])
    state = hub.runner.load(run)
    who = state["pending"]["assignees"][0]
    hub.runner.decide(run, who, "approve", meaning="ok", payload_digest=hub.runner.seen_digest(state))
    html = page(shell, co, "alice")
    assert "approved, held for this change order" in html and "Send for approval" in html
    status, url, body = post(shell, "/co-send", {"as": "alice", "co": co["locator"]})
    assert status == 200, body[:400]
    cos = hub.runner.run_for_record(co["locator"])
    assert cos["status"] == "waiting" and cos["pending"]["assignees"] == ["carol"]
    assert "Sent for approval" in page(shell, co, "alice")
    status, text = get(shell, "/proposals", **{"as": "carol"})
    assert f"staged by" in text and co["number"] in text


def test_change_orders_are_for_members_who_may_read_changes(shell):
    co = new_co(shell)
    status, _ = get(shell, "/change-orders", **{"as": "mallory"})
    assert status in (400, 403)
    status, _ = get(shell, "/record/" + quote(co["locator"], safe=":"), **{"as": "mallory"})
    assert status in (400, 403, 404)


def test_frame_pages_say_the_frame_is_derived_from_its_document(shell):
    hub = shell["hub"]
    doc = sopdocs.in_force(hub.kernel, "document-control-sop")
    status, text = get(shell, "/frame/document-control-sop", **{"as": "carol"})
    body = main_of(text)
    assert status == 200 and "This Frame is compiled from" in body and doc["number"] in body and "Open as a document" not in body


def test_a_policy_owner_converts_the_sops_from_the_policies_page(tmp_path):
    """No command line: the Policies page checks that every Frame reads back exactly and that change orders exist,
    then a policy owner converts; the other policy owner is told; the rules are then compiled from the documents."""
    import shutil

    from conftest import ROOT, release_all
    from hubproto.app import Hub
    from hubproto.shell import free_port, serve_in_thread

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    port = free_port()
    server = serve_in_thread(hub, "127.0.0.1", port)
    shell = {"hub": hub, "base": f"http://127.0.0.1:{port}"}
    try:
        status, text = get(shell, "/frames", **{"as": "dave"})
        assert "A policy owner converts them" in main_of(text)
        status, text = get(shell, "/frames", **{"as": "bob"})
        body = main_of(text)
        assert "read back exactly from their documents" in body and "Convert the SOPs to documents" in body
        status, url, body = post(shell, "/sops-convert", {"as": "dave", "confirm": "yes"})
        assert status == 403
        status, url, body = post(shell, "/sops-convert", {"as": "bob", "confirm": "yes"})
        assert status == 200 and hub.sop_source()
        assert hub.compile(save=False)["changes"] == []
        assert any(m["to"] == "carol" and "SOPs are documents" in m["subject"] for m in hub.inbox.all())
        status, text = get(shell, "/frames", **{"as": "bob"})
        assert "The rules are compiled from" in main_of(text)
    finally:
        server.shutdown()
        server.server_close()


def test_converting_waits_for_the_change_order_process(tmp_path, monkeypatch):
    import shutil

    from conftest import ROOT, release_all
    from hubproto import sopdocs
    from hubproto.app import Hub

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    real = hub.releases.bundle

    def without_change_orders(d):
        b = real(d)
        b["ops"].pop("change-order", None)
        return b

    monkeypatch.setattr(hub.releases, "bundle", without_change_orders)
    blockers = sopdocs.conversion_blockers(hub)
    assert any("no change-order process" in b for b in blockers)
    with pytest.raises(ValueError):
        sopdocs.convert_from_ui(hub, by="bob")
    assert not hub.sop_source()
