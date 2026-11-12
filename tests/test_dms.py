"""The DMS: blobs, versions, classes, check-out, attachments, snapshots, intake, revisions."""

import shutil

import pytest

from conftest import BIG_BILL, ROOT, dated, release_all, seen
from hubproto.app import Hub
from hubproto.dms import BlobStore, next_revision
from hubproto.kernel import KernelGuardError

FOLDER = "Quality/Controlled Documents"


@pytest.fixture
def d(hub):
    return hub.dms


@pytest.fixture
def p2p(tmp_path) -> Hub:
    """A released hub with only the repository org model and finance policy (the purchase-to-pay flow)."""
    for sub, name in (("company", "org-model"), ("finance", "finance-policy")):
        (tmp_path / "frames" / sub).mkdir(parents=True)
        shutil.copy(ROOT / "frames" / sub / f"{name}.frame.md", tmp_path / "frames" / sub)
    h = Hub(tmp_path)
    h.kernel.seed()
    release_all(h)
    return h


def working(d, content=b"first draft", title="Working doc", **kw):
    return d.create(title=title, doc_class="working", folder=FOLDER, by="alice", content=content,
                    filename="draft.txt", **kw)


# ----------------------------------------------------------------------- blobs

def test_blobstore_dedupes_identical_content(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    sha1 = store.put(b"same bytes")
    sha2 = store.put(b"same bytes")
    assert sha1 == sha2 and len(sha1) == 64
    assert [p.name for p in (tmp_path / "blobs").iterdir()] == [sha1]
    assert store.get(sha1) == b"same bytes" and store.exists(sha1)
    other = store.put(b"other bytes")
    assert other != sha1 and len(list((tmp_path / "blobs").iterdir())) == 2
    assert not store.exists("0" * 64)


def test_content_returns_the_bytes_and_the_version_metadata(d):
    doc = working(d, b"v1 text")
    content, version = d.content(doc["locator"])
    assert content == b"v1 text"
    assert version["version"] == 1 and version["size"] == 7 and version["by"] == "alice"
    assert version["filename"] == "draft.txt" and version["mime"] == "text/plain" and version["note"] == "initial"
    d.add_version(doc["locator"], b"v2 text!", by="alice", note="second")
    assert d.content(doc["locator"])[0] == b"v2 text!"
    assert d.content(doc["locator"], version=1) == (b"v1 text", version)
    empty = d.create(title="no content", doc_class="working", folder=FOLDER, by="alice")
    with pytest.raises(KernelGuardError, match="has no content") as e:
        d.content(empty["locator"])
    assert e.value.guard == "no-content"


# -------------------------------------------------------------------- versions

def test_create_with_content_adds_version_one(d):
    doc = working(d, b"hello")
    assert doc["type"] == "document" and doc["state"] == "open"
    f = doc["fields"]
    assert f["doc_class"] == "working" and f["revision"] == "1" and f["minor"] == 0
    assert f["size"] == 5 and f["mime"] == "text/plain" and f["filename"] == "draft.txt"
    assert f["content_sha"] == d.blobs.put(b"hello") and f["checked_out_by"] is None
    assert [v["version"] for v in d.versions(doc["locator"])] == [1]
    assert d.versions(d.create(title="empty", doc_class="working", folder=FOLDER, by="alice")["locator"]) == []


def test_add_version_bumps_minor_and_versions_list_in_order(d):
    doc = working(d, b"one")
    d.add_version(doc["locator"], b"two!", by="bob", note="edited", filename="draft.md")
    rec = d.add_version(doc["locator"], b"three!!", by="alice", note="again")
    assert rec["fields"]["minor"] == 2 and rec["fields"]["revision"] == "1"
    assert rec["fields"]["content_sha"] == d.blobs.put(b"three!!") and rec["fields"]["size"] == 7
    vs = d.versions(doc["locator"])
    assert [(v["version"], v["sha256"], v["size"], v["by"], v["note"]) for v in vs] == [
        (1, d.blobs.put(b"one"), 3, "alice", "initial"),
        (2, d.blobs.put(b"two!"), 4, "bob", "edited"),
        (3, d.blobs.put(b"three!!"), 7, "alice", "again"),
    ]
    assert vs[1]["filename"] == "draft.md" and vs[1]["mime"] == "text/markdown"
    assert all(v["at"] for v in vs)


def test_add_version_only_applies_to_documents(d, hub):
    t = hub.kernel.create("task", "Widgets", {"title": "t"}, by="erin")
    with pytest.raises(KernelGuardError, match="is not a document") as e:
        d.add_version(t["locator"], b"x", by="erin")
    assert e.value.guard == "document"


def test_text_is_extracted_for_text_and_markdown_and_is_searchable(d, hub):
    md = d.create(title="SOP-9", doc_class="controlled", folder=FOLDER, by="alice",
                  content=b"# Calibration procedure\n\nUse the reference weights.", filename="sop-9.md")
    txt = d.create(title="notes", doc_class="working", folder=FOLDER, by="alice", content=b"Plain text about torque",
                   filename="notes.txt")
    pdf = d.create(title="scan", doc_class="working", folder=FOLDER, by="alice", content=b"%PDF-1.4 reference weights",
                   filename="scan.pdf")
    assert md["fields"]["mime"] == "text/markdown" and "Calibration procedure" in d.text_of(md)
    assert txt["fields"]["mime"] == "text/plain" and d.text_of(txt) == "Plain text about torque"
    assert pdf["fields"]["mime"] == "application/pdf" and d.text_of(pdf) == ""
    assert [r["number"] for r in hub.kernel.query(type_="document", text="reference weights")] == [md["number"]]
    assert [r["number"] for r in hub.kernel.query(text="about torque")] == [txt["number"]]
    assert d.versions(md["locator"])[0]["text_excerpt"].startswith("# Calibration procedure")
    d.add_version(md["locator"], b"# Calibration procedure\n\nUse the torque wrench.", by="alice", mime="text/markdown")
    assert [r["number"] for r in hub.kernel.query(text="reference weights")] == [], "search follows the latest version"
    assert [r["number"] for r in hub.kernel.query(text="torque wrench")] == [md["number"]]


def test_the_file_is_the_one_source_of_a_documents_words(d, hub):
    """#22: the record keeps no copy of a document's text. Every reading comes from the file of the version asked for,
    so search, reviews and the cogs see what the file says; a text field an older record still carries is ignored."""
    from hubproto.review import gather

    doc = d.create(title="WI-7", doc_class="working", folder=FOLDER, by="alice", content=b"Torque to 2 Nm.", filename="wi-7.txt")
    assert "text" not in doc["fields"] and d.text_of(doc) == "Torque to 2 Nm."
    d.add_version(doc["locator"], b"Torque to 3 Nm.", by="alice", filename="wi-7.txt")
    assert d.text_of(doc["locator"]) == "Torque to 3 Nm." and d.text_of(doc["locator"], version=1) == "Torque to 2 Nm."
    hub.kernel.set_fields(doc["locator"], {"text": "a stale copy about rivets"}, by="alice")  # as an older record carries it
    assert hub.kernel.query(type_="document", text="rivets") == [], "the stale copy is not the document's words"
    assert [r["number"] for r in hub.kernel.query(type_="document", text="3 nm")] == [doc["number"]]
    assert hub.kernel.query(type_="document", text="2 nm") == [], "an earlier version's words are not the document's"
    ctx = type("Ctx", (), {"dms": d})()
    assert gather(ctx, hub.kernel.get(doc["locator"]))["documents"][0]["text"] == "Torque to 3 Nm.", "a reviewer reads the file"
    assert d.text_of("rec:document:none") == "" and d.text_of(d.create(title="empty", doc_class="working", folder=FOLDER, by="alice")) == ""
    held = hub.kernel.get(doc["locator"])
    d.add_version(doc["locator"], b"Torque to 4 Nm.", by="alice", filename="wi-7.txt")
    assert d.text_of(held) == "Torque to 4 Nm.", "a record the caller held from before is read as it is now"
    big = b"x" * 30000
    assert len(d.text_of(d.create(title="long", doc_class="working", folder=FOLDER, by="alice", content=big, filename="long.txt"))) == 30000, \
        "no cap: the whole file"


def test_an_sop_reads_the_same_for_the_reviewing_cog_and_the_compile(released):
    """#29: an SOP or policy document reads as the compile reads it (sopdocs.frame_text: its header, and its body with a
    line per picture), so the cog reviewing it, the document page and the compile see the same lines."""
    from hubproto import sopdocs
    from hubproto.cogs import RunContext, item_text

    hub = released
    sopdocs.convert(hub, by="carol")
    rec = next(x for x in hub.kernel.query(type_="document") if str(x["fields"].get("lineage") or "").startswith(sopdocs.LINEAGE))
    compiled = sopdocs.frame_text(hub, rec)
    assert hub.dms.text_of(rec) == compiled and compiled.startswith("---")
    ctx = RunContext(run_id="t", release="", bundle={}, owner="quality-assistant", kernel=hub.kernel, dms=hub.dms)
    line = next(x for x in compiled.split("\n---", 1)[1].splitlines() if x.strip() and ">>>" not in x and "<<<" not in x)
    assert line in item_text(ctx, rec, 10 ** 6), "the reviewing cog reads the compile's lines"


# --------------------------------------------------------------------- classes

def test_unknown_class_is_refused(d):
    with pytest.raises(KernelGuardError, match="unknown document class") as e:
        d.create(title="x", doc_class="secret", folder=FOLDER, by="alice")
    assert e.value.guard == "document-class"


@pytest.mark.parametrize("doc_class,state", [("evidence", "filed"), ("external", "received")])
def test_immutable_classes_refuse_a_second_version_and_checkout(d, doc_class, state):
    doc = d.create(title="fixed", doc_class=doc_class, folder=FOLDER, by="alice", content=b"signed", filename="f.txt")
    assert doc["state"] == state and doc["fields"]["revision"] == "1"
    with pytest.raises(KernelGuardError, match="cannot be changed") as e:
        d.add_version(doc["locator"], b"tampered", by="alice")
    assert e.value.guard == "immutable"
    with pytest.raises(KernelGuardError, match="never edited") as e:
        d.checkout(doc["locator"], by="alice")
    assert e.value.guard == "immutable"
    with pytest.raises(KernelGuardError) as e:
        d.checkin(doc["locator"], b"tampered", by="alice")
    assert e.value.guard == "immutable"
    assert d.content(doc["locator"])[0] == b"signed" and len(d.versions(doc["locator"])) == 1


@pytest.mark.parametrize("doc_class,state,revision", [("working", "open", "1"), ("controlled", "draft", "A")])
def test_mutable_classes_allow_versions_and_checkout(d, doc_class, state, revision):
    doc = d.create(title="editable", doc_class=doc_class, folder=FOLDER, by="alice", content=b"v1", filename="e.txt")
    assert doc["state"] == state and doc["fields"]["revision"] == revision
    d.add_version(doc["locator"], b"v2", by="alice")
    assert d.checkout(doc["locator"], by="alice")["fields"]["checked_out_by"] == "alice"
    rec = d.checkin(doc["locator"], b"v3", by="alice")
    assert rec["fields"]["checked_out_by"] is None and len(d.versions(doc["locator"])) == 3


# -------------------------------------------------------------------- check-out

def test_checkout_locks_the_document_for_other_users(d, hub):
    doc = working(d)
    d.checkout(doc["locator"], by="alice")
    assert [e["type"] for e in hub.kernel.events(aggregate=doc["locator"])][-2:] == ["document.checked_out",
                                                                                      "record.fields_set"]
    for cmd in (lambda: d.add_version(doc["locator"], b"bob's", by="bob"),
                lambda: d.checkin(doc["locator"], b"bob's", by="bob"),
                lambda: d.checkout(doc["locator"], by="bob")):
        with pytest.raises(KernelGuardError, match="checked out by alice") as e:
            cmd()
        assert e.value.guard == "checked-out"
    assert len(d.versions(doc["locator"])) == 1
    assert d.checkout(doc["locator"], by="alice")["fields"]["checked_out_by"] == "alice", "re-checkout by the holder"
    rec = d.checkin(doc["locator"], b"alice's edit", by="alice", note="done")
    assert rec["fields"]["checked_out_by"] is None
    vs = d.versions(doc["locator"])
    assert len(vs) == 2 and vs[-1]["note"] == "done" and vs[-1]["filename"] == "draft.txt"
    assert d.add_version(doc["locator"], b"now bob can", by="bob")["fields"]["minor"] == 2


# ------------------------------------------------------------------ attachments

def test_one_document_attached_to_two_records_of_different_types(d, hub):
    k = hub.kernel
    t = k.create("task", "Widgets", {"title": "t"}, by="erin")
    n = k.create("ncr", "Quality", {"title": "n", "description": "d"}, by="carol")
    doc = d.create(title="photo", doc_class="evidence", folder="Quality", by="carol", content=b"jpg", filename="p.jpg",
                   attach_to=t["locator"])
    d.attach(n["locator"], doc["locator"], by="carol")
    d.attach(n["locator"], doc["locator"], by="carol")  # attaching twice makes one link
    assert len(k.query(type_="document")) == 1
    assert [x["locator"] for x in d.documents_of(t["locator"])] == [doc["locator"]]
    assert [x["locator"] for x in d.documents_of(n["locator"])] == [doc["locator"]]
    assert d.documents_of(doc["locator"]) == []
    assert k.get(t["locator"])["links"] == [{"rel": "has-document", "target": doc["locator"],
                                             "at": k.get(t["locator"])["links"][0]["at"]}]
    assert len(k.get(n["locator"])["links"]) == 1
    assert sorted((l["direction"], l["label"], l["target"]) for l in k.links_of(doc["locator"])) == [
        ("in", "attached-to", n["locator"]), ("in", "attached-to", t["locator"])]


# -------------------------------------------------------------------- snapshots

def approved_big_bill(p2p):
    run_id = p2p.runner.submit("purchase-to-pay", {"record": dated(BIG_BILL)}, owner="alice")
    p2p.runner.decide(run_id, "bob", "approve", meaning="approved for posting", payload_digest=seen(p2p.runner, run_id))
    state = p2p.runner.load(run_id)
    assert state["status"] == "completed"
    return p2p.kernel.get(state["record"])


def test_render_snapshot_files_an_evidence_document_with_the_record(p2p):
    bill = approved_big_bill(p2p)
    doc = p2p.dms.render_snapshot(bill["locator"], by="carol")
    f = doc["fields"]
    assert f["doc_class"] == "evidence" and f["doc_type"] == "snapshot" and doc["state"] == "filed"
    assert f["title"] == f"{bill['number']} snapshot at posted" and doc["folder"] == bill["folder"]
    assert f["provenance"]["record"] == bill["locator"] and f["provenance"]["state"] == "posted"
    assert [x["locator"] for x in p2p.dms.documents_of(bill["locator"])] == [doc["locator"]]
    content, version = p2p.dms.content(doc["locator"])
    assert version["mime"] == "text/markdown" and version["filename"] == f"{bill['number']}-posted.md"
    md = content.decode("utf-8")
    assert md.startswith(f"# {bill['number']} (bill)")
    assert "State: **posted**" in md and f"Folder: {bill['folder']}" in md
    assert "- **amount**: 12000.0" in md and "- **supplier**: acme-metals" in md and "- **po_ref**: PO-1002" in md
    assert f"- **journal**: {bill['fields']['journal']}" in md
    assert f"- settled-by: rec:journal:{bill['fields']['journal']}" in md
    assert "open -> awaiting-approve-the-bill by hub" in md and "awaiting-approve-the-bill -> posted" in md
    assert "Printed copies are uncontrolled." in md
    with pytest.raises(KernelGuardError, match="cannot be changed"):
        p2p.dms.add_version(doc["locator"], b"edited", by="carol")


def test_render_snapshot_shows_the_approval_signature(p2p):
    bill = approved_big_bill(p2p)
    assert [(x["by"], x["by_type"], x["verdict"], x["meaning"]) for x in bill["decisions"]] == [
        ("bob", "person", "approve", "approved for posting")]
    decision = bill["decisions"][0]
    assert decision["signature"] and decision["step"] == "approve-the-bill" and decision["run_id"] == bill["run_id"]
    doc = p2p.dms.render_snapshot(bill["locator"], by="carol")
    md = p2p.dms.content(doc["locator"])[0].decode("utf-8")
    assert "## Signatures" in md
    from hubproto.co_record import when_utc

    # Part 11 11.50: the signer, the date and time with its zone, the verdict, the meaning category and the meaning.
    assert f"| bob | {when_utc(decision['at'])} | Approved | approval | approved for posting | approve the bill | {decision['signature'][:24]} |" in md
    assert when_utc(decision["at"]).endswith(" UTC")


def test_render_snapshot_shows_the_rejection_signature(p2p):
    run_id = p2p.runner.submit("purchase-to-pay", {"record": dated(BIG_BILL)}, owner="alice")
    p2p.runner.decide(run_id, "bob", "reject", meaning="duplicate invoice", payload_digest=seen(p2p.runner, run_id))
    bill = p2p.kernel.get(p2p.runner.load(run_id)["record"])
    assert bill["state"] == "rejected"
    decision = bill["decisions"][0]
    assert decision["by"] == "bob" and decision["verdict"] == "reject" and decision["signature"]
    doc = p2p.dms.render_snapshot(bill["locator"], by="carol")
    md = p2p.dms.content(doc["locator"])[0].decode("utf-8")
    assert "## Signatures" in md
    from hubproto.co_record import when_utc

    assert f"| bob | {when_utc(decision['at'])} | Rejected | rejection | duplicate invoice |" in md
    assert "State: **rejected**" in md


# ----------------------------------------------------------------------- intake

def test_intake_files_an_external_document_with_provenance(d, hub):
    t = hub.kernel.create("task", "Widgets", {"title": "t"}, by="erin")
    doc = d.intake(folder="Finance/Payables", by="ap-reviewer", content=b"Invoice 42", filename="inv-42.txt",
                   channel="email", sender="acme@example.com", attach_to=t["locator"])
    f = doc["fields"]
    assert f["doc_class"] == "external" and f["doc_type"] == "received" and doc["state"] == "received"
    assert f["title"] == "inv-42.txt" and doc["folder"] == "Finance/Payables"
    assert f["provenance"]["channel"] == "email" and f["provenance"]["sender"] == "acme@example.com"
    assert f["provenance"]["received_at"]
    assert d.content(doc["locator"])[0] == b"Invoice 42"
    assert [x["locator"] for x in d.documents_of(t["locator"])] == [doc["locator"]]
    with pytest.raises(KernelGuardError, match="cannot be changed"):
        d.add_version(doc["locator"], b"edited", by="ap-reviewer")
    loose = d.intake(folder="Finance/Payables", by="ap-reviewer", content=b"x", filename="loose.txt", channel="dm",
                     sender="alice", title="A loose file")
    assert loose["fields"]["title"] == "A loose file" and hub.kernel.links_of(loose["locator"]) == []


# -------------------------------------------------------------------- revisions

@pytest.mark.parametrize("current,expected", [(None, "A"), ("", "A"), ("A", "B"), ("Y", "Z"), ("Z", "AA"),
                                              ("AB", "AC"), ("1", "2"), ("9", "10")])
def test_next_revision(current, expected):
    assert next_revision(current) == expected


def test_release_revision_keeps_a_for_the_first_effectiveness_and_bumps_after(d, hub):
    doc = d.create(title="SOP-1", doc_class="controlled", folder=FOLDER, by="alice", content=b"v1", filename="sop.md",
                   fields={"review_date": "2027-01-01"})
    d.add_version(doc["locator"], b"v1.1", by="alice")
    assert d.release_revision(doc["locator"], by="hub")["fields"] | {} == {**d.release_revision(doc["locator"], by="hub")["fields"]}
    rec = hub.kernel.get(doc["locator"])
    assert rec["fields"]["revision"] == "A" and rec["fields"]["minor"] == 0, "first effectiveness keeps revision A"
    hub.kernel.transition(doc["locator"], "effective", by="hub")
    with pytest.raises(KernelGuardError, match="controlled"):
        d.add_version(doc["locator"], b"v2 draft", by="alice")  # an effective controlled document is immutable
    with pytest.raises(KernelGuardError, match="controlled"):
        d.checkout(doc["locator"], by="alice")
    rec = d.release_revision(doc["locator"], by="hub")
    assert rec["fields"]["revision"] == "B" and rec["fields"]["minor"] == 0
    assert d.release_revision(doc["locator"], by="hub")["fields"]["revision"] == "C"
