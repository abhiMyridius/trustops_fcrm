# Resilience drills

Design A section 10 says how the system should behave when a dependency or a process fails. The drills below inject those failures into the **running** stack and check that it does. A drill that passes proves a property; each one states the property, the failure injected and the observable that proves recovery. `make resilience` runs them and prints a PASS/FAIL table with the seconds each took.

## Running them

```bash
make up                      # the 8-container stack, healthy
make resilience              # all drills, about 25 minutes, prints the table
make resilience k=d3         # one drill (ids d1a d1b d1c d2 d3 d4 d5a d5b d6 d7 d8)
make dlq                     # what the DLQ tool shows (see "Dead letters" below)
```

- **They are destructive on purpose.** They stop, kill and restart containers of the running stack and must not share it with anyone. They are opt-in three ways: the `resilience` pytest marker (never part of `make check`), a guard that skips them unless `-m resilience` asked for them, and a stack check before each drill. `make resilience` sets `FCRM_RESILIENCE_STRICT=1`, so a stack that is not ready fails the target instead of skipping it.
- **They need only the `docker` CLI and the gateway.** Containers are addressed by name (`trustops-<service>-1`), HTTP goes through the gateway on `:8080` as the demo personas, nothing reads `.env` and no credential is printed. A broker operation runs as a script *inside* the harness container (`ops/scripts/resilience/broker_agent.py`, sent on stdin), which already holds the broker URL; the one read of Mongo documents (`mongosh` in the mongo container, `find` only) uses the container's own root credentials the same way. `FCRM_COMPOSE_PROJECT` and `FCRM_GATEWAY_URL` point the drills at another stack.
- **Cost.** Three drills (D1a, D1b, D1c) each run one real intake agent call on the smallest demo document (5 KB), a few cents on Bedrock. Nothing else calls a model.
- **Each drill is independent**, creates its own scratch case (title `[resilience-drill] ...`) and starts from a healthy stack (it waits for one). Whatever a drill does, a fixture puts back anything it left stopped and waits for the api and harness to be ready. The chain of a scratch case is append-only and stays; a draft is discarded so it leaves the PO's lists.
- The result of each run is written to `ops/reports/resilience/<UTC stamp>.json` with the commit it ran on.

## Two things that shaped how the drills observe

- **RabbitMQ's queue statistics lag by up to five seconds** (`rabbitmqctl list_queues` reads them). A small intake call takes about that long, so `messages_unacknowledged` cannot say "a job is in flight right now". The in-flight job is caught from the harness log instead (a delivery on `agent-worker.run` with no `Processed` after it), and every baseline and every "is it idle" check waits out the refresh.
- **The log's message ids are ten characters** (`evt_01M487`, the first six of a ULID's timestamp), so messages minted within some minutes of each other share one. Deliveries are counted, not collected in a set.

## The drills

| Id | Property | Injection | Observable that proves it |
|---|---|---|---|
| D1a | At-least-once delivery with idempotent steps (ADR-28, ADR-35): a worker that goes away politely loses nothing and repeats nothing | `docker restart` of the harness (SIGTERM, up to 70 s to drain) while the intake job of a scratch case is running | the case's chain reaches `proposal.extraction`; `seq` gapless and unique; one `case.created`, `workflow.started`, `proposal.extraction`; at most one record per step, job and attempt; `audit/verify` ok with the record count the records route shows; no dead-letter queue grew |
| D1b | The same, when the worker dies with no chance to ack | `docker kill` (SIGKILL) of the harness while the job runs | the new process is handed the job again (its log shows the delivery), then the same chain checks as D1a |
| D1c | A repeat `agent.done` for a finished job changes nothing | after an intake run finished, publish another `agent.done` for the same job under a fresh event id (what the second run of a redelivered job publishes if the first had already reported) | no audit record appears (no second `agent.done.intake`, no second `proposal.*`); chain verifies; no dead letter |
| D2 | The transactional outbox is the durability boundary (ADR-02, BUILD-PLAN 3.9) | `docker stop` of RabbitMQ; a PO writes a case while it is down; `docker start` | the write answers 201; the harness reports not ready meanwhile; every work queue has a consumer again; the case gets `case.created` and `workflow.started` exactly once each; a case raised after recovery goes the same way; chains verify; no dead letter |
| D3 | A dependency outage fails clean and heals (design A 10.3) | `docker stop` of Mongo; a read and a write as the PO, `auth/me` and the analyst queue as the analyst; `docker start` | each request answers 502/503/504 in under 40 s (never a hang, never an unhandled 500); the api and harness report not ready meanwhile; no case exists for the failed write (the case and its outbox row rolled back together); the case raised before the outage is readable; a new case gets its `workflow.started`; chains verify; no dead letter |
| D4 | A stream resumes without loss (ADR-30) | an analyst stream is open on a scratch case; `docker restart` of the api; the PO adds an event; reconnect with `Last-Event-ID` = the last `seq` seen | the first connection ended (the client noticed); the resumed one opens 200 and delivers the new event; the two connections carry every `seq` from 1 to the head once, equal to a fresh replay from 0 |
| D5a | Replay of a parked message is safe and goes through the repo's tool (design A 4.5) | a valid `harness.step` of an already finished step is put on `dlx`; `make dlq-replay q=caseflow.steps id=<message id>` | the tool showed it parked with `x-attempt` 3; the DLQ is back to its starting depth; the harness log says "step already done"; the chain gained no record |
| D5b | Nothing is dropped and nothing is stuck | onto `caseflow.steps`: an unparseable body (poison: straight to the DLQ) and a well-formed envelope with an invalid payload (retried through `caseflow.steps.retry` three times, then parked); a case is raised while the second is retrying; `make dlq-purge` by id, refused without `CONFIRM=1` | the first never touches the retry queue; the second does and lands in the DLQ after at least 60 s; both show their error and attempt count (0 and 3); the bystander case gets its `workflow.started` meanwhile; the refused purge deleted nothing; after the confirmed purges the DLQ is where it started and the consumer is attached |
| D6 | A failure is never papered over: it ends in a human handoff (flow map F-path, ADR-14) | an `agent.run` for the intake agent with no `doc_id` published for a scratch case's run (see "Why this trigger") | H2's `agent.failed` record names a context pack failure; H1 records `agent.failed.after_retry` into state `handoff`; the run in H1's database is in `handoff` with an open `handoff` gate; H1 holds no result for the job and the chain has no `proposal.*`; no dead letter; chain verifies |
| D7 | An outage of the token service fails clean, heals without a re-login and never fails open (design A 8.1) | `docker stop` of the api with an analyst token issued earlier; two calls through the gateway with it; `docker start` | before, `auth/me` 200; during, both calls 502/503/504 in under 40 s; after, the same token answers 200 for the same user and the analyst queue answers 200; a token with an altered signature gets 401 or 403, never 200; the harness is ready again |
| D8 | The chain survives and nothing is left parked | after the others | every scratch case of the session and two untouched cases (a seeded closed case and the demo case, read only): `audit/verify` ok with the records route's count, `seq` 1..n; every `*.dlq` empty |

### Why this trigger for D6 (budget exhaustion)

`JobPayload` carries no budget field and the tier budgets are constants in `fcrm_guardrails.budget`, so a tiny per-job budget cannot be injected without changing shared configuration. An `agent.run` for the intake agent without a `doc_id` takes the identical route (H2's `agent.failed`, H1's `AGENT_FAILED_AFTER_RETRY` transition to `handoff`, a `handoff` gate for the analyst) at once, with no model call and no cost. The budget-specific part (Guard 3 refusing a call, `UsageLimitExceeded`, the second attempt, then `agent.failed`) is asserted over fakes in `tests/unit/workers/agent-worker` and `tests/unit/packages/guardrails`. What the drill cannot show is listed under "Open gaps".

## Dead letters

`make dlq` lists every dead-letter queue and its depth; `make dlq-inspect q=caseflow.steps` shows what is parked; `make dlq-replay q=caseflow.steps [id=<message id>]` moves messages back onto the work queue with a **fresh retry budget** (`x-attempt` reset to 0, so each gets its three attempts again) and removes one from the DLQ only after the broker confirmed the republish; `make dlq-purge q=caseflow.steps CONFIRM=1 [id=<message id>]` deletes, and without `CONFIRM=1` only says what it would delete. The tool is `ops/scripts/dlq.py`, standard library only. Replay only after the cause is fixed: a poison message parks again.

The depths `make dlq` prints are RabbitMQ's statistics (up to five seconds old); `inspect`, `replay` and `purge` act on the queue itself and report their own counts.

## Defects the drills and their rehearsal found

Fixed in this lane, each with a test that fails without the change:

1. **A second `agent.done` for a finished job was applied again** (`fcrm_caseflow.engine`, `AgentResultStore`). H2 acknowledges a job only after publishing `agent.done` and writing its audit records; a worker killed in that window is redelivered the job and publishes a second `agent.done` under a fresh event id, which the consumer's `message_id` dedupe cannot see. H1 fired `AGENT_DONE_INTAKE` again: a second `intake.publish` step and every `proposal.*` record twice. The store now keeps the first result per job (`$setOnInsert` only) and says whether it stored one; the engine ignores an `agent.done` whose result was already stored. D1c is the live regression check. Oversight jobs, whose ids are fixed on purpose so a re-dispatch is the same job, are untouched.
2. **A parked message was dropped by being looked at six times** (`ops/rabbitmq/definitions.json`). The `fcrm-quorum-safety` policy's `delivery-limit: 5` also matched every `*.dlq`, which has no dead-letter exchange of its own. Measured on a scratch RabbitMQ 4.0 with this topology: the seventh look found an empty queue. `fcrm-dlq-keep` (priority 10, `delivery-limit: -1`, pattern `\.dlq$`) switches the limit off for the dead-letter queues only; with it a message survived eight looks, and a broker restarted on the updated file showed each policy on the right queues. It reaches the running broker the next time it boots.
3. **`make dlq-replay` could not replay** (the shovel it used). A shovel moved the message with `x-attempt: 3`, so a replayed message that failed once was parked again at its first failure, with no retries. Replaced by `ops/scripts/dlq.py`.

## Open gaps (found by reading, not fixed here)

- **The case token cap (120k) is not enforced in the live path.** H2 builds `CaseBudget(job.case_id)` fresh for every job and H1 never reserves, so Guard 3's case ledger (design A 3.3: `budgets`, refuse when `used + reserved + request > cap`, open `handoff{reason: budget_exhausted}`) does not exist across jobs. The per-attempt token limit does (`UsageLimits`). Proposed fix: an H1 `budgets` collection with reserve at dispatch and settle on `agent.done`/`agent.failed` in the dispatch transaction, and a refused reservation opening the handoff.
- **A handoff shows no raw inputs.** `AgentFailed.raw_inputs_ref` is never set, H2 does not store `jobs/{run_id}/{job_id}.raw.json`, `StepKind.HANDOFF_OPEN` has no handler (the audit map lists `human.handoff` and nothing writes it), and the harness monitor is only refreshed after a step, so the failure that opens the handoff does not refresh `GET cases/{id}/harness`. D6 asserts what exists (the state, the gate, the records, nothing invented).
- **`evals.events` grows without bound.** Nothing consumes it (no consumer, no TTL or length limit); it held 18 messages when this lane started and over a hundred a few hours later.

## Last run

Not yet run against the stack: it was in use by the integration lane while this lane was written. The helpers, the DLQ tool and the flows are covered by unit tests over fakes, and the broker operations and the `make dlq-*` targets were rehearsed against a scratch RabbitMQ with the repository's topology.
