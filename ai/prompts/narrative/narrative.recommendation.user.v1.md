---
id: narrative.recommendation.user
agent: narrative
mode: recommendation
role: user
version: 1
model_tier: mid
cache_prefix: false
variables:
  - {name: case, type: CaseIdentity, description: "id, submission_v"}
  - {name: versions, type: PinnedVersions}
  - {name: score, type: ScoreSummary, description: "locked S2 result, analyst-visible: composite, band, route, score_record_seq"}
  - {name: ratings, type: "list[ConfirmedRating]", description: "domain, L, I, control, rating, overridden, reason"}
  - {name: clauses, type: "list[AcceptedClause]", description: "id, quote"}
  - {name: planned, type: "list[PlannedControl]", description: "control id or description, owner, target date"}
  - {name: findings, type: "list[CriticFindingResolved]", description: "finding, analyst response (explained or adjusted)"}
  - {name: gaps, type: "list[PolicyGapCompact]", description: recorded policy gaps}
  - {name: answers, type: "list[ClarificationAnswer]", description: "question, answer, doc"}
  - {name: owner_facts, type: "list[OwnerFact]", description: "facts allowed in the business summary: go-live, partner, corridor, owner name"}
includes: []
output_schema: RecommendationOutput
changelog:
  - {version: 1, date: 2026-10-03, reason: initial, eval_run: null}
---
Case {{ case.id }} · v{{ case.submission_v }} · locked result (analyst-visible, read-only): composite {{ score.composite }} · band {{ score.band }} · route {{ score.route }} · recorded at #{{ score.score_record_seq }}
Versions: scoring {{ versions.scoring_v }} · controls {{ versions.controls_v }} · corpus {{ versions.corpus_v }} · routing {{ versions.routing_v }}

Confirmed factors and ratings (domain · L · I · control · rating · overridden? · reason):
{{ section("ratings", ratings, cap=1200) }}

Accepted clauses (id · quote):
{{ section("clauses", clauses, cap=800) }}

Planned controls (not scored):
{{ section("planned", planned, cap=200) }}

Critic findings and the analyst's responses:
{{ section("findings", findings, cap=600) }}

Policy gaps recorded:
{{ section("gaps", gaps, cap=200) }}

Clarification answers (question · answer · document):
{{ section("answers", answers, cap=500) }}

Owner-facing facts allowed in the business summary (go-live, partner, corridor, owner):
{{ section("owner_facts", owner_facts, cap=300) }}

Write the recommendation (its source_ref is audit record #{{ score.score_record_seq }} of case {{ case.id }}), the conditions and the business summary. Return the schema object.
