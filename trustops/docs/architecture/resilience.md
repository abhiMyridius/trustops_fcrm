# Resilience drills

Design A section 10 says how the system should behave when a dependency or a process fails. The drills below inject those failures into the **running** stack and check that it does. A drill that passes proves a property; each one states the property, the failure injected and the observable that proves recovery. `make resilience` runs them and prints a PASS/FAIL table with the seconds each took.

## Running them

```bash
make up                                  # the 8-container stack, healthy
make resilience                          # the drills that restart nothing and call no AWS service: D5a, D5b, D6, D6b, D8
make resilience DISRUPTIVE=1             # adds the ones that stop, kill or restart containers: D2, D3 (and D1, D4, D7 below)
make resilience DISRUPTIVE=1 AWS=1       # adds the ones that call AWS: D1a, D1b, D1c, D4, D7
make resilience k=d3 DISRUPTIVE=1        # one drill (ids d1a d1b d1c d2 d3 d4 d5a d5b d6 d6b d7 d8; `k=d6` runs d6 and d6b)
make dlq                                 # what the DLQ tool shows (see "Dead letters" below)
```

A drill that lacks an approval is **skipped with the variable that would give it** in the table (`needs FCRM_RESILIENCE_AWS=1 (make resilience AWS=1 DISRUPTIVE=1): ...`), never run. `AWS=1` approves exactly `FCRM_RESILIENCE_AWS=1`, and only the value `1` counts.

### What each drill costs

| Drill | Restarts a container | Live AWS calls | Expected tokens |
|---|---|---|---|
| D1a | harness (SIGTERM) | 1 intake agent call on Bedrock (Haiku 4.5, the 5 KB vendor letter); 2 if the restart cuts it off and it is redelivered | about 6,000 per call, at most about 12,000 (the intake runs of the 6 October evals and demo took 5,600 to 12,200); about USD 0.01 a call |
| D1b | harness (SIGKILL) | 1 intake call, cut off, and 1 more when the job is redelivered: 2 | about 12,000, at most about 24,000 |
| D1c | none | 1 intake call (the duplicate `agent.done` that follows is injected, not a model call) | about 6,000, at most about 12,000 |
| D2 | RabbitMQ | none | none |
| D3 | Mongo | none (the api is not restarted; it would re-embed if the outage made it exit) | none |
| D4 | api (restart) | the api embeds the 197 policy clauses on Bedrock each time it starts: 1 start | about 20,000 embedding tokens (an estimate of about 100 per clause, not measured), under USD 0.01 |
| D5a | none | none | none |
| D5b | none | none | none |
| D6 | none | none (the job cannot build its context pack, so H2 never reaches the model) | none |
| D6b | none | none (the dispatch is refused before any job is published; the drill refuses to upload the document unless the tiny ledger is in place) | none |
| D7 | api (stop, start) | 1 api start, as D4 | as D4 |
| D8 | none | none | none |

So the default `make resilience` calls no AWS service and restarts nothing. With everything approved a run makes 3 to 5 intake calls (about 18,000 to 30,000 tokens, about USD 0.03 to 0.05) and 2 api starts (394 clause embeddings). Nothing in a harness restart calls AWS that I could find (its `/readyz` checks local MinIO buckets). D5a, D5b, D6, D6b and D8 raise scratch cases (a `POST /cases` and, for D6b and D5a, a document upload to the local MinIO and H3's extraction); none of that touches AWS.

- **They are destructive on purpose.** The `disruptive` ones stop, kill and restart containers of the running stack and must not share it with anyone. They are opt-in four ways: the `resilience` pytest marker (never part of `make check`), a guard that skips them unless `-m resilience` asked for them, the two approvals above, and a stack check before each drill. `make resilience` sets `FCRM_RESILIENCE_STRICT=1`, so a stack that is not ready fails the target instead of skipping it.
- **They need only the `docker` CLI and the gateway.** Containers are addressed by name (`trustops-<service>-1`), HTTP goes through the gateway on `:8080` as the demo personas, nothing reads `.env` and no credential is printed. A broker operation runs as a script *inside* the harness container (`ops/scripts/resilience/broker_agent.py`, sent on stdin), which already holds the broker URL; Mongo is reached with `mongosh` in the mongo container the same way: `find`, and the one narrow write of D6b. `FCRM_COMPOSE_PROJECT` and `FCRM_GATEWAY_URL` point the drills at another stack (`Stack.from_environment`).
- **Each drill is independent**, creates its own scratch case (title `[resilience-drill] ...`) and starts from a healthy stack (it waits for one). Whatever a drill does, a fixture puts back anything it left stopped and waits for the api and harness to be ready. The chain of a scratch case is append-only and stays; a draft is discarded so it leaves the PO's lists. The demo case is never touched.
- The login route allows ten a minute per address and other users of the machine share that; the drills wait out a 429 (one login per persona per drill that needs a fresh token).
- The result of each run is written to `ops/reports/resilience/<UTC stamp>.json` with the commit it ran on.

## Two things that shaped how the drills observe

- **RabbitMQ's queue statistics lag by up to five seconds** (`rabbitmqctl list_queues` reads them). A small intake call takes about that long, so `messages_unacknowledged` cannot say "a job is in flight right now". The in-flight job is caught from the harness log instead (a delivery on `agent-worker.run` with no `Processed` after it), and every baseline and every "is it idle" check waits out the refresh.
- **The log's message ids are ten characters** (`evt_01M487`, the first six of a ULID's timestamp), so messages minted within some minutes of each other share one. Deliveries are counted, not collected in a set.

## The drills

| Id | Property | Injection | Observable that proves it |
|---|---|---|---|
| D1a | At-least-once delivery with idempotent steps (ADR-28, ADR-35): a worker that goes away politely loses nothing and repeats nothing | `docker restart` of the harness (SIGTERM, up to 70 s to drain) while the intake job of a scratch case is running | the case's chain reaches `proposal.extraction`; `seq` gapless and unique; one `case.created`, `workflow.started`, `proposal.extraction`; at most one record per step, job and attempt; `audit/verify` ok with the record count the records route shows; no dead-letter queue grew; the case's token ledger holds nothing reserved and every job settled (a cut-off job's tokens are not counted twice) |
| D1b | The same, when the worker dies with no chance to ack | `docker kill` (SIGKILL) of the harness while the job runs | the new process is handed the job again (its log shows the delivery), then the same chain checks as D1a |
| D1c | A repeat `agent.done` for a finished job changes nothing | after an intake run finished, publish another `agent.done` for the same job under a fresh event id (what the second run of a redelivered job publishes if the first had already reported) | no audit record appears (no second `agent.done.intake`, no second `proposal.*`); chain verifies; no dead letter; the token ledger is exactly as it was (a job settles once) |
| D2 | The transactional outbox is the durability boundary (ADR-02, BUILD-PLAN 3.9) | `docker stop` of RabbitMQ; a PO writes a case while it is down; `docker start` | the write answers 201; the harness reports not ready meanwhile; every work queue has a consumer again; the case gets `case.created` and `workflow.started` exactly once each; a case raised after recovery goes the same way; chains verify; no dead letter |
| D3 | A dependency outage fails clean and heals (design A 10.3) | `docker stop` of Mongo; a read and a write as the PO, `auth/me` and the analyst queue as the analyst; `docker start` | each request answers 502/503/504 in under 40 s (never a hang, never an unhandled 500); the api and harness report not ready meanwhile; no case exists for the failed write (the case and its outbox row rolled back together); the case raised before the outage is readable; a new case gets its `workflow.started`; chains verify; no dead letter |
| D4 | A stream resumes without loss (ADR-30) | an analyst stream is open on a scratch case; `docker restart` of the api; the PO adds an event; reconnect with `Last-Event-ID` = the last `seq` seen | the first connection ended (the client noticed); the resumed one opens 200 and delivers the new event; the two connections carry every `seq` from 1 to the head once, equal to a fresh replay from 0 |
| D5a | Replay of a parked message is safe and goes through the repo's tool (design A 4.5) | a valid `harness.step` of an already finished step is put on `dlx`; `make dlq-replay q=caseflow.steps id=<message id>` | the tool showed it parked with `x-attempt` 3; the DLQ is back to its starting depth; the harness log says "step already done"; the chain gained no record |
| D5b | Nothing is dropped and nothing is stuck | onto `caseflow.steps`: a body cut off mid-object (poison: straight to the DLQ) and a well-formed envelope with an invalid payload (retried through `caseflow.steps.retry` eight times, about four minutes, then parked); a case is raised while the second is retrying; `make dlq-purge` by id, refused without `CONFIRM=1` | the first never touches the retry queue; the second does and lands in the DLQ after at least 60 s; both show their error and attempt count (0 and 8); the bystander case gets its `workflow.started` meanwhile; the refused purge deleted nothing; after the confirmed purges the DLQ is where it started and the consumer is attached |
| D6 | A failure is never papered over: it ends in a human handoff that carries what the job was given (flow map F-path, ADR-14) | an `agent.run` for the intake agent with no `doc_id` published for a scratch case's run (see "Why these triggers") | H2's `agent.failed` record names a context pack failure; H1 records `agent.failed.after_retry` into state `handoff` and a `human.handoff` record (phase `opened`) naming the job and the reference to its inputs; the run in H1's database is in `handoff` with an open `handoff` gate; the analyst's `GET cases/{id}/handoff/gates` shows that gate with the failure and the reference, and H2 kept the job's inputs (`agent_raw_inputs`); H1 holds no result for the job and the chain has no `proposal.*`; the token ledger holds nothing reserved; no dead letter; chain verifies |
| D6b | Budget exhaustion ends in the same handoff, before any model is called (ADR-14, ADR-27) | a scratch case gets a token ledger with a cap of 1,000 (one `$setOnInsert` write in `fcrm_harness.case_budgets`, only for a case with no ledger) and its document is uploaded, so the intake job H1 would dispatch cannot be paid for | a `budget.exhausted` audit record (cap, spent, reserved, requested); the run in `handoff` with an open gate whose reason is `budget_exhausted` and a `human.handoff` record; the analyst's gate list shows it with no raw inputs (no job ever ran); the harness received no agent job; the ledger reserved nothing and kept the refusal; no `proposal.*`; no dead letter; chain verifies |
| D7 | An outage of the token service fails clean, heals without a re-login and never fails open (design A 8.1) | `docker stop` of the api with an analyst token issued earlier; two calls through the gateway with it; `docker start` | before, `auth/me` 200; during, both calls 502/503/504 in under 40 s; after, the same token answers 200 for the same user and the analyst queue answers 200; a token with an altered signature gets 401 or 403, never 200; the harness is ready again |
| D8 | The chain survives and nothing is left parked | after the others | every scratch case of the session and one untouched case (a seeded closed case, read only; the demo case is left alone): `audit/verify` ok for no more records than the records route shows, `seq` 1..n; no `*.dlq` holds more than before the first drill (what someone else parked earlier is left alone and logged at the session's start) |

### Why these triggers for D6 and D6b

Neither calls a model. `JobPayload` carries no budget field, so D6 uses an `agent.run` for the intake agent without a `doc_id`: H2 cannot build its context pack and reports `agent.failed` at once, H1 takes `AGENT_FAILED_AFTER_RETRY` to `handoff`, and the gate carries what the job was given (the dispatched job and the builder's error; there is no pack to show). The per-case token ledger (`case_budgets`, ADR-14) lets D6b provoke the other way into the same handoff: a scratch case seeded with a tiny cap has the dispatch itself refused by Guard 3. That is the drills' one write, and it is as narrow as it can be (`MongoReader.seed_scratch_ledger`: one document, `$setOnInsert`, a scratch case id, a cap of at most 5,000, never an existing ledger). The per-attempt budget path (`UsageLimitExceeded`, the second attempt) is asserted over fakes in `tests/unit/workers/agent-worker` and `tests/unit/packages/guardrails`.

## Dead letters

`make dlq` lists every dead-letter queue and its depth; `make dlq-inspect q=caseflow.steps` shows what is parked; `make dlq-replay q=caseflow.steps [id=<message id>]` moves messages back onto the work queue with a **fresh retry budget** (`x-attempt` reset to 0, so each gets its three attempts again) and removes one from the DLQ only after the broker confirmed the republish; `make dlq-purge q=caseflow.steps CONFIRM=1 [id=<message id>]` deletes, and without `CONFIRM=1` only says what it would delete. The tool is `ops/scripts/dlq.py`, standard library only. Replay only after the cause is fixed: a poison message parks again.

The depths `make dlq` prints are RabbitMQ's statistics (up to five seconds old); `inspect`, `replay` and `purge` act on the queue itself and report their own counts.

## Defects the drills and their rehearsal found

Fixed in this lane, each with a test that fails without the change (items 3 and 4 reach a running stack only when its image is rebuilt; D5b plants a JSON-typed malformed message only where `fcrm_common.broker.delivery_headers` exists in the running harness, and a plain-text one otherwise, because the first would sit unacknowledged on an old harness for good):

1. **A second `agent.done` for a finished job was applied again** (`fcrm_caseflow.engine`, `AgentResultStore`). H2 acknowledges a job only after publishing `agent.done` and writing its audit records; a worker killed in that window is redelivered the job and publishes a second `agent.done` under a fresh event id, which the consumer's `message_id` dedupe cannot see. H1 fired `AGENT_DONE_INTAKE` again: a second `intake.publish` step and every `proposal.*` record twice. The store now keeps the first result per job (`$setOnInsert` only) and says whether it stored one; the engine ignores an `agent.done` whose result was already stored. D1c is the live regression check. Oversight jobs, whose ids are fixed on purpose so a re-dispatch is the same job, are untouched.
2. **A parked message was dropped by being looked at six times** (`ops/rabbitmq/definitions.json`). The `fcrm-quorum-safety` policy's `delivery-limit: 5` also matched every `*.dlq`, which has no dead-letter exchange of its own. Measured on a scratch RabbitMQ 4.0 with this topology: the seventh look found an empty queue. `fcrm-dlq-keep` (priority 10, `delivery-limit: -1`, pattern `\.dlq$`) switches the limit off for the dead-letter queues only; with it a message survived eight looks, and a broker restarted on the updated file showed each policy on the right queues. The running broker has it (`rabbitmqctl list_policies` on 6 October, evening).
3. **A message whose body is not JSON was never acknowledged** (`fcrm_common.broker`). FastStream decodes a message by its `content_type` before the handler runs, and for `application/json` (every message this system publishes) it raises on a body that is not JSON. With manual acks that left the message neither acked nor nacked: it sat unacknowledged on the channel, holding one of the consumer's prefetch slots, and came back after every restart; `Consumer.process` never saw it, so the "poison goes straight to the DLQ" rule did not hold for it. Reproduced on a scratch broker with the real consumer logic: two such messages stalled a consumer with a prefetch of two. `wire_consumer` now registers a decoder that leaves the body as bytes, so the consumer parses the envelope itself and dead-letters what does not parse. D5b exercises it (when the running harness has the fix; see below).
4. **A retried message lost its AMQP properties** (`fcrm_common.broker`). A failed message is republished to the retry queue or the DLQ from `(body, headers)`, and the headers FastStream hands over do not include the message id, type or correlation id (they are AMQP properties), so every retry hop gave the message a made-up id and dropped its type and the run it belonged to: a parked message could not be found by the id it was published with. The adapter now passes the properties along under the names `message_headers` gives them. The DLQ tool also names a message by its envelope's `event_id`, which is the id in the logs and the audit trail and is readable even from a body that is cut off. On the scratch broker with both fixes, a cut-off body reached the DLQ in under 5 s with attempt 0 and error `poison: 1 errors`, an invalid payload after 90 s of retries (three cycles of the 30 s TTL) with attempt 3 and `ValidationError`, each under the id it was published with.
5. **`make dlq-replay` could not replay** (the shovel it used). A shovel moved the message with `x-attempt: 3`, so a replayed message that failed once was parked again at its first failure, with no retries. Replaced by `ops/scripts/dlq.py`.
6. **The running OPA did not have the handoff routes' allow rule** (`docker-compose.yaml`, `Makefile`; D-92). OPA reads its policy and data once, at start, and compose does not recreate it when a bind-mounted file changes. The OPA container had started before the handoff rule was merged, so D6 and D6b got a 403 on `GET /cases/{id}/handoff/gates`; a tightened rule would have gone unenforced the same way. Every `make up*` target now recreates the `opa` service before the stack, and a test pins the order. After an OPA restart D6 and D6b passed.
7. **A harness step was parked by an ordinary api restart** (`fcrm_caseflow.consumers`; D-93). D4 restarted the api right after creating its scratch case, so the case's first step failed three times, 30 s apart, while the api took about 90 s to come back (it embeds the 197 clauses on start); the step was dead-lettered and D8 reported it. A `caseflow.steps` message now gets eight attempts (about four minutes); steps are idempotent, so more attempts only delay a permanent failure's trip to the DLQ. `make dlq-replay` moved the parked step back and it ran. D4 then passed with nothing parked.
8. **Three drills assumed three attempts** (`ops/scripts/resilience/drills.py`, `tests/resilience/test_d05_dlq_replay_and_poison.py`; D-94). After item 7, D5b still waited 180 s for the invalid payload to park and asserted `[0, 3]` attempts. Its message parked correctly, after the drill had given up, and D6b and D8 failed on the message it left. The deadline is now derived from the attempts and the retry delay, the assertion reads `[0, 8]`, and a unit test keeps them equal to the consumer's constant and the broker's TTL.

## Gaps this lane found that others have since closed

The first version of this document listed three gaps found by reading: the per-case token cap was not enforced across jobs, a handoff showed no raw inputs, and `evals.events` grew without bound. The integration lanes closed all three (the token ledger, the handoff that carries raw inputs and writes `human.handoff`, a bounded `evals.events`); D6 and D6b now check the first two on the running stack.

## Last run

All twelve drills have passed on the live stack on the final product build. No single session had all twelve green: the first sessions found the two product defects above (items 6 and 7) and two drill-side assumptions (item 8), so the last passing result of each drill comes from a different run. The drills ran on the economy profile (`make up-economy`, ADR-56); the only agent call in any drill is the intake agent, which runs on Haiku 4.5 in both profiles, and each api restart also embeds the 197 clauses on Titan. Each run wrote `ops/reports/resilience/<stamp>.json` with the commit it ran on; all five are committed, the failed ones too.

| Drill | Result | Seconds | Run (commit) |
|---|---|---|---|
| D1a harness SIGTERM mid run | PASS | 121.3 | `20261006T140722Z` (`34c63b7`) |
| D1b harness SIGKILL mid run | PASS | 90.7 | `20261006T140722Z` |
| D1c second `agent.done` ignored | PASS | 60.0 | `20261006T140722Z` |
| D2 RabbitMQ stop and start | PASS | 39.3 | `20261006T140722Z` |
| D3 MongoDB stop and start | PASS | 68.1 | `20261006T140722Z` |
| D4 api restart under an open stream | PASS, nothing parked | 139.3 | `20261006T140722Z` |
| D5a parked step replayed | PASS | 38.6 | `20261006T140722Z` |
| D5b poison messages parked, other work goes on | PASS | 279.2 | `20261006T141956Z` (`f2b9a2f`) |
| D6 failed job ends in a handoff | PASS | 32.5 | `20261006T140722Z` |
| D6b spent token cap refused into a handoff | PASS | 32.1 | `20261006T141425Z` (`20c5622`) |
| D7 api stopped under an issued token | PASS | 95.9 | `20261006T140722Z` |
| D8 every chain verifies, every DLQ empty | PASS | 1.3 | `20261006T141425Z` |

What each failed run showed: `20261006T134218Z` (`6a53446`, 9 passed, 3 failed): D6 and D6b got a 403 from a stale OPA (item 6) and D8 found D4's parked step (item 7); `20261006T134526Z` (D6 and D6b passed after an OPA restart); `20261006T140722Z` (`34c63b7`, 9 passed, 3 failed): D5b's stale deadline, and D6b and D8 on the message it left (item 8); `20261006T141425Z` (`20c5622`, D6b and D8 passed, D5b failed on its stale assertion). D8's passing run covered the scratch cases of that short session (D5b and D6b); in the two earlier sessions, which covered every drill, it did its job and flagged the parked step.

What the drills did not cover: a failure of Bedrock itself (D6 uses a job that cannot build its context pack, D6b a seeded ledger; neither reaches the model), a network partition, a full disk, and more than one container failing at once.
