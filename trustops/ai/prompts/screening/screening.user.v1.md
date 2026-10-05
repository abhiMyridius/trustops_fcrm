---
id: screening.user
agent: screening
role: user
version: 1
model_tier: cheap
cache_prefix: false
variables:
  - {name: case, type: CaseFields, description: "id, submission_v, change_types, fields[{id, label, value, source}]"}
  - {name: versions, type: PinnedVersions}
  - {name: derived, type: ScreeningDerivedFacts, description: "code facts: volume_usd parsed from F05, vendor_known (existing vendor match)"}
  - {name: precedents, type: "list[PrecedentTitle]", description: "top 3: case_id, title, change_types, fast_lane"}
includes: []
output_schema: fcrm_contracts.proposals.screening.ScreeningProposal
changelog:
  - {version: 1, date: 2026-10-03, reason: initial, eval_run: null}
---
Case {{ case.id }} · submission v{{ case.submission_v }} · change types: {{ case.change_types | join(", ") }}
Versions: routing {{ versions.routing_v }} · field set {{ versions.fieldset_v }}

Case fields (id · label · value · source):
{{ section("fields", case.fields, cap=600) }}

Code-derived facts (do not re-derive): annual_volume_usd={{ derived.volume_usd }} · existing_vendor_match={{ derived.vendor_known }}

Nearest closed cases (id · title · types · fast_lane):
{{ section("precedents", precedents, cap=300) }}

Establish the five facts, decide fast_lane, and answer in the schema. Use case_id "{{ case.id }}".
