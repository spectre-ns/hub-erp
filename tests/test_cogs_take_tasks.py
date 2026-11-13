"""Any cog can be assigned a task (issue #73): the tasks SOP lets the cogs group complete and read tasks, and nothing
more; a cog still writes only where it and the person it works for may."""

import pytest

from hubproto import authz


def cogs_of(hub):
    return sorted(n for n, m in hub.current_bundle()["members"].items() if m.get("type") == "cog")


def test_every_cog_of_the_example_company_may_complete_a_task(released):
    """The org model's cogs are in the cogs group; the stores and purchasing-assistant cogs (defined in the inventory
    and purchasing policies) are staff. Either way every cog may complete the task assigned to it."""
    bundle = released.current_bundle()
    for cog in cogs_of(released):
        assert authz.check(bundle, cog, "approve", "task", "Tasks")["allowed"], f"{cog} could not be given a task"


@pytest.mark.parametrize("cog", ["auditor", "it-admin", "management", "ap-reviewer", "organization", "doc-reviewer",
                                 "senior-organization", "senior-qa-manager", "quality-assistant", "qa-manager", "purchasing-assistant",
                                 "stores"])
def test_a_task_filed_for_any_cog_waits_for_that_cog(released, cog):
    released.queue.mode = "service"  # the cog takes its turn later, from its queue: here we look at where the task waits
    run = released.runner.submit("task", {"record": {"title": f"Work for {cog}", "description": "Do the work, or say why you cannot.",
                                                     "assignee": cog}}, owner="bob")
    state = released.runner.load(run)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == [cog], state.get("failure") or state.get("pending")


def test_the_cogs_group_gets_tasks_and_nothing_else(released):
    bundle = released.current_bundle()
    granted = [r for r in bundle["permissions"] if r.get("subject") == "group:cogs"]
    assert sorted((r["action"], r["object"], r.get("folder") or r.get("resource")) for r in granted) == sorted(
        [("approve", "task", r.get("folder") or r.get("resource")) for r in granted if r["action"] == "approve"]
        + [("read", r["object"], r.get("folder") or r.get("resource")) for r in granted if r["action"] == "read"])
    assert {r["action"] for r in granted} == {"approve", "read"} and len(granted) == 2
    # The auditor, now able to take a task, is not made staff: it still may not create a change order or a part.
    assert not authz.check(bundle, "auditor", "create", "change-order", "Quality/Changes")["allowed"]
    assert not authz.check(bundle, "auditor", "create", "part", "Inventory/Parts")["allowed"]


def test_the_auditor_working_for_bob_may_draft_in_quality_audits(released):
    """TASK-0002's audit plan: the auditor cog and bob (an internal auditor) may both create documents there."""
    bundle = released.current_bundle()
    for who in ("auditor", "bob"):
        assert authz.check(bundle, who, "create", "document", "Quality/Audits")["allowed"], who
