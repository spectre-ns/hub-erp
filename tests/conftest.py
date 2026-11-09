import os
import shutil
from pathlib import Path

import pytest

from hubproto.app import Hub

ROOT = Path(__file__).resolve().parent.parent

# Tests run without models: the deterministic stand-in answers for every cog (never in a real hub).
os.environ.setdefault("HUBPROTO_ALLOW_STAND_IN", "1")


@pytest.fixture
def hub(tmp_path) -> Hub:
    """A fresh hub with a copy of the repository Frames and a seeded kernel."""
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    return h


@pytest.fixture
def today(monkeypatch):
    """The day the runner sees, fixed for this test: due dates and the dates a test expects come from one clock, so a
    test that runs across midnight cannot see two days (bind no date at import)."""
    import hubproto.runner as runner_module
    from datetime import date as real_date

    day = real_date.today()

    class Day(real_date):
        @classmethod
        def today(cls):
            return day
    monkeypatch.setattr(runner_module, "date", Day)
    return day


def release_all(hub: Hub, reviewers=("bob", "carol"), effective_from=None, now=None) -> dict:
    """Compile, approve every change with two policy owners, and release."""
    proposal = hub.compile(now=now)
    for r in reviewers:
        hub.review(proposal["id"], r, approve_all=True)
    return hub.release(proposal["id"], reviewers[0], effective_from=effective_from, now=now)


@pytest.fixture
def released(hub) -> Hub:
    release_all(hub)
    return hub


def edit_frame(hub: Hub, name: str, old: str, new: str, bump: str | None = None) -> None:
    frame = hub.frames.get(name)
    text = frame.path.read_text()
    assert old in text, old
    text = text.replace(old, new)
    if bump:
        text = text.replace(f'version: "{frame.version}"', f'version: "{bump}"')
    frame.path.write_text(text)
    hub.frames = type(hub.frames)(hub.frames.root)


SMALL_BILL = {"supplier": "acme-metals", "amount": 1500, "currency": "USD", "date": "{date}", "po_ref": "PO-1001",
              "description": "Aluminium stock"}
BIG_BILL = {"supplier": "acme-metals", "amount": 12000, "currency": "USD", "date": "{date}", "po_ref": "PO-1002",
            "description": "CNC fixtures"}
MID_BILL = {"supplier": "acme-metals", "amount": 4000, "currency": "USD", "date": "{date}", "po_ref": "PO-1003",
            "description": "Calibration weights"}


def dated(bill: dict) -> dict:
    from datetime import date

    return {**bill, "date": date.today().isoformat()}


def seen(runner, run_id: str) -> str | None:
    """The digest a person quotes with a decision: what the card shows right now."""
    try:
        state = runner.load(run_id)
    except Exception:
        return None
    return state.get("seen_digest")


def make_part(k, mpn: str, *, supplier: str | None = "acme-metals", unit_cost=0.5, stock: float = 0, manufacturer: str = "Acme",
              title: str | None = None, state: str = "active", **fields) -> dict:
    """A part on the approved manufacturer list, with a supplier part (priced at unit_cost when given) and, when stock is
    given, a lot of that quantity in Stores. The test-side counterpart of the old inventory item."""
    from hubproto import inventory

    part = inventory.create_part(k, title=title or mpn, mpn=mpn, manufacturer=manufacturer, supplier=supplier, unit_cost=unit_cost,
                                 by="alice", state=state, **fields)
    if stock:
        inventory.receive(k, part=part, quantity=stock, location=inventory.location_of_kind(k, "MAIN", "store")["locator"], by="alice")
    return part
