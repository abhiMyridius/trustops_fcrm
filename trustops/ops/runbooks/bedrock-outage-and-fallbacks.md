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

A second region is the last entry of every chain. `BEDROCK_REGION_FALLBACK` (default `us-west-2` in `docker-compose.yaml`; empty, or equal to `AWS_DEFAULT_REGION`, turns it off) adds the chain's first model id, read from `tiers.yaml` like the rest, served from that region: attempt 1 ends with the tier's primary, attempt 2 with its first fallback, so a retry still never returns to the model whose output was rejected. It is tried only after every model in the first region failed, and it needs model access for that profile in the second region (not checked against the account). The audit record names the model, not the region. There is no circuit breaker; a throttled model is tried on every call (`fcrm_agents.models.bedrock_chain`, `fcrm_agent_worker.live_runtime.model_factory_for`).

## What the run does when every attempt fails

1. H2 keeps what the job was given (`agent_raw_inputs`, by job id), publishes `agent.failed` for the job (never `agent.done`) naming that copy in `raw_inputs_ref`, and writes `agent.failed` to the audit chain.
2. H1 moves the run to the `handoff` state (from any state except `closed`), remembers the state it left (`suspended_from`) and opens a `handoff` gate for the analyst. The gate's id derives from the run and the job; its subject names the job, why it failed and carries the inputs the agent saw with their SHA-256. The step `handoff.open` writes `human.handoff` (`phase: opened`, with the digest, never the text) and tells S1 the stage. A second failed job in the same run opens its own gate; the run does not freeze and nothing is dead-lettered (`fcrm_caseflow/transitions.py`, D-42).
3. The analyst's Summary tab shows a red banner, "`<step>` step handed to a person", and below it a form per open gate: the failure, what the agent was given, a box for the proposals as a JSON array and a reason of at least 20 characters. The PO sees the stage name only.
4. Nothing else advances until a person acts: no score is computed from a missing proposal, and nothing is approved or rejected.

## Recovery: complete the step by hand

The analyst's route is `POST /api/fcrm/cases/{id}/handoff/{gate_id}/complete` (`GET .../handoff/gates` lists the gates). The output is checked like an agent's (the same proposal contracts and the source-free part of Guard 4; a 422 names each problem), stored with the reason, and only then announced as `gate.decided handoff`. Caseflow checks it again and treats it exactly like the failed job's `agent.done` with model `human`: the run resumes to the state it was suspended from and the join or trigger fires as it would have. While another failed job's gate is open the run stays in `handoff`. The sequence is in [`docs/architecture/sequences/handoff.md`](../../docs/architecture/sequences/handoff.md).

What a manual output is not: its claims are not matched against their sources (the pack is built by H2 from live tools), so `guard_summary.claims_with_source` is 0 and the `human.handoff` record says `claims_matched_to_sources: 0`; every item still reaches a human review gate and the critic.

If the cause was an outage, restore Bedrock first: a run that is handed to a person stays with them, and a re-run (`POST /api/fcrm/cases/{id}/runs`) works from `review` only. For a case suspended before this was built, the gate has no subject and cannot be completed; `make demo-reset` restarts the demo case.

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

- Checked: the chain per tier, budgets and wall clocks against `ai/models/tiers.yaml`; the attempt rules and the handoff transition against the code; that `BEDROCK_REGION_FALLBACK` is read by `model_factory_for` and unit-tested with a fake provider (no call to a second region was made); the handoff path against the code and its tests (the unit tests, and the live-Mongo tests under `tests/integration/live_mongo/test_analyst_handoff_live.py` and `tests/unit/workers/caseflow/test_engine_handoff_live.py`). Not checked: a real Bedrock failure driving it end to end, and the web form against a running stack.
- Not checked: an induced Bedrock failure. No outage drill exists (`make resilience` is not wired). The statement that review, lock, release and vote use no model is read from the sequence documents (`docs/architecture/sequences/assessment.md`, `committee-decide.md`).
