-- ---------------------------------------------------------------------------
-- Lab 04 — PRIMARY side: streaming replication status + a write.
--
-- Run on the Primary:
--   docker compose exec -T db psql -U flower -d flower_shop -f - < scripts/lab04_primary.sql
-- ---------------------------------------------------------------------------
\timing on

\echo '=== Lab 04 / PRIMARY: replication-related settings ==='
SHOW wal_level;
SHOW max_wal_senders;
SHOW wal_keep_size;
SHOW hot_standby;

\echo '=== Lab 04 / PRIMARY: connected replicas (pg_stat_replication) ==='
SELECT application_name,
       client_addr,
       state,
       sync_state,
       sent_lsn,
       write_lsn,
       flush_lsn,
       replay_lsn,
       EXTRACT(EPOCH FROM (now() - reply_time)) AS reply_lag_seconds
FROM pg_stat_replication
ORDER BY application_name;

\echo '=== Lab 04 / PRIMARY: write a marker row ==='
INSERT INTO shop_customer (first_name, last_name, email, phone, address, created_at)
VALUES ('Lab04', 'Replica', 'lab04.replica@example.com', '+70000000000', 'Primary', NOW())
ON CONFLICT (email) DO UPDATE
    SET first_name = EXCLUDED.first_name,
        last_name  = EXCLUDED.last_name,
        address    = 'Primary (updated)'
RETURNING id, email, created_at;

\echo '=== Lab 04 / PRIMARY: current WAL position ==='
SELECT pg_current_wal_lsn() AS primary_current_lsn,
       NOW()                 AS primary_now;

\echo '=> Теперь выполните scripts/lab04_replica.sql на Replica и найдите lab04.replica@example.com'
