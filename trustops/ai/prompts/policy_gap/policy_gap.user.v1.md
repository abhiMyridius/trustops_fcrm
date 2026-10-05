---
id: policy_gap.user
agent: policy_gap
role: user
version: 1
model_tier: mid
cache_prefix: false
variables:
  - {name: versions, type: PinnedVersions, description: "the active corpus version at run time"}
  - {name: gaps, type: "list[GapRecord]", description: "gap id, case id, topic, statement, searched queries, filters and versions"}
  - {name: nearest_clauses, type: "list[NearestClause]", description: "gap id, clause id, source, score, text (fresh search, top 5 per gap)"}
includes: []
output_schema: PolicyGapOutput
changelog:
  - {version: 1, date: 2026-10-03, reason: initial, eval_run: null}
---
Corpus in force: {{ versions.corpus_v }}

Open gap records (gap id · case · topic · statement · searched queries, filters and versions):
{{ section("gaps", gaps, cap=1200) }}

Fresh nearest clauses per gap (gap id · clause id · source · score · text):
{{ section("nearest", nearest_clauses, cap=1800) }}

Close gaps that are now covered and write suggestions for the rest. Return the schema object.
