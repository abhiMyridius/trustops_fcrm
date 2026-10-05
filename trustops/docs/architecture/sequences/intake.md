# Sequence: intake

From document upload through submission. Every step names the real transition in `fcrm_caseflow.transitions` that fires it.

```mermaid
sequenceDiagram
    actor PO as Product Owner
    participant W1 as fcrm-web
    participant S1 as case (S1)
    participant X3 as RabbitMQ
    participant H1 as Caseflow (H1)
    participant H3 as ingestion-worker (H3)
    participant H2 as agent-worker (H2)
    participant S4 as audit (S4)
    participant Br as Bedrock

    PO->>W1: Upload documents
    W1->>S1: POST /cases/{id}/documents
    S1->>X3: case.document_stored
    X3->>H1: consume
    Note over H1: CASE_DOCUMENT_STORED (INTAKE -> INTAKE)
    H1->>H3: ingest.run
    H3->>H3: PDF -> Markdown, page:line anchors, Guard 1 (PII mask)
    H3-->>H1: ingest.done
    H1->>H2: agent.run intake (once every doc extracted)
    H2->>Br: one call, cheap tier
    Br-->>H2: ExtractionProposal[], CategoryProposal[], GapQuestion[]
    H2-->>H1: agent.done
    Note over H1: AGENT_DONE_INTAKE (INTAKE -> INTAKE)
    H1->>S4: append proposals
    H1->>S1: push items; open po_confirm_category gates
    S1-->>W1: SSE: items ready

    PO->>W1: Confirm change types
    W1->>S1: gate.decided po_confirm_category (per type)
    S1->>X3: gate.decided
    X3->>H1: consume
    Note over H1: GATE_DECIDED_PO_CONFIRM_CATEGORY_ALL (INTAKE -> INTAKE)
    H1->>S1: fieldset.resolved (resolve_required_fields)

    PO->>W1: Review & submit (owner corrections)
    PO->>W1: Submit
    W1->>S1: POST /cases/{id}/submit
    S1->>S1: evaluate_submit_checks (completion, always-required, gaps, owner-reviewed)
    S1->>X3: case.submitted
    X3->>H1: consume
    Note over H1: CASE_SUBMITTED, guard: four_submit_checks_passed (INTAKE -> TRIAGE)
    H1->>S1: record case.submitted; request intake eval grade
```
