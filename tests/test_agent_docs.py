"""Document and procedure tools for cogs: create a working document, apply edit operations the agent writes,
versions and their diff, read a procedure with file line numbers, search the procedures; governed by autonomy."""

import shutil

import pytest

from conftest import ROOT, release_all
from hubproto import office, wopi
from hubproto.agent import AgentStore, AgentTools
from hubproto.app import Hub
from hubproto.cogs import RunContext, cog_context


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    root = tmp_path_factory.mktemp("hub")
    shutil.copytree(ROOT / "frames", root / "frames")
    h = Hub(root)
    h.kernel.seed()
    release_all(h)
    return h


def tools(hub, mode="act", cog="quality-assistant"):
    return AgentTools(hub, cog, f"t-{mode}", mode, 8, AgentStore(hub.root / "state" / "agents"), requester="carol")


def text_of(hub, number):
    content, v = hub.dms.content(f"rec:document:{number}")
    return office.extract_text(content, wopi.extension_of(v.get("filename") or ""), limit=100000)


def test_create_a_working_document_with_content_where_the_cog_may(hub):
    t = tools(hub)
    out = t.execute("create_document", {"title": "Torque notes", "folder": "Policy", "text": "# Torque notes\nTorque 2 Nm.\n- check weekly"})
    assert out["ok"] and out["format"] == "docx" and out["state"] == "open"
    rec = hub.kernel.get(f"rec:document:{out['document']}")
    assert rec["fields"]["doc_class"] == "working" and rec["fields"]["author"] == "quality-assistant"
    assert "Torque 2 Nm." in hub.dms.text_of(rec) and "check weekly" in text_of(hub, out["document"])
    md = t.execute("create_document", {"title": "Plain notes", "folder": "Policy", "format": "md", "text": "## Plain\nA line."})
    assert md["ok"] and hub.dms.content(f"rec:document:{md['document']}")[0].decode().startswith("## Plain")
    sheet = t.execute("create_document", {"title": "Log", "folder": "Policy", "format": "xlsx", "text": "| Item | Qty |\n| Washer | 12 |"})
    assert sheet["ok"] and "Washer" in text_of(hub, sheet["document"])
    ap = type(t)(hub, "ap-reviewer", "t-ap", "act", 5, t.store, requester="carol")  # a cog with no rights on quality documents
    refused = ap.execute("create_document", {"title": "Nope", "folder": "Quality/Controlled Documents"})
    assert "error" in refused and "create document in Quality/Controlled Documents" in refused["error"]
    assert "error" in t.execute("create_document", {"title": "Nowhere", "folder": "No/Such"})
    assert "error" in t.execute("create_document", {"title": "Bad", "folder": "Policy", "format": "exe"})


def test_apply_document_ops_with_a_report_per_operation(hub):
    t = tools(hub)
    doc = t.execute("create_document", {"title": "Audit plan", "folder": "Policy",
                                        "text": "# Audit plan\n## Purpose\nAudit suppliers yearly.\n## Scope\nAll suppliers shall be audited.\nOld line.\n## Records\nKeep 3 years."})
    ops = "@@ replace: shall\n@@ with: must\n@@ delete paragraph: Old line.\n@@ insert after: Audit suppliers yearly.\nCritical ones twice a year.\n" \
          "@@ delete section: Records\n@@ delete: not in the document\n@@ juggle the widgets"
    out = t.execute("apply_document_ops", {"number": doc["document"], "ops": ops})
    assert out["ok"] and out["version"] == 2
    assert [c["ok"] for c in out["changes"]] == [True, True, True, True, False, False]
    lines = [x for x in text_of(hub, doc["document"]).splitlines() if x.strip()]
    assert lines == ["Audit plan", "Purpose", "Audit suppliers yearly.", "Critical ones twice a year.", "Scope", "All suppliers must be audited."]
    v = hub.dms.versions(f"rec:document:{doc['document']}")[-1]
    assert v["by"] in ("hub", "quality-assistant") and "edited by quality-assistant" in v["note"] and "agent run t-act" in v["note"]
    assert "error" in t.execute("apply_document_ops", {"number": doc["document"], "ops": "just make it better"})


def test_a_document_in_force_is_not_edited(hub):
    run_id = hub.runner.submit("document-control", {"record": {"title": "SOP-DOCS", "change_type": "major", "body": "# SOP\nA step."}}, owner="alice")
    hub.runner.decide(run_id, "bob", "approve", meaning="approved", payload_digest=hub.runner.seen_digest(hub.runner.load(run_id)))
    rec = hub.kernel.get(hub.runner.load(run_id)["record"])
    assert rec["state"] == "effective"
    out = tools(hub).execute("apply_document_ops", {"number": rec["number"], "ops": "@@ replace: step\n@@ with: stage"})
    assert "error" in out and "not a working document" in out["error"]


def test_versions_and_a_diff_between_two(hub):
    t = tools(hub)
    doc = t.execute("create_document", {"title": "Diff me", "folder": "Policy", "text": "First line.\nSecond line."})
    t.execute("apply_document_ops", {"number": doc["document"], "ops": "@@ replace: Second\n@@ with: Other"})
    out = t.execute("document_versions", {"number": doc["document"], "compare": [1, 2]})
    assert [v["version"] for v in out["versions"]] == [1, 2] and out["versions"][1]["by"] in ("hub", "quality-assistant")
    assert "-Second line." in out["diff"] and "+Other line." in out["diff"]
    assert "error" in t.execute("document_versions", {"number": doc["document"], "compare": [1, 9]})
    assert "error" in t.execute("document_versions", {"number": "DOC-9999"})


def test_read_a_procedure_with_its_file_line_numbers(hub):
    out = tools(hub).execute("read_procedure", {"name": "document-control-sop"})
    frame = hub.frames.get("document-control-sop")
    first = out["text"].splitlines()[0]
    n, _, line = first.partition(": ")
    assert int(n) == frame.body_start and line == frame.line(frame.body_start)
    for row in out["text"].splitlines()[:30]:
        n, _, line = row.partition(": ")
        assert frame.line(int(n)) == line
    part = tools(hub).execute("read_procedure", {"name": "document-control-sop", "from_line": 40, "to_line": 42})
    assert part["lines"] == "40-42" and len(part["text"].splitlines()) == 3
    miss = tools(hub).execute("read_procedure", {"name": "document-control"})
    assert "error" in miss and "document-control-sop" in miss["error"]


def test_search_procedures_finds_the_passage_that_answers(hub):
    out = tools(hub).execute("search_procedures", {"query": "who approves a minor revision"})
    top = out["results"][0]
    assert top["procedure"] == "document-control-sop" and "minor revision" in top["text"]
    lo, hi = map(int, top["lines"].split("-"))
    frame = hub.frames.get("document-control-sop")
    assert frame.line(lo).strip() and "minor revision" in "\n".join(frame.line(n) for n in range(lo, hi + 1))
    assert len(tools(hub).execute("search_procedures", {"query": "supplier approved list", "k": 3})["results"]) <= 3
    assert "error" in tools(hub).execute("search_procedures", {"query": "a"})


def test_where_an_sop_says_a_person_confirms_the_write_tools_are_recorded_as_proposals(hub):
    # "The doc-reviewer cog may create documents in Quality/Controlled Documents when a document controller confirms."
    t = tools(hub, cog="doc-reviewer")
    out = t.execute("create_document", {"title": "Proposed doc", "folder": "Quality/Controlled Documents"})
    assert out.get("proposed") and t.proposed[-1]["tool"] == "create_document" and t.proposed[-1]["confirmer"] == "document-controller"
    assert not [d for d in hub.kernel.query(type_="document") if d["fields"].get("title") == "Proposed doc"]
    looked = t.execute("search_procedures", {"query": "minor revision"})
    assert looked["results"] and not t.proposed[-1]["tool"] == "search_procedures"
    # Nowhere an SOP gives it the right, it is refused and says what should be done instead.
    out = t.execute("create_document", {"title": "Policy doc", "folder": "Policy"})
    assert "describe what should be done instead" in out["error"]
    r = tools(hub, mode="read")
    assert "error" in r.execute("apply_document_ops", {"number": "DOC-0001", "ops": "@@ delete: x"})
    assert "results" in r.execute("search_procedures", {"query": "minor revision"})


def test_the_cog_is_told_about_these_tools(hub):
    bundle = hub.releases.bundle(hub.releases.current()["digest"])
    ctx = RunContext(run_id="t", release="", bundle=bundle, owner="quality-assistant", kernel=hub.kernel, frames=lambda: hub.frames,
                     extras={"cog_profile": bundle["cogs"]["quality-assistant"]})
    text = cog_context(ctx, None)
    for name in ("create_document", "apply_document_ops", "document_versions", "read_procedure", "search_procedures"):
        assert f"- {name}: " in text, name


def test_an_agent_quoting_an_sop_draft_as_read_document_shows_it_changes_what_it_meant(hub):
    """read_document shows an SOP as the compile reads it ('## Scope', '| Audit report | QA |', its header); the
    operations the agent writes from those lines find the heading, the section and the row's cell (review of #36)."""
    from test_edits import LINES, SOP_QUOTES
    from hubproto import sopdocs

    t = tools(hub)

    def sop_draft(title):  # an SOP's lineage and Frame header, as a draft on a change order has; in a folder the cog edits
        made = t.execute("create_document", {"title": title, "folder": "Policy", "text": "\n".join(LINES)})
        loc = f"rec:document:{made['document']}"
        hub.kernel.set_fields(loc, {"lineage": sopdocs.LINEAGE + "supplier-audit", "frame_header": "---\nname: supplier-audit\nversion: 1\n---"},
                              by="carol")
        return hub.kernel.get(loc)

    rec = sop_draft("Agent SOP draft")
    read = t.execute("read_document", {"number": rec["number"]})
    assert "## Scope" in read["text"] and "| Audit report | QA |" in read["text"] and "not in the file" in read["note"]
    out = t.execute("apply_document_ops", {"number": rec["number"], "ops": SOP_QUOTES})
    assert out.get("ok") and all(c["ok"] for c in out["changes"]), out
    lines = [x for x in text_of(hub, rec["number"]).splitlines() if x.strip()]
    assert "Purpose" not in lines and lines[lines.index("Scope") + 1] == "Applies to every supplier." and "Quality" in lines, lines
    gone = sop_draft("Agent SOP draft, Records")
    out = t.execute("apply_document_ops", {"number": gone["number"], "ops": "@@ delete section: ## Records"})
    assert out.get("ok") and "Records" not in text_of(hub, gone["number"]), out
