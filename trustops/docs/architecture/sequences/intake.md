# Sequence: intake

From document upload through submission. Every step names the real transition in `fcrm_caseflow.transitions` that fires it (or the step-only trigger, where no transition exists) and the harness step (`fcrm_caseflow.cores.intake`) that does its HTTP-side work.

```mermaid
sequenceDiagram
    actor PO as Product Owner
    participant W1 as fcrm-web
    participant S1 as case (S1)
    participant X3 as RabbitMQ
    participant H1 as Caseflow (H1)
    participant H3 as ingestion-worker (H3)
    participant H2 as agent-worker (H2)
    participant S3 as policy (S3)
    participant S4 as audit (S4)
    participant Br as Bedrock

    S1->>X3: case.created
    X3->>H1: consume
    Note over H1: run opens, step intake.open (case.created, workflow.started)
    H1->>S1: PATCH state intake, event case.stage_changed

    PO->>W1: Upload documents
    W1->>S1: POST /cases/{id}/documents (multipart files, or the committed demo files)
    Note over S1: file checked, stored in MinIO uploads/{case}/{doc}/{name}, row + outbox event in one transaction
    S1->>X3: case.document_stored
    X3->>H1: consume
    Note over H1: CASE_DOCUMENT_STORED (INTAKE -> INTAKE)
    H1->>X3: ingest.run (same transaction as the transition)
    X3->>H3: consume
    H3->>H3: PDF -> Markdown, Guard 1 (PII mask, injection scan), anchors
    H3->>X3: canonical/<case>/<doc>.md and .anchored.md written, ingest.done
    X3->>H1: consume
    Note over H1: INGEST_DONE (INTAKE -> INTAKE), step intake.ingested
    H1->>S1: PATCH document extracted, event document.extracted (PO)
    H1->>S4: guard.input
    H1->>H2: agent.run intake, one per document (once none is still being read)
    H2->>Br: one call per document, cheap tier
    Br-->>H2: ExtractionProposal[], CategoryProposal[], GapQuestion[], FactFlag[]
    H2-->>H1: agent.done (each one is kept, the join waits for the last)
    Note over H1: AGENT_DONE_INTAKE (INTAKE -> INTAKE), step intake.publish
    H1->>S3: POST /search precedent_cases (top hit)
    H1->>S1: PUT intake (merged values, categories, gaps, precedent, flags): ONE write
    H1->>S4: proposal.extraction, proposal.category, proposal.gaps, precedent.matched, fact.flagged
    H1->>H1: open gates: one per change type, per blocking gap, per value, one for the precedent
    S1-->>W1: SSE: intake.proposals_ready

    PO->>W1: Confirm change types
    W1->>S1: POST /cases/{id}/intake/categories/{type}
    Note over S1: type joins the case, required fields follow, case, item, activity row and gate.decided commit together
    S1->>X3: gate.decided po_confirm_category (per type)
    X3->>H1: consume
    Note over H1: not the last: step intake.po_action only (gate.decided record), no transition
    Note over H1: the last: GATE_DECIDED_PO_CONFIRM_CATEGORY_ALL (INTAKE -> INTAKE), step intake.fieldset_resolved
    H1->>S4: gate.decided, fieldset.resolved
    H1->>H2: agent.run intake, scope questions_only (if blocking gaps are still open)
    H2-->>H1: agent.done, intake.publish merges every intake result again

    PO->>W1: Reuse values, answer questions, correct a value
    W1->>S1: POST /cases/{id}/intake/reuse, /intake/answers, /intake/values/{field}
    S1->>X3: gate.decided po_reuse / po_answer_gaps / po_confirm_value
    Note over H1: step intake.po_action (values.reused, owner.answered, owner.corrected)

    PO->>W1: Submit
    W1->>S1: POST /cases/{id}/submit
    S1->>S1: four checks (completion, always_required, types_confirmed, no_conflicts), submission v1 frozen
    S1->>X3: case.submitted
    X3->>H1: consume
    Note over H1: CASE_SUBMITTED, guard: four_submit_checks_passed (INTAKE -> TRIAGE), step submit.record
    H1->>S4: gate.validation, case.submitted
    Note over H1: TRIAGE_DISPATCH (TRIAGE -> SCREENING), step triage.assign (R5)
    H1->>S1: GET analysts/load, POST assign (lowest open count), PATCH state screening
    H1->>S4: triage.assigned, eval.recorded
    S1-->>W1: SSE: case.assigned (the analyst's name only)
```

## What the steps guarantee

- **Idempotent.** A step may run again after a failure. S1 writes are PUT or PATCH of the whole intended state; every audit record and S1 event carries an `Idempotency-Key` derived from the step id; gate and job ids are deterministic (`deterministic_id("gate", run_id, gate_type, subject_key)`); the R5 pick looks for an analyst S1 already assigned before choosing.
- **One join for the fan-out.** The intake agent runs once per document. The engine stores every `agent.done` (`agent_results`, one row per job) and tracks the run's pending jobs by `"<agent>:<job_id>"`, so `AGENT_DONE_INTAKE` fires when the last one reports, and `intake.publish` merges all of them: one candidate per field (highest confidence, ties broken by document, page, line), one entry per change type and per question.
- **S1's side of a decision.** One PO action is one Mongo transaction: the case document, its field values, review items, documents, the frozen submission, the activity rows and the outbox events commit together or not at all. Caseflow decides a gate once, so S1 keeps a ledger (`po_decisions`, deterministic `_id` per gate): the first decision on a gate emits `gate.decided`; a later change to the same data updates it (the original stays in `history`) and is logged in the activity feed, without a second event. A repeat of the same request changes nothing.
- **The PO's gates.** `po_confirm_category` and `po_answer_gaps` move the case; `po_confirm_value` (one per proposed value) and `po_reuse` (one for the precedent) are opened so that S1's `gate.decided` for them meets a gate H1 opened (Guard 7) instead of being refused as unknown. A gate id is `deterministic_id("gate", run_id, <gate type>, <field id | change type | question id | precedent case id>)`.
- **What the PO sees.** Only `visibility: po | all` events, none with a score or band; `case.assigned` carries the analyst's name and nothing else.
- **A document that cannot be used** (a scan, an unsupported type, Guard 1's quarantine, a deadline overrun) is an `ingest.failed` with a code; H1 marks the document and tells the PO to upload another version, and still reads the case's other documents. A store or broker outage is retried, then dead-lettered, never answered.
