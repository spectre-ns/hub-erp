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


NS = {"wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing", "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006", "v": "urn:schemas-microsoft-com:vml", "o": "urn:schemas-microsoft-com:office:office",
      "asvg": "http://schemas.microsoft.com/office/drawing/2016/SVG/main"}
IMAGE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"


def word_drawing(blip: str, *, alt="Routing of a bill", q='"', crop="", xfrm="") -> str:
    """A picture the way Word and Collabora write one: an anchored DrawingML picture, with a VML copy for old readers in
    mc:Fallback (``blip`` is the a:blip element; ``q`` the quote the attributes are written with)."""
    return (f'<mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><wp:anchor behindDoc="0" locked="0" layoutInCell="1" '
            f'allowOverlap="1" relativeHeight="2"><wp:simplePos x="0" y="0"/><wp:extent cx={q}3200400{q} cy={q}1828800{q}/>'
            f'<wp:docPr id="7" name="Image 7" descr={q}{alt}{q}/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/'
            'drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="7" name="Image 7"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill>{blip}{crop}</pic:blipFill><pic:spPr><a:xfrm{xfrm}><a:off x="0" y="0"/><a:ext cx="1" cy="1"/></a:xfrm></pic:spPr>'
            '</pic:pic></a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice><mc:Fallback><w:pict><v:shape>'
            '<v:imagedata r:id="rId9" o:title=""/></v:shape></w:pict></mc:Fallback></mc:AlternateContent>')


def word_picture(content: bytes, after: str, data: bytes | None, *, alt="Routing of a bill", media="image1.png", rid="rId9",
                 run: str | None = None, target: str | None = None, more: dict | None = None, inside: bool = False) -> bytes:
    """``content`` with a picture after the paragraph ``after`` (in a paragraph of its own, or ``inside`` it), its part
    under word/media (``data``; None: no part), and a relationship ``rid`` to ``target`` (default ``media/<media>``).
    ``run`` replaces the picture's XML; ``more`` adds parts {name: bytes}."""
    files = {}
    z = zipfile.ZipFile(io.BytesIO(content))
    for n in z.namelist():
        files[n] = z.read(n)
    run = run if run is not None else word_drawing(f'<a:blip r:embed="{rid}"/>', alt=alt).replace('r:id="rId9"', f'r:id="{rid}"')
    xml = files["word/document.xml"].decode()
    para = f'<w:p><w:r><w:t xml:space="preserve">{after}</w:t></w:r></w:p>'
    assert xml.count(para) == 1
    xml = xml.replace(para, para[:-len("</w:p>")] + f"<w:r>{run}</w:r></w:p>" if inside else para + f"<w:p><w:r>{run}</w:r></w:p>")
    decl = "".join(f' xmlns:{k}="{v}"' for k, v in NS.items() if f"xmlns:{k}=" not in xml)
    files["word/document.xml"] = re.sub(r"<w:document\b", lambda m: m.group(0) + decl, xml, count=1).encode()
    files["word/_rels/document.xml.rels"] = files["word/_rels/document.xml.rels"].replace(
        b"</Relationships>", f'<Relationship Id="{rid}" Type="{IMAGE_REL}" Target="{target or "media/" + media}"/></Relationships>'.encode())
    if data is not None:
        files[f"word/media/{media}"] = data
    files.update(more or {})
    return package(files)


def package(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
        for n, d in files.items():
            zo.writestr(n, d)
    return out.getvalue()


def edit_part(content: bytes, part: str, change) -> bytes:
    z = zipfile.ZipFile(io.BytesIO(content))
    return package({n: (change(z.read(n).decode()).encode() if n == part else z.read(n)) for n in z.namelist()})


BASE = "# X\n\n" + ANCHOR + "\nAfter."


def lines_with(**kw) -> list[str]:
    data = kw.pop("data", PNG)
    return sopdocs.docx_lines(word_picture(sopdocs.to_docx(BASE), ANCHOR, data, **kw))


def picture_of(lines: list[str]) -> list[str]:
    return [x for x in lines if office.is_picture_line(x)]


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
    assert sopdocs.pictures_of(new)[ref(PNG)]["members"][0]["data"] == PNG


def test_typed_words_never_read_as_a_picture():
    fake = f"![Routing of a bill]({ref(PNG)})"
    typed = edit_part(sopdocs.to_docx("# X\nplaceholder"), "word/document.xml", lambda x: x.replace("placeholder", fake))  # typed in the editor
    assert sopdocs.docx_lines(typed) == ["# X", "\\" + fake], "the words, marked: they hold no picture"
    content = sopdocs.to_docx("# X\n\\" + fake)
    assert sopdocs.docx_lines(content) == ["# X", "\\" + fake], "and they are written back as the words"
    z = zipfile.ZipFile(io.BytesIO(content))
    assert fake in z.read("word/document.xml").decode() and not [n for n in z.namelist() if "media" in n]


def odt_with(href: str, parts: dict[str, bytes]) -> bytes:
    from hubproto.wopi import blank_document

    blank, _ = blank_document("odt")
    z = zipfile.ZipFile(io.BytesIO(blank))
    files = {n: z.read(n) for n in z.namelist()}
    para = ('<text:p>See the flow.<draw:frame draw:name="Image1" svg:width="5cm" svg:height="2.5cm"><draw:image '
            f'xlink:href="{href}" xlink:type="simple"/><svg:desc>Routing of a bill</svg:desc></draw:frame></text:p>')
    xml = files["content.xml"].decode().replace("<text:p/>", para).replace(
        "<office:document-content", '<office:document-content xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
        'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" xmlns:xlink="http://www.w3.org/1999/xlink"')
    files["content.xml"] = xml.encode()
    files.update(parts)
    return package(files)


def test_an_odt_picture_reads_as_its_line():
    content = odt_with("Pictures/1000000.png", {"Pictures/1000000.png": PNG})
    assert sopdocs.docx_lines(content, "odt")[-1] == f"![Routing of a bill]({ref(PNG)})"
    assert sopdocs.pictures_of(content, "odt")[ref(PNG)]["cx"] == 5 * 360000
    # a part name written percent-encoded is that part: its bytes, never a link
    assert sopdocs.docx_lines(odt_with("Pictures/my%20pic.png", {"Pictures/my pic.png": PNG2}), "odt")[-1] == f"![Routing of a bill]({ref(PNG2)})"


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


# ---------------------------------------------------------------- pictures written in other ways (review of #10)
def test_an_svg_picture_is_its_svg_and_its_png_together():
    blip = ('<a:blip r:embed="rId9"><a:extLst><a:ext uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}">'
            '<asvg:svgBlip r:embed="rId10"/></a:ext></a:extLst></a:blip>')

    def doc(svg: bytes) -> bytes:
        c = word_picture(sopdocs.to_docx(BASE), ANCHOR, PNG, run=word_drawing(blip), more={"word/media/image2.svg": svg})
        return edit_part(c, "word/_rels/document.xml.rels", lambda x: x.replace(
            "</Relationships>", f'<Relationship Id="rId10" Type="{IMAGE_REL}" Target="media/image2.svg"/></Relationships>'))

    a, b = doc(b"<svg>one</svg>"), doc(b"<svg>two</svg>")
    assert picture_of(sopdocs.docx_lines(a)) != picture_of(sopdocs.docx_lines(b)), "Word shows the SVG: changing it is a change"
    [line] = picture_of(sopdocs.docx_lines(a))
    again = sopdocs.to_docx("\n".join(sopdocs.docx_lines(a)), sopdocs.pictures_of(a))
    assert sopdocs.docx_lines(again) == sopdocs.docx_lines(a)
    z = zipfile.ZipFile(io.BytesIO(again))
    assert sorted(z.read(n) for n in z.namelist() if n.startswith("word/media/")) == sorted([PNG, b"<svg>one</svg>"]), "both are kept"


def test_a_picture_reads_whatever_the_quotes_and_prefixes():
    want = picture_of(lines_with())
    single = word_drawing("<a:blip r:embed='rId9'/>", q="'")
    assert picture_of(lines_with(run=single)) == want
    other_prefix = ('<x:drawing xmlns:x="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><wp:inline><wp:extent cx="1" cy="1"/>'
                    '<wp:docPr id="7" name="Image 7" descr="Routing of a bill"/><a:graphic><a:graphicData uri="x"><pic:pic><pic:blipFill>'
                    '<a:blip xmlns:q="http://schemas.openxmlformats.org/officeDocument/2006/relationships" q:embed="rId9"/>'
                    "</pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline></x:drawing>")
    assert picture_of(lines_with(run=other_prefix)) == want


def test_a_picture_only_in_the_fallback_is_read():
    only_fallback = ('<mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><wp:inline><wp:docPr id="7" name="Shape 7" '
                     'descr="Routing of a bill"/></wp:inline></w:drawing></mc:Choice><mc:Fallback><w:pict><v:shape>'
                     '<v:imagedata r:id="rId9"/></v:shape></w:pict></mc:Fallback></mc:AlternateContent>')
    assert picture_of(lines_with(run=only_fallback)) == [f"![Routing of a bill]({ref(PNG)})"]
    assert picture_of(lines_with(run=only_fallback, data=PNG2)) == [f"![Routing of a bill]({ref(PNG2)})"]


@pytest.mark.parametrize("target,part", [("media/IMAGE1.png", "image1.png"), ("media/my%20pic.png", "my pic.png"),
                                         ("/word/media/image1.png", "image1.png")])
def test_a_part_is_found_as_the_package_format_names_it(target, part):
    assert picture_of(lines_with(target=target, media=part)) == [f"![Routing of a bill]({ref(PNG)})"]
    assert picture_of(lines_with(target=target, media=part, data=PNG2)) == [f"![Routing of a bill]({ref(PNG2)})"]


def test_a_picture_the_package_does_not_hold_is_never_a_link_and_is_not_written_again():
    content = word_picture(sopdocs.to_docx(BASE), ANCHOR, None)
    [line] = picture_of(sopdocs.docx_lines(content))
    assert "(sha256:" in line, "an internal relationship with no part is not a link"
    with pytest.raises(ValueError, match="cannot write again"):
        sopdocs.to_docx("\n".join(sopdocs.docx_lines(content)), sopdocs.pictures_of(content))


@pytest.mark.parametrize("crop,xfrm", [('<a:srcRect l="10000" r="5000"/>', ""), ("", ' rot="5400000"'), ("", ' flipH="1"')])
def test_a_crop_rotation_or_flip_is_part_of_the_picture(crop, xfrm):
    plain = picture_of(lines_with())
    changed = word_drawing('<a:blip r:embed="rId9"/>', crop=crop, xfrm=xfrm)
    content = word_picture(sopdocs.to_docx(BASE), ANCHOR, PNG, run=changed)
    assert picture_of(sopdocs.docx_lines(content)) != plain, "the same bytes shown differently are a different picture"
    again = sopdocs.to_docx("\n".join(sopdocs.docx_lines(content)), sopdocs.pictures_of(content))
    assert sopdocs.docx_lines(again) == sopdocs.docx_lines(content), "and written again as they are shown"


def test_the_extension_written_is_a_plain_one():
    content = word_picture(sopdocs.to_docx(BASE), ANCHOR, PNG, media="image1.p$g")
    again = sopdocs.to_docx("\n".join(sopdocs.docx_lines(content)), sopdocs.pictures_of(content))
    assert [n for n in zipfile.ZipFile(io.BytesIO(again)).namelist() if n.startswith("word/media/")] == [f"word/media/{ref(PNG)[7:23]}.bin"]


def test_a_picture_question_stays_the_same_when_lines_above_it_change():
    from hubproto.llm_extract import picture_questions as asked

    pic = f"![flow]({ref(PNG)})"
    a = _frame(f"# X\n{pic}\n")
    b = _frame(f"# X\nA new sentence.\nAnother.\n{pic}\n")
    [qa], [qb] = asked(a, {8}), asked(b, {10})
    assert qa["id"] == qb["id"] and qa["line"] == 8 and qb["line"] == 10, "an answer given to it stays with the picture"


def test_a_text_file_never_holds_a_picture():
    class Dms:
        def content(self, locator, version=None):
            return f"# X\n![flow]({ref(PNG)})\n".encode(), {"filename": "x.md"}

    class Hub:
        dms = Dms()

    text = sopdocs.frame_text(Hub(), {"locator": "x", "fields": {"frame_header": "---\nname: x\n---"}})
    assert text.splitlines()[-1] == f"\\![flow]({ref(PNG)})"


GOLDEN = ("# Golden SOP\n\nA sentence with **bold** & <angle> words.\n  An indented line.\n- a bullet\n1. a number\n\n"
          "## Roles and responsibilities\n| Role | Responsibilities |\n|---|---|\n| Quality managers | approve it |\n\n"
          "## Permissions\nQuality managers may approve documents in Quality.\n![not a picture](see) and ![x](y)\n")


def test_an_sop_without_pictures_is_written_and_read_exactly_as_before():
    """Pinned against the hub before pictures had lines (the digests main gave): a document without pictures is the same
    file, and reads as the same text, so no release digest moves."""
    z = zipfile.ZipFile(io.BytesIO(sopdocs.to_docx(GOLDEN)))
    assert sorted(z.namelist()) == ["[Content_Types].xml", "_rels/.rels", "word/_rels/document.xml.rels", "word/document.xml", "word/styles.xml"]
    assert {n: hashlib.sha256(z.read(n)).hexdigest() for n in ("word/document.xml", "word/_rels/document.xml.rels", "[Content_Types].xml")} == {
        "word/document.xml": "124e19f080768db9beab547094e61160ed2d9914cabc7618fd0f212846d22ed2",
        "word/_rels/document.xml.rels": "5e5ca4db34a66be6fdaa7c2d90b9ef7a22399f35b3104a374af5710a241a57b3",
        "[Content_Types].xml": "42591d0489f7e86a01b2a608cd6cde7294d7753bd5a05a38dacbcb861b2cda93"}
    lines = sopdocs.docx_lines(sopdocs.to_docx(GOLDEN))
    assert hashlib.sha256("\n".join(lines).encode()).hexdigest() == "20c5acb33174a10912ed0f1f22ddc74353c62325ce4174b9a797965de968a6fb"


# ---------------------------------------------------------------- upgrading a hub whose SOPs already hold pictures
def test_pictures_already_in_force_come_in_only_with_a_change_order_that_carries_their_sop(hub):
    """An SOP in force that holds a picture, released when the hub read text only: its text in force stands for every
    change order that does not carry it; the one that carries it reads the picture, and asks about it."""
    rec = sopdocs.in_force(hub.kernel, "finance-policy")
    content, _ = hub.dms.content(rec["locator"])
    hub.dms.add_version(rec["locator"], word_picture(content, ANCHOR, PNG, inside=True), by="hub", system=True,
                        filename="finance-policy.docx", mime=DOCX)  # as it was in force before this change
    released = hub.releases.frame_texts(hub.releases.current()["digest"])["finance-policy"]
    assert sopdocs.frame_text(hub, rec, pictures=False) == released, "the reading it was released under"
    assert hub.compile(save=False)["changes"] == [] and not picture_questions(hub.compile(save=False))

    other = new_co(hub, title="Shorter document approvals")
    d = eco.add_sop(hub, other["number"], "document-control-sop", by="alice")
    body = sopdocs.frame_text(hub, d).split("\n---\n", 1)[1]
    sla = "Approve the document in document-control is due within 10 days."
    assert sla in body
    hub.dms.add_version(d["locator"], sopdocs.to_docx(body.replace(sla, sla.replace("10", "7"))), by="alice",
                        filename="document-control-sop.docx", mime=DOCX)
    p = review(hub, other)
    assert picture_questions(p) == [] and "finance-policy" not in (p.get("overrides") or {}), "finance-policy is not in this change"

    co = new_co(hub, title="Revise the finance policy")
    eco.add_sop(hub, co["number"], "finance-policy", by="alice")
    [q] = picture_questions(review(hub, co))
    assert q["quote"] == f"![Routing of a bill]({ref(PNG)})" and q["blocking"]
