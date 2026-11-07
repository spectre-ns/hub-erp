"""Review 7 Nov 2026, AGENT, the edit engine on real files (reliability 3-6 and minors; cogs-5).

A paragraph operation acts on the paragraph inside a table, list or content control, never on the whole container;
sheet edits keep formulas and cell comments and read prefixed and gapped serialisations; text files keep their
encoding; case folding next to "İ" does not shift the match; nested docx paragraphs are read once; "in section:"
scopes a replace."""

import io
import zipfile

import pytest

from hubproto import edits, office, wopi

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def ops(src: str) -> list[dict]:
    return edits.parse_ops(src)


def files_of(content: bytes) -> dict[str, bytes]:
    z = zipfile.ZipFile(io.BytesIO(content))
    return {n: z.read(n) for n in z.namelist()}


def with_part(blank: bytes, name: str, xml: str) -> bytes:
    files = files_of(blank)
    files[name] = xml.encode()
    return edits._rezip(list(files), files)


def p(text: str) -> str:
    return f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def docx(body: str) -> bytes:
    blank, _ = wopi.blank_document("docx")
    return with_part(blank, "word/document.xml", f'<?xml version="1.0"?><w:document {W_NS}><w:body>{body}<w:sectPr/></w:body></w:document>')


def text(content: bytes, ext: str) -> list[str]:
    return [x for x in office.extract_text(content, ext, limit=100000).splitlines() if x.strip()]


# ---------------------------------------------------------------- reliability-3: containers
def table(*rows: str) -> str:
    return "<w:tbl>" + "".join(f"<w:tr><w:tc>{p(r)}</w:tc></w:tr>" for r in rows) + "</w:tbl>"


def test_delete_paragraph_in_a_table_removes_only_its_row():
    content = docx(p("Intro") + table("Torque check", "Gap check", "Burr check") + p("Outro"))
    new, report = edits.apply_ops(content, "docx", ops("@@ delete paragraph: Gap check"))
    assert report[0]["ok"], report
    assert text(new, "docx") == ["Intro", "Torque check", "Burr check", "Outro"]


def test_replace_paragraph_in_a_table_is_refused_with_a_report_and_the_table_is_kept():
    content = docx(p("Intro") + table("Torque check", "Gap check") + p("Outro"))
    new, report = edits.apply_ops(content, "docx", ops("@@ replace paragraph: Gap check\nClearance check"))
    assert new is None and not report[0]["ok"] and "inside a table" in report[0]["detail"]
    # The words inside it are changed with a replace, and the table stays a table.
    new, report = edits.apply_ops(content, "docx", ops("@@ replace: Gap check\n@@ with: Clearance check"))
    assert report[0]["ok"] and text(new, "docx") == ["Intro", "Torque check", "Clearance check", "Outro"]
    assert files_of(new)["word/document.xml"].count(b"<w:tbl>") == 1


def test_a_paragraph_in_a_content_control_is_not_removed_with_the_control():
    sdt = f"<w:sdt><w:sdtContent>{p('Signed: QA')}{p('Date: today')}</w:sdtContent></w:sdt>"
    content = docx(p("Intro") + sdt + p("Outro"))
    new, report = edits.apply_ops(content, "docx", ops("@@ delete paragraph: Date: today"))
    after = text(new, "docx") if new else text(content, "docx")
    # Either only that paragraph goes, or the hub refuses and says why; never the whole control.
    assert "Signed: QA" in after and "Intro" in after and "Outro" in after
    assert report[0]["ok"] == ("Date: today" not in after), report


def test_delete_section_with_a_list_keeps_the_next_section():
    md = "# Plan\n## Steps\n- one\n- two\n## Records\nKeep them.\n".encode()
    new, report = edits.apply_ops(md, "md", ops("@@ delete paragraph: two"))
    assert report[0]["ok"], report
    assert new.decode() == "# Plan\n## Steps\n- one\n## Records\nKeep them.\n"


# ---------------------------------------------------------------- cogs-5: replace within a section
def test_replace_in_a_section_changes_only_that_section():
    md = b"# SOP\n## Purpose\nAudit yearly.\n## Scope\nAudit yearly.\n"
    new, report = edits.apply_ops(md, "md", ops("@@ replace: yearly\n@@ with: twice a year\n@@ in section: Scope"))
    assert report[0]["ok"], report
    assert new.decode() == "# SOP\n## Purpose\nAudit yearly.\n## Scope\nAudit twice a year.\n"
    assert "Scope" in edits.describe(ops("@@ replace: a\n@@ with: b\n@@ in section: Scope")[0])


def test_replace_in_a_missing_section_is_refused():
    md = b"# SOP\n## Purpose\nAudit yearly.\n"
    new, report = edits.apply_ops(md, "md", ops("@@ replace: yearly\n@@ with: monthly\n@@ in section: Scope"))
    assert new is None and not report[0]["ok"]


# ---------------------------------------------------------------- reliability-4: formulas and comments
def xlsx(sheet_xml: str, shared: str | None = None) -> bytes:
    blank, _ = wopi.blank_document("xlsx")
    files = files_of(blank)
    files["xl/worksheets/sheet1.xml"] = sheet_xml.encode()
    if shared is not None:
        files["xl/sharedStrings.xml"] = shared.encode()
    return edits._rezip(list(files), files)


def test_a_text_replace_never_turns_a_formula_into_a_constant():
    sheet = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
             '<row r="1"><c r="A1"><v>2</v></c><c r="B1"><f>A1*2</f><v>4</v></c></row></sheetData></worksheet>')
    new, report = edits.apply_ops(xlsx(sheet), "xlsx", ops("@@ replace: 4\n@@ with: 8"))
    xml = files_of(new)["xl/worksheets/sheet1.xml"].decode() if new else sheet
    assert "<f>A1*2</f>" in xml


def ods(rows_xml: str) -> bytes:
    blank, _ = wopi.blank_document("ods")
    content = files_of(blank)["content.xml"].decode()
    import re

    content = re.sub(r"<table:table-row.*</table:table-row>", rows_xml, content, flags=re.S) if "<table:table-row" in content \
        else content.replace("</table:table>", rows_xml + "</table:table>", 1)
    return with_part(blank, "content.xml", content)


def test_an_ods_edit_keeps_cell_comments():
    rows = ('<table:table-row><table:table-cell office:value-type="string"><office:annotation><dc:creator>carol</dc:creator>'
            '<text:p>check the limit</text:p></office:annotation><text:p>Torque</text:p></table:table-cell>'
            '<table:table-cell office:value-type="string"><text:p>ok</text:p></table:table-cell></table:table-row>')
    content = ods(rows)
    new, report = edits.apply_ops(content, "ods", ops("@@ replace: Torque\n@@ with: Torque (Nm)"))
    assert report[0]["ok"], report
    xml = files_of(new)["content.xml"].decode()
    assert "<office:annotation>" in xml and "check the limit" in xml
    g = {f"{edits._col(c)}{r}": v for (r, c), v in edits.Sheet(new, "ods").grid().items()}
    assert g["A1"] == "Torque (Nm)"  # the comment is not part of the value


# ---------------------------------------------------------------- reliability-5: other serialisations
def test_an_xlsx_with_a_namespace_prefix_is_edited():
    sheet = ('<?xml version="1.0"?><x:worksheet xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><x:sheetData>'
             '<x:row r="1"><x:c r="A1" t="inlineStr"><x:is><x:t>Washer</x:t></x:is></x:c></x:row></x:sheetData></x:worksheet>')
    new, report = edits.apply_ops(xlsx(sheet), "xlsx", ops("@@ set: B1: 12"))
    assert report[0]["ok"] and new is not None, report
    g = {f"{edits._col(c)}{r}": v for (r, c), v in edits.Sheet(new, "xlsx").grid().items()}
    assert g == {"A1": "Washer", "B1": "12"}


def test_an_ods_edit_after_a_large_empty_gap_keeps_rows_in_place():
    rows = ('<table:table-row><table:table-cell office:value-type="string"><text:p>Head</text:p></table:table-cell></table:table-row>'
            '<table:table-row table:number-rows-repeated="3000"><table:table-cell/></table:table-row>'
            '<table:table-row><table:table-cell office:value-type="string"><text:p>Bottom total</text:p></table:table-cell></table:table-row>')
    new, report = edits.apply_ops(ods(rows), "ods", ops("@@ set: A1: Header"))
    assert report[0]["ok"], report
    g = {f"{edits._col(c)}{r}": v for (r, c), v in edits.Sheet(new, "ods").grid().items()}
    assert g == {"A1": "Header", "A3002": "Bottom total"}


def test_a_huge_repeat_count_is_not_expanded_in_memory():
    rows = ('<table:table-row table:number-rows-repeated="1048000"><table:table-cell table:number-columns-repeated="16384"/>'
            '</table:table-row>')
    new, report = edits.apply_ops(ods(rows), "ods", ops("@@ set: A1: x"))
    assert report  # it answers (applied or refused) without building a billion-cell grid


# ---------------------------------------------------------------- reliability-6: encodings and folding
def test_a_non_utf8_text_file_is_refused_not_mangled():
    content = "Prüfanweisung – Lieferant Müller\r\nLine two\r\n".encode("cp1252")
    new, report = edits.apply_ops(content, "txt", ops("@@ replace: Line two\n@@ with: Row two"))
    assert new is None and not report[0]["ok"]  # never written back with every non-ASCII character replaced


def test_a_utf8_file_keeps_its_bom_and_line_endings():
    content = "﻿Line one\r\nLine two\r\n".encode("utf-8")
    new, report = edits.apply_ops(content, "txt", ops("@@ replace: two\n@@ with: 2"))
    assert report[0]["ok"] and new == "﻿Line one\r\nLine 2\r\n".encode("utf-8")


def test_a_bom_does_not_hide_the_first_heading():
    content = "\ufeff## Scope\nAll suppliers.\n".encode("utf-8")
    new, report = edits.apply_ops(content, "md", ops("@@ append to section: Scope\nAnd subcontractors."))
    assert report[0]["ok"], report
    out = new.decode("utf-8")
    assert out.startswith("\ufeff## Scope\n") and out.index("All suppliers.") < out.index("And subcontractors.")


def test_case_folding_next_to_dotted_capital_i_does_not_shift_the_match():
    assert len(edits.fold("İstanbul")) == len("İstanbul")
    md = "İzmir plant: audit yearly.\n".encode()
    new, report = edits.apply_ops(md, "md", ops("@@ replace: Audit Yearly\n@@ with: audit twice a year"))
    assert report[0]["ok"], report
    assert new.decode() == "İzmir plant: audit twice a year.\n"


# ---------------------------------------------------------------- minor: nested docx text read once
def test_text_in_a_textbox_is_extracted_once():
    inner = p("In the box")
    box = f'<w:p><w:r><w:t>Before</w:t></w:r><w:r><w:pict><w:txbxContent>{inner}</w:txbxContent></w:pict></w:r></w:p>'
    assert text(docx(box), "docx") == ["Before", "In the box"]


@pytest.mark.parametrize("ext", ["docx", "odt"])
def test_plain_documents_still_read_as_before(ext):
    blank, _ = wopi.blank_document(ext)
    content = office.insert_paragraphs(blank, ext, ["# Title", "One line.", "| A | B |"], "replace")
    assert text(content, ext)[:2] == ["Title", "One line."]
