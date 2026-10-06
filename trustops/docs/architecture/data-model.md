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

### S1's assessment views (lane I3-A)

Written only by H1 through the service-only routes below, read by the analyst routes (the next lane) and by H1 itself. Every one is analyst-only: S1's PO shapes have no field to carry them, and the routes answer 403 to any person role. Stored shapes are `fcrm_case.domain.assessment` (views) and `fcrm_case.domain.views` (`ReviewItemRow`, `ScoreSnapshotRow`); the item payloads are the typed models in `fcrm_contracts.api.case_internal_assessment`.

| Collection | `_id` | Written by | Shape and rules |
|---|---|---|---|
| `screening_views` | `case_id` | `PUT /internal/cases/{id}/screening` | `{run_id, fast_lane_proposed, reasons[{text, source_ref, quote, confidence}], proposal_id, gate_id, updated_at}`. `gate_id` is the `fast_lane_confirm` gate, `null` when no fast lane was proposed |
| `score_snapshots` | `snapshot_id` (`snap_` + digest of case and idempotency key) | `PUT .../score` | History, one row per score of record; the latest by `computed_at` is the score the analyst sees. `{case_id, run_id, purpose (proposed\|confirmed), scoring_v, composite, band (1-4), route, boundary_flag, boundary_distance, domains{<domain>: {L, I, inherent, ce, retention, residual, weight, contribution}}, previous_composite, previous_band, band_changed, gate_id, request, response, computed_at}`. `request` is the exact S2 request (every rating that went in; a planned control is never among them) and `response` S2's answer verbatim. Seeded rows carry only the first block of fields |
| `critic_views` | `case_id` | `PUT .../critic` | `{run_id, findings[{finding_id, gate_id, finding_type, severity, record_refs, text, source_ref, quote, confidence}], counts{total, high, medium, low}, updated_at}`; `finding_id` is the id of the finding's `critic_finding` review item |
| `completeness_views` | `case_id` | `PUT .../completeness` | `{run_id, report{missing, stale, contradictory, questions_drafted, questions_kept}, questions[{question_id, anchored_to, text, dropped_reason}], gate_id, proposal_id, updated_at}`; `gate_id` is the `completeness` gate (ask or proceed) |
| `recommendations` | `case_id` | `PUT .../recommendation-draft` (the `draft` fields only) | `{run_id, draft{sections[{key, text, source_ref, quote, confidence}], conditions[Condition], business_summary{text, restricted_term_hits}, score_record_seq, proposal_ids}, draft_seq, draft_at, edits, locked, locked_at, locked_by}`. H1 never writes `edits` or the lock fields: a re-drafted narrative arrives as a new `draft_seq` beside the analyst's edit. The analyst surface writes `edits` as `RecommendationEdits` (`fcrm_contracts.api.case_internal_assessment`): `{sections[{key, text}], conditions?, business_summary?, outcome?, edit_count}`, where an absent field means "not edited: the draft stands"; `GET /internal/cases/{id}/recommendation` returns draft, edits and lock together, and the pack H1 freezes at the lock reads the effective text from it `business_summary.text` is empty when H1 withheld it because the restricted-term scan hit |
| `handoff_views` | `case_id` | `PUT .../handoff` | `{run_id, pack_id, pack_version, content_hash, checks{<S5 release check>: bool}, audit_head_seq, audit_head_hash, gate_id, updated_at}`; `audit_head_*` is the `recommendation.locked` record the pack was built against, `gate_id` the `pack_release` gate |
| `idempotency_keys` | `"<case_id>\|<Idempotency-Key>"` | every `PUT` above | `{view, case_id, fingerprint, result, at}`, inserted in the same transaction as the write; 24 h TTL on `at`. The same key and body replays the stored result and writes nothing; another body, or the key on another view, is a 409 |

**Review items of the assessment** (`review_items`, `kind` and the payloads H1 writes through `PUT .../items`; the analyst's decision lands in `decided`):

| `kind` | `subject` | `proposed` | `decided.value` | Gate |
|---|---|---|---|---|
| `clause` | `{clause_id, domain, corpus_v}` | `{quote, applicability, confidence}`; `source_refs` = the clause | none (accept = `confirmed`, remove = `removed`) | `review_item`, one per item |
| `factor` (the analyst's "rating") | `{domain}` | `{likelihood, impact, justification, quote, confidence}` | `{likelihood, impact}` on override or adjust | `review_item` |
| `control` | `{control_id, domain, planned, name, catalogue_weight, controls_v}` | `{rating, rationale, evidence_artifact, evidence_date, quote, confidence}`; `rating` is `null` exactly when `planned` | `{rating}` on override or adjust | `review_item` |
| `gap` (research) | `{origin: "research", domain, description, searched_query, searched_indexes, searched_hits}` | `{}` | none | none: a recorded absence is shown, not decided |
| `critic_finding` | `{finding_type, severity, record_refs, text, confidence, quote}` | `{}` | none (explain = `explained`, adjust = `adjusted`) | `review_item` |

`catalogue_weight` is stamped by H1 from S3's inventory at the case's pinned `controls_v`; an agent never supplies one, and an item without one cannot be scored (S1 answers 409 naming the item, never a zero). An item is **superseded** when a later item names it in `supersedes`: it stays as history and is never read as current. A decided item is never edited: a re-run proposes a new item (`itm_` id with a revision) whose gate H1 opens, and expires the old gate if it was still open.

**The score request** (`GET /internal/cases/{id}/score-request`, `ScoreRequestView`): the single place the S2 request is built from items, shared by H1's scoring, H1's re-scoring after a decision, and S2's simulate. One domain per current, not-removed `factor` item; in it every current, not-removed, not-planned `control` item of that domain at its decided value (override or adjustment) or, while undecided, its proposed one; at the case's pinned `scoring_v`. A planned control is left out and listed in `planned_control_ids`; a control whose domain has no factor item is listed in `unscored_control_ids`; every replaced value is listed in `overrides` with the reason. 409 until the assessment has produced a factor item. `GET .../evidence?control_id=` answers the `evidence_store` tool's shape from the same items (artefact, date, the document a source reference cites).

### S1's analyst writes and the owner's follow-up (lane I3-B2)

Every human gate of scenes 8-16 is decided through an analyst route (`fcrm_case.routes_analyst_write`) and the owner's follow-up through `fcrm_case.routes_po_followup`. **Store before emit**: a route commits the decision, its ledger row, its activity row and the outbox event in ONE transaction (`fcrm_case.analyst_write_store`, `fcrm_case.po_followup_store`), because Caseflow's `review.item` reads the decided value from the item. A failure while the event is enqueued leaves nothing decided. A replay (the same decision, the same recorded row, the same lock) changes nothing and emits nothing.

| Collection | `_id` | Written by | Shape and rules |
|---|---|---|---|
| `analyst_decisions` | `gate_id` | every analyst gate route | The ledger of gates the analyst decided: `{case_id, gate_type, decision, fingerprint, decided_by, decided_at}`. The gate id is read from the item (`review_items.gate_id`), the score snapshot that opened an acknowledgement (`score_snapshots.gate_id`), the screening or completeness view; never recomputed. The first decision on a gate emits `gate.decided`; the same fingerprint replays, another one is a 409 `gate_already_decided`. After an unlock an item carries a new gate id, so it is decided again |
| `clarification_threads` | `thread_id` (`thr_` + digest of case, round, question) | `POST completeness {ask}` / `POST clarifications` (open), the owner's answer, `POST .../resolve` | One row per question of the one round (D-16): `{case_id, round, question_id, text, anchor, field_ids, field_label, status (awaiting_response\|answered\|resolved), opened_at, answer{text, documents, document_ids, field_ids, at, by}, resulting_version, resolved_*}`. The owner reads `field_label` and the words, never `anchor` (it may name a control). The answer that closes the round freezes submission v2 and emits `case.answered{round, submission_version, changed_field_ids, added_document_ids, thread_ids}` in the same transaction; the changed fields are the ones the owner set plus the fields the questions were about |
| `planned_controls` | `planned_id` (digest of the content) | `POST controls/planned` | `{case_id, control_id?, description, owner, due, created_*}`; announced as `case.control_planned{planned_control{.., target_date}}`. **Never a review item**, so it is in no score request (rule 7) |
| `gaps` | `gap_id` (digest of the text) | `POST gaps` | `{case_id, text, scope?, created_*}`; announced as `case.gap_recorded` |
| `rerun_requests` | `request_id` (digest of scope and reason) | `POST runs` | `{case_id, scope, reason, requested_*}`; announced as `case.rerun_requested{rerun_reason}`. The same reason and scope twice is one request |
| `recommendations` (analyst fields) | `case_id` | `PUT recommendation`, `POST recommendation/lock\|unlock` | `edits` (`RecommendationEdits`), `locked`, `locked_at`, `locked_by`, and since this lane `unlock_reason`, `unlocked_at`, `unlocked_by`. A lock needs the row unlocked and an unlock needs it locked (conditional updates), so two requests at once flip it once and announce `case.locked` / `case.unlocked{unlock_reason}` once |
| `conditions` (owner's evidence) | `condition_id` | `POST conditions/{c}/evidence` | `evidence_note`, `evidence_at`, `evidence_by` beside the analyst's own `note`; `status` becomes `evidence_submitted`; announced as `condition.updated`. Allowed after the case is closed (rule 5) |

The owner's reads are cut at the data layer: the plain outcome reads `outcomes` with a projection of `outcome`, `plain_summary` and `decided_at` only, and the conditions are read without `source`, `pack_id` and `note`, so the analyst's reason, the pack and the provenance never reach an owner module.

## `fcrm_scoring` (S2)

| Collection | Shape | Notes |
|---|---|---|
| `scoring_model_versions` | `ScoringModelVersion` | One active version at a time (`active: true`); built from the seed via `fcrm_scoring.seedmodel.scoring_model_from_seed` |
| `routing_rules_versions` | `RoutingRulesVersion` | R1–R6 as data, versioned the same way; a published version also carries the parameter table (`parameters`, `committee_quorum`) |

Both version collections have a partial unique index on `active: true` (`single_active`). A new version arrives only through S3's configuration publish (`POST /internal/models`, `/internal/routing-rules`, service account only): one transaction deactivates the old version and inserts the next, guarded by `base_version`, and an existing version is never edited. The startup self-test replays the 80 seeded cases against the model the seed was scored with, not the active one, so a published model never makes S2 unready.

## `fcrm_policy` (S3)

| Collection | Shape | Notes |
|---|---|---|
| `corpus_versions` / `clauses` | `ClauseOut` | 197 clauses across 9 sources in the seed (v14) |
| `control_inventory_versions` | `ControlOut` | 24 controls in the seed (v9); `_id` = `version`, `controls[]`, `active` |
| `jurisdiction_list_versions` | `JurisdictionListOut` | `v2026-06` (active) and the watchlist-demo fixture `v2026-10-fixture` (`fixture: true`, inactive); `_id` = `version` |
| `field_set_versions` | mirrors `fcrm_case.domain.fieldset.FieldSetData` | `v6`; the one source both S1's resolution logic and this service's `/field-sets/{v}/resolve` read (a test pins them equal for every combination of change types) |
| `precedents` | `PrecedentOut` | Closed-case summaries for precedent matching |
| `config_snapshots` | `fcrm_policy.config.Snapshot` | The configuration screens' history: one **append-only** document per published version of each of the eight sections, `_id` = `"<section>@<version>"`, `{section, seq, version, body, published_by, published_at, reason, previous_version}`. Unique `(section, version)` and unique `(section, seq desc)`; the store reaches it through a collection that refuses every write but insert, and a test and a live test attempt an update and a delete and assert they fail |
| `config_drafts` | `fcrm_policy.config.DraftState` | At most one working set per section, `_id` = section: `{base_version, rev, changes[{path, before, after}], updated_by, updated_at}`. Written by compare-and-swap on `rev`; its ETag is `W/"<section>:<base_version>:<rev>"` |
| `config_changes` | change record | One insert-only record per publish (`_id` = `"<section>@<version>"`): who, when, why, the changes and which effect ran |
| `config_idempotency` | `KeyRow` | `_id` = the `Idempotency-Key` of a publish; `{fingerprint, section, version, at}`, inserted in the publish transaction. The same body replays, another body is a 409 |

Each of the three versioned collections has a unique `version` index and a partial unique index on `active: true`, so at most one version is active (`fcrm_policy.reference`). They are seeded per collection under `SEED_ON_EMPTY`.

**Configuration publish** (`policy/config/{section}/draft/publish`): one Mongo transaction appends the snapshot, runs the section's effect (a new active `field_set_versions` or `control_inventory_versions` document, or the jurisdiction activation with its `jurisdiction.list_published` outbox row, the same transaction body as the activate route), deletes the draft and writes the change record and the idempotency key. The scoring model and routing rules are put into force in S2 first (guarded by `base_version`; 503 when `SCORING_SERVICE_URL` is unset). The policy corpus and the agent table are read-only sections. Cases pin a version by label and read it by that label for ever; a later publish is only what `latest` answers.

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
