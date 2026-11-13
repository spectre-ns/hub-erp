"""The agent harness: a cog working on its own, as itself, through the system's governed paths, within its autonomy and
budget; duties; new cogs and people as policy proposals; profile cogs with no code."""

import json
import re
from urllib.parse import quote

import pytest

from conftest import ROOT, edit_frame, release_all
from hubproto.agent import Agent, AgentStore, due_duties, run_duties


def scripted(steps, final="done"):
    """A stand-in for a model: calls the given tools in order, then answers."""
    def fake(**kw):
        calls = []
        for name, args in steps:
            res = kw["call_tool"](name, args)
            calls.append({"tool": name, "arguments": args, "ok": "error" not in res, "result": res})
        return {"text": final, "stand_in": False, "served_model": "scripted", "model": "scripted", "tool_calls": calls}
    return fake


def test_an_acting_cog_looks_then_comments_within_its_budget_and_the_person_is_told(released, monkeypatch):
    hub = released
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "Bent pins", "description": "connector J4"}, by="carol")
    monkeypatch.setattr(hub.model, "complete_with_tools", scripted([
        ("search_records", {"type": "ncr"}),
        ("comment", {"number": ncr["number"], "text": "This has waited a while for a disposition."}),
        ("finish", {"summary": f"Reminded the owner of {ncr['number']}."})]))
    res = Agent(hub, "quality-assistant").run("remind owners of old nonconformances", by="carol")
    assert res["status"] == "done" and res["actions"] == 1 and ncr["number"] in res["summary"]
    chatter = hub.inbox.on_record(ncr["locator"])
    assert chatter and chatter[-1]["from"] == "quality-assistant" and "waited a while" in chatter[-1]["body"]
    assert any(m["kind"] == "agent-run" and "quality-assistant finished" in m["subject"] for m in hub.inbox.for_principal("carol"))
    evs = AgentStore(hub.root / "state" / "agents").load(res["run"])
    assert [e["type"] for e in evs] == ["started", "tool", "tool", "tool", "finished"] and evs[2]["write"] is True


def test_the_system_still_decides_what_an_autonomous_cog_may_do(released, monkeypatch):
    hub = released
    item = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "x", "description": "y"}, by="carol")
    monkeypatch.setattr(hub.model, "complete_with_tools", scripted([
        ("take_turn", {"number": item["number"]}),                               # nothing is waiting for it there
        ("start_process", {"process": "purchase-to-pay", "fields": {"supplier": "acme-metals"}}),  # not its role
        ("message", {"to": "ap-reviewer", "body": "hi"}),                        # cogs do not message cogs
        ("finish", {"summary": "tried three things"})]))
    res = Agent(hub, "quality-assistant").run("do things", by="carol")
    evs = [e for e in AgentStore(hub.root / "state" / "agents").load(res["run"]) if e["type"] == "tool" and e.get("write")]
    assert [e["ok"] for e in evs] == [False, False, False], [e["result"] for e in evs]


def test_the_budget_stops_actions_and_propose_mode_waits_for_a_person(released, monkeypatch):
    hub = released
    item = hub.kernel.create("task", "Tasks", {"title": "t"}, by="alice")
    many = [("comment", {"number": item["number"], "text": f"note {i}"}) for i in range(4)] + [("finish", {"summary": "noted"})]
    monkeypatch.setattr(hub.model, "complete_with_tools", scripted(many))
    res = Agent(hub, "quality-assistant").run("note it", by="alice", max_actions=2)
    assert res["actions"] == 2 and len(hub.inbox.on_record(item["locator"])) == 2
    # doc-reviewer is "Autonomy: propose": nothing happens until someone confirms; it cannot propose where it cannot read.
    doc = hub.kernel.create("task", "Quality", {"title": "q"}, by="carol")
    monkeypatch.setattr(hub.model, "complete_with_tools", scripted([("comment", {"number": item["number"], "text": "where it cannot read"}),
                                                                    ("comment", {"number": doc["number"], "text": "proposed note"}), ("finish", {"summary": "s"})]))
    res = Agent(hub, "doc-reviewer").run("note it", by="carol")
    assert res["status"] == "proposed" and len(res["proposed"]) == 1 and hub.inbox.on_record(doc["locator"]) == []
    with pytest.raises(ValueError):
        Agent(hub, "doc-reviewer").confirm("nope", "carol")
    out = Agent(hub, "doc-reviewer").confirm(res["run"], "carol")
    assert out["results"][0]["result"]["ok"] and hub.inbox.on_record(doc["locator"])[-1]["body"] == "proposed note"


def test_the_kill_switch_and_duties(released, monkeypatch):
    hub = released
    monkeypatch.setenv("HUBPROTO_AGENTS", "off")
    with pytest.raises(ValueError, match="switched off"):
        Agent(hub, "quality-assistant").run("anything", by="carol")
    monkeypatch.delenv("HUBPROTO_AGENTS")
    # Duties are sentences in the SOPs that own the work: every day, every week or every month.
    due = due_duties(hub, today="2026-11-07")
    assert {c for c, _ in due} == {"quality-assistant", "purchasing-assistant", "it-admin", "management", "auditor"}
    assert any(d.startswith("Every day the quality-assistant cog finds nonconformances") for _, d in due)
    monkeypatch.setattr(hub.model, "complete_with_tools", scripted([("finish", {"summary": "nothing overdue"})]))
    done = run_duties(hub, today="2026-11-07")
    # The auditor is a senior cog on a hosted model, which tests leave off: it is not available, and its duty stays due.
    assert sorted(d["status"] for d in done) == ["done"] * 4 + ["unavailable"]
    assert [c for c, _ in due_duties(hub, today="2026-11-07")] == ["auditor"]
    assert {c for c, _ in due_duties(hub, today="2026-11-08")} == {"quality-assistant", "purchasing-assistant", "auditor"}, "the daily ones"
    assert {c for c, _ in due_duties(hub, today="2026-11-14")} == {"quality-assistant", "purchasing-assistant", "it-admin", "auditor"}, "a week on"


def test_a_new_cog_from_a_proposal_works_with_no_code(released, monkeypatch):
    hub = released
    p = hub.propose_new_cog(name="calibration-assistant", roles=["quality-manager", "staff"], model="ollama/qwen3:14b",
                            about="Keeps calibration on schedule.", persona="executor", by="carol")
    assert set(p["overrides"]) == {"org-model", "cog-profiles"} and not [f for f in p["findings"] if f["severity"] == "error"]
    hub.review(p["id"], "bob", approve_all=True, meaning="ok")
    hub.review(p["id"], "carol", approve_all=True, meaning="ok")
    hub.release(p["id"], "bob")
    b = hub.releases.bundle(hub.releases.current()["digest"])
    prof = b["cogs"]["calibration-assistant"]
    assert b["members"]["calibration-assistant"]["type"] == "cog" and prof["persona"] == "executor" and prof["about"] == "Keeps calibration on schedule."
    assert "calibration-sop" in prof["follows"], "it follows the SOPs that name its roles"
    assert "calibration-assistant" in hub.registry.ids() and "chat" in hub.registry.get("calibration-assistant").tasks
    assert "calibration-assistant" in hub.chat_cogs()
    monkeypatch.setattr(hub.model, "complete_with_tools", scripted([("overview", {}), ("finish", {"summary": "counted"})]))
    assert Agent(hub, "calibration-assistant").run("count things", by="carol")["status"] == "done"
    from hubproto.app import Hub

    assert "calibration-assistant" in Hub(hub.root).registry.ids(), "a fresh hub installs it from the release"


def test_a_new_person_from_a_proposal(released):
    hub = released
    p = hub.propose_new_person(name="erin", roles=["finance-clerk", "staff"], by="bob")
    assert list(p["overrides"]) == ["org-model"]
    hub.review(p["id"], "bob", approve_all=True, meaning="ok")
    hub.review(p["id"], "carol", approve_all=True, meaning="ok")
    hub.release(p["id"], "bob")
    b = hub.releases.bundle(hub.releases.current()["digest"])
    assert b["members"]["erin"] == {**b["members"]["erin"], "type": "person"} and set(b["members"]["erin"]["roles"]) == {"finance-clerk", "staff"}
    with pytest.raises(ValueError, match="already a member"):
        hub.propose_new_person(name="erin", roles=["staff"], by="bob")
    with pytest.raises(ValueError, match="no such role"):
        hub.propose_new_person(name="frank", roles=["wizard"], by="bob")


def test_the_shell_hands_a_cog_a_task_and_adds_members(monkeypatch, tmp_path):
    import shutil

    from hubproto.app import Hub
    from hubproto.shell import serve_in_thread
    from test_shell import get, post

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    server = serve_in_thread(hub, "127.0.0.1", 0)  # port 0: bound at once, no race with other test workers
    port = server.server_address[1]
    sh = {"hub": hub, "base": f"http://127.0.0.1:{port}"}
    try:
        status, text = get(sh, "/user/quality-assistant", **{"as": "carol"})
        assert "Working on its own" in text and "Give quality-assistant a task" in text and "Duties" in text
        monkeypatch.setattr(hub.model, "complete_with_tools", scripted([("overview", {}), ("finish", {"summary": "Counted the records."})]))
        status, url, text = post(sh, "/agent-task", {"as": "carol", "cog": "quality-assistant", "goal": "count the records"})
        assert status == 200 and "/agent-run/" in url and "Counted the records." in text and "overview" in text
        status, text = get(sh, "/people", **{"as": "bob"})
        assert "A new person" in text and "A new cog" in text and "name='roles' value='finance-clerk'" in text
        status, url, text = post(sh, "/new-person", {"as": "bob", "name": "erin", "roles": "finance-clerk"})
        assert status == 200 and "/proposal/" in url and "erin (person): finance-clerk" in text
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------- a task assigned to a cog (issue #72)
def assigned_cog(plan):
    """A stand-in for a cog working a task assigned to it: ``plan(number)`` gives the tools it calls, in order. A step
    may be a function of what the cog has heard so far (``heard``: each tool's last result), so it can react to a
    refusal as a model would; None skips it."""
    def fake(**kw):
        number = re.search(r"You are assigned (\S+)", kw["user"]).group(1)
        calls, heard = [], {}
        for step in plan(number):
            step = step(heard) if callable(step) else step
            if step is None:
                continue
            name, args = step
            heard[name] = res = kw["call_tool"](name, args)
            calls.append({"tool": name, "arguments": args, "ok": "error" not in res, "result": res})
        return {"text": "done", "stand_in": False, "served_model": "scripted", "model": "scripted", "tool_calls": calls}
    return fake


def file_task(hub, cog: str, by: str, title: str) -> tuple[str, dict]:
    """A task filed for a cog; the cog queue is inline, so the cog has worked it when this returns."""
    run_id = hub.runner.submit("task", {"record": {"title": title, "description": title, "assignee": cog}}, owner=by)
    return run_id, hub.runner.load(run_id)


def refused_writes_then_complete(number: str) -> list:
    # quality-assistant may not create documents in Quality/Audits, and there is no folder Quality/Plans: both refused.
    return [("create_document", {"title": "Audit plan", "folder": "Quality/Audits", "text": "# Audit plan"}),
            ("create_document", {"title": "Audit plan", "folder": "Quality/Plans", "text": "# Audit plan"}),
            ("comment", {"number": number, "text": "I could not create the audit plan."}),  # telling is not the work
            ("complete_step", {"number": number, "summary": "I could not create the document."})]


def the_runs_complete_step(hub, cog: str) -> dict:
    store = AgentStore(hub.root / "state" / "agents")
    [run] = store.runs(cog)
    [ev] = [e for e in store.load(run["run"]) if e.get("tool") == "complete_step"]
    return ev


def test_a_cog_whose_every_change_was_refused_may_not_complete_its_task_and_hands_it_back(released, monkeypatch):
    hub = released
    monkeypatch.setattr(hub.model, "complete_with_tools", assigned_cog(lambda number: refused_writes_then_complete(number) + [
        lambda heard: ("hand_back", {"number": number, "reason": "I may not create documents in Quality/Audits"})
        if "error" in heard["complete_step"] else None,
        ("finish", {"summary": "Could not create the audit plan."})]))
    run_id, state = file_task(hub, "quality-assistant", "bob", "Create an audit plan")
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["bob"], \
        f"the task closed although nothing was done: {state['status']}"
    assert not any(d["meaning"].startswith("completed") for d in hub.kernel.get(state["record"])["decisions"])
    refusal = the_runs_complete_step(hub, "quality-assistant")
    assert not refusal["ok"] and "nothing was done: every change you tried was refused" in refusal["result"] \
        and "hand it back" in refusal["result"]
    [handed] = [e for e in hub.runner.track(run_id) if e["type"] == "gate.handed_back"]
    assert "I may not create documents in Quality/Audits" in json.dumps(handed), "the cog's own hand-back, with its reason"


def test_a_cog_that_ignores_the_refusal_still_leaves_the_task_with_the_person(released, monkeypatch):
    hub = released
    monkeypatch.setattr(hub.model, "complete_with_tools", assigned_cog(lambda number: refused_writes_then_complete(number) + [
        ("finish", {"summary": "Created the audit plan."})]))
    run_id, state = file_task(hub, "quality-assistant", "bob", "Create an audit plan")
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["bob"], \
        f"the task closed although nothing was done: {state['status']}"
    [handed] = [e for e in hub.runner.track(run_id) if e["type"] == "gate.handed_back"]
    assert "Not done (the hub refused)" in json.dumps(handed), "the hand-back says what the hub refused"


def test_a_cog_may_complete_a_task_that_needs_no_change(released, monkeypatch):
    hub = released
    monkeypatch.setattr(hub.model, "complete_with_tools", assigned_cog(lambda number: [
        ("search_records", {"type": "ncr"}),
        ("complete_step", {"number": number, "summary": "there are no open nonconformances"}),
        ("finish", {"summary": "Checked: no open nonconformances."})]))
    run_id, state = file_task(hub, "quality-assistant", "bob", "Check for open nonconformances")
    assert state["status"] == "completed"
    assert hub.kernel.get(state["record"])["decisions"][-1]["meaning"].startswith("completed: there are no open")


def test_a_cog_may_complete_a_task_when_one_of_its_changes_went_through(released, monkeypatch):
    hub = released
    monkeypatch.setattr(hub.model, "complete_with_tools", assigned_cog(lambda number: [
        ("create_document", {"title": "Calibration plan", "folder": "Quality/Audits", "text": "# Calibration plan"}),
        ("create_document", {"title": "Calibration plan", "folder": "Quality/Controlled Documents", "text": "# Calibration plan"}),
        ("complete_step", {"number": number, "summary": "created the calibration plan"}),
        ("finish", {"summary": "Created the calibration plan."})]))
    run_id, state = file_task(hub, "qa-manager", "carol", "Create a calibration plan")
    assert the_runs_complete_step(hub, "qa-manager")["ok"]
    assert state["status"] == "completed", "a refused attempt and then one that went through is work done"
