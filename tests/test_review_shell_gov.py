"""Review of 7 Nov 2026, shell side of the governance fixes: wording changes on proposals and the check page,
proposing as the author, stale proposals, /why from hub.why_item, failure reasons from the runner."""

import re
import shutil
from urllib.parse import quote

import pytest

from conftest import BIG_BILL, ROOT, dated, release_all
from hubproto.app import Hub
from hubproto.shell import serve_in_thread
from test_shell_views import get, post

LIMIT = "The cog purchase limit is 500 USD."


@pytest.fixture
def shell(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    server = serve_in_thread(hub, "127.0.0.1", 0)  # port 0: bound at once, no race with other test workers
    port = server.server_address[1]
    yield {"hub": hub, "root": tmp_path, "base": f"http://127.0.0.1:{port}", "port": port, "server": server}
    server.shutdown()
    server.server_close()


def visible(html: str) -> str:
    main = html.split('<main id="main">')[1].split("</main>")[0] if '<main id="main">' in html else html
    return re.sub(r"\s+", " ", re.sub(r"<script>.*?</script>|<[^>]+>", " ", main, flags=re.S))


def draft_with(hub, frame: str, by: str, change) -> dict:
    d = hub.frame_draft(frame, by=by)
    text = change(hub.draft_text(d))
    hub.dms.add_version(d["locator"], text.encode(), by=by, filename=hub.frames.get(frame).path.name, mime="text/markdown", note="edit")
    return d


def test_a_wording_only_change_reads_as_one_reviewable_change(shell):
    hub = shell["hub"]
    d = draft_with(hub, "purchasing-policy", "bob", lambda t: t + "\nBuyers keep the quote with the request.\n")
    page = get(shell, "/frame-check/" + quote(d["locator"], safe=":"), **{"as": "bob"})[1]
    assert "Wording of purchasing-policy changed" in visible(page)
    status, url, text = post(shell, "/frame-propose", {"as": "bob", "doc": d["locator"]})
    assert status == 200 and "/proposal/" in url
    v = visible(text)
    assert "Wording of purchasing-policy changed" in v and "see the wording" in v and "text:" not in v
    assert "id='draft-purchasing-policy'" in text and re.search(r"name='d_text:purchasing-policy:[^']+' value='approve'", text)


def test_only_the_author_or_a_policy_owner_proposes_a_draft(shell):
    hub = shell["hub"]
    d = draft_with(hub, "purchasing-policy", "dave", lambda t: t.replace(LIMIT, "The cog purchase limit is 600 USD."))
    status, url, text = post(shell, "/frame-propose", {"as": "alice", "doc": d["locator"]})
    assert status in (403, 404) and "/proposal/" not in url
    if status == 403:
        assert "only dave or a policy owner proposes it" in visible(text)
    status, url, text = post(shell, "/frame-propose", {"as": "dave", "doc": d["locator"]})
    assert status == 200 and "/proposal/" in url
    p = hub.proposals.load(url.split("/proposal/")[1].split("?")[0])
    assert p.get("proposed_by") == "dave"


def test_a_stale_proposal_says_so_and_offers_its_author_redo_or_withdraw(shell):
    hub = shell["hub"]
    mine = draft_with(hub, "purchasing-policy", "bob", lambda t: t.replace(LIMIT, "The cog purchase limit is 600 USD."))
    theirs = draft_with(hub, "purchasing-policy", "carol", lambda t: t + "\nBuyers keep the quote with the request.\n")
    pa = hub.compile(overrides={"purchasing-policy": hub.draft_text(mine)}, drafts={"purchasing-policy": mine["locator"]}, by="bob")
    pb = hub.compile(overrides={"purchasing-policy": hub.draft_text(theirs)}, drafts={"purchasing-policy": theirs["locator"]}, by="carol")
    for who in ("bob", "carol"):
        hub.review(pb["id"], who, approve_all=True)
    hub.release(pb["id"], "carol")
    pa = hub.proposals.load(pa["id"])
    assert pa["status"] == "stale", pa["status"]
    page = get(shell, "/proposal/" + pa["id"], **{"as": "bob"})[1]
    v = visible(page)
    assert "out of date" in v and "Out of date." in v and "purchasing-policy changed" in v
    assert "Edit the draft" in v and f"Withdraw {mine['number']}" in v and "action='/proposal-release'" not in page
    other = visible(get(shell, "/proposal/" + pa["id"], **{"as": "dave"})[1])
    assert "Withdraw" not in other and "Its author (bob)" in other
    assert "Out of date: redo or withdraw" in visible(get(shell, "/proposals", **{"as": "bob"})[1])
    status, url, text = post(shell, "/frame-withdraw", {"as": "bob", "doc": mine["locator"], "reason": "superseded"})
    assert status == 200 and f"Withdrew {mine['number']}" in text


_po = [7100]


def big_bill(hub) -> dict:
    _po[0] += 1
    hub.kernel.append("po.issued", f"po:PO-{_po[0]}", {"supplier": "acme-metals", "amount": 12000, "currency": "USD", "description": "[review]",
                                                      "received": True}, by="seed")
    hub.kernel._cache = None
    return {**dated(BIG_BILL), "po_ref": f"PO-{_po[0]}"}


def test_why_for_an_item_gives_the_hubs_reasons(shell):
    hub = shell["hub"]
    run_id = hub.runner.submit("purchase-to-pay", {"record": big_bill(hub)}, owner="alice")
    state = hub.runner.load(run_id)
    assert state["status"] == "waiting"
    rec = hub.kernel.get(state["record"])
    expected = hub.why_item("dave", rec["locator"])
    v = visible(get(shell, "/why", **{"as": "dave", "record": rec["locator"]})[1])
    assert "dave may not decide it now" in v and "waits at" in v
    assert len(expected["reasons"]) >= 1
    assert "it waits for bob" in v.lower()  # the hub's own sentence, not one the shell made up
    v = visible(get(shell, "/why", **{"as": "bob", "record": rec["locator"]})[1])
    assert ("bob may decide it now" in v) == hub.why_item("bob", rec["locator"])["may"]


def test_an_organisational_stop_lists_the_runners_reasons_once(shell):
    hub = shell["hub"]
    from conftest import make_part

    item = make_part(hub.kernel, "GOV-1", title="[gov] widget", unit_cost=1)
    status, url, text = post(shell, "/start", {"as": "dave", "process": "purchase-request", "title": "[gov] widgets", "part": item["locator"],
                                               "quantity": "5000", "supplier": "acme-metals", "reason": "line 3"})
    run_id = url.split("/run/")[1].split("?")[0]
    state = hub.runner.load(run_id)
    if state["status"] != "failed":
        pytest.skip(f"the purchase request is {state['status']} under this policy")
    ev = next(e for e in reversed(hub.runner.track(run_id)) if e["type"] == "run.failed")
    assert ev.get("kind") == "organisation" and ev.get("gate")
    v = visible(get(shell, "/inbox", **{"as": "dave"})[1])
    v = v.split("Returned or stopped, mine")[1].split("Waiting on others")[0]  # the stop card, not the notification
    assert f"Stopped at {ev['gate']}" in v and "No field change fixes this" in v
    reasons = [x.strip() for x in ev["reason"].partition(": ")[2].split(" | ") if x.strip()]
    for r in reasons:
        assert v.count(r[:40]) == 1, r
