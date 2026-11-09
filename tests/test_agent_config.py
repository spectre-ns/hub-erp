"""Configuration tools for cogs: a cog proposes a policy change, a model for a cog or a new member; the hub compiles
the proposal and the policy owners decide. Nothing a cog does here changes the policy in force."""

import pytest

from hubproto.agent import AgentStore, AgentTools, act_specs, look_specs


@pytest.fixture
def tools(released):
    hub = released
    return AgentTools(hub, "qa-manager", "t-config", "act", 8, AgentStore(hub.root / "state" / "agents"), requester="carol")


def digest(hub):
    return hub.releases.current()["digest"]


def test_a_policy_change_becomes_an_open_proposal_and_nothing_changes_in_force(released, tools):
    hub = released
    before, path = digest(hub), hub.frames.get("finance-policy").path
    on_disk = path.read_text()
    out = tools.execute("propose_policy_change", {"frame": "finance-policy", "reason": "invoices have grown; fewer escalations",
                                                  "ops": "@@ replace: The controller approval threshold is 10,000 USD.\n"
                                                         "@@ with: The controller approval threshold is 20,000 USD."})
    assert out["ok"] and out["proposal"] and not out["errors"] and out["reviewers"] == ["bob", "carol"]
    # The threshold widening, plus (since governance-8) the Frame text change itself.
    assert "change threshold controller-approval-threshold = 20000.0 USD (value: 10000.0 -> 20000.0)" in out["widenings"], out
    assert "policy owners" in out["note"]
    p = hub.proposals.load(out["proposal_id"])
    assert p["status"] == "open" and "finance-policy" in p["overrides"] and "20,000 USD" in p["overrides"]["finance-policy"]["text"]
    assert path.read_text() == on_disk and digest(hub) == before  # the file and the release in force are untouched
    draft = hub.kernel.get(p["overrides"]["finance-policy"]["draft"])
    assert draft["fields"]["owner"] == "qa-manager" and draft["fields"]["doc_type"] == "frame-draft"
    notes = [m for m in hub.inbox.all() if m.get("kind") == "policy-proposal"]
    assert {m["to"] for m in notes} == {"bob", "carol"} and "invoices have grown" in notes[-1]["body"]
    lower = tools.execute("propose_policy_change", {"frame": "finance-policy", "reason": "tighter control",
                                                    "ops": "@@ replace: is 20,000 USD.\n@@ with: is 5,000 USD."})
    # The cog's draft carries forward (10,000 -> 5,000 against the release in force). Since governance-2/8 any changed
    # threshold, and any Frame text a cog follows, is reviewed by both owners: a threshold can cut either way.
    assert lower["ok"] and "change threshold controller-approval-threshold = 5000.0 USD (value: 10000.0 -> 5000.0)" in lower["widenings"]
    out = lower
    listed = tools.execute("list_proposals", {})
    row = next(r for r in listed["open"] if r["proposal_id"] == out["proposal_id"])
    assert row["frames"] == ["finance-policy"] and row["still_need_decisions"] and row["errors"] == 0


def test_a_change_that_does_not_parse_is_reported_as_an_error(released, tools):
    out = tools.execute("propose_policy_change", {"frame": "finance-policy", "reason": "simpler wording",
                                                  "ops": "@@ replace: The controller approval threshold is 10,000 USD.\n"
                                                         "@@ with: The controller approval threshold may be about ten thousand dollars."})
    assert out["ok"] and out["blocked"] and not out["errors"] and out["unread"] and "reword" in out["note"]
    assert digest(released) == released.releases.current()["digest"]


def test_bad_requests_are_refused(released, tools):
    assert "no policy Frame 'no-such-policy'" in tools.execute("propose_policy_change", {"frame": "no-such-policy", "reason": "x", "ops": "@@ delete: a"})["error"]
    assert "say why" in tools.execute("propose_policy_change", {"frame": "finance-policy", "ops": "@@ delete: a"})["error"]
    assert "changed nothing" in tools.execute("propose_policy_change", {"frame": "finance-policy", "reason": "x", "ops": "@@ delete: not in the frame"})["error"]
    out = tools.execute("propose_member", {"kind": "person", "name": "erin", "roles": ["wizard"]})
    assert "no such role: wizard" in out["error"]
    assert "already uses" in tools.execute("propose_cog_model", {"cog": "quality-assistant", "model": "ollama/qwen3:14b"})["error"]
    assert "not a cog" in tools.execute("propose_cog_model", {"cog": "bob", "model": "ollama/qwen3:8b"})["error"]


def test_a_model_the_hub_cannot_reach_is_refused_and_one_it_can_is_proposed(released, tools, monkeypatch):
    before = digest(released)
    monkeypatch.delenv("HUBPROTO_ALLOW_STAND_IN", raising=False)
    monkeypatch.delenv("HUBPROTO_OLLAMA_URL", raising=False)
    out = tools.execute("propose_cog_model", {"cog": "quality-assistant", "model": "ollama/qwen3:8b"})
    assert "cannot be used here" in out["error"]
    monkeypatch.setenv("HUBPROTO_ALLOW_STAND_IN", "1")
    out = tools.execute("propose_cog_model", {"cog": "quality-assistant", "model": "ollama/qwen3:8b", "reason": "faster answers"})
    assert out["ok"] and out["proposal"] and out["changes"] >= 1 and digest(released) == before
    assert "qwen3:8b" in released.proposals.load(out["proposal_id"])["overrides"]["cog-profiles"]["text"]


def test_a_new_member_is_proposed_not_added(released, tools):
    out = tools.execute("propose_member", {"kind": "person", "name": "erin", "roles": ["reviewer", "staff"], "reason": "new QA engineer"})
    assert out["ok"] and out["proposal"] and out["widenings"]
    assert "erin" not in released.releases.bundle(digest(released))["members"]
    out2 = tools.execute("propose_member", {"kind": "cog", "name": "calibration-assistant", "roles": ["staff"], "model": "ollama/qwen3:14b",
                                            "prompt": "You keep calibration records.", "follows": ["calibration-sop"], "autonomy": "propose"})
    assert out2["ok"] and "calibration-assistant" in released.proposals.load(out2["proposal_id"])["overrides"]["cog-profiles"]["text"]


def test_in_propose_mode_the_change_waits_for_a_person(released):
    t = AgentTools(released, "doc-reviewer", "t-propose", "propose", 5, AgentStore(released.root / "state" / "agents"), requester="carol")
    out = t.execute("propose_policy_change", {"frame": "finance-policy", "reason": "x", "ops": "@@ replace: 10,000 USD\n@@ with: 12,000 USD"})
    assert out["proposed"] and not [p for p in released.proposals.list() if p.get("status") == "open"]


def test_the_tools_are_offered_and_disclosed(released, tools):
    names = {t["name"] for t in act_specs("act")} | {t["name"] for t in look_specs()}
    assert {"propose_policy_change", "propose_cog_model", "propose_member", "list_proposals"} <= names
    assert "propose_policy_change" not in {t["name"] for t in act_specs("read")}
    from hubproto.cogs import cog_context

    text = cog_context(tools._ctx(None), None)
    for n in ("propose_policy_change", "propose_cog_model", "propose_member", "list_proposals"):
        assert n in text
