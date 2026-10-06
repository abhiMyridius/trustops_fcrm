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
| `reflags` | `fcrm_case.domain.reflags.ReflagRow` | A watchlist re-flag (`_id` = `rf_` plus a digest of the list version, the case and the jurisdiction, derived by H1, so a second post creates nothing): the reason, who wrote it (`agent` or `code`), the list entry it cites, the harness `run_id` and `gate_id` that decide it and, once an analyst acknowledged it, who and why. Acknowledging stores the row and enqueues `gate.decided` in one transaction and works on a closed case. Indexes `{created_at, _id}` (the paged list) and `{case_id}`. Analyst-only: no PO shape can carry one |
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

### S1's analyst read surface (lane I3-B1)

Served by `fcrm_case.routes_analyst_read` in the web's shapes (`fcrm_contracts.api.case_analyst_read`, locked to `analyst.ts` and `harness.ts` by a parity test; the few keys a model carries beyond the web type, the review item and gate behind a row, are listed in that test). Every route reads stored rows and computes nothing: a score box is what S2 answered and H1 (or the seed) stored. Two things are stored for the reads' sake:

| Where | Shape and rules |
|---|---|
| `cases.review_started_at`, `review_started_by` | Set once, by the case's own analyst, the first time they open `GET /cases/{id}/risk`. Until then the analyst is shown no score for a live case (`ScoreBox.withheld`); a seeded snapshot (`purpose: "seed"`) shows at once. A conditional update (`{review_started_at: null}`), so a repeat keeps the first time |
| `harness_views` | `_id = case_id`; `{case_id, run_id, snapshot: HarnessSnapshot, updated_at}`. Written by H1's `HarnessReporter` through `PUT /internal/cases/{id}/harness` after every step (a replace, so a repeat changes nothing; the log is at most 500 lines, a push that names another case is a 422); read by `GET /cases/{id}/harness` for the analyst, the committee and the observer. **The owner never receives it**: the route refuses the PO role, OPA denies the PO, and a test drives every PO read route with a snapshot stored and asserts none of its text appears |

A score's acknowledgement is owed until the analyst's ledger decides the gate it opened; a band change and a boundary flag are read the way `POST acknowledge` reads them (one gate per snapshot, the kind from the snapshot).

The queue is **one aggregation** over `cases` (`$match` scope/stage, a `$lookup` of the latest `score_snapshots` row on `{case_id, computed_at}` and of the `critic_views` count, the band filter, a rank by what needs the analyst first, and a `$facet` returning the page and the total); the dashboard is two (a `$facet` over `cases`, and the latest band per case over `score_snapshots`). `cases` gains the index `{state, submitted_at}`.

**What each read is built from**

| Route | Built from |
|---|---|
| `GET /queue`, `GET /dashboard/analyst` | `cases`, the latest `score_snapshots` row, `critic_views`, `analyst_decisions` (a confirmed fast lane); the dashboard's agent evaluation and control health are the seed's reference tables (`fcrm_case.reference`) |
| `GET /cases/{id}/workbench` | the case, its latest snapshot, `completeness_views` (is the assessment ready), the open acknowledgement gates (`score_snapshots.gate_id` not in `analyst_decisions`, the same rule the acknowledge route applies), the `case.stage_changed` rows of `activity` (the two clocks), `harness_views` (the chain badge), the analyst's ledger (`analyst_decisions`: which acknowledgement gates are still open, the completeness decision) |
| `GET .../assessment`, `.../risk`, `.../policy` | `review_items` without the superseded ones, the snapshot of record (every L, I, CE, retention, residual and weight is S2's figure), `critic_views`, `completeness_views`, `intake_views`, `field_values`, `planned_controls` and `gaps` (what the analyst recorded); a clause's title, source and section come from the corpus file, its quote from the stored item. A control's `weight_norm` is its share of its domain's catalogue weight, the one derived number |
| `GET .../handoff` | `handoff_views` (the pack and its four checks as Caseflow stored them; before a pack, a preview marked "not yet checked by Caseflow"), `outcomes`, the `pack.released` row of `activity` |
| `GET .../submissions`, `.../submissions/diff` | `submission_versions`; `fcrm_case.domain.diffing.diff_rows` compares any two |
| `GET .../activity` | `activity`: one handler for the owner (`PoService`, plain words, unchanged) and the analyst (every row, who did it, the audit record and the gate it cites) |
| `GET .../outcome` | one path, two shapes: the owner's plain outcome (`PoFollowupService`) and, for the analyst and the committee, `outcomes` and `conditions` with how it was decided (a seeded closed case reads its outcome from the seed) |
| `GET /internal/cases/{id}/submissions/diff` | the `submission_diff` tool's service route (service role only), in the tool's own output model: the fields whose *value* differs between two submissions (an origin-only change is not a changed field for a re-run). `GET /internal/cases/search`, the `case_search` tool's route, is the re-flag lane's (`routes_internal_reflags`) |

Two item shapes read: H1's typed items (``fcrm_contracts.api.case_internal_assessment``) and the older flat items the seed wrote for cases that predate this system (a likelihood and an impact are two items; a control carries ``evidence``, no weight). The older ones take their control name and weight from the catalogue and have no factor justification.
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
| `examiner_questions` | `fcrm_audit.examiner.QuestionRow` | An examiner's question about one case (`_id` = `exq_` plus a ULID): `{case_id, question, asked_by, asked_at}`. Inserted once, under the insert-only role; `{case_id, asked_at}` indexed |
| `examiner_answers` | `fcrm_audit.examiner.AnswerRow` | The answer H1 posted (`_id` = the question's id, so the unique `_id` is the "one answer per question" guarantee): `{case_id, run_id, refused, sentences[{t, refs}], references, refusal_reason, fingerprint, answered_at}`. Inserted once; an identical post replays, a different one is a 409, and S4 refuses a `ref` beyond the chain head |

S4 also **reads** `audit_records` and `proposals` for the Assurance screens (`GET overrides`, `GET versions/{case}`): the override rate is one aggregation over `audit_records` with `$unionWith` the ledger, and a case's pins are the `data.pins` of its `workflow.started` record. Both need only `find`, which the insert-only role has; indexes `{type, created_at}` on `audit_records` and `{agent}` on `proposals` serve them.

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
| `case_runs` | `fcrm_caseflow.states.RunSnapshot` | One per run; `state` plus `RunFlags` (fast_lane, round_open, locked, pack_version, vote_round). Oversight adds two kinds of row that are never a case in flight (state `closed`, `closed_at` set, the closing dispatch marked done, `refs.oversight` naming the kind): an **oversight run** for a case H1 never ran (so the examiner has a run to hang its job on) and the **harness run** of one jurisdiction list version (`case_id` = `WATCHLIST-<version>`, `_id` derived from the version), which owns the watchlist job and the re-flag gates |
| `run_events` | `fcrm_caseflow.eventstore.RecordedTransition` (persisted form) | Append-only; `eventstore.rebuild` folds these back to a state and must equal the snapshot |
| `agent_jobs` | — | Idempotency key = `sha256(case_id\|run_id\|agent\|step_key\|input_hash)`, unique |
| `gates` | — | One per open-or-decided gate; `GateDecision` is what closes one |
| `case_budgets` | `fcrm_caseflow.budget.CaseLedger` | The per-case token ledger, `_id` = `case_id`: `{cap, spent, reserved, jobs{job_id: {agent, reserved, spent, status}}, last_refusal?}`. H1 reserves one attempt of a job's tier budget in the transaction that publishes it and settles the job's real usage once when its result is applied; one ledger per case, so a re-run continues it (`docs/architecture/sequences/token-ledger.md`) |
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

S4 reads this database through its **own read-only credential** (`fcrm_evals_reader`, role `fcrmEvalsReadOnly`: `find` on `eval_runs` and `eval_dashboards` and nothing else, `MONGODB_URI_EVALS_READER`); the insert-only audit role never reaches it, and S4 answers 503 for agent quality when the credential is missing.

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
