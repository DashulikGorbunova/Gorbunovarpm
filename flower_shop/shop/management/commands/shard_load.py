"""
ShardLoad — разложить заказы из Primary по шардам по shard key (lab 05).

Пример:
  python manage.py shard_load --orders 100000
  python manage.py shard_load --orders 300000 --clear
  python manage.py shard_load --orders 100000 --strategy modulo
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from shop.shard_router import count_rows, insert_rows, set_meta, truncate_shard
from shop.sharding import configured_aliases, get_router

SELECT_SQL = """
    SELECT id, customer_id, status, total_amount, delivery_address,
           comment, created_at, updated_at
    FROM shop_order
    ORDER BY created_at DESC
    LIMIT %s
"""


class Command(BaseCommand):
    help = 'Загрузить N заказов с Primary и распределить их по шардам по shard key.'

    def add_arguments(self, parser):
        parser.add_argument('--orders', type=int, default=100000,
                            help='Сколько заказов прочитать с Primary')
        parser.add_argument('--clear', action='store_true',
                            help='Очистить шарды перед загрузкой')
        parser.add_argument('--batch-size', type=int, default=5000,
                            help='Размер пачки вставки')
        parser.add_argument('--strategy', choices=['modulo', 'consistent'], default=None,
                            help='Стратегия размещения (по умолчанию из настроек)')

    def handle(self, *args, **options):
        aliases = configured_aliases()
        if not aliases:
            raise CommandError('Шарды не сконфигурированы (SHARD0_URL, SHARD1_URL, ...).')

        router = get_router(options['strategy'])
        limit = options['orders']
        batch_size = max(1, options['batch_size'])

        self.stdout.write(f'Shard load started: {limit} orders, strategy={router.strategy}')
        self.stdout.write(f"Shards: {', '.join(aliases)}")

        if options['clear']:
            for alias in aliases:
                truncate_shard(alias)
                set_meta(alias, 'loaded_rows', '0')
            self.stdout.write('Shards cleared.')

        buffered = {alias: [] for alias in aliases}
        inserted = {alias: 0 for alias in aliases}
        read = 0
        flush_after = max(batch_size, 20000)

        # Server-side (chunked) cursor keeps memory flat for large --orders.
        with transaction.atomic():
            with connections['default'].chunked_cursor() as cursor:
                cursor.itersize = flush_after
                cursor.execute(SELECT_SQL, [limit])
                for row in cursor:
                    read += 1
                    alias = router.alias_for(row[1])  # row[1] == customer_id
                    buffered[alias].append(row)
                    if len(buffered[alias]) >= flush_after:
                        inserted[alias] += insert_rows(alias, buffered[alias])
                        buffered[alias].clear()
                        self.stdout.write(
                            f'  read={read} (flushed {alias}: {inserted[alias]})'
                        )

        for alias in aliases:
            if buffered[alias]:
                inserted[alias] += insert_rows(alias, buffered[alias])
                buffered[alias].clear()

        for alias in aliases:
            set_meta(alias, 'loaded_rows', str(inserted[alias]))
            set_meta(alias, 'strategy', router.strategy)
            set_meta(alias, 'vnodes', str(router.vnodes))

        self.stdout.write('')
        self.stdout.write(f'Rows read from Primary: {read}')
        for alias in aliases:
            self.stdout.write(f'  {alias}: inserted {inserted[alias]}, now in table {count_rows(alias)}')
        self.stdout.write(self.style.SUCCESS('Shard load finished.'))