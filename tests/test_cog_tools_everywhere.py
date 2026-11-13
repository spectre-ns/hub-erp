"""Cogs have their tools wherever they work, within their access policy (issue #94): answering a mention, editing a
document, reviewing an item or a change order. Every tool call goes through the same governed path as an agent run
(hubproto.agent.AgentTools.execute: the budget, the decision from the released SOPs, both_may with the person asking,
the run's Track); a reviewer never changes what it reviews; the hub's own model steps get no tools. The model is a
scripted fake of ``complete_with_tools`` (it calls the tools it is given, then answers), as in tests/test_agents.py."""

import inspect
import shutil

import pytest

from conftest import ROOT, release_all
from hubproto import automation, changecontrol as cc, eco, llm_extract, mentions, prose_drafting, sopdocs, wopi
from hubproto.agent import AgentStore, AgentTools
from hubproto.app import Hub
from test_eco import write


def scripted(cog, steps, final, seen=None):
    """A model for ``cog`` that calls the given tools in order, then answers ``final``; any other caller gets the
    gateway's own answer. ``seen`` collects what each tool returned and the tools offered."""
    def install(hub, monkeypatch):
        real = hub.model.complete_with_tools

        def fake(**kw):
            if kw.get("by") != cog:
                return real(**kw)
            if seen is not None:
                seen.setdefault("offered", [t["name"] for t in kw["tools"]])
                seen.setdefault("system", kw["system"])
            calls = []
            for name, args in steps:
                res = kw["call_tool"](name, args)
                calls.append({"tool": name, "arguments": args, "ok": "error" not in res})
                if seen is not None:
                    seen.setdefault("results", []).append((name, res))
            return {"text": final, "stand_in": False, "served_model": "scripted", "model": "scripted", "tool_calls": calls}

        monkeypatch.setattr(hub.model, "complete_with_tools", fake)
    return install


def runs_of(hub, cog, trigger):
    store = AgentStore(hub.root / "state" / "agents")
    return [r for r in store.runs(cog, limit=50) if r["trigger"] == trigger], store


def a_document(hub, title="Test procedure TP-9", by="carol"):
    content, mime = wopi.blank_document("docx")
    return hub.dms.create(title=title, doc_class="working", folder="Quality/Controlled Documents", by=by, content=content,
                          filename="tp9.docx", mime=mime, doc_type="document")


# ---------------------------------------------------------------- (a) a mention answer looks a record up and acts
def test_a_mention_answer_looks_a_record_up_and_acts_within_policy(released, monkeypatch):
    hub = released
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "Bent pins", "description": "connector J4"}, by="carol")
    task = hub.kernel.create("task", "Tasks", {"title": "Check connectors"}, by="carol")
    seen = {}
    scripted("quality-assistant", [("get_record", {"number": ncr["number"]}),
                                   ("comment", {"number": ncr["number"], "text": "Carol asked about this on the connector task."})],
             f"{ncr['number']} is open (Bent pins); I noted it there.", seen)(hub, monkeypatch)
    reply = hub.send("carol", "quality-assistant", "Status", f"@quality-assistant what is the status of {ncr['number']}?", record=task["locator"])
    assert reply["from"] == "quality-assistant" and f"{ncr['number']} is open" in reply["body"]
    assert {"get_record", "search_records", "comment"} <= set(seen["offered"]) and "finish" not in seen["offered"]
    assert "TOOLS IN THIS REPLY" in seen["system"] and "Tools you can call now" in seen["system"]
    got = dict(seen["results"])
    assert got["get_record"]["number"] == ncr["number"], "it read the record it was asked about"
    assert got["comment"].get("ok"), got["comment"]
    assert hub.inbox.on_record(ncr["locator"])[-1]["body"].startswith("Carol asked about this"), "it acted, as its procedures let it"
    [run], store = runs_of(hub, "quality-assistant", "answer")
    evs = store.load(run["run"])
    assert evs[0]["by"] == "carol", "it worked for the person who asked (both_may)"
    assert [e["tool"] for e in evs if e["type"] == "tool"] == ["get_record", "comment"] and evs[-1]["type"] == "finished"


def test_a_reply_that_calls_no_tools_leaves_no_run(released, monkeypatch):
    hub = released
    task = hub.kernel.create("task", "Tasks", {"title": "Check connectors"}, by="carol")
    scripted("quality-assistant", [], "Nothing to look up: it is a task.")(hub, monkeypatch)
    reply = hub.send("carol", "quality-assistant", "Q", "@quality-assistant what is this about?", record=task["locator"])
    assert reply["body"] == "Nothing to look up: it is a task."
    assert runs_of(hub, "quality-assistant", "answer")[0] == []


# ---------------------------------------------------------------- (b) what the policy refuses is refused there too
def test_a_write_the_policy_refuses_is_refused_in_a_mention_answer_and_the_reply_says_so(released, monkeypatch):
    hub = released
    task = hub.kernel.create("task", "Tasks", {"title": "Pay the supplier"}, by="carol")
    scripted("quality-assistant", [("start_process", {"process": "purchase-to-pay", "fields": {"supplier": "acme-metals"}}),
                                   ("message", {"to": "ap-reviewer", "body": "pay them"})],
             "I started the payment.")(hub, monkeypatch)
    reply = hub.send("carol", "quality-assistant", "Pay", "@quality-assistant could we pay acme for this?", record=task["locator"])
    assert "Not done (the hub refused): start_process" in reply["body"], "the hub, not the model, says what did not happen"
    [run], store = runs_of(hub, "quality-assistant", "answer")
    writes = [e for e in store.load(run["run"]) if e["type"] == "tool" and e.get("write")]
    assert [e["tool"] for e in writes] == ["start_process", "message"] and [e["ok"] for e in writes] == [False, False]
    assert all(e.get("decided") for e in writes), "each write was decided as in any agent run"
    assert not any(m["to"] == "ap-reviewer" and m["from"] == "quality-assistant" for m in hub.inbox.all())


def test_working_for_someone_the_cog_reads_and_writes_only_where_they_may(released, monkeypatch):
    hub = released
    # dave may not read Engineering/Designs; the cog may, but working for him it may not (both_may).
    design = hub.kernel.create("design", "Engineering/Designs", {"title": "secret bracket", "kind": "mechanical"}, by="alice")
    task = hub.kernel.create("task", "Tasks", {"title": "t"}, by="dave")
    seen = {}
    scripted("quality-assistant", [("get_record", {"number": design["number"]}), ("comment", {"number": design["number"], "text": "x"})],
             "done", seen)(hub, monkeypatch)
    hub.send("dave", "quality-assistant", "Q", "@quality-assistant what about the bracket?", record=task["locator"])
    got = dict(seen["results"])
    assert "error" in got["get_record"] and "error" in got["comment"], got
    assert hub.inbox.on_record(design["locator"]) == []


# ---------------------------------------------------------------- (c) a change-order reviewer never changes what it reviews
@pytest.fixture
def cc_hub(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    sopdocs.convert(h, by="carol")
    h.kernel.set_fields(sopdocs.in_force(h.kernel, "document-control-sop")["locator"], {"owner": "alice"}, by="setup")
    return h


ROUTE_OLD = "Approvals in document-control go to the reviewer."
ROUTE_NEW = "Approvals in document-control go to the quality manager."


def a_change_order(hub):
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Document approvals", "description": "why", "requested_by": "alice"}, by="alice")
    d = eco.add_sop(hub, co["number"], "document-control-sop", by="alice")
    write(hub, hub.kernel.get(d["locator"]), ROUTE_OLD, ROUTE_NEW, "alice")
    return hub.kernel.get(co["locator"]), hub.kernel.get(d["locator"])


def test_a_change_order_reviewer_may_look_but_not_write_to_its_change_order_or_drafts(cc_hub, monkeypatch):
    hub = cc_hub
    co, draft = a_change_order(hub)
    text_before = hub.kernel.get(draft["locator"])["fields"].get("text")
    seen = {}
    scripted("qa-manager", [("get_record", {"number": co["number"]}),
                            ("comment", {"number": co["number"], "text": "looks fine"}),
                            ("apply_document_ops", {"number": draft["number"], "ops": "@@ replace: quality manager\n@@ with: reviewer"}),
                            ("propose_policy_change", {"frame": "document-control-sop", "ops": "@@ replace: reviewer\n@@ with: x", "reason": "r"})],
             "- [advisory] general: the routing reads well", seen)(hub, monkeypatch)
    cc.request_review(hub, co["number"], "alice", panel=["qa-manager"])
    res = cc.current_round(hub.kernel.get(co["locator"]))["results"]["qa-manager"]
    got = dict(seen["results"])
    assert got["get_record"]["number"] == co["number"], "it looked at the change order"
    for tool in ("comment", "apply_document_ops", "propose_policy_change"):
        assert "a reviewer never changes" in got[tool].get("error", ""), (tool, got[tool])
    assert res["status"] == "done" and any("routing reads well" in f["text"] for f in res["findings"])
    assert "Not done (the hub refused)" in res["summary"] and res.get("tool_run")
    assert hub.kernel.get(draft["locator"])["fields"].get("text") == text_before, "the draft under review is unchanged"
    assert hub.inbox.on_record(co["locator"]) == [] or all(m["from"] != "qa-manager" for m in hub.inbox.on_record(co["locator"]))
    assert "qa-manager" not in cc.contributors(hub, co["number"]), "it did not become a contributor"
    evs = AgentStore(hub.root / "state" / "agents").load(res["tool_run"])
    assert [e["ok"] for e in evs if e["type"] == "tool"] == [True, False, False, False], "each refusal is on the run's Track"


def test_a_reviewer_that_became_a_contributor_during_its_review_is_not_counted(cc_hub, monkeypatch):
    hub = cc_hub
    co, draft = a_change_order(hub)

    def slips(**kw):  # stands for any way a tool run could make the reviewer part of the change order
        if kw.get("by") != "qa-manager":
            return {"text": "", "stand_in": True, "model": "stand-in", "tool_calls": []}
        kw["call_tool"]("get_record", {"number": co["number"]})
        hub.kernel.set_fields(co["locator"], {"retires": {"training-sop": {"document": "DOC-X", "by": "qa-manager", "reason": "r"}}}, by="qa-manager")
        return {"text": "- [advisory] general: fine", "stand_in": False, "model": "scripted", "tool_calls": []}

    monkeypatch.setattr(hub.model, "complete_with_tools", slips)
    cc.request_review(hub, co["number"], "alice", panel=["qa-manager"])
    res = cc.current_round(hub.kernel.get(co["locator"]))["results"]["qa-manager"]
    assert res["status"] == "failed" and res.get("excluded") and "became a contributor" in res["error"], res


# ---------------------------------------------------------------- (d) a document reviewer cannot edit what it reviews
def test_a_document_reviewer_looks_but_cannot_edit_the_document_under_review(released, monkeypatch):
    hub = released
    doc = a_document(hub)
    other = a_document(hub, title="Another procedure")
    before = hub.dms.content(doc["locator"])[1]["version"]
    seen = {}
    scripted("quality-assistant", [("read_document", {"number": doc["number"]}),
                                   ("apply_document_ops", {"number": doc["number"], "ops": "@@ insert at start\nReviewed."}),
                                   ("comment", {"number": doc["locator"], "text": "fixed it"}),
                                   ("get_record", {"number": other["number"]})],
             "VERDICT: pass\nThe procedure reads well.", seen)(hub, monkeypatch)
    reply = hub.send("carol", "quality-assistant", "Review", "@quality-assistant please review this document", record=doc["locator"])
    assert reply["body"].startswith(f"Review of {doc['number']}"), reply["body"]
    got = dict(seen["results"])
    assert "error" not in got["read_document"] and "error" not in got["get_record"], "it may look, at the document and elsewhere"
    for tool in ("apply_document_ops", "comment"):
        assert "a reviewer never changes what it reviews" in got[tool].get("error", ""), (tool, got[tool])
    assert hub.dms.content(doc["locator"])[1]["version"] == before, "the document under review is unchanged"
    assert "Not done (the hub refused)" in reply["body"]


def test_a_review_for_a_decision_only_looks(released, monkeypatch):
    from hubproto import review as review_mod
    from hubproto.cogs import RunContext

    hub = released
    doc = a_document(hub)
    seen = {}
    scripted("quality-assistant", [("comment", {"number": "TASK-1", "text": "x"})], "VERDICT: pass", seen)(hub, monkeypatch)
    b = hub.releases.bundle(hub.releases.current()["digest"])
    ctx = RunContext(run_id="t", release=hub.releases.current()["digest"], bundle=b, owner="quality-assistant", kernel=hub.kernel,
                     runner=hub.runner, inbox=hub.inbox, record=doc["locator"], dms=hub.dms, frames=lambda: hub.released_frames(),
                     model=hub.model, extras={"cog_profile": b["cogs"]["quality-assistant"]})
    review_mod.review("quality-assistant", b["cogs"]["quality-assistant"], ctx, hub.kernel.get(doc["locator"]), judgment=True)
    assert "comment" not in seen["offered"] and "get_record" in seen["offered"], "a judgment for an approval gets look tools only"
    assert "error" in dict(seen["results"])["comment"]


# ---------------------------------------------------------------- (e) the edit task looks before it returns its operations
def test_the_edit_task_can_look_before_it_returns_its_operations(released, monkeypatch):
    hub = released
    doc = a_document(hub)
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "Bent pins", "description": "connector J4"}, by="carol")
    seen = {}
    scripted("quality-assistant", [("get_record", {"number": ncr["number"]}),
                                   ("apply_document_ops", {"number": doc["number"], "ops": "@@ insert at start\nsneaked in"})],
             f"@@ insert at start\nSee {ncr['number']} (Bent pins).", seen)(hub, monkeypatch)
    reply = hub.send("carol", "quality-assistant", "Edit", f"@quality-assistant please insert a line at the start naming {ncr['number']}",
                     record=doc["locator"])
    got = dict(seen["results"])
    assert got["get_record"]["number"] == ncr["number"], "it looked the record up first"
    assert "error" in got["apply_document_ops"], "the edit is the operations it returns, not a tool write on the document"
    edit = hub.inbox.find(reply["id"]).get("edit") or {}
    assert edit.get("ops") == [{"op": "insert", "at": "start", "lines": [f"See {ncr['number']} (Bent pins)."]}], edit
    assert edit.get("asked_by") == "carol"


# ---------------------------------------------------------------- (f) the hub's own model steps get no tools
def test_the_automation_model_steps_get_no_tools(monkeypatch):
    assert {k: set(v) for k, v in automation.MODEL_STEPS.items()} == {
        "compile-frame": {"Compile the Frame", "Draft prose for direct changes", "Suggest the panel"},
        "release-change-order": {"Send related changes back"}}, "no new model step, and none with tools"
    # The functions behind those steps, and the one-word classifier of a mention, ask the model with complete() only.
    for fn in (llm_extract.gateway_call, prose_drafting._writer, cc._panel_by_model, cc._meaning, mentions.intent_of):
        src = inspect.getsource(fn)
        assert ".complete(" in src and "complete_with_tools" not in src and "tool_turn" not in src and "site_tools" not in src, fn.__name__

    class NoTools:
        calls = []

        def available(self, m):
            return True

        def complete(self, **kw):
            NoTools.calls.append(kw)
            return {"text": "ANSWER"}

        def complete_with_tools(self, **kw):
            raise AssertionError("the classifier was offered tools")

    assert mentions.intent_of("What do you make of the scope?", model=NoTools(), model_name="x") == "message" and NoTools.calls


# ---------------------------------------------------------------- review findings on PR #97
def test_a_protected_record_cannot_be_written_under_another_spelling(released):
    """The tools find a number in upper case ("TAſK-0001" is TASK-0001 there): the protection is checked on the record
    the lookup resolves, whatever spelling named it."""
    hub = released
    task = hub.kernel.create("task", "Tasks", {"title": "Review me"}, by="carol")
    t = AgentTools(hub, "quality-assistant", "spell", "derived", 9, AgentStore(hub.root / "state" / "agents"), requester="carol", goal="g")
    t.protect([task["locator"], task["number"]], "you are reviewing it")
    for spelling in (task["number"], task["number"].replace("S", "ſ"), task["number"].lower(), f"  {task['number'].swapcase()}\t"):
        res = t.execute("comment", {"number": spelling, "text": "changed while reviewing"})
        assert res.get("error") == "you are reviewing it", (spelling, res)
    assert hub.inbox.on_record(task["locator"]) == [], "no spelling got a comment onto the protected record"


def test_the_record_a_write_resolves_is_checked_even_when_its_words_are_not(released, monkeypatch):
    """The second line: whatever the arguments say, a write whose tool resolves a protected record is refused."""
    hub = released
    task = hub.kernel.create("task", "Tasks", {"title": "Review me"}, by="carol")
    other = hub.kernel.create("task", "Tasks", {"title": "Another"}, by="carol")
    t = AgentTools(hub, "quality-assistant", "resolve", "derived", 9, AgentStore(hub.root / "state" / "agents"), requester="carol", goal="g")
    t.protect([task["locator"]], "you are reviewing it")
    real = AgentTools._find
    monkeypatch.setattr(AgentTools, "_find", lambda self, n: real(self, task["number"] if n == "ALIAS" else n))
    assert t.execute("comment", {"number": "ALIAS", "text": "x"}).get("error") == "you are reviewing it"
    assert t.execute("comment", {"number": other["number"], "text": "fine"}).get("ok"), "another record is not protected"
    assert hub.inbox.on_record(task["locator"]) == []


def test_a_change_order_reviewer_works_for_the_person_who_asked_for_the_round(cc_hub, monkeypatch):
    hub = cc_hub
    co, _ = a_change_order(hub)
    seen = {}
    scripted("qa-manager", [], "- [advisory] general: fine", seen)(hub, monkeypatch)
    cc.request_review(hub, co["number"], "alice", panel=["qa-manager"])
    assert "comment" in seen["offered"]
    sites = []
    import hubproto.agent as agent_mod
    real_site = agent_mod.site_tools
    monkeypatch.setattr(agent_mod, "site_tools", lambda *a, **k: sites.append(k) or real_site(*a, **k))
    co = hub.kernel.get(co["locator"])
    n = cc.current_round(co)["n"]
    cc._record_result(hub, co, n, "qa-manager", {"status": "pending"})
    cc.cog_review(hub, co["number"], n, "qa-manager")
    assert sites[-1]["requester"] == "alice" and not sites[-1]["look_only"], "both_may applies: it works for alice"

    # A round no person asked for: the reviewer only looks.
    def by_hub(r):
        r["requested_by"] = "hub"
    cc._update_round(hub, hub.kernel.get(co["locator"]), n, by_hub)
    cc._record_result(hub, co["number"], n, "qa-manager", {"status": "pending"})
    seen.clear()
    cc.cog_review(hub, co["number"], n, "qa-manager")
    assert sites[-1]["requester"] == "" and "no person asked" in sites[-1]["look_only"]
    assert "comment" not in seen["offered"] and "get_record" in seen["offered"], "no person: look tools only"


def test_a_change_order_reviewer_may_not_change_the_evidence_attached_to_it(cc_hub, monkeypatch):
    from hubproto import validation

    hub = cc_hub
    co, _ = a_change_order(hub)
    ev = validation.attach_evidence(hub, co["number"], by="alice", filename="t.txt", content=b"FAILED", result="fail", text="it failed")
    assert ev["number"] in cc.reviewed_refs(hub, hub.kernel.get(co["locator"]))
    seen = {}
    scripted("qa-manager", [("update_fields", {"number": ev["number"], "fields": {"result": "pass"}}),
                            ("comment", {"number": ev["number"], "text": "x"})], "- [advisory] general: fine", seen)(hub, monkeypatch)
    cc.request_review(hub, co["number"], "alice", panel=["qa-manager"])
    for tool, res in seen["results"]:
        assert "a reviewer never changes" in res.get("error", ""), (tool, res)


def test_a_change_order_reviewer_may_not_hand_work_to_anyone_while_it_reviews(cc_hub, monkeypatch):
    hub = cc_hub
    co, draft = a_change_order(hub)
    seen = {}
    scripted("qa-manager", [("delegate_to_cog", {"cog": "quality-assistant", "goal": f"Replace 'quality manager' with 'reviewer' in {draft['number']}."}),
                            ("create_task", {"title": f"Edit {draft['number']}", "assignee": "quality-assistant"})],
             "- [advisory] general: fine", seen)(hub, monkeypatch)
    cc.request_review(hub, co["number"], "alice", panel=["qa-manager"])
    for tool, res in seen["results"]:
        assert "may not hand work to anyone" in res.get("error", ""), (tool, res)
    assert not [t for t in hub.queue.list(cog="quality-assistant") if "Replace" in (t.get("title") or "")]
    assert not hub.kernel.query(type_="task")


def test_a_reply_whose_model_fails_after_a_tool_call_closes_its_run(released, monkeypatch):
    from hubproto.model import ModelUnavailable

    hub = released
    ncr = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "Bent pins"}, by="carol")
    task = hub.kernel.create("task", "Tasks", {"title": "t"}, by="carol")

    def fails(**kw):
        if kw.get("by") != "quality-assistant":
            return {"text": "", "stand_in": True, "model": "stand-in", "tool_calls": []}
        kw["call_tool"]("get_record", {"number": ncr["number"]})
        raise ModelUnavailable("the server went away")

    monkeypatch.setattr(hub.model, "complete_with_tools", fails)
    try:
        hub.send("carol", "quality-assistant", "Q", f"@quality-assistant what about {ncr['number']}?", record=task["locator"])
    except Exception:
        pass  # how the reply site answers a failed model is unchanged; its tool run must not stay open
    [run], store = runs_of(hub, "quality-assistant", "answer")
    end = store.load(run["run"])[-1]
    assert end["type"] == "finished" and end["status"] == "unavailable" and "went away" in end["error"], end


def test_an_edit_whose_model_errors_after_proposing_closes_its_run_as_proposed(released, monkeypatch):
    from hubproto.agent import close_site, site_tools, tool_turn

    hub = released
    doc = a_document(hub)
    site = site_tools(hub, "quality-assistant", trigger="edit", goal="g", requester="carol")
    site["tools"].proposed.append({"tool": "comment", "arguments": {}, "confirmer": "person"})

    def boom(**kw):
        kw["call_tool"]("get_record", {"number": doc["number"]})
        raise RuntimeError("bad answer")

    monkeypatch.setattr(hub.model, "complete_with_tools", boom)
    with pytest.raises(RuntimeError):
        tool_turn(hub.model, site, model="x", system="s", user="u", by="quality-assistant", max_tokens=10)
    end = site["store"].load(site["run"])[-1]
    assert end["type"] == "finished" and end["status"] == "proposed" and "RuntimeError: bad answer" in end["error"], end
    assert close_site(site, {"text": "late"}) == "", "a run is closed once"


def test_a_long_prompt_is_trimmed_in_its_middle_and_the_tools_count(monkeypatch):
    import io
    import json
    import urllib.error

    from hubproto import model as model_mod

    head, tail = "WHO YOU ARE: the head " + "h" * 500, "OUTPUT FORMAT and the rule about untrusted text " + "t" * 500
    system = head + "m" * 20000 + tail
    cut = model_mod.fit_prompt(system, 4000)
    assert cut.startswith(head) and cut.endswith(tail) and "context trimmed" in cut and len(cut) <= 4000 + 10

    monkeypatch.setenv("HUBPROTO_MODEL_MAX_CHARS", "12000")
    monkeypatch.delenv("HUBPROTO_MODEL_THINK", raising=False)
    monkeypatch.setattr(model_mod, "ollama_url", lambda: "http://ollama.invalid")
    tools = [{"name": f"tool{i}", "description": "d" * 400, "parameters": {"type": "object", "properties": {}}} for i in range(10)]
    sent = []

    def fetch(req, timeout):
        body = json.loads(req.data)
        sent.append(body)
        if "think" in body:
            raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(b'{"error": "model does not support think"}'))
        return {"message": {"content": "the answer"}}

    monkeypatch.setattr(model_mod, "_fetch_json", fetch)
    text, _ = model_mod.ModelGateway._ollama_tools(None, "ollama/x", system, "the question", None, tools, lambda n, a: {}, 100, 2, str)
    assert text == "the answer" and "think" in sent[0] and "think" not in sent[1], "a model that refuses the flag is asked again without it"
    sys_sent = sent[1]["messages"][0]["content"]
    assert sys_sent.startswith(head) and sys_sent.endswith(tail), "the head and the tail are never cut"
    assert len(sys_sent) + len("the question") + len(json.dumps(sent[1]["tools"])) <= 12000, "the tool definitions count"
