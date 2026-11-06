import shutil
from pathlib import Path

import pytest

from hubproto.app import Hub

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def hub(tmp_path) -> Hub:
    """A fresh hub with a copy of the repository Frames and a seeded kernel."""
    shutil.copytree(ROOT / "frames", tmp_path / "frames")
    h = Hub(tmp_path)
    h.kernel.seed()
    return h


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
