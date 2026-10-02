# Open questions

Carried forward as the build progresses. An entry here means an operator decision is needed, or a later lane's design will need to reconcile something; it is not a defect (see `docs/governance/defect-trail.md` for those).

| # | Question | Raised by | Status |
|---|---|---|---|
| Q-01 | Does *any* reject outcome route to committee regardless of band, or only Band 3–4? The brief implies the former; R1/R2 as specified route purely on band. | Requirements research, confirmed against `fcrm_caseflow.transitions`'s `CASE_CLOSE_REQUESTED` guard | Open — current implementation is band-only (R1/R2 as literally specified); flag for operator confirmation before freeze |
| Q-02 | Is the ~2-business-day SLA measured end-to-end (intake to decision) or intake-to-decision-ready (excluding committee scheduling latency)? | Spec research | Open — `fcrm_common.time` (pending) will carry whichever definition is confirmed |
| Q-03 | Should the 0.6 retention factor and 0.4 floor be treated as the risk function's own calibration (tunable, as the scoring model version already allows) or as something to source from a published methodology? | Scoring research | Open — currently documented as the team's own calibration (`docs/requirements/rules.md` "Grounding") |
| Q-04 | Exact Bedrock model IDs beyond the four tiers' primaries (e.g. whether Nova Pro is an acceptable fallback long-term, or only a stopgap) — pricing and availability should be reconfirmed against the account close to the deadline. | `ai/models/tiers.yaml` (pending lane) | Open, low risk — the account's model access was verified once (3 Oct); reconfirm before the freeze milestone |
| Q-05 | Whether `policy_gap` suggestions need a distinct reviewer role, or the analyst reviewing them under Assurance (ADR-18) is sufficient for the brief's governance expectations. | Decision log review | Resolved by ADR-18 for this build; flagged here in case the operator disagrees |
| Q-06 | Whether a live AWS deployment will exist by the freeze milestone, and if not, whether the clean-clone local proof (`make up && make seed-verify && make e2e`) is accepted as equivalent evidence of production readiness. | BUILD-PLAN §9/§11 | Open — the operator said they will handle the live AWS deployment themselves; local proof is the one this build guarantees |
