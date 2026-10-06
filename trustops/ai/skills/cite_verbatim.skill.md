---
id: cite_verbatim
version: 2
applies_to: [screening, intake, research, assessment, watchlist, policy_gap]
changelog:
  - {version: 1, date: 2026-10-03, reason: initial, eval_run: null}
  - {version: 2, date: 2026-10-06, reason: "states the handle of every source kind a pack shows (the first live run found models citing a case field as a document and a list by an id the pack never showed); drops the second example to cut tokens", eval_run: null}
---
## Citing sources verbatim

Every item that states a fact carries a `source_ref` and a `quote`. Code checks both before any person sees your output, and an item that fails is dropped.

1. Copy the quote character for character from the source text shown to you: same words, order, numbers and punctuation. Do not fix typos, expand abbreviations or swap in synonyms.
2. Quote the shortest span that proves the claim, at least 12 characters. You may cut the start or the end of a sentence and mark the cut with "…". Never put "…" in the middle to join two pieces. One quote comes from one place; a claim that needs two passages is two items. A value shorter than 12 characters cannot stand as a quote: quote the whole line it sits on instead (a document line without its `p.N l.M:` anchor), or, for a case field, its whole line from the fields section (id and label included).
3. The `source_ref` names where the quote is, by the handle the pack shows, and nothing else resolves:
   - a case field (an `F02 · label · value` line): `{kind: case, case_id}` with the case id from the identity line. The quote is the field's value alone, or the whole line (id and label included) when the value is shorter than 12 characters. A field is never a document.
   - a document line (`p.N l.M: text`): `{kind: doc, doc_id, page, line_start, line_end}` with the anchors as shown.
   - a policy clause: `{kind: clause, clause_id, corpus_v}`; an audit record (`#seq`): `{kind: record, case_id, seq}`.
   - a control: `{kind: control, control_id, controls_v}`; an owner answer: `{kind: owner_answer, thread_id, question_id}`.
   - a list line: `{kind: list, list_id, version}`, copied from the `list_id@version` the pack prints with the line.
4. Cite only what is in your pack or in a tool result of this run. Never invent an id, a page or a line number.
5. `confidence` is your own estimate between 0 and 1. Below 0.5 an item is still shown, marked low confidence.
6. If no span supports a claim, do not make it. Report it as not found or as a gap.

Example: the pack shows `p.6 l.14: Expected volume: $48M year 1 (base case)`.
- Correct: `quote: "$48M year 1"`, `source_ref: {kind: doc, doc_id: "doc_bc_mx", page: 6, line_start: 14, line_end: 14}`.
- Wrong: `quote: "$48 million in the first year"` (paraphrase), or the same quote with `page: 5` (wrong anchor).
