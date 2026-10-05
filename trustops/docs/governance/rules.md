# The eight rules and the six routing rules

The rules as code, not as policy — every row below names the real function or test that enforces it today, or says plainly that it is still pending.

## The eight rules (Guard 5 / rule functions)

| # | Rule | Enforced by | Status |
|---|---|---|---|
| 1 | No agent writes a score | `fcrm_scoring.engine` is the only module that ever computes a composite/band; `fcrm_contracts.proposals.assessment.FactorProposal`/`ControlProposal` have no score field, and the assessment agent is given no scoring tool (ADR-07) | **Enforced structurally** — the schema cannot carry a score |
| 2 | No bulk accept | Every review-item decision in `fcrm_contracts.api.case_analyst.OverrideReviewItemRequest` and the `review_item` gate operates on exactly one item; no endpoint accepts a list of item ids to decide at once | **Enforced structurally** in the contracts; `fcrm_guardrails.rules.r2_no_bulk_accept` (pending that lane) will additionally check any future request shape |
| 3 | The PO view has no restricted fields | `fcrm_contracts.api.case_po.CaseDetailPO` has no score/band/rating field at all | **Enforced structurally**; see `docs/governance/visibility-matrix.md` |
| 4 | The analyst never edits the submission | `fcrm_contracts.api.case_analyst` has no "edit field value" request shape; only `ReviewItem` decisions and `SubmissionVersion` (read-only diff) exist | **Enforced structurally** in the contracts; the HTTP route layer's actual rejection is pending `fcrm_common` |
| 5 | A closed case is untouched | `fcrm_caseflow.transitions`: `CaseState.CLOSED` has exactly one outgoing transition (`CLOSED_DISPATCH`, itself idempotent); every other trigger from `CLOSED` raises `IllegalTransition` | **Enforced and tested** (`test_agent_failure_after_retry_hands_off_from_any_open_state` confirms `CLOSED` is excluded from the "any open state" handoff rule) |
| 6 | An override has a reason | `fcrm_contracts.api.case_analyst.OverrideReviewItemRequest.reason: str` is required, not optional | **Enforced structurally** |
| 7 | A planned control is not scored | `fcrm_scoring.seedmodel.score_request_from_case` filters out `planned=True` controls before building a `ScoreRequest`; `fcrm_contracts.api.scoring.ControlInput` has no `planned` field at all, so a planned control structurally cannot reach the engine | **Enforced and tested** (the demo's after-override planned control, weight_norm 0.0, is excluded by every golden test) |
| 8 | Policy is quoted, not generated | `fcrm_contracts.proposals.provenance.Claim.quote` plus Guard 4's verbatim-quote matching (pending the guardrails lane) | **Partly enforced** — the schema requires a quote on every claim; the matching algorithm that proves the quote is verbatim is being built in a parallel lane |

## Routing rules v4 (R1–R6, ADR-19)

| Rule | What | Enforced by | Status |
|---|---|---|---|
| R1 | Band 1–2: analyst closes under delegated authority | `fcrm_caseflow.routing.route_for_band`; the `CASE_CLOSE_REQUESTED` transition's guard (`band in (1, 2)`) | **Enforced and tested** |
| R2 | Band 3–4: committee decides, quorum 3, hidden votes, rationale required | `fcrm_contracts.api.decision.VoteCast.rationale: str` (required); `fcrm_contracts.voting.tally_votes` requires exactly 3 votes | **Enforced structurally**; the hidden-until-quorum storage guarantee is pending the HTTP/Mongo layer |
| R3 | Boundary flag / band change needs acknowledgement before release | `fcrm_scoring.engine.boundary_flag` (ADR-13's distance rule, verified against all 18 flagged seed cases); the `ack` gate in `fcrm_caseflow.transitions` | **Enforced and tested** |
| R4 | Fast lane: vendor renewal or scope-free feature under $5M, analyst confirms | `fcrm_caseflow.routing.is_fast_lane_candidate`; the `fast_lane_confirm` gate (never auto-applied — `AGENT_DONE_SCREENING`'s transition always opens a gate when proposed, never skips to confirmed) | **Enforced and tested** |
| R5 | Auto-triage to the analyst with the fewest open cases | `fcrm_caseflow.routing.pick_auto_triage_analyst` | **Enforced and tested**, verified against the seed's real queue counts |
| R6 | Committee question → addendum; a number change unlocks, builds pack v2, re-requests every vote | `fcrm_caseflow.transitions`' `COMMITTEE_ANSWERED_CHANGED` → `REVIEW` transition | **Enforced and tested** at the state-machine level; the pack-v2 build and re-request mechanics are pending the decision-service HTTP layer |

## ADR-08 (the ninth rule, added during planning)

Committee tally is never auto-resolved: two of three identical votes decide the outcome; anything else (a three-way split, or any other count) is `no_majority`, which reopens a vote round rather than defaulting to any outcome. `fcrm_contracts.voting.tally_votes`, tested against the demo's real 2–1 vote and against a hypothetical three-way split in both `tests/unit/packages/contracts/test_voting.py` and `tests/unit/services/decision/test_packs_and_outcomes.py`.
