"""A change that widens a role or a group widens the authority of everyone who holds it (issue #42; AGENTS.md: nobody,
person or cog, reviews a change that widens their own authority). ``changecontrol.widened_holders`` expands a widening
to the role's holders (in force, or given the role by the same change order) and the group's members (listed, or holding
one of its roles); none of them is on the panel, decides an item, accepts a warning or signs the Gate, and the page says
so. Too few approvers left is a sentence that stops the signature. A narrowing excludes nobody.

The example company has two policy owners (bob and carol); here dave is a third, so a change that widens bob's role can
still be approved by two owners whose authority it does not widen. No model is called: cogs answer with their stand-ins."""

import shutil
from urllib.parse import quote

import pytest

from conftest import ROOT, release_all
from hubproto import changecontrol as cc
from hubproto import eco, sopdocs
from hubproto.app import Hub
from hubproto.shell import serve_in_thread
from test_eco import write
from test_shell_views import get, main_of, plain, post

AUDITS = "Internal auditors may create audits in Quality/Audits."  # in force (audit-sop)
WIDEN_AUDITORS = "Internal auditors may create nonconformances in Quality/Audits."  # bob holds internal-auditor
WIDEN_CLERKS = "Finance clerks may create nonconformances in Quality/Audits."  # alice and the ap-reviewer cog do; bob does not
WIDEN_QMS = "Quality managers may create nonconformances in Quality/Audits."  # carol, who signs change orders
WIDEN_BOARD = "Change boards may create nonconformances in Quality/Audits."  # the change-board group: dave, and every internal auditor
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
    text = text.replace("- junior-cogs:", "- change-board: dave and everyone holding internal-auditor\n- junior-cogs:")
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
    cc.send_for_approval(hub, co["number"], "alice")
    item = the_item(hub, co, "nonconformance")
    with pytest.raises(ValueError, match="dave is given the internal auditor role by this change order"):
        approve(hub, co, "dave", item)
    approve(hub, co, "carol", item)
    # Only carol is left of the three policy owners: the widening cannot be approved, and the change order says so.
    reasons = cc.approval_reasons(hub, co["number"])
    [few] = [r for r in reasons if r.startswith(f"too few policy owners are left to approve “{WIDEN_AUDITORS[:-1]}”")]
    assert few.startswith(f"too few policy owners are left to approve “{WIDEN_AUDITORS[:-1]}”: a widening needs two whose authority it "
                          "does not widen, one of whom contributed nothing, and only carol is left (bob holds the internal auditor role, "
                          "which this change order widens; dave is given the internal auditor role by this change order"), few
    assert "it needs another policy owner" in few


def test_a_group_widening_leaves_out_everyone_in_the_group(hub):
    co = change_order(hub, added(WIDEN_BOARD + "\n" + WIDEN_JUNIORS))
    why = cc.widened_holders(hub, co["number"])
    assert why["dave"].startswith("is in the change board group, which this change order widens"), why
    assert why["bob"].startswith("is in the change board group"), "bob is in it by his role (everyone holding internal-auditor)"
    assert why["qa-manager"].startswith("is in the junior cogs group") and why["quality-assistant"].startswith("is in the junior cogs group")
    assert "carol" not in why
    rnd = cc.current_round(co)
    off = {x["who"] for x in rnd["left_off"]}
    assert {"qa-manager", "quality-assistant"} <= off and not off & {e["who"] for e in rnd["panel"]}, rnd
    with pytest.raises(ValueError, match="qa-manager is in the junior cogs group"):
        cc.add_reviewer(hub, co["number"], "qa-manager", "alice")
    cc.send_for_approval(hub, co["number"], "alice")
    item = the_item(hub, co, "group:change-board")
    for who in ("bob", "dave"):
        with pytest.raises(ValueError, match=f"{who} is in the change board group"):
            approve(hub, co, who, item)
    approve(hub, co, "carol", item)
    assert the_item(hub, co, "group:change-board")["state"] == "pending", "carol is the only policy owner left"


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
