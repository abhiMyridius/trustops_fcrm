# Commit convention

Commit history is evidence the panel reads (brief; BUILD-PLAN §8). Every commit says what changed, which decision it implements, which SDLC stage it belongs to, and which gate it passed. `ai/guidance/hooks/commit_guard.py` enforces this before `git commit` runs, and `ops/scripts/commit_msg_lint.py` enforces it again as the pre-commit `commit-msg` hook.

## Shape

```
type(scope): imperative summary, 72 characters or fewer [D-xx, ADR-nn]

Why, in one to three lines. What changed, as bullets if there is more than one thing.
Links to defect rows (DEF-nnn) and failure-log rows (FL-nn).

Stage: Development
Gate: G-C1 make check · G-T1 evals research clause_recall_at_k 0.87 (baseline 0.85)
Refs: D-19, ADR-07, DEF-012
Co-Authored-By: <the model that wrote it> <noreply@anthropic.com>
```

| Part | Rule |
|---|---|
| `type` | one of `feat fix test docs ops ai eval refactor chore seed` |
| `scope` | lower-case component id or folder: `s1-case`, `s2-scoring`, `s3-policy`, `s4-audit`, `s5-decision`, `h1-caseflow`, `h2-agent-worker`, `h3-ingestion`, `h4-context`, `contracts`, `guardrails`, `agents`, `tools`, `prompts`, `ai`, `seed`, `web`, `evals`, `ops`, `gov`, `plan` |
| summary | imperative ("add", not "added"), 72 characters or fewer before the `[...]`; no full stop |
| `[...]` | the decision ids the change implements: `D-xx` from the team's log v1.3, `ADR-nn` from `docs/governance/decision-log.md`. At least one reference is required, either here or in `Refs:` |
| `Stage:` | one of `Requirements`, `Design`, `Development`, `Testing`, `Deployment`, `Operations` |
| `Gate:` | the gate the change passed, with its evidence: `G-C1 make check` at minimum; add `G-T1 evals <agent> <metric> <value>` when a prompt or model changed |
| `Refs:` | optional; repeats decision ids and adds `DEF-nnn` and `FL-nn` |

Rules that go with the shape:
- One logical change per commit. Commit small and often: a prompt version, its eval result and the lock update go together; unrelated fixes do not.
- A `fix` commit names its `DEF-nnn` row (`make gate` fails a `fix` without one).
- A change to `ai/prompts/**` or `ai/models/tiers.yaml` carries the eval evidence in `Gate:`, and the results file goes in the same commit.
- `.cache/check.ok` must be newer than every changed file: run `make check` last, then commit.
- Never `--no-verify`.

## Worked examples

A feature in a service:

```
feat(s2-scoring): POST /score returns boundary distance to the nearer edge [D-42, ADR-13]

The boundary flag now reports the distance to the nearer of the next band's lower edge
and the edge just crossed, which keeps the flag on at 10.4 after the CTL-T01 override.
- engine.boundary_flag returns (flag, distance, edge)
- golden tests: 9.8 -> flag 0.3 below 10.1; 10.4 -> flag 0.3 above 10.1

Stage: Development
Gate: G-C1 make check (92 passed)
Refs: D-42, ADR-13
```

A new prompt version promoted through the eval gate:

```
ai(prompts): research.system v2 rejects paraphrased clause quotes [D-91, ADR-14]

v1 produced one paraphrased clause on 0151 attempt 1 (Guard 4 reject, audit #28).
v2 adds a worked wrong/right example to cite_verbatim and moves the quote rule to rule 1.
- prompts/research/research.system.v2.md, MANIFEST current -> 2, set 2026-10-06.1
- evals/results/2026-10-06T14-22Z-a1b2c3/research.json committed
- registry.lock/prompts.lock regenerated

Stage: Testing
Gate: G-C1 make check · G-T1 evals research citation_validity 0.962 (baseline 0.931) PASS
Refs: D-91, ADR-14, FL-03
```

A defect fix:

```
fix(s1-case): PO case response no longer carries nested score.band [ADR-19, D-62]

The PO projection copied the run snapshot, which nests score{band}. The projection now
builds CasePO field by field, and the response middleware fails closed on any restricted key.

Stage: Testing
Gate: G-C1 make check · contract test test_po_dto_no_restricted_fields
Refs: DEF-001
```

An operations change:

```
ops(compose): agent-worker waits for OPA and RabbitMQ health before consuming [ADR-02, ADR-20]

Stage: Deployment
Gate: G-C1 make check · make up && make seed-verify on a clean clone
```
