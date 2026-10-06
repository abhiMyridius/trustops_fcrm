---
id: compare_precedent
version: 2
applies_to: [assessment, critic]
changelog:
  - {version: 1, date: 2026-10-03, reason: initial, eval_run: null}
  - {version: 2, date: 2026-10-06, reason: "research no longer includes it (it compares ratings with precedent ratings, which research never rates); the example names no case ids", eval_run: null}
---
## Comparing with precedent cases

Precedents are closed cases that a person already decided. They tell you what was rated before; they do not tell you what to rate now.

1. Compare like with like: the same control id, in cases with overlapping change types. Do not compare a vendor due-diligence rating with a monitoring rating, or a domestic case with a corridor case, without saying so.
2. Use only the precedents and numbers in your context or returned by `precedent_search` in this run. Copy similarity scores and ratings exactly as given; never estimate one.
3. When you compare, report:
   - the precedent case ids you used;
   - the range (lowest to highest) and the mean if it is given to you;
   - the deviation as a signed number: your value or the proposed value minus the mean.
   Do not compute a mean yourself when one is provided; if none is provided, give the range and the ids.
4. Never move a rating to match the precedents. Rate the evidence in front of you, then state how it compares. A large deviation is something to explain or to flag, not something to correct.
5. A precedent with a different outcome, an older control inventory or a different corridor is still evidence; say what differs in one short clause.
6. If fewer than two precedents rated the same control, say "no precedent comparison possible" instead of comparing with one case.

Example: five closest cases rated CTL-T01 at 4.5, 5.2, 4.0, 5.5 and 5.3 (mean 4.9 as given). The proposal is 7.6.
- Correct: "CTL-T01 at 7.6 is 2.7 above the 4.9 mean of the five closest cases (range 4.0-5.5)."
- Wrong: "rated 5.0 to align with precedent" (adjusting to match) or "about 3 points higher than usual" (inexact, no ids).
