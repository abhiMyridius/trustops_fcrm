# Prompt changelog

Append-only. One row per prompt file version, newest last. The same entry appears in the file's own front-matter `changelog`; this table is the readable index across agents (design C §5.9). `eval delta` is filled from `evals/results/<run_id>/<agent>.json` once a baseline exists.

| Date | Prompt id | Version | Set version | Reason | Eval run | Eval delta (primary metric) |
|---|---|---|---|---|---|---|
| 2026-10-03 | screening.system | 1 | 2026-10-03.1 | initial; five R4 facts as one reason each (contract has no typed classification) | — | baseline pending |
| 2026-10-03 | screening.user | 1 | 2026-10-03.1 | initial | — | baseline pending |
| 2026-10-03 | intake.system | 1 | 2026-10-03.1 | initial; duplicate field values emitted as separate items for code to merge | — | baseline pending |
| 2026-10-03 | intake.user | 1 | 2026-10-03.1 | initial | — | baseline pending |
| 2026-10-03 | research.system | 1 | 2026-10-03.1 | initial; "Implies:" suffix in applicability until ClauseProposal gains implies_controls | — | baseline pending |
| 2026-10-03 | research.user | 1 | 2026-10-03.1 | initial | — | baseline pending |
| 2026-10-03 | assessment.system | 1 | 2026-10-03.1 | initial; no scoring tool (ADR-07); planned controls carry no rating | — | baseline pending |
| 2026-10-03 | assessment.user | 1 | 2026-10-03.1 | initial | — | baseline pending |
| 2026-10-03 | critic.system | 1 | 2026-10-03.1 | initial; six checks mapped onto the contract's four finding types | — | baseline pending |
| 2026-10-03 | critic.user | 1 | 2026-10-03.1 | initial | — | baseline pending |
| 2026-10-03 | narrative.system | 1 | 2026-10-03.1 | initial; conditions only for overridden, planned and gap items (matches the seed's three conditions) | — | baseline pending |
| 2026-10-03 | narrative.completeness.user | 1 | 2026-10-03.1 | initial | — | baseline pending |
| 2026-10-03 | narrative.recommendation.user | 1 | 2026-10-03.1 | initial | — | baseline pending |
| 2026-10-03 | examiner.system | 1 | 2026-10-03.1 | initial | — | baseline pending |
| 2026-10-03 | examiner.user | 1 | 2026-10-03.1 | initial | — | baseline pending |
| 2026-10-03 | watchlist.system | 1 | 2026-10-03.1 | initial | — | baseline pending |
| 2026-10-03 | watchlist.user | 1 | 2026-10-03.1 | initial | — | baseline pending |
| 2026-10-03 | policy_gap.system | 1 | 2026-10-03.1 | initial; scope suggestion prefixed "Suggested scope (not policy): " | — | baseline pending |
| 2026-10-03 | policy_gap.user | 1 | 2026-10-03.1 | initial | — | baseline pending |
| 2026-10-05 | narrative.judge | 1 | 2026-10-05 | initial; the rubric judge behind the narrative_faithfulness eval metric, judge tier, evals only | — | baseline pending |
