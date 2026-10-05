---
id: intake.user
agent: intake
role: user
version: 1
model_tier: cheap
cache_prefix: false        # the field-set block is the second cache point candidate for continuation windows (design B §3.4)
variables:
  - {name: case, type: CaseIdentity, description: "id, submission_v, candidate_types"}
  - {name: versions, type: PinnedVersions}
  - {name: doc, type: DocumentRef, description: "id, name, pages (total)"}
  - {name: window, type: PageWindow, description: "id, start, end, deferred (pages left for a later window)"}
  - {name: fields, type: "list[FieldRule]", description: "field set rows for the candidate types: id, label, value_type, always_required, synonyms"}
  - {name: prior_ids, type: "list[str]", description: field ids already extracted in other windows}
  - {name: pages, type: "list[DocumentPage]", description: "whole pages in document order, lines rendered as 'p.N l.M: text'"}
  - {name: country_names, type: "list[str]", description: "jurisdiction list names only, for F20 spelling"}
includes: []
output_schema: IntakeOutput
changelog:
  - {version: 1, date: 2026-10-03, reason: initial, eval_run: null}
---
Case {{ case.id }} · submission v{{ case.submission_v }} · document {{ doc.id }} "{{ doc.name }}" · window {{ window.id }}: pages {{ window.start }}–{{ window.end }} of {{ doc.pages }}{% if window.deferred %} (pages {{ window.deferred }} come in a later window){% endif %}
Versions: field set {{ versions.fieldset_v }} · jurisdictions {{ versions.jurisdictions_v }}
Candidate change types so far: {{ case.candidate_types | join(", ") if case.candidate_types else "none yet" }}

Fields to look for (id · label · type · always required · synonyms):
{{ section("field_set", fields, cap=700) }}

Already extracted in other windows (do not repeat): {{ prior_ids | join(", ") if prior_ids else "none" }}
Country names as the jurisdiction list spells them: {{ country_names | join(", ") }}

Document text with page and line anchors:
{{ section("document", pages, cap=3400) }}

Extract values, propose change types, flag contradictions and ask gap questions for this window only. Return the schema object with case_id "{{ case.id }}" and window_id "{{ window.id }}".
