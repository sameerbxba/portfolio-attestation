# Application portfolio attestation

A quarterly attestation cycle for an application register. It asks each
application's owner to confirm their record, records what comes back, and
reports how much of the estate is actually covered. It will not record an
attestation from a reply that did not decide anything, from anyone other than
the recorded owner, or against a version of the record the owner never saw.

The interesting part is not that it sends the emails. It is what it refuses to
count.

```
git clone <this repo> && cd portfolio-attestation
pip install -r requirements.txt
python -m attest.cli run
```

That replays a full recorded quarter end to end with no mail server, no
credentials and no network.

---

## Why this exists

An application register is the thing everything else in IT governance hangs
off. Access reviews, vendor assessments, continuity planning and audit scope
all start by asking what systems exist and who owns them. The register is
usually maintained by asking owners to confirm it once a quarter, over email,
into a spreadsheet.

That process fails quietly rather than loudly. Nobody refuses to attest. What
happens instead is that some owners have left, some replies are not answers,
some records change after they were confirmed, and someone reports the
response rate because it is the number that is easy to get. The register ends
up with a column of green ticks and no way to say what any of them mean.

This project is an attempt to make each of those failures visible in code
rather than in a policy document.

---

## What it does

`data/applications.yaml` is the register for a fictional organisation. Fourteen
applications, one out of scope, deliberately imperfect in the ways real ones
are:

- one owner has **left the organisation**
- one owner is **not in the directory at all**
- one **owner cell is empty**
- one record is **amended after its owner confirmed it**

`data/replay/session_default.json` is a recorded quarter. Ten requests go out
and nine inbound messages come back. Four of them are decisions. The other five
are an ambiguous reply, a reply in prose, an out of office, a colleague
answering for an owner on leave, and a bounce from the mail system.

The distinctions it has to hold are the ones that matter in practice: a reply
against an answer, silence against refusal, the version agreed against the
version on file, and the owner of record against whoever happened to write
back.

---

## The cycle

```
open      -> ask every in-scope application's owner, or record why you cannot
ingest    -> classify each reply: a decision, or a reason it was not one
escalate  -> overdue requests go to the owner's manager, once
expire    -> an unanswered request is recorded as not attested, never as attested
close     -> refused while anything in scope is still unresolved
```

Time is a parameter, not a call to `now()`. That is what makes AT-5 testable:
a control that only fires after a real ten day wait is a control nobody tests.

---

## The controls

| id | control | where | what it stops |
|---|---|---|---|
| AT-1 | Owner required | `controls.check_owner` | an unowned application quietly leaving the denominator |
| AT-2 | Explicit decision only | `controls.check_is_decision` | prose, silence and auto-replies becoming attestations |
| AT-3 | Bound to a version | `controls.check_binding`, `register.record_digest` | an attestation transferring to a record that changed |
| AT-4 | Evidence log | `evidence.EvidenceLog` | acting without a record |
| AT-5 | Escalate and expire | `cycle.advance` | an unanswered request staying open forever, or resolving itself |
| AT-6 | Coverage over population | `reporting.coverage`, `controls.check_complete` | response rate being reported as coverage |
| AT-7 | Owner scope | `controls.check_owner_scope` | a helpful colleague answering for the accountable person |
| AT-8 | Revocable | `cycle.revoke` | a withdrawn attestation still counting, or its history disappearing |

Each has sub-controls, and `CONTROLS.md` maps all of them to their tests. The
one worth naming here is **AT-5b**: a resolved item stays resolved. Without it
a reply arriving after the deadline overwrote the "not attested" exception and
recorded a full attestation, which is how the first version of this project
managed to contradict its own headline claim.

**AT-3 does the most work.** Every request embeds a digest of the exact fields
the owner is being asked about, and the reply has to carry it back. When the
record is amended afterwards the digest no longer matches, the attestation
stays on file as evidence of what the owner actually saw, and it stops counting
toward coverage. Nobody has to notice. `ATTESTABLE_FIELDS` in `models.py`
declares what an owner is agreeing to, in one place, so that the answer to
"what did they attest to" is read rather than reconstructed.

**AT-6 is the one with an opinion.** Coverage and response rate are both
computed and both printed, because the gap between them is the finding:

```
  response rate    90.0%   (9 replies to 10 requests)
  COVERAGE         23.1%   (3 valid attestations across 13 in scope)
```

Nine of ten requests produced something in the inbox. Three of thirteen
applications are backed by an attestation that still describes the record.
Both sentences are true. Only one of them describes the register.

Read the numerator of that first figure and it gets worse. Three of the nine
are a bounce from a mail daemon, an autoresponder, and a colleague answering
for an owner on leave. Six are messages an owner of record actually sat down and
wrote, and those six come from three people. The denominator, meanwhile, excludes the three
applications the cycle could not ask about at all. Every one of those errors
pushes the number up, which is how a metric like this survives: nobody checks
what it counts, because its name already tells them.

### Watching them fire

Verbatim, with the successful attestations between them cut for length:

```
  could not be asked
    APP-004  owner_inactive   AT-1: APP-004 is owned by lena.ostrowski@meridianfin.example, who is not active.
    APP-005  no_owner         AT-1: APP-005 has no owner recorded.
    APP-008  no_owner         AT-1: APP-008 names owner 'j.kaur@meridianfin.example', who is not in the owner directory.

  replies
    day  1  APP-009  not a decision (auto_reply), still open
    day  1  APP-011  undeliverable: APP-011 closed, the request never arrived
    day  2  APP-010  not a decision (wrong_owner), still open
    day  3  APP-007  not a decision (unparseable), clarification sent, still open
    day  3  APP-006  not a decision (ambiguous), clarification sent, still open
    day  4  APP-002  register amended, attestation on file no longer matches

  day 11  escalated to line management: APP-006, APP-007, APP-009, APP-010, APP-013
  day 21  recorded as not attested: APP-006, APP-007, APP-009, APP-010, APP-013

  attested, then the record changed
    APP-002  Client Onboarding Portal       agreed 389d353c07a9 -> now f80e2fde15c7
```

Almost nothing closes an item. An owner who replies with prose, whose
colleague answers for them, or who is out of the office has not attested and
has also not refused, so the request stays open and a clarification goes back.
The single exception is a bounce, because a request that never arrived is not
something to keep waiting on, and the fix is different: repair the mailbox or
reassign the owner rather than chase a person who never got it.

That exception needed a second thought. A bounce is identified by its subject
line, which is whatever the sender put there, and it is the one message that
skips the owner check. Left terminal, anybody who can send mail could have
removed an application from coverage for the quarter by writing
`Undeliverable:` at the top. So a decision from the accountable owner
supersedes a delivery failure, and the supersession is logged. Expiry and
withdrawal are not supersedable and never become so.

---

## Everything it did

```
python -m attest.cli evidence --cycle-id 2026-Q4
```

Every request, inbound message, refused reply, escalation, amendment, clock
movement and closure, in order, with a timestamp. Refusals raised at the API
boundary, such as an amendment to an application that does not exist, are
returned to the caller rather than logged; the log is an account of the
cycle, not of every way it can be called wrongly. `out/evidence.jsonl` is opened in append mode and fsynced per
record, so a cycle that dies halfway through still leaves a complete account up
to the moment it died.

Withdrawing an attestation writes the withdrawal into the log and leaves the
original in place. Undoing an action and erasing the record of it are different
operations, and only one of them is available here.

---

## Tests

```
python -m pytest tests/ -q
```

Seventy tests, one or more per control, including a colleague's reply
refused, an amendment stripping a valid attestation of its coverage, a quoted
request read back as an answer and rejected, silence escalating and then
expiring without ever becoming an attestation, a late reply that cannot undo
the expiry, a forged bounce that cannot close an application, and a full
recorded quarter.

`tests/test_hardening.py` is the interesting file. Every test in it exists
because an adversarial read found a control that CONTROLS.md claimed and no
test defended, and in some cases a control the code did not have at all: a
reply arriving after expiry overwrote the exception and recorded a full
attestation, taking coverage to 100%. A second pass, after the first round of
fixes, found that the fixes had introduced two failures of their own and that
two controls still survived having their bodies deleted. Each test in that
file names the failure it was written for, and `CONTROLS.md` has the list.

Every coverage figure and every transcript line quoted in this README is
asserted, against a golden transcript in `tests/golden/`. The README and the
tests share one execution path, so a change that moves a number turns a test
red rather than quietly making this document wrong.

---

## Layout

```
attest/
  models.py       what an application, an owner and an attestation are
  register.py     the register, and the digest that pins an attestation to a version
  responses.py    turning a reply into a decision, or into a reason it was not one
  controls.py     AT-1, AT-2, AT-3, AT-6's completeness check, AT-7
  cycle.py        open, ingest, escalate, expire, close, amend, revoke; AT-5, AT-8
  evidence.py     AT-4, append-only log
  reporting.py    AT-6, coverage against response rate
  notify.py       the outbound side, as a port with a recorded implementation
  replay.py       drives a recorded quarter, shared by the CLI and the tests
  cli.py          run / evidence
data/
  applications.yaml        the register
  replay/                  recorded quarters
tests/
out/                       evidence log, gitignored
```

---

## What this does not do

Stated plainly, because the point of the project is not to overclaim.

- **It has never run against a real register.** The register is fictional and
  its defects were planted. Real estates are larger, have worse data, and
  disagree with the CMDB.
- **It does not send email.** `notify.py` declares a three-method `Notifier`
  protocol and ships only a recorded implementation. Whether a real transport
  behaves is not something anything here can tell you. The tradeoff is
  deliberate: the controls are testable because the cycle is deterministic.
- **Nothing is authenticated.** Sender identity is a string compared against
  the owner's address, which is a control against a colleague being helpful
  and not against anyone who wants to forge a reply. A real deployment needs
  the attestation to happen somewhere the person is signed in.
- **Token matching is strict and it will annoy people.** "Yes that looks fine"
  is not recorded as an attestation. That is the intended behaviour and it is
  still a real cost, paid by owners who answered in good faith. The alternative
  is a register whose accuracy rests on a parser's reading of a sentence,
  defended later to somebody asking how one specific green tick got there.
- **AT-3 proves the version, not the truth.** An owner can confirm a record
  that is wrong, and nothing here would know. The claim is narrow: whatever
  they agreed to can be identified exactly.
- **Nothing here detects an omission.** Every control operates on an
  application that is in the register. An application nobody ever entered is
  invisible to all eight, and no coverage figure will hint at it. This is the
  largest residual risk in the system and the hardest to control for.
- **The evidence log is append-only by convention, not by permission.** The
  process writes it and could overwrite it, and the demo CLI does exactly
  that: `attest.cli run` truncates the log first so successive runs are
  comparable. Appending within a cycle and destroying it between them is a
  demo convenience, not a property. In a real deployment the log belongs on
  write-once storage, or shipped to a platform this process has no
  credentials for.
- **A reverted amendment restores coverage.** Because AT-6 checks the register
  live, editing a record back to a previously attested state makes the
  attestation count again. Both the invalidation and the revalidation are
  logged, so the movement is traceable, but no human is asked to agree to it.
- **Escalation stops at the owner's manager.** There is no second escalation
  tier and no concept of accepting a risk, which means a cycle can close with
  a tier 1 application unattested and nothing forcing anybody to decide about
  it.

`CONTROLS.md` maps each control to its implementation and its tests.
