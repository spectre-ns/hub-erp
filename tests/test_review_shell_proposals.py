"""Review of 7 Nov 2026, shell: policy proposals (ux 2, 3, 4; security 11). The second owner is told; changes read as
sentences with what they replace; each change is decided on its own; Release is offered only when it will work."""

import re
import shutil

import pytest

from conftest import ROOT, release_all
from hubproto.app import Hub
from hubproto.rules import rule_key
from hubproto.shell import free_port, serve_in_thread
from test_shell_views import get, post


@pytest.fixture
def shell(tmp_path):
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    hub = Hub(tmp_path)
    hub.kernel.seed()
    release_all(hub)
    port = free_port()
    server = serve_in_thread(hub, "127.0.0.1", port)
    yield {"hub": hub, "root": tmp_path, "base": f"http://127.0.0.1:{port}", "port": port, "server": server}
    server.shutdown()
    server.server_close()


def propose_rail(hub, by="bob", amount=5000.0):
    rules = hub.settings_draft(by)["rules"]
    ap = next(r for r in rules if r["kind"] == "approval_criteria" and r["approver"] == "cog:ap-reviewer")
    hub.settings_edit(by, {"action": "replace", "key": rule_key(ap), "rule": {**{k: v for k, v in ap.items() if k != "source"}, "max_amount": amount}})
    return hub.settings_propose(by, reason="mid-size bills")


def test_the_second_owner_finds_the_proposal_in_her_inbox_and_the_list(shell):
    p = propose_rail(shell["hub"])
    inbox = get(shell, "/inbox", **{"as": "carol"})[1]
    assert f"Review policy proposal #{p['number']}" in inbox and "need your decision" in inbox
    listing = get(shell, "/proposals", **{"as": "carol"})[1]
    assert f"#{p['number']}" in listing and "your decision" in listing
    assert "Policy proposals (1 open)" in get(shell, "/releases", **{"as": "dave"})[1]


def test_changes_read_as_sentences_with_what_they_replace(shell):
    p = propose_rail(shell["hub"])
    page = get(shell, "/proposal/" + p["id"], **{"as": "carol"})[1]
    main = page.split('<main id="main">')[1]
    assert "Changed</b>: The ap-reviewer cog may approve purchase-to-pay bills under 5,000 USD" in main
    assert "was: The ap-reviewer cog may approve purchase-to-pay bills under 2,000 USD" in main
    visible = re.sub(r"<script>.*?</script>|<[^>]+>", " ", main, flags=re.S)
    assert "add:" not in visible and "remove:" not in visible and "['" not in visible and "2000.0" not in visible
    assert "widening: two owners" in main


def test_each_change_is_decided_on_its_own_and_release_is_offered_only_when_ready(shell):
    hub = shell["hub"]
    p = propose_rail(hub)
    page = get(shell, "/proposal/" + p["id"], **{"as": "bob"})[1]
    assert "Record my decisions" in page and "action='/proposal-release'" not in page
    cid = re.search(r"name='d_([^']+)' value='approve'", page).group(1)
    status, url, text = post(shell, "/proposal-review", {"as": "bob", "proposal": p["id"], "verdict": "each", f"d_{cid}": "approve",
                                                           "meaning": "checked the new limit"})
    assert status == 200 and "You approved" in text
    page = get(shell, "/proposal/" + p["id"], **{"as": "bob"})[1]
    assert "needs 2 approvals (has 1)" in page and "action='/proposal-release'" not in page
    status, url, text = post(shell, "/proposal-review", {"as": "carol", "proposal": p["id"], "verdict": "each", f"d_{cid}": "approve",
                                                           "meaning": "second owner"})
    assert status == 200
    page = get(shell, "/proposal/" + p["id"], **{"as": "bob"})[1]
    assert "action='/proposal-release'" in page
    status, url, text = post(shell, "/proposal-release", {"as": "bob", "proposal": p["id"]})
    assert status == 200 and "/release/" in url


def test_a_proposal_overtaken_by_another_release_says_so_and_offers_no_release(shell):
    hub = shell["hub"]
    a = propose_rail(hub, by="bob", amount=4000.0)
    rules = hub.settings_draft("carol")["rules"]
    th = next(r for r in rules if r["kind"] == "threshold" and r["name"] == "match-tolerance")
    hub.settings_edit("carol", {"action": "replace", "key": rule_key(th), "rule": {**{k: v for k, v in th.items() if k != "source"}, "value": 1.0}})
    b = hub.settings_propose("carol")
    for who in ("bob", "carol"):
        hub.review(b["id"], who, approve_all=True)
        hub.review(a["id"], who, approve_all=True)
    hub.release(b["id"], "bob")
    page = get(shell, "/proposal/" + a["id"], **{"as": "bob"})[1]
    # Either it stays open and says the policy changed under it, or the hub replaced it with a fresh proposal and says so.
    low = page.lower()
    assert ("the policy in force changed after this proposal was made" in low or "a newer proposal replaced this one" in low)
    assert "action='/proposal-release'" not in page
    status, url, text = post(shell, "/proposal-release", {"as": "bob", "proposal": a["id"]})
    assert status == 400 and ("cannot be released yet" in text or "replaced by a newer one" in text)
    assert a["id"] not in text.split('<main id="main">')[1].split("</main>")[0]


def test_the_frame_page_says_a_change_is_proposed_and_names_who_wrote_the_draft(shell):
    hub = shell["hub"]
    draft = hub.frame_draft("purchasing-policy", by="bob")
    text = hub.draft_text(draft).replace("The cog purchase limit is 500 USD.", "The cog purchase limit is 600 USD.")
    hub.dms.add_version(draft["locator"], text.encode(), by="bob", filename="p.md", mime="text/markdown", note="edit")
    p = hub.compile(overrides={"purchasing-policy": text}, drafts={"purchasing-policy": draft["locator"]})
    assert f"proposal #{p['number']}" in get(shell, "/frame/purchasing-policy", **{"as": "dave"})[1]
    assert "written by bob" in get(shell, "/proposal/" + p["id"], **{"as": "carol"})[1]
