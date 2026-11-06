"""Workspace shell suite: every page answers, the main flows work over HTTP (start with files, decide, resubmit, retry,
cancel), request handling stays responsive and bounded, pages are permission-trimmed, and the records CLI runs in-process."""

import http.client
import io
import shutil
import socket
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pytest

from conftest import BIG_BILL, MID_BILL, ROOT, SMALL_BILL, dated, release_all
from hubproto import cli
from hubproto.app import Hub
from hubproto.shell import free_port, serve_in_thread


@pytest.fixture(scope="module")
def shell(tmp_path_factory):
    root = tmp_path_factory.mktemp("hub")
    shutil.copytree(ROOT / "frames", root / "frames")
    hub = Hub(root)
    hub.kernel.seed()
    release_all(hub)
    port = free_port()
    server = serve_in_thread(hub, "127.0.0.1", port)
    yield {"hub": hub, "root": root, "base": f"http://127.0.0.1:{port}", "port": port, "server": server}
    server.shutdown()
    server.server_close()


def get(shell, path: str, **params) -> tuple[int, str]:
    url = shell["base"] + path + ("&" if "?" in path else "?") + urlencode(params) if params else shell["base"] + path
    try:
        with urlopen(url, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except HTTPError as e:
        return e.code, e.read().decode("utf-8")


def post(shell, path: str, fields: dict, files: list[tuple[str, str, bytes]] | None = None) -> tuple[int, str, str]:
    """POST a form (urlencoded, or multipart when files are given); follows the redirect. Returns (status, final url, body)."""
    if files:
        boundary = "----hubproto" + uuid.uuid4().hex
        body = b""
        for k, v in fields.items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
        for name, filename, content in files:
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8") + content + b"\r\n"
        body += f"--{boundary}--\r\n".encode("utf-8")
        ctype = f"multipart/form-data; boundary={boundary}"
    else:
        body = urlencode(fields).encode("utf-8")
        ctype = "application/x-www-form-urlencoded"
    req = Request(shell["base"] + path, data=body, headers={"Content-Type": ctype}, method="POST")
    try:
        with urlopen(req, timeout=10) as r:
            return r.status, r.url, r.read().decode("utf-8")
    except HTTPError as e:
        return e.code, e.url, e.read().decode("utf-8")


# ------------------------------------------------------------------ pages
def test_healthz(shell):
    status, text = get(shell, "/healthz")
    assert status == 200 and text == "ok"


@pytest.mark.parametrize("path,needle", [
    ("/", "Processes you can start"),
    ("/files?folder=Finance/Payables", "Upload a file"),
    ("/boards?type=bill", "awaiting-approve-the-bill"),
    ("/inbox", "Send a message"),
    ("/runs", "Runs"),
    ("/releases", "Routing in effect"),
    ("/frames", "finance-policy"),
    ("/frame/finance-policy", 'id=\'L1\''),
    ("/why?action=create&object=bill&folder=Finance/Payables", "allowed by finance-policy line"),
    ("/admin", "record-filer"),
    ("/conformance", "Conformance"),
    ("/files", "Finance"),
    ("/boards", "Boards"),
])
def test_pages_answer(shell, path, needle):
    status, text = get(shell, path, **{"as": "alice"})
    assert status == 200, text[:500]
    assert needle in text
    assert "as=alice" in text, "links keep the acting user"


def test_home_shows_roles_and_release(shell):
    status, text = get(shell, "/", **{"as": "bob"})
    assert status == 200
    assert "controller" in text and "policy-owner" in text
    assert "Policy release in effect" in text


def test_denied_folder_shows_why(shell):
    status, text = get(shell, "/files?folder=Finance/Payables", **{"as": "carol"})
    assert status == 403
    assert "no rule in the release" in text or "denied by" in text


def test_why_links_to_the_frame_line(shell):
    status, text = get(shell, "/why?action=create&object=bill&folder=Finance/Payables", **{"as": "carol"})
    assert status == 200 and "denied" in text and "Quality managers may not create bills" in text
    assert "/frame/finance-policy?as=carol#L" in text


def test_unknown_page_is_a_readable_404(shell):
    status, text = get(shell, "/nope", **{"as": "alice"})
    assert status == 404 and "No such page" in text


def test_conformance_without_profile_says_so(shell):
    status, text = get(shell, "/conformance", **{"as": "alice"})
    assert status == 200
    assert "not installed" in text or "<table>" in text


# ------------------------------------------------------------------ flows
def test_start_decide_upload_attach_flow(shell):
    hub = shell["hub"]
    bill = dated(BIG_BILL)
    status, url, text = post(shell, "/start", {"as": "alice", "process": "purchase-to-pay", "supplier": bill["supplier"],
                                               "amount": str(bill["amount"]), "currency": bill["currency"], "date": bill["date"],
                                               "po_ref": bill["po_ref"], "description": bill["description"], "json": ""})
    assert status == 200 and "/run/" in url, text[:500]
    run_id = url.split("/run/")[1].split("?")[0]
    state = hub.runner.load(run_id)
    assert state["status"] == "waiting" and state["pending"]["assignees"] == ["bob"]
    locator = state["record"]
    rec = hub.kernel.get(locator)
    assert rec["fields"]["amount"] == 12000.0 and rec["fields"]["po_ref"] == "PO-1002"

    # The record page shows the workflow states and the pending transition.
    status, text = get(shell, "/record/" + quote(locator, safe=":"), **{"as": "alice"})
    assert status == 200
    for s in ("open", "awaiting-approve-the-bill", "posted", "rejected"):
        assert s in text
    assert "stateDiagram-v2" in text and "pending" in text
    status, text = get(shell, "/record/" + rec["number"], **{"as": "bob"})
    assert status == 200 and "may bob approve bill in Finance/Payables" in text and "allowed" in text

    # The inbox shows the approval with the routing sentence; bob decides.
    status, text = get(shell, "/inbox", **{"as": "bob"})
    assert status == 200 and "over 10,000 USD" in text and state["pending"]["payload_digest"] in text
    status, url, text = post(shell, "/decide", {"as": "dave", "run": run_id, "verdict": "approve", "meaning": "x",
                                                "digest": state["pending"]["payload_digest"]})
    assert status == 400 and "not an assignee" in text
    status, url, text = post(shell, "/decide", {"as": "bob", "run": run_id, "verdict": "approve", "meaning": "approved for posting",
                                                "digest": state["pending"]["payload_digest"]})
    assert status == 200, text[:500]
    assert hub.runner.load(run_id)["status"] == "completed"
    status, text = get(shell, "/record/" + quote(locator, safe=":"), **{"as": "alice"})
    assert status == 200 and ">posted<" in text and "approved for posting" in text

    # Upload files an external document into the folder; it appears on /files.
    content = b"Invoice 4711\nAcme Metals\nTotal 12,000.00 USD\n"
    status, url, text = post(shell, "/upload", {"as": "alice", "folder": "Finance/Payables", "title": "Acme invoice 4711"},
                             files=[("file", "acme-4711.txt", content)])
    assert status == 200 and "/files" in url, text[:500]
    assert "acme-4711.txt" in text or "Acme invoice 4711" in text
    doc = next(d for d in hub.kernel.query(type_="document", folder="Finance/Payables") if d["fields"]["title"] == "Acme invoice 4711")
    assert doc["fields"]["doc_class"] == "external" and doc["fields"]["provenance"]["channel"] == "shell"

    # Attach it to the bill; the record page lists it.
    status, url, text = post(shell, "/attach", {"as": "alice", "record": locator, "doc": doc["locator"]})
    assert status == 200 and "/record/" in url
    assert doc["number"] in text
    assert any(d["locator"] == doc["locator"] for d in hub.dms.documents_of(locator))

    # The document page and the download.
    status, text = get(shell, "/document/" + quote(doc["locator"], safe=":"), **{"as": "alice"})
    assert status == 200 and "Acme invoice 4711" in text and "Invoice 4711" in text and rec["number"] in text
    with urlopen(shell["base"] + "/download?" + urlencode({"doc": doc["locator"], "v": 1, "as": "alice"}), timeout=10) as r:
        assert r.status == 200 and r.read() == content
    status, text = get(shell, "/document/" + quote(doc["locator"], safe=":"), **{"as": "carol"})
    assert status == 403

    # Uploading and attaching in one step, then a snapshot of the bill.
    status, url, text = post(shell, "/attach", {"as": "alice", "record": locator, "title": "receipt"}, files=[("file", "receipt.txt", b"received")])
    assert status == 200 and "receipt" in text
    status, url, text = post(shell, "/snapshot", {"as": "alice", "record": locator})
    assert status == 200 and "/document/" in url and "snapshot" in text
    assert len(hub.dms.documents_of(locator)) == 3

    # A denied upload shows the policy sentence.
    status, url, text = post(shell, "/upload", {"as": "carol", "folder": "Finance/Payables"}, files=[("file", "x.txt", b"x")])
    assert status == 403 and "cannot file documents" in text


def test_document_checkout_checkin_over_http(shell):
    hub = shell["hub"]
    doc = hub.dms.create(title="SOP-9 Cleaning", doc_class="controlled", folder="Quality/Controlled Documents", by="alice",
                         content=b"v1", filename="sop9.md")
    loc = doc["locator"]
    status, url, text = post(shell, "/checkout", {"as": "alice", "doc": loc})
    assert status == 200 and "checked out by" in text and "alice" in text
    status, url, text = post(shell, "/checkin", {"as": "doc-reviewer", "doc": loc, "note": "edit"}, files=[("file", "sop9.md", b"v2")])
    assert status == 400 and "checked out by alice" in text
    status, url, text = post(shell, "/checkin", {"as": "alice", "doc": loc, "note": "second"}, files=[("file", "sop9.md", b"v2")])
    assert status == 200
    assert len(hub.dms.versions(loc)) == 2 and hub.dms.content(loc)[0] == b"v2"
    assert "second" in text


def test_boards_filters_and_inbox_message(shell):
    status, text = get(shell, "/boards", **{"as": "alice", "type": "bill", "folder": "Finance/Payables", "text": "fixtures"})
    assert status == 200 and "CNC fixtures" in text and "Saved filter" in text
    status, url, text = post(shell, "/msg", {"as": "alice", "to": "ap-reviewer", "subject": "status", "body": "what is the status?", "channel": "dm"})
    assert status == 200 and "/inbox" in url and "replied" in text.lower() or "Re: status" in text


def test_record_links_and_hold(shell):
    hub = shell["hub"]
    bills = hub.kernel.query(type_="bill")
    assert bills
    loc = bills[0]["locator"]
    status, url, text = post(shell, "/link", {"as": "alice", "record": loc, "rel": "references", "target": loc})
    assert status == 200 and "references" in text
    status, url, text = post(shell, "/link", {"as": "alice", "record": loc, "rel": "bogus", "target": loc})
    assert status == 400 and "link-vocabulary" in text
    status, url, text = post(shell, "/hold", {"as": "alice", "record": loc, "on": "1", "reason": "audit"})
    assert status == 403 and "approve permission" in text
    status, url, text = post(shell, "/hold", {"as": "bob", "record": loc, "on": "1", "reason": "audit"})
    assert status == 200 and "on hold" in text
    post(shell, "/hold", {"as": "bob", "record": loc, "on": "0", "reason": "done"})
    assert not hub.kernel.get(loc)["hold"]


# -------------------------------------------------------------------- CLI
def run_cli(root: Path, *argv) -> str:
    out = io.StringIO()
    with redirect_stdout(out):
        cli.main(["--root", str(root), *argv])
    return out.getvalue()


def test_cli_records_and_documents(shell, tmp_path):
    root = shell["root"]
    hub = shell["hub"]
    out = run_cli(root, "records")
    assert "BILL-0001" in out and "bill" in out
    out = run_cli(root, "records", "--type", "bill", "--state", "posted")
    assert "BILL-0001" in out
    out = run_cli(root, "record", "BILL-0001")
    assert "fields:" in out and "links:" in out and "documents:" in out and "decisions:" in out and "transitions:" in out
    assert "approved for posting" in out and "has-document" in out
    src = tmp_path / "wi-3.md"
    src.write_text("# Work instruction 3\n")
    out = run_cli(root, "doc-new", "--title", "WI-3", "--class", "controlled", "--folder", "Quality/Controlled Documents", "--as", "alice",
                  "--file", str(src), "--attach-to", "BILL-0001", "--type", "work-instruction")
    number = out.split()[0]
    assert number.startswith("DOC-") and "rev A.0" in out
    out = run_cli(root, "doc-versions", number)
    assert "wi-3.md" in out and " 1 " in out
    out = run_cli(root, "docs", "--folder", "Quality/Controlled Documents")
    assert "WI-3" in out
    out = run_cli(root, "doc-checkout", number, "--as", "alice")
    assert "checked out" in out
    src.write_text("# Work instruction 3, revised\n")
    out = run_cli(root, "doc-checkin", number, "--as", "alice", "--file", str(src), "--note", "revised")
    assert "version 2" in out
    target = tmp_path / "out.md"
    run_cli(root, "doc-get", number, "--out", str(target), "--version", "1")
    assert target.read_text() == "# Work instruction 3\n"
    out = run_cli(root, "upload", "--folder", "Finance/Payables", "--as", "alice", "--file", str(src), "--attach-to", "BILL-0001")
    assert "filed in Finance/Payables" in out
    out = run_cli(root, "snapshot", "BILL-0001", "--as", "bob")
    assert "snapshot" in out
    out = run_cli(root, "link", "BILL-0001", "references", number, "--as", "alice")
    assert "references" in out
    out = run_cli(root, "attach", "BILL-0001", number, "--as", "alice")
    assert "attached to BILL-0001" in out
    hub.kernel._cache = None
    assert any(d["number"] == number for d in hub.dms.documents_of("BILL-0001"))
    with pytest.raises(SystemExit, match="link-vocabulary"):
        run_cli(root, "link", "BILL-0001", "bogus", number, "--as", "alice")


# ------------------------------------------------------- request handling
def _raw(shell, request: bytes) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", shell["port"]), timeout=10)
    s.sendall(request)
    return s


def test_healthz_is_served_while_a_malformed_or_stalled_post_is_open(shell):
    bad = _raw(shell, b"POST /msg HTTP/1.1\r\nHost: x\r\nContent-Length: -1\r\n\r\n")
    stalled = _raw(shell, b"POST /msg HTTP/1.1\r\nHost: x\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: 100\r\n\r\n")
    try:
        with urlopen(shell["base"] + "/healthz", timeout=3) as r:  # must not wait on either of them
            assert r.status == 200 and r.read() == b"ok"
            assert "Python" not in (r.headers.get("Server") or "") and r.headers.get("Server", "").startswith("hubproto-shell")
        with urlopen(shell["base"] + "/?as=alice", timeout=3) as r:  # the hub lock is free too
            assert r.status == 200
        first = bad.recv(65536).split(b"\r\n", 1)[0]
        assert b" 400 " in first, first
    finally:
        bad.close()
        stalled.close()
    status, text = post(shell, "/msg", {"as": "alice", "to": "bob", "subject": "after", "body": "still works"})[0::2]
    assert status == 200


def test_post_without_content_length_is_411(shell):
    conn = http.client.HTTPConnection("127.0.0.1", shell["port"], timeout=10)
    conn.putrequest("POST", "/msg", skip_accept_encoding=True)
    conn.putheader("Content-Type", "application/x-www-form-urlencoded")
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 411 and b"Content-Length" in resp.read()
    conn.close()


def test_oversize_upload_is_refused_with_413(shell):
    handler = shell["server"].RequestHandlerClass
    old = handler.max_upload
    handler.max_upload = 4096
    try:
        conn = http.client.HTTPConnection("127.0.0.1", shell["port"], timeout=10)
        conn.putrequest("POST", "/upload", skip_accept_encoding=True)
        conn.putheader("Content-Type", "multipart/form-data; boundary=x")
        conn.putheader("Content-Length", str(5000))
        conn.endheaders()  # the body is never sent: the limit is checked before it is read
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        assert resp.status == 413 and "HUBPROTO_MAX_UPLOAD" in body and "4,096" in body
        conn.close()
        status, url, text = post(shell, "/upload", {"as": "alice", "folder": "Finance/Payables", "title": "small"}, files=[("file", "s.txt", b"x" * 100)])
        assert status == 200, text[:300]
    finally:
        handler.max_upload = old
    assert not any(d["fields"].get("size", 0) == 5000 for d in shell["hub"].kernel.query(type_="document"))


# ------------------------------------------------------- permission trimming
def test_non_member_sees_no_release_routing_runs_or_inbox(shell):
    hub = shell["hub"]
    run_id = hub.runner.list()[0]["id"]
    status, text = get(shell, "/", **{"as": "mallory"})
    assert status == 200 and "not a member" in text
    assert "Policy release in effect" not in text and "Processes you can start" not in text and hub.releases.current()["digest"][:16] not in text
    status, text = get(shell, "/releases", **{"as": "mallory"})
    assert status == 200 and "Routing in effect" not in text and "not a member" in text
    status, text = get(shell, "/runs", **{"as": "mallory"})
    assert status == 200 and "/run/" not in text and "not a member" in text
    status, text = get(shell, "/run/" + run_id, **{"as": "mallory"})
    assert status == 403 and "not a member" in text
    status, text = get(shell, "/inbox", **{"as": "mallory"})
    assert status == 200 and "not a member" in text and "/decide" not in text
    status, url, text = post(shell, "/msg", {"as": "mallory", "to": "bob", "subject": "hi", "body": "x"})
    assert status == 403
    status, url, text = post(shell, "/msg", {"as": "alice", "to": "nobody", "subject": "hi", "body": "x"})
    assert status == 404


def test_runs_are_trimmed_to_folders_the_user_may_read(shell):
    hub = shell["hub"]
    bills = [r for r in hub.runner.list() if r["process"] == "purchase-to-pay"]
    assert bills
    run_id = bills[0]["id"]
    status, text = get(shell, "/runs", **{"as": "bob"})  # controller: reads Finance
    assert status == 200 and run_id in text
    status, text = get(shell, "/runs", **{"as": "carol"})  # quality manager: no read rule on Finance
    assert status == 200 and run_id not in text
    status, text = get(shell, "/run/" + run_id, **{"as": "carol"})
    assert status == 403 and "cannot read run" in text
    status, text = get(shell, "/run/" + run_id, **{"as": "alice"})  # the owner always sees her own run
    assert status == 200
    status, text = get(shell, "/why", **{"as": "bob", "principal": "carol", "action": "read", "object": "*", "folder": "Finance"})
    assert status == 200 and "May <b>carol</b> read" in text and "denied" in text


# ------------------------------------------------------- decisions
def test_decide_form_offers_only_allowed_fields_and_refuses_others(shell):
    hub = shell["hub"]
    # A bill: the next state needs a journal, which the system posts after the signature; the form must not ask for it.
    bill = dated(MID_BILL)
    status, url, text = post(shell, "/start", {"as": "alice", "process": "purchase-to-pay", **{k: str(v) for k, v in bill.items()}})
    assert status == 200, text[:300]
    run_id = url.split("/run/")[1].split("?")[0]
    state = hub.runner.load(run_id)
    assert state["status"] == "waiting", state["status"]
    approver = state["pending"]["assignees"][0]
    status, text = get(shell, "/inbox", **{"as": approver})
    card = text.split(f'name="run" value="{run_id}"')[0].rsplit('<div class="card', 1)[1]
    assert 'name="journal"' not in text and 'name="meaning"' in text and "required" in text
    assert "billed to date" in card and "match result" in card and "4000" in card
    status, url, text = post(shell, "/decide", {"as": approver, "run": run_id, "verdict": "approve", "meaning": "x", "journal": "TYPED-BY-BOB",
                                                "digest": state["pending"]["payload_digest"]})
    assert status == 400 and "may not set journal" in text and "this Gate accepts" in text
    assert "TYPED-BY-BOB" not in str(hub.kernel.get(state["record"])["fields"])
    # An NCR: disposition and rationale are required for 'closed', containment is optional; risk and capa are never offered.
    status, url, text = post(shell, "/start", {"as": "alice", "process": "nonconformance", "title": "[shell] scratched housing",
                                               "description": "Cosmetic scratch on the housing, safety risk noted", "source": "inspection"})
    assert status == 200, text[:300]
    ncr_run = url.split("/run/")[1].split("?")[0]
    state = hub.runner.load(ncr_run)
    assert state["status"] == "waiting", state["status"]
    who = state["pending"]["assignees"][0]
    status, text = get(shell, "/inbox", **{"as": who})
    card = text.split(f'name="run" value="{ncr_run}"')[1].split("</form>")[0]
    assert '<select name="disposition" required>' in card and 'name="disposition_rationale"' in card and "required" in card
    assert 'name="containment"' in card and 'name="risk"' not in card and 'name="capa"' not in card
    assert "closed needs: disposition, disposition_rationale" in card
    status, url, text = post(shell, "/decide", {"as": who, "run": ncr_run, "verdict": "approve", "meaning": "scrap it", "risk": "low",
                                                "digest": state["pending"]["payload_digest"]})
    assert status == 400 and "may not set risk" in text
    status, url, text = post(shell, "/decide", {"as": who, "run": ncr_run, "verdict": "approve", "meaning": "scrap it", "disposition": "scrap",
                                                "disposition_rationale": "beyond repair", "digest": state["pending"]["payload_digest"]})
    assert status == 200, text[:300]
    rec = hub.kernel.get(state["record"])
    assert rec["state"] == "closed" and rec["fields"]["disposition"] == "scrap"
    status, text = get(shell, "/inbox", **{"as": who})
    assert ncr_run not in text, "decided items leave the inbox"


def test_list_fields_on_a_decision_take_json(shell):
    from hubproto.shell import coerce

    assert coerce("findings", "list", '[{"clause": "7.5.3", "severity": "major"}]') == [{"clause": "7.5.3", "severity": "major"}]
    assert coerce("actions", "list", "fix the jig, retrain") == ["fix the jig", "retrain"]
    assert coerce("lines", None, "rec:item:ITEM-00001,4\n", "lines:locator,qty") == [{"item": "rec:item:ITEM-00001", "quantity": 4.0}]
    with pytest.raises(Exception, match="must be one of"):
        coerce("priority", "enum:low,medium,high", "normal")


# ------------------------------------------------------- design: start with files, requirement statements, resubmit
SCHEMATIC = (ROOT / "examples" / "sensor.kicad_sch").read_bytes()


def _start_design(shell, title: str) -> tuple[str, dict]:
    status, url, text = post(shell, "/start", {"as": "alice", "process": "design-release", "title": title, "kind": "electrical",
                                               "change_class": "3", "description": "sensor node", "json": "",
                                               "user_needs": "The sensor node must run 24 hours on battery.\nThe enclosure must survive a 1 m drop."},
                             files=[("files", "sensor.kicad_sch", SCHEMATIC)])
    assert status == 200, text[:500]
    run_id = url.split("/run/")[1].split("?")[0]
    return run_id, shell["hub"].runner.load(run_id)


def test_start_with_a_multipart_file_creates_an_attached_document(shell, monkeypatch):
    monkeypatch.delenv("HUBPROTO_CAD_WORKER_URL", raising=False)
    hub = shell["hub"]
    run_id, state = _start_design(shell, "[shell] sensor board")
    assert state["status"] == "waiting" and state["pending"]["kind"] == "decision" and state["pending"]["assignees"] == ["bob"], state["status"]
    design = hub.kernel.get(state["record"])
    assert design["fields"]["kind"] == "electrical" and design["fields"]["change_class"] == "3"
    needs = sorted(r["fields"]["statement"] for r in hub.kernel.query(type_="requirement") if r["fields"].get("design") == design["locator"])
    assert needs == ["The enclosure must survive a 1 m drop.", "The sensor node must run 24 hours on battery."], "one requirement per user-need line"
    docs = hub.dms.documents_of(design["locator"])
    sch = next(d for d in docs if d["fields"].get("filename") == "sensor.kicad_sch")
    assert sch["fields"]["doc_type"] == "schematic" and sch["fields"]["doc_class"] == "controlled"
    assert hub.dms.content(sch["locator"])[0] == SCHEMATIC
    # No worker: the export step warns and the run still reaches review; the card shows the trace and the Guard findings.
    status, text = get(shell, "/inbox", **{"as": "bob"})
    assert status == 200 and "worker" in text.lower() and 'name="part"' not in text
    card = text.split(f'name="run" value="{run_id}"')[0].rsplit("<div class=\"card", 1)[1]
    assert "The sensor node must run 24 hours on battery." in card and "Requirements" in card and "sensor.kicad_sch" in card
    # The Start form never marks a field required and refuses a system field or an unknown one.
    status, text = get(shell, "/", **{"as": "alice"})
    design_form = text.split('value="design-release"')[1].split("</form>")[0]
    assert 'name="kind"' in design_form and 'name="user_needs"' in design_form and 'type="file"' in design_form
    assert '<input name="title" value="" size="24" required>' in design_form, "the first Gate's state needs title and kind"
    assert '<select name="kind" required>' in design_form
    assert '<textarea name="description"' in design_form and 'name="description" rows="2" cols="50" required' not in design_form
    status, url, text = post(shell, "/start", {"as": "alice", "process": "design-release", "title": "x", "json": '{"part": "rec:part:PN-00001"}'})
    assert status == 400 and "set by the system" in text
    status, url, text = post(shell, "/start", {"as": "alice", "process": "design-release", "title": "x", "nonsense": "1"})
    assert status == 400 and "has no field" in text


def test_requirement_statement_is_visible_on_inbox_card_record_page_and_cli(shell, monkeypatch):
    monkeypatch.delenv("HUBPROTO_CAD_WORKER_URL", raising=False)
    hub = shell["hub"]
    run_id, state = _start_design(shell, "[shell] statement check")
    reqs = [r for r in hub.kernel.query(type_="requirement") if r["fields"].get("design") == state["record"]]
    assert len(reqs) == 2
    req = next(r for r in reqs if "24 hours" in r["fields"]["statement"])
    status, text = get(shell, "/inbox", **{"as": "bob"})
    assert status == 200 and "The sensor node must run 24 hours on battery." in text
    status, text = get(shell, "/record/" + quote(req["locator"], safe=":"), **{"as": "bob"})
    assert status == 200 and text.count("The sensor node must run 24 hours on battery.") >= 2  # headline and Details
    status, text = get(shell, "/boards", **{"as": "bob", "type": "requirement"})
    assert "The sensor node must run 24 hours on battery." in text
    out = run_cli(shell["root"], "record", req["number"])
    assert "statement: The sensor node must run 24 hours on battery." in out
    out = run_cli(shell["root"], "records", "--type", "requirement")
    assert "The sensor node must run 24 hours" in out


def test_reject_returns_the_design_and_resubmit_restarts_it(shell, monkeypatch):
    monkeypatch.delenv("HUBPROTO_CAD_WORKER_URL", raising=False)
    hub = shell["hub"]
    run_id, state = _start_design(shell, "[shell] resubmit")
    loc = state["record"]
    status, url, text = post(shell, "/decide", {"as": "bob", "run": run_id, "verdict": "reject", "meaning": "needs a drawing",
                                                "digest": state["pending"]["payload_digest"]})
    assert status == 200, text[:300]
    state = hub.runner.load(run_id)
    assert state["pending"]["kind"] == "resubmit" and state["pending"]["assignees"] == ["alice"]
    assert hub.kernel.get(loc)["state"] == "returned"
    # Bob's inbox no longer shows it; alice's shows the returned item with a resubmit form and the reason; the record page too.
    status, text = get(shell, "/inbox", **{"as": "bob"})
    assert f'name="run" value="{run_id}"' not in text
    status, text = get(shell, "/inbox", **{"as": "alice"})
    assert "Returned to you for rework" in text and "needs a drawing" in text and 'action="/resubmit"' in text and "returned" in text
    status, text = get(shell, "/record/" + quote(loc, safe=":"), **{"as": "alice"})
    assert 'action="/resubmit"' in text and ">returned<" in text and ">cancelled<" in text and ">failed<" in text
    # Bob may not resubmit; alice resubmits with a changed description and a drawing.
    status, url, text = post(shell, "/resubmit", {"as": "bob", "run": run_id, "note": "no"})
    assert status == 400 and "not an assignee" in text
    status, url, text = post(shell, "/resubmit", {"as": "alice", "run": run_id, "description": "drawing added", "note": "added the drawing"},
                             files=[("files", "bracket.pdf", b"%PDF-1.4 drawing")])
    assert status == 200, text[:300]
    state = hub.runner.load(run_id)
    assert state["status"] == "waiting" and state["pending"]["kind"] == "decision" and state["pending"]["assignees"] == ["bob"]
    design = hub.kernel.get(loc)
    assert design["fields"]["description"] == "drawing added"
    assert any(d["fields"].get("doc_type") == "drawing" and d["fields"].get("filename") == "bracket.pdf" for d in hub.dms.documents_of(loc))
    assert any(e["type"] == "run.resubmitted" and e["note"] == "added the drawing" for e in hub.runner.track(run_id))
    status, text = get(shell, "/inbox", **{"as": "alice"})
    assert "Returned to you for rework" not in text
    status, text = get(shell, "/record/" + quote(loc, safe=":"), **{"as": "alice"})
    assert "bracket.pdf" in text or "drawing" in text


# ------------------------------------------------------- failed runs: retry and cancel
def _closed_period_bill(shell) -> tuple[str, dict]:
    from datetime import date

    bill = {**SMALL_BILL, "date": f"{date.today().year - 1}-12-15", "po_ref": "PO-1003"}
    status, url, text = post(shell, "/start", {"as": "alice", "process": "purchase-to-pay", **{k: str(v) for k, v in bill.items()}})
    assert status == 200, text[:300]
    run_id = url.split("/run/")[1].split("?")[0]
    return run_id, shell["hub"].runner.load(run_id)


def test_failed_run_returns_to_its_owner_who_can_retry(shell):
    hub = shell["hub"]
    run_id, state = _closed_period_bill(shell)
    assert state["status"] == "failed" and state["pending"] is None
    rec = hub.kernel.get(state["record"])
    assert rec["state"] == "failed" and rec["assignees"] == ["alice"]
    status, text = get(shell, "/inbox", **{"as": "alice"})
    card = text.split(f'name="run" value="{run_id}"')[0].rsplit("<div class=\"card", 1)[1]
    assert "Failed runs of yours" in text and "not open" in card and 'action="/retry"' in text and 'action="/cancel"' in text
    status, text = get(shell, "/runs", **{"as": "alice"})
    assert "failed:" in text and "not open" in text
    status, text = get(shell, "/record/" + quote(state["record"], safe=":"), **{"as": "alice"})
    assert "The run failed at" in text and 'action="/retry"' in text
    status, text = get(shell, "/record/" + quote(state["record"], safe=":"), **{"as": "carol"})
    assert status == 403
    status, url, text = post(shell, "/retry", {"as": "carol", "run": run_id, "note": "mine"})
    assert status == 400 and "may not retry" in text
    status, url, text = post(shell, "/retry", {"as": "alice", "run": run_id, "note": "trying again"})
    assert status == 200 and "/record/" in url, text[:300]
    state = hub.runner.load(run_id)
    assert state["status"] == "failed", "the period is still closed, so it fails again"
    track = hub.runner.track(run_id)
    assert [e["type"] for e in track if e["type"] in ("run.retried", "run.failed")] == ["run.failed", "run.retried", "run.failed"]
    assert next(e for e in track if e["type"] == "run.retried")["by"] == "alice"
    rec = hub.kernel.get(state["record"])
    assert rec["state"] == "failed" and rec["assignees"] == ["alice"]
    assert sum(1 for m in hub.inbox.for_principal("alice") if m["kind"] == "failure" and m["run_id"] == run_id) == 2
    status, text = get(shell, "/inbox", **{"as": "alice"})
    assert f'name="run" value="{run_id}"' in text and "failure" in text


def test_owner_cancels_a_failed_run(shell):
    hub = shell["hub"]
    run_id, state = _closed_period_bill(shell)
    status, url, text = post(shell, "/cancel", {"as": "carol", "run": run_id, "reason": "not mine"})
    assert status == 400 and "may not cancel" in text
    status, url, text = post(shell, "/cancel", {"as": "alice", "run": run_id, "reason": "wrong period"})
    assert status == 200 and ">cancelled<" in text, text[:300]
    assert hub.runner.load(run_id)["status"] == "cancelled" and hub.kernel.get(state["record"])["state"] == "cancelled"
    status, text = get(shell, "/inbox", **{"as": "alice"})
    assert f'name="run" value="{run_id}"' not in text
    status, url, text = post(shell, "/retry", {"as": "alice", "run": run_id, "note": "again"})
    assert status == 400 and "not failed" in text
    status, text = get(shell, "/boards", **{"as": "alice", "type": "bill", "state": "cancelled"})
    assert hub.kernel.get(state["record"])["number"] in text


def test_decisions_table_lists_each_signature_once(shell):
    hub = shell["hub"]
    rec = next(r for r in hub.kernel.query(type_="bill", state="posted") if r["decisions"])
    status, text = get(shell, "/record/" + quote(rec["locator"], safe=":"), **{"as": "bob"})
    assert status == 200
    section = text.split('<h2 id="decisions">')[1].split('<h2 id="track">')[0]
    assert section.count("<tr>") - 1 == len(rec["decisions"]) == 1
    assert section.count(rec["decisions"][0]["signature"]) == 1
    out = run_cli(shell["root"], "record", rec["number"])
    assert out.count(rec["decisions"][0]["signature"]) == 1


def test_controlled_document_controls_follow_permission_and_state(shell):
    hub = shell["hub"]
    effective = next((d for d in hub.kernel.query(type_="document") if d["state"] == "effective"), None)
    if effective is None:
        run_id = hub.runner.submit("document-control", {"record": {"title": "[shell] SOP-Q", "change_type": "minor"}}, owner="alice")
        effective = hub.kernel.get(hub.runner.load(run_id)["record"])
        assert effective["state"] == "effective"
    status, text = get(shell, "/document/" + quote(effective["locator"], safe=":"), **{"as": "alice"})
    assert status == 200 and "changes only through a new revision" in text and "action='/checkout'" not in text
    draft = hub.dms.create(title="[shell] draft", doc_class="controlled", folder="Quality/Controlled Documents", by="alice", content=b"d", filename="d.md")
    status, text = get(shell, "/document/" + quote(draft["locator"], safe=":"), **{"as": "alice"})
    assert "action='/checkout'" in text
    status, text = get(shell, "/document/" + quote(draft["locator"], safe=":"), **{"as": "bob"})  # reviewer: may read, not create documents
    assert status == 200 and "action='/checkout'" not in text and "cannot change documents" in text


# ------------------------------------------------------- CLI: pending, resubmit, retry, cancel
def test_cli_pending_resubmit_retry_cancel(shell, monkeypatch, tmp_path):
    monkeypatch.delenv("HUBPROTO_CAD_WORKER_URL", raising=False)
    hub = shell["hub"]
    root = shell["root"]
    run_id = hub.runner.submit("design-release", {"record": {"title": "[cli] bracket", "kind": "mechanical", "user_needs": ["Must fit."]}}, owner="alice")
    out = run_cli(root, "inbox-pending", "--as", "bob")
    assert run_id in out and "Review the design" in out and "fields=" in out
    hub.runner.decide(run_id, "bob", "reject", meaning="needs a model")
    out = run_cli(root, "inbox-pending", "--as", "alice")
    assert run_id in out and "resubmit" in out and "needs a model" in out
    with pytest.raises(SystemExit, match="not an assignee"):
        run_cli(root, "resubmit", run_id, "--as", "bob")
    out = run_cli(root, "resubmit", run_id, "--as", "alice", "--field", "description=model added", "--field", "change_class=1",
                  "--file", str(ROOT / "examples" / "box.FCStd"), "--note", "added the model")
    assert "resubmitted status=waiting" in out and "bob" in out
    hub.kernel._cache = None
    design = hub.kernel.get(hub.runner.load(run_id)["record"])
    assert design["fields"]["description"] == "model added" and design["fields"]["change_class"] == "1"
    assert any(d["fields"].get("doc_type") == "model" for d in hub.dms.documents_of(design["locator"]))
    from datetime import date

    bad = hub.runner.submit("purchase-to-pay", {"record": {**SMALL_BILL, "date": f"{date.today().year - 1}-12-15", "po_ref": "PO-1003"}}, owner="alice")
    out = run_cli(root, "inbox-pending", "--as", "alice")
    assert bad in out and "failed" in out and "retry or cancel" in out
    out = run_cli(root, "retry", bad, "--as", "alice", "--note", "again")
    assert "retried status=failed" in out
    with pytest.raises(SystemExit, match="may not cancel"):
        run_cli(root, "cancel", bad, "--as", "carol", "--reason", "x")
    out = run_cli(root, "cancel", bad, "--as", "alice", "--reason", "wrong period")
    assert "cancelled" in out and "state=cancelled" in out
    hub.kernel._cache = None
    assert hub.runner.load(bad)["status"] == "cancelled"
