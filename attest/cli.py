"""Command line entry point.

`run` replays a recorded quarter end to end and prints what each control did.
It needs no mail server, no credentials and no network, which is the point:
the controls are only testable because the cycle is deterministic.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .controls import ControlError
from .cycle import AttestationCycle
from .evidence import EvidenceLog
from .register import Register
from .replay import OPENED, replay
from .reporting import by_criticality, coverage, exceptions_table, stale_table

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTER = ROOT / "data" / "applications.yaml"
DEFAULT_REPLAY = ROOT / "data" / "replay" / "session_default.json"
DEFAULT_LOG = ROOT / "out" / "evidence.jsonl"


def _run(args) -> int:
    register = Register.load(args.register)
    log_path = Path(args.log)
    if log_path.exists():
        log_path.unlink()
    evidence = EvidenceLog(log_path)
    cycle = AttestationCycle(register, evidence)

    header_printed = []

    def show(kind, payload):
        if kind == "opened":
            print(f"\n  cycle {args.cycle_id}  opened {OPENED.date()}")
            print(f"  {len(register.in_scope())} applications in scope, "
                  f"{len(payload.requested)} requests sent")
            if payload.exceptions:
                print("\n  could not be asked")
                for app_id, exc in sorted(payload.exceptions.items()):
                    print(f"    {app_id:<8} {exc.reason.value:<16} {exc.detail}")
        elif kind in ("reply", "amend"):
            if not header_printed:
                print("\n  replies")
                header_printed.append(True)
            if kind == "amend":
                print(f"    day {payload['day']:>2}  {payload['app_id']:<8} "
                      f"register amended, attestation on file no longer matches")
            else:
                print(f"    day {payload['day']:>2}  {payload['app_id']:<8} "
                      f"{payload['outcome']}")
        elif kind == "due" and payload["escalated"]:
            print(f"\n  day {cycle.due_days + 1}  escalated to line management: "
                  f"{', '.join(payload['escalated'])}")
        elif kind == "expiry" and payload["expired"]:
            print(f"  day {cycle.expiry_days + 1}  recorded as not attested: "
                  f"{', '.join(payload['expired'])}")

    c = replay(register, cycle, args.replay, args.cycle_id, OPENED, on_event=show)
    _report(c, register)
    try:
        shown = log_path.relative_to(ROOT)
    except ValueError:
        shown = log_path
    print(f"  evidence log  {shown} ({len(evidence.read(c.id))} records)\n")
    return 0


def _report(c, register) -> None:
    cov = coverage(c, register)
    print("\n  ────────────────────────────────────────────────")
    print(f"  response rate   {cov.response_rate_pct:5.1f}%   "
          f"({cov.responded} replies to {cov.requested} requests)")
    print(f"  COVERAGE        {cov.coverage_pct:5.1f}%   "
          f"({cov.valid} valid attestations across {cov.in_scope} in scope)")
    print("  ────────────────────────────────────────────────")
    print(f"    stale              {cov.stale}")
    print(f"    revoked            {cov.revoked}")
    print(f"    exceptions         {cov.exceptions}")
    print(f"    unresolved         {cov.unresolved}")

    print("\n  by criticality")
    for tier, (valid, total) in by_criticality(c, register).items():
        pct = 0.0 if not total else 100.0 * valid / total
        print(f"    {tier:<8} {valid}/{total}  {pct:5.1f}%")

    stale = stale_table(c, register)
    if stale:
        print("\n  attested, then the record changed")
        for app_id, name, attested, current in stale:
            print(f"    {app_id:<8} {name:<30} "
                  f"agreed {attested} -> now {current}")

    rows = exceptions_table(c, register)
    if rows:
        print("\n  exceptions")
        for app_id, name, reason, note in rows:
            trail = f"  ({note})" if note else ""
            print(f"    {app_id:<8} {name:<30} {reason}{trail}")
    print()


def _evidence(args) -> int:
    try:
        for row in EvidenceLog(args.log).read(args.cycle_id):
            print(json.dumps(row, sort_keys=True))
    except BrokenPipeError:
        # Piping into head is the normal way to read this. Exiting with a
        # traceback because the reader went away is noise, not information.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="attest")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="replay a recorded cycle end to end")
    r.add_argument("--register", default=str(DEFAULT_REGISTER))
    r.add_argument("--replay", default=str(DEFAULT_REPLAY))
    r.add_argument("--log", default=str(DEFAULT_LOG))
    r.add_argument("--cycle-id", default="2026-Q4")
    r.set_defaults(func=_run)

    e = sub.add_parser("evidence", help="print the append-only log")
    e.add_argument("--log", default=str(DEFAULT_LOG))
    e.add_argument("--cycle-id", default=None)
    e.set_defaults(func=_evidence)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except ControlError as exc:
        print(f"\n  refused  {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
