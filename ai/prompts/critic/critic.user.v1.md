---
id: critic.user
agent: critic
role: user
version: 1
model_tier: judge
cache_prefix: false
variables:
  - {name: case, type: CaseIdentity, description: "id, submission_v, domains, opened"}
  - {name: versions, type: PinnedVersions}
  - {name: score, type: ScoreSummary, description: "read-only S2 result: composite, band, boundary_flag"}
  - {name: proposals, type: "list[LedgerProposalCompact]", description: "id, agent, item type, key fields, quote, source, guard4 result"}
  - {name: precedent_ratings, type: "list[PrecedentRating]", description: "case, control, rating; plus the given mean per control"}
  - {name: rules_result, type: "list[RuleResult]", description: "R-1..R-8 with ok and detail, computed by code"}
  - {name: evidence_dates, type: "list[EvidenceDate]", description: "control, artefact, date, scope, stale (code flag)"}
includes: []
output_schema: CriticOutput
changelog:
  - {version: 1, date: 2026-10-03, reason: initial, eval_run: null}
---
Case {{ case.id }} · v{{ case.submission_v }} · domains in scope {{ case.domains | join(", ") }} · case opened {{ case.opened }}
Versions: scoring {{ versions.scoring_v }} · corpus {{ versions.corpus_v }} · controls {{ versions.controls_v }}
Engine result (read-only, not yours to judge): composite {{ score.composite }} · band {{ score.band }} · boundary flag {{ score.boundary_flag }}

Proposals in the ledger (id · agent · item · key fields · quote · source · quote check):
{{ section("proposals", proposals, cap=4200) }}

Precedent ratings (case · control · rating; mean as given):
{{ section("precedent_ratings", precedent_ratings, cap=600) }}

Rules check (computed by code):
{{ section("rules", rules_result, cap=400) }}

Evidence dates and code stale flags (control · artefact · date · scope · stale):
{{ section("evidence_dates", evidence_dates, cap=400) }}

Run the six checks in order and report findings in the schema.
