"""AT-4. The append-only record of everything the cycle did.

Opened in append mode and fsynced per record, so a process that dies halfway
through a cycle still leaves a complete account up to the moment it died. That
matters more here than throughput: the log is the only artifact that survives
an argument about what happened.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from .models import to_jsonable


class EvidenceLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, cycle_id: str, event: str, at: datetime, **fields) -> dict:
        record = {
            "cycle_id": cycle_id,
            "event": event,
            "at": at.isoformat(),
            **{k: to_jsonable(v) for k, v in fields.items()},
        }
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return record

    def read(self, cycle_id: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        rows = [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if cycle_id:
            rows = [r for r in rows if r.get("cycle_id") == cycle_id]
        return rows
