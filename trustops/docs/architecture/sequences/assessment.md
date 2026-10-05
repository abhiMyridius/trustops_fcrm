# Sequence: assessment, review and the pack

From a submitted case in screening to a pack released to the committee, as the harness steps in `fcrm_caseflow.cores.assessment*` implement it (ADR-28). Each step names the transition in `fcrm_caseflow.transitions` that starts it. The high-level picture of the override is in [assessment-and-override.md](assessment-and-override.md); this page is the one to read for what is written where, and what makes a step safe to run twice.

```mermaid
sequenceDiagram
    actor An as Analyst
    participant S1 as case (S1)
    participant H1 as Caseflow (H1)
    participant H2 as agent-worker (H2)
    participant S2 as scoring (S2)
    participant S3 as policy (S3)
    participant S4 as audit (S4)
    participant S5 as decision (S5)

    Note over H1: TRIAGE_DISPATCH (TRIAGE -> SCREENING); agent.run screening
    H2-->>H1: agent.done screening
    Note over H1: AGENT_DONE_SCREENING (SCREENING -> ASSESS); step screening.publish
    H1->>S1: PUT screening
    H1->>S4: screening.proposed

    Note over H1: ASSESS_DISPATCH; agent.run research and assessment (assessment alone on the fast lane); step assess.open
    H1->>S1: PATCH state assess
    H1->>S4: workflow.stage
    H2-->>H1: agent.done (each is kept; the join waits for both)
    Note over H1: AGENT_DONE_ASSESS_BOTH (ASSESS -> CRITIC); step assess.publish
    H1->>S3: GET /controls (catalogue weights, cached)
    H1->>S4: proposal.research, proposal.assessment (one ledger entry per item)
    H1->>S1: PUT items (clauses, factors, controls with their weights, gaps)
    H1->>H1: open one review_item gate per gated item
    H1->>S1: GET score-request (the one builder; planned controls never in it)
    H1->>S2: POST /score
    S2-->>H1: 9.8, Band 2, boundary flag, distance 0.3
    H1->>S1: PUT score (request and response kept as the score of record)
    H1->>S4: score.computed, boundary.flagged
    Note over H1: first score flagged -> open ack(boundary)

    H1->>H2: agent.run critic
    H2-->>H1: agent.done critic
    Note over H1: AGENT_DONE_CRITIC (CRITIC -> COMPLETENESS); step critic.publish
    H1->>S1: PUT critic (findings; each is an item with a gate)
    H1->>S4: critic.findings

    H1->>H2: agent.run narrative (completeness mode)
    H2-->>H1: agent.done narrative
    Note over H1: AGENT_DONE_NARRATIVE_COMPLETENESS; step completeness.publish; completeness gate opens
    H1->>S1: PUT completeness (report and the questions to ask)
    H1->>S4: completeness.report

    An->>S1: Ask the owner one round of questions
    S1->>H1: gate.decided completeness (ask)
    Note over H1: GATE_DECIDED_COMPLETENESS_ASK (COMPLETENESS -> CLARIFICATION); step clarification.record
    H1->>S4: clarification.sent
    S1->>H1: case.answered (the owner's answers and any added documents)
    Note over H1: CASE_ANSWERED; step rerun.record (waits for added documents to be extracted)
    H1->>S4: owner.answered, agent.rerun
    H1->>H2: agent.run research and assessment, only for the changed fields
    H2-->>H1: agent.done (both)
    Note over H1: AGENT_DONE_RERUN_BOTH (CLARIFICATION -> REVIEW); step rerun.publish
    H1->>S1: PUT items (a changed proposal is a new item; the old one is superseded)
    H1->>S2: POST /score
    H1->>S4: proposal.research, proposal.assessment, score.recomputed

    An->>S1: Override CTL-T01 7.6 to 4.5, with a reason
    S1->>S1: store the decision on the item
    S1->>H1: gate.decided review_item
    Note over H1: GATE_DECIDED_REVIEW_ITEM (REVIEW -> REVIEW); step review.item
    H1->>S4: gate.decided, human.override (reason recorded)
    H1->>S1: GET score-request (CTL-T01 now 4.5)
    H1->>S2: POST /score
    S2-->>H1: 10.4, Band 3, route committee
    H1->>S1: PUT score (previous 9.8 Band 2; band_changed)
    H1->>S4: score.recomputed
    H1->>H1: open ack(band_change)
    An->>S1: Acknowledge the band change
    S1->>H1: gate.decided ack
    Note over H1: step review.ack

    Note over H1: ALL_ITEMS_DECIDED (REVIEW -> RECOMMEND), when every item and every ack is done
    H1->>H2: agent.run narrative (recommendation mode)
    H2-->>H1: agent.done narrative
    Note over H1: step narrative.publish
    H1->>S4: proposal.narrative, guard.output
    H1->>S1: PUT recommendation-draft (the PO summary withheld if it names a restricted term)

    An->>S1: Lock the recommendation
    S1->>H1: case.locked
    Note over H1: CASE_LOCKED (RECOMMEND -> RECOMMEND); pack_release gate opens; step lock.build_pack
    H1->>S4: recommendation.locked (this record is the pack's audit head)
    H1->>S2: POST /score (no model involved; the numbers are S2's)
    H1->>S5: POST /packs (the snapshot, with the four release checks computed here)
    H1->>S4: pack.assembled
    H1->>S1: PUT handoff (pack id and hash, the four checks, the audit head, the release gate id)
    An->>S5: Release the pack to the committee
    S5->>H1: pack.released
    Note over H1: PACK_RELEASED (RECOMMEND -> DECIDE); step pack.approved (R1/R2 pick the route)
    H1->>S4: pack.approved
```

The analyst's other review actions are steps of their own, none of which moves the state: `gap.record` (`gap.recorded`), `control.planned` (`control.planned`: a planned control is shown and recorded, never scored), `retag.record` (`fieldset.resolved`) and `rerun.requested` (`agent.rerun`, a re-run of research and assessment the analyst asked for). `case.unlocked` (`RECOMMEND -> REVIEW`) runs `unlock.supersede`: the pack is superseded in S5, its release gate is closed, and every gated item gets a new review gate, so the analyst decides them again.

## What the steps guarantee

- **Idempotent.** A step may run again after a failure. S4 proposals and records carry an `Idempotency-Key` derived from the step id; every S1 `PUT` carries one too, kept 24 hours with a fingerprint of the body (the same key with a different body is a 409); item ids are `deterministic_id("itm", case_id, kind, *subject[, "rN"])` and gate ids `deterministic_id("gate", run_id, gate_type, subject_key)`. The engine records a step as done in the same transaction that applies its result, so a second run of a finished step changes nothing.
- **One writer of the number.** `GET score-request` on S1 is the only place a score request is built (`fcrm_case.domain.assessment.build_score_view`): one domain per current factor item and, in it, every current, not-removed, not-planned control at its decided value (an override or an adjustment) or, while undecided, at the value the agent proposed. H1 scores from it, H1 re-scores from it and S2's `POST /simulate` previews from it, so the three cannot disagree. H1 also checks rule 7 itself (no planned control in the body) before it calls S2. No agent proposes a score, a band or a route; S2 answers them.
- **Acknowledgements are decided by S2's answer, not the payload's.** `review.item` re-scores when a decision changed what S2 reads, then compares with the previous score of record: a different band opens `ack(band_change)`, and a boundary flag that newly rises opens `ack(boundary)`. The gate key is `<kind>:<step id>`, so a re-run of the step opens nothing twice. The pack cannot be released while one is open (the release check `boundary_and_band_acknowledged`).
- **A re-run replaces an item only when it must.** A proposal replaces an item only if the item's input fields overlap the fields that changed and the new content differs; the replacement is a new item (revision suffix, `supersedes=<old id>`) with a new gate, and the old gate is closed as `superseded` (`StepResult.gates_to_close`). A decided item is never edited, an unchanged item is kept as it is, and a step that runs twice closes the same gates again harmlessly.
- **The pack is deterministic.** `lock.build_pack` takes the `recommendation.locked` record as the audit head (an idempotent append returns the same record on a retry) and judges "stale" as of that record's date, so a retry builds the same snapshot and S5 returns the same pack instead of minting version 2. The release checks (`numbers_locked`, `conditions_drafted`, `all_review_items_decided`, `boundary_and_band_acknowledged`) are computed in code from S1's items and H1's own gates, never by a model.
- **Who sees what.** Everything on this page is analyst-only. The PO is told the stage name and nothing else: no score, band, rating, critic finding or harness event reaches a PO stream, and `harness.step` messages, which carry agent output, never reach one. The narrative's PO summary is scanned for restricted terms; on a hit the text is withheld and the hit is counted, rather than the step failing.
- **Fails closed.** A step that cannot finish (S2 down, S5 refusing, a malformed proposal) is retried and then dead-lettered, and a dead-lettered step freezes its run; nothing is skipped and no step is marked done that did not finish.

## S2's preview

`POST /scoring/simulate {case_id, control_id, value}` is the analyst's "what if" before an override. S2 reads S1's score request with its service token, scores it as it stands and with the one control at `value`, and answers both: the domain's CE, retention and residual, the composite, the band, the route and the boundary flag, each as (before, after), with the line the analyst reads. It stores nothing and decides nothing. A control that is not scored in the case (unknown, or held only as planned) is a 404, a value outside 0 to 10 is a 422, and with no `CASE_SERVICE_URL` the route answers 503 rather than guess. OPA allows it to the analyst only and denies the service account, which inherits every analyst grant through `role_hierarchy`.
