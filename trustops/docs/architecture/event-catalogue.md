# Event catalogue

Every event type this system emits or consumes is in `fcrm_contracts.events.catalogue.EVENT_TYPES` (`src/packages/contracts/fcrm_contracts/events/catalogue.py`) — that set, not this document, is authoritative; a typo in an event type anywhere in the code is a lookup failure against that set, not a silent mismatch. This document adds the routing detail the code doesn't need to carry.

Every event travels inside `fcrm_contracts.events.envelope.Envelope`: `event_id` (ULID, `evt_` prefix), `type`, `version`, `case_id`, `run_id`, `correlation_id` (shared across one run's events), `trace_id`/`span_id` (W3C trace propagation), `occurred_at`, `payload`. Four typed payloads already exist (`fcrm_contracts.events.payloads`): `JobPayload` (H1→H2 over `agent.run`), `AgentDone`, `AgentFailed`, `GateOpened`. The rest carry an untyped `payload: dict` today; promoting each to a typed model as its owning lane lands is tracked, not urgent — the envelope itself is already strict (`extra="forbid"`).

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
`case.created` · `case.document_stored` · `case.submitted` · `case.answered` · `case.retagged` · `case.rerun_requested` · `case.gap_recorded` · `case.locked` · `case.unlocked` · `case.close_requested` · `case.closed`

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

That is 57 event types in the committed catalogue (`len(EVENT_TYPES) == 57`, checked directly — the design docs' "30 event types" was an earlier estimate before every row of the transition table was transcribed into code, and `case.close_requested`/`ingest.failed`/`index.failed`/the four `condition.*` events were added once `fcrm_common.events`' `build_envelope`/`publish` needed them; the catalogue, not the estimate, is current).

## Versioning and compatibility (design A §4.4)

- `Envelope.version` is per-type, starting at 1. A breaking payload change bumps the version; consumers key their parsing on `(type, version)`.
- A new, optional field is not a breaking change and does not bump the version.
- `fcrm_contracts` is published as one versioned package; every service and worker pins it via the shared `uv.lock`, so a contract change and every consumer's update land in the same commit (BUILD-PLAN §5's "a change to `fcrm_contracts` is a PR touching the fixtures and schema exports in the same commit").

## What's built vs. pending

The envelope, the catalogue, and the four typed payloads above are implemented and tested (`tests/unit/packages/contracts/test_contracts_sanity.py`). The actual publish/consume wiring (`fcrm_common.events`, the RabbitMQ topology in `ops/rabbitmq/definitions.json`, and each service's real publish calls) is being built in parallel lanes; this document describes the target, which the code above is already shaped for.
