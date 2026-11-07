"""Record and admin tools for cogs working on their own (hubproto/agent_records.py): create records (through their
process when one files them), update fields, move, assign, link, send back to the start, file tasks, hand work to
another cog and check a permission, each refused where the policy, the kernel or the item's state says no."""

import copy
import shutil

import pytest

from conftest import ROOT, release_all
from hubproto.agent import Agent, AgentStore, AgentTools
from hubproto.app import Hub
from hubproto.cogs import RunContext, capabilities_text

TOOLS = ["create_record", "update_fields", "move_record", "assign", "link_records", "send_back_to_start", "create_task",
         "delegate_to_cog", "check_permission"]


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    root = tmp_path_factory.mktemp("hub")
    shutil.copytree(ROOT / "frames", root / "frames")
    h = Hub(root)
    h.kernel.seed()
    release_all(h)
    return h


@pytest.fixture
def queued(hub):
    """Nobody works the queue during the test (as when the worker is behind)."""
    hub.queue.mode = "service"
    yield hub
    for t in hub.queue.open_tasks():
        hub.queue.cancel(t["id"], "test")
    hub.queue.mode = "inline"


def tools_for(hub, cog="quality-assistant", mode="act", budget=8):
    return AgentTools(hub, cog, f"t-{cog}-{mode}", mode, budget, AgentStore(hub.root / "state" / "agents"), requester="carol")


def new_ncr(t, title="Burr on bracket"):
    out = t.execute("create_record", {"type": "ncr", "fields": {"title": title, "description": "Burr found at inspection", "source": "inspection"}})
    assert out.get("ok"), out
    return out


# ---------------------------------------------------------------- create_record
def test_create_record_starts_the_process_that_files_the_type(hub):
    t = tools_for(hub)
    out = new_ncr(t)
    rec = hub.kernel.get(f"rec:ncr:{out['number']}")
    assert out["process"] == "nonconformance" and rec["created_by"] == "quality-assistant" and rec["folder"] == "Quality/Nonconformances"
    assert hub.runner.run_for_record(rec["locator"])["owner"] == "quality-assistant"
    # Object names work too, and the process's own checks apply.
    by_object = t.execute("create_record", {"type": "nonconformances", "fields": {"title": "x"}})
    assert "error" in by_object and "needs description" in by_object["error"]
    assert "no record type" in t.execute("create_record", {"type": "spaceship", "fields": {}})["error"]
    assert "does not file" in t.execute("create_record", {"type": "ncr", "process": "task", "fields": {}})["error"]


def test_create_record_without_a_process_needs_the_permission(hub):
    t = tools_for(hub)
    refused = t.execute("create_record", {"type": "asset", "folder": "Quality", "fields": {"name": "Torque wrench"}})
    assert "error" in refused and "no rule" in refused["error"]
    assert "no folder" in t.execute("create_record", {"type": "asset", "folder": "Nowhere", "fields": {}})["error"]
    assert "say which folder" in t.execute("create_record", {"type": "asset", "fields": {}})["error"]
    t.bundle = copy.deepcopy(t.bundle)  # a policy that lets staff file assets in Quality
    t.bundle["permissions"].append({"kind": "permission", "subject": "role:staff", "effect": "allow", "action": "create", "object": "asset",
                                    "resource": "folder:Quality", "source": {"frame": "test", "line": 1, "text": "Staff may create assets in Quality."}})
    out = t.execute("create_record", {"type": "asset", "folder": "Quality", "fields": {"name": "Torque wrench"}})
    assert out["ok"] and hub.kernel.get(f"rec:asset:{out['number']}")["folder"] == "Quality" and "Staff may create assets" in out["why"]


# ---------------------------------------------------------------- update_fields
def test_update_fields_sets_what_the_cog_may_change_and_nothing_managed(hub):
    t = tools_for(hub)
    n = new_ncr(t, "Scratch on cover")["number"]
    out = t.execute("update_fields", {"number": n, "fields": {"lot": "L-42", "quantity": 12}})
    assert out["ok"] and hub.kernel.get(f"rec:ncr:{n}")["fields"]["lot"] == "L-42"
    assert "managed by the hub" in t.execute("update_fields", {"number": n, "fields": {"owner": "carol", "risk": "low"}})["error"]
    guarded = t.execute("update_fields", {"number": n, "fields": {"source": "rumour"}})
    assert "field-enum" in guarded["error"]
    assert "no record" in t.execute("update_fields", {"number": "NCR-9999", "fields": {"lot": "x"}})["error"]
    assert "say which fields" in t.execute("update_fields", {"number": n, "fields": {}})["error"]


def test_update_fields_is_refused_without_permission(hub):
    t = tools_for(hub, cog="quality-assistant")
    doc = hub.dms.create(title="Carol's SOP", doc_class="controlled", folder="Quality/Controlled Documents", by="carol",
                         content=b"# SOP\n", filename="sop.md", mime="text/markdown", doc_type="sop")
    out = t.execute("update_fields", {"number": doc["number"], "fields": {"title": "Renamed"}})
    assert "error" in out and "no rule" in out["error"]


# ---------------------------------------------------------------- move_record
def test_move_record_needs_a_folder_the_policy_allows_and_no_running_process(hub):
    t = tools_for(hub)
    in_run = new_ncr(t, "Moving target")["number"]
    assert "is in a nonconformance run" in t.execute("move_record", {"number": in_run, "folder": "Quality/CAPA"})["error"]
    loose = hub.kernel.create("ncr", "Quality/Nonconformances", {"title": "Loose record"}, by="carol")
    assert "no rule" in t.execute("move_record", {"number": loose["number"], "folder": "Quality/CAPA"})["error"]
    assert "no folder" in t.execute("move_record", {"number": loose["number"], "folder": "Nowhere"})["error"]
    assert "already in" in t.execute("move_record", {"number": loose["number"], "folder": "Quality/Nonconformances"})["error"]
    t.bundle = copy.deepcopy(t.bundle)
    t.bundle["permissions"].append({"kind": "permission", "subject": "role:staff", "effect": "allow", "action": "create", "object": "nonconformance",
                                    "resource": "folder:Quality/CAPA", "source": {"frame": "test", "line": 1, "text": "t"}})
    out = t.execute("move_record", {"number": loose["number"], "folder": "Quality/CAPA", "reason": "belongs with its CAPA"})
    assert out["ok"] and out["to"] == "Quality/CAPA" and hub.kernel.get(loose["locator"])["folder"] == "Quality/CAPA"


# ---------------------------------------------------------------- assign
def test_assign_to_a_person_or_a_cog(queued):
    hub = queued
    t = tools_for(hub)
    n = new_ncr(t, "Assign me")["number"]
    loc = f"rec:ncr:{n}"
    out = t.execute("assign", {"number": n, "to": "carol", "instruction": "please disposition this"})
    assert out["ok"] and out["notified"] and "carol" in hub.kernel.get(loc)["assignees"]
    assert any(m["to"] == "carol" and m["from"] == "quality-assistant" and m["record"] == loc for m in hub.inbox.on_record(loc))
    assert "say what qa-manager should do" in t.execute("assign", {"number": n, "to": "qa-manager"})["error"]
    out = t.execute("assign", {"number": n, "to": "qa-manager", "instruction": "review the containment"})
    assert out["ok"] and out["queued"]
    [task] = hub.queue.open_tasks(cog="qa-manager")
    assert task["kind"] == "assigned" and task["by"] == "quality-assistant" and task["payload"]["instruction"] == "review the containment"
    assert "to yourself" in t.execute("assign", {"number": n, "to": "quality-assistant"})["error"]
    assert "not a member" in t.execute("assign", {"number": n, "to": "zed"})["error"]


def test_assign_is_refused_on_someone_elses_item(hub):
    t = tools_for(hub)
    doc = hub.dms.create(title="Not mine", doc_class="controlled", folder="Quality/Controlled Documents", by="carol",
                         content=b"# Not mine\n", filename="nm.md", mime="text/markdown", doc_type="sop")
    out = t.execute("assign", {"number": doc["number"], "to": "bob", "instruction": "x"})
    assert "only its owner" in out["error"]


# ---------------------------------------------------------------- link_records
def test_link_records_within_the_vocabulary(hub):
    t = tools_for(hub)
    a = new_ncr(t, "Link from")["number"]
    b = new_ncr(t, "Link to")["number"]
    out = t.execute("link_records", {"number": a, "rel": "concerns", "target": b})
    assert out["ok"] and out["inverse"] == f"{b} concerned-by {a}"
    assert any(l["rel"] == "concerns" for l in hub.kernel.get(f"rec:ncr:{a}")["links"])
    assert "already" in t.execute("link_records", {"number": a, "rel": "concerns", "target": b})["error"]
    assert "unknown link" in t.execute("link_records", {"number": a, "rel": "loves", "target": b})["error"]
    assert "no record" in t.execute("link_records", {"number": a, "rel": "concerns", "target": "BILL-0001"})["error"]


# ---------------------------------------------------------------- send_back_to_start
def test_send_back_to_start_for_items_the_cog_may_restart(hub):
    t = tools_for(hub)
    n = new_ncr(t, "Start over")["number"]
    assert "say why" in t.execute("send_back_to_start", {"number": n})["error"]
    out = t.execute("send_back_to_start", {"number": n, "reason": "the description was wrong"})
    assert out["ok"] and out["state"] == "open"
    doc = hub.dms.create(title="Carol's other SOP", doc_class="controlled", folder="Quality/Controlled Documents", by="carol",
                         content=b"# SOP\n", filename="sop2.md", mime="text/markdown", doc_type="sop")
    assert "only carol" in t.execute("send_back_to_start", {"number": doc["number"], "reason": "x"})["error"]


# ---------------------------------------------------------------- create_task
def test_create_task_for_a_person_or_a_cog(queued):
    hub = queued
    t = tools_for(hub)
    out = t.execute("create_task", {"title": "Check the gauge", "description": "calibration due", "assignee": "carol", "due": "2026-12-01"})
    assert out["ok"] and out["waiting_for"] == ["carol"]
    rec = hub.kernel.get(f"rec:task:{out['number']}")
    assert rec["fields"]["due"] == "2026-12-01" and rec["created_by"] == "quality-assistant"
    to_cog = t.execute("create_task", {"title": "Summarise open NCRs", "assignee": "qa-manager"})
    assert to_cog["ok"] and to_cog["waiting_for"] == ["qa-manager"]
    assert any(x["kind"] == "gate" for x in hub.queue.open_tasks(cog="qa-manager"))
    assert "needs a title" in t.execute("create_task", {"title": " "})["error"]
    assert "not a member" in t.execute("create_task", {"title": "x", "assignee": "zed"})["error"]
    bad = t.execute("create_task", {"title": "x", "due": "next week"})
    assert "error" in bad and "date" in bad["error"]
    # A task's due date is a guarded field for update_fields too.
    assert "field-date" in t.execute("update_fields", {"number": out["number"], "fields": {"due": "soon"}})["error"]


# ---------------------------------------------------------------- delegate_to_cog
def test_delegate_to_another_cog_queues_once(queued):
    hub = queued
    t = tools_for(hub)
    out = t.execute("delegate_to_cog", {"cog": "qa-manager", "goal": "draft a calibration work instruction"})
    assert out["ok"] and out["status"] == "queued"
    again = t.execute("delegate_to_cog", {"cog": "qa-manager", "goal": "draft a calibration work instruction"})
    assert again["task"] == out["task"]
    n = new_ncr(t, "Delegated item")["number"]
    about = t.execute("delegate_to_cog", {"cog": "qa-manager", "goal": "check the containment", "number": n})
    task = hub.queue.get(about["task"])
    assert task["kind"] == "assigned" and task["record"] == f"rec:ncr:{n}"
    assert "to yourself" in t.execute("delegate_to_cog", {"cog": "quality-assistant", "goal": "x"})["error"]
    assert "not a cog" in t.execute("delegate_to_cog", {"cog": "carol", "goal": "x"})["error"]
    assert "say what" in t.execute("delegate_to_cog", {"cog": "qa-manager", "goal": ""})["error"]


# ---------------------------------------------------------------- check_permission
def test_check_permission_quotes_the_deciding_sentence(hub):
    t = tools_for(hub)
    calls_before = t.actions
    yes = t.execute("check_permission", {"action": "create", "object": "nonconformance", "folder": "Quality/Nonconformances"})
    assert yes["allowed"] and yes["principal"] == "quality-assistant" and "nonconformances" in yes["rule"]["text"].lower()
    no = t.execute("check_permission", {"action": "create", "object": "bill", "folder": "Finance/Payables", "principal": "carol"})
    assert not no["allowed"] and no["rule"]["text"] == "Quality managers may not create bills in Finance/Payables."
    none = t.execute("check_permission", {"action": "approve", "object": "bill", "folder": "Finance/Payables"})
    assert not none["allowed"] and none["rule"] is None and "no rule" in none["why"]
    assert "say the action" in t.execute("check_permission", {"action": "create"})["error"]
    assert t.actions == calls_before  # looking costs nothing from the budget


# ---------------------------------------------------------------- autonomy and budget
def test_propose_mode_records_the_action_and_read_mode_refuses_it(hub):
    t = tools_for(hub, mode="propose")
    out = t.execute("create_task", {"title": "Proposed task"})
    assert out["proposed"] and t.proposed[-1]["tool"] == "create_task"
    assert not [r for r in hub.kernel.query(type_="task") if r["fields"].get("title") == "Proposed task"]
    r = tools_for(hub, mode="read")
    assert "may only read" in r.execute("create_task", {"title": "x"})["error"]
    assert r.execute("check_permission", {"action": "read", "object": "*", "folder": "Quality"})["allowed"]


def test_the_budget_counts_these_tools(hub):
    t = tools_for(hub, budget=1)
    assert t.execute("create_task", {"title": "Within budget"})["ok"]
    assert "budget" in t.execute("create_task", {"title": "Over budget"})["error"]


# ---------------------------------------------------------------- disclosure
def test_the_cog_is_told_about_these_tools(hub):
    bundle = hub.releases.bundle(hub.releases.current()["digest"])
    ctx = RunContext(run_id="t", release="", bundle=bundle, owner="quality-assistant", kernel=hub.kernel,
                     extras={"cog_profile": bundle["cogs"]["quality-assistant"]})
    text = capabilities_text(ctx)
    for name in TOOLS:
        assert f"- {name}:" in text, name
    prep = Agent(hub, "quality-assistant").prepare("look around", by="carol")
    names = [s["name"] for s in prep["specs"]]
    assert set(TOOLS) <= set(names) and names[-1] == "finish"
