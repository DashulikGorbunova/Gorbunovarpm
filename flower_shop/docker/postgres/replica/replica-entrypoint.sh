#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Flower Shop — Replica entrypoint (lab 04: Primary + Replica)
#
# First start:
#   1. wait until the Primary accepts connections;
#   2. take a physical base backup with pg_basebackup;
#   3. write primary_conninfo (host/port/user/password/application_name)
#      into postgresql.auto.conf;
#   4. create standby.signal so PostgreSQL boots as a hot standby.
# Later starts: it just boots the already-initialised hot standby.
#
# Started as `bash /usr/local/bin/replica-entrypoint.sh` (see docker-compose),
# so the executable bit is not required on Windows bind mounts.
# ---------------------------------------------------------------------------
set -Eeuo pipefail

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
PRIMARY_HOST="${PRIMARY_HOST:-db}"
PRIMARY_PORT="${PRIMARY_PORT:-5432}"
REPLICATION_USER="${REPLICATION_USER:-replicator}"
REPLICATION_PASSWORD="${REPLICATION_PASSWORD:-replicapass}"
POSTGRES_USER="${POSTGRES_USER:-flower}"
APPLICATION_NAME="${REPLICA_APPLICATION_NAME:-flower_shop_replica}"

mkdir -p "$PGDATA"
if [ "$(id -u)" = '0' ]; then
  find "$PGDATA" \! -user postgres -exec chown postgres '{}' + 2>/dev/null || true
fi

echo "[lab04][replica] waiting for Primary ${PRIMARY_HOST}:${PRIMARY_PORT} ..."
until pg_isready -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U "$POSTGRES_USER" >/dev/null 2>&1; do
  sleep 1
done
echo "[lab04][replica] Primary is accepting connections."

# Bootstrap fallback: when the Primary data directory already exists (e.g. the
# volume was created by labs 1-3), /docker-entrypoint-initdb.d is NOT executed,
# so the replication role may be missing. Ensure it exists before base backup.
echo "[lab04][replica] ensuring replication role '${REPLICATION_USER}' on Primary ..."
role_exists="$(
  gosu postgres env PGPASSWORD="$POSTGRES_PASSWORD" \
    psql -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -tA -c "SELECT 1 FROM pg_roles WHERE rolname = '${REPLICATION_USER}'" 2>/dev/null || true
)"
if [ "$role_exists" != "1" ]; then
  gosu postgres env PGPASSWORD="$POSTGRES_PASSWORD" \
    psql -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -v ON_ERROR_STOP=1 \
    -c "CREATE ROLE ${REPLICATION_USER} WITH REPLICATION LOGIN PASSWORD '${REPLICATION_PASSWORD}';"
  echo "[lab04][replica] replication role '${REPLICATION_USER}' created."
else
  echo "[lab04][replica] replication role '${REPLICATION_USER}' already exists."
fi

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "[lab04][replica] empty data directory -> running pg_basebackup"
  rm -rf "${PGDATA:?}/"*

  gosu postgres env PGPASSWORD="$REPLICATION_PASSWORD" pg_basebackup \
    -h "$PRIMARY_HOST" \
    -p "$PRIMARY_PORT" \
    -U "$REPLICATION_USER" \
    -D "$PGDATA" \
    -Fp -Xs -P -w

  {
    echo ""
    echo "# added by replica-entrypoint.sh (lab 04)"
    echo "primary_conninfo = 'host=$PRIMARY_HOST port=$PRIMARY_PORT user=$REPLICATION_USER password=$REPLICATION_PASSWORD application_name=$APPLICATION_NAME'"
  } >> "$PGDATA/postgresql.auto.conf"

  touch "$PGDATA/standby.signal"
  chown postgres:postgres "$PGDATA/postgresql.auto.conf" "$PGDATA/standby.signal"
  echo "[lab04][replica] base backup complete, standby.signal created."
else
  echo "[lab04][replica] data directory already initialised — keeping it."
  touch "$PGDATA/standby.signal"
  chown postgres:postgres "$PGDATA/standby.signal"
fi

echo "[lab04][replica] starting PostgreSQL as hot standby ..."
exec docker-entrypoint.sh postgres -c hot_standby=on
