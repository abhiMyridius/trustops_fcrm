# Event catalogue

Every event type this system emits or consumes is in `fcrm_contracts.events.catalogue.EVENT_TYPES` (`src/backend/packages/contracts/fcrm_contracts/events/catalogue.py`) — that set, not this document, is authoritative; a typo in an event type anywhere in the code is a lookup failure against that set, not a silent mismatch. This document adds the routing detail the code doesn't need to carry.

Every event travels inside `fcrm_contracts.events.envelope.Envelope`: `event_id` (ULID, `evt_` prefix), `type`, `version`, `case_id`, `run_id`, `correlation_id` (shared across one run's events), `trace_id`/`span_id` (W3C trace propagation), `occurred_at`, `payload`. Four strict typed payloads existed first (`fcrm_contracts.events.payloads`): `JobPayload` (H1→H2 over `agent.run`), `AgentDone`, `AgentFailed`, `GateOpened`. The live-wiring models listed under "Live wiring" below were added with ADR-28; they derive from `EventPayload` and ignore unknown fields (design A §4.4). The envelope itself stays strict (`extra="forbid"`). A type with no model is still valid: its `payload` is a plain dict.

## Exchanges (RabbitMQ, design A §4.1 / ADR-02)

| Exchange | Type | Carries |
|---|---|---|
| `case.events` | topic | Case lifecycle events published by S1, consumed by H1 |
| `agent.jobs` | topic | `agent.run` (H1→H2) and `agent.done`/`agent.failed` (H2→H1) |
| `ingestion.jobs` | topic | `ingest.run`/`ingest.done` (H1↔H3) |
| `context.jobs` | topic | `index.build`/`index.built` (H1/S3↔H4) |

Each has a matching `<exchange>.dlx` dead-letter exchange and per-queue retry queues (design A §4.5: redeliver up to 3 times, then park in `<queue>.dlq`).

## Event types by phase

**Case lifecycle (S1 → H1)**
`case.created` · `case.document_stored` · `case.submitted` · `case.answered` · `case.retagged` · `case.types_changed` (ADR-75: the owner added or removed a change type outside a gate) · `case.rerun_requested` · `case.gap_recorded` · `case.locked` · `case.unlocked` · `case.close_requested` · `case.closed`

**Conditions (oversight, post-close; D-19a's 14-day reminder)**
`condition.activated` · `condition.updated` · `condition.completed` · `condition.reminder_due`

**Harness-internal (H1's own state-machine triggers, surfaced as events for the audit map and replay)**
`workflow.started` · `agent.run` · `agent.done` · `agent.failed` · `agent.retry` · `gate.opened` · `gate.decided` · `score.computed` · `score.recomputed` · `boundary.flagged` · `triage.assigned` · `screening.proposed` · `critic.findings` · `completeness.report` · `clarification.sent` · `fieldset.resolved` · `values.reused` · `gap.recorded` · `recommendation.locked` · `pack.assembled` · `pack.approved` · `pack.superseded` · `human.override` · `human.handoff`

**Ingestion / context (H3/H4)**
`ingest.run` · `ingest.done` · `ingest.failed` · `index.build` · `index.built` · `index.failed`

**Decision (S5 → H1)**
`vote.cast` · `vote.revealed` · `vote.no_majority` · `vote.rerequested` · `committee.question` · `committee.answer` · `outcome.set`

**Oversight**
`reflag.proposed` · `reflag.acknowledged` · `chain.verified` · `outcome.delivered` · `eval.recorded`

**Live wiring (ADR-28)**
`harness.step` · `pack.released` · `vote.tallied` · `committee.question_asked` · `committee.answered` · `case.clarification_sent` · `case.control_planned` · `jurisdiction.list_published` · `examiner.asked`

**A clause the analyst adds from a corpus search (ADR-74)**
`case.clause_added` · `case.clause_removed` (S1 → H1, payloads `CaseClauseAdded{case_id, actor, item_id, clause_id, corpus_v, reason, title, ref}` and `CaseClauseRemoved{case_id, actor, item_id, clause_id, reason}`; step-only triggers `CASE_CLAUSE_ADDED` / `CASE_CLAUSE_REMOVED` start `clause.record`, the run does not move) · `clause.added` · `clause.removed` (the audit records that step writes)

**Ad-hoc questions to the owner (ADR-73)**
`case.question_asked` · `case.question_answered` (S1 → H1) · `question.asked` · `question.answered` (the records H1's steps write for them)

That is 75 event types in the committed catalogue (`len(EVENT_TYPES) == 75`, locked by `tests/unit/packages/contracts/test_event_payloads.py` — the design docs' "30 event types" was an earlier estimate before every row of the transition table was transcribed into code; 57 was the count before the live wiring added the nine above, and ADR-74 added the four of an analyst's clause; the catalogue, not an estimate, is current).

### Live wiring: the nine types added with ADR-28

All nine publish to `case.events` (topic) with the event type as routing key, through the producer's transactional outbox, and are delivered to `caseflow.events` through its `#` binding. `harness.step` additionally has its own queue.

| Type | Publisher | Consumer | Payload model (`fcrm_contracts.events.payloads`) |
|---|---|---|---|
| `harness.step` | H1 (outbox, same transaction as the transition that needs HTTP work) | H1, queue `caseflow.steps` (`harness.step`); `caseflow.events` receives and ignores it | `HarnessStep{step_id, step: StepKind, case_id, run_id, args}` |
| `pack.released` | S5 | H1 (`PACK_RELEASED`, guard `release_checks_passed`), S1 (`case-service.events`) | `PackReleased{pack_id, version, released_to, released_to_names, content_hash, release_checks_passed}` |
| `vote.tallied` | S5 | H1 (`VOTE_TALLIED`, key `vote_outcome`) | `VoteTallied{pack_id, round, outcome, vote_outcome, counts, votes}`; a split is `vote.no_majority`, never a tally |
| `committee.question_asked` | S5 | H1 (`COMMITTEE_QUESTION_ASKED`) | `CommitteeQuestionAsked{pack_id, thread_id, member_id, text}` |
| `committee.answered` | S5 | H1: `numbers_changed` picks `COMMITTEE_ANSWERED_CHANGED` or `…_NO_CHANGE` | `CommitteeAnswered{pack_id, thread_id, addendum_id, by, numbers_changed, committee_numbers_changed, unlock_reason}` |
| `case.clarification_sent` | S1 | H1 (informational, no state change; the round opens on the analyst's `completeness` gate) | `CaseClarificationSent{case_id, actor, round, thread_ids, question_count}` |
| `case.control_planned` | S1 | H1 (`CASE_CONTROL_PLANNED`, REVIEW self-loop; a planned control is never scored) | `CaseControlPlanned{case_id, actor, planned_control}` |
| `jurisdiction.list_published` | S3 | H1 (oversight: names no case, so it gets a harness run of its own; `watchlist.publish` asks S1 for the candidates and dispatches the watchlist agent) | `JurisdictionListPublished{version, added, removed, fixture}` |
| `examiner.asked` | S4, published directly after the question is stored (the insert-only audit role cannot update, so it has no outbox relay) | H1 (oversight: `examiner.answer` records the question and dispatches the examiner agent on the case's run, or an oversight run) | `ExaminerAsked{case_id, question_id, question, asked_by}` |

### Ad-hoc questions: the four types added with ADR-73

| Type | Publisher | Consumer | Payload model (`fcrm_contracts.events.payloads`) |
|---|---|---|---|
| `case.question_asked` | S1 (`POST /cases/{id}/questions`, same transaction as the threads) | H1 (`CASE_QUESTION_ASKED`, a self-loop in every state from TRIAGE to DECIDE and in HANDOFF; step `question.asked`) | `CaseQuestionAsked{case_id, actor, thread_ids, question_count}` |
| `case.question_answered` | S1 (the owner's answer to an ad-hoc thread, same transaction as the answer, the values and the frozen version) | H1 (`CASE_QUESTION_ANSWERED`, the same self-loops; step `question.answered` re-runs research and assessment on the changed fields only in REVIEW with no agent running) | `CaseQuestionAnswered{case_id, actor, thread_id, question_id, changed_field_ids, added_document_ids, submission_version}` |
| `question.asked` | H1's `question.asked` step | S4 (audit record) | record data `{thread_ids, question_count}` |
| `question.answered` | H1's `question.answered` step | S4 (audit record) | record data `{thread_id, question_id, changed_fields, added_documents, submission_version, rerun}` |

`harness.step` carries agent output in `args.payload` (H1 holds no other copy of the proposals). It is harness data: S1's SSE stream must never forward the `harness.` prefix to a PO.

The other live-wiring payload models, for events already in the catalogue: `CaseCreated`, `CaseDocumentStored`, `CaseSubmitted` (S1→H1, guard `four_submit_checks_passed`), `CaseAnswered`, `CaseRetagged`, `CaseTypesChanged` (ADR-75; `CaseCreatedData` also gains `intake_mode`), `CaseRerunRequested` (guard `rerun_reason`), `CaseGapRecorded`, `CaseLocked`, `CaseUnlocked` (guard `unlock_reason`), `CaseCloseRequested` (`band`), `GateDecided`, `IngestRun`/`IngestDone`/`IngestFailed` (H1↔H3), `IndexBuild`/`IndexBuilt`/`IndexFailed` (S3↔H4), `VoteCastPayload` (never the option), `VoteNoMajority`, `VoteRerequested`, `OutcomeSetPayload` (H1 reacts only to `mode: delegated`). `JobPayload` gains `refs` (the context-pack inputs H1 supplies) and `AgentDone` gains an optional `fast_lane_proposed`.

### What S5 publishes, and when

S5 writes each event to its own `outbox` in the same Mongo transaction as the state change, and a relay (lease `outbox-relay:decision`, the composite's publisher) sends it to `case.events` with the type as routing key; `envelope.case_id` is always set. Models are in `fcrm_contracts.events.payloads`.

| Event | Written by | Payload model | Notes |
|---|---|---|---|
| `pack.released` | `POST /packs/{p}/release`, only when all four checks pass | `PackReleased` | `released_to` is the committee roster's user ids, `released_to_names` the same people by name (for S1's handoff) |
| `vote.cast` | every `POST /packs/{p}/votes` | `VoteCastPayload{pack_id, round, member_id, cast_count, quorum}` | **Never the option or the rationale** (the model refuses them; so does every outbox row until the round is revealed) |
| `vote.tallied` | the third vote, when two options agree | `VoteTallied{pack_id, round, outcome, vote_outcome, counts, votes[]}` | Reveal and tally together; `votes` is the first place the options leave S5 |
| `vote.no_majority` | the third vote, on a 1-1-1 split (ADR-31) | `VoteNoMajority{pack_id, round, counts}` | Counts only; never a `vote.tallied`, never an outcome |
| `vote.rerequested` | the same transaction as `vote.no_majority` (round n+1, `reason: no_majority`); and `POST /packs/{p}/supersede` when members had voted (next version, round 1) | `VoteRerequested` | `member_ids` are the people asked to vote again |
| `pack.superseded` | `POST /packs/{p}/supersede` | `PackSuperseded{pack_id, version, reason, voided_votes}` | The pack's votes are voided (kept), not deleted |
| `committee.question_asked` | `POST /packs/{p}/threads` | `CommitteeQuestionAsked` | A member's question |
| `committee.answered` | `POST /threads/{t}/answer` | `CommitteeAnswered` | `numbers_changed` needs `unlock_reason` (>= 20 characters) and closes the pack to votes at once |
| `outcome.set` | `POST /outcomes` (`committee` or `delegated`) | `OutcomeSetPayload` | Written once per recorded outcome; a replay announces nothing |

`vote.tallied` and `vote.no_majority` are mutually exclusive for a round. S5 opens the next round itself in the transaction that tallies a split, so `POST /packs/{p}/revote` (H1's re-vote step) only confirms it.

**Guard keys.** H1's `resolve.context_from_payload` reads the guards above from top-level payload keys, so each producing model declares its key at the top level (`tests/unit/workers/caseflow/test_payload_context_keys.py` fails if one is missing).

## Versioning and compatibility (design A §4.4)

- `Envelope.version` is per-type, starting at 1. A breaking payload change bumps the version; consumers key their parsing on `(type, version)`.
- A new, optional field is not a breaking change and does not bump the version.
- `fcrm_contracts` is published as one versioned package; every service and worker pins it via the shared `uv.lock`, so a contract change and every consumer's update land in the same commit (BUILD-PLAN §5's "a change to `fcrm_contracts` is a PR touching the fixtures and schema exports in the same commit").

## What's built vs. pending

The envelope, the catalogue, and the typed payloads above are implemented and tested (`tests/unit/packages/contracts/test_contracts_sanity.py`, `test_event_payloads.py`). The actual publish/consume wiring (`fcrm_common.events`, the RabbitMQ topology in `ops/rabbitmq/definitions.json`, and each service's real publish calls) is being built in parallel lanes; this document describes the target, which the code above is already shaped for.
