# Лабораторная работа №4

## Масштабирование чтения PostgreSQL: Primary + Replica

**Проект:** Flower Shop API  
**Студент:** Gorbunova · группа K0709-23/3  
**СУБД:** PostgreSQL 16.11 (образ `postgres:16-alpine`)

Цель: поднять **Primary + Replica** для собственного сервиса, настроить streaming
replication и понять идею **Read Scaling** — записи идут на Primary, часть чтений
обслуживает Replica, которая может временно отставать.

Все цифры и вывод ниже сняты с реально запущенного `docker compose`.

Артефакты:

- `docker-compose.yml` — сервисы `db` (Primary) и `db_replica` (Replica);
- `docker/postgres/primary/pg_hba.conf` — правило для `replication`;
- `docker/postgres/primary/initdb/01-create-replication-user.sh` — роль `replicator`;
- `docker/postgres/replica/replica-entrypoint.sh` — `pg_basebackup` + `standby.signal`;
- `shop/db_router.py` — роутер чтение→Replica / запись→Primary;
- `shop/replication.py` — статус репликации и lag, `use_primary()`;
- `shop/management/commands/replication_status.py` — команда `replication_status`;
- `scripts/lab04_primary.sql`, `scripts/lab04_replica.sql`, `scripts/lab04_read_scaling.sql`;
- API: `GET /api/replication/status/`, `GET /api/orders/read-demo/`, `GET /health`.

---

# Часть 1. Primary и Replica

В `docker-compose.yml` два экземпляра PostgreSQL:

```yaml
services:
  # Primary — принимает INSERT / UPDATE / DELETE и отдаёт WAL
  db:
    image: postgres:16-alpine
    container_name: flower_shop_db_primary
    environment:
      POSTGRES_DB: flower_shop
      POSTGRES_USER: flower
      POSTGRES_PASSWORD: flowerpass
      POSTGRES_REPLICATION_USER: replicator
      POSTGRES_REPLICATION_PASSWORD: replicapass
    command:                        # параметры репликации
      - postgres
      - -c
      - wal_level=replica
      - -c
      - max_wal_senders=10
      - -c
      - wal_keep_size=128MB
      - -c
      - hot_standby=on
      - -c
      - hba_file=/etc/postgresql/pg_hba.conf
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./docker/postgres/primary/pg_hba.conf:/etc/postgresql/pg_hba.conf:ro
      - ./docker/postgres/primary/initdb:/docker-entrypoint-initdb.d:ro
    ports:
      - "5432:5432"

  # Replica — read-only hot standby
  db_replica:
    image: postgres:16-alpine
    container_name: flower_shop_db_replica
    environment:
      PRIMARY_HOST: db
      REPLICATION_USER: replicator
      REPLICATION_PASSWORD: replicapass
      REPLICA_APPLICATION_NAME: flower_shop_replica
    entrypoint: ["/bin/bash", "/usr/local/bin/replica-entrypoint.sh"]
    volumes:
      - postgres_replica_data:/var/lib/postgresql/data
      - ./docker/postgres/replica/replica-entrypoint.sh:/usr/local/bin/replica-entrypoint.sh:ro
    ports:
      - "5433:5432"                 # наружу Replica на 5433, чтобы не конфликтовать
    depends_on:
      db:
        condition: service_healthy
```

**Какой контейнер кто:**

| Сервис | Контейнер | Роль | Порт наружу |
|--------|-----------|------|-------------|
| `db` | `flower_shop_db_primary` | **Primary** | 5432 |
| `db_replica` | `flower_shop_db_replica` | **Replica** (hot standby) | 5433 |

Проверка:

```bash
docker compose up -d
docker ps --format "{{.Names}} {{.Status}}"
```

```
flower_shop_db_primary   Up (healthy)
flower_shop_db_replica   Up (healthy)
flower_shop_backend      Up
```

Как подключиться:

```bash
# Primary
docker compose exec db psql -U flower -d flower_shop
# Replica
docker compose exec db_replica psql -U flower -d flower_shop
```

---

# Часть 2. Streaming replication

Цепочка: Primary изменяет данные → изменение фиксируется в **WAL** →
Replica получает поток WAL (`walreceiver`) → Replica воспроизводит его (`startup`).
Это **физическая** репликация на уровне WAL-записей, а не логическая.

## Настройка

1. **Primary** запускается с `wal_level=replica`, `max_wal_senders=10`,
   `wal_keep_size=128MB`, `hot_standby=on` и с `hba_file` из репозитория.
   В `pg_hba.conf` добавлено то, чего нет в дефолтном файле — правило репликации:

   ```
   host  replication  replicator  0.0.0.0/0  scram-sha-256
   host  replication  replicator  ::/0       scram-sha-256
   ```

   Без этой строки Replica не сможет открыть replication-соединение.

2. При инициализации Primary создаётся роль репликации
   (`initdb/01-create-replication-user.sh`):

   ```sql
   CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replicapass';
   ```

3. **Replica** при первом старте (`replica-entrypoint.sh`):
   ждёт Primary → делает `pg_basebackup` → прописывает `primary_conninfo`
   в `postgresql.auto.conf` → создаёт `standby.signal` → стартует hot standby:

   ```bash
   gosu postgres pg_basebackup -h db -p 5432 -U replicator \
        -D "$PGDATA" -Fp -Xs -P -w
   echo "primary_conninfo = 'host=db port=5432 user=replicator \
   password=replicapass application_name=flower_shop_replica'" \
        >> "$PGDATA/postgresql.auto.conf"
   touch "$PGDATA/standby.signal"
   ```

## Проверка на Primary

```sql
SELECT application_name, client_addr, state, sync_state,
       sent_lsn, write_lsn, flush_lsn, replay_lsn
FROM pg_stat_replication;
```

```
  application_name   | client_addr |   state   | sync_state |  sent_lsn  | flush_lsn  | replay_lsn
---------------------+-------------+-----------+------------+------------+------------+------------
 flower_shop_replica | 172.22.0.3  | streaming | async      | 3/380017B8 | 3/380017B8 | 3/380017B8
(1 row)
```

`state = streaming` — Replica подключена и получает WAL,
`sync_state = async` — асинхронная репликация (по умолчанию).

Лог Replica подтверждает цепочку:

```
LOG:  entering standby mode
LOG:  consistent recovery state reached at 3/37000100
LOG:  database system is ready to accept read-only connections
LOG:  started streaming WAL from primary at 3/38000000 on timeline 1
```

---

# Часть 3. Доказательство работы репликации

Скрипт `scripts/lab04_primary.sql` (на Primary) пишет маркер-клиента
`lab04.replica@example.com`:

```sql
INSERT INTO shop_customer (first_name, last_name, email, phone, address, created_at)
VALUES ('Lab04', 'Replica', 'lab04.replica@example.com', '+70000000000', 'Primary', NOW())
ON CONFLICT (email) DO UPDATE SET address = 'Primary (updated)'
RETURNING id, email, created_at;
```

```
  id  |           email           |          created_at
------+---------------------------+-------------------------------
 5001 | lab04.replica@example.com | 2026-09-16 20:43:24.467699+00
(1 row)
INSERT 0 1
```

Сразу после этого `scripts/lab04_replica.sql` (на Replica) находит её:

```sql
SELECT id, first_name, last_name, email, address, created_at
FROM shop_customer WHERE email = 'lab04.replica@example.com';
```

```
  id  | first_name | last_name |           email           | address |          created_at
------+------------+-----------+---------------------------+---------+-------------------------------
 5001 | Lab04      | Replica   | lab04.replica@example.com | Primary | 2026-09-16 20:43:24.467699+00
(1 row)
```

То же самое через API: `POST /api/customers/` создал клиента
`lab04.write@example.com` (`id = 5003`), и он виден на обоих узлах.

```
--- Primary ---           id 5003 | lab04.write@example.com | created via API POST
--- Replica ---           id 5003 | lab04.write@example.com | created via API POST
```

**Подтверждение подключённой Replica** — вывод `pg_stat_replication` (см. Часть 2):
одна строка `flower_shop_replica`, `state=streaming`.

---

# Часть 4. Read-only поведение Replica

Попытка записи на Replica:

```sql
INSERT INTO shop_customer (first_name, last_name, email, phone, address, created_at)
VALUES ('Should', 'Fail', 'should.fail@example.com', '', '', NOW());
```

```
ERROR:  cannot execute INSERT in a read-only transaction
```

Кроме того, `pg_is_in_recovery()` на Replica возвращает `t`:

```
 is_in_recovery | server_addr | server_port | current_user
----------------+-------------+-------------+--------------
 t              |             |             | flower
```

**Почему Replica нельзя использовать как независимую базу для записи.**
Replica — это физическая копия Primary, которая постоянно воспроизводит его WAL.
Любая запись в неё:

1. **запрещена движком** (hot standby работает в режиме read-only);
2. если бы её разрешить — она «разошлась» бы с потоком WAL и сломала
   консистентность; при переключении (failover) изменения были бы потеряны;
3. у Replica нет своего независимого журнала приёмки транзакций — её задача
   быть копией, а не второй точкой записи.

Поэтому в приложении запись жёстко направлена на Primary (Часть 5).

---

# Часть 5. Направление чтения сервиса на Replica

В backend добавлено **отдельное подключение** `replica`
(`DATABASE_REPLICA_URL`), а между Primary и Replica распределяет трафик
роутер `shop/db_router.py`:

- `db_for_write` → всегда `default` (Primary);
- `db_for_read` → `replica` **только** для моделей из
  `settings.READ_REPLICA_MODELS`: `shop.order`, `shop.orderitem`,
  `shop.product`, `shop.category`;
- `allow_migrate` → только `default` (миграции на Primary);
- auth / sessions / admin остаются на Primary (read-after-write без сюрпризов).

```python
class ReadReplicaRouter:
    def db_for_read(self, model, **hints):
        if hints.get('db_alias'):
            return hints['db_alias']
        if not self._replica_ready() or force_primary():
            return None                       # -> default (Primary)
        key = f'{model._meta.app_label}.{model._meta.model_name}'
        return REPLICA_ALIAS if key in settings.READ_REPLICA_MODELS else None

    def db_for_write(self, model, **hints):
        return 'default'

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return db == 'default'
```

Выбранный read-сценарий — **список заказов** `GET /api/orders/`
(`Order` входит в `READ_REPLICA_MODELS`).

## Доказательство, что чтение идёт на Replica

Серия из 5 запросов `GET /api/orders/` и разница в счётчиках сканов
`shop_order` до/после:

```
replica scans: 575 -> 904   delta = +329
primary scans: 260 -> 260   delta = 0
```

Все сканы таблицы заказов ушли на **Replica**, на Primary — ноль.

Отдельный эндпоинт `GET /api/orders/read-demo/` явно выполняет SELECT через
`Order.objects.using('replica')` и показывает, какой узел обслужил запрос:

```json
{
  "replica_alias": "replica",
  "replica_used": true,
  "served_by": {"alias": "replica", "role": "replica",
                "host": "172.22.0.3/32", "in_recovery": true},
  "count_primary": 5000001,
  "count_replica": 5000001,
  "lag_seconds": 0.0,
  "lag_bytes": 0
}
```

`served_by.role = replica`, `in_recovery = true` — чтение действительно
обслужил standby. Запись при этом по-прежнему уходит на Primary
(`POST /api/customers/` → `201`, строка на Primary и затем на Replica).

Статус всей связки: `GET /api/replication/status/` и
`python manage.py replication_status`:

```
=== Replication status (lab 04) ===
Read scaling enabled : True
Replica alias        : replica

--- Primary (default) ---
  role=primary host=172.22.0.2/32 port=5432 db=flower_shop
  current WAL LSN: 3/38026938

--- pg_stat_replication (on Primary) ---
  app=flower_shop_replica client=172.22.0.3/32 state=streaming sync=async
    sent=3/38026938 flush=3/38026938 replay=3/38026938 reply_lag=4.689s

--- Replica ---
  role=replica host=172.22.0.3/32 port=5432 db=flower_shop
  in recovery     : True
  replay LSN      : 3/38026938
  lag (seconds)   : 0.000
  lag (bytes)     : 0
```

---

# Часть 6. Replication lag

Чтобы гарантированно поймать лаг, воспроизведение WAL на Replica было
**приостановлено**, после чего на Primary вставлен заказ `id = 9000002`:

```sql
-- на Replica
SELECT pg_wal_replay_pause();
-- на Primary
INSERT INTO shop_order (...) VALUES (...) RETURNING id;   -- id = 9000002
```

Пока репликация на паузе:

```
--- Primary: pg_stat_replication ---
  application_name   |  sent_lsn  | replay_lsn | lag_bytes
---------------------+------------+------------+-----------
 flower_shop_replica | 3/38026938 | 3/38018D28 |     56336

--- Replica: виден ли заказ 9000002? ---
 paused | visible_on_replica
--------+--------------------
 t      |                  0
```

Тот же лаг виден через API (`GET /api/replication/status/`):

```json
"lag_seconds": 0.605,
"lag_bytes": 56336,
"senders": [{"state": "streaming",
             "sent_lsn": "3/38026938",
             "replay_lsn": "3/38018D28",
             "replay_lag_seconds": 0.605}]
```

После `SELECT pg_wal_replay_resume()` реплика догнала:

```
 paused | order_9000002_visible
--------+-----------------------
 f      |                     1

lag_seconds = 0.0, lag_bytes = 0
```

**Вывод.** Между фиксацией на Primary и применением на Replica есть задержка
— **replication lag**. Репликация **не мгновенная**: сразу после INSERT
SELECT с Replica может вернуть старые данные (заказ `9000002` был вставлен на
Primary, но на Replica появился только после возобновления replay).

Примечание по метрике: `now() - pg_last_xact_replay_timestamp()` в простое растёт
даже у полностью догнавшей реплики, поэтому в `shop/replication.py` лаг
считается так: байтовый лаг `pg_current_wal_lsn - pg_last_wal_replay_lsn`
(0 ⇒ лаг 0), иначе — `replay_lag` из `pg_stat_replication`.

---

# Контрольные вопросы

**1. Чем Primary отличается от Replica?**
Primary принимает запись (INSERT/UPDATE/DELETE), генерирует WAL и является
источником истины. Replica — физическая копия Primary в режиме hot standby:
она читает WAL и воспроизводит его, отвечает только на read-only запросы
(`pg_is_in_recovery() = true`). У нас: `flower_shop_db_primary` и
`flower_shop_db_replica`.

**2. Почему запись выполняем на Primary?**
Только Primary ведёт изменение данных и пишет WAL. Replica read-only: запись там
падает с `cannot execute INSERT in a read-only transaction`. Писать на Replica
нельзя ещё и потому, что она обязана повторять поток WAL Primary — расхождение
сломало бы репликацию и потерялось бы при failover.

**3. Как изменение из Primary попадает на Replica?**
1) транзакция фиксируется на Primary и её записи попадают в WAL;
2) `walsender` на Primary передаёт WAL по протоколу репликации;
3) `walreceiver` на Replica пишет его в локальный WAL;
4) процесс `startup` воспроизводит записи — изменение становится видимым
для чтения. Это асинхронный физический streaming replication.

**4. Что такое WAL в контексте репликации?**
Write-Ahead Log — журнал изменений, который PostgreSQL пишет **до**
применения к страницам данных. Для репликации WAL — это единый поток
физических изменений, по которому Replica детерминированно повторяет
состояние Primary. Позиции в WAL сравниваются как `LSN`
(Log Sequence Number), например `3/38026938`.

**5. Что такое replication lag?**
Отставание Replica от Primary: разница между позицией, уже зафиксированной на
Primary, и позицией, реально воспроизведённой на Replica. Измеряется в байтах
(`pg_current_wal_lsn - pg_last_wal_replay_lsn`) и/или во времени
(`replay_lag` из `pg_stat_replication`). В демо мы видели `lag_bytes = 56336`
при приостановленном replay.

**6. Почему следующий SELECT после INSERT потенциально может увидеть старые данные, если его отправить на Replica?**
Потому что запись сначала фиксируется на Primary и лишь затем по WAL доезжает до
Replica. Если SELECT уйдёт на Replica до применения соответствующего WAL, он
увидит прежнее состояние (в демо: заказ `9000002` был на Primary, но
`visible_on_replica = 0`). Это нормальное проявление асинхронной репликации,
а не ошибка. Отсюда правило: критичные для read-after-write чтения нужно
направлять на Primary (`shop.replication.use_primary()` или `.using('default')`).

**7. Что именно масштабируется при Read Scaling: скорость одного SQL-запроса или способность системы обслуживать больше чтений?**
Масштабируется **пропускная способность чтений** (число чтений в единицу
времени), а не скорость одного конкретного запроса. Один и тот же запрос на
Replica выполняется примерно так же быстро, как на Primary; выигрыш в том, что
чтения распределяются по нескольким узлам, снижая нагрузку на Primary и
позволяя обслуживать больше параллельных читателей. Оптимизация же отдельного
запроса — это индексы и планы SQL.

**8. Почему наличие Replica не отменяет необходимость индексов и оптимизации SQL?**
Replica копирует ту же схему, те же данные и те же медленные планы. Плохой
запрос останется плохим и на Replica, только теперь он ещё и создаёт нагрузку на
копию и сеть репликации. Более того, чем больше приложение читает с Replica,
тем важнее, чтобы запросы были селективными: реплика — это не замена
индексам/партиционированию (лабы 1–3), а дополнение к ним.

**9. Расскажите про CAP-теорему.**
CAP (Brewer): распределённая система при сетевом разделении (Partition) не может
одновременно гарантировать строгую согласованность (Consistency) и полную
доступность (Availability) — приходится выбирать. PostgreSQL streaming
replication с асинхронной Replica — это выбор в пользу **AP** для чтений с
реплики: система доступна, но чтение может вернуть слегка устаревшие данные
(eventual consistency), потому что подтверждение записи не ждёт Replica.
Синхронная репликация (`synchronous_commit=on` + `sync_state=synchronous`)
сдвигает систему к **CP**: запись подтверждается только после применения на
реплике, но при недоступности реплики запись может блокироваться. Наш выбор —
асинхронная реплика, компромисс в пользу доступности и низкой задержки записи
ценой возможного lag.

---

# Воспроизведение

```bash
cd flower_shop/flower_shop

# Primary + Replica + backend
docker compose up -d --build
docker compose ps

# Часть 2: состояние репликации на Primary
docker compose exec -T db psql -U flower -d flower_shop \
  -c "SELECT application_name, state, sync_state, sent_lsn, replay_lsn FROM pg_stat_replication"

# Часть 3: запись на Primary
Get-Content -Raw .\scripts\lab04_primary.sql | docker compose exec -T db psql -U flower -d flower_shop
# Часть 3-4: чтение и read-only на Replica
Get-Content -Raw .\scripts\lab04_replica.sql | docker compose exec -T db_replica psql -U flower -d flower_shop

# Часть 5: read scaling + статус
Get-Content -Raw .\scripts\lab04_read_scaling.sql | docker compose exec -T db_replica psql -U flower -d flower_shop
docker compose exec -T backend python manage.py replication_status
curl http://localhost:8000/api/replication/status/
curl http://localhost:8000/api/orders/read-demo/

# Часть 6: lag — пауза replay, запись на Primary, чтение с Replica
docker compose exec -T db_replica psql -U flower -d flower_shop -c "SELECT pg_wal_replay_pause()"
docker compose exec -T db psql -U flower -d flower_shop \
  -c "INSERT INTO shop_order (status,total_amount,delivery_address,comment,created_at,updated_at,customer_id) SELECT 'NEW',10,'lag','lag',now(),now(),MIN(id) FROM shop_customer RETURNING id"
docker compose exec -T db_replica psql -U flower -d flower_shop -c "SELECT pg_last_wal_replay_lsn(), pg_is_wal_replay_paused()"
docker compose exec -T db_replica psql -U flower -d flower_shop -c "SELECT pg_wal_replay_resume()"
```

## Что показать на защите

```
docker compose ps                       -> primary (db) + replica (db_replica)
pg_stat_replication                     -> state=streaming, app=flower_shop_replica
INSERT на Primary -> SELECT на Replica  -> маркер найдена (репликация работает)
INSERT на Replica                       -> ERROR: read-only transaction
GET /api/orders/                        -> сканы растут на Replica, не на Primary
read-demo / replication_status          -> served_by.role=replica, lag_bytes
pg_wal_replay_pause() -> INSERT -> SELECT-> заказ ещё не виден (replication lag)
pg_wal_replay_resume() -> SELECT        -> заказ виден, lag = 0
docker compose exec backend python manage.py test shop  -> 9 тестов OK
```