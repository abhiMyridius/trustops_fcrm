#!/usr/bin/env bash
# Initialiser run by ops/mongo/entrypoint.sh inside the `mongo` container (design A §11.1, §11.2).
#
# Runs every ops/mongo/init/NN-*.js in lexical order against the local mongod
# as the root user. Every script is idempotent, so it can safely run on
# every container start: an existing replica set, user, validator or index
# is left as it is (or brought back to the declared shape).
#
# These scripts are deliberately NOT mounted into the mongo container's
# /docker-entrypoint-initdb.d: the official entrypoint runs that directory on
# a temporary mongod started without --replSet, so rs.initiate() and
# transactions-dependent setup cannot run there.
set -euo pipefail

: "${MONGO_INITDB_ROOT_USERNAME:?MONGO_INITDB_ROOT_USERNAME is required}"
: "${MONGO_INITDB_ROOT_PASSWORD:?MONGO_INITDB_ROOT_PASSWORD is required}"
: "${MONGO_AUDIT_WRITER_PASSWORD:?MONGO_AUDIT_WRITER_PASSWORD is required}"
: "${MONGO_APP_PASSWORD:?MONGO_APP_PASSWORD is required}"
: "${MONGO_EVALS_READER_PASSWORD:?MONGO_EVALS_READER_PASSWORD is required}"

MONGO_HOST="${MONGO_HOST:-mongo:27017}"
INIT_DIR="$(cd "$(dirname "$0")" && pwd)"

# directConnection: before rs.initiate there is no primary to discover.
uri="mongodb://${MONGO_HOST}/admin?directConnection=true"

for script in "${INIT_DIR}"/[0-9][0-9]-*.js; do
  echo "mongo-init: running $(basename "${script}")"
  mongosh "${uri}" \
    --quiet \
    --username "${MONGO_INITDB_ROOT_USERNAME}" \
    --password "${MONGO_INITDB_ROOT_PASSWORD}" \
    --authenticationDatabase admin \
    --file "${script}"
done

echo "mongo-init: done"
