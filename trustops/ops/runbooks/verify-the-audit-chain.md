# Verify the audit chain

Every state change and every human decision writes one record to the case's audit chain through S4. Records are hash-chained and append-only. This page shows how to check a chain, what a break looks like, and where the records live. The specification is `docs/governance/audit-chain.md`; the code is `src/backend/services/audit/fcrm_audit/chain.py`.

## The rule

```
hash = sha256(prev_hash + "\n" + canonical_body)
```

The first record of a case has `prev_hash` of 64 zeros. `seq` starts at 1 and is contiguous. `canonical_body` is the record's `case_id`, `seq`, `type`, `actor`, `summary`, `data` and `created_at` serialised with sorted keys and no spaces. `created_at` is truncated to milliseconds before hashing, because BSON stores milliseconds (D-19).

## Verify through the gateway

```bash
G=http://localhost:8080
login() {
  curl -s -X POST $G/api/fcrm/auth/login -H 'content-type: application/json' \
    -d "{\"username\":\"$1\",\"password\":\"demo-password-2026\"}" |
  python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])'
}
A=$(login riyer)                                                    # an analyst; a committee member (amendes) also works

curl -s -H "Authorization: Bearer $A" $G/api/fcrm/audit/verify/FCRM-2026-0118; echo
# {"case_id":"FCRM-2026-0118","record_count":9,"ok":true,"first_break_seq":null}

curl -s -H "Authorization: Bearer $A" $G/api/fcrm/audit/verify/FCRM-2026-0151; echo   # the demo case, after a live run
curl -s -H "Authorization: Bearer $A" $G/api/fcrm/audit/records/FCRM-2026-0151 | head -c 600; echo
curl -s -H "Authorization: Bearer $A" $G/api/fcrm/audit/head/FCRM-2026-0151; echo
```

`/verify` is allowed to the analyst, the committee and the service account; the PO, and the observer, get 403 (`ops/opa/data/permissions.json`). The login is rate limited to 10 per minute per address (the gateway and identity both enforce it): reuse the token, do not log in per call.

A case that was never run (the demo case before its first upload) has no chain. A closed seed case has nine records, seeded by S4 at start. The demo case's chain is whatever the live run wrote; the golden "67 records" belongs to the static walkthrough and the tests, not to a live run (ADR-27).

## What a break looks like

`ok` is false and `first_break_seq` names the first record that does not match: either its own hash differs from the recomputed one, or its `prev_hash` does not equal the previous record's hash, or `seq` skips. Everything from that record onward is untrusted.

You can see it without the stack. This builds the nine-record chain of seed case FCRM-2026-0118 in memory, verifies it, edits one summary and verifies again (run on 6 Oct 2026):

```bash
uv run --project src/backend --all-packages python - <<'PY'
from fcrm_audit.chain import verify_chain
from fcrm_audit.seedmodel import build_closed_case_chain
from fcrm_seed.loader import load_raw

CASE = "FCRM-2026-0118"
chain = build_closed_case_chain(next(c for c in load_raw()["cases"] if c["id"] == CASE))
print("intact:  ", verify_chain(CASE, chain))
chain[4] = chain[4].model_copy(update={"summary": "edited after the fact"})
print("tampered:", verify_chain(CASE, chain))
PY
# intact:   case_id='FCRM-2026-0118' record_count=9 ok=True first_break_seq=None
# tampered: case_id='FCRM-2026-0118' record_count=9 ok=False first_break_seq=5
```

The same property is tested against a real database in `tests/integration/blackbox/test_bb_04_audit_chain_tamper_anywhere.py`.

## What the system does with a broken chain

| Where | Behaviour |
|---|---|
| S4 start | its self-test verifies a seeded chain and refuses to become ready on a mismatch (`ChainVerifyFailedError`). After a hash-format change the message reads like corruption; see D-29 |
| H1 `closed.finalize` | calls `GET /verify/{case}`; on `ok: false` it raises `ChainBrokenError`, does not mark the step done, does not tell the PO, and the message ends in `caseflow.steps.dlq` after the retries. No `chain.verified` record is written ([dlq-replay.md](dlq-replay.md)) |
| The examiner | H1 verifies the chain before the examiner answers and never answers from a chain that does not verify |

## Where the records live

| Place | What |
|---|---|
| MongoDB `fcrm_audit.audit_records` | the chain; unique index `{case_id, seq}`, a `$jsonSchema` validator, indexes `{type, created_at}` |
| `fcrm_audit.proposals` | the evidence ledger: every agent proposal with prompt id and version, model label, pack hash and attempt |
| `fcrm_audit.verify_runs`, `idempotency_keys`, `examiner_questions`, `examiner_answers` | verification runs, replay keys, the examiner's questions and answers |

The only Mongo user S4 connects as, `fcrm_audit_writer`, can `find` and `insert` on these collections and nothing else, so a bug in the service cannot rewrite history; the database refuses (`ops/mongo/init/02-users.js`; S4's start-up self-test tries an update and must get `Unauthorized`). The Mongo root user can still change anything, which is why the chain exists as well: the guarantee is tamper evidence, not tamper prevention (`docs/governance/audit-chain.md`). Nothing is anchored outside the system.

Reading a collection directly (root only, credentials expanded in the container):

```bash
docker compose exec -T mongo sh -c 'mongosh --quiet --norc "mongodb://localhost:27017/fcrm_audit?directConnection=true" --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --eval "db.audit_records.countDocuments({case_id: \"FCRM-2026-0118\"})"'
```

## Checked and not checked

- Run: the offline verify-and-tamper snippet above, and the 9-record seed chain verifying. `make verify case=...` prints "not yet wired"; use the route.
- Checked against `docs/architecture/openapi/api.json` and `ops/opa/data/permissions.json`: the three routes, the `VerifyResult` shape and who may call each.
- Needs the live stack: the `curl` calls, the 9-record count in the running database, a chain of a live demo run, and the mongosh line.
