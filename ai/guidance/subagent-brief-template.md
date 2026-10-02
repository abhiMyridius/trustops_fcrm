# Subagent brief template

Every build subagent gets a brief in this shape. A brief that names the files to read and the gate to pass saves the subagent from exploring (fewer tokens) and from stopping to ask a question it cannot get answered (design C §5.3, §5.9). Fill every section. Write "none" rather than leaving one out.

```markdown
# Task: <one line, imperative>

## Goal
<What exists when this is done, in two to four sentences. Name the component id (S1–S5, H1–H4, P1–P7, X1–X7) and the rubric line it serves.>

## Decisions not to reopen
<The decisions in force that bound this task, by id, one line each. Do not reopen these; if one blocks you, say so in Decided.>
- ADR-01 one monorepo · ADR-04 model ids only in ai/models/tiers.yaml · ADR-07 no agent scores · ...

## Read first (in this order)
1. CLAUDE.md, and the CLAUDE.md of every package you touch
2. <design section, e.g. docs/architecture/design/B-ai-layer.md §2.3>
3. <contracts you must import, e.g. src/packages/contracts/fcrm_contracts/proposals/research.py>
4. <seed values the work must reproduce, e.g. docs/requirements/research/06-walkthrough-seed-extraction.md §3.5>

## Outputs
<Every file you create or change, with one line of purpose each. Paths are absolute from the repo root.>
- src/... — ...
- tests/unit/... — ...

## Forbidden changes
- Anything outside the paths listed in Outputs.
- `src/packages/seed/fcrm_seed/seed.json` (generated), `src/web/src/api/generated/**` (generated), any `.env*`, existing rows in `docs/governance/*.md`.
- Contract shapes in `fcrm_contracts`, unless this task is the contracts task. If a contract is wrong, report it in Failed or Decided; do not define a local copy.
- Model ids outside `ai/models/tiers.yaml`; committed prompt files (add a new version instead).

## When you are unsure
Decide, mark the decision "(proposed)" in Decided with the reason and the decision ids it touches, and carry on. Do not stop to ask: nobody will answer mid-task.

## Gate to pass
- `make check` green (G-C1) before every commit; the Stop hook runs it for you.
- `make gate` green before you report done, or the subset named here: <e.g. uv run --package fcrm-scoring pytest tests/unit/services/scoring>
- <Task-specific proof, e.g. "the 9.8 -> 10.4 golden tests pass", "opa test ops/opa green">

## Commit plan
Small commits, each one green, each one in the convention (`ai/guidance/commit-convention.md`):
1. <type(scope): summary [refs]> — <what it contains>
2. ...
(Or "do not commit; the lead merges" when the lead owns the history for this lane.)

## Report (your final message must end with these three sections)

## Changed
- <path> — <what and why>

## Failed
- <what failed or was left undone, the exact command and the first lines of its output>   (or "nothing")

## Decided
- <each judgement call, marked (proposed), with the D-xx / ADR-nn it touches>   (or "nothing")
```

The SubagentStop hook (`ai/guidance/hooks/subagent_report.py`) blocks a final message that lacks `Changed`, `Failed` or `Decided`. The lead copies Failed items into `docs/governance/failure-log.md` or `defect-trail.md`, and proposes ADR rows for the Decided items.
