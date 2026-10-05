// 03 · $jsonSchema validators (design A §2.4).
//
// Only fcrm_audit.audit_records is declared here, because its writer
// (fcrm_audit_writer) holds insert+find only and could never run collMod
// itself. The required fields follow the contract model
// fcrm_contracts.api.audit.AuditRecord (contracts-first; design A §2.4.1's
// richer record shape is the S4 lane's to reconcile with the contract).
//
// OTHER COLLECTIONS' SCHEMAS ARE ADDED BY THE LANES THAT OWN THEM (S1 case,
// S2 scoring, S3 policy, S5 decision, H1 caseflow, X1 identity): they hold
// readWrite through fcrm_app and declare their validators at startup or in a
// new NN-*.js file here. Do not add another lane's schema to this file.
//
// Idempotent: creates the collection with the validator, or re-applies it
// with collMod when the collection already exists.

const HEX64 = "^[0-9a-f]{64}$";

const auditRecordSchema = {
  $jsonSchema: {
    bsonType: "object",
    title: "fcrm_audit.audit_records (hash-chained, append-only)",
    required: ["case_id", "seq", "type", "actor", "summary", "prev_hash", "hash", "created_at"],
    properties: {
      case_id: {
        bsonType: "string",
        minLength: 1,
        description: "case id (e.g. FCRM-2026-0151) or an oversight scope id",
      },
      seq: {
        bsonType: ["int", "long"],
        minimum: 0,
        description: "position in the case chain; unique with case_id (04-indexes.js)",
      },
      type: {
        bsonType: "string",
        minLength: 1,
        description: "P7 audit vocabulary, e.g. human.override",
      },
      actor: {
        bsonType: "string",
        minLength: 1,
        description: "system | ai | <user id> (AuditRecord.actor)",
      },
      summary: { bsonType: "string", description: "one-line human-readable what" },
      data: { bsonType: "object", description: "type-specific payload" },
      prev_hash: { bsonType: "string", pattern: HEX64, description: "sha256 hex of the previous record" },
      hash: { bsonType: "string", pattern: HEX64, description: "sha256(prev_hash + \\n + canonical body)" },
      created_at: { bsonType: "date", description: "UTC timestamp the record was appended" },
    },
  },
};

function applyValidator(database, collection, validator) {
  const target = db.getSiblingDB(database);
  const exists = target.getCollectionNames().includes(collection);
  const options = { validator, validationLevel: "strict", validationAction: "error" };
  if (exists) {
    const res = target.runCommand({ collMod: collection, ...options });
    if (res.ok !== 1) {
      throw new Error(`collMod ${database}.${collection} failed: ${JSON.stringify(res)}`);
    }
    print(`03-schemas: re-applied validator on ${database}.${collection}`);
  } else {
    target.createCollection(collection, options);
    print(`03-schemas: created ${database}.${collection} with validator`);
  }
}

applyValidator("fcrm_audit", "audit_records", auditRecordSchema);
