# Glossary

| Term | Meaning |
|---|---|
| **Case** | One assessment of a proposed change at the bank, identified `FCRM-YYYY-NNNN`. |
| **Change type** | One of six: new product, new feature, process change, vendor/third-party onboarding, new geography, new customer segment. A case may have more than one. |
| **Domain** | One of four FFIEC risk domains: customer, product, geography, channel/third-party. Each change type maps to one or more domains. |
| **Field set** | The versioned list of fields a case must fill: a fixed always-required core plus one pack per selected change type. |
| **Completion** | filled required fields ÷ total required fields. Computed in exactly one function, `fcrm_case.domain.completion.compute_completion`. |
| **Submission version** | A case's fields as they stood at one point in time; a clarification round or an owner correction creates a new version, never edits the old one in place. |
| **Review item** | One AI-proposed clause, risk factor, or control rating (or one critic finding) awaiting the analyst's per-item decision. Never decided in bulk. |
| **Inherent score** | Likelihood × Impact (each 1–5), before controls. |
| **Control effectiveness (CE)** | The weighted mean of a domain's matched, non-planned controls' ratings, weights normalised to the domain's matched controls. |
| **Retention** | `max(0.4, 1 − 0.6 × CE / 10)` — the fraction of inherent risk that survives the controls. |
| **Residual** | Inherent × Retention, per domain. |
| **Composite** | The weighted sum of residuals across in-scope domains, rounded once to one decimal. The one number a band is looked up from. |
| **Band** | 1 (0–5.0) · 2 (5.1–10.0) · 3 (10.1–17.0) · 4 (17.1–25.0). Bands 1–2 route to the analyst; 3–4 to committee. |
| **Boundary flag** | Raised when the composite sits within 0.5 of the next band's lower edge, or of the edge just crossed. Must be acknowledged before release. |
| **Planned control** | A control that is scheduled but not yet evidenced. Recorded, never scored (rule 7). |
| **Override** | An analyst replacing an AI-proposed value with their own, always with a written reason (rule 6). |
| **Pack** | The frozen, content-hashed snapshot of a case's locked recommendation, score and conditions, sent to committee. Never recomputed from live data while a vote is open. |
| **No-majority** | The tally result when no two of three committee votes agree. Reopens a vote round; never resolves to any outcome by default (ADR-08). |
| **Outcome** | One of four: approve, approve with conditions, defer, reject. Never produced automatically. |
| **Condition** | A commitment attached to an approve-with-conditions outcome (e.g. "complete the corridor due-diligence review before go-live"), tracked with an owner and a due date. |
| **Audit record** | One hash-chained, append-only entry in a case's evidence trail. See `docs/governance/audit-chain.md`. |
| **Proposal / ledger** | An agent's output before a human decides on it, stored in the evidence ledger (distinct from the audit chain, which only ever records decided state). |
| **Claim** | The provenance mixin (`source_ref` + `quote` + `confidence`) every agent assertion carries, so it can be checked against its cited source. |
| **Fast lane** | A screening-agent-proposed shortcut for a vendor renewal or a small, scope-free feature change — still analyst-confirmed, never automatic. |
| **Caseflow (H1)** | The orchestrator: a state machine that dispatches agent jobs, opens human gates, calls the scoring engine, and records what happened. It never reasons. |
| **Guard** | One of seven checks every agent call passes through: input scan, OPA tool permit, budget, output shape, the eight rules, critic, human gate. |
| **Tier** | An agent's model class: cheap, mid, strong, or judge — each with its own Bedrock model, fallback, and token/wall-clock budget. |
| **R1–R6** | The six routing rules: delegated close, committee quorum, boundary acknowledgement, fast lane, auto-triage, and the committee-question/addendum/re-vote path. See `docs/governance/rules.md`. |
