# Control matrix

Each control is stated the way it would be stated in an assessment, then mapped
to the code that enforces it and the test that proves it does. The column that
matters is the last one: a control with no test is a claim, not a control.

Several rows here were added after the fact. An adversarial read of the first
version deleted each control in turn and re-ran the suite, and five of them
were found to be defended by tests that passed with the control gone. Those
tests now live in `tests/test_hardening.py` and each one names the failure it
was written for.

| id | control statement | type | implementation | test |
|---|---|---|---|---|
| AT-1 | Every in-scope application must have an active, known owner before it can be asked. One that does not is recorded as an exception against the population, never skipped. | Preventive | `controls.check_owner`, applied in `cycle.open` | `test_application_with_no_owner_is_an_exception_not_a_skip`, `test_owner_not_in_the_directory_is_an_exception`, `test_inactive_owner_is_an_exception` |
| AT-2 | An attestation may only be recorded from an explicit decision. Prose, silence, an auto-reply and an ambiguous reply are each recorded with their own reason and none of them is an attestation. | Preventive | `responses.classify`, `controls.check_is_decision` | `test_prose_agreement_is_not_an_attestation`, `test_two_tokens_is_ambiguous_rather_than_a_guess`, `test_auto_reply_is_not_an_attestation_but_keeps_its_application`, `test_the_instruction_text_quoted_back_is_not_read_as_an_answer` |
| AT-2a | A reply that is not a decision does not close the request. The owner is told what was wrong and the request stays open until it is answered or it expires. | Corrective | `cycle._non_answer`, `notify.RecordedNotifier.clarify` | `test_an_unusable_reply_keeps_the_request_open_rather_than_closing_it` |
| AT-3 | An attestation is bound to the exact version of the record the owner was shown. A reference that is missing, empty or corrupted is refused. A record amended afterwards invalidates the attestation on file; a record reverted revalidates it, and both movements are logged. | Preventive, Detective | `register.record_digest`, `controls.check_binding`, `controls.is_stale`, `cycle.amend`, `reporting.is_valid` | `test_reply_referencing_a_different_version_is_refused`, `test_an_empty_reference_is_refused_by_the_binding_control`, `test_a_corrupted_reference_keeps_its_application_and_is_refused`, `test_amendment_after_an_attestation_stops_it_counting`, `test_a_record_that_moves_while_the_request_is_out_arrives_stale`, `test_reverting_a_record_restores_coverage_and_says_so` |
| AT-3a | The digest covers a declared subset of the record, and the owner is shown exactly that subset. | Preventive | `models.ATTESTABLE_FIELDS`, `notify.field_block` | `test_a_non_attestable_field_changing_does_not_move_the_digest`, `test_the_owner_is_shown_exactly_the_fields_the_digest_covers` |
| AT-3b | An application's id cannot be amended, and an amendment to an application not in the register is refused rather than raised as a traceback. | Preventive | `cycle.amend`, `register.Register.amend` | `test_an_application_id_cannot_be_amended`, `test_amending_something_not_in_the_register_is_refused_not_a_traceback` |
| AT-3c | The record reference is read from the start of a line, and a reply carrying references to two different applications is not attributed to either. | Preventive | the anchored `responses.REF` pattern and the conflict check in `responses.classify` | `test_a_reference_inside_a_url_or_a_header_is_not_read_as_the_reference`, `test_references_to_two_different_applications_are_not_attributed`, `test_two_versions_of_the_same_application_are_ambiguous`, `test_a_reference_followed_by_punctuation_is_still_a_valid_reference` |
| AT-4 | Every request, inbound message, refused reply, escalation, amendment, clock movement and closure is written to an append-only log with a timestamp before it is reflected anywhere else. | Detective | `evidence.EvidenceLog.write`, append mode and fsync per record; write-then-mutate ordering throughout `cycle.py` | `test_evidence_log_records_every_event_and_survives_a_reread`, `test_a_refusal_is_logged_not_just_returned`, `test_every_exception_reaches_the_evidence_log`, `test_the_log_is_written_before_the_state_it_describes`, `test_the_evidence_log_accounts_for_the_whole_cycle` |
| AT-4a | Nothing may be dated before the cycle it belongs to, the clock may not run backwards, and moving it is itself logged. A cycle cannot be opened over an open one. | Preventive | `cycle._check_time`, the monotonicity guard and `clock_advanced` entry in `cycle.advance`, the guard in `cycle.open` | `test_nothing_can_be_dated_before_the_cycle_opened`, `test_the_clock_cannot_go_backwards`, `test_out_of_order_advance_would_have_skipped_escalation`, `test_moving_the_clock_is_itself_recorded`, `test_a_cycle_cannot_be_opened_over_an_open_one` |
| AT-5 | An overdue request escalates to the owner's manager exactly once, and an unanswered request is closed as not attested. Elapsed time can make a request louder and then final. It can never make it an answer. | Corrective | `cycle.advance` | `test_silence_escalates_then_expires_and_never_becomes_an_attestation`, `test_escalation_goes_to_the_manager_when_there_is_one`, `test_escalation_happens_once_not_on_every_tick` |
| AT-5a | A bounce closes its request with its own reason rather than as silence, because the remediation differs. | Detective | `cycle.RESOLVING`, `responses.classify` | `test_a_bounce_closes_the_item_with_its_own_reason` |
| AT-5b | A resolved item stays resolved, and the deadline is a date rather than a function call. A reply dated after expiry, or arriving after the outcome is settled, is logged and ignored rather than overwriting it. | Preventive | the expiry and resolved checks in `cycle.ingest` | `test_a_reply_after_expiry_cannot_undo_the_expiry`, `test_a_second_reply_cannot_quietly_replace_a_recorded_decision`, `test_a_reply_dated_after_expiry_is_ignored_even_if_the_clock_never_moved` |
| AT-5c | A decision from the accountable owner supersedes a delivery failure, and only a delivery failure. Expiry and withdrawal are terminal. | Corrective | `cycle.SUPERSEDABLE`, the supersession branch in `cycle.ingest` | `test_a_forged_bounce_cannot_permanently_close_an_application`, `test_an_expiry_is_not_supersedable` |
| AT-6 | Coverage is valid attestations over every in-scope application, checked live against the register. Response rate is reported alongside it and never in place of it. A cycle cannot close while anything in scope is unresolved. | Detective | `reporting.coverage`, `reporting.is_valid`, `controls.check_complete` | `test_coverage_is_measured_against_the_population_not_the_responders`, `test_a_cycle_cannot_close_with_anything_unresolved`, `test_out_of_scope_applications_are_never_requested_or_counted`, `test_the_gap_between_the_two_numbers_is_the_finding`, `test_coverage_follows_the_register_not_the_flag`, `test_the_response_rate_numerator_is_not_what_it_sounds_like` |
| AT-7 | Only the owner of record may attest for an application, and a reply from anyone else is refused before it can reach the record at all. Delivery failures are exempt, because a bounce comes from the mail system rather than from a person. | Preventive | `controls.check_owner_scope`, `cycle.SYSTEM_ORIGINATED`, checked ahead of AT-2 in `cycle.ingest` | `test_a_colleague_answering_on_the_owners_behalf_is_refused`, `test_a_stranger_cannot_trigger_a_clarification_to_the_owner`, `test_a_delivery_failure_is_exempt_from_the_owner_check` |
| AT-8 | An attestation can be withdrawn while the cycle is open. The withdrawal is written to the log, the original record of the attestation stays in it, and the resulting exception says it was withdrawn rather than that nobody answered. | Corrective | `cycle.revoke` | `test_revoking_removes_it_from_coverage_and_leaves_the_log_intact`, `test_revoking_something_that_was_never_attested_is_refused`, `test_a_withdrawal_is_filed_as_a_withdrawal_not_as_silence`, `test_a_closed_cycle_cannot_have_its_coverage_changed` |

## What the first version got wrong

Kept because the failures are more instructive than the controls.

**A late reply could undo an expiry.** `ingest` checked that an application had
been requested and never that it had already been resolved, so a reply arriving
after the deadline overwrote the `no_response` exception and recorded a full
attestation. Coverage went to 100%. Every sentence in this repository about
silence never becoming an attestation was false, by a route that had nothing to
do with the clock. AT-5b exists because of it, and the control it protects had
been sitting in the matrix for a while, described as enforced.

**An earlier token matcher searched the whole body.** A reply that quoted the
original request back was read as an answer, because the request lists all three
tokens in its instructions, and the parser had to choose between CONFIRM, UPDATE
and RETIRE from text the owner had not written. Found by replaying a message
that contained nothing but the quoted request. Matching is now anchored to a
line of its own.

**A corrupted reference used to lose its application.** The pattern demanded
twelve hex characters, so a mangled digest failed to match at all and took the
application id with it, and the reply was discarded as untraceable. The same
file's comments argued at length against exactly that outcome. The version is
now captured loosely and validated where the value it should have had is known.

**`amend` logged after it mutated.** Every other state change in `cycle.py`
wrote its evidence entry first. Three sites inside one function did it the
other way round, so a failed log write there would have left a register
silently edited with nothing to show it, which is the exact failure the
module docstring claims was traded away. The digest of the amended record is
now computed without committing the change, so the entry can be written
before the register is touched.

**A forged bounce could close an application for the quarter.** A bounce is
identified by its subject line, skips the owner check because it legitimately
comes from a mail daemon, and resolved its item terminally. Anyone who could
send mail could therefore remove an application from coverage by writing
`Undeliverable:` at the top of a message. AT-5c is the narrowest fix that
closes it: a decision from the accountable owner supersedes a delivery
failure, and nothing else is supersedable.

**The reference pattern was loosened too far.** Fixing the case where a
mangled digest lost its application id introduced two new ones. Capturing to
the next whitespace swallowed trailing punctuation, so a real decision whose
reference ended in a full stop became unparseable. Searching anywhere in the
body found references in URLs, in `X-REF:` headers and in quoted earlier
requests, which is the same mistake the decision tokens had already been
anchored to avoid, on the line that decides which record an answer is filed
against.

**AT-7 ran after AT-2.** A stranger's prose was filed as a non-answer against
the owner's application and sent the owner a clarification about a message they
had never written.

**A withdrawal was filed as `no_response`.** For an application whose owner did
respond, with a decision, which was recorded. In a project whose argument is
that a cycle unable to say why it is missing records is not evidence of
anything, a false reason code is worse than a missing one.

## Where the controls do not reach

Listed here rather than omitted, because the gap between what a control does
and what it is assumed to do is the thing worth writing down.

**AT-1 does not fix the register, it only refuses to hide it.** Three
applications in the recorded quarter cannot be asked at all, and the cycle
completes with them sitting in the exception list. Nothing forces anybody to
assign an owner, and the next cycle will open with the same three.

**AT-2 trades one failure for another and the trade is visible.** Strict token
matching means a real agreement written in prose is not recorded. That cost is
paid by owners who answered in good faith and it is the reason AT-2a exists:
the request stays open and they get told. The alternative, inferring intent
from a sentence, moves the failure somewhere nobody can see it. A wrong reading
would not announce itself, and the green tick it produced would be
indistinguishable from a real one.

**AT-3 proves the version, not the truth of it.** An owner can confirm a record
that is wrong in every field, and every control here will pass. The claim the
digest supports is narrow: what they agreed to can be identified exactly and
checked against what the register says now.

It is also only as good as `ATTESTABLE_FIELDS`. A field left out of that tuple
can change without invalidating anything. AT-3a at least guarantees the owner
saw exactly what the digest covers, so the two cannot drift apart, but nothing
checks that the list is the right list.

**A reverted amendment revalidates without anybody agreeing to it.** AT-6
checks the register live, so editing a record back to a previously attested
state returns the attestation to coverage. Both movements are logged and the
number is traceable. Nobody is asked.

**AT-6 counts what is in the register.** An application nobody ever entered is
outside the population, so it cannot be unresolved, cannot be an exception, and
does not reduce coverage. Every figure this system prints is conditional on the
register being complete, and nothing here tests that. This is the largest
residual risk in the project and the hardest to control for, because the
evidence of the failure is by definition absent.

Scope is also self-declared. An application marked `in_scope: false` leaves the
denominator entirely, and no control asks whether it should have.

**A stale attestation counts as resolved.** AT-6 refuses to close a cycle with
anything unresolved, and an application holding a stale attestation satisfies
that test while appearing in neither the coverage figure nor the exception
table. It has its own section in the report for exactly this reason, but a
reader who checks only that the cycle closed will not have looked at it.

**AT-5 stops at one escalation.** There is no second tier and no route to a
documented risk acceptance, so a cycle can close with a tier 1 application
unattested and nobody obliged to decide anything about it. The report makes
that visible. It does not make it anyone's problem.

**AT-8 reverses the record, not the consequences.** If a downstream process
read the register between the attestation and its withdrawal, nothing here
knows or tells it.

**The evidence log is append-only by convention, not by permission.** The
process writes it and could overwrite it, and the demo CLI truncates it at the
start of every run so successive runs are comparable. On a real deployment it
belongs on write-once storage or shipped to a log platform this process has no
credentials for.

**Refusals at the API boundary are not logged.** A `ControlError` raised by
`amend` on an unknown application, by `advance` on a backwards clock or by
`open` over an open cycle is returned to the caller and nothing is written.
Only refusals of inbound messages reach the log. The distinction is between
the cycle's account of itself and a record of every way its API can be called
wrongly, and only the first is claimed here.

**Nothing is authenticated.** Sender identity is a string compared against the
owner's email address, which is a control against a colleague being helpful and
not against anybody who wants to forge a reply. A real deployment needs the
attestation to happen somewhere the person is signed in.
