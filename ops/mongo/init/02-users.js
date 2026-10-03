// 02 · database users and the insert-only audit role (design A §2.4, BP §3.5).
//
// fcrm_audit_writer: the ONLY credential S4 (audit-service) holds. Its custom
// role grants `find` and `insert` on the append-only audit collections and
// nothing else: no update, no remove, no createIndex, no dropCollection, no
// collMod. The hash chain is therefore protected twice — by the chain itself
// (tampering is detectable) and by the database (tampering is refused).
// S4's startup self-test attempts an update_one with this user and must get
// `Unauthorized` (code 13) or it refuses to become ready.
//
// Because the writer cannot create indexes, every fcrm_audit index is created
// here by the root user (04-indexes.js); S4 must not call ensure_indexes().
//
// fcrm_app: the credential every other service and worker uses
// (MONGODB_URI). readWrite on the six non-audit databases and NO privilege on
// fcrm_audit, so no service except S4 can read or touch the audit trail
// ("no service reads another's database").
//
// Idempotent: existing roles/users are updated to the declared shape and the
// passwords re-applied from the environment (so rotating a password is
// `make up` after editing .env).

const AUDIT_DB = "fcrm_audit";
const AUDIT_ROLE = "fcrmAuditInsertOnly";

// Append-only collections in fcrm_audit. `audit_records` is the hash chain;
// `proposals` (evidence ledger), `verify_runs` and `idempotency_keys` are
// also append-only by design (a later proposal version is a new document;
// idempotency keys expire by TTL, which the server applies, not the user).
const AUDIT_APPEND_ONLY = ["audit_records", "proposals", "verify_runs", "idempotency_keys"];

const APP_DATABASES = [
  "fcrm_case",
  "fcrm_scoring",
  "fcrm_policy",
  "fcrm_decision",
  "fcrm_harness",
  "fcrm_identity",
];

function requireEnv(name) {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set`);
  }
  return value;
}

function upsertRole(database, roleName, privileges) {
  const target = db.getSiblingDB(database);
  if (target.getRole(roleName)) {
    target.updateRole(roleName, { privileges, roles: [] });
    print(`02-users: updated role ${database}.${roleName}`);
  } else {
    target.createRole({ role: roleName, privileges, roles: [] });
    print(`02-users: created role ${database}.${roleName}`);
  }
}

function upsertUser(userName, password, roles) {
  const admin = db.getSiblingDB("admin");
  if (admin.getUser(userName)) {
    admin.updateUser(userName, { pwd: password, roles });
    print(`02-users: updated user ${userName}`);
  } else {
    admin.createUser({ user: userName, pwd: password, roles });
    print(`02-users: created user ${userName}`);
  }
}

// ── insert-only audit role ────────────────────────────────────────────────
upsertRole(
  AUDIT_DB,
  AUDIT_ROLE,
  AUDIT_APPEND_ONLY.map((collection) => ({
    resource: { db: AUDIT_DB, collection },
    actions: ["find", "insert"],
  })),
);

upsertUser("fcrm_audit_writer", requireEnv("MONGO_AUDIT_WRITER_PASSWORD"), [
  { role: AUDIT_ROLE, db: AUDIT_DB },
]);

// ── application user (everything except the audit database) ──────────────
upsertUser(
  "fcrm_app",
  requireEnv("MONGO_APP_PASSWORD"),
  APP_DATABASES.map((database) => ({ role: "readWrite", db: database })),
);

// ── self-check: the audit role must hold exactly find + insert ────────────
const granted = db
  .getSiblingDB(AUDIT_DB)
  .getRole(AUDIT_ROLE, { showPrivileges: true })
  .privileges.flatMap((p) => p.actions);
const unexpected = granted.filter((a) => a !== "find" && a !== "insert");
if (unexpected.length > 0) {
  throw new Error(`audit role carries unexpected actions: ${unexpected}`);
}
print(`02-users: ${AUDIT_ROLE} verified (find, insert only)`);
