"""The automation engine: the hub's own jobs, step by step, with an audit trail. Approving a revision a change order
carries compiles the Frame; approving the change order releases it; a job that stops says why and can be retried by
the people involved; the Automation pages show it all, live."""

import shutil

import pytest

from conftest import ROOT, release_all
from hubproto import automation, eco, sopdocs
from hubproto.app import Hub
from hubproto.shell import free_port, serve_in_thread
from test_eco import NEW, OLD, approve, write
from test_shell_views import get, main_of, post


@pytest.fixture
def shell(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    sopdocs.convert(hub, by="carol")
    hub.kernel.set_fields(sopdocs.in_force(hub.kernel, "finance-policy")["locator"], {"owner": "alice"}, by="setup")
    port = free_port()
    server = serve_in_thread(hub, "127.0.0.1", port)
    yield {"hub": hub, "base": f"http://127.0.0.1:{port}"}
    server.shutdown()
    server.server_close()


def prepared(hub):
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Lower the AP limit", "description": "x", "requested_by": "alice"}, by="alice")
    draft = eco.add_sop(hub, co["number"], "finance-policy", by="alice")
    write(hub, draft, OLD, NEW, "alice")
    run = hub.runner.submit("document-control", {"record": {"change_type": "minor"}}, owner="alice", record=draft["locator"])
    approve(hub, run, hub.runner.load(run)["pending"]["assignees"][0])
    return hub.kernel.get(co["locator"]), draft


def test_the_automation_page_shows_each_job_and_its_steps(shell):
    hub = shell["hub"]
    co, draft = prepared(hub)
    [job] = hub.automation.of(co["locator"])
    assert job["release"] == hub.releases.current()["digest"] and job["started_by"] == "hub" and draft["number"] in job["acting_on"]
    status, text = get(shell, "/automation", **{"as": "carol"})
    body = main_of(text)
    assert status == 200 and "Compile the Frame" in body and "succeeded" in body and co["number"] in body and "Release the change order" in body
    status, text = get(shell, "/automation/" + job["id"], **{"as": "carol"})
    body = main_of(text)
    for step in automation.AUTOMATIONS["compile-frame"]["steps"]:
        assert step in body
    assert body.count("✓") == 5 and "compiled as proposal" in body and "Under policy" in body
    status, text = get(shell, "/automation-status", **{"as": "carol", "job": job["id"]})
    assert status == 200 and job["id"] in text
    status, text = get(shell, "/record/" + co["locator"], **{"as": "alice"})
    assert "Compile the Frame" in main_of(text), "the change order shows its latest automation job"


def test_a_job_that_stops_says_why_and_only_the_people_involved_retry_it(shell):
    hub = shell["hub"]
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Not ready", "description": "x", "requested_by": "alice"}, by="alice")
    eco.add_sop(hub, co["number"], "finance-policy", by="alice")
    job = automation.start(hub, "release-change-order", subject=co["locator"], trigger="test", by="alice")
    job = hub.automation.load(job["id"])
    assert job["status"] == "failed" and "not ready" in job["outcome"] and job["steps"][0]["status"] == "failed"
    assert all(s["status"] == "skipped" for s in job["steps"][1:])
    assert any("stopped on" in m["subject"] for m in hub.inbox.all() if m["to"] == "alice"), "the requester is told"
    status, url, body = post(shell, "/automation-retry", {"as": "dave", "job": job["id"]})
    assert status == 403
    status, url, body = post(shell, "/automation-retry", {"as": "alice", "job": job["id"]})
    assert status == 200 and hub.automation.load(job["id"])["attempt"] == 2
    assert automation.start(hub, "compile-frame", subject=co["locator"], trigger="x")["status"] == "no-actions"


def test_an_automation_running_again_and_again_on_one_item_is_stopped_as_a_loop(shell):
    hub = shell["hub"]
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Loop", "description": "x", "requested_by": "alice"}, by="alice")
    for _ in range(automation.LOOP_LIMIT):
        automation.start(hub, "compile-frame", subject=co["locator"], trigger="again")
    with pytest.raises(ValueError, match="loop"):
        automation.start(hub, "compile-frame", subject=co["locator"], trigger="again")


def test_only_members_who_may_read_the_item_see_its_jobs(shell):
    hub = shell["hub"]
    co, _ = prepared(hub)
    status, text = get(shell, "/automation", **{"as": "nobody"})
    assert status in (400, 403)
