# Flower Shop API

Backend-сервис цветочного магазина на **Django + Django REST Framework + PostgreSQL**.

Проект подготовлен для модуля по масштабированию баз данных (индексы, партиционирование, репликация, шардирование).

---

## О проекте

Предметная область — **интернет-магазин цветов и букетов**.

Основные сущности:
- Категории товаров
- Товары (цветы, букеты, композиции)
- Клиенты
- Заказы и позиции заказов

---

## Запуск

```bash
docker compose up --build
```

После старта:
- API: http://localhost:8000/api/
- Swagger: http://localhost:8000/api/docs/
- OpenAPI schema: http://localhost:8000/api/schema/
- Health: http://localhost:8000/health
- Admin: http://localhost:8000/admin/ (логин: `admin` / пароль: `admin123`)

Миграции лежат в репозитории (`shop/migrations/`) и применяются автоматически при старте контейнера (`migrate --noinput`). `makemigrations` при запуске не вызывается.

Небольшой набор тестовых данных создаётся при первом запуске, если заказов ещё нет.

Отчёт лабораторной №1: [`../docs/lab-01-indexes.md`](../docs/lab-01-indexes.md).

Отчёты: [лаб. №1](../docs/lab-01-indexes.md) · [лаб. №2](../docs/lab-02.md) ·
[лаб. №3](../docs/lab-03.md) · [лаб. №4 — Primary + Replica](../docs/lab-04.md) ·
[лаб. №5 — шардирование](../docs/lab-05.md) · [лаб. №6 — запросы после шардирования](../docs/lab-06.md).

С лабораторной №4 в окружении **два** PostgreSQL: `db` (Primary, 5432) и
`db_replica` (Replica, 5433). Запись идёт в Primary, часть SELECT’ов (заказы,
каталог) — в Replica через `shop.db_router.ReadReplicaRouter`.

С лабораторной №5 добавлены **три шарда** — `shard0` (6440), `shard1` (6441),
`shard2` (6442), БД `flower_shop_shard`. Заказы распределяются между ними по
shard key `customer_id`; роутер — `shop/sharding.py` (`ModuloSharding` /
`ConsistentHashing`), доступ — `shop/shard_router.py`.

---

## Архитектура

```
Client
  ↓
Backend API (Django REST Framework)
  ↓
Service / ViewSet layer
  ↓
Repository (Django ORM + raw SQL для сложных запросов)
  ↓
PostgreSQL
```

Слои разделены:
- **Views / ViewSets** — HTTP-слой
- **Serializers** — валидация и представление
- **Models** — работа с БД
- Сложные JOIN и агрегации вынесены в отдельные action-методы (удобно анализировать через `EXPLAIN ANALYZE`)

---

## Схема БД

```
Category 1 ───────< Product
                      │
                      │
Customer 1 ───────< Order 1 ───────< OrderItem >─────── Product
```

| Таблица        | Описание                                      |
|----------------|-----------------------------------------------|
| `shop_category`| Категории (розы, тюльпаны, букеты…)           |
| `shop_product` | Товары                                        |
| `shop_customer`| Клиенты                                       |
| `shop_order`   | **Заказы** (основная растущая сущность)       |
| `shop_orderitem`| Позиции заказа (many-to-many Order ↔ Product)|

Связи:
- one-to-many: Category → Product, Customer → Order, Order → OrderItem
- many-to-many: Order ↔ Product через `OrderItem`

---

## Основная сущность для масштабирования

```
Основная сущность для масштабирования: orders (таблица shop_order)
```

**Почему она подходит:**
- Количество заказов естественным образом растёт со временем
- На неё ссылаются позиции (`OrderItem`)
- По ней удобно делать фильтрацию по статусу, дате, клиенту
- Идеально подходит для экспериментов с индексами, партиционированием по `created_at` и шардированием

У `shop_order` есть `created_at` — обязательное поле для сортировки ленты и будущих партиций.

---

## Индексы

Индексы описаны в моделях и создаются миграциями, не руками в psql.

| Индекс | Колонки | Зачем |
|--------|---------|--------|
| `idx_order_cust_created_desc` | `(customer_id, created_at DESC)` | `GET /customers/{id}/orders?ordering=-created_at` |
| `idx_order_status_created` | `(status, created_at)` | фильтр `status` + диапазон дат |
| `idx_order_new_created` | `created_at WHERE status='NEW'` | очередь новых заказов |
| `idx_order_created_at` | `(created_at)` | фильтр только по дате |
| `idx_order_customer_status` | `(customer_id, status)` | `?customer=&status=` |
| `idx_product_name` | `(name)` | поиск товара |
| `idx_product_cat_active` | `(category_id, is_active)` | витрина категории |

Базовая схема — `0001_initial`, индексы лабораторной — `0002_order_performance_indexes`.

---

## Основные endpoint’ы

| Метод | URL | Описание |
|-------|-----|----------|
| GET/POST | `/api/categories/` | CRUD категорий |
| GET/POST | `/api/products/` | CRUD товаров (+ поиск, фильтры) |
| GET/POST | `/api/customers/` | CRUD клиентов |
| GET/POST | `/api/orders/` | CRUD заказов (+ pagination, filtering) |
| GET | `/api/customers/{id}/orders` | Заказы клиента |
| GET | `/api/orders/{id}/items` | Позиции заказа |
| GET | `/api/orders/with-details/` | JOIN-запрос №1 |
| GET | `/api/orders/customer-products/?customer_id=` | JOIN-запрос №2 |
| GET | `/api/orders/sales-by-category/` | Агрегирующий запрос |
| GET | `/api/orders/read-demo/` | Read scaling: SELECT через Replica + replication lag (лаб. 4) |
| GET | `/api/replication/status/` | Primary / Replica, `pg_stat_replication`, lag (лаб. 4) |
| GET | `/api/shards/report/` | Распределение заказов по шардам + баланс (лаб. 5) |
| GET | `/api/shards/route/?customer_id=` | Какой шард обслуживает клиента + его заказы (лаб. 5) |
| GET | `/api/shards/plan/` | modulo vs consistent: объём переноса при N→N+k (лаб. 5) |
| GET | `/api/shards/distributed/?mode=` | Single-shard vs distributed: `count\|sum\|group\|top\|join\|hot\|summary` (лаб. 6) |
| GET | `/health` | Health-check + проверка БД |
| GET | `/api/docs/` | Swagger UI |

### Pagination и Filtering

```
GET /api/orders/?page=1&page_size=20
GET /api/orders/?status=PAID
GET /api/orders/?from_date=2025-01-01&to_date=2026-01-01
GET /api/orders/?min_amount=1000&max_amount=5000
GET /api/products/?search=роза&category=1&min_price=500
GET /api/orders/?ordering=-created_at
```

---

## Сложные запросы

### 1. JOIN-запрос №1 — детали заказов

`GET /api/orders/with-details/?status=PAID&from=2025-01-01`

```sql
SELECT
    o.id AS order_id,
    o.status,
    o.total_amount,
    o.created_at,
    c.id AS customer_id,
    (c.first_name || ' ' || c.last_name) AS customer_name,
    c.email AS customer_email,
    p.id AS product_id,
    p.name AS product_name,
    cat.name AS category_name,
    oi.quantity,
    oi.price AS item_price
FROM shop_order o
JOIN shop_customer c ON c.id = o.customer_id
JOIN shop_orderitem oi ON oi.order_id = o.id
JOIN shop_product p ON p.id = oi.product_id
JOIN shop_category cat ON cat.id = p.category_id
WHERE ...
ORDER BY o.created_at DESC
```

### 2. JOIN-запрос №2 — товары конкретного клиента

`GET /api/orders/customer-products/?customer_id=1`

```sql
SELECT DISTINCT
    p.id AS product_id,
    p.name AS product_name,
    cat.name AS category_name,
    p.price,
    COUNT(oi.id) AS times_ordered,
    SUM(oi.quantity) AS total_quantity
FROM shop_order o
JOIN shop_orderitem oi ON oi.order_id = o.id
JOIN shop_product p ON p.id = oi.product_id
JOIN shop_category cat ON cat.id = p.category_id
WHERE o.customer_id = %s
GROUP BY p.id, p.name, cat.name, p.price
ORDER BY total_quantity DESC
```

### 3. Агрегирующий запрос — продажи по категориям

`GET /api/orders/sales-by-category/?from=2025-01-01`

Использует `COUNT`, `SUM`, `GROUP BY` по категориям.

---

## Генерация данных

### Небольшой набор (для проверки API)

```bash
docker compose exec backend python manage.py seed_data --small
```

### Большой объём (для тестов производительности)

```bash
# 100 000 заказов
docker compose exec backend python manage.py seed_data --orders 100000 --customers 5000

# 1 000 000 заказов (займёт время)
docker compose exec backend python manage.py seed_data --orders 1000000 --customers 50000 --clear
```

Параметры:
- `--small` — ~20 клиентов, ~50 заказов
- `--customers N` — количество клиентов
- `--orders N` — количество заказов
- `--clear` — очистить таблицы перед генерацией

Данные генерируются автоматически, без ручного добавления через API.

---

## Технологический стек

- Python 3.12
- Django 5.1 + Django REST Framework
- PostgreSQL 16
- Docker + Docker Compose
- drf-spectacular (Swagger)
- django-filter
- Faker (генерация данных)

---

## Полезные команды

```bash
# Применить миграции вручную
docker compose exec backend python manage.py migrate

# Создать суперпользователя
docker compose exec backend python manage.py createsuperuser

# Зайти в shell
docker compose exec backend python manage.py shell

# EXPLAIN ANALYZE пример
docker compose exec db psql -U flower -d flower_shop -c "EXPLAIN ANALYZE SELECT * FROM shop_order WHERE status = 'PAID';"
```

---

## Шардирование (лаба 5)

Заказы распределяются между тремя шардами по shard key `customer_id`:

```bash
# Схема shop_order_shard + индексы на каждом шарде
docker compose exec backend python manage.py shard_setup

# Разложить 100 000 заказов из Primary по шардам
docker compose exec backend python manage.py shard_load --orders 100000 --clear

# Сколько записей на каждом шарде
docker compose exec backend python manage.py shard_report

# Сравнение hash(key) % N и Consistent Hashing при 3 -> 4 шардах
docker compose exec backend python manage.py shard_experiment --samples 100000
```

| Команда | Назначение |
|---------|------------|
| `shard_setup` | создать таблицу `shop_order_shard`, индексы и `shard_meta` |
| `shard_load` | разложить N заказов по шардам (`--strategy modulo\|consistent`) |
| `shard_report` | распределение записей и клиентов по шардам + баланс |
| `shard_experiment` | процент перемещаемых данных при `N → N+1` для двух стратегий |
| `shard_queries` | анализ запросов после шардирования: single-shard, агрегации, top-N, join, hot shard (лаб. 6) |
