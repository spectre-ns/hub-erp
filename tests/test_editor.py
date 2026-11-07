"""The integrated editor: the hub as a WOPI host, new blank documents, and the editor page."""

import json
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pytest

from hubproto import wopi
from test_shell import shell, get, post  # noqa: F401  (the module-scoped live shell)


def wopi_call(shell, path: str, token: str, method: str = "GET", body: bytes | None = None, headers: dict | None = None):
    req = Request(shell["base"] + path + "?" + urlencode({"access_token": token}), data=body, method=method, headers=headers or {})
    try:
        with urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except HTTPError as e:
        return e.code, e.read(), dict(e.headers)


@pytest.fixture
def working_doc(shell):
    hub = shell["hub"]
    content, mime = wopi.blank_document("docx")
    return hub.dms.create(title="Test procedure", doc_class="working", folder="Finance/Payables", by="alice", content=content,
                          filename="Test procedure.docx", mime=mime, doc_type="document")


def test_tokens_are_signed_and_expire():
    t = wopi.issue_token("alice", "DOC-0001", can_write=True)
    assert wopi.read_token(t)["u"] == "alice" and wopi.read_token(t + "x") is None
    assert wopi.read_token(wopi.issue_token("alice", "DOC-0001", can_write=True, ttl=-1)) is None


def test_check_file_info_get_file_and_put_file_make_versions(shell, working_doc):
    hub = shell["hub"]
    number, loc = working_doc["number"], working_doc["locator"]
    token = wopi.issue_token("alice", number, can_write=True, root=shell["root"])
    status, body, _ = wopi_call(shell, f"/wopi/files/{number}", token)
    info = json.loads(body)
    assert status == 200 and info["BaseFileName"] == "Test procedure.docx" and info["UserCanWrite"] is True and info["Version"] == "1"
    status, body, headers = wopi_call(shell, f"/wopi/files/{number}/contents", token)
    assert status == 200 and body[:2] == b"PK" and headers["X-WOPI-ItemVersion"] == "1"
    # A save from the editor: the document is checked out to alice and gains a version.
    status, body, _ = wopi_call(shell, f"/wopi/files/{number}/contents", token, "POST", b"PK-new-bytes", {"X-WOPI-Override": "PUT"})
    assert status == 200 and json.loads(body)["LastModifiedTime"]
    rec = hub.kernel.get(loc)
    versions = hub.dms.versions(loc)
    assert rec["fields"]["checked_out_by"] == "alice" and len(versions) == 2 and versions[-1]["note"] == "saved in the editor"
    assert hub.dms.content(loc)[0] == b"PK-new-bytes"
    # Someone else joins the editing session with write access (the editor merges everyone's changes).
    bob = wopi.issue_token("bob", number, can_write=True, root=shell["root"])
    info = json.loads(wopi_call(shell, f"/wopi/files/{number}", bob)[1])
    assert info["UserCanWrite"] is True
    # Finish editing releases the check-out.
    status, url, text = post(shell, "/finish-edit", {"as": "alice", "doc": loc})
    assert status == 200 and "Finished editing" in text and hub.kernel.get(loc)["fields"]["checked_out_by"] is None


def test_bad_tokens_and_wrong_files_are_refused(shell, working_doc):
    number = working_doc["number"]
    assert wopi_call(shell, f"/wopi/files/{number}", "nonsense")[0] == 401
    other = wopi.issue_token("alice", "DOC-9999", can_write=True, root=shell["root"])
    assert wopi_call(shell, f"/wopi/files/{number}", other)[0] == 404


def test_locks_map_onto_the_checkout(shell, working_doc):
    hub = shell["hub"]
    number, loc = working_doc["number"], working_doc["locator"]
    token = wopi.issue_token("alice", number, can_write=True, root=shell["root"])
    status, _, _ = wopi_call(shell, f"/wopi/files/{number}", token, "POST", b"", {"X-WOPI-Override": "LOCK", "X-WOPI-Lock": "L1"})
    assert status == 200 and hub.kernel.get(loc)["fields"]["checked_out_by"] == "alice"
    status, _, headers = wopi_call(shell, f"/wopi/files/{number}", token, "POST", b"", {"X-WOPI-Override": "GET_LOCK"})
    assert status == 200 and headers["X-WOPI-Lock"] == "L1"
    bob = wopi.issue_token("bob", number, can_write=True, root=shell["root"])
    status, _, headers = wopi_call(shell, f"/wopi/files/{number}", bob, "POST", b"", {"X-WOPI-Override": "LOCK", "X-WOPI-Lock": "L2"})
    assert status == 409 and headers["X-WOPI-Lock"] == "L1"
    status, _, _ = wopi_call(shell, f"/wopi/files/{number}", token, "POST", b"", {"X-WOPI-Override": "UNLOCK", "X-WOPI-Lock": "L1"})
    assert status == 200
    hub.dms.undo_checkout(loc, by="alice")


def test_effective_controlled_documents_are_read_only_in_the_editor(shell):
    hub = shell["hub"]
    content, mime = wopi.blank_document("odt")
    doc = hub.dms.create(title="SOP in force", doc_class="controlled", folder="Quality/Controlled Documents", by="carol", content=content,
                         filename="sop.odt", mime=mime, doc_type="sop", fields={"revision": "A", "review_date": "2027-01-01"})
    doc = hub.kernel.transition(doc["locator"], "effective", by="carol")
    assert doc["state"] == "effective"
    token = wopi.issue_token("carol", doc["number"], can_write=True, root=shell["root"])
    info = json.loads(wopi_call(shell, f"/wopi/files/{doc['number']}", token)[1])
    assert info["UserCanWrite"] is False
    status, _, headers = wopi_call(shell, f"/wopi/files/{doc['number']}/contents", token, "POST", b"x", {"X-WOPI-Override": "PUT"})
    assert status == 409 and "new revision" in headers.get("X-WOPI-LockFailureReason", "")


def test_new_document_is_created_blank_and_opens(shell, monkeypatch):
    hub = shell["hub"]
    monkeypatch.setenv("HUBPROTO_COLLABORA_URL", "http://editor.test:9980")
    monkeypatch.setenv("HUBPROTO_WOPI_BASE", "http://hub.test:8080")
    monkeypatch.setenv("HUBPROTO_COLLABORA_URLSRC", "http://editor.test:9980/browser/dist/cool.html?")
    status, url, text = post(shell, "/new-document", {"as": "alice", "folder": "Finance/Payables", "title": "Inspection plan", "kind": "xlsx"})
    assert status == 200 and "/edit/" in url, text[:300]
    doc = next(d for d in hub.kernel.query(type_="document") if d["fields"].get("title") == "Inspection plan")
    assert doc["fields"]["doc_class"] == "working" and doc["fields"]["filename"] == "Inspection plan.xlsx"
    assert hub.dms.content(doc["locator"])[0][:2] == b"PK"
    assert '<iframe class="editor"' in text and "WOPISrc=http%3A%2F%2Fhub.test%3A8080%2Fwopi%2Ffiles%2F" + doc["number"] in text
    assert "access_token=" in text and "Finish editing" in text
    assert hub.kernel.get(doc["locator"])["fields"]["checked_out_by"] == "alice", "opening the editor checks the document out"
    # The document page offers the editor; a second person sees it read-only.
    status, page = get(shell, "/document/" + quote(doc["locator"], safe=":"), **{"as": "bob"})
    assert status == 200 and "Open in the viewer" in page
    status, edit = get(shell, "/edit/" + quote(doc["locator"], safe=":"), **{"as": "bob"})
    bar = edit.split("<main")[1][:1500]
    assert status == 200 and "read-only" in bar and ("checked out by alice" in bar or "only its author or owner" in bar), bar


def test_without_an_editor_configured_the_page_explains(shell, working_doc, monkeypatch):
    monkeypatch.delenv("HUBPROTO_COLLABORA_URL", raising=False)
    monkeypatch.delenv("HUBPROTO_WOPI_BASE", raising=False)
    status, text = get(shell, "/edit/" + quote(working_doc["locator"], safe=":"), **{"as": "alice"})
    assert status == 200 and "No online editor is configured" in text and "HUBPROTO_COLLABORA_URL" in text
    shell["hub"].dms.undo_checkout(working_doc["locator"], by="alice")


def test_new_document_needs_a_title_and_a_known_kind(shell):
    status, url, text = post(shell, "/new-document", {"as": "alice", "folder": "Finance/Payables", "title": "", "kind": "docx"})
    assert status == 400 and "Give the document a title" in text
    status, url, text = post(shell, "/new-document", {"as": "alice", "folder": "Finance/Payables", "title": "x", "kind": "exe"})
    assert status == 400 and "cannot create a blank .exe" in text


def test_an_uploaded_office_file_is_a_working_document_unless_kept_as_received(shell):
    hub = shell["hub"]
    content, _ = wopi.blank_document("docx")
    status, url, text = post(shell, "/upload", {"as": "alice", "folder": "Finance/Payables", "title": "Plan"}, files=[("file", "plan.docx", content)])
    assert status == 200 and "working document you can edit online" in text
    doc = next(d for d in hub.kernel.query(type_="document") if d["fields"].get("title") == "Plan")
    assert doc["fields"]["doc_class"] == "working" and wopi.WopiHost(hub).can_write(doc, "alice")[0]
    status, url, text = post(shell, "/upload", {"as": "alice", "folder": "Finance/Payables", "title": "Kept", "keep": "1"}, files=[("file", "kept.docx", content)])
    kept = next(d for d in hub.kernel.query(type_="document") if d["fields"].get("title") == "Kept")
    assert kept["fields"]["doc_class"] == "external" and not wopi.WopiHost(hub).can_write(kept, "alice")[0]


def test_wopi_times_are_in_the_shape_clients_parse():
    assert wopi.wopi_time("2026-11-06T17:20:35+00:00") == "2026-11-06T17:20:35.000000Z"
    assert wopi.wopi_time("2026-11-06T17:20:35.5Z") == "2026-11-06T17:20:35.5Z"


def test_a_received_document_gets_a_working_copy_that_opens_in_the_editor(shell, monkeypatch):
    hub = shell["hub"]
    monkeypatch.setenv("HUBPROTO_COLLABORA_URL", "http://editor.test:9980")
    monkeypatch.setenv("HUBPROTO_WOPI_BASE", "http://hub.test:8080")
    monkeypatch.setenv("HUBPROTO_COLLABORA_URLSRC", "http://editor.test:9980/browser/dist/cool.html?")
    content, mime = wopi.blank_document("docx")
    received = hub.dms.intake(folder="Finance/Payables", by="alice", content=content, filename="spec.docx", channel="email", sender="vendor")
    assert received["fields"]["doc_class"] == "external"
    status, page = get(shell, "/document/" + quote(received["locator"], safe=":"), **{"as": "alice"})
    assert status == 200 and "Make a working copy" in page and "Open in the viewer" in page
    status, url, text = post(shell, "/working-copy", {"as": "alice", "doc": received["locator"]})
    assert status == 200 and "/edit/" in url, text[:300]
    copy = next(d for d in hub.kernel.query(type_="document") if d["fields"].get("title") == "spec.docx (working copy)")
    assert copy["fields"]["doc_class"] == "working" and hub.dms.content(copy["locator"])[0] == content
    assert any(l["rel"] == "references" and l["target"] == received["locator"] for l in copy["links"])
    assert hub.kernel.get(copy["locator"])["fields"]["checked_out_by"] == "alice"


def docx_with_comment(text: str, author: str = "carol") -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/_rels/document.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/></Relationships>')
        z.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Inspection plan for the sensor board.</w:t></w:r><w:commentRangeStart w:id="0"/><w:r><w:t>Torque 2 Nm.</w:t></w:r><w:commentRangeEnd w:id="0"/></w:p><w:sectPr/></w:body></w:document>')
        z.writestr("word/comments.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:comment w:id="0" w:author="{author}" w:date="2026-11-06T18:43:00Z"><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:comment></w:comments>')
    return buf.getvalue()


def test_a_comment_in_the_document_that_mentions_a_cog_reaches_the_chatter_and_is_answered(shell):
    hub = shell["hub"]
    content, mime = wopi.blank_document("docx")
    doc = hub.dms.create(title="Inspection plan", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                         filename="Inspection plan.docx", mime=mime, doc_type="document")
    number, loc = doc["number"], doc["locator"]
    token = wopi.issue_token("carol", number, can_write=True, root=shell["root"])
    saved = docx_with_comment("@quality-assistant can you review this document?")
    status, body, _ = wopi_call(shell, f"/wopi/files/{number}/contents", token, "POST", saved, {"X-WOPI-Override": "PUT"})
    assert status == 200
    rec = hub.kernel.get(loc)
    assert "Inspection plan for the sensor board." in rec["fields"]["text"], "reviews can read the document"
    assert rec["fields"]["office_comments"][0]["text"].startswith("@quality-assistant") and rec["fields"]["office_comments"][0]["author"] == "carol"
    chatter = hub.inbox.on_record(loc)
    kinds = [(m["kind"], m["from"]) for m in chatter]
    assert ("message", "carol") in kinds and ("reply", "quality-assistant") in kinds
    reply = next(m for m in chatter if m["kind"] == "reply")
    assert f"Review of {number} by quality-assistant" in reply["body"]
    # Saving again with the same comment does not ask twice.
    wopi_call(shell, f"/wopi/files/{number}/contents", token, "POST", saved, {"X-WOPI-Override": "PUT"})
    assert len([m for m in hub.inbox.on_record(loc) if m["kind"] == "message"]) == 1
    hub.dms.undo_checkout(loc, by="carol")


def test_the_editor_page_offers_members_and_roles_as_mentions(shell, working_doc, monkeypatch):
    monkeypatch.setenv("HUBPROTO_COLLABORA_URL", "http://editor.test:9980")
    monkeypatch.setenv("HUBPROTO_WOPI_BASE", "http://hub.test:8080")
    monkeypatch.setenv("HUBPROTO_COLLABORA_URLSRC", "http://editor.test:9980/browser/dist/cool.html?")
    status, text = get(shell, "/edit/" + quote(working_doc["locator"], safe=":"), **{"as": "alice"})
    assert status == 200 and "Action_Mention" in text and "UI_Mention" in text and "Host_PostmessageReady" in text
    assert '"username": "quality-assistant"' in text and '"username": "design-review-committee"' in text and '"username": "bob"' in text
    assert 'var origin="http://editor.test:9980"' in text
    shell["hub"].dms.undo_checkout(working_doc["locator"], by="alice")


def test_two_people_edit_the_same_document_together_and_the_last_out_releases_it(shell):
    hub = shell["hub"]
    content, mime = wopi.blank_document("docx")
    doc = hub.dms.create(title="Shared plan", doc_class="working", folder="Quality/Controlled Documents", by="alice", content=content,
                         filename="shared.docx", mime=mime, doc_type="document")
    number, loc = doc["number"], doc["locator"]
    hub.dms.checkout(loc, by="alice", via="editor")  # alice opens the editor
    alice = wopi.issue_token("alice", number, can_write=True, root=shell["root"])
    carol = wopi.issue_token("carol", number, can_write=True, root=shell["root"])
    assert json.loads(wopi_call(shell, f"/wopi/files/{number}", carol)[1])["UserCanWrite"] is True, "a second editor joins with write access"
    status, _, _ = wopi_call(shell, f"/wopi/files/{number}/contents", carol, "POST", b"PK-carol", {"X-WOPI-Override": "PUT"})
    assert status == 200
    rec = hub.kernel.get(loc)
    assert rec["fields"]["checked_out_by"] == "alice" and set(rec["fields"]["editors"]) == {"alice", "carol"}
    assert hub.dms.versions(loc)[-1]["by"] == "carol"
    # A manual check-in by someone outside the session is still refused.
    with pytest.raises(Exception, match="checked out by alice"):
        hub.dms.checkin(loc, b"x", by="dave")
    # The document page tells carol it is being edited and offers joining; alice finishing leaves bob holding it.
    status, page = get(shell, "/document/" + quote(loc, safe=":"), **{"as": "carol"})
    assert status == 200 and "Join the editing" in page
    post(shell, "/finish-edit", {"as": "alice", "doc": loc})
    rec = hub.kernel.get(loc)
    assert rec["fields"]["checked_out_by"] == "carol" and rec["fields"]["editors"] == ["carol"]
    post(shell, "/finish-edit", {"as": "carol", "doc": loc})
    rec = hub.kernel.get(loc)
    assert rec["fields"]["checked_out_by"] is None and rec["fields"]["editors"] == []



def test_a_cogs_answer_is_written_into_the_document_as_a_reply_comment(shell):
    hub = shell["hub"]
    content, mime = wopi.blank_document("docx")
    doc = hub.dms.create(title="Torque spec", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                         filename="torque.docx", mime=mime, doc_type="document")
    number, loc = doc["number"], doc["locator"]
    token = wopi.issue_token("carol", number, can_write=True, root=shell["root"])
    saved = docx_with_comment("@quality-assistant please review this document.")
    assert wopi_call(shell, f"/wopi/files/{number}/contents", token, "POST", saved, {"X-WOPI-Override": "PUT"})[0] == 200
    status, text = get(shell, "/editor-replies/" + quote(loc, safe=":"), **{"as": "carol"})
    assert status == 200 and json.loads(text)["pending"] == 1 and json.loads(text)["by"] == ["quality-assistant"]
    status, url, text = post(shell, "/apply-replies", {"as": "carol", "doc": loc})
    assert status == 200 and json.loads(text)["applied"] == 1, text[:300]
    from hubproto import office

    content, version = hub.dms.content(loc)
    comments = office.extract_comments(content, "docx")
    assert [c["author"] for c in comments] == ["carol", "quality-assistant"] and f"Review of {number}" in comments[1]["text"]
    assert version["by"] == "hub" and "replies from quality-assistant" in version["note"]
    assert json.loads(get(shell, "/editor-replies/" + quote(loc, safe=":"), **{"as": "carol"})[1])["pending"] == 0
    # A session that has not seen the new version is told to reload before it may overwrite it.
    status, _, _ = wopi_call(shell, f"/wopi/files/{number}/contents", token, "POST", saved,
                             {"X-WOPI-Override": "PUT", "X-COOL-WOPI-Timestamp": "2026-01-01T00:00:00.000000Z"})
    assert status == 409
    stamp = wopi.wopi_time(version["at"])
    assert wopi_call(shell, f"/wopi/files/{number}/contents", token, "POST", content, {"X-WOPI-Override": "PUT", "X-COOL-WOPI-Timestamp": stamp})[0] == 200
    assert len([m for m in hub.inbox.on_record(loc) if m["kind"] == "message"]) == 1, "the written reply is not posted to the chatter again"
    hub.dms.undo_checkout(loc, by="carol")


def test_a_comment_asking_the_cog_to_edit_makes_it_write_into_the_document(shell):
    hub = shell["hub"]
    from hubproto import office

    content, mime = wopi.blank_document("docx")
    doc = hub.dms.create(title="Test procedure TP-7", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                         filename="tp7.docx", mime=mime, doc_type="document")
    number, loc = doc["number"], doc["locator"]
    hub.dms.checkout(loc, by="carol", via="editor")
    token = wopi.issue_token("carol", number, can_write=True, root=shell["root"])
    saved = docx_with_comment("@quality-assistant please edit this document to include a quality header.")
    assert wopi_call(shell, f"/wopi/files/{number}/contents", token, "POST", saved, {"X-WOPI-Override": "PUT"})[0] == 200
    # The cog drafted the content; it waits as a pending action for the live seat, or for the hub to write it.
    j = json.loads(get(shell, "/editor-replies/" + quote(loc, safe=":"), **{"as": "carol"})[1])
    assert j["pending"] == 1 and j["replies"][0]["from"] == "quality-assistant" and j["replies"][0]["edit"]["paragraphs"][0].startswith("# ")
    assert j["replies"][0]["comment_text"].startswith("@quality-assistant") and j["replies"][0]["comment_author"] == "carol"
    # A seat for the cog is granted to someone in the session, for a cog with something pending, and nobody else.
    status, text = get(shell, "/cog-seat/" + quote(loc, safe=":"), **{"as": "carol", "cog": "quality-assistant"})
    assert status == 200 and "access_token=" in json.loads(text)["frame"] and "quality-assistant" in hub.kernel.get(loc)["fields"]["editors"]
    assert get(shell, "/cog-seat/" + quote(loc, safe=":"), **{"as": "bob", "cog": "quality-assistant"})[0] == 403
    assert get(shell, "/cog-seat/" + quote(loc, safe=":"), **{"as": "carol", "cog": "ap-reviewer"})[0] == 403
    # No editor open: the hub writes the edit and the explanation into the file.
    status, url, text = post(shell, "/apply-replies", {"as": "carol", "doc": loc})
    assert json.loads(text)["applied"] == 1
    content, version = hub.dms.content(loc)
    assert version["by"] == "hub" and "edits and replies from quality-assistant" in version["note"]
    text = office.extract_text(content, "docx")
    assert text.startswith("Test procedure TP-7") and "Inspection plan for the sensor board." in text, "the header was written before the existing text"
    comments = office.extract_comments(content, "docx")
    assert [c["author"] for c in comments] == ["carol", "quality-assistant"] and "Changes: insert 3 line(s) at the start" in comments[1]["text"]
    assert hub.kernel.get(loc)["fields"]["checked_out_by"] == "carol", "the editing session survives the cog's edit"
    j = json.loads(get(shell, "/editor-replies/" + quote(loc, safe=":"), **{"as": "carol"})[1])
    assert j["pending"] == 0 and j["version"] == version["version"] and j["machine"] is True
    hub.dms.undo_checkout(loc, by="carol")


def test_a_live_seat_marks_an_answer_as_applied_so_the_hub_does_not_write_it_twice(shell):
    hub = shell["hub"]
    content, mime = wopi.blank_document("docx")
    doc = hub.dms.create(title="Live plan", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                         filename="live.docx", mime=mime, doc_type="document")
    number, loc = doc["number"], doc["locator"]
    hub.dms.checkout(loc, by="carol", via="editor")
    token = wopi.issue_token("carol", number, can_write=True, root=shell["root"])
    wopi_call(shell, f"/wopi/files/{number}/contents", token, "POST", docx_with_comment("@quality-assistant please review this document."), {"X-WOPI-Override": "PUT"})
    j = json.loads(get(shell, "/editor-replies/" + quote(loc, safe=":"), **{"as": "carol"})[1])
    assert j["pending"] == 1
    status, url, text = post(shell, "/mark-applied", {"as": "carol", "doc": loc, "ids": j["replies"][0]["id"]})
    assert json.loads(text)["applied"] == 1
    assert json.loads(get(shell, "/editor-replies/" + quote(loc, safe=":"), **{"as": "carol"})[1])["pending"] == 0
    assert json.loads(post(shell, "/apply-replies", {"as": "carol", "doc": loc})[2])["applied"] == 0
    hub.dms.undo_checkout(loc, by="carol")


def docx_thread(root_author: str, root_text: str, replies: list[tuple[str, str]]) -> bytes:
    """A docx whose comment thread has a root and threaded replies (Word's commentsExtended threading)."""
    import io
    import zipfile

    ids = list(range(len(replies) + 1))
    paras = [f"{0x1000 + i:08X}" for i in ids]
    comments = f'<w:comment w:id="0" w:author="{root_author}" w:date="2026-11-06T19:00:00Z"><w:p w14:paraId="{paras[0]}"><w:r><w:t>{root_text}</w:t></w:r></w:p></w:comment>'
    exts = ""
    for i, (author, text) in enumerate(replies, start=1):
        comments += f'<w:comment w:id="{i}" w:author="{author}" w:date="2026-11-06T19:0{i}:00Z"><w:p w14:paraId="{paras[i]}"><w:r><w:t>{text}</w:t></w:r></w:p></w:comment>'
        exts += f'<w15:commentEx w15:paraId="{paras[i]}" w15:paraIdParent="{paras[i - 1]}" w15:done="0"/>'
    refs = "".join(f'<w:r><w:commentReference w:id="{i}"/></w:r>' for i in ids)
    starts = "".join(f'<w:commentRangeStart w:id="{i}"/>' for i in ids)
    ends = "".join(f'<w:commentRangeEnd w:id="{i}"/>' for i in ids)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/><Override PartName="/word/commentsExtended.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/_rels/document.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/><Relationship Id="rId2" Type="http://schemas.microsoft.com/office/2011/relationships/commentsExtended" Target="commentsExtended.xml"/></Relationships>')
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Inspection plan. </w:t></w:r>{starts}<w:r><w:t>Torque 2 Nm.</w:t></w:r>{ends}{refs}</w:p><w:sectPr/></w:body></w:document>')
        z.writestr("word/comments.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml">{comments}</w:comments>')
        z.writestr("word/commentsExtended.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w15:commentsEx xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">{exts}</w15:commentsEx>')
    return buf.getvalue()


def test_a_reply_in_a_cogs_thread_triggers_the_cog_without_a_mention(shell):
    hub = shell["hub"]
    from hubproto import office

    content, mime = wopi.blank_document("docx")
    doc = hub.dms.create(title="Thread test", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                         filename="thread.docx", mime=mime, doc_type="document")
    number, loc = doc["number"], doc["locator"]
    hub.dms.checkout(loc, by="carol", via="editor")
    token = wopi.issue_token("carol", number, can_write=True, root=shell["root"])
    # Save 1: carol asks the cog; it answers (pending for the seat).
    first = docx_thread("carol", "@quality-assistant please review this document.", [])
    assert wopi_call(shell, f"/wopi/files/{number}/contents", token, "POST", first, {"X-WOPI-Override": "PUT"})[0] == 200
    pending = wopi.WopiHost(hub).pending_replies(hub.kernel.get(loc))
    assert len(pending) == 1
    post(shell, "/mark-applied", {"as": "carol", "doc": loc, "ids": pending[0]["id"]})
    # Save 2: the editor saved the thread with the cog's reply, and carol replied in it without any @mention.
    second = docx_thread("carol", "@quality-assistant please review this document.",
                         [("quality-assistant", "Review: pass. Torque is within the procedure."), ("carol", "Which procedure gives the torque limit?")])
    assert wopi_call(shell, f"/wopi/files/{number}/contents", token, "POST", second, {"X-WOPI-Override": "PUT"})[0] == 200
    comments = office.extract_comments(second, "docx")
    assert [c["parent"] for c in comments] == [None, "0", "1"], "the thread is read from Word's threading data"
    chatter = hub.inbox.on_record(loc)
    asked = [m for m in chatter if m["kind"] == "message"]
    assert len(asked) == 2 and asked[-1]["comment_id"] == "2" and "reply in a thread" in asked[-1]["subject"]
    answers = [m for m in chatter if m["kind"] == "reply" and m.get("in_reply_to") == asked[-1]["id"]]
    assert answers and answers[0]["from"] == "quality-assistant" and "Review of" not in answers[0]["body"], "a question in the thread gets an answer, not another full review"
    pending = wopi.WopiHost(hub).pending_replies(hub.kernel.get(loc))
    assert [m["comment_id"] for m in pending] == ["2"], "the answer threads under carol's reply"
    # Save 3: the same thread saved again does not trigger anything; the cog's own words never do.
    assert wopi_call(shell, f"/wopi/files/{number}/contents", token, "POST", second, {"X-WOPI-Override": "PUT"})[0] == 200
    assert len([m for m in hub.inbox.on_record(loc) if m["kind"] == "message"]) == 2
    hub.dms.undo_checkout(loc, by="carol")


def test_the_hub_writes_formatted_content_into_a_docx():
    from hubproto import office

    content, _ = wopi.blank_document("docx")
    new = office.insert_paragraphs(content, "docx", ["# Quality header", "Prepared under **ISO 9001** procedures.", "- first check", "- second check",
                                                     "| Item | Limit |", "|---|---|", "| Torque | 2 Nm |"], "start")
    import zipfile, io

    xml = zipfile.ZipFile(io.BytesIO(new)).read("word/document.xml").decode()
    assert '<w:pStyle w:val="Heading1"/>' in xml and "<w:b/>" in xml and "ISO 9001" in xml
    assert xml.count("• ") == 2 and "<w:tbl>" in xml and xml.count("<w:tr>") == 2 and "Torque" in xml
    assert office.extract_text(new, "docx").startswith("Quality header")


def test_the_hub_writes_rows_into_xlsx_and_ods_sheets():
    from hubproto import office

    for ext in ("xlsx", "ods"):
        content, _ = wopi.blank_document(ext)
        new = office.insert_paragraphs(content, ext, ["| Item | Limit |", "|---|---|", "| Torque | 2 Nm |", "Note: checked"], "start")
        assert new is not None, ext
        text = office.extract_text(new, ext)
        assert "Item | Limit" in text and "Torque | 2 Nm" in text and "Note: checked" in text, (ext, text)
        assert office.sheet_rows_used(new, ext) == 3, (ext, office.sheet_rows_used(new, ext))
        again = office.insert_paragraphs(new, ext, ["| Mass | 12 g |"], "end")
        assert office.sheet_rows_used(again, ext) == 4 and "Mass | 12 g" in office.extract_text(again, ext)


def test_an_edit_request_on_a_spreadsheet_yields_rows_for_the_seat(shell):
    hub = shell["hub"]
    content, mime = wopi.blank_document("xlsx")
    doc = hub.dms.create(title="Inspection log", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                         filename="log.xlsx", mime=mime, doc_type="document")
    number, loc = doc["number"], doc["locator"]
    hub.dms.checkout(loc, by="carol", via="editor")
    reply = hub.send("carol", "carol", "Comment", "@quality-assistant please add a checklist with columns for item, requirement and result", record=loc)
    pending = wopi.WopiHost(hub).pending_replies(hub.kernel.get(loc))
    assert pending and pending[0]["edit"]["kind"] == "sheet" and pending[0]["edit"]["start_row"] == 1
    [op] = pending[0]["edit"]["ops"]
    rows = op["rows"]
    assert op["op"] == "append_rows"
    assert rows and all(isinstance(r, list) for r in rows) and len(rows[0]) >= 2
    status, url, text = post(shell, "/apply-replies", {"as": "carol", "doc": loc})
    assert json.loads(text)["applied"] == 1
    from hubproto import office

    content, version = hub.dms.content(loc)
    assert office.sheet_rows_used(content, "xlsx") == len(rows) and version["by"] == "hub"
    hub.dms.undo_checkout(loc, by="carol")


def test_sheets_store_numbers_and_formulas_and_the_prompt_states_the_writing_interface(released):
    from hubproto import office
    from hubproto.cogs import RunContext, cog_context
    import zipfile, io

    content, _ = wopi.blank_document("xlsx")
    new = office.insert_rows(content, "xlsx", [["Item", "Qty", "Unit", "Total"], ["Washer", "12", "0.5", "=B2*C2"], ["Total", "", "", "=SUM(D2:D2)"]])
    xml = zipfile.ZipFile(io.BytesIO(new)).read("xl/worksheets/sheet1.xml").decode()
    assert '<c r="B2"><v>12</v></c>' in xml and "<f>B2*C2</f>" in xml and "<f>SUM(D2:D2)</f>" in xml and 'r="A1" t="inlineStr"' in xml
    ods, _ = wopi.blank_document("ods")
    new2 = office.insert_rows(ods, "ods", [["Qty", "2.5", "=A1*2"]])
    cxml = zipfile.ZipFile(io.BytesIO(new2)).read("content.xml").decode()
    assert 'office:value-type="float" office:value="2.5"' in cxml and 'table:formula="of:=A1*2"' in cxml
    # The system prompt states the interface for the target format.
    bundle = released.releases.bundle(released.releases.current()["digest"])
    doc = released.dms.create(title="Log", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                              filename="log.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", doc_type="document")
    ctx = RunContext(run_id="t", release="", bundle=bundle, owner="quality-assistant", kernel=released.kernel, frames=lambda: released.frames,
                     extras={"cog_profile": bundle["cogs"]["quality-assistant"]})
    rec = released.kernel.get(doc["locator"])
    assert "OUTPUT FORMAT for a change to this spreadsheet" in cog_context(ctx, rec, task="edit") and "@@ delete rows" in cog_context(ctx, rec, task="edit")
    rec["fields"]["filename"] = "wi.docx"
    text_iface = cog_context(ctx, rec, task="edit")
    assert "OUTPUT FORMAT for a change to this document" in text_iface and "page break" in text_iface and "@@ delete section" in text_iface
    assert "Verdict: pass" in cog_context(ctx, rec, task="review") and "comment thread" in cog_context(ctx, rec, task="answer")


def test_a_chatter_message_to_a_cog_about_a_document_is_handled_like_a_mention(shell):
    hub = shell["hub"]
    content, mime = wopi.blank_document("docx")
    doc = hub.dms.create(title="Header test", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                         filename="header.docx", mime=mime, doc_type="document")
    loc = doc["locator"]
    reply = hub.send("carol", "quality-assistant", "Revision", "Revise this document with the prescribed document header.", record=loc)
    assert reply["kind"] == "reply" and reply["from"] == "quality-assistant" and reply["record"] == loc
    # Nobody has the editor open, so the revision was written into the file at once (replacing the body).
    assert wopi.WopiHost(hub).pending_replies(hub.kernel.get(loc)) == []
    from hubproto import office

    content, version = hub.dms.content(loc)
    assert version["by"] == "hub" and "quality-assistant" in version["note"]
    text = office.extract_text(content, "docx")
    assert text and "Header test" in text


def test_keyword_intents_understand_negation_and_formatting_requests():
    from hubproto.mentions import intent_by_keywords

    assert intent_by_keywords("@quality-assistant I didn't ask for a review") == "message"
    assert intent_by_keywords("Please make it a table") == "edit"
    assert intent_by_keywords("Format the content of the document. You now have a duplicate text as well.") == "edit"
    assert intent_by_keywords("Please move this document to approval") == "approve"
    assert intent_by_keywords("please review this drawing") == "review"


def test_cogs_never_hold_the_editing_lock(shell):
    hub = shell["hub"]
    content, mime = wopi.blank_document("docx")
    doc = hub.dms.create(title="Lock test", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                         filename="lock.docx", mime=mime, doc_type="document")
    loc = doc["locator"]
    hub.dms.checkout(loc, by="carol", via="editor")
    hub.dms.join_editing(loc, by="quality-assistant")
    hub.dms.leave_editing(loc, by="carol")
    f = hub.kernel.get(loc)["fields"]
    assert f["checked_out_by"] is None and f["editors"] == [], "the last person out releases the session even if a cog's seat is still open"


def test_hot_keys_need_the_leader_and_a_cogs_answer_lands_in_the_file_when_no_editor_is_open(shell):
    hub = shell["hub"]
    from hubproto import office

    content, mime = wopi.blank_document("docx")
    doc = hub.dms.create(title="Quiet doc", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                         filename="quiet.docx", mime=mime, doc_type="document")
    loc = doc["locator"]
    status, text = get(shell, "/document/" + quote(loc, safe=":"), **{"as": "carol"})
    assert "armLeader" in text and "k==='o'" in text, "a bare key never fires an Op; 'o' then the key does"
    # Nobody has the editor open: the review asked for in the chatter is written into the document at once.
    reply = hub.send("carol", "quality-assistant", "Review", "@quality-assistant please review this document", record=loc)
    assert "Review of" in reply["body"], "'please review' is a review, whatever the model would say"
    assert wopi.WopiHost(hub).pending_replies(hub.kernel.get(loc)) == []
    # A new markdown document is the hub's own: it lands on the document page, not in the office editor.
    status, url, text = post(shell, "/new-document", {"as": "carol", "folder": "Quality/Controlled Documents", "title": "Notes", "kind": "md"})
    assert status == 200 and "/document/" in url and "Markdown is edited by the hub" in text
    md = next(d for d in hub.kernel.query(type_="document") if d["fields"].get("title") == "Notes")
    hub.send("carol", "quality-assistant", "Edit", "@quality-assistant please add a short quality header", record=md["locator"])
    assert hub.dms.content(md["locator"])[0].decode().startswith("# "), "a cog writes markdown as text"


def test_every_version_of_an_office_file_updates_the_text_the_cogs_read(shell):
    hub = shell["hub"]
    from hubproto import office

    content, mime = wopi.blank_document("docx")
    content = office.insert_paragraphs(content, "docx", ["# Torque spec", "Torque the M4 screws to 2 Nm."], "start")
    doc = hub.dms.create(title="Torque spec 2", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                         filename="torque2.docx", mime=mime, doc_type="document")
    assert "Torque the M4 screws to 2 Nm." in hub.kernel.get(doc["locator"])["fields"]["text"], "created through the DMS, not the editor"
    newer = office.insert_paragraphs(content, "docx", ["Record the value on the traveller."], "end")
    hub.dms.checkout(doc["locator"], by="carol")
    hub.dms.checkin(doc["locator"], newer, by="carol", filename="torque2.docx", mime=mime)
    assert "traveller" in hub.kernel.get(doc["locator"])["fields"]["text"], "a manual check-in too"


def test_a_document_goes_back_to_the_start(shell):
    hub = shell["hub"]
    content, mime = wopi.blank_document("docx")
    doc = hub.dms.create(title="WI to restart", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=content,
                         filename="restart.docx", mime=mime, doc_type="wi")
    loc = doc["locator"]
    hub.runner.submit("document-control", {"record": {}}, owner="carol", record=loc)
    run = hub.runner.run_for_record(loc)
    assert run["status"] == "waiting"
    waiting_for = run["pending"]["assignees"]
    status, page = get(shell, "/document/" + quote(loc, safe=":"), **{"as": "carol"})
    assert "Back to the start" in page
    status, page = get(shell, "/document/" + quote(loc, safe=":"), **{"as": "dave"})
    assert "Back to the start" not in page, "neither its owner nor an approver"
    status, url, text = post(shell, "/restart", {"as": "carol", "record": loc, "reason": "wrong template; starting again"})
    assert status == 200 and "is back at the start" in text
    rec = hub.kernel.get(loc)
    assert rec["state"] == "draft" and rec["assignees"] == [] and hub.runner.load(run["id"])["status"] == "cancelled"
    assert len(hub.dms.versions(loc)) >= 1, "versions stay"
    told = [m for p_ in waiting_for if p_ != "carol" for m in hub.inbox.for_principal(p_) if m.get("kind") == "returned" and "back at the start" in m["subject"]]
    assert told, "whoever it waited for is told"
    status, url, text = post(shell, "/restart", {"as": "carol", "record": loc, "reason": ""})
    assert status == 400
    # It can go round again.
    hub.runner.submit("document-control", {"record": {}}, owner="carol", record=loc)
    assert hub.runner.run_for_record(loc)["status"] == "waiting"
