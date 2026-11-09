"""The change-order pages over HTTP (change control v2, workstream 5): the whole loop driven from the pages as alice (the
requester), bob (policy owner, a person on the panel) and carol (quality manager, the approver): request review, a
person's review, answer a question, accept a suggestion, review again, send, decide the items accepting their
warnings (a refusal keeps what was typed), sign, released. Then the divergence markers an override leaves, Settings
edits as direct changes on the person's settings change order, the per-cog view, the record types page and the
inbox. No model is called: cogs answer with their stand-ins."""

import re
import shutil
import shutil
from urllib.parse import quote

import pytest

from conftest import ROOT, release_all
from hubproto import changecontrol as cc
from hubproto import eco, sopdocs
from hubproto.app import Hub
from hubproto.rules import rule_key
from hubproto.shell import serve_in_thread
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
    server = serve_in_thread(hub, "127.0.0.1", 0)  # port 0: bound at once, no race with other test workers
    port = server.server_address[1]
    yield {"hub": hub, "base": f"http://127.0.0.1:{port}"}
    server.shutdown()
    server.server_close()


def co_page(shell, co, who):
    status, text = get(shell, "/record/" + quote(co["locator"], safe=":"), **{"as": who})
    assert status == 200, text[:600]
    return main_of(text)


def ok(resp):
    status, url, body = resp
    if status != 200:
        m = re.findall(r"class=.err. role=.alert.>([^<]*)|class=\"card crit\" role=\"alert\"><p>([^<]*)|class='err'[^>]*>([^<]*)", body)
        assert status == 200, (m[:3], plain(main_of(body) if '<main id="main">' in body else body)[:300])
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
    assert any("needs two policy owners, one of whom contributed nothing to it (approved by carol)" in r for r in cc.approval_reasons(hub, co["number"]))
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


def test_usability_fixes_on_the_change_order_and_shell_pages(shell):
    hub = shell["hub"]
    co = new_co(shell)
    html = co_page(shell, co, "alice")
    # Add an SOP is always there: the ones alice may not revise are listed, disabled, with who may, and she can ask.
    assert "Add an SOP or policy" in html and re.search(r"<option value='ncr-sop' disabled>[^<]*only", html)
    assert "Ask them to add it here" in html
    ok(post(shell, "/co-ask-sop", {"as": "alice", "co": co["locator"], "form": "ask-sop", "sop": "ncr-sop"}))
    owner = sopdocs.in_force(hub.kernel, "ncr-sop")["fields"]["owner"]
    assert any(m["to"] == owner and "ncr-sop" in m["subject"] for m in hub.inbox.all())
    # No generic Start on a change order; a start form makes a change order in preparing.
    assert "Start change order" not in html
    url, body = ok(post(shell, "/start", {"as": "alice", "process": "change-order", "title": "From a start form"}))
    made = next(r for r in hub.kernel.query(type_="change-order") if r["fields"]["title"] == "From a start form")
    assert "/record/" in url and cc.stage_of(made) == "preparing" and hub.runner.run_for_record(made["locator"]) is None
    # Settings is in the rail for people; the theme follows the OS until chosen.
    text = get(shell, "/", **{"as": "alice"})[1]
    assert 'href="/settings?as=alice"' in text.split('<nav class="rail"')[1].split("</nav>")[0] and 'data-theme="auto"' in text
    # The training matrix is rendered, not shown as Markdown source.
    from hubproto import training

    training.publish_matrix(hub, hub.releases.current()["digest"])
    doc = training.matrix_document(hub)
    page = main_of(get(shell, "/document/" + quote(doc["locator"], safe=":"), **{"as": "carol"})[1])
    text = page.split("<h2>Text</h2>")[1].split("<h2 id=")[0]
    assert "<table>" in text and "|---|" not in text and "<a href='/document/rec:document:DOC-" in text


def test_printed_records_carry_signatures_the_decided_delta_and_divergence_markers(shell):
    """QMS B3, B4 and divergences on copies: the change order record (the snapshot) holds the delta item by item with its
    decision, accepted warnings and override; signatures print the signer, the time with its zone, the verdict and the
    meaning; a downloaded SOP copy carries the divergence marker under its line."""
    from hubproto.co_record import when_utc

    hub = shell["hub"]
    co = new_co(shell)
    carry_revision(shell, co)
    ok(post(shell, "/co-review", {"as": "alice", "co": co["locator"], "choose": "plus", "panel": ["bob"]}))
    assert "bob" in {e["who"] for e in cc.current_round(hub.kernel.get(co["locator"]))["panel"]}, "the suggested panel plus the one ticked"
    cc.submit_review(hub, co["number"], "bob", findings=[{"severity": "blocking", "text": "watch the queue"}])
    ok(post(shell, "/co-send", {"as": "alice", "co": co["locator"]})) if not cc.send_reasons(hub, co["number"]) else None
    if cc.stage_of(hub.kernel.get(co["locator"])) != "awaiting-approval":
        ok(post(shell, "/co-review", {"as": "alice", "co": co["locator"], "choose": "own", "panel": ["management"]}))
        ok(post(shell, "/co-send", {"as": "alice", "co": co["locator"]}))
    limit = next(i for i in items(hub, co) if i["op"] == "add" and i["rule"]["kind"] == "approval_criteria")
    html = co_page(shell, co, "carol")
    assert "name='ov_max_amount'" in html and "name='ov_process'" not in html and "name='ov_approver'" not in html, "only the value that can change"
    ok(decide(shell, co, limit, "bob", "1,200 is what we agreed", verdict="override", ov_max_amount="1200", accept=[w["ref"] for w in limit["warnings"]]))
    for i in items(hub, co):
        if i["state"] == "pending" and not (i["op"] == "remove" and i.get("changed_to")):
            ok(decide(shell, co, i, "carol", "ok", accept=[w["ref"] for w in i["warnings"]]))
    for w in cc.co_warnings(hub, co["number"]):
        if not w.get("accepted"):
            ok(post(shell, "/co-accept-warning", {"as": "carol", "co": co["locator"], "form": "acc", "ref": w["ref"], "confirm": "1", "meaning": "known"}))
    page = co_page(shell, co, "dave")
    assert "id='co-validation'" in page and "high risk" in page
    ok(post(shell, "/co-evidence", {"as": "dave", "co": co["locator"], "form": "evidence", "kind": "test-record", "result": "pass",
                                    "text": "a 1,300 USD bill", "expected": "it waits for a person", "actual": "it waited for dave"}))
    ok(sign(shell, co))
    url, body = ok(post(shell, "/snapshot", {"as": "carol", "record": co["locator"]}))
    doc = hub.kernel.get("rec:document:" + url.split("rec:document:")[1].split("?")[0])
    md = hub.dms.content(doc["locator"])[0].decode("utf-8")
    assert "## The delta, item by item" in md and "1,200" in md and "Override by bob" in md and "accepted by" in md
    assert "Decision: carol" in md and "## Divergences it leaves" in md and "## Review" in md and "## Validation evidence" in md and "risk class high" in md
    sig = next(d for d in hub.kernel.get(co["locator"])["decisions"] if d["by"] == "carol")
    assert f"| carol, " in md and when_utc(sig["at"]) in md and "| Approved | approval |" in md and '"rounds"' not in md
    # On screen: the signature with the verdict and the time with its zone.
    page = co_page(shell, co, "alice")
    assert "Meaning category" in page and when_utc(sig["at"]) in page
    # The SOP in force, downloaded with the marker under its line; Settings cites the rule with the marker.
    policy = sopdocs.in_force(hub.kernel, "finance-policy")
    status, text = get(shell, "/download-marked", **{"as": "dave", "doc": policy["locator"]})
    assert status == 200 and "> In force differs from this line:" in text and "accepted by bob on " + co["number"] in text
    lines = text.splitlines()
    marker = next(i for i, ln in enumerate(lines) if ln.startswith("> In force differs"))
    assert "1,500" in lines[marker - 1], "the marker sits under the line it names"
    rails = main_of(get(shell, "/settings/rails", **{"as": "bob"})[1])
    assert "in force differs from this line" in rails


def test_a_committee_member_agrees_or_objects_and_a_cog_profile_lists_its_committees(tmp_path):
    from test_committee_pages import serve

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
    hub.kernel.set_fields(sopdocs.in_force(hub.kernel, "finance-policy")["locator"], {"owner": "alice"}, by="setup")
    server, sh = serve(hub)
    try:
        co = new_co(sh)
        carry_revision(sh, co)
        ok(post(sh, "/co-review", {"as": "alice", "co": co["locator"]}))
        page = co_page(sh, co, "dave")
        assert "You review as a member of the change-board committee" in page and ">I agree</button>" not in page or "I agree" in page
        refused(post(sh, "/co-submit-review", {"as": "dave", "co": co["locator"], "form": "committee-vote", "vote": "object", "objection": ""}))
        ok(post(sh, "/co-submit-review", {"as": "dave", "co": co["locator"], "form": "committee-vote", "vote": "agree"}))
        assert cc.committee_reviews(hub, co["number"])[0]["status"] == "agreed"
        prof = main_of(get(sh, "/user/it-admin", **{"as": "dave"})[1])
        assert "Committees" in prof and "change-board" in prof
    finally:
        server.shutdown()
        server.server_close()


def _post_file(shell, path, fields, files):
    import uuid
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    boundary = "----hubproto" + uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        for one in (v if isinstance(v, list) else [v]):
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{one}\r\n'.encode("utf-8")
    for name, filename, content in files:
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8") + content + b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")
    req = Request(shell["base"] + path, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    try:
        with urlopen(req, timeout=10) as r:
            return r.status, r.url, r.read().decode("utf-8")
    except HTTPError as e:
        return e.code, e.url, e.read().decode("utf-8")


def test_a_test_report_is_attached_as_validation_evidence_from_the_page(shell):
    """The Validation section takes a test report as a file: filed as a test-evidence document on the change order, it
    counts as a test record, so the change order can be signed without typing a record."""
    from hubproto import validation

    hub = shell["hub"]
    co = new_co(shell)
    carry_revision(shell, co)
    ok(post(shell, "/co-review", {"as": "alice", "co": co["locator"], "choose": "own", "panel": ["management"]}))
    ok(post(shell, "/co-send", {"as": "alice", "co": co["locator"]}))
    for i in items(hub, co):
        if i["state"] == "pending" and not (i["op"] == "remove" and i.get("changed_to")):
            ok(decide(shell, co, i, "carol", "checked against the SOP", accept=[w["ref"] for w in i["warnings"]]))
    assert any("need test evidence" in r for r in cc.approval_reasons(hub, co["number"]))
    html = co_page(shell, co, "dave")
    assert "Attach a test report as evidence" in html and "attach a test report as evidence" in html
    assert "document of type test-evidence" not in html, "the advice names what the page offers"
    # No file: refused, nothing recorded.
    status, _, page = _post_file(shell, "/co-evidence-file", {"as": "dave", "co": co["locator"], "form": "evidence-file", "result": "pass"}, [])
    assert status == 400 and "choose the test report" in page.lower()
    assert validation.evidence(hub, co["number"]) == []
    status, url, page = _post_file(shell, "/co-evidence-file",
                                   {"as": "dave", "co": co["locator"], "form": "evidence-file", "result": "pass", "title": "Limit test",
                                    "text": "a 1,600 USD bill waited for a person; a 1,400 USD one was approved by the cog"},
                                   [("file", "limit-test.txt", b"ran two bills against the staged rules: as expected\n")])
    assert status == 200 and "is attached as validation evidence" in page and "#co-validation" in url
    [e] = validation.evidence(hub, co["number"])
    doc = next(d for d in hub.dms.documents_of(co["locator"]) if d["number"] == e["document"])
    assert doc["fields"]["doc_type"] == "test-evidence" and doc["fields"]["author"] == "dave" and doc["fields"]["result"] == "pass"
    assert e["kind"] == "test-record" and e["result"] == "pass" and "Limit test" in e["text"] and "1,600 USD" in e["text"]
    assert not any("need test evidence" in r for r in cc.approval_reasons(hub, co["number"])), cc.approval_reasons(hub, co["number"])
    assert "Limit test" in co_page(shell, co, "carol")
    ok(sign(shell, co))
    assert hub.kernel.get(co["locator"])["state"] == "released" and ap_limit(hub) == 1500.0


def test_a_policy_owner_who_made_a_widening_decides_it_with_its_superseded_half_from_the_page(shell):
    """A changed rule is two items, the change and the removal it supersedes (its "from"), shown and decided together. The
    removal follows its change: a policy owner who made the widening may be one of its two owners for both halves, and the
    removal needs no decision or test evidence of its own."""
    from hubproto import validation

    hub = shell["hub"]
    co = new_co(shell, who="bob", title="Raise the AP cog limit")
    carry_revision(shell, co, new=OLD.replace("2,000", "5,000"), who="bob")
    ok(post(shell, "/co-review", {"as": "bob", "co": co["locator"], "choose": "own", "panel": ["management"]}))
    ok(post(shell, "/co-send", {"as": "bob", "co": co["locator"]}))
    [change] = [i for i in items(hub, co) if i["op"] == "add"]
    [half] = [i for i in items(hub, co) if i.get("half_of")]
    assert change["widening"] and half["half_of"] == change["id"] and "bob" in change["makers"]
    ok(decide(shell, co, change, "carol", "checked against the SOP", accept=[w["ref"] for w in change["warnings"] + half["warnings"]]))
    ok(decide(shell, co, change, "bob", "mine, and checked", accept=[w["ref"] for w in change["warnings"] + half["warnings"]]))
    states = {i["id"]: i["state"] for i in items(hub, co)}
    assert states == {change["id"]: "approved", half["id"]: "approved"}, states
    reasons = cc.approval_reasons(hub, co["number"])
    assert not any("segregation" in r or "superseded" in r or "not decided" in r for r in reasons), reasons
    assert half["id"] not in {i["id"] for i in validation.items_of(hub, hub.kernel.get(co["locator"]))}, "its change's evidence covers it"
    out = cc.decide_item(hub, co["number"], half["id"], "reject", "carol", "on its own")
    assert out["state"] == "approved" and "decided with the change" in out["note"], "a half alone records nothing"
    ok(post(shell, "/co-evidence", {"as": "dave", "co": co["locator"], "form": "evidence", "kind": "test-record", "result": "pass",
                                    "text": "a 4,000 USD bill was approved by the cog", "expected": "approved", "actual": "approved"}))
    assert cc.approval_reasons(hub, co["number"]) == [], cc.approval_reasons(hub, co["number"])
    ok(sign(shell, co))
    assert hub.kernel.get(co["locator"])["state"] == "released" and ap_limit(hub) == 5000.0


def test_the_preview_leaves_a_sentence_the_grammar_cannot_read_to_the_compile_committee(shell):
    """A page never calls a model, so the preview reads the drafts with the grammar alone. A changed sentence it cannot
    read is not an error of the draft: the compile-frame committee (the senior cogs) reads the diff in the review round.
    The preview says so, and does not tell the author to fix it first."""
    co = new_co(shell, title="No cog limit")
    # No full stop: the grammar does not read it (the committee does, and asks whether the sentence is complete).
    carry_revision(shell, co, new="The ap-reviewer cog may approve purchase-to-pay bills with no upper limit")
    html = co_page(shell, co, "alice")
    assert "Not read yet." in html and "the senior cogs" in html and "request review" in html
    assert "bills with no upper limit</q>" in html
    assert "looks like a rule but does not parse" not in html and "Fix these first" not in html
    assert "As the grammar alone reads the drafts" in html, "what follows from the unread sentence is marked as the grammar's"



def doc_page(shell, doc, who):
    status, text = get(shell, "/document/" + quote(doc["locator"], safe=":"), **{"as": who})
    assert status == 200, text[:600]
    return main_of(text)


def test_a_cancelled_change_order_withdraws_its_revision_so_a_new_one_can_start(shell):
    """A revision exists only on its change order: cancelled, its draft is withdrawn (no other change order can carry it,
    and an open draft would stop anyone revising the SOP again). A draft an open change order carries is taken off it,
    not withdrawn from the document page; one that nothing carries any more is withdrawn there by its author."""
    from hubproto import sopdocs

    hub = shell["hub"]
    co = new_co(shell)
    draft = carry_revision(shell, co)
    assert "Withdraw this draft" not in doc_page(shell, draft, "alice"), "carried by an open change order: taken off it instead"
    ok(post(shell, "/co-review", {"as": "alice", "co": co["locator"], "choose": "own", "panel": ["management"]}))
    ok(post(shell, "/co-send", {"as": "alice", "co": co["locator"]}))
    run = hub.runner.run_for_record(co["locator"])
    ok(post(shell, "/cancel", {"as": "alice", "run": run["id"], "reason": "a different revision instead", "confirm": "1"}))
    assert hub.kernel.get(co["locator"])["state"] == "cancelled"
    policy = sopdocs.in_force(hub.kernel, "finance-policy")
    ok(post(shell, "/sop-revise", {"as": "alice", "doc": policy["locator"], "co": ""}))
    old = hub.kernel.get(draft["locator"])
    assert old["state"] == "withdrawn", "the cancelled change order's revision no longer stops a new one"
    assert any("cancelled" in str((e.get("data") or {}).get("reason")) for e in hub.kernel.events(aggregate=old["locator"]))
    new = next(c["draft"] for r in hub.kernel.query(type_="change-order") if r["state"] != "cancelled"
               for c in eco.carried(hub, r) if c["name"] == "finance-policy")
    assert new["locator"] != old["locator"] and new["state"] == "draft"


def test_the_author_withdraws_a_draft_nothing_carries_from_the_document_page(shell):
    hub = shell["hub"]
    co = new_co(shell)
    draft = carry_revision(shell, co)
    hub.kernel.unlink(co["locator"], eco.CARRIES, draft["locator"], by="alice")  # left behind (as before cancelled orders withdrew theirs)
    html = doc_page(shell, draft, "alice")
    assert "Withdraw this draft" in html and "a new revision of the SOP can be started" in html
    assert "Withdraw this draft" not in doc_page(shell, draft, "dave"), "only its author or a document controller"
    refused(post(shell, "/withdraw-record", {"as": "alice", "record": draft["locator"], "reason": ""}))
    ok(post(shell, "/withdraw-record", {"as": "alice", "record": draft["locator"], "reason": "starting over"}))
    assert hub.kernel.get(draft["locator"])["state"] == "withdrawn"


def test_a_change_order_begun_from_a_policy_is_described_on_its_page_before_it_is_sent(shell):
    """"Revise on a change order" makes the change order from the policy's page, without a description; the change order
    page asks for it (title and what it changes and why) and sending says it is missing, instead of the change-order
    process stopping on a required field."""
    from hubproto import sopdocs

    hub = shell["hub"]
    policy = sopdocs.in_force(hub.kernel, "finance-policy")
    ok(post(shell, "/sop-revise", {"as": "alice", "doc": policy["locator"], "co": ""}))
    co = next(r for r in hub.kernel.query(type_="change-order") if r["fields"]["title"].startswith("Revise"))
    draft = next(c["draft"] for c in eco.carried(hub, co))
    write(hub, draft, OLD, NEW, "alice")
    ok(post(shell, "/co-review", {"as": "alice", "co": co["locator"], "choose": "own", "panel": ["management"]}))
    html = co_page(shell, co, "alice")
    assert "Title and description: say what it changes and why" in html and "action='/co-details'" in html
    assert any("say what it changes and why" in r for r in cc.send_reasons(hub, co["number"]))
    page = refused(post(shell, "/co-send", {"as": "alice", "co": co["locator"]}))
    assert "say what it changes and why" in page and "required-fields" not in page
    refused(post(shell, "/co-details", {"as": "alice", "co": co["locator"], "form": "details", "title": "Lower the AP cog limit", "description": ""}))
    refused(post(shell, "/co-details", {"as": "dave", "co": co["locator"], "form": "details", "title": "x", "description": "y"}), 400)
    ok(post(shell, "/co-details", {"as": "alice", "co": co["locator"], "form": "details", "title": "Lower the AP cog limit",
                                   "description": "fewer cog approvals: the limit goes to 1,500 USD"}))
    co = hub.kernel.get(co["locator"])
    assert co["fields"]["title"] == "Lower the AP cog limit" and "1,500 USD" in co["fields"]["description"]
    assert not any("say what it changes" in r for r in cc.send_reasons(hub, co["number"]))
    ok(post(shell, "/co-send", {"as": "alice", "co": co["locator"]}))
    assert cc.stage_of(hub.kernel.get(co["locator"])) == "awaiting-approval"


def test_an_advisory_finding_is_named_advisory_where_it_is_a_warning(shell):
    hub = shell["hub"]
    co = new_co(shell)
    carry_revision(shell, co)
    ok(post(shell, "/co-review", {"as": "alice", "co": co["locator"], "choose": "own", "panel": ["management", "bob"]}))
    [it] = [i for i in items(hub, co) if i["op"] == "add"]
    ok(post(shell, "/co-submit-review", {"as": "bob", "co": co["locator"], "form": "review", "sev1": "advisory", "fabout1": it["id"],
                                         "ftext1": "mind the queue at month end", "summary": "fine"}))
    html = co_page(shell, co, "alice")
    assert "advisory finding:</b> bob: mind the queue at month end" in html
    assert "blocking finding:</b> bob: mind the queue" not in html


def test_the_same_task_for_different_people_is_not_flagged_as_a_duplicate():
    from hubproto.shell.inbox import InboxMixin

    def rec(person):
        return {"type": "training", "fields": {"title": "Read WI-17 rev A", "person": person}}
    key = lambda r: InboxMixin._dup_key(None, r)
    assert key(rec("bob")) != key(rec("carol")), "each person's own training"
    assert key(rec("bob")) == key(rec("bob"))


def test_each_rounds_panel_lists_the_compile_ops_senior_cogs_first(shell):
    """The compile-frame committee compiles the diff first in every round: the round records it, and its members head the
    round's panel as the compile Op (here, with hosted models off, as cogs that cannot read here)."""
    hub = shell["hub"]
    co = new_co(shell)
    carry_revision(shell, co)
    ok(post(shell, "/co-review", {"as": "alice", "co": co["locator"], "choose": "own", "panel": ["management"]}))
    rnd = cc.current_round(hub.kernel.get(co["locator"]))
    members = [m["who"] for m in (rnd.get("committee") or {}).get("members") or []]
    assert members == [w for w, _ in hub.compile_committee()["members"]] and members, rnd.get("committee")
    html = co_page(shell, co, "alice")
    panel = html.split("<h4>Panel</h4>", 1)[1].split("</table>", 1)[0]
    for who in members:
        assert who in panel and panel.index(who) < panel.index("management"), "the senior cogs come first"
    assert panel.count("<span class='chip'>compile Op</span>") == len(members) and "compiles the diff into the delta" in panel
    assert "Always on it: the compile-frame committee" in html


def test_a_reasoning_models_thinking_never_reaches_a_review():
    from hubproto.model import strip_thinking

    assert strip_thinking("</think>\n\nFINDING advisory: general: mind the queue") == "FINDING advisory: general: mind the queue"
    assert strip_thinking("<think>weigh it\nagain</think>The answer.") == "The answer."
    assert strip_thinking("no tags here") == "no tags here" and strip_thinking("") == ""

