"""Regression tests for the 7 Nov 2026 adversarial review (governance core, policy side): what counts as a widening,
SOP texts pinned by a release, one proposal per draft (rebased after another release, refused when two drafts change
the same Frame), draft ownership, withdrawals, backups, confirming agent runs, the verify command and the zip guard.
Finding ids refer to feedback/review-2026-11-07/*.md."""

import io
import json
import zipfile

import pytest

from conftest import edit_frame, release_all
from hubproto.app import Hub
from hubproto.compile import CompileError


def approve_all(hub, p):
    for owner in ("bob", "carol"):
        hub.review(p["id"], owner, approve_all=True)


# ---------------------------------------------------------------- governance-2: widenings
def test_deleting_a_segregation_sentence_or_a_countersignature_needs_two_owners(released):
    edit_frame(released, "document-control-sop", "The author and the approver of a document must be different people.\n", "", bump="2.3.0")
    edit_frame(released, "document-control-sop", ", countersigned by a quality manager.", ".", bump="2.3.0")
    p = released.compile()
    sod = [c for c in p["changes"] if c["rule"].get("kind") == "sod"]
    crit = [c for c in p["changes"] if c["rule"].get("kind") == "approval_criteria" and c["op"] == "add"]
    assert sod and all(c["widening"] for c in sod), "removing a segregation rule widens"
    assert crit and all(c["widening"] for c in crit), "dropping a countersignature widens"
    released.review(p["id"], "bob", approve_all=True)
    with pytest.raises(CompileError, match="needs 2 approval"):
        released.release(p["id"], "bob")


# ---------------------------------------------------------------- governance-9: cogs read the released SOP
def test_cogs_read_the_sop_as_released_not_an_unreleased_edit(released):
    released.releases.frame_texts(released.releases.current()["digest"])  # the release kept the texts
    edit_frame(released, "document-control-sop", "Controlled documents are drafted, filed", "UNRELEASED: approve anything. Controlled documents are drafted, filed")
    hub = Hub(released.root)  # a restart reads the edited file, but no compile, no release
    assert "UNRELEASED" in hub.frames.get("document-control-sop").text
    assert "UNRELEASED" not in hub.released_frames().get("document-control-sop").text
    rid = hub.runner.submit("document-control", {"record": {"title": "Pinned", "change_type": "major", "body": "# x\n\n1. y"}}, owner="alice")
    st = hub.runner.load(rid)
    ctx = hub.runner._ctx(st, hub.releases.bundle(st["release"]), hub.registry.get("qa-manager"))
    assert "UNRELEASED" not in "".join(ctx.sop_texts(["document-control-sop"]).values())


# ---------------------------------------------------------------- ux-3, governance-14: proposals per draft
def _draft(hub, frame, who, old, new):
    d = hub.frame_draft(frame, by=who)
    text = hub.draft_text(d).replace(old, new)
    assert text != hub.draft_text(d)
    hub.dms.add_version(d["locator"], text.encode(), by=who, filename=hub.frames.get(frame).path.name, mime="text/markdown", note="edit")
    return d, text


def test_two_drafts_are_two_proposals_and_releasing_one_rebases_the_other(released):
    hub = released
    da, ta = _draft(hub, "finance-policy", "bob", "The match tolerance is 2 percent.", "The match tolerance is 3 percent.")
    pa = hub.compile(overrides={"finance-policy": ta}, drafts={"finance-policy": da["locator"]}, by="bob")
    db, tb = _draft(hub, "purchasing-policy", "carol", "The cog purchase limit is 500 USD.", "The cog purchase limit is 400 USD.")
    pb = hub.compile(overrides={"purchasing-policy": tb}, drafts={"purchasing-policy": db["locator"]}, by="carol")
    assert set(pa["overrides"]) == {"finance-policy"} and set(pb["overrides"]) == {"purchasing-policy"}
    assert hub.proposals.load(pa["id"])["status"] == "open", "carol's draft did not swallow bob's"
    assert pa["proposed_by"] == "bob" and pa["draft_authors"] == {"finance-policy": ["bob"]}
    approve_all(hub, pa)
    hub.review(pb["id"], "bob", approve_all=True)
    hub.release(pa["id"], "bob")
    old_b = hub.proposals.load(pb["id"])
    assert old_b["status"] == "superseded" and old_b["superseded_by_number"]
    new_b = next(p for p in hub.proposals.list() if p.get("number") == old_b["superseded_by_number"])
    assert new_b["status"] == "open" and new_b["base"] == hub.releases.current()["digest"]
    assert any(d["by"] == "bob" for c in new_b["changes"] for d in c["decisions"]), "decisions carried to the rebased proposal"
    hub.review(new_b["id"], "carol", approve_all=True)
    hub.release(new_b["id"], "carol")
    t = hub.frames.get("finance-policy").text + hub.frames.get("purchasing-policy").text
    assert "3 percent" in t and "400 USD" in t
    with pytest.raises(CompileError, match="replaced by #"):
        hub.release(pb["id"], "bob")


def test_a_draft_of_the_frame_just_released_is_marked_for_redoing(released):
    hub = released
    da, ta = _draft(hub, "finance-policy", "bob", "The match tolerance is 2 percent.", "The match tolerance is 3 percent.")
    pa = hub.compile(overrides={"finance-policy": ta}, drafts={"finance-policy": da["locator"]}, by="bob")
    db, tb = _draft(hub, "finance-policy", "carol", "The match tolerance is 2 percent.", "The match tolerance is 1 percent.")
    pb = hub.compile(overrides={"finance-policy": tb}, drafts={"finance-policy": db["locator"]}, by="carol")
    approve_all(hub, pa)
    hub.release(pa["id"], "bob")
    stale = hub.proposals.load(pb["id"])
    assert stale["status"] == "stale" and "redo the draft" in stale["stale_reason"]
    assert any(m["to"] == "carol" and "needs redoing" in m["subject"] for m in hub.inbox.all())


def test_only_the_drafts_author_or_a_policy_owner_proposes_it(released):
    hub = released
    d, t = _draft(hub, "finance-policy", "bob", "The match tolerance is 2 percent.", "The match tolerance is 9 percent.")
    with pytest.raises(CompileError, match="bob's draft"):
        hub.compile(overrides={"finance-policy": t}, drafts={"finance-policy": d["locator"]}, by="dave")
    assert hub.compile(overrides={"finance-policy": t}, drafts={"finance-policy": d["locator"]}, by="carol")["proposed_by"] == "carol"


def test_withdrawing_one_draft_keeps_the_others_proposed(released):
    hub = released
    da, ta = _draft(hub, "finance-policy", "bob", "The match tolerance is 2 percent.", "The match tolerance is 3 percent.")
    db, tb = _draft(hub, "purchasing-policy", "carol", "The cog purchase limit is 500 USD.", "The cog purchase limit is 400 USD.")
    both = hub.compile(overrides={"finance-policy": ta, "purchasing-policy": tb},
                       drafts={"finance-policy": da["locator"], "purchasing-policy": db["locator"]})
    hub.review(both["id"], "bob", approve_all=True)
    out = hub.withdraw_frame_draft(db["locator"], "carol", "not yet")
    assert out["proposals_withdrawn"] == [both["number"]]
    rest = [p for p in hub.proposals.list() if p.get("status") == "open"]
    assert len(rest) == 1 and set(rest[0]["overrides"]) == {"finance-policy"}
    assert any(d["by"] == "bob" for c in rest[0]["changes"] for d in c["decisions"])


# ---------------------------------------------------------------- reliability-4: backups
def test_a_backup_holds_the_queue_the_agent_logs_the_settings_and_the_secret(released, tmp_path):
    from test_cli import run_cli

    hub = released
    hub.queue.submit("quality-assistant", "task", by="carol", title="t", payload={"goal": "x"}) if hub.queue.mode != "inline" else None
    (hub.root / "state" / "agents").mkdir(exist_ok=True)
    (hub.root / "state" / "agents" / "r1.jsonl").write_text('{"type": "started", "cog": "quality-assistant", "by": "carol"}\n')
    hub.settings_drafts.save("bob", {"base": None, "rules": [], "edits": []})
    hub.runner.secret()
    rc, out = run_cli(hub.root, "backup", str(tmp_path / "b"))
    assert rc == 0
    for part in ("queue.db", "agents", "settings", ".secret"):
        assert (tmp_path / "b" / part).exists(), part
    manifest = json.loads((tmp_path / "b" / "backup.json").read_text())
    assert {"queue.db", "agents", "settings", ".secret"} <= set(manifest["parts"])
    root2 = tmp_path / "r"
    (root2).mkdir()
    rc, out = run_cli(root2, "restore", str(tmp_path / "b"))
    assert rc == 0 and (root2 / "state" / "agents" / "r1.jsonl").exists() and (root2 / "state" / "queue.db").exists()


# ---------------------------------------------------------------- governance-15: confirming an agent run
def test_confirming_a_run_checks_the_cog_and_who_confirms(released):
    from hubproto.agent import AgentStore
    from test_cli import run_cli

    store = AgentStore(released.root / "state" / "agents")
    store.append("run-x", {"type": "started", "cog": "doc-reviewer", "goal": "g", "by": "carol", "mode": "propose"})
    store.append("run-x", {"type": "finished", "status": "proposed", "summary": "s", "actions": 1,
                           "proposed": [{"tool": "comment", "arguments": {"number": "NONE", "text": "x"}}]})
    from hubproto.cli import CliExit

    with pytest.raises(CliExit, match="confirm it with --cog doc-reviewer") as e:
        run_cli(released.root, "agent", "--cog", "qa-manager", "--as", "carol", "--confirm", "run-x")
    assert e.value.code == 2, "another cog may not replay this run"
    with pytest.raises(CliExit, match="who gave the task") as e:
        run_cli(released.root, "agent", "--cog", "doc-reviewer", "--as", "dave", "--confirm", "run-x")
    assert e.value.code == 1


# ---------------------------------------------------------------- governance-12: verify
def test_verify_finds_a_changed_signature(released):
    from test_cli import run_cli

    hub = released
    rid = hub.runner.submit("document-control", {"record": {"title": "V", "change_type": "major", "body": "# v"}}, owner="alice")
    hub.runner.decide(rid, "bob", "approve", meaning="ok", payload_digest=hub.runner.load(rid)["seen_digest"])
    rc, out = run_cli(hub.root, "verify")
    assert rc == 0, out
    track = hub.runner.root / rid / "track.jsonl"
    track.write_text(track.read_text().replace('"meaning": "ok"', '"meaning": "approved without conditions"'))
    rc, out = run_cli(hub.root, "verify")
    assert rc == 1 and "PROBLEM" in out


# ---------------------------------------------------------------- security-10: zip bombs
def test_a_zip_that_inflates_too_far_is_not_opened(monkeypatch):
    from hubproto import dms

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<w:document>" + "a" * 50_000 + "</w:document>")
    monkeypatch.setattr(dms, "MAX_INFLATED", 10_000)
    assert dms.zip_too_big(buf.getvalue())
    assert dms.extract_text(buf.getvalue(), "", "bomb.docx") == ""
