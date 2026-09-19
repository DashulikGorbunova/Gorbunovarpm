# Flower Shop API

**Gorbunova · группа K0709-23/3**

Backend интернет-магазина цветов и букетов на **Django REST Framework + PostgreSQL**. Репозиторий содержит полный цикл лабораторных работ по масштабированию баз данных: индексы → рост данных → партиционирование → репликация (read scaling) → шардирование → анализ запросов после шардирования.

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-5.1-092E20?logo=django&logoColor=white)](https://www.djangoproject.com/)
[![DRF](https://img.shields.io/badge/DRF-3.15-A30000?logo=django&logoColor=white)](https://www.django-rest-framework.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)

## О проекте

Предметная область — магазин цветов. В БД минимум четыре связанные таблицы: категории, товары, клиенты, заказы и позиции заказа.

Лабораторная №1 (индексы и `EXPLAIN ANALYZE`): [`docs/lab-01-indexes.md`](docs/lab-01-indexes.md).  
Лабораторная №2 (рост данных, когда индексов недостаточно): [`docs/lab-02.md`](docs/lab-02.md).  
Лабораторная №3 (партиционирование PostgreSQL): [`docs/lab-03.md`](docs/lab-03.md).
Лабораторная №4 (масштабирование чтения, Primary + Replica): [`docs/lab-04.md`](docs/lab-04.md).  
Лабораторная №5 (шардирование, Router и Consistent Hashing): [`docs/lab-05.md`](docs/lab-05.md).  
Лабораторная №6 (запросы после шардирования: single-shard vs distributed): [`docs/lab-06.md`](docs/lab-06.md).

## Лабораторные работы

| № | Тема | Отчёт | Ключевые артефакты |
|---|------|-------|--------------------|
| 1 | Индексы и `EXPLAIN ANALYZE` | [lab-01](docs/lab-01-indexes.md) | `shop/migrations/0002_order_performance_indexes.py`, `scripts/lab01_*` |
| 2 | Рост данных: когда индексов недостаточно | [lab-02](docs/lab-02.md) | `scripts/lab02_part_a.sql`, `scripts/lab02_part_b.sql` |
| 3 | Партиционирование (RANGE / LIST / HASH) + job'ы и алерты | [lab-03](docs/lab-03.md) | `shop/partitions.py`, `shop/alerts.py`, команды `create_partitions` / `check_partitions` |
| 4 | Read scaling: Primary + Replica, streaming replication, lag | [lab-04](docs/lab-04.md) | `shop/replication.py`, `shop/db_router.py`, `docker/postgres/**` |
| 5 | Шардирование: `hash(key) % N` vs Consistent Hashing | [lab-05](docs/lab-05.md) | `shop/sharding.py`, `shop/shard_router.py`, команды `shard_setup` / `shard_load` / `shard_report` / `shard_experiment` |
| 6 | Запросы после шардирования: single-shard vs distributed | [lab-06](docs/lab-06.md) | `shop/distributed.py`, команда `shard_queries` |

## Структура репозитория

```
.
├── README.md                 # этот файл
├── docs/                     # отчёты по лабораторным работам 1–6
└── flower_shop/              # Django-проект
    ├── docker-compose.yml    # Primary + Replica + 3 шарда + backend
    ├── Dockerfile
    ├── requirements.txt
    ├── config/               # settings (подключения, роутеры), urls, wsgi
    ├── docker/postgres/      # pg_hba (primary) и entrypoint реплики
    ├── scripts/              # SQL/PS1 эксперименты и выгрузки результатов lab01–lab06
    └── shop/                 # приложение: модели, API, шардирование, партиции, репликация
        ├── models.py         # Category, Product, Customer, Order, OrderItem
        ├── views.py          # ViewSets + health + replication/shard endpoints
        ├── sharding.py       # ModuloSharding, ConsistentHashing, ShardRouter
        ├── shard_router.py   # доступ к шардам, Django-роутер
        ├── distributed.py    # single-shard / агрегации / top-N / cross-shard JOIN
        ├── db_router.py      # read → Replica, write → Primary
        ├── replication.py    # статус репликации и lag
        ├── partitions.py     # партиции shop_order и lab_events
        ├── alerts.py         # dedup-алерты (Telegram / email / webhook)
        ├── tests.py          # 35 тестов (роутеры, кольцо, merge-логика)
        └── management/commands/
            ├── seed_data.py            # генерация данных
            ├── create_partitions.py    # job создания партиций (лаб. 3)
            ├── check_partitions.py     # health-check + alert (лаб. 3)
            ├── replication_status.py   # состояние Primary/Replica (лаб. 4)
            ├── shard_setup.py          # схема на шардах (лаб. 5)
            ├── shard_load.py           # раскладка данных по шардам (лаб. 5)
            ├── shard_report.py         # распределение + баланс (лаб. 5)
            ├── shard_experiment.py     # modulo vs consistent при N→N+1 (лаб. 5)
            └── shard_queries.py        # анализ запросов после шардирования (лаб. 6)
```

## Стек

- Python 3.12
- Django 5.1 + Django REST Framework
- PostgreSQL 16
- Docker + Docker Compose
- drf-spectacular (Swagger / OpenAPI)
- django-filter
- Faker
- gunicorn

## Запуск

```bash
cd flower_shop
docker compose up --build
```

Одна команда поднимает PostgreSQL и backend.

С лабораторной №4 поднимаются **два** экземпляра PostgreSQL:
`db` (Primary, порт 5432) и `db_replica` (Replica, порт 5433), связанные
streaming replication. Backend читает каталог и заказы с Replica, а пишет
в Primary. Подробности — в [`docs/lab-04.md`](docs/lab-04.md).

С лабораторной №5 добавляются **три шарда** — `shard0` (6440), `shard1` (6441),
`shard2` (6442) с БД `flower_shop_shard`. Заказы распределяются по шардам по
ключу `customer_id` (`shop/sharding.py`, `shop/shard_router.py`).
Подробности — в [`docs/lab-05.md`](docs/lab-05.md).

| Сервис | Адрес |
|--------|--------|
| API | http://localhost:8000/api/ |
| Swagger | http://localhost:8000/api/docs/ |
| OpenAPI schema | http://localhost:8000/api/schema/ |
| Health | http://localhost:8000/health |
| Admin | http://localhost:8000/admin/ |

Логин администратора: `admin` / `admin123`

## Миграции

Файлы миграций лежат в репозитории:

- `flower_shop/shop/migrations/0001_initial.py` — таблицы, FK, базовые индексы
- `flower_shop/shop/migrations/0002_order_performance_indexes.py` — составные и partial-индексы лабораторной

При старте контейнера выполняется только `python manage.py migrate --noinput`.  
`makemigrations` при запуске **не** вызывается: схема должна воспроизводиться из git, а не генерироваться заново на каждой машине.

Небольшой набор данных (`seed_data --small`) создаётся сам, если таблица заказов пустая.

## Архитектура

```
Client / Swagger
  → Django REST Framework (ViewSet, pagination, filters)
    → Serializers
      → Models / ORM и raw SQL для JOIN
        → PostgreSQL 16
```

Код: `flower_shop/config` (настройки, URL), `flower_shop/shop` (модели, API, генератор данных).

## Схема БД

```
Category 1 ───────< Product
                      │
Customer 1 ───────< Order 1 ───────< OrderItem >─────── Product
```

| Таблица | Описание |
|---------|----------|
| `shop_category` | Категории |
| `shop_product` | Товары |
| `shop_customer` | Клиенты |
| `shop_order` | Заказы — scaling entity, есть `created_at` |
| `shop_orderitem` | Позиции, many-to-many Order ↔ Product |

Связи: one-to-many Category→Product, Customer→Order, Order→OrderItem; many-to-many Order↔Product через `OrderItem`.

## Scaling Entity

Основная растущая сущность — **`shop_order`**.

Хранит заказы. Растёт быстрее категорий, товаров и клиентов: каждый заказ клиента = новая строка, история обычно не удаляется. По ней фильтруют статус, дату и клиента — удобно для индексов, позже для партиционирования по `created_at` и шардирования.

## API

CRUD (GET list/detail, POST, PUT/PATCH, DELETE) есть у категорий, товаров, клиентов и заказов.

| Метод | URL | Описание |
|-------|-----|----------|
| GET/POST | `/api/categories/` | CRUD категорий |
| GET/POST | `/api/products/` | CRUD товаров, поиск и фильтры |
| GET/POST | `/api/customers/` | CRUD клиентов |
| GET/POST | `/api/orders/` | CRUD заказов, пагинация и фильтры |
| GET | `/api/customers/{id}/orders/` | Заказы клиента |
| GET | `/api/orders/{id}/items/` | Позиции заказа |
| GET | `/api/orders/with-details/` | JOIN №1: заказ → клиент → позиции → товар → категория |
| GET | `/api/orders/customer-products/?customer_id=` | JOIN №2: товары клиента |
| GET | `/api/orders/sales-by-category/` | Агрегация: COUNT / SUM / GROUP BY по категориям |
| GET | `/api/orders/read-demo/` | Read scaling: SELECT через Replica + replication lag (лаб. 4) |
| GET | `/api/replication/status/` | Primary / Replica, `pg_stat_replication`, lag (лаб. 4) |
| GET | `/api/shards/report/` | Распределение заказов по шардам + баланс (лаб. 5) |
| GET | `/api/shards/route/?customer_id=` | Какой шард обслуживает клиента + его заказы (лаб. 5) |
| GET | `/api/shards/plan/?from=&to=&samples=` | modulo vs consistent: объём переноса при N→N+k (лаб. 5) |
| GET | `/api/shards/distributed/?mode=` | Single-shard vs distributed: `count\|sum\|group\|top\|join\|hot\|summary` (лаб. 6) |
| GET | `/health` | Проверка процесса и подключения к PostgreSQL |
| GET | `/api/docs/` | Swagger UI |
| GET | `/api/schema/` | OpenAPI schema |

Pagination: `?page=1&page_size=20` (max 100).  
Filtering: `status`, `customer`, `from_date`, `to_date`, `min_amount`, `max_amount`.  
Sorting: `?ordering=-created_at`.

## Индексы

Важные индексы `shop_order` (описаны в миграциях, не создаются руками):

| Индекс | Колонки | Запрос |
|--------|---------|--------|
| `idx_order_cust_created_desc` | `(customer_id, created_at DESC)` | лента заказов клиента |
| `idx_order_status_created` | `(status, created_at)` | фильтр статуса и даты |
| `idx_order_new_created` | `created_at WHERE status='NEW'` | новые заказы |
| `idx_order_created_at` | `(created_at)` | диапазон только по дате |
| `idx_order_customer_status` | `(customer_id, status)` | фильтр клиента и статуса |

Разбор планов — в [`docs/lab-01-indexes.md`](docs/lab-01-indexes.md).

## Генерация данных

```bash
docker compose exec backend python manage.py seed_data --small
docker compose exec backend python manage.py seed_data --orders 100000 --customers 5000
docker compose exec backend python manage.py seed_data --orders 1000000 --customers 50000 --clear
```

| Параметр | Назначение |
|----------|------------|
| `--small` | ~20 клиентов, ~50 заказов |
| `--customers N` | число клиентов |
| `--orders N` | число заказов |
| `--clear` | очистить таблицы перед генерацией |

Данные создаются командой, без ручного POST через API.

## Полезные команды

### Репликация (лаба 4)

```bash
# Состояние репликации
docker compose exec backend python manage.py replication_status
docker compose exec db psql -U flower -d flower_shop -c "SELECT application_name, state, sync_state, sent_lsn, replay_lsn FROM pg_stat_replication"

# Replica read-only?
docker compose exec db_replica psql -U flower -d flower_shop -c "SELECT pg_is_in_recovery()"

# API
curl http://localhost:8000/api/replication/status/
curl http://localhost:8000/api/orders/read-demo/
```

### Шардирование (лаба 5)

```bash
# Схема на шардах
docker compose exec backend python manage.py shard_setup

# Разложить 100 000 заказов по шардам по ключу customer_id
docker compose exec backend python manage.py shard_load --orders 100000 --clear
docker compose exec backend python manage.py shard_report

# Сравнение modulo и consistent hashing при 3 -> 4 шардах
docker compose exec backend python manage.py shard_experiment --samples 100000

# Напрямую по шардам
docker compose exec shard0 psql -U flower -d flower_shop_shard -c "SELECT count(*) FROM shop_order_shard"

# API
curl http://localhost:8000/api/shards/report/
curl "http://localhost:8000/api/shards/route/?customer_id=1"
curl "http://localhost:8000/api/shards/plan/?samples=100000"
```

### Запросы после шардирования (лаба 6)

```bash
# single-shard / агрегации / top-N / cross-shard JOIN / hot shard
docker compose exec backend python manage.py shard_queries --customer-id 1 --limit 20

# API
curl "http://localhost:8000/api/shards/distributed/?mode=summary"
curl "http://localhost:8000/api/shards/distributed/?mode=count"
curl "http://localhost:8000/api/shards/distributed/?mode=top&limit=20"

# отказ шарда: часть клиентов недоступна, остальные работают
docker stop flower_shop_shard2
curl "http://localhost:8000/api/shards/distributed/?mode=count"
docker start flower_shop_shard2
```

```bash
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py createsuperuser
docker compose exec db psql -U flower -d flower_shop -c "EXPLAIN ANALYZE SELECT * FROM shop_order WHERE customer_id = 1 ORDER BY created_at DESC LIMIT 20;"
```
