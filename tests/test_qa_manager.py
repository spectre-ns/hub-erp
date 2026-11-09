"""The QA manager cog: it approves on its own review when asked (a minor change on its signature, a major one with a
quality manager's countersignature), drafts controlled documents as their author (and never signs its own), and
works items assigned to it, completing an assigned step or handing it back."""

import shutil
from urllib.parse import quote

import pytest

from conftest import ROOT, release_all
from hubproto import review as review_mod
from hubproto.agent import AgentStore, AgentTools
from hubproto.app import Hub
from hubproto.guards import _widens
from hubproto.runner import RunError
from hubproto.shell import serve_in_thread
from test_shell_views import get, post


@pytest.fixture(scope="module")
def shell(tmp_path_factory):
    root = tmp_path_factory.mktemp("hub")
    shutil.copytree(ROOT / "frames", root / "frames")
    hub = Hub(root)
    hub.kernel.seed()
    release_all(hub)
    server = serve_in_thread(hub, "127.0.0.1", 0)  # port 0: bound at once, no race with other test workers
    port = server.server_address[1]
    yield {"hub": hub, "root": root, "base": f"http://127.0.0.1:{port}", "port": port, "server": server}
    server.shutdown()
    server.server_close()


def submit_doc(hub, title: str, change_type: str, owner: str = "alice") -> dict:
    run_id = hub.runner.submit("document-control", {"record": {"title": title, "change_type": change_type,
                                                               "body": f"# {title}\n\n1. Purpose\n2. Scope\n3. Steps"}}, owner=owner)
    return hub.runner.load(run_id)


def ask_to_approve(hub, rec_loc: str, who: str = "bob") -> dict:
    return hub.send(who, "qa-manager", "Approval", "@qa-manager please approve this document", record=rec_loc)


def test_dropping_a_countersignature_is_a_widening():
    from hubproto.guards import drift_guard

    src = {"frame": "f", "version": "1", "line": 1, "text": "x"}
    old = {"kind": "approval_criteria", "approver": "cog:qa-manager", "process": "document-control", "max_amount": None, "currency": None,
           "conditions": ["review_passes"], "countersign": "quality-manager", "source": src}
    new = {**old, "countersign": None}
    [add, remove] = sorted(drift_guard([old], [new]), key=lambda c: c["op"])
    assert add["widening"] and add["changes_from"]["countersign"] == "quality-manager"
    assert drift_guard([old], [old]) == []
    assert _widens(new, [old])


def test_the_qa_manager_is_released_with_criteria_but_no_route(shell):
    hub = shell["hub"]
    bundle = hub.releases.bundle(hub.releases.current()["digest"])
    crits = [c for c in bundle["approval_criteria"] if c["approver"] == "cog:qa-manager"]
    assert {(c["process"], c.get("countersign")) for c in crits} == {("document-control", None), ("document-control", "quality-manager"),
                                                                       ("change-control", "quality-manager")}
    assert not [r for r in bundle["routing"] if "qa-manager" in str(r.get("assignee"))]  # people stay first in line
    state = submit_doc(hub, "SOP-QA-route", "major")
    assert "qa-manager" not in state["pending"]["assignees"] and state["pending"]["assignee_type"] == "person"


def test_asked_to_approve_a_major_change_it_signs_and_a_quality_manager_countersigns(shell):
    hub = shell["hub"]
    state = submit_doc(hub, "SOP-QA-major", "major")
    waited = state["pending"]["assignees"]
    ask_to_approve(hub, state["record"])
    after = hub.runner.load(state["id"])
    assert after["status"] == "waiting" and after["pending"]["countersign_of"] == "qa-manager"
    assert after["pending"]["assignees"] == ["carol"] and after["pending"]["title"].startswith("Countersign")
    track = hub.runner.track(state["id"])
    signed = next(e for e in track if e["type"] == "gate.signed")
    assert signed["by"] == "qa-manager" and "3 adversarial reviews passed (compliance, consistency, impact)" in signed["meaning"] and signed["countersign_needed"] == "quality-manager"
    assert any(e["type"] == "gate.stepped_in" and e["was_waiting_for"] == waited for e in track)
    rec = hub.kernel.get(state["record"])
    assert any(d.get("by") == "qa-manager" and d.get("countersign_needed") for d in rec["decisions"])
    reply = [m for m in hub.inbox.on_record(state["record"]) if m["from"] == "qa-manager"][-1]
    assert "countersign" in reply["body"] and "carol" in reply["body"]
    # The countersignature completes the Gate; the document is published.
    done = hub.runner.decide(state["id"], "carol", "approve", meaning="countersigned the QA manager's approval", payload_digest=hub.runner.seen_digest(after))
    assert done["status"] == "completed" and hub.kernel.get(state["record"])["state"] == "effective"
    final = [d for d in hub.kernel.get(state["record"])["decisions"] if d.get("by") == "carol"][-1]
    assert "countersigning qa-manager" in final["meaning"]


def test_asked_to_approve_a_minor_change_its_signature_stands_alone(shell):
    hub = shell["hub"]
    # A minor change is a revision of a document in force (a brand-new document is always major: a person approves it).
    base = submit_doc(hub, "SOP-QA-base", "major")
    hub.runner.decide(base["id"], "bob", "approve", meaning="approved the base procedure", payload_digest=hub.runner.seen_digest(hub.runner.load(base["id"])))
    base_no = hub.kernel.get(base["record"])["number"]
    assert hub.kernel.get(base["record"])["state"] == "effective"
    hub.runner.cog_users_inline = False  # the minor change waits for the doc-reviewer cog instead of being taken at once
    try:
        run_id = hub.runner.submit("document-control", {"record": {"title": "SOP-QA-base", "change_type": "minor", "revises": base_no,
                                                                   "body": "# SOP-QA-base\n\n1. Purpose\n2. Scope\n3. Steps, reworded"}}, owner="alice")
        state = hub.runner.load(run_id)
        assert state["pending"]["assignees"] == ["doc-reviewer"]
    finally:
        hub.runner.cog_users_inline = True
    ask_to_approve(hub, state["record"])
    after = hub.runner.load(state["id"])
    assert after["status"] == "completed" and hub.kernel.get(state["record"])["state"] == "effective"
    decided = next(e for e in hub.runner.track(state["id"]) if e["type"] == "gate.decided")
    assert decided["by"] == "qa-manager" and "3 adversarial reviews passed (compliance, consistency, impact)" in decided["meaning"] and "the change is minor: yes" in decided["meaning"]


def test_a_new_document_declared_minor_is_a_major_change(shell):
    hub = shell["hub"]
    state = submit_doc(hub, "SOP-QA-new-minor", "minor")
    assert "doc-reviewer" not in state["pending"]["assignees"] and state["pending"]["assignee_type"] == "person"
    rec = hub.kernel.get(state["record"])
    assert rec["fields"]["conditions"]["change_minor"] is False


def test_a_failed_review_leaves_the_item_with_the_people(shell, monkeypatch):
    hub = shell["hub"]
    state = submit_doc(hub, "SOP-QA-fail", "major")
    waited = state["pending"]["assignees"]
    real = review_mod.review

    def failing(*a, **kw):
        r = real(*a, **kw)
        return {**r, "verdict": "fail", "findings": [{"severity": "error", "check": "scope", "detail": "no scope section"}]}

    monkeypatch.setattr(review_mod, "review", failing)
    ask_to_approve(hub, state["record"])
    after = hub.runner.load(state["id"])
    assert after["status"] == "waiting" and after["pending"]["assignees"] == waited and "stepped_in_from" not in after["pending"]
    reply = [m for m in hub.inbox.on_record(state["record"]) if m["from"] == "qa-manager"][-1]
    assert "cannot sign" in reply["body"] and "did not pass" in reply["body"]


def test_it_drafts_a_document_and_never_signs_its_own(shell):
    hub = shell["hub"]
    tools = AgentTools(hub, "qa-manager", "t-draft", "act", 5, AgentStore(shell["root"] / "state" / "agents"), requester="carol")
    out = tools.execute("draft_document", {"title": "Supplier audit procedure", "change_type": "major", "submit": True,
                                           "text": "## Purpose\nAudit suppliers.\n## Steps\n1. Plan\n2. Audit\n3. Report"})
    assert out["ok"] and out["submitted"] and "qa-manager" not in (out["waiting_for"] or [])
    rec = hub.kernel.get(f"rec:document:{out['document']}")
    assert rec["fields"]["author"] == "qa-manager" and rec["fields"]["doc_class"] == "controlled"
    assert "Supplier audit procedure" in (rec["fields"].get("text") or hub.dms.content(rec["locator"])[0].decode("latin-1"))
    run = hub.runner.run_for_record(rec["locator"])
    assert "segregation of duties" in hub.runner.may_step_in(run, "qa-manager")
    with pytest.raises(RunError):
        hub.runner.cog_step_in(run["id"], "qa-manager", asked_by="carol")


def test_a_task_filed_for_a_cog_is_worked_and_handed_back_when_not_finished(shell):
    hub = shell["hub"]
    run_id = hub.runner.submit("task", {"record": {"title": "Draft the calibration WI", "description": "Write a work instruction", "assignee": "qa-manager"}},
                               owner="alice")
    state = hub.runner.load(run_id)
    # The stand-in model only looks things up, so the cog cannot finish: the step goes back to alice with why.
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["alice"] and state["pending"]["handed_back_by"] == "qa-manager"
    assert any(e["type"] == "gate.handed_back" for e in hub.runner.track(run_id))
    assert any(m["from"] == "qa-manager" and "handed back" in m["body"] for m in hub.inbox.on_record(state["record"]))


def test_a_cog_completes_a_task_assigned_to_it(shell):
    hub = shell["hub"]
    hub.queue.mode = "service"  # it waits in the queue so the test can complete it as the cog would
    try:
        run_id = hub.runner.submit("task", {"record": {"title": "List open CAPAs", "assignee": "qa-manager"}}, owner="alice")
        state = hub.runner.load(run_id)
        assert state["pending"]["assignee_type"] == "cog" and hub.runner.is_assigned_work(state, "qa-manager")
        tools = AgentTools(hub, "qa-manager", "t-complete", "act", 5, AgentStore(shell["root"] / "state" / "agents"), requester="alice")
        number = hub.kernel.get(state["record"])["number"]
        assert "error" in tools.execute("take_turn", {"number": number})  # work, not an approval
        out = tools.execute("complete_step", {"number": number, "summary": "there are 2 open CAPAs: CAPA-0001, CAPA-0002"})
        assert out["ok"] and hub.runner.load(run_id)["status"] == "completed"
        assert hub.kernel.get(state["record"])["decisions"][-1]["meaning"].startswith("completed: there are 2 open CAPAs")
    finally:
        for t in hub.queue.open_tasks(cog="qa-manager"):
            hub.queue.cancel(t["id"], "test")
        hub.queue.mode = "inline"


def test_any_item_can_be_assigned_to_a_cog_from_its_page(shell):
    hub = shell["hub"]
    hub.queue.mode = "service"
    try:
        state = submit_doc(hub, "SOP-QA-assign", "major")
        page = "/document/" + quote(state["record"], safe=":")
        status, text = get(shell, page, **{"as": "alice"})
        assert "action='/assign'" in text and "qa-manager (cog)" in text
        status, url, text = post(shell, "/assign", {"as": "alice", "record": state["record"], "to": "qa-manager", "instruction": ""})
        assert status == 400 and "Say what qa-manager should do" in text
        status, url, text = post(shell, "/assign", {"as": "alice", "record": state["record"], "to": "qa-manager",
                                                    "instruction": "review the scope section and approve if it is ready"})
        assert status == 200 and "Assigned" in text and "it is next" in text
        assert "qa-manager" in hub.kernel.get(state["record"])["assignees"]
        [task] = hub.queue.open_tasks(cog="qa-manager")
        assert task["kind"] == "assigned" and task["payload"]["instruction"].startswith("review the scope")
        [done] = hub.queue.drain()
        assert done["status"] == "done" and done["result"]["run"]
        assert any(m["from"] == "qa-manager" and m["subject"].startswith("qa-manager on") for m in hub.inbox.on_record(state["record"]))
        status, url, text = post(shell, "/assign", {"as": "dave", "record": state["record"], "to": "qa-manager", "instruction": "x"})
        assert status == 403
    finally:
        hub.queue.mode = "inline"


def test_a_task_form_offers_cogs_as_assignees(shell):
    status, text = get(shell, "/start", **{"as": "alice", "process": "task"})
    assert status == 200 and '<option value="qa-manager"' in text
