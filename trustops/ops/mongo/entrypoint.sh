#!/usr/bin/env bash
# Entrypoint wrapper for the `mongo` container (D1, design A §11.1).
#
# MongoDB refuses to run a replica set with authorization enabled unless the
# members share a keyFile, even when there is only one member. This writes a
# fresh random keyFile (a single-node set has no peer that must agree with
# it) and then hands over to the official entrypoint, which creates the root
# user from MONGO_INITDB_ROOT_* on first boot and drops to the mongodb user.
set -euo pipefail

KEYFILE=/run/fcrm-mongo/keyfile
install -d -m 0700 -o mongodb -g mongodb "$(dirname "${KEYFILE}")"
head -c 756 /dev/urandom | base64 -w 0 > "${KEYFILE}"
chown mongodb:mongodb "${KEYFILE}"
chmod 0400 "${KEYFILE}"

exec docker-entrypoint.sh mongod \
  --replSet rs0 \
  --bind_ip_all \
  --keyFile "${KEYFILE}" \
  "$@"
