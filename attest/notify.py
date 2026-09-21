"""The outbound side, as a port with a recorded implementation.

Nothing here sends email. A recorded notifier makes the cycle deterministic,
which is what makes the controls testable at all: a control you can only
exercise against a live mail server is a control you cannot test. A real
transport is a class satisfying the Notifier protocol below.

The field block in the request is generated from ATTESTABLE_FIELDS rather than
written out, because the owner has to be shown exactly the fields the digest
covers. Writing the list twice would let the two drift, and the failure would
be silent in the direction that matters: a field in the digest that the owner
was never shown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from .models import ATTESTABLE_FIELDS

LABELS = {
    "name": "Application",
    "owner_email": "Owner",
    "criticality": "Criticality",
    "vendor": "Vendor",
    "data_classification": "Data classification",
}

REQUEST_BODY = """\
{owner_name},

Your quarterly confirmation is due for the application below. Reply to this
message with exactly one of CONFIRM, UPDATE or RETIRE on a line of its own,
and leave the reference line in place.

{fields}

  CONFIRM  the details above are accurate and the application is still in use
  UPDATE   the application is still in use and the details above are wrong
  RETIRE   the application is no longer in use

REF: {app_id}:{digest}
"""


def field_block(app) -> str:
    """The attestable fields, exactly as the digest sees them."""
    width = max(len(LABELS.get(f, f)) for f in ATTESTABLE_FIELDS)
    lines = []
    for name in ATTESTABLE_FIELDS:
        value = getattr(app, name)
        value = value.value if hasattr(value, "value") else value
        lines.append(f"  {LABELS.get(name, name):<{width}}  {value}")
    return "\n".join(lines)


class Notifier(Protocol):
    """What the cycle needs from the outbound side. Three messages, no more."""

    def request(self, app, owner, digest, cycle, at: datetime) -> "Message": ...

    def clarify(self, app, owner, digest, reason, cycle, at: datetime) -> "Message": ...

    def escalate(self, app, owner, manager, digest, cycle, at: datetime) -> "Message": ...

ESCALATION_BODY = """\
{manager_name},

{owner_name} has not responded to the confirmation request for {name}
({app_id}), which was due {due}. The request is reissued below and will be
recorded as not attested on {expires} if no decision is received.

REF: {app_id}:{digest}
"""


CLARIFICATION_BODY = """\
{owner_name},

Your reply about {name} was received but did not contain a decision
({reason}), so nothing has been recorded against the application.

Reply with exactly one of CONFIRM, UPDATE or RETIRE on a line of its own and
leave the reference line below in place. The request is still open and will be
recorded as not attested on {expires} if no decision is received.

REF: {app_id}:{digest}
"""


@dataclass
class Message:
    to: str
    subject: str
    body: str
    sent_at: datetime
    kind: str  # request | escalation


@dataclass
class RecordedNotifier:
    """Keeps every message in memory so tests and the CLI can read them back."""

    sent: list[Message] = field(default_factory=list)

    def request(self, app, owner, digest, cycle, at: datetime) -> Message:
        msg = Message(
            to=owner.email,
            subject=f"Application confirmation due: {app.name}",
            body=REQUEST_BODY.format(
                owner_name=owner.name.split()[0],
                fields=field_block(app),
                app_id=app.id,
                digest=digest,
            ),
            sent_at=at,
            kind="request",
        )
        self.sent.append(msg)
        return msg

    def clarify(self, app, owner, digest, reason, cycle, at: datetime) -> Message:
        msg = Message(
            to=owner.email,
            subject=f"Still open: {app.name}",
            body=CLARIFICATION_BODY.format(
                owner_name=owner.name.split()[0],
                name=app.name,
                reason=reason.value,
                expires=cycle.expires_at.date().isoformat(),
                app_id=app.id,
                digest=digest,
            ),
            sent_at=at,
            kind="clarification",
        )
        self.sent.append(msg)
        return msg

    def escalate(self, app, owner, manager, digest, cycle, at: datetime) -> Message:
        msg = Message(
            to=manager.email if manager else owner.email,
            subject=f"Overdue application confirmation: {app.name}",
            body=ESCALATION_BODY.format(
                manager_name=(manager.name.split()[0] if manager else owner.name.split()[0]),
                owner_name=owner.name,
                name=app.name,
                app_id=app.id,
                due=cycle.due_at.date().isoformat(),
                expires=cycle.expires_at.date().isoformat(),
                digest=digest,
            ),
            sent_at=at,
            kind="escalation",
        )
        self.sent.append(msg)
        return msg
