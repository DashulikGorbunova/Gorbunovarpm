"""
ShardSetup — создать схему шардирования на каждом шарде (lab 05, идемпотентно).

Пример:
  python manage.py shard_setup
"""
from django.core.management.base import BaseCommand, CommandError

from shop.shard_router import ensure_schema, set_meta, shard_key_field, shard_table
from shop.sharding import configured_aliases, get_router


class Command(BaseCommand):
    help = 'Создать таблицу shop_order_shard, индексы и shard_meta на всех шардах.'

    def handle(self, *args, **options):
        aliases = configured_aliases()
        if not aliases:
            raise CommandError(
                'Шарды не сконфигурированы. Задайте SHARD0_URL, SHARD1_URL, SHARD2_URL.'
            )

        router = get_router()
        info = router.describe()

        self.stdout.write('Shard setup started.')
        self.stdout.write(f"Table: {shard_table()}")
        self.stdout.write(f"Shard key: {shard_key_field()}")
        self.stdout.write(f"Strategy: {info['strategy']} (vnodes={info['vnodes']})")
        self.stdout.write(f"Ring points: {info['ring_points']}")
        self.stdout.write('')

        for alias in aliases:
            ensure_schema(alias)
            set_meta(alias, 'strategy', info['strategy'])
            set_meta(alias, 'vnodes', str(info['vnodes']))
            set_meta(alias, 'shard_key', shard_key_field())
            set_meta(alias, 'table', shard_table())
            self.stdout.write(
                self.style.SUCCESS(f'{alias}: schema ready ({shard_table()} + indexes + shard_meta)')
            )

        self.stdout.write(self.style.SUCCESS('Shard setup finished.'))