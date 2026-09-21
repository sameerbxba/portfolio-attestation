"""Driving a recorded quarter through the cycle.

Shared by the CLI and the end-to-end test so the numbers printed in the README
and the numbers asserted in the tests come from one execution path. A README
that can drift from the code is a README nobody should trust.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .cycle import AttestationCycle
from .models import Criticality
from .register import Register

OPENED = datetime(2026, 10, 1, 9, 0)


def replay(
    register: Register,
    cycle: AttestationCycle,
    replay_path: str | Path,
    cycle_id: str = "2026-Q4",
    opened: datetime = OPENED,
    on_event=None,
):
    """Open a cycle, play the recorded events in order, escalate, expire, close."""
    c = cycle.open(cycle_id, opened)
    if on_event:
        on_event("opened", c)

    events = json.loads(Path(replay_path).read_text())["events"]
    for ev in sorted(events, key=lambda e: e["day"]):
        at = opened + timedelta(days=ev["day"])

        if ev["type"] == "amend":
            changes = dict(ev["changes"])
            if "criticality" in changes:
                changes["criticality"] = Criticality(changes["criticality"])
            cycle.amend(ev["app_id"], changes, at)
            if on_event:
                on_event("amend", ev)
            continue

        digest = c.requested.get(ev["app_id"], "0" * 12)
        outcome = cycle.ingest(
            ev["sender"], ev["subject"],
            ev["body"].replace("{digest}", digest), at,
        )
        if on_event:
            on_event("reply", {**ev, "outcome": outcome})

    due = opened + timedelta(days=cycle.due_days + 1)
    moved_due = cycle.advance(due)
    if on_event:
        on_event("due", moved_due)

    expiry = opened + timedelta(days=cycle.expiry_days + 1)
    moved_expiry = cycle.advance(expiry)
    if on_event:
        on_event("expiry", moved_expiry)

    cycle.close(expiry)
    return c
