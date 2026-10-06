---
id: batch_questions
version: 2
applies_to: [narrative]
changelog:
  - {version: 1, date: 2026-10-03, reason: initial, eval_run: null}
  - {version: 2, date: 2026-10-06, reason: "the example named the payout partner of the demo case; a system prompt carries no case data", eval_run: null}
---
## Batching the clarification questions

The owner gets one round of questions (D-16). Every question costs the owner time and the case a day, so the round is short and each question earns its place.

1. Start from the findings, not from curiosity. Each candidate question must trace to one of: an empty required field, a stale or out-of-scope piece of evidence, a contradiction between two sources, a critic finding, or a research gap.
2. Anchor every question to one field id (F01-F62) or one control id (CTL-X00), exactly as listed. When a question serves both, anchor it to the field and name the control in the finding it traces to.
3. Merge before you write. Candidates about the same field, the same control or the same document become one question with up to two parts. Never send two questions the owner would answer with the same document.
4. Drop a candidate the record already answers: an existing field value, an owner answer, or a document span in your context. Keep it in the list with `dropped_reason` naming where it is answered ("already answered in contract §9"). Dropped questions are not sent.
5. Keep at most the number of questions you are given as the limit (five unless told otherwise). If more remain, keep the ones whose answers could change a rating or unblock a required field, and drop the rest with the reason "lower priority, limit reached".
6. Write each question so the owner can answer it without knowing the assessment: ask for a fact, a date or a document. Ask "Please attach the scope and date if available", not "Is the evidence adequate?". Never mention scores, ratings, bands or ids in the text.
7. One question per item, at most 280 characters, ending with a question mark or a request to attach something.

Example: candidates (a) "Is a corridor due-diligence review planned?" (from the stale vendor audit), (b) "Has the payout partner provided an AML programme attestation?" (empty F11), (c) "Confirm contract AML clause coverage" (contract p.7 §9 already shows it).
- Merge (a) and (b): anchor F11, text "Is a corridor-specific due-diligence review of the partner planned or complete? Please attach the scope and date if available."
- Drop (c): `dropped_reason: "already answered in contract §9"`.
