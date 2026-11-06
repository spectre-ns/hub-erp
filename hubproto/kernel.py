"""The domain kernel: a generic record system on an event store (SQLite).

Every module is a set of record types on this one model. A record has a
type and number, a scope folder, a state, typed fields, typed links to other
records, labels (sensitivity, retention), a hold flag, and its event history.
Transitions carry the run and the policy release that caused them, and the
kernel re-checks its own invariants (required fields by state, link
vocabulary, balanced journal, open period, segregation of duties, grant
present) on every command so a mis-built Cog cannot slip past them.

Record type definitions, the link vocabulary and the retention classes are
versioned configuration documents under ``config/``; their digests go into
the release bill of materials.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from .release import utcnow
from .rules import sha256_of

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class KernelGuardError(Exception):
    def __init__(self, guard: str, detail: str):
        super().__init__(f"{guard}: {detail}")
        self.guard = guard
        self.detail = detail


def _load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("$comment", None)
    return data


def locator(type_: str, number: str) -> str:
    return f"rec:{type_}:{number}"


def parse_locator(ref: str) -> tuple[str | None, str]:
    """``rec:ncr:NCR-2026-0001`` -> ("ncr", "NCR-2026-0001"); a bare number -> (None, number)."""
    if ref.startswith("rec:"):
        _, type_, number = ref.split(":", 2)
        return type_, number
    if ":" in ref and not ref.startswith(("supplier:", "po:", "period:")):
        type_, number = ref.split(":", 1)
        return type_, number
    return None, ref


class Kernel:
    def __init__(self, path: Path, config_dir: Path | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # One connection shared by the shell's handler threads; callers serialize writes (the shell holds a lock).
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, type TEXT, "
            "aggregate TEXT, data TEXT, run_id TEXT, release_digest TEXT, by TEXT)"
        )
        self.db.commit()
        cfg = Path(config_dir) if config_dir else CONFIG_DIR
        self.config_dir = cfg
        self.types: dict[str, dict] = _load(cfg / "record-types.json")
        self.link_vocab: dict[str, str] = _load(cfg / "links.json")
        self.retention: dict[str, dict] = _load(cfg / "retention.json")
        self.config_digests = {p.name: sha256_of(_load(p)) for p in sorted(cfg.glob("*.json"))}
        self._cache: dict[str, dict] | None = None
        self._cache_version: tuple | None = None

    def _data_version(self) -> tuple:
        """Changes whenever any connection (this or another process) commits to the file."""
        dv = self.db.execute("PRAGMA data_version").fetchone()[0]
        seq = self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
        return (dv, seq)

    # ------------------------------------------------------------------ events
    def append(self, type_: str, aggregate: str, data: dict, *, run_id: str = "", release: str = "",
               by: str = "") -> int:
        cur = self.db.execute(
            "INSERT INTO events (at, type, aggregate, data, run_id, release_digest, by) VALUES (?,?,?,?,?,?,?)",
            (utcnow(), type_, aggregate, json.dumps(data, sort_keys=True, default=str), run_id, release, by),
        )
        self.db.commit()
        self._cache = None
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

    # ------------------------------------------------------------- projections
    def _records(self) -> dict[str, dict]:
        """All records keyed by locator, rebuilt from the event stream when the file changed."""
        version = self._data_version()
        if self._cache is not None and self._cache_version == version:
            return self._cache
        recs: dict[str, dict] = {}
        for ev in self.events(prefix="rec:"):
            loc = ev["aggregate"]
            rec = recs.get(loc)
            t = ev["type"]
            d = ev["data"]
            if t == "record.created":
                rec = {"locator": loc, "type": d["type"], "id": d["number"], "number": d["number"], "folder": d["folder"],
                       "state": d.get("state", "open"), "fields": dict(d.get("fields", {})), "labels": dict(d.get("labels", {})),
                       "hold": False, "archived": False, "created_at": ev["at"], "created_by": ev["by"],
                       "run_id": ev["run_id"], "release": ev["release"], "events": [], "links": [], "decisions": [],
                       "transitions": [], "assignees": [], "due": None}
                recs[loc] = rec
            if rec is None:
                continue
            rec["events"].append(ev["seq"])
            rec["last_event"] = t
            rec["updated_at"] = ev["at"]
            if ev["run_id"]:
                rec["run_id"] = ev["run_id"]
            if ev["release"]:
                rec["release"] = ev["release"]
            if t == "record.fields_set":
                rec["fields"].update(d["fields"])
            elif t == "record.transitioned":
                rec["state"] = d["to"]
                rec["transitions"].append({"from": d["from"], "to": d["to"], "at": ev["at"], "by": ev["by"],
                                           "run_id": ev["run_id"], "reason": d.get("reason", "")})
                if d.get("decision"):
                    rec["decisions"].append(d["decision"])
                rec["assignees"] = list(d.get("assignees", []))
                rec["due"] = d.get("due")
            elif t == "record.decision":
                rec["decisions"].append(d)
            elif t == "record.assigned":
                rec["assignees"] = list(d.get("assignees", []))
                rec["due"] = d.get("due", rec.get("due"))
            elif t == "record.linked":
                rec["links"].append({"rel": d["rel"], "target": d["target"], "at": ev["at"]})
            elif t == "record.unlinked":
                rec["links"] = [l for l in rec["links"] if not (l["rel"] == d["rel"] and l["target"] == d["target"])]
            elif t == "record.labelled":
                rec["labels"].update(d["labels"])
            elif t == "record.held":
                rec["hold"] = bool(d["on"])
                rec["hold_reason"] = d.get("reason", "")
            elif t == "record.archived":
                rec["archived"] = True
        self._cache = recs
        self._cache_version = version
        return recs

    def get(self, ref: str) -> dict | None:
        recs = self._records()
        if ref in recs:
            return recs[ref]
        type_, number = parse_locator(ref)
        if type_:
            return recs.get(locator(type_, number))
        for rec in recs.values():
            if rec["number"] == number:
                return rec
        return None

    def require(self, ref: str) -> dict:
        rec = self.get(ref)
        if rec is None:
            raise KernelGuardError("exists", f"no record {ref!r}")
        return rec

    def type_def(self, type_: str) -> dict:
        if type_ not in self.types:
            raise KernelGuardError("record-type", f"unknown record type {type_!r}")
        return self.types[type_]

    def query(self, type_: str | None = None, folder: str | None = None, state: str | None = None,
              linked_to: str | None = None, text: str | None = None, assignee: str | None = None,
              include_archived: bool = False, overdue_at: str | None = None) -> list[dict]:
        out = []
        needle = (text or "").lower()
        for rec in self._records().values():
            if rec["archived"] and not include_archived:
                continue
            if type_ and rec["type"] != type_:
                continue
            if folder and not (rec["folder"] == folder or rec["folder"].startswith(folder + "/")):
                continue
            if state and rec["state"] != state:
                continue
            if assignee and assignee not in rec["assignees"]:
                continue
            if linked_to and not any(l["target"] == linked_to for l in rec["links"]) and \
                    not any(l["target"] == rec["locator"] for l in self.get(linked_to)["links"] if self.get(linked_to)):
                continue
            if overdue_at and not (rec["due"] and rec["due"] < overdue_at and rec["state"].startswith("awaiting")):
                continue
            if needle and needle not in json.dumps(rec["fields"], default=str).lower() and needle not in rec["number"].lower():
                continue
            out.append(rec)
        return sorted(out, key=lambda r: r["events"][0])

    def links_of(self, ref: str) -> list[dict]:
        rec = self.require(ref)
        out = [{"rel": l["rel"], "target": l["target"], "direction": "out", "label": l["rel"]} for l in rec["links"]]
        for other in self._records().values():
            for l in other["links"]:
                if l["target"] == rec["locator"]:
                    out.append({"rel": l["rel"], "target": other["locator"], "direction": "in",
                                "label": self.link_vocab.get(l["rel"], l["rel"] + " (inverse)")})
        return out

    def records_in_folder(self, folder: str) -> list[dict]:
        return [self.flat(r) for r in self.query(folder=folder) if r["folder"] == folder]

    @staticmethod
    def flat(rec: dict) -> dict:
        """Fields merged to the top level, for list views and older callers."""
        out = {**rec["fields"], "id": rec["number"], "kind": rec["type"], "type": rec["type"], "state": rec["state"],
               "folder": rec["folder"], "run_id": rec["run_id"], "release": rec["release"], "events": rec["events"],
               "locator": rec["locator"], "last_event": rec.get("last_event", ""), "assignees": rec["assignees"],
               "due": rec["due"], "labels": rec["labels"]}
        return out

    # ---------------------------------------------------------------- commands
    def next_number(self, type_: str) -> str:
        """Next number for a type, counted in the database (not the cache), so two processes never share one."""
        td = self.type_def(type_)
        year = date.today().year
        like = f"rec:{type_}:{td['prefix']}-{year}-%" if td.get("yearly") else f"rec:{type_}:{td['prefix']}-%"
        count = self.db.execute("SELECT COUNT(*) FROM events WHERE type = 'record.created' AND aggregate LIKE ?", (like,)).fetchone()[0]
        n = count + 1
        while True:
            number = f"{td['prefix']}-{year}-{n:0{td['digits']}d}" if td.get("yearly") else f"{td['prefix']}-{n:0{td['digits']}d}"
            taken = self.db.execute("SELECT 1 FROM events WHERE type = 'record.created' AND aggregate = ? LIMIT 1",
                                    (locator(type_, number),)).fetchone()
            if not taken:
                return number
            n += 1

    def _check_fields(self, type_: str, fields: dict) -> None:
        td = self.type_def(type_)
        for name, value in fields.items():
            spec = td["fields"].get(name)
            if spec is None:
                continue  # unknown fields are kept; the type definition lists the governed ones
            if spec.startswith("enum:") and value not in (None, "") and str(value) not in spec[5:].split(","):
                raise KernelGuardError("field-enum", f"{type_}.{name} must be one of {spec[5:]}, not {value!r}")

    def create(self, type_: str, folder: str, fields: dict | None = None, *, by: str, run_id: str = "",
               release: str = "", state: str = "open", labels: dict | None = None) -> dict:
        td = self.type_def(type_)
        fields = dict(fields or {})
        self._check_fields(type_, fields)
        lab = {"sensitivity": td.get("sensitivity", "internal"), "retention": td.get("retention", "standard")}
        lab.update(labels or {})
        # Number and insert in one immediate transaction so concurrent processes cannot take the same number.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            number = self.next_number(type_)
            loc = locator(type_, number)
            self.db.execute(
                "INSERT INTO events (at, type, aggregate, data, run_id, release_digest, by) VALUES (?,?,?,?,?,?,?)",
                (utcnow(), "record.created", loc, json.dumps({"type": type_, "number": number, "folder": folder, "fields": fields,
                                                              "state": state, "labels": lab}, sort_keys=True, default=str),
                 run_id, release, by))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        self._cache = None
        missing = self.required_missing(self.get(loc), state)
        if missing and state not in ("open", "draft", "received", "filed", "captured"):
            self.append("record.transitioned", loc, {"from": state, "to": "open", "reason": f"missing {', '.join(missing)} for {state}"},
                        run_id=run_id, release=release, by=by)
        return self.get(loc)

    def set_fields(self, ref: str, fields: dict, *, by: str, run_id: str = "", release: str = "") -> dict:
        rec = self.require(ref)
        if rec["hold"]:
            raise KernelGuardError("hold", f"{rec['number']} is on hold")
        self._check_fields(rec["type"], fields)
        self.append("record.fields_set", rec["locator"], {"fields": fields}, run_id=run_id, release=release, by=by)
        return self.get(rec["locator"])

    def required_missing(self, rec: dict, state: str) -> list[str]:
        req = self.type_def(rec["type"]).get("required_by_state", {}).get(state, [])
        return [f for f in req if rec["fields"].get(f) in (None, "", [], {})]

    def transition(self, ref: str, to_state: str, *, by: str, run_id: str = "", release: str = "",
                   decision: dict | None = None, reason: str = "", assignees: list[str] | None = None,
                   due: str | None = None) -> dict:
        rec = self.require(ref)
        if rec["hold"]:
            raise KernelGuardError("hold", f"{rec['number']} is on hold")
        missing = self.required_missing(rec, to_state)
        if missing:
            raise KernelGuardError("required-fields", f"{rec['number']} cannot enter {to_state!r} without {', '.join(missing)}")
        data = {"from": rec["state"], "to": to_state, "reason": reason, "assignees": assignees or [], "due": due}
        if decision:
            data["decision"] = decision
        self.append("record.transitioned", rec["locator"], data, run_id=run_id, release=release, by=by)
        return self.get(rec["locator"])

    def assign(self, ref: str, assignees: list[str], *, by: str, run_id: str = "", release: str = "",
               due: str | None = None) -> dict:
        rec = self.require(ref)
        self.append("record.assigned", rec["locator"], {"assignees": assignees, "due": due or rec["due"]},
                    run_id=run_id, release=release, by=by)
        return self.get(rec["locator"])

    def link(self, ref: str, rel: str, target: str, *, by: str, run_id: str = "", release: str = "") -> dict:
        if rel not in self.link_vocab:
            raise KernelGuardError("link-vocabulary", f"unknown link relation {rel!r}")
        rec = self.require(ref)
        tgt = self.require(target)
        if any(l["rel"] == rel and l["target"] == tgt["locator"] for l in rec["links"]):
            return rec
        self.append("record.linked", rec["locator"], {"rel": rel, "target": tgt["locator"]}, run_id=run_id,
                    release=release, by=by)
        return self.get(rec["locator"])

    def unlink(self, ref: str, rel: str, target: str, *, by: str, run_id: str = "", release: str = "") -> dict:
        rec = self.require(ref)
        tgt = self.require(target)
        self.append("record.unlinked", rec["locator"], {"rel": rel, "target": tgt["locator"]}, run_id=run_id,
                    release=release, by=by)
        return self.get(rec["locator"])

    def set_labels(self, ref: str, labels: dict, *, by: str, run_id: str = "", release: str = "",
                   decision: dict | None = None) -> dict:
        rec = self.require(ref)
        order = ["public", "internal", "confidential", "restricted"]
        new = labels.get("sensitivity")
        cur = rec["labels"].get("sensitivity", "internal")
        if new and new in order and cur in order and order.index(new) < order.index(cur) and not decision:
            raise KernelGuardError("label-lowering", "lowering a sensitivity label needs a Gate decision")
        self.append("record.labelled", rec["locator"], {"labels": labels, "decision": decision}, run_id=run_id,
                    release=release, by=by)
        return self.get(rec["locator"])

    def hold(self, ref: str, on: bool, reason: str, *, by: str, run_id: str = "", release: str = "") -> dict:
        rec = self.require(ref)
        self.append("record.held", rec["locator"], {"on": on, "reason": reason}, run_id=run_id, release=release, by=by)
        return self.get(rec["locator"])

    def archive(self, ref: str, *, by: str, run_id: str = "", release: str = "") -> dict:
        rec = self.require(ref)
        if rec["hold"]:
            raise KernelGuardError("hold", f"{rec['number']} is on hold")
        self.append("record.archived", rec["locator"], {}, run_id=run_id, release=release, by=by)
        return self.get(rec["locator"])

    # ------------------------------------------------------- plain aggregates
    def _latest(self, prefix: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for ev in self.events(prefix=prefix):
            rec = out.setdefault(ev["aggregate"], {"id": ev["aggregate"].split(":", 1)[1], "events": []})
            rec.update(ev["data"])
            rec["events"].append(ev["seq"])
            rec["last_event"] = ev["type"]
        return out

    def suppliers(self) -> dict[str, dict]:
        return self._latest("supplier:")

    def purchase_orders(self) -> dict[str, dict]:
        return self._latest("po:")

    def periods(self) -> dict[str, dict]:
        return self._latest("period:")

    def _by_type(self, type_: str) -> dict[str, dict]:
        return {f"{type_}:{r['number']}": self.flat(r) for r in self.query(type_=type_, include_archived=True)}

    def bills(self) -> dict[str, dict]:
        return self._by_type("bill")

    def journals(self) -> dict[str, dict]:
        return self._by_type("journal")

    def documents(self) -> dict[str, dict]:
        return self._by_type("document")

    # --------------------------------------------------------------------- seed
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

    # ------------------------------------------------------- module commands
    def file_bill(self, bill: dict, *, folder: str, run_id: str, release: str, by: str) -> str:
        fields = {k: v for k, v in bill.items() if k not in ("id", "folder", "state")}
        fields["filed_by"] = by
        rec = self.create("bill", folder, fields, by=by, run_id=run_id, release=release, state="filed")
        return rec["number"]

    def mark_bill(self, bill_id: str, state: str, extra: dict, *, run_id: str, release: str, by: str) -> None:
        rec = self.require(f"bill:{bill_id}")
        if extra:
            self.set_fields(rec["locator"], extra, by=by, run_id=run_id, release=release)
        self.transition(rec["locator"], state, by=by, run_id=run_id, release=release)

    def post_journal(self, journal: dict, *, run_id: str, release: str, grant: dict | None, prepared_by: str,
                     approved_by: str, by: str) -> str:
        if not grant or grant.get("run_id") != run_id:
            raise KernelGuardError("grant", "no grant for this run; posting requires an approved change")
        lines = journal.get("lines", [])
        if not lines:
            raise KernelGuardError("balanced-journal", "a journal needs at least two lines")
        if any(float(x.get("debit", 0)) < 0 or float(x.get("credit", 0)) < 0 for x in lines):
            raise KernelGuardError("balanced-journal", "journal lines cannot carry negative amounts; post a reversal instead")
        debit = round(sum(float(x.get("debit", 0)) for x in lines), 2)
        credit = round(sum(float(x.get("credit", 0)) for x in lines), 2)
        if debit != credit or debit <= 0:
            raise KernelGuardError("balanced-journal", f"debits {debit} do not equal credits {credit}, or the journal is empty")
        period = str(journal.get("date", ""))[:7]
        p = self.periods().get(f"period:{period}")
        if not p or not p.get("open"):
            raise KernelGuardError("period-open", f"period {period} is not open")
        if prepared_by == approved_by:
            raise KernelGuardError("segregation-of-duties", f"{prepared_by} prepared and approved the same journal")
        fields = {**{k: v for k, v in journal.items() if k != "folder"}, "prepared_by": prepared_by,
                  "approved_by": approved_by, "grant": grant.get("id")}
        rec = self.create("journal", journal.get("folder", "Finance"), fields, by=by, run_id=run_id, release=release,
                          state="posted")
        if journal.get("bill"):
            bill = self.get(f"bill:{journal['bill']}")
            if bill:
                self.link(rec["locator"], "settles", bill["locator"], by=by, run_id=run_id, release=release)
        return rec["number"]

    def file_document(self, doc: dict, *, folder: str, run_id: str, release: str, by: str) -> str:
        fields = {k: v for k, v in doc.items() if k not in ("id", "folder", "state")}
        fields.setdefault("owner", by)
        fields["author"] = by
        fields.setdefault("doc_class", "controlled")
        fields.setdefault("revision", "A")
        fields.setdefault("minor", 0)
        rec = self.create("document", folder, fields, by=by, run_id=run_id, release=release, state="draft")
        return rec["number"]

    def publish_document(self, doc_id: str, *, run_id: str, release: str, grant: dict | None, approved_by: str,
                         by: str) -> None:
        if not grant or grant.get("run_id") != run_id:
            raise KernelGuardError("grant", "no grant for this run; publishing requires an approved change")
        rec = self.require(f"document:{doc_id}")
        if rec["fields"].get("author") == approved_by:
            raise KernelGuardError("segregation-of-duties", f"{approved_by} authored and approved {doc_id}")
        self.set_fields(rec["locator"], {"approved_by": approved_by, "grant": grant.get("id"),
                                         "effective_from": rec["fields"].get("effective_from") or date.today().isoformat(),
                                         "review_date": rec["fields"].get("review_date") or f"{date.today().year + 1}-{date.today().month:02d}-{date.today().day:02d}"},
                        by=by, run_id=run_id, release=release)
        # Withdraw the previously effective revision of the same title, if any.
        for other in self.query(type_="document", state="effective"):
            if other["fields"].get("title") == rec["fields"].get("title") and other["locator"] != rec["locator"]:
                self.transition(other["locator"], "obsolete", by=by, run_id=run_id, release=release,
                                reason=f"superseded by {rec['number']}")
                self.link(rec["locator"], "supersedes", other["locator"], by=by, run_id=run_id, release=release)


WRITE_REACHES = {"kernel:records", "kernel:documents", "kernel:journals", "kernel:links", "kernel:bills", "kernel:write"}
MUTATORS = ("create", "set_fields", "transition", "assign", "link", "unlink", "set_labels", "hold", "archive", "append",
            "file_bill", "mark_bill", "post_journal", "file_document", "publish_document")


class ScopedKernel:
    """The kernel as a Cog sees it: reads pass through; writes need a declared write reach, and
    writes to anything other than the run's own record (or records this Cog created in this
    run) need the run's grant. The kernel stays the authority; the Cog's declaration is the key."""

    def __init__(self, kernel: Kernel, *, cog_id: str, reaches: list[str], run_id: str, record: str | None,
                 grant: dict | None):
        self._k = kernel
        self.cog_id = cog_id
        self.reaches = set(reaches or [])
        self.run_id = run_id
        self.own = {record} if record else set()
        self.grant = grant

    def __getattr__(self, name):
        if name in MUTATORS:
            return self._guarded(name)
        return getattr(self._k, name)

    @property
    def types(self):
        return self._k.types

    @property
    def link_vocab(self):
        return self._k.link_vocab

    def _can_write(self) -> bool:
        return bool(self.reaches & WRITE_REACHES)

    def _is_own(self, target: str | None) -> bool:
        if target is None or target in self.own:
            return True
        for owned in list(self.own):
            rec = self._k.get(owned)
            if rec and any(l["rel"] == "has-document" and l["target"] == target for l in rec["links"]):
                return True
        return False

    def _target_of(self, name: str, args, kwargs):
        if name in ("create", "append", "post_journal", "file_bill", "file_document"):
            return None  # creates a new aggregate
        ref = args[0] if args else kwargs.get("ref") or kwargs.get("bill_id") or kwargs.get("doc_id")
        if name in ("mark_bill",):
            return f"rec:bill:{ref}"
        if name == "publish_document":
            return f"rec:document:{ref}"
        rec = self._k.get(str(ref)) if ref else None
        return rec["locator"] if rec else str(ref)

    def _guarded(self, name: str):
        def call(*args, **kwargs):
            if not self._can_write():
                raise KernelGuardError("reach", f"{self.cog_id} declares no write reach ({sorted(self.reaches) or 'none'}) and may not {name}")
            target = self._target_of(name, args, kwargs)
            if name == "append" and args and str(args[1]).startswith("rec:"):
                target = str(args[1])
            needs_grant = name in ("post_journal", "publish_document") or not self._is_own(target)
            if needs_grant and not (self.grant and self.grant.get("run_id") == self.run_id):
                raise KernelGuardError("grant", f"{self.cog_id} may not {name} {target or ''} without this run's grant")
            result = getattr(self._k, name)(*args, **kwargs)
            if name in ("create",) and isinstance(result, dict):
                self.own.add(result["locator"])
            elif name in ("file_bill", "file_document") and isinstance(result, str):
                self.own.add(f"rec:{'bill' if name == 'file_bill' else 'document'}:{result}")
            return result
        return call
