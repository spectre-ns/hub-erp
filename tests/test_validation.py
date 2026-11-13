"""The IT SOP made real: validation of configuration changes in proportion to their risk (review panel QMS A5), the
system-administrator role that approves nothing of its own (fidelity 4), hosted model use (QMS B8), the model and
competence record kept with every cog decision, review and drafted text (QMS B7), and look tools that serve only what a
cog's roles may read (governance M4)."""

import re

import pytest

from conftest import edit_frame, release_all
from hubproto import authz, validation
from hubproto import changecontrol as cc
from hubproto.agent import AgentStore, AgentTools
from test_change_control import new_co, revise
from test_change_control import hub as cc_hub  # noqa: F401  (the fixture: SOPs are documents)
from test_eco import NEW, OLD


def bundle(hub):
    return hub.releases.bundle(hub.releases.current()["digest"])


def tools(hub, cog, requester=""):
    return AgentTools(hub, cog, f"t-{cog}-{requester}", "derived", 8, AgentStore(hub.root / "state" / "agents"), requester=requester)


# ---------------------------------------------------------------- the IT SOP (fidelity 4, QMS B8)
def test_a_system_administrator_approves_nothing_as_one(released):
    b = bundle(released)
    text = released.frames.get("it-sop").text
    assert "The system administrator role grants no approval of its own" in text and "never as a system administrator" in text
    assert "system-administrator" in b["members"]["bob"]["roles"] and "policy-owner" in b["members"]["bob"]["roles"]
    assert not [p for p in b["permissions"] if p["subject"] == "role:system-administrator" and p["action"] == "approve"]
    assert authz.check(b, "bob", "approve", "document", "Policy")["allowed"], "bob approves as a policy owner"
    assert not authz.check(b, "it-admin", "approve", "change-order", "Quality/Changes")["allowed"]
    assert any(s.get("only") and s["object"] == "change-order" for s in b["sod"]), "whoever made a direct change is not its only approver"


def test_hosted_model_use_is_governed_by_the_it_sop(released):
    text = released.frames.get("it-sop").text
    hosted = text.split("Hosted models.")[1].split("## ")[0]
    for words in ("a system administrator", "installation change recorded under this SOP", "only by the cogs bundled with it",
                  "for the tasks assigned to that cog", "working for the person who filed the task",  # issue #73: hosted cogs take tasks
                  "supplier evaluation SOP", "What may leave the hub", "its diff against the release in force", "Each use is recorded"):
        assert words in " ".join(hosted.split()), words
    assert not [f for f in released.compile(save=False)["findings"] if f["severity"] == "error"]


# ---------------------------------------------------------------- validation (QMS A5)
def test_the_risk_classes_come_from_the_it_sop(released):
    b = bundle(released)
    assert b["validation"]["classes"]["permission"] == "high" and b["validation"]["needs"]["medium"] == "review"
    assert all(s_["frame"] == "it-sop" for s_ in b["validation"]["sources"]), "compiled from the IT SOP, each with its line"
    pol = validation.policy(released)
    assert pol["source"] == "release" and pol["needs"] == {"high": "review", "medium": "review", "low": "review"}, \
        "the example IT SOP asks only for the review of the delta, for now"
    assert "it-sop" in validation.basis(released)
    perm = {"op": "add", "rule": {"kind": "permission"}, "widening": True}
    assert validation.classify(perm, pol) == "high"
    assert validation.classify({"op": "add", "rule": {"kind": "threshold"}, "widening": False}, pol) == "medium"
    assert validation.classify({"op": "add", "rule": {"kind": "threshold"}, "widening": True}, pol) == "high", "a widening is high"
    assert validation.classify({"op": "text", "rule": {"kind": "text"}}, pol) == "low"
    gate = {"op": "add", "rule": {"kind": "process", "steps": [{"id": "a", "kind": "checkpoint"}]},
            "changes_from": {"kind": "process", "steps": [{"id": "b", "kind": "checkpoint"}]}}
    assert validation.classify(gate, pol) == "high", "a workflow gate"
    same = {**gate, "changes_from": {"kind": "process", "steps": [{"id": "a", "kind": "checkpoint"}], "folder": "X"}}
    assert validation.classify(same, pol) == "medium"
    assert validation.classify({"kind": "form"}, pol) == "low" and validation.classify({"kind": "record_type"}, pol) == "high"
    # The release decides, never the prose: a released change that makes limits low risk makes them so.
    edit_frame(released, "it-sop", "processes, thresholds, required fields", "processes, required fields")
    edit_frame(released, "it-sop", "Changes to form layouts and wording are low risk.", "Changes to form layouts, thresholds and wording are low risk.")
    assert validation.classify({"op": "add", "rule": {"kind": "threshold"}}, validation.policy(released)) == "medium", "not released yet"
    p = released.compile()
    assert any(c["rule"]["kind"] == "validation_class" and c["widening"] for c in p["changes"]), "loosening validation: two policy owners"
    for r in ("bob", "carol"):
        released.review(p["id"], r, approve_all=True)
    released.release(p["id"], "bob")
    assert validation.classify({"op": "add", "rule": {"kind": "threshold"}}, validation.policy(released)) == "low"


def test_the_example_it_sop_asks_for_no_test_evidence(cc_hub):
    """For the demo, a change order of any risk waits for no test record or test note: the review of the delta is its
    validation. A failed test still stops it."""
    hub = cc_hub
    co = new_co(hub)
    revise(hub, co, "finance-policy", OLD, NEW)  # approval criteria: high risk
    cc.request_review(hub, co["number"], "alice")
    its = validation.annotate(validation.items_of(hub, hub.kernel.get(co["locator"])), validation.policy(hub))
    assert any(i["risk"] == "high" for i in its) and all(i["validation"] == "review" for i in its)
    assert validation.requirements(hub, co["number"]) == []
    assert not any("test evidence" in r or "test note" in r for r in cc.approval_reasons(hub, co["number"]))
    validation.record_evidence(hub, co["number"], by="bob", kind="test-record", text="bill of 1,400 USD submitted",
                               expected="the cog approves", actual="it waited for dave", result="fail")
    assert validation.requirements(hub, co["number"])[0].startswith("a test of"), "a failed test still stops it"


def test_without_validation_sentences_the_defaults_apply_and_say_so(released):
    text = released.frames.get("it-sop").path.read_text()
    head, rest = text.split("## Validation\n", 1)
    released.frames.get("it-sop").path.write_text(head + "## " + rest.split("\n## ", 1)[1])  # the section gone
    released.frames = type(released.frames)(released.frames.root)
    release_all(released)
    assert "validation" not in bundle(released)
    pol = validation.policy(released)
    assert pol["source"] == "default" and "the release in force states no validation classes" in pol["basis"]
    assert validation.classify({"op": "add", "rule": {"kind": "permission"}}, pol) == "high"
    from hubproto.extract import ConventionExtractor
    from hubproto.frames import load_frame_text
    from pathlib import Path

    frame = load_frame_text('---\ntype: frame [0.2]\nname: v-sop\ndescription: x\nvisibility: internal\nversion: "1"\nscope: department\n'
                            'maintainer: bob\n---\n# V\n\n## Validation\nChanges to the weather are high risk.\n', Path("v-sop.frame.md"))
    notes = ConventionExtractor().extract(frame).notes
    assert any(n.get("severity") == "unread" and "names nothing the hub can class" in n["note"] for n in notes)


def test_a_change_order_needs_the_evidence_its_items_risk_class_asks_for(cc_hub, evidence_required):
    hub = cc_hub
    co = new_co(hub)
    revise(hub, co, "finance-policy", OLD, NEW)  # the ap-reviewer cog's approval limit: approval criteria, high risk
    cc.request_review(hub, co["number"], "alice")
    [need] = validation.requirements(hub, co["number"])
    assert re.match(r"\d+ high-risk item\(s\) need test evidence", need) and "attach a test report as evidence" in need and "record a test record" in need
    risky = [i for i in validation.annotate(validation.items_of(hub, hub.kernel.get(co["locator"])), validation.policy(hub))
             if i["risk"] == "high"]
    assert risky and risky[0]["validation"] == "test-record"
    with pytest.raises(ValueError, match="expected and what actually happened"):
        validation.record_evidence(hub, co["number"], by="bob", kind="test-record", text="ran it")
    with pytest.raises(ValueError, match="not a member"):
        validation.record_evidence(hub, co["number"], by="mallory", kind="test-note", text="tried it")
    validation.record_evidence(hub, co["number"], by="bob", kind="test-note", text="tried a 1,800 USD bill")
    assert validation.requirements(hub, co["number"]), "a note is not a test record"
    validation.record_evidence(hub, co["number"], by="bob", kind="test-record", text="bill of 1,800 USD submitted",
                               expected="it waits for a person", actual="it waited for dave", result="pass")
    assert validation.requirements(hub, co["number"]) == []
    validation.record_evidence(hub, co["number"], by="bob", kind="test-record", text="bill of 1,400 USD submitted",
                               expected="the cog approves", actual="it waited for dave", result="fail", items=[risky[0]["id"]])
    assert validation.requirements(hub, co["number"])[0].startswith("a test of"), "a failed test stops it"


def test_a_test_evidence_document_on_the_change_order_counts(cc_hub, evidence_required):
    hub = cc_hub
    co = new_co(hub, title="Tolerance")
    revise(hub, co, "finance-policy", "The match tolerance is 2 percent.", "The match tolerance is 3 percent.")
    cc.request_review(hub, co["number"], "alice")
    reasons = validation.requirements(hub, co["number"])
    assert reasons and ("high-risk" in reasons[0] or "medium-risk" in reasons[0])
    hub.dms.create(title="Tolerance test", doc_class="evidence", folder="Quality/Changes", by="bob", content=b"script; expected; actual: pass",
                   filename="t.md", mime="text/markdown", doc_type="test-evidence", attach_to=co["locator"])
    assert validation.requirements(hub, co["number"]) == []
    assert validation.evidence(hub, co["number"])[0]["kind"] == "test-record"


# ---------------------------------------------------------------- the model and competence record (QMS B7)
def test_every_cog_decision_review_and_draft_keeps_what_produced_it(released):
    hub = released
    run_id = hub.runner.submit("nonconformance", {"record": {"title": "Scratch", "description": "cosmetic scratch", "severity": "low",
                                                             "source": "inspection"}}, owner="alice")
    loc = hub.runner.load(run_id)["record"]
    worked = [e for e in hub.kernel.events(loc) if e["type"] == "cog.worked"]
    assert worked, "the cog's decision at the Gate"
    d = worked[-1]["data"]
    assert d["kind"] == "decision" and d["cog"] == "quality-assistant" and d["persona"] == "reviewer" and d["persona_version"] == "2.0.0"
    assert d["model"] == "no model (fixed criteria)" and d["release"] == hub.releases.current()["digest"] and d["release_n"] == 1
    env = hub.registry.get("quality-assistant").invoke("review-document", {"record": loc, "question": "ok?"},
                                                     tools(hub, "quality-assistant", "carol")._ctx(hub.kernel.get(loc)))
    review = [e["data"] for e in hub.kernel.events(loc) if e["type"] == "cog.worked" and e["data"]["kind"] == "review"]
    assert review and review[-1]["model"] == "ollama/gemma4:12b-it-qat" and review[-1]["stand_in"] is True and env["payload"]["provenance"]["cog"] == "quality-assistant"
    t = tools(hub, "qa-manager", "carol")
    out = t.execute("draft_document", {"title": "Torque WI", "text": "# Torque WI\n1. Torque to 2 Nm."})
    doc = hub.kernel.get(out["document"])
    drafted = [e["data"] for e in hub.kernel.events(doc["locator"]) if e["type"] == "cog.worked"]
    assert drafted and drafted[-1]["kind"] == "draft" and drafted[-1]["persona"] == "author" and drafted[-1]["model"] == "ollama/gemma4:12b-it-qat"
    from hubproto.shell.text import event_sentence

    line = event_sentence(next(e for e in hub.kernel.events(doc["locator"]) if e["type"] == "cog.worked"))
    assert "wrote it" in line and "model ollama/gemma4:12b-it-qat" in line and "persona author 2.0.0" in line and "trained by release v1" in line


# ---------------------------------------------------------------- look tools and read rights (governance M4)
def test_every_look_tool_serves_only_what_the_cogs_roles_may_read(released):
    ap = tools(released, "ap-reviewer")
    assert "may not read 'ncr-sop'" in ap.execute("read_procedure", {"name": "ncr-sop"})["error"]
    found = ap.execute("search_procedures", {"query": "nonconformance disposition quality manager", "k": 10})["results"]
    mine = set(bundle(released)["cogs"]["ap-reviewer"]["follows"])
    assert found and {r["procedure"] for r in found} <= mine, "only what it is trained on: it may read nothing in Quality"
    assert ap.execute("read_procedure", {"name": "finance-policy"})["procedure"] == "finance-policy", "trained on it"
    qa = tools(released, "quality-assistant")
    assert qa.execute("read_procedure", {"name": "audit-sop"})["procedure"] == "audit-sop", "its roles read Quality"
    assert any(r["procedure"] == "ncr-sop" for r in qa.execute("search_procedures", {"query": "disposition nonconformance"})["results"])
    # Working for someone, only what that person may read too.
    release_all(released)
    assert "may not read" not in str(tools(released, "doc-reviewer", "alice").execute("read_procedure", {"name": "org-model"}))
