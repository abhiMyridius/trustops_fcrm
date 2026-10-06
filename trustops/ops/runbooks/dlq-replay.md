# Dead-letter queues: inspect, replay, purge

Use this page when a case stops moving and `make dlq` shows a non-empty `*.dlq` queue.

## How a message gets there

Topology is `ops/rabbitmq/definitions.json`; the delivery rules are in `fcrm_common/events.py` (design A section 4.5).

1. A handler raises. If the message's `x-attempt` header is below 3, the consumer republishes it to `<queue>.retry` with `x-attempt + 1` and acks the original. `<queue>.retry` has no consumer: it holds the message for 30 seconds, then dead-letters it straight back onto `<queue>`. So a failing message is tried up to four times, 30 seconds apart.
2. At `x-attempt` 3 the consumer publishes it to the `dlx` exchange instead. It lands in `<queue>.dlq` with no expiry and an `x-last-error` header holding the exception class name.
3. A message whose envelope does not parse (poison) goes to the DLQ at once.
4. If a consumer crashes repeatedly on one message, the quorum `delivery-limit` of 5 (policy `fcrm-quorum-safety`) dead-letters it into the same DLQ. If the republish itself fails, the original is nacked with requeue, so nothing is dropped between queues.

| Work queue | Consumer | A parked message means |
|---|---|---|
| `caseflow.events`, `caseflow.agent-results` | H1 | a trigger or an agent result that H1 could not apply |
| `caseflow.steps` | H1 (`HarnessStepConsumer`) | an HTTP step to S1-S5 that kept failing: the case's next move |
| `agent-worker.run` | H2 | an agent job H2 could not even report on (an agent that fails after its retry is an `agent.failed` event, not a parked message) |
| `ingestion-worker.run`, `context-worker.run` | H3, H4 | a document or index job |
| `case-service.events` | S1 consumer | a mirrored event that did not apply |
| `policy-service.events` | S3 | a policy-side event that did not apply |
| `evals.events` | nobody | nothing: no consumer exists (`docs/governance/known-gaps.md`) |

## What a parked step does to a run

H1 does its HTTP work in steps (ADR-28). The state transition and the `harness.step` message commit in one Mongo transaction, and the run records the step id in `case_runs.pending_steps`. While a step is pending, internal chaining stays blocked. If the step lands in `caseflow.steps.dlq`, the run stays where it was: nothing is skipped and no step is marked done that did not finish. The PO and the analyst see a case that does not advance. The harness monitor (`/monitor/<case id>`) is where the stuck step shows (ADR-28; not checked against the live monitor by the lane that wrote this page).

## Inspect

```bash
make dlq                                          # every *.dlq queue and its depth
make logs svc=harness                             # the warning "handler failed on <queue> (<ExceptionName>); dead-lettering" with the traceback
```

Read a parked message without removing it (it is requeued, `ack_requeue_true`). The credentials are expanded inside the container, so they never touch your shell history:

```bash
docker compose exec -T rabbitmq sh -c 'rabbitmqadmin -u "$RABBITMQ_DEFAULT_USER" -p "$RABBITMQ_DEFAULT_PASS" get messages --queue caseflow.steps.dlq --ack-mode ack_requeue_true --count 5'
```

The table shows the body and the headers (`x-attempt`, `x-last-error`). Bodies are event envelopes; they name the case, run and step. You can also read them in the RabbitMQ management UI after `make up-debug` (http://localhost:15672; the user and password are `RABBITMQ_DEFAULT_USER` and `RABBITMQ_DEFAULT_PASS` in `.env`).

## Fix the cause, then replay

Replaying a message into the thing that just failed only parks it again. Read `x-last-error` and the log first.

| Cause | Fix before replaying |
|---|---|
| S2, S4 or S5 was down (`UpstreamUnavailableError`, a timeout, a 5xx) | start it; `make ps` healthy |
| S5 refused with a 409 (`not_tallied`, `outcome_mismatch`, `committee_pending`...) | a permanent refusal (ADR-42): the case state is wrong, not the transport. Fix the state, or purge and re-drive the case by hand |
| The chain does not verify (`ChainBrokenError` in `closed.finalize`) | repair the chain first ([verify-the-audit-chain.md](verify-the-audit-chain.md)); H1 does not tell the PO until it verifies |
| A code defect | fix, rebuild (`make up`), then replay |

```bash
make dlq-replay q=caseflow.steps       # q is the WORK queue, not the .dlq
```

This sets a RabbitMQ shovel named `dlq-replay-<q>` that moves the current backlog of `<q>.dlq` to `<q>` and deletes itself (`src-delete-after: queue-length`). It refuses a queue that has no `.dlq`. Check progress with `make dlq` (the stats lag a few seconds) and `docker compose exec -T rabbitmq rabbitmqctl -q shovel_status`. If the shovel hangs, remove it: `docker compose exec -T rabbitmq rabbitmqctl clear_parameter shovel dlq-replay-caseflow.steps`.

Two properties to know:

- **Steps are safe to run twice.** A step carries its id; S4 and S5 writes carry an `Idempotency-Key` derived from it, gate and job ids are deterministic, and consumers dedupe on `message_id` (`consumed_messages`). A replay cannot append a second audit record for the same step (ADR-28, ADR-35).
- **A replayed message is not retried again.** The shovel moves the message with its headers, so `x-attempt` is still 3. Reading `deliver_with_retry`, the first failure after a replay goes straight back to the DLQ. Fix first.

## Purge

Only when the message is known to be wrong, or the case is being thrown away. The run stays frozen; purging does not unfreeze it.

```bash
docker compose exec -T rabbitmq rabbitmqctl purge_queue caseflow.steps.dlq
```

For the demo case, `make demo-reset` is the clean way: it deletes the case's documents, audit records and Caseflow run, so the next upload starts a fresh run.

## Checked and not checked

- Run against a throwaway RabbitMQ 4.0 with this repo's definitions: `list_queues`, the exact shovel command that `make dlq-replay` runs (the message moved from `agent-worker.run.dlq` to `agent-worker.run` and the shovel removed itself), `purge_queue`, and `rabbitmqadmin get messages` with `ack_requeue_true` (the message stayed in the queue).
- Read from code, not run: the retry and park rules (`fcrm_common/events.py`), "a replayed message is not retried", and the effect of a pending step on a run (ADR-28, `fcrm_caseflow/steps.py`).
- Needs the live stack: how the harness monitor presents a stuck step, and an end-to-end replay of a real `caseflow.steps` message. `make resilience` (the DLQ replay drill) prints "not yet wired".
