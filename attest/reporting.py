"""AT-6. Coverage, and the number people quote instead of coverage.

Both are computed and both are printed, because the gap between them is the
finding. Response rate is how many of the people who were asked replied.
Coverage is how much of the estate is currently backed by a valid attestation.
A cycle with twelve unowned applications can post a 100% response rate, and
that sentence is the reason this module exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Attestation, Criticality, Cycle
from .register import Register


def is_valid(att: Attestation, register: Register) -> bool:
    """Does this attestation still describe the record as it stands now?

    Checked live against the register rather than trusted from a flag set when
    the attestation arrived. The flag records what was known at the time; this
    records what is true at the moment somebody asks. When those two disagree
    the flag is the one that is out of date, and a coverage number computed
    from a stale flag is exactly the failure this project is about.
    """
    if att.revoked:
        return False
    if att.app_id not in register.applications:
        return False
    return att.digest == register.digest(att.app_id)


@dataclass
class Coverage:
    in_scope: int
    requested: int
    responded: int
    valid: int
    stale: int
    revoked: int
    exceptions: int
    unresolved: int

    @property
    def coverage_pct(self) -> float:
        """Valid attestations over everything in scope."""
        return 0.0 if not self.in_scope else 100.0 * self.valid / self.in_scope

    @property
    def response_rate_pct(self) -> float:
        """Inbound messages tied to a request, over requests sent.

        Deliberately the naive metric, computed the naive way, because the
        point is to print it next to coverage. The numerator counts anything
        that came back: an out of office, a bounce from a mail daemon, a
        colleague replying on the owner's behalf, a sentence that decided
        nothing. The denominator excludes every application the cycle could
        not ask about at all. Both errors push the number up.

        It is not "how many owners replied", and the gap between what it
        sounds like and what it counts is most of the reason it is here.
        """
        return 0.0 if not self.requested else 100.0 * self.responded / self.requested


def coverage(cycle: Cycle, register: Register) -> Coverage:
    in_scope = [a.id for a in register.in_scope()]
    atts = cycle.attestations
    return Coverage(
        in_scope=len(in_scope),
        requested=len(cycle.requested),
        responded=len(cycle.responded),
        valid=len([a for a in atts.values() if is_valid(a, register)]),
        stale=len([a for a in atts.values()
                   if not a.revoked and not is_valid(a, register)]),
        revoked=len([a for a in atts.values() if a.revoked]),
        exceptions=len(cycle.exceptions),
        unresolved=len([i for i in in_scope if not cycle.resolved(i)]),
    )


def by_criticality(cycle: Cycle, register: Register) -> dict[str, tuple[int, int]]:
    """Valid attestations and in-scope count, per criticality tier.

    Tier 1 coverage is the number that matters and it is the one an estate-wide
    average is best at hiding.
    """
    out: dict[str, list[int]] = {t.value: [0, 0] for t in Criticality}
    for app in register.in_scope():
        out[app.criticality.value][1] += 1
        att = cycle.attestations.get(app.id)
        if att and is_valid(att, register):
            out[app.criticality.value][0] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def exceptions_table(cycle: Cycle, register: Register) -> list[tuple[str, str, str, str]]:
    rows = []
    for app_id, exc in sorted(cycle.exceptions.items()):
        app = register.applications.get(app_id)
        seen = cycle.non_answers.get(app_id, [])
        note = ", ".join(n.value for n in seen) if seen else ""
        rows.append((app_id, app.name if app else "?", exc.reason.value, note))
    return rows


def stale_table(cycle: Cycle, register: Register) -> list[tuple[str, str, str, str]]:
    """Attestations that were real when made and no longer describe the record.

    Surfaced as its own section rather than folded into exceptions, because
    the remediation is different and so is the conversation. Nobody failed to
    respond here. Someone answered, and then the question changed.
    """
    rows = []
    for app_id, att in sorted(cycle.attestations.items()):
        if att.revoked or is_valid(att, register):
            continue
        app = register.applications.get(app_id)
        rows.append((
            app_id,
            app.name if app else "?",
            att.digest,
            register.digest(app_id) if app else "?",
        ))
    return rows
