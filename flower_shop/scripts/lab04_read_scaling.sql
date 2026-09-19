-- ---------------------------------------------------------------------------
-- Lab 04 — read scaling: the real read scenario of the backend
-- (GET /api/orders/ -> SELECT ... FROM shop_order ORDER BY created_at DESC)
-- executed on the Replica.
--
-- Run on the Replica:
--   docker compose exec -T db_replica psql -U flower -d flower_shop -f - < scripts/lab04_read_scaling.sql
-- ---------------------------------------------------------------------------
\timing on

\echo '=== Lab 04 / READ SCALING: which node serves the read? ==='
SELECT pg_is_in_recovery() AS is_replica,
       inet_server_addr()  AS server_addr,
       inet_server_port()  AS server_port;

\echo '=== Lab 04 / READ SCALING: same query the list endpoint runs ==='
SELECT o.id, o.status, o.total_amount, o.created_at
FROM shop_order o
ORDER BY o.created_at DESC
LIMIT 20;

\echo '=== Lab 04 / READ SCALING: plan for that query ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT o.id, o.status, o.total_amount, o.created_at
FROM shop_order o
ORDER BY o.created_at DESC
LIMIT 20;
