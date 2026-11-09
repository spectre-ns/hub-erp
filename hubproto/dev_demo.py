"""Demo data for a development hub (``hubproto dev serve --seed demo``), made with the hub's own APIs.

Run by ``hubproto.cli_dev`` as ``python -c <this file's text> ROOT`` under the code of the worktree being served (which
may be another branch, without this module), so it imports nothing from ``cli_dev`` and stays standalone. It prints one
line per item and, last, ``demo: {json}`` with what it made. An item the branch's APIs cannot make is reported and
skipped; the exit code is 1 when nothing could be made.

* an open nonconformance waiting for carol (the quality manager dispositions it);
* a bill under the ap-reviewer cog's limit (the cog approves it within its criteria and the ledger posts it);
* an open change order prepared by alice, carrying one revision of the finance policy (drafted by carol, a document
  controller: alice owns no SOP, so she may not start a revision herself) that lowers the ap-reviewer limit.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

AP_OLD = "The ap-reviewer cog may approve purchase-to-pay bills under 2,000 USD"
AP_NEW = "The ap-reviewer cog may approve purchase-to-pay bills under 1,500 USD"


def ncr(hub) -> str:
    run_id = hub.runner.submit("nonconformance", {"record": {
        "title": "Scratched housing on lot 42", "description": "Deep scratch across the housing face; possible safety risk at the edge.",
        "severity": "high", "source": "inspection"}}, owner="alice")
    state = hub.runner.load(run_id)
    rec = hub.kernel.get(state["record"])
    who = ", ".join((state.get("pending") or {}).get("assignees") or []) or state["status"]
    print(f"nonconformance {rec['number']} waiting for {who}")
    return rec["number"]


def bill(hub) -> str:
    run_id = hub.runner.submit("purchase-to-pay", {"record": {
        "supplier": "acme-metals", "amount": 1500, "currency": "USD", "date": date.today().isoformat(), "po_ref": "PO-1001",
        "description": "Aluminium stock"}}, owner="alice")
    state = hub.runner.load(run_id)
    rec = hub.kernel.get(state["record"])
    print(f"bill {rec['number']} for 1,500 USD (under the ap-reviewer limit): {rec['state']}")
    return rec["number"]


def change_order(hub) -> str:
    from hubproto import eco, sopdocs

    co = hub.kernel.create("change-order", "Quality/Changes", {
        "title": "Lower the AP cog limit", "description": "Bills from 1,500 USD up should get a person's review.",
        "requested_by": "alice"}, by="alice")
    draft = eco.add_sop(hub, co["number"], "finance-policy", by="carol")
    text = sopdocs.frame_text(hub, draft)
    _, body = text.split("\n---\n", 1)
    if AP_OLD not in body:
        raise ValueError("the finance policy no longer states the ap-reviewer limit this demo lowers")
    hub.dms.add_version(draft["locator"], sopdocs.to_docx(body.replace(AP_OLD, AP_NEW)), by="carol",
                        filename=f"{draft['fields']['sop_name']}.docx", note="lower the ap-reviewer limit to 1,500 USD",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    print(f"change order {co['number']} prepared by alice, carrying {draft['number']} (finance-policy rev "
          f"{draft['fields'].get('revision')})")
    return co["number"]


def main(root: str) -> int:
    from hubproto.app import Hub

    hub = Hub(Path(root))
    made: dict[str, str] = {}
    for key, fn in (("ncr", ncr), ("bill", bill), ("change_order", change_order)):
        try:
            made[key] = fn(hub)
        except Exception as e:  # another branch's APIs may differ: say so and carry on with the rest
            print(f"skipped the demo {key.replace('_', ' ')}: {type(e).__name__}: {e}")
    queue = getattr(hub, "queue", None)
    if queue is not None and hasattr(queue, "drain"):
        try:
            queue.drain()
        except Exception:
            pass
    print("demo: " + json.dumps(made, sort_keys=True))
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
