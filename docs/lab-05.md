# Лабораторная работа №5

## Шардирование PostgreSQL: Router и Consistent Hashing

**Проект:** Flower Shop API  
**Студент:** Gorbunova · группа K0709-23/3  
**СУБД:** PostgreSQL 16 (`postgres:16-alpine`, Docker Compose)  

Цель: распределить данные собственного сервиса между несколькими экземплярами PostgreSQL и сравнить две стратегии выбора шарда — `hash(key) % N` и **Consistent Hashing** — по объёму данных, которые нужно перенести при изменении числа шардов.

Все цифры ниже сняты с реально запущенного стека (`docker compose`). Транскрипты — `flower_shop/scripts/lab05_*_out.*`.

Артефакты:

- `docker-compose.yml` — сервисы `shard0`, `shard1`, `shard2`;
- `shop/sharding.py` — `key_hash`, `ModuloSharding`, `ConsistentHashing`, `ShardRouter`, `plan_migration`;
- `shop/shard_router.py` — доступ к шардированной таблице + Django-роутер;
- `shop/management/commands/shard_setup.py`, `shard_load.py`, `shard_report.py`, `shard_experiment.py`;
- API: `GET /api/shards/report/`, `GET /api/shards/route/?customer_id=`, `GET /api/shards/plan/`;
- тесты: `shop/tests.py` (классы `ShardingKeyHashTests`, `ModuloShardingTests`, `ConsistentHashingTests`, `ShardRouterTests`, `MigrationPlanTests`).

---

# Часть 1. Выбор данных для шардирования

**1. Какую сущность выбрали?**  
Заказы — таблица `shop_order`. Это уже «scaling entity» сервиса: она растёт быстрее всего (в проекте 5 000 000 строк из лабы 3), по ней идут все тяжёлые чтения.

**2. Какое поле — shard key?**  
`shop_order.customer_id`.

**3. Почему именно этот ключ?**

- **Ко-локация.** Все заказы одного клиента попадают на один шард. Тогда «лента заказов клиента», «заказы клиента», «товары клиента» читаются с одного узла, без cross-shard запросов.
- **Кардинальность.** В базе ~50 000 клиентов — высокая кардинальность даёт равномерное распределение хэша.
- **Совпадение с шаблоном доступа.** Основные запросы сервиса фильтруют именно по `customer_id`:

```sql
-- GET /api/customers/{id}/orders/
SELECT * FROM shop_order WHERE customer_id = %s ORDER BY created_at DESC;
-- GET /api/orders/?customer=42
SELECT * FROM shop_order WHERE customer_id = 42;
-- GET /api/orders/customer-products/?customer_id=42
... WHERE o.customer_id = %s ...
```

**4. Насколько равномерно распределит?**  
`customer_id` — целые числа без «горячих» значений, а `md5`-хэш от них распределяется близко к равномерному. На практике: 33 350 / 33 766 / 32 884 записей, отклонение от идеала **1.3 %** (см. Часть 4).

**5. Какие запросы сервиса используют ключ?**  
Список заказов клиента, фильтр заказов по клиенту, товары клиента, отчёт «продажи по клиенту». Именно поэтому шардирование по `customer_id` не порождает распределённых JOIN.

> Примечание: это **другой** разрез, чем партиционирование из лабы 3. Партиции режут по времени (`created_at`), шарды — по клиенту. Их можно совмещать: шард = «чей», партиция внутри шарда = «когда».

---

# Часть 2. Запуск нескольких PostgreSQL

В `docker-compose.yml` добавлены три независимых экземпляра:

```yaml
  shard0:
    image: postgres:16-alpine
    container_name: flower_shop_shard0
    environment:
      POSTGRES_DB: flower_shop_shard
      POSTGRES_USER: flower
      POSTGRES_PASSWORD: flowerpass
    volumes:
      - shard0_data:/var/lib/postgresql/data
    ports:
      - "6440:5432"          # наружу, чтобы подключаться вручную
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U flower -d flower_shop_shard"]

  shard1:  # порт 6441
  shard2:  # порт 6442
```

Backend видит все три по своим URL:

```yaml
  backend:
    environment:
      - SHARD0_URL=postgres://flower:flowerpass@shard0:5432/flower_shop_shard
      - SHARD1_URL=postgres://flower:flowerpass@shard1:5432/flower_shop_shard
      - SHARD2_URL=postgres://flower:flowerpass@shard2:5432/flower_shop_shard
      - SHARD_STRATEGY=consistent
      - SHARD_VNODES=160
```

`config/settings.py` превращает их в Django-алиасы `shard0`, `shard1`, `shard2` и список `SHARD_ALIASES`:

```python
SHARD_ALIASES: list[str] = []
_shard_index = 0
while os.environ.get(f'SHARD{_shard_index}_URL'):
    _alias = f'shard{_shard_index}'
    DATABASES[_alias] = _db_config_from_url(os.environ[f'SHARD{_shard_index}_URL'])
    SHARD_ALIASES.append(_alias)
    _shard_index += 1
```

Проверка:

```
flower_shop_shard0 | Up (healthy)
flower_shop_shard1 | Up (healthy)
flower_shop_shard2 | Up (healthy)
flower_shop_db_primary | Up (healthy)     # Primary из лабы 4 — источник данных
flower_shop_db_replica | Up (healthy)
```

Схема шардированной таблицы создаётся идемпотентно:

```bash
docker compose exec backend python manage.py shard_setup
```

```
Shard setup started.
Table: shop_order_shard
Shard key: customer_id
Strategy: consistent (vnodes=160)
Ring points: 480

shard0: schema ready (shop_order_shard + indexes + shard_meta)
shard1: schema ready (shop_order_shard + indexes + shard_meta)
shard2: schema ready (shop_order_shard + indexes + shard_meta)
Shard setup finished.
```

---

# Часть 3. Простой Router: `hash(key) % N`

Реализация — `shop/sharding.py`:

```python
RING_SIZE = 2 ** 32

def key_hash(key) -> int:
    """Детерминированный 32-битный хэш ключа шардирования."""
    digest = hashlib.md5(str(key).encode('utf-8')).digest()
    return int.from_bytes(digest[:4], 'big')


@dataclass(frozen=True)
class ModuloSharding:
    shards: int

    def index_for(self, key) -> int:
        return key_hash(key) % self.shards
```

**Почему свой `key_hash`, а не встроенный `hash()`.** В Python хэш строк рандомизируется на процесс (`PYTHONHASHSEED`). Если бы мы использовали `hash(key) % N`, то gunicorn с двумя воркерами (`--workers 2`) считал бы шард по-разному в каждом процессе, и одна и та же запись «переезжала» бы между шардами. `md5`, урезанный до 32 бит, стабилен и воспроизводим.

Проверка формулы на реальных ключах:

```
shard = key_hash(key) % 3

key=101 -> shard2
key=102 -> shard0
key=103 -> shard1
```

Тот же роутер используется для чтения (`shard_router.alias_for_customer`) и для записи: `shard_load` раскладывает строку по шардам тем же `alias_for`, поэтому запись и чтение гарантированно согласованы.

---

# Часть 4. Распределение данных

Загрузка — **100 000 заказов**, прочитанных из Primary (данные лабы 3) и разложенных по шардам:

```bash
docker compose exec backend python manage.py shard_load --orders 100000 --clear
```

```
Shard load started: 100000 orders, strategy=consistent
Shards: shard0, shard1, shard2
Shards cleared.
  read=59224 (flushed shard1: 20000)
  read=60089 (flushed shard0: 20000)
  read=60708 (flushed shard2: 20000)

Rows read from Primary: 100000
  shard0: inserted 33350, now in table 33350
  shard1: inserted 33766, now in table 33766
  shard2: inserted 32884, now in table 32884
Shard load finished.
```

Отчёт (`docker compose exec backend python manage.py shard_report`):

```
=== Shard report (lab 05) ===
Table: shop_order_shard
Shard key: customer_id
Strategy: consistent

Shard             Rows   Customers
----------------------------------
shard0           33350        1661
shard1           33766        1698
shard2           32884        1641
----------------------------------
TOTAL           100000

Ideal per shard: 33333.33 rows, worst shard: 33766 rows, deviation: 1.3%
```

| Shard | Записей | Доля | Клиентов |
|-------|--------:|-----:|---------:|
| `shard0` | 33 350 | 33.35 % | 1 661 |
| `shard1` | 33 766 | 33.77 % | 1 698 |
| `shard2` | 32 884 | 32.88 % | 1 641 |
| **Итого** | **100 000** | 100 % | 5 000 |

**Вывод:**

- Распределение **очень равномерное**: максимальное отклонение от идеальных 33 333 строк — **1.3 %** (≈ 433 строки).
- Клиентов тоже почти поровну (1 641…1 698) — значит ключ распределяет не только строки, но и нагрузку по клиентам.
- Заметного дисбаланса нет. Если бы он был (например, 60/30/10), «горячий» шард стал бы бутылочным горлышком: он получал бы большую часть запросов и первым упёрся бы в CPU/IO, а добавление мощностей другим шардам не помогало бы. Именно поэтому ключ с высокой кардинальностью и без «горячих» значений так важен.

### Чтение по shard key (реальный сценарий сервиса)

`GET /api/shards/route/?customer_id=1` находит шард клиента и читает его заказы **только с этого шарда**:

```json
{
  "customer_id": 1,
  "strategy": "consistent",
  "shard_index": 2,
  "shard_alias": "shard2",
  "orders_count": 10,
  "orders_on_shard": [
    {"id": 9000003, "customer_id": 1, "status": "NEW", "total_amount": "1234.56",
     "created_at": "2026-09-19T10:31:54.239848Z"},
    {"id": 5138482, "customer_id": 1, "status": "PROCESSING", "total_amount": "6182.79",
     "created_at": "2026-09-09T22:23:22.953066Z"}
  ]
}
```

---

# Часть 5. Что произойдёт при добавлении нового shard?

Добавляем `shard3`: было `hash(key) % 3`, стало `hash(key) % 4`.

```bash
docker compose exec backend python manage.py shard_experiment --samples 100000
```

```
=== Shard migration experiment (lab 05) ===
Source: shop_order.customer_id (records)
Keys in experiment: 100000
Shards: 3 -> 4

--- modulo: распределение при 3 шардах ---
  shard0: 33586 (33.59%)
  shard1: 33557 (33.56%)
  shard2: 32857 (32.86%)
--- modulo: распределение при 4 шардах ---
  shard0: 24009 (24.01%)
  shard1: 25572 (25.57%)
  shard2: 25263 (25.26%)
  shard3: 25156 (25.16%)
```

Результат по перемещению:

| Метрика | Значение |
|---------|---------:|
| Всего записей | 100 000 |
| **Изменили shard** | **76 102** |
| Не изменили | 23 898 |
| **Процент перемещения** | **76.10 %** |

**Почему так много.** При `% 3` позиция записи задаётся остатком `h mod 3`, при `% 4` — остатком `h mod 4`. Совпадёт остаток только у тех ключей, где `h mod 3 == h mod 4`, а это редкое событие: доля «на месте» ≈ `1/N` (здесь 23.9 % ≈ 1/4 ≈ 25 %). Все остальные **76 %** записей формально меняют адрес, хотя большая часть данных при этом просто «перетасовывается» между **старыми** шардами (из shard0 уехало 33 586 − 24 009 = 9 577 записей, и не все они поехали в новый shard3).

Отсюда вывод: **добавление одного PostgreSQL в схеме `modulo` требует массового переноса данных**. На 100 000 записей это уже 76 102 строки, а на 5 000 000 заказов — около 3.8 млн строк. Это дорогие операции: чтение + запись через сеть, растущий лаг, риск деградации сервиса на время миграции. Именно поэтому `hash % N` плохо масштабируется горизонтально.

---

# Часть 6. Consistent Hashing

Реализация — `ConsistentHashing` в `shop/sharding.py`: кольцо + virtual nodes.

```python
@dataclass
class ConsistentHashing:
    vnodes: int = 160
    _ring: list[tuple[int, str]] = field(default_factory=list, repr=False)
    _positions: list[int] = field(default_factory=list, repr=False)
    _nodes: set[str] = field(default_factory=set, repr=False)

    def _rebuild(self):
        ring = []
        for node in sorted(self._nodes):
            for i in range(self.vnodes):            # virtual nodes
                ring.append((key_hash(f'{node}#{i}'), node))
        ring.sort(key=lambda point: (point[0], point[1]))
        self._ring = ring
        self._positions = [point[0] for point in ring]

    def node_for(self, key) -> str | None:
        """Ближайший узел по часовой стрелке от hash(key), с заворотом кольца."""
        if not self._ring:
            return None
        position = key_hash(key)
        idx = bisect.bisect_left(self._positions, position)
        if idx == len(self._positions):
            idx = 0                                # wraparound
        return self._ring[idx][1]
```

Схема:

```
        hash(shard key)
              │
              ▼
   ┌──────────────────────────────┐
   │      hash ring (0..2^32)     │
   │  shard0#137 ────────────┐    │
   │        shard1#42        │    │
   │  shard2#7               │    │
   │  *ключ* ──► ближайший узел по часовой стрелке
   └──────────────────────────────┘
```

**Virtual nodes** — каждый физический шард ставится на кольцо 160 раз (`shard0#0 … shard0#159`). Без них три точки делят кольцо на три случайных по длине дуги, и распределение выходит очень неровным. С виртуальными узлами дуги усредняются — тест `test_virtual_nodes_improve_balance` проверяет, что разброс при 160 vnodes меньше, чем при 1.

---

# Часть 7. Сравнение стратегий

Тот же эксперимент `3 → 4`, но с Consistent Hashing:

```
--- consistent: распределение при 3 шардах ---
  shard0: 33350 (33.35%)
  shard1: 33766 (33.77%)
  shard2: 32884 (32.88%)
--- consistent: распределение при 4 шардах ---
  shard0: 27890 (27.89%)
  shard1: 22945 (22.95%)
  shard2: 24444 (24.44%)
  shard3: 24721 (24.72%)

=== Перемещение данных при 3 -> 4 шардов ===
Strategy           Moved        Kept   Moved %   Ideal %
--------------------------------------------------------
modulo             76102       23898    76.10%    25.00%
consistent         24721       75279    24.72%    25.00%
--------------------------------------------------------
Consistent hashing перемещает в 3.08x меньше данных.
```

| Стратегия | Перемещено записей | **Перемещено %** | Теоретический минимум |
|-----------|-------------------:|-----------------:|----------------------:|
| `hash(key) % N` | 76 102 | **76.10 %** | 1/N → 25 % |
| Consistent Hashing | 24 721 | **24.72 %** | 1/N → 25 % |

**1. Почему результаты отличаются?**  
`modulo` пересчитывает остаток от деления на новое N, поэтому меняется адрес почти у всех ключей (совпадает лишь там, где `h mod 3 == h mod 4`). Consistent hashing не переназначает ключи: он лишь отдаёт новому узлу те ключи, чей ближайший по кольцу узел изменился. Появляется один новый узел — он забирает ~`1/N_new` дуги, то есть ~25 % ключей, и всё.

**2. Почему Consistent Hashing уменьшает объём перемещаемых данных?**  
Кольцо — это отображение «точка пространства хэшей → узел». Добавление узла лишь вставляет новые точки в кольцо и перераспределяет небольшие непрерывные интервалы. Поэтому затронуто ровно `1/N_new` ключей (в примере 24.72 % против идеальных 25 %), а не почти вся база. Именно поэтому такой подход масштабируется: рост кластера не превращается в полную перезагрузку данных.

**3. Что произойдёт, если добавить ещё один shard?**  
`shard4` заберёт около `1/5 = 20 %` ключей у своих соседей по кольцу. Старые шарды между собой ключами не обмениваются — каждый отдаёт лишь часть дуги новому узлу. Это проверяется тестом `test_new_shard_only_receives_data_under_consistent_hashing`: при переходе `3 → 4` любой ключ, сменивший шард, попадает **только** в `shard3`, но никогда из `shard1` в `shard0`.

**4. Что произойдёт, если удалить shard?**  
Обратная операция: ключи удалённого узла переходят его соседу по кольцу. Затронуты снова только `1/N` ключей (все, что были у удалённого узла). Тест `test_removing_node_returns_its_keys` показывает: после удаления `shard1` все его ключи уходят на оставшийся `shard0`, остальные данные не двигаются. Это делает вывод узла из кластера дешёвой операцией (при условии, что данные есть куда принять).

---

# Часть 8. Итог

**Shard** — это независимый экземпляр PostgreSQL, которому отдана часть данных одной логической сущности; вместе шарды образуют один логический набор данных. **Shard key** — поле, по которому определяется шард записи; у нас это `shop_order.customer_id`, потому что он даёт ко-локацию всех заказов клиента и совпадает с фильтрами реальных запросов сервиса. **Router** нужен, чтобы приложение само, по ключу, определяло нужный узел: без него каждый запрос пришлось бы рассылать во все шарды и склеивать результаты. Стратегия `hash(key) % N` вычисляет шард как остаток от деления хэша ключа на число шардов — просто и равномерно (у нас отклонение 1.3 %), но при изменении `N` меняется значение почти для всех ключей: переход `3 → 4` требует переноса **76.1 %** строк. **Consistent Hashing** решает эту проблему: узлы и виртуальные узлы кладутся на кольцо хэшей, а ключ уходит к ближайшему узлу по часовой стрелке, поэтому добавление шарда затрагивает лишь `~1/N` ключей — в нашем эксперименте **24.72 %**, то есть в **3.08 раза** меньше данных, чем при `modulo`, и почти в точности теоретический минимум 25 %. Я бы выбрал для сервиса **Consistent Hashing с виртуальными узлами**: кластер будет расти, а стоимость добавления узла в нём остаётся пропорциональной доле нового узла, а не размеру всей базы; виртуальные узлы при этом дают распределение, близкое к равномерному (отклонение 1.3 %).

---

# Вопросы для самопроверки

**1. Что такое shard?**  
Горизонтальная часть данных: независимый экземпляр PostgreSQL, на котором лежит подмножество строк одной логической сущности. Вместе шарды образуют один логический набор данных. У нас: `shard0`, `shard1`, `shard2` с таблицей `shop_order_shard`.

**2. Что такое shard key?**  
Поле, значение которого однозначно определяет шард записи (у нас `customer_id`). От него берётся хэш, и результат отображается на шард. Правильный shard key — высококардинальный и совпадающий с фильтрами частых запросов, чтобы данные были ко-локализованы.

**3. Почему нельзя просто случайно распределять записи между PostgreSQL?**  
Тогда нельзя найти запись: чтобы прочитать заказ, пришлось бы опросить **все** шарды (scatter-gather). Случайное распределение ломает также ко-локацию (заказы одного клиента разлетаются) и делает фильтры/сортировки по клиенту распределёнными. Нужно **детерминированное** отображение key → shard.

**4. Как работает `hash(key) % N`?**  
Берём стабильный хэш ключа, берём остаток от деления на число шардов N — получаем индекс шарда. Одинаковый ключ всегда даёт один и тот же шард, а разные ключи распределяются по шардам примерно равномерно.

**5. Почему изменение количества шардов — проблема?**  
При смене N меняется делитель, и остаток меняется почти для всех ключей: `h mod 3` и `h mod 4` совпадают лишь у ~25 % ключей. Значит, при переходе `3 → 4` нужно физически перенести ~75 % данных (у нас 76.1 %). Это долго, дорого и рискованно.

**6. Что такое Consistent Hash Ring?**  
Кольцо всех значений хэша `0..2^32−1`. На него наносятся узлы (шарды), а ключ относится к **ближайшему узлу по часовой стрелке**. Тот же принцип используется в распределённых кэшах.

**7. Почему при добавлении shard'а не приходится переносить все данные?**  
Новый узел занимает лишь свою дугу кольца; меняют шард только ключи, ближайший узел которых стал новым — это ~`1/N_new` от общего числа. Остальные ключи по-прежнему указывают на те же узлы. У нас: 24.72 % при добавлении 4-го шарда вместо 76.1 % у `modulo`.

**8. Что такое virtual nodes и зачем они нужны?**  
Это несколько (у нас 160) точек на кольце на каждый физический узел: `key_hash("shard0#0")`, `key_hash("shard0#1")`, … Они нужны, чтобы сгладить неравномерность: с малым числом физических узлов дуги получаются случайной длины, и один шард может получить непропорционально большой диапазон. 160 виртуальных узлов дают отклонение около 1.3 %.

**9. Может ли Consistent Hashing гарантировать идеальное распределение нагрузки?**  
Нет. Он даёт распределение *близкое* к равномерному, но не идеальное: при `3 → 4` видно 27.89 % / 22.95 % / 24.44 % / 24.72 % — разброс есть. Кроме того, реальная нагрузка зависит не только от числа ключей, но и от их «веса» (сколько запросов на ключ). Больше виртуальных узлов — ровнее, но и больше памяти/времени на кольцо.

**10. Что произойдёт, если один shard станет значительно более загруженным остальных?**  
Возникнет «горячий» узел: он будет обслуживать непропорционально много запросов и упрётся в CPU/IO первым, а остальные простаивают. Решения: разбить горячий ключ/диапазон на несколько шардов (ре-)шардированием, добавить виртуальных узлов и перенести часть дуги, вынести горячие ключи в отдельный шард, либо применить кэш/реплики на чтение (как в лабе 4).

---

# Воспроизведение

```bash
cd flower_shop/flower_shop

# Primary + Replica (лаб. 4) + 3 шарда (лаб. 5) + backend
docker compose up -d --build
docker compose ps

# Часть 2: схема на шардах
docker compose exec backend python manage.py shard_setup

# Часть 4: распределение 100 000 заказов
docker compose exec backend python manage.py shard_load --orders 100000 --clear
docker compose exec backend python manage.py shard_report
# отдельно по узлам:
docker compose exec shard0 psql -U flower -d flower_shop_shard -c "SELECT count(*) FROM shop_order_shard"
docker compose exec shard1 psql -U flower -d flower_shop_shard -c "SELECT count(*) FROM shop_order_shard"
docker compose exec shard2 psql -U flower -d flower_shop_shard -c "SELECT count(*) FROM shop_order_shard"

# Части 5 и 7: сравнение стратегий при 3 -> 4
docker compose exec backend python manage.py shard_experiment --samples 100000
docker compose exec backend python manage.py shard_experiment --samples 100000 --source customers

# API
curl http://localhost:8000/api/shards/report/
curl "http://localhost:8000/api/shards/route/?customer_id=1"
curl "http://localhost:8000/api/shards/plan/?samples=100000"

# Тесты (роутер, кольцо, миграция)
docker compose exec backend python manage.py test shop
```

> Примечание: контейнеры шардов называются `flower_shop_shard0/1/2`; обращаться к ним можно так:
> `docker compose exec shard0 psql -U flower -d flower_shop_shard`.

## Что показать на защите

```
docker compose ps                     -> shard0/shard1/shard2 (healthy) + Primary/Replica
shard_setup                           -> shop_order_shard + индексы на каждом шарде
shard_load --orders 100000            -> 33350 / 33766 / 32884 (отклонение 1.3%)
shard_report                          -> таблица распределения по шардам
/api/shards/route/?customer_id=1      -> клиент -> shard2, чтение только с одного шарда
shard_experiment (modulo)             -> 3->4: 76.10% перемещения
shard_experiment (consistent)         -> 3->4: 24.72% перемещения (в 3.08x меньше)
python manage.py test shop            -> 29 тестов OK (в т.ч. кольцо и vnodes)
```