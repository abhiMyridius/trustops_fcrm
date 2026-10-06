# Known gaps

What is not done, not wired, or not proven, each with the file that shows it. Written on 6 October 2026 from the repository at that date, by reading the code, the Makefile, the compose files, the committed eval results and the governance tables. A line here is a claim about the repository, not a plan.

Rules for this file: add a dated line when something changes; do not delete a gap by silence. Where a defect row in `defect-trail.md` still says "Open" for something that is fixed, the correction is here, because the trail is append-only and its owner appends the closing row.

## A. Out of scope by decision

| Gap | Evidence |
|---|---|
| No OCR. A scanned PDF fails ingestion with a code (`ingest.failed`) and the PO is asked for another version | ADR-17; `docs/architecture/sequences/intake.md` (last bullet of "What the steps guarantee") |
| No SharePoint. The Integrations screen shows the connector list with SharePoint "not connected"; a `DocumentSource` interface ships with the S3/MinIO source | ADR-17 |
| No Temporal, no GitHub Actions, no Trivy (also no mobile layout, notification settings or multi-language documents). Local gates (`make check`, `make gate`, the hooks) stand in for CI | ADR-15; `docs/requirements/source/00-README.md` ("Out of scope") |
| Synthetic data and a synthetic corpus. The seed declares 197 clauses over nine sources but holds four clause texts; 193 were generated once on Bedrock and carry `provenance` (`seed_verbatim`, `synthetic_internal_standard`, `synthetic_public_stand_in`). A "verbatim quote" is verbatim from that stored text, not from a real FFIEC, OFAC or FATF publication | ADR-29; `src/backend/packages/seed/fcrm_seed/clauses.json` |

## B. Targets and tests that print "not yet wired" or do not exist

| Gap | Evidence |
|---|---|
| `make demo`, `make seed-verify`, `make verify`, `make e2e`, `make resilience`, `make nightly`, `make contracts`, `make api-gen`, `make locks`, `make build-usage`, `make backup`, `make restore` all print "not yet wired" and exit 1. The golden figures are proved by unit tests, not by a scripted live run | `Makefile` (the `pending` helper); `RUNNING.md`, section "Make targets" |
| No live end-to-end suite. `tests/e2e/` and `tests/resilience/` do not exist; the web's Playwright scenes (`src/frontend/e2e/scenes`, 4 specs) run against the in-browser mock; the live config has no scenes (`npm run test:e2e:live` passes with no tests). The "black-box" BB tests run in process against the apps (`tests/integration/blackbox`), not against the gateway | `src/frontend/package.json`; `src/frontend/e2e/`; `tests/` |
| Six black-box scenarios are named skips: BB-03 override reasons, BB-06 PUT after lock, BB-08 no bulk accept, BB-09 v2 leaves v1 untouched, BB-13 release without acknowledgement, BB-14 score unchanged after an inventory publish. Their stated blocker (no gate, override, acknowledge or recommendation route) is out of date: those routes exist. They were not rewritten. The rules may be covered elsewhere (for example `tests/integration/live_mongo/test_analyst_write_live.py`); that was not checked | `tests/integration/blackbox/test_bb_not_yet_testable.py`; D-16 |
| No kill-and-restart drill, no Bedrock-outage drill, no DLQ-replay test against the stack. The statements about in-flight work in `ops/runbooks/restart-a-worker.md` are read from code and ADRs | `tests/` has no resilience folder |
| 538 tests are marked `integration` and deselected by `make check`. `make test-int` and `make gate` run the 464 under `tests/integration` (Docker, or `FCRM_TEST_MONGO_URI`). The other 74, in `tests/unit/**/*_live.py` (S2, S3, S4 and H1 against a real Mongo), are selected by no make target: they run only when someone calls pytest by hand with `-m integration tests/unit`. All of them together passed (525 passed, 14 skipped) on a throwaway replica set on 6 Oct | `pytest.ini`; `Makefile`; `tests/unit/**/*_live.py` |
| The rule "model ids appear only in `ai/models/tiers.yaml`" and the prompt lock freshness are not part of `make check`. `tests/registry/test_locks.py`, which `tiers.yaml` and `ai/README.md` cite, does not exist. `ai/_tools/validate_ai.py` (802 checks, green on 6 Oct under the project environment) and `ai/_tools/compute_locks.py --check` are run by hand; `make locks` is not wired. The loader's own unit tests cover lock computation (`tests/unit/packages/prompts_loader/test_prompt_locks.py`) | `Makefile`; `ai/_tools/validate_ai.py`; `ai/README.md` |
| The live eval gate (G-T1) runs on demand (`make evals`, `make evals-gate`); no hook blocks a prompt change that lowers a score. `make gate` runs only `fcrm_evals build --check` and `selfcheck` | `Makefile` (`gate`); `docs/governance/gates-register.md` says "hooks" |
| Claude Code hook registration is not in the repository: no project `.claude/settings.json` is tracked. The hook scripts and their convention are committed; whether they ran on a given session is not provable from the repo. The git `pre-commit` and `commit-msg` hooks are installed by `make bootstrap` and are the enforceable part | `git ls-files` (no `.claude/settings.json`); `ai/guidance/hooks/`; `.pre-commit-config.yaml` |
| No nightly run (G-O1): `ops/reports/nightly/` does not exist. No runtime token report is committed (`make token-report` needs `make up-obs`); `ops/token-analysis/` does not exist; build-time token use is unmeasured | `Makefile`; `ops/reports/token-analysis.md` |
| `make backup` and `make restore` are not wired; the runbook gives manual commands that were verified only against throwaway containers | `ops/runbooks/backup-restore.md` |

## C. Behaviour that is built differently from what the design or the docs say

| Gap | Evidence |
|---|---|
| **A run in `handoff` cannot be resumed.** An agent that fails twice opens a `handoff` gate and the run moves to `handoff`. `GATE_DECIDED_HANDOFF` exists in the transition table but nothing publishes it: no route decides a handoff gate, `POST /cases/{id}/runs` works from `review` only, and the web has no re-run control (its banner says "Re-run from the harness panel"). The gates register says the analyst completes the step and the machine resumes | `src/backend/workers/caseflow/fcrm_caseflow/transitions.py`; `docs/architecture/openapi/api.json`; `src/frontend/src/features/analyst/tabs/Summary.tsx`; `ops/runbooks/bedrock-outage-and-fallbacks.md` |
| **S1 pins versions from the seed.** `active_pinned_versions()` reads the seed's `demo.versions`. A configuration publish (scoring model, corpus, controls, field set, routing rules) goes live in S2 and S3, and S3 exposes `GET /internal/config/pins` ("the versions in force", ADR-47), but S1 does not call it, so a case created after a publish still pins the seed's versions | `src/backend/services/case/fcrm_case/fieldset_provider.py`; `src/backend/services/policy/fcrm_policy/routes_config.py` |
| **The 120,000-token case cap is not enforced across jobs.** Each H2 job gets a fresh `CaseBudget`; H1 sums the total but does not stop a case. Guard 3 reserves `tokens x (1 + retries)` per job (strong 48k, judge and cheap 30k, mid 22k), so enforcing it as designed would block the critic after a research and assessment pair | `evals/README.md`, "The demo case against the 120k per-case cap" |
| **H1 does not apply `is_fast_lane_candidate`.** Only the evals' dataset generator calls it. The fast-lane gate opens on the screening agent's boolean; screening v3 applies R4 in its prompt (precision 1.00, recall 0.83 on 100 rows, below its 0.90 line) | `src/backend/workers/caseflow/fcrm_caseflow/routing.py`; `evals/README.md` finding 6 |
| **`evals.events` has no consumer.** The queue and its DLQ are declared; nothing consumes or publishes | `ops/rabbitmq/definitions.json`; `grep -rn evals.events src/backend` finds no code |
| **`BEDROCK_REGION_FALLBACK` is read by no code.** It is a setting of H2 and the harness, defaulted to `us-west-2` in compose. All fallbacks are other models in one region. There is no circuit breaker on Bedrock (BUILD-PLAN section 3.9 lists one) | `src/backend/workers/agent-worker/fcrm_agent_worker/settings.py`; `grep -rn region_fallback src/backend` |
| **Redis fan-out is not implemented.** `REDIS_URL` is a defined setting and `make up-cache` starts Redis, but the SSE hub is in process (`fcrm_case/sse.py`: "Not implemented"). Run one `api` replica | `src/backend/services/case/fcrm_case/sse.py` |
| **S3 Vectors is not in the shipped image.** The backend exists behind the `s3vectors` extra of `fcrm-context-worker`, which conflicts with the agents' `boto3` requirement; the image installs no extras. Vectors are in memory, and each `api` start embeds the 197 clauses on Bedrock (about 85 s) | `src/backend/pyproject.toml` (conflicts); `src/backend/Dockerfile`; commit `5a02c09` |
| **No database-level insert-only role for configuration snapshots.** Each configuration section is an append-only sequence of immutable snapshots, and the store refuses amend and delete (409 `snapshot_immutable`) with a unique `(section, seq)` index (ADR-47). That is enforced by the application, through `fcrm_app` (readWrite on six databases). Only the audit collections have an insert-only database role | `ops/mongo/init/02-users.js`; ADR-47 |
| **The observer role is narrower than the visibility matrix.** `fcrm_observer` may read the monitor, the case stream, agent quality and `GET /cases/{id}/harness`; it has no S5 access (no pack, votes or outcome) and no audit records, although the matrix says "yes" to several of those | `ops/opa/data/permissions.json`; `docs/governance/visibility-matrix.md` |
| **The web examiner does not handle a 202.** `POST /audit/examiner/ask` waits up to 60 s and then answers 202 with `{question_id, status: pending}`; the web reads the response as an answer (`api<ExaminerAnswer>`) and has no poll of `GET /examiner/answers/{id}`. A slow answer renders as an error. (Re-checked at the merge base `5a02c09`; see the report of this lane for the final check) | `src/frontend/src/features/analyst/assurance/AskTab.tsx`; `src/frontend/src/api/queries/assuranceQueries.ts`; `src/backend/services/audit/fcrm_audit/routes_examiner.py` |
| **Identity is a demo directory.** Twelve seeded users share one bcrypt hash; one JWT keypair loaded from a file at start, with no overlap window on rotation; no external identity provider; a rotation signs everyone out | `src/backend/services/identity/fcrm_identity/`; `ops/runbooks/secrets-and-keys.md` |
| **No TLS and no high availability in the compose stack.** The gateway serves plain HTTP on 8080; Mongo, RabbitMQ and MinIO are single nodes | `docker-compose.yaml`; `ops/aws/README.md` |
| **The audit chain has no format version.** A change to the canonical form makes an old chain read as corrupt (D-29) | `docs/governance/defect-trail.md` D-29 |
| **The precedent similarity is a fused rank score, not a cosine.** The seed's 0.81 for FCRM-2026-0118 is a literal that no code path reproduces; S3 returns the fused score | D-20; ADR-41 |

## D. Evals and tokens

| Gap | Evidence |
|---|---|
| **Assessment fails its gate**: `li_within_one` 0.70 against 0.85. The labels cannot be inferred from what a dataset row carries (a constant L 3, I 3 earns 0.61; the same title and domain carries more than one pair in 37 of 77 groups). The fix is in the dataset, not the agent; the threshold was not touched. Control-match precision 0.79 and recall 0.60 are below 0.90 | `evals/results/2026-10-06/gate.json`; `evals/README.md` finding 5 |
| **Non-primary metrics under their lines** (the gate does not read them): `narrative_faithfulness` 0.20, `category_f1` 0.67, `precedent_top1` 0.00 (the eval pack cannot print the similarity and outcome the metric needs), `must_cite_coverage` 0.89, `fast_lane_recall` 0.83 on the full screening set | `evals/results/2026-10-06/index.json`; `evals/README.md` |
| **Thin datasets.** Examiner has 5 rows (design wanted 120); policy_gap has 1; the gate samples 5 rows per agent, so a primary metric of 1.0 over five rows is evidence, not a rate. The seed carries field values for the demo case only, so intake's other 79 rows hold change types only. Judge thresholds are proposed, not from the design | `evals/README.md`, "Datasets (real sizes)" |
| **Not built**: the red-team sets, override harvesting, a judge cache (a re-grade pays again), `pydantic_evals`, and the metrics `gap_honesty`, `planned_never_rated`, `stale_evidence_detection`, `question_grounding`, `question_dedupe` | `evals/README.md`, "Not built yet" (its mention of the S4 eval routes is out of date: they exist, commit `90033d7`) |
| **The model adds a stray key now and then and the job pays a retry**: `source_ref_note: null` on a research row cost a 23.6k retry. The fix (drop empty unknown keys at decode) was deliberately not made so the committed run is the code that ships. The retry runs on the fallback model, and a mid-tier retry once came back from Haiku with two invented conditions | `evals/README.md`, "Where it still fails" |
| **The demo case's token total was measured once, before `research.system` v3**: 114,626 of 120,000 with one retry, 91.0k clean; 5.4k (4.5%) under the cap with all eleven jobs and the retry, and over it once the second intake window (about 6k, an estimate) is added. The README's sentence that the oversight agents are "not in either number" disagrees with its own table, whose total includes their 15.1k | `evals/README.md`; `ops/reports/token-analysis.md` |
| **Prices are placeholders.** Anthropic rows are first-party list rates standing in for Bedrock; Nova and Titan rows are the last published on-demand rates; cost figures are estimates until reconciled with the AWS bill | `ai/models/tiers.yaml` (`status: placeholder_confirm_before_go_live`) |
| **No live full-case run is recorded in the repository.** The live composite before and after the override, the live vote and the live record count of a hand-driven FCRM-2026-0151 are not committed anywhere; the golden 9.8 to 10.4 is proved by unit tests only. The live demo asserts structure and prints the live numbers (ADR-27) | `ops/reports/` does not exist; ADR-27 |

## E. Governance and documentation that lag the code

The governance tables are append-only, so these are left for the owner to supersede with new rows.

| Gap | Evidence |
|---|---|
| D-46 and D-47 (P1) and D-48 (P2) still read "Open", and are closed by later rows: D-54 resolves D-46 and D-47 (run 2 passes 8 of 9 gates with every row completed, `evals/results/2026-10-06/gate.json`) and D-55 resolves D-48 (the diff and `case_search` routes exist). Any scan for open P1 rows must read those closing rows | `docs/governance/defect-trail.md` |
| Other rows that read open but are resolved or superseded in practice: D-04 (vote secrecy: S5's HTTP layer hides votes, BB-05), D-09 (superseded by D-11), D-18 (resolved by D-34), the vote-identity part of D-23 (closed by D-28). No closing row exists for them | the rows and the commits named in them |
| Rows that are genuinely still open: D-16 (two numberings of BB-04 onward), D-20 (the 0.81), D-29 (no chain-format version), D-39 (a live-Mongo run failed once and was not reproduced), D-50 (P1, process: the build machine slept with the lid closed and stalled every lane), D-52 (the `evidence_store` tool reads a different shape than S1 answers: a run that calls it would fail), D-53 (the web's decide call may not resolve clause and control ids: the first review decision may error), D-56 (the assessment gate; a dataset decision), D-57 (three worktree branches from other sessions never merged), and the remaining items of D-23 (re-posting identical content after an unlock, voided votes still counted in `votes_cast`, S1's two lookups for one version) | `docs/governance/defect-trail.md` |
| `rules.md` and `visibility-matrix.md` carry status text from before the HTTP layer ("pending `fcrm_common`", "Still to build") | `docs/governance/rules.md`; `docs/governance/visibility-matrix.md` |
| `data-model.md` and `event-catalogue.md` end with "What's built vs. pending" sections written before the wiring landed | `docs/architecture/data-model.md`; `docs/architecture/event-catalogue.md` |
| `audit-chain.md` points at `src/services/audit/...` (the layout before ADR-26) | `docs/governance/audit-chain.md` |
| `docker-compose.yaml`'s header comment describes 22 containers and `.env.example` says empty AWS keys keep the stack "in replay/memory mode"; both predate ADR-26 and ADR-27 | `docker-compose.yaml`; `.env.example` |
| The web shows copy from the mock era: "Replay fixtures exist only for FCRM-2026-0151" (PO Overview), "FCRM-2026-0151 is the live case in mock mode" (workbench) | `src/frontend/src/features/po/tabs/Overview.tsx`; `src/frontend/src/features/analyst/WorkbenchLayout.tsx` |
| The test-file docstring of `test_bb_not_yet_testable.py` lists a route inventory that is out of date | `tests/integration/blackbox/test_bb_not_yet_testable.py` |

## F. Process notes a reviewer should know

- The build was done by AI lanes under an operator-approved plan. Operator gates (G-R1, G-D1, G-D2, G-C2, G-T2, G-P1, G-O1) were recorded as "passed pending operator review" and did not block; see `docs/governance/sdlc-evidence.md`.
- `/code-review` (G-C2) on each merge is the plan; the repository does not hold a record that proves it ran on each one.


## G. Changes of 6 October evening (appended)

Closed since the sections above were written:

- Handoff resume, end to end (ADR-51): raw inputs on the gate, an analyst route and workbench control, `human.handoff` in the chain.
- `evals.events` is bounded and its dead-letter queues keep parked messages (ADR-54); `BEDROCK_REGION_FALLBACK` is wired as the last fallback of every chain.
- `make test-int` runs every integration-marked test and `make check` runs the `ai/` validators (prompt, model-id and lock checks).
- S1 pins the versions S3 holds in force and loads published field sets by version (ADR-52).
- Model prices are verified against the AWS Price List (ADR-53).
- The per-case token cap is enforced by a ledger: 160,000 enforced, 120,000 target (ADR-50). The web examiner handles the 202 and polls the answer.
- The full 21-scene run passes live (about 4 minutes), the live Playwright scenes and the live black-box tests passed on the previous build; `make demo`, `seed-verify`, `verify` and `e2e` are wired.

Still open:

- The eight resilience drills have not run on the live stack (D-66).
- Handoff resume has no end-to-end run against a real Bedrock failure (D-67); no per-call runtime cost metric (D-68); H1's `DEFAULT_PINS` for oversight pseudo-runs (D-69).
- The assessment eval gate (`li_within_one` 0.70 against 0.85) is capped by the dataset (D-56).
- Three branches from other sessions are unmerged (D-57); `docs/governance/known-gaps.md` section B rows for `make test-int`, `validate_ai` and the compose text are closed by the items above.
