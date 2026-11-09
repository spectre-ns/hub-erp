"""Change control v2, the review panel's fix batch (governance, QMS, correctness and fidelity reviews): state maps
checked and locked, the change order frozen when it is sent, every decision a signed event the signature covers,
segregation of duties on items, warnings and the panel, and the release paths that used to fail. No model is called."""

import json

import pytest

from conftest import ROOT  # noqa: F401
from hubproto import changecontrol as cc
from hubproto import eco, prose_drafting
from hubproto.compile import question
from hubproto.kernel import KernelGuardError
from hubproto.runner import RunError
from hubproto.track import verify_signature
from test_change_control import (NEW, OLD, ROUTE_NEW, ROUTE_OLD, accept_co_warnings, ap_limit, decide_all, gate, hub,  # noqa: F401
                                 items, job, new_co, revise, validated, write)


def ready(hub, co, who=("carol",), panel=None, by=None):  # noqa: D401
    """Reviewed, sent, every item decided and every warning accepted: ready to sign."""
    by = by or cc.requester(co)
    cc.request_review(hub, co["number"], by, panel=panel)
    cc.send_for_approval(hub, co["number"], by)
    decide_all(hub, co, who)
    accept_co_warnings(hub, co, who[0])
    return hub.kernel.get(co["locator"])


def verify_cr(hub):
    """A change request waiting at Verify (in the status a state-map test removes)."""
    run = hub.runner.submit("change-control", {"record": {"title": "Move inspection", "description": "x", "impact": "WI"}}, owner="alice")
    state = hub.runner.load(run)
    hub.runner.decide(run, "carol", "approve", meaning="ok", payload_digest=hub.runner.seen_digest(state))
    return hub.kernel.get(hub.runner.load(run)["record"])


def rename_verify(hub, co):
    d = revise(hub, co, "change-control-sop", "5. [staff] Verify the change", "5. [staff] Confirm the change")
    write(hub, d, "At Verify the change in change-control go to the requester.", "At Confirm the change in change-control go to the requester.", "alice")
    return d


# ---------------------------------------------------------------- 1. integrity
def test_a_state_map_names_only_removed_statuses_and_real_targets_is_decided_and_locks_when_sent(hub):  # governance C1
    verify_cr(hub)
    co = new_co(hub, title="Confirm, not verify")
    rename_verify(hub, co)
    cc.request_review(hub, co["number"], "alice", panel=[])
    with pytest.raises(ValueError, match="removes no status of purchase-to-pay"):
        cc.set_state_map(hub, co["number"], "purchase-to-pay", {"awaiting-approve-the-bill": "posted"}, "alice")
    with pytest.raises(ValueError, match="is not a status this change removes"):
        cc.set_state_map(hub, co["number"], "change-control", {"awaiting-approve-the-change": "closed"}, "alice")
    with pytest.raises(ValueError, match="is not a status of change-control as this change leaves it"):
        cc.set_state_map(hub, co["number"], "change-control", {"awaiting-verify-the-change": "nowhere"}, "alice")
    cc.set_state_map(hub, co["number"], "change-control", {"awaiting-verify-the-change": "awaiting-confirm-the-change"}, "alice")
    [sm] = [i for i in items(hub, co) if i["op"] == "state-map"]
    assert sm["rule"]["count"] == 1 and sm["makers"] == ["alice"] and sm["state"] == "pending", "the approver decides each entry"
    cc.send_for_approval(hub, co["number"], "alice")
    with pytest.raises(ValueError, match="state map is fixed now"):
        cc.set_state_map(hub, co["number"], "change-control", {"awaiting-verify-the-change": "closed"}, "alice")
    decide_all(hub, co, who=("carol", "bob"))
    assert any(i["op"] == "state-map" and i["state"] == "approved" for i in items(hub, co))
    # Writing the field behind the hub's back sends the change order back to revising, its decisions void.
    hub.kernel.set_fields(co["locator"], {"state_map": {"purchase-to-pay": {"awaiting-approve-the-bill": "posted"}}}, by="mallory")
    co = cc.sync(hub, co["number"])
    assert cc.stage_of(co) == "revising" and "its state map" in co["fields"]["reopened"][-1]["why"]
    assert any("the state map moves purchase-to-pay items" in r for r in cc.approval_reasons(hub, co["number"]))
    assert all(i.get("stale") or not i.get("decisions") for i in items(hub, co))


def test_a_change_order_is_frozen_when_sent_and_its_release_checks_what_was_signed(hub):  # governance C2
    co = new_co(hub)
    draft = revise(hub, co, "finance-policy", OLD, NEW)
    co = ready(hub, co)
    assert hub.kernel.get(draft["locator"])["state"] == "held"
    with pytest.raises(KernelGuardError, match="held"):
        write(hub, hub.kernel.get(draft["locator"]), NEW, OLD.replace("2,000", "9,999"), "alice")
    with pytest.raises(ValueError, match="fixed now"):
        eco.remove_sop(hub, co["number"], "finance-policy", by="alice")
    with pytest.raises(ValueError, match="fixed now"):
        cc.add_direct_change(hub, co["number"], {"kind": "threshold", "name": "match-tolerance", "value": 3.0, "unit": "percent"}, "alice")
    assert co["fields"]["frozen"]["drafts"]["finance-policy"] == hub.kernel.get(draft["locator"])["fields"]["content_sha"]
    # Signed, then changed before the release job runs (the shell's queue modes): the release refuses it.
    hub.queue.mode = "service"
    assert gate(hub, co)["status"] == "completed"
    hub.dms.add_version(draft["locator"], b"tampered", by="alice", filename="finance-policy.docx", system=True)
    hub.queue.mode = "inline"
    hub.queue.drain()
    [rel] = job(hub, co, "release-change-order")
    assert rel["status"] == "failed" and "changed after it was signed" in rel["outcome"], rel["outcome"]
    assert ap_limit(hub) == 2000.0
    co = cc.reopen(hub, co["number"], "alice", "the policy text changed after the signature")
    assert co["state"] == "open" and cc.stage_of(co) == "revising" and hub.kernel.get(draft["locator"])["state"] == "draft"


def test_every_decision_is_a_signed_event_the_signature_covers_and_tampering_is_found(hub):  # QMS A2, governance H3
    co = new_co(hub)
    revise(hub, co, "finance-policy", OLD, NEW)
    cc.request_review(hub, co["number"], "alice")
    cc.send_for_approval(hub, co["number"], "alice")
    add = next(i for i in items(hub, co) if i["op"] == "add")
    cc.decide_item(hub, co["number"], add["id"], "reject", "carol", "say why first")
    cc.decide_item(hub, co["number"], add["id"], "approve", "carol", "the reason is given")
    evs = [e for e in hub.kernel.events(aggregate=co["locator"]) if e["type"] == "co.item_decided"]
    assert [e["data"]["verdict"] for e in evs] == ["reject", "approve"], "a re-decision appends; the rejection stays on the record"
    assert all(verify_signature(hub.runner.secret(), e["data"]) == "ok" for e in evs) and hub.kernel.verify_chain() == []
    # The Gate card drawn now is refused once the delta changes under it (the CO's delta seal is one of its fields).
    run = hub.runner.run_for_record(co["locator"])
    seen = hub.runner.seen_digest(hub.runner.load(run["id"]))
    rest = next(i for i in items(hub, co) if i["id"] != add["id"])
    cc.decide_item(hub, co["number"], rest["id"], "approve", "bob", "ok")
    accept_co_warnings(hub, co)
    validated(hub, co)
    with pytest.raises(RunError, match="changed since you opened it"):
        hub.runner.decide(run["id"], "carol", "approve", meaning="signed", payload_digest=seen)
    # Editing the proposal file (the projection) is found: the decision has no signed record.
    p = hub.proposals.load(hub.kernel.get(co["locator"])["fields"]["staged_proposal"])
    for c in p["changes"]:
        for d in c.get("decisions") or []:
            if d["by"] == "bob":
                d["by"], d["meaning"] = "dave", "tampered"
    hub.proposals.save(p)
    problems = cc.verify_delta(hub, co["number"])
    assert any("dave" in x and "no signed record" in x for x in problems)
    assert any("no signed record" in r for r in cc.approval_reasons(hub, co["number"]))


def test_the_revision_keeps_the_co_signature_unchanged_and_wording_is_covered_honestly(hub):  # QMS B1, B2
    co = new_co(hub)
    draft = revise(hub, co, "finance-policy", OLD, NEW)
    ready(hub, co)
    assert gate(hub, co)["status"] == "completed"
    rev = hub.kernel.get(draft["locator"])
    sec = hub.runner.secret()
    assert rev["state"] == "effective" and [verify_signature(sec, d) for d in rev["decisions"] if d.get("via")] == ["ok"]
    p = hub.proposals.load(hub.kernel.get(co["locator"])["fields"]["staged_proposal"])
    [txt] = [c for c in p["changes"] if c["op"] == "text"]
    assert "covered by the signature" in txt["decisions"][0]["meaning"] and "document control" not in txt["decisions"][0]["meaning"]


# ---------------------------------------------------------------- 2. segregation of duties
def test_who_made_a_change_does_not_decide_it_accept_against_it_or_count_on_its_widening(hub):  # QMS A3, fidelity 3
    co = new_co(hub, by="bob", title="Raise the cog limit")
    revise(hub, co, "finance-policy", OLD, OLD.replace("2,000", "5,000"), by="bob")
    cc.request_review(hub, co["number"], "bob", panel=[])  # management, named by the SOP, left off: a warning on the CO
    cc.send_for_approval(hub, co["number"], "bob")
    add = next(i for i in items(hub, co) if i["op"] == "add")
    assert add["widening"] and "bob" in add["makers"]
    # Whoever made a change is not its only approver: bob, a policy owner, may be one of the two owners on his widening.
    out = cc.decide_item(hub, co["number"], add["id"], "approve", "bob", "mine, checked", accept_warnings=["all"])
    assert out["state"] == "pending", "a maker never decides alone"
    assert any("needs two policy owners, one of whom contributed nothing to it (approved by bob)" in r for r in cc.approval_reasons(hub, co["number"]))
    out = cc.decide_item(hub, co["number"], add["id"], "approve", "carol", "ok", accept_warnings=["all"])
    assert out["state"] == "approved", "carol, who contributed nothing, is the other owner"
    rest = next(i for i in items(hub, co) if i["id"] != add["id"] and not i["widening"])
    with pytest.raises(ValueError, match="segregation of duties: bob made this change"):
        cc.decide_item(hub, co["number"], rest["id"], "approve", "bob", "mine is fine", accept_warnings=["all"])
    w = next(w for w in cc.co_warnings(hub, co["number"]))
    with pytest.raises(ValueError, match="segregation of duties: bob contributed"):
        cc.accept_warning(hub, co["number"], w["ref"], "bob", "not needed")


def test_contributors_do_not_review_and_reviewers_who_returned_results_stay(hub):  # governance H5, correctness M1
    co = new_co(hub, title="Document approvals")
    revise(hub, co, "document-control-sop", ROUTE_OLD, ROUTE_NEW)
    cc.request_review(hub, co["number"], "alice", panel=["qa-manager"])
    with pytest.raises(ValueError, match="segregation of duties: alice contributed"):
        cc.add_reviewer(hub, co["number"], "alice", "alice")
    with pytest.raises(ValueError, match="has reviewed this round already"):
        cc.remove_reviewer(hub, co["number"], "qa-manager", "alice")
    ws = {w["code"]: w for w in cc.co_warnings(hub, co["number"])}
    assert "panel" in ws and "quality-assistant" in ws["panel"]["text"], "leaving out a reviewer the SOP names is a warning to accept"


def test_only_the_committee_chair_or_a_system_administrator_answers_for_an_unavailable_member(hub):  # governance M1, QMS A4
    co = new_co(hub)
    revise(hub, co, "finance-policy", OLD, NEW)
    cc.request_review(hub, co["number"], "alice")
    p = hub.proposals.load(hub.kernel.get(co["locator"])["fields"]["staged_proposal"])
    q = question("finance-policy", 42, "senior-qa-manager could not read the change (its model is not available here)",
                 raised_by="hub", code="member-unavailable", chair="carol")
    p["questions"].append(q)
    hub.proposals.save(p)
    with pytest.raises(ValueError, match="only the committee's chair"):
        cc.answer_question(hub, co["number"], q["id"], "obvious", "alice")
    cc.answer_question(hub, co["number"], q["id"], "the line states no rule beyond the sentence", "carol")
    [w] = [w for w in cc.co_warnings(hub, co["number"]) if w["ref"] == q["id"]]
    assert w["accepted"]["by"] == "carol" and "answered by carol" in w["text"], "the intervention stays visible to the approver"


# ---------------------------------------------------------------- 3. release correctness
def test_an_unstated_direct_change_of_any_kind_comes_into_force_alone_in_its_slot(hub, monkeypatch):  # correctness C1
    monkeypatch.setattr(prose_drafting, "draft_direct_changes", lambda *a, **k: [])  # a drafter that writes nothing
    co = new_co(hub, title="Looser match")
    tol = next(r for r in hub.settings_rules() if r["kind"] == "threshold" and r["name"] == "match-tolerance")
    cc.add_direct_change(hub, co["number"], {**{k: v for k, v in tol.items() if k != "source"}, "value": 3.0}, "alice")
    [d] = hub.kernel.get(co["locator"])["fields"]["direct_changes"]
    assert d["rule"]["source"]["direct"] == d["id"], "a direct rule keeps a source"
    ready(hub, co, who=("carol", "bob"), panel=[])
    assert gate(hub, co)["status"] == "completed"
    [rel] = job(hub, co, "release-change-order")
    assert rel["status"] == "success", rel["outcome"]
    rules = [r for r in hub.settings_rules() if r["kind"] == "threshold" and r["name"] == "match-tolerance"]
    assert [r["value"] for r in rules] == [3.0] and hub.releases.current()["divergences"][0]["in_force"]["value"] == 3.0


def test_two_approved_items_for_one_rule_are_refused(hub):  # correctness C1 (s5)
    co = new_co(hub, title="Looser match")
    tol = next(r for r in hub.settings_rules() if r["kind"] == "threshold" and r["name"] == "match-tolerance")
    cc.add_direct_change(hub, co["number"], {**{k: v for k, v in tol.items() if k != "source"}, "value": 3.0}, "alice")
    cc.request_review(hub, co["number"], "alice", panel=[])
    draft = eco.carried(hub, hub.kernel.get(co["locator"]))[0]["draft"]
    write(hub, draft, "The match tolerance is 3 percent.", "The match tolerance is 4 percent.", "alice")
    cc.request_review(hub, co["number"], "alice", panel=[])
    cc.send_for_approval(hub, co["number"], "alice")
    decide_all(hub, co, who=("carol", "bob"))
    assert any("two approved items set the same rule" in r for r in cc.approval_reasons(hub, co["number"]))


def test_changing_the_configuration_back_resolves_a_divergence_and_later_compiles_keep_it(hub):  # correctness C3
    co1 = new_co(hub)
    revise(hub, co1, "finance-policy", OLD, NEW)
    cc.request_review(hub, co1["number"], "alice")
    cc.send_for_approval(hub, co1["number"], "alice")
    add = next(i for i in items(hub, co1) if i["op"] == "add")
    cc.decide_item(hub, co1["number"], add["id"], "approve", "bob", "1,200 agreed", override={"max_amount": 1200.0})
    decide_all(hub, co1)
    accept_co_warnings(hub, co1)
    assert gate(hub, co1)["status"] == "completed" and ap_limit(hub) == 1200.0
    [d1] = hub.releases.current()["divergences"]
    cr = next(r for r in hub.kernel.query(type_="change-request") if r["number"] == d1["change_request"])
    assert cr["assignees"] == ["alice"] and cr["due"], "the divergence's change request is the SOP owner's, with a due date"
    ap = next(r for r in hub.settings_rules() if r["kind"] == "approval_criteria" and r["approver"] == "cog:ap-reviewer" and r["process"] == "purchase-to-pay")
    co2 = new_co(hub, title="Back to 1,500")
    cc.add_direct_change(hub, co2["number"], {**{k: v for k, v in ap.items() if k != "source"}, "max_amount": 1500.0}, "alice")
    ready(hub, co2, who=("carol", "bob"), panel=[])
    assert gate(hub, co2)["status"] == "completed" and ap_limit(hub) == 1500.0
    meta = hub.releases.current()
    assert d1["id"] in meta["divergences_resolved"] and not [d for d in meta["divergences"] if d["id"] == d1["id"]]
    co3 = new_co(hub, title="Raise the controller threshold")
    revise(hub, co3, "finance-policy", "The controller approval threshold is 10,000 USD.", "The controller approval threshold is 12,000 USD.")
    cc.request_review(hub, co3["number"], "alice", panel=[])
    assert {i["rule"]["kind"] for i in items(hub, co3)} == {"threshold"}, "nothing puts the old 1,200 back"


def test_two_signed_change_orders_and_one_release_the_second_goes_back_to_revising(hub):  # correctness H1
    hub.queue.mode = "service"
    a = new_co(hub, title="Lower the AP cog limit")
    revise(hub, a, "finance-policy", OLD, NEW)
    b = new_co(hub, title="Shorter document approvals")
    revise(hub, b, "document-control-sop", "Approve the document in document-control is due within 10 days.",
           "Approve the document in document-control is due within 7 days.")
    for co in (a, b):
        cc.request_review(hub, co["number"], "alice", panel=[])
    hub.queue.drain()
    for co in (a, b):  # both signed before the first release job runs
        cc.send_for_approval(hub, co["number"], "alice")
        decide_all(hub, co, who=("carol", "bob"))
        accept_co_warnings(hub, co)
        assert gate(hub, co)["status"] == "completed"
    hub.queue.drain()
    assert hub.kernel.get(a["locator"])["state"] == "released"
    b = hub.kernel.get(b["locator"])
    assert b["state"] == "open" and cc.stage_of(b) == "revising" and "was released before it" in b["fields"]["reopened"][-1]["why"]
    [rel_b] = job(hub, b, "release-change-order")
    assert rel_b["status"] == "failed" and "not approved" in rel_b["outcome"]
    hub.queue.mode = "inline"
    cc.request_review(hub, b["number"], "alice", panel=[])
    assert cc.send_reasons(hub, b["number"]) == [], "it can be reviewed and sent again"


def test_an_override_that_narrows_a_widening_is_released_with_one_approver(hub):  # correctness H3
    co = new_co(hub)
    revise(hub, co, "finance-policy", OLD, OLD.replace("2,000", "3,000"))
    cc.request_review(hub, co["number"], "alice")
    cc.send_for_approval(hub, co["number"], "alice")
    add = next(i for i in items(hub, co) if i["op"] == "add")
    assert add["widening"]
    out = cc.decide_item(hub, co["number"], add["id"], "approve", "bob", "1,800 at most", override={"max_amount": 1800.0})
    assert out["override"]["widening"] is False
    decide_all(hub, co)
    accept_co_warnings(hub, co)
    assert cc.approval_reasons(hub, co["number"]) == []
    assert gate(hub, co)["status"] == "completed"
    assert job(hub, co, "release-change-order")[0]["status"] == "success" and ap_limit(hub) == 1800.0


def test_eco_release_is_the_release_automation(hub):  # correctness L2
    hub.queue.mode = "service"
    co = new_co(hub)
    revise(hub, co, "finance-policy", OLD, NEW)
    cc.request_review(hub, co["number"], "alice")
    hub.queue.drain()
    cc.send_for_approval(hub, co["number"], "alice")
    decide_all(hub, co)
    accept_co_warnings(hub, co)
    assert gate(hub, co)["status"] == "completed" and hub.kernel.get(co["locator"])["state"] == "approved"
    meta = eco.release(hub, co["number"], by="carol")
    assert meta["change_order"] == co["number"] and [j["status"] for j in job(hub, co, "release-change-order")] == ["success"]


# ---------------------------------------------------------------- 4. fidelity
def test_data_model_changes_carried_after_the_round_need_another_round(hub):  # governance M3
    from hubproto import recordtypes

    co = new_co(hub)
    revise(hub, co, "finance-policy", OLD, NEW)
    cc.request_review(hub, co["number"], "alice", panel=[])
    recordtypes.carry(hub, co["number"], [{"op": "add_field", "type": "ncr", "field": "batch", "spec": "text"}], by="alice")
    assert any("record-type changes changed since round 1" in r for r in cc.send_reasons(hub, co["number"]))


def test_new_work_a_member_s_roles_do_not_cover_is_a_suggested_role_grant(hub):  # fidelity 58, 59
    co = new_co(hub, title="Dave approves documents")
    revise(hub, co, "document-control-sop", ROUTE_OLD, "Approvals in document-control go to dave.")
    cc.request_review(hub, co["number"], "alice", panel=[])
    [s] = [s for s in cc.suggestions(hub, co["number"]) if s.get("kind") == "role-grant"]
    assert s["by"] == "hub" and "dave" in s["text"] and "reviewer" in s["change"]["rule"]["roles"]
    assert "reviewer" not in hub.current_bundle()["members"]["dave"]["roles"], "never automatic"
    ap = new_co(hub, title="Lower the AP cog limit")
    revise(hub, ap, "finance-policy", OLD, NEW)
    cc.request_review(hub, ap["number"], "alice", panel=[])
    assert "ap-reviewer" in cc.cog_impact(hub, ap["number"])
    cc.send_for_approval(hub, ap["number"], "alice")
    for i in items(hub, ap):
        cc.decide_item(hub, ap["number"], i["id"], "reject", "carol", "not now")
    left = cc.cog_impact(hub, ap["number"]).get("ap-reviewer") or []
    assert [x["item"].split(":")[0] for x in left] == ["text"], "rejected items leave the cog's view; the wording of the SOP it follows stays"


def test_a_change_to_a_cog_profile_gets_a_panel(hub):  # QMS B9
    out = hub.propose_cog_model("ap-reviewer", "claude-opus-5-5", by="bob")
    names = {e["who"] for e in cc.suggest_panel(hub, out["change_order"])}
    assert {"it-admin", "qa-manager"} <= names


def test_validation_annotates_the_items_when_the_module_is_there(hub):
    co = new_co(hub)
    revise(hub, co, "finance-policy", OLD, NEW)
    cc.request_review(hub, co["number"], "alice", panel=[])
    got = cc.validation_items(hub, co["number"])
    assert len(got) == len(cc.delta(hub, co["number"]))
    json.dumps(got, default=str)


def test_a_drafted_revision_is_the_qa_manager_s_so_a_settings_maker_may_sign_alongside_an_independent_approver(hub):
    co = new_co(hub, by="carol", title="Looser match")
    tol = next(r for r in hub.settings_rules() if r["kind"] == "threshold" and r["name"] == "match-tolerance")
    cc.add_direct_change(hub, co["number"], {**{k: v for k, v in tol.items() if k != "source"}, "value": 1.0}, "carol")
    cc.request_review(hub, co["number"], "carol", panel=[])
    [c] = eco.carried(hub, hub.kernel.get(co["locator"]))
    assert c["draft"]["fields"]["author"] == "qa-manager" and c["draft"]["fields"]["started_by"] == "carol"
    assert "carol" not in cc.revision_authors(hub, co["number"]) and "carol" in cc.contributors(hub, co["number"])


# ---------------------------------------------------------------- round 3
def test_a_revision_of_an_sop_comes_into_force_only_with_a_change_order(hub):  # manual agent: no SOP change outside a CO
    from hubproto import sopdocs

    policy = sopdocs.in_force(hub.kernel, "finance-policy")
    loose = hub.dms.create(title=policy["fields"]["title"], doc_class="controlled", folder=policy["folder"], by="alice", content=b"x",
                           filename="finance-policy.md", mime="text/markdown", doc_type="policy",
                           fields={"revises": policy["number"], "review_date": "2027-11-01"})
    with pytest.raises(KernelGuardError, match="only with the change order that carries it"):
        hub.kernel.transition(loose["locator"], "effective", by="alice")
    with pytest.raises(KernelGuardError, match="Carry it on a change order"):
        hub.kernel.publish_document(loose["number"], run_id="r", release="", grant={"run_id": "r", "id": "g"}, approved_by="carol", by="hub")
    assert sopdocs.in_force(hub.kernel, "finance-policy")["locator"] == policy["locator"]


def test_the_demo_change_order_is_written_by_bob_so_carol_may_sign_it(hub):
    from hubproto import dev_demo

    number = dev_demo.change_order(hub)
    [c] = eco.carried(hub, cc._co(hub, number))
    assert c["draft"]["fields"]["author"] == "bob" and "carol" not in cc.revision_authors(hub, number)


def test_a_widening_made_by_a_policy_owner_passes_with_the_other_independent_owner(hub):  # fidelity 25
    co = new_co(hub, by="carol", title="Shorter document approvals")
    sla = next(r for r in hub.settings_rules() if r["kind"] == "sla" and r["process"] == "document-control")
    cc.add_direct_change(hub, co["number"], {**{k: v for k, v in sla.items() if k != "source"}, "days": 7}, "carol")
    cc.request_review(hub, co["number"], "carol", panel=[])
    cc.send_for_approval(hub, co["number"], "carol")
    for it in items(hub, co):
        if it["widening"]:
            cc.decide_item(hub, co["number"], it["id"], "approve", "carol", "mine, checked", accept_warnings=["all"])
            assert cc.decide_item(hub, co["number"], it["id"], "approve", "bob", "agreed", accept_warnings=["all"])["state"] == "approved"


def test_the_chair_recording_unread_prose_as_a_divergence_records_one(hub):  # qms A4
    co = new_co(hub)
    revise(hub, co, "finance-policy", OLD, NEW)
    cc.request_review(hub, co["number"], "alice", panel=[])
    p = hub.proposals.load(hub.kernel.get(co["locator"])["fields"]["staged_proposal"])
    q = question("finance-policy", 42, "a sentence the hub cannot read", raised_by="hub", code="unread-rule", chair="carol", blocking=True,
                 quote="New suppliers' bills always go to the controller.")
    p["questions"].append(q)
    hub.proposals.save(p)
    assert any("answer the question first" in r for r in cc.approval_reasons(hub, co["number"]))
    with pytest.raises(ValueError, match="only a committee question"):
        cc.answer_question(hub, co["number"], "not-a-committee-question", "x", "alice", record_divergence=True)
    cc.answer_question(hub, co["number"], q["id"], "this is not a divergence by its words, but record it", "carol", record_divergence=True)
    cc.send_for_approval(hub, co["number"], "alice")
    decide_all(hub, co, who=("bob",))
    accept_co_warnings(hub, co, "bob")
    assert gate(hub, co)["status"] == "completed"
    [d] = hub.releases.current()["divergences"]
    assert d["kind"] == "unread" and d["accepted_by"] == "carol" and "controller" in d["prose"] and d["change_request"]


def test_a_dropped_reviewer_s_objection_stands(hub, monkeypatch):  # governance H5
    co = new_co(hub, title="Document approvals")
    revise(hub, co, "document-control-sop", ROUTE_OLD, ROUTE_NEW)
    monkeypatch.setattr(cc, "REVIEWER", lambda hub_, co_, cog, brief: "- [blocking] general: the routing leaves no reviewer")
    cc.request_review(hub, co["number"], "alice", panel=["qa-manager"])
    with pytest.raises(ValueError, match="qa-manager objected"):
        cc.request_review(hub, co["number"], "alice", panel=["quality-assistant"])
    cc.request_review(hub, co["number"], "alice", panel=["quality-assistant"], drop_reason="qa-manager is on leave")
    rnd = cc.current_round(hub.kernel.get(co["locator"]))
    assert rnd["dropped"] == [{"who": "qa-manager", "reason": "qa-manager is on leave", "by": "alice"}]
    assert any(w["code"] == "finding" and w["text"].startswith("qa-manager:") for w in cc.co_warnings(hub, co["number"])), "it stays a warning"


def test_a_carried_record_type_and_the_process_that_uses_it_compile_and_release_together(hub):  # correctness M3
    from hubproto import recordtypes

    co = new_co(hub, by="carol", title="Deviations")
    recordtypes.carry(hub, co["number"], [{"op": "add_type", "type": "deviation", "definition": {
        "prefix": "DEV", "digits": 4, "yearly": True, "object": "deviation", "retention": "quality", "sensitivity": "internal",
        "fields": {"title": "text", "description": "text"}}}], by="carol", reason="deviations")
    d = eco.add_sop(hub, co["number"], "ncr-sop", by="carol")
    write(hub, hub.kernel.get(d["locator"]), "## Roles and responsibilities",
          "## Process: deviation\nRecords are filed in Quality/Nonconformances.\nThe record type is deviation.\n1. [staff] Raise the deviation (record-filer)\n"
          "2. [quality-manager] Approve the deviation\n\n## Roles and responsibilities", "carol")
    cc.request_review(hub, co["number"], "carol", panel=[])
    co = hub.kernel.get(co["locator"])
    assert cc.current_round(co)["status"] != "failed" and not co["fields"].get("staged_errors")
    assert "deviation" not in hub.kernel.types, "nothing is in force before the release"


def test_a_settings_change_by_someone_who_may_not_revise_the_sop_says_who_must_carry_it(hub, monkeypatch):
    monkeypatch.setattr(prose_drafting, "draft_direct_changes", lambda *a, **k: [])
    co = new_co(hub, by="dave", title="Looser match")
    tol = next(r for r in hub.settings_rules() if r["kind"] == "threshold" and r["name"] == "match-tolerance")
    cc.add_direct_change(hub, co["number"], {**{k: v for k, v in tol.items() if k != "source"}, "value": 3.0}, "dave")
    cc.request_review(hub, co["number"], "dave", panel=[])
    [q] = [q for q in cc.questions(hub, co["number"]) if q.get("code") == "carrier"]
    assert q["blocking"] and "dave may not revise finance-policy" in q["text"] and "document controller" in q["text"]
    assert any("answer the question first" in r for r in cc.approval_reasons(hub, co["number"]))
