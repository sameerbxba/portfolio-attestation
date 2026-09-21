"""Tests for the paths an adversarial read of the first version found.

Every test here exists because a control was claimed in CONTROLS.md and either
had no test, or had one that passed with the control removed. They are kept in
their own file rather than folded in, because the reason they exist is worth
being able to see.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from attest.controls import ControlError
from attest.models import Criticality, Decision, NonAnswer
from attest.notify import field_block
from attest.register import record_digest
from attest.reporting import coverage, is_valid

from .conftest import OPENED, app, day, owner, reply


# ------------------------------------------------- AT-5, expiry is terminal
def test_a_reply_after_expiry_cannot_undo_the_expiry(make_cycle, log):
    """The bug this file was written for.

    Expiry recorded NO_RESPONSE, and then a late reply overwrote the exception
    and recorded a full attestation, taking coverage to 100%. Every claim the
    project makes about silence never becoming an attestation was false by a
    route nobody had thought to try.
    """
    cycle, reg = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.advance(day(21))
    assert cycle.cycle.exceptions["APP-001"].reason is NonAnswer.NO_RESPONSE

    out = cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(40))

    assert "ignored" in out
    assert "APP-001" not in cycle.cycle.attestations
    assert cycle.cycle.exceptions["APP-001"].reason is NonAnswer.NO_RESPONSE
    assert coverage(cycle.cycle, reg).valid == 0
    # Ignored, not silently dropped. Somebody did answer, late, and that is
    # worth knowing when the next cycle opens.
    assert any(r["event"] == "late_reply_ignored" for r in log.read("TEST"))


def test_a_second_reply_cannot_quietly_replace_a_recorded_decision(make_cycle):
    cycle, _ = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(1))
    cycle.ingest("ravi.menon@x.example", "RE:",
                 reply("APP-001", d, token="RETIRE"), day(2))
    # Changing a recorded decision is what AT-8 is for, and it leaves a trail.
    assert cycle.cycle.attestations["APP-001"].decision is Decision.CONFIRM


# ----------------------------------------------------- AT-4/AT-5, the clock
def test_the_clock_cannot_go_backwards(make_cycle):
    cycle, _ = make_cycle()
    cycle.advance(day(21))
    with pytest.raises(ControlError, match="AT-5"):
        cycle.advance(day(11))


def test_out_of_order_advance_would_have_skipped_escalation(make_cycle):
    """Why the guard above is worth having.

    Without it, advancing to expiry and then back to the due date expired the
    item having never escalated it, and reported an empty escalation list
    while doing so.
    """
    cycle, _ = make_cycle()
    cycle.advance(day(21))
    assert cycle.cycle.escalated == set()
    with pytest.raises(ControlError):
        cycle.advance(day(11))


def test_nothing_can_be_dated_before_the_cycle_opened(make_cycle):
    cycle, _ = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    with pytest.raises(ControlError, match="AT-4"):
        cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d),
                     OPENED - timedelta(days=400))


# --------------------------------------------- AT-3, the live staleness check
def test_a_record_that_moves_while_the_request_is_out_arrives_stale(make_cycle):
    """Exercises controls.is_stale, which nothing previously reached.

    The digest the owner quotes is the one they were sent. If the register
    moved in between, the attestation is genuine and already out of date the
    moment it is recorded.
    """
    cycle, reg = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.amend("APP-001", {"vendor": "Northwind Systems EMEA"}, day(1))

    out = cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(2))

    assert "stale" in out
    att = cycle.cycle.attestations["APP-001"]
    assert att.stale is True
    assert not is_valid(att, reg)
    assert coverage(cycle.cycle, reg).valid == 0


def test_reverting_a_record_restores_coverage_and_says_so(make_cycle, log):
    """A record edited away from what was attested, and then back.

    The attestation describes the current record again, so coverage returns.
    That is defensible and it is also a number moving on its own, which is
    why both the invalidation and the revalidation are written to the log.

    This test defends cycle.amend's bookkeeping. The live check in is_valid
    is defended separately, below, because amend maintains the flag too and
    the two agree on every path that goes through it.
    """
    cycle, reg = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(1))

    cycle.amend("APP-001", {"criticality": Criticality.TIER_3}, day(2))
    assert coverage(cycle.cycle, reg).valid == 0

    cycle.amend("APP-001", {"criticality": Criticality.TIER_1}, day(3))
    assert coverage(cycle.cycle, reg).valid == 1

    events = [r["event"] for r in log.read("TEST")]
    assert "attestation_invalidated" in events
    assert "attestation_revalidated" in events


def test_a_non_attestable_field_changing_does_not_move_the_digest():
    """Defends ATTESTABLE_FIELDS actually being a subset.

    The previous version of this test amended a field to the value it already
    held, so it passed even when the digest covered the entire record.
    """
    before = record_digest(app("APP-001", in_scope=True))
    after = record_digest(app("APP-001", in_scope=False))
    assert before == after

    changed = record_digest(app("APP-001", criticality=Criticality.TIER_3))
    assert changed != before


def test_the_owner_is_shown_exactly_the_fields_the_digest_covers():
    """The request body is generated from ATTESTABLE_FIELDS, not written out.

    A field in the digest that the owner was never shown is the quiet version
    of this failure, and writing the list twice is how you get it.
    """
    a = app("APP-001")
    block = field_block(a)
    for value in a.attestable().values():
        value = value.value if hasattr(value, "value") else value
        assert str(value) in block


def test_a_corrupted_reference_keeps_its_application_and_is_refused(make_cycle):
    """A mangled digest used to fail the pattern, lose the app id with it, and
    land in the discard pile as untraceable."""
    cycle, _ = make_cycle()
    cycle.ingest("ravi.menon@x.example", "RE:",
                 "CONFIRM\n\nREF: APP-001:not-a-digest\n", day(1))
    assert NonAnswer.UNPARSEABLE in cycle.cycle.non_answers["APP-001"]
    assert "APP-001" not in cycle.cycle.attestations


def test_an_empty_reference_is_refused_by_the_binding_control(make_cycle):
    cycle, _ = make_cycle()
    cycle.ingest("ravi.menon@x.example", "RE:", "CONFIRM\n\nREF: APP-001:\n", day(1))
    assert NonAnswer.UNPARSEABLE in cycle.cycle.non_answers["APP-001"]


def test_an_application_id_cannot_be_amended(make_cycle):
    cycle, _ = make_cycle()
    with pytest.raises(ControlError, match="AT-3"):
        cycle.amend("APP-001", {"id": "APP-999"}, day(2))


def test_amending_something_not_in_the_register_is_refused_not_a_traceback(make_cycle):
    cycle, _ = make_cycle()
    with pytest.raises(ControlError, match="AT-3"):
        cycle.amend("APP-404", {"vendor": "x"}, day(2))


# ------------------------------------------------------------ AT-7 ordering
def test_a_stranger_cannot_trigger_a_clarification_to_the_owner(make_cycle):
    """AT-7 used to run after AT-2.

    A stranger's prose was filed against the owner's application and sent the
    owner a clarification about a message they never wrote.
    """
    cycle, _ = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("stranger@elsewhere.example", "RE:",
                 f"looks fine to me\n\nREF: APP-001:{d}\n", day(1))
    assert cycle.cycle.non_answers["APP-001"] == [NonAnswer.WRONG_OWNER]
    assert not any(m.kind == "clarification" for m in cycle.notifier.sent)


def test_a_delivery_failure_is_exempt_from_the_owner_check(make_cycle):
    """And only a delivery failure.

    A bounce arrives from a mail daemon by definition. Rejecting it for not
    being the owner would throw away the clearest signal in the cycle.
    """
    cycle, _ = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("postmaster@x.example", "Undeliverable: confirmation due",
                 f"Mailbox full.\n\nREF: APP-001:{d}\n", day(1))
    assert cycle.cycle.exceptions["APP-001"].reason is NonAnswer.BOUNCED


# ---------------------------------------------------------------- AT-8, AT-4
def test_a_withdrawal_is_filed_as_a_withdrawal_not_as_silence(make_cycle):
    """It used to be recorded with reason NO_RESPONSE.

    For an application whose owner did respond, with a decision, which was
    recorded. In a project whose argument is that a cycle unable to say why it
    is missing records is not evidence of anything, a false reason code is
    worse than a missing one.
    """
    cycle, _ = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(1))
    cycle.revoke("APP-001", "owner misread the vendor row", day(3))
    assert cycle.cycle.exceptions["APP-001"].reason is NonAnswer.REVOKED


def test_a_closed_cycle_cannot_have_its_coverage_changed(make_cycle):
    """revoke used to skip the open check.

    The cycle's own closing entry recorded one attestation while live coverage
    afterwards said zero, and nothing flagged the disagreement.
    """
    cycle, _ = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(1))
    cycle.close(day(2))
    with pytest.raises(ControlError, match="closed"):
        cycle.revoke("APP-001", "too late", day(3))


def test_every_exception_reaches_the_evidence_log(make_cycle, log):
    """AT-4 claimed this and no test checked it."""
    cycle, _ = make_cycle([app("APP-001"), app("APP-002", owner_email=None)])
    cycle.advance(day(21))
    logged = {
        r["app_id"] for r in log.read("TEST")
        if r["event"] == "exception_recorded"
    }
    assert logged == {"APP-001", "APP-002"}


def test_the_log_is_written_before_the_state_it_describes(make_cycle, log):
    """The ordering claim in cycle.py, checked rather than asserted in prose.

    A notifier that raises part way through leaves the log entry and no state
    change, which is the detectable failure. The other order leaves a state
    change nobody can account for.
    """
    cycle, _ = make_cycle()
    d = cycle.cycle.requested["APP-001"]

    class Boom(Exception):
        pass

    def explode(*a, **kw):
        raise Boom()

    cycle.notifier.clarify = explode
    with pytest.raises(Boom):
        cycle.ingest("ravi.menon@x.example", "RE:",
                     f"looks fine\n\nREF: APP-001:{d}\n", day(1))

    assert any(r["event"] == "non_answer_recorded" for r in log.read("TEST"))


# ------------------------------------- AT-6, the live check, defended properly
def test_coverage_follows_the_register_not_the_flag(make_cycle):
    """Defends reporting.is_valid rather than cycle.amend's bookkeeping.

    The earlier test for this went through `cycle.amend`, which maintains the
    stale flag itself, so flag and live check agreed on every path the suite
    reached and `is_valid` could be replaced by `return not att.stale` with
    nothing going red. Here the register is changed underneath the cycle, the
    way any code holding a reference to it could, and the flag is left saying
    what it said before.
    """
    cycle, reg = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(1))

    att = cycle.cycle.attestations["APP-001"]
    reg.amend("APP-001", vendor="Somebody Else Entirely")

    assert att.stale is False          # the flag is out of date
    assert not is_valid(att, reg)      # the register is not
    assert coverage(cycle.cycle, reg).valid == 0


def test_the_empty_reference_refusal_says_what_was_wrong(make_cycle, log):
    """The message, not just the outcome.

    An empty digest and a wrong digest both end as UNPARSEABLE, so a test that
    checks only the outcome passes with the empty-reference branch deleted.
    """
    cycle, _ = make_cycle()
    cycle.ingest("ravi.menon@x.example", "RE:", "CONFIRM\n\nREF: APP-001:\n", day(1))
    details = [
        r["detail"] for r in log.read("TEST")
        if r["event"] == "non_answer_recorded"
    ]
    assert any("empty record reference" in d for d in details)


# -------------------------------------------------- AT-5b, expiry and bounces
def test_a_reply_dated_after_expiry_is_ignored_even_if_the_clock_never_moved(make_cycle):
    """The deadline is a date, not a function call.

    Guarding only on "has advance() run" makes the control depend on the
    driver. A real mail poller that ingested before ticking would have
    recorded a full attestation for a reply weeks late.
    """
    cycle, reg = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    out = cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(40))
    assert "ignored" in out
    assert coverage(cycle.cycle, reg).valid == 0


def test_a_forged_bounce_cannot_permanently_close_an_application(make_cycle, log):
    """A bounce is the one message a non-owner can use to resolve an item.

    Its subject line is whatever the sender wrote, so leaving that resolution
    terminal would let anyone who can send mail remove an application from
    coverage for the quarter. A decision from the accountable owner is better
    evidence than a delivery failure, so it supersedes one.
    """
    cycle, reg = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("stranger@elsewhere.example", "Undeliverable: confirmation due",
                 f"nope\n\nREF: APP-001:{d}\n", day(1))
    assert cycle.cycle.exceptions["APP-001"].reason is NonAnswer.BOUNCED

    cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(2))

    assert coverage(cycle.cycle, reg).valid == 1
    assert "APP-001" not in cycle.cycle.exceptions
    assert any(r["event"] == "exception_superseded" for r in log.read("TEST"))


def test_an_expiry_is_not_supersedable(make_cycle):
    """Only the reasons a non-owner can cause are reversible this way.

    The reply is dated before the deadline on purpose. `ingest` does not
    enforce the clock, only `advance` does, so a backdated message after an
    expiry gets past the AT-5b timestamp guard and reaches the supersession
    branch. An earlier version of this test dated the reply on the expiry day,
    where the timestamp guard answered first and the test passed with
    SUPERSEDABLE widened to every reason there is.
    """
    cycle, reg = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.advance(day(21))
    assert cycle.cycle.exceptions["APP-001"].reason is NonAnswer.NO_RESPONSE

    out = cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(5))

    assert "already resolved" in out
    assert coverage(cycle.cycle, reg).valid == 0
    assert cycle.cycle.exceptions["APP-001"].reason is NonAnswer.NO_RESPONSE


def test_a_withdrawal_is_not_supersedable(make_cycle):
    cycle, reg = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(1))
    cycle.revoke("APP-001", "owner misread the vendor row", day(2))
    cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(3))
    assert coverage(cycle.cycle, reg).valid == 0
    assert cycle.cycle.exceptions["APP-001"].reason is NonAnswer.REVOKED


# ---------------------------------------------------- AT-3, the REF line
def test_a_reference_followed_by_punctuation_is_still_a_valid_reference(make_cycle):
    """The loosened pattern must not reject genuine answers.

    Capturing to the next whitespace swallowed a trailing full stop or a
    closing angle bracket from a mail client, turning a real decision into an
    unparseable one. Under the original strict pattern these all worked.
    """
    for suffix in (".", ">", ",", "</p>"):
        cycle, reg = make_cycle()
        d = cycle.cycle.requested["APP-001"]
        cycle.ingest("ravi.menon@x.example", "RE:",
                     f"CONFIRM\n\nREF: APP-001:{d}{suffix}\n", day(1))
        assert coverage(cycle.cycle, reg).valid == 1, f"rejected a reply ending {suffix!r}"


def test_a_reference_inside_a_url_or_a_header_is_not_read_as_the_reference(make_cycle):
    """Anchored to a line for the same reason the decision tokens are.

    The reference decides which record the answer is filed against, so it is
    the last thing that should be picked up from text the owner did not write
    on this occasion.
    """
    cycle, reg = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    body = (
        "CONFIRM\n\n"
        "see https://wiki.example/x-REF:APP-999:deadbeef00ff for background\n"
        f"REF: APP-001:{d}\n"
    )
    cycle.ingest("ravi.menon@x.example", "RE:", body, day(1))
    assert coverage(cycle.cycle, reg).valid == 1


def test_references_to_two_different_applications_are_not_attributed(make_cycle):
    """There is no honest way to choose between them.

    Filing against whichever appeared first would attach a decision to a
    record the owner may not have been looking at. The message is logged as
    discarded instead, which is a thing a person can go and read.
    """
    cycle, _ = make_cycle([app("APP-001"), app("APP-002")])
    d = cycle.cycle.requested["APP-001"]
    body = f"CONFIRM\n\nREF: APP-001:{d}\n\n> REF: APP-002:aaaaaaaaaaaa\n"
    out = cycle.ingest("ravi.menon@x.example", "RE:", body, day(1))
    assert "discarded" in out
    assert cycle.cycle.attestations == {}


def test_two_versions_of_the_same_application_are_ambiguous(make_cycle):
    """Same record, two versions quoted. The application is known, the
    version is not, so it is filed against that application as ambiguous."""
    cycle, _ = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    body = f"CONFIRM\n\nREF: APP-001:{d}\n\n> REF: APP-001:aaaaaaaaaaaa\n"
    cycle.ingest("ravi.menon@x.example", "RE:", body, day(1))
    assert "APP-001" not in cycle.cycle.attestations
    assert NonAnswer.AMBIGUOUS in cycle.cycle.non_answers["APP-001"]


def test_a_quoted_copy_of_the_same_reference_is_fine(make_cycle):
    """The common case: the thread below the reply quotes the request."""
    cycle, reg = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    body = f"CONFIRM\n\nREF: APP-001:{d}\n\n> REF: APP-001:{d}\n"
    cycle.ingest("ravi.menon@x.example", "RE:", body, day(1))
    assert coverage(cycle.cycle, reg).valid == 1


# ------------------------------------------------------------- AT-4, the cycle
def test_moving_the_clock_is_itself_recorded(make_cycle, log):
    """Otherwise "nobody ran it" and "it ran and found nothing" look the same."""
    cycle, _ = make_cycle()
    cycle.advance(day(2))
    assert any(r["event"] == "clock_advanced" for r in log.read("TEST"))


def test_a_cycle_cannot_be_opened_over_an_open_one(make_cycle):
    """It used to discard everything the open cycle held, silently."""
    cycle, _ = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(1))
    with pytest.raises(ControlError, match="still open"):
        cycle.open("TEST-2", day(2))
    assert "APP-001" in cycle.cycle.attestations
