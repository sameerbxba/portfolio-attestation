from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from attest.cycle import AttestationCycle
from attest.evidence import EvidenceLog
from attest.models import Application, Criticality, Owner
from attest.register import Register

ROOT = Path(__file__).resolve().parent.parent
OPENED = datetime(2026, 10, 1, 9, 0)


def day(n: int) -> datetime:
    return OPENED + timedelta(days=n)


def owner(email="ravi.menon@x.example", name="Ravi Menon", active=True,
          manager="dana.whitfield@x.example"):
    return Owner(email=email, name=name, manager_email=manager, active=active)


def app(app_id="APP-001", owner_email="ravi.menon@x.example",
        criticality=Criticality.TIER_1, in_scope=True, **kw):
    return Application(
        id=app_id,
        name=kw.get("name", "Ledger Consolidation Platform"),
        owner_email=owner_email,
        criticality=criticality,
        vendor=kw.get("vendor", "Northwind Systems"),
        data_classification=kw.get("data_classification", "confidential"),
        in_scope=in_scope,
    )


def reply(app_id: str, digest: str, token: str = "CONFIRM", extra: str = "") -> str:
    return f"{token}\n\n{extra}\n\nREF: {app_id}:{digest}\n"


@pytest.fixture
def log(tmp_path):
    return EvidenceLog(tmp_path / "evidence.jsonl")


@pytest.fixture
def make_cycle(log):
    """Build a cycle over a register you describe, already opened."""

    def _make(applications=None, owners=None, cycle_id="TEST"):
        applications = applications if applications is not None else [app()]
        owners = owners if owners is not None else [
            owner(), owner(email="dana.whitfield@x.example",
                           name="Dana Whitfield", manager=None)
        ]
        register = Register(applications, owners)
        cycle = AttestationCycle(register, log)
        cycle.open(cycle_id, OPENED)
        return cycle, register

    return _make


@pytest.fixture
def real_register():
    return Register.load(ROOT / "data" / "applications.yaml")
