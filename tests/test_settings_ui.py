"""The Settings app: every section renders, people edit their own draft (rails, permissions, limits, roles), cogs and
non-members cannot edit, a refusal keeps what was typed, Check shows widenings, Propose makes a proposal."""

import re
import shutil
from urllib.parse import unquote

import pytest

from conftest import ROOT, release_all
from hubproto.app import Hub
from hubproto.rules import rule_key
from hubproto.shell import free_port, serve_in_thread
from test_shell_views import get, post

SECTIONS = ("overview", "people", "permissions", "limits", "rails", "sod", "workflows")


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


@pytest.fixture
def clean(shell):
    for u in ("bob", "carol", "alice"):
        shell["hub"].settings_discard(u)
    yield shell["hub"]
    for u in ("bob", "carol", "alice"):
        shell["hub"].settings_discard(u)


def ap_rail(hub, user="bob"):
    rules = hub.settings_draft(user)["rules"]
    return next(r for r in rules if r["kind"] == "approval_criteria" and r["approver"] == "cog:ap-reviewer")


def test_every_section_renders_for_a_person(shell, clean):
    for s in SECTIONS:
        status, text = get(shell, f"/settings/{s}", **{"as": "bob"})
        assert status == 200, (s, text[:300])
    status, text = get(shell, "/settings/rails", **{"as": "bob"})
    assert "The ap-reviewer cog may approve purchase-to-pay bills under 2,000 USD" in text and "action='/settings-edit'" in text
    assert "Settings the source of truth" in text  # the release still came from the Frames
    status, text = get(shell, "/settings/permissions", **{"as": "bob"})
    assert "Staff may read Policy." in text and "finance-policy line" in text
    status, text = get(shell, "/settings/workflows", **{"as": "bob"})
    assert "## Process: purchase-to-pay" in text and "workflow builder" in text
    status, text = get(shell, "/", **{"as": "bob"})
    assert "/settings" in text  # in the launcher


def test_cogs_read_and_non_members_are_refused(shell, clean):
    status, text = get(shell, "/settings/rails", **{"as": "ap-reviewer"})
    assert status == 200 and "action='/settings-edit'" not in text and "You can read the settings" in text
    status, _, _ = post(shell, "/settings-edit", {"as": "ap-reviewer", "section": "limits", "kind": "threshold", "action": "add", "name": "x-limit", "value": "1", "unit": "USD"})
    assert status == 403
    assert get(shell, "/settings", **{"as": "nobody"})[0] == 403
    status, _, _ = post(shell, "/settings-edit", {"as": "nobody", "section": "limits", "kind": "role", "action": "add", "name": "x"})
    assert status == 403


def test_a_cogs_rail_is_edited_checked_and_proposed(shell, clean):
    hub = shell["hub"]
    rail = ap_rail(hub)
    form = {"as": "bob", "form": "c-x", "section": "rails", "kind": "approval_criteria", "action": "replace", "key": rule_key(rail), "cog": "ap-reviewer",
            "process": "purchase-to-pay", "max_amount": "5000", "currency": "USD", "max_per_day": "20", "max_amount_per_month": "",
            "countersign": "", "conditions": ["match_within_tolerance", "supplier_approved"]}
    status, url, text = post(shell, "/settings-edit", form)
    assert status == 200 and "/settings/rails" in url
    done = unquote(url.split("done=")[1]).replace("+", " ")
    assert done.startswith("Changed: The ap-reviewer cog may approve purchase-to-pay bills under 2,000 USD") and "under 5,000 USD" in done and "at most 20 a day" in done
    assert "in your draft" in text
    status, text = get(shell, "/settings", **{"as": "bob"})
    assert "1 edit(s) in your draft" in text and "action='/settings-propose'" in text
    status, url, text = post(shell, "/settings-check", {"as": "bob"})
    assert status == 200 and "widening" in text and "No problem stops a release" in text
    status, url, text = post(shell, "/settings-propose", {"as": "bob", "reason": "bigger bills for ap-reviewer"})
    assert status == 200 and "/proposal/" in url and "Proposed as #" in unquote(url).replace("+", " ")
    pid = re.search(r"/proposal/([0-9a-f]+)", url).group(1)
    p = hub.proposals.load(pid)
    assert p["source"] == "settings" and any(c.get("widening") for c in p["changes"]) and hub.settings_drafts.load("bob") is None


def test_a_refused_edit_keeps_what_was_typed(shell, clean):
    form = {"as": "carol", "form": "add-threshold", "section": "limits", "kind": "threshold", "action": "add", "name": "night shift limit",
            "value": "lots", "unit": "USD"}
    status, url, text = post(shell, "/settings-edit", form)
    assert status == 400 and "&#x27;lots&#x27; is not a number." in text
    m = re.search(r"<form[^>]*id='f-add-threshold'.*?</form>", text, re.S)
    assert m and "value='night shift limit'" in m.group(0) and "value='lots'" in m.group(0) and "role=\"alert\"" in m.group(0)
    # A duplicate is refused by the core with its own sentence.
    perm = {"as": "carol", "form": "add-perm", "section": "permissions", "kind": "permission", "action": "add", "subject": "staff", "effect": "allow",
            "action_word": "read", "object": "*", "folder": "Policy"}
    status, url, text = post(shell, "/settings-edit", perm)
    assert status == 400 and "already there" in text


def test_roles_limits_permissions_and_discard(shell, clean):
    hub = shell["hub"]
    base = {"as": "alice"}
    assert post(shell, "/settings-edit", {**base, "form": "add-role", "section": "people", "kind": "role", "action": "add", "name": "Shift lead"})[0] == 200
    assert post(shell, "/settings-edit", {**base, "form": "add-perm", "section": "permissions", "kind": "permission", "action": "add", "subject": "shift-lead",
                                          "effect": "allow", "action_word": "approve", "object": "ncr", "folder": "Quality/Nonconformances"})[0] == 200
    t = next(r for r in hub.settings_draft("alice")["rules"] if r["kind"] == "threshold" and r["name"] == "cog-purchase-limit")
    assert post(shell, "/settings-edit", {**base, "form": "t-x", "section": "limits", "kind": "threshold", "action": "replace", "key": rule_key(t),
                                          "name": "cog-purchase-limit", "value": "750", "unit": "USD"})[0] == 200
    said = [e["said"] for e in hub.settings_draft("alice")["edits"]]
    assert said[0] == "added: - shift-lead" and "Shift leads may approve ncrs in Quality/Nonconformances." in said[1]
    assert said[2] == "changed: The cog purchase limit is 500 USD. → The cog purchase limit is 750 USD."
    status, url, text = post(shell, "/settings-discard", {"as": "alice"})
    assert status == 200 and "discarded" in unquote(url).replace("+", " ") and hub.settings_draft("alice")["edits"] == []
