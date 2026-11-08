"""Frames and cog settings as documents: a Frame is a controlled document; a change is a draft written in the hub,
compiled into a proposal, reviewed by the policy owners and written into the Frame when released. A cog profile links
documents for its prompt and its references, pinned to a version by each release."""

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
    assert system.startswith("You are the quality assistant of Example Instruments.") and "M5: 4 Nm" in system and "REFERENCE DOCUMENTS" in system
    # A newer version of the brief is not used until a release pins it, and pinning it is a reviewed change.
    hub.dms.add_version(prompt["locator"], b"You are the quality assistant. Be gentle.", by="hub", filename="qa-brief.md", mime="text/markdown", system=True)
    assert cog_context(ctx, None).startswith("You are the quality assistant of Example Instruments.")
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
