# Sequence: assessment, scoring, and the analyst's override

The demo's central moment: the score moves from 9.8 (Band 2) to 10.4 (Band 3) because the analyst overrides one control rating with a reason.

```mermaid
sequenceDiagram
    actor An as Analyst
    participant W1 as fcrm-web
    participant H1 as Caseflow (H1)
    participant H2 as agent-worker (H2)
    participant Br as Bedrock
    participant S2 as scoring (S2)
    participant S4 as audit (S4)
    participant S1 as case (S1)

    Note over H1: TRIAGE_DISPATCH (R5: fewest-open-cases) -> SCREENING
    H1->>H2: agent.run screening
    H2-->>H1: agent.done (fast_lane_proposed: false)
    Note over H1: AGENT_DONE_SCREENING -> ASSESS

    H1->>H2: agent.run research (parallel)
    H1->>H2: agent.run assessment (parallel)
    H2->>Br: strong tier, 2 calls
    Br-->>H2: ClauseProposal[], PrecedentMatch[] / FactorProposal[], ControlProposal[]
    H2-->>H1: agent.done (both)
    Note over H1: AGENT_DONE_ASSESS_BOTH -> CRITIC
    H1->>S4: append proposals (Guard 5)
    H1->>S1: push items
    H1->>S2: POST /score (ScoreRequest, no planned controls)
    S2-->>H1: 9.8, Band 2, boundary_flag=true, distance=0.3
    H1->>S4: score.computed, boundary.flagged

    H1->>H2: agent.run critic (judge tier, Nova Pro)
    H2-->>H1: CriticFinding[] (precedent_deviation: CTL-T01 7.6 vs 4.9 avg)
    Note over H1: AGENT_DONE_CRITIC -> COMPLETENESS

    H1->>H2: agent.run narrative (completeness mode)
    H2-->>H1: CompletenessReport, Question[]
    Note over H1: GATE_DECIDED_COMPLETENESS_ASK -> CLARIFICATION
    An->>W1: Review completeness, ask one round
    Note over S1: PO answers -> new submission version
    Note over H1: CASE_ANSWERED, AGENT_DONE_RERUN_BOTH -> REVIEW

    An->>W1: Open Risk & Controls tab
    An->>W1: Override CTL-T01: 7.6 -> 4.5, reason required
    W1->>S1: POST review item decision (OverrideReviewItemRequest.reason)
    S1->>H1: gate.decided review_item
    Note over H1: rule 6 (reason present), rebuild ScoreRequest from confirmed values
    H1->>S2: POST /score (CTL-T01 now 4.5)
    S2-->>H1: 10.4, Band 3, boundary_flag=true (edge just crossed), distance=0.3
    H1->>S4: human.override, score.recomputed
    Note over H1: band_changed=true -> open ack gate (R3)
    An->>W1: Acknowledge band change
    Note over H1: GATE_DECIDED_REVIEW_ITEM (ack) -> REVIEW, route now COMMITTEE
```
