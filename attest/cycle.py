"""The attestation cycle: open, ingest replies, escalate, expire, close.

Every state change is written to the evidence log before it is reflected in
memory. Most of them also pass a check in controls.py; AT-5 and AT-8 are
enforced here instead, because they are rules about what may follow what. The ordering is deliberate and
it is the uncomfortable way round: a cycle that mutates and then fails to log
has produced a register nobody can defend, while a cycle that logs and then
fails to mutate has produced a discrepancy somebody can find. Given a choice
between an undetectable error and a detectable one, take the detectable one.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import controls
from .controls import ControlError
from .evidence import EvidenceLog
from .models import (
    Attestation,
    Cycle,
    CycleState,
    Exception_,
    NonAnswer,
)
from .notify import Notifier, RecordedNotifier
from .register import Register
from .responses import classify


class AttestationCycle:
    # A bounce is the only inbound message that closes an item. It is evidence
    # that the request never arrived, so waiting for a decision on it is a
    # fiction, and the remediation differs from silence: fix the mailbox or
    # reassign the owner, rather than chase a person who never got it.
    RESOLVING = {NonAnswer.BOUNCED}

    # Classes of inbound message that the mail system generates about the
    # request, rather than a person generating them in answer to it. Exempt
    # from AT-7 because the sender is expected not to be the owner.
    SYSTEM_ORIGINATED = {NonAnswer.BOUNCED}

    # Exception reasons that a later decision from the owner may overturn.
    # Exactly the ones a non-owner is able to cause, which is exactly the set
    # above. Expiry and withdrawal are not here and never become so.
    SUPERSEDABLE = {NonAnswer.BOUNCED}

    def __init__(
        self,
        register: Register,
        evidence: EvidenceLog,
        notifier: Notifier | None = None,
        due_days: int = 10,
        expiry_days: int = 20,
    ):
        self.register = register
        self.evidence = evidence
        self.notifier: Notifier = notifier or RecordedNotifier()
        self.due_days = due_days
        self.expiry_days = expiry_days
        self.cycle: Cycle | None = None
        self._clock: datetime | None = None

    # ------------------------------------------------------------------ open
    def open(self, cycle_id: str, at: datetime) -> Cycle:
        if self.cycle is not None and self.cycle.state is CycleState.OPEN:
            # Opening over an open cycle discarded everything it had recorded,
            # silently, and returned a fresh one as though nothing had been
            # there. Nothing in the log would have shown it.
            raise ControlError(
                f"cycle {self.cycle.id} is still open; close it first."
            )
        cycle = Cycle(
            id=cycle_id,
            opened_at=at,
            due_at=at + timedelta(days=self.due_days),
            expires_at=at + timedelta(days=self.expiry_days),
        )
        self.evidence.write(
            cycle_id, "cycle_opened", at,
            due_at=cycle.due_at, expires_at=cycle.expires_at,
            in_scope=len(self.register.in_scope()),
        )
        self.cycle = cycle
        self._clock = at

        for app in self.register.in_scope():
            owner = self.register.owner_of(app)
            try:
                controls.check_owner(app, owner)
            except ControlError as exc:
                self._except(
                    app.id, controls.owner_failure_reason(app, owner),
                    str(exc), at,
                )
                continue

            digest = self.register.digest(app.id)
            msg = self.notifier.request(app, owner, digest, cycle, at)
            self.evidence.write(
                cycle_id, "request_sent", at,
                app_id=app.id, to=msg.to, digest=digest,
            )
            cycle.requested[app.id] = digest

        return cycle

    # ---------------------------------------------------------------- ingest
    def ingest(self, sender: str, subject: str, body: str, at: datetime) -> str:
        """Feed one inbound reply in. Returns a short outcome string."""
        c = self._require_open()
        self._check_time(at)
        result = classify(sender, subject, body)

        app_id = result.app_id
        if app_id is None or app_id not in self.register.applications:
            self.evidence.write(
                c.id, "response_discarded", at,
                sender=sender, subject=subject,
                reason=(result.non_answer.value if result.non_answer
                        else "unknown_application"),
                detail=result.detail,
            )
            return "discarded: could not be tied to an application in scope"

        app = self.register.applications[app_id]

        if app_id not in c.requested:
            self.evidence.write(
                c.id, "response_discarded", at,
                app_id=app_id, sender=sender, reason="not_requested",
            )
            return f"discarded: {app_id} was not requested in this cycle"

        # AT-5b. The deadline is a date, not a function call. Guarding only on
        # "has advance() run yet" would make the control depend on the driver:
        # a real mail poller that ingested before ticking the clock would
        # record a full attestation for a reply that arrived weeks late.
        if at >= c.expires_at:
            self.evidence.write(
                c.id, "late_reply_ignored", at,
                app_id=app_id, sender=sender, reason="after_expiry",
            )
            return f"ignored: {app_id} closed on {c.expires_at.date().isoformat()}"

        # Once an item is resolved it stays resolved. Without this, a reply
        # arriving after expiry silently overwrote the "not attested"
        # exception and the cycle reported an attestation for an owner who
        # missed the deadline. Changing a resolved item is what AT-8 is for,
        # and it leaves a record; this did not.
        #
        # One exception, and it is narrow. A bounce is the only inbound
        # message a non-owner can send that closes an item, and the subject
        # line that identifies it is whatever the sender put there. Left
        # terminal, anyone who can send mail could permanently remove an
        # application from coverage. A genuine decision from the accountable
        # owner is better evidence than a delivery failure, so it supersedes
        # one, and the supersession is logged.
        if c.resolved(app_id):
            existing = c.exceptions.get(app_id)
            supersedable = (
                existing is not None
                and existing.reason in self.SUPERSEDABLE
                and app_id not in c.attestations
            )
            if not supersedable:
                self.evidence.write(
                    c.id, "late_reply_ignored", at,
                    app_id=app_id, sender=sender,
                    resolved_as=("attested" if app_id in c.attestations
                                 else c.exceptions[app_id].reason),
                )
                return f"ignored: {app_id} was already resolved in this cycle"

        self.evidence.write(
            c.id, "inbound_received", at, app_id=app_id, sender=sender,
        )
        # A reply arrived and was tied to a requested application. True
        # regardless of whether it turns out to be an attestation, and
        # recorded here so response rate cannot be read off the attestations.
        c.responded.add(app_id)

        # AT-7 runs before AT-2. A message from someone who was not asked
        # should not be able to reach the owner's record at all, not even to
        # file a non-answer against it or to trigger a clarification the owner
        # would then receive about a message they never sent.
        #
        # Delivery failures are exempt, and only delivery failures. A bounce is
        # the mail system reporting on the request rather than anybody
        # answering it, so it legitimately arrives from a daemon address and
        # rejecting it for not being the owner would discard the clearest
        # signal in the cycle: that the owner never got the message.
        if result.non_answer not in self.SYSTEM_ORIGINATED:
            try:
                controls.check_owner_scope(app, sender)
            except ControlError as exc:
                return self._non_answer(
                    app, NonAnswer.WRONG_OWNER, str(exc), sender, at
                )

        # AT-2: is this a decision at all?
        try:
            controls.check_is_decision(result)
        except ControlError as exc:
            return self._non_answer(app, result.non_answer, str(exc), sender, at)

        # AT-3: does it reference the version that was sent?
        requested_digest = c.requested[app_id]
        try:
            controls.check_binding(app_id, requested_digest, result.digest)
        except ControlError as exc:
            return self._non_answer(app, NonAnswer.UNPARSEABLE, str(exc), sender, at)

        # The register can move between the request going out and the reply
        # coming back. The owner is not wrong and the attestation is real; it
        # just describes a version that is no longer current.
        stale = controls.is_stale(requested_digest, self.register.digest(app_id))

        att = Attestation(
            app_id=app_id,
            owner_email=app.owner_email,
            decision=result.decision,
            responded_at=at,
            digest=requested_digest,
            cycle_id=c.id,
            stale=stale,
        )
        superseded = c.exceptions.get(app_id)
        if superseded is not None:
            self.evidence.write(
                c.id, "exception_superseded", at,
                app_id=app_id, was=superseded.reason,
                by=f"decision from {sender}",
            )
        self.evidence.write(
            c.id, "attestation_recorded", at,
            app_id=app_id, owner=app.owner_email,
            decision=result.decision, digest=requested_digest, stale=stale,
        )
        c.exceptions.pop(app_id, None)
        c.attestations[app_id] = att

        if stale:
            self.evidence.write(
                c.id, "attestation_stale_on_arrival", at,
                app_id=app_id, attested_version=requested_digest,
                current_version=self.register.digest(app_id),
            )
            return f"recorded but stale: {app_id} changed while it was out"
        return f"attested: {app_id} {result.decision.value}"

    # -------------------------------------------------------------- advance
    def advance(self, now: datetime) -> dict:
        """Move the clock. Escalate what is overdue, expire what ran out.

        AT-5. Nothing here can turn silence into an attestation. The only
        thing elapsed time does is make an unanswered request louder, and then
        final. The clock is required to move forwards, because an out of order
        call would skip the escalation tier and expire an item nobody ever
        chased, reporting an empty escalation list while doing it.
        """
        c = self._require_open()
        self._check_time(now)
        if self._clock is not None and now < self._clock:
            raise ControlError(
                f"AT-5: the clock cannot go backwards; last advanced to "
                f"{self._clock.isoformat()}, asked for {now.isoformat()}."
            )
        # Moving the clock is itself an action the log should account for.
        # Without this entry, a cycle that escalated nothing and expired
        # nothing leaves no trace of having been checked at all, and "nobody
        # ran it" and "it ran and found nothing" look identical afterwards.
        self.evidence.write(
            c.id, "clock_advanced", now, previous=self._clock,
        )
        self._clock = now
        escalated, expired = [], []

        for app_id in list(c.requested):
            if c.resolved(app_id):
                continue
            app = self.register.applications[app_id]
            owner = self.register.owner_of(app)

            if now >= c.expires_at:
                seen = c.non_answers.get(app_id, [])
                detail = f"no decision by {c.expires_at.date().isoformat()}"
                if seen:
                    detail += (
                        "; received but did not decide: "
                        + ", ".join(n.value for n in seen)
                    )
                self._except(app_id, NonAnswer.NO_RESPONSE, detail, now)
                expired.append(app_id)
                continue

            if now >= c.due_at and app_id not in c.escalated:
                manager = (
                    self.register.owners.get((owner.manager_email or "").lower())
                    if owner else None
                )
                msg = self.notifier.escalate(
                    app, owner, manager, c.requested[app_id], c, now
                )
                self.evidence.write(
                    c.id, "escalated", now,
                    app_id=app_id, to=msg.to, manager=bool(manager),
                )
                c.escalated.add(app_id)
                escalated.append(app_id)

        return {"escalated": escalated, "expired": expired}

    # ----------------------------------------------------------------- amend
    def amend(self, app_id: str, changes: dict, at: datetime) -> None:
        """Change a register record while a cycle is open.

        This is the event the digest mechanism exists for. Somebody edits a
        record after its owner confirmed it, the edit is legitimate, nobody
        involved is doing anything wrong, and the register silently acquires
        an attestation describing a version that no longer exists.

        A revert is logged too. Editing a record back to a previously attested
        state restores coverage, because AT-6 checks the register live, and a
        number that moves without an entry explaining it is the thing this
        project is supposed to be against.
        """
        c = self._require_open()
        self._check_time(at)
        if app_id not in self.register.applications:
            raise ControlError(f"AT-3: {app_id} is not in the register.")
        if "id" in changes:
            raise ControlError("AT-3: an application's id cannot be amended.")

        before = self.register.digest(app_id)
        after = self.register.digest_after(app_id, **changes)
        att = c.attestations.get(app_id)
        was_valid = bool(att) and not att.revoked and att.digest == before
        now_valid = bool(att) and not att.revoked and att.digest == after

        self.evidence.write(
            c.id, "register_amended", at,
            app_id=app_id, changes=changes, before=before, after=after,
        )
        if was_valid and not now_valid:
            self.evidence.write(
                c.id, "attestation_invalidated", at,
                app_id=app_id, attested_version=att.digest,
                current_version=after,
            )
        elif att is not None and not was_valid and now_valid:
            self.evidence.write(
                c.id, "attestation_revalidated", at,
                app_id=app_id, attested_version=att.digest,
                current_version=after,
            )

        self.register.amend(app_id, **changes)
        if att is not None and not att.revoked:
            att.stale = not now_valid

    # ----------------------------------------------------------------- close
    def close(self, at: datetime) -> Cycle:
        from .reporting import is_valid

        c = self._require_open()
        self._check_time(at)
        controls.check_complete(c, [a.id for a in self.register.in_scope()])
        self.evidence.write(
            c.id, "cycle_closed", at,
            attested=len([a for a in c.attestations.values()
                          if is_valid(a, self.register)]),
            exceptions=len(c.exceptions),
        )
        c.state = CycleState.CLOSED
        c.closed_at = at
        return c

    # ---------------------------------------------------------------- revoke
    def revoke(self, app_id: str, reason: str, at: datetime) -> Attestation:
        """AT-8. Withdraw an attestation. The log keeps both halves."""
        c = self._require_open()
        self._check_time(at)
        if app_id not in c.attestations:
            raise ControlError(f"AT-8: no attestation for {app_id} to revoke.")

        self.evidence.write(
            c.id, "attestation_revoked", at, app_id=app_id, reason=reason
        )
        att = c.attestations[app_id]
        att.revoked = True
        att.revoked_reason = reason
        self._except(
            app_id, NonAnswer.REVOKED, f"attestation revoked: {reason}", at
        )
        return att

    # ------------------------------------------------------------ non-answer
    def _non_answer(self, app, reason: NonAnswer, detail: str,
                    sender: str, at: datetime) -> str:
        """Record something that came back and was not an attestation.

        Almost none of these close the item. An owner who replies with prose,
        or whose colleague answers for them, or who is out of the office, has
        not attested and has also not refused. Treating any of that as a
        resolution would let the cycle report a tidy exception list while the
        actual question stayed unanswered, and would remove the last chance to
        get a real answer before the deadline.
        """
        c = self.cycle
        self.evidence.write(
            c.id, "non_answer_recorded", at,
            app_id=app.id, reason=reason, sender=sender, detail=detail,
        )
        c.non_answers.setdefault(app.id, []).append(reason)

        if reason in self.RESOLVING:
            self._except(app.id, reason, detail, at)
            return f"undeliverable: {app.id} closed, the request never arrived"

        if reason in (NonAnswer.UNPARSEABLE, NonAnswer.AMBIGUOUS):
            owner = self.register.owner_of(app)
            if owner:
                msg = self.notifier.clarify(
                    app, owner, c.requested[app.id], reason, c, at
                )
                self.evidence.write(
                    c.id, "clarification_sent", at, app_id=app.id, to=msg.to,
                )
                return (f"not a decision ({reason.value}), "
                        f"clarification sent, still open")
        return f"not a decision ({reason.value}), still open"

    # ---------------------------------------------------------------- helper
    def _except(self, app_id: str, reason: NonAnswer, detail: str, at: datetime):
        c = self.cycle
        self.evidence.write(
            c.id, "exception_recorded", at,
            app_id=app_id, reason=reason, detail=detail,
        )
        c.exceptions[app_id] = Exception_(app_id=app_id, reason=reason, detail=detail)

    def _require_open(self) -> Cycle:
        if self.cycle is None:
            raise ControlError("no cycle is open.")
        if self.cycle.state is not CycleState.OPEN:
            raise ControlError(f"cycle {self.cycle.id} is closed.")
        return self.cycle

    def _check_time(self, at: datetime) -> None:
        """Nothing may be dated before the cycle opened.

        Time being a parameter is what makes AT-5 testable. It also means the
        evidence log's chronology is whatever the caller says it is, so the
        one property worth enforcing is that nothing predates the cycle it
        belongs to.
        """
        c = self.cycle
        if c is not None and at < c.opened_at:
            raise ControlError(
                f"AT-4: {at.isoformat()} predates the cycle, which opened "
                f"{c.opened_at.isoformat()}."
            )
