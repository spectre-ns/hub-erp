"""Change control v2, the rollout blockers: a change order carries a new SOP, policy or persona document (in force only
with its release, like any revision) and retires one (obsolete at release, its Frame leaves the release, an item of the
delta); whoever opens a revision for its author counts as a contributor; and when the release in force has no person
to answer for the compile committee, the policy owners answer. No model is called."""

import shutil

import pytest

from conftest import ROOT, release_all
from hubproto import changecontrol as cc
from hubproto import eco, sopdocs
from hubproto.app import Hub
from hubproto.compile import question
from test_change_control import OLD, NEW, accept_co_warnings, decide_all, gate, hub, new_co, write  # noqa: F401
from test_change_order_pages import co_page, shell  # noqa: F401

COACH = "personas/persona-coach.frame.md"


def coach_text() -> str:
    text = (ROOT / "frames/personas/persona-advisor.frame.md").read_text(encoding="utf-8")
    return (text.replace("name: persona-advisor", "name: persona-coach").replace("# Persona: the advisor", "# Persona: the coach")
            .replace("## Persona: advisor", "## Persona: coach"))


def released(hub, co, deciders=("bob", "carol"), signer="carol"):
    by = cc.requester(co)
    cc.request_review(hub, co["number"], by, panel=[])
    cc.send_for_approval(hub, co["number"], by)
    decide_all(hub, co, who=deciders)
    accept_co_warnings(hub, co, deciders[0])
    assert cc.approval_reasons(hub, co["number"]) == []
    assert gate(hub, co, who=signer)["status"] == "completed"
    return hub.kernel.get(co["locator"])


def add_coach(hub):
    co = new_co(hub, by="carol", title="Add the coach persona")
    draft = eco.add_new_sop(hub, co["number"], "persona-coach", coach_text(), by="carol", author="alice", relpath=COACH)
    return co, draft


def test_a_change_order_carries_a_new_document_that_comes_into_force_with_its_release(hub):
    co, draft = add_coach(hub)
    assert draft["state"] == "draft" and draft["fields"]["revision"] == "A" and draft["fields"]["author"] == "alice"
    assert sopdocs.in_force(hub.kernel, "persona-coach") is None
    assert eco.add_new_sop(hub, co["number"], "persona-coach", coach_text(), by="carol")["locator"] == draft["locator"], "carried once"
    with pytest.raises(ValueError, match="already has a document"):
        eco.add_new_sop(hub, co["number"], "persona-advisor", coach_text().replace("persona-coach", "persona-advisor"), by="carol")
    with pytest.raises(ValueError, match="not 'persona-other'"):
        eco.add_new_sop(hub, co["number"], "persona-other", coach_text(), by="carol")
    with pytest.raises(ValueError, match="carol's change order"):
        eco.add_new_sop(hub, co["number"], "persona-x", coach_text(), by="dave")
    cc.request_review(hub, co["number"], "carol", panel=[])
    ops = {i["op"]: i for i in cc.delta(hub, co["number"])}
    assert "is new" in ops["text"]["reason"] and ops["add"]["makers"] == ["alice"], "the new document's rules are items, alice's"
    assert not (hub.root / "frames" / COACH).exists(), "nothing is written before the release"
    released(hub, co)
    assert hub.kernel.get(draft["locator"])["state"] == "effective"
    assert sopdocs.in_force(hub.kernel, "persona-coach")["locator"] == draft["locator"]
    assert (hub.root / "frames" / COACH).read_text(encoding="utf-8") == hub.releases.frame_texts(hub.releases.current()["digest"])["persona-coach"]
    assert "persona-coach" in hub.frames.frames


def test_a_change_order_retires_a_document_as_an_item_of_its_delta(hub):
    co, draft = add_coach(hub)
    released(hub, co)
    co2 = new_co(hub, by="carol", title="Retire the coach persona")
    with pytest.raises(ValueError, match="say why"):
        eco.retire_sop(hub, co2["number"], "persona-coach", by="carol", reason=" ")
    with pytest.raises(ValueError, match="no SOP or policy 'persona-none'"):
        eco.retire_sop(hub, co2["number"], "persona-none", by="carol", reason="gone")
    eco.retire_sop(hub, co2["number"], "persona-coach", by="carol", reason="no cog behaves by it")
    assert eco.drafts_of(hub, hub.kernel.get(co2["locator"])) == {"persona-coach": None}
    assert cc.content_digest(hub, co2["number"])["drafts"] == {"persona-coach": "retired"}
    cc.request_review(hub, co2["number"], "carol", panel=[])
    delta = cc.delta(hub, co2["number"])
    [ret] = [i for i in delta if i["op"] == "retire"]
    assert ret["id"] == "retire:persona-coach" and draft["number"] in ret["reason"] and ret["makers"] == ["carol"]
    assert {i["op"] for i in delta} == {"retire", "remove"} and all(i["makers"] == ["carol"] for i in delta), \
        "the rules its Frame stated are taken out, by whoever retires it"
    with pytest.raises(ValueError, match="segregation of duties"):
        cc.decide_item(hub, co2["number"], ret["id"], "approve", "carol", "mine")
    assert "carol" in cc.contributors(hub, co2["number"])
    released(hub, co2, deciders=("bob",))
    assert hub.kernel.get(draft["locator"])["state"] == "obsolete"
    assert "persona-coach" not in hub.releases.frame_texts(hub.releases.current()["digest"])
    assert not (hub.root / "frames" / COACH).exists() and (hub.root / "frames/.retired" / COACH).exists()
    assert "persona-coach" not in hub.frames.frames, "its Frame left the release and the store"


def test_a_retirement_comes_off_and_a_carried_revision_is_not_retired(hub):
    co = new_co(hub)
    eco.retire_sop(hub, co["number"], "persona-reviewer", by="alice", reason="replaced")
    eco.unretire_sop(hub, co["number"], "persona-reviewer", by="alice")
    assert eco.retirements(hub.kernel.get(co["locator"])) == {}
    eco.add_sop(hub, co["number"], "finance-policy", by="alice")
    with pytest.raises(ValueError, match="carries a revision of finance-policy"):
        eco.retire_sop(hub, co["number"], "finance-policy", by="alice", reason="x")


def test_whoever_opens_a_revision_for_its_author_is_a_contributor(hub):
    co = new_co(hub, by="carol", title="Tighter AP limit")
    d = eco.add_sop(hub, co["number"], "finance-policy", by="carol", author="alice")
    write(hub, hub.kernel.get(d["locator"]), OLD, NEW, "alice")
    assert d["fields"]["started_by"] == "carol" and cc.revision_authors(hub, co["number"]) == ["alice"]
    assert "carol" in cc.contributors(hub, co["number"])
    cc.request_review(hub, co["number"], "carol", panel=[])
    assert "carol" in cc.may_not_sign(hub, co["number"]), "a contributor until each item has an independent approver"
    cc.send_for_approval(hub, co["number"], "carol")
    decide_all(hub, co, who=("bob",))
    assert "carol" not in cc.may_not_sign(hub, co["number"]), "bob approved every item: she may sign, she wrote nothing"


def test_the_change_order_page_shows_a_retirement(shell):  # noqa: F811
    hub = shell["hub"]
    co = new_co(hub, by="carol", title="Retire the reviewer persona")
    eco.retire_sop(hub, co["number"], "persona-reviewer", by="carol", reason="replaced by the checker")
    cc.request_review(hub, co["number"], "carol", panel=[])
    assert "retire" in co_page(shell, co, "carol") and "replaced by the checker" in co_page(shell, co, "carol")


# ---------------------------------------------------------------- no person answers for the committee
@pytest.fixture
def v9(tmp_path):
    """A release like v9: no person chairs the compile committee and no person is a system administrator (the it-admin
    cog is one)."""
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    org = tmp_path / "frames/company/org-model.frame.md"
    org.write_text(org.read_text(encoding="utf-8").replace("design-review-committee, system-administrator", "design-review-committee"),
                   encoding="utf-8")
    cogs = tmp_path / "frames/company/cogs.frame.md"
    text = cogs.read_text(encoding="utf-8")  # v9 predates the committee's chair: here it has no committee at all
    head, rest = text.split("## Committee: compile-frame", 1)
    cogs.write_text(head + rest[rest.index("\n## "):], encoding="utf-8")
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    sopdocs.convert(h, by="carol")
    h.kernel.set_fields(sopdocs.in_force(h.kernel, "finance-policy")["locator"], {"owner": "alice"}, by="setup")
    assert not cc._human_intervener(h, (h.compile_committee() or {}).get("chair"))
    return h


def committee_question(hub, co, line=None, item=None):
    p = hub.proposals.load(hub.kernel.get(co["locator"])["fields"]["staged_proposal"])
    q = question("finance-policy", line or 999, "senior-qa-manager could not read the change", raised_by="hub", code="member-unavailable",
                 blocking=True, **({"item": item} if item else {}))
    p["questions"].append(q)
    hub.proposals.save(p)
    return q


def test_with_no_person_to_answer_for_the_committee_a_policy_owner_answers(v9):
    hub = v9
    co = new_co(hub)
    d = eco.add_sop(hub, co["number"], "finance-policy", by="alice")
    write(hub, hub.kernel.get(d["locator"]), OLD, NEW, "alice")
    cc.request_review(hub, co["number"], "alice", panel=[])
    [it] = [i for i in cc.delta(hub, co["number"]) if i["op"] == "add"]
    q = committee_question(hub, co, item=it["id"])
    with pytest.raises(ValueError, match="a policy owner"):
        cc.answer_question(hub, co["number"], q["id"], "fine", "alice")
    with pytest.raises(ValueError, match="it-admin is a cog: a person answers"):  # a cog system administrator does not count
        cc.answer_question(hub, co["number"], q["id"], "fine", "it-admin")
    cc.answer_question(hub, co["number"], q["id"], "the line states the limit, nothing more", "bob")
    [it] = [i for i in cc.delta(hub, co["number"]) if i["id"] == it["id"]]
    [w] = [w for w in it["warnings"] if w["ref"] == q["id"]]
    assert w["accepted"]["by"] == "bob" and "a policy owner" in w["text"], "both owners see it on the item when they decide"


def test_a_policy_owner_does_not_answer_for_the_committee_about_their_own_items(v9):
    hub = v9
    co = new_co(hub, by="carol", title="Shorter document approvals")
    sla = next(r for r in hub.settings_rules() if r["kind"] == "sla" and r["process"] == "document-control")
    cc.add_direct_change(hub, co["number"], {**{k: v for k, v in sla.items() if k != "source"}, "days": 7}, "carol")
    cc.request_review(hub, co["number"], "carol", panel=[])
    it = next(i for i in cc.delta(hub, co["number"]) if i["op"] != "text" and "carol" in i["makers"])
    q = committee_question(hub, co, item=it["id"])
    with pytest.raises(ValueError, match="the other policy owner answers"):
        cc.answer_question(hub, co["number"], q["id"], "mine is fine", "carol")
    assert cc.answer_question(hub, co["number"], q["id"], "checked", "bob")["answered_as"] == "policy-owner"


def test_once_a_person_chairs_the_committee_the_normal_rule_applies(hub):
    co = new_co(hub)
    d = eco.add_sop(hub, co["number"], "finance-policy", by="alice")
    write(hub, hub.kernel.get(d["locator"]), OLD, NEW, "alice")
    cc.request_review(hub, co["number"], "alice", panel=[])
    q = committee_question(hub, co)
    assert cc._human_intervener(hub, "carol")
    with pytest.raises(ValueError, match="only the committee's chair"):  # bob is a system administrator here: dave is neither
        cc.answer_question(hub, co["number"], q["id"], "fine", "dave")
