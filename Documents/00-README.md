# Risk Assessment Workbench · final document set

Genius Hacks Q3 2026 · Myridius · submitted 9 October 2026 · this index last brought in line with the build on 19 October 2026

Open the HTML files in a browser (they load fonts and the Mermaid renderer from the web). The same pages were published as live artifacts on claude.ai; links below. The local files are the current versions: the live pages have not been republished with the 19 October changes.

## The set

| # | File | What it is | Live page |
|---|------|------------|-----------|
| 1 | `01-build-plan.html` | Master spec as approved on 3 October: scope, use case, AEO reuse, services, stack-to-services mapping, agent platform, memory, seven-guard suite, scoring, data, evals, observability, auth, deploy, repos, decisions | https://claude.ai/artifact/9G5Y2dMRZqkwNB3M5hJmzd |
| 2 | `02-blueprint.html` | Architecture tab (interactive component board incl. P7 seed, components table, naming standard, assessment sequence) and Flow map tab (full case cycle, the v2 21-scene order), linked by component IDs | https://claude.ai/artifact/RMSFzCFaUum36MjKFRTbf4 |
| 3 | `03-decision-log.html` | 57 decisions (D-01 to D-97), one line each: why, what it gives us, rubric served, status. Filterable. v1.3, frozen | https://claude.ai/artifact/Qr1weE9a4yQk8twDNu1TsE |
| 4 | `04-walkthrough.html` | As-built walkthrough (v3, 7 October): 24 animated scenes in 7 chapters replicating the built app's screens for case 0151, with a chapters drawer and hover previews, a scrubber, a "behind the screen" harness panel, and callouts tagged by rubric theme; press A to hide callouts and the cursor | https://claude.ai/artifact/CnkdcYPkpWcj7zxBfXoSji |
| 5 | `05-prototype-teardown.html` | Analysis of the BA's TrustOps prototype (FCRM_v2): screens, agents, flows, rubric fit, gaps | https://claude.ai/artifact/Fd4nnxqPuaF3EAsihTQZNW |
| 6 | `06-flow-map.html` | Standalone pan-and-zoom flow map of the full case cycle with glossary and component filter (same content as the Blueprint's Flow map tab) | https://claude.ai/artifact/DN6Mo94R8ehjuV8aDRw3Qq |

Inputs, unchanged from the organisers and the BA: `Genius Hacks Q3 2026.html` and `Genius_Hacks_Connect.pdf` (the brief and rubric; the deadline printed on the page is out of date, the real one was 9 October), `TrustOps — How the Risk Assessment Workbench Works.html` (the product concept, written for the prototype), and `trascript - session1 - genius-hacks-3.txt` (the kickoff call).

## Planning set and build

Pages 1, 2, 3 and 6 are the plan the team approved on 1 to 3 October and are kept as that record; the concept page predates them. Each now opens with an "As built" notice listing where the build differs. The as-built truth is in the repository:

- `trustops/BUILD-PLAN.md`, section "Status as built": every departure from the plan, with its reason and decision row.
- `trustops/docs/governance/decision-log.md`: ADR-01 to ADR-76, each saying which D-row it adds to or amends. D-98 onward are in `defect-trail.md`.
- `trustops/docs/governance/known-gaps.md`: what is not done. `trustops/RUNNING.md`: how to run it.

`trustops/docs/requirements/source/` holds a copy of this set. It is identical except for `04-walkthrough.html`: the copy is the v2 walkthrough (21 scenes, 1 October), kept because it embeds the canonical seed (`window.FCRM_SEED`) that the seed package `fcrm_seed` is generated from.

## Decisions in force, as built

- **Stack**: FastAPI, React 19, MongoDB 8, RabbitMQ with FastStream, Python 3.13 on uv, Pydantic 2. One monorepo, eight containers: `mongo`, `rabbitmq`, `minio`, `opa`, `api` (X1 and S1 to S5 mounted as sub-apps), `harness` (H1 to H4), `gateway`, `web` (ADR-01, ADR-25, ADR-26). Redis is an optional profile nothing reads; OpenObserve is the one observability store (ADR-72).
- **Product rules**: nothing is approved or rejected automatically; scores come only from the deterministic engine; policy is quoted verbatim with a source reference; overrides need a reason; the audit trail is hash-chained and append-only; the product owner never sees a score or band. Committee: two identical votes win, a 1-1-1 split is `no_majority` and re-votes through R6 (ADR-08, ADR-31, ADR-36).
- **Lifecycle**: owner review-and-correct before submit (D-17); the completeness check and one clarification round before Policy and Controls (D-16), and since ADR-73 the analyst may also ask the owner questions at any time; committee questions answered as a pack addendum, pack v2 and re-vote if a number changes (D-18); routing rules v4 R1 to R6 (D-19); composite after the override 10.4 (D-42); one generated seed, P7 (D-57).
- **Demo case**: FCRM-2026-0151, US to Mexico remittances via Remesas del Norte. The golden figures (9.8 Band 2 with a boundary flag, override CTL-T01 7.6 to 4.5, 10.4 Band 3, approve with conditions 2-1, 67 audit records) are unit tests and the walkthrough's figures; a live run prints its own (ADR-27).

## The AI layer, as built

- **Orchestration**: Caseflow (H1, original code) runs the case; each agent job is one Pydantic AI call in the agent worker (H2). Agents propose; a person accepts, edits or rejects every proposal at a named gate (`trustops/docs/governance/gates-register.md`).
- **Nine agents**:

  | Agent | When it runs | What it proposes | Human gate |
  |---|---|---|---|
  | intake | each uploaded document | change types and field values, each with a page:line source | owner confirms each value and type |
  | screening | after triage | fast lane or full assessment (R4) | analyst confirms the lane |
  | research | assessment, in parallel with assessment | policy clauses, quoted verbatim, and declared gaps | analyst accepts each clause |
  | assessment | assessment | likelihood, impact and control ratings with evidence; never a score | analyst confirms or overrides with a reason |
  | critic | after the first score | findings against precedents, evidence dates and the eight rules, on a different model family | analyst reviews each finding |
  | narrative | completeness check, then recommendation | the completeness report and clarification questions; the recommendation and the owner's plain-language summary | analyst sends the round; analyst locks the recommendation |
  | examiner | on demand | answers from the verified audit chain only | none: it changes nothing |
  | watchlist | when a jurisdiction list is published | open cases to re-flag | analyst acknowledges each |
  | policy_gap | when the analyst notes a corpus gap | coverage suggestions, off the decision path | analyst (ADR-18); the review screen is not built |

- **Models**: Amazon Bedrock only, no direct vendor keys (ADR-03). Tiers in `trustops/ai/models/tiers.yaml`: cheap Haiku 4.5 then Nova Lite; mid Sonnet 5.5 then Haiku 4.5; strong Sonnet 5.5, retried on Sonnet 5.5, then Nova Pro; judge Nova Pro then Haiku 4.5, for the critic and the eval judges. No Opus (ADR-62, ADR-65). Live only: no replay or mock mode (ADR-27).
- **Tokens**: per-attempt budgets cheap 15k, mid 15k, strong 24k, judge 28k; a per-case ledger in H1, 160,000 enforced and 120,000 the target (ADR-50). Prompt caching is on for the Haiku and Sonnet models.
- **Failure**: one retry on the first fallback with the error shown and the full allowance, then a handoff gate the analyst completes by hand with a reason (ADR-14, ADR-48, ADR-51).
- **Seven guards**: input scan (injection patterns, rule-based PII masking), OPA tool permit, budget, output shape (verbatim quotes and sources), the eight rules, the critic, the human gate. No agent has a scoring tool (ADR-07).
- **Context**: per-agent context packs built in code and snapshotted by hash; a 197-clause synthetic policy corpus (ADR-29) searched by Titan embeddings held in memory, fused with keyword search; precedents and controls by keyword search (ADR-41, ADR-49).
- **Evals**: nine datasets, 27 deterministic metrics and a judge model, one gate per agent (ADR-43). The baseline is 8 of 9 gates, measured on the 6 October chain (assessment at 0.70 against 0.85); no baseline exists on the committed chain. The weekly figures in the walkthrough are the seed's targets.
- **SDLC**: Claude Code with written guidance, briefs and worktrees (`trustops/ai/guidance/`), git hooks that enforce the commit gate, and `trustops/docs/governance/sdlc-evidence.md`.
- **Not built**: see `known-gaps.md` section U (prompt pinning is recorded but not enforced, no amber-band model check, policy_gap review screen and weekly run).

## Out of scope

Mobile layout, notification settings, Temporal, GitHub Actions, Trivy, OCR, multi-language documents, SharePoint.

## Superseded pages (kept for history, not part of the set)

Scope · Agent Stack · standalone Architecture, each with a banner pointing to its replacement. The earlier 20-scene walkthrough was replaced by v2, and v2 by the as-built v3 on 8 October.

## To run it

AWS credentials with Bedrock access in `trustops/.env` (no Anthropic or OpenAI keys, no S3 Vectors needed); then `trustops/RUNNING.md`. Anything that calls Bedrock costs money and is run only with the operator's approval (ADR-59).
