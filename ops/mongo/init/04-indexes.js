// 04 · indexes that carry correctness guarantees (design A §2.4, §3.4, §3.5, §4.5).
//
// Every fcrm_audit index lives here because fcrm_audit_writer cannot create
// indexes. The harness and dedupe indexes are created here too so the
// guarantees exist before any worker starts; owning lanes may add more.
//
// Index names are left to MongoDB's defaults (e.g. `case_id_1_seq_1`) on
// purpose: a lane that calls ensure_indexes() with the identical spec is a
// no-op, while a lane that declares the same keys with different options
// fails loudly (IndexOptionsConflict) instead of silently weakening a
// uniqueness rule.
//
// consumed_messages contract for every consumer: write
// {message_id, expires_at: now + 7 days, ...}; the TTL index deletes the row
// at expires_at (expireAfterSeconds: 0).

function ensureIndex(database, collection, keys, options) {
  const name = db.getSiblingDB(database).getCollection(collection).createIndex(keys, options || {});
  print(`04-indexes: ${database}.${collection} ${JSON.stringify(keys)} ${JSON.stringify(options || {})} -> ${name}`);
}

// ── fcrm_audit (S4) ───────────────────────────────────────────────────────
// One record per (case, seq): optimistic seq allocation retries on duplicate.
ensureIndex("fcrm_audit", "audit_records", { case_id: 1, seq: 1 }, { unique: true });
ensureIndex("fcrm_audit", "audit_records", { case_id: 1, type: 1 });
ensureIndex("fcrm_audit", "proposals", { case_id: 1, agent: 1, kind: 1 });
ensureIndex("fcrm_audit", "proposals", { run_id: 1 });
ensureIndex("fcrm_audit", "verify_runs", { case_id: 1, at: -1 });
// Idempotency-Key replay window: 24 h (design A §2).
ensureIndex("fcrm_audit", "idempotency_keys", { at: 1 }, { expireAfterSeconds: 86400 });

// ── fcrm_harness (H1 caseflow, H2 agent-worker) ───────────────────────────
// Replayable event store: one event per (run, seq).
ensureIndex("fcrm_harness", "run_events", { run_id: 1, seq: 1 }, { unique: true });
// Re-dispatching the same step with the same inputs is a no-op.
ensureIndex("fcrm_harness", "agent_jobs", { idempotency_key: 1 }, { unique: true });
ensureIndex("fcrm_harness", "agent_jobs", { status: 1, lease_until: 1 });
ensureIndex("fcrm_harness", "outbox", { published_at: 1, _id: 1 });

// ── consumed-message dedupe in every database that consumes events ────────
for (const database of ["fcrm_harness", "fcrm_case", "fcrm_policy"]) {
  ensureIndex(database, "consumed_messages", { message_id: 1 }, { unique: true });
  ensureIndex(database, "consumed_messages", { expires_at: 1 }, { expireAfterSeconds: 0 });
}
