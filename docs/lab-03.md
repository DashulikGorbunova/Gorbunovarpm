# Лабораторная работа №3

## Партиционирование PostgreSQL: разделение больших таблиц и автоматизация

**Проект:** Flower Shop API  
**Студент:** Gorbunova · группа K0709-23/3  
**СУБД:** PostgreSQL 16  

Цель: RANGE / LIST / HASH, partition pruning, индексы на партициях, job создания партиций, health-check и alert с recovery.

Скрипты: `flower_shop/scripts/lab03_*.sql`.  
Job’ы: `create_partitions`, `check_partitions`.

---

# Часть 1. RANGE по дате (`events`)

Таблица `events` партиционирована по `created_at` (день):

| Партиция | Диапазон |
|----------|----------|
| `events_2026_09_09` | [09, 10) |
| `events_2026_09_10` | [10, 11) |
| `events_2026_09_11` | [11, 12) |

По ~10 000 строк в каждой.

### Ответы

1. `created_at = '2026-09-10 12:00:00'` → **`events_2026_09_10`** (проверено через `tableoid`).
2. `created_at = '2026-09-11 00:00:00'` → **`events_2026_09_11`** (граница `FROM` включительно).
3. Вставка за `2026-09-12` → ошибка: `no partition of relation "events" found for row`.
4. Граница **`TO` исключающая** (`[FROM, TO)`): иначе соседние партиции пересекались бы в точке стыка.

---

# Часть 2. Partition pruning

Запрос с фильтром по `created_at` за 10 сентября:

```
Seq Scan on events_2026_09_10
Execution Time: ~6.9 ms
```

- Проверена **1** партиция (`events_2026_09_10`).
- Остальные исключены на этапе планирования — **partition pruning**.
- В плане нет `Append` по трём детям, только нужный child.

Запрос `WHERE event_type = 'click'` (ключ партиционирования не в условии):

```
Append → Seq Scan на events_2026_09_09 / _10 / _11
Execution Time: ~23.7 ms
```

Почему все партиции: pruning работает только когда предикат позволяет доказать, что child не пересекается с условием. Фильтр по `event_type` такого доказательства не даёт.

---

# Часть 3. RANGE по цене (`products`)

| Партиция | Диапазон |
|----------|----------|
| `products_cheap` | [0, 100) |
| `products_medium` | [100, 1000) |
| `products_expensive` | [1000, MAXVALUE) |

`price >= 100 AND price < 500` → план только **`products_medium`** (cheap и expensive отсечены pruning’ом).

---

# Часть 4. LIST (`customers`)

Партиции: `B2C` / `B2B` / `Enterprise`.

`WHERE customer_type = 'B2B'` → только **`customers_b2b`**.

Отличие от RANGE: дискретное множество значений, а не непрерывный интервал. Pruning по равенству/IN, а не по диапазону.

---

# Часть 5. DEFAULT partition

Без DEFAULT вставка `VIP` падает: `no partition ... found for row`.  
После `PARTITION OF customers DEFAULT` строка уходит в **`customers_default`**.

1. DEFAULT ловит значения вне списка.
2. Полезно как страховка от падений INSERT.
3. Риск: DEFAULT раздувается «мусором», pruning хуже, забывают завести нормальные партиции для новых типов.

---

# Часть 6. HASH (`user_events`, 200 000 строк)

| Партиция | Строк |
|----------|------:|
| `user_events_0` | 50 252 |
| `user_events_1` | 49 956 |
| `user_events_2` | 49 942 |
| `user_events_3` | 49 850 |

1. Распределение почти равномерное (~±0.5%).
2. HASH полезен, когда нет естественного диапазона, а нужна равномерная нагрузка по дискам/файлам.
3. RANGE режет по интервалу ключа; HASH — по остатку от хеша.
4. Удалить «старше 3 лет» по HASH нельзя одним `DROP PARTITION`: строки разных лет перемешаны по бакетам.

---

# Часть 7. Выбор стратегии

| Сценарий | Стратегия | Почему |
|----------|-----------|--------|
| A. Миллионы событий/день, удалять старше 3 лет | **RANGE по дате** | Старые партиции снимаются `DROP TABLE` / `DETACH` без массового DELETE |
| B. B2C / B2B / Enterprise | **LIST** | Мало категорий, запросы фильтруют по типу |
| C. Равномерно по `user_id` | **HASH** | Нет естественного диапазона, нужна балансировка |
| D. Аналитика `created_at BETWEEN` | **RANGE по дате** | Pruning по диапазону дат |
| E. Платежи по странам EE/LV/LT/FI/SE | **LIST** | Фиксированный набор стран |

---

# Часть 8. Партиционирование + индексы

`CREATE INDEX idx_events_user_id ON events (user_id)` создаёт индекс **на каждой партиции**.

Запрос с датой + `user_id = 12345`:

- pruning → только `events_2026_09_10`;
- внутри — **Bitmap Index Scan** на `events_2026_09_10_user_id_idx`;
- время ~**0.28 ms**.

Комбинация сильнее по отдельности: pruning сужает объём, индекс ускоряет lookup внутри оставшейся партиции.

---

# Часть 9. Когда partitioning не помогает

`WHERE event_type = 'click'` без индекса по типу: Append + Seq Scan по **всем** трём партициям (~17–24 ms).

После `CREATE INDEX ON events (event_type)`: Bitmap Index Scan на каждой партиции, ~**6 ms**.

**Вывод:** partitioning отвечает на «какой срез таблицы читать», индекс — на «как быстро найти строки внутри среза». Без предиката по partition key pruning не срабатывает.

---

# Часть 10–11. CreatePartitionsJob и PartitionHealthCheck

Реализация (Django management commands):

```bash
# создать недостающие партиции
docker compose exec backend python manage.py create_partitions --table lab_events
docker compose exec backend python manage.py create_partitions --table shop_order

# проверка горизонта + alert
docker compose exec backend python manage.py check_partitions --table lab_events
docker compose exec backend python manage.py check_partitions --table shop_order
```

### `lab_events` (демо из методички, дневные партиции)

- Горизонт: **сегодня + 3 дня**.
- Job идемпотентна: не создаёт уже существующие, пишет лог.
- Health-check: отсутствуют нужные → **CRITICAL** + уведомление; после восстановления → **OK** (recovery), повторный CRITICAL с тем же набором missing **не спамится**.

Канал alert: **Telegram** (`@Dahnfueshnr_bot`) — CRITICAL и recovery.  
Дедуп: повторный CRITICAL с тем же missing не шлётся; после починки уходит 🟢 OK.

```bash
# один раз: напиши боту /start, затем
docker compose exec backend python manage.py telegram_setup --test
```

Сценарий проверки сбоя:

```bash
# 1) поднять lab_events и создать горизонт
Get-Content -Raw .\scripts\lab03_setup_lab_events.sql | docker compose exec -T db psql -U flower -d flower_shop
docker compose exec backend python manage.py create_partitions --table lab_events

# 2) «сломать» ночной job — удалить будущую партицию
docker compose exec db psql -U flower -d flower_shop -c "DROP TABLE lab_events_$(Get-Date -Format yyyy_MM_dd);"  # пример; лучше DROP конкретной даты +3

# 3) check → CRITICAL + alert
docker compose exec backend python manage.py check_partitions --table lab_events

# 4) восстановить job’ом → check → OK / recovery
docker compose exec backend python manage.py create_partitions --table lab_events
docker compose exec backend python manage.py check_partitions --table lab_events
```

---

# Часть 12. Свой сервис: `shop_order`

### Шаги 1–4. Выбор

| | |
|--|--|
| Таблица | **`shop_order`** |
| Ключ | **`created_at`** |
| Стратегия | **RANGE по месяцам** |

Почему: в лабе 2 на 5M заказов запросы «PAID за 30 дней» и диапазоны дат деградировали до сотен мс–секунд; история почти не удаляется; API уже фильтрует `from_date` / `to_date` / `status+created_at`.

Месячный RANGE даёт pruning для типичных отчётов и позволяет архивировать старые месяцы через `DROP`/`DETACH`.

### Шаг 5. Партиции

Конвертация (на стенде ~5M строк):

```bash
Get-Content -Raw .\scripts\lab03_partition_shop_order.sql | docker compose exec -T db psql -U flower -d flower_shop
```

- PK: `(id, created_at)` (требование PostgreSQL для partitioned PK).
- FK `shop_orderitem → shop_order` снят (нельзя ссылаться только на `id`); логическая связь в приложении сохраняется.
- Индексы лабы 1 пересозданы на parent и наследуются партициями.
- Партиции: от минимальной даты данных до **сейчас + 3 месяца**.

### Шаг 6. Запросы API + EXPLAIN

Скрипт: `scripts/lab03_shop_order_queries.sql` (на 5M строк после партиционирования).

| Запрос | Pruning? | Факт |
|--------|----------|------|
| Список август 2026 (`from`/`to`) | **Да** | `Subplans Removed: 15`, скан только `shop_order_2026_08`, **~4.5 ms** |
| `status=PAID` + 30 дней | **Да** + индекс | убраны лишние месяцы, **~0.9 ms** |
| Лента клиента LIMIT 50 без даты | **Слабо** | InitPlan ищет top customer по всем партициям (~4.6 s); сама лента — Index Scan |
| `GET /orders/{id}` только по `id` | **Нет** | Append по всем партициям, **~22 ms** |

### Шаги 7–9. Автоматизация и контроль

```bash
docker compose exec backend python manage.py create_partitions --table shop_order
docker compose exec backend python manage.py check_partitions --table shop_order
```

Горизонт: **3 месяца вперёд** (`ORDER_PARTITION_HORIZON_MONTHS`).

---

## Воспроизведение

```bash
cd flower_shop/flower_shop
docker compose up -d

# части 1–11 (учебные таблицы)
Get-Content -Raw .\scripts\lab03_parts_01_11.sql | docker compose exec -T db psql -U flower -d flower_shop

# демо job/alert на дневных партициях
Get-Content -Raw .\scripts\lab03_setup_lab_events.sql | docker compose exec -T db psql -U flower -d flower_shop
docker compose exec backend python manage.py create_partitions --table lab_events
docker compose exec backend python manage.py check_partitions --table lab_events

# сервис: партиционировать заказы + планы
Get-Content -Raw .\scripts\lab03_partition_shop_order.sql | docker compose exec -T db psql -U flower -d flower_shop
Get-Content -Raw .\scripts\lab03_shop_order_queries.sql | docker compose exec -T db psql -U flower -d flower_shop
docker compose exec backend python manage.py create_partitions --table shop_order
```

---

## Контрольные ответы (кратко)

1. Partitioning — физическое разбиение одной логической таблицы на части.  
2. Индекс ускоряет поиск внутри набора строк; partitioning уменьшает сам набор (и упрощает lifecycle).  
3. RANGE, LIST, HASH (и комбинации).  
4. RANGE — даты, числовые интервалы, TTL/архивация.  
5. LIST — категории, регионы, типы клиентов.  
6. HASH — равномерное распределение без естественного диапазона.  
7. Ключ = то, по чему режут горячие запросы и/или данные для DROP.  
8. Pruning — исключение ненужных партиций на планировании.  
9. Нет предиката по ключу / функции от ключа / параметры, которые планировщик не видит.  
10. Да, индексы создаются на parent и есть на каждой партиции.  
11. INSERT падает, если нет DEFAULT.  
12. Чтобы INSERT завтра не упал ночью.  
13. Руками забывают; горизонт должен поддерживаться job’ом.  
14. Лог — запись факта; alert — активное уведомление ответственным.  
15. Recovery alert — сообщение, что проблема устранена.  
16. Иначе alert fatigue; дедуп до смены статуса.  
17. Нет: без предиката по ключу или при слишком мелких/крупных партициях может быть хуже.  
18. Слишком мелкие — overhead планирования; слишком крупные — слабый pruning и тяжёлый DROP.

---

## Что показать на защите

```
shop_order (большая)
  → ключ created_at / RANGE month
  → партиции
  → pruning на from_date / status+date
  → create_partitions
  → DROP будущей партиции
  → check_partitions → CRITICAL + alert
  → create_partitions → check → OK
```
