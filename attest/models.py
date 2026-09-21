"""The things the system is about, and nothing it does to them.

Kept separate from the controls on purpose. A reviewer should be able to read
what an attestation *is* without reading what the system will refuse to accept
as one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum


# The fields an owner is actually confirming when they attest. Declared in one
# place, because "what did they attest to" is the first question anyone asks of
# a register entry and the answer should not be inferred from the code.
ATTESTABLE_FIELDS = (
    "name",
    "owner_email",
    "criticality",
    "vendor",
    "data_classification",
)


class Criticality(str, Enum):
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"


class Decision(str, Enum):
    """What an owner is permitted to say. Nothing else is a decision."""

    CONFIRM = "confirm"      # record is accurate, application still in use
    UPDATE = "update"        # application still in use, record is wrong
    RETIRE = "retire"        # application is no longer in use


class NonAnswer(str, Enum):
    """Why a response was not a decision.

    These are recorded, not discarded. An attestation cycle that cannot say why
    it is missing 40 records is not evidence of anything.
    """

    NO_RESPONSE = "no_response"
    AUTO_REPLY = "auto_reply"
    BOUNCED = "bounced"
    UNPARSEABLE = "unparseable"
    AMBIGUOUS = "ambiguous"
    WRONG_OWNER = "wrong_owner"
    NO_OWNER = "no_owner"
    OWNER_INACTIVE = "owner_inactive"
    # An owner did answer and the answer was withdrawn. Kept distinct from
    # NO_RESPONSE because filing a withdrawal as silence is a false reason
    # code, and a false reason code is worse than a missing one.
    REVOKED = "revoked"


class CycleState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True)
class Owner:
    email: str
    name: str
    manager_email: str | None = None
    active: bool = True


@dataclass(frozen=True)
class Application:
    id: str
    name: str
    owner_email: str | None
    criticality: Criticality
    vendor: str
    data_classification: str
    in_scope: bool = True

    def attestable(self) -> dict:
        return {f: getattr(self, f) for f in ATTESTABLE_FIELDS}


@dataclass
class Attestation:
    app_id: str
    owner_email: str
    decision: Decision
    responded_at: datetime
    digest: str            # the record digest the owner was shown
    cycle_id: str
    stale: bool = False    # record moved out from under it at some point
    revoked: bool = False
    revoked_reason: str = ""

    @property
    def active(self) -> bool:
        """Not withdrawn. Says nothing about whether the record still matches.

        Whether this attestation counts is deliberately NOT answered here,
        because answering it needs the register as it stands now. See
        reporting.is_valid.
        """
        return not self.revoked


@dataclass
class Exception_:
    """An application that did not produce a valid attestation, and why.

    Named with a trailing underscore to stay out of the way of the builtin.
    """

    app_id: str
    reason: NonAnswer
    detail: str = ""


@dataclass
class Cycle:
    id: str
    opened_at: datetime
    due_at: datetime
    expires_at: datetime
    state: CycleState = CycleState.OPEN
    closed_at: datetime | None = None
    requested: dict[str, str] = field(default_factory=dict)   # app_id -> digest
    attestations: dict[str, Attestation] = field(default_factory=dict)
    exceptions: dict[str, Exception_] = field(default_factory=dict)
    escalated: set[str] = field(default_factory=set)
    # Every requested application that produced any inbound reply, valid or
    # not. Kept separately from attestations so response rate and coverage
    # cannot quietly become the same number.
    responded: set[str] = field(default_factory=set)
    # What came back that was not a decision, per application, in order. An
    # application that expires having received three unusable replies is a
    # different problem from one whose owner never wrote back, and the
    # exception table should be able to tell them apart.
    non_answers: dict[str, list[NonAnswer]] = field(default_factory=dict)

    def resolved(self, app_id: str) -> bool:
        return app_id in self.attestations or app_id in self.exceptions


def to_jsonable(obj):
    """Flatten a dataclass or enum for the evidence log."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    return obj
