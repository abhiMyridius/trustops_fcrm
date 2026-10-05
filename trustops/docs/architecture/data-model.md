# Data model

One MongoDB database per service (no service reads another's database directly — BUILD-PLAN §3.1). This document lists each collection, its owning service, and the Pydantic contract that defines its shape; the contract, not this prose, is authoritative when they disagree.

## `fcrm_case` (S1)

| Collection | Shape | Notes |
|---|---|---|
| `cases` | `CaseDetailPO` (PO projection) / `CaseWorkbenchSummary` (analyst projection) | One case document; role-shaped responses come from which fields the HTTP layer selects, not from two different stored documents |
| `submission_versions` | `SubmissionVersion` | Append-only per case; `SubmissionVersionDiff` is computed between two versions, not stored redundantly |
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
| `packs` | `fcrm_decision.packs.PackSnapshot` | Frozen, content-hashed; `superseded_by` on a pack v2 |
| `votes` | `VoteCast` | Unique `{pack_id, member_id, round}` once the DB layer lands, so a member cannot vote twice in one round |
| `threads` / `addenda` | `CommitteeQuestion` / `CommitteeAnswer` | R6 |
| `outcomes` | `OutcomeSet` | One per case; `no_majority` is a valid terminal state for a round, never for the case |

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

## `fcrm_evals` (X6)

Written by `python -m fcrm_evals run --producer live --publish` (`fcrm_evals.publish`), read by S4's `GET audit/agent-quality` and `GET audit/agent-quality/dashboard`. Nothing else writes here and no service reads it for a decision: it is assurance data about the agents, not case data.

| Collection | Shape | Notes |
|---|---|---|
| `eval_runs` | `fcrm_evals.publish.run_document` (below) | One document per run **and agent**, `_id = "<run_id>:<agent>"` (`run_id` is `<UTC minute>-<git sha>`, e.g. `2026-10-05T14-22Z-64dd794`). Index `agent_ts` = `{agent: 1, ts: -1}`. Written with `replace_one(upsert=True)`, so publishing a run twice leaves one document per agent |
| `eval_dashboards` | `{_id, run_id, ts, git_sha, html, bytes}` | Two documents per run: `_id = "latest"` (moved by every publish) and `_id = "run:<run_id>"` (kept). `html` is the self-contained one-page dashboard, served as is |

`eval_runs` document, one agent in one run:

```
{ _id, run_id, agent, ts (tz-aware), week ("2026-W41"), git_sha,
  producer ("live"), judge ("bedrock" | "fake"), judge_votes,
  dataset ("screening/fast-lane-100"), dataset_sha256, n,
  sample: {n, of, seed, strategy, row_ids[], demo_row_ids[]} | null,
  primary_metric,
  gate: {status: "PASS" | "FAIL", candidate, baseline, threshold, tolerance, reasons[]},
  metrics: [{name, kind: "deterministic" | "llm_judge", score | null, threshold,
             passed | null, rows_scored, primary, flags[<=5]}],
  usage:       {calls, retries, blocked_rows, failed_rows, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens, cost_usd, latency_p50_s,
                latency_max_s, models[]} | null,
  judge_usage: same shape, the judge's calls | null,
  cases: [{row_id, status: "ok" | "blocked" | "failed" | null, tokens, cost_usd,
           latency_s | null, metrics: {name: score | null}}] }
```

Rules for readers:

- `models` hold **labels** from `ai/models/tiers.yaml` (`models.<id>.label`, else the tier slot such as `mid.primary`), never model ids: committed result files live under `/evals`, where ADR-04 forbids ids.
- `score: null` / `passed: null` mean *not measured* (no row produced an observation), which is a FAIL for a gate's primary metric, never a pass.
- `gate.status` is the agent's primary metric against its threshold, and against the baseline run when `baseline` is set; judge metrics are reported in `metrics` and do not decide it.
- `cost_usd` is an estimate at the list prices in `tiers.yaml` (`prices_usd_per_mtok.status`), not an invoice.
- `GET audit/agent-quality?week=` filters on `week`; 404 (no run yet) is the FE's empty state.

## Files

MinIO/S3 buckets: `uploads` (as submitted), `canonical` (post-ingestion Markdown), `packs` (versioned), `vectors` (local fixture mode), `jobs` (scratch space for workers).

## What's built vs. pending

Every Pydantic shape named above exists and is tested in isolation (163 tests across 7 packages as of this document). The actual Mongo collections, indexes and `$jsonSchema` validators are created by `ops/mongo/init/` and the Mongo repository wiring in `fcrm_common`, both landing in parallel lanes; this document describes the target those pieces are built against.
