"""A change that widens a role or a group widens the authority of everyone who holds it (issue #42; AGENTS.md: nobody,
person or cog, reviews a change that widens their own authority). ``changecontrol.widened_holders`` expands a widening
to the role's holders (in force, or given the role by the same change order) and the group's members (listed, or holding
one of its roles); none of them is on the panel, decides an item, accepts a warning or signs the Gate, and the page says
so. Too few approvers left is a sentence that stops the signature. A narrowing excludes nobody.

The example company has two policy owners (bob and carol); here dave is a third, so a change that widens bob's role can
still be approved by two owners whose authority it does not widen. No model is called: cogs answer with their stand-ins."""

import shutil
from html import unescape
from urllib.parse import quote

import pytest

from conftest import ROOT, release_all
from hubproto import changecontrol as cc
from hubproto import eco, sopdocs
from hubproto.app import Hub
from hubproto.shell import serve_in_thread
from test_committees import board_hub  # noqa: F401  (a fixture: the change board on a CO's panel)
from test_eco import write
from test_shell_views import get, main_of, plain, post

AUDITS = "Internal auditors may create audits in Quality/Audits."  # in force (audit-sop)
WIDEN_AUDITORS = "Internal auditors may create nonconformances in Quality/Audits."  # bob holds internal-auditor
WIDEN_CLERKS = "Finance clerks may create nonconformances in Quality/Audits."  # alice and the ap-reviewer cog do; bob does not
WIDEN_QMS = "Quality managers may create nonconformances in Quality/Audits."  # carol, who signs change orders
WIDEN_BOARD = "Change boards may create nonconformances in Quality/Audits."  # the change-board group: alice, and every payables reviewer
WIDEN_JUNIORS = "Junior cogs may create nonconformances in Quality/Audits."  # the junior-cogs group: the panel's cogs
NARROW = "Internal auditors may read Finance."  # taken out: internal auditors may do less
DAVE = "- dave (person): payables-reviewer, staff"


@pytest.fixture
def hub(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    org = tmp_path / "frames" / "company" / "org-model.frame.md"
    text = org.read_text()
    assert DAVE in text and "- junior-cogs:" in text
    text = text.replace(DAVE, "- dave (person): payables-reviewer, staff, policy-owner")
    text = text.replace("- junior-cogs:", "- change-board: alice and everyone holding payables-reviewer\n- junior-cogs:")
    org.write_text(text)
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    sopdocs.convert(h, by="carol")
    for name in ("audit-sop", "org-model"):  # alice, an author, owns these here
        h.kernel.set_fields(sopdocs.in_force(h.kernel, name)["locator"], {"owner": "alice"}, by="setup")
    assert sorted(cc.owners(h)) == ["bob", "carol", "dave"]
    return h


def change_order(hub, *edits, title="Who may raise nonconformances in Quality/Audits"):
    """A change order alice prepares: each edit is (sop, old, new) in that SOP's prose; reviewed once (the suggested panel)."""
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": title, "description": "why it changes", "requested_by": "alice"}, by="alice")
    for sop, old, new in edits:
        d = eco.add_sop(hub, co["number"], sop, by="alice")
        write(hub, hub.kernel.get(d["locator"]), old, new, "alice")
    cc.request_review(hub, co["number"], "alice")
    return hub.kernel.get(co["locator"])


def added(sentence):
    return ("audit-sop", AUDITS, AUDITS + "\n" + sentence)


def rule_items(hub, co):
    return [i for i in cc.delta(hub, co["number"]) if i["op"] != "text"]


def the_item(hub, co, words):
    return next(i for i in rule_items(hub, co) if i["op"] == "add" and words in str(i["rule"]))


def approve(hub, co, who, item):
    return cc.decide_item(hub, co["number"], item["id"], "approve", who, "checked against the SOP", accept_warnings=["all"])


def ready(hub, co, who=("carol", "dave")):
    """Every item approved by ``who``, the co's warnings accepted, the validation evidence recorded."""
    from hubproto import validation

    for it in rule_items(hub, co):
        for w in who:
            if w not in (it.get("makers") or []):
                approve(hub, co, w, it)
    for w in cc.co_warnings(hub, co["number"]):
        if not w.get("accepted"):
            cc.accept_warning(hub, co["number"], w["ref"], who[0], "seen and accepted")
    if validation.requirements(hub, co["number"]):
        validation.record_evidence(hub, co["number"], by="dave", kind="test-record", text="raised a nonconformance in Quality/Audits",
                                   expected="the new permission applies", actual="it applied")


def gate_state(hub, co):
    run = hub.runner.run_for_record(co["locator"])
    return hub.runner.load(run["id"])


def sign(hub, co, who="carol"):
    state = gate_state(hub, co)
    hub.runner.decide(state["id"], who, "approve", meaning="signed", payload_digest=hub.runner.seen_digest(state))
    return hub.runner.load(state["id"])


def page(hub, co, who):
    server = serve_in_thread(hub, "127.0.0.1", 0)
    try:
        shell = {"hub": hub, "base": f"http://127.0.0.1:{server.server_address[1]}"}
        status, text = get(shell, "/record/" + quote(co["locator"], safe=":"), **{"as": who})
        assert status == 200, text[:600]
        return main_of(text)
    finally:
        server.shutdown()
        server.server_close()


def test_a_change_that_widens_a_role_bob_holds_is_one_he_does_not_review_decide_or_sign(hub):
    co = change_order(hub, added(WIDEN_AUDITORS))
    why = cc.widened_holders(hub, co["number"])
    assert why["bob"] == f"holds the internal auditor role, which this change order widens (“{WIDEN_AUDITORS}”)", why
    assert "auditor" in why, "the auditor cog holds the role too"
    with pytest.raises(ValueError, match="bob holds the internal auditor role, which this change order widens .*nobody reviews, decides or signs"):
        cc.add_reviewer(hub, co["number"], "bob", "alice", reason="an internal auditor's eye")
    cc.send_for_approval(hub, co["number"], "alice")
    item = the_item(hub, co, "nonconformance")
    assert item["widening"]
    with pytest.raises(ValueError, match="bob holds the internal auditor role"):
        approve(hub, co, "bob", item)
    # The Gate keeps him off too: the change-order process never lets him sign it.
    excluded = hub.runner.excluded(hub.current_bundle(), gate_state(hub, co))
    assert "widens their own authority" in excluded.get("bob", ""), excluded
    # The page says who is left out and why, in a sentence, and gives bob no decision form.
    html = plain(page(hub, co, "bob"))
    assert ("bob holds the internal auditor role, which this change order widens (“Internal auditors may create nonconformances in "
            "Quality/Audits.”).") in html, html[:2000]
    assert "Left out of this change order's review, decisions and signature, because it widens their own authority" in html
    assert "You do not decide these items: bob holds the internal auditor role" in html
    assert "Approve item" not in page(hub, co, "bob") and "Approve item" in page(hub, co, "carol")
    # Two policy owners whose authority it does not widen approve it, and carol signs it.
    ready(hub, co, ("carol", "dave"))
    assert cc.approval_reasons(hub, co["number"]) == []
    assert sign(hub, co)["status"] == "completed"
    released = hub.kernel.get(co["locator"])
    assert cc.stage_of(released) == "released"
    assert cc.widened_holders(hub, co["number"])["bob"].startswith("holds the internal auditor role"), "kept as it was when decided"


def test_the_same_change_for_a_role_bob_does_not_hold_he_reviews_decides_and_may_sign(hub):
    co = change_order(hub, added(WIDEN_CLERKS))
    why = cc.widened_holders(hub, co["number"])
    assert "bob" not in why and set(why) == {"alice", "ap-reviewer"}, why
    cc.add_reviewer(hub, co["number"], "bob", "alice", reason="an internal auditor's eye")
    cc.submit_review(hub, co["number"], "bob", summary="fine")
    cc.send_for_approval(hub, co["number"], "alice")
    item = the_item(hub, co, "nonconformance")
    assert item["widening"]
    approve(hub, co, "bob", item)
    approve(hub, co, "carol", item)
    assert the_item(hub, co, "nonconformance")["state"] == "approved"
    assert "bob" not in hub.runner.excluded(hub.current_bundle(), gate_state(hub, co))


def test_someone_the_same_change_order_gives_the_role_is_left_out_too(hub):
    co = change_order(hub, added(WIDEN_AUDITORS), ("org-model", DAVE + ", policy-owner", DAVE + ", policy-owner, internal-auditor"))
    why = cc.widened_holders(hub, co["number"])
    assert why["dave"].startswith("is given the internal auditor role by this change order, which also widens that role"), why
    assert why["bob"].startswith("holds the internal auditor role"), why
    with pytest.raises(ValueError, match="dave is given the internal auditor role by this change order"):
        cc.add_reviewer(hub, co["number"], "dave", "alice")
    # Only carol is left of the three policy owners: the widening can never be approved. The change order says so before
    # it is sent, and it is not sent.
    [few] = [r for r in cc.send_reasons(hub, co["number"]) if r.startswith(f"too few policy owners are left to approve “{WIDEN_AUDITORS[:-1]}”")]
    assert few.startswith(f"too few policy owners are left to approve “{WIDEN_AUDITORS[:-1]}”: a widening needs two whose authority it "
                          "does not widen, one of whom contributed nothing, and only carol is left (bob holds the internal auditor role, "
                          "which this change order widens; dave is given the internal auditor role by this change order"), few
    assert "it needs another policy owner" in few
    with pytest.raises(ValueError, match="it cannot be sent for approval yet: .*too few policy owners are left to approve"):
        cc.send_for_approval(hub, co["number"], "alice")
    assert hub.runner.run_for_record(co["locator"]) is None


def test_a_group_widening_leaves_out_everyone_in_the_group(hub):
    co = change_order(hub, added(WIDEN_BOARD + "\n" + WIDEN_JUNIORS))
    why = cc.widened_holders(hub, co["number"])
    assert why["alice"].startswith("is in the change board group, which this change order widens"), why
    assert why["dave"].startswith("is in the change board group"), "dave is in it by his role (everyone holding payables-reviewer)"
    assert why["ap-reviewer"].startswith("is in the change board group"), "so is the ap-reviewer cog"
    assert why["qa-manager"].startswith("is in the junior cogs group") and why["quality-assistant"].startswith("is in the junior cogs group")
    assert not {"bob", "carol"} & set(why)
    rnd = cc.current_round(co)
    off = {x["who"] for x in rnd["left_off"]}
    assert {"qa-manager", "quality-assistant"} <= off and not off & {e["who"] for e in rnd["panel"]}, rnd
    with pytest.raises(ValueError, match="qa-manager is in the junior cogs group"):
        cc.add_reviewer(hub, co["number"], "qa-manager", "alice")
    cc.send_for_approval(hub, co["number"], "alice")
    item = the_item(hub, co, "group:change-board")
    with pytest.raises(ValueError, match="dave is in the change board group"):
        approve(hub, co, "dave", item)
    approve(hub, co, "bob", item)
    approve(hub, co, "carol", item)
    assert the_item(hub, co, "group:change-board")["state"] == "approved"


def test_a_narrowing_of_a_role_leaves_none_of_its_holders_out(hub):
    co = change_order(hub, ("audit-sop", NARROW + "\n", ""), added(WIDEN_CLERKS))
    narrow = next(i for i in rule_items(hub, co) if i["op"] == "remove" and "Finance" in str(i["rule"]))
    assert not narrow["widening"]
    why = cc.widened_holders(hub, co["number"])
    assert set(why) == {"alice", "ap-reviewer"}, "the clerks' widening leaves its holders out; the auditors' narrowing leaves nobody out"
    cc.send_for_approval(hub, co["number"], "alice")
    cc.decide_item(hub, co["number"], narrow["id"], "approve", "bob", "auditors read less", accept_warnings=["all"])
    assert next(i for i in rule_items(hub, co) if i["id"] == narrow["id"])["state"] == "approved", "bob holds the role and decides it"


def test_too_few_to_sign_is_a_sentence_and_the_gate_cannot_be_signed(hub):
    co = change_order(hub, added(WIDEN_QMS))
    why = cc.widened_holders(hub, co["number"])
    assert why["carol"].startswith("holds the quality manager role"), why
    no_signer = ("nobody is left to sign it: carol holds the quality manager role, which this change order widens; it needs someone else "
                 "who may approve change orders and whose authority it does not widen")
    assert cc.send_reasons(hub, co["number"]) == [no_signer]
    assert cc.too_few_approvers(hub, co["number"]) == [no_signer]
    assert no_signer in plain(page(hub, co, "alice")), "the page says it where it says what stops sending"
    with pytest.raises(ValueError, match="it cannot be sent for approval yet: nobody is left to sign it"):
        cc.send_for_approval(hub, co["number"], "alice")
    assert hub.runner.run_for_record(co["locator"]) is None and cc.stage_of(hub.kernel.get(co["locator"])) == "revising"


def test_a_decision_recorded_before_the_rule_no_longer_counts_on_an_open_change_order(hub, monkeypatch):
    """The upgrade: on a change order still open, the exclusion holds from its next evaluation, so a decision bob made on a
    change that widens his own role (before the hub knew) no longer counts; nothing is re-opened."""
    co = change_order(hub, added(WIDEN_AUDITORS))
    cc.send_for_approval(hub, co["number"], "alice")
    item = the_item(hub, co, "nonconformance")
    with monkeypatch.context() as m:  # the hub as it was: widenings counted against the principals they name only
        m.setattr(cc, "widened_holders", lambda hub_, co_ref, p=None: {})
        approve(hub, co, "bob", item)
        approve(hub, co, "carol", item)
        assert the_item(hub, co, "nonconformance")["state"] == "approved"
    after = the_item(hub, co, "nonconformance")
    assert after["state"] == "pending" and {d["by"] for d in after["decisions"]} == {"bob", "carol"}, "recorded, not counted"
    reasons = cc.approval_reasons(hub, co["number"])
    assert any(r.startswith("widening: ") and "needs two policy owners" in r for r in reasons), reasons
    approve(hub, co, "dave", item)
    assert the_item(hub, co, "nonconformance")["state"] == "approved"
    assert cc.too_few_approvers(hub, co["number"]) == [], "carol and dave are two policy owners it does not widen"


# ---------------------------------------------------------------- review findings on #56: no state nobody can act on
def test_a_change_order_sent_before_the_rule_that_nobody_could_sign_goes_back_to_its_requester(hub):
    """Finding 1: sent under the old rule, waiting at carol's Gate; it widens carol's role, so she may neither sign nor
    reject it and nobody else may. It does not stay there: the runner's tick (or anyone opening it) sends it back to
    revising, with the sentence, and tells alice."""
    co = change_order(hub, added(WIDEN_QMS))
    with pytest.MonkeyPatch.context() as m:
        m.setattr(cc, "widened_holders", lambda hub_, co_ref, p=None: {})
        cc.send_for_approval(hub, co["number"], "alice")
    assert gate_state(hub, co)["pending"]["assignees"] == ["carol"]
    assert cc.stage_of(hub.kernel.get(co["locator"])) == "awaiting-approval"
    cc.tick_panels(hub)
    co = hub.kernel.get(co["locator"])
    assert cc.stage_of(co) == "revising" and not co["fields"].get("frozen")
    why = co["fields"]["reopened"][-1]["why"]
    assert why.startswith("it can no longer be approved as it stands: nobody is left to sign it: carol holds the quality manager role"), why
    told = [m for m in hub.inbox.for_principal("alice") if m["subject"] == f"{co['number']} is back to revising"]
    assert told and "nobody is left to sign it" in told[-1]["body"]
    # alice can act on it: change it (here: take the widening out) and review it again.
    d = next(c["draft"] for c in eco.carried(hub, co))
    write(hub, hub.kernel.get(d["locator"]), WIDEN_QMS, WIDEN_CLERKS, "alice")
    cc.request_review(hub, co["number"], "alice")
    assert cc.send_reasons(hub, co["number"]) == []


def test_a_decision_that_does_not_count_is_named_and_is_not_an_independent_approval(hub):
    """Findings 5 and 6: bob approved before the rule; the sentence names him as not counting, and his approval does not
    make him the independent approver that lets a contributor sign."""
    co = change_order(hub, added(WIDEN_AUDITORS))
    with pytest.MonkeyPatch.context() as m:
        m.setattr(cc, "widened_holders", lambda hub_, co_ref, p=None: {})
        cc.send_for_approval(hub, co["number"], "alice")
        approve(hub, co, "bob", the_item(hub, co, "nonconformance"))
        assert cc.independent_approval(hub, co["number"])
    assert not cc.independent_approval(hub, co["number"]), "bob's approval does not count, so nobody approved it independently"
    [r] = [r for r in cc.approval_reasons(hub, co["number"]) if r.startswith("widening: ")]
    assert "(approved by" not in r and r.endswith("; this change order widens bob's own authority, so bob's decision does not count"), r
    html = unescape(plain(page(hub, co, "carol")))
    assert "two policy owners approve it; this change order widens bob's own authority, so bob's decision does not count" in html


def test_someone_left_out_does_not_accept_a_warning_on_it(hub, monkeypatch):
    monkeypatch.setattr(cc, "REVIEWER", lambda hub_, co_, cog, brief: "- [advisory] general: fine otherwise")
    co = change_order(hub, added(WIDEN_AUDITORS))
    cc.send_for_approval(hub, co["number"], "alice")
    w = next(w for w in cc.co_warnings(hub, co["number"]) if not w.get("accepted"))
    with pytest.raises(ValueError, match="bob holds the internal auditor role, which this change order widens"):
        cc.accept_warning(hub, co["number"], w["ref"], "bob", "seen")
    cc.accept_warning(hub, co["number"], w["ref"], "carol", "seen")


def test_someone_left_out_is_not_asked_and_does_not_review_even_if_an_earlier_round_put_them_on_its_panel(hub):
    """A round run before the rule may have bob (asked as a person) or the auditor cog on its panel: neither reviews it."""
    co = change_order(hub, added(WIDEN_AUDITORS))
    n = cc.current_round(co)["n"]

    def put(r):
        r["panel"] += [{"who": "bob", "kind": "person", "member": True, "reason": "named by alice"},
                       {"who": "auditor", "kind": "cog", "member": True, "reason": "named by alice"}]
        r["results"].update({w: {"status": "queued", "kind": k} for w, k in (("bob", "person"), ("auditor", "cog"))})
    cc._update_round(hub, co, n, put)
    with pytest.raises(ValueError, match="bob holds the internal auditor role, which this change order widens"):
        cc.submit_review(hub, co["number"], "bob", summary="fine by me")
    res = cc.cog_review(hub, co["number"], n, "auditor")
    assert res["status"] == "failed" and res.get("excluded"), res
    assert res["error"].startswith("auditor holds the internal auditor role, which this change order widens"), res


COMMITTEE_Q = {"id": "Q-committee", "code": "disagreement", "blocking": True, "status": "open", "chair": "carol",
               "text": "the compile-frame committee does not agree on what the new sentence permits", "frame": "audit-sop"}


def with_committee_question(hub, co):
    p = cc._proposal(hub, hub.kernel.get(co["locator"]))
    p["questions"] = list(p.get("questions") or []) + [dict(COMMITTEE_Q)]
    hub.proposals.save(p)


def test_the_committee_chair_left_out_does_not_answer_for_the_committee(hub):
    co = change_order(hub, added(WIDEN_QMS))
    with_committee_question(hub, co)
    with pytest.raises(ValueError, match="carol holds the quality manager role, which this change order widens"):
        cc.answer_question(hub, co["number"], COMMITTEE_Q["id"], "it permits raising them", "carol")
    q = cc.answer_question(hub, co["number"], COMMITTEE_Q["id"], "it permits raising them", "bob")  # a system administrator
    assert q["status"] == "answered" and q["by"] == "bob"


def test_nobody_left_to_answer_for_the_committee_is_a_sentence(hub):
    co = change_order(hub, added("Design review committees may create nonconformances in Quality/Audits."))
    why = cc.widened_holders(hub, co["number"])
    assert {"bob", "carol"} <= set(why), why  # the chair, and the only person who is a system administrator
    with_committee_question(hub, co)
    reasons = cc.send_reasons(hub, co["number"])
    [r] = [r for r in reasons if r.startswith("nobody is left to answer the committee's question")]
    assert "bob holds the design review committee role, which this change order widens" in r and "carol holds" in r, r
    with pytest.raises(ValueError, match="nobody is left to answer the committee's question"):
        cc.send_for_approval(hub, co["number"], "alice")


def test_the_release_does_not_take_a_signature_by_someone_left_out(hub):
    """Signed before the rule by carol, whose role it widens; its release had not run. The release's own check refuses it
    (the release job stops; the requester reopens it)."""
    co = change_order(hub, added(WIDEN_QMS))
    with pytest.MonkeyPatch.context() as m:
        m.setattr(cc, "widened_holders", lambda hub_, co_ref, p=None: {})
        cc.send_for_approval(hub, co["number"], "alice")
        ready(hub, co, ("bob", "dave"))
        hub.queue.mode = "service"  # the release job waits in the queue
        assert sign(hub, co, "carol")["status"] == "completed"
    assert hub.kernel.get(co["locator"])["state"] == "approved"
    with pytest.raises(ValueError, match="carol holds the quality manager role, which this change order widens"):
        cc.approve_revisions(hub, co["number"])


def test_the_gate_fails_closed_when_the_hub_cannot_tell_whose_authority_it_widens(hub, monkeypatch):
    co = change_order(hub, added(WIDEN_CLERKS))
    cc.send_for_approval(hub, co["number"], "alice")

    def broken(hub_, co_ref, p=None):
        raise RuntimeError("the proposal cannot be read")
    monkeypatch.setattr(cc, "widened_holders", broken)
    excluded = hub.runner.excluded(hub.current_bundle(), gate_state(hub, co))
    assert excluded.get("carol", "").startswith("the hub could not tell whose authority this change order widens"), excluded


def test_a_chair_left_out_does_not_step_in_for_a_late_member_and_nobody_left_is_a_sentence(board_hub, monkeypatch):
    """The change board (dave and the it-admin cog, chaired by bob) reviews changes to limits (test_committees). This change
    order also widens the controller role, which bob holds: when it-admin cannot review, bob does not step in for it, nor
    does the overdue route (the management representative, bob again), and the committee's reason says nobody is left."""
    from hubproto.model import ModelUnavailable
    from test_eco import NEW, OLD

    hub = board_hub
    real = hub.model.complete

    def complete(**kw):
        if kw.get("by") == "it-admin":
            raise ModelUnavailable("its model is down")
        return real(**kw)
    monkeypatch.setattr(hub.model, "complete", complete)
    widen = "Controllers may create nonconformances in Quality/Audits."
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Limits and controllers", "description": "why", "requested_by": "alice"},
                           by="alice")
    d = eco.add_sop(hub, co["number"], "finance-policy", by="alice")
    write(hub, hub.kernel.get(d["locator"]), OLD, NEW + "\n" + widen, "alice")
    cc.request_review(hub, co["number"], "alice", panel=["dave", "the change-board committee"])
    assert cc.widened_holders(hub, co["number"])["bob"].startswith("holds the controller role"), "the chair is left out"
    cc.submit_review(hub, co["number"], "dave")
    [c] = cc.committee_reviews(hub, co["number"])
    assert c["status"] == "paused" and c["interveners"] == [], c
    assert "nobody is left to step in for it-admin: the chair, bob, is someone whose authority" in c["reason"], c["reason"]
    assert c["reason"] in cc.send_reasons(hub, co["number"])
    with pytest.raises(ValueError, match="only the chair of the change-board committee"):
        cc.intervene_review(hub, co["number"], "change-board", "it-admin", "bob", "I reviewed it myself")
