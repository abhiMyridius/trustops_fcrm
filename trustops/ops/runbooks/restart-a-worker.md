# Restart a worker (or any container)

There are two Python processes, not ten (ADR-26). A "worker" restart means one of these:

| Container | Hosts | Restart with | Grace period |
|---|---|---|---|
| `harness` | H1 Caseflow, H2 agent worker, H3 ingestion, H4 context, on one RabbitMQ broker and one Mongo client | `docker compose restart harness` | 70 s (`x-worker` in `docker-compose.yaml`) |
| `api` | X1 identity, S1 case, S2 scoring, S3 policy, S4 audit, S5 decision, the outbox relays | `docker compose restart api` | 30 s |

You cannot restart H2 without H1, or S5 without S1: the process is the unit. After `api` restarts it takes about 85 seconds to become healthy again (S3 re-embeds the corpus). `harness` waits for `api` to be healthy only at `up`, not on a plain restart; if it starts first, its HTTP calls to the services fail and retry (see below).

```bash
docker compose restart harness
make ps                       # wait for healthy
make logs svc=harness         # the first lines show each unit wiring its queues
```

## What is safe

| Restart | Safe at any time? | What happens |
|---|---|---|
| `harness` | yes | Consumers stop, the 70 s grace lets handlers finish and ack, then the process exits. Anything not acked is redelivered when the new process connects |
| `api` | yes | HTTP calls in flight fail with a 5xx or a closed connection; the gateway answers 503 for the gap. Each outbox relay drains for a bounded time and releases its lease (`fcrm_common/outbox.py`); a lease the process died holding expires after its 10 s TTL |
| `gateway`, `web` | yes | stateless. Open SSE streams drop and the web reconnects with `Last-Event-ID` (ADR-30) |
| `opa` | yes, briefly | every service fails closed (503) while OPA is unreachable. Nothing is allowed by default |
| `mongo` | yes | healthy again after about 15 s on existing volumes (D-32). Transactions in flight abort and retry |
| `rabbitmq` | yes | queues are durable quorum queues on the `rabbit_data` volume; unacked messages are redelivered |
| `minio` | yes | objects are on the `minio_data` volume |

Never use `make down-v` to fix a stuck process: it deletes the data.

## What happens to work in flight

The guarantee is at-least-once delivery with idempotent handlers, not exactly-once.

- **A message being handled.** It is acked only after the handler finishes. H1 applies a trigger in one Mongo transaction that also records the message id in `consumed_messages`; if the process stops before the commit, nothing was applied and the message comes back. If it stops after the commit but before the ack, the redelivery finds its id in `consumed_messages` and is acked without being applied twice (`fcrm_common/events.py`).
- **An HTTP step (`caseflow.steps`).** The step id is derived from the triggering message id, so a redelivery re-mints the same one. A `step_done` run event is written in the same transaction as the step's result, and S4 and S5 writes carry an `Idempotency-Key` from the step id. A step that ran twice changes nothing the second time (ADR-28, ADR-35).
- **An agent job (H2) in the middle of a Bedrock call.** The call is lost with the process. The `agent.run` message was not acked, so the job runs again from the start: the tokens of the lost attempt are spent twice and are on no ledger. H1 keeps every `agent.done` by job id (ADR-33) so the join counts a job once. Read from the ADRs, not drilled.
- **A document being ingested (H3) or an index being built (H4).** Redelivered and redone. A build has a 300 s deadline (`CONTEXT_BUILD_TIMEOUT_SECONDS`).
- **An event waiting in an outbox.** It is in Mongo, committed with the state change that produced it. The relay publishes it after the restart. Nothing is lost; a publish can repeat, which consumers dedupe.
- **A peer that is not up yet.** An unreachable S1-S5 is retried by the broker's retry queue (four tries 30 s apart); only a permanent refusal, or the end of the retries, parks the message ([dlq-replay.md](dlq-replay.md)).

## When a restart does not fix it

- Read `make logs svc=harness` for the first error, not the last.
- `make dlq`: parked messages mean a cause to fix, then replay.
- A case that is in the `handoff` state because an agent failed after its retry will not resume by a restart: the analyst completes the step by hand on the Summary tab. See [bedrock-outage-and-fallbacks.md](bedrock-outage-and-fallbacks.md).

## Checked and not checked

- Checked: the container names, grace periods and restart commands against `docker-compose.yaml`; the delivery, dedupe and lease behaviour by reading `fcrm_common/events.py` and `fcrm_common/outbox.py`; the idempotency rules by their ADRs.
- Not checked: a kill-and-restart drill against the running stack. `make resilience` is not wired and `tests/resilience/` does not exist, so every statement under "What happens to work in flight" is read from code and ADRs. The 70 s grace is a configured value, not a measured shutdown time.
