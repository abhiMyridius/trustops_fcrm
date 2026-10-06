# Runbooks

One short page per operational task. Every command assumes you are in the repo root (the folder with the `Makefile`) and that the stack was started with `make up`. The first column says whether a page needs the running stack.

| Page | Use it when | Needs the stack |
|---|---|---|
| [start-stop-reset.md](start-stop-reset.md) | Starting, stopping, wiping or re-seeding the stack, or re-running the demo case | yes |
| [dlq-replay.md](dlq-replay.md) | A case stops moving and a `*.dlq` queue is not empty | yes |
| [restart-a-worker.md](restart-a-worker.md) | A container is stuck, or you need to restart one process | yes |
| [bedrock-outage-and-fallbacks.md](bedrock-outage-and-fallbacks.md) | Agent runs fail at the model call, or Bedrock throttles | yes (the fallback chain itself is in `ai/models/tiers.yaml`) |
| [secrets-and-keys.md](secrets-and-keys.md) | Setting up `.env`, rotating the JWT key, handling AWS credentials | no, except the rotation step |
| [backup-restore.md](backup-restore.md) | Copying the data out of the stack and back in | yes |
| [verify-the-audit-chain.md](verify-the-audit-chain.md) | Checking that a case's audit trail is intact, or reading what a break looks like | yes |
| [incident-agent-misbehaviour.md](incident-agent-misbehaviour.md) | An agent produced a wrong, unsupported or restricted answer | yes (reading the audit trail) |
| [release-checklist.md](release-checklist.md) | Freezing a release: gates, clean-clone proof, tag | partly |

## How these pages were checked

- Make targets were read from the `Makefile` and expanded with `make -n` (nothing executed). `make dlq-replay` and `make evals-gate` were run without their argument and print their usage line.
- The RabbitMQ commands (`list_queues`, the shovel in `make dlq-replay`, `purge_queue`, peeking at a message) were run against a throwaway RabbitMQ 4.0 container started with this repo's `definitions.json`, `20-fcrm.conf`, `enabled_plugins` and `entrypoint.sh`. The container was not the project stack and was removed afterwards.
- The `mongodump`, `mongorestore` and MinIO `mc mirror` round trips were run against throwaway Mongo 8.0 and MinIO containers started from the images the compose file pins. Authentication flags were not part of the throwaway runs; they are the same flags `ops/scripts/seed_reset.py` uses.
- Nothing in these pages was run against the project stack by the lane that wrote them. Each page ends with the statements that still need the live stack to confirm.

## What is not wired

`make backup`, `make restore`, `make nightly`, `make resilience` and `make e2e` print "not yet wired". The backup page gives the manual commands instead. There is no automated kill-and-restart drill, so the statements about in-flight work are read from the code and the ADRs, not from a test. See `docs/governance/known-gaps.md`.
