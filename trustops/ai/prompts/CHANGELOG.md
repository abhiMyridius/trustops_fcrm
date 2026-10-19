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
| 2026-10-06 | screening.system | 2 | 2026-10-06 | case-field citation stated; pack declared complete; cite_verbatim replaces declare_gap; no pasted schema | see `evals/README.md` | see `evals/README.md` |
| 2026-10-06 | intake.system | 2 | 2026-10-06 | written to the contract (no not_found, facts_flagged, notes); doc id and window id stated; pack declared complete | see `evals/README.md` | see `evals/README.md` |
| 2026-10-06 | research.system | 2 | 2026-10-06 | gap and jurisdiction items mapped to their fields; search is the exception; compare_precedent dropped | see `evals/README.md` | see `evals/README.md` |
| 2026-10-06 | assessment.system | 2 | 2026-10-06 | citation of factors and controls stated; fields mapped to the contract; pack declared complete | see `evals/README.md` | see `evals/README.md` |
| 2026-10-06 | critic.system | 2 | 2026-10-06 | record_refs are sequence numbers only; a finding cites an audit record; empty list is valid | see `evals/README.md` | see `evals/README.md` |
| 2026-10-06 | narrative.system | 2 | 2026-10-06 | written to the contract; the Narrative cites the case; numbers only from the pack; a thin pack still gets a recommendation | see `evals/README.md` | see `evals/README.md` |
| 2026-10-06 | examiner.system | 2 | 2026-10-06 | written to ExaminerAnswer: a refusal is no sentences; no answered, records_read or refusal reason | see `evals/README.md` | see `evals/README.md` |
| 2026-10-06 | watchlist.system | 2 | 2026-10-06 | aligned to ReflagProposal (rerun boolean); list handle stated; empty list is the right answer when no candidate | see `evals/README.md` | see `evals/README.md` |
| 2026-10-06 | policy_gap.system | 2 | 2026-10-06 | written to GapSuggestion; the gap id is printed in the pack and copied; empty list when every gap is covered | see `evals/README.md` | see `evals/README.md` |
| 2026-10-06 | screening.system | 3 | 2026-10-06.1 | R4 stated as the vendor case and the feature case; change types read from the identity line, the volume from F05 (EMPTY or at least $5M is no); six facts; a described new corridor, segment or product vetoes both cases | see `evals/README.md` | see `evals/README.md` |
| 2026-10-06 | research.system | 3 | 2026-10-06.2 | a clause proposal's clause_id and its source_ref name the same clause | see `evals/README.md` | see `evals/README.md` |
| 2026-10-19 | narrative.reduce | 1 | 2026-10-19 | initial; merges the partial recommendation answers of a split job on the cheap tier (ADR-77, ADR-78) | — | baseline pending |
