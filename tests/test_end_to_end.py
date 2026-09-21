"""The recorded quarter, asserted.

Every figure the README quotes is checked here. The README and the code share
one execution path through attest.replay, so a change that moves the numbers
turns this file red rather than quietly making the documentation wrong.
"""

from __future__ import annotations

from pathlib import Path

from attest.cycle import AttestationCycle
from attest.evidence import EvidenceLog
from attest.models import CycleState, Decision, NonAnswer
from attest.register import Register
from attest.replay import replay
from attest.reporting import by_criticality, coverage

ROOT = Path(__file__).resolve().parent.parent
REGISTER = ROOT / "data" / "applications.yaml"
SESSION = ROOT / "data" / "replay" / "session_default.json"


def run(tmp_path):
    register = Register.load(REGISTER)
    log = EvidenceLog(tmp_path / "evidence.jsonl")
    cycle = AttestationCycle(register, log)
    c = replay(register, cycle, SESSION)
    return c, register, cycle, log


def test_the_recorded_quarter_produces_the_numbers_the_readme_quotes(tmp_path):
    c, register, _, _ = run(tmp_path)
    cov = coverage(c, register)

    assert len(register.applications) == 14
    assert cov.in_scope == 13          # one of the fourteen is out of scope
    assert cov.requested == 10         # three could not be asked at all
    assert cov.responded == 9
    assert cov.valid == 3
    assert cov.stale == 1
    assert cov.unresolved == 0
    assert cov.exceptions == 9

    assert round(cov.response_rate_pct, 1) == 90.0
    assert round(cov.coverage_pct, 1) == 23.1


def test_the_gap_between_the_two_numbers_is_the_finding(tmp_path):
    c, register, _, _ = run(tmp_path)
    cov = coverage(c, register)
    # Nine of ten requests produced something in the inbox. Three of thirteen
    # applications are covered. Both are true and only one describes the
    # register.
    assert cov.response_rate_pct > 85
    assert cov.coverage_pct < 25


def test_the_response_rate_numerator_is_not_what_it_sounds_like(tmp_path):
    """Nine "replies" are not nine owners replying.

    Two of the nine are a bounce from a mail daemon and a colleague answering
    for an owner on leave, and one more is an autoresponder. Six are messages
    an owner actually sat down and wrote, and they come from three people. For
    a figure whose whole purpose here is to show what a flattering metric
    hides, the count is worth pinning rather than describing.
    """
    c, register, _, _ = run(tmp_path)
    senders = {}
    for app_id in c.responded:
        app = register.applications[app_id]
        senders.setdefault(app.owner_email, set()).add(app_id)

    assert len(c.responded) == 9
    # APP-011's bounce came from postmaster and APP-010's reply from a
    # colleague, so neither owner wrote anything about them.
    assert c.exceptions["APP-011"].reason is NonAnswer.BOUNCED
    assert NonAnswer.WRONG_OWNER in c.non_answers["APP-010"]
    assert NonAnswer.AUTO_REPLY in c.non_answers["APP-009"]
    # Three distinct owners of record account for everything a human wrote.
    human = {
        app_id for app_id in c.responded
        if app_id not in ("APP-011", "APP-010", "APP-009")
    }
    assert len(human) == 6
    assert len({register.applications[a].owner_email for a in human}) == 3


def test_tier_1_coverage_is_visible_separately_from_the_average(tmp_path):
    c, register, _, _ = run(tmp_path)
    tiers = by_criticality(c, register)
    assert tiers["tier_1"] == (2, 4)
    assert tiers["tier_3"] == (0, 4)


def test_every_in_scope_application_ends_resolved_and_the_cycle_closes(tmp_path):
    c, register, _, _ = run(tmp_path)
    assert c.state is CycleState.CLOSED
    for app in register.in_scope():
        assert c.resolved(app.id), f"{app.id} ended unresolved"


def test_each_planted_condition_lands_where_it_should(tmp_path):
    c, _, _, _ = run(tmp_path)

    assert c.attestations["APP-001"].decision is Decision.CONFIRM
    assert c.attestations["APP-003"].decision is Decision.UPDATE
    assert c.attestations["APP-014"].decision is Decision.CONFIRM

    # Confirmed on day 2, record amended on day 4, still on file, no longer counted.
    assert c.attestations["APP-002"].stale is True

    assert c.exceptions["APP-004"].reason is NonAnswer.OWNER_INACTIVE
    assert c.exceptions["APP-005"].reason is NonAnswer.NO_OWNER
    assert c.exceptions["APP-008"].reason is NonAnswer.NO_OWNER
    assert c.exceptions["APP-011"].reason is NonAnswer.BOUNCED

    # These four replied, none of them decided, all four expired.
    for app_id, seen in (
        ("APP-006", NonAnswer.AMBIGUOUS),
        ("APP-007", NonAnswer.UNPARSEABLE),
        ("APP-009", NonAnswer.AUTO_REPLY),
        ("APP-010", NonAnswer.WRONG_OWNER),
    ):
        assert seen in c.non_answers[app_id]
        assert c.exceptions[app_id].reason is NonAnswer.NO_RESPONSE

    # Never wrote back at all.
    assert "APP-013" not in c.non_answers


def test_the_expiry_reason_remembers_what_did_come_back(tmp_path):
    c, _, _, _ = run(tmp_path)
    assert "unparseable" in c.exceptions["APP-007"].detail
    assert "did not decide" not in c.exceptions["APP-013"].detail


def test_out_of_scope_application_appears_nowhere(tmp_path):
    c, _, _, _ = run(tmp_path)
    assert "APP-012" not in c.requested
    assert "APP-012" not in c.attestations
    assert "APP-012" not in c.exceptions


def test_the_evidence_log_accounts_for_the_whole_cycle(tmp_path):
    c, _, _, log = run(tmp_path)
    rows = log.read("2026-Q4")
    events = [r["event"] for r in rows]

    assert events[0] == "cycle_opened"
    assert events[-1] == "cycle_closed"
    assert events.count("request_sent") == 10
    assert events.count("attestation_recorded") == 4
    assert events.count("attestation_invalidated") == 1
    assert events.count("inbound_received") == 9
    # Nine exceptions in the report means nine in the log. AT-4 claimed this
    # and, for a while, nothing checked it.
    assert events.count("exception_recorded") == 9
    assert "escalated" in events
    assert "clarification_sent" in events

    # Every row carries when it happened and which cycle it belongs to.
    assert all(r.get("at") and r.get("cycle_id") == "2026-Q4" for r in rows)


def test_replaying_twice_gives_the_same_result(tmp_path):
    a, reg_a, _, _ = run(tmp_path / "a")
    b, reg_b, _, _ = run(tmp_path / "b")
    assert coverage(a, reg_a) == coverage(b, reg_b)
