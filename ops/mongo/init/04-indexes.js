// 04 · indexes that carry correctness guarantees (design A §2.4, §3.4, §3.5, §4.5).
//
// Every fcrm_audit index lives here because fcrm_audit_writer cannot create
// indexes. The harness and dedupe indexes are created here too so the
// guarantees exist before any worker starts; owning lanes may add more.
//
// fcrm_audit indexes keep MongoDB's default names (no code creates them).
// The harness and dedupe indexes below carry the exact names the owning code
// passes to create_indexes() (fcrm_caseflow/store.py, fcrm_common/outbox.py,
// fcrm_common/events.py): MongoDB raises IndexOptionsConflict (code 85) when
// the same keys are created twice under different names, which is how H1
// failed its first composed boot (defect-trail D-24).
//
// consumed_messages contract (fcrm_common.events.Consumer): one row per
// {message_id, consumer}, TTL on consumed_at. A single-key unique index on
// message_id would reject the second consumer of the same message.

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
ensureIndex("fcrm_harness", "run_events", { run_id: 1, seq: 1 }, { unique: true, name: "run_id_seq_unique" });
// Re-dispatching the same step with the same inputs is a no-op.
ensureIndex("fcrm_harness", "agent_jobs", { idempotency_key: 1 }, { unique: true });
ensureIndex("fcrm_harness", "agent_jobs", { status: 1, lease_until: 1 });
ensureIndex("fcrm_harness", "outbox", { published_at: 1, _id: 1 }, { name: "unpublished_scan" });
ensureIndex("fcrm_harness", "outbox", { event_id: 1 }, { unique: true, name: "event_id_unique" });

// ── consumed-message dedupe in every database that consumes events ────────
for (const database of ["fcrm_harness", "fcrm_case", "fcrm_policy"]) {
  ensureIndex(database, "consumed_messages", { message_id: 1, consumer: 1 }, { unique: true, name: "message_id_consumer_unique" });
}
