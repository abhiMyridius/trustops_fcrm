# Data model

One MongoDB database per service (no service reads another's database directly — BUILD-PLAN §3.1). This document lists each collection, its owning service, and the Pydantic contract that defines its shape; the contract, not this prose, is authoritative when they disagree.

## `fcrm_case` (S1)

| Collection | Shape | Notes |
|---|---|---|
| `cases` | `fcrm_case.domain.record.CaseRecord` | One case document; every role's response is built from stored rows by its own module (`fcrm_case.po_views` for the owner), never by stripping a shared shape. A discarded draft carries `discarded_at` and reads as missing |
| `field_values` | `FieldValueRow` | One row per case field (`_id = case:field`); `origin`, `source` (document span, case or question) and `history[]`, which keeps the extracted original when the owner corrects it |
| `documents` | `DocumentRow` | Uploaded files (MinIO `uploads/{case}/{doc}/{name}`) and what H3 made of them; inserted with their `case.document_stored` outbox row in one transaction |
| `submission_versions` | `SubmissionVersionRow` | Frozen per case, unique `{case_id, version}`; written once, never updated. `SubmissionVersionDiff` is computed between two versions, not stored redundantly |
| `po_decisions` | `PoDecisionRow` | The ledger of owner gate decisions: a deterministic `_id` per gate, so the first decision emits `gate.decided` and a later change to the same data does not |
| `integrations` | `IntegrationRow` | One owner's choice per integration (`{user_id}`); no row means the catalogue default |
| `review_items` | `ReviewItem` | One per clause/factor/control/critic-finding proposal; `kind` discriminates (`ItemKind`) |
| `threads` | `ClarificationThreadPO` | One clarification round per document; `round_open` flag on the run mirrors whether a thread is still awaiting an answer |
| `conditions` | `ConditionPO` | Activated on `outcome.set`; 14-day-before-due reminders are computed from `due`, not stored as separate reminder documents |
| `counters` | — | `mint_case_id`'s per-year sequence (`FCRM-2026-NNNN`) |

## `fcrm_scoring` (S2)

| Collection | Shape | Notes |
|---|---|---|
| `scoring_model_versions` | `ScoringModelVersion` | One active version at a time (`active: true`); built from the seed via `fcrm_scoring.seedmodel.scoring_model_from_seed` |
| `routing_rules_versions` | `RoutingRulesVersion` | R1–R6 as data, versioned the same way |

## `fcrm_policy` (S3)

| Collection | Shape | Notes |
|---|---|---|
| `corpus_versions` / `clauses` | `ClauseOut` | 197 clauses across 9 sources in the seed (v14) |
| `control_inventory_versions` | `ControlOut` | 24 controls in the seed (v9); `_id` = `version`, `controls[]`, `active` |
| `jurisdiction_list_versions` | `JurisdictionListOut` | `v2026-06` (active) and the watchlist-demo fixture `v2026-10-fixture` (`fixture: true`, inactive); `_id` = `version` |
| `field_set_versions` | mirrors `fcrm_case.domain.fieldset.FieldSetData` | `v6`; the one source both S1's resolution logic and this service's `/field-sets/{v}/resolve` read (a test pins them equal for every combination of change types) |
| `precedents` | `PrecedentOut` | Closed-case summaries for precedent matching |

Each of the three versioned collections has a unique `version` index and a partial unique index on `active: true`, so at most one version is active (`fcrm_policy.reference`). They are seeded per collection under `SEED_ON_EMPTY`.

**Vector indexes** (S3 Vectors, not Mongo): `policy_clauses`, `precedent_cases`, `controls`, each with the Index Contract metadata keys in `fcrm_policy.search.filters.ALLOWED_METADATA_KEYS`.

## `fcrm_audit` (S4)

| Collection | Shape | Notes |
|---|---|---|
| `proposals` | `Proposal` | The evidence ledger; `seq` is one monotonic number per case (`{case_id, seq}` unique); a later version is a new entry whose `supersedes` names the one it replaces, and `superseded_by` is derived on read |
| `audit_records` | `AuditRecord` | Hash-chained, append-only (see `docs/governance/audit-chain.md`); written only by the `fcrm_audit_writer` Mongo user, which has no `update`/`remove` grant |
| `idempotency_keys` | `_id` = the `Idempotency-Key` | `{kind, case_id, fingerprint, seq, result_id, at}`, inserted in the same transaction as the record or proposal it produced; 24 h TTL on `at`. A repeat with the same body replays the stored result, another body is a 409 |

## `fcrm_decision` (S5)

| Collection | Shape | Notes |
|---|---|---|
| `packs` | `fcrm_decision.rows.PackRow` | `content` is H1's `PackContent` snapshot, stored and hashed as sent and never changed; `content_hash`, the four `checks`, `released_at`/`released_to`, `superseded_at`/`superseded_by`, `current_round`, `decided`. Unique `{case_id, version}` and `{pack_id}`; `{released_at}` serves the committee queue. A pack is built unreleased; the analyst's release opens it |
| `votes` | `fcrm_decision.rows.VoteRow` | Unique `{pack_id, round, member_id}` (one vote per member per round, voided rows keep their seat); a voided vote is kept, never deleted. A round's votes are read by `{pack_id, round}` |
| `outcomes` | `OutcomeRow{outcome: OutcomeSet, mode, decided_at, recorded}` | `_id` = `<pack_id>:<round>`; one row per tallied round, so a `no_majority` round is kept beside the next one. `delegated` closes carry `delegated:<case_id>` as the pack id. Indexed by `{outcome.pack_id, outcome.round}` and `{outcome.case_id, outcome.round}` |
| `questions` | `ThreadRow` | One committee thread (R6) with its `answer` (the pack addendum) embedded; `{pack_id, created_at}`. S5 opens a system thread when a round ends with no majority |
| `outbox` | `fcrm_common.outbox` row | The events S5 publishes (`pack.released`, `vote.cast`, `vote.tallied`, `vote.no_majority`, `vote.rerequested`, `pack.superseded`, `committee.question_asked`, `committee.answered`, `outcome.set`), written in the same transaction as the state change; relayed under the lease `outbox-relay:decision` |
| `idempotency_keys` | `KeyRow` | `_id` = the `Idempotency-Key` on a service `POST`; `{kind, fingerprint, result, case_id, at}`, inserted with the change it produced; 24 h TTL on `at`. The same body replays the stored result, another body is a 409 |

## `fcrm_harness` (H1)

| Collection | Shape | Notes |
|---|---|---|
| `case_runs` | `fcrm_caseflow.states.RunSnapshot` | One per run; `state` plus `RunFlags` (fast_lane, round_open, locked, pack_version, vote_round) |
| `run_events` | `fcrm_caseflow.eventstore.RecordedTransition` (persisted form) | Append-only; `eventstore.rebuild` folds these back to a state and must equal the snapshot |
| `agent_jobs` | — | Idempotency key = `sha256(case_id\|run_id\|agent\|step_key\|input_hash)`, unique |
| `gates` | — | One per open-or-decided gate; `GateDecision` is what closes one |
| `budgets` | — | Per-run token/wall-clock accounting against the tier budgets in `ai/models/tiers.yaml` |
| `outbox` | — | Written in the same transaction as the state change it accompanies; relayed to RabbitMQ by a leader-elected relay |
| `consumed_messages` | — | Dedup by `message_id`, TTL 7 days |
| `model_calls` | — | One row per Bedrock call attempt: model, prompt_v, tokens, cost, latency |

## `fcrm_identity` (X1)

| Collection | Shape | Notes |
|---|---|---|
| `users` | — | 11 seeded users (5 PO, 3 analyst, 3 committee) |
| `refresh_tokens` | — | One per active session; distinct `typ` from access tokens fixes the cloned identity's verified bug of accepting a refresh token as an access token |

## Files

MinIO/S3 buckets: `uploads` (as submitted), `canonical` (post-ingestion Markdown), `packs` (versioned), `vectors` (local fixture mode), `jobs` (scratch space for workers).

## What's built vs. pending

Every Pydantic shape named above exists and is tested in isolation (163 tests across 7 packages as of this document). The actual Mongo collections, indexes and `$jsonSchema` validators are created by `ops/mongo/init/` and the Mongo repository wiring in `fcrm_common`, both landing in parallel lanes; this document describes the target those pieces are built against.
