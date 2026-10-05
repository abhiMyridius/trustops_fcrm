# Gates register

Every point where a person decides, with the risk the gate controls and why it sits where it does. Append-only. Product gates are opened by Caseflow (H1) and decided through the owning service; SDLC gates are decided by the operator or by hooks.

## Product gates (Guard 7)

| Gate | Who | Where in the flow | Risk controlled | Why here | Record |
|---|---|---|---|---|---|
| `po_confirm_category` | PO | intake, after the intake agent proposes change types | a wrong change type resolves the wrong field set and risk domains | before the field set is resolved, so nothing downstream is built on an unconfirmed type; removal needs a reason | `gate.decided` |
| `po_confirm_value` | PO | intake, per extracted value | an extraction error becomes a submitted fact | per item, never bulk, so each value is seen with its source chip | `gate.decided` |
| `po_reuse` | PO | intake, precedent reuse | stale values copied from an old case | reuse is explicit and carries provenance | `values.reused` |
| `po_answer_gaps` | PO | intake, always-required gaps | an incomplete case enters assessment | only blocking gaps are asked; completion must reach 80% and every always-required field | `owner.answered` |
| review-and-submit (D-17) | PO | before submission | the owner submits values they did not read | the owner corrects before the four code checks run; the original is kept | `owner.corrected`, `gate.validation` |
| `fast_lane_confirm` (R4) | analyst | after screening | a non-trivial change takes the short path | the agent proposes, the analyst confirms; Band 1 expected | `screening.proposed`, `gate.decided` |
| `completeness` ask / proceed (D-16) | analyst | before reviewing Policy and Controls | reviewing on missing, stale or contradictory inputs, then re-doing it | one batched round before review, so the analyst clock pauses once | `completeness.report`, `clarification.sent` |
| `review_item` (Gate A) | analyst | review, per clause, rating, control and critic finding | accepting an AI proposal without reading it | per item; no bulk accept endpoint exists (rule 2); an override needs a reason (rule 6) and recomputes the score | `gate.decided`, `human.override`, `score.recomputed` |
| `ack` (R3) | analyst | after any band change or boundary flag | a routing change goes unnoticed | release is blocked until the analyst acknowledges the band change and the boundary | `gate.decided` |
| `lock` | analyst | recommendation | numbers change after the narrative is written | lock freezes the numbers the narrative cites; unlock needs a reason and supersedes the pack | `recommendation.locked`, `case.unlocked` |
| `pack_release` | analyst | after the four release checks | an incomplete pack reaches the committee | checks are code; the release is a person | `pack.approved` |
| `vote` (R2) | each committee member | decide, Band 3–4 | anchoring on colleagues' votes | hidden until all three are cast; rationale required; one vote per member per round (DB-unique) | `vote.cast`, `vote.revealed`, `outcome.set` |
| committee question (R6) | committee → analyst | decide | deciding on a misread pack | the answer becomes an addendum; if a number changes, unlock, pack v2 and re-vote | `committee.question`, `committee.answer`, `vote.rerequested` |
| delegated close (R1) | analyst | decide, Band 1–2 | an analyst closes a case above their authority | code evaluates R1 from the latest band; lock and acknowledgements required | `outcome.set` |
| `reflag_ack` | analyst | oversight, new FATF list | a changed jurisdiction list is missed on open cases | the watchlist agent proposes; the analyst acknowledges each | `reflag.acknowledged` |
| `handoff` | analyst | any step that failed after its retry | a stuck case | the raw inputs are shown; the analyst completes the step by hand and the machine resumes | `agent.failed`, `human.handoff` |

## SDLC gates

During the single continuous build run (BUILD-PLAN §5) operator gates do not block: the run records the evidence, marks the gate "passed pending operator review" in `ops/reports/`, and continues; the operator reviews asynchronously and objects through an ADR or defect row. Hook gates (G-C1, G-T1) always block.

| Gate | Who | Stage | Risk controlled | Why here |
|---|---|---|---|---|
| G-R1 spec sign-off | operator | Requirements | a wrong assumption multiplies through every later stage | before design; the brief says refining the spec is assessed |
| G-D1 architecture review | operator | Design | service boundaries, contracts and the deterministic/LLM split are the costliest things to change after code exists | each ADR row accepted before the lane that implements it starts |
| G-D2 contract freeze | operator | Design → Development | the web and every lane build against moving shapes | OpenAPI and event schemas committed at IC-1; a change touches fixtures and exports in the same commit |
| G-C1 commit gate | hooks | Development | mechanical faults (lint, types, unit tests) reach the history | automatic and cheap: Stop hook and pre-commit run `make check` |
| G-C2 review | operator | Development | a change moves a product rule or widens scope | `/code-review` on each merge; a person judges rule drift |
| G-T1 eval gate | hooks | Testing | a prompt or model change silently degrades an agent | a prompt cannot land if its agent's eval score drops |
| G-T2 release gate | operator | Testing → Deployment | residual defects at release | `make gate` and `make e2e` green and no open P1 in the defect trail |
| G-P1 clean-clone proof | operator | Deployment | "works on my machine" | `make up && make seed-verify && make e2e` on a fresh checkout, watched |
| G-O1 nightly review | operator | Operations | automated signals are many; prioritisation is a judgement | ten minutes each morning over `ops/reports/nightly/<date>.md` |
