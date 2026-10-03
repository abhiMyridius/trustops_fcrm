---
id: examiner.user
agent: examiner
role: user
version: 1
model_tier: mid
cache_prefix: false
variables:
  - {name: case, type: CaseIdentity, description: "id"}
  - {name: chain, type: ChainVerification, description: "verified, count, head_hash (from GET /verify/{case})"}
  - {name: versions, type: PinnedVersions}
  - {name: question, type: str, description: "the asker's question, verbatim"}
  - {name: records, type: "list[AuditRecordCompact]", description: "seq, type, by, what, ts, payload summary"}
  - {name: routing_rules, type: "list[RoutingRule]", description: "rule id and text of the pinned routing version"}
includes: []
output_schema: ExaminerOutput
changelog:
  - {version: 1, date: 2026-10-03, reason: initial, eval_run: null}
---
Case {{ case.id }} · chain verified: {{ chain.verified }} ({{ chain.count }} records, head {{ chain.head_hash[:8] }})
Versions pinned on the case: {{ versions | tojson }}

Question: {{ question }}

Audit records (seq · type · by · what · ts):
{{ section("records", records, cap=5200) }}

Routing rules {{ versions.routing_v }} (quoted):
{{ section("routing", routing_rules, cap=400) }}

Answer from these records only, or refuse. Return the schema object.
