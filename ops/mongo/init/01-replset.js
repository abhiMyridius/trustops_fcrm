// 01 · single-node replica set `rs0` (design A §1.3, §11.2).
//
// Transactions (state write + outbox in one transaction, design A §3.5) need
// a replica set even on one node. Idempotent: an initialised set is left
// alone. Waits until this node is a writable primary so 02–04 can write.
//
// The member host is the compose service name, so containers reach it as
// `mongo:27017`. Clients on the host must use `directConnection=true`
// (they cannot resolve `mongo`).

const RS_NAME = "rs0";
const MEMBER_HOST = process.env.MONGO_RS_MEMBER_HOST || "mongo:27017";
const WAIT_SECONDS = 60;

function replicaSetInitialised() {
  try {
    return rs.status().ok === 1;
  } catch (e) {
    if (e.codeName === "NotYetInitialized") {
      return false;
    }
    throw e;
  }
}

if (replicaSetInitialised()) {
  print(`01-replset: ${RS_NAME} already initialised`);
} else {
  const res = rs.initiate({
    _id: RS_NAME,
    members: [{ _id: 0, host: MEMBER_HOST }],
  });
  if (res.ok !== 1) {
    throw new Error(`rs.initiate failed: ${JSON.stringify(res)}`);
  }
  print(`01-replset: initiated ${RS_NAME} with member ${MEMBER_HOST}`);
}

let waited = 0;
while (!db.hello().isWritablePrimary) {
  if (waited >= WAIT_SECONDS) {
    throw new Error(`no writable primary after ${WAIT_SECONDS}s`);
  }
  sleep(1000);
  waited += 1;
}
print(`01-replset: writable primary after ${waited}s`);
