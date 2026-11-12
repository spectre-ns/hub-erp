"""Frames and cog settings as documents: a Frame is a controlled document; a change is a draft written in the hub,
compiled into a proposal, reviewed by the policy owners and written into the Frame when released. A cog profile links
documents for its prompt and its references, pinned to a version by each release."""

import json

import pytest

from conftest import edit_frame, release_all
from hubproto.compile import CompileError
from hubproto.frames import FrameError


def test_a_frame_draft_is_compiled_reviewed_and_released_into_the_frame(released):
    hub = released
    doc = hub.frame_document("finance-policy")
    assert doc["fields"]["doc_class"] == "controlled" and doc["state"] == "effective" and doc["folder"] == "Policy"
    assert doc["fields"]["doc_type"] == "policy" and doc["fields"]["title"] != "finance-policy (policy Frame)", "filed as the policy it is"
    sop = hub.frame_document("document-control-sop")
    assert sop["folder"] == "Quality/Controlled Documents" and sop["fields"]["doc_type"] == "sop", "an SOP is a quality document"
    draft = hub.frame_draft("finance-policy", by="alice")
    assert draft["fields"]["doc_type"] == "frame-draft" and hub.frame_draft("finance-policy", by="alice")["locator"] == draft["locator"]
    text = hub.draft_text(draft).replace("Finance clerks may create bills in Finance/Payables.",
                                          "Finance clerks may create bills in Finance/Payables.\nStaff may read Finance/Payables.")
    hub.dms.add_version(draft["locator"], text.encode(), by="alice", filename="finance-policy.frame.md", mime="text/markdown")
    preview = hub.compile(overrides={"finance-policy": hub.draft_text(hub.kernel.get(draft["locator"]))}, save=False)
    assert any("Staff" in c["reason"] or "staff" in c["reason"] for c in preview["changes"]) and not hub.proposals.list()[-1].get("overrides")
    proposal = hub.compile(overrides={"finance-policy": hub.draft_text(hub.kernel.get(draft["locator"]))}, drafts={"finance-policy": draft["locator"]})
    assert proposal["overrides"]["finance-policy"]["draft"] == draft["locator"]
    assert hub.kernel.get(draft["locator"])["fields"]["proposal_number"] == proposal["number"]
    before = hub.frames.get("finance-policy").version
    with pytest.raises(CompileError):
        hub.release(proposal["id"], "bob")  # a widening needs two policy owners
    hub.review(proposal["id"], "bob", approve_all=True, meaning="ok")
    hub.review(proposal["id"], "carol", approve_all=True, meaning="ok")
    hub.release(proposal["id"], "bob")
    frame = hub.frames.get("finance-policy")
    assert "Staff may read Finance/Payables." in frame.text and frame.version != before, "the file was written and its version raised"
    doc = hub.kernel.get(doc["locator"])
    assert hub.dms.content(doc["locator"])[0].decode() == frame.text and doc["fields"]["frame_version"] == frame.version
    assert hub.kernel.get(draft["locator"])["state"] == "released"
    bundle = hub.releases.bundle(hub.releases.current()["digest"])
    assert any(p["subject"] == "role:staff" and p["resource"] == "folder:Finance/Payables" and p["action"] == "read" for p in bundle["permissions"])


def test_a_pending_draft_keeps_its_own_proposal(released):
    """Review ux-3 / governance-14: one proposal per draft. A compile of the files leaves someone's pending draft in
    its own open proposal (never merged into another), and proposing the same draft again replaces its own earlier
    proposal, carrying the decisions given on it."""
    hub = released
    draft = hub.frame_draft("finance-policy", by="alice")
    text = hub.draft_text(draft) + "\nStaff may read Finance.\n"
    first = hub.compile(overrides={"finance-policy": text}, drafts={"finance-policy": draft["locator"]})
    files = hub.compile()  # someone recompiles the files from the CLI
    assert hub.proposals.load(first["id"])["status"] == "open" and not files["overrides"]
    hub.review(first["id"], "bob", approve_all=True)
    again = hub.compile(overrides={"finance-policy": text}, drafts={"finance-policy": draft["locator"]})
    old = hub.proposals.load(first["id"])
    assert old["status"] == "superseded" and old["superseded_by_number"] == again["number"]
    assert again["overrides"]["finance-policy"]["draft"] == draft["locator"]
    assert all(any(d["by"] == "bob" for d in c["decisions"]) for c in again["changes"])


def test_a_draft_may_not_rename_its_frame(released):
    hub = released
    text = hub.frames.get("finance-policy").text.replace("name: finance-policy", "name: money-policy")
    with pytest.raises(FrameError, match="a Frame keeps its name"):
        hub.compile(overrides={"finance-policy": text}, save=False)


def test_a_cog_takes_its_prompt_and_references_from_documents_pinned_by_the_release(released):
    hub = released
    prompt = hub.dms.create(title="Quality assistant brief", doc_class="controlled", folder="Policy", by="carol",
                            content=b"You are the quality assistant of Example Instruments. Be blunt about missing traceability.",
                            filename="qa-brief.md", mime="text/markdown", doc_type="brief")
    ref = hub.dms.create(title="Torque table", doc_class="controlled", folder="Policy", by="carol",
                         content=b"M4: 2 Nm\nM5: 4 Nm", filename="torque.md", mime="text/markdown", doc_type="reference")
    edit_frame(hub, "cog-profiles", "## Cog: quality-assistant\n",
               f"## Cog: quality-assistant\nPrompt from: {prompt['number']}\nReferences: {ref['number']}\n", bump="1.1.0")
    release_all(hub)
    b = hub.releases.bundle(hub.releases.current()["digest"])
    prof = b["cogs"]["quality-assistant"]
    assert prof["prompt_doc"]["number"] == prompt["number"] and prof["prompt_doc"]["version"] == 1
    assert prof["reference_docs"][0]["number"] == ref["number"]
    from hubproto.cogs import RunContext, cog_context

    ctx = RunContext(run_id="t", release="", bundle=b, owner="quality-assistant", kernel=hub.kernel, dms=hub.dms,
                     frames=lambda: hub.frames, extras={"cog_profile": prof})
    system = cog_context(ctx, None)
    assert system.startswith("== WHO YOU ARE ==\nYou are the quality assistant of Example Instruments.") and "M5: 4 Nm" in system
    assert "Reference documents your profile pins" in system.split("== REFERENCE: THE KNOWLEDGE BASE ==")[1]
    # A newer version of the brief is not used until a release pins it, and pinning it is a reviewed change.
    hub.dms.add_version(prompt["locator"], b"You are the quality assistant. Be gentle.", by="hub", filename="qa-brief.md", mime="text/markdown", system=True)
    assert cog_context(ctx, None).startswith("== WHO YOU ARE ==\nYou are the quality assistant of Example Instruments.")
    proposal = hub.compile()
    change = next(c for c in proposal["changes"] if c["rule"]["kind"] == "cog" and c["rule"]["name"] == "quality-assistant" and c["op"] == "add")
    assert change["widening"] is True and change["rule"]["prompt_doc"]["version"] == 2


def test_a_link_to_a_missing_document_blocks_the_release(released):
    hub = released
    edit_frame(hub, "cog-profiles", "## Cog: doc-reviewer\n", "## Cog: doc-reviewer\nPrompt from: DOC-9999\n", bump="1.1.0")
    proposal = hub.compile()
    assert any("links document 'DOC-9999', which does not exist" in f["detail"] for f in proposal["findings"] if f["severity"] == "error")


def test_a_frame_draft_can_be_withdrawn_and_is_no_longer_compiled(released):
    hub = released
    draft = hub.frame_draft("cog-profiles", by="carol")
    text = hub.draft_text(draft) + "\nTest change\n"
    hub.dms.add_version(draft["locator"], text.encode("utf-8"), by="carol", filename="cogs.frame.md", mime="text/markdown", note="test")
    p = hub.compile(overrides={"cog-profiles": text}, drafts={"cog-profiles": draft["locator"]})
    assert "cog-profiles" in p["overrides"]
    assert not hub.compile()["overrides"]  # a draft stays in its own proposal; a compile of the files does not take it
    import pytest as _pt
    with _pt.raises(ValueError):
        hub.withdraw_frame_draft(draft["locator"], "alice")  # neither its author nor a policy owner
    out = hub.withdraw_frame_draft(draft["locator"], "bob", "it was a test")
    assert out["proposals_withdrawn"]
    assert not hub.compile()["overrides"]  # the files again
    assert hub.kernel.get(draft["locator"])["fields"]["withdrawn"] is True
    assert hub.frame_draft("cog-profiles", by="carol")["locator"] != draft["locator"]  # a new edit starts a new draft


def test_a_frame_that_is_not_released_has_no_document(released):
    hub = released
    path = hub.frames.get("finance-policy").path.parent / "draft-only.frame.md"
    path.write_text('---\ntype: frame [0.2]\nname: draft-only\ndescription: "a draft"\nvisibility: internal\nversion: "0.1.0"\nscope: department\n'
                    'maintainer: carol\ninherits: org-model\n---\n# Draft only\n')
    hub._frames = None
    assert hub.frame_document("draft-only") is None, "an unreleased Frame is not a document in force"
    assert all(d["fields"].get("frame_name") != "draft-only" for d in hub.frame_documents())


def test_a_retired_frame_document_comes_back_when_its_frame_is_released(released):
    hub = released
    doc = hub.frame_document("finance-policy")
    hub.kernel.transition(doc["locator"], "obsolete", by="test", reason="as if its Frame had been dropped")
    again = hub.frame_document("finance-policy")
    assert again["locator"] == doc["locator"] and again["state"] == "effective"


def test_the_stdlib_frontmatter_parser_reads_a_one_line_json_value(monkeypatch):
    """Without PyYAML (the container has none) the hub's own parser reads workflow_ids the drafter writes."""
    import hubproto.frames as frames

    monkeypatch.setattr(frames, "yaml", None)
    data, _ = frames.parse_frontmatter('---\nname: x\nworkflow_ids: {"gizmo-review": {"in-review": "review"}}\n---\nbody\n')
    assert data["workflow_ids"] == {"gizmo-review": {"in-review": "review"}}


def test_frontmatter_flow_values_read_without_pyyaml(monkeypatch):
    """The Docker image and CI have no PyYAML: the frontmatter subset reads YAML flow style itself (nested, bare or
    quoted keys and values, trailing commas, a trailing comment) the way PyYAML would, JSON exactly (numbers, escapes,
    null), and a malformed value says what is wrong; a bracketed title that is no list stays a string, as before."""
    import pytest

    import hubproto.frames as frames

    monkeypatch.setattr(frames, "yaml", None)
    data, _ = frames.parse_frontmatter(
        '---\nname: x\nworkflow_ids: {gizmo-review: {in-review: review}}\njson: {"a": "x\\ny", "n": 1, "t": true, "z": null}\n'
        "tags: [x, 'y, z', 'it''s', ]\nurl: {http://x: y}\nnote: [a, b] # a comment\ntitle: [Draft] Supplier review\n---\nbody\n")
    assert data["workflow_ids"] == {"gizmo-review": {"in-review": "review"}}
    assert data["json"] == {"a": "x\ny", "n": 1, "t": True, "z": None}, "JSON reads exactly"
    assert data["tags"] == ["x", "y, z", "it's"] and data["url"] == {"http://x": "y"} and data["note"] == ["a", "b"]
    assert data["title"] == "[Draft] Supplier review"
    data, _ = frames.parse_frontmatter('---\nclauses: [7.2, 7.10]\nmixed: [1, true, NaN]\nmap: {"a": NaN}\n'
                                       'company: {Company} quality policy\nesc: ["a\\\\", b] # c\n---\n')
    assert data["clauses"] == ["7.2", "7.10"], "list items stay strings: 7.10 is not 7.1"
    assert data["mixed"] == ["1", "true", "NaN"] and data["map"] == {"a": "NaN"}
    assert data["company"] == "{Company} quality policy" and data["esc"] == ["a\\", "b"]
    for bad, why in (("{a b}", "expected ':'"), ('{a: "b}', "not closed"), ("[base, 'x]", "not closed"), ("[a, {b}]", "expected ':'")):
        with pytest.raises(frames.FrameError, match=why):
            frames.parse_frontmatter(f"---\nv: {bad}\n---\n")


def test_a_quoted_frontmatter_value_reads_its_escapes_without_pyyaml(monkeypatch):
    """A top-level quoted value reads as PyYAML reads it and as the same string inside a list does (#9): a description
    written with json.dumps comes back as it went in. What YAML would refuse keeps the old reading: quotes stripped."""
    import json

    import hubproto.frames as frames

    said = 'He said "hi" \\ and left'
    text = (f"---\ndescription: {json.dumps(said)}\nsingle: 'it''s'\nlisted:\n  - {json.dumps(said)}\n"
            f"inline: [{json.dumps(said)}]\nplain: \"no escapes\"\nwin: \"C:\\path\"\n---\nbody\n")
    monkeypatch.setattr(frames, "yaml", None)
    data, _ = frames.parse_frontmatter(text)
    assert data["description"] == said and data["listed"] == [said] and data["inline"] == [said], "top level and lists agree"
    assert data["single"] == "it's" and data["plain"] == "no escapes"
    assert data["win"] == "C:\\path", "an escape JSON has no reading for keeps the old reading"


# json.dumps(..., ensure_ascii=False) writes these as they are; str.splitlines() breaks a line at each of them.
LINE_SEPARATORS = [" ", " ", "\x85"]


def _header_text_with(sep: str) -> tuple[dict, str]:
    from hubproto import sopdocs

    meta = {"type": "frame [0.2]", "name": "x", "description": f"first{sep}second", "visibility": "internal", "version": "1.0.0",
            "inherits": ["org-model"]}
    return meta, sopdocs.header_text(meta) + "\n# X\n\nbody\n"


@pytest.mark.parametrize("sep", LINE_SEPARATORS, ids=["U+2028", "U+2029", "U+0085"])
def test_a_line_separator_in_a_description_reads_back_without_pyyaml(monkeypatch, sep):
    """A description holding U+2028, U+2029 or U+0085 reads back as header_text wrote it (#32): the stdlib subset ends a
    frontmatter line at a line break only, as YAML does, and the header kept on a document ends with its ``---``."""
    from pathlib import Path

    import hubproto.frames as frames

    monkeypatch.setattr(frames, "yaml", None)
    meta, text = _header_text_with(sep)
    frame = frames.load_frame_text(text, Path("x.frame.md"))
    assert frame.frontmatter == meta and frame.description == meta["description"]
    assert frame.body.startswith("# X"), "the body starts after the closing ---"
    head = frames.header_of(text, frame.body_start)
    assert head.endswith("\n---") and head + "\n# X\n\nbody\n" == text, "the kept header is the whole frontmatter block"


@pytest.mark.parametrize("sep", [" ", " "], ids=["U+2028", "U+2029"])
def test_a_line_separator_in_a_description_reads_as_pyyaml_reads_it(monkeypatch, sep):
    """With PyYAML installed the stdlib subset reads the header as PyYAML does. (Not U+0085: PyYAML folds it inside
    double quotes into a space, so with PyYAML that one does not read back exactly.)"""
    yaml = pytest.importorskip("yaml")
    import hubproto.frames as frames

    meta, text = _header_text_with(sep)
    assert yaml.safe_load(text.split("\n---", 1)[0][3:]) == meta
    monkeypatch.setattr(frames, "yaml", None)
    assert frames.parse_frontmatter(text)[0] == meta


def test_a_converted_sop_keeps_its_whole_header_when_its_description_holds_a_line_separator(hub):
    """Converting the SOPs keeps each Frame's header on its document: with a U+2028 in a description the header still
    ends with its closing ``---``, and the document reads back as the Frame's text."""
    from hubproto import sopdocs

    said = "Who may file and approve supplier bills, how they are routed, and how purchase-to-pay runs."
    edit_frame(hub, "finance-policy", "description: Who may file and approve supplier bills, how they are routed, and how "
               "purchase-to-pay runs.", f"description: {json.dumps(said, ensure_ascii=False)}")
    assert hub.frames.get("finance-policy").description == said
    release_all(hub)
    assert "finance-policy" not in sopdocs.roundtrip_problems(hub), "the header and the body read back as the Frame"
    sopdocs.convert(hub, by="carol")
    doc = sopdocs.in_force(hub.kernel, "finance-policy")
    head = doc["fields"]["frame_header"]
    assert head.startswith("---\n") and head.endswith("\n---"), f"the stored header lost its closing ---: {head!r}"
    assert sopdocs.frame_text(hub, doc).startswith(head + "\n# Finance policy")
