-- Lab 02 Part A: events growth experiments
-- Volumes: 10k, 100k, 1M, 5M

\timing off
\pset pager off

DROP TABLE IF EXISTS events CASCADE;

CREATE TABLE events (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  event_type VARCHAR(50) NOT NULL,
  payload JSONB,
  created_at TIMESTAMP NOT NULL
);

CREATE OR REPLACE FUNCTION lab02_fill_events(n bigint) RETURNS void AS $$
BEGIN
  INSERT INTO events (user_id, event_type, payload, created_at)
  SELECT
    (random() * 100000)::bigint,
    CASE
      WHEN random() < 0.4 THEN 'MESSAGE'
      WHEN random() < 0.7 THEN 'LOGIN'
      WHEN random() < 0.9 THEN 'PURCHASE'
      ELSE 'OTHER'
    END,
    '{}'::jsonb,
    NOW() - (random() * INTERVAL '365 days')
  FROM generate_series(1, n);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION lab02_report_size(label text) RETURNS void AS $$
BEGIN
  RAISE NOTICE 'SIZE|%|rows=%|table=%|total=%',
    label,
    (SELECT COUNT(*) FROM events),
    pg_size_pretty(pg_relation_size('events')),
    pg_size_pretty(pg_total_relation_size('events'));
END;
$$ LANGUAGE plpgsql;

-- ========== grow without secondary indexes ==========
\echo '=== FILL 10000 ==='
SELECT lab02_fill_events(10000);
ANALYZE events;
SELECT lab02_report_size('10k');

\echo '=== SELECT no index 10k ==='
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT * FROM events WHERE user_id = 123;

\echo '=== FILL to 100000 ==='
SELECT lab02_fill_events(90000);
ANALYZE events;
SELECT lab02_report_size('100k');

\echo '=== SELECT no index 100k ==='
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT * FROM events WHERE user_id = 123;

\echo '=== SELECT WITH index 100k ==='
CREATE INDEX idx_events_user_id ON events(user_id);
ANALYZE events;
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT * FROM events WHERE user_id = 123;
DROP INDEX idx_events_user_id;

\echo '=== FILL to 1000000 ==='
SELECT lab02_fill_events(900000);
ANALYZE events;
SELECT lab02_report_size('1m');

\echo '=== SELECT no index 1m ==='
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT * FROM events WHERE user_id = 123;

\echo '=== SELECT WITH index 1m ==='
CREATE INDEX idx_events_user_id ON events(user_id);
ANALYZE events;
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT * FROM events WHERE user_id = 123;
DROP INDEX idx_events_user_id;

\echo '=== FILL to 5000000 ==='
SELECT lab02_fill_events(4000000);
ANALYZE events;
SELECT lab02_report_size('5m');

\echo '=== SELECT no index 5m ==='
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT * FROM events WHERE user_id = 123;

\echo '=== SELECT WITH index 5m ==='
CREATE INDEX idx_events_user_id ON events(user_id);
ANALYZE events;
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT * FROM events WHERE user_id = 123;

\echo '=== RANGE 1 day BEFORE created_at index ==='
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT * FROM events WHERE created_at >= NOW() - INTERVAL '1 day';

CREATE INDEX idx_events_created_at ON events(created_at);
ANALYZE events;

\echo '=== RANGE 1 day AFTER created_at index ==='
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT * FROM events WHERE created_at >= NOW() - INTERVAL '1 day';

\echo '=== SORT/LIMIT before composite ==='
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT * FROM events
WHERE user_id = 123
ORDER BY created_at DESC
LIMIT 100;

CREATE INDEX idx_events_user_created ON events(user_id, created_at DESC);
ANALYZE events;

\echo '=== SORT/LIMIT after composite ==='
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT * FROM events
WHERE user_id = 123
ORDER BY created_at DESC
LIMIT 100;

\echo '=== AGGREGATION 30 days ==='
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT event_type, COUNT(*)
FROM events
WHERE created_at >= NOW() - INTERVAL '30 days'
GROUP BY event_type;

\echo '=== INDEX SIZES at 5m ==='
SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
WHERE relname = 'events'
ORDER BY pg_relation_size(indexrelid) DESC;

\echo '=== INSERT cost WITH indexes (100k) ==='
\timing on
INSERT INTO events (user_id, event_type, payload, created_at)
SELECT
  (random() * 100000)::bigint,
  'MESSAGE',
  '{}'::jsonb,
  NOW()
FROM generate_series(1, 100000);
\timing off

\echo '=== INSERT cost WITHOUT secondary indexes (100k) ==='
DROP INDEX idx_events_user_id;
DROP INDEX idx_events_created_at;
DROP INDEX idx_events_user_created;
\timing on
INSERT INTO events (user_id, event_type, payload, created_at)
SELECT
  (random() * 100000)::bigint,
  'MESSAGE',
  '{}'::jsonb,
  NOW()
FROM generate_series(1, 100000);
\timing off

-- restore indexes for final state / sizes
CREATE INDEX idx_events_user_id ON events(user_id);
CREATE INDEX idx_events_created_at ON events(created_at);
CREATE INDEX idx_events_user_created ON events(user_id, created_at DESC);
ANALYZE events;

\echo '=== FINAL SIZE ==='
SELECT lab02_report_size('final');
SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
WHERE relname = 'events'
ORDER BY 1;

\echo '=== DONE PART A ==='
