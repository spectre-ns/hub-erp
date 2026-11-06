"""The Track: an append-only record of a run.

Every event carries a sequence number, a timestamp, the run id and the
release digest the run is pinned to. The run's status is derived from the
Track, never stored as the only truth.
"""

from __future__ import annotations

import json
from pathlib import Path

from .release import utcnow
from .rules import sha256_of


class Track:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "track.jsonl"

    def append(self, event: dict) -> dict:
        seq = len(self.read()) + 1
        record = {"seq": seq, "at": utcnow(), **event}
        record["record_digest"] = sha256_of(record)[:16]
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def status(self) -> str:
        events = self.read()
        if not events:
            return "unknown"
        for ev in reversed(events):
            t = ev["type"]
            if t == "run.completed":
                return "completed"
            if t == "run.failed":
                return "failed"
            if t == "run.rejected":
                return "rejected"
            if t == "gate.pending":
                return "waiting"
            if t == "run.submitted":
                return "submitted"
            if t in ("step.started", "step.result", "gate.decided", "gate.passed", "grant.issued"):
                return "running"
        return "running"
