# Incident: an agent misbehaved

An agent proposed something wrong, unsupported, restricted or expensive. This page says how to contain it, how to reproduce what happened from the audit trail and the proposals ledger, and how to change the agent safely.

## Why the blast radius is small

- No agent decides. Agents propose; a person decides item by item (Guard 7), code scores, and nothing is approved or rejected automatically (`docs/governance/gates-register.md`).
- No agent writes a score, band or route. S2's engine is the only writer, called by H1 (ADR-07). The assessment agent has no scoring tool.
- Policy is quoted verbatim with a source reference. Guard 4 rejects a quote that is not a substring of the stored clause.
- The PO never receives a score, band, rating or harness event, by three independent layers (ADR-32).

## The guards, and where each leaves a trace

Seven guards run in a fixed order (`ai/guards/guards.yaml`). Every one that fires writes a record.

| Guard | Stops | Audit record |
|---|---|---|
| 1 input scan | injection patterns, personal data (masked before any agent reads it) | `guard.input` |
| 2 permit | a tool the agent is not allowed (OPA, default deny, fail closed) | `guard.permit` |
| 3 budget | a call over its tier budget (cheap 15k, mid 15k, strong 24k, judge 28k tokens per attempt) or its wall clock | `guard.budget`, `agent.failed` |
| 4 output shape | a bad schema, an unresolved source, a paraphrased quote, restricted terms in PO text, an ungrounded number | `guard.output` per attempt, `agent.retry` |
| 5 rules | the eight rules as pure functions | the refusal reason of the attempt (`guard.output`, or `agent.failed` on the last attempt) |
| 6 critic | a rating the critic disputes (scheduled by H1 after scoring) | `critic.findings` |
| 7 human gate | everything else: a person decides each item | `gate.decided`, `human.override` |

A refused attempt is retried once on the fallback model with the refusal reasons shown. A second failure becomes `agent.failed` and a `handoff` gate ([bedrock-outage-and-fallbacks.md](bedrock-outage-and-fallbacks.md)).

## Triage

| What you see | What it means | Do |
|---|---|---|
| `guard.output` rejected, then the retry passed | the guard worked | nothing; if it repeats for one agent, the prompt needs a version (below) |
| `agent.failed` and a `handoff` gate | the agent could not produce a valid answer twice | read the reason on the Summary tab; the run is stuck until a person intervenes (known gap) |
| A wrong proposal that reached the analyst | the review gate is the control | the analyst overrides with a reason (`human.override` and `score.recomputed` are written); record the miss as a defect |
| A wrong number or clause in a released pack | it passed review | the committee question path (R6): answer with `numbers_changed`, unlock with a reason, supersede, new pack, every vote re-requested |
| A score, band, rating or restricted term in anything a PO can read | **P1**: a product rule is broken | stop; capture the response; write a failing test first (`tests/integration/blackbox/test_bb_02_po_no_restricted_fields.py` is the model); add a defect row; fix the fence, not the symptom |
| A document that tried to instruct the agent | Guard 1 | check `guard.input` for the case; the document is blocked or annotated, not read as an instruction |
| Cost above plan | a tier budget, or the case's tokens over the 120k target | read [../reports/token-analysis.md](../reports/token-analysis.md); H1's ledger refuses a dispatch past the enforced 160,000 and opens a handoff, and the monitor reports against the 120,000 target (ADR-50) |

## Reproduce a decision from the trail

Everything below is read-only. You need an analyst token (`$A`, see [verify-the-audit-chain.md](verify-the-audit-chain.md)).

```bash
G=http://localhost:8080; CASE=FCRM-2026-0151
curl -s -H "Authorization: Bearer $A" "$G/api/fcrm/audit/records/$CASE?limit=200"          # the chain: type, actor, summary, data, hash
curl -s -H "Authorization: Bearer $A" "$G/api/fcrm/audit/proposals?case_id=$CASE&agent=assessment"   # the ledger entries for one agent
curl -s -H "Authorization: Bearer $A" "$G/api/fcrm/audit/versions/$CASE"                    # the versions the case pinned
curl -s -H "Authorization: Bearer $A" "$G/api/fcrm/audit/overrides"                         # human overrides: proposed, final, who, reason
```

1. **Find the proposal.** In the chain, `proposal.<kind>` is the record H1 wrote when it consumed the agent's output; the `guard.output` records before it say which attempt passed and why earlier ones did not. `human.override` records carry the person and the reason.
2. **Open the ledger entry.** Each entry holds the agent, the kind, the validated `payload`, the model that answered, the tier, `prompt_v`, token counts and, when it replaced another, `supersedes`. The ledger never updates an entry; a re-run is a new one.
3. **Pin the prompt.** `prompt_v` names the prompt version. The text is `ai/prompts/<agent>/<id>.v<N>.md` at that version in git; `ai/registry.lock/prompts.lock` holds the sha256 of every version file, so you can prove which text was in force.
4. **Pin the inputs.** `audit/versions/{case}` gives the scoring model, corpus, controls, field set, jurisdictions, routing rules and prompt-set versions the case pinned. Config published later never reaches an open case.
5. **Re-compute the number.** A score is deterministic. S1 keeps the score request and S2's response as the score of record, and the engine is a pure function of the request and the pinned model. The golden numbers and all 80 seed cases are reproduced by `uv run --project src/backend --all-packages pytest tests/unit/services/scoring -k golden`.
6. **Re-run the agent, if you must.** `uv run --project src/backend --all-packages python ops/scripts/demo_case_tokens.py --only research` re-runs agents on the demo case with the real pack builders, rendered prompts and shipped budgets, live on Bedrock (about USD 0.50 for all agents). It reads seed-backed tools, not the live case rows, and a model may answer differently the second time: it shows how the agent behaves on the demo case today, not what it said then. The ledger is the record of what it said.

## Change the agent safely

1. Write the failing case into the dataset or a test first (`src/backend/packages/evals/fcrm_evals/datasets/`).
2. Add a new prompt version file (`ai/prompts/<agent>/<id>.v<N+1>.md`); never edit a committed one. Update `ai/prompts/MANIFEST.yaml` and the changelog, then `python3 ai/_tools/compute_locks.py` to regenerate `ai/registry.lock/` (`make locks` is not wired).
3. Run the agent's evals live and gate them: `make evals AGENT=<agent>` then `make evals-gate AGENT=<agent>`. A prompt change carries its eval evidence in the commit's `Gate:` trailer.
4. To roll back, point `current` at the previous version, regenerate the locks and add a decision-log row. Cases already open keep the prompt set they pinned.
5. Never put a model id anywhere but `ai/models/tiers.yaml` (a test enforces it).

## Checked and not checked

- Checked by reading: the guard table (`ai/guards/guards.yaml`), the ledger entry shape (`ProposalAppend` in `docs/architecture/openapi/api.json`), the routes and who may call them (`ops/opa/data/permissions.json`: the analyst; the committee may read `proposals`, not `overrides`), the prompt files and the manifest. Ran the golden scoring tests (green) and the offline chain snippet.
- Not run: the four `curl` reads against the live stack, and a real replay of an old agent output.
