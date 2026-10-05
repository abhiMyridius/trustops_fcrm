---
id: research.user
agent: research
role: user
version: 1
model_tier: strong
cache_prefix: false
variables:
  - {name: case, type: CaseFields, description: "id, submission_v, change_types, domains, fields"}
  - {name: versions, type: PinnedVersions}
  - {name: changed_fields, type: "list[str]", description: "empty on the first run; field ids changed since the last run (from submission_diff)"}
  - {name: new_docs, type: "list[str]", description: document ids added since the last run}
  - {name: clauses, type: "list[RetrievedClause]", description: "top 12 after rank fusion: clause_id, source_id, ref, page, score, text"}
  - {name: precedents, type: "list[RetrievedPrecedent]", description: "top 5: case_id, title, types, outcome, closed_date, similarity, ratings for in-scope domains"}
  - {name: countries, type: "list[str]", description: destination countries from F20}
  - {name: list_lines, type: "list[ListLine]", description: "country, status, verbatim line"}
  - {name: controls_brief, type: "list[ControlBrief]", description: "control_id, name, domain (in-scope domains only)"}
  - {name: tool_call_cap, type: int}
includes: []
output_schema: ResearchOutput
changelog:
  - {version: 1, date: 2026-10-03, reason: initial, eval_run: null}
---
Case {{ case.id }} · submission v{{ case.submission_v }} · types {{ case.change_types | join(", ") }} · domains in scope {{ case.domains | join(", ") }}
Versions in force: corpus {{ versions.corpus_v }} · controls {{ versions.controls_v }} · jurisdictions {{ versions.jurisdictions_v }}
{% if changed_fields %}Re-run: only these fields changed since the last run: {{ changed_fields | join(", ") }}{% if new_docs %}; new documents: {{ new_docs | join(", ") }}{% endif %}. Re-examine clauses and gaps touching them; repeat the other clauses unchanged by id.
{% endif %}
Case fields (id · label · value · source):
{{ section("fields", case.fields, cap=700) }}

Retrieved clauses (id · source · ref · page · score · text):
{{ section("clauses", clauses, cap=2400) }}

Closest closed cases (id · title · types · outcome · closed · similarity · ratings):
{{ section("precedents", precedents, cap=900) }}

Jurisdiction list lines for {{ countries | join(", ") }}:
{{ section("jurisdictions", list_lines, cap=300) }}

Controls in scope (id · name · domain):
{{ section("controls", controls_brief, cap=500) }}

If a topic in the fields has no retrieved clause, you may call policy_search up to {{ tool_call_cap }} times before declaring a gap. Return the schema object.
