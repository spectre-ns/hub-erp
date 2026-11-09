"""Settings: rules edited as data (no SOP sentences, no Python), each change a proposal the policy owners release;
how each rule reads; and the running limits on a cog's rails (at most N approvals a day, at most an amount a month)."""

import shutil

import pytest

from conftest import ROOT, SMALL_BILL, dated, release_all
from hubproto.app import Hub
from hubproto.compile import CompileError
from hubproto.rules import rule_key
from hubproto.settings import sentence


@pytest.fixture
def hub(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    return h


def bundle(h):
    return h.releases.bundle(h.releases.current()["digest"])


def find(rules, **kw):
    return next(r for r in rules if all(r.get(k) == v for k, v in kw.items()))


_po = [3100]


def bill(h, amount=1500):
    _po[0] += 1
    h.kernel.append("po.issued", f"po:PO-{_po[0]}", {"supplier": "acme-metals", "amount": amount, "currency": "USD", "description": "[settings]",
                                                      "received": True}, by="seed")
    h.kernel._cache = None
    return {**dated(SMALL_BILL), "amount": amount, "po_ref": f"PO-{_po[0]}"}


def test_rules_read_as_the_sentences_people_wrote(hub):
    procs = bundle(hub)["processes"]
    rules = hub.settings_rules()
    same = 0
    checked = 0
    for r in rules:
        if r["kind"] in ("permission", "threshold", "sod", "approval_criteria", "sla") or (r["kind"] == "routing" and r["step"] in ("approve", "*")):
            checked += 1
            same += sentence(r, procs) == r["source"]["text"]
    assert checked > 80 and same / checked > 0.9, f"{same}/{checked} read as written"
    ap = find(rules, kind="approval_criteria", approver="cog:ap-reviewer")
    assert sentence(ap, procs) == ap["source"]["text"]
    route = next(r for r in rules if r["kind"] == "routing" and (r.get("condition") or {}).get("value") == 10000.0 and r["process"] == "purchase-request")
    assert sentence(route, procs) == "Approvals in purchase-request over 10,000 USD go to the controller."


def test_a_rail_changed_in_settings_is_proposed_reviewed_and_released(hub):
    rules = hub.settings_draft("bob")["rules"]
    ap = find(rules, kind="approval_criteria", approver="cog:ap-reviewer")
    new = {k: v for k, v in ap.items() if k != "source"} | {"max_amount": 5000.0, "max_per_day": 20}
    d = hub.settings_edit("bob", {"action": "replace", "key": rule_key(ap), "rule": new})
    assert d["edits"][-1]["said"].startswith("changed: The ap-reviewer cog may approve purchase-to-pay bills under 2,000 USD")
    check = hub.settings_check("bob")
    [change] = [c for c in check["changes"] if c["op"] == "add"]
    assert change["widening"] and not [f for f in check["findings"] if f["severity"] == "error"]
    p = hub.settings_propose("bob", reason="ap-reviewer handles mid-size bills")
    assert p["source"] == "settings" and p["number"] and hub.settings_drafts.load("bob") is None
    assert any(m["to"] == "carol" and "Settings change" in m["subject"] for m in hub.inbox.all())
    with pytest.raises(Exception):
        hub.release(p["id"], "bob")  # a widening needs both owners
    hub.review(p["id"], "bob", approve_all=True)
    hub.review(p["id"], "carol", approve_all=True)
    hub.release(p["id"], "bob")
    released = find(bundle(hub)["approval_criteria"], approver="cog:ap-reviewer")
    assert released["max_amount"] == 5000.0 and released["max_per_day"] == 20
    assert released["source"]["frame"] == "settings" and "at most 20 a day" in released["source"]["text"] and released["source"]["by"] == "bob"
    assert hub.settings_source()
    with pytest.raises(CompileError, match="edited in Settings"):
        hub.compile()


def test_invalid_and_stale_edits_are_refused(hub):
    rules = hub.settings_draft("carol")["rules"]
    with pytest.raises(ValueError, match="unknown kind"):
        hub.settings_edit("carol", {"action": "add", "rule": {"kind": "magic"}})
    with pytest.raises(ValueError, match="missing field"):
        hub.settings_edit("carol", {"action": "add", "rule": {"kind": "threshold", "name": "x-limit"}})
    with pytest.raises(ValueError, match="not in the draft"):
        hub.settings_edit("carol", {"action": "remove", "key": "nope"})
    t = find(rules, kind="threshold", name="purchase-approval-threshold")
    with pytest.raises(ValueError, match="already there"):
        hub.settings_edit("carol", {"action": "add", "rule": {k: v for k, v in t.items() if k != "source"}})
    with pytest.raises(ValueError, match="changes nothing"):
        hub.settings_propose("carol")
    hub.settings_edit("carol", {"action": "replace", "key": rule_key(t), "rule": {**{k: v for k, v in t.items() if k != "source"}, "value": 9000.0}})
    # Someone else's change is released meanwhile: carol's edits are against an old policy.
    other = hub.settings_draft("bob")["rules"]
    th = find(other, kind="threshold", name="match-tolerance")
    hub.settings_edit("bob", {"action": "replace", "key": rule_key(th), "rule": {**{k: v for k, v in th.items() if k != "source"}, "value": 1.0}})
    p = hub.settings_propose("bob")
    hub.review(p["id"], "bob", approve_all=True)
    hub.review(p["id"], "carol", approve_all=True)
    hub.release(p["id"], "bob")
    assert hub.settings_draft("carol")["stale"]
    with pytest.raises(ValueError, match="policy changed"):
        hub.settings_propose("carol")
    hub.settings_discard("carol")
    assert not hub.settings_draft("carol")["stale"]


def test_a_rail_with_a_daily_limit_declines_once_it_is_reached(hub):
    rules = hub.settings_draft("bob")["rules"]
    ap = find(rules, kind="approval_criteria", approver="cog:ap-reviewer")
    hub.settings_edit("bob", {"action": "replace", "key": rule_key(ap), "rule": {**{k: v for k, v in ap.items() if k != "source"}, "max_per_day": 1}})
    p = hub.settings_propose("bob")
    hub.review(p["id"], "bob", approve_all=True)
    hub.review(p["id"], "carol", approve_all=True)
    hub.release(p["id"], "bob")
    first = hub.runner.load(hub.runner.submit("purchase-to-pay", {"record": bill(hub)}, owner="alice"))
    assert first["status"] == "completed"  # ap-reviewer approved it within its rails
    second = hub.runner.load(hub.runner.submit("purchase-to-pay", {"record": bill(hub)}, owner="alice"))
    assert second["status"] == "waiting" and "ap-reviewer" not in second["pending"]["assignees"]
    declined = [e for e in hub.runner.track(second["id"]) if e["type"] == "gate.declined"]
    assert declined and "limit of 1 approvals a day is reached" in declined[0]["reasons"][0]


def test_a_monthly_amount_limit_is_counted_from_its_approvals(hub):
    rules = hub.settings_draft("bob")["rules"]
    ap = find(rules, kind="approval_criteria", approver="cog:ap-reviewer")
    hub.settings_edit("bob", {"action": "replace", "key": rule_key(ap),
                              "rule": {**{k: v for k, v in ap.items() if k != "source"}, "max_amount_per_month": 2500.0}})
    p = hub.settings_propose("bob")
    hub.review(p["id"], "bob", approve_all=True)
    hub.review(p["id"], "carol", approve_all=True)
    hub.release(p["id"], "bob")
    assert "at most 2,500 USD a month" in find(bundle(hub)["approval_criteria"], approver="cog:ap-reviewer")["source"]["text"]
    assert hub.runner.load(hub.runner.submit("purchase-to-pay", {"record": bill(hub, 1500)}, owner="alice"))["status"] == "completed"
    second = hub.runner.load(hub.runner.submit("purchase-to-pay", {"record": bill(hub, 1500)}, owner="alice"))
    assert second["status"] == "waiting"
    reasons = [e for e in hub.runner.track(second["id"]) if e["type"] == "gate.declined"][0]["reasons"]
    assert "a month would be passed" in reasons[0]


def test_frames_can_state_running_limits_too(tmp_path):
    from hubproto.extract import _CRITERIA

    m = _CRITERIA.match("The ap-reviewer cog may approve purchase-to-pay bills under 2,000 USD when the supplier is approved, "
                        "at most 10 a day, at most 20,000 USD a month, countersigned by a controller.")
    assert m and m.group("perday") == "10" and m.group("permonth") == "20,000" and m.group("counter") == "controller"


def test_a_cog_proposes_a_setting_change_and_never_applies_it(hub):
    from hubproto.agent import AgentStore, AgentTools

    tools = AgentTools(hub, "qa-manager", "t-settings", "act", 6, AgentStore(hub.root / "state" / "agents"), requester="carol")
    found = tools.execute("list_settings", {"kind": "approval_criteria", "contains": "ap-reviewer"})
    [s] = found["settings"]
    assert s["reads"].startswith("The ap-reviewer cog may approve purchase-to-pay bills under 2,000 USD")
    before = hub.releases.current()["digest"]
    out = tools.execute("propose_setting_change", {"action": "replace", "key": s["key"], "rule": {**s["rule"], "max_per_day": 15},
                                                   "reason": "keep cog approvals to a reviewable volume"})
    assert out["ok"] and out["proposal"] and out["changes"] >= 1 and hub.releases.current()["digest"] == before
    assert hub.proposals.load(out["proposal_id"])["source"] == "settings"
    assert "error" in tools.execute("propose_setting_change", {"action": "replace", "key": "nope", "rule": {}, "reason": "x"})
    assert "error" in tools.execute("propose_setting_change", {"action": "remove", "key": s["key"], "reason": ""})


def test_a_model_change_carries_only_itself_and_its_release_overwrites_the_file(hub):
    """A model change is proposed on the release in force: unreleased edits on disk are not swept into it, and
    releasing it overwrites the Frame file with the released text (the Frame is derived output)."""
    path = hub.frames.get("cog-profiles").path
    pending = "About: Checks suppliers against the approved list"
    text = path.read_text()
    assert pending in text
    path.write_text(text.replace(pending, pending + " (an unreleased edit)"))
    hub._frames = None
    p = hub.propose_cog_model("quality-assistant", "claude-opus-5-5", by="bob")
    assert {c["rule"].get("kind") for c in p["changes"] if c.get("rule")} <= {"cog", "text"}, [c["reason"] for c in p["changes"]]
    assert all(c.get("frame") in (None, "cog-profiles") for c in p["changes"] if c["op"] == "text"), "only the cog's own Frame"
    assert all((c.get("rule") or {}).get("name") == "quality-assistant" for c in p["changes"] if (c.get("rule") or {}).get("kind") == "cog")
    for who in ("bob", "carol"):
        hub.review(p["id"], who, approve_all=True)
    hub.release(p["id"], "bob")
    b = hub.releases.bundle(hub.releases.current()["digest"])
    assert b["cogs"]["quality-assistant"]["model"] == "claude-opus-5-5"
    after = path.read_text()
    assert "Model: claude-opus-5-5" in after and "(an unreleased edit)" not in after, "the released text overwrites the file"
    assert after == hub.releases.frame_texts(hub.releases.current()["digest"])["cog-profiles"]

