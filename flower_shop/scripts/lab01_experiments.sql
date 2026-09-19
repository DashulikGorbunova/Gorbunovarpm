-- Lab 01 experiments on lab_orders (1M rows)
-- Results go to stdout for the report

\echo '=== Z3 EXPLAIN user_id=123 ==='
EXPLAIN SELECT * FROM lab_orders WHERE user_id = 123;

\echo '=== Z4 EXPLAIN ANALYZE user_id=123 ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE user_id = 123;

\echo '=== Z5 Seq Scan full ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders;

\echo '=== Z5 Seq Scan amount>0 ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE amount > 0;

\echo '=== Z6 CREATE idx_orders_user_id ==='
CREATE INDEX idx_orders_user_id ON lab_orders(user_id);
ANALYZE lab_orders;

\echo '=== Z6 AFTER index ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE user_id = 123;

\echo '=== Z7 CREATE idx_orders_status ==='
CREATE INDEX idx_orders_status ON lab_orders(status);
ANALYZE lab_orders;

\echo '=== Z7 status=PAID ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE status = 'PAID';

\echo '=== Z7 status=NEW ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE status = 'NEW';

\echo '=== Z8 counts ==='
SELECT status, COUNT(*) FROM lab_orders GROUP BY status ORDER BY 1;

\echo '=== Z9 range 7 days BEFORE created_at index ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE created_at > NOW() - INTERVAL '7 days';

CREATE INDEX idx_orders_created_at ON lab_orders(created_at);
ANALYZE lab_orders;

\echo '=== Z9 range 7 days AFTER ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE created_at > NOW() - INTERVAL '7 days';

\echo '=== Z9 range 1 day ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE created_at > NOW() - INTERVAL '1 day';

\echo '=== Z9 range 1 month ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE created_at > NOW() - INTERVAL '1 month';

\echo '=== Z9 range 1 year ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE created_at > NOW() - INTERVAL '1 year';

\echo '=== Z10 Bitmap amount BETWEEN ==='
CREATE INDEX IF NOT EXISTS idx_orders_amount ON lab_orders(amount);
ANALYZE lab_orders;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE amount BETWEEN 1000 AND 3000;

\echo '=== Z11 two indexes AND ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE user_id = 123 AND status = 'PAID';

\echo '=== Z12 composite ==='
CREATE INDEX idx_orders_user_status ON lab_orders(user_id, status);
ANALYZE lab_orders;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE user_id = 123 AND status = 'PAID';

\echo '=== Z13 order of columns ==='
CREATE INDEX idx_orders_user_created_at ON lab_orders(user_id, created_at);
CREATE INDEX idx_orders_created_at_user ON lab_orders(created_at, user_id);
ANALYZE lab_orders;

\echo '=== Z13 Q1 user_id only ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE user_id = 123;

\echo '=== Z13 Q2 user_id + created_at ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE user_id = 123 AND created_at > NOW() - INTERVAL '30 days';

\echo '=== Z13 Q3 created_at only ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE created_at > NOW() - INTERVAL '30 days';

\echo '=== Z14 ORDER BY before desc index ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE user_id = 123 ORDER BY created_at DESC;

CREATE INDEX idx_orders_user_created_at_desc ON lab_orders(user_id, created_at DESC);
ANALYZE lab_orders;

\echo '=== Z14 ORDER BY after desc index ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE user_id = 123 ORDER BY created_at DESC;

\echo '=== Z15 pagination ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE user_id = 123 ORDER BY created_at DESC LIMIT 20;

\echo '=== Z16 Index Only Scan ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT id, user_id FROM lab_orders WHERE user_id = 123;

\echo '=== Z16 INCLUDE ==='
CREATE INDEX idx_orders_user_id_include ON lab_orders(user_id) INCLUDE (id, status, created_at);
ANALYZE lab_orders;
EXPLAIN (ANALYZE, BUFFERS) SELECT id, user_id, status, created_at FROM lab_orders WHERE user_id = 123;

\echo '=== Z17 partial BEFORE ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE status = 'NEW' ORDER BY created_at LIMIT 50;

CREATE INDEX idx_orders_new ON lab_orders(created_at) WHERE status = 'NEW';
ANALYZE lab_orders;

\echo '=== Z17 partial AFTER ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE status = 'NEW' ORDER BY created_at LIMIT 50;

\echo '=== Z18 expression index ==='
DROP TABLE IF EXISTS lab_users CASCADE;
CREATE TABLE lab_users (
  id BIGSERIAL PRIMARY KEY,
  email VARCHAR(255) NOT NULL
);
INSERT INTO lab_users (email)
SELECT 'User' || g || '@Example.COM' FROM generate_series(1, 100000) g;
CREATE INDEX idx_users_email ON lab_users(email);
ANALYZE lab_users;

\echo '=== Z18 LOWER without expr index ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_users WHERE LOWER(email) = 'user1@example.com';

CREATE INDEX idx_users_lower_email ON lab_users(LOWER(email));
ANALYZE lab_users;

\echo '=== Z18 LOWER with expr index ==='
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_users WHERE LOWER(email) = 'user1@example.com';

\echo '=== Z19 INSERT cost ==='
DROP TABLE IF EXISTS lab_insert_test CASCADE;
CREATE TABLE lab_insert_test (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  status VARCHAR(20) NOT NULL,
  amount NUMERIC(10,2) NOT NULL,
  created_at TIMESTAMP NOT NULL
);
\timing on
INSERT INTO lab_insert_test (user_id, status, amount, created_at)
SELECT (random()*1000)::bigint, 'NEW', random()*100, NOW() FROM generate_series(1, 50000);
\timing off
CREATE INDEX idx_it_user ON lab_insert_test(user_id);
CREATE INDEX idx_it_status ON lab_insert_test(status);
CREATE INDEX idx_it_created ON lab_insert_test(created_at);
CREATE INDEX idx_it_user_status ON lab_insert_test(user_id, status);
\timing on
INSERT INTO lab_insert_test (user_id, status, amount, created_at)
SELECT (random()*1000)::bigint, 'NEW', random()*100, NOW() FROM generate_series(1, 50000);
\timing off

\echo '=== Z20 index usage ==='
SELECT schemaname, relname, indexrelname, idx_scan
FROM pg_stat_user_indexes
WHERE relname IN ('lab_orders', 'lab_users', 'lab_insert_test')
ORDER BY idx_scan;

\echo '=== Z21 final query BEFORE specialized index ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, amount, status, created_at
FROM lab_orders
WHERE user_id = 123
  AND status = 'PAID'
  AND created_at >= NOW() - INTERVAL '30 days'
ORDER BY created_at DESC
LIMIT 50;

CREATE INDEX idx_orders_user_paid_created ON lab_orders(user_id, created_at DESC)
WHERE status = 'PAID';
ANALYZE lab_orders;

\echo '=== Z21 final query AFTER ==='
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, amount, status, created_at
FROM lab_orders
WHERE user_id = 123
  AND status = 'PAID'
  AND created_at >= NOW() - INTERVAL '30 days'
ORDER BY created_at DESC
LIMIT 50;
