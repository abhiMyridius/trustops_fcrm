---
id: research.user
agent: research
role: user
version: 1
model_tier: strong
cache_prefix: false
variables:
  - {name: case_id, type: str}
  - {name: pack, type: ContextPack}
changelog:
  - {version: 1, date: 2026-10-03, reason: test fixture}
---
Case {{ case_id }} · strong-tier budget {{ config("tiers.strong.budget.tokens") }} tokens
Case fields: {{ section(pack, "fields") }}
Retrieved clauses: {{ section(pack, "clauses") }}
