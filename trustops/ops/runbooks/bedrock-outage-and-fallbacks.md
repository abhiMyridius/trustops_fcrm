# Bedrock outage and the fallback chain

Every agent call and every embedding goes to Amazon Bedrock. There is no replay or mock mode (ADR-27). When Bedrock fails, the run does not fall back to canned answers: it falls back to another model, then to a person.

## The chain per tier

The ids live only in `ai/models/tiers.yaml`; this table uses the labels the file gives them. Region is `AWS_DEFAULT_REGION` (default `us-east-1`).

| Tier | Used by | Attempt 1 (in order) | Attempt 2 | Wall clock | Tokens per attempt |
|---|---|---|---|---|---|
| cheap | screening, intake, watchlist | Haiku 4.5, then Nova Lite | Nova Lite alone | 60 s | 15,000 |
| mid | narrative, examiner, policy_gap | Sonnet 5.5, then Haiku 4.5 | Haiku 4.5 alone | 120 s | 11,000 |
| strong | research, assessment | Sonnet 5.5, then Opus 5.5, then Nova Pro | Opus 5.5, then Nova Pro | 180 s | 24,000 |
| judge | critic (and the eval judge) | Opus 5.5, then Sonnet 5.5 | Sonnet 5.5 alone | 180 s | 15,000 |
| embeddings | S3 policy search, H4 index builds | Titan Embed Text v2 (1024 dimensions), no fallback | | | |

How the chain is used (ADR-14; `fcrm_agent_worker/live_runtime.py`, `fcrm_agents/models.py`):

1. **Attempt 1** runs the whole `FallbackModel`. A transport, 4xx or 5xx error from one model moves to the next in the list.
2. **Attempt 2** runs only after a Guard 4 rejection, a timeout, an unexpected model output, or a chain in which every model failed. It uses the fallbacks alone, with the previous attempt's refusal reasons appended to the prompt, and keeps the tier's whole output allowance.
3. There is exactly one retry. The critic runs on a different model than the assessor it checks, so a Sonnet outage degrades the critic to Sonnet only on attempt 2.
4. Models that reject a request field are handled per model, not per chain: Opus 5.5 and Sonnet 5.5 get `effort: low`, Haiku and Nova do not (`tiers.yaml`).

Not in the chain: a second region. `BEDROCK_REGION_FALLBACK` (default `us-west-2` in `docker-compose.yaml`) is passed to H2's settings and no code reads it. All fallbacks are other models in the same region. There is no circuit breaker either; a throttled model is tried on every call.

## What the run does when every attempt fails

1. H2 publishes `agent.failed` for the job (never `agent.done`), and writes `agent.failed` to the audit chain.
2. H1 moves the run to the `handoff` state (from any state except `closed`) and opens a `handoff` gate for the analyst with the raw inputs. It records `agent.failed` and `human.handoff`. A second failed job in the same run opens its own gate; the run does not freeze and nothing is dead-lettered (`fcrm_caseflow/transitions.py`, D-42).
3. The analyst's Summary tab shows a red banner: "`<step>` step handed to a person". The PO sees the stage name only.
4. Nothing else advances: no score is computed from a missing proposal, and nothing is approved or rejected.

**Recovery is not built end to end.** The gates register says the analyst completes the step by hand and the machine resumes. Today:

- `GATE_DECIDED_HANDOFF` is in the transition table, but no service route emits a decision for a `handoff` gate (`docs/architecture/openapi/api.json` has none, and `grep -rn GATE_DECIDED_HANDOFF src/backend` finds only `triggers.py`, `resolve.py` and `transitions.py`).
- `POST /api/fcrm/cases/{id}/runs` (re-run research and assessment with a reason) works from the `review` state only, and the web has no button for it. The banner's "Re-run from the harness panel" has no control behind it.

So a run in `handoff` stays there. To get unstuck: restore Bedrock, then for the demo case run `make demo-reset` and start again; for any other case there is no supported resume. This is listed in `docs/governance/known-gaps.md`.

## Diagnose

```bash
make logs svc=harness      # look for "model call failed" and FallbackExceptionGroup; the exception names ThrottlingException, a timeout or a validation error
make logs svc=api          # S3's corpus embedding at start: an embedding failure shows here
make dlq                   # parked messages are a different failure from a failed agent
```

Check what the account can invoke, with your own credentials in the shell (not run by the lane that wrote this page; needs credentials):

```bash
uv run --project src/backend --all-packages python -c "import boto3; print(sorted(p['inferenceProfileId'] for p in boto3.client('bedrock', region_name='us-east-1').list_inference_profiles()['inferenceProfileSummaries']))"
```

Compare the result with the ids in `ai/models/tiers.yaml`. A model id missing from the list is a configuration fault, not an outage. Do not edit an id without re-verifying it and adding a decision-log row.

## While Bedrock is down

- The stack stays up. Login, queues, the case lists, the audit chain, the pack and the votes are plain services and work. A case already in `review` or later can still be reviewed, locked, released and voted: those steps use no model, except the narrative draft after the last review (mid tier).
- New intake, assessment, the completeness report, the examiner and the critic need a model.
- Do not set `FCRM_LLM_MODE` to anything else: `live` is the only supported value (ADR-27).

## Checked and not checked

- Checked: the chain per tier, budgets and wall clocks against `ai/models/tiers.yaml`; the attempt rules and the handoff transition against the code; that nothing reads `BEDROCK_REGION_FALLBACK` (`grep -rn region_fallback src/backend`); that no route decides a handoff gate.
- Not checked: an induced Bedrock failure. No outage drill exists (`make resilience` is not wired). The statement that review, lock, release and vote use no model is read from the sequence documents (`docs/architecture/sequences/assessment.md`, `committee-decide.md`).
