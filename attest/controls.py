"""The controls, as enforced code rather than as policy text.

Separated from the cycle logic on purpose, so that most of the control surface
can be read without reading the orchestration around it.

It is most of the surface, not all of it. AT-4 is in evidence.py, AT-5 and AT-8
are sequencing and lifecycle rules that live in cycle.py because they are about
the order things happen in, and AT-6's live coverage check is in reporting.py.
CONTROLS.md maps every one of them to the code that enforces it; this file is
where the ones that are decisions about a single message live.

AT-1  OwnerRequired          an unowned application is an exception, not a skip
AT-2  ExplicitResponseOnly   silence and prose are not attestations
AT-3  BindToSnapshot         an attestation names the version it agreed to
AT-4  EvidenceLog            in evidence.py, append-only
AT-5  EscalateAndExpire      an unanswered request ends as not attested
AT-6  CoverageOverPopulation coverage is measured against everything in scope
AT-7  OwnerScope             only the recorded owner may attest
AT-8  Revocable              an attestation can be withdrawn, the log cannot
"""

from __future__ import annotations

from .models import Application, NonAnswer, Owner


class ControlError(Exception):
    """Raised when something is refused. Carries the control id in the text."""


# ---------------------------------------------------------------------- AT-1
def check_owner(app: Application, owner: Owner | None) -> None:
    """An application nobody owns cannot be attested by anybody.

    The failure mode this exists for is quiet: an application with a blank
    owner column is skipped by the loop, never appears in the exception list,
    and the cycle reports 100% of what it asked about. The number is true and
    the impression it gives is false.
    """
    if app.owner_email is None or not str(app.owner_email).strip():
        raise ControlError(f"AT-1: {app.id} has no owner recorded.")
    if owner is None:
        raise ControlError(
            f"AT-1: {app.id} names owner '{app.owner_email}', "
            "who is not in the owner directory."
        )
    if not owner.active:
        raise ControlError(
            f"AT-1: {app.id} is owned by {owner.email}, who is not active."
        )


def owner_failure_reason(app: Application, owner: Owner | None) -> NonAnswer:
    if app.owner_email is None or not str(app.owner_email).strip():
        return NonAnswer.NO_OWNER
    if owner is None:
        return NonAnswer.NO_OWNER
    return NonAnswer.OWNER_INACTIVE


# ---------------------------------------------------------------------- AT-7
def check_owner_scope(app: Application, sender: str) -> None:
    """The reply has to come from the person who was asked.

    Accepting a colleague's reply is the helpful thing to do and it destroys
    the only claim the record makes, which is that a named accountable person
    said this.
    """
    if not sender or sender.strip().lower() != (app.owner_email or "").lower():
        raise ControlError(
            f"AT-7: {app.id} was sent to {app.owner_email}; "
            f"the reply came from {sender or 'an unknown sender'}."
        )


# ---------------------------------------------------------------------- AT-3
def check_binding(app_id: str, requested_digest: str, replied_digest: str | None) -> None:
    """The reply must carry back the reference it was sent.

    Reached by a reply whose reference is present but empty or corrupted. A
    reply with no reference at all never gets here: it cannot be tied to an
    application, so it is discarded upstream and logged as such.
    """
    if not replied_digest:
        raise ControlError(
            f"AT-3: reply for {app_id} carries an empty record reference."
        )
    if replied_digest != requested_digest:
        raise ControlError(
            f"AT-3: reply for {app_id} references record version "
            f"{replied_digest}, but {requested_digest} was sent."
        )


def is_stale(requested_digest: str, current_digest: str) -> bool:
    """True when the record moved under an attestation that was valid when made.

    Not an error. The owner did nothing wrong and the attestation is real
    evidence of what they saw. It just is not evidence about the record as it
    stands now, so AT-6 declines to count it and the cycle says so out loud.
    """
    return requested_digest != current_digest


# ---------------------------------------------------------------------- AT-2
def check_is_decision(classified) -> None:
    """Only an explicit decision counts. Everything else has a reason."""
    if not classified.is_decision:
        raise ControlError(
            f"AT-2: not an attestation ({classified.non_answer.value})"
            + (f": {classified.detail}" if classified.detail else "")
        )


# ---------------------------------------------------------------------- AT-6
def check_complete(cycle, in_scope_ids) -> None:
    """A cycle cannot be reported complete with anything unresolved."""
    unresolved = [a for a in in_scope_ids if not cycle.resolved(a)]
    if unresolved:
        raise ControlError(
            f"AT-6: {len(unresolved)} application(s) neither attested nor "
            f"excepted: {', '.join(sorted(unresolved)[:5])}"
            + (" ..." if len(unresolved) > 5 else "")
        )
