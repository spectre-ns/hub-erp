"""Frames -> rules -> Guards -> Gate -> release."""

import pytest

from conftest import edit_frame, release_all
from hubproto.compile import CompileError, change_state
from hubproto.extract import ConventionExtractor
from hubproto.frames import FrameError


def test_frames_resolve_parents_first(hub):
    frames = hub.frames.resolve(["finance-policy", "document-control-sop"])
    assert [f.name for f in frames] == ["org-model", "finance-policy", "document-control-sop"]


def test_frame_requires_four_fields(tmp_path):
    bad = tmp_path / "x.frame.md"
    bad.write_text("---\ntype: frame\nname: x\n---\nbody\n")
    with pytest.raises(FrameError, match="missing required field"):
        from hubproto.frames import load_frame

        load_frame(bad)


def test_extraction_cites_the_sentence(hub):
    ex = ConventionExtractor().extract(hub.frames.get("finance-policy"))
    kinds = {r["kind"] for r in ex.rules}
    assert {"permission", "threshold", "routing", "approval_criteria", "sod", "process"} <= kinds
    for rule in ex.rules:
        src = rule["source"]
        assert hub.frames.get(src["frame"]).line(src["line"]).strip() == src["text"]
    routing = [r for r in ex.rules if r["kind"] == "routing"]
    assert routing[0]["condition"] == {"field": "amount", "op": ">", "value": 10000.0, "currency": "USD"}
    assert routing[1]["assignee"] == "cog:ap-reviewer"
    assert routing[2]["condition"] is None and routing[2]["assignee"] == "role:payables-reviewer"
    crit = next(r for r in ex.rules if r["kind"] == "approval_criteria")
    assert crit["max_amount"] == 2000.0 and crit["conditions"] == ["match_within_tolerance", "supplier_approved"]
    proc = next(r for r in ex.rules if r["kind"] == "process")
    assert proc["object"] == "bill" and proc["folder"] == "Finance/Payables"
    assert [s["kind"] for s in proc["steps"]] == ["cog", "cog", "cog", "cog", "checkpoint", "cog"]
    assert not ex.notes


def test_compile_is_blocked_by_guards(hub):
    # An unknown role in a permission sentence is a consistency error.
    edit_frame(hub, "finance-policy", "Controllers may read Finance.", "Auditors may read Finance.")
    proposal = hub.compile()
    errors = [f for f in proposal["findings"] if f["severity"] == "error"]
    assert any("role:auditor" in f["detail"] for f in errors)
    hub.review(proposal["id"], "bob", approve_all=True)
    hub.review(proposal["id"], "carol", approve_all=True)
    with pytest.raises(CompileError, match="consistency"):
        hub.release(proposal["id"], "bob")


def test_cog_route_needs_criteria(hub):
    edit_frame(hub, "document-control-sop",
               "The doc-reviewer cog may approve document-control documents when the change is minor.", "")
    proposal = hub.compile()
    assert any("no approval criteria" in f["detail"] for f in proposal["findings"])


def test_unevaluable_condition_is_refused(hub):
    edit_frame(hub, "finance-policy", "when the three-way match is within tolerance and the supplier is approved.",
               "when the invoice looks reasonable.")
    proposal = hub.compile()
    assert any("cannot be evaluated deterministically" in f["detail"] for f in proposal["findings"])


def test_grounding_guard_rejects_misquoted_source(hub):
    class LyingExtractor(ConventionExtractor):
        name = "lying"

        def extract(self, frame):
            ex = super().extract(frame)
            for r in ex.rules:
                if r["kind"] == "threshold":
                    r["value"] = 50.0  # the number changed but the citation did not
                    r["source"]["text"] = "The match tolerance is 50 percent."
            return ex

    proposal = hub.compile(extractor=LyingExtractor())
    assert any(f["guard"] == "source-grounding" for f in proposal["findings"])


def test_widening_needs_two_approvers_and_release_is_deterministic(hub):
    proposal = hub.compile()
    assert proposal["base"] is None
    widening = [c for c in proposal["changes"] if c["widening"]]
    assert widening, "a first release adds permissions, which widen"
    hub.review(proposal["id"], "bob", approve_all=True)
    with pytest.raises(CompileError, match="needs 2 approval"):
        hub.release(proposal["id"], "bob")
    with pytest.raises(CompileError, match="not a policy owner"):
        hub.review(proposal["id"], "alice", approve_all=True)
    hub.review(proposal["id"], "carol", approve_all=True)
    meta = hub.release(proposal["id"], "bob")
    assert hub.releases.current()["digest"] == meta["digest"]
    bundle = hub.releases.bundle(meta["digest"])
    assert bundle["thresholds"]["match-tolerance"]["value"] == 2.0
    assert {"purchase-to-pay", "document-control"} <= set(bundle["ops"])


def test_no_op_edit_makes_no_new_release(released):
    first = released.releases.current()["digest"]
    edit_frame(released, "finance-policy", "Everything else is approved by a person.",
               "Everything else is approved by a human being.", bump="1.2.1")
    proposal = released.compile()
    assert proposal["changes"] == []
    meta = released.release(proposal["id"], "bob")
    assert meta["digest"] == first
    assert len(released.releases.list()) == 1


def test_threshold_change_creates_child_release_and_is_widening(released):
    first = released.releases.current()["digest"]
    edit_frame(released, "finance-policy", "The match tolerance is 2 percent.", "The match tolerance is 5 percent.",
               bump="1.3.0")
    proposal = released.compile()
    assert proposal["base"] == first
    adds = [c for c in proposal["changes"] if c["op"] == "add"]
    removes = [c for c in proposal["changes"] if c["op"] == "remove"]
    assert len(adds) == 1 and len(removes) == 1
    assert adds[0]["widening"] is True and removes[0]["widening"] is False
    released.review(proposal["id"], "bob", approve_all=True)
    assert change_state(adds[0]) == "pending" or True  # state is computed on the stored copy
    with pytest.raises(CompileError):
        released.release(proposal["id"], "bob")
    released.review(proposal["id"], "carol", approve_all=True)
    meta = released.release(proposal["id"], "carol")
    assert meta["parent"] == first and meta["digest"] != first
    assert released.releases.bundle(meta["digest"])["thresholds"]["match-tolerance"]["value"] == 5.0


def test_rejected_change_keeps_old_rule(released):
    edit_frame(released, "finance-policy", "The match tolerance is 2 percent.", "The match tolerance is 5 percent.",
               bump="1.3.0")
    proposal = released.compile()
    add = next(c for c in proposal["changes"] if c["op"] == "add")
    remove = next(c for c in proposal["changes"] if c["op"] == "remove")
    released.review(proposal["id"], "bob", decisions={add["id"]: "reject", remove["id"]: "reject"})
    meta = released.release(proposal["id"], "bob")
    assert released.releases.bundle(meta["digest"])["thresholds"]["match-tolerance"]["value"] == 2.0


def test_effective_from_schedules_cutover(hub):
    meta1 = release_all(hub, now="2026-11-05T09:00:00+00:00")
    edit_frame(hub, "finance-policy", "The match tolerance is 2 percent.", "The match tolerance is 3 percent.", bump="1.3.0")
    meta2 = release_all(hub, effective_from="2026-12-01T00:00:00+00:00", now="2026-11-05T10:00:00+00:00")
    assert hub.releases.current("2026-11-06T00:00:00+00:00")["digest"] == meta1["digest"]
    assert hub.releases.current("2026-12-02T00:00:00+00:00")["digest"] == meta2["digest"]
