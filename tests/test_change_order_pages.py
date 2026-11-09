"""The change-order pages over HTTP (change control v2, workstream 5): the whole loop driven from the pages as alice (the
requester), bob (policy owner, a person on the panel) and carol (quality manager, the approver): request review, a
person's review, answer a question, accept a suggestion, review again, send, decide the items accepting their
warnings (a refusal keeps what was typed), sign, released. Then the divergence markers an override leaves, Settings
edits as direct changes on the person's settings change order, the per-cog view, the record types page and the
inbox. No model is called: cogs answer with their stand-ins."""

import re
import shutil
from urllib.parse import quote

import pytest

from conftest import ROOT, release_all
from hubproto import changecontrol as cc
from hubproto import eco, sopdocs
from hubproto.app import Hub
from hubproto.rules import rule_key
from hubproto.shell import free_port, serve_in_thread
from test_eco import NEW, OLD, ap_limit, write
from test_shell_views import get, main_of, plain, post


@pytest.fixture
def shell(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    sopdocs.convert(hub, by="carol")
    for name in ("finance-policy", "document-control-sop", "change-control-sop"):  # alice, an author, owns these here
        hub.kernel.set_fields(sopdocs.in_force(hub.kernel, name)["locator"], {"owner": "alice"}, by="setup")
    port = free_port()
    server = serve_in_thread(hub, "127.0.0.1", port)
    yield {"hub": hub, "base": f"http://127.0.0.1:{port}"}
    server.shutdown()
    server.server_close()


def co_page(shell, co, who):
    status, text = get(shell, "/record/" + quote(co["locator"], safe=":"), **{"as": who})
    assert status == 200, text[:600]
    return main_of(text)


def ok(resp):
    status, url, body = resp
    assert status == 200, plain(main_of(body) if '<main id="main">' in body else body)[:800]
    return url, body


def refused(resp, status=400):
    code, url, body = resp
    assert code == status, (code, plain(body)[:600])
    return main_of(body)


def new_co(shell, who="alice", title="Lower the AP cog limit"):
    ok(post(shell, "/co-new", {"as": who, "title": title, "description": "fewer cog approvals"}))
    return next(r for r in shell["hub"].kernel.query(type_="change-order") if r["fields"]["title"] == title)


def carry_revision(shell, co, sop="finance-policy", old=OLD, new=NEW, who="alice"):
    hub = shell["hub"]
    ok(post(shell, "/co-add-sop", {"as": who, "co": co["locator"], "sop": sop}))
    draft = next(c["draft"] for c in eco.carried(hub, hub.kernel.get(co["locator"])) if c["name"] == sop)
    write(hub, draft, old, new, who)  # what the online editor would save
    return hub.kernel.get(draft["locator"])


def items(hub, co):
    return [i for i in cc.delta(hub, co["number"]) if i["op"] != "text"]


def sign(shell, co, who="carol", meaning="signed as quality manager"):
    hub = shell["hub"]
    run = hub.runner.run_for_record(co["locator"])
    state = hub.runner.load(run["id"])
    return post(shell, "/decide", {"as": who, "run": run["id"], "digest": hub.runner.seen_digest(state), "verdict": "approve",
                                   "meaning": meaning, "confirm": "1"})


def decide(shell, co, item, who, meaning, verdict="approve", accept=(), **extra):
    group = [item["id"]] + [x["id"] for x in items(shell["hub"], co) if x["op"] == "remove" and x.get("changed_to")
                            and "add:" + rule_key(x["changed_to"]) == item["id"]]
    from hubproto.shell.change_order_ui import anchor

    return post(shell, "/co-decide", {"as": who, "co": co["locator"], "form": "decide-" + anchor(item["id"]), "item": group, "verdict": verdict, "meaning": meaning,
                                      "accept": list(accept), **extra})


def test_the_loop_from_the_pages(shell):
    hub = shell["hub"]
    co = new_co(shell)
    html = co_page(shell, co, "alice")
    assert "Where CO-" in html and "aria-current='step'" in html and "<b>Preparing</b>" in html
    assert "carry what it changes" in html, "an empty change order says what to do first"
    carry_revision(shell, co)
    html = co_page(shell, co, "alice")
    assert "Request review" in html and "Send for approval" not in html and "Preview from the drafts" in html
    # Round 1: alice names the panel herself: the management cog and bob.
    url, body = ok(post(shell, "/co-review", {"as": "alice", "co": co["locator"], "form": "request-review", "choose": "own", "panel": ["management", "bob"]}))
    assert "Review round 1" in main_of(body) and "#co-review" in url or "Review round 1" in main_of(body)
    co = hub.kernel.get(co["locator"])
    rnd = cc.current_round(co)
    assert [e["who"] for e in rnd["panel"]] == ["management", "bob"] and rnd["results"]["bob"]["status"] == "queued"
    html = co_page(shell, co, "alice")
    assert "The compile-frame job" in html and "Suggested panel" in html and "limits" in html, "the suggestion, with its reason, is shown"
    assert "Your review of round 1" not in html
    [it] = [i for i in items(hub, co) if i["op"] == "add"]
    assert "The SOP says" in html and "1,500" in html and "The hub reads it as" in html and "finance-policy line" in html
    # bob reviews from the page: a blocking finding about the item, a question, a suggested sentence.
    bob = co_page(shell, co, "bob")
    assert "Your review of round 1" in bob and "You are on the panel" in bob
    form = {"as": "bob", "co": co["locator"], "form": "review", "sev1": "blocking", "fabout1": it["id"], "ftext1": "30% more bills wait for dave",
            "qtext1": "Does the limit apply to bills in EUR?", "ssop1": "finance-policy", "ssec1": "Thresholds",
            "stext1": "The match tolerance is 1 percent.", "summary": "tighter is fine, mind the queue"}
    ok(post(shell, "/co-submit-review", form))
    co = hub.kernel.get(co["locator"])
    assert cc.stage_of(co) == "revising"
    html = co_page(shell, co, "alice")
    assert "30% more bills wait for dave" in html and "Does the limit apply to bills in EUR?" in html and "The match tolerance is 1 percent." in html
    assert "answer 1 question" in html and "accept or reject 1 suggestion" in html
    # The inbox asks alice to answer and settle the suggestion.
    inbox = main_of(get(shell, "/inbox", **{"as": "alice"})[1])
    assert "Answer 1 question" in inbox and "Accept or reject 1 suggested change" in inbox
    # Answering: a refusal keeps what was typed (no answer, a line that is not a number).
    [q] = [q for q in cc.questions(hub, co["number"]) if q.get("raised_by") == "bob"]
    from hubproto.shell.change_order_ui import anchor
    fid = "answer-" + anchor(q["id"])
    page = refused(post(shell, "/co-answer", {"as": "alice", "co": co["locator"], "form": fid, "qid": q["id"], "answer": "yes, or the same in EUR", "line": "forty"}))
    assert "The line is a number" in page and "yes, or the same in EUR" in page and "forty" in page
    ok(post(shell, "/co-answer", {"as": "alice", "co": co["locator"], "form": fid, "qid": q["id"], "answer": "the same in EUR, as line 42 says", "line": "42"}))
    assert next(x for x in cc.questions(hub, co["number"]) if x["id"] == q["id"])["status"] == "answered"
    html = co_page(shell, co, "alice")
    assert "the same in EUR, as line 42 says" in html
    # Accepting the suggestion makes it a delta item, suggested by bob, accepted by alice.
    [s] = cc.suggestions(hub, co["number"])
    assert s["change"] and s["status"] == "open"
    assert refused(post(shell, "/co-suggestion", {"as": "dave", "co": co["locator"], "form": "x", "sid": s["id"], "action": "accept"}))
    ok(post(shell, "/co-suggestion", {"as": "alice", "co": co["locator"], "form": "sug-a", "sid": s["id"], "action": "accept"}))
    co = hub.kernel.get(co["locator"])
    [d] = co["fields"]["direct_changes"]
    assert d["origin"]["kind"] == "suggested" and d["origin"]["by"] == "bob" and d["origin"]["accepted_by"] == "alice"
    html = co_page(shell, co, "alice")
    assert "suggested by" in html and "direct changes changed since round 1" in html and "Send for approval" not in html.split("id='co-approval'")[1]
    # Round 2 (the suggested panel), then send.
    ok(post(shell, "/co-review", {"as": "alice", "co": co["locator"], "form": "request-review", "choose": "suggested"}))
    co = hub.kernel.get(co["locator"])
    assert len(cc.rounds(co)) == 2 and cc.send_reasons(hub, co["number"]) == []
    html = co_page(shell, co, "alice")
    assert "Send for approval" in html and "Earlier rounds (1)" in html
    ok(post(shell, "/co-send", {"as": "alice", "co": co["locator"]}))
    co = hub.kernel.get(co["locator"])
    assert cc.stage_of(co) == "awaiting-approval"
    assert "Sent for approval: it waits for carol" in co_page(shell, co, "alice")
    # carol decides each item: approving the limit needs bob's finding accepted; the refusal keeps the meaning she typed.
    html = co_page(shell, co, "carol")
    assert "Decide the delta item by item" in html and "Before it can be signed" in html, "the Gate card links to the delta"
    assert "I accept: bob: 30% more bills wait for dave" in html and "Override the value" in html
    inbox = main_of(get(shell, "/inbox", **{"as": "carol"})[1])
    assert re.search(r"Decide \d+ items?</b> of " + co["number"], inbox) and "Policy proposals waiting" not in inbox
    limit = next(i for i in items(hub, co) if i["op"] == "add" and i["rule"]["kind"] == "approval_criteria")
    page = refused(decide(shell, co, limit, "carol", "the lower limit, as the policy now says"))
    assert "Tick I accept for each warning" in page and "the lower limit, as the policy now says" in page
    assert cc.delta(hub, co["number"]) and next(i for i in items(hub, co) if i["id"] == limit["id"])["state"] == "pending", "nothing was recorded"
    refs = [w["ref"] for w in limit["warnings"]]
    ok(decide(shell, co, limit, "carol", "the lower limit, as the policy now says", accept=refs))
    for i in items(hub, co):
        if i["state"] == "pending" and not (i["op"] == "remove" and i.get("changed_to")):
            ok(decide(shell, co, i, "carol", "checked against the SOP", accept=[w["ref"] for w in i["warnings"]]))
    # bob's suggestion changes a tolerance: a widening, so the second policy owner decides it too (the page says so).
    widening = [i for i in items(hub, co) if i["state"] == "pending" and i["widening"]]
    assert widening and "widening: needs both policy owners" in co_page(shell, co, "bob")
    assert any("needs two policy owners who did not make it (approved by carol)" in r for r in cc.approval_reasons(hub, co["number"]))
    for i in widening:
        ok(decide(shell, co, i, "bob", "tighter matching, as I suggested", accept=[w["ref"] for w in i["warnings"]]))
    for w in cc.co_warnings(hub, co["number"]):
        if not w.get("accepted"):
            ok(post(shell, "/co-accept-warning", {"as": "carol", "co": co["locator"], "form": "acc", "ref": w["ref"], "confirm": "1", "meaning": "known"}))
    from hubproto import validation

    validation.record_evidence(hub, co["number"], by="dave", kind="test-record", text="tried a bill against the new rules",
                               expected="the new configuration applies", actual="it applied")
    assert cc.approval_reasons(hub, co["number"]) == [], cc.approval_reasons(hub, co["number"])
    html = co_page(shell, co, "carol")
    assert "Ready to sign" in html and "accepted by carol" in html
    ok(sign(shell, co))
    co = hub.kernel.get(co["locator"])
    assert co["state"] == "released" and cc.stage_of(co) == "released" and ap_limit(hub) == 1500.0
    html = co_page(shell, co, "alice")
    assert "Released: its revisions and configuration are in force" in html and "Override the value" not in html


def test_an_override_is_marked_at_the_line_everywhere_the_prose_is_read(shell):
    hub = shell["hub"]
    co = new_co(shell)
    carry_revision(shell, co)
    ok(post(shell, "/co-review", {"as": "alice", "co": co["locator"], "choose": "own", "panel": ["management"]}))
    ok(post(shell, "/co-send", {"as": "alice", "co": co["locator"]}))
    limit = next(i for i in items(hub, co) if i["op"] == "add" and i["rule"]["kind"] == "approval_criteria")
    page = refused(decide(shell, co, limit, "bob", "keep 1,200", verdict="override", ov_max_amount="twelve hundred"))
    assert "max amount is a number" in page and "twelve hundred" in page
    # bob overrides the value: he made the new value, so another approver (carol) approves it and signs.
    ok(decide(shell, co, limit, "bob", "1,200 is what we agreed", verdict="override", ov_max_amount="1200"))
    html = co_page(shell, co, "carol")
    assert "Overridden" in html and "1,200" in html
    for i in items(hub, co):
        if i["state"] == "pending" and not (i["op"] == "remove" and i.get("changed_to")):
            ok(decide(shell, co, i, "carol", "ok", accept=[w["ref"] for w in i["warnings"]]))
    from hubproto import validation

    validation.record_evidence(hub, co["number"], by="dave", kind="test-record", text="tried a bill against the new rules",
                               expected="the new configuration applies", actual="it applied")
    ok(sign(shell, co))
    assert ap_limit(hub) == 1200.0
    [d] = hub.releases.current()["divergences"]
    words = "In force differs from this line"
    doc = sopdocs.in_force(hub.kernel, "finance-policy")
    html = main_of(get(shell, "/document/" + quote(doc["locator"], safe=":"), **{"as": "alice"})[1])
    assert words in html and f"accepted by bob on" in html and co["number"] in html and "1,200" in html
    lines = re.findall(r"<tr id='L(\d+)' class='co-div'>", html)
    assert lines == [str(d["line"])], "the marker sits under the line it names"
    assert "The text, line by line" in html
    frame = main_of(get(shell, "/frame/finance-policy", **{"as": "alice"})[1])
    assert words in frame and re.search(rf"<tr id='L{d['line']}' class='co-div'>", frame)
    viewer = main_of(get(shell, "/edit/" + quote(doc["locator"], safe=":"), **{"as": "dave"})[1])
    assert words in viewer, "the viewer says it too"
    rel = main_of(get(shell, "/release/" + hub.releases.current()["digest"][:16], **{"as": "bob"})[1])
    assert "Divergences <span class='muted'>1</span>" in rel and d["id"] in rel
    conf = main_of(get(shell, "/conformance", **{"as": "bob"})[1])
    assert "Divergences <span class='muted'>1</span>" in conf and d["id"] in conf and "finance-policy line" in conf
    # A later change order carrying the same policy says so on its draft.
    later = new_co(shell, title="Raise the controller threshold")
    draft = carry_revision(shell, later, old="The controller approval threshold is 10,000 USD.", new="The controller approval threshold is 12,000 USD.")
    assert "The revision in force has 1 line(s)" in main_of(get(shell, "/document/" + quote(draft["locator"], safe=":"), **{"as": "alice"})[1])


def test_settings_edits_are_direct_changes_on_the_settings_change_order(shell):
    hub = shell["hub"]
    html = main_of(get(shell, "/settings/limits", **{"as": "bob"})[1])
    assert "Your edits become direct changes on a change order" in html
    th = next(r for r in hub.settings_rules() if r["kind"] == "threshold" and r["name"] == "purchase-approval-threshold")
    ok(post(shell, "/settings-edit", {"as": "bob", "form": "th", "section": "limits", "kind": "threshold", "action": "replace", "key": rule_key(th),
                                      "name": th["name"], "value": "8000", "unit": th["unit"]}))
    overview = main_of(get(shell, "/settings", **{"as": "bob"})[1])
    assert "Put on my change order" in overview
    url, body = ok(post(shell, "/settings-propose", {"as": "bob", "reason": "a lower purchase threshold"}))
    assert "/record/" in url and "direct change(s)" in main_of(body)
    co = next(r for r in hub.kernel.query(type_="change-order") if r["fields"].get("from_settings"))
    [d] = co["fields"]["direct_changes"]
    assert d["origin"]["kind"] == "direct" and d["origin"]["by"] == "bob"
    html = main_of(get(shell, "/settings/limits", **{"as": "bob"})[1])
    assert f"Your edits are direct changes on" in html and co["number"] in html and "carries 1 direct change(s)" in html
    assert "Pending on change orders" in html and "8,000" in html
    assert "Pending on change orders" not in main_of(get(shell, "/settings/sod", **{"as": "bob"})[1]), "each section shows its own"
    page = co_page(shell, co, "bob")
    assert "Direct changes" in page and "not in the prose yet" in page and "8,000" in page
    ok(post(shell, "/co-review", {"as": "bob", "co": co["locator"], "choose": "own", "panel": ["management"]}))
    page = co_page(shell, co, "bob")
    assert "stated in" in page and "purchasing-policy line" in page, "the round wrote the prose"


def test_what_a_change_does_to_each_cog(shell):
    hub = shell["hub"]
    co = new_co(shell)
    carry_revision(shell, co)
    ok(post(shell, "/co-review", {"as": "alice", "co": co["locator"], "choose": "own", "panel": ["management"]}))
    html = co_page(shell, co, "alice")
    sec = html.split("id='co-cogs'")[1].split("</section>")[0]
    assert "What this does to each cog" in sec and "ap-reviewer" in sec
    assert "under 1,500" in sec and "<del>" in sec and "under 2,000" in sec, "the criterion it gains and the one it loses"
    # A direct change that lets a cog's role do more is marked as a widening.
    perms = new_co(shell, who="bob", title="Reviewers file")
    b = hub.current_bundle()
    role = next(r for r in b["members"]["doc-reviewer"]["roles"])
    cc.add_direct_change(hub, perms["number"], {"kind": "permission", "subject": f"role:{role}", "action": "create", "object": "document",
                                                "resource": "folder:Quality", "effect": "allow"}, "bob")
    sec = co_page(shell, perms, "bob").split("id='co-cogs'")[1].split("</section>")[0]
    assert "doc-reviewer" in sec and "widening" in sec and "Permissions" in sec


def test_the_record_types_page_carries_a_change_on_a_change_order(shell):
    hub = shell["hub"]
    co = new_co(shell)
    status, text = get(shell, "/record-types", **{"as": "alice", "co": co["number"]})
    html = main_of(text)
    assert status == 200 and "The record types in force" in html and "Carry a record-type change" in html and "ncr" in html
    page = refused(post(shell, "/record-types-carry", {"as": "alice", "co": co["locator"], "op": "add_field", "type": "ncr", "field": "Bad Name",
                                                       "spec": "text", "reason": "batch tracing"}))
    assert "refused" in page and "Bad Name" in page and "batch tracing" in page, "the refusal keeps what was typed"
    url, body = ok(post(shell, "/record-types-carry", {"as": "alice", "co": co["locator"], "op": "add_field", "type": "ncr", "field": "batch",
                                                       "spec": "text", "reason": "batch tracing"}))
    assert co["number"] in url
    co = hub.kernel.get(co["locator"])
    [c] = co["fields"]["record_type_changes"]
    assert c["op"] == "add_field" and c["field"] == "batch"
    html = co_page(shell, co, "alice")
    assert "ncr: new field batch (text)" in html
    assert "ncr: new field batch" in main_of(get(shell, "/record-types", **{"as": "alice"})[1]), "listed as carried"
    ok(post(shell, "/co-rt-uncarry", {"as": "alice", "co": co["locator"], "form": "rt", "change": c["id"]}))
    assert hub.kernel.get(co["locator"])["fields"]["record_type_changes"] == []


def test_reviewers_are_added_and_removed_and_the_inbox_asks_them(shell):
    hub = shell["hub"]
    co = new_co(shell)
    carry_revision(shell, co)
    ok(post(shell, "/co-review", {"as": "alice", "co": co["locator"], "choose": "own", "panel": ["management"]}))
    page = refused(post(shell, "/co-reviewer-add", {"as": "alice", "co": co["locator"], "form": "add-reviewer", "who": "", "reason": "he signs widenings"}))
    assert "Choose who to add" in page and "he signs widenings" in page
    ok(post(shell, "/co-reviewer-add", {"as": "alice", "co": co["locator"], "form": "add-reviewer", "who": "bob", "reason": "he signs widenings"}))
    inbox = main_of(get(shell, "/inbox", **{"as": "bob"})[1])
    assert f"Review {co['number']}" in inbox and "he signs widenings" in inbox and "#co-your-review" in inbox
    assert "Asked to review" in inbox, "the notification says it is a request"
    ok(post(shell, "/co-reviewer-remove", {"as": "alice", "co": co["locator"], "form": "rmr-bob", "who": "bob"}))
    co = hub.kernel.get(co["locator"])
    assert "bob" not in {e["who"] for e in cc.current_round(co)["panel"]}
    section = main_of(get(shell, "/inbox", **{"as": "bob"})[1]).split("id='change-orders'")[1].split("<h2")[0]
    assert f"Review {co['number']}" not in section, "off the panel: no longer asked (the notification stays in the history)"
    assert "taken off the panel by alice" in co_page(shell, co, "alice")


def test_a_related_release_flags_the_change_order_and_a_by_meaning_flag_is_dismissed(shell, monkeypatch):
    hub = shell["hub"]
    other = new_co(shell, who="bob", title="Looser match")
    tol = next(r for r in hub.settings_rules() if r["kind"] == "threshold" and r["name"] == "match-tolerance")
    cc.add_direct_change(hub, other["number"], {**{k: v for k, v in tol.items() if k != "source"}, "value": 3.0}, "bob")  # homed in finance-policy
    co = new_co(shell)
    carry_revision(shell, co)
    ok(post(shell, "/co-review", {"as": "alice", "co": co["locator"], "choose": "own", "panel": ["management"]}))
    ok(post(shell, "/co-send", {"as": "alice", "co": co["locator"]}))
    for i in items(hub, co):
        if not (i["op"] == "remove" and i.get("changed_to")):
            ok(decide(shell, co, i, "carol", "ok", accept=[w["ref"] for w in i["warnings"]]))
    from hubproto import validation

    validation.record_evidence(hub, co["number"], by="dave", kind="test-record", text="tried a bill against the new rules",
                               expected="the new configuration applies", actual="it applied")
    ok(sign(shell, co))
    other = hub.kernel.get(other["locator"])
    assert (other["fields"]["baseline_changed"] or {}).get("kind") == "definition"
    html = co_page(shell, other, "bob")
    assert "The baseline changed" in html and "cannot be dismissed" in html
    assert "the baseline changed" in main_of(get(shell, "/inbox", **{"as": "bob"})[1])
    # By meaning: dismissible, with a reason and an accepted warning.
    bc = {**other["fields"]["baseline_changed"], "kind": "meaning", "cog": "qa-manager"}
    hub.kernel.set_fields(other["locator"], {"baseline_changed": bc}, by="test")
    html = co_page(shell, other, "bob")
    assert "Dismiss it" in html
    page = refused(post(shell, "/co-dismiss-related", {"as": "bob", "co": other["locator"], "form": "dismiss", "reason": "tolerance is not the AP limit", "meaning": ""}))
    assert "tolerance is not the AP limit" in page
    ok(post(shell, "/co-dismiss-related", {"as": "bob", "co": other["locator"], "form": "dismiss", "reason": "tolerance is not the AP limit",
                                           "meaning": "the match change goes ahead as reviewed"}))
    assert "Dismissed by bob" in co_page(shell, hub.kernel.get(other["locator"]), "bob")


def test_the_state_map_is_answered_from_the_page(shell):
    hub = shell["hub"]
    run = hub.runner.submit("change-control", {"record": {"title": "Move inspection", "description": "x", "impact": "inspection WI"}}, owner="alice")
    state = hub.runner.load(run)
    hub.runner.decide(run, "carol", "approve", meaning="ok", payload_digest=hub.runner.seen_digest(state))
    co = new_co(shell, title="Confirm, not verify")
    d = carry_revision(shell, co, sop="change-control-sop", old="5. [staff] Verify the change", new="5. [staff] Confirm the change")
    write(hub, d, "At Verify the change in change-control go to the requester.", "At Confirm the change in change-control go to the requester.", "alice")
    ok(post(shell, "/co-review", {"as": "alice", "co": co["locator"], "choose": "own", "panel": ["management"]}))
    html = co_page(shell, co, "alice")
    sec = html.split("id='co-state-map'")[1].split("</section>")[0]
    assert "Awaiting verify the change" in sec and ">1<" in sec and "needs an answer" in sec and "Consequences" in sec
    page = refused(post(shell, "/co-state-map", {"as": "alice", "co": co["locator"], "form": "sm", "process": "change-control",
                                                 "state": "awaiting-verify-the-change", "to": ""}))
    assert "Choose where the items go" in page
    ok(post(shell, "/co-state-map", {"as": "alice", "co": co["locator"], "form": "sm", "process": "change-control",
                                     "state": "awaiting-verify-the-change", "to": "awaiting-confirm-the-change"}))
    assert not any("state map" in r for r in cc.approval_reasons(hub, co["number"]))


def test_the_pages_work_without_a_round_and_for_people_who_may_only_read(shell):
    hub = shell["hub"]
    co = new_co(shell)
    html = co_page(shell, co, "dave")
    for heading in ("What it carries", "Review", "Questions", "Findings", "Suggested changes", "The delta", "State map", "What this does to each cog",
                    "Related changes", "Approval"):
        assert heading in html, heading
    assert "alice is preparing it" in html and "Request review" not in html and "/co-decide" not in html
    assert "No review round yet." in html and "No question has been asked." in html and "No item is in a state this change removes." in html
    assert refused(post(shell, "/co-review", {"as": "dave", "co": co["locator"]}), 403)
