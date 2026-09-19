-- EXPLAIN plans for real Flower Shop API-style queries after partitioning.
\timing on
\echo '=== Lab 03: shop_order query plans ==='

-- Q1: date range (API from_date / to_date) — should prune
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, status, total_amount, created_at
FROM shop_order
WHERE created_at >= DATE '2026-08-01'
  AND created_at < DATE '2026-09-01'
ORDER BY created_at DESC
LIMIT 50;

-- Q2: status + date (API ?status=PAID&from_date=) — prune + index
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, status, total_amount, created_at
FROM shop_order
WHERE status = 'PAID'
  AND created_at >= NOW() - INTERVAL '30 days'
ORDER BY created_at DESC
LIMIT 50;

-- Q3: customer feed — may touch many partitions (no created_at predicate)
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, status, total_amount, created_at
FROM shop_order
WHERE customer_id = (
  SELECT customer_id FROM shop_order
  GROUP BY customer_id ORDER BY COUNT(*) DESC LIMIT 1
)
ORDER BY created_at DESC
LIMIT 50;

-- Q4: lookup by id only — no prune (id not partition key)
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM shop_order WHERE id = 1;

\echo '=== done ==='
