"""Change control v2: the change-order loop (docs/design/change-control.md, sections 3, 6 and 8; hubproto.changecontrol).

A change order is prepared, reviewed in rounds (each compiles the delta from the drafts as they stand, drafts the prose
for direct changes, suggests a panel from the change-control SOP and queues its reviews), revised, sent for approval;
the approvers decide each item of the delta, accepting its warnings; the quality manager's signature approves the
revisions it carries; the release records divergences, compiles every open change order again and sends related ones
back. No model is called: cogs answer with their stand-ins, or through the test seams."""

import shutil

import pytest

from conftest import ROOT, release_all
from hubproto import changecontrol as cc
from hubproto import eco, prose_drafting, sopdocs
from hubproto.app import Hub
from hubproto.rules import rule_key
from hubproto.runner import RunError
from test_eco import NEW, OLD, ap_limit, write

ROUTE_OLD = "Approvals in document-control go to the reviewer."
ROUTE_NEW = "Approvals in document-control go to the quality manager."
SLA_OLD = "Approve the document in document-control is due within 10 days."
SLA_NEW = "Approve the document in document-control is due within 7 days."


@pytest.fixture
def hub(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    sopdocs.convert(h, by="carol")
    for name in ("finance-policy", "document-control-sop", "change-control-sop"):  # alice, an author, owns these here
        h.kernel.set_fields(sopdocs.in_force(h.kernel, name)["locator"], {"owner": "alice"}, by="setup")
    return h


def new_co(hub, by="alice", title="Lower the AP cog limit", **fields):
    return hub.kernel.create("change-order", "Quality/Changes", {"title": title, "description": "why it changes", "requested_by": by, **fields}, by=by)


def revise(hub, co, sop, old, new, by="alice"):
    d = eco.add_sop(hub, co["number"], sop, by=by)
    write(hub, hub.kernel.get(d["locator"]), old, new, by)
    return hub.kernel.get(d["locator"])


def items(hub, co):
    return [i for i in cc.delta(hub, co["number"]) if i["op"] != "text"]


def decide_all(hub, co, who=("carol",), accept=("all",)):
    """Every item approved by each of ``who`` who did not make it (who made an item never decides it)."""
    for it in items(hub, co):
        for w in who:
            if w not in (it.get("makers") or []):
                cc.decide_item(hub, co["number"], it["id"], "approve", w, "checked against the SOP", accept_warnings=accept)
    validated(hub, co)


def validated(hub, co):
    """The IT SOP's validation: a passing test record for the change order's items (hubproto.validation)."""
    from hubproto import validation

    if validation.requirements(hub, co["number"]):
        validation.record_evidence(hub, co["number"], by="dave", kind="test-record", text="ran the changed rules on a test bill",
                                   expected="the new configuration applies", actual="it applied")


def accept_co_warnings(hub, co, who="carol"):
    for w in cc.co_warnings(hub, co["number"]):
        if not w.get("accepted"):
            cc.accept_warning(hub, co["number"], w["ref"], who, "seen and accepted")


def gate(hub, co, who="carol", verdict="approve", meaning="signed"):
    run = hub.runner.run_for_record(co["locator"])
    state = hub.runner.load(run["id"])
    hub.runner.decide(run["id"], who, verdict, meaning=meaning, payload_digest=hub.runner.seen_digest(state))
    return hub.runner.load(run["id"])


def released(hub, co, who=("carol",), panel=None, by=None):
    """Request review, send, decide every item, sign: the change order is released."""
    by = by or cc.requester(co)
    cc.request_review(hub, co["number"], by, panel=panel)
    cc.send_for_approval(hub, co["number"], by)
    decide_all(hub, co, who)
    accept_co_warnings(hub, co, who[0])
    assert gate(hub, co)["status"] == "completed"
    return hub.kernel.get(co["locator"])


def job(hub, co, automation):
    return [j for j in hub.automation.of(co["locator"]) if j["automation"] == automation]


def test_a_change_order_goes_round_the_loop_and_is_released(hub):
    co = new_co(hub)
    draft = revise(hub, co, "finance-policy", OLD, NEW)
    assert cc.stage_of(hub.kernel.get(co["locator"])) == "preparing"
    assert cc.send_reasons(hub, co["number"]) == ["no review round has run yet: request review"]
    with pytest.raises(ValueError, match="request review first"):
        cc.send_for_approval(hub, co["number"], "alice")
    with pytest.raises(ValueError, match="alice's change order"):
        cc.request_review(hub, co["number"], "dave")

    rnd = cc.request_review(hub, co["number"], "alice")
    co = hub.kernel.get(co["locator"])
    assert rnd["n"] == 1 and cc.stage_of(co) == "revising", "no panel member to wait for: straight to revising"
    [j] = job(hub, co, "compile-frame")  # the round is carried by the compile-frame Op, every step recorded
    assert j["status"] == "success" and [s["name"] for s in j["steps"]] == [
        "Collect the carried drafts and direct changes", "Compile the Frame", "Draft prose for direct changes", "Check the guards",
        "Stage it on the change order", "Suggest the panel", "Queue the panel's reviews", "Tell the people involved"]
    assert cc.current_round(co)["job"] == j["id"] and j["trigger"] == "alice requested review (round 1)"
    # The panel: a limit is reviewed by the management cog (a member since the cog model landed): asked, and its review is in.
    [m] = cc.current_round(co)["panel"]
    assert m["who"] == "management" and m["member"] and "limits" in m["reason"]
    assert cc.current_round(co)["results"]["management"]["status"] == "done"
    # The delta: the changed limit, read from alice's prose (the line and its words), and the wording change.
    add = next(i for i in items(hub, co) if i["op"] == "add")
    assert add["origin"]["kind"] == "prose" and add["origin"]["by"] == "alice" and add["origin"]["frame"] == "finance-policy"
    assert "1,500" in add["origin"]["quote"] and add["origin"]["line"] and add["state"] == "pending" and add["warnings"] == []
    assert any(i["op"] == "text" and i["state"] == "covered" for i in cc.delta(hub, co["number"]))
    assert cc.send_reasons(hub, co["number"]) == []

    state = cc.send_for_approval(hub, co["number"], "alice")
    co = hub.kernel.get(co["locator"])
    assert state["pending"]["assignees"] == ["carol"] and cc.stage_of(co) == "awaiting-approval" and co["fields"]["contributor"] == ["alice"]
    with pytest.raises(RunError, match="not decided yet"):
        gate(hub, co)
    with pytest.raises(ValueError, match="meaning"):
        cc.decide_item(hub, co["number"], add["id"], "approve", "carol", "")
    with pytest.raises(ValueError, match="not a person"):
        cc.decide_item(hub, co["number"], add["id"], "approve", "qa-manager", "ok")
    with pytest.raises(ValueError, match="neither a policy owner"):
        cc.decide_item(hub, co["number"], add["id"], "approve", "dave", "ok")
    decide_all(hub, co)
    assert cc.approval_reasons(hub, co["number"]) == []
    assert gate(hub, co)["status"] == "completed"

    [rel] = job(hub, co, "release-change-order")
    assert rel["status"] == "success", rel
    assert [s["name"] for s in rel["steps"]][:3] == ["Check the change order is ready", "Approve the carried revisions", "Bring the Frame into force"]
    assert ap_limit(hub) == 1500.0
    co = hub.kernel.get(co["locator"])
    assert co["state"] == "released" and cc.stage_of(co) == "released"
    d = hub.kernel.get(draft["locator"])
    assert d["state"] == "effective" and d["fields"]["approved_by"] == "carol" and d["fields"]["approved_with"] == co["number"]
    sig = d["decisions"][-1]
    assert sig["by"] == "carol" and sig["via"] == co["number"] and sig["step"] == "approve-the-change-order", \
        "the CO signature is recorded on the revision as its document-control approval"
    assert hub.releases.current()["change_order"] == co["number"] and hub.releases.current()["divergences"] == []


def test_compiling_moves_from_document_control_to_the_review_round(hub):
    co = new_co(hub)
    draft = revise(hub, co, "finance-policy", OLD, NEW)
    run = hub.runner.submit("document-control", {"record": {"change_type": "minor"}}, owner="alice", record=draft["locator"])
    state = hub.runner.load(run)
    hub.runner.decide(run, state["pending"]["assignees"][0], "approve", meaning="ok", payload_digest=hub.runner.seen_digest(state))
    assert hub.kernel.get(draft["locator"])["fields"]["held_for"] == co["number"]
    assert not job(hub, co, "compile-frame") and not hub.kernel.get(co["locator"])["fields"].get("staged_proposal"), \
        "approving a revision through document control no longer compiles anything"
    cc.request_review(hub, co["number"], "alice")
    first = hub.kernel.get(co["locator"])["fields"]["staged_proposal"]
    assert first and any("1,500" in (i["origin"].get("quote") or "") for i in items(hub, co))
    cc.request_review(hub, co["number"], "alice")
    co = hub.kernel.get(co["locator"])
    assert co["fields"]["staged_proposal"] == first and len(cc.rounds(co)) == 2, "nothing changed since round 1: not compiled again"
    assert "not compiled again" in job(hub, co, "compile-frame")[0]["steps"][1]["detail"]


def test_the_panel_is_suggested_from_the_change_control_sop(hub, monkeypatch):
    section = cc.review_section(hub)
    assert "A change that touches permissions, record types or forms is reviewed by the it-admin cog." in section
    p = hub.compile(save=False)
    assert not [n for n in p["notes"] if n["frame"] == "change-control-sop"], "the section is narrative to the compiler"
    assert not [r for r in p["rules"] if (r.get("source") or {}).get("frame") == "change-control-sop"
                and "reviewed by" in (r.get("source") or {}).get("text", "")]
    quality = new_co(hub, title="Route document approvals to the quality manager")
    revise(hub, quality, "document-control-sop", ROUTE_OLD, ROUTE_NEW)
    cc.compile_delta(hub, quality["number"])
    panel = {e["who"]: e for e in cc.suggest_panel(hub, quality["number"])}
    assert set(panel) == {"qa-manager", "quality-assistant"} and all(e["member"] and e["kind"] == "cog" for e in panel.values())
    assert "quality process" in panel["qa-manager"]["reason"] and panel["qa-manager"]["suggested_by"] == "quality-assistant"
    perms = new_co(hub, by="bob", title="Clerks read less")
    rule = next(r for r in hub.settings_rules() if r["kind"] == "permission" and (r.get("source") or {}).get("text") == "Finance clerks may read Finance.")
    cc.add_direct_change(hub, perms["number"], {"op": "remove", "rule": rule}, "bob")
    [e] = cc.suggest_panel(hub, perms["number"])
    assert e["who"] == "it-admin" and e["member"] and e["kind"] == "cog" and "permissions" in e["reason"]
    # A name the section gives that is not a member is suggested (shown) but cannot be asked.
    monkeypatch.setattr(cc, "review_section", lambda hub_: "A change that touches permissions is reviewed by the auditor-bot cog.")
    [e] = cc.suggest_panel(hub, perms["number"])
    assert e["who"] == "auditor-bot" and not e["member"] and e["kind"] == "cog"
    cc.request_review(hub, perms["number"], "bob")
    rnd = cc.current_round(hub.kernel.get(perms["locator"]))
    assert [x["who"] for x in rnd["panel"]] == ["auditor-bot"] and rnd["results"] == {} and rnd["status"] == "reviewed"


def test_cog_reviews_land_in_the_round_and_round_two_sees_only_the_delta_of_the_delta(hub):
    co = new_co(hub, title="Document approvals")
    draft = revise(hub, co, "document-control-sop", ROUTE_OLD, ROUTE_NEW)
    cc.request_review(hub, co["number"], "alice")
    co = hub.kernel.get(co["locator"])
    rnd = cc.current_round(co)
    assert {e["who"] for e in rnd["panel"]} == {"qa-manager", "quality-assistant"} and rnd["status"] == "reviewed"
    assert all(r["status"] == "done" and r["kind"] == "cog" and r["stand_in"] for r in rnd["results"].values())
    tasks = hub.queue.list(record=co["locator"])
    assert {t["cog"] for t in tasks if t["kind"] == "co-review" and t["status"] == "done"} == {"qa-manager", "quality-assistant"}
    route = next(i for i in items(hub, co) if i["op"] == "add")
    [f1] = rnd["results"]["qa-manager"]["findings"]
    assert f1["severity"] == "advisory" and f1["about"] == {"item": route["id"]} and "both policy owners" in f1["text"]
    assert cc.stage_of(co) == "revising"

    write(hub, hub.kernel.get(draft["locator"]), SLA_OLD, SLA_NEW, "alice")
    assert any("revisions changed since round 1" in r for r in cc.send_reasons(hub, co["number"]))
    cc.request_review(hub, co["number"], "alice")
    co = hub.kernel.get(co["locator"])
    res = cc.current_round(co)["results"]["qa-manager"]
    sla = {i["id"] for i in items(hub, co) if i["rule"]["kind"] == "sla"}
    assert res["since"] == 1 and {f["about"].get("item") for f in res["findings"]} <= sla, "only what changed since its round-1 review"
    b = cc.brief(hub, co["number"], 3, "qa-manager")
    assert {c["id"] for c in b["items"]} == set() and b["since"] == 2 and "Your findings in round 2" in b["text"]


def test_people_review_ask_and_suggest_and_the_author_answers(hub):
    co = new_co(hub, title="Document approvals")
    revise(hub, co, "document-control-sop", ROUTE_OLD, ROUTE_NEW)
    cc.request_review(hub, co["number"], "alice", panel=["qa-manager"])
    co = hub.kernel.get(co["locator"])
    assert [e["who"] for e in cc.current_round(co)["panel"]] == ["qa-manager"], "the requester's panel replaces the suggestion"
    assert {e["who"] for e in cc.current_round(co)["suggested"]} == {"qa-manager", "quality-assistant"}, "the suggestion is still recorded"
    with pytest.raises(ValueError, match="not a member"):
        cc.add_reviewer(hub, co["number"], "auditor-bot", "alice")
    cc.add_reviewer(hub, co["number"], "bob", "alice", reason="he signs the widenings")
    co = hub.kernel.get(co["locator"])
    assert cc.stage_of(co) == "in-review" and cc.current_round(co)["results"]["bob"]["status"] == "queued"
    assert any(m["to"] == "bob" and m["kind"] == "review" and co["number"] in m["subject"] for m in hub.inbox.all())
    route = next(i for i in items(hub, co) if i["op"] == "add")
    cc.submit_review(hub, co["number"], "bob", findings=[{"severity": "blocking", "text": "a quality manager is not a reviewer", "about": {"item": route["id"]}}],
                     questions=["Who approves when the quality manager is away?"],
                     suggestions=[{"frame": "document-control-sop", "section": "Routing", "sentence": "Overdue items in document-control go to the quality manager."}])
    co = hub.kernel.get(co["locator"])
    assert cc.stage_of(co) == "revising" and cc.current_round(co)["status"] == "reviewed"
    route = next(i for i in items(hub, co) if i["id"] == route["id"])
    assert {w["code"] for w in route["warnings"]} == {"finding"} and any(w["text"].startswith("bob:") and w["severity"] == "blocking" for w in route["warnings"])
    assert any(w["text"].startswith("qa-manager:") and w["severity"] == "advisory" for w in route["warnings"]), "an advisory finding is a warning too (P.10)"
    q = next(q for q in cc.questions(hub, co["number"]) if q["raised_by"] == "bob")
    assert any(w["code"] == "question" and w["ref"] == q["id"] for w in cc.co_warnings(hub, co["number"]))
    cc.answer_question(hub, co["number"], q["id"], "the management representative, as Overdue items says", "alice", line=50)
    assert not any(w["code"] == "question" for w in cc.co_warnings(hub, co["number"]))
    [s] = cc.suggestions(hub, co["number"])
    assert s["change"] and s["change"]["rule"]["kind"] == "routing" and s["status"] == "open"
    with pytest.raises(ValueError):
        cc.accept_suggestion(hub, co["number"], s["id"], "dave")
    cc.accept_suggestion(hub, co["number"], s["id"], "alice")
    co = hub.kernel.get(co["locator"])
    [d] = co["fields"]["direct_changes"]
    assert d["origin"]["kind"] == "suggested" and d["origin"]["by"] == "bob" and d["origin"]["accepted_by"] == "alice"
    assert cc.suggestions(hub, co["number"])[0]["status"] == "accepted"
    assert any("direct changes changed since round 1" in r for r in cc.send_reasons(hub, co["number"]))
    cc.request_review(hub, co["number"], "alice", panel=[])
    stated = next(i for i in items(hub, co) if i.get("direct") == d["id"])
    assert stated["origin"]["kind"] == "suggested" and stated["origin"]["frame"] == "document-control-sop" and stated["origin"]["line"], \
        "the qa-manager wrote it into the SOP and it reads back"


def test_every_warning_is_accepted_and_a_widening_needs_both_policy_owners(hub, monkeypatch):
    co = new_co(hub, title="Document approvals")
    revise(hub, co, "document-control-sop", ROUTE_OLD, ROUTE_NEW)

    def reviewer(hub_, co_, cog, brief):
        add = next(c for c in brief["items"] if c.get("op") == "add")
        return f"- [blocking] {add['id']}: the routing leaves no reviewer\n- [question] general: is this a major change?\n- [advisory] general: fine otherwise"
    monkeypatch.setattr(cc, "REVIEWER", reviewer)
    cc.request_review(hub, co["number"], "alice", panel=["qa-manager"])
    cc.send_for_approval(hub, co["number"], "alice")
    route = next(i for i in items(hub, co) if i["op"] == "add")
    assert route["widening"] and [w["code"] for w in route["warnings"]] == ["finding"]
    with pytest.raises(ValueError, match="accept each warning"):
        cc.decide_item(hub, co["number"], route["id"], "approve", "carol", "routing checked")
    ref = route["warnings"][0]["ref"]
    out = cc.decide_item(hub, co["number"], route["id"], "approve", "carol", "routing checked", accept_warnings=[ref])
    assert out["state"] == "pending" and out["decisions"][-1]["accepted_warnings"] == [ref]
    assert any("needs two policy owners who did not make it (approved by carol)" in r for r in cc.approval_reasons(hub, co["number"]))
    cc.decide_item(hub, co["number"], route["id"], "approve", "bob", "routing checked")  # carol accepted it already
    rest = [i for i in items(hub, co) if i["id"] != route["id"]]
    for i in rest:
        cc.decide_item(hub, co["number"], i["id"], "approve", "carol", "ok")
    validated(hub, co)
    reasons = cc.approval_reasons(hub, co["number"])
    ws = [w for w in cc.co_warnings(hub, co["number"]) if not w.get("accepted")]
    assert {w["code"] for w in ws} >= {"question", "finding"}, "a general question and an advisory finding are warnings on the change order"
    assert reasons == [f"accept the warning: [{w['code']}] {w['text']}" for w in ws]
    for w in ws:
        cc.accept_warning(hub, co["number"], w["ref"], "carol", "minor enough")
    assert cc.approval_reasons(hub, co["number"]) == []
    assert gate(hub, co)["status"] == "completed"
    assert job(hub, co, "release-change-order")[0]["status"] == "success"


def test_an_override_leaves_a_divergence_that_later_compiles_keep_until_one_resolves_it(hub):
    co = new_co(hub)
    revise(hub, co, "finance-policy", OLD, NEW)
    cc.request_review(hub, co["number"], "alice")
    cc.send_for_approval(hub, co["number"], "alice")
    add = next(i for i in items(hub, co) if i["op"] == "add")
    with pytest.raises(ValueError, match="give it with approve"):
        cc.decide_item(hub, co["number"], add["id"], "reject", "carol", "no", override={"max_amount": 1200.0})
    with pytest.raises(ValueError, match="keeps its kind"):
        cc.decide_item(hub, co["number"], add["id"], "approve", "carol", "x", override={"kind": "sla", "process": "purchase-to-pay", "step": "approve", "days": 60})
    out = cc.decide_item(hub, co["number"], add["id"], "approve", "bob", "1,200 is what we agreed", override={"max_amount": 1200.0})
    assert out["state"] == "pending" and "bob" in out["makers"] and [w["code"] for w in out["warnings"]] == ["override"]
    assert "bob" in cc.contributors(hub, co["number"]), "an overrider contributed the value"
    with pytest.raises(ValueError, match="segregation of duties: bob made this change"):
        cc.decide_item(hub, co["number"], add["id"], "approve", "bob", "fine", accept_warnings=["override"])
    decide_all(hub, co)  # carol approves the overridden value (and accepts its warning)
    assert gate(hub, co)["status"] == "completed"
    assert ap_limit(hub) == 1200.0, "the override is in force"
    meta = hub.releases.current()
    [d] = meta["divergences"]
    assert d["frame"] == "finance-policy" and "1,500" in d["prose"] and d["in_force"]["max_amount"] == 1200.0
    assert d["accepted_by"] == "bob" and d["co"] == co["number"] and d["status"] == "open" and d["change_request"]
    cr = next(r for r in hub.kernel.query(type_="change-request") if r["number"] == d["change_request"])
    assert cr["fields"]["requested_by"] == "alice" and cr["fields"]["divergence"] == d["id"] and "line" in cr["fields"]["title"]
    assert any(m["to"] == "alice" and cr["number"] in m["subject"] for m in hub.inbox.all())
    # Compiling again keeps the rule in force for that slot, with a warning naming the divergence.
    p = hub.compile(save=False)
    assert not [c for c in p["changes"] if c["rule"].get("kind") == "approval_criteria"]
    assert any(f["guard"] == "divergence" and d["id"] in f["detail"] for f in p["findings"])
    # A later change order elsewhere in the same SOP keeps it too; its approver accepts the divergence warning.
    later = new_co(hub, title="Raise the controller threshold")
    revise(hub, later, "finance-policy", "The controller approval threshold is 10,000 USD.", "The controller approval threshold is 12,000 USD.")
    cc.request_review(hub, later["number"], "alice")
    assert {i["rule"]["kind"] for i in items(hub, later)} == {"threshold"}
    cc.send_for_approval(hub, later["number"], "alice")
    decide_all(hub, later, who=("carol", "bob"))
    [w] = cc.co_warnings(hub, later["number"])
    assert w["code"] == "divergence" and any("accept the warning: [divergence]" in r for r in cc.approval_reasons(hub, later["number"]))
    cc.accept_warning(hub, later["number"], w["ref"], "carol", "the 1,200 limit stays")
    assert gate(hub, later)["status"] == "completed"
    assert ap_limit(hub) == 1200.0 and [x["id"] for x in hub.releases.current()["divergences"]] == [d["id"]]
    # A change order that brings the prose in line resolves it.
    fix = new_co(hub, title="Say 1,200")
    revise(hub, fix, "finance-policy", NEW, OLD.replace("2,000", "1,200"))
    cc.request_review(hub, fix["number"], "alice")
    assert items(hub, fix) == [], "the prose now says what is in force: no rule changes"
    cc.send_for_approval(hub, fix["number"], "alice")
    validated(hub, fix)
    assert gate(hub, fix)["status"] == "completed"
    meta = hub.releases.current()
    assert meta["divergences"] == [] and meta["divergences_resolved"] == [d["id"]] and ap_limit(hub) == 1200.0


def test_settings_edits_are_direct_changes_written_into_the_prose_by_the_review_round(hub):
    rules = hub.settings_draft("bob")["rules"]
    ap = next(r for r in rules if r["kind"] == "approval_criteria" and r["approver"] == "cog:ap-reviewer" and r["process"] == "purchase-to-pay")
    hub.settings_edit("bob", {"action": "replace", "key": rule_key(ap), "rule": {**{k: v for k, v in ap.items() if k != "source"}, "max_amount": 3000.0}})
    out = hub.settings_propose("bob", reason="tune limits")
    assert out["change_order"] and all(r["ok"] for r in out["report"]) and "3,000" in out["report"][0]["says"]
    co = hub.kernel.get(out["locator"])
    assert co["fields"]["from_settings"] and eco.carried(hub, co) == [], "Settings no longer writes the prose"
    [d] = co["fields"]["direct_changes"]
    assert d["op"] == "change" and d["origin"]["kind"] == "direct" and d["origin"]["by"] == "bob" and d["widening"]
    assert d["origin"]["frame"] == "finance-policy" and cc.contributors(hub, co["number"]) == ["bob"]
    again = hub.settings_draft("bob")
    th = next(r for r in again["rules"] if r["kind"] == "threshold" and r["name"] == "purchase-approval-threshold")
    hub.settings_edit("bob", {"action": "replace", "key": rule_key(th), "rule": {**{k: v for k, v in th.items() if k != "source"}, "value": 8000.0}})
    assert hub.settings_propose("bob")["change_order"] == co["number"], "the same open settings change order collects the next changes"
    cc.request_review(hub, co["number"], "bob")
    co = hub.kernel.get(co["locator"])
    assert sorted(c["name"] for c in eco.carried(hub, co)) == ["finance-policy", "purchasing-policy"]
    fin = sopdocs.frame_text(hub, next(c["draft"] for c in eco.carried(hub, co) if c["name"] == "finance-policy"))
    assert "under 3,000 USD" in fin
    direct = [i for i in items(hub, co) if i.get("direct")]
    assert len(direct) >= 2 and all(i["origin"]["kind"] == "direct" and i["origin"]["line"] and not i.get("unstated") for i in direct)
    assert all(x["round_trip"]["ok"] for x in co["fields"]["direct_changes"]), "the prose reads back as each direct change"
    assert "qa-manager" in cc.contributors(hub, co["number"]) and "bob" in cc.contributors(hub, co["number"])


def test_a_direct_change_the_prose_does_not_state_is_a_round_trip_question_and_then_a_divergence(hub, monkeypatch):
    from hubproto.compile import question

    def draft_nothing(hub_, co_ref, by, *, call=None, extractor=None):  # a drafter that writes nothing (as if it failed)
        co_ = eco._co(hub_, co_ref)
        return [question("finance-policy", 0, f"could not write {i['id']}", raised_by="qa-manager", code="round-trip", item=i["id"])
                for i in co_["fields"].get("direct_changes") or []]
    monkeypatch.setattr(prose_drafting, "draft_direct_changes", draft_nothing)
    co = new_co(hub, by="bob", title="Controllers read less")
    rule = next(r for r in hub.settings_rules() if r["kind"] == "permission" and (r.get("source") or {}).get("text") == "Controllers may read Finance.")
    cc.add_direct_change(hub, co["number"], {"op": "remove", "rule": rule}, "bob")
    cc.request_review(hub, co["number"], "bob", panel=[])
    co = hub.kernel.get(co["locator"])
    assert cc.current_round(co)["drafted"]["by"] == "the qa-manager cog"
    [it] = items(hub, co)
    assert it["unstated"] and it["op"] == "remove" and [w["code"] for w in it["warnings"]] == ["round-trip"]
    assert any(q["code"] == "round-trip" and q["item"] == it["id"] and q["status"] == "open" for q in cc.questions(hub, co["number"]))
    cc.send_for_approval(hub, co["number"], "bob")
    with pytest.raises(ValueError, match="round-trip"):
        cc.decide_item(hub, co["number"], it["id"], "approve", "carol", "fine")
    cc.decide_item(hub, co["number"], it["id"], "approve", "carol", "fine", accept_warnings=["round-trip"])
    accept_co_warnings(hub, co)  # it-admin, named by the SOP for permissions, was left off the panel
    validated(hub, co)
    assert gate(hub, co)["status"] == "completed"
    b = hub.current_bundle()
    assert not any(p["subject"] == "role:controller" and p["action"] == "read" and p["resource"] == "folder:Finance" for p in b["permissions"])
    [d] = hub.releases.current()["divergences"]
    assert d["in_force"] is None and d["prose_rule"]["subject"] == "role:controller" and d["prose"] == "Controllers may read Finance."
    p = hub.compile(save=False)
    assert not [c for c in p["changes"] if c["rule"].get("kind") == "permission"], "a later compile keeps it removed"


def test_the_approver_may_not_be_a_contributor_unless_an_independent_approver_decided_every_item(hub):
    co = new_co(hub)
    revise(hub, co, "finance-policy", OLD, NEW)
    read = next(r for r in hub.settings_rules() if r["kind"] == "permission" and (r.get("source") or {}).get("text") == "Controllers may read Finance.")
    cc.add_direct_change(hub, co["number"], {"op": "remove", "rule": read}, "alice")  # a narrowing: one approver
    assert cc.contributors(hub, co["number"]) == ["alice"]
    cc.request_review(hub, co["number"], "alice", panel=[])
    cc.send_for_approval(hub, co["number"], "alice")
    rule = next(s for s in hub.current_bundle()["sod"] if s["object"] == "change-order")
    assert rule["roles"] == ["contributor", "approver"] and cc.SOD_SENTENCE in rule["source"]["text"]
    # A contributor who is not a revision author may sign once every item has an independent approver.
    hub.kernel.set_fields(co["locator"], {"direct_changes": [{**d, "origin": {**d["origin"], "by": "carol"}}
                                                             for d in hub.kernel.get(co["locator"])["fields"]["direct_changes"]]}, by="test")
    hub.kernel.set_fields(co["locator"], {"frozen": {**hub.kernel.get(co["locator"])["fields"]["frozen"],
                                                     "items": cc.content_digest(hub, co["number"])["items"]}}, by="test")
    cc.compile_delta(hub, co["number"])  # as if carol had made the direct change
    assert "carol" in cc.contributors(hub, co["number"]) and "carol" in hub.kernel.get(co["locator"])["fields"]["contributor"]
    state = hub.runner.load(hub.runner.run_for_record(co["locator"])["id"])
    assert "contributor" in hub.runner.excluded(hub.current_bundle(), state).get("carol", ""), "the SOP sentence keeps her off the Gate"
    with pytest.raises(ValueError, match="segregation of duties: carol contributed"):
        cc.accept_warning(hub, co["number"], cc.co_warnings(hub, co["number"])[0]["ref"], "carol", "fine by me")
    decide_all(hub, co, who=("bob",))  # bob, who contributed nothing, approves every item
    accept_co_warnings(hub, co, "bob")
    excluded = hub.kernel.get(co["locator"])["fields"]["contributor"]
    assert "carol" not in excluded and "alice" in excluded, "now carol may sign; alice (and the qa-manager) wrote the revision"
    assert cc.approval_reasons(hub, co["number"]) == [] and "carol" not in cc.may_not_sign(hub, co["number"])
    # The runner keeps every contributor off the Gate (it reads changecontrol.contributors): carol is still refused there.
    with pytest.raises(RunError, match="segregation of duties"):
        gate(hub, co)
    hub.kernel.set_fields(co["locator"], {"direct_changes": [{**d, "origin": {**d["origin"], "by": "alice"}}
                                                             for d in hub.kernel.get(co["locator"])["fields"]["direct_changes"]]}, by="test")
    assert gate(hub, co)["status"] == "completed"
    original = cc.may_not_sign
    try:  # the release re-checks it (the backstop when a release lacks the sentence)
        cc.may_not_sign = lambda hub_, co_ref: ["alice", "carol"]
        with pytest.raises(ValueError, match="segregation of duties"):
            cc.approve_revisions(hub, co["number"])
    finally:
        cc.may_not_sign = original


def test_a_rejected_approval_goes_back_to_revising(hub):
    co = new_co(hub)
    revise(hub, co, "finance-policy", OLD, NEW)
    cc.request_review(hub, co["number"], "alice")
    cc.send_for_approval(hub, co["number"], "alice")
    assert gate(hub, co, verdict="reject", meaning="say why the limit drops")["status"] != "waiting"
    co = cc.sync(hub, co["number"])
    assert co["state"] == "open" and cc.stage_of(co) == "revising" and co["fields"]["rejected"]["by"] == "carol"
    cc.request_review(hub, co["number"], "alice")
    state = cc.send_for_approval(hub, co["number"], "alice")
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["carol"]


def test_a_state_items_are_in_needs_a_state_map_before_approval(hub):
    run = hub.runner.submit("change-control", {"record": {"title": "Move inspection", "description": "x", "impact": "inspection WI"}}, owner="alice")
    state = hub.runner.load(run)
    hub.runner.decide(run, "carol", "approve", meaning="ok", payload_digest=hub.runner.seen_digest(state))
    cr = hub.kernel.get(hub.runner.load(run)["record"])
    assert cr["state"] == "awaiting-verify-the-change"
    co = new_co(hub, title="Confirm, not verify")
    d = revise(hub, co, "change-control-sop", "5. [staff] Verify the change", "5. [staff] Confirm the change")
    write(hub, d, "At Verify the change in change-control go to the requester.", "At Confirm the change in change-control go to the requester.", "alice")
    cc.request_review(hub, co["number"], "alice", panel=[])
    [q] = cc.state_questions(hub, co["number"])
    assert q == {"process": "change-control", "state": "awaiting-verify-the-change", "count": 1, "record_type": "change-request", "answer": None}
    assert any("which this change removes from change-control" in r for r in cc.approval_reasons(hub, co["number"]))
    cc.set_state_map(hub, co["number"], "change-control", {"awaiting-verify-the-change": "awaiting-confirm-the-change"}, "alice")
    assert not any("state map" in r for r in cc.approval_reasons(hub, co["number"]))


def test_a_release_recompiles_every_open_change_order_and_sends_related_ones_back(hub, monkeypatch):
    a = new_co(hub, title="Lower the AP cog limit")
    revise(hub, a, "finance-policy", OLD, NEW)
    # b changes the same SOP (a direct change homed in finance-policy): related by definition.
    b = new_co(hub, by="bob", title="Looser match")
    tol = next(r for r in hub.settings_rules() if r["kind"] == "threshold" and r["name"] == "match-tolerance")
    cc.add_direct_change(hub, b["number"], {**{k: v for k, v in tol.items() if k != "source"}, "value": 3.0}, "bob")
    cc.request_review(hub, b["number"], "bob", panel=[])
    cc.send_for_approval(hub, b["number"], "bob")
    decide_all(hub, b, who=("carol", "bob"))  # carol approves it (bob made it, so his decision would not count)
    assert [d["by"] for d in items(hub, b)[0]["decisions"]] == ["carol"]
    # c changes something else entirely: compiled again, its decisions kept.
    c = new_co(hub, by="bob", title="Tasks")
    task_rule = next(r for r in hub.settings_rules() if r["kind"] == "permission" and (r.get("source") or {}).get("text") == "Staff may approve tasks in Tasks.")
    cc.add_direct_change(hub, c["number"], {"op": "remove", "rule": task_rule}, "bob")
    cc.request_review(hub, c["number"], "bob", panel=[])
    decide_all(hub, c)
    base_before = hub.kernel.get(c["locator"])["fields"]["staged_base"]

    released(hub, a)
    [rel] = job(hub, a, "release-change-order")
    assert rel["status"] == "success" and b["number"] in next(s for s in rel["steps"] if s["name"] == "Send related changes back")["detail"]
    b = hub.kernel.get(b["locator"])
    bc = b["fields"]["baseline_changed"]
    assert bc["by"] == a["number"] and bc["kind"] == "definition" and any("finance-policy" in r for r in bc["reasons"])
    assert cc.stage_of(b) == "revising" and any("the baseline changed" in r for r in cc.send_reasons(hub, b["number"]))
    assert any("decide it again" in r for r in cc.approval_reasons(hub, b["number"])), "its decisions are stale"
    assert any(m["to"] == "bob" and "goes back to revising" in m["subject"] for m in hub.inbox.all())
    assert any(m["to"] == "carol" and "cannot be signed now" in m["subject"] for m in hub.inbox.all()), "its Gate waits: the approver is told"
    with pytest.raises(ValueError, match="by definition"):
        cc.dismiss_related(hub, b["number"], "bob", "not related", "fine")
    c = hub.kernel.get(c["locator"])
    assert not c["fields"].get("baseline_changed") and job(hub, c, "compile-frame")[0]["status"] == "success"
    assert c["fields"]["staged_base"] == hub.releases.current()["digest"] != base_before, "compiled again against the new release"
    assert cc.approval_reasons(hub, c["number"]) == [] or all("decide" not in r for r in cc.approval_reasons(hub, c["number"])), \
        "an unrelated change order keeps its decisions"
    cc.request_review(hub, b["number"], "bob", panel=[])
    assert not any("baseline" in r for r in cc.send_reasons(hub, b["number"])), "another round clears the flag"


def test_related_by_meaning_flags_change_orders_and_change_requests_and_may_be_dismissed(hub, monkeypatch):
    run = hub.runner.submit("change-control", {"record": {"title": "AP review", "description": "x", "impact": "finance-policy"}}, owner="alice")
    state = hub.runner.load(run)
    hub.runner.decide(run, "carol", "approve", meaning="ok", payload_digest=hub.runner.seen_digest(state))
    cr = hub.kernel.get(hub.runner.load(run)["record"])
    d = new_co(hub, by="bob", title="Tasks")
    task_rule = next(r for r in hub.settings_rules() if r["kind"] == "permission" and (r.get("source") or {}).get("text") == "Staff may approve tasks in Tasks.")
    cc.add_direct_change(hub, d["number"], {"op": "remove", "rule": task_rule}, "bob")
    cc.request_review(hub, d["number"], "bob", panel=[])
    seen = []

    def meaning(hub_, cog, released_co, candidates):
        seen.append((cog, sorted(c["number"] for c in candidates)))
        return [(d["number"], "fewer people approve tasks while the AP limit drops"), (cr["number"], "the AP review should cover the new limit")]
    monkeypatch.setattr(cc, "MEANING", meaning)
    a = new_co(hub, title="Lower the AP cog limit")
    revise(hub, a, "finance-policy", OLD, NEW)
    released(hub, a, panel=["qa-manager"])
    assert seen and seen[0][0] == "qa-manager" and cr["number"] in seen[0][1]
    d = hub.kernel.get(d["locator"])
    assert d["fields"]["baseline_changed"]["kind"] == "meaning" and d["fields"]["baseline_changed"]["cog"] == "qa-manager"
    cr = hub.kernel.get(cr["locator"])
    fl = cr["fields"]["flagged"]
    assert fl["by"] == a["number"] and fl["approver"] == "carol" and fl["task"]
    e = new_co(hub, by="alice", title="Implements the CR", implements=[cr["number"]])
    revise(hub, e, "document-control-sop", SLA_OLD, SLA_NEW)
    cc.request_review(hub, e["number"], "alice", panel=[])
    assert [r for r in cc.send_reasons(hub, e["number"]) if cr["number"] in r] == [f"{cr['number']} is flagged by {a['number']}: the AP review "
                                                                                  f"should cover the new limit (until {fl['task']} is done)"]
    task = next(t for t in hub.kernel.query(type_="task") if t["number"] == fl["task"])
    trun = hub.runner.run_for_record(task["locator"])
    tstate = hub.runner.load(trun["id"])
    assert tstate["pending"]["assignees"] == ["carol"]
    hub.runner.decide(trun["id"], "carol", "approve", meaning="reviewed again", payload_digest=hub.runner.seen_digest(tstate))
    assert not cc.cr_flag_open(hub, hub.kernel.get(cr["locator"])), "the review task done clears the flag"
    assert cc.send_reasons(hub, e["number"]) == []
    with pytest.raises(ValueError, match="say why"):
        cc.dismiss_related(hub, d["number"], "bob", "", "")
    cc.dismiss_related(hub, d["number"], "bob", "tasks are not purchasing", "I accept the tasks change goes ahead as reviewed")
    assert not any("baseline" in r for r in cc.send_reasons(hub, d["number"]))
    [w] = [w for w in cc.co_warnings(hub, d["number"]) if w["code"] == "related"]
    assert w["accepted"]["by"] == "bob"


def test_the_change_order_page_requests_review_and_sends_for_approval(hub):
    from hubproto.shell import serve_in_thread
    from test_shell_views import get, main_of, post

    server = serve_in_thread(hub, "127.0.0.1", 0)  # port 0: bound at once, no race with other test workers
    port = server.server_address[1]
    shell = {"hub": hub, "base": f"http://127.0.0.1:{port}"}
    try:
        co = new_co(hub)
        revise(hub, co, "finance-policy", OLD, NEW)
        page = main_of(get(shell, "/record/" + co["locator"], **{"as": "alice"})[1])
        assert "Request review" in page and "no review round has run yet" in page and "Send for approval" not in page
        status, url, body = post(shell, "/co-review", {"as": "dave", "co": co["locator"]})
        assert status == 403
        status, url, body = post(shell, "/co-review", {"as": "alice", "co": co["locator"]})
        assert status == 200, body[:300]
        page = main_of(get(shell, "/record/" + co["locator"], **{"as": "alice"})[1])
        assert "Review round 1" in page and "panel: management" in page and "Send for approval" in page
        status, url, body = post(shell, "/co-send", {"as": "alice", "co": co["locator"]})
        assert status == 200, body[:300]
        page = main_of(get(shell, "/record/" + co["locator"], **{"as": "alice"})[1])
        assert "Before it can be approved" in page and "not decided yet" in page
    finally:
        server.shutdown()
        server.server_close()


def test_the_release_moves_items_by_the_change_orders_state_map(hub, monkeypatch):
    """The release automation applies the state map once the new release is in force (design note 5.2)."""
    from hubproto import automation, runner

    calls = []
    monkeypatch.setattr(runner, "apply_state_map", lambda h, process, mapping, by, co: calls.append((process, mapping, by, co)) or
                        [{"record": "NCR-2026-0001", "run": "r1", "from": "awaiting-disposition", "to": "open"}])
    co = new_co(hub)
    hub.kernel.set_fields(co["locator"], {"state_map": {"nonconformance": {"awaiting-disposition": "open"}, "capa": {}}}, by="hub")
    text, memo = automation.r_state_maps(hub, {"subject": co["locator"]}, {})
    assert calls == [("nonconformance", {"awaiting-disposition": "open"}, "hub", co["number"])]
    assert text.startswith("moved 1 item(s)") and memo == {"moved": 1}
    hub.kernel.set_fields(co["locator"], {"state_map": {}}, by="hub")
    assert automation.r_state_maps(hub, {"subject": co["locator"]}, {})[0] == "no item to move"
    assert "Move items by the state map" in automation.AUTOMATIONS["release-change-order"]["steps"]
