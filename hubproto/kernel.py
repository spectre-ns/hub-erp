"""The domain kernel: an event-sourced system of record on SQLite.

Only ``code`` Cogs write here, and only under a grant. The kernel re-checks
its own invariants on every command (balanced journal, open period,
segregation of duties, grant present) so a mis-built Cog cannot post.
Every event records the run and the release digest that produced it.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from .release import utcnow


class KernelGuardError(Exception):
    def __init__(self, guard: str, detail: str):
        super().__init__(f"{guard}: {detail}")
        self.guard = guard
        self.detail = detail


class Kernel:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, type TEXT, "
            "aggregate TEXT, data TEXT, run_id TEXT, release_digest TEXT, by TEXT)"
        )
        self.db.commit()

    # --- event store -----------------------------------------------------
    def append(self, type_: str, aggregate: str, data: dict, *, run_id: str = "", release: str = "",
               by: str = "") -> int:
        cur = self.db.execute(
            "INSERT INTO events (at, type, aggregate, data, run_id, release_digest, by) VALUES (?,?,?,?,?,?,?)",
            (utcnow(), type_, aggregate, json.dumps(data, sort_keys=True), run_id, release, by),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def events(self, aggregate: str | None = None, prefix: str | None = None) -> list[dict]:
        sql = "SELECT seq, at, type, aggregate, data, run_id, release_digest, by FROM events"
        args: tuple = ()
        if aggregate:
            sql += " WHERE aggregate = ?"
            args = (aggregate,)
        elif prefix:
            sql += " WHERE aggregate LIKE ?"
            args = (prefix + "%",)
        sql += " ORDER BY seq"
        return [
            {"seq": s, "at": a, "type": t, "aggregate": ag, "data": json.loads(d), "run_id": r, "release": rel, "by": b}
            for s, a, t, ag, d, r, rel, b in self.db.execute(sql, args)
        ]

    # --- projections -----------------------------------------------------
    def _latest(self, prefix: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for ev in self.events(prefix=prefix):
            rec = out.setdefault(ev["aggregate"], {"id": ev["aggregate"].split(":", 1)[1], "events": []})
            rec.update(ev["data"])
            rec["events"].append(ev["seq"])
            rec["last_event"] = ev["type"]
            rec["run_id"] = ev["run_id"] or rec.get("run_id", "")
            rec["release"] = ev["release"] or rec.get("release", "")
        return out

    def suppliers(self) -> dict[str, dict]:
        return self._latest("supplier:")

    def purchase_orders(self) -> dict[str, dict]:
        return self._latest("po:")

    def periods(self) -> dict[str, dict]:
        return self._latest("period:")

    def bills(self) -> dict[str, dict]:
        return self._latest("bill:")

    def journals(self) -> dict[str, dict]:
        return self._latest("journal:")

    def documents(self) -> dict[str, dict]:
        return self._latest("document:")

    def records_in_folder(self, folder: str) -> list[dict]:
        out = []
        for proj, kind in ((self.bills(), "bill"), (self.documents(), "document"), (self.journals(), "journal")):
            for rec in proj.values():
                if rec.get("folder") == folder:
                    out.append({"kind": kind, **rec})
        return sorted(out, key=lambda r: r["events"][0])

    # --- seed ------------------------------------------------------------
    def seed(self) -> None:
        if self.events(prefix="supplier:"):
            return
        self.append("supplier.approved", "supplier:acme-metals", {"name": "Acme Metals", "approved": True}, by="seed")
        self.append("supplier.registered", "supplier:newco-tools", {"name": "NewCo Tools", "approved": False}, by="seed")
        self.append("po.issued", "po:PO-1001", {"supplier": "acme-metals", "amount": 1500.0, "currency": "USD",
                                                 "description": "Aluminium stock", "received": True}, by="seed")
        self.append("po.issued", "po:PO-1002", {"supplier": "acme-metals", "amount": 12000.0, "currency": "USD",
                                                 "description": "CNC fixtures", "received": True}, by="seed")
        self.append("po.issued", "po:PO-1003", {"supplier": "acme-metals", "amount": 4000.0, "currency": "USD",
                                                 "description": "Calibration weights", "received": False}, by="seed")
        today = date.today()
        for delta in (-1, 0, 1):
            month = (today.month - 1 + delta) % 12 + 1
            year = today.year + ((today.month - 1 + delta) // 12)
            self.append("period.opened", f"period:{year:04d}-{month:02d}", {"open": True}, by="seed")
        self.append("period.closed", f"period:{today.year - 1:04d}-12", {"open": False}, by="seed")

    # --- commands with guards -------------------------------------------
    def file_bill(self, bill: dict, *, folder: str, run_id: str, release: str, by: str) -> str:
        bill_id = bill.get("id") or f"BILL-{len(self.bills()) + 1:04d}"
        data = {**bill, "id": bill_id, "folder": folder, "state": "filed", "filed_by": by}
        self.append("bill.filed", f"bill:{bill_id}", data, run_id=run_id, release=release, by=by)
        return bill_id

    def mark_bill(self, bill_id: str, state: str, extra: dict, *, run_id: str, release: str, by: str) -> None:
        self.append(f"bill.{state}", f"bill:{bill_id}", {"state": state, **extra}, run_id=run_id, release=release, by=by)

    def post_journal(self, journal: dict, *, run_id: str, release: str, grant: dict | None, prepared_by: str,
                     approved_by: str, by: str) -> str:
        if not grant or grant.get("run_id") != run_id:
            raise KernelGuardError("grant", "no grant for this run; posting requires an approved change")
        lines = journal.get("lines", [])
        debit = round(sum(float(x.get("debit", 0)) for x in lines), 2)
        credit = round(sum(float(x.get("credit", 0)) for x in lines), 2)
        if debit != credit or debit == 0:
            raise KernelGuardError("balanced-journal", f"debits {debit} do not equal credits {credit}")
        period = journal.get("date", "")[:7]
        p = self.periods().get(f"period:{period}")
        if not p or not p.get("open"):
            raise KernelGuardError("period-open", f"period {period} is not open")
        if prepared_by == approved_by:
            raise KernelGuardError("segregation-of-duties", f"{prepared_by} prepared and approved the same journal")
        number = f"JE-{len(self.journals()) + 1:05d}"
        data = {**journal, "number": number, "prepared_by": prepared_by, "approved_by": approved_by,
                "grant": grant.get("id"), "folder": journal.get("folder", "Finance")}
        self.append("journal.posted", f"journal:{number}", data, run_id=run_id, release=release, by=by)
        return number

    def file_document(self, doc: dict, *, folder: str, run_id: str, release: str, by: str) -> str:
        doc_id = doc.get("id") or f"DOC-{len(self.documents()) + 1:04d}"
        data = {**doc, "id": doc_id, "folder": folder, "state": "draft", "author": by}
        self.append("document.drafted", f"document:{doc_id}", data, run_id=run_id, release=release, by=by)
        return doc_id

    def publish_document(self, doc_id: str, *, run_id: str, release: str, grant: dict | None, approved_by: str,
                         by: str) -> None:
        if not grant or grant.get("run_id") != run_id:
            raise KernelGuardError("grant", "no grant for this run; publishing requires an approved change")
        doc = self.documents().get(f"document:{doc_id}")
        if not doc:
            raise KernelGuardError("exists", f"document {doc_id} not found")
        if doc.get("author") == approved_by:
            raise KernelGuardError("segregation-of-duties", f"{approved_by} authored and approved {doc_id}")
        self.append("document.published", f"document:{doc_id}", {"state": "effective", "approved_by": approved_by,
                                                                  "grant": grant.get("id")},
                    run_id=run_id, release=release, by=by)
