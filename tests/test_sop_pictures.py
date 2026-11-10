"""Pictures in SOP documents (issue #7): a picture has a place in the Frame text, a line naming it with its hash, so
an added, removed or replaced picture is a changed line of the diff; nobody reads pictures yet, so one put in or
replaced is a blocking unread-rule question for a person, and an unchanged one asks nothing. A revision the hub writes
keeps its pictures, and an SOP without pictures reads and compiles exactly as before. No model is called, and no
picture is sent to one."""

import hashlib
import io
import re
import zipfile
from pathlib import Path

import pytest

from hubproto import changecontrol as cc
from hubproto import edits, eco, office, sopdocs
from hubproto.frames import load_frame_text
from hubproto.llm_extract import sentence_groups, with_unread_rules
from test_change_control import accept_co_warnings, decide_all, gate, hub, new_co  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\n" + b"a routing flowchart"
PNG2 = b"\x89PNG\r\n\x1a\n" + b"the flowchart, redrawn"
ANCHOR = "Approvals in journal-reversal go to the controller."
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def ref(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def word_picture(content: bytes, after: str, data: bytes, *, alt="Routing of a bill", media="image1.png", rid="rId9") -> bytes:
    """``content`` with a picture in a paragraph of its own after the paragraph ``after``, written the way Word and
    Collabora write one (an anchored DrawingML picture, its part under word/media, a relationship, a VML copy for old
    readers in mc:Fallback)."""
    files = {}
    z = zipfile.ZipFile(io.BytesIO(content))
    for n in z.namelist():
        files[n] = z.read(n)
    pic = ('<w:p><w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><wp:anchor behindDoc="0" locked="0" '
           'layoutInCell="1" allowOverlap="1" relativeHeight="2"><wp:simplePos x="0" y="0"/><wp:extent cx="3200400" cy="1828800"/>'
           f'<wp:docPr id="7" name="Image 7" descr="{alt}"/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/'
           'drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="7" name="Image 7"/><pic:cNvPicPr/></pic:nvPicPr>'
           f'<pic:blipFill><a:blip r:embed="{rid}"/></pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:anchor>'
           f'</w:drawing></mc:Choice><mc:Fallback><w:pict><v:shape><v:imagedata r:id="{rid}" o:title=""/></v:shape></w:pict>'
           "</mc:Fallback></mc:AlternateContent></w:r></w:p>")
    xml = files["word/document.xml"].decode()
    para = f'<w:p><w:r><w:t xml:space="preserve">{after}</w:t></w:r></w:p>'
    assert xml.count(para) == 1
    xml = xml.replace(para, para + pic)
    ns = {"wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing", "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
          "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006", "v": "urn:schemas-microsoft-com:vml", "o": "urn:schemas-microsoft-com:office:office"}
    decl = "".join(f' xmlns:{k}="{v}"' for k, v in ns.items() if f"xmlns:{k}=" not in xml)
    files["word/document.xml"] = re.sub(r"<w:document\b", lambda m: m.group(0) + decl, xml, count=1).encode()
    files["word/_rels/document.xml.rels"] = files["word/_rels/document.xml.rels"].replace(
        b"</Relationships>", f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                             f'Target="media/{media}"/></Relationships>'.encode())
    files[f"word/media/{media}"] = data
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
        for n, d in files.items():
            zo.writestr(n, d)
    return out.getvalue()


def edit_part(content: bytes, part: str, change) -> bytes:
    z = zipfile.ZipFile(io.BytesIO(content))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
        for n in z.namelist():
            zo.writestr(n, change(z.read(n).decode()).encode() if n == part else z.read(n))
    return out.getvalue()


def picture_revision(hub, co, data: bytes, by="alice") -> dict:
    """A revision of the finance policy whose only change is a picture after the journal-reversal routing sentence."""
    d = eco.add_sop(hub, co["number"], "finance-policy", by=by)
    content, _ = hub.dms.content(d["locator"])
    hub.dms.add_version(d["locator"], word_picture(content, ANCHOR, data), by=by, filename="finance-policy.docx", mime=DOCX)
    return hub.kernel.get(d["locator"])


def review(hub, co) -> dict:
    cc.request_review(hub, co["number"], cc.requester(co), panel=[])
    return hub.proposals.load(hub.kernel.get(co["locator"])["fields"]["staged_proposal"])


def picture_questions(p: dict) -> list[dict]:
    return [q for q in p.get("questions") or [] if q.get("picture")]


# ---------------------------------------------------------------- reading and writing pictures
def test_a_picture_reads_as_a_line_naming_it_with_its_hash():
    content = word_picture(sopdocs.to_docx("# X\n\n" + ANCHOR + "\nAfter."), ANCHOR, PNG)
    assert sopdocs.docx_lines(content) == ["# X", "", ANCHOR, f"![Routing of a bill]({ref(PNG)})", "After."], \
        "one line, after its paragraph, read once (the VML copy for old readers is the same picture)"
    assert office.is_picture_line(f"![Routing of a bill]({ref(PNG)})")


def test_a_picture_reads_the_same_whatever_the_file_calls_it_and_differs_when_it_changes():
    body = "# X\n\n" + ANCHOR + "\nAfter."
    a = sopdocs.docx_lines(word_picture(sopdocs.to_docx(body), ANCHOR, PNG))
    assert sopdocs.docx_lines(word_picture(sopdocs.to_docx(body), ANCHOR, PNG, media="image12.png", rid="rId42")) == a, \
        "an editor renumbering its media parts changes nothing"
    b = sopdocs.docx_lines(word_picture(sopdocs.to_docx(body), ANCHOR, PNG2))
    assert [x for x in b if x not in a] == [f"![Routing of a bill]({ref(PNG2)})"], "a replaced picture is a changed line"


def test_the_docx_round_trip_keeps_the_picture():
    content = word_picture(sopdocs.to_docx("# X\n\n" + ANCHOR + "\nAfter."), ANCHOR, PNG)
    lines = sopdocs.docx_lines(content)
    again = sopdocs.to_docx("\n".join(lines), sopdocs.pictures_of(content))
    assert sopdocs.docx_lines(again) == lines
    z = zipfile.ZipFile(io.BytesIO(again))
    assert [z.read(n) for n in z.namelist() if n.startswith("word/media/")] == [PNG], "the picture itself, not only its line"
    assert 'Extension="png" ContentType="image/png"' in z.read("[Content_Types].xml").decode()
    with pytest.raises(ValueError, match="does not hold"):  # a picture line with no picture is refused, never written as words
        sopdocs.to_docx("\n".join(lines))


def test_a_revision_the_hub_edits_keeps_its_pictures():
    content = word_picture(sopdocs.to_docx("# X\n\n" + ANCHOR + "\nAfter."), ANCHOR, PNG)
    new, report = edits.apply_ops(content, "docx", edits.parse_ops("@@ insert at end\nA new sentence."))
    assert all(r.get("ok") for r in report)
    assert sopdocs.docx_lines(new) == ["# X", "", ANCHOR, f"![Routing of a bill]({ref(PNG)})", "After.", "A new sentence."]
    assert sopdocs.pictures_of(new)[ref(PNG)]["data"] == PNG


def test_typed_words_never_read_as_a_picture():
    fake = f"![Routing of a bill]({ref(PNG)})"
    typed = edit_part(sopdocs.to_docx("# X\nplaceholder"), "word/document.xml", lambda x: x.replace("placeholder", fake))  # typed in the editor
    assert sopdocs.docx_lines(typed) == ["# X", "\\" + fake], "the words, marked: they hold no picture"
    content = sopdocs.to_docx("# X\n\\" + fake)
    assert sopdocs.docx_lines(content) == ["# X", "\\" + fake], "and they are written back as the words"
    z = zipfile.ZipFile(io.BytesIO(content))
    assert fake in z.read("word/document.xml").decode() and not [n for n in z.namelist() if "media" in n]


def test_an_odt_picture_reads_as_its_line():
    from hubproto.wopi import blank_document

    blank, _ = blank_document("odt")
    z = zipfile.ZipFile(io.BytesIO(blank))
    files = {n: z.read(n) for n in z.namelist()}
    para = ('<text:p>See the flow.<draw:frame draw:name="Image1" svg:width="5cm" svg:height="2.5cm"><draw:image '
            'xlink:href="Pictures/1000000.png" xlink:type="simple"/><svg:desc>Routing of a bill</svg:desc></draw:frame></text:p>')
    xml = files["content.xml"].decode().replace("<text:p/>", para).replace(
        "<office:document-content", '<office:document-content xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
        'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" xmlns:xlink="http://www.w3.org/1999/xlink"')
    files["content.xml"], files["Pictures/1000000.png"] = xml.encode(), PNG
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zo:
        for n, d in files.items():
            zo.writestr(n, d)
    lines = sopdocs.docx_lines(out.getvalue(), "odt")
    assert lines[-1] == f"![Routing of a bill]({ref(PNG)})"
    assert sopdocs.pictures_of(out.getvalue(), "odt")[ref(PNG)]["cx"] == 5 * 360000


def test_a_picture_is_a_sentence_of_its_own():
    f = load_frame_text(f"---\ntype: frame\nname: x\ndescription: d\nvisibility: internal\n---\n# X\nSee the flow below\n"
                        f"![flow]({ref(PNG)})\nAfter it.\n", Path("x.frame.md"))
    assert sentence_groups(f) == [[7], [8], [9], [10]]


# ---------------------------------------------------------------- reading the diff
def _frame(body: str):
    return load_frame_text("---\ntype: frame\nname: x\ndescription: d\nvisibility: internal\n---\n" + body, Path("x.frame.md"))


@pytest.mark.parametrize("narrative", [False, True])
def test_a_new_or_replaced_picture_is_a_blocking_question_and_an_unchanged_one_asks_nothing(narrative):
    from hubproto.extract import ConventionExtractor

    head = "## Purpose\n" if narrative else "## Routing\n"
    old = _frame(f"# X\n{head}Approvals in purchase-to-pay go to the controller.\n![flow]({ref(PNG)})\n")
    base = {"digest": "d", "texts": {"x": old.text}, "rules": [], "chair": "carol"}

    def asked(body):
        f = _frame(body)
        return [q for q in with_unread_rules(f, ConventionExtractor().extract(f), base).questions if q.get("picture")]

    assert asked(f"# X\n{head}Approvals in purchase-to-pay go to the payables reviewer.\n![flow]({ref(PNG)})\n") == [], \
        "the picture did not change"
    [q] = asked(f"# X\n{head}Approvals in purchase-to-pay go to the controller.\n![flow]({ref(PNG2)})\n")
    assert q["code"] == "unread-rule" and q["blocking"] and q["chair"] == "carol" and q["line"] == 10 and ref(PNG2) in q["quote"]
    assert len(asked(f"# X\n{head}Approvals in purchase-to-pay go to the controller.\n![flow]({ref(PNG)})\n![chart]({ref(PNG2)})\n")) == 1
    assert asked(f"# X\n{head}Approvals in purchase-to-pay go to the controller.\n") == [], "a picture taken out is a changed line, not a question"


def test_the_grammar_reads_nothing_from_a_picture():
    from hubproto.extract import ConventionExtractor

    out = ConventionExtractor().extract(_frame(f"# X\n## Routing\n![Approvals go to the controller]({ref(PNG)})\n"))
    assert out.rules == [] and out.notes == []


# ---------------------------------------------------------------- change orders
def test_a_picture_only_revision_is_a_blocking_question_for_a_person(hub):
    co = new_co(hub, title="Add the routing flowchart")
    d = picture_revision(hub, co, PNG)
    text = sopdocs.frame_text(hub, d)
    assert f"![Routing of a bill]({ref(PNG)})" in text.splitlines()
    p = review(hub, co)
    [q] = picture_questions(p)
    assert q["code"] == "unread-rule" and q["blocking"] and q["status"] == "open" and q["quote"] == f"![Routing of a bill]({ref(PNG)})"
    assert q["line"] == text.splitlines().index(q["quote"]) + 1
    assert any("answer the question first" in r and "picture" in r for r in cc.approval_reasons(hub, co["number"])), \
        "it does not come into force unread"


def test_an_unchanged_picture_asks_nothing_in_the_next_revision(hub):
    co = new_co(hub, title="Add the routing flowchart")
    picture_revision(hub, co, PNG)
    [q] = picture_questions(review(hub, co))
    cc.answer_question(hub, co["number"], q["id"], "a drawing of the routing sentences above; it states no rule", "carol")
    cc.send_for_approval(hub, co["number"], "alice")
    decide_all(hub, co)
    accept_co_warnings(hub, co)
    assert cc.approval_reasons(hub, co["number"]) == []
    assert gate(hub, co)["status"] == "completed"
    released = hub.releases.frame_texts(hub.releases.current()["digest"])["finance-policy"]
    assert f"![Routing of a bill]({ref(PNG)})" in released, "the picture is part of the release"

    co2 = new_co(hub, title="Lower the AP cog limit")
    d = eco.add_sop(hub, co2["number"], "finance-policy", by="alice")
    body = sopdocs.frame_text(hub, d).split("\n---\n", 1)[1]
    content, _ = hub.dms.content(d["locator"])
    old = "Approvals in purchase-to-pay at or under 2,000 USD go to the ap-reviewer cog."
    hub.dms.add_version(d["locator"], sopdocs.to_docx(body.replace(old, old.replace("2,000", "1,500")), sopdocs.pictures_of(content)),
                        by="alice", filename="finance-policy.docx", mime=DOCX)
    assert f"![Routing of a bill]({ref(PNG)})" in sopdocs.frame_text(hub, hub.kernel.get(d["locator"])), "the hub's revision kept it"
    assert picture_questions(review(hub, co2)) == []


def test_an_sop_without_pictures_reads_and_compiles_exactly_as_before(hub):
    for name in sorted(hub.released_frames().frames):
        frame = hub.released_frames().get(name)
        content = sopdocs.to_docx(frame.body)
        assert not [n for n in zipfile.ZipFile(io.BytesIO(content)).namelist() if "media" in n]
        assert not any(office.is_picture_line(x) for x in sopdocs.docx_lines(content)), name
        assert sopdocs.frame_text(hub, sopdocs.in_force(hub.kernel, name)) == frame.text, name
    assert hub.compile(save=False)["changes"] == [] and hub.compile(save=False)["questions"] == []
    assert sopdocs.roundtrip_problems(hub) == []
