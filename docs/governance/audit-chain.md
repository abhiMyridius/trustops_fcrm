# Audit chain specification

The implementation is `src/services/audit/fcrm_audit/chain.py`, fully pure and tested in `tests/unit/services/audit/test_chain.py` (9 tests, including a full replay of the demo case's real 67-record trail). This document is the spec in prose; the code is the source of truth if they ever disagree.

## Format

```
hash = sha256(prev_hash + "\n" + canonical_body)
```

- `canonical_body` is the record's content (`case_id`, `seq`, `type`, `actor`, `summary`, `data`, `created_at` as ISO 8601) serialised with `json.dumps(..., sort_keys=True, separators=(",", ":"))` — sorted keys, no incidental whitespace, so the same logical record always hashes identically regardless of field insertion order.
- `prev_hash` for the first record in a case's chain is the genesis hash: 64 `"0"` characters.
- Every record's `prev_hash` is the previous record's `hash`. `seq` is 1-indexed and contiguous within a case.

## Why this shape

- **Tamper-evident, not tamper-proof against a privileged database user** — the guarantee is that *any* later alteration to a stored record (its summary, its data, its position) is detectable by recomputing the chain, not that alteration is impossible. Real tamper-*prevention* is the database-level control below.
- **Insert-only at the database layer** — `fcrm_audit_writer` (the Mongo user this service connects as) is granted only `insert` and `find` on `audit_records`, never `update` or `remove`, so even a bug in this service's own code cannot mutate history; the database refuses the operation outright.
- **No external anchoring (e.g. a public blockchain) is used.** The brief asks for an immutable audit trail within the system, not public verifiability; this is a deliberate scope decision, not an oversight.

## API surface (once the HTTP layer lands)

- `POST /records` — append-only; the caller supplies `case_id, type, actor, summary, data`; the service computes `seq`, `prev_hash` and `hash` from the case's current chain head, inside one transaction with the Mongo write, so a concurrent append is a duplicate-key error on the unique `{case_id, seq}` index, never a silent corruption.
- `GET /verify/{case_id}` — recomputes every hash in sequence and reports `{ok, record_count, first_break_seq}`. Used by `make demo` (the golden run must verify) and by the examiner agent's tool (`audit_log`) before it will answer a question — it only reads records from a chain that currently verifies.

## Worked example: the demo case

FCRM-2026-0151's real audit trail has 67 records. Record #49 (`human.override`) is the vendor-due-diligence rating change from 7.6 to 4.5; record #50 (`score.recomputed`) is the resulting 9.8→10.4 recomputation; record #67 (`chain.verified`) is the closing self-check. `tests/unit/services/audit/test_chain.py::test_the_demo_case_s_67_record_audit_trail_verifies_end_to_end` replays this exact sequence through the real chain-building code (not a mock) and confirms it verifies, and that mutating any one record's content or reordering two records is detected.
