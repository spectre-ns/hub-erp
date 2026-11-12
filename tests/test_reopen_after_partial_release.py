"""A change order whose release stopped part-way is never sent back to revising (issue #53, from the review of #43).

The release applies exactly what was signed (AGENTS.md, hard rule 3). A release that stops part-way leaves some of the
change order in force: its policy release, published revisions, form layouts, link-vocabulary and retention versions,
record types. Sending it back then (``reopen``, or a later release sending it back as related or to compile again) let
it be signed again with other content, while what was in force stayed as the first signature made it and the release
steps, matching on the change order's number, counted it as done. Now:

* ``reopen`` is refused once any part of the change order is in force, with a sentence naming what is, and the
  automatic paths leave it approved and say the same sentence; its release is retried, or the rest goes on a new one;
* each release step counts what is in force with the change order as done only when it was made from what is signed
  (its digest), and refuses with a sentence otherwise;
* a retry after a stop still completes (#43, finding 1), and a change order whose release never started can still be
  sent back.

No model is called."""

import json
import shutil

import pytest

from conftest import ROOT, release_all
from hubproto import automation, closure, configdocs, eco, formlayout, recordtypes, sopdocs, training, workflows
from hubproto import changecontrol as cc
from hubproto.app import Hub
from test_change_control import accept_co_warnings, decide_all, gate, job, new_co, revise
from test_eco import NEW, OLD

REFUSED = "cannot go back to revising: part of it is already in force"
NOT_SIGNED = "but it was made from other changes than the ones signed"


@pytest.fixture
def hub(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    shutil.copytree(ROOT / "config", cfg)
    monkeypatch.setattr("hubproto.kernel.CONFIG_DIR", cfg)
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    sopdocs.convert(h, by="carol")
    h.kernel.set_fields(sopdocs.in_force(h.kernel, "finance-policy")["locator"], {"owner": "alice"}, by="setup")
    return h


class Stop:
    """Stops the release at "Assign training", after every step that brings configuration into force, as a full disk
    would; ``on = False`` lets the retry through."""

    def __init__(self, monkeypatch):
        self.on = True
        real = training.publish_matrix

        def publish_matrix(*a, **k):
            if self.on:
                raise OSError(28, "No space left on device")
            return real(*a, **k)
        monkeypatch.setattr(training, "publish_matrix", publish_matrix)


@pytest.fixture
def stop(monkeypatch):
    return Stop(monkeypatch)


# ---------------------------------------------------------------- what each change order carries
def links(hub, co):
    configdocs.carry(hub, co["number"], [{"doc": "links", "op": "set", "key": "inspires", "value": "inspired-by"},
                                         {"doc": "retention", "op": "set", "key": "clinical",
                                          "value": {"years": 15, "evidence": ["release_digest", "decisions", "documents"]}}], by="alice")


def record_types(hub, co, field="batch"):
    recordtypes.carry(hub, co["number"], [{"op": "add_field", "type": "ncr", "field": field, "spec": "text"}], by="alice")


def forms(hub, co, title="The nonconformance"):
    lay = formlayout.active_layout(hub, "ncr")
    lay["sections"][0]["title"] = title
    formlayout.save_draft(hub, "alice", "ncr", lay)
    formlayout.carry(hub, co["number"], "alice", "ncr")


def workflow_layout(hub, co, at=(1200, 600)):
    workflows.edit(hub, "bob", "nonconformance", {"op": "move", "id": "closed", "at": list(at)})
    workflows.carry(hub, co["locator"], "bob", "nonconformance")


workflow_layout.by = "bob"  # a policy owner edits workflows: his change order


def requested(hub, carry, title):
    return new_co(hub, by=getattr(carry, "by", "alice"), title=title)


def frame(hub, co):
    revise(hub, co, "finance-policy", OLD, NEW)


KINDS = {"links and retention": (links, "link vocabulary v2, retention classes v2"),
         "record types": (record_types, "record types v2"),
         "form layouts": (forms, "the ncr form v1"),
         "workflow layouts": (workflow_layout, "the nonconformance workflow layout v1"),
         "policy release": (frame, "the policy release ")}


def signed(hub, co, *, before_release=None):
    """Reviewed, sent, decided and signed; ``before_release`` runs after the signature, before the release job does."""
    cc.request_review(hub, co["number"], cc.requester(co), panel=[])
    cc.send_for_approval(hub, co["number"], cc.requester(co))
    decide_all(hub, co, ("carol", "bob"))
    accept_co_warnings(hub, co)
    if before_release:
        hub.queue.mode = "service"
    assert gate(hub, co)["status"] == "completed"
    if before_release:
        before_release(hub.kernel.get(co["locator"]))
        hub.queue.mode = "inline"
        hub.queue.drain()
    return hub.kernel.get(co["locator"])


def release_job(hub, co):
    [j] = job(hub, co, "release-change-order")
    return j


def steps(j):
    return [(s["name"], s["status"], s["detail"]) for s in j["steps"]]


def stopped(hub, stop, carry, title="Partial"):
    """A change order carrying ``carry``, signed, whose release stopped at "Assign training"."""
    co = requested(hub, carry, title)
    carry(hub, co)
    co = signed(hub, co)
    j = release_job(hub, co)
    assert j["status"] == "some-errors" and j["steps"][8]["status"] == "failed", steps(j)
    return co, j


# ---------------------------------------------------------------- 1. reopen is refused once part of it is in force
@pytest.mark.parametrize("kind", KINDS)
def test_a_reopen_after_a_partial_release_is_refused_and_a_retry_completes_it(hub, stop, kind):
    carry, words = KINDS[kind]
    co, j = stopped(hub, stop, carry)
    assert cc.in_force_from(hub, co) and words.rstrip() in ", ".join(cc.in_force_from(hub, co))
    with pytest.raises(ValueError, match=REFUSED) as e:
        cc.reopen(hub, co["number"], cc.requester(co), "replace what it carries")
    assert str(e.value).startswith(f"{co['number']} {REFUSED} ({words}") and "Retry its release" in str(e.value) \
        and "put the rest of the change on a new change order" in str(e.value), str(e.value)
    co = hub.kernel.get(co["locator"])
    assert co["state"] == "approved" and cc.stage_of(co) == "approved", "it is not sent back"
    stop.on = False
    automation.retry(hub, j["id"], by="carol")
    j = release_job(hub, co)
    assert j["status"] == "success", steps(j)
    assert hub.kernel.get(co["locator"])["state"] == "released"


def test_a_release_stopped_between_the_link_and_retention_writes_refuses_a_reopen_and_completes_on_retry(hub, monkeypatch):
    """The reproduction of the review of #43, finding A: links v2 in force, retention not yet."""
    real = configdocs.DocStore.set_active
    calls = []

    def flaky(self, entry):
        calls.append(self.root.name)
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        return real(self, entry)
    monkeypatch.setattr(configdocs.DocStore, "set_active", flaky)
    co = new_co(hub, title="Calibration")
    links(hub, co)
    co = signed(hub, co)
    j = release_job(hub, co)
    assert j["status"] == "some-errors" and configdocs.in_force(hub, "links")["version"] == 2 and configdocs.in_force(hub, "retention")["version"] == 1
    with pytest.raises(ValueError, match=rf"{REFUSED} \(link vocabulary v2\)"):
        cc.reopen(hub, co["number"], "alice", "replace inspires with motivates")
    monkeypatch.setattr(configdocs.DocStore, "set_active", real)
    automation.retry(hub, j["id"], by="carol")
    j = release_job(hub, co)
    assert j["status"] == "success", steps(j)
    assert hub.kernel.link_vocab["inspires"] == "inspired-by" and hub.kernel.retention["clinical"]["years"] == 15


@pytest.mark.parametrize("kind", KINDS)
def test_a_change_order_whose_release_never_started_can_still_be_sent_back(hub, kind):
    carry, _ = KINDS[kind]
    co = requested(hub, carry, "Not yet")
    carry(hub, co)
    seen = {}

    def reopen(c):
        assert cc.in_force_from(hub, c) == []
        seen["co"] = cc.reopen(hub, c["number"], cc.requester(c), "the wrong change")
    signed(hub, co, before_release=reopen)
    assert seen["co"]["state"] == "open" and cc.stage_of(seen["co"]) == "revising"
    j = release_job(hub, co)
    assert j["status"] == "failed" and "not approved" in j["outcome"], j["outcome"]
    assert cc.in_force_from(hub, co) == [], "nothing of it came into force"


# ---------------------------------------------------------------- 2. a later release does not send it back either
def test_a_later_release_does_not_send_back_or_compile_again_a_change_order_partly_in_force(hub, stop):
    a, ja = stopped(hub, stop, links, title="Links first")
    stop.on = False
    b = new_co(hub, title="A form")
    forms(hub, b)
    b = signed(hub, b)
    jb = release_job(hub, b)
    assert jb["status"] == "success", steps(jb)
    a = hub.kernel.get(a["locator"])
    assert a["state"] == "approved" and cc.stage_of(a) == "approved", "the later release left it as signed"
    restage = next(s for s in jb["steps"] if s["name"] == "Compile every open change order")
    assert f"{a['number']} {REFUSED} (link vocabulary v2, retention classes v2)" in restage["detail"], restage["detail"]
    told = [m for m in hub.inbox.for_principal("alice") if m.get("subject") == f"{a['number']} stays approved: part of it is in force"]
    assert told and REFUSED in told[-1]["body"], "its requester is told why"
    automation.retry(hub, ja["id"], by="carol")
    assert release_job(hub, a)["status"] == "success" and hub.kernel.get(a["locator"])["state"] == "released"


def test_a_related_release_does_not_send_back_a_change_order_partly_in_force(hub, stop):
    a, ja = stopped(hub, stop, record_types, title="Batch field")
    stop.on = False
    b = new_co(hub, title="Lot field")
    record_types(hub, b, field="gauge")  # both change the ncr record type: related by definition
    b = signed(hub, b)
    jb = release_job(hub, b)
    assert jb["status"] == "success", steps(jb)
    related = next(s for s in jb["steps"] if s["name"] == "Send related changes back")
    assert f"not sent back: {a['number']} {REFUSED} (record types v2)" in related["detail"], related["detail"]
    a = hub.kernel.get(a["locator"])
    assert a["state"] == "approved" and not a["fields"].get("baseline_changed") and not a["fields"].get("reopened")
    automation.retry(hub, ja["id"], by="carol")
    assert release_job(hub, a)["status"] == "success"
    assert {"batch", "gauge"} <= set(hub.kernel.types["ncr"]["fields"])


# ---------------------------------------------------------------- 3. "already done" means what was signed
def resigned(hub, co, field, value, key):
    """As a change order sent back after a partial release used to be: carrying other changes, signed with them."""
    co = hub.kernel.get(co["locator"])
    sig = json.loads(json.dumps(co["fields"]["signed"]))
    sig["frozen"][key] = closure.part_digest(value)
    hub.kernel.set_fields(co["locator"], {field: value, "signed": sig}, by="hub")
    return hub.kernel.get(co["locator"])


def test_the_links_step_refuses_what_is_in_force_from_other_changes_than_the_signed_ones(hub, stop):
    co, _ = stopped(hub, stop, links)
    changes = json.loads(json.dumps(co["fields"]["config_changes"]))
    assert configdocs.activate(hub, co, "d")[0] == f"link vocabulary v2 (already in force with {co['number']})", "as signed: done"
    changes[0].update({"key": "motivates", "value": "motivated-by", "summary": "link vocabulary: motivates (inverse motivated-by)"})
    co = resigned(hub, co, "config_changes", changes, "config")
    with pytest.raises(ValueError, match=f"link vocabulary v2 is in force with {co['number']}, {NOT_SIGNED}"):
        configdocs.activate(hub, co, "d")
    assert "motivates" not in hub.kernel.link_vocab and configdocs.in_force(hub, "links")["version"] == 2


def test_the_links_step_compares_by_content_what_came_into_force_before_the_digest_was_recorded(hub, stop):
    co, _ = stopped(hub, stop, links)
    for doc in configdocs.DOCS:  # as a hub released before this fix keeps them
        s = configdocs.store(hub, doc)
        entry = s.active()
        entry.pop("signed_digest")
        s.active_path.write_text(json.dumps(entry), encoding="utf-8")
    assert configdocs.activate(hub, co, "d")[0].endswith("(already in force with " + co["number"] + ")"), "the same changes: done"
    changes = json.loads(json.dumps(co["fields"]["config_changes"]))
    changes[0].update({"key": "motivates", "value": "motivated-by", "summary": "link vocabulary: motivates (inverse motivated-by)"})
    co = resigned(hub, co, "config_changes", changes, "config")
    with pytest.raises(ValueError, match=NOT_SIGNED):
        configdocs.activate(hub, co, "d")


def test_the_record_type_step_refuses_what_is_in_force_from_other_changes_than_the_signed_ones(hub, stop):
    co, _ = stopped(hub, stop, record_types)
    assert recordtypes.activate(hub, co, "d") == [f"record types v2 (already in force with {co['number']})"]
    changes = json.loads(json.dumps(co["fields"]["record_type_changes"]))
    changes[0].update({"field": "gauge", "summary": "ncr: new field gauge (text)"})
    co = resigned(hub, co, "record_type_changes", changes, "record_types")
    with pytest.raises(ValueError, match=f"record types v2 is in force with {co['number']}, {NOT_SIGNED}"):
        recordtypes.activate(hub, co, "d")
    assert "gauge" not in hub.kernel.types["ncr"]["fields"] and recordtypes.in_force(hub)["version"] == 2


def test_the_form_step_refuses_what_is_in_force_from_other_changes_than_the_signed_ones(hub, stop):
    co, _ = stopped(hub, stop, forms)
    assert formlayout.activate(hub, co, "d") == ["ncr v1"]
    carried = json.loads(json.dumps(co["fields"]["form_changes"]))
    carried["ncr"]["layout"]["sections"][0]["title"] = "Something else"
    co = resigned(hub, co, "form_changes", carried, "forms")
    with pytest.raises(ValueError, match=f"the ncr form v1 is in force with {co['number']}, {NOT_SIGNED}"):
        formlayout.activate(hub, co, "d")
    assert formlayout.active_entry(hub, "ncr")["layout"]["sections"][0]["title"] == "The nonconformance"


def test_the_workflow_layout_step_refuses_what_is_in_force_from_other_changes_than_the_signed_ones(hub, stop):
    co, _ = stopped(hub, stop, workflow_layout)
    assert workflows.activate(hub, co, "d") == ["nonconformance layout v1"]
    carried = json.loads(json.dumps(co["fields"]["workflow_changes"]))
    carried["nonconformance"]["layout"]["closed"] = [10, 10]
    co = resigned(hub, co, "workflow_changes", carried, "workflows")
    with pytest.raises(ValueError, match=f"the nonconformance workflow layout v1 is in force with {co['number']}, {NOT_SIGNED}"):
        workflows.activate(hub, co, "d")
    assert workflows.layout_entry(hub, "nonconformance")["layout"]["closed"] == [1200, 600]


def test_the_policy_release_step_refuses_a_release_in_force_from_other_content_than_the_signed_one(hub, stop):
    co, _ = stopped(hub, stop, frame)
    digest = co["fields"]["released_release"]
    assert eco.activate_frame(hub, co["number"])["digest"] == digest, "as signed: done"
    sig = json.loads(json.dumps(co["fields"]["signed"]))
    sig["frozen"]["items"] = "0" * 16
    hub.kernel.set_fields(co["locator"], {"signed": sig}, by="hub")
    with pytest.raises(ValueError, match=f"the policy release {digest[:12]} is in force with {co['number']}, {NOT_SIGNED}"):
        eco.activate_frame(hub, co["number"])
