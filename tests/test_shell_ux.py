"""Regression suite for the shell findings of the adversarial UX panel (feedback/ux/TRIAGE.md, shell column).

Each test reproduces a panel's steps over HTTP and checks the page a person would see. Finding ids are quoted
in the test names and docstrings."""

import html
import http.client
import re
import shutil
import socket
import uuid
from datetime import date
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pytest

from conftest import BIG_BILL, SMALL_BILL, ROOT, dated, edit_frame, release_all
from hubproto.app import Hub
from hubproto.shell import CONTRAST_PAIRS, DARK, LIGHT, free_port, serve_in_thread


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


def get(shell, path: str, **params):
    url = shell["base"] + path + (("&" if "?" in path else "?") + urlencode(params) if params else "")
    try:
        with urlopen(url, timeout=10) as r:
            return r.status, r.read().decode("utf-8"), r.headers
    except HTTPError as e:
        return e.code, e.read().decode("utf-8"), e.headers


def post(shell, path: str, fields: dict, files=None, follow: bool = True):
    """POST a form; returns (status, final url, body). ``follow=False`` returns the redirect itself."""
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
    if not follow:
        conn = http.client.HTTPConnection("127.0.0.1", shell["port"], timeout=10)
        conn.request("POST", path, body=body, headers={"Content-Type": ctype})
        resp = conn.getresponse()
        out = (resp.status, resp.getheader("Location") or "", resp.read().decode("utf-8"))
        conn.close()
        return out
    req = Request(shell["base"] + path, data=body, headers={"Content-Type": ctype}, method="POST")
    try:
        with urlopen(req, timeout=10) as r:
            return r.status, r.url, r.read().decode("utf-8")
    except HTTPError as e:
        return e.code, e.url, e.read().decode("utf-8")


def plain(html: str) -> str:
    html = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def main_of(html: str) -> str:
    return html.split('<main id="main">')[1].split("</main>")[0]


def run_of(url: str) -> str:
    return url.split("/run/")[1].split("?")[0]


def entry(text: str, process: str) -> str:
    """One Start entry on the home page (its own <details>, which nests the Advanced one)."""
    after = text.split(f'<details class="card start" id="start-{process}"')[1]
    return after.split('<details class="card start"')[0]


def open_card(shell, who: str, run_id: str) -> str:
    status, text, _ = get(shell, "/inbox", **{"as": who, "open": run_id})
    assert status == 200, text[:300]
    return text.split("<article class='card decide'")[1].split("</article>")[0]


_po_seq = [1100]


def new_po(hub, amount: float = 12000.0, received: bool = True) -> str:
    """A fresh purchase order to bill against (PO-1002 is fully billed after the first bill)."""
    _po_seq[0] += 1
    po = f"PO-{_po_seq[0]}"
    hub.kernel.append("po.issued", f"po:{po}", {"supplier": "acme-metals", "amount": amount, "currency": "USD", "description": "[ux] fixtures", "received": received}, by="seed")
    hub.kernel._cache = None
    return po


def big_bill(hub) -> dict:
    return {**dated(BIG_BILL), "po_ref": new_po(hub)}


def start_bill(shell, bill: dict, who: str = "alice") -> tuple[str, dict]:
    status, url, text = post(shell, "/start", {"as": who, "process": "purchase-to-pay", **{k: str(v) for k, v in bill.items()}})
    assert status == 200 and "/run/" in url, text[:400]
    run_id = run_of(url)
    return run_id, shell["hub"].runner.load(run_id)


def start_task(shell, title: str, assignee: str = "carol", **extra) -> tuple[str, dict]:
    status, url, text = post(shell, "/start", {"as": "alice", "process": "task", "title": title, "assignee": assignee, **extra})
    assert status == 200 and "/run/" in url, text[:400]
    run_id = run_of(url)
    return run_id, shell["hub"].runner.load(run_id)


# ------------------------------------------------------------- 1. identity and errors
def test_no_default_principal_a_page_without_as_asks_who_you_are(shell):
    """hostile-4, first-time-user-16: blank or missing `as` is nobody, not alice."""
    for path in ("/", "/inbox", "/inbox?as=", "/inbox?as=%20%20", "/?as=%00"):
        status, text, _ = get(shell, path)
        assert status == 200 and "Who are you?" in text, path
        assert 'name="as" value="alice"' not in text and "Welcome, alice" not in text and 'action="/decide"' not in text
        assert "alice" in text and "bob" in text and "ap-reviewer" not in text.split("Automatic reviewers")[0].split("<div class='who'>")[-1]
    status, url, text = post(shell, "/msg", {"to": "bob", "subject": "x", "body": "y"})
    assert status == 400 and "Say who you are" in text


def test_a_refused_start_rerenders_the_form_with_the_typed_values(shell):
    """hostile-2, interaction-7, accessibility-5: the typed description survives and the message sits beside the field."""
    long_text = "Aluminium stock for the fixture run, " * 40
    status, url, text = post(shell, "/start", {"as": "alice", "process": "purchase-to-pay", "supplier": "acme-metals", "amount": "$1,000",
                                               "currency": "USD", "date": date.today().isoformat(), "description": long_text})
    assert status == 400
    assert "Not done." not in text and "<h1>Welcome, alice</h1>" in text
    form = entry(text, "purchase-to-pay")
    assert ' open' in text.split('id="start-purchase-to-pay"')[1][:10] and long_text.strip() in form, "the entry is open and the typed description is kept"
    assert 'name="amount"' in form and 'aria-invalid="true"' in form and "must be a number" in form
    bad = form.split('class="f bad"')[1].split("</label>")[0]
    assert 'name="amount"' in bad and "must be a number" in bad, "the message is beside the failing control"
    assert not any(r["fields"].get("description") == long_text.strip() for r in shell["hub"].kernel.query(type_="bill")), "nothing was filed"
    # A JSON typo on a task re-renders that entry with the JSON box open and the title kept (interaction-7).
    status, url, text = post(shell, "/start", {"as": "alice", "process": "task", "title": "Typo check", "json": '{"lot": L-42}'})
    assert status == 400 and 'id="start-task" open' in text and 'value="Typo check"' in text and "does not parse" in text
    assert "/start" not in re.search(r'<form method="get" action="([^"]+)"', text).group(1), "the Acting-as switcher never posts to an action"


def test_a_refused_decision_rerenders_the_card_with_the_meaning_kept(shell):
    """approver-11, interaction-3: a server-side rule (missing disposition) keeps the typed meaning on the open card."""
    hub = shell["hub"]
    status, url, text = post(shell, "/start", {"as": "alice", "process": "nonconformance", "title": "[ux] scratched housing",
                                               "description": "deep scratch, safety risk noted", "source": "inspection", "severity": "high"})
    run_id = run_of(url)
    state = hub.runner.load(run_id)
    who = state["pending"]["assignees"][0]
    status, url, text = post(shell, "/decide", {"as": who, "run": run_id, "verdict": "approve", "meaning": "dispositioned after review",
                                                "confirm": "1", "digest": state["seen_digest"]})
    assert status == 400 and "<h1>Inbox</h1>" in text
    card = text.split("<article class='card decide'")[1].split("</article>")[0]
    assert "dispositioned after review" in card, "the typed meaning is kept"
    assert "Fill in disposition" in card or "needs disposition" in card
    assert 'name="disposition"' in card and "RunError" not in text


def test_error_pages_have_a_heading_a_specific_title_and_no_class_names(shell):
    """content-2, first-time-user-17, hostile-18, accessibility-16."""
    cases = [("/run/nope?as=alice", "There is no run starting with 'nope'"), ("/record/NOPE-1?as=alice", "There is no item 'NOPE-1'"),
             ("/boards?as=alice&type=nonsense", "no item type"), ("/doc/DOC-9999?as=alice", "There is no document"),
             ("/download?as=alice&doc=DOC-9999&v=abc", "There is no document"), ("/nope?as=alice", "There is no such page")]
    for path, needle in cases:
        status, text, _ = get(shell, path)
        assert status in (400, 404), path
        assert "<h1>Not done</h1>" in text and re.search(r"<title>Not done: .+ - Hub</title>", text), path
        assert needle in html.unescape(text) and 'href="/inbox?as=alice"' in text, path
        for leak in ("RunError", "KernelGuardError", "matches", "Not done."):
            assert leak not in plain(text), (path, leak)
    status, url, text = post(shell, "/decide", {"as": "alice"})
    assert status == 400 and "No item was named" in text and "matches" not in plain(text)
    status, url, text = post(shell, "/attach", {"as": "alice"})
    assert status == 400 and "Choose an item" in text and "KernelGuardError" not in text
    run_id, state = start_task(shell, "[ux] link target", "bob")
    number = shell["hub"].kernel.get(state["record"])["number"]
    status, url, text = post(shell, "/link", {"as": "alice", "record": number, "rel": "bogus", "target": number})
    assert status == 400 and "not a link relation" in text and "link-vocabulary" not in text


def test_html_is_never_cached(shell):
    """hostile-12: Cache-Control: no-store on every HTML page and redirect."""
    for path in ("/", "/?as=alice", "/inbox?as=bob", "/run/nope?as=alice"):
        status, text, headers = get(shell, path)
        assert headers.get("Cache-Control") == "no-store", path
    status, location, body = post(shell, "/start", {"as": "alice", "process": "task", "title": "[ux] cache"}, follow=False)
    assert status == 303


def test_a_stale_card_rerenders_the_inbox_with_the_card_marked(shell):
    """first-time-user-4, hostile-12, approver-1: already decided, or changed since opened, never an error page."""
    hub = shell["hub"]
    run_id, state = start_bill(shell, big_bill(shell["hub"]))
    old_digest = state["seen_digest"]
    assert state["pending"]["assignees"] == ["bob"]
    # The preparer attaches a credit note after bob opened the card: bob's decision quotes a digest that no longer matches.
    status, url, text = post(shell, "/attach", {"as": "alice", "record": state["record"], "title": "Credit note CN-77"}, files=[("file", "cn77.txt", b"amount corrected to 1,900.00 USD")])
    assert status == 200
    status, url, text = post(shell, "/decide", {"as": "bob", "run": run_id, "verdict": "approve", "meaning": "approved for posting", "confirm": "1", "digest": old_digest})
    assert status == 400 and "<h1>Inbox</h1>" in text and "The item changed after you opened it" in text
    card = text.split("<article class='card decide'")[1].split("</article>")[0]
    new_digest = hub.runner.load(run_id)["seen_digest"]
    assert f'name="digest" value="{new_digest}"' in card and "Credit note CN-77" in card, "the current card is shown, with the new document"
    assert hub.runner.load(run_id)["status"] == "waiting"
    # bob decides on the current card; then a Back-button resubmit of the old form says who decided and when.
    status, url, text = post(shell, "/decide", {"as": "bob", "run": run_id, "verdict": "approve", "meaning": "approved for posting", "confirm": "1", "digest": new_digest})
    assert status == 200
    status, url, text = post(shell, "/decide", {"as": "bob", "run": run_id, "verdict": "reject", "reason": "changed my mind", "digest": new_digest})
    assert status == 400 and "<h1>Inbox</h1>" in text
    assert "Already decided by bob at" in text and "approved for posting" in text and "RunError" not in text
    assert "not waiting for a decision" not in text


def test_double_submit_lands_on_the_first_result(shell):
    """hostile-1, interaction-4, hostile-10: a one-use token on create forms and retry."""
    hub = shell["hub"]
    status, text, _ = get(shell, "/", **{"as": "alice"})
    form = text.split('id="start-task"')[1].split("</form>")[0]
    token = re.search(r'name="once" value="([0-9a-f]+)"', form).group(1)
    before = len(hub.kernel.query(type_="task"))
    fields = {"as": "alice", "process": "task", "title": "[ux] dup-click task", "assignee": "bob", "once": token}
    s1, url1, _ = post(shell, "/start", fields)
    s2, url2, _ = post(shell, "/start", fields)
    assert s1 == 200 and s2 == 200 and run_of(url1) == run_of(url2), "the second click lands on the first run"
    assert len(hub.kernel.query(type_="task")) == before + 1
    # A message sent twice with the same token is one message.
    status, text, _ = get(shell, "/inbox", **{"as": "alice"})
    token = re.search(r'action="/msg" class="card">.*?name="once" value="([0-9a-f]+)"', text, re.S).group(1)
    n = len([m for m in hub.inbox.for_principal("bob") if m["subject"] == "[ux] double send"])
    for _ in range(2):
        post(shell, "/msg", {"as": "alice", "to": "bob", "subject": "[ux] double send", "body": "once", "once": token})
    assert len([m for m in hub.inbox.for_principal("bob") if m["subject"] == "[ux] double send"]) == n + 1
    # Retry: a double-clicked Fix-and-retry runs the step once.
    run_id, state = start_bill(shell, {**SMALL_BILL, "date": f"{date.today().year - 1}-12-15", "po_ref": "PO-1003"})
    assert state["status"] == "failed"
    status, text, _ = get(shell, "/record/" + quote(state["record"], safe=":"), **{"as": "alice"})
    token = re.search(r'action="/retry".*?name="once" value="([0-9a-f]+)"', text, re.S).group(1)
    for _ in range(2):
        post(shell, "/retry", {"as": "alice", "run": run_id, "note": "again", "once": token})
    assert [e["type"] for e in hub.runner.track(run_id) if e["type"] == "run.retried"] == ["run.retried"]


def test_oversize_and_stalled_uploads_get_readable_answers(shell):
    """hostile-3: a 413 the client can read (the body is drained), a 408 for a stalled body."""
    handler = shell["server"].RequestHandlerClass
    old_max, old_timeout = handler.max_upload, handler.timeout
    handler.max_upload = 4096
    handler.timeout = 2
    try:
        conn = http.client.HTTPConnection("127.0.0.1", shell["port"], timeout=10)
        conn.request("POST", "/upload", body=b"x" * 5000, headers={"Content-Type": "multipart/form-data; boundary=x", "Content-Length": "5000"})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        assert resp.status == 413 and "too big" in body and "4,096" in body and "<h1>Not done</h1>" in body
        conn.close()
        s = socket.create_connection(("127.0.0.1", shell["port"]), timeout=10)
        s.sendall(b"POST /msg HTTP/1.1\r\nHost: x\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: 100\r\n\r\nas=alice&to=bob&subject=x")
        data = b""
        while b"</html>" not in data:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        s.close()
        assert b" 408 " in data.split(b"\r\n", 1)[0] and b"stalled" in data
    finally:
        handler.max_upload = old_max
        handler.timeout = old_timeout


# ------------------------------------------------------------- 2. inbox as a queue
def test_inbox_is_a_queue_with_one_card_open_and_paging(shell):
    """visual-1, visual-2, hostile-11, accessibility-3/4: summary rows, one open card, 25 per page, filter by type."""
    hub = shell["hub"]
    ids = [start_task(shell, f"[ux] queue task {i}", "carol", priority="high")[0] for i in range(27)]
    status, text, _ = get(shell, "/inbox", **{"as": "carol"})
    assert status == 200 and "<ol class='queue'>" in text and 'action="/decide"' not in text, "no form until a row is opened"
    assert text.count("<li id=\"r-") == 25 and "Page 1 of" in text
    assert "<h3><a href=" in text and "Complete the task: TASK-" in text
    status, text, _ = get(shell, "/inbox", **{"as": "carol", "page": 2})
    assert 0 < text.count("<li id=\"r-") <= 25
    status, text, _ = get(shell, "/inbox", **{"as": "carol", "open": ids[0]})
    assert text.count('name="verdict" value="approve"') == 1 and text.count("<article class='card decide'") == 1
    status, text, _ = get(shell, "/inbox", **{"as": "carol", "type": "task"})
    assert "<li id=\"r-" in text and 'name="type"' in text


def test_the_open_card_decides_first_with_reject_apart_and_a_confirm_step(shell):
    """approver-3, interaction-3, interaction-9, accessibility-1, visual-6, approver-14, first-time-user-5."""
    hub = shell["hub"]
    run_id, state = start_bill(shell, big_bill(shell["hub"]))
    card = open_card(shell, "bob", run_id)
    approve_at, item_at, reject_at = card.index('name="verdict" value="approve"'), card.index("<h4>The item</h4>"), card.index('name="verdict" value="reject"')
    assert approve_at < item_at < reject_at, "decision first, the brief after, Reject apart at the end"
    assert 'formnovalidate' in card.split('name="verdict" value="reject"')[1] and card.count('name="reason"') == 1
    assert 'type="checkbox" name="confirm"' in card and card.index('name="confirm"') < card.index(">Approve BILL-")
    assert "Meaning of your signature" in card and "For example:" in card and "invoice matches" in card
    assert "Reject closes it as rejected" in card and "alice will see your reason" in card
    assert 'placeholder=' not in card
    digest = hub.runner.load(run_id)["seen_digest"]
    status, url, text = post(shell, "/decide", {"as": "bob", "run": run_id, "verdict": "approve", "meaning": "ok", "digest": digest})
    assert status == 400 and "Tick" in text and hub.runner.load(run_id)["status"] == "waiting"
    status, url, text = post(shell, "/decide", {"as": "bob", "run": run_id, "verdict": "reject", "reason": "", "digest": digest})
    assert status == 400 and "Say why you are rejecting" in text and hub.runner.load(run_id)["status"] == "waiting"
    status, url, text = post(shell, "/decide", {"as": "bob", "run": run_id, "verdict": "reject", "reason": "PO-1002 is already fully billed", "digest": digest})
    assert status == 200 and "Rejected BILL-" in text
    notes = [m for m in hub.inbox.for_principal("alice") if m["run_id"] == run_id and m["kind"] in ("rejection", "returned")]
    assert notes and "PO-1002 is already fully billed" in notes[-1]["body"] + notes[-1]["subject"]
    assert "rejectd" not in str(hub.kernel.get(state["record"])["decisions"])


def test_the_card_states_the_policy_band_and_the_human_criterion(shell):
    """approver-9, approver-6 (routing sentence), visual-3 (coloured chips)."""
    run_id, state = start_bill(shell, big_bill(shell["hub"]))
    card = open_card(shell, "bob", run_id)
    text = plain(card)
    assert "Routed to you by finance-policy line" in text and "over 10,000 USD go to the controller" in text
    assert "the ap-reviewer cog may approve up to 2,000.00 USD when the three-way match is within tolerance and the supplier is approved" in text
    assert "this item is 12,000.00 USD" in text and "you are asked whether the bill may be posted" in text
    assert '<span class="chip crit">high</span>' in card and '<span class="chip ok">within tolerance</span>' in card
    assert '<span class="tag">Supplier bill</span>' in card


def test_a_held_item_shows_why_and_disables_approve(shell):
    """approver-10, interaction-10: blocked items say why; Approve is disabled; a hold needs a reason."""
    hub = shell["hub"]
    run_id, state = start_bill(shell, big_bill(shell["hub"]))
    status, url, text = post(shell, "/hold", {"as": "bob", "record": state["record"], "on": "1", "reason": ""})
    assert status == 400 and "Say why" in text and not hub.kernel.get(state["record"])["hold"]
    status, url, text = post(shell, "/hold", {"as": "bob", "record": state["record"], "on": "1", "reason": "query with supplier"})
    assert status == 200 and "put on hold: query with supplier" in text
    card = open_card(shell, "bob", run_id)
    assert "On hold: query with supplier" in card and "release the hold" in card
    assert re.search(r"<button disabled>Approve BILL-", card), "Approve is disabled while blocked"
    post(shell, "/hold", {"as": "bob", "record": state["record"], "on": "0", "reason": "answered"})
    card = open_card(shell, "bob", run_id)
    assert "<button disabled>" not in card


def test_inbox_sections_mine_others_and_quiet_notifications(shell):
    """first-time-user-8, first-time-user-12, approver-16, first-time-user-14: what is mine, what waits on others, no mirrored messages."""
    hub = shell["hub"]
    run_id, state = start_bill(shell, big_bill(shell["hub"]))
    status, text, _ = get(shell, "/inbox", **{"as": "alice"})
    for h in ("Waiting for me", "Returned or stopped, mine", "Waiting on others (mine)", "Notifications", "Messages"):
        assert h in text, h
    others = text.split("id='others'")[1].split("<h2")[0]
    assert hub.kernel.get(state["record"])["number"] in others and "bob" in others and "Approve the bill" in others
    status, text, _ = get(shell, "/inbox", **{"as": "bob"})
    messages = text.split("id='messages'")[1].split('<h2 id="send">')[0]
    assert "Approve the bill:" not in messages, "approval requests are cards, not messages"
    assert "Overdue" not in text.split("id='notifications'")[1].split("<h2")[0]
    # A stopped run is listed once under mine, with its date, and its notification is a sentence that names the item.
    bad, bad_state = start_bill(shell, {**SMALL_BILL, "date": f"{date.today().year - 1}-12-15", "po_ref": "PO-1003"})
    number = hub.kernel.get(bad_state["record"])["number"]
    status, text, _ = get(shell, "/inbox", **{"as": "alice"})
    mine = text.split("id='mine'")[1].split("id='others'")[0]
    assert mine.count(f"Stopped: <a") >= 1 and f">{number}</a>" in mine and "stopped on" in mine
    notes = text.split("id='notifications'")[1].split("<h2")[0]
    assert "<b>Stopped:</b>" in notes and f"Open {number}" in notes and "rec:bill" not in plain(notes)


def test_a_decision_lands_on_the_next_item_with_a_status_line(shell):
    """interaction-6, approver-13, first-time-user-6, accessibility-11."""
    hub = shell["hub"]
    a, sa = start_task(shell, "[ux] next item A", "dave")
    b, sb = start_task(shell, "[ux] next item B", "dave")
    status, text, _ = get(shell, "/inbox", **{"as": "dave"})
    order = [m.group(1) for m in re.finditer(r'<li id="r-([0-9a-f]+)"', text)]
    first = next(i for i in order if i in (a, b))
    status, url, text = post(shell, "/decide", {"as": "dave", "run": first, "verdict": "approve", "meaning": "done", "confirm": "1",
                                                "digest": hub.runner.load(first)["seen_digest"]})
    assert status == 200 and "/inbox" in url and "open=" in url
    assert '<p class="status" role="status">Approved TASK-' in text and "it is now done" in text
    assert "<article class='card decide'" in text, "the next waiting item is open"
    assert re.search(r"<title>Approved TASK-\S+ .* - Hub</title>", text)
    assert "Not done" not in text


def test_a_stopped_run_shows_the_problems_and_a_rework_form_that_retries(shell):
    """first-time-user-1, interaction-2, content-1, cli-admin-7: fix the record where the failure is shown."""
    hub = shell["hub"]
    run_id, state = start_bill(shell, {**SMALL_BILL, "date": f"{date.today().year - 1}-12-15", "po_ref": "PO-1003"})
    assert state["status"] == "failed"
    status, text, _ = get(shell, "/record/" + quote(state["record"], safe=":"), **{"as": "alice"})
    stopped = text.split("id='stopped'")[1].split("<h2 id=\"details\"")[0] if "id='stopped'" in text else text.split('id="stopped"')[1]
    assert "Stopped at match the bill to purchase orders" in stopped and "is not open for posting" in stopped
    assert 'action="/retry"' in stopped and 'name="date"' in stopped and 'name="supplier"' in stopped
    date_ctl = stopped.split('name="date"')[0].rsplit("<label", 1)[1]
    assert 'class="f bad"' in date_ctl, "the field the step complained about is marked"
    assert "Fix the record" not in stopped and "its page has the details" not in text
    assert "Cancel this supplier bill" in stopped
    # The inbox card carries the same problems and form.
    status, text, _ = get(shell, "/inbox", **{"as": "alice"})
    assert "is not open for posting" in text and 'action="/retry"' in text
    # Fixing the date on the way retries from the failed step and says what happened.
    status, url, text = post(shell, "/retry", {"as": "alice", "run": run_id, "date": date.today().isoformat(), "note": "date corrected"})
    assert status == 200, text[:300]
    state = hub.runner.load(run_id)
    assert state["status"] == "waiting", state["status"]
    assert hub.kernel.get(state["record"])["fields"]["date"] == date.today().isoformat()
    assert "Retried BILL-" in text and "invoice date changed" in text and "it now waits for" in text
    # A retry that is refused re-renders the rework form with the values kept.
    bad, bad_state = start_bill(shell, {**SMALL_BILL, "date": f"{date.today().year - 1}-12-15", "po_ref": "PO-1003"})
    status, url, text = post(shell, "/retry", {"as": "alice", "run": bad, "amount": "abc", "description": "kept text", "note": "n"})
    assert status == 400 and "must be a number" in text and 'value="abc"' in text and "kept text" in text


def test_duplicate_hint_and_co_assignees(shell):
    """approver-8, approver-7."""
    hub = shell["hub"]
    fields = {"as": "alice", "process": "nonconformance", "title": "[ux] Bent pins on connector J4", "description": "three pins bent", "source": "inspection",
              "severity": "medium", "lot": "C-9", "quantity": "3"}
    r1 = run_of(post(shell, "/start", fields)[1])
    r2 = run_of(post(shell, "/start", fields)[1])
    who = hub.runner.load(r1)["pending"]["assignees"][0]
    status, text, _ = get(shell, "/inbox", **{"as": who})
    n1, n2 = (hub.kernel.get(hub.runner.load(r)["record"])["number"] for r in (r1, r2))
    row = text.split(f'<li id="r-{r1}"')[1].split("</li>")[0]
    assert f"duplicate of {n2}?" in row
    status, url, text = post(shell, "/start", {"as": "alice", "process": "document-control", "title": "[ux] WI-014 Soldering rework", "change_type": "major",
                                               "doc_type": "work-instruction", "body": "rework steps"})
    run_id = run_of(url)
    people = hub.runner.load(run_id)["pending"]["assignees"]
    assert len(people) >= 2, people
    status, text, _ = get(shell, "/inbox", **{"as": people[0]})
    row = text.split(f'<li id="r-{run_id}"')[1].split("</li>")[0]
    assert "also with " + ", ".join(p for p in people if p != people[0]) in row


def test_typed_due_is_shown_on_the_record_and_board(shell):
    """hostile-5: the due date typed on the form is the one the hub shows."""
    hub = shell["hub"]
    run_id, state = start_task(shell, "[ux] overdue task", "carol", due="2026-11-01", priority="high")
    number = hub.kernel.get(state["record"])["number"]
    status, text, _ = get(shell, "/record/" + quote(state["record"], safe=":"), **{"as": "carol"})
    assert "due 1 Nov 2026" in plain(text)
    status, text, _ = get(shell, "/boards", **{"as": "carol", "type": "task"})
    card = text.split(f">{number}</a>")[1].split("</div></div>")[0]
    assert "overdue" in card


# ------------------------------------------------------------- 3. forms
def test_start_forms_mark_required_fields_use_pickers_and_refuse_before_any_record(shell):
    """first-time-user-2, first-time-user-13, content-20, interaction-5, interaction-13/14/16/17/20, accessibility-13."""
    hub = shell["hub"]
    status, text, _ = get(shell, "/", **{"as": "alice"})
    assert "Start something" in text and "<summary>File a supplier bill" in text and ">File a supplier bill</button>" in text
    assert "Start purchase-to-pay" not in text and "Processes you can start" not in text
    form = entry(text, "purchase-to-pay")
    for f in ("supplier", "amount", "currency", "date"):
        assert re.search(rf'<(select|input)[^>]*name="{f}"[^>]*required', form), f
    assert '<select name="supplier" required>' in form and "Acme Metals" in form and "NewCo Tools (not approved)" in form
    assert '<select name="po_ref">' in form and "PO-1002: Acme Metals, 12,000.00 USD" in form
    assert "Invoice date" in form and "(YYYY-MM-DD)" in form and "Amount" in form and "(amount, e.g. 1500.00)" in form
    assert "choose…" in form and "(record number or locator)" not in form and "other fields (JSON object)" not in form
    pr_form = entry(text, "purchase-request")
    assert "Advanced: other fields" in pr_form and "for example {" in pr_form and "manufacturer" in pr_form
    assert 'size="' not in text and 'cols="' not in text and "placeholder=" not in form
    before = len(hub.kernel.query(type_="bill"))
    status, url, text = post(shell, "/start", {"as": "alice", "process": "purchase-to-pay", "json": ""})
    assert status == 400 and "needs supplier, amount, currency and invoice date" in text
    assert len(hub.kernel.query(type_="bill")) == before, "no record is created for a refused Start"
    status, url, text = post(shell, "/start", {"as": "alice", "process": "purchase-to-pay", "supplier": "acme-metals", "amount": "-5", "currency": "USD", "date": "06/11/2026"})
    assert status == 400 and ("cannot be negative" in text or "YYYY-MM-DD" in text) and len(hub.kernel.query(type_="bill")) == before
    assert '<select name="item" required>' in entry(text, "purchase-request")
    task = entry(text, "task")
    assert '<select name="assignee"' in task and '<option value="">choose…</option>' in task
    assert ">Report a nonconformance</button>" in entry(text, "nonconformance")
    status, text, _ = get(shell, "/", **{"as": "dave"})
    receipt = entry(text, "goods-receipt")
    assert '<select name="po" required>' in receipt and "PO-1001" not in receipt, "a receipt names a purchase-order record, not a finance PO"
    assert "Lines received" in receipt and "(one per line: item number, quantity)" in receipt


def test_message_form_needs_a_body_defaults_to_a_person_and_shows_what_was_sent(shell):
    """interaction-8, hostile-19."""
    hub = shell["hub"]
    status, text, _ = get(shell, "/inbox", **{"as": "alice"})
    form = text.split('<h2 id="send">')[1]
    assert '<select name="to" required>' in form and form.index('value="bob"') < form.index("ap-reviewer (automatic reviewer)")
    assert 'name="body" rows="2" required' in form
    status, url, text = post(shell, "/msg", {"as": "alice", "to": "bob", "subject": "[ux] ping", "body": ""})
    assert status == 400 and "Write the message" in text and 'value="[ux] ping"' in text
    status, url, text = post(shell, "/msg", {"as": "alice", "to": "bob", "subject": "[ux] ping", "body": "hello\n\nsecond paragraph"})
    assert status == 200 and "Message sent to bob" in text and "Sent by me" in text and "[ux] ping" in text.split("Sent by me")[1]
    status, url, text = post(shell, "/msg", {"as": "alice", "to": "bob", "subject": "[ux] pigeon", "body": "x", "channel": "carrier-pigeon"})
    assert status == 400 and "by DM or by email" in text


def test_cancel_confirms_with_one_label_and_a_reason(shell):
    """interaction-12."""
    hub = shell["hub"]
    run_id, state = start_bill(shell, {**SMALL_BILL, "date": f"{date.today().year - 1}-12-15", "po_ref": "PO-1003"})
    status, text, _ = get(shell, "/record/" + quote(state["record"], safe=":"), **{"as": "alice"})
    assert text.count("Cancel this supplier bill</button>") == 1 and "Withdraw" not in text and "Cancel the run" not in text
    status, url, text = post(shell, "/cancel", {"as": "alice", "run": run_id, "reason": ""})
    assert status == 400 and "Say why you are cancelling" in text and hub.runner.load(run_id)["status"] == "failed"
    status, url, text = post(shell, "/cancel", {"as": "alice", "run": run_id, "reason": "wrong period"})
    assert status == 400 and "Tick" in text and hub.runner.load(run_id)["status"] == "failed"
    status, url, text = post(shell, "/cancel", {"as": "alice", "run": run_id, "reason": "wrong period", "confirm": "1"})
    assert status == 200 and "Cancelled BILL-" in text and "wrong period" in text and hub.runner.load(run_id)["status"] == "cancelled"


def test_check_out_for_the_author_check_in_needs_a_check_out_and_undo(shell):
    """interaction-11."""
    hub = shell["hub"]
    doc = hub.dms.create(title="[ux] bracket drawing", doc_class="controlled", folder="Engineering/Designs", by="alice", content=b"v1", filename="bracket.md")
    loc = doc["locator"]
    status, text, _ = get(shell, "/document/" + quote(loc, safe=":"), **{"as": "alice"})
    assert "action='/checkout'" in text, "the author may check out even without a create-document rule in the folder"
    status, url, text = post(shell, "/checkin", {"as": "alice", "doc": loc, "note": "no checkout"}, files=[("file", "bracket.md", b"v2")])
    assert status == 400 and "not checked out" in text and len(hub.dms.versions(loc)) == 1
    status, url, text = post(shell, "/checkout", {"as": "alice", "doc": loc})
    assert status == 200 and "checked out to you" in text and 'action="/undo-checkout"' in text
    status, url, text = post(shell, "/checkout", {"as": "dave", "doc": loc})
    assert status == 403 and "cannot change" in text
    status, url, text = post(shell, "/undo-checkout", {"as": "alice", "doc": loc})
    assert status == 200 and "undone" in text and not hub.kernel.get(loc)["fields"].get("checked_out_by")
    assert len(hub.dms.versions(loc)) == 1
    status, text, _ = get(shell, "/document/" + quote(loc, safe=":"), **{"as": "carol"})
    assert "action='/checkout'" in text, "a document controller may check out"


def test_resubmit_marks_required_fields(shell):
    """interaction-18."""
    hub = shell["hub"]
    status, url, text = post(shell, "/start", {"as": "alice", "process": "design-release", "title": "[ux] resubmit marks", "kind": "mechanical"})
    run_id = run_of(url)
    hub.runner.decide(run_id, "bob", "reject", meaning="needs a drawing", payload_digest=hub.runner.load(run_id)["seen_digest"])
    status, text, _ = get(shell, "/inbox", **{"as": "alice"})
    form = text.split('action="/resubmit"')[1].split("</form>")[0]
    assert '<input name="title" value="[ux] resubmit marks" required>' in form and '<select name="kind" required>' in form
    assert "fields marked * are required" in form


# ------------------------------------------------------------- 4. words and identifiers
def test_one_name_per_thing_numbers_not_locators_money_and_dates_in_words(shell):
    """content-3, content-5, content-6, content-10, content-12, content-13, content-18, first-time-user-20, first-time-user-9."""
    hub = shell["hub"]
    run_id, state = start_bill(shell, big_bill(shell["hub"]))
    number = hub.kernel.get(state["record"])["number"]
    status, text, _ = get(shell, "/record/" + quote(state["record"], safe=":"), **{"as": "alice"})
    body = plain(re.sub(r"<details>.*?</details>", "", main_of(text), flags=re.S))
    assert "rec:bill" not in body and "12,000.00 USD" in body and f"{date.today().day} {date.today().strftime('%b')} {date.today().year}" in body
    assert "Supplier bill" in body and "Awaiting: Approve the bill" in body and "approval due" in body
    assert "Why:" not in body and "denied" not in body, "no unasked permission denial on the record header"
    assert "Acme Metals" in body
    assert f"alice created the supplier bill" in body and "Track" not in body and "manifest_digest" not in body
    assert re.search(r'<ol class="crumb"[^>]*>.*Files.*Finance.*Payables.*Supplier bill.*' + number, text, re.S)
    assert "<details><summary>details</summary>" in main_of(text), "JSON behind a disclosure"
    status, text, _ = get(shell, "/run/" + run_id, **{"as": "alice"})
    body = plain(main_of(text))
    assert "policy version 1 (" in body and "pinned to release" not in body and "Gate" not in body and "Cog" not in body.replace("cog", "")
    assert "Automatic reviewer" in body and "Result" in body
    status, text, _ = get(shell, "/", **{"as": "alice"})
    assert "policy version 1 (" in text and hub.releases.current()["digest"][:16] not in plain(main_of(text))
    status, text, _ = get(shell, "/why", **{"as": "dave", "action": "create", "object": "capa", "folder": "Quality/CAPA"})
    body = plain(main_of(text))
    assert "Who may:" in body and "you are: buyer, payables reviewer and staff" in body and "ask: bob or carol" in body
    assert "['buyer'" not in body and "no rule in the release" not in body
    status, text, _ = get(shell, "/", **{"as": "dave"})
    assert "['buyer'" not in text


def test_rail_is_trimmed_by_role_and_boards_open_on_my_work(shell):
    """content-4, content-11, content-14, accessibility-10, visual-15."""
    status, text, _ = get(shell, "/inbox", **{"as": "alice"})
    nav = text.split('<nav class="rail" aria-label="Workspace">')[1].split("</nav>")[0]
    assert "People and roles" not in nav and "Policy versions" not in nav and ">Why<" not in nav
    assert '<a aria-current="page" href="/inbox?as=alice">Inbox</a>' in nav and nav.count("<li>") >= 4
    status, text, _ = get(shell, "/releases", **{"as": "bob"})
    nav = text.split('<nav class="rail" aria-label="Workspace">')[1].split("</nav>")[0]
    assert "People and roles" in nav and "Policy versions" in nav and "<h2 id=\"rail-policy\">Policy</h2>" in nav
    assert '<a aria-current="page" href="/releases?as=bob">Policy versions</a>' in nav
    status, text, _ = get(shell, "/boards", **{"as": "alice"})
    assert "<h1>My work</h1>" in text and "Waiting on others" in text and "Returned or stopped" in text
    assert "awaiting-approve-the-bill" not in plain(text)
    status, text, _ = get(shell, "/boards", **{"as": "alice", "type": "bill"})
    assert "Awaiting: Approve the bill" in text and "Link to this filter" in text and "Saved filter" not in text


def test_chips_are_coloured_by_value_and_types_are_outlined(shell):
    """visual-3, approver-15, visual-19."""
    from hubproto.shell import chip

    assert 'class="chip crit"' in chip("high") and 'class="chip warn"' in chip("medium") and 'class="chip ok"' in chip("low")
    assert 'class="chip ok"' in chip("yes") and 'class="chip crit"' in chip("no") and 'class="chip crit"' in chip("overdue")
    assert 'class="chip crit"' in chip("outside tolerance") and 'class="chip ok"' in chip("within tolerance")
    status, text, _ = get(shell, "/", **{"as": "bob"})
    assert '<span class="tag">controller</span>' in text and '<span class="chip">person</span>' not in text


# ------------------------------------------------------------- 5. accessibility and layout
def test_accessibility_markup(shell):
    """accessibility-2/3/4/6/7/9/10/12/14/15/18, visual-5/8/9/11/16."""
    hub = shell["hub"]
    run_id, state = start_bill(shell, big_bill(shell["hub"]))
    for path, who in (("/", "alice"), ("/inbox?open=" + run_id, "bob"), ("/record/" + quote(state["record"], safe=":"), "alice"), ("/files?folder=Finance/Payables", "alice"), ("/why", "bob")):
        status, text, _ = get(shell, path, **{"as": who})
        assert status == 200, path
        assert text.index('<a class="skip" href="#main">Skip to content</a>') < text.index("<div class=\"top\">"), path
        assert '<main id="main">' in text and '<nav class="rail" aria-label="Workspace"><ul' in text, path
        assert "onchange=" not in text and ">Go</button>" in text, path
        assert 'size="' not in text and 'cols="' not in text, path
        assert "<table><thead><tr><th scope=\"col\">" in text or "<table>" not in text, path
        for m in re.finditer(r'<(input|select|textarea)[^>]*name="([^"]+)"[^>]*>', text):
            if 'type="hidden"' in m.group(0):
                continue
            before = text[: m.start()]
            assert before.rfind("<label") > before.rfind("</label>"), (path, m.group(0)[:80])
    status, text, _ = get(shell, "/inbox", **{"as": "bob"})
    assert "<h3><a href=" in text and "open run</a>" not in text and "open record</a>" not in text
    css = text.split("<style>")[1].split("</style>")[0]
    assert ":focus-visible{outline:3px solid var(--focus)" in css and ".tbl{overflow-x:auto" in css
    assert "@media(max-width:900px)" in css and "nav.rail .tree,nav.rail h2{display:none}" in css
    assert "--fs-s:" in css and "--sp-2:" in css and "input,select,textarea{" in css and "width:100%" in css
    assert "font-size:13.5px" not in css and "font-size:12.5px" not in css
    status, text, _ = get(shell, "/frame/finance-policy", **{"as": "bob"})
    assert "<th scope='row'><a href='#L1' aria-label='line 1'>1</a></th>" in text


def test_colours_meet_aa_contrast_in_both_themes(shell):
    """accessibility-8, accessibility-9, visual-10."""
    def lum(h):
        h = h.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

    def ratio(a, b):
        la, lb = lum(a), lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

    for name, theme in (("light", LIGHT), ("dark", DARK)):
        for fg, bg, need in CONTRAST_PAIRS:
            assert ratio(theme[fg], theme[bg]) >= need, (name, fg, bg, round(ratio(theme[fg], theme[bg]), 2), need)
    status, text, _ = get(shell, "/", **{"as": "alice"})
    css = text.split("<style>")[1].split("</style>")[0]
    assert "color:#fff" not in css.split("@media (prefers-color-scheme: dark)")[0].replace("--top-fg:#ffffff", "").replace("--btn-fg:#ffffff", "").replace("--panel:#ffffff", "").replace("button.secondary{background:var(--secondary);color:#fff}", "")
    assert "background:#6c7a86" not in css


# ------------------------------------------------------------- 6. policy owner surfaces
def test_release_page_shows_the_sentence_diff_and_decisions(shell):
    """cli-admin-15, content-15, content-16, content-17."""
    hub = shell["hub"]
    edit_frame(hub, "finance-policy", "Controllers may approve bills in Finance/Payables.",
               "Controllers may approve bills in Finance/Payables.\nPayables reviewers may create bills in Finance/Payables.", bump="1.3.0")
    meta = release_all(hub)
    status, text, _ = get(shell, "/releases", **{"as": "bob"})
    assert "<h1>Policy versions</h1>" in text and "policy version 2 (" in text and "policy version 1 (" in text and "What changed" in text
    assert meta["digest"] not in plain(main_of(text)).replace(meta["digest"], "", 0) or "<details><summary>digest</summary>" in text
    status, text, _ = get(shell, "/release/" + meta["digest"][:16], **{"as": "bob"})
    assert status == 200 and "<h1>Policy version 2 (" in text and "What changed since policy version 1" in text
    added = text.split("<h3>Added</h3>")[1].split("<h2")[0]
    assert "Payables reviewers may create bills in Finance/Payables." in added and "finance-policy line" in added and "#L" in added
    assert "bob approved on" in added and "carol approved on" in added and "permission" in added and 'class="chip warn">widening' in added
    assert "proposal <code>" in text
    status, text, _ = get(shell, "/frame/finance-policy", **{"as": "bob"})
    assert "Payables reviewers may create bills" in text, "the frame page reads the current file"
    line = int(re.search(r"finance-policy\?as=bob#L(\d+)", added).group(1))
    assert hub.frames.get("finance-policy").line(line) == "Payables reviewers may create bills in Finance/Payables."
    status, text, _ = get(shell, "/admin", **{"as": "alice"})
    assert status == 403 and "for policy owners" in text
    status, text, _ = get(shell, "/admin", **{"as": "bob"})
    assert "<h1>People and roles</h1>" in text and "Automatic reviewers" in text and "payables reviewer" in text and "Cog catalog" in text.split("Developer details")[1]
    status, text, _ = get(shell, "/conformance", **{"as": "carol"})
    assert "<h1>Conformance</h1>" in text and '<th scope="col">Clause</th>' in text and "covered" in text and '{"' not in plain(main_of(text))


def test_escalated_cards_quote_the_escalation_and_the_original_route(shell):
    """approver-6, first-time-user-12, visual-18. Runs last: the tick escalates every overdue item in this hub."""
    hub = shell["hub"]
    item = hub.kernel.create("item", "Inventory/Items", {"name": "[ux] 10k resistor", "mpn": "RC0603FR-0710KL", "manufacturer": "Yageo", "stock": 0,
                                                          "supplier": "acme-metals", "unit_cost": 0.1}, by="alice")
    status, url, text = post(shell, "/start", {"as": "alice", "process": "purchase-request", "title": "[ux] resistors for line 2", "item": item["locator"],
                                               "quantity": "20000", "reason": "line 2 rework"})
    run_id = run_of(url)
    state = hub.runner.load(run_id)
    assert state["status"] == "waiting" and "dave" in state["pending"]["assignees"], state["status"]
    hub.runner.tick(today="2099-01-02")
    state = hub.runner.load(run_id)
    assert "bob" in state["pending"]["assignees"]
    status, text, _ = get(shell, "/inbox", **{"as": "bob"})
    row = text.split(f'<li id="r-{run_id}"')[1].split("</li>")[0]
    assert "escalated" in row and "also with dave" in row
    card = plain(open_card(shell, "bob", run_id))
    assert "escalated to bob on" in card and "Overdue items in purchase-request go to the controller." in card
    assert "originally routed by purchasing-policy line" in card and "go to the buyer." in card
    assert "2099" not in card.split("originally routed")[0].split("escalated to bob")[0]


def test_inbox_survives_odd_field_shapes_from_the_json_box(shell):
    """A title set to a list through the JSON box (hostile-user data) must not 500 anyone's inbox."""
    hub = shell["hub"]
    run_id, state = start_task(shell, "List title later")
    hub.kernel.set_fields(state["record"], {"title": ["a", "list"], "lot": {"odd": 1}}, by="alice")
    status, text, _ = get(shell, "/inbox", **{"as": "carol"})
    assert status == 200 and "Waiting for me" in text and "a list" in text
