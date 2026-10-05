# The rules, as specified

This is the *specification* — what each rule requires and where it came from. For which function or test enforces each one today, see `docs/governance/rules.md`.

## The eight rules (the BA's original eight, D-series decisions)

1. **No agent writes a score.** Scores come only from the deterministic engine (`fcrm_scoring`); an agent may propose the inputs a score is computed from (likelihood, impact, control ratings), never the score itself.
2. **No bulk accept.** Every AI proposal is confirmed, overridden or removed one at a time. There is no "accept all" action anywhere a human reviews AI output.
3. **The PO view has no restricted fields.** The product owner never receives a score, band, control rating, or any agent proposal before it is confirmed — not hidden in the UI, but absent from the data they are ever sent.
4. **The analyst never edits the submission.** The analyst reviews and decides on proposals about the case; they cannot change what the business actually submitted. A correction to the submission comes from the business, through a clarification round.
5. **A closed case is untouched.** Once a case closes, nothing about it changes — not a field, not a score, not a condition's text (though its status may update, e.g. a condition being marked complete).
6. **An override has a reason.** Whenever a human's decision differs from the AI's proposal, a written reason is required and stored.
7. **A planned control is not scored.** A control that is scheduled but not yet evidenced is recorded for the record, and excluded entirely from the control-effectiveness calculation.
8. **Policy is quoted, not generated.** Wherever the system cites a policy clause, the text is the actual clause, verified to appear verbatim in the source corpus — never a paraphrase or an invention.

## Routing rules v4, R1–R6 (D-19)

- **R1** — Band 1 or 2: the analyst closes the case under delegated authority.
- **R2** — Band 3 or 4: the risk committee decides. Quorum is three. Votes stay hidden until all three are cast. A rationale is required on every vote.
- **R3** — A boundary flag (composite within 0.5 of the next band's edge) or any band change must be acknowledged by the analyst before the case can proceed to release.
- **R4** — The screening agent may propose the fast lane for a vendor renewal, or a feature change with no new geography, segment or product and an annual volume under $5M. The analyst still confirms it.
- **R5** — A new case is auto-assigned to the analyst with the fewest open cases.
- **R6** — A committee question is answered as a pack addendum. If the answer changes a number, the recommendation is unlocked with a reason, a new pack version is built, and every cast vote is re-requested.

## ADR-08 (added during this build's planning, not in the original brief)

The brief does not specify how three votes resolve to one of four outcomes, nor what happens on a split. This build's answer: the outcome is whichever option two of the three votes agree on. Any other result — most commonly a three-way split — is `no_majority`: a new round opens on the same pack. No outcome, including defer, is ever produced automatically; a split is reported, not resolved by default, because the brief's core constraint is that nothing is decided without a human majority actually agreeing.

## Grounding

Risk decomposition follows FFIEC's four risk-domain structure (customer, product, geography, channel/third-party); sanctions and watchlist screening references OFAC and FATF; internal policy clauses are the team's own synthetic corpus (`docs/requirements/research/06-walkthrough-seed-extraction.md` §2.4 lists all nine corpus sources, five public and four synthetic-internal). No frameworks are invented; where a figure (e.g. the 0.6 retention factor, the 0.4 floor) is the team's own calibration rather than a published standard, it is recorded as such in the scoring model's `owner`/`approved` fields, versioned, and open to the risk function's own tuning per the brief's "scoring and workflow rules must be tunable" requirement.
