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

## H. Changes of 6 October night (appended)

Closed since section G:

- The full 21-scene run passes live on the final build (21 of 21; 197 audit records, the chain verifies, every dead-letter queue empty). Its first run on that build found a case stuck at the decision, fixed in `4e156a1` (D-70).
- The exception and loop audit fixed nine defects (D-77 to D-83, ADR-57); the backend and frontend sweeps removed dead code and mock-era screen copy (D-84, D-85, ADR-58). The row of section E about mock-era web copy is closed by D-84.
- An economy profile (ADR-56) lets the live checks run with no Sonnet or Opus: `make up-economy`, `make evals-economy`.
- Live calls need the operator's approval first (ADR-59).

Still open:

- Findings of the audit and the sweep: the outbox poison row (D-86), the web logout (D-87), H2's audit key (D-88), the low findings (D-89), guards that may be unwired (D-90), a live 1-1-1 and R6 run (D-91).
- Two chart insights in the web hard-code a count ("12 of 80 reached the Risk Committee", "38 of 212 proposals"); four buttons have no backend route (export case record, export and import CSV, upload source).
- Everything listed as open in section G, including the eight resilience drills (D-66) until they are run.

## I. After the live checks of 6 October night (appended)

Closed:

- The twelve resilience drills have run live and pass (D-97). They found two product defects, an OPA that was never reloaded and a step retry window shorter than an api restart, both fixed (D-92, D-93, ADR-61).
- The economy profile (ADR-56, ADR-60) exists and was used: `make up-economy` runs the stack with no Opus and no Sonnet 5, `make evals-economy` runs the evals on it. The eval pipeline ran end to end on it at one row per agent (7 of 9 gates, about USD 0.44, the Sonnet 4.5 price unverified); that result is not comparable with the committed baseline and is not kept in the repository.

Still open:

- **No complete live acceptance pass on the final build.** The last 21-scene pass is 21 of 21 on the shipped chain at 18:25 IST, before the clean-up merges; on cheaper chains the run stopped at scene 3, 8 or 9 (D-95). The live black-box rules and the Playwright scenes need a completed demo and were not run on the final build. A run on the shipped chain would cost about USD 0.6 to 1.0 at the verified prices and needs the operator's approval (ADR-59).
- The Guard 4 retry model for the cheap tier (D-96).
- The stack was left running the economy profile, not the shipped tiers, until `make up` is run (it restarts the api, which embeds the clauses on Titan).
- A job parked in `agent-worker.run.dlq` by a transport failure (a Bedrock timeout on every delivery) may leave the case waiting with no signal; no drill covers it (D-98).

## J. After the model change of 7 October (appended)

Closed:

- The committed chain has no Opus and no Sonnet 5: Sonnet 4.5 on the mid and strong tiers, Nova Pro on the judge, the judge budget at 28,000 tokens, the Sonnet 4.5 price read from the AWS Price List (ADR-62, D-99). A unit test fails if either returns to a chain.
- The economy profile is retired (its builder, file, make targets and tests); the chain it ran is now the committed chain.

Still open:

- **No eval baseline and no complete live pass for the committed chain** (D-100, D-95). The 8 of 9 gates and the 21 of 21 run were measured on the previous chain. The committed chain's own checks so far: a one-row-per-agent eval run (7 of 9 gates, about USD 0.44) and two live runs that stopped at scene 8 and 9.
- A Sonnet 4.5 token costs about 1.5 times a Sonnet 5.5 token at the verified prices, so a demo run costs more than the earlier figure of about USD 0.6 to 1.0 on the previous chain; it was not re-measured.

## K. After the browser sweep of 7 October (appended)

Closed in code, not yet confirmed on the live application:

- The seven sweep defects (D-105 to D-111) are fixed on main with tests (D-112). The running application still has the old build until `make up`; the sweep is then run again to confirm.

Still open:

- The API's access rows still let the observer and the committee read agent quality, but no screen shows it to them (D-109, ADR-64).
- The owner's chain-status contract (`case_po.ChainStatusPO`, design A section 2.4) has no route and is unused since D-105.
- Several Dashboard chart captions are static copy that can contradict live data (for example "week of 2026-09-28", "12 of 80 reached the Risk Committee").
- `ai/agents/examiner.agent.yaml` names the observer under "Assurance, Ask" although the web gives the observer no such screen; left because editing under `ai/` means regenerating the locks.
- The monitor's counters, guard strip and log filters have never been seen in the browser sweep, because no sample case has a run.
- `CLAUDE.md` carries old test counts (7,689 unit, 164 web); it was not edited because another session has an unsaved edit in that file.

## L. After the move back to Sonnet 5.5 (7 October, appended)

Closed:

- The committed chain is Haiku 4.5, Sonnet 5.5, Nova Pro and Nova Lite, with no Opus (ADR-65). The code, the tests and `ai/models/tiers.yaml` agree; the prose is being brought into line.

Still open:

- **No complete live pass and no eval baseline for the exact committed combination** (D-95, D-100). The eval baseline (8 of 9 gates) and the last complete 21-of-21 run were measured with Sonnet 5.5 on mid and strong and an Opus critic; the committed chain differs in the critic (Nova Pro, 28,000 tokens), which has had only a one-row eval check and one live run that hit its old 20,000 limit.
- The two live runs that stopped at scene 8 and scene 9, and the one-row eval check (7 of 9, about USD 0.44), were made on Sonnet 4.5. They describe a chain that is no longer committed; the Sonnet 5.5 combination has had no live run since the change.
- The cost of one complete cycle, about USD 0.47 (0.41 to 0.59), is an estimate from the last complete run's token counts at the verified prices, not a measurement.
- The running application keeps the chain it was started with: the operator's next `make up` loads the committed one.

## M. After the OTLP logs change (7 October, appended)

Closed:

- The api and harness export their application logs over OTLP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set, next to the JSON on stdout, with the same fields and redaction and with trace and span ids (ADR-66). The collector forwards them to OpenObserve (`make up-obs`, http://localhost:5080, stream `trustops`). With the endpoint empty nothing is created and nothing is exported, as before. The api now flushes telemetry last on stop, as the harness already did.
- Checked live on 7 October with the real `init_otel` and `configure_logging` code, a real collector and a real OpenObserve v1.0.4 in a separate compose project: the record arrived with its message, `case_id`, `run_id`, severity, and the same `trace_id` and `span_id` as the span it was logged in; a `token` extra and a nested `password` were absent. That run also found, and fixed, OpenObserve refusing a lowercase-hex password.

Still open:

- **The composed api and harness have not been run against it.** The live check above bypassed them. Running `make up-obs` rebuilds and restarts the api, which re-embeds the corpus on Bedrock (ADR-59), so it waits for the operator.
- OpenObserve is a single local node with the root login only: data in the named volume `openobserve_data` (not backed up, no retention set), no TLS, no per-user access. The AWS path ships stdout to CloudWatch instead and is not built.
- Traces and metrics still go to Jaeger and Prometheus only, so there is no click-through from a trace or a Grafana panel to its logs; search the `trace_id` in OpenObserve.
- An older `.env` has no OpenObserve login: `make up-obs` stops and says what to add (`make env` never overwrites). The login is stored on the first start, so changing it later needs `make down-v`, which also deletes the stored logs.
- The standalone per-service and worker entrypoints (`uvicorn <pkg>.main:app`, the four workers run on their own) never call `init_otel`, so they export no traces, metrics or logs. Only the composed `api` and `harness` do.
- Model calls (Pydantic AI) are not instrumented, so they appear neither as spans nor as log records of their own.

## N. Traces and metrics to OpenObserve (8 October, appended)

Closed:

- Traces, metrics and logs all reach OpenObserve (ADR-68); the address and login come from `OPENOBSERVE_*` in `.env`, so the stack can report to a team's own OpenObserve. This closes section M's "traces and metrics still go to Jaeger and Prometheus only": a log's `trace_id` now leads to its trace in the same tool.
- Checked on 8 October without Docker: the real collector (otelcol-contrib 0.135.0, the image's version) with `ops/otel/collector.yaml` and `env/local.yaml` passed `validate`, and the real `init_otel` and `configure_logging` sent one span, one metric and one log through it to a stand-in for OpenObserve, which received one POST each on `/api/default/v1/traces`, `/v1/metrics` and `/v1/logs`, each with a basic-auth login and `stream-name: trustops`, and the collector logged no OpenObserve export error. `docker compose config` renders both the bundled default and a team instance set in `.env`.

Still open:

- **Not run against a real OpenObserve with the composed stack.** The stand-in proves the routing and the login, not that OpenObserve stores and shows the data; `make up-obs` restarts the api, which re-embeds the corpus on Bedrock (ADR-59), so it waits for the operator.
- With a team instance set, the bundled OpenObserve container still starts and receives nothing.

## O. Jaeger removed, and the OpenObserve edge cases (8 October, appended)

Closed:

- Jaeger is gone (ADR-71): OpenObserve holds traces, metrics and logs; Grafana keeps Prometheus and opens traces in OpenObserve. With the real collector and the new config, one span, one metric and one log reached the OpenObserve stand-in with no export error at all (before, every batch also logged a failed Jaeger export when no Jaeger ran).
- `make up-obs` refuses, with the fix, an `.env` that would cut the OpenObserve password, a half-set team instance, or an address without the organisation path (D-114).
- Checked against the real collector: a trailing `/` on the address still posts to `/api/<org>/v1/...`; a refused login (401) and an unreachable OpenObserve each leave the collector running, Prometheus serving and the application unaffected; the password never appears in the collector's log.

Still open:

- **Not run against a real OpenObserve in the composed stack** (as in section N); the same ADR-59 restart cost applies.
- The monitor's link adds `trace_id` to OpenObserve's traces page; that this opens the one trace directly was not checked against a running OpenObserve (it opens the traces page in any case).
- A trace or metric sent while OpenObserve is unreachable is retried for a while by the collector, then dropped; there is no persistent queue.
- A snapshot stored before the rename keeps its old Jaeger link until the run's next step rewrites it.
- `docs/architecture/design/C-web-tests-ops-sdlc.md` and BUILD-PLAN's decision row 09 still describe the original Jaeger design; they are the design record, and ADR-71 is the change.

## P. A clause the analyst adds from a corpus search (8 October, appended)

Built (ADR-74): `POST /cases/{id}/clauses`, the Policy tab's "Add to case" with a reason, the `clause.added` and `clause.removed` audit records, the pack's "added by the analyst" line. Unit tests with fakes only.

Still open:

- **Not run on the live stack.** S1 reading the clause from the real S3 at the pinned corpus version, H1 writing the record to the real S4 and the pack carrying the clause were proved with fakes and the in-memory stores, not end to end.
- The narrative agent does not see a clause the analyst added (its context pack reads the S4 proposal ledger); the clause reaches the committee through the pack. Giving it to the narrative changes a live model's input and needs a live eval (ADR-59).
- A later re-run of research may propose the same clause again as a new item beside the analyst's; the analyst then accepts or removes the duplicate.

## Q. New case form, manual path and owner-added change types (8 October, appended)

Built (ADR-72): New case opens a form and creates nothing until "Create case"; an AI-assisted path and a manual path; the owner adds or removes change types on the case until she submits; H1 records each change (`case.types_changed`) and the field set it resolves. Checked with unit tests, fakes and vitest only.

Still open:

- **Not run live.** No manual case has been driven past submit on the composed stack. Screening, research and assessment then work from typed values and no document; the unit tests prove the run reaches screening, not how the agents fare without document quotes. That needs a Bedrock run, so it waits for the operator (ADR-59).
- If every change type the assistant proposes was already added by the owner, no `po_confirm_category` gate opens, so the questions-only intake round (which follows the last category decision) does not run. The owner's own `fieldset.resolved` stands and she fills the open fields herself.
- On the AI-assisted path the web offers "Add change type" only once the assistant's proposals are on Overview; before that Overview shows "Nothing proposed yet". The route accepts an addition at any time before submit.
- In MSW mode (`dev:msw`, vitest) a case created from the form can be filled in and its types changed, but not submitted: the mock submits only FCRM-2026-0151.

## R. Ad-hoc questions to the owner (ADR-73, D-120; 8 October, appended)

Closed:

- The analyst can ask the owner questions at any stage from triage to an unlocked recommendation (ADR-73), and the clarification round's composer shows before the round is sent (D-120). Unit tests cover the route, the stages, the PO text fence, the owner's answer, the frozen version, H1's transitions and steps; vitest covers the composer and the owner's answer.

Still open:

- **Not run on the live stack.** No live check was made (a live H1 re-run calls Bedrock, ADR-59): the ask, the owner's answer, the `question.*` records on the chain and the REVIEW re-run on changed fields are proved only with fakes and the MSW mock.
- An answer that changes a value outside REVIEW, or while agents run, is recorded and flagged ("values changed since the agents ran"), not re-run: the analyst asks for a re-run in review. The flag clears when the agents next score the case, whatever that re-run's scope was.
- A re-run marker (`rerun_active`) left set by a run that never published (a budget refusal handed to a person) keeps later answers from re-running on their own until a re-run publishes; they are flagged instead.
- The MSW mock re-runs nothing, and its Submission tab does not list the version an ad-hoc answer froze.
