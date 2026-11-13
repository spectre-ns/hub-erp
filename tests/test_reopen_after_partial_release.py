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


def forms(hub, co, title="The nonconformance", type_="ncr", by="alice"):
    lay = formlayout.active_layout(hub, type_)
    lay["sections"][0]["title"] = title
    formlayout.save_draft(hub, by, type_, lay)
    formlayout.carry(hub, co["number"], by, type_)


def workflow_layout(hub, co, at=(1200, 600), process="nonconformance"):
    workflows.discard(hub, "bob", process)  # a draft started before a later release is stale: start again
    workflows.edit(hub, "bob", process, {"op": "move", "id": "closed", "at": list(at)})
    workflows.carry(hub, co["locator"], "bob", process)


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


# ---------------------------------------------------------------- 4. review of #58: a retry never undoes a later change order
class StopSecondWrite:
    """Stops the release at its second write of a form or workflow layout, as a full disk would."""

    def __init__(self, monkeypatch, target, name):
        self.on, self.calls = True, 0
        real = getattr(target, name)

        def write(store, *a, **k):
            self.calls += 1
            if self.on and self.calls == 2:
                raise OSError(28, "No space left on device")
            return real(store, *a, **k)
        monkeypatch.setattr(target, name, write)


LAYOUTS = {  # kind: (carry one layout, the two names, a change for a later change order, what is in force, the store write)
    "form layouts": (lambda hub, co, name, v: forms(hub, co, title=f"Title {v}", type_=name, by=cc.requester(co)), ("capa", "ncr"),
                     lambda hub, name: formlayout.active_entry(hub, name), "the {} form", (formlayout.FormStore, "set_active")),
    "workflow layouts": (lambda hub, co, name, v: workflow_layout(hub, co, at=(100 * v, 600), process=name), ("capa", "nonconformance"),
                         lambda hub, name: workflows.layout_entry(hub, name), "the {} workflow layout", (workflows.WorkflowStore, "set_layout")),
}


def two_layouts_stopped(hub, monkeypatch, kind):
    """CO-1 carries two layouts; its release stops after the first is written."""
    carry, names, _, _, (target, write) = LAYOUTS[kind]
    stopper = StopSecondWrite(monkeypatch, target, write)
    co1 = new_co(hub, by="bob", title="Two layouts")
    for name in names:
        carry(hub, co1, name, 1)
    co1 = signed(hub, co1)
    j1 = release_job(hub, co1)
    assert j1["status"] == "some-errors" and "No space left" in j1["outcome"], steps(j1)
    stopper.on = False
    return co1, j1


def later(hub, kind, name, v=2):
    carry = LAYOUTS[kind][0]
    co2 = new_co(hub, by="bob", title=f"Later {name}")
    carry(hub, co2, name, v)
    co2 = signed(hub, co2)
    assert release_job(hub, co2)["status"] == "success", steps(release_job(hub, co2))
    return co2


@pytest.mark.parametrize("kind", LAYOUTS)
def test_a_retry_leaves_a_layout_a_later_change_order_released_as_it_is(hub, monkeypatch, kind):
    """The probe of the review of #58, finding A: CO-1 stops after its first layout; CO-2 releases that layout again;
    retrying CO-1 used to write CO-1's layout over CO-2's. It is found done in the history and left as CO-2 made it."""
    co1, j1 = two_layouts_stopped(hub, monkeypatch, kind)
    first, second = LAYOUTS[kind][1]
    entry = LAYOUTS[kind][2]
    co2 = later(hub, kind, first)
    in_force = entry(hub, first)
    assert in_force["released_with"] == co2["number"]
    automation.retry(hub, j1["id"], by="carol")
    j1 = release_job(hub, co1)
    assert j1["status"] == "success", steps(j1)
    assert entry(hub, first) == in_force, f"{co2['number']}'s {first} layout stays in force, as signed"
    assert entry(hub, second)["released_with"] == co1["number"]
    step = next(s for s in j1["steps"] if s["name"] == "Apply the form layouts")
    assert f"is in force now, released with {co2['number']}" in step["detail"], step["detail"]


@pytest.mark.parametrize("kind", LAYOUTS)
def test_a_retry_refuses_a_layout_carried_against_a_version_a_later_change_order_replaced(hub, monkeypatch, kind):
    co1, j1 = two_layouts_stopped(hub, monkeypatch, kind)
    first, second = LAYOUTS[kind][1]
    entry, what = LAYOUTS[kind][2], LAYOUTS[kind][3].format(second)
    co2 = later(hub, kind, second)
    in_force = entry(hub, second)
    with pytest.raises(ValueError) as e:
        cc.reopen(hub, co1["number"], "bob", "carry it again")
    assert (f"Its release cannot finish either: {what} (v1 now, released with {co2['number']}) changed after {co1['number']} carried "
            "its changes") in str(e.value) and "Retry" not in str(e.value), str(e.value)
    automation.retry(hub, j1["id"], by="carol")
    j1 = release_job(hub, co1)
    assert j1["status"] != "success" and (f"{co1['number']}'s release cannot finish: {what} (v1 now, released with {co2['number']}) "
                                          "changed after") in j1["outcome"] and "new change order" in j1["outcome"], j1["outcome"]
    assert entry(hub, second) == in_force, "the later change order's layout is never overwritten"


def test_a_retry_blocked_by_a_later_record_type_change_says_so_and_names_it(hub, monkeypatch):
    """The probe of finding B: CO-1 stops before its record types; CO-2 adds the same field; a retry cannot finish."""
    store_cls = recordtypes.RecordTypeStore
    real = store_cls.set_active
    state = {"on": True}

    def full(self, entry):
        if state["on"]:
            raise OSError(28, "No space left on device")
        return real(self, entry)
    monkeypatch.setattr(store_cls, "set_active", full)
    co1 = new_co(hub, title="Batch on the form and the record")
    forms(hub, co1)
    record_types(hub, co1)
    co1 = signed(hub, co1)
    j1 = release_job(hub, co1)
    assert j1["status"] == "some-errors" and formlayout.active_entry(hub, "ncr")["released_with"] == co1["number"], steps(j1)
    state["on"] = False
    co2 = new_co(hub, title="Batch on the record")
    record_types(hub, co2)
    co2 = signed(hub, co2)
    assert release_job(hub, co2)["status"] == "success"
    with pytest.raises(ValueError, match=rf"Its release cannot finish either: the record types \(v2 now, released with {co2['number']}\)"):
        cc.reopen(hub, co1["number"], "alice", "drop the field")
    automation.retry(hub, j1["id"], by="carol")
    j1 = release_job(hub, co1)
    assert j1["status"] != "success" and "ncr already has a field batch" in j1["outcome"] and (
        f"The record types (v2 now, released with {co2['number']}) changed after {co1['number']} carried them: put the rest of the "
        "change on a new change order") in j1["outcome"], j1["outcome"]
    assert recordtypes.in_force(hub)["released_with"] == co2["number"]


def test_record_type_changes_carried_before_another_release_come_in_on_top_of_it(hub):
    """The re-check of #58, probe 1: record-type changes are operations checked again at release. CO-1 carries a field,
    CO-2 adds another and is released first; CO-1's first release brings its field in beside CO-2's."""
    co1 = new_co(hub, title="Batch field")
    record_types(hub, co1)
    co2 = new_co(hub, title="Gauge field")
    record_types(hub, co2, field="gauge")
    co2 = signed(hub, co2)
    assert release_job(hub, co2)["status"] == "success"
    co1 = signed(hub, co1)
    j1 = release_job(hub, co1)
    assert j1["status"] == "success", steps(j1)
    assert {"batch", "gauge"} <= set(hub.kernel.types["ncr"]["fields"]) and recordtypes.in_force(hub)["released_with"] == co1["number"]


def test_a_retry_completes_when_a_later_change_order_changed_another_record_type(hub, monkeypatch):
    """The re-check of #58, probe 2: CO-1 stops before its record types; CO-2 changes the asset type; the retry
    brings CO-1's ncr field in on top of it."""
    real = recordtypes.RecordTypeStore.set_active
    state = {"on": True}

    def full(self, entry):
        if state["on"]:
            raise OSError(28, "No space left on device")
        return real(self, entry)
    monkeypatch.setattr(recordtypes.RecordTypeStore, "set_active", full)
    co1 = new_co(hub, title="Batch on the form and the record")
    forms(hub, co1)
    record_types(hub, co1)
    co1 = signed(hub, co1)
    j1 = release_job(hub, co1)
    assert j1["status"] == "some-errors", steps(j1)
    state["on"] = False
    co2 = new_co(hub, title="Asset tag")
    recordtypes.carry(hub, co2["number"], [{"op": "add_field", "type": "asset", "field": "tag_code", "spec": "text"}], by="alice")
    co2 = signed(hub, co2)
    assert release_job(hub, co2)["status"] == "success"
    assert "Retry its release" in cc.in_force_refusal(hub, hub.kernel.get(co1["locator"]), cc.in_force_from(hub, co1))
    automation.retry(hub, j1["id"], by="carol")
    j1 = release_job(hub, co1)
    assert j1["status"] == "success", steps(j1)
    assert "batch" in hub.kernel.types["ncr"]["fields"] and "tag_code" in hub.kernel.types["asset"]["fields"]


def test_the_requester_is_told_once_while_nothing_changes(hub, stop):
    a, _ = stopped(hub, stop, links, title="Links first")
    stop.on = False
    for i, title in enumerate(("A form", "Another form")):
        b = new_co(hub, title=title)
        forms(hub, b, title=f"Form {i}")
        signed(hub, b)
    told = [m for m in hub.inbox.for_principal("alice") if m.get("subject") == f"{a['number']} stays approved: part of it is in force"]
    assert len(told) == 1, [m["body"] for m in told]


def test_a_published_revision_counts_as_done_only_when_it_is_the_one_signed(hub, stop):
    co, _ = stopped(hub, stop, frame)
    digest = co["fields"]["released_release"]
    [doc] = eco.publish_revisions(hub, co["number"], digest)
    sig = json.loads(json.dumps(co["fields"]["signed"]))
    sig["frozen"]["drafts"]["finance-policy"] = "0" * 64
    hub.kernel.set_fields(co["locator"], {"signed": sig}, by="hub")
    with pytest.raises(ValueError, match=f"{doc} \\(published\\) is in force with {co['number']}, {NOT_SIGNED}"):
        eco.publish_revisions(hub, co["number"], digest)


def test_a_revision_approved_already_counts_as_done_only_when_it_is_the_one_signed(hub, stop):
    co, _ = stopped(hub, stop, frame)
    assert cc.approve_revisions(hub, co["number"]), "as signed: done"
    frozen = json.loads(json.dumps(co["fields"]["frozen"]))
    frozen["drafts"]["finance-policy"] = "0" * 64
    hub.kernel.set_fields(co["locator"], {"frozen": frozen}, by="hub")
    with pytest.raises(ValueError, match="changed after .* was sent for approval: it is not what was signed"):
        cc.approve_revisions(hub, co["number"])


def test_a_policy_release_in_force_before_its_marker_was_written_is_found_and_not_released_twice(hub, monkeypatch):
    """The review of #58, finding E: the process stops between bringing the release in and writing released_release."""
    real = hub.kernel.set_fields
    state = {"on": True}

    def died(locator, fields, **kw):
        if state["on"] and "released_release" in fields:
            raise OSError(5, "Input/output error")
        return real(locator, fields, **kw)
    monkeypatch.setattr(hub.kernel, "set_fields", died)
    co = new_co(hub, title="Lower the AP cog limit")
    frame(hub, co)
    co = signed(hub, co)
    j = release_job(hub, co)
    assert j["status"] == "some-errors" and not hub.kernel.get(co["locator"])["fields"].get("released_release"), steps(j)
    made = hub.releases.current()["digest"]
    assert hub.releases.current()["released_by"] == f"change-order:{co['number']}"
    with pytest.raises(ValueError, match=f"{REFUSED} \\(the policy release {made[:12]}"):
        cc.reopen(hub, co["number"], "alice", "send it back")
    state["on"] = False
    n = len(hub.releases.list())
    automation.retry(hub, j["id"], by="carol")
    assert release_job(hub, co)["status"] == "success", steps(release_job(hub, co))
    assert len(hub.releases.list()) == n and hub.kernel.get(co["locator"])["fields"]["released_release"] == made
