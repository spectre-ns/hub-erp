"""SOPs and policies as documents, and change orders.

The Frame is derived from the SOP documents: converting the Frames into documents changes no rule. A change order
carries draft revisions of the SOPs it changes; its review rounds compile the Frame they make from the drafts as they
stand (change control v2, hubproto.changecontrol); it is sent for approval once a round has run on what it carries; the
approvers decide each item of its delta; the quality manager's approval also approves the revisions it carries;
releasing it brings the Frame into force and the revisions into effect together."""

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


def test_a_change_order_is_reviewed_approved_and_released_with_its_revisions(hub):
    from hubproto import changecontrol

    policy = sopdocs.in_force(hub.kernel, "finance-policy")
    hub.kernel.set_fields(policy["locator"], {"owner": "alice"}, by="setup")  # alice, an author, owns the policy here
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Lower the AP cog limit", "description": "fewer cog approvals",
                                                                "requested_by": "alice"}, by="alice")
    draft = eco.add_sop(hub, co["number"], "finance-policy", by="alice")
    assert draft["state"] == "draft" and draft["fields"]["revises"] == policy["number"] and draft["fields"]["change_order"] == co["number"]
    assert eco.add_sop(hub, co["number"], "finance-policy", by="alice")["locator"] == draft["locator"], "one draft per SOP"
    write(hub, draft, OLD, NEW, "alice")
    st = eco.status(hub, co["number"])
    assert st["proposal"] is None, "nothing is compiled until a review round runs"
    pv = eco.preview(hub, co["number"])
    assert any("1,500" in (c["rule"].get("source") or {}).get("text", "") for c in pv["changes"]) and not pv["errors"]
    assert any("request review" in r for r in eco.check_approval(hub, co["number"]))
    assert ap_limit(hub) == 2000.0, "nothing is in force yet"

    # A review round (the compile-frame Op) compiles the Frame from the drafts as they stand and stages it on the change order.
    changecontrol.request_review(hub, co["number"], "alice")
    [job] = [j for j in hub.automation.of(co["locator"]) if j["automation"] == "compile-frame"]
    assert job["status"] == "success" and [s["status"] for s in job["steps"]] == ["done"] * 8, job
    assert "requested review (round 1)" in job["trigger"]
    st = eco.status(hub, co["number"])
    assert st["proposal"]["eco"] == co["number"] and any("1,500" in (c["rule"].get("source") or {}).get("text", "") for c in st["proposal"]["changes"])
    assert eco.frame_reasons(hub, hub.kernel.get(co["locator"])) == [], "it may be sent for approval"
    assert hub.kernel.get(draft["locator"])["state"] == "draft", "the revision is approved with the change order, not before"

    # Sent for approval; each item decided; approved by the quality manager, then released by the release automation.
    cos = changecontrol.send_for_approval(hub, co["number"], "alice")
    assert cos["status"] == "waiting" and cos["pending"]["assignees"] == ["carol"]
    with pytest.raises(RunError):
        approve(hub, cos["id"], "carol")
    for item in changecontrol.delta(hub, co["number"]):
        if item["op"] != "text":
            changecontrol.decide_item(hub, co["number"], item["id"], "approve", "carol", "the lower limit is right")
    assert eco.check_approval(hub, co["number"]) == []
    done = approve(hub, cos["id"], "carol")
    assert done["status"] == "completed", hub.runner.track(cos["id"])[-3:]
    [rel] = [j for j in hub.automation.of(co["locator"]) if j["automation"] == "release-change-order"]
    assert rel["status"] == "success", rel
    assert [s["name"] for s in rel["steps"]][:5] == ["Check the change order is ready", "Approve the carried revisions", "Bring the Frame into force",
                                                     "Move items by the state map", "Publish the revisions"]
    assert "approved by carol" in rel["acting_on"]
    assert ap_limit(hub) == 1500.0
    new = hub.kernel.get(draft["locator"])
    assert new["state"] == "effective" and new["fields"]["approved_by"] == "carol" and hub.kernel.get(policy["locator"])["state"] == "obsolete"
    assert new["folder"] == "Policy", "a policy stays in Policy"
    assert hub.kernel.get(co["locator"])["state"] == "released"
    assert sopdocs.source_texts(hub)["finance-policy"] == hub.released_frames().get("finance-policy").text


def test_a_change_order_with_a_broken_sop_cannot_be_approved(hub):
    co = hub.kernel.create("change-order", "Quality/Changes", {"title": "Broken", "description": "x", "requested_by": "carol"}, by="carol")
    draft = eco.add_sop(hub, co["number"], "document-control-sop", by="carol")
    write(hub, draft, "Approvals in document-control go to the reviewer.", "Approvals in document-control go to the wizard.", "carol")
    pv = eco.preview(hub, co["number"])
    assert pv["errors"], "the preview shows the drafts do not compile"
    assert not eco.status(hub, co["number"])["ready"]
    with pytest.raises(ValueError):
        eco.release(hub, co["number"], by="carol")
    # Staging the broken draft (as the automation would once it were approved) records why it does not compile.
    fields = eco.stage(hub, co["number"], drafts={"document-control-sop": draft["locator"]})
    assert fields["staged_errors"]


def test_frames_are_not_compiled_from_disk_once_the_sops_are_documents(hub):
    path = hub.frames.get("finance-policy").path
    path.write_text(path.read_text().replace(OLD, NEW))
    hub._frames = None
    assert hub.compile(save=False)["changes"] == [], "an edit to the Frame file is not policy: the documents are the source"


def test_a_settings_change_is_a_direct_change_its_review_round_writes_into_the_sop(hub):
    from hubproto import changecontrol
    from hubproto.rules import rule_key

    rules = hub.settings_draft("bob")["rules"]
    ap = next(r for r in rules if r["kind"] == "approval_criteria" and r["approver"] == "cog:ap-reviewer" and r["process"] == "purchase-to-pay")
    hub.settings_edit("bob", {"action": "replace", "key": rule_key(ap), "rule": {**{k: v for k, v in ap.items() if k != "source"}, "max_amount": 3000.0}})
    th = next(r for r in rules if r["kind"] == "threshold" and r["name"] == "purchase-approval-threshold")
    hub.settings_edit("bob", {"action": "replace", "key": rule_key(th), "rule": {**{k: v for k, v in th.items() if k != "source"}, "value": 8000.0}})
    out = hub.settings_propose("bob", reason="tune limits")
    assert out["change_order"] and all(r["ok"] for r in out["report"]), out["report"]
    co = hub.kernel.get(out["locator"])
    assert co["fields"]["from_settings"] and co["state"] == "open"
    assert eco.carried(hub, co) == [] and len(co["fields"]["direct_changes"]) == 2, "Settings records direct changes; it writes no prose"
    assert hub.settings_drafts.load("bob") is None
    changecontrol.request_review(hub, co["number"], "bob")
    st = eco.status(hub, co["number"])
    names = sorted(c["name"] for c in st["carried"])
    assert names == ["finance-policy", "purchasing-policy"], names
    fin = sopdocs.frame_text(hub, next(c["draft"] for c in st["carried"] if c["name"] == "finance-policy"))
    assert "under 3,000 USD" in fin and "under 2,000 USD when the three-way" not in fin
    pv = eco.preview(hub, co["number"])
    texts = [(c["rule"].get("source") or {}).get("text", "") for c in pv["changes"] if c["op"] == "add"]
    assert any("under 3,000 USD" in t for t in texts) and any("8,000 USD" in t for t in texts), texts
    assert any(c.get("widening") and c["op"] != "text" for c in pv["changes"]), "a higher cog limit is a widening"
    again = hub.settings_draft("bob")
    hub.settings_edit("bob", {"action": "remove", "key": rule_key(next(r for r in again["rules"] if r["kind"] == "sla" and r["process"] == "document-control"))})
    second = hub.settings_propose("bob")
    assert second["change_order"] == co["number"], "the same open settings change order collects the next changes"


def test_once_the_sops_are_documents_a_model_change_is_a_direct_change_on_a_change_order(hub):
    from hubproto import changecontrol

    out = hub.propose_cog_model("quality-assistant", "claude-opus-5-5", by="bob")
    assert out["change_order"] and all(r["ok"] for r in out["report"]), out
    co = hub.kernel.get(out["locator"])
    [d] = co["fields"]["direct_changes"]
    assert d["rule"]["kind"] == "cog" and d["rule"]["model"] == "claude-opus-5-5" and d["origin"]["frame"] == "cog-profiles"
    b = hub.releases.bundle(hub.releases.current()["digest"])
    assert b["cogs"]["quality-assistant"]["model"] != "claude-opus-5-5", "nothing changes until the change order is released"
    changecontrol.request_review(hub, co["number"], "bob")
    [c] = eco.carried(hub, hub.kernel.get(co["locator"]))
    assert c["name"] == "cog-profiles"
    text = sopdocs.frame_text(hub, c["draft"])
    section = text.split("## Cog: quality-assistant", 1)[1].split("\n## ", 1)[0]
    assert "Model: claude-opus-5-5" in section and "Model: ollama/qwen3:14b" in text, "only that cog's Model line changes"
    pv = eco.preview(hub, co["number"])
    assert any((ch.get("rule") or {}).get("kind") == "cog" and (ch.get("rule") or {}).get("model") == "claude-opus-5-5" for ch in pv["changes"])
    new = hub.propose_new_cog(name="audit-helper", roles=["staff"], model="ollama/qwen3:14b", about="Helps with audits.",
                              persona="advisor", by="bob")
    assert new["change_order"] == out["change_order"], "the same open change order collects the person's policy changes"
    changecontrol.request_review(hub, co["number"], "bob")
    names = sorted(x["name"] for x in eco.carried(hub, hub.kernel.get(out["locator"])))
    assert names == ["cog-profiles", "org-model"], names


def test_converting_turns_the_frame_documents_into_the_sops_themselves(tmp_path):
    """The document that carried a Frame becomes the SOP: one document per SOP, its number and history kept."""
    import shutil

    from conftest import ROOT, release_all

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    before = h.frame_document("document-control-sop")
    assert before["folder"] == "Quality/Controlled Documents" and before["fields"]["doc_type"] == "sop"
    sopdocs.convert(h, by="carol")
    after = h.kernel.get(before["locator"])
    assert after["fields"]["lineage"] == "sop:document-control-sop" and after["fields"]["filename"].endswith(".docx")
    assert sopdocs.in_force(h.kernel, "document-control-sop")["locator"] == before["locator"], "the same document, not a copy"
    sops = [d for d in h.kernel.query(type_="document") if d["fields"].get("sop_name") == "document-control-sop"]
    assert len(sops) == 1
    assert h.compile(save=False)["changes"] == []
