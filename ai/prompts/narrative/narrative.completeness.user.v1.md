---
id: narrative.completeness.user
agent: narrative
mode: completeness
role: user
version: 1
model_tier: mid
cache_prefix: false
variables:
  - {name: case, type: CaseIdentity, description: "id, submission_v"}
  - {name: versions, type: PinnedVersions}
  - {name: required, type: RequiredCounts, description: "count, filled (computed by code)"}
  - {name: fields_status, type: "list[FieldStatus]", description: "id, label, value or EMPTY, source, always_required"}
  - {name: findings, type: "list[CriticFindingCompact]", description: "id, type, severity, about, text"}
  - {name: gaps, type: "list[PolicyGapCompact]", description: "topic, statement"}
  - {name: evidence_dates, type: "list[EvidenceDate]", description: "control, artefact, date, scope, stale (code flag)"}
  - {name: answers, type: "list[OwnerAnswer]", description: "question id, anchored_to, answer, doc"}
includes: []
output_schema: CompletenessOutput
changelog:
  - {version: 1, date: 2026-10-03, reason: initial, eval_run: null}
---
Case {{ case.id }} · v{{ case.submission_v }} · required fields {{ required.count }} · filled {{ required.filled }}
Versions: field set {{ versions.fieldset_v }} · controls {{ versions.controls_v }} · corpus {{ versions.corpus_v }}

Field status (id · label · value or EMPTY · source · always required):
{{ section("fields", fields_status, cap=900) }}

Critic findings (id · type · severity · about · text):
{{ section("findings", findings, cap=700) }}

Research gaps (topic · statement):
{{ section("gaps", gaps, cap=300) }}

Evidence dates (control · artefact · date · scope · stale):
{{ section("evidence_dates", evidence_dates, cap=300) }}

Owner answers so far (question · anchored to · answer · document):
{{ section("answers", answers, cap=400) }}

Report missing, stale and contradictory items, draft the single clarification round, and decide enough_to_assess. Return the schema object.
