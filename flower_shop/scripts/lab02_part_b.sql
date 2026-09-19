-- Lab 02 Part B: scale shop_order to 100k / 1M / 5M and measure API-like queries
-- Assumes customers already exist. Bulk-inserts orders via generate_series.

\pset pager off

\echo '=== CURRENT ORDERS ==='
SELECT COUNT(*) AS orders FROM shop_order;
SELECT COUNT(*) AS customers FROM shop_customer;

-- Ensure enough customers for FK
DO $$
DECLARE
  need int;
  i int;
BEGIN
  need := 5000 - (SELECT COUNT(*)::int FROM shop_customer);
  IF need > 0 THEN
    FOR i IN 1..need LOOP
      INSERT INTO shop_customer (first_name, last_name, email, phone, address, created_at)
      VALUES (
        'Cust', i::text, 'lab02_' || i || '@example.com', '', '', NOW()
      );
    END LOOP;
  END IF;
END $$;

CREATE OR REPLACE FUNCTION lab02_fill_orders(n bigint) RETURNS void AS $$
DECLARE
  cust_max int;
BEGIN
  SELECT COUNT(*)::int INTO cust_max FROM shop_customer;
  INSERT INTO shop_order (status, total_amount, delivery_address, comment, created_at, updated_at, customer_id)
  SELECT
    (ARRAY['NEW','PAID','PROCESSING','DELIVERED','CANCELLED'])[1 + (random()*4)::int],
    (random() * 10000)::numeric(12,2),
    '',
    '',
    NOW() - (random() * INTERVAL '365 days'),
    NOW(),
    (1 + (random() * (cust_max - 1)))::int
  FROM generate_series(1, n);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION lab02_measure_queries(label text) RETURNS void AS $$
DECLARE
  cid int;
BEGIN
  RAISE NOTICE '======= VOLUME % =======', label;
  RAISE NOTICE 'SIZE|%|rows=%|table=%|total=%',
    label,
    (SELECT COUNT(*) FROM shop_order),
    pg_size_pretty(pg_relation_size('shop_order')),
    pg_size_pretty(pg_total_relation_size('shop_order'));

  SELECT customer_id INTO cid
  FROM shop_order
  GROUP BY customer_id
  ORDER BY COUNT(*) DESC
  LIMIT 1;

  RAISE NOTICE 'TOP_CUSTOMER=%', cid;
END;
$$ LANGUAGE plpgsql;

-- Start clean for controlled volumes: keep customers/products, wipe orders
TRUNCATE shop_orderitem, shop_order RESTART IDENTITY CASCADE;

\echo '=== BUILD 100k ==='
SELECT lab02_fill_orders(100000);
ANALYZE shop_order;

\echo '=== SIZE 100k ==='
SELECT COUNT(*) AS rows, pg_size_pretty(pg_relation_size('shop_order')) AS table_size,
       pg_size_pretty(pg_total_relation_size('shop_order')) AS total_size
FROM shop_order;

\echo '=== Q1 FK lookup 100k ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM shop_order WHERE customer_id = (
  SELECT customer_id FROM shop_order GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1
);

\echo '=== Q2 date range 100k ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM shop_order WHERE created_at >= NOW() - INTERVAL '7 days';

\echo '=== Q3 filter+sort+limit 100k ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM shop_order
WHERE customer_id = (SELECT customer_id FROM shop_order GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1)
ORDER BY created_at DESC LIMIT 50;

\echo '=== Q2b status+date 100k ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM shop_order
WHERE status = 'PAID' AND created_at >= NOW() - INTERVAL '30 days';

\echo '=== BUILD to 1M ==='
SELECT lab02_fill_orders(900000);
ANALYZE shop_order;

\echo '=== SIZE 1m ==='
SELECT COUNT(*) AS rows, pg_size_pretty(pg_relation_size('shop_order')) AS table_size,
       pg_size_pretty(pg_total_relation_size('shop_order')) AS total_size
FROM shop_order;

\echo '=== Q1 FK lookup 1m ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM shop_order WHERE customer_id = (
  SELECT customer_id FROM shop_order GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1
);

\echo '=== Q2 date range 1m ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM shop_order WHERE created_at >= NOW() - INTERVAL '7 days';

\echo '=== Q3 filter+sort+limit 1m ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM shop_order
WHERE customer_id = (SELECT customer_id FROM shop_order GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1)
ORDER BY created_at DESC LIMIT 50;

\echo '=== Q2b status+date 1m ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM shop_order
WHERE status = 'PAID' AND created_at >= NOW() - INTERVAL '30 days';

\echo '=== BUILD to 5M ==='
SELECT lab02_fill_orders(4000000);
ANALYZE shop_order;

\echo '=== SIZE 5m ==='
SELECT COUNT(*) AS rows, pg_size_pretty(pg_relation_size('shop_order')) AS table_size,
       pg_size_pretty(pg_total_relation_size('shop_order')) AS total_size
FROM shop_order;

\echo '=== Q1 FK lookup 5m ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM shop_order WHERE customer_id = (
  SELECT customer_id FROM shop_order GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1
);

\echo '=== Q2 date range 5m ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM shop_order WHERE created_at >= NOW() - INTERVAL '7 days';

\echo '=== Q3 filter+sort+limit 5m ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM shop_order
WHERE customer_id = (SELECT customer_id FROM shop_order GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1)
ORDER BY created_at DESC LIMIT 50;

\echo '=== Q2b status+date 5m (bottleneck candidate) ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM shop_order
WHERE status = 'PAID' AND created_at >= NOW() - INTERVAL '30 days';

\echo '=== INDEX SIZES shop_order 5m ==='
SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
WHERE relname = 'shop_order'
ORDER BY pg_relation_size(indexrelid) DESC;

\echo '=== DONE PART B ==='
