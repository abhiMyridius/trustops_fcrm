# AI across the six SDLC stages: the evidence

The brief grades whether AI was used across all six stages of the software lifecycle, and whether humans gate the stages with a stated reason. This page says what was done at each stage, by what, where the artefacts are, which gate applied, and how a reviewer can check each claim from the repository.

## What this build was

**The build was done by AI lanes under an operator-approved plan.** The plan is `BUILD-PLAN.md` (section 5, "one continuous run"). Each lane was one Claude Code subagent in its own git worktree, briefed from `ai/guidance/subagent-brief-template.md`; a lead session owned `main`, merged, and kept the governance tables. The operator approved the plan and the ADR rows, and reviews asynchronously.

**Operator gates were recorded, not blocking.** `docs/governance/gates-register.md` says it directly: "During the single continuous build run (BUILD-PLAN section 5) operator gates do not block: the run records the evidence ... and continues; the operator reviews asynchronously and objects through an ADR or defect row." The hook gates (G-C1 commit gate, G-T1 eval gate) are meant to block. In practice: G-C1 is enforced by the git `pre-commit` and `commit-msg` hooks that `make bootstrap` installs, and by the discipline of running `make check` before every commit (the `Gate:` trailer says so); the Claude Code Stop-hook registration is not in the repository, and the live eval gate G-T1 is run on demand, not by a hook (`docs/governance/known-gaps.md`, section B). The plan says gate records go to `ops/reports/`; no such record exists. The visible trace of operator approval is the status column of the ADRs: 13 of 49 read "accepted (operator, 3 Oct)" or "(operator, 5 Oct)", 19 "accepted (orchestrator)" or "(lead)", and 17 are still "proposed", which the plan treats as accepted from the start of the run.

## Evidence queries

Run from the repository root on 6 October 2026 at commit `f602969`, the commit before this file was added. To refresh them, re-run the queries and append a new dated table below; the governance hook refuses edits to existing table rows, so the old snapshot stays.

```bash
git log --oneline | wc -l                                     # all commits
git log --merges --oneline | wc -l                            # merge commits
git log -i --grep "Stage: requirements" --oneline | wc -l     # and design, development, testing, deployment, operations
git log --grep "^Gate:" --oneline | wc -l                     # commits that name the gate they passed
git log --grep "Co-Authored-By: Claude" --oneline | wc -l     # commits the model co-authored
git log --merges --oneline | grep -ci "merge lane"            # lane merges
grep -cE '^\| ADR-[0-9]+ ' docs/governance/decision-log.md    # decisions
grep -cE '^\| D-[0-9]+ '   docs/governance/defect-trail.md    # defects
grep -cE '^\| F-[0-9]+ '   docs/governance/failure-log.md     # build-process failures
```

| Measure | 6 Oct 2026, `f602969` |
|---|---|
| Commits | 224 (first 3 Oct, last 6 Oct) |
| Merge commits | 26 (16 named "Merge lane ...", 9 syncs of `main` into a lane worktree, 1 the merge of the inner repository) |
| `Stage: requirements` | 2 |
| `Stage: design` | 8 |
| `Stage: development` | 127 |
| `Stage: testing` | 28 |
| `Stage: deployment` | 10 |
| `Stage: operations` | 2 |
| Commits with any `Stage:` trailer | 203 of 224 |
| Commits with a `Gate:` trailer | 202 |
| Commits with `Co-Authored-By: Claude` | 216 |
| ADRs / defect rows / failure-log rows | 49 / 57 / 5 |
| Gates in the register | 16 product gates, 9 SDLC gates |
| Unit tests | 7,094 passed, 4 skipped (`make check`, 288 s of 355 s total) |
| Integration tests | 538 marked and deselected by `make check` (464 run by `make test-int`, 74 by no target); all run on a throwaway replica set: 525 passed, 14 skipped |
| Rego tests / web tests | 120 / 144 |
| Live eval gate | 8 of 9 agents pass (`evals/results/2026-10-06/gate.json`) |

**Read the stage counts with care.** The `Stage:` trailer convention (`ai/guidance/commit-convention.md`) is enforced by the `commit-msg` hook, which `make bootstrap` installs. It says which stage a commit belongs to; it does not measure effort. Requirements and design were done in a few large commits on 3 October (`1431780`, `9d673d6`, `c3af3ad`, `dc392a8`, `f601ed2`, `c2f3822`) and reopened later through ADR rows and the research documents, so their trailer counts are small. Operations has two trailers (a repository housekeeping commit and the runbooks) because the operating loop (monitoring, nightly runs, triage) was designed and partly built but not run as a routine (see stage 6). The 21 commits without a trailer are the first imports, git-generated merges and a few early housekeeping commits.

## The mechanics, once

| Mechanism | Where | What it enforces |
|---|---|---|
| Commit convention | `ai/guidance/commit-convention.md` | `type(scope): summary [D-xx, ADR-nn]`, a `Stage:` and a `Gate:` trailer, one logical change per commit |
| commit-msg lint | `ops/scripts/commit_msg_lint.py`, `.pre-commit-config.yaml` | a decision reference and a `Stage:` of the six; a missing `Gate:` warns |
| Append-only governance | `ops/scripts/governance_append_only.py` (pre-commit) | no edit or deletion of an existing table row in `docs/governance/*.md` |
| Commit gate G-C1 | `make check`; the pre-commit hooks lint, format-check, type-check and run the unit tests of the changed packages | ruff, mypy `--strict` per `mypy.ini`, OPA, unit tests, env contract, web typecheck and vitest |
| Claude Code hooks (scripts) | `ai/guidance/hooks/`: `protect_paths.py`, `commit_guard.py`, `fmt_py.sh`, `fmt_ts.sh`, `stop_gate.py`, `subagent_report.py`, `stage_context.py` | refuse edits to generated, secret or append-only files; block `git commit` without a fresh green check and a conventional message; run `make check` before a turn ends; require Changed, Failed, Decided in a subagent's report; show the stage and open defects. Registration is in the operator's `.claude/settings.json`, which is not committed |
| Worktree protocol | `ai/guidance/worktree-protocol.md` | one worktree and one branch per lane; the lead alone merges; rebase on `main` before asking; no bare `git stash` |
| Subagent brief template | `ai/guidance/subagent-brief-template.md` | every brief names the decisions not to reopen, files to read, outputs, forbidden changes, the gate, and ends with Changed, Failed, Decided |
| Review checklist | `ai/guidance/review-checklist.md` | the eight rules, the visibility matrix, version pins, idempotency, audit, accessibility, code standards, for the G-C2 review |
| Coding standards | `ai/guidance/coding-standards.md` (ADR-21) | Python 3.13, type hints, docstrings, `logging`, specific exceptions, tests only under `tests/` |
| Eval gate G-T1 | `make evals`, `make evals-gate AGENT=<a>`, `evals/results/` | an agent passes if its primary metric is measured, meets its threshold, is no worse than the baseline, and 80% of rows completed (ADR-43) |

Lane merges, by id: I1-A, I1-B, I2-A1, I2-A2, I2-B, I2-C, I3-A, I3-B1, I3-B2, I3-C, I3-D, I3-E, I4-A, I4-B, I4-C, I5-A (`git log --merges --oneline | grep -i "merge lane"`). Before the I-series, the first wave of lanes (contracts, scoring, audit, caseflow, case, decision, policy, guardrails, common, the `ai/` layer, web shell, agents, evals, workers, ops) merged on 3 and 4 October with commit messages such as "merged from the parallel lane". The build-process failures the lanes and the lead hit are in `docs/governance/failure-log.md` (F-01 to F-05: embedded worktree gitlinks committed, a corrupted git identity, a shell substitution in a commit message, a wedged Docker container, a fix that shipped a subtler bug than the one it fixed). They are the answer to "how did your build agents fail and how did you recover".

## Stage 1: Requirements

- **What AI did.** Research subagents read the brief, the planning set and the BA's prototype and wrote `docs/requirements/research/00-synthesis.md` to `07-runtime-facts.md`: the brief and rubric, the build-plan and decision-log notes, the blueprint and flow map, the prototype teardown, the AEO reuse inventory (with verified bugs to fix), the walkthrough seed extraction (every demo figure with its source line, and a recompute of all 80 seed cases) and the verified AWS facts. A synthesis subagent expanded these into the specification, personas, glossary, the eight rules and R1 to R6, and the open questions. The scoring recompute found the post-override composite that the documents disagreed on (D-01: 9.8 to 10.4).
- **Artefacts.** `docs/requirements/spec.md`, `personas.md`, `rules.md`, `glossary.md`, `open-questions.md`, `research/`, `source/`.
- **Human gate.** G-R1 spec sign-off (operator): "a wrong assumption multiplies through every later stage." Recorded as the operator's acceptance of the first ADR rows on 3 October (ADR-01, ADR-02, ADR-03, ADR-05, ADR-06 and ADR-21: "accepted (operator, 3 Oct)").
- **Check.** `git log -i --grep "Stage: requirements" --oneline` (2 commits, 3 Oct); `docs/governance/defect-trail.md` D-01 to D-07 (defects found in the documents before any code existed).

## Stage 2: Design

- **What AI did.** Plan subagents wrote three design documents (A backend, B AI layer, C web, tests, ops and the SDLC harness), the C4 context and container diagrams, six sequence diagrams, the data model and the event catalogue, and drafted the ADR rows. `fcrm_contracts` was written first so every lane built against the same shapes; the OpenAPI export (`make openapi`, 110 paths) is the committed contract and the web's route test fails when the web calls a route the export does not hold.
- **Artefacts.** `docs/architecture/` (`design/`, `c4-*.md`, `sequences/`, `data-model.md`, `event-catalogue.md`, `openapi/api.json`), `docs/governance/decision-log.md` (49 ADRs), `gates-register.md`.
- **Human gates.** G-D1 architecture review (each ADR accepted before the lane that implements it starts) and G-D2 contract freeze. Evidence: ADRs carry "accepted (operator, ...)" on the ones that change the shape of the system (ADR-01 monorepo, ADR-02 RabbitMQ, ADR-03 AWS everywhere, ADR-25 and ADR-26 the repo move and the eight-container split, ADR-27 live Bedrock only). Several design documents were found wrong when code met them (D-16 numbering drift, D-18, the sequence-versus-walkthrough conflict resolved by ADR-07), and the web's route names became the API contract by ADR-30.
- **Check.** `git log -i --grep "Stage: design" --oneline` (8 commits); the ADR table; `docs/architecture/sequences/README.md`.

## Stage 3: Development

- **What AI did.** Sixteen named lane merges and the first wave wrote every service, worker, package and the web app. The agents the product runs (nine, one Pydantic AI call each) were themselves written, prompted and tuned by lanes against live evals (lane I3-E). Hooks and guidance in `ai/guidance/` shaped what a lane could do; `CLAUDE.md` at the root, in `ai/` and in `src/frontend/` tells a lane the rules.
- **Artefacts.** `src/backend/`, `src/frontend/`, `ai/` (prompts with versions and locks, agent and tool specs, guards, model tiers, OPA policies), `ops/`.
- **Human gates.** G-C1 commit gate (automatic: a failing `make check` blocks); G-C2 review, `/code-review` on each merge plus a person judging rule drift (the plan; the repository holds no record that it ran on each merge). The merge routine caught real defects the lanes could not see alone: D-17, D-37 (two lanes' settings merged without a textual conflict), D-38 (a stale OpenAPI export), D-44 (a lane coded against an out-of-date contract), D-45 (two lanes added the same fake). Each became a row and, from D-37, a step in the merge routine.
- **Check.** `git log -i --grep "Stage: development" --oneline | wc -l` (127); `git log --merges --oneline`; the defect trail (57 rows, each with a disposition); `make check`.

## Stage 4: Testing

- **What AI did.** Tests were generated from the rules and the scene table: golden scoring for the demo and all 80 seed cases (9.8 to 10.4 is a unit test, not a seeded figure), property tests on the rules, state-machine and replay tests, live-Mongo tests on a throwaway replica set (transactions, the outbox, the insert-only audit role), black-box scenarios on the product rules (BB-01 to BB-14, in process), 120 Rego tests, 144 web tests with axe checks, and an adversarial edge-case audit (D-27: eight input-hardening defects found by driving the real apps with hostile input). Evals: nine datasets, 27 deterministic metrics and a DeepEval judge on a different model than the assessor, run live on Bedrock. The first live run found that the system passed 1 of 9 gates (D-46, D-47); a lane fixed seven findings; the second run passes 8 of 9 (`evals/README.md`, `evals/results/`).
- **Artefacts.** `tests/`, `evals/` (README, results, dashboards), `src/backend/packages/evals/`.
- **Human gates.** G-T1 eval gate (hook-class: "a prompt cannot land if its agent's score drops"; in practice run on demand and recorded in commit trailers such as `c61484a`, `09b23ce`, `13d6dbe`); G-T2 release gate (operator): `make gate`, `make e2e` and no open P1. `make e2e` is not wired, and `ops/runbooks/release-checklist.md` names what replaces it.
- **Limits, stated.** The assessment agent fails its gate (`li_within_one` 0.70 against 0.85) and is reported failing with the reason (`known-gaps.md`, section D). There is no live end-to-end suite and no resilience drill.
- **Check.** `git log -i --grep "Stage: testing" --oneline | wc -l` (28); `make check`; `evals/results/2026-10-06/gate.json`; `evals/results/2026-10-05/` (the first, failing run, kept).

## Stage 5: Deployment

- **What AI did.** The compose stack (first 24 containers, then eight, ADR-26), Dockerfiles, nginx gateway, OPA policy, Mongo and RabbitMQ init, MinIO init, the env contract and its checker (`make compose-check`), the Makefile, `make env` and `make keys`, the seed reset scripts, the runbooks, `RUNNING.md` and the AWS design (`ops/aws/README.md`). Booting the stack found real defects that unit tests could not: an index-name conflict that crashed H1 (D-24), a fresh clone that could not start because `make env` did not generate the demo password hash (D-25), an unhealthy gateway on IPv6 (D-26), live mode with no AWS region (D-30), an untracked mounted file a fresh clone would lack (D-33).
- **Artefacts.** `docker-compose.yaml`, `compose.*.yaml`, `Makefile`, `ops/`, `RUNNING.md`, `ops/runbooks/`, `ops/aws/README.md`.
- **Human gate.** G-P1 clean-clone proof (operator): "a demo that works once is not the bar." The steps are in `ops/runbooks/release-checklist.md`. No clean-clone proof is recorded in the repository yet; `make seed-verify` and `make e2e`, which the plan used for it, are not wired.
- **Check.** `git log -i --grep "Stage: deployment" --oneline | wc -l` (10, plus the earlier `feat(ops)` commits without a trailer); `make compose-check`; `docs/governance/defect-trail.md` D-24 to D-26, D-30 to D-33.

## Stage 6: Operations

- **What AI did.** Two kinds of operations work. (1) The product's own operations loop, which is AI too: the examiner agent answers an auditor's question from the audit chain only, each sentence citing a record that exists; the watchlist agent explains which open cases a new FATF list touches, after code decides who is affected; the policy-gap agent suggests missing clauses to an analyst; the Assurance screens read override rates, version pins and agent quality from the eval results (`docs/architecture/sequences/oversight.md`). (2) The observability stack (OpenTelemetry to Jaeger, Prometheus and three Grafana dashboards), the runbooks, the token analysis and the dead-letter tooling.
- **Artefacts.** `ops/otel/`, `ops/grafana/`, `ops/prometheus/`, `ops/runbooks/`, `ops/reports/token-analysis.md`, `src/backend/workers/caseflow/fcrm_caseflow/cores/oversight*.py`.
- **Human gate.** G-O1 nightly review (operator, ten minutes over `ops/reports/nightly/<date>.md`).
- **Limits, stated.** The nightly run was not built: `make nightly`, `make resilience`, `make token-report` output and `make build-usage` are not wired or not run, `ops/reports/nightly/` does not exist, and G-O1 was not exercised. One commit carries `Stage: operations`. The operations evidence is the runbooks, the observability configuration and the product's oversight agents, not a record of operating it.
- **Check.** `git log -i --grep "Stage: operations" --oneline` (2 commits); `ops/runbooks/README.md` (what was verified and how); `known-gaps.md` section B.

## Human gates, by stage

The product gates (16, each with the risk it controls and why it sits where it does) are in `gates-register.md`; they are what the product enforces on its users. The SDLC gates are what this build enforced on itself.

| Stage | SDLC gate | Blocking in this build? | Evidence |
|---|---|---|---|
| Requirements | G-R1 | recorded, not blocking | first ADRs accepted by the operator, 3 Oct |
| Design | G-D1, G-D2 | recorded, not blocking | ADR status column; the OpenAPI export and its ratchet test |
| Development | G-C1 | **blocking** (hooks and `make check`) | `Gate:` trailer on 202 commits |
| Development | G-C2 | recorded, not blocking | the plan; no per-merge record in the repository |
| Testing | G-T1 | on demand, not automatic | eval commits and `evals/results/` |
| Testing | G-T2 | recorded, not blocking | `ops/runbooks/release-checklist.md` |
| Deployment | G-P1 | not yet performed in the record | the checklist |
| Operations | G-O1 | not exercised | `known-gaps.md` |
