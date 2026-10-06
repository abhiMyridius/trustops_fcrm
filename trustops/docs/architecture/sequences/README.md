# Sequences: index

One sequence document per stage of the case flow. Each names the real transition in `fcrm_caseflow.transitions` and the harness step that does the work, and what makes a step safe to run twice. The 21 scenes are the walkthrough's (`docs/requirements/research/06-walkthrough-seed-extraction.md`; the scene list and the audit records each scene writes are in its section 3.10). `RUNNING.md`, "Drive the demo by hand", maps each scene to a screen.

| Document | Flow stage | Scenes | What it covers | Decisions |
|---|---|---|---|---|
| [intake.md](intake.md) | Intake: case created, documents stored and read, proposals, gates, submit, triage | 2 to 7 | `case.created`, upload and `ingest.run`, Guard 1, one intake job per document and the join, the PO's four gates, the four submit checks, R5 triage | ADR-28, ADR-33, ADR-34, ADR-38, ADR-39 |
| [assessment.md](assessment.md) | Screening, research and assessment, score, critic, completeness, clarification, review, recommendation, lock, pack | 8 to 16 | every harness step from `TRIAGE_DISPATCH` to `PACK_RELEASED`, what is written where, the re-run rule, the S2 preview (`/simulate`) | ADR-07, ADR-28, ADR-35, ADR-40 |
| [assessment-and-override.md](assessment-and-override.md) | The override and the recompute (the demo's central moment) | 9, 14 | the shorter picture of scoring and the CTL-T01 override with a reason, R3 acknowledgement | ADR-07, ADR-13 |
| [committee-decide.md](committee-decide.md) | Pack release, committee question, hidden votes, tally | 15 to 18 | the pack freeze and content hash, the four release checks, the tally table, the no-majority path, R6 (a number changes), delegated close | ADR-08, ADR-31, ADR-36, ADR-37 |
| [close.md](close.md) | Tally to outcome, conditions, close, chain verification | 18, 19 | the steps `vote.record`, `decide.outcome`, `closed.finalize`, how pack conditions become tracked rows, S1's single transaction, failure semantics | ADR-28, ADR-37, ADR-42 |
| [handoff.md](handoff.md) | A failed agent and the human handoff | none of the 21 (the failure path) | `AGENT_FAILED_AFTER_RETRY` to `handoff` with `suspended_from`, the gate that names the failed job and carries its inputs, the analyst completing the step by hand through S1, `GATE_DECIDED_HANDOFF` and the output handled as the agent's `agent.done` (model `human`), the two `human.handoff` records | ADR-14, ADR-28 |
| [oversight.md](oversight.md) | After a decision: examiner, watchlist re-flags, assurance reads | 20 | the examiner answering from the chain only, a jurisdiction list re-flagging open cases for an analyst, override rates, version pins and agent quality | ADR-07, ADR-19, ADR-28, ADR-33 |

## Scene by scene

| Scene | Name | Sequence document |
|---|---|---|
| 1 | Sign in | none: login and the token are identity (X1), ADR-10 and ADR-30 |
| 2 | New case | [intake.md](intake.md) |
| 3 | Upload | [intake.md](intake.md) |
| 4 | Proposals | [intake.md](intake.md) |
| 5 | Reuse | [intake.md](intake.md) |
| 6 | Gaps | [intake.md](intake.md) |
| 7 | Review and submit | [intake.md](intake.md) |
| 8 | Analyst queue | [assessment.md](assessment.md), [assessment-and-override.md](assessment-and-override.md) |
| 9 | Summary | [assessment.md](assessment.md), [assessment-and-override.md](assessment-and-override.md) |
| 10 | Clarify | [assessment.md](assessment.md) |
| 11 | PO answers | [assessment.md](assessment.md) |
| 12 | Policy | [assessment.md](assessment.md) |
| 13 | Controls | [assessment.md](assessment.md) |
| 14 | Override | [assessment-and-override.md](assessment-and-override.md), [assessment.md](assessment.md) |
| 15 | Recommend | [assessment.md](assessment.md), [committee-decide.md](committee-decide.md) |
| 16 | Handoff (the pack to the committee) | [assessment.md](assessment.md), [committee-decide.md](committee-decide.md) |
| 17 | Committee asks | [committee-decide.md](committee-decide.md) |
| 18 | Vote | [committee-decide.md](committee-decide.md), [close.md](close.md) |
| 19 | Outcome | [close.md](close.md) |
| 20 | Examiner | [oversight.md](oversight.md) |
| 21 | Recap | none: the harness monitor (`/monitor/<case id>`) reads Caseflow's snapshot from S1 (ADR-19, ADR-28) |

## Coverage

Every flow stage of the case has a document: intake, assessment and review, committee decision, close, oversight, and the failed-agent handoff. One thing has none:

- **Sign-in and the monitor** (scenes 1 and 21), which are not Caseflow steps.

The diagrams are Mermaid. A semicolon ends a statement in Mermaid sequence diagrams, so message and note text uses commas; the six documents before `handoff.md` (ten diagrams, and `../c4-container.md`) were parsed with Mermaid 12.1 on 6 October 2026; `handoff.md` follows the same rules but was not parsed.
