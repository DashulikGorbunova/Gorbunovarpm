# Лабораторная работа №1

## Индексы и EXPLAIN ANALYZE в PostgreSQL

**Проект:** Flower Shop API  
**Студент:** Gorbunova · группа K0709-23/3  
**СУБД:** PostgreSQL 16  

Все планы — реальный вывод `EXPLAIN` / `EXPLAIN ANALYZE`. Перед измерениями выполнялся `ANALYZE`.

| База | Таблица | Объём |
|------|--------|-------|
| Тестовая (часть 1) | `lab_orders` | **1 000 000** строк |
| Сервис (часть 2) | `shop_order` | **100 000** заказов, ~300k позиций, 5000 клиентов |

Воспроизведение тестовой части:

```bash
cd flower_shop/flower_shop
docker compose up -d
Get-Content -Raw .\scripts\lab01_setup.sql | docker compose exec -T db psql -U flower -d flower_shop
Get-Content -Raw .\scripts\lab01_experiments.sql | docker compose exec -T db psql -U flower -d flower_shop
```

Воспроизведение сервиса:

```bash
docker compose exec backend python manage.py seed_data --clear --customers 5000 --orders 100000
docker compose exec db psql -U flower -d flower_shop -c "ANALYZE shop_order; ANALYZE shop_orderitem;"
```

---

# Часть 1. Работа с тестовой базой

Таблица `lab_orders` по заданию: `user_id`, `product_id`, `status`, `amount`, `created_at`, `updated_at`.  
Распределение статусов почти равномерное (~250k на каждый).

---

## Задание 3. EXPLAIN

```sql
EXPLAIN SELECT * FROM lab_orders WHERE user_id = 123;
```

```
Gather
  -> Parallel Seq Scan on lab_orders
       Filter: (user_id = 123)
```

1. **План:** Parallel Seq Scan (через Gather).  
2. **Тип сканирования:** последовательное (параллельное).  
3. PostgreSQL читает всю таблицу и фильтрует `user_id = 123` — индекса ещё нет.

---

## Задание 4. EXPLAIN ANALYZE

```sql
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM lab_orders WHERE user_id = 123;
```

| Метрика | Значение |
|---------|----------|
| Scan | Parallel Seq Scan |
| actual rows | 5 |
| Rows Removed by Filter | ~333 332 на воркер |
| Planning Time | 0.093 ms |
| Execution Time | **436.883 ms** |

1. `EXPLAIN` — только оценка плана; `EXPLAIN ANALYZE` — реально выполняет запрос и пишет actual time/rows.  
2. ANALYZE дольше, потому что данные реально читаются с диска/из буферов.  
3. Estimated (~11 rows) vs actual (5) — статистика приблизительная; планировщик всё равно верно выбрал Seq Scan без индекса.

---

## Задание 5. Sequential Scan

| Запрос | Scan | Execution Time |
|--------|------|----------------|
| `SELECT * FROM lab_orders` | Seq Scan | 609.7 ms |
| `WHERE amount > 0` (~99.99% строк) | Seq Scan | 1191.8 ms |

1. Используется Sequential Scan.  
2. Нужны почти все строки — индекс только добавил бы random I/O.  
3. Seq Scan **не всегда плох**: при низкой селективности (большая доля таблицы) он дешевле Index Scan.

**Вывод:** Seq Scan выгоднее, когда результат — большая часть таблицы (десятки процентов и выше).

---

## Задание 6. Первый B-tree индекс

```sql
CREATE INDEX idx_orders_user_id ON lab_orders(user_id);
ANALYZE lab_orders;
```

| Метрика | До индекса | После индекса |
|---------|------------|---------------|
| Тип Scan | Parallel Seq Scan | Bitmap Index Scan → Bitmap Heap Scan |
| Execution Time | 436.883 ms | **0.341 ms** |
| Обработано строк | ~1M (фильтр) | 5 (по индексу) |
| Индекс | — | `idx_orders_user_id` |

1. План изменился.  
2. Индекс используется.  
3. Ускорение ~**1280×** (436 ms → 0.34 ms) — условие высокоселективное.

---

## Задание 7–8. Индекс и селективность

```sql
CREATE INDEX idx_orders_status ON lab_orders(status);
```

| status | COUNT | % таблицы | Scan | Execution Time |
|--------|------:|----------:|------|----------------|
| PAID | 249842 | ~25% | Bitmap Index/Heap | 243.7 ms |
| NEW | 249730 | ~25% | Bitmap Index/Heap | 225.2 ms |
| DELIVERED / CANCELLED | ~250k | ~25% | аналогично | — |

1. Индекс **используется** (Bitmap), но выгода небольшая: всё равно читается ~¼ таблицы.  
2. При ещё большей доле PostgreSQL может выбрать чистый Seq Scan — индекс дороже random reads.  
3. Чем больше строк возвращает условие, тем менее выгоден индекс.

**Вывод:** селективность — главный критерий. Индекс по `status` с 4 равномерными значениями почти бесполезен для `SELECT *`.

---

## Задание 9. Range query по `created_at`

| Диапазон | До индекса | После `idx_orders_created_at` |
|----------|------------|-------------------------------|
| 7 days (~9.5k строк) | Parallel Seq Scan, **662 ms** | Bitmap, **44 ms** |
| 1 day (~1.4k) | — | Bitmap, 15 ms |
| 1 month (~41k) | — | Bitmap, 144 ms |
| 1 year (~501k, ~50%) | — | **Seq Scan**, 1329 ms |

1. Не во всех диапазонах индекс используется.  
2. Чем шире диапазон, тем ближе план к Seq Scan.  
3. Индекс невыгоден, когда диапазон захватывает большую долю таблицы (~половина и больше).

---

## Задание 10. Bitmap Scan

```sql
EXPLAIN ANALYZE SELECT * FROM lab_orders WHERE amount BETWEEN 1000 AND 3000;
```

```
Bitmap Heap Scan on lab_orders
  -> Bitmap Index Scan on idx_orders_amount
Execution Time: 443.174 ms  (~200k rows)
```

1. **Bitmap Index Scan** — собирает bitmap tid’ов из индекса.  
2. **Bitmap Heap Scan** — читает кучу по bitmap (часто по порядку страниц).  
3. Bitmap выгоднее обычного Index Scan при «среднем» числе строк: меньше случайных прыжков по heap.

---

## Задание 11–12. Несколько индексов vs composite

```sql
WHERE user_id = 123 AND status = 'PAID'
```

| Вариант | План | Time |
|---------|------|------|
| Два отдельных индекса | Bitmap по `user_id` + Filter `status` (BitmapAnd **нет**) | 0.204 ms |
| `idx_orders_user_status (user_id, status)` | Index Scan, оба условия в Index Cond | **0.149 ms** |

1. В нашем случае PostgreSQL взял один селективный индекс (`user_id`), а не BitmapAnd.  
2. BitmapAnd появляется, когда оба индекса примерно одинаково полезны.  
3. Составной индекс эффективнее: оба предиката в Index Cond, нет Filter.  
4. Составной **не всегда** лучше двух отдельных: если запросы часто фильтруют только по второй колонке — left-prefix не сработает.

---

## Задание 13. Порядок колонок

Индексы: `(user_id, created_at)` и `(created_at, user_id)`.

| Запрос | Какой индекс | Почему |
|--------|--------------|--------|
| `WHERE user_id = 123` | `(user_id, created_at)` | left-most prefix |
| `WHERE user_id = 123 AND created_at > ...` | `(user_id, created_at)` | оба поля по порядку |
| `WHERE created_at > ...` (30 days) | `idx_orders_created_at`, **не** `(user_id, created_at)` | нельзя начать с второй колонки |

1. Порядок колонок = порядок ключа B-tree.  
2. `(a, b)` и `(b, a)` — разные структуры.  
3. Правило: сначала равенство с высокой селективностью, потом диапазон.

---

## Задание 14–15. ORDER BY и пагинация

```sql
SELECT * FROM lab_orders WHERE user_id = 123 ORDER BY created_at DESC LIMIT 20;
CREATE INDEX idx_orders_user_created_at_desc ON lab_orders(user_id, created_at DESC);
```

У `user_id = 123` всего **5 строк** — Sort остаётся (quicksort 25 kB), индекс используется для поиска, но убирать Sort при 5 строках планировщику незачем.

1. Sort не исчез — мало строк, сортировка дешевле перестройки плана.  
2. Индекс хранит ключи в порядке — при большом числе строк на user_id Sort пропадает (Index Scan + LIMIT).  
3. Порядок `(user_id, created_at DESC)` важен: сначала фильтр, потом направление сортировки.

Для API-пагинации индекс всё равно правильный: он масштабируется, когда у пользователя тысячи заказов.

---

## Задание 16. Index Only Scan

Обычный `SELECT id, user_id WHERE user_id = 123` → Bitmap Heap Scan (нужна куча).

С covering-индексом:

```sql
CREATE INDEX idx_orders_user_id_include
ON lab_orders(user_id) INCLUDE (id, status, created_at);
```

```
Index Only Scan using idx_orders_user_id_include
Heap Fetches: 0
Execution Time: 0.162 ms
```

1. Index Only Scan читает данные из индекса, не из heap (если visibility map позволяет).  
2. К таблице не ходят, когда все нужные колонки есть в индексе (+ INCLUDE).  
3. Условия: covering index, актуальной visibility map (`VACUUM`/`ANALYZE`).

---

## Задание 17. Partial Index

```sql
CREATE INDEX idx_orders_new ON lab_orders(created_at) WHERE status = 'NEW';
```

Запрос `WHERE status = 'NEW' ORDER BY created_at LIMIT 50` после создания идёт через `idx_orders_new`.

1. Partial меньше по размеру и быстрее обновляется — индексирует только нужный срез.  
2. Полезен для «горячих» статусов (`NEW`, `PENDING`).  
3. Один partial не покрывает все статусы — условие индекса должно совпадать с запросом.

---

## Задание 18. Expression Index

```sql
WHERE LOWER(email) = 'user1@example.com'
```

| Индекс | План | Time |
|--------|------|------|
| `ON users(email)` | Seq Scan (выражение ≠ ключ индекса) | **147.7 ms** |
| `ON users(LOWER(email))` | Index Scan | **0.112 ms** |

Обычный индекс не используется: в запросе `LOWER(email)`, а ключ — сырой `email`. Индекс должен совпадать с выражением в `WHERE`.

---

## Задание 19. Цена индексов на INSERT

Вставка **50 000** строк в `lab_insert_test`:

| Состояние | Время INSERT |
|-----------|--------------|
| Только PK | **962 ms** |
| + 4 дополнительных индекса | **1667 ms** (~+73%) |

1. INSERT замедлился.  
2. Каждая новая строка обновляет все индексы.  
3. Больше индексов → дороже запись.

---

## Задание 20. Статистика индексов

```sql
SELECT indexrelname, idx_scan FROM pg_stat_user_indexes
WHERE relname = 'lab_orders' ORDER BY idx_scan;
```

Чаще всего в эксперименте: `idx_orders_created_at`, `idx_orders_status`, составные по `user_id`.  
`idx_scan = 0` у `(created_at, user_id)` и ряда служебных — в этом прогоне не понадобились.

1. Часто использовались индексы под реальные WHERE.  
2. Нулевые `idx_scan` есть.  
3. `idx_scan = 0` ≠ автоматически бесполезен (мало запросов, статистика сброшена, редкий отчёт).  
4. Риск удаления: сломать редкий, но критичный запрос.

---

## Задание 21. Финальная оптимизация на тестовой базе

```sql
SELECT id, amount, status, created_at
FROM lab_orders
WHERE user_id = $1 AND status = 'PAID'
  AND created_at >= NOW() - INTERVAL '30 days'
ORDER BY created_at DESC LIMIT 50;
```

До: Index Scan `(user_id, created_at DESC)` + **Filter status**.  
После:

```sql
CREATE INDEX idx_orders_user_paid_created
ON lab_orders(user_id, created_at DESC) WHERE status = 'PAID';
```

План: Index Scan по partial без Filter по status.  
Порядок колонок: равенство `user_id` → диапазон/сортировка `created_at`; статус вынесен в `WHERE` индекса.

---

# Часть 2. Работа со своим сервисом

## Scaling Entity (задание 22)

| | |
|--|--|
| **Таблица** | `shop_order` (модель `Order`) |
| **Отвечает за** | заказы клиентов: статус, сумма, адрес, комментарий, `created_at` |
| **Почему растёт быстрее** | каждый заказ = новая строка; категории/товары почти статичны; клиенты растут медленнее |
| **Объём за год** | сотни тысяч — миллионы при живом магазине |
| **Поля фильтрации** | `customer_id`, `status`, `created_at` и комбинации |

Индексы добавлены миграцией `shop.0002_order_performance_indexes` (не руками в psql).

---

## Исследуемые запросы (задания 23–24)

Данные: 100 000 заказов. Клиент с максимумом заказов: `customer_id = 4276` (37 заказов).

Сравнение до/после: `migrate shop 0001` → замер → `migrate shop 0002` → `ANALYZE` → замер.

### Query 1 — лента заказов клиента

API: `GET /api/customers/{id}/orders/?ordering=-created_at`

```sql
SELECT * FROM shop_order
WHERE customer_id = 4276
ORDER BY created_at DESC LIMIT 20;
```

| Метрика | До (0001) | После (0002) |
|---------|-----------|--------------|
| Scan | Bitmap по FK `customer_id` + **Sort** | Bitmap по FK + **Sort** (при 37 строках) |
| Execution Time | 12.9 ms (cold) / ~0.4 ms (warm) | ~0.4 ms |
| Индекс | `shop_order_customer_id_*` | тот же; `idx_order_cust_created_desc` создан, но не выбран |

```sql
CREATE INDEX idx_order_cust_created_desc
ON shop_order (customer_id, created_at DESC);
```

**Честный вывод:** при 37 заказах Sort дешёвый (34 kB), планировщик не переключается на составной индекс. Индекс правильный под API и проявится, когда у клиента тысячи заказов (тогда Index Scan + LIMIT без Sort). Наличие индекса ≠ гарантия использования — тема лабы.

---

### Query 2 — статус + диапазон дат

API: `GET /api/orders/?status=PAID&from_date=...`

```sql
SELECT * FROM shop_order
WHERE status = 'PAID'
  AND created_at >= NOW() - INTERVAL '30 days';
```

| Метрика | До | После |
|---------|----|-------|
| Scan | Bitmap `idx_order_created_at` + Filter status | Bitmap `idx_order_status_created` |
| Rows Removed by Filter | **3226** | 0 |
| Heap Blocks | 2158 | **701** |
| Actual Rows | 824 | 824 |
| Execution Time | **100.4 ms** | **11.0 ms** (~9×) |

```sql
CREATE INDEX idx_order_status_created ON shop_order (status, created_at);
```

Здесь выигрыш явный: индекс сразу отбирает PAID за период.

---

### Query 3 — JOIN with-details

API: `GET /api/orders/with-details/?status=PAID`

```sql
SELECT ... FROM shop_order o
JOIN shop_customer / shop_orderitem / shop_product / shop_category ...
WHERE o.status = 'PAID' AND o.created_at >= NOW() - INTERVAL '30 days'
ORDER BY o.created_at DESC LIMIT 500;
```

После 0002 доступ к заказам: `Index Scan Backward using idx_order_status_created`.  
JOIN’ы идут по PK/FK. Отдельный «супер-индекс на 5 таблиц» не нужен — ускоряется входная выборка заказов.

---

## Анализ индексов сервиса (задание 25)

| Индекс | Колонки | Назначение |
|--------|---------|------------|
| `shop_order_pkey` | id | PK / JOIN |
| `shop_order_customer_id_*` | customer_id | FK, Query 1 |
| `idx_order_created_at` | created_at | диапазон только по дате |
| `idx_order_customer_status` | customer_id, status | фильтр клиента+статуса |
| `idx_order_cust_created_desc` | customer_id, created_at DESC | лента заказов |
| `idx_order_status_created` | status, created_at | Query 2 / 3 |
| `idx_order_new_created` | created_at WHERE status='NEW' | partial для новых |

Размеры при 100k заказов: таблица `shop_order` ≈ **20 MB**; btree-индексы суммарно порядка **12–18 MB**. Partial `idx_order_new_created` ≈ **456 kB** против ~3 MB полного индексa по дате.

---

## Проблемы и оптимизация (задания 26–29)

| Запрос | Проблема до | Решение | Результат |
|--------|-------------|---------|-----------|
| Q1 | Sort на ленте | `(customer_id, created_at DESC)` в миграции 0002 | готов к росту; на 37 строках Sort ещё есть |
| Q2 | Filter status после индекса по дате | `(status, created_at)` | 100 ms → 11 ms |
| Q3 | тот же Filter на заказах в JOIN | тот же составной | Index Scan Backward по точному условию |

Изменение воспроизводимо: `flower_shop/shop/migrations/0002_order_performance_indexes.py`.

---

## Задание 30. Почему нельзя индексировать всё

- индексы занимают диск и shared buffers (у нас индексы `shop_order` сопоставимы с размером таблицы);  
- каждый `INSERT`/`UPDATE`/`DELETE` обновляет все затронутые индексы (на тесте +73% к INSERT);  
- `UPDATE status` трогает `idx_order_status_created`, `idx_order_customer_status` и partial `NEW`;  
- планировщик перебирает больше вариантов → иногда Planning Time > Execution Time;  
- индекс без реального запроса — мёртвый вес (`idx_scan = 0`).

Индекс добавляли только под измеренный API-запрос и проверяли `EXPLAIN ANALYZE`.

---

## Итог

1. На 1M `lab_orders` показаны Seq Scan, Index/Bitmap Scan, влияние селективности, composite/partial/expression, цена INSERT.  
2. В сервисе scaling entity — `shop_order`; три реальных запроса; индексы в миграции 0002.  
3. Лучший измеримый эффект — Query 2: **~9×** за счёт `(status, created_at)`.  
4. Главный принцип: **сначала измерить, потом индексировать**; индекс без подтверждения планом не считается оптимизацией.
