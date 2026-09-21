"""One or more tests per control.

Each test is named for the claim it defends, so a reader can check the control
matrix against this file without running anything.
"""

from __future__ import annotations

import pytest

from attest.controls import ControlError
from attest.models import Criticality, Decision, NonAnswer
from attest.notify import REQUEST_BODY, field_block
from attest.reporting import coverage, is_valid
from attest.responses import classify

from .conftest import app, day, owner, reply


# ---------------------------------------------------------------------- AT-1
def test_application_with_no_owner_is_an_exception_not_a_skip(make_cycle):
    cycle, _ = make_cycle([app("APP-001"), app("APP-002", owner_email=None)])
    c = cycle.cycle
    assert "APP-002" not in c.requested
    assert c.exceptions["APP-002"].reason is NonAnswer.NO_OWNER
    # The point of the control: it is resolved, visible, and counted against
    # the population rather than quietly dropped from the denominator.
    assert coverage(c, cycle.register).in_scope == 2


def test_owner_not_in_the_directory_is_an_exception(make_cycle):
    cycle, _ = make_cycle([app("APP-001", owner_email="ghost@x.example")])
    assert cycle.cycle.exceptions["APP-001"].reason is NonAnswer.NO_OWNER


def test_inactive_owner_is_an_exception(make_cycle):
    cycle, _ = make_cycle(
        [app("APP-001", owner_email="lena@x.example")],
        [owner(email="lena@x.example", name="Lena Ostrowski", active=False)],
    )
    assert cycle.cycle.exceptions["APP-001"].reason is NonAnswer.OWNER_INACTIVE


# ---------------------------------------------------------------------- AT-2
def test_prose_agreement_is_not_an_attestation(make_cycle):
    cycle, reg = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest(
        "ravi.menon@x.example", "RE: confirmation",
        f"Yes that all looks right to me, thanks\n\nREF: APP-001:{d}\n", day(1),
    )
    assert "APP-001" not in cycle.cycle.attestations
    assert NonAnswer.UNPARSEABLE in cycle.cycle.non_answers["APP-001"]


def test_two_tokens_is_ambiguous_rather_than_a_guess(make_cycle):
    cycle, _ = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest(
        "ravi.menon@x.example", "RE: confirmation",
        f"CONFIRM\n\nactually no\n\nRETIRE\n\nREF: APP-001:{d}\n", day(1),
    )
    assert "APP-001" not in cycle.cycle.attestations
    assert NonAnswer.AMBIGUOUS in cycle.cycle.non_answers["APP-001"]


def test_auto_reply_is_not_an_attestation_but_keeps_its_application(make_cycle):
    cycle, _ = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest(
        "ravi.menon@x.example", "Automatic reply: confirmation due",
        f"I am out of the office.\n\nREF: APP-001:{d}\n", day(1),
    )
    assert "APP-001" not in cycle.cycle.attestations
    # Attributed, not discarded. That is the difference between a known gap
    # and an unattributable message in a pile.
    assert NonAnswer.AUTO_REPLY in cycle.cycle.non_answers["APP-001"]


def test_the_instruction_text_quoted_back_is_not_read_as_an_answer():
    """The request lists all three tokens. A loose match reads them as a reply.

    This is the failure that made token matching line-anchored: the owner
    replies with nothing but a quote of the original request, and a substring
    search finds CONFIRM, UPDATE and RETIRE in the instructions and has to
    decide between them.
    """
    body = REQUEST_BODY.format(
        owner_name="Ravi", fields=field_block(app()),
        app_id="APP-001", digest="a" * 12,
    )
    result = classify("ravi.menon@x.example", "RE: confirmation", body)
    assert not result.is_decision
    assert result.non_answer is NonAnswer.UNPARSEABLE


def test_an_explicit_decision_is_accepted(make_cycle):
    cycle, reg = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(1))
    att = cycle.cycle.attestations["APP-001"]
    assert att.decision is Decision.CONFIRM
    assert is_valid(att, reg)


# ---------------------------------------------------------------------- AT-3
def test_reply_with_no_reference_cannot_be_bound(make_cycle):
    cycle, _ = make_cycle()
    out = cycle.ingest(
        "ravi.menon@x.example", "RE:", "CONFIRM\n", day(1),
    )
    assert "discarded" in out
    assert "APP-001" not in cycle.cycle.attestations


def test_reply_referencing_a_different_version_is_refused(make_cycle):
    cycle, _ = make_cycle()
    cycle.ingest(
        "ravi.menon@x.example", "RE:", reply("APP-001", "0" * 12), day(1),
    )
    assert "APP-001" not in cycle.cycle.attestations
    assert NonAnswer.UNPARSEABLE in cycle.cycle.non_answers["APP-001"]


def test_amendment_after_an_attestation_stops_it_counting(make_cycle):
    cycle, reg = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(1))
    assert coverage(cycle.cycle, reg).valid == 1

    cycle.amend("APP-001", {"criticality": Criticality.TIER_3}, day(4))

    cov = coverage(cycle.cycle, reg)
    assert cov.valid == 0
    assert cov.stale == 1
    # The attestation itself is not deleted. It is real evidence of what the
    # owner was shown and agreed to; it just is not evidence about now.
    assert cycle.cycle.attestations["APP-001"].decision is Decision.CONFIRM


def test_an_amendment_that_changes_nothing_attestable_leaves_it_valid(make_cycle):
    cycle, reg = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(1))
    cycle.amend("APP-001", {"in_scope": True}, day(4))
    assert coverage(cycle.cycle, reg).valid == 1


# ---------------------------------------------------------------------- AT-4
def test_evidence_log_records_every_event_and_survives_a_reread(make_cycle, log):
    cycle, _ = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(1))
    cycle.close(day(2))

    events = [r["event"] for r in log.read("TEST")]
    for expected in ("cycle_opened", "request_sent",
                     "attestation_recorded", "cycle_closed"):
        assert expected in events
    # Re-reading from disk gives the same thing, which is the only property
    # that makes the log usable as evidence after the process is gone.
    assert log.read("TEST") == log.read("TEST")


def test_a_refusal_is_logged_not_just_returned(make_cycle, log):
    cycle, _ = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("someone.else@x.example", "RE:", reply("APP-001", d), day(1))
    assert any(r["event"] == "non_answer_recorded" for r in log.read("TEST"))


# ---------------------------------------------------------------------- AT-5
def test_silence_escalates_then_expires_and_never_becomes_an_attestation(make_cycle):
    cycle, _ = make_cycle()
    assert cycle.advance(day(11))["escalated"] == ["APP-001"]
    assert cycle.advance(day(21))["expired"] == ["APP-001"]
    assert cycle.cycle.attestations == {}
    assert cycle.cycle.exceptions["APP-001"].reason is NonAnswer.NO_RESPONSE


def test_escalation_goes_to_the_manager_when_there_is_one(make_cycle):
    cycle, _ = make_cycle()
    cycle.advance(day(11))
    escalations = [m for m in cycle.notifier.sent if m.kind == "escalation"]
    assert escalations and escalations[0].to == "dana.whitfield@x.example"


def test_escalation_happens_once_not_on_every_tick(make_cycle):
    cycle, _ = make_cycle()
    cycle.advance(day(11))
    assert cycle.advance(day(12))["escalated"] == []


def test_an_unusable_reply_keeps_the_request_open_rather_than_closing_it(make_cycle):
    cycle, _ = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("ravi.menon@x.example", "RE:",
                 f"looks fine\n\nREF: APP-001:{d}\n", day(1))
    assert not cycle.cycle.resolved("APP-001")
    assert any(m.kind == "clarification" for m in cycle.notifier.sent)
    # And a real decision afterwards still lands.
    cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(3))
    assert cycle.cycle.attestations["APP-001"].decision is Decision.CONFIRM


def test_a_bounce_closes_the_item_with_its_own_reason(make_cycle):
    cycle, _ = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("postmaster@x.example", "Undeliverable: confirmation due",
                 f"Mailbox full.\n\nREF: APP-001:{d}\n", day(1))
    exc = cycle.cycle.exceptions["APP-001"]
    # Distinct from silence, because the fix is different: repair the mailbox
    # or reassign the owner, rather than chase a person who never got it.
    assert exc.reason is NonAnswer.BOUNCED


# ---------------------------------------------------------------------- AT-6
def test_a_cycle_cannot_close_with_anything_unresolved(make_cycle):
    cycle, _ = make_cycle()
    with pytest.raises(ControlError, match="AT-6"):
        cycle.close(day(2))


def test_coverage_is_measured_against_the_population_not_the_responders(make_cycle):
    cycle, reg = make_cycle([
        app("APP-001"),
        app("APP-002", owner_email=None),
        app("APP-003", owner_email=None),
    ])
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(1))

    cov = coverage(cycle.cycle, reg)
    assert cov.requested == 1 and cov.responded == 1
    assert cov.response_rate_pct == 100.0   # the flattering one
    assert round(cov.coverage_pct, 1) == 33.3
    assert cov.in_scope == 3


def test_out_of_scope_applications_are_never_requested_or_counted(make_cycle):
    cycle, reg = make_cycle([app("APP-001"), app("APP-002", in_scope=False)])
    assert "APP-002" not in cycle.cycle.requested
    assert "APP-002" not in cycle.cycle.exceptions
    assert coverage(cycle.cycle, reg).in_scope == 1


# ---------------------------------------------------------------------- AT-7
def test_a_colleague_answering_on_the_owners_behalf_is_refused(make_cycle):
    cycle, _ = make_cycle(
        owners=[owner(), owner(email="sam.okafor@x.example", name="Sam Okafor"),
                owner(email="dana.whitfield@x.example", name="Dana Whitfield",
                      manager=None)],
    )
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("sam.okafor@x.example", "RE:", reply("APP-001", d), day(2))
    assert "APP-001" not in cycle.cycle.attestations
    assert NonAnswer.WRONG_OWNER in cycle.cycle.non_answers["APP-001"]
    assert not cycle.cycle.resolved("APP-001")


# ---------------------------------------------------------------------- AT-8
def test_revoking_removes_it_from_coverage_and_leaves_the_log_intact(make_cycle, log):
    cycle, reg = make_cycle()
    d = cycle.cycle.requested["APP-001"]
    cycle.ingest("ravi.menon@x.example", "RE:", reply("APP-001", d), day(1))
    assert coverage(cycle.cycle, reg).valid == 1

    cycle.revoke("APP-001", "owner says they misread the vendor row", day(5))

    assert coverage(cycle.cycle, reg).valid == 0
    events = [r["event"] for r in log.read("TEST")]
    # Undoing an action and erasing the record of it are different operations,
    # and only one of them is available here.
    assert "attestation_recorded" in events
    assert "attestation_revoked" in events


def test_revoking_something_that_was_never_attested_is_refused(make_cycle):
    cycle, _ = make_cycle()
    with pytest.raises(ControlError, match="AT-8"):
        cycle.revoke("APP-001", "no reason", day(5))
