"""Controlled templates: the profile's Markdown templates in the library as .docx documents of class template,
imported idempotently by lineage, approved through document-control like an SOP, read-only once effective, and
changed only through a new revision that supersedes the earlier one of the same template (never one by title)."""

import shutil
from urllib.parse import quote

import pytest

from conftest import ROOT, release_all, seen
from hubproto import office
from hubproto import templates as tpl
from hubproto.app import Hub
from hubproto.kernel import KernelGuardError
from test_shell import get, post, shell  # noqa: F401  (the module-scoped live shell)


@pytest.fixture
def hub(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    return h


def doc(hub, number):
    return hub.kernel.get(f"document:{number}")


def line(report, name):
    return next(r for r in report if r["lineage"] == f"iso9001/{name}")


def approve_template(hub, rec, by="alice", approver="bob"):
    rid = hub.runner.submit("document-control", {"record": {"change_type": "major"}}, owner=by, record=rec["locator"])
    st = hub.runner.load(rid)
    assert st["status"] == "waiting" and approver in st["pending"]["assignees"], st.get("pending")
    hub.runner.decide(rid, approver, "approve", meaning="fit for use", payload_digest=seen(hub.runner, rid))
    return hub.kernel.get(rec["locator"])


def test_the_markdown_templates_come_in_as_docx_drafts(hub):
    report = hub.import_templates(by="alice")
    assert len(report) == len(tpl.sources(hub.frames.root)) == 11 and {r["action"] for r in report} == {"created"}
    ncr = doc(hub, line(report, "ncr.md")["number"])
    f = ncr["fields"]
    assert ncr["state"] == "draft" and ncr["folder"] == tpl.FOLDER
    assert f["doc_class"] == "template" and f["doc_type"] == "template" and f["revision"] == "A" and f["template_for"] == "ncr"
    assert f["filename"] == "ncr.docx" and f["mime"] == tpl.DOCX_MIME and f["template_source"] == "iso9001/ncr.md"
    assert f["title"] == "Nonconformance report (NCR)" and f["placeholders"][:3] == ["number", "title", "reported_by"]


def test_a_docx_keeps_headings_tables_lists_and_placeholders_as_plain_text(hub):
    md = (ROOT / "frames/iso9001/templates/capa.md").read_text(encoding="utf-8")
    content = tpl.to_docx(md)
    xml = office._zip_safe(content).read("word/document.xml").decode("utf-8")
    assert '<w:pStyle w:val="Heading1"/>' in xml and '<w:pStyle w:val="Heading2"/>' in xml and "<w:tbl>" in xml
    text = office.extract_text(content, "docx")
    for name in tpl.placeholders(md):
        assert "{{" + name + "}}" in text, name
    assert "`" not in text, "inline code marks are dropped, the words kept"
    soft = (ROOT / "frames/iso9001/templates/training-record.md").read_text(encoding="utf-8")
    lines = tpl.markdown_lines(soft)
    assert any(l.startswith("I have read and understood the document named above, the quality policy and objectives") for l in lines), \
        "a soft-wrapped paragraph is one paragraph"


def test_importing_again_changes_nothing_and_a_changed_source_updates_the_draft(hub, tmp_path):
    first = hub.import_templates(by="alice")
    again = hub.import_templates(by="alice")
    assert {r["action"] for r in again} == {"unchanged"} and [r["number"] for r in again] == [r["number"] for r in first]
    count = len(hub.kernel.query(type_="document"))
    src = hub.frames.root / "iso9001/templates/deviation.md"
    src.write_text(src.read_text(encoding="utf-8") + "\n## Approval\n{{approved_by}}\n", encoding="utf-8")
    changed = line(hub.import_templates(by="alice"), "deviation.md")
    assert changed["action"] == "updated" and changed["number"] == line(first, "deviation.md")["number"]
    assert len(hub.kernel.query(type_="document")) == count, "never a duplicate"
    rec = doc(hub, changed["number"])
    assert len(hub.dms.versions(rec["locator"])) == 2 and "{{approved_by}}" in hub.dms.text_of(rec)


def test_a_template_is_approved_into_force_and_then_read_only(hub):
    report = hub.import_templates(by="alice")
    rec = approve_template(hub, doc(hub, line(report, "complaint.md")["number"]))
    assert rec["state"] == "effective" and rec["fields"]["doc_class"] == "template", "approval keeps it a template"
    assert rec["fields"]["approved_by"] == "bob"
    with pytest.raises(KernelGuardError, match="new revision"):
        hub.dms.add_version(rec["locator"], b"x", by="alice", filename="complaint.docx")
    with pytest.raises(KernelGuardError):
        hub.dms.checkout(rec["locator"], by="alice")


def test_a_new_revision_supersedes_only_its_own_lineage(hub):
    report = hub.import_templates(by="alice")
    capa = approve_template(hub, doc(hub, line(report, "capa.md")["number"]))
    # A document with the same title but another lineage is not touched when the revision comes into force.
    other = approve_template(hub, doc(hub, line(report, "change-request.md")["number"]))
    hub.kernel.set_fields(other["locator"], {"title": capa["fields"]["title"]}, by="hub")
    src = hub.frames.root / "iso9001/templates/capa.md"
    src.write_text(src.read_text(encoding="utf-8") + "\n## Lessons learned\n{{lessons}}\n", encoding="utf-8")
    rev = line(hub.import_templates(by="alice"), "capa.md")
    assert rev["action"] == "revised" and rev["number"] != capa["number"]
    new = doc(hub, rev["number"])
    assert new["fields"]["revises"] == capa["number"] and new["fields"]["revision"] == "B" and new["fields"]["template_source"] == "iso9001/capa.md"
    assert line(hub.import_templates(by="alice"), "capa.md")["action"] == "unchanged"
    new = approve_template(hub, new)
    assert new["state"] == "effective" and doc(hub, capa["number"])["state"] == "obsolete"
    assert doc(hub, other["number"])["state"] == "effective", "same title, another template: still in force"
    [g] = [g for g in tpl.templates(hub.kernel) if g["lineage"] == "iso9001/capa.md"]
    assert g["effective"]["number"] == new["number"] and g["open"] is None and len(g["revisions"]) == 2


def test_a_revision_started_from_the_library_copies_the_template_in_force(hub):
    report = hub.import_templates(by="alice")
    eff = approve_template(hub, doc(hub, line(report, "audit-finding.md")["number"]))
    bundle = hub.releases.bundle(hub.releases.current()["digest"])
    assert "only its owner" in tpl.may_revise(bundle, eff, "dave")
    new = tpl.new_revision(hub, eff["locator"], by="alice", bundle=bundle)
    assert new["state"] == "draft" and new["fields"]["revises"] == eff["number"] and new["fields"]["revision"] == "B"
    assert hub.dms.content(new["locator"])[0] == hub.dms.content(eff["locator"])[0]
    assert tpl.new_revision(hub, eff["locator"], by="alice", bundle=bundle)["number"] == new["number"], "one open revision at a time"
    with pytest.raises(ValueError, match="edit it as it is"):
        tpl.new_revision(hub, new["locator"], by="alice", bundle=bundle)


def test_the_library_lists_templates_and_starts_a_revision(shell):
    hub = shell["hub"]
    report = hub.import_templates(by="alice")
    status, text = get(shell, "/templates", **{"as": "carol"})
    assert status == 200 and "<h1>" in text and "Nonconformance report (NCR)" in text and "not yet" in text and "Edit online" in text
    assert 'href="/templates?as=carol"' in text or "/templates" in text, "listed in the Documents app"
    eff = approve_template(hub, doc(hub, line(report, "supplier-evaluation.md")["number"]))
    status, text = get(shell, "/document/" + quote(eff["locator"], safe=":"), **{"as": "alice"})
    assert "Start a new revision" in text
    status, text = get(shell, "/document/" + quote(eff["locator"], safe=":"), **{"as": "dave"})
    assert "Start a new revision" not in text
    status, url, text = post(shell, "/template-revise", {"as": "alice", "doc": eff["locator"]})
    assert status == 200 and "is the new revision (B)" in text, text[:400]
    status, text = get(shell, "/templates", **{"as": "carol"})
    assert eff["number"] in text and "rev B" in text


def test_seeding_a_profile_with_an_author_imports_its_templates(tmp_path, capsys):
    from hubproto import cli

    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    assert cli.main(["--root", str(tmp_path), "init"]) in (0, None)
    cli.main(["--root", str(tmp_path), "seed", "iso9001", "--as", "alice"])
    out = capsys.readouterr().out
    assert "templates: 11 created" in out
    cli.main(["--root", str(tmp_path), "templates", "import", "--as", "alice"])
    assert "templates: 11 unchanged" in capsys.readouterr().out
