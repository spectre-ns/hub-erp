"""The agent harness: a cog working on its own, as itself, through the system's governed paths, within its autonomy and
budget; duties; new cogs and people as policy proposals; profile cogs with no code."""

import json
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
    due = due_duties(hub, today="2026-11-07")
    assert {c for c, _ in due} == {"quality-assistant", "purchasing-assistant"}
    monkeypatch.setattr(hub.model, "complete_with_tools", scripted([("finish", {"summary": "nothing overdue"})]))
    done = run_duties(hub, today="2026-11-07")
    assert len(done) == 2 and due_duties(hub, today="2026-11-07") == [] and len(due_duties(hub, today="2026-11-08")) == 2


def test_a_new_cog_from_a_proposal_works_with_no_code(released, monkeypatch):
    hub = released
    p = hub.propose_new_cog(name="calibration-assistant", roles=["quality-manager", "staff"], model="claude-sonnet-5-5",
                            prompt="You keep calibration on schedule.", follows=["calibration-sop"], autonomy="act",
                            duties=["Find calibrations due this month and tell the quality manager."], by="carol")
    assert set(p["overrides"]) == {"org-model", "cog-profiles"} and not [f for f in p["findings"] if f["severity"] == "error"]
    hub.review(p["id"], "bob", approve_all=True, meaning="ok")
    hub.review(p["id"], "carol", approve_all=True, meaning="ok")
    hub.release(p["id"], "bob")
    b = hub.releases.bundle(hub.releases.current()["digest"])
    assert b["members"]["calibration-assistant"]["type"] == "cog" and b["cogs"]["calibration-assistant"]["autonomy"] == "act"
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
    from hubproto.shell import free_port, serve_in_thread
    from test_shell import get, post

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    port = free_port()
    server = serve_in_thread(hub, "127.0.0.1", port)
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
