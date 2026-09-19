-- ============================================================
-- Лабораторная работа №1 — подготовка тестовой таблицы
-- Выполнять в psql или через: docker compose exec db psql -U flower -d flower_shop -f ...
-- ============================================================

DROP TABLE IF EXISTS lab_orders;

CREATE TABLE lab_orders (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    product_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL,
    amount NUMERIC(10, 2) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

INSERT INTO lab_orders (user_id, product_id, status, amount, created_at, updated_at)
SELECT
    (random() * 100000)::BIGINT,
    (random() * 10000)::BIGINT,
    (ARRAY['NEW', 'PAID', 'DELIVERED', 'CANCELLED'])[floor(random() * 4 + 1)],
    (random() * 10000)::NUMERIC(10,2),
    NOW() - (random() * INTERVAL '2 years'),
    NOW()
FROM generate_series(1, 1000000);

ANALYZE lab_orders;

SELECT COUNT(*) AS total_rows FROM lab_orders;
SELECT status, COUNT(*) FROM lab_orders GROUP BY status;
