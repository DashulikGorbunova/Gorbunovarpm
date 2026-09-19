-- ---------------------------------------------------------------------------
-- Lab 04 — REPLICA side: prove the Primary's write is visible, and that the
-- Replica is read-only.
--
-- Run on the Replica:
--   docker compose exec -T db_replica psql -U flower -d flower_shop -f - < scripts/lab04_replica.sql
-- ---------------------------------------------------------------------------
\timing on

\echo '=== Lab 04 / REPLICA: this node is a hot standby (read-only) ==='
SELECT pg_is_in_recovery() AS is_in_recovery,
       inet_server_addr()  AS server_addr,
       inet_server_port()  AS server_port,
       current_user;

\echo '=== Lab 04 / REPLICA: the marker row written on PRIMARY ==='
SELECT id, first_name, last_name, email, address, created_at
FROM shop_customer
WHERE email = 'lab04.replica@example.com';

\echo '=== Lab 04 / REPLICA: replay position and lag ==='
SELECT pg_last_wal_replay_lsn()          AS replay_lsn,
       pg_last_xact_replay_timestamp()   AS last_replay_ts,
       now() - pg_last_xact_replay_timestamp() AS replay_lag;

\echo '=== Lab 04 / REPLICA: attempt to WRITE (expected to FAIL) ==='
INSERT INTO shop_customer (first_name, last_name, email, phone, address, created_at)
VALUES ('Should', 'Fail', 'should.fail@example.com', '', '', NOW());

\echo '=> Ожидаем: ERROR: cannot execute INSERT in a read-only transaction'
