"""
ShardExperiment — сравнение стратегий при изменении числа шардов (lab 05).

Считает, сколько ключей (записей) меняют шард при переходе N -> N+1 для:
  * hash(key) % N  (modulo)
  * consistent hashing (hash ring с virtual nodes)

Пример:
  python manage.py shard_experiment --samples 100000
  python manage.py shard_experiment --source customers --from-shards 3 --to-shards 4
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from shop.sharding import (
    STRATEGY_CONSISTENT,
    STRATEGY_MODULO,
    plan_migration,
    shard_aliases,
)

ORDERS_KEYS_SQL = "SELECT customer_id FROM shop_order ORDER BY created_at DESC LIMIT %s"
CUSTOMERS_KEYS_SQL = "SELECT id FROM shop_customer ORDER BY id LIMIT %s"


class Command(BaseCommand):
    help = 'Сравнить modulo и consistent hashing при изменении количества шардов.'

    def add_arguments(self, parser):
        parser.add_argument('--samples', type=int, default=100000,
                            help='Сколько ключей взять для эксперимента')
        parser.add_argument('--source', choices=['orders', 'customers'], default='orders',
                            help='orders — shard key заказов (записи), customers — уникальные клиенты')
        parser.add_argument('--from-shards', type=int, default=3)
        parser.add_argument('--to-shards', type=int, default=4)
        parser.add_argument('--vnodes', type=int, default=None)

    def handle(self, *args, **options):
        from django.conf import settings

        n_from = options['from_shards']
        n_to = options['to_shards']
        vnodes = options['vnodes'] or int(getattr(settings, 'SHARD_VNODES', 160))
        samples = options['samples']

        if n_to <= n_from:
            raise CommandError('--to-shards должен быть больше --from-shards')

        sql = ORDERS_KEYS_SQL if options['source'] == 'orders' else CUSTOMERS_KEYS_SQL
        with connections['default'].cursor() as cursor:
            cursor.execute(sql, [samples])
            keys = [row[0] for row in cursor.fetchall()]

        if not keys:
            raise CommandError('Нет данных для эксперимента — сначала загрузите заказы.')

        aliases_from = shard_aliases(n_from)
        aliases_to = shard_aliases(n_to)

        self.stdout.write(self.style.SUCCESS('=== Shard migration experiment (lab 05) ==='))
        self.stdout.write(
            f"Source: {'shop_order.customer_id (records)' if options['source'] == 'orders' else 'shop_customer.id (unique keys)'}"
        )
        self.stdout.write(f'Keys in experiment: {len(keys)}')
        self.stdout.write(f'Shards: {n_from} -> {n_to}')
        self.stdout.write('')

        plans = {}
        for strategy in (STRATEGY_MODULO, STRATEGY_CONSISTENT):
            plan = plan_migration(keys, aliases_from, aliases_to, strategy, vnodes=vnodes)
            plans[strategy] = plan

            self.stdout.write(f"--- {strategy}: распределение при {n_from} шардах ---")
            for alias in aliases_from:
                count = plan.distribution_before.get(alias, 0)
                share = count * 100.0 / len(keys)
                self.stdout.write(f'  {alias}: {count} ({share:.2f}%)')
            self.stdout.write(f"--- {strategy}: распределение при {n_to} шардах ---")
            for alias in aliases_to:
                count = plan.distribution_after.get(alias, 0)
                share = count * 100.0 / len(keys)
                self.stdout.write(f'  {alias}: {count} ({share:.2f}%)')
            self.stdout.write('')

        self.stdout.write(self.style.SUCCESS(f'=== Перемещение данных при {n_from} -> {n_to} шардов ==='))
        self.stdout.write(
            f"{'Strategy':<12}{'Moved':>12}{'Kept':>12}{'Moved %':>10}{'Ideal %':>10}"
        )
        self.stdout.write('-' * 56)
        for strategy in (STRATEGY_MODULO, STRATEGY_CONSISTENT):
            plan = plans[strategy]
            self.stdout.write(
                f"{strategy:<12}{plan.moved_keys:>12}{plan.kept_keys:>12}"
                f"{plan.moved_percent:>9.2f}%{plan.ideal_percent:>9.2f}%"
            )
        self.stdout.write('-' * 56)

        modulo_percent = plans[STRATEGY_MODULO].moved_percent
        consistent_percent = plans[STRATEGY_CONSISTENT].moved_percent
        if consistent_percent:
            self.stdout.write(
                f'Consistent hashing перемещает в {modulo_percent / consistent_percent:.2f}x меньше данных.'
            )