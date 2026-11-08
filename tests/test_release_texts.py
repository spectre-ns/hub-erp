"""A release records exactly the Frame texts its proposal was compiled from: never a file as it is when the release is
made (an edit since the compile was not reviewed), and never a lost edit when a draft's text overwrites a file."""

import hashlib
import shutil

import pytest

from conftest import ROOT, edit_frame, release_all
from hubproto.app import Hub
from hubproto.compile import CompileError

NARRATIVE = "Everything else is approved by a person."
REVIEWED = NARRATIVE + " (a reviewed wording)"
UNREVIEWED_RULE = "Finance clerks may approve bills in Finance/Payables.\n"


@pytest.fixture
def hub(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    return h


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def approve(hub, p):
    for who in ("bob", "carol"):
        hub.review(p["id"], who, approve_all=True)


def compile_reviewed_edit(hub):
    """A wording change compiled into a proposal, then a further edit to the same file after the compile."""
    edit_frame(hub, "finance-policy", NARRATIVE, REVIEWED)
    p = hub.compile()
    compiled = hub.frames.get("finance-policy").text
    edit_frame(hub, "finance-policy", "## Permissions\n", "## Permissions\n" + UNREVIEWED_RULE)
    return p, compiled


def test_a_release_records_the_compiled_text_not_the_file_edited_since(hub):
    p, compiled = compile_reviewed_edit(hub)
    approve(hub, p)
    meta = hub.release(p["id"], "bob")
    stored = hub.releases.frame_texts(meta["digest"])["finance-policy"]
    assert stored == compiled and REVIEWED in stored and UNREVIEWED_RULE not in stored
    assert sha(stored) == next(f["sha256"] for f in meta["frames"] if f["name"] == "finance-policy"), "the text is the one the BOM names"
    stored_file = (hub.releases.root / meta["digest"] / "frames" / "finance-policy.frame.md").read_text(encoding="utf-8")
    assert stored_file == compiled, "written as compiled, not repaired on read"
    perms = hub.releases.bundle(meta["digest"])["permissions"]
    assert not any(r.get("subject") == "role:finance-clerk" and r.get("action") == "approve" for r in perms), "the edit since is not in force"
    assert hub.released_frames().get("finance-policy").text == compiled


def test_a_release_is_refused_when_the_compiled_text_is_gone(hub):
    """A proposal compiled before texts were kept, whose file has changed since: nothing to record but an unreviewed
    text, so the release is refused and nothing changes."""
    p, _ = compile_reviewed_edit(hub)
    approve(hub, p)
    before = hub.releases.current()["digest"]
    hub.releases.blobs = None  # the compiled text is not kept anywhere
    with pytest.raises(CompileError, match=r"finance/finance-policy.frame.md.*Compile again"):
        hub.release(p["id"], "bob")
    assert hub.releases.current()["digest"] == before
    assert hub.proposals.load(p["id"])["status"] == "open"


def test_a_stored_text_that_is_not_the_compiled_one_is_read_as_the_compiled_one(hub):
    """A release made while release() read the files (before this fix) may have stored a later edit: reading it gives
    the text its bill of materials names, kept by hash at compile time, so cogs and documents see what was reviewed."""
    meta = hub.releases.current()
    path = hub.releases.root / meta["digest"] / "frames" / "finance-policy.frame.md"
    compiled = path.read_text(encoding="utf-8")
    path.write_text(compiled.replace(NARRATIVE, NARRATIVE + " (never reviewed)"), encoding="utf-8")
    hub._released_cache = {}
    assert hub.releases.frame_texts(meta["digest"])["finance-policy"] == compiled
    assert hub.released_frames().get("finance-policy").text == compiled
    hub.releases.blobs = None  # nothing kept: the stored text is all there is
    assert "(never reviewed)" in hub.releases.frame_texts(meta["digest"])["finance-policy"]


def test_a_draft_written_on_an_older_text_keeps_the_edits_it_replaces(hub):
    """Releasing a draft overwrites its Frame file. Edits on disk in neither the text in force nor the draft are kept
    by hash, and the policy owners are told."""
    pending = "Prompt: You are the purchasing assistant. You check suppliers against the approved list"
    path = hub.frames.get("cog-profiles").path
    path.write_text(path.read_text().replace(pending, pending + " (an unreleased edit)"))
    hub._frames = None
    p = hub.propose_cog_model("quality-assistant", "ollama/granite4.2:3b", by="bob")
    approve(hub, p)
    hub.release(p["id"], "bob")
    assert "(an unreleased edit)" not in path.read_text()
    [kept] = hub.proposals.load(p["id"])["replaced_on_disk"]
    assert kept["frame"] == "cog-profiles"
    assert "(an unreleased edit)" in hub.dms.blobs.get(kept["sha256"]).decode("utf-8")
    for owner in ("bob", "carol"):
        assert any("replaced unreleased edits" in (n.get("subject") or "") for n in hub.inbox.for_principal(owner))
