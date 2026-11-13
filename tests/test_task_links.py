"""Completing a task links it to what its work produced (issue #91): the Tasks SOP says "Completing the task links it to
what its work produced.", and the hub links the task with ``produces`` to every record the work on it created or changed:
what a cog assigned to it wrote, and what the person completing it names. Deterministic, on the Track, no duplicates."""

import re

import pytest

from conftest import ROOT, edit_frame, seen
from hubproto.extract import ConventionExtractor
from hubproto.frames import FrameStore
from hubproto.guards import rule_key
from hubproto.runner import RunError
from hubproto.settings import sentence
from test_agents import assigned_cog, file_task

SENTENCE = "Completing the task links it to what its work produced."


def produced(hub, task_ref: str) -> list[str]:
    """The numbers of the records the task links to with ``produces``, in order."""
    rec = hub.kernel.get(task_ref)
    return [hub.kernel.get(l["target"])["number"] for l in rec["links"] if l["rel"] == "produces"]


def the_task_process(rules: list[dict]) -> dict:
    return next(r for r in rules if r["kind"] == "process" and r["name"] == "task")


# ---------------------------------------------------------------- the sentence
def test_the_sentence_compiles_into_the_task_process_and_round_trips(released, tmp_path):
    frames = FrameStore(ROOT / "frames")
    out = ConventionExtractor().extract(frames.get("tasks-sop"))
    rule = the_task_process(out.rules)
    assert rule.get("links_produced") is True, "the Tasks SOP's sentence is read into the task process"
    assert not [n for n in out.notes if SENTENCE in (n.get("text") or "")], "the sentence is read, not left as a note"
    assert released.releases.bundle(released.releases.current()["digest"])["processes"]["task"].get("links_produced") is True, \
        "the release carries it to the runner"
    # The Settings sentence writes it back, and what it writes compiles to the same rule.
    text = sentence(rule)
    assert SENTENCE in text.splitlines()
    path = tmp_path / "f" / "t.frame.md"
    path.parent.mkdir()
    path.write_text("---\ntype: frame [0.2]\nname: t\ndescription: t\nvisibility: internal\nversion: \"0.1.0\"\ncompile: convention\n---\n# T\n\n" + text + "\n")
    again = the_task_process(ConventionExtractor().extract(FrameStore(tmp_path / "f").get("t")).rules)
    unplaced = lambda r: {**r, "steps": [{k: v for k, v in s.items() if k != "line"} for s in r["steps"]]}  # where, not what
    assert again.get("links_produced") is True and rule_key(unplaced(again)) == rule_key(unplaced(rule))


def test_a_sentence_about_another_item_or_a_near_miss_is_not_read(tmp_path):
    (tmp_path / "f").mkdir()
    (tmp_path / "f" / "t.frame.md").write_text(
        "---\ntype: frame [0.2]\nname: t\ndescription: t\nvisibility: internal\nversion: \"0.1.0\"\ncompile: convention\n---\n# T\n\n## Process: task\n"
        "Records are filed in Tasks.\nThe record type is task.\nCompleting the invoice links it to what its work produced.\n"
        "1. [staff] File the task (record-filer)\n2. [staff] Complete the task\n\n## Process: chore\nRecords are filed in Tasks.\n"
        "Completing the chore links it to everything it made.\n1. [staff] File the chore\n")
    out = ConventionExtractor().extract(FrameStore(tmp_path / "f").get("t"))
    procs = {r["name"]: r for r in out.rules if r["kind"] == "process"}
    assert not procs["task"].get("links_produced") and not procs["chore"].get("links_produced")
    unread = [n for n in out.notes if n.get("severity") == "unread"]
    assert any("not 'invoice'" in n["note"] for n in unread), unread
    assert any("Completing the chore links it to everything" in n["text"] for n in unread), "a near miss becomes a question"


def test_turning_it_on_or_off_is_a_change_reviewers_see_and_not_a_widening(released):
    edit_frame(released, "tasks-sop", SENTENCE + "\n", "")
    p = released.compile()
    [c] = [c for c in p["changes"] if c["rule"]["kind"] == "process" and c["rule"]["name"] == "task" and c["op"] == "add"]
    assert "no longer links it to what its work produced" in c["reason"], c["reason"]
    assert not c["widening"], "traceability gives nobody authority"


# ---------------------------------------------------------------- a cog's work
def drafts_and_files(number: str) -> list:
    return [("create_document", {"title": "Calibration plan", "folder": "Quality/Controlled Documents", "text": "# Calibration plan"}),
            ("create_record", {"type": "ncr", "fields": {"title": "Torque wrench out of calibration", "description": "found while planning",
                                                         "source": "inspection"}}),
            ("comment", {"number": number, "text": "Drafted the plan and filed the follow-up."}),  # telling is not a product
            ("complete_step", {"number": number, "summary": "drafted the calibration plan and filed the follow-up"}),
            ("finish", {"summary": "Drafted the calibration plan."})]


def test_a_cog_that_drafts_a_document_and_files_a_record_links_both_when_it_completes_the_task(released, monkeypatch):
    hub = released
    monkeypatch.setattr(hub.model, "complete_with_tools", assigned_cog(drafts_and_files))
    run_id, state = file_task(hub, "qa-manager", "carol", "Write a calibration plan")
    assert state["status"] == "completed", state
    numbers = produced(hub, state["record"])
    assert len(numbers) == 2, f"the document and the record the cog made: {numbers}"
    doc, filed = (hub.kernel.get(n) for n in numbers)
    assert doc["type"] == "document" and doc["fields"]["title"] == "Calibration plan"
    assert filed["type"] == "ncr" and filed["fields"]["title"] == "Torque wrench out of calibration"
    # The other side shows it was produced by the task; the Track says the hub linked them, on completion.
    assert {"rel": "produces", "target": state["record"], "direction": "in", "label": "produced-by"} in hub.kernel.links_of(doc["locator"])
    [ev] = [e for e in hub.runner.track(run_id) if e["type"] == "record.produced_linked"]
    assert ev["when"] == "completed" and [x["number"] for x in ev["links"]] == numbers and {x["by"] for x in ev["links"]} == {"qa-manager"}
    linked = [e for e in hub.kernel.events(state["record"]) if e["type"] == "record.linked"]
    assert {e["run_id"] for e in linked} == {run_id} and {e["by"] for e in linked} == {"hub"}
    # The cog's completion is still its signature on the task as it saw it.
    assert hub.kernel.get(state["record"])["decisions"][-1]["meaning"].startswith("completed: drafted the calibration plan")


def test_hand_back_links_what_was_produced_so_far(released, monkeypatch):
    hub = released
    monkeypatch.setattr(hub.model, "complete_with_tools", assigned_cog(lambda number: [
        ("create_document", {"title": "Half a plan", "folder": "Quality/Controlled Documents", "text": "# Half a plan"}),
        ("hand_back", {"number": number, "reason": "I need the torque specs from a person"}),
        ("finish", {"summary": "Started the plan; handed it back."})]))
    run_id, state = file_task(hub, "qa-manager", "carol", "Write a calibration plan")
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["carol"]
    [number] = produced(hub, state["record"])
    assert hub.kernel.get(number)["fields"]["title"] == "Half a plan"
    [ev] = [e for e in hub.runner.track(run_id) if e["type"] == "record.produced_linked"]
    assert ev["when"] == "handed back"
    # carol finishes it; what the cog produced is linked once, not again.
    state = hub.runner.load(run_id)
    hub.runner.decide(run_id, "carol", "approve", meaning="finished the plan", payload_digest=seen(hub.runner, run_id))
    assert produced(hub, state["record"]) == [number], "no duplicate link at completion"
    assert len([e for e in hub.kernel.events(state["record"]) if e["type"] == "record.linked"]) == 1


# ---------------------------------------------------------------- a person's work
def test_a_person_names_what_their_work_produced_when_they_complete_the_task(released):
    hub = released
    run_id = hub.runner.submit("task", {"record": {"title": "Write the torque procedure", "assignee": "bob"}}, owner="carol")
    task = hub.runner.load(run_id)["record"]
    doc = hub.dms.create(title="Torque procedure", doc_class="working", folder="Quality/Working Documents", by="bob")
    other = hub.kernel.create("task", "Tasks", {"title": "Buy a torque wrench", "assignee": "bob"}, by="bob")
    with pytest.raises(RunError, match="is the item itself"):
        hub.runner.decide(run_id, "bob", "approve", meaning="written", payload_digest=seen(hub.runner, run_id), produced=[task])
    with pytest.raises(RunError, match="there is no record NOPE-0001 that bob may read"):
        hub.runner.decide(run_id, "bob", "approve", meaning="written", payload_digest=seen(hub.runner, run_id), produced=["NOPE-0001"])
    s = hub.runner.decide(run_id, "bob", "approve", meaning="wrote the procedure", payload_digest=seen(hub.runner, run_id),
                          produced=[doc["number"], other["number"], doc["number"]])
    assert s["status"] == "completed"
    assert produced(hub, task) == [doc["number"], other["number"]], "each named record once"
    [ev] = [e for e in hub.runner.track(run_id) if e["type"] == "record.produced_linked"]
    assert {x["by"] for x in ev["links"]} == {"bob"}
    # Nothing named, nothing linked: a person's work is what they say it was, never a guess.
    run2 = hub.runner.submit("task", {"record": {"title": "Tidy the lab", "assignee": "bob"}}, owner="carol")
    hub.runner.decide(run2, "bob", "approve", meaning="tidied", payload_digest=seen(hub.runner, run2))
    assert produced(hub, hub.runner.load(run2)["record"]) == []


def test_without_the_sentence_nothing_is_linked(released, monkeypatch):
    hub = released
    edit_frame(hub, "tasks-sop", SENTENCE + "\n", "")
    from conftest import release_all

    release_all(hub)
    assert not hub.releases.bundle(hub.releases.current()["digest"])["processes"]["task"].get("links_produced")
    monkeypatch.setattr(hub.model, "complete_with_tools", assigned_cog(drafts_and_files))
    run_id, state = file_task(hub, "qa-manager", "carol", "Write a calibration plan")
    assert state["status"] == "completed"
    assert produced(hub, state["record"]) == [] and not [e for e in hub.runner.track(run_id) if e["type"] == "record.produced_linked"]
    run2 = hub.runner.submit("task", {"record": {"title": "Write it", "assignee": "bob"}}, owner="carol")
    doc = hub.dms.create(title="Notes", doc_class="working", folder="Quality/Working Documents", by="bob")
    with pytest.raises(RunError, match="does not link an item to what its work produced"):
        hub.runner.decide(run2, "bob", "approve", meaning="done", payload_digest=seen(hub.runner, run2), produced=[doc["number"]])


# ---------------------------------------------------------------- the pages
def test_the_task_page_shows_what_it_produced_and_the_card_asks_for_it(released, monkeypatch):
    from test_shell_views import get, main_of, plain, post
    from hubproto.shell import serve_in_thread

    hub = released
    monkeypatch.setattr(hub.model, "complete_with_tools", assigned_cog(drafts_and_files))
    _run_id, state = file_task(hub, "qa-manager", "carol", "Write a calibration plan")
    task = hub.kernel.get(state["record"])
    doc_number = produced(hub, task["locator"])[0]
    server = serve_in_thread(hub, "127.0.0.1", 0)
    try:
        sh = {"base": f"http://127.0.0.1:{server.server_address[1]}"}
        status, page = get(sh, f"/record/{task['number']}", **{"as": "carol"})
        assert status == 200
        text = plain(main_of(page))
        assert "produces" in text and doc_number in text, "the task's Links show what it produced"
        assert "the hub linked it to what its work produced" in text, "its history says the hub linked them, and when"
        status, page = get(sh, f"/document/{doc_number}", **{"as": "carol"})
        assert "produced by" in plain(main_of(page)).replace("-", " ") and task["number"] in plain(main_of(page))
        # A person completing a task is asked what their work produced; what they name is linked.
        run2 = hub.runner.submit("task", {"record": {"title": "Write the torque procedure", "assignee": "bob"}}, owner="carol")
        task2 = hub.runner.load(run2)["record"]
        status, inbox = get(sh, "/inbox", **{"as": "bob", "open": run2})
        assert "What your work produced" in inbox
        status, url, body = post(sh, "/decide", {"as": "bob", "run": run2, "verdict": "approve", "digest": seen(hub.runner, run2),
                                                 "meaning": "wrote it", "confirm": "1", "produced": doc_number})
        assert status == 200 and produced(hub, task2) == [doc_number], body[:400]
    finally:
        server.shutdown()
        server.server_close()
