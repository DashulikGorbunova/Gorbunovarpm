#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Primary init hook (lab 04): create the role used for streaming replication.
#
# The official postgres image runs every file from /docker-entrypoint-initdb.d
# once, while initialising an empty data directory. `*.sh` files are *sourced*,
# so the executable bit is not required (important on Windows bind mounts).
# ---------------------------------------------------------------------------
set -e

REPL_USER="${POSTGRES_REPLICATION_USER:-replicator}"
REPL_PASSWORD="${POSTGRES_REPLICATION_PASSWORD:-replicapass}"

echo "[lab04] ensuring replication role '${REPL_USER}' exists on Primary"

role_exists="$(
  psql -tA \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    -c "SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '${REPL_USER}'"
)"

if [ "$role_exists" != "1" ]; then
  psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    -c "CREATE ROLE ${REPL_USER} WITH REPLICATION LOGIN PASSWORD '${REPL_PASSWORD}';"
  echo "[lab04] replication role '${REPL_USER}' created"
else
  echo "[lab04] replication role '${REPL_USER}' already exists — skip"
fi
