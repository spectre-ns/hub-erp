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
