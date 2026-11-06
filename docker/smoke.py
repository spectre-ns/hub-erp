"""End-to-end smoke run against a bootstrapped hub: user needs -> requirements -> design review ->
part and items -> purchase requests -> approved supplier -> purchase order -> goods receipt -> bill -> ledger.

Run inside the container:  docker compose exec -T hub python - < docker/smoke.py
Uses the release in force and the CAD worker the hub is configured with (real or fake)."""

import base64
import os
import sys
from datetime import date
from pathlib import Path

from hubproto.app import Hub
from hubproto.runner import RunError

root = Path(os.environ.get("HUBPROTO_ROOT", "/hub"))
hub = Hub(root)
k = hub.kernel
ok = True


def check(cond, msg):
    global ok
    print(("  PASS " if cond else "  FAIL ") + msg)
    ok = ok and bool(cond)


def seen(run_id):
    """What the card shows now: a person's decision must quote it."""
    return hub.runner.load(run_id).get("seen_digest")


def events(run_id, *types):
    return [e for e in hub.runner.track(run_id) if e["type"] in types]


print("release in force:", (hub.releases.current() or {}).get("digest", "none")[:12])
tag = date.today().strftime("%H%M%S") if False else os.urandom(2).hex()
sch = (root / "examples" / "sensor.kicad_sch").read_text()
fcstd = (root / "examples" / "box.FCStd").read_bytes()

# Inventory: one BOM item known, from an approved supplier, with a unit cost.
known_item = k.create("item", "Inventory/Items", {"name": f"10k 0603 {tag}", "mpn": "RC0603FR-0710KL", "manufacturer": "Yageo",
                                                  "stock": 1, "supplier": "acme-metals", "unit_cost": 0.02, "currency": "USD"}, by="alice")

print("1. design engineer submits the design with user needs and CAD")
run1 = hub.runner.submit("design-release", {"record": {
    "title": f"Sensor board {tag}", "kind": "electrical", "change_class": "3",
    "user_needs": ["The sensor must read temperature to within 0.5 C between -20 and 85 C.",
                   {"text": "The board must survive a 1 m drop onto concrete.", "kind": "performance", "source": "customer"}],
    "attachments": [{"name": "sensor.kicad_sch", "content": sch, "doc_class": "controlled", "doc_type": "schematic"},
                    {"name": "box.FCStd", "content_b64": base64.b64encode(fcstd).decode(), "doc_class": "controlled", "doc_type": "model"}]}},
    owner="alice")
s1 = hub.runner.load(run1)
check(s1["status"] == "waiting", f"design run waits for review (status {s1['status']}) {[e.get('reason') for e in events(run1, 'run.failed')]}")
design = k.get(s1["record"])
reqs = [k.get(r) for r in design["fields"].get("requirements", [])]
check(len(reqs) == 2 and all(r["state"] == "awaiting-accept-the-requirement" for r in reqs), f"{len(reqs)} requirements captured, awaiting acceptance")
check(design["fields"].get("requirements_accepted") is False and design["fields"].get("risk") == "high", "design risk high while requirements unaccepted")
exports = design["fields"].get("exports") or {}
checks = design["fields"].get("checks") or {}
print("     exports:", {kk: (vv if isinstance(vv, str) else type(vv).__name__) for kk, vv in exports.items()} if isinstance(exports, dict) else exports)
print("     checks:", checks)
print("     bom lines:", len(design["fields"].get("bom_lines") or []), "resolved:", design["fields"].get("bom_resolved"), "unresolved:", design["fields"].get("bom_unresolved"))
check(design["fields"].get("bom_lines"), "BOM extracted from the schematic by the CAD worker")

print("2. design authority accepts both requirements at the Gate")
for r in reqs:
    rr = hub.runner.run_for_record(r["locator"])
    try:
        hub.runner.decide(rr["id"], "alice", "approve", meaning="smoke", payload_digest=seen(rr["id"]))
        check(False, "owner must not accept their own requirement")
    except RunError as e:
        check("not an assignee" in str(e) or "segregation" in str(e), f"owner refused: {e}")
    hub.runner.decide(rr["id"], "bob", "approve", meaning="requirement accepted",
                      fields={"acceptance_criteria": "Verified by the acceptance test procedure", "verification_method": "test"}, payload_digest=seen(rr["id"]))
    check(k.get(r["locator"])["state"] == "accepted", f"{r['number']} accepted")
design = k.get(design["locator"])
check(design["fields"].get("requirements_accepted") is True, "acceptance refreshed the waiting design's trace")

print("3. design authority approves the review: part released, items created, shortages requested")
hub.runner.decide(run1, "bob", "approve", meaning="design package reviewed and released", payload_digest=seen(run1))
design = k.get(design["locator"])
check(design["state"] == "released", f"design {design['number']} state {design['state']} {[e.get('reason') for e in events(run1, 'run.failed')]}")
part = design["fields"].get("part")
check(part and k.get(part)["state"] == "released", f"part {k.get(part)['number'] if part else None} released")
created = [k.get(x) for x in design["fields"].get("items_created") or []]
print("     items created:", [c["fields"].get("mpn") for c in created])
prs = [k.get(x) for x in design["fields"].get("purchase_requests") or []]
print("     purchase requests:", [(p["number"], p["fields"].get("mpn"), p["state"], p.get("assignees")) for p in prs])
check(prs, "purchase requests raised for the shortages")
by_mpn = {p["fields"].get("mpn"): p for p in prs}
known = by_mpn.get("RC0603FR-0710KL")
check(known and known["state"] == "ordered", "known item from the approved supplier was ordered by the purchasing cog within criteria")
po = k.get(known["fields"]["po"]) if known and known["fields"].get("po") else None
check(po and po["state"] == "issued" and po["fields"]["supplier"] == "acme-metals" and po["fields"]["amount"] > 0,
      f"PO {po['number'] if po else None} issued to acme-metals for {po['fields']['amount'] if po else None} USD")
pr_run = hub.runner.run_for_record(known["locator"])
dec = next(e for e in hub.runner.track(pr_run["id"]) if e["type"] == "gate.decided")
check(dec["by"] == "purchasing-assistant" and dec["by_type"] == "cog", f"decided by {dec['by']} under: {dec['delegating_rule']['text'][:80]}")
unknown = [p for p in prs if p is not known and p["state"].startswith("awaiting")]
check(unknown, "unknown-supplier requests escalated to the buyer")
for u in unknown:
    u_run = hub.runner.run_for_record(u["locator"])
    try:
        hub.runner.decide(u_run["id"], "dave", "approve", meaning="ordered from NoSuchCo", fields={"supplier": "nosuchco", "unit_cost": 1.0}, payload_digest=seen(u_run["id"]))
        check(hub.runner.load(u_run["id"])["status"] == "failed", "unapproved supplier named at the Gate: run failed back to the owner")
        hub.runner.cancel(u_run["id"], "alice", reason="smoke: unapproved supplier")
    except RunError as e:
        check(True, f"unapproved supplier refused: {e}")
    break

print("4. goods receipt books stock and marks the order received")
gr = hub.runner.submit("goods-receipt", {"record": {"po": po["locator"], "lines": [{"item": known_item["locator"], "quantity": 1}]}}, owner="alice")
g = hub.runner.load(gr)
check(g["status"] == "waiting" and "dave" in g["pending"]["assignees"], "receipt waits for the buyer")
try:
    hub.runner.decide(gr, "alice", "approve", meaning="smoke", payload_digest=seen(gr))
    check(False, "receiver must not confirm their own receipt")
except RunError as e:
    check(True, f"segregation of duties on receipt: {str(e)[:60]}")
hub.runner.decide(gr, "dave", "approve", meaning="goods checked and received", payload_digest=seen(gr))
check(k.get(g["record"])["state"] == "received" and k.get(known_item["locator"])["fields"]["stock"] == 2, "stock booked 1 -> 2")
check(k.get(po["locator"])["state"] == "received", "PO received")

print("5. supplier bill matched three ways, approved within criteria, posted to the ledger")
bill = {"supplier": "acme-metals", "amount": round(po["fields"]["amount"], 2), "currency": "USD", "po_ref": po["number"],
        "description": "resistors", "date": date.today().isoformat()}
b = hub.runner.submit("purchase-to-pay", {"record": bill}, owner="alice")
bs = hub.runner.load(b)
check(bs["status"] == "completed", f"bill run {bs['status']} {[e.get('reason') for e in events(b, 'run.failed')]}")
journals = [j for j in k.journals().values() if j.get("source_record") == bs["record"] or True]
j = k.get(bs["record"])["fields"].get("journal")
check(j, f"journal {j} posted")
dup = hub.runner.submit("purchase-to-pay", {"record": bill}, owner="alice")
check(hub.runner.load(dup)["status"] == "failed", "duplicate bill against the same PO refused")

print("6. one spine: PO links request, item and design")
links = {(l["rel"], l["target"]) for l in k.get(po["locator"])["links"]}
check(("fulfils", known["locator"]) in links and ("concerns", design["locator"]) in links, "PO fulfils the request and concerns the design")
snap = [d for d in hub.dms.documents_of(design["locator"]) if d["fields"].get("doc_type") in ("snapshot", "record-snapshot")]
print("     design documents:", [(d["fields"].get("doc_type"), d["fields"].get("title")) for d in hub.dms.documents_of(design["locator"])])

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
