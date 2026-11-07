"""SOPs and policies as documents, and change orders.

The Frame is derived from the SOP documents: converting the Frames into documents changes no rule. A change order
carries draft revisions of the SOPs it changes; the hub stages the Frame they make on every change; each revision is
approved through document control and then held; the change order is approved only when they all are and its staged
Frame is clean; releasing it brings the Frame into force and the revisions into effect together."""

import shutil

import pytest

from conftest import ROOT, release_all
from hubproto import eco, sopdocs
from hubproto.app import Hub
from hubproto.runner import RunError

OLD = "The ap-reviewer cog may approve purchase-to-pay bills under 2,000 USD when the three-way match is within tolerance and the supplier is approved."
NEW = OLD.replace("2,000", "1,500")


@pytest.fixture
def hub(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    sopdocs.convert(h, by="carol")
    return h


def ap_limit(hub) -> float:
    b = hub.releases.bundle(hub.releases.current()["digest"])
    return next(c for c in b["approval_criteria"] if c["approver"] == "cog:ap-reviewer" and c["process"] == "purchase-to-pay")["max_amount"]


def approve(hub, run_id: str, who: str) -> dict:
    state = hub.runner.load(run_id)
    hub.runner.decide(run_id, who, "approve", meaning="approved", payload_digest=hub.runner.seen_digest(state))
    return hub.runner.load(run_id)


def write(hub, draft: dict, old: str, new: str, by: str) -> None:
    text = sopdocs.frame_text(hub, draft)
    body = text.split("\n---\n", 1)[1]
    assert old in body
    hub.dms.add_version(draft["locator"], sopdocs.to_docx(body.replace(old, new)), by=by, filename=f"{draft['fields']['sop_name']}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def test_converting_the_frames_changes_no_rule(hub):
    docs = [d for d in hub.kernel.query(type_="document") if str(d["fields"].get("lineage", "")).startswith("sop:")]
    assert len(docs) == len(hub.frames.frames) and all(d["state"] == "effective" for d in docs)
    kinds = {d["fields"]["sop_name"]: (d["fields"]["doc_type"], d["folder"]) for d in docs}
    assert kinds["finance-policy"] == ("policy", "Policy") and kinds["document-control-sop"] == ("sop", "Quality/Controlled Documents")
    assert kinds["wi-review-a-document"][0] == "wi"
    for name, text in sopdocs.source_texts(hub).items():
        assert text == hub.released_frames().get(name).text, f"{name} reads back as its Frame"
    assert hub.compile(save=False)["changes"] == []
    assert sopdocs.convert(hub, by="carol") == [], "converting twice does nothing"


def test_a_change_order_stages_the_frame_and_releases_it_with_its_revisions(hub):
    policy = sopdocs.in_force(hub.kernel, "finance-policy")
    hub.kernel.set_fields(policy["locator"], {"owner": "alice"}, by="setup")  # alice, an author, owns the policy here
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Lower the AP cog limit", "description": "fewer cog approvals",
                                                                "requested_by": "alice"}, by="alice")
    draft = eco.add_sop(hub, co["number"], "finance-policy", by="alice")
    assert draft["state"] == "draft" and draft["fields"]["revises"] == policy["number"] and draft["fields"]["change_order"] == co["number"]
    assert eco.add_sop(hub, co["number"], "finance-policy", by="alice")["locator"] == draft["locator"], "one draft per SOP"
    write(hub, draft, OLD, NEW, "alice")
    st = eco.status(hub, co["number"])
    staged = st["proposal"]
    assert staged and staged["eco"] == co["number"] and any("1,500" in (c["rule"].get("source") or {}).get("text", "") for c in staged["changes"])
    assert not st["co"]["fields"]["staged_errors"]
    assert any("must be approved first" in r for r in eco.check_approval(hub, co["number"]))
    assert ap_limit(hub) == 2000.0, "nothing is in force yet"

    # The revision is approved through document control (a policy goes to a policy owner) and then held.
    run = hub.runner.submit("document-control", {"record": {"change_type": "minor"}}, owner="alice", record=draft["locator"])
    state = hub.runner.load(run)
    assert state["pending"]["assignees"] and set(state["pending"]["assignees"]) <= {"bob", "carol"}
    after = approve(hub, run, state["pending"]["assignees"][0])
    held = hub.kernel.get(draft["locator"])
    assert after["status"] == "completed" and held["state"] == "approved" and held["fields"]["held_for"] == co["number"]
    assert held["folder"] == "Policy", "a policy stays in Policy"
    assert sopdocs.in_force(hub.kernel, "finance-policy")["locator"] == policy["locator"] and ap_limit(hub) == 2000.0

    # The change order: staged, approved by the quality manager, released.
    corun = hub.runner.submit("change-order", {"record": {}}, owner="alice", record=co["locator"])
    cos = hub.runner.load(corun)
    assert cos["status"] == "waiting" and cos["pending"]["assignees"] == ["carol"]
    done = approve(hub, corun, "carol")
    assert done["status"] == "completed", hub.runner.track(corun)[-3:]
    assert ap_limit(hub) == 1500.0
    new = hub.kernel.get(draft["locator"])
    assert new["state"] == "effective" and hub.kernel.get(policy["locator"])["state"] == "obsolete"
    assert hub.kernel.get(co["locator"])["state"] == "released"
    assert sopdocs.source_texts(hub)["finance-policy"] == hub.released_frames().get("finance-policy").text


def test_a_change_order_with_a_broken_sop_cannot_be_approved(hub):
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Broken", "description": "x", "requested_by": "carol"}, by="carol")
    draft = eco.add_sop(hub, co["number"], "document-control-sop", by="carol")
    write(hub, draft, "Approvals in document-control go to the reviewer.", "Approvals in document-control go to the wizard.", "carol")
    st = eco.status(hub, co["number"])
    assert st["co"]["fields"]["staged_errors"] or not st["ready"]
    with pytest.raises(ValueError):
        eco.release(hub, co["number"], by="carol")


def test_frames_are_not_compiled_from_disk_once_the_sops_are_documents(hub):
    path = hub.frames.get("finance-policy").path
    path.write_text(path.read_text().replace(OLD, NEW))
    hub._frames = None
    assert hub.compile(save=False)["changes"] == [], "an edit to the Frame file is not policy: the documents are the source"
