# Sequence: recommendation, pack release, and the committee vote

Ends with the demo's real outcome: two votes for approve-with-conditions, one defer, tallied to `approve_with_conditions` (never resolved any other way — ADR-08).

```mermaid
sequenceDiagram
    actor An as Analyst
    actor Cm as Committee (3 members)
    participant W1 as fcrm-web
    participant H1 as Caseflow (H1)
    participant S1 as case (S1)
    participant S5 as decision (S5)
    participant S4 as audit (S4)

    Note over H1: ALL_ITEMS_DECIDED (all_items_decided & acks_done) -> RECOMMEND
    H1->>H1: agent.run narrative (recommendation mode)
    Note over H1: Guard 4 checks the PO summary has 0 restricted-term hits
    An->>W1: Edit, then lock the recommendation
    W1->>S1: case.locked
    Note over H1: CASE_LOCKED -> RECOMMEND
    H1->>S5: POST /packs (build_pack: S1's locked view + S4's ledger head)
    S5->>S5: content_hash(snapshot) -- the pack is now frozen
    S5->>S5: evaluate_release_checks (4 checks)
    S5-->>H1: pack.assembled
    An->>W1: Release the pack
    Note over H1: PACK_RELEASED, guard: release_checks_passed -> DECIDE
    H1->>S5: route_for_band(3) = COMMITTEE

    Cm->>W1: Ana asks a committee question
    W1->>S5: committee.question_asked
    An->>W1: Rahul answers (numbers unchanged)
    W1->>S5: committee.answer (numbers_changed: false)
    Note over H1: COMMITTEE_ANSWERED_NO_CHANGE -> DECIDE (addendum only)

    Cm->>W1: Joseph Okafor votes: approve_with_conditions
    Cm->>W1: Ana Mendes votes: approve_with_conditions
    Cm->>W1: Lin Chen votes: defer
    Note over S5: votes hidden until all 3 cast (pack_unchanged check first)
    S5->>S5: tally_round(votes, round=1) via fcrm_contracts.voting.tally_votes
    Note over S5: 2 of 3 agree -> outcome = approve_with_conditions (never auto-resolved)
    S5-->>H1: vote.tallied(outcome=approve_with_conditions)
    Note over H1: VOTE_TALLIED -> CLOSED
    H1->>S1: PUT outcome (activate 3 conditions)
    H1->>S4: vote.revealed, outcome.set
    Note over H1: CLOSED_DISPATCH
    H1->>S4: outcome.delivered, case.closed
    H1->>S4: GET /verify -> chain.verified (67 records)
```

## The no-majority path (not in the demo, but a required path)

```mermaid
sequenceDiagram
    participant S5 as decision (S5)
    participant H1 as Caseflow (H1)

    Note over S5: tally_round(["approve", "defer", "reject"], round=1)
    S5-->>H1: no_majority=true
    Note over H1: VOTE_NO_MAJORITY -> DECIDE (same state, new round)
    H1->>S5: POST /packs/{id}/revote
    Note over S5: round=2; no outcome is ever assumed from the split
```
