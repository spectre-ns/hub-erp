"""Junior cogs on the local model (issues #46 and #47): a review reaches a verdict the hub can read, a cog at a Gate
decides or escalates with a reason, and the AP cog approves inside its criteria and hands on what is outside them.

Properties, not words: the model's answer varies; what must hold is that the hub can read it and that its guards hold."""

import re

from conftest import BIG_BILL, CALLS, JUNIOR, SMALL_BILL, _current, dated, need
from hubproto.agent import AgentStore, AgentTools
from hubproto.edits import squash
from hubproto.review import _FINDING, _VERDICT, checklist, gather

DOCUMENT = ("# Torque the cover screws\n\nPurpose: the cover screws of the M4 housing are torqued so the seal holds.\n\n"
            "Scope: every M4 housing assembled in Cell 2.\n\n"
            "1. Set the torque driver to 2.0 Nm and check it on the bench gauge at the start of each shift.\n"
            "2. Torque the four cover screws crosswise.\n"
            "3. Mark each screw with the paint pen.\n"
            "4. Record the housing serial number and the driver's calibration number on the traveller.\n")


def ctx_for(hub, cog: str, rec: dict, requester: str = "carol"):
    return AgentTools(hub, cog, f"t-{cog}-{requester}", "derived", 8, AgentStore(hub.root / "state" / "agents"), requester=requester)._ctx(rec)


def submit_document(hub, title: str, change_type: str = "minor", owner: str = "alice") -> str:
    return hub.runner.submit("document-control", {"record": {"title": title, "change_type": change_type, "body": DOCUMENT}}, owner=owner)


def test_a_junior_cog_reviews_a_document_to_a_verdict_the_hub_reads(released, timed, raw):
    need(JUNIOR)
    hub = released
    loc = hub.runner.load(submit_document(hub, "Torque the cover screws"))["record"]
    rec = hub.kernel.get(loc)
    ctx = ctx_for(hub, "doc-reviewer", rec)
    env = hub.registry.get("doc-reviewer").invoke("review-document", {"record": loc, "question": "Review this work instruction."}, ctx)
    assert not env.get("error"), env.get("error")
    r = env["payload"]
    assert r["model"] == JUNIOR and r["stand_in"] is False, "the cog's own model answered, not a stand-in"
    assert r["verdict"] in ("pass", "fail"), f"no verdict ({r['verdict']}): {r['summary']}\n{raw()}"
    [said] = [c["text"] for c in hub.model.calls if c["by"] == "doc-reviewer"][-1:]
    lines = said.splitlines()
    assert any(_VERDICT.match(ln) for ln in lines), f"the answer has no 'Verdict: pass|fail' line, the review's output format:\n{raw()}"
    drift = [ln for ln in lines if re.match(r"^\s*[-*]\s*\[", ln) and not _FINDING.match(ln)]
    assert not drift, f"finding lines the hub cannot read (the format is '- [error|warning] ...'): {drift}\n{raw()}"
    assert r["summary"].strip(), f"the review says nothing a person can read:\n{raw()}"
    sops = ctx.sop_texts(ctx.profile.get("follows") or [])
    own = {f["detail"] for f in checklist(gather(ctx, rec), sops)}  # the hub's own checks, not the model's
    procedures = squash(" ".join(sops.values()))
    for f in r["findings"]:
        assert f["severity"] in ("error", "warning", "info") and f["detail"].strip(), f
        if f["severity"] == "error" and f["detail"] not in own:  # the guard: a model's error quotes a procedure, else it is a remark
            quotes = re.findall(r"[\"“]([^\"”]{8,})[\"”]", f["detail"])
            assert any(squash(q) in procedures for q in quotes), f"an error that quotes no procedure was counted: {f}\n{raw()}"
    assert r["evidence"] and hub.kernel.get(r["evidence"])["fields"]["doc_class"] == "evidence", "the review is filed as evidence"
    timed()


def test_a_junior_cog_asked_to_sign_decides_on_its_review_or_hands_it_back_with_a_reason(released, timed, raw):
    """The qa-manager cog may approve change requests when its review passes, countersigned by a quality manager:
    asked to sign, it reviews with its model, then either approves (and a quality manager countersigns) or gives the
    item back to the people with the reason."""
    need(JUNIOR)
    hub = released
    rid = hub.runner.submit("change-control", {"record": {"title": "Torque the cover screws to 2.2 Nm",
                                                          "description": "Raise the cover-screw torque from 2.0 to 2.2 Nm; the seal leaked on two housings in May.",
                                                          "impact": "WI torque the cover screws; the bench gauge check"}}, owner="alice")
    state = hub.runner.load(rid)
    assert state["status"] == "waiting", state["status"]
    hub.runner.cog_step_in(rid, "qa-manager", asked_by="carol")
    track = hub.runner.track(rid)
    [rv] = [e for e in track if e["type"] == "gate.cog_review" and e["cog"] == "qa-manager"][-1:]
    reviews = [c for c in hub.model.calls if c["by"] == "qa-manager"]
    assert reviews and not any(c["stand_in"] for c in reviews), f"its model answered:\n{raw()}"
    after = hub.runner.load(rid)
    if rv["verdict"] == "approve":
        # ``judged`` is recorded only on an approval (cogs._decide): the approval rests on its model's review.
        assert rv["judged"], f"the approval rests on its review, by its model:\n{raw()}"
        assert after["pending"].get("countersign_of") and after["pending"]["countersign_role"] == "quality-manager", \
            "a cog's approval here waits for a quality manager's countersignature"
    else:
        declined = [e for e in track if e["type"] == "gate.declined" and e["by"] == "qa-manager"]
        assert declined and all(str(x).strip() for x in declined[-1]["reasons"]) and declined[-1]["reasons"], \
            f"handed back without a reason:\n{raw()}"
        assert "qa-manager" not in (after["pending"].get("assignees") or []), "the item went back to the people"
    timed()


def test_the_ap_cog_approves_a_bill_inside_its_criteria_and_hands_on_one_outside(released):
    """The ap-reviewer cog's criteria are fixed (under 2,000 USD, three-way match within tolerance, supplier approved):
    no model is asked to decide, but the cog must be available on its local model to take the Gate at all."""
    need(JUNIOR)
    hub = released
    small = hub.runner.submit("purchase-to-pay", {"record": dated(SMALL_BILL)}, owner="alice")
    track = hub.runner.track(small)
    assert any(e["type"] == "gate.decided" and e.get("by") == "ap-reviewer" and e.get("verdict") == "approve" for e in track), \
        f"the small bill was not approved by the ap-reviewer cog: {[(e['type'], e.get('by'), e.get('reasons') or e.get('reason')) for e in track]}"
    big = hub.runner.submit("purchase-to-pay", {"record": dated(BIG_BILL)}, owner="alice")
    state, track = hub.runner.load(big), hub.runner.track(big)
    assert "ap-reviewer" not in ((state.get("pending") or {}).get("assignees") or []), "a bill outside its criteria is not its to decide"
    assert not any(e["type"] == "gate.decided" and e.get("by") == "ap-reviewer" for e in track)


def test_the_ap_cog_answers_about_a_bill_it_approved_from_the_record(released, timed, raw):
    """In chat it looks the bill up with its tools and answers from the record."""
    need(JUNIOR)
    hub = released
    run = hub.runner.load(hub.runner.submit("purchase-to-pay", {"record": dated(SMALL_BILL)}, owner="alice"))
    number = hub.kernel.get(run["record"])["number"]
    msg = hub.chat("alice", "ap-reviewer", f"What is the amount of {number}, and who approved it?")
    text = msg.get("body") or msg.get("text") or ""
    assert msg.get("stand_in") is False, f"its model answered:\n{raw()}"
    # The model itself must use a tool: the hub's own search before the call (chat_lookup) does not count.
    model_tools = [t for c in CALLS.get(_current[0], []) if c["kind"] == "tools" for t in c.get("tools") or []]
    assert model_tools, f"the model answered without looking anything up with its tools:\n{raw()}"
    assert re.search(r"1[,.]?500", text), f"the answer does not give the bill's amount (1,500 USD):\n{raw()}"
    timed()
