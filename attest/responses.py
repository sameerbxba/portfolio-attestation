"""Turning an inbound reply into a decision, or into a reason it was not one.

The design choice here is that intent is never inferred. The request asks the
owner to reply with one of three tokens, and this module looks for exactly one
of them. Prose is not parsed, sentiment is not scored, and "yeah that looks
fine to me" is not an attestation.

That is a deliberately narrow reading and it will annoy people who replied in
good faith. The alternative is a register whose accuracy depends on a language
model's opinion of a sentence, defended later to someone asking how a specific
green tick got there. Annoying the owner is the cheaper failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Decision, NonAnswer

# The reference line the request embeds and asks the owner to leave in place.
# It binds the reply to one application and one version of its record.
#
# Anchored to the start of a line, past any quote markers, for the same reason
# the decision tokens are: a reply contains the thread beneath it, and a search
# that can match mid-line will find a reference in a URL, in an `X-REF:` header
# or in a quoted earlier request. The reference decides which record the answer
# is filed against, so it is the last thing that should be picked up from text
# the owner did not write on this occasion.
#
# The version part is captured as bare alphanumerics rather than as twelve hex
# characters. An earlier version demanded the exact shape, so a mangled digest
# failed to match at all and took the application id with it, leaving a real
# problem on a named application sitting in a discard pile as untraceable. It
# stops at punctuation so that a trailing full stop or a closing angle bracket
# does not turn a genuine decision into a rejected one. Validating the version
# is AT-3's job, done where the value it should have had is known.
REF = re.compile(r"(?m)^[>\s]*REF:\s*([A-Za-z0-9_\-]+):([A-Za-z0-9]*)")

TOKENS = {
    "CONFIRM": Decision.CONFIRM,
    "UPDATE": Decision.UPDATE,
    "RETIRE": Decision.RETIRE,
}

AUTO_REPLY_PREFIXES = (
    "automatic reply",
    "auto-reply",
    "out of office",
    "autoreply",
)

BOUNCE_PREFIXES = (
    "undeliverable",
    "delivery status notification",
    "delivery has failed",
    "mail delivery failed",
    "returned mail",
)


@dataclass
class Classified:
    decision: Decision | None = None
    non_answer: NonAnswer | None = None
    app_id: str | None = None
    digest: str | None = None
    detail: str = ""

    @property
    def is_decision(self) -> bool:
        return self.decision is not None


def classify(sender: str, subject: str, body: str) -> Classified:
    subject = (subject or "").strip()
    body = body or ""
    low = subject.lower()

    # The reference is read first, before anything else about the message is
    # decided. A bounce or an out-of-office still tells you which request it
    # came back from, and losing that turns a known problem on a named
    # application into an unattributable message in a discard pile.
    #
    # A reply can legitimately carry more than one reference, because the
    # thread below it quotes the earlier request. That is fine while they all
    # point at the same record and version. When they disagree there is no
    # honest way to choose, so the message is ambiguous rather than filed
    # against whichever one happened to appear first.
    refs = list(dict.fromkeys(REF.findall(body)))
    app_id = refs[0][0] if refs else None
    digest = refs[0][1] if refs else None
    if len(refs) > 1:
        ids = sorted({r[0] for r in refs})
        return Classified(
            non_answer=NonAnswer.AMBIGUOUS,
            app_id=app_id if len(ids) == 1 else None,
            detail=("conflicting record references: "
                    + ", ".join(f"{a}:{d}" for a, d in refs)),
        )

    for prefix in BOUNCE_PREFIXES:
        if low.startswith(prefix):
            return Classified(
                non_answer=NonAnswer.BOUNCED,
                app_id=app_id, digest=digest, detail=subject,
            )

    for prefix in AUTO_REPLY_PREFIXES:
        if low.startswith(prefix):
            return Classified(
                non_answer=NonAnswer.AUTO_REPLY,
                app_id=app_id, digest=digest, detail=subject,
            )

    if not body.strip():
        return Classified(
            non_answer=NonAnswer.UNPARSEABLE,
            app_id=app_id,
            digest=digest,
            detail="empty body",
        )

    # Token matching is word-boundary and case sensitive. A quoted request
    # further down the thread contains the instruction text, so a loose match
    # would read the instructions back as the answer.
    found = {
        name for name in TOKENS if re.search(rf"(?m)^\s*{name}\s*$", body)
    }

    if not refs:
        return Classified(
            non_answer=NonAnswer.UNPARSEABLE,
            detail="no REF line, cannot bind the reply to a record",
        )

    if not found:
        return Classified(
            non_answer=NonAnswer.UNPARSEABLE,
            app_id=app_id,
            digest=digest,
            detail="no decision token on a line of its own",
        )

    if len(found) > 1:
        return Classified(
            non_answer=NonAnswer.AMBIGUOUS,
            app_id=app_id,
            digest=digest,
            detail=f"more than one decision token: {', '.join(sorted(found))}",
        )

    return Classified(
        decision=TOKENS[found.pop()],
        app_id=app_id,
        digest=digest,
    )
