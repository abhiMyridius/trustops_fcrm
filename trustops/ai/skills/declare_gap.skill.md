---
id: declare_gap
version: 2
applies_to: [intake, research, policy_gap]
changelog:
  - {version: 1, date: 2026-10-03, reason: initial, eval_run: null}
  - {version: 2, date: 2026-10-06, reason: "screening and the examiner no longer include it (their output has no gap item); the absence is reported where the contract has a place for it (PolicyGap, GapQuestion, searched) instead of a list of fields from other agents", eval_run: null}
---
## Declaring a gap

A gap is a correct result. "The sources shown do not cover this" is useful to the analyst; a plausible sentence with nothing behind it is a defect that someone has to find later.

1. When nothing in your context or your tool results supports a claim, do not make the claim. Report the absence where your output has a place for it: a PolicyGap (research), a GapQuestion for a required field (intake), or the `searched` block of a GapSuggestion (policy gap).
2. Say what you looked at, so the next person can repeat the search, in the `searched` block: the query you ran, word for word; the indexes (with the corpus version in force); and how many hits it returned.
3. Name the topic in a few plain words ("cash-pickup agent networks"), not as a question.
4. Do not fill a gap with general knowledge, a regulation you remember, or a clause that is only close. Close is not a match: if the clause text does not address the topic, it is a gap.
5. Do not soften a gap into a weak claim ("the corpus may partly cover..."). Either quote the text that covers it or declare the gap.
6. Use your tool calls before declaring a gap when your instructions allow one more search; after that, stop and declare.

Example: the case describes cash pickup at partner agents. Search "cash pickup agent network AML" with filters `risk_areas: [channel]`, `corpus_version: v14` returns only clauses on payout agents in general, none on cash pickup.
- Correct gap: description "No clause for cash-pickup agent networks in corpus v14"; searched: the query, the corpus index, hits 0.
- Wrong: proposing TP71-2.3 with the reason "cash pickup is a kind of payout agent". That is a judgement the analyst makes, not a citation you can make.
