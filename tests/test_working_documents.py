"""Working documents (issue #74): staff and every cog create, edit and read plans, notes, analyses and reports in
Quality/Working Documents, under a short SOP of their own; a controlled document still starts in Quality/Controlled
Documents under document control, and nobody gains anything there."""

import copy

from conftest import edit_frame, release_all
from hubproto import authz
from hubproto.agent import AgentStore, AgentTools
from hubproto.tools import may_edit_document

FOLDER = "Quality/Working Documents"
CONTROLLED = "Quality/Controlled Documents"
PEOPLE = ("alice", "bob", "carol", "dave")


def cogs(bundle):
    return sorted(n for n, m in bundle["members"].items() if m.get("type") == "cog")


def tools(hub, cog, requester, tmp_path):
    return AgentTools(hub, cog, f"wd-{cog}-{requester}", "act", 5, AgentStore(tmp_path / "agents" / requester), requester=requester)


def test_the_folder_is_in_the_org_model_and_its_sop_names_staff_and_the_cogs_group(released):
    bundle = released.current_bundle()
    assert FOLDER in bundle["folders"]
    granted = sorted((r["subject"], r["action"], r["object"]) for r in bundle["permissions"]
                     if r["source"]["frame"] == "working-documents-sop")
    assert granted == [("group:cogs", "create", "document"), ("group:cogs", "read", "*"),
                       ("role:staff", "create", "document"), ("role:staff", "read", "*")], granted
    assert {r["resource"] for r in bundle["permissions"] if r["source"]["frame"] == "working-documents-sop"} == {"folder:" + FOLDER}
    rows = {r["who"] for r in bundle["responsibilities"] if r.get("frame") == "working-documents-sop"}
    assert rows == {"role:staff", "group:cogs"}, rows


def test_every_person_and_every_cog_may_create_and_read_working_documents(released):
    bundle = released.current_bundle()
    everyone = list(PEOPLE) + cogs(bundle)
    assert {"senior-qa-manager", "senior-organization", "auditor", "stores", "purchasing-assistant"} <= set(everyone)
    for who in everyone:
        assert authz.check(bundle, who, "create", "document", FOLDER)["allowed"], who
        assert authz.check(bundle, who, "read", "document", FOLDER)["allowed"], who


def test_a_working_document_drafted_by_a_senior_cog_for_bob_is_edited_by_bob_dave_and_another_cog(released, tmp_path):
    t = tools(released, "senior-qa-manager", "bob", tmp_path)
    assert t.both_may("create", "document", FOLDER)["allowed"]
    out = t.execute("create_document", {"title": "Current system deficiencies", "folder": FOLDER, "format": "md",
                                        "text": "# Deficiencies\nNo working documents folder."})
    assert out.get("ok"), out
    rec = released.kernel.get(f"rec:document:{out['document']}")
    assert rec["folder"] == FOLDER and rec["fields"]["doc_class"] == "working"
    bundle = released.current_bundle()
    for who in ("bob", "dave", "quality-assistant", "auditor"):
        ok, why = may_edit_document(bundle, who, rec)
        assert ok, f"{who} may not edit a working document: {why}"
    assert tools(released, "auditor", "dave", tmp_path).may_edit(rec) == (True, "")


def test_nobody_gains_anything_in_controlled_documents(released):
    bundle = released.current_bundle()
    without = copy.deepcopy(bundle)
    without["permissions"] = [r for r in bundle["permissions"] if r["source"]["frame"] != "working-documents-sop"]
    assert len(without["permissions"]) < len(bundle["permissions"]), "the working-documents SOP grants nothing"
    for who in sorted(bundle["members"]):
        for action in ("create", "update", "approve", "read", "comment", "delete"):
            for obj in ("document", "*"):
                for folder in (CONTROLLED, "Quality"):
                    now = authz.check(bundle, who, action, obj, folder)["allowed"]
                    before = authz.check(without, who, action, obj, folder)["allowed"]
                    assert now == before, (who, action, obj, folder)
    # Dave and the cogs still start no controlled document: that stays with authors and document controllers.
    for who in ("dave", "senior-qa-manager", "auditor"):
        assert not authz.check(bundle, who, "create", "document", CONTROLLED)["allowed"], who


def test_a_cog_working_for_someone_who_may_not_create_there_is_refused(hub, tmp_path):
    edit_frame(hub, "org-model", "- dave (person): payables-reviewer, staff\n",
               "- dave (person): payables-reviewer, staff\n- erin (person): payables-reviewer\n")
    release_all(hub)
    bundle = hub.current_bundle()
    assert not authz.check(bundle, "erin", "create", "document", FOLDER)["allowed"], "erin is not staff"
    t = tools(hub, "senior-qa-manager", "erin", tmp_path)
    refused = t.both_may("create", "document", FOLDER)
    assert not refused["allowed"] and "erin, for whom you work, may not" in refused["why"], refused
    out = t.execute("create_document", {"title": "Not for erin", "folder": FOLDER})
    assert "erin, for whom you work, may not" in str(out.get("error")), out
    assert tools(hub, "senior-qa-manager", "bob", tmp_path).both_may("create", "document", FOLDER)["allowed"]
