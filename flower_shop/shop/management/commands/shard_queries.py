"""
ShardQueries — анализ запросов после шардирования (lab 06).

Прогоняет на реальных данных:
  * single-shard query (WHERE customer_id = ?);
  * распределённую агрегацию (COUNT / SUM / GROUP BY + merge);
  * распределённый ORDER BY ... LIMIT (per-shard top-N + merge);
  * cross-shard JOIN (шарды + Primary, join в backend);
  * симуляцию hot shard.

Пример:
  python manage.py shard_queries
  python manage.py shard_queries --customer-id 1 --limit 20 --json
"""
import json as json_module

from django.core.management.base import BaseCommand, CommandError

from shop.distributed import (
    cross_shard_join_demo,
    customer_orders_single_shard,
    distributed_count,
    distributed_group_by_status,
    distributed_sum,
    distributed_top_orders,
    routing_summary,
    simulate_hot_shard,
)
from shop.sharding import shards_configured


class Command(BaseCommand):
    help = 'Сравнить single-shard и distributed запросы на реальных шардах.'

    def add_arguments(self, parser):
        parser.add_argument('--customer-id', type=int, default=1)
        parser.add_argument('--limit', type=int, default=20)
        parser.add_argument('--requests', type=int, default=10000)
        parser.add_argument('--json', action='store_true')

    def handle(self, *args, **options):
        if not shards_configured():
            raise CommandError('Шарды не сконфигурированы (SHARD0_URL, ...).')

        customer_id = options['customer_id']
        limit = options['limit']
        payload = {'summary': routing_summary()}

        # ---- 2. single-shard -------------------------------------------- #
        payload['single_shard'] = customer_orders_single_shard(customer_id, limit=limit)

        # ---- 3. distributed aggregation --------------------------------- #
        count = distributed_count()
        total = distributed_sum()
        grouped = distributed_group_by_status()
        payload['aggregation'] = {
            'count': count.as_dict(),
            'sum_total_amount': total.as_dict(),
            'group_by_status': grouped.as_dict(),
        }

        # ---- 5. distributed ORDER BY + LIMIT ---------------------------- #
        payload['order_by_limit'] = distributed_top_orders(limit=limit).as_dict()

        # ---- 4. cross-shard JOIN ---------------------------------------- #
        payload['cross_shard_join'] = cross_shard_join_demo(customer_id, limit=5)

        # ---- 7. hot shard ----------------------------------------------- #
        payload['hot_shard'] = simulate_hot_shard(requests=options['requests'])

        if options['json']:
            self.stdout.write(
                json_module.dumps(payload, ensure_ascii=False, indent=2, default=str)
            )
            return

        self._render(payload, customer_id, limit)

    def _render(self, payload, customer_id, limit):
        self.stdout.write(self.style.SUCCESS('=== Lab 06: запросы после шардирования ==='))
        style = self.style
        s = payload['summary']

        self.stdout.write(
            f"Router: strategy={s['router']['strategy']} shards={s['router']['shards']} "
            f"vnodes={s['router']['vnodes']} ring_points={s['router']['ring_points']}"
        )
        self.stdout.write(f"Где объединяются результаты: {s['where_merging_happens']}")
        self.stdout.write('')

        # 2. single-shard
        single = payload['single_shard']
        self.stdout.write(style.SUCCESS('--- 2. Single-shard query ---'))
        self.stdout.write(f"WHERE customer_id = {customer_id}  ->  shard: {single['shard_alias']}")
        self.stdout.write(f"Затронуто шардов: {single['shards_touched']} (остальные не читаются)")
        self.stdout.write(f"Время: {single['elapsed_ms']} ms, строк: {len(single['rows'] or [])}")
        self.stdout.write('')

        # 3. aggregation
        agg = payload['aggregation']
        self.stdout.write(style.SUCCESS('--- 3. Распределённая агрегация ---'))
        self.stdout.write(f"COUNT(*) по шардам: {agg['count']['per_shard']}")
        self.stdout.write(f"  merged COUNT(*) = {agg['count']['merged']}")
        self.stdout.write(f"SUM(total_amount) по шардам: {agg['sum_total_amount']['per_shard']}")
        self.stdout.write(f"  merged SUM = {agg['sum_total_amount']['merged']}")
        self.stdout.write(f"GROUP BY status по шардам: {agg['group_by_status']['per_shard']}")
        self.stdout.write(f"  merged groups = {agg['group_by_status']['merged']}")
        self.stdout.write(
            f"  суммарно по шардам: {agg['count']['sum_shard_ms']} ms, "
            f"максимум на шарде: {agg['count']['max_shard_ms']} ms"
        )
        self.stdout.write('')

        # 5. order by + limit
        d = payload['order_by_limit']
        self.stdout.write(style.SUCCESS(f'--- 5. ORDER BY + LIMIT {limit} ---'))
        for item in d['per_shard']:
            first = item['data'][0]['id'] if item['data'] else '—'
            self.stdout.write(f"  {item['alias']}: top-1 id = {first} ({item['elapsed_ms']} ms)")
        self.stdout.write(f"  merged top-{limit}: первые id = {[r['id'] for r in d['merged'][:5]]}")
        self.stdout.write(
            f"  max на шарде: {d['max_shard_ms']} ms, суммарно: {d['sum_shard_ms']} ms"
        )
        self.stdout.write('')

        # 4. cross-shard join
        j = payload['cross_shard_join']
        self.stdout.write(style.SUCCESS('--- 4. Cross-shard JOIN ---'))
        self.stdout.write(
            f"Заказы: {j['orders_shard']} ({j['orders_ms']} ms), "
            f"клиент: {j['customer_source']} ({j['customer_ms']} ms)"
        )
        for alias, state in j['joined_table_available_on_shards'].items():
            self.stdout.write(f"  shop_customer на {alias}: {state}")
        self.stdout.write(f"  join выполнен в backend, строк: {len(j['rows'])}")
        self.stdout.write('')

        # 7. hot shard
        h = payload['hot_shard']
        self.stdout.write(style.SUCCESS('--- 7. Hot shard simulation ---'))
        self.stdout.write(
            f"Запросов: {h['requests']} (из них {h['hot_requests']} — к популярным клиентам)"
        )
        self.stdout.write(f"Доля строк по шардам:    {h['row_share_percent']}")
        self.stdout.write(f"Доля запросов по шардам: {h['request_share_percent']}")
        self.stdout.write(f"Вердикт: {h['verdict']}")

        self.stdout.write('')
        self.stdout.write('Single-shard запросы:')
        for q in s['single_shard_queries']:
            self.stdout.write(f'  + {q}')
        self.stdout.write('Distributed запросы:')
        for q in s['distributed_queries']:
            self.stdout.write(f'  - {q}')