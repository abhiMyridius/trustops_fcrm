#!/usr/bin/env bash
# Entrypoint wrapper for the `rabbitmq` container (X3).
#
# RabbitMQ skips creating RABBITMQ_DEFAULT_USER when definitions are imported
# at boot, and the committed definitions.json must not carry a credential.
# So the user is added here, at container start: the password from .env is
# hashed with `rabbitmqctl hash_password` (offline, salted SHA-256), and a
# runtime copy of definitions.json with that one administrator user and its
# permissions on "/" is written to /run (never to the repo or the data
# volume). rabbitmq.conf (20-fcrm.conf) imports that runtime copy at boot.
set -euo pipefail

: "${RABBITMQ_DEFAULT_USER:?RABBITMQ_DEFAULT_USER is required}"
: "${RABBITMQ_DEFAULT_PASS:?RABBITMQ_DEFAULT_PASS is required}"
if [[ ! "${RABBITMQ_DEFAULT_USER}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "rabbitmq entrypoint: RABBITMQ_DEFAULT_USER may only use [A-Za-z0-9_.-]" >&2
  exit 1
fi

SOURCE=/etc/rabbitmq/definitions.json
RUNTIME_DIR=/run/fcrm-rabbitmq
RUNTIME="${RUNTIME_DIR}/definitions.json"

hash="$(rabbitmqctl hash_password "${RABBITMQ_DEFAULT_PASS}" 2>/dev/null | tail -n 1)"
if [[ ! "${hash}" =~ ^[A-Za-z0-9+/=]{20,}$ ]]; then
  echo "rabbitmq entrypoint: could not hash the password" >&2
  exit 1
fi

install -d -m 0750 "${RUNTIME_DIR}"
user="${RABBITMQ_DEFAULT_USER}"
{
  printf '{\n'
  printf '  "users": [{"name": "%s", "password_hash": "%s", "hashing_algorithm": "rabbit_password_hashing_sha256", "tags": ["administrator"]}],\n' "${user}" "${hash}"
  printf '  "permissions": [{"user": "%s", "vhost": "/", "configure": ".*", "write": ".*", "read": ".*"}],\n' "${user}"
  # the committed file minus its opening brace
  tail -n +2 "${SOURCE}"
} > "${RUNTIME}"
chown -R rabbitmq:rabbitmq "${RUNTIME_DIR}"
chmod 0640 "${RUNTIME}"

exec docker-entrypoint.sh "$@"
