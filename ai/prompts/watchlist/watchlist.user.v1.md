---
id: watchlist.user
agent: watchlist
role: user
version: 1
model_tier: cheap
cache_prefix: false
variables:
  - {name: list, type: ListPublication, description: "list_id, from_v, to_v, published, fixture"}
  - {name: list_diff, type: "list[ListDiffLine]", description: "country, change (added|removed|moved), verbatim line"}
  - {name: candidates, type: "list[WatchlistCandidate]", description: "id, title, stage, geographies, types, geography controls with evidence"}
  - {name: fatf_clauses, type: "list[ClauseBrief]", description: "clauses referencing the lists (FATF source), id and text"}
includes: []
output_schema: WatchlistOutput
changelog:
  - {version: 1, date: 2026-10-03, reason: initial, eval_run: null}
---
Jurisdiction list {{ list.list_id }}: {{ list.from_v }} → {{ list.to_v }} published {{ list.published }}{% if list.fixture %} (synthetic test list){% endif %}

Diff (country · change · list line):
{{ section("list_diff", list_diff, cap=400) }}

Candidate cases selected by code (id · title · stage · geographies · types · geography controls with evidence):
{{ section("candidates", candidates, cap=900) }}

Clauses referencing the lists (id · text):
{{ section("clauses", fatf_clauses, cap=300) }}

Write exactly one proposal per candidate. Return the schema object.
