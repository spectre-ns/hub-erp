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
    # Someone else sees it read-only and cannot save.
    bob = wopi.issue_token("bob", number, can_write=True, root=shell["root"])
    info = json.loads(wopi_call(shell, f"/wopi/files/{number}", bob)[1])
    assert info["UserCanWrite"] is False and info["ReadOnly"] is True
    status, _, headers = wopi_call(shell, f"/wopi/files/{number}/contents", bob, "POST", b"x", {"X-WOPI-Override": "PUT"})
    assert status == 409 and "checked out by alice" in headers.get("X-WOPI-LockFailureReason", "")
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
