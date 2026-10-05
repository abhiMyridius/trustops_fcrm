---
id: cite_verbatim
version: 1
applies_to: [intake, research, assessment, examiner, watchlist, policy_gap]
changelog:
  - {version: 1, date: 2026-10-03, reason: initial, eval_run: null}
---
## Citing sources verbatim

Every item that states a fact carries a `source_ref` and a `quote`. Code checks both before any person sees your output, and an item that fails is dropped.

1. Copy the quote character for character from the source text shown to you: same words, same order, same numbers, same punctuation. Do not fix typos, expand abbreviations, change tense or swap in synonyms.
2. Quote the shortest span that proves the claim, at least 12 characters. You may cut the start or the end of a sentence and mark the cut with "…". Never put "…" in the middle to join two pieces.
3. One quote comes from one place. If a claim needs two passages, make two items, or give the item the stronger passage and leave the other out.
4. The `source_ref` must point to where the quote is:
   - document: `{kind: doc, doc_id, page, line_start, line_end}` using the `p.N l.M` anchors exactly as shown. If the sentence wraps over two lines, `line_end` is the second line.
   - clause: `{kind: clause, clause_id, corpus_v}`, and the quote must come from that clause's text.
   - audit record: `{kind: record, case_id, seq}`, and the quote must come from that record's text.
   - control: `{kind: control, control_id, controls_v}`; owner answer: `{kind: owner_answer, thread_id, question_id}`; list line: `{kind: list, list_id, version}`.
5. Never cite a source that is not in your context or in a tool result from this run. Never invent an id, a page or a line number.
6. `confidence` is your own estimate between 0 and 1 that the item is right. Use it honestly: an item below 0.5 is still shown, marked low confidence.
7. If you cannot find a span that supports the claim, do not make the claim. Report it as not found or as a gap.

Example: the context shows `p.6 l.14: Expected volume: $48M year 1 (base case)`.
- Correct: `quote: "$48M year 1"`, `source_ref: {kind: doc, doc_id: "doc_bc_mx", page: 6, line_start: 14, line_end: 14}`.
- Wrong: `quote: "$48 million in the first year"` (paraphrase), or the same quote with `page: 5` (wrong anchor).

Example: clause TP71-2.3 reads "...the bank shall obtain an independent assessment of the agent network's AML controls covering the specific corridor before go-live."
- Correct: `quote: "…the bank shall obtain an independent assessment of the agent network's AML controls covering the specific corridor…"`.
- Wrong: `quote: "the bank must independently assess the corridor"` (rewritten policy).
