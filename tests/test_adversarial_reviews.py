"""Adversarial reviews before a cog signs: "The qa-manager cog may approve document-control documents when 3 adversarial
reviews pass". Each review is a fresh read with one brief (a lens) trying to find a reason not to approve; all must
pass; a failed lens stands for that version of the document; with a queue each lens is its own task in the cog's
queue and the cog signs when the last one is in."""

import shutil

import pytest

from conftest import ROOT, release_all
from hubproto import review as review_mod
from hubproto.app import Hub
from hubproto.extract import _CRITERIA
from hubproto.guards import _criteria_widens
from hubproto.settings import sentence


@pytest.fixture
def hub(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    return h


def submit_doc(hub, title: str, change_type: str = "minor", owner: str = "alice") -> dict:
    run_id = hub.runner.submit("document-control", {"record": {"title": title, "change_type": change_type,
                                                               "body": f"# {title}\n\n1. Purpose\n2. Scope\n3. Steps"}}, owner=owner)
    return hub.runner.load(run_id)


def ask(hub, state, who="bob"):
    hub.send(who, "qa-manager", "Approval", "@qa-manager please approve this document", record=state["record"])
    return hub.runner.load(state["id"])


def crit(hub, **kw):
    b = hub.releases.bundle(hub.releases.current()["digest"])
    return next(c for c in b["approval_criteria"] if c["approver"] == "cog:qa-manager" and c["process"] == "document-control"
                and all(c.get(k) == v for k, v in kw.items()))


def lenses_called(monkeypatch, fail: str = ""):
    calls = []
    real = review_mod.review

    def fake(cog_id, profile, ctx, record, question="", judgment=False, lens="", lens_of=(1, 1)):
        calls.append(lens)
        r = real(cog_id, profile, ctx, record, question, judgment=judgment, lens=lens, lens_of=lens_of)
        if lens and lens == fail:
            r = {**r, "verdict": "fail", "findings": [{"severity": "error", "check": lens, "detail": f"the {lens} lens found a gap"}]}
        return r

    monkeypatch.setattr(review_mod, "review", fake)
    return calls


def test_the_sop_states_how_many_reviews_and_reads_back_the_same(hub):
    c = crit(hub, countersign="quality-manager")
    assert c["conditions"] == ["adversarial_reviews_pass"] and c["adversarial_reviews"] == 3
    b = hub.releases.bundle(hub.releases.current()["digest"])
    assert sentence({**c, "kind": "approval_criteria"}, b["processes"]) == c["source"]["text"]
    assert b["cogs"]["qa-manager"]["lenses"] == ["compliance", "consistency", "impact"]
    m = _CRITERIA.match("The qa-manager cog may approve document-control documents when three adversarial reviews pass, countersigned by a quality manager.")
    assert m and "three adversarial reviews pass" in m.group("conds")


def test_fewer_reviews_is_a_widening():
    old = {"conditions": ["adversarial_reviews_pass"], "adversarial_reviews": 3, "countersign": "quality-manager"}
    assert _criteria_widens(old, {**old, "adversarial_reviews": 2})
    assert not _criteria_widens(old, {**old, "adversarial_reviews": 4})


def test_it_signs_only_after_every_lens_passed_and_files_them_as_one_evidence(hub, monkeypatch):
    calls = lenses_called(monkeypatch)
    state = submit_doc(hub, "SOP-ADV-pass")
    after = ask(hub, state)
    assert calls == ["compliance", "consistency", "impact"], "three fresh reviews, one per lens"
    decided = next(e for e in hub.runner.track(state["id"]) if e["type"] in ("gate.decided", "gate.signed") and e.get("by") == "qa-manager")
    assert decided["by"] == "qa-manager" and "3 adversarial reviews passed (compliance, consistency, impact)" in decided["meaning"]
    ev = hub.kernel.get(decided["evidence"])
    text = ev["fields"].get("text") or ""
    assert all(f"qa-manager, {x} lens: pass" in text for x in ("compliance", "consistency", "impact"))
    assert after["status"] in ("waiting", "completed")


def test_one_failed_lens_stops_it_and_stands_for_that_version(hub, monkeypatch):
    calls = lenses_called(monkeypatch, fail="consistency")
    state = submit_doc(hub, "SOP-ADV-fail")
    waited = state["pending"]["assignees"]
    after = ask(hub, state)
    assert calls == ["compliance", "consistency"], "once a lens fails the rest are not needed"
    declined = [e for e in hub.runner.track(state["id"]) if e["type"] == "gate.declined"]
    assert declined and "the consistency review did not pass" in declined[-1]["reasons"][0]
    assert after["status"] == "waiting" and after["pending"]["assignees"] == waited, "the item stays with the people"
    ask(hub, state)
    assert calls == ["compliance", "consistency"], "asking again does not roll the dice again on the same version"


def test_with_a_queue_each_lens_is_a_task_and_the_cog_signs_after_the_last(hub, monkeypatch):
    calls = lenses_called(monkeypatch)
    hub.queue.mode = "service"  # the shell only queues; a worker takes the tasks
    state = submit_doc(hub, "SOP-ADV-queue")
    ask(hub, state)
    hub.queue.drain(cogs=["qa-manager"], limit=1)  # the mention: it steps in and takes the Gate
    for _ in range(3):
        hub.queue.drain(cogs=["qa-manager"], limit=1)
    kinds = [t["kind"] for t in hub.queue.list(cog="qa-manager", limit=50)]
    assert kinds.count("lens") == 3, kinds
    hub.queue.drain(cogs=["qa-manager"])
    track = hub.runner.track(state["id"])
    assert [e["lens"] for e in track if e["type"] == "gate.lens"] == ["compliance", "consistency", "impact"]
    assert any(e["type"] == "gate.reviewing" for e in track)
    assert any(e["type"] in ("gate.decided", "gate.signed") and e.get("by") == "qa-manager" for e in track), [e["type"] for e in track]
    assert calls == ["compliance", "consistency", "impact"]


def test_a_failed_lens_in_the_queue_withdraws_the_others(hub, monkeypatch):
    lenses_called(monkeypatch, fail="compliance")
    hub.queue.mode = "service"
    state = submit_doc(hub, "SOP-ADV-queue-fail")
    ask(hub, state)
    hub.queue.drain(cogs=["qa-manager"])
    lens_tasks = [t for t in hub.queue.list(cog="qa-manager", limit=50) if t["kind"] == "lens"]
    assert sorted(t["status"] for t in lens_tasks) == ["done", "withdrawn", "withdrawn"]
    assert any(e["type"] == "gate.declined" and "compliance" in e["reasons"][0] for e in hub.runner.track(state["id"]))
