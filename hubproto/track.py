"""The Track: an append-only record of a run.

Every event carries a sequence number, a timestamp, the run id and the
release digest the run is pinned to. The run's status is derived from the
Track, never stored as the only truth.

Each entry is chained to the one before it (``prev`` is the previous entry's ``entry_digest``; ``entry_digest``
covers the entry including ``prev``), so editing or deleting an entry is detectable (``verify``). A decision's own
``record_digest`` (the signed digest of the record) is kept as signed; older Tracks overwrote it with the entry
digest and carry no chain, and ``verify`` reports them as unchained rather than as tampered.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

from .release import utcnow
from .rules import canonical, sha256_of

SIGNED_FIELDS = ("by", "verdict", "meaning", "payload_digest", "record_digest", "at", "run_id", "step", "release_digest")


# ---------------------------------------------------------------- signing (keyed with the hub's secret)
def hub_secret(state_dir: Path) -> str:
    """The hub's signing secret: HUBPROTO_SECRET, else the one in <state>/.secret (shared with the editor's tokens),
    created once when missing."""
    env = os.environ.get("HUBPROTO_SECRET")
    if env:
        return env
    path = Path(state_dir) / ".secret"
    try:
        value = path.read_text().strip()
    except OSError:
        value = ""
    if not value:
        import secrets

        value = secrets.token_hex(32)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(value)
        except FileExistsError:
            value = path.read_text().strip()
        except OSError:
            pass
    return value


def sign(secret: str, fields: dict) -> str:
    """An HMAC-SHA256 of the signed fields: only someone holding the hub's secret can make one that verifies."""
    return "h2:" + hmac.new(secret.encode("utf-8"), canonical({k: fields.get(k) for k in SIGNED_FIELDS}).encode("utf-8"),
                            hashlib.sha256).hexdigest()[:40]


def verify_signature(secret: str, decision: dict) -> str:
    """'ok', 'forged' (a keyed signature that does not match) or 'legacy' (an unkeyed signature from before)."""
    sig = str(decision.get("signature") or "")
    if sig.startswith("h2:"):
        return "ok" if hmac.compare_digest(sig, sign(secret, decision)) else "forged"
    return "legacy"


class Track:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "track.jsonl"

    def append(self, event: dict) -> dict:
        prior = self.read()
        seq = len(prior) + 1
        prev = (prior[-1].get("entry_digest") or prior[-1].get("record_digest") or "") if prior else ""
        record = {"seq": seq, "at": utcnow(), **event, "prev": prev}
        record.pop("entry_digest", None)
        record["entry_digest"] = sha256_of(record)[:32]
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return record

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn last line from a crash: that entry never happened
        return out

    def verify(self) -> list[str]:
        """Problems with the chain: an edited, deleted or reordered entry shows here. Entries written before the chain
        existed are reported once as unchained, not as tampering."""
        problems: list[str] = []
        prev = None
        unchained = 0
        for ev in self.read():
            if "entry_digest" not in ev:
                unchained += 1
                prev = ev.get("record_digest") or ""
                continue
            body = {k: v for k, v in ev.items() if k != "entry_digest"}
            if sha256_of(body)[:32] != ev["entry_digest"]:
                problems.append(f"entry {ev.get('seq')} was changed after it was written")
            if prev is not None and ev.get("prev") != prev:
                problems.append(f"entry {ev.get('seq')} does not follow the entry before it (an entry was removed or reordered)")
            prev = ev["entry_digest"]
        if unchained:
            problems.append(f"{unchained} entr{'y' if unchained == 1 else 'ies'} from before the chain was introduced are not chained")
        return problems

    def has_decision(self, *steps: str) -> bool:
        """A signature already landed on this Gate (and no rejection, retry or resubmission has reopened it). A Gate is
        named by its step id and by its checkpoint (decisions carry the checkpoint); pass both."""
        decided = False
        names = {s for s in steps if s}
        for ev in self.read():
            if ev.get("step") not in names and ev["type"] not in ("run.retried", "run.resubmitted"):
                continue
            if ev["type"] == "gate.decided":
                decided = True
            elif ev["type"] in ("gate.pending", "run.retried", "run.resubmitted"):
                decided = False
        return decided

    def status(self) -> str:
        events = self.read()
        if not events:
            return "unknown"
        for ev in reversed(events):
            t = ev["type"]
            if t in ("gate.cog_review", "gate.skipped", "record.fields_set", "gate.signed"):
                continue  # informational: the entry before it says where the run stands
            if t == "run.completed":
                return "completed"
            if t == "run.cancelled":
                return "cancelled"
            if t == "run.failed":
                return "failed"
            if t == "gate.returned":
                return "waiting"  # returned to the owner for rework: the run waits for a resubmission
            if t == "run.rejected":
                return "rejected"
            if t in ("gate.pending", "gate.escalated", "gate.overdue", "gate.handed_back", "gate.stepped_in"):
                return "waiting"
            if t == "run.submitted":
                return "submitted"
            if t in ("step.started", "step.result", "gate.decided", "gate.passed", "grant.issued", "run.retried",
                     "run.resubmitted"):
                return "running"
        return "running"
