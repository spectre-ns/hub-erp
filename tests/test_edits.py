"""Targeted edits: the operations a cog writes (insert at a place, replace, delete a paragraph or a section,
rewrite; in sheets set, clear, insert and delete rows) applied to docx, odt, md, xlsx and ods; what a live edit is
checked against; and the end-to-end path from a comment asking for a deletion to the document without it."""

import io
import json
import re
import zipfile
from urllib.parse import quote

import pytest

from hubproto import edits, office, wopi
from test_editor import docx_with_comment
from test_shell import shell, get, post  # noqa: F401  (the module-scoped live shell)

LINES = ["# Supplier audit", "## Purpose", "Audit suppliers yearly.", "## Scope", "All suppliers **shall** be audited.",
         "Old line to delete.", "## Records", "Keep records for 3 years.", "| Record | Owner |", "|---|---|", "| Audit report | QA |"]


def make(ext: str, lines=LINES) -> bytes:
    blank, _ = wopi.blank_document(ext)
    if ext in ("md", "txt"):
        return ("\n".join(lines) + "\n").encode()
    return office.insert_paragraphs(blank, ext, lines, "replace")


def text_of(content: bytes, ext: str) -> str:
    return office.extract_text(content, ext, limit=100000)


def ops(src: str) -> list[dict]:
    return edits.parse_ops(src)


# ---------------------------------------------------------------- parsing
def test_operations_are_read_from_what_a_model_writes():
    got = ops('''Sure, here you go:
```
@@ insert at start
# Title
@@ insert after: "Audit suppliers yearly."
Second line.
@@ insert before: Scope
@@ append to section: Records
- one more
@@ replace: shall
@@ with: must
@@ replace: colour
colour, as in UK English
@@ replace paragraph: Old line
New line.
@@ replace section: Purpose
New purpose.
@@ delete: “quoted”
@@ delete paragraph: Old line
@@ delete section: Records
@@ rewrite
# All new
@@ set: b3: 42
@@ clear: A2 : C4
@@ delete rows: 4-6
@@ insert rows at: 2
| a | b |
@@ append rows
@@ frobnicate the widget
```''')
    names = [o["op"] for o in got]
    assert names == ["insert", "insert", "insert", "insert", "replace", "replace", "replace_paragraph", "replace_section", "delete",
                     "delete_paragraph", "delete_section", "rewrite", "set", "clear", "delete_rows", "insert_rows", "append_rows", "unknown"]
    assert got[0] == {"op": "insert", "at": "start", "lines": ["# Title"]}
    assert got[1]["after"] == "Audit suppliers yearly." and got[1]["lines"] == ["Second line."]
    assert got[2]["before"] == "Scope" and got[2]["lines"] == []
    assert got[4]["with"] == "must" and got[5]["with"] == "colour, as in UK English"
    assert got[8]["target"] == "quoted"
    assert got[12] == {"op": "set", "cell": "B3", "value": "42"} and got[13]["range"] == "A2:C4" and got[14]["from"] == 4 and got[14]["to"] == 6
    assert got[15]["at"] == 2 and got[15]["lines"] == ["| a | b |"]


def test_plain_text_without_operations_is_an_insert_at_the_start():
    assert ops("# Header\nSome text") == [{"op": "insert", "at": "start", "lines": ["# Header", "Some text"]}]


# ---------------------------------------------------------------- text documents
@pytest.mark.parametrize("ext", ["docx", "odt", "md"])
def test_text_operations_in_every_text_format(ext):
    content = make(ext)
    new, report = edits.apply_ops(content, ext, ops('''@@ replace: shall
@@ with: must
@@ delete paragraph: Old line
@@ insert after: Audit suppliers yearly.
They cover quality and delivery.
@@ insert before: Scope
## Definitions
A supplier is anyone we buy from.
@@ append to section: Scope
Contractors are included.
@@ delete section: Records
@@ insert at end
## Approval
Approved by the QA manager.
@@ delete: nonexistent thing'''))
    assert [r["ok"] for r in report] == [True, True, True, True, True, True, True, False]
    assert "could not find" in report[-1]["detail"]
    lines = [x.strip() for x in text_of(new, ext).splitlines() if x.strip()]
    flat = [re.sub(r"^#+\s*|\*\*", "", x) for x in lines]
    assert flat == ["Supplier audit", "Purpose", "Audit suppliers yearly.", "They cover quality and delivery.", "Definitions",
                    "A supplier is anyone we buy from.", "Scope", "All suppliers must be audited.", "Contractors are included.",
                    "Approval", "Approved by the QA manager."], flat


@pytest.mark.parametrize("ext", ["docx", "odt", "md"])
def test_replace_and_rewrite_sections_and_paragraphs(ext):
    new, report = edits.apply_ops(make(ext), ext, ops('''@@ replace section: Scope
## Scope
Only critical suppliers are audited.
@@ replace paragraph: Audit suppliers
Audit critical suppliers every year.
@@ delete: Keep records for 3 years.'''))
    assert all(r["ok"] for r in report), report
    t = text_of(new, ext)
    assert "Only critical suppliers are audited." in t and "Old line to delete" not in t and "All suppliers" not in t
    assert t.count("Scope") == 1  # the repeated heading is not doubled
    assert "Audit critical suppliers every year." in t and "Audit suppliers yearly." not in t and "Keep records" not in t
    new2, report2 = edits.apply_ops(new, ext, ops("@@ rewrite\n# One page\nThat is all."))
    assert report2[0]["ok"] and [x for x in text_of(new2, ext).splitlines() if x.strip()][-1].strip() == "That is all."


def test_an_inline_replace_keeps_the_formatting_of_each_run_in_docx():
    content = make("docx")
    new, report = edits.apply_ops(content, "docx", ops("@@ replace: suppliers shall be\n@@ with: suppliers must be"))
    xml = zipfile.ZipFile(io.BytesIO(new)).read("word/document.xml").decode()
    para = re.search(r"<w:p>(?:(?!</w:p>).)*All suppliers (?:(?!</w:p>).)*</w:p>", xml, re.S).group(0)
    assert report[0]["ok"] and "All suppliers must be audited." in text_of(new, "docx")
    assert "<w:b/>" in para  # the bold run is still there (now holding 'must')
    assert re.search(r"<w:b/>.*?<w:t xml:space=\"preserve\">must</w:t>", para, re.S)


def test_text_in_a_table_is_replaced_and_a_quote_style_difference_still_matches():
    content = make("docx", LINES + ["Don’t skip the closing meeting."])
    new, report = edits.apply_ops(content, "docx", ops("@@ replace: Audit report\n@@ with: Audit report and actions\n@@ delete: Don't skip the closing meeting."))
    assert all(r["ok"] for r in report) and "Audit report and actions" in text_of(new, "docx") and "closing meeting" not in text_of(new, "docx")


def test_deleting_commented_text_keeps_the_comment():
    content = docx_with_comment("@quality-assistant check the torque")
    content = office.insert_paragraphs(content, "docx", ["Second paragraph stays."], "end")
    new, report = edits.apply_ops(content, "docx", ops("@@ delete paragraph: Inspection plan"))
    assert report[0]["ok"] and "Inspection plan" not in text_of(new, "docx")
    xml = zipfile.ZipFile(io.BytesIO(new)).read("word/document.xml").decode()
    assert 'w:commentRangeStart w:id="0"' in xml and 'w:commentRangeEnd w:id="0"' in xml
    assert [c["text"] for c in office.extract_comments(new, "docx")] == ["@quality-assistant check the torque"]


def test_deleting_a_paragraph_with_a_comment_in_odt_keeps_the_comment():
    blank, _ = wopi.blank_document("odt")
    z = zipfile.ZipFile(io.BytesIO(blank))
    files = {n: z.read(n) for n in z.namelist()}
    xml = files["content.xml"].decode().replace('xmlns:office=', 'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:office=')
    xml = xml.replace("<office:text><text:p/></office:text>",
                      '<office:text><text:p>Keep me.</text:p><text:p>Drop me<office:annotation office:name="a1"><dc:creator>carol</dc:creator>'
                      "<text:p>why is this here?</text:p></office:annotation> now.</text:p></office:text>")
    files["content.xml"] = xml.encode()
    content = edits._rezip(z.namelist(), files)
    new, report = edits.apply_ops(content, "odt", ops("@@ delete paragraph: Drop me now."))
    assert report[0]["ok"]
    assert "Drop me" not in text_of(new, "odt") and "Keep me." in text_of(new, "odt")
    assert [c["text"] for c in office.extract_comments(new, "odt")] == ["why is this here?"]


def test_an_empty_document_can_be_written_into():
    for ext in ("docx", "odt", "md"):
        blank, _ = wopi.blank_document(ext)
        new, report = edits.apply_ops(blank, ext, ops("@@ insert at end\n# Hello\nWorld"))
        assert report[0]["ok"] and "Hello" in text_of(new, ext) and "World" in text_of(new, ext), ext


# ---------------------------------------------------------------- spreadsheets
def grid(content, ext):
    return {f"{edits._col(c)}{r}": v for (r, c), v in edits.Sheet(content, ext).grid().items()}


@pytest.mark.parametrize("ext", ["xlsx", "ods"])
def test_sheet_operations(ext):
    blank, _ = wopi.blank_document(ext)
    content = office.insert_rows(blank, ext, [["Item", "Limit", "Result"], ["Torque", "2", "ok"], ["Gap", "0.1", "fail"],
                                              ["Burr", "none", "ok"], ["Paint", "RAL 7035", "ok"]])
    new, report = edits.apply_ops(content, ext, ops('''@@ set: C3: pass
@@ replace: ok
@@ with: pass
@@ delete rows: 3
@@ insert rows at: 2
| Visual | none | pass |
@@ clear: B5
@@ append rows
| Total | | =COUNTIF(C2:C6;"pass") |'''))
    assert all(r["ok"] for r in report), report
    g = grid(new, ext)
    assert g["A1"] == "Item" and g["A2"] == "Visual" and g["A3"] == "Torque" and g["C3"] == "pass" and g["C4"] == "pass" and g["C5"] == "pass"
    assert g["A4"] == "Burr" and g["A5"] == "Paint" and "B5" not in g and g["A6"] == "Total"
    assert "Gap" not in g.values()


def test_xlsx_shared_strings_are_read_and_replaced():
    blank, _ = wopi.blank_document("xlsx")
    z = zipfile.ZipFile(io.BytesIO(blank))
    files = {n: z.read(n) for n in z.namelist()}
    files["xl/sharedStrings.xml"] = b'<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Washer</t></si><si><t>Bolt M6</t></si></sst>'
    files["xl/worksheets/sheet1.xml"] = (b'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:B2"/>'
                                         b'<sheetData><row r="1"><c r="A1" t="s" s="3"><v>0</v></c><c r="B1"><v>4</v></c></row><row r="2"><c r="A2" t="s"><v>1</v></c></row></sheetData></worksheet>')
    content = edits._rezip(list(files) + ([] if "xl/sharedStrings.xml" in z.namelist() else []), files)
    new, report = edits.apply_ops(content, "xlsx", ops("@@ replace: Bolt M6\n@@ with: Bolt M8\n@@ set: A1: Spring washer"))
    assert all(r["ok"] for r in report)
    g = grid(new, "xlsx")
    assert g == {"A1": "Spring washer", "B1": "4", "A2": "Bolt M8"}
    sheet = zipfile.ZipFile(io.BytesIO(new)).read("xl/worksheets/sheet1.xml").decode()
    assert 'r="A1" s="3"' in sheet  # the cell keeps its style
    assert "<dimension" not in sheet


def test_the_sheet_grid_shows_cell_addresses_for_the_cog():
    blank, _ = wopi.blank_document("xlsx")
    content = office.insert_rows(blank, "xlsx", [["Item", "Qty"], ["Washer", "12"]])
    assert edits.sheet_grid_text(content, "xlsx") == "row 1: A=Item | B=Qty\nrow 2: A=Washer | B=12"


# ---------------------------------------------------------------- live edits and their check
def test_what_a_live_edit_is_checked_against():
    t = "Purpose\nAudit suppliers yearly.\nAll suppliers must be audited."
    assert edits.op_applied({"op": "replace", "target": "shall", "with": "must"}, t)
    assert not edits.op_applied({"op": "replace", "target": "must", "with": "shall"}, t)
    assert edits.op_applied({"op": "delete_paragraph", "target": "Old line"}, t)
    assert not edits.op_applied({"op": "delete_section", "target": "Purpose"}, t)
    assert edits.op_applied({"op": "insert", "at": "start", "lines": ["# Purpose"]}, t)
    assert edits.op_applied({"op": "replace", "target": "audit", "with": "audit and review"}, t + " audit and review")
    assert edits.live_capable({"kind": "text", "ops": [{"op": "replace", "target": "a", "with": "b"}, {"op": "insert", "at": "end", "lines": ["# X", "y"]}]})
    assert not edits.live_capable({"kind": "text", "ops": [{"op": "insert", "after": "a", "lines": ["y"]}]})  # needs the cursor at a found paragraph
    assert not edits.live_capable({"kind": "text", "ops": [{"op": "delete_paragraph", "target": "a"}]})
    assert not edits.live_capable({"kind": "text", "ops": [{"op": "delete_section", "target": "a"}]})
    assert not edits.live_capable({"kind": "text", "ops": [{"op": "insert", "at": "end", "lines": ["- bullet"]}]})
    assert edits.live_capable({"kind": "sheet", "ops": [{"op": "set", "cell": "A1", "value": "1"}]})


class FakeModel:
    """Answers an edit request with fixed operations, as qwen would."""

    def __init__(self, answer: str):
        self.answer, self.calls = answer, []

    def available(self, model):
        return False

    def complete(self, **kw):
        self.calls.append(kw)
        return {"text": self.answer, "stand_in": False, "model": "fake", "served_model": "fake", "tool_calls": []}


def _doc(hub, ext="docx", title="Edit target"):
    content = make(ext)
    return hub.dms.create(title=title, doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                          filename=f"{title}.{ext}", mime=wopi.EDITABLE_EXTENSIONS.get(ext, "text/markdown"), doc_type="document")


def test_a_comment_asking_for_a_deletion_removes_it_from_the_file(shell, monkeypatch):
    hub = shell["hub"]
    doc = _doc(hub)
    fake = FakeModel("@@ delete section: Records\n@@ replace: shall\n@@ with: must\n@@ delete: Old line to delete.\n@@ delete: not in the document")
    monkeypatch.setattr(hub, "model", fake)
    hub.send("carol", "carol", "Comment", "@quality-assistant please delete the Records section and say must, not shall", record=doc["locator"])
    content, version = hub.dms.content(doc["locator"])
    t = text_of(content, "docx")
    assert version["by"] in ("hub", "quality-assistant") and "Records" not in t and "Keep records" not in t and "must be audited" in t and "Old line" not in t
    assert "OUTPUT FORMAT for a change to this document" in fake.calls[0]["system"]
    msgs = [m for m in hub.inbox.on_record(doc["locator"]) if m["from"] == "quality-assistant"]
    body = msgs[0]["body"]  # the reply states what was done and what could not be, once
    assert body.startswith("Done: delete section") and "delete paragraph" in body  # a whole paragraph's text: its paragraph
    assert "Could not: delete “not in the document”" in body and len(msgs) == 1
    applied = hub.inbox.find(msgs[0]["id"])
    assert applied["applied_in"] and [r["ok"] for r in applied["edit_report"]] == [True, True, True, False]


def test_after_a_live_edit_the_hub_finishes_what_did_not_take(shell, monkeypatch):
    hub = shell["hub"]
    doc = _doc(hub, title="Live target")
    loc = doc["locator"]
    hub.dms.checkout(loc, by="carol", via="editor")  # carol is in the editor: the seat edits live
    monkeypatch.setattr(hub, "model", FakeModel("@@ replace: shall\n@@ with: must\n@@ delete: Old line to"))
    hub.send("carol", "carol", "Comment", "@quality-assistant change shall to must and remove the words old line to", record=loc)
    j = json.loads(get(shell, "/editor-replies/" + quote(loc, safe=":"), **{"as": "carol"})[1])
    [reply] = j["replies"]
    assert reply["edit"]["ops"][0]["op"] == "replace" and edits.live_capable(reply["edit"])
    # The seat did only the replacement live; the session saved that.
    content, _ = hub.dms.content(loc)
    half, _ = edits.apply_ops(content, "docx", [reply["edit"]["ops"][0]])
    hub.dms.add_version(loc, half, by="carol", filename="Live target.docx", mime=wopi.EDITABLE_EXTENSIONS["docx"], note="saved in the editor")
    status, url, text = post(shell, "/verify-edit", {"as": "carol", "doc": loc, "id": reply["id"]})
    out = json.loads(text)
    assert status == 200 and out["fixed"] == 1 and not out["failed"]
    content, version = hub.dms.content(loc)
    assert version["by"] in ("hub", "quality-assistant") and "finished in the file" in version["note"]
    t = text_of(content, "docx")
    assert "must be audited" in t and "Old line to" not in t and "delete." in t and t.count("must") == 1
    m = hub.inbox.find(reply["id"])
    assert m["applied_in"] and [r["detail"].endswith("(by the hub, in the file)") for r in m["edit_report"]] == [False, True]
    again = json.loads(post(shell, "/verify-edit", {"as": "carol", "doc": loc, "id": reply["id"]})[2])
    assert again["already"] and again["fixed"] == 0
    hub.dms.undo_checkout(loc, by="carol")


def test_the_editor_page_carries_the_live_operations(shell, monkeypatch):
    monkeypatch.setenv("HUBPROTO_COLLABORA_URL", "http://editor.test:9980")
    monkeypatch.setenv("HUBPROTO_WOPI_BASE", "http://hub.test:8080")
    monkeypatch.setenv("HUBPROTO_COLLABORA_URLSRC", "http://editor.test:9980/browser/dist/cool.html?")
    hub = shell["hub"]
    doc = _doc(hub, title="Page target")
    status, text = get(shell, "/edit/" + quote(doc["locator"], safe=":"), **{"as": "carol"})
    assert status == 200 and "window.HubSeatOps" in text and ".uno:ExecuteSearch" in text and "verifyAfterSave" in text and "/verify-edit" in text
    hub.dms.undo_checkout(doc["locator"], by="carol")


def test_the_agent_tool_edits_with_operations(shell, monkeypatch):
    from hubproto.agent import AgentStore, AgentTools

    hub = shell["hub"]
    doc = _doc(hub, ext="md", title="Agent target")
    monkeypatch.setattr(hub, "model", FakeModel("@@ delete section: Records\n@@ insert at end\n## Review\nReviewed yearly."))
    refused = AgentTools(hub, "ap-reviewer", "t-edit0", "act", 5, AgentStore(shell["root"] / "state" / "agents"), requester="carol")
    assert "error" in refused.execute("edit_document", {"number": doc["number"], "request": "x"})  # it may not change documents there
    # The document control SOP says a document controller confirms what the doc-reviewer cog would write: it proposes.
    waits = AgentTools(hub, "doc-reviewer", "t-edit1", "act", 5, AgentStore(shell["root"] / "state" / "agents"), requester="carol")
    assert waits.execute("edit_document", {"number": doc["number"], "request": "x"}).get("proposed")
    tools = AgentTools(hub, "qa-manager", "t-edit", "act", 5, AgentStore(shell["root"] / "state" / "agents"), requester="carol")
    out = tools.execute("edit_document", {"number": doc["number"], "request": "drop the records section and add a review section"})
    assert out["ok"] and [c["ok"] for c in out["changes"]] == [True, True]
    t = hub.dms.content(doc["locator"])[0].decode()
    assert "Records" not in t and t.rstrip().endswith("Reviewed yearly.")
    last = hub.dms.versions(doc["locator"])[-1]
    assert last["by"] == "qa-manager" and "asked by carol" in last["note"]


def test_an_insert_in_the_wrong_place_is_caught_and_moved(shell, monkeypatch):
    hub = shell["hub"]
    doc = _doc(hub, title="Misplaced target")
    loc = doc["locator"]
    hub.dms.checkout(loc, by="carol", via="editor")
    monkeypatch.setattr(hub, "model", FakeModel("@@ insert after: Audit suppliers yearly.\nCritical suppliers twice a year."))
    hub.send("carol", "carol", "Comment", "@quality-assistant please add a line after the purpose", record=loc)
    [reply] = json.loads(get(shell, "/editor-replies/" + quote(loc, safe=":"), **{"as": "carol"})[1])["replies"]
    # Live, the seat put the line after the next heading instead.
    content, _ = hub.dms.content(loc)
    wrong, _ = edits.apply_ops(content, "docx", ops("@@ insert after: Scope\nCritical suppliers twice a year."))
    assert not edits.op_applied(reply["edit"]["ops"][0], text_of(wrong, "docx"))
    hub.dms.add_version(loc, wrong, by="carol", filename="Misplaced target.docx", mime=wopi.EDITABLE_EXTENSIONS["docx"], note="saved in the editor")
    out = json.loads(post(shell, "/verify-edit", {"as": "carol", "doc": loc, "id": reply["id"]})[2])
    assert out["fixed"] == 1
    lines = [x for x in text_of(hub.dms.content(loc)[0], "docx").splitlines() if x.strip()]
    assert lines.count("Critical suppliers twice a year.") == 1
    assert lines[lines.index("Audit suppliers yearly.") + 1] == "Critical suppliers twice a year."
    hub.dms.undo_checkout(loc, by="carol")


def test_content_written_on_the_operation_line_is_split_off():
    got = ops("@@ insert after: Audit suppliers yearly.      Critical suppliers twice a year.\n"
              "@@ replace: Keep records for 3 years.        Keep records for 5 years.\n"
              "@@ replace: shall -> must\n@@ replace: colour → color\n@@ replace: lonely")
    assert got[0]["after"] == "Audit suppliers yearly." and got[0]["lines"] == ["Critical suppliers twice a year."]
    assert (got[1]["target"], got[1]["with"]) == ("Keep records for 3 years.", "Keep records for 5 years.")
    assert (got[2]["target"], got[2]["with"]) == ("shall", "must") and (got[3]["target"], got[3]["with"]) == ("colour", "color")
    assert got[4]["with"] is None and edits.describe(got[4]) == "replace “lonely” (no new text given)"



def test_a_new_docx_carries_heading_styles():
    blank, _ = wopi.blank_document("docx")
    z = zipfile.ZipFile(io.BytesIO(blank))
    styles = z.read("word/styles.xml").decode()
    assert 'w:styleId="Heading1"' in styles and 'w:styleId="Heading3"' in styles and 'w:styleId="TableGrid"' in styles
    assert "styles.xml" in z.read("word/_rels/document.xml.rels").decode() and "/word/styles.xml" in z.read("[Content_Types].xml").decode()
