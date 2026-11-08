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
    assert st["proposal"] is None, "nothing is compiled until a revision is approved"
    pv = eco.preview(hub, co["number"])
    assert any("1,500" in (c["rule"].get("source") or {}).get("text", "") for c in pv["changes"]) and not pv["errors"]
    assert any("must be approved through document control first" in r for r in eco.check_approval(hub, co["number"]))
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
    # The approval triggered the compile-frame automation: the Frame is compiled from the approved revision and staged.
    [job] = [j for j in hub.automation.of(co["locator"]) if j["automation"] == "compile-frame"]
    assert job["status"] == "success" and [s["status"] for s in job["steps"]] == ["done"] * 5, job
    assert f"{draft['number']} was approved" in job["trigger"]
    st = eco.status(hub, co["number"])
    assert st["proposal"]["eco"] == co["number"] and any("1,500" in (c["rule"].get("source") or {}).get("text", "") for c in st["proposal"]["changes"])
    assert eco.check_approval(hub, co["number"]) == [], "the compiled Frame is ready"

    # The change order: checked, approved by the quality manager, then released by the release automation.
    corun = hub.runner.submit("change-order", {"record": {}}, owner="alice", record=co["locator"])
    cos = hub.runner.load(corun)
    assert cos["status"] == "waiting" and cos["pending"]["assignees"] == ["carol"]
    done = approve(hub, corun, "carol")
    assert done["status"] == "completed", hub.runner.track(corun)[-3:]
    [rel] = [j for j in hub.automation.of(co["locator"]) if j["automation"] == "release-change-order"]
    assert rel["status"] == "success", rel
    assert [s["name"] for s in rel["steps"]][:3] == ["Check the change order is ready", "Bring the Frame into force", "Publish the revisions"]
    assert "approved by carol" in rel["acting_on"]
    assert ap_limit(hub) == 1500.0
    new = hub.kernel.get(draft["locator"])
    assert new["state"] == "effective" and hub.kernel.get(policy["locator"])["state"] == "obsolete"
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


def test_a_settings_change_is_written_into_its_sop_on_a_change_order(hub):
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
    st = eco.status(hub, co["number"])
    names = sorted(c["name"] for c in st["carried"])
    assert names == ["finance-policy", "purchasing-policy"], names
    fin = sopdocs.frame_text(hub, next(c["draft"] for c in st["carried"] if c["name"] == "finance-policy"))
    assert "under 3,000 USD" in fin and "under 2,000 USD when the three-way" not in fin
    pv = eco.preview(hub, co["number"])
    texts = [(c["rule"].get("source") or {}).get("text", "") for c in pv["changes"] if c["op"] == "add"]
    assert any("under 3,000 USD" in t for t in texts) and any("8,000 USD" in t for t in texts), texts
    assert any(c.get("widening") and c["op"] != "text" for c in pv["changes"]), "a higher cog limit is a widening"
    assert hub.settings_drafts.load("bob") is None
    again = hub.settings_draft("bob")
    hub.settings_edit("bob", {"action": "remove", "key": rule_key(next(r for r in again["rules"] if r["kind"] == "sla" and r["process"] == "document-control"))})
    second = hub.settings_propose("bob")
    assert second["change_order"] == co["number"], "the same open settings change order collects the next changes"


def test_once_the_sops_are_documents_a_model_change_is_an_sop_edit_on_a_change_order(hub):
    out = hub.propose_cog_model("quality-assistant", "claude-opus-5-5", by="bob")
    assert out["change_order"] and all(r["ok"] for r in out["report"]), out
    co = hub.kernel.get(out["locator"])
    [c] = eco.carried(hub, co)
    assert c["name"] == "cog-profiles"
    text = sopdocs.frame_text(hub, c["draft"])
    section = text.split("## Cog: quality-assistant", 1)[1].split("\n## ", 1)[0]
    assert "Model: claude-opus-5-5" in section and "Model: ollama/qwen3:14b" in text, "only that cog's Model line changes"
    b = hub.releases.bundle(hub.releases.current()["digest"])
    assert b["cogs"]["quality-assistant"]["model"] != "claude-opus-5-5", "nothing changes until the change order is released"
    pv = eco.preview(hub, co["number"])
    assert any((ch.get("rule") or {}).get("kind") == "cog" and (ch.get("rule") or {}).get("model") == "claude-opus-5-5" for ch in pv["changes"])
    new = hub.propose_new_cog(name="audit-helper", roles=["staff"], model="ollama/qwen3:14b", prompt="You help with audits.",
                              follows=["audit-sop"], autonomy="propose", duties=[], by="bob")
    assert new["change_order"] == out["change_order"], "the same open change order collects the person's policy changes"
    names = sorted(x["name"] for x in eco.carried(hub, hub.kernel.get(out["locator"])))
    assert names == ["cog-profiles", "org-model"], names
