"""Groups and committees on the pages (workstream 5 over workstream 9's backend): the committee tally on the inbox card,
a member's vote, the chair's intervention (decide in a member's place, extend, return; a chair's vote is refused and
pointed there), a committee on a change order's panel and its chair reviewing in a member's place, the compile-frame
committee that cannot read here, Settings > Groups and committees as direct changes, and what a cog may do with what
each permission comes through. No model is called."""

import shutil
from datetime import date, timedelta
from urllib.parse import quote

import pytest

from conftest import ROOT, release_all, seen
from hubproto import changecontrol as cc
from hubproto import sopdocs
from hubproto.app import Hub
from hubproto.compile import question
from hubproto.shell import free_port, serve_in_thread
from test_shell_views import get, main_of, plain, post


def serve(hub):
    port = free_port()
    server = serve_in_thread(hub, "127.0.0.1", port)
    return server, {"hub": hub, "base": f"http://127.0.0.1:{port}"}


@pytest.fixture
def design(tmp_path):
    import test_committees as tc

    hub = tc.design_hub(tmp_path)
    server, shell = serve(hub)
    shell["submit"] = tc.submit
    yield shell
    server.shutdown()
    server.server_close()


@pytest.fixture
def board(tmp_path):
    """test_committees.board_hub's company: a change board (dave and the it-admin cog, chaired by bob) reviews changes to limits."""
    import test_committees as tc

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    org = tmp_path / "frames" / "company" / "org-model.frame.md"
    org.write_text(org.read_text().replace("- junior-cogs:", "- change-board: dave and it-admin\n- junior-cogs:")
                   .replace("## Folders", "## Committee: change-board\nGroup: change-board\nChair: bob\nPurpose: Reviews changes to limits.\n\n## Folders"))
    sop = tmp_path / "frames" / "iso9001" / "change-control-sop.frame.md"
    sop.write_text(sop.read_text().replace("A change to roles, limits or segregation of duties is reviewed by the management cog.",
                                           "A change to roles, limits or segregation of duties is reviewed by the change-board committee."))
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    sopdocs.convert(hub, by="carol")
    for name in ("finance-policy", "document-control-sop", "change-control-sop"):
        hub.kernel.set_fields(sopdocs.in_force(hub.kernel, name)["locator"], {"owner": "alice"}, by="setup")
    server, shell = serve(hub)
    shell["limit_co"] = tc._limit_co
    yield shell
    server.shutdown()
    server.server_close()


def ok(resp):
    status, url, body = resp
    assert status == 200, plain(main_of(body) if '<main id="main">' in body else body)[:800]
    return url, body


def refused(resp, status=400):
    code, url, body = resp
    assert code == status, (code, plain(body)[:600])
    return main_of(body)


def card(shell, run_id, who):
    status, text = get(shell, "/inbox", **{"as": who, "open": run_id})
    assert status == 200
    return main_of(text)


def test_the_committee_tally_a_members_vote_and_the_chairs_intervention(design):
    hub = design["hub"]
    run_id = design["submit"](hub, risk="high")  # the doc-reviewer cog's criteria do not hold: the decision pauses for sam
    kim = card(design, run_id, "kim")
    assert "The design-review committee decides by consensus" in kim and "Chair: <b>sam</b>" in kim
    assert "doc-reviewer" in kim and "paused" in kim and "the risk is high" in kim and "to vote" in kim
    assert "Your vote:</b> the design-review committee decides by consensus; one reasoned objection sends it back" in kim and ">Release</button>" in kim and ">Return</button>" in kim
    state = hub.runner.load(run_id)
    ok(post(design, "/decide", {"as": "kim", "run": run_id, "digest": hub.runner.seen_digest(state), "verdict": "approve", "transition": "release",
                                "meaning": "fit to release", "confirm": "1"}))
    rec = hub.runner.load(run_id)["record"]
    kim = main_of(get(design, "/record/" + quote(rec, safe=":"), **{"as": "kim"})[1])
    assert "You agreed" in kim and "agrees" in kim and "fit to release" in kim, "the item's page shows the tally to a member who voted"
    # sam, the chair: told, listed, and given the intervention forms instead of a vote.
    inbox = main_of(get(design, "/inbox", **{"as": "sam"})[1])
    assert "Committees waiting for you to intervene" in inbox and "A committee needs you" in inbox
    sam = card(design, run_id, "sam")
    assert "you intervene as its chair" in sam and "Decide in the member's place" in sam and "Extend the due date" in sam and "Return the item" in sam
    assert "Your vote:" not in sam
    state = hub.runner.load(run_id)
    resp = post(design, "/decide", {"as": "sam", "run": run_id, "digest": hub.runner.seen_digest(state), "verdict": "approve", "transition": "release",
                                    "meaning": "fine", "confirm": "1"})
    assert resp[0] == 400 and "as its chair you intervene" in plain(resp[2]), "a chair's vote is refused and pointed to the intervention"
    page = refused(post(design, "/intervene", {"as": "sam", "run": run_id, "digest": seen(hub.runner, run_id), "action": "decide",
                                               "member": "doc-reviewer", "verdict": "agree", "meaning": ""}))
    assert "needs its meaning" in page
    ok(post(design, "/intervene", {"as": "sam", "run": run_id, "digest": seen(hub.runner, run_id), "action": "decide", "member": "doc-reviewer",
                                   "verdict": "agree", "meaning": "the high risk is covered by the FMEA attached"}))
    assert hub.runner.load(run_id)["status"] == "completed"


def test_the_chair_extends_the_due_date_or_returns_the_item(design):
    hub = design["hub"]
    run_id = design["submit"](hub, risk="high")
    later = (date.today() + timedelta(days=7)).isoformat()
    ok(post(design, "/intervene", {"as": "sam", "run": run_id, "digest": seen(hub.runner, run_id), "action": "extend", "due": later,
                                   "meaning": "waiting for the FMEA"}))
    assert hub.runner.load(run_id)["pending"]["due"] == later
    run_id = design["submit"](hub, risk="high")
    ok(post(design, "/intervene", {"as": "sam", "run": run_id, "digest": seen(hub.runner, run_id), "action": "return", "meaning": "needs a risk assessment"}))
    assert hub.runner.load(run_id)["at"] == "draft"


def test_a_committee_on_the_panel_and_its_chair_reviews_in_a_members_place(board, monkeypatch):
    from hubproto.model import ModelUnavailable

    hub = board["hub"]
    real = hub.model.complete

    def complete(**kw):
        if kw.get("by") == "it-admin":
            raise ModelUnavailable("its model is down")
        return real(**kw)
    monkeypatch.setattr(hub.model, "complete", complete)
    co = board["limit_co"](hub)
    ok(post(board, "/co-review", {"as": "alice", "co": co["locator"]}))
    cc.submit_review(hub, co["number"], "dave")
    page = main_of(get(board, "/record/" + quote(co["locator"], safe=":"), **{"as": "alice"})[1])
    assert "Committees on the panel" in page and "The change-board committee" in page and "could not review" in page and "its model is down" in page
    assert "Intervene for it-admin" not in page, "only the chair intervenes"
    page = main_of(get(board, "/record/" + quote(co["locator"], safe=":"), **{"as": "bob"})[1])
    assert "The change-board committee needs you, its chair" in page and "Intervene for it-admin" in page
    refused(post(board, "/co-intervene-review", {"as": "carol", "co": co["locator"], "form": "x", "committee": "change-board", "member": "it-admin",
                                                 "meaning": "looked"}))
    ok(post(board, "/co-intervene-review", {"as": "bob", "co": co["locator"], "form": "x", "committee": "change-board", "member": "it-admin",
                                            "meaning": "I reviewed the limit change myself: fine"}))
    assert cc.committee_reviews(hub, co["number"])[0]["status"] == "agreed"
    page = main_of(get(board, "/record/" + quote(co["locator"], safe=":"), **{"as": "alice"})[1])
    assert "agreed" in page and "in it-admin&#x27;s place" in page


def test_the_compile_frame_committee_that_cannot_read_here_is_one_card_with_who_can_fix_it(board):
    hub = board["hub"]
    co = board["limit_co"](hub)
    cc.request_review(hub, co["number"], "alice", panel=[])
    p = hub.proposals.load(hub.kernel.get(co["locator"])["fields"]["staged_proposal"])
    p["questions"] = list(p.get("questions") or []) + [
        question("finance-policy", 0, f"The compile-frame committee cannot read the prose of finance-policy: the {m} cog is not available", raised_by="hub",
                 code="member-unavailable", member=m, chair="carol") for m in ("senior-organization", "senior-qa-manager")]
    hub.proposals.save(p)
    path = "/record/" + quote(co["locator"], safe=":")
    page = main_of(get(board, path, **{"as": "alice"})[1])
    assert "The compile-frame committee could not read part of the prose" in page and "The compile-frame committee could not read finance-policy" in page
    assert "Who can change it" in page and "system administrators (bob)" in page and "HUBPROTO_HOSTED_MODELS=on" in page
    assert page.count("The compile-frame committee cannot read the prose of finance-policy") <= 2, "one card, not a wall of questions"
    ok(post(board, "/co-report-unavailable", {"as": "alice", "co": co["locator"], "form": "report-unavailable"}))
    assert any(m["to"] == "bob" and m["kind"] == "task" and "Incident" in m["subject"] for m in hub.inbox.all())
    assert "Reported to bob" in main_of(get(board, path, **{"as": "alice"})[1])
    refused(post(board, "/co-answer-all", {"as": "alice", "co": co["locator"], "form": "answer-unavailable", "answer": ""}))
    ok(post(board, "/co-answer-all", {"as": "alice", "co": co["locator"], "form": "answer-unavailable", "answer": "the changed sentence is in the grammar; read exactly"}))
    assert all(q["status"] == "answered" for q in cc.questions(hub, co["number"]) if q.get("code") == "member-unavailable")


def test_groups_and_committees_in_settings_are_direct_changes(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    sopdocs.convert(hub, by="carol")
    server, shell = serve(hub)
    try:
        html = main_of(get(shell, "/settings/groups", **{"as": "bob"})[1])
        assert "Groups and committees" in html and "senior-cogs" in html and "compile-frame" in html
        page = refused(post(shell, "/settings-edit", {"as": "bob", "form": "add-group", "section": "groups", "kind": "group", "action": "add",
                                                      "name": "quality-board"}))
        assert "tick at least one" in page and "quality-board" in page, "the refusal keeps the name typed"
        ok(post(shell, "/settings-edit", {"as": "bob", "form": "add-group", "section": "groups", "kind": "group", "action": "add",
                                          "name": "quality-board", "members": ["carol", "qa-manager"], "roles": ["quality-manager"]}))
        ok(post(shell, "/settings-edit", {"as": "bob", "form": "add-committee", "section": "groups", "kind": "committee", "action": "add",
                                          "name": "quality-board", "group": "quality-board", "chair": "carol", "purpose": "Reviews quality changes."}))
        ok(post(shell, "/settings-propose", {"as": "bob", "reason": "a quality board"}))
        co = next(r for r in hub.kernel.query(type_="change-order") if r["fields"].get("from_settings"))
        kinds = sorted(cc._old_new(d)[1]["kind"] for d in co["fields"]["direct_changes"])
        assert kinds == ["committee", "group"] and all(d["origin"]["kind"] == "direct" for d in co["fields"]["direct_changes"])
        html = main_of(get(shell, "/settings/groups", **{"as": "bob"})[1])
        assert "Pending on change orders" in html and "quality-board" in html and co["number"] in html
    finally:
        server.shutdown()
        server.server_close()


def test_a_cogs_profile_lists_what_it_may_do_and_through_what(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    server, shell = serve(hub)
    try:
        html = main_of(get(shell, "/user/it-admin", **{"as": "dave"})[1])
        assert "What it may do" in html and "Through" in html and "its role system administrator" in html and "acts" in html
    finally:
        server.shutdown()
        server.server_close()


def test_a_paused_member_is_not_offered_a_vote(tmp_path):
    import test_committees as tc

    hub = tc.design_hub(tmp_path, {"- maya (person): designer": "- maya (person): designer, design-reviewer"})
    run_id = tc.submit(hub, **tc.LOW)  # maya prepared it: kept out, the chair decides in her place
    server, shell = serve(hub)
    try:
        inbox = main_of(get(shell, "/inbox", **{"as": "maya"})[1])
        assert run_id not in inbox.split("id='forme'")[1].split("<h2")[0], "not listed as waiting for her"
        rec = hub.runner.load(run_id)["record"]
        page = main_of(get(shell, "/record/" + quote(rec, safe=":"), **{"as": "maya"})[1])
        assert "maya" in page and "paused" in page and "Release</button>" not in page
    finally:
        server.shutdown()
        server.server_close()
