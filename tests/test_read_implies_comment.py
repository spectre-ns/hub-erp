"""Whoever may read a record may comment on it (issue #89): ``comment`` is allowed wherever ``read`` is, for people and
cogs alike, through ``authz.check`` and the cog's derived autonomy. An explicit "may not comment" still refuses, a
"when a person confirms" sentence about commenting still makes the cog propose, a cog working for someone comments only
where that person may too, and the decision cites the read sentence it comes from ("Why?", the cog's profile)."""


import pytest

from conftest import edit_frame, release_all
from hubproto import authz
from hubproto.agent import Agent, AgentStore, AgentTools
from hubproto.shell import serve_in_thread
from test_shell_views import get, main_of, plain, post

DENY_ALICE = ("Payables reviewers may comment on anything in Finance.\n",
              "Payables reviewers may comment on anything in Finance.\nFinance clerks may not comment on anything in Finance.\n"
              "Finance clerks may not comment on anything in Tasks.\n")


def bundle(hub):
    return hub.releases.bundle(hub.releases.current()["digest"])


def tools(hub, cog, requester=""):
    return AgentTools(hub, cog, f"t-{cog}-{requester or 'own'}", "derived", 8, AgentStore(hub.root / "state" / "agents"), requester=requester)


def scripted(steps, final="done"):
    """A stand-in for the cog's model: calls the given tools in order, then answers."""
    def fake(**kw):
        calls = []
        for name, args in steps:
            res = kw["call_tool"](name, args)
            calls.append({"tool": name, "arguments": args, "ok": "error" not in res, "result": res})
        return {"text": final, "stand_in": False, "served_model": "scripted", "model": "scripted", "tool_calls": calls}
    return fake


@pytest.fixture
def denied(hub):
    """The example company, plus: finance clerks (alice) may not comment in Finance or in Tasks."""
    edit_frame(hub, "org-model", *DENY_ALICE)
    release_all(hub)
    return hub


@pytest.fixture
def shell(denied):
    server = serve_in_thread(denied, "127.0.0.1", 0)
    yield {"hub": denied, "base": f"http://127.0.0.1:{server.server_address[1]}"}
    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------- authz: reading gives commenting
def test_the_auditor_may_comment_on_a_task_it_may_read(released):
    b = bundle(released)
    assert authz.check(b, "auditor", "read", "task", "Tasks")["allowed"], "the auditor reads Tasks through the cogs group"
    d = authz.check(b, "auditor", "comment", "task", "Tasks")
    assert d["allowed"], d["why"]
    assert d.get("implied") == "read" and d["rule"]["action"] == "read", "the decision cites the read sentence it comes from"
    assert "auditor may read Tasks (tasks-sop line" in d["why"] and authz.IMPLIED in d["why"], d["why"]
    a = authz.autonomy(b, "auditor", "comment", "task", "Tasks")
    assert a["mode"] == "act" and "may read Tasks" in a["why"] and authz.IMPLIED in a["why"], a


def test_a_cog_working_on_a_task_comments_on_it(released, monkeypatch):
    """The live hub's refusal: the auditor, working on a task, was told it may not comment on it."""
    hub = released
    monkeypatch.setenv("HUBPROTO_HOSTED_MODELS", "on")  # the auditor is a senior cog (no key: nothing is called)
    task = hub.kernel.create("task", "Tasks", {"title": "Check the calibration records"}, by="carol")
    out = tools(hub, "auditor", requester="carol").execute("comment", {"number": task["number"], "text": "Looked; two records are late."})
    assert out.get("ok"), out
    monkeypatch.setattr(hub.model, "complete_with_tools", scripted([
        ("comment", {"number": task["number"], "text": "Started on it."}), ("finish", {"summary": f"Commented on {task['number']}."})]))
    res = Agent(hub, "auditor").run(f"work on {task['number']}", by="carol")
    evs = [e for e in AgentStore(hub.root / "state" / "agents").load(res["run"]) if e["type"] == "tool" and e["tool"] == "comment"]
    assert evs and evs[0]["ok"] and evs[0]["decided"]["mode"] == "act", evs
    assert authz.IMPLIED in evs[0]["decided"]["why"], "the run's Track says where the right came from"
    assert res["actions"] == 1 and [m["body"] for m in hub.inbox.on_record(task["locator"]) if m["from"] == "auditor"] == [
        "Looked; two records are late.", "Started on it."]


def test_a_person_who_may_read_a_folder_may_comment_there(released):
    b = bundle(released)
    assert not [r for r in authz.permissions_of(b, "alice") if r["action"] == "comment" and r["resource"] == "folder:Finance"], \
        "no sentence lets alice comment in Finance"
    assert authz.check(b, "alice", "read", "*", "Finance")["allowed"]
    d = authz.check(b, "alice", "comment", "*", "Finance")
    assert d["allowed"] and d["implied"] == "read" and "alice may read Finance (finance-policy line" in d["why"], d["why"]
    # A sentence about commenting still decides where there is one: it is cited, not the read sentence.
    staff = authz.check(b, "alice", "comment", "task", "Tasks")
    assert staff["allowed"] and not staff.get("implied") and "Staff may comment on anything in Tasks." in staff["why"]


def test_who_may_not_read_a_folder_may_not_comment_there(released):
    hub = released
    b = bundle(hub)
    assert authz.check(b, "alice", "comment", "*", "Finance")["allowed"], "a reader of Finance comments there"
    assert not authz.check(b, "carol", "read", "*", "Finance")["allowed"]
    d = authz.check(b, "carol", "comment", "*", "Finance")
    assert not d["allowed"] and not d.get("implied") and "comment * in Finance" in d["why"], d
    assert authz.autonomy(b, "quality-assistant", "comment", "*", "Finance")["mode"] == "refuse"
    item = hub.kernel.create("task", "Finance", {"title": "Accrual check"}, by="alice")
    out = tools(hub, "quality-assistant", requester="carol").execute("comment", {"number": item["number"], "text": "x"})
    assert "error" in out and hub.inbox.on_record(item["locator"]) == []


# ---------------------------------------------------------------- what still holds
def test_an_explicit_deny_on_commenting_still_refuses(denied):
    b = bundle(denied)
    assert authz.check(b, "alice", "read", "*", "Finance")["allowed"], "alice still reads Finance"
    d = authz.check(b, "alice", "comment", "*", "Finance")
    assert not d["allowed"] and "Finance clerks may not comment on anything in Finance." in d["why"], d
    # The deny wins over an explicit allow too (Staff may comment on anything in Tasks).
    assert not authz.check(b, "alice", "comment", "task", "Tasks")["allowed"]
    assert authz.check(b, "bob", "comment", "*", "Finance")["allowed"], "the deny is for finance clerks only"


def test_a_confirm_first_rule_still_proposes(released):
    hub = released
    b = bundle(hub)
    # "The doc-reviewer cog may comment on anything in Quality when a person confirms." narrows what reading gives.
    assert authz.check(b, "doc-reviewer", "read", "*", "Quality")["allowed"]
    assert not authz.check(b, "doc-reviewer", "comment", "nonconformance", "Quality/Nonconformances")["allowed"], "a confirm sentence is no allow"
    a = authz.autonomy(b, "doc-reviewer", "comment", "nonconformance", "Quality/Nonconformances")
    assert a["mode"] == "propose" and a["confirmer"] == "person" and not a.get("implied"), a
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "Scratched housing", "description": "x"}, by="carol")
    out = tools(hub, "doc-reviewer", requester="carol").execute("comment", {"number": ncr["number"], "text": "Shall I?"})
    assert out.get("proposed") and hub.inbox.on_record(ncr["locator"]) == [], out
    # Where no such sentence is (Tasks), reading decides: it comments on its own.
    task = hub.kernel.create("task", "Tasks", {"title": "Tidy the index"}, by="carol")
    out = tools(hub, "doc-reviewer", requester="carol").execute("comment", {"number": task["number"], "text": "On it."})
    assert out.get("ok"), out


def test_a_cog_comments_only_where_the_person_it_works_for_may(denied):
    hub = denied
    task = hub.kernel.create("task", "Tasks", {"title": "Reconcile the ledger"}, by="alice")
    out = tools(hub, "auditor", requester="alice").execute("comment", {"number": task["number"], "text": "x"})
    assert "alice, for whom you work, may not comment" in out.get("error", ""), out
    assert "Finance clerks may not comment on anything in Tasks." in out["error"]
    assert tools(hub, "auditor", requester="carol").execute("comment", {"number": task["number"], "text": "y"}).get("ok")


# ---------------------------------------------------------------- the pages say where it comes from
def test_why_and_the_profile_say_reading_gives_commenting(shell):
    status, html = get(shell, "/why", **{"as": "alice", "principal": "auditor", "action": "comment", "object": "task", "folder": "Tasks"})
    text = plain(main_of(html))
    assert status == 200 and "allowed" in text and "may read Tasks" in text and authz.IMPLIED in text, text[-600:]
    prof = plain(main_of(get(shell, "/user/auditor", **{"as": "carol"})[1]))
    assert "it may read Tasks: " + authz.IMPLIED in prof, "the profile lists the right to comment by reading"


def test_the_chatter_takes_a_readers_comment_and_refuses_a_denied_one(shell):
    hub = shell["hub"]
    rec = hub.kernel.create("task", "Finance", {"title": "Month-end close"}, by="bob")
    back = f"/record/{rec['locator']}"
    ok = post(shell, "/msg", {"as": "dave", "record": rec["locator"], "to": "bob", "subject": "About it", "body": "Looks fine.", "return": back})
    assert ok[0] == 200 and [m["body"] for m in hub.inbox.on_record(rec["locator"])] == ["Looks fine."], ok[2][:300]
    no = post(shell, "/msg", {"as": "alice", "record": rec["locator"], "to": "bob", "subject": "About it", "body": "Me too.", "return": back})
    assert no[0] == 403 and "You may not comment on " + rec["number"] in plain(no[2]), plain(no[2])[-400:]
    assert "Finance clerks may not comment" in plain(no[2])
    assert [m["body"] for m in hub.inbox.on_record(rec["locator"])] == ["Looks fine."], "the denied comment was not posted"
    page = plain(main_of(get(shell, back, **{"as": "alice"})[1]))
    assert "You may not comment on " + rec["number"] in page, "the chatter says why instead of offering a form"
