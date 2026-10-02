---
id: assessment.user
agent: assessment
role: user
version: 1
model_tier: strong
cache_prefix: false
variables:
  - {name: case, type: CaseFields, description: "id, submission_v, domains, fields, opened (case date for recency)"}
  - {name: versions, type: PinnedVersions}
  - {name: changed_fields, type: "list[str]"}
  - {name: new_docs, type: "list[str]"}
  - {name: controls, type: "list[ControlRow]", description: "inventory rows for in-scope domains, never truncated: id, name, weight, evidence artefact, last tested"}
  - {name: evidence, type: "list[EvidenceItem]", description: "control, artefact, date, scope, span text with doc page:line"}
  - {name: precedent_ratings, type: "list[PrecedentRating]", description: "case, control, rating, closed; plus the given mean per control"}
  - {name: clauses_brief, type: "list[ClauseBrief]", description: "clause id and the controls it implies (from research, when it landed first)"}
includes: []
output_schema: AssessmentOutput
changelog:
  - {version: 1, date: 2026-10-03, reason: initial, eval_run: null}
---
Case {{ case.id }} · v{{ case.submission_v }} · domains in scope {{ case.domains | join(", ") }} · case opened {{ case.opened }}
Versions in force: scoring {{ versions.scoring_v }} · controls {{ versions.controls_v }}
{% if changed_fields %}Re-run on changed fields {{ changed_fields | join(", ") }}{% if new_docs %} and new document(s) {{ new_docs | join(", ") }}{% endif %}. Re-rate only the factors and controls they touch; repeat the others unchanged.
{% endif %}
Case fields (id · label · value · source):
{{ section("fields", case.fields, cap=700) }}

Controls in scope (id · domain · name · weight · evidence artefact · last tested):
{{ section("controls", controls, cap=1300) }}

Evidence seen for this case (control · artefact · date · scope · source · span text):
{{ section("evidence", evidence, cap=1200) }}

Precedent ratings (case · control · rating · closed; mean as given):
{{ section("precedent_ratings", precedent_ratings, cap=700) }}

Clauses already proposed by research (id · implies controls):
{{ section("clauses_brief", clauses_brief, cap=400) }}

Propose likelihood and impact per domain in scope and a decision for every control shown. Return the schema object.
