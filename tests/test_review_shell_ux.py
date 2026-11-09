"""Review of 7 Nov 2026, shell UX fixes: stops explained, cards in words, hotkeys, messenger focus, Assign kept,
compare view, previews, naming, Why not me (ux 1, 5, 6, 7, 8, 9, 11, 12, 14, 16, 17, 18, 19, 20)."""

import re
import shutil
from urllib.parse import quote

import pytest

from conftest import BIG_BILL, ROOT, dated, release_all
from hubproto import wopi
from hubproto.app import Hub
from hubproto.shell import free_port, serve_in_thread
from hubproto.shell.forms import coerce, widget
from hubproto.shell.text import humanize, notification_sentence
from test_shell_views import get, post


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


_po = [4100]


def big_bill(hub) -> dict:
    """A bill over the controller threshold against a fresh purchase order (a PO is fully billed by its first bill)."""
    _po[0] += 1
    hub.kernel.append("po.issued", f"po:PO-{_po[0]}", {"supplier": "acme-metals", "amount": 12000, "currency": "USD", "description": "[review]",
                                                      "received": True}, by="seed")
    hub.kernel._cache = None
    return {**dated(BIG_BILL), "po_ref": f"PO-{_po[0]}"}


def visible(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<script>.*?</script>|<style>.*?</style>|<[^>]+>", " ", html, flags=re.S))


def test_machine_text_is_shown_in_words():
    t = humanize("Due 2026-11-17 at awaiting-verify-the-actions when ['supplier_approved', 'item_known'] up to 1000.0 USD; risk_low")
    assert "17 Nov 2026" in t and "waiting at Verify the actions" in t and "the supplier is approved, the item is known" in t
    assert "1,000 USD" in t and "the risk is low" in t and "risk_low" not in t


def test_a_long_notification_is_cut_at_a_word_and_the_rest_is_kept():
    body = ("Segregation of duties: checked against the document control procedure " * 12).strip()
    html = notification_sentence({"kind": "auto-decided", "subject": "qa-manager approved DOC-1", "body": body})
    head = visible(html.split("<details")[0]).strip()
    assert head.endswith("…") and "<details class='more'><summary>more</summary>" in html
    shown = head.split("approved DOC-1 ", 1)[1][:-1]
    assert body.startswith(shown) and body[len(shown)] == " "  # cut at a word, not inside one
    assert "Decided by a cog" in html


def test_capa_actions_are_lines_not_json():
    rows = coerce("actions", "list", "Contain and investigate; bob; 2026-11-20\nRetrain the line; carol;; done")
    assert rows == [{"action": "Contain and investigate", "owner": "bob", "due": "2026-11-20", "status": "open"},
                    {"action": "Retrain the line", "owner": "carol", "status": "done"}]
    html = widget("actions", "list", [{"action": "Contain and investigate", "owner": "bob", "status": "open"}])
    assert "{" not in html and "Contain and investigate; bob; ; open" in html and "one per line" in html


def test_a_stop_nobody_may_fix_with_a_field_says_who_can(shell):
    hub = shell["hub"]
    # A purchase request filed by the only buyer: segregation of duties leaves nobody to approve it.
    from conftest import make_part

    item = make_part(hub.kernel, "WID-1", title="[review] widget", unit_cost=1)
    status, url, text = post(shell, "/start", {"as": "dave", "process": "purchase-request", "title": "[review] widgets", "part": item["locator"],
                                               "quantity": "5000", "supplier": "acme-metals", "reason": "line 3"})
    run_id = url.split("/run/")[1].split("?")[0]
    state = hub.runner.load(run_id)
    if state["status"] != "failed":
        pytest.skip(f"the purchase request is {state['status']} under this policy")
    page = get(shell, "/inbox", **{"as": "dave"})[1]
    assert "No field change fixes this" in page and "Retry now" in page and "Fix and retry</button>" not in page.split("No field change fixes this")[1][:3000]


def test_a_purchase_request_names_its_supplier(shell):
    status, url, text = post(shell, "/start", {"as": "dave", "process": "purchase-request", "title": "[review] no supplier", "quantity": "5"})
    assert status == 400 and "supplier" in visible(text).lower()


def test_ops_hotkeys_focus_and_confirm_and_can_be_switched_off(shell):
    hub = shell["hub"]
    content, mime = wopi.blank_document("docx")
    doc = hub.dms.create(title="[review] WI", doc_class="working", folder="Tasks", by="carol", content=content, filename="wi.docx", mime=mime, doc_type="wi")
    page = get(shell, "/document/" + quote(doc["locator"], safe=":"), **{"as": "carol"})[1]
    assert "data-hotkeys-toggle" in page and "hk-armed" in page and "el.click()" not in page and "Enter confirms" in page
    assert 'name="change_type" required' in page  # Send for approval asks minor or major
    # Only cogs that follow the item's SOP (derived: its rules name them or their roles): not the payables reviewer.
    assert "Ask ap-reviewer to review" not in page and "Ask doc-reviewer to review" in page


def test_the_messenger_never_takes_the_focus_on_page_load(shell):
    page = get(shell, "/", **{"as": "carol"})[1]
    assert "open(true,true)" in page and "if(load('open')==='1')open(true);" not in page


def test_assign_without_an_instruction_comes_back_with_the_form(shell):
    hub = shell["hub"]
    doc = hub.dms.create(title="[review] assign me", doc_class="working", folder="Quality/Controlled Documents", by="carol", content=b"x",
                         filename="a.md", mime="text/markdown", doc_type="document")
    status, url, text = post(shell, "/assign", {"as": "carol", "record": doc["locator"], "to": "qa-manager", "instruction": ""})
    assert status == 400 and "<details class='inline restart' open>" in text and "Say what qa-manager should do" in text
    assert "<option value='qa-manager' selected>" in text


def test_a_cogs_change_links_to_what_changed(shell):
    hub = shell["hub"]
    doc = hub.dms.create(title="[review] compare", doc_class="working", folder="Quality/Controlled Documents", by="carol",
                         content=b"# One\nshall do it\n", filename="c.md", mime="text/markdown", doc_type="document")
    hub.dms.add_version(doc["locator"], b"# One\nmust do it\n", by="hub", filename="c.md", mime="text/markdown", note="edited by qa-manager")
    page = get(shell, "/document/" + quote(doc["locator"], safe=":"), **{"as": "carol"})[1]
    assert "what changed</a>" in page
    status, text = get(shell, "/compare/" + quote(doc["locator"], safe=":"), **{"as": "carol", "a": 1, "b": 2})
    assert status == 200 and "-shall do it" in text and "+must do it" in text and "(version 1)" in text
    assert get(shell, "/compare/" + quote(doc["locator"], safe=":"), **{"as": "dave", "a": 1, "b": 9})[0] in (403, 404)


def test_library_previews_are_text_not_markdown_source(shell):
    hub = shell["hub"]
    hub.dms.create(title="[review] snapshot", doc_class="working", folder="Quality/Controlled Documents", by="carol",
                   content=b"# NCR-1 (ncr)\nState: **closed**\nPolicy release: `e3fb5b99aad2c079`\n", filename="s.md", mime="text/markdown",
                   doc_type="document", fields={"text": "# NCR-1 (ncr)\nState: **closed**\nPolicy release: `e3fb5b99aad2c079`"})
    page = get(shell, "/files", **{"as": "carol"})[1]
    tiles = page.split("Recent documents")[1]
    assert "**closed**" not in tiles and "e3fb5b99aad2c079" not in tiles


def test_one_name_per_thing(shell):
    page = get(shell, "/inbox", **{"as": "alice"})[1]
    assert "automatic reviewer" not in page and "(cog)" in page
    assert "<option value=\"dm\" selected>direct message</option>" in page and page.count(">dm<") == 0
    home = get(shell, "/", **{"as": "dave"})[1]
    assert re.search(r"You are .*,.* and ", visible(home))
    people = get(shell, "/people", **{"as": "bob"})[1]
    assert " 1 decisions" not in visible(people)


def test_why_is_in_everyones_rail_and_answers_for_an_item(shell):
    hub = shell["hub"]
    page = get(shell, "/", **{"as": "dave"})[1]
    assert 'href="/why?as=dave"' in page
    run_id = hub.runner.submit("purchase-to-pay", {"record": big_bill(hub)}, owner="alice")
    state = hub.runner.load(run_id)
    if state["status"] != "waiting":
        pytest.skip("the bill is not waiting under this policy")
    rec = hub.kernel.get(state["record"])
    record_page = get(shell, "/record/" + quote(rec["locator"], safe=":"), **{"as": "dave"})[1]
    assert "Why not me?" in record_page
    why = get(shell, "/why", **{"as": "dave", "record": rec["locator"]})[1]
    assert "waits at" in why and "for <b>bob</b>" in why and "dave may not decide it now" in why


def test_approvals_have_their_own_row_on_the_run_page(shell):
    hub = shell["hub"]
    run_id = hub.runner.submit("purchase-to-pay", {"record": big_bill(hub)}, owner="alice")
    state = hub.runner.load(run_id)
    if state["status"] != "waiting":
        pytest.skip("the bill is not waiting under this policy")
    page = visible(get(shell, "/run/" + run_id, **{"as": "alice"})[1])
    title = state["pending"]["title"]
    assert re.search(re.escape(title) + r" .*waiting", page) and "waiting for approval" not in page
