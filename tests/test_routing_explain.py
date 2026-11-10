"""How a Gate is routed, shown on the run page (routing.explain), and which automation steps use a model."""

from conftest import make_part  # noqa: F401
from hubproto.routing import explain
from test_change_order_pages import co_page, ok, post, shell  # noqa: F401
from test_shell_views import get, main_of

SRC = lambda line, text: {"frame": "purchasing-policy", "line": line, "text": text}  # noqa: E731
BUNDLE = {
    "routing": [
        {"process": "purchase-request", "step": "approve", "assignee": "role:controller", "mode": "assign",
         "condition": {"field": "amount", "op": ">", "value": 10000.0, "currency": "USD"}, "source": SRC(57, "over 10,000 USD go to the controller.")},
        {"process": "purchase-request", "step": "approve", "assignee": "cog:purchasing-assistant", "mode": "assign",
         "condition": {"field": "amount", "op": "<", "value": 2000.0, "currency": "USD"}, "source": SRC(58, "under 2,000 USD go to the cog.")},
        {"process": "purchase-request", "step": "approve", "assignee": "role:buyer", "mode": "assign", "condition": None,
         "source": SRC(59, "go to the buyer.")},
    ],
    "approval_criteria": [{"approver": "cog:purchasing-assistant", "process": "purchase-request", "max_amount": 1000.0, "currency": "USD",
                           "conditions": ["supplier_approved"]}],
}


def test_routing_is_explained_in_the_order_it_is_tried():
    ok_ctx = {"amount": 300.0, "currency": "USD", "conditions": {"supplier_approved": True}}
    rows = explain(BUNDLE, "purchase-request", "approve", ok_ctx)
    assert [(r["rule"]["assignee"], r["taken"], r["not_needed"]) for r in rows] == [
        ("role:controller", False, False), ("cog:purchasing-assistant", True, False), ("role:buyer", False, True)]
    assert rows[0]["why"] == "300 USD is not over 10,000 USD" and rows[1]["within"]
    # Over the cog's limit (its criteria) though its route applies: the next sentence, the buyer, takes it.
    rows = explain(BUNDLE, "purchase-request", "approve", {**ok_ctx, "amount": 1500.0})
    assert rows[1]["holds"] and not rows[1]["within"] and "amount 1,500 is not under 1,000 USD" in rows[1]["criteria"]
    assert rows[2]["taken"]
    # Another currency: no amount sentence in USD applies.
    rows = explain(BUNDLE, "purchase-request", "approve", {**ok_ctx, "currency": "CAD"})
    assert "the item is in CAD, this sentence in USD" in rows[1]["why"] and rows[2]["taken"]


def test_the_run_page_shows_how_its_approval_was_routed_and_automation_steps_say_if_a_model_reads(shell):
    from conftest import make_part as mk

    hub = shell["hub"]
    item = mk(hub.kernel, "BRK-9", supplier="acme-metals", unit_cost=375.0)
    run_id = hub.runner.submit("purchase-request", {"record": {"title": "brackets", "part": item["locator"], "quantity": 4}}, owner="alice")
    status, text = get(shell, "/run/" + run_id, **{"as": "alice"})
    html = main_of(text)
    assert status == 200 and "is routed</h2>" in html and "Routing tries the policy" in html
    assert "takes it" in html and "1,500 USD" in html
    # The automation steps say whether a model takes part; the change order's compile job too.
    from test_change_order_pages import carry_revision, new_co

    co = new_co(shell)
    carry_revision(shell, co)
    ok(post(shell, "/co-review", {"as": "alice", "co": co["locator"], "choose": "own", "panel": ["management"]}))
    page = co_page(shell, co, "alice")
    assert "Compile the Frame <span class='chip info'" in page and "Check the guards <span class='chip'>no model</span>" in page
    job = next(j for j in hub.automation.of(co["locator"]) if j["automation"] == "compile-frame")
    status, text = get(shell, "/automation/" + job["id"], **{"as": "alice"})
    assert status == 200 and "cogs (model)" in text and "no model" in text


def test_a_local_model_that_times_out_is_asked_once_more_before_the_call_fails(monkeypatch):
    import io
    import json as _json

    from hubproto import model

    calls = []

    def fake_urlopen(req, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return io.BytesIO(_json.dumps({"ok": True}).encode())

    monkeypatch.setattr(model.urllib.request, "urlopen", lambda req, timeout: _Ctx(fake_urlopen(req, timeout)))
    req = model.urllib.request.Request("http://127.0.0.1:11434/api/chat", data=b"{}", method="POST")
    assert model._fetch_json(req, 10) == {"ok": True} and calls == [10, 15.0]
    calls.clear()

    def always(req, timeout):
        calls.append(timeout)
        raise TimeoutError("timed out")
    monkeypatch.setattr(model.urllib.request, "urlopen", lambda req, timeout: always(req, timeout))
    import pytest

    with pytest.raises(TimeoutError):
        model._fetch_json(req, 10)
    assert calls == [10, 15.0], "asked twice, then it fails"


class _Ctx:
    def __init__(self, f):
        self.f = f

    def __enter__(self):
        return self.f

    def __exit__(self, *a):
        return False


def test_approval_criteria_read_a_list_of_conditions():
    from pathlib import Path

    from hubproto.extract import ConventionExtractor
    from hubproto.frames import load_frame_text

    head = '---\ntype: frame [0.2]\nname: x-policy\ndescription: x\nvisibility: internal\nversion: "1"\nscope: department\nmaintainer: bob\n---\n# X\n\n## Approval criteria\n'
    for when in ("the supplier is approved, the part is active and its review passes",
                 "the supplier is approved, the part is active, and its review passes"):
        text = head + f"The purchasing-assistant cog may approve purchase-request purchase requests under 2,000 USD when {when}.\n"
        [r] = ConventionExtractor().extract(load_frame_text(text, Path("x.frame.md"))).rules
        assert r["conditions"] == ["supplier_approved", "part_active", "review_passes"] and r["max_amount"] == 2000.0, r


def test_cog_rails_list_each_cog_once_though_several_sops_name_it(shell):
    import re

    status, text = get(shell, "/settings/rails", **{"as": "bob"})
    names = re.findall(r"<section class='cog-rails'><h2>.*?</span> ([a-z0-9-]+) <span class='muted'>", text)
    assert status == 200 and names and len(names) == len(set(names)), names


JUDGED_BUNDLE = {**BUNDLE, "approval_criteria": [{"approver": "cog:purchasing-assistant", "process": "purchase-request", "max_amount": 2000.0,
                                                  "currency": "USD", "conditions": ["supplier_approved", "review_passes"]}]}


def test_a_cogs_own_review_is_decided_at_the_gate_and_the_table_follows_the_decision():
    ctx = {"amount": 1500.0, "currency": "USD", "conditions": {"supplier_approved": True}}
    rows = explain(JUDGED_BUNDLE, "purchase-request", "approve", ctx)
    assert rows[1]["taken"] and rows[1]["judged"] and rows[1]["criteria"] == [], "its review is not a fact the item lacks"
    rows = explain(JUDGED_BUNDLE, "purchase-request", "approve", ctx, decided_by="purchasing-assistant")
    assert rows[1]["taken"] and not rows[2]["taken"] and rows[2]["not_needed"]
    members = {"members": {"dave": {"roles": ["buyer"]}}}
    rows = explain({**JUDGED_BUNDLE, **members}, "purchase-request", "approve", ctx, decided_by="dave")
    assert rows[2]["taken"] and not rows[1]["taken"] and rows[1]["criteria"] == ["its review did not pass"]


def test_a_cog_whose_authority_the_change_widens_is_left_off_its_review_panel(shell):
    from hubproto import changecontrol as cc
    from test_change_order_pages import NEW, OLD, carry_revision, new_co

    hub = shell["hub"]
    co = new_co(shell, title="Raise the AP cog limit")
    carry_revision(shell, co, new=OLD.replace("2,000", "5,000"))
    ok(post(shell, "/co-review", {"as": "alice", "co": co["locator"], "choose": "own", "panel": ["ap-reviewer", "management"]}))
    rnd = cc.current_round(hub.kernel.get(co["locator"]))
    assert "ap-reviewer" not in {e["who"] for e in rnd["panel"]} and [x["who"] for x in rnd["left_off"]] == ["ap-reviewer"]
    assert "Left off the panel: <b>ap-reviewer</b>, the change widens its own authority" in co_page(shell, co, "alice")
    assert NEW  # the narrowing edit (2,000 -> 1,500) is the other tests'


def test_a_reviews_summary_reads_right_where_a_signature_quotes_it():
    import inspect

    from hubproto import review

    src = inspect.getsource(review)
    assert "listed above" not in src.split("summary = text or")[1].split("\n")[1]


def test_frontmatter_flow_values_read_without_pyyaml(monkeypatch):
    """The Docker image and CI have no PyYAML: the frontmatter subset reads YAML flow style itself, nested, bare or
    quoted, and JSON (which is flow YAML), the way PyYAML would; a malformed value says what is wrong."""
    import pytest

    from hubproto import frames

    monkeypatch.setattr(frames, "yaml", None)
    data, _ = frames.parse_frontmatter('---\nname: x\nworkflow_ids: {gizmo-review: {in-review: review}}\n'
                                       'json: {"a": "b"}\ntags: [x, \'y, z\']\n---\nbody\n')
    assert data["workflow_ids"] == {"gizmo-review": {"in-review": "review"}}
    assert data["json"] == {"a": "b"} and data["tags"] == ["x", "y, z"]
    for bad, why in (("{a b}", "expected ':'"), ("{a: b", "not closed"), ("['x", "not closed")):
        with pytest.raises(frames.FrameError, match=why):
            frames.parse_frontmatter(f"---\nv: {bad}\n---\n")
