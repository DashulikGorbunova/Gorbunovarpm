-- Lab 03 parts 1–11: partitioning strategies (standalone demo tables)
\timing on
\echo '=== Lab 03: parts 1–11 ==='

DROP TABLE IF EXISTS events CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS user_events CASCADE;

-- ─── Part 1. RANGE by date ───────────────────────────────────────────────
\echo '=== Part 1: RANGE events by created_at ==='

CREATE TABLE events (
  id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  event_type VARCHAR(50) NOT NULL,
  payload TEXT,
  created_at TIMESTAMP NOT NULL
) PARTITION BY RANGE (created_at);

CREATE TABLE events_2026_09_09
  PARTITION OF events FOR VALUES FROM ('2026-09-09') TO ('2026-09-10');
CREATE TABLE events_2026_09_10
  PARTITION OF events FOR VALUES FROM ('2026-09-10') TO ('2026-09-11');
CREATE TABLE events_2026_09_11
  PARTITION OF events FOR VALUES FROM ('2026-09-11') TO ('2026-09-12');

INSERT INTO events (id, user_id, event_type, payload, created_at)
SELECT
  g,
  (g % 5000) + 1,
  (ARRAY['click', 'view', 'purchase', 'login'])[1 + (g % 4)],
  'payload-' || g,
  TIMESTAMP '2026-09-09 00:00:00'
    + ((g % 3) * INTERVAL '1 day')
    + ((g % 86400) * INTERVAL '1 second')
FROM generate_series(1, 30000) AS g;

\echo '--- rows per partition ---'
SELECT tableoid::regclass AS partition_name, COUNT(*)
FROM events
GROUP BY tableoid
ORDER BY partition_name;

\echo '--- boundary probes ---'
INSERT INTO events VALUES (900001, 1, 'click', 'boundary', '2026-09-10 12:00:00');
SELECT tableoid::regclass FROM events WHERE id = 900001;

INSERT INTO events VALUES (900002, 1, 'click', 'boundary', '2026-09-11 00:00:00');
SELECT tableoid::regclass FROM events WHERE id = 900002;

\echo '--- insert without partition (expect error) ---'
DO $$
BEGIN
  INSERT INTO events VALUES (900003, 1, 'click', 'no-part', '2026-09-12 00:00:00');
EXCEPTION WHEN others THEN
  RAISE NOTICE 'Expected error: %', SQLERRM;
END $$;

-- ─── Part 2. Partition pruning ───────────────────────────────────────────
\echo '=== Part 2: partition pruning ==='

EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*)
FROM events
WHERE created_at >= '2026-09-10'
  AND created_at < '2026-09-11';

EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*)
FROM events
WHERE event_type = 'click';

-- ─── Part 3. RANGE by numeric ────────────────────────────────────────────
\echo '=== Part 3: RANGE products by price ==='

CREATE TABLE products (
  id BIGINT NOT NULL,
  name TEXT NOT NULL,
  price NUMERIC NOT NULL
) PARTITION BY RANGE (price);

CREATE TABLE products_cheap
  PARTITION OF products FOR VALUES FROM (0) TO (100);
CREATE TABLE products_medium
  PARTITION OF products FOR VALUES FROM (100) TO (1000);
CREATE TABLE products_expensive
  PARTITION OF products FOR VALUES FROM (1000) TO (MAXVALUE);

INSERT INTO products VALUES
  (1, 'Rose stem', 45),
  (2, 'Tulip bunch', 180),
  (3, 'Premium bouquet', 2500),
  (4, 'Daisy pack', 99.99),
  (5, 'Orchid', 100),
  (6, 'Luxury set', 1000);

SELECT tableoid::regclass AS partition_name, id, name, price
FROM products
ORDER BY price;

EXPLAIN (ANALYZE, BUFFERS)
SELECT *
FROM products
WHERE price >= 100 AND price < 500;

-- ─── Part 4–5. LIST + DEFAULT ────────────────────────────────────────────
\echo '=== Part 4–5: LIST customers ==='

CREATE TABLE customers (
  id BIGINT NOT NULL,
  name TEXT NOT NULL,
  customer_type VARCHAR(30) NOT NULL
) PARTITION BY LIST (customer_type);

CREATE TABLE customers_b2c
  PARTITION OF customers FOR VALUES IN ('B2C');
CREATE TABLE customers_b2b
  PARTITION OF customers FOR VALUES IN ('B2B');
CREATE TABLE customers_enterprise
  PARTITION OF customers FOR VALUES IN ('Enterprise');

INSERT INTO customers VALUES
  (1, 'Anna', 'B2C'),
  (2, 'OOO Flowers', 'B2B'),
  (3, 'MegaCorp', 'Enterprise');

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM customers WHERE customer_type = 'B2B';

\echo '--- VIP without DEFAULT (expect error) ---'
DO $$
BEGIN
  INSERT INTO customers VALUES (100, 'Test User', 'VIP');
EXCEPTION WHEN others THEN
  RAISE NOTICE 'Expected error: %', SQLERRM;
END $$;

CREATE TABLE customers_default PARTITION OF customers DEFAULT;

INSERT INTO customers VALUES (100, 'Test User', 'VIP');
SELECT tableoid::regclass, * FROM customers WHERE id = 100;

-- ─── Part 6. HASH ────────────────────────────────────────────────────────
\echo '=== Part 6: HASH user_events ==='

CREATE TABLE user_events (
  id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  event_type VARCHAR(50),
  created_at TIMESTAMP NOT NULL
) PARTITION BY HASH (user_id);

CREATE TABLE user_events_0 PARTITION OF user_events FOR VALUES WITH (MODULUS 4, REMAINDER 0);
CREATE TABLE user_events_1 PARTITION OF user_events FOR VALUES WITH (MODULUS 4, REMAINDER 1);
CREATE TABLE user_events_2 PARTITION OF user_events FOR VALUES WITH (MODULUS 4, REMAINDER 2);
CREATE TABLE user_events_3 PARTITION OF user_events FOR VALUES WITH (MODULUS 4, REMAINDER 3);

INSERT INTO user_events (id, user_id, event_type, created_at)
SELECT
  g,
  (g % 100000) + 1,
  (ARRAY['click', 'view', 'purchase'])[1 + (g % 3)],
  TIMESTAMP '2026-01-01' + ((g % 200) * INTERVAL '1 day')
FROM generate_series(1, 200000) AS g;

SELECT tableoid::regclass AS partition_name, COUNT(*)
FROM user_events
GROUP BY tableoid
ORDER BY partition_name;

-- ─── Part 8. Partitioning + indexes ─────────────────────────────────────
\echo '=== Part 8: indexes on partitioned events ==='

CREATE INDEX idx_events_user_id ON events (user_id);

EXPLAIN (ANALYZE, BUFFERS)
SELECT *
FROM events
WHERE created_at >= '2026-09-10'
  AND created_at < '2026-09-11'
  AND user_id = 12345;

-- ─── Part 9. When partitioning does not help ─────────────────────────────
\echo '=== Part 9: filter not on partition key ==='

EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM events WHERE event_type = 'click';

CREATE INDEX idx_events_event_type ON events (event_type);

EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM events WHERE event_type = 'click';

\echo '=== Lab 03 parts 1–11 SQL done ==='
