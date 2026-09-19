"""
ShardReport — распределение данных по шардам (lab 05).

Пример:
  python manage.py shard_report
  python manage.py shard_report --json
"""
import json as json_module

from django.core.management.base import BaseCommand, CommandError

from shop.shard_router import shard_report
from shop.sharding import shards_configured


class Command(BaseCommand):
    help = 'Показать количество записей и клиентов на каждом шарде + баланс.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', help='Вывести JSON')

    def handle(self, *args, **options):
        if not shards_configured():
            raise CommandError('Шарды не сконфигурированы (SHARD0_URL, SHARD1_URL, ...).')

        data = shard_report()

        if options['json']:
            self.stdout.write(json_module.dumps(data, ensure_ascii=False, indent=2, default=str))
            return

        self.stdout.write(self.style.SUCCESS('=== Shard report (lab 05) ==='))
        self.stdout.write(f"Table: {data['table']}")
        self.stdout.write(f"Shard key: {data['key']}")
        self.stdout.write(f"Strategy: {data['strategy']}")
        self.stdout.write('')
        self.stdout.write(f"{'Shard':<10}{'Rows':>12}{'Customers':>12}")
        self.stdout.write('-' * 34)
        for alias in data['shards']:
            rows = data['rows'].get(alias, '—')
            customers = data['distinct_customers'].get(alias, '—')
            self.stdout.write(f'{alias:<10}{rows:>12}{customers:>12}')
        self.stdout.write('-' * 34)
        self.stdout.write(f"{'TOTAL':<10}{data['total_rows']:>12}")

        balance = data.get('balance')
        if balance:
            self.stdout.write('')
            self.stdout.write(
                f"Ideal per shard: {balance['ideal_per_shard']} rows, "
                f"worst shard: {balance['max_shard_rows']} rows, "
                f"deviation: {balance['deviation_percent']}%"
            )

        if data.get('errors'):
            self.stdout.write('')
            self.stdout.write(self.style.ERROR(f"Errors: {data['errors']}"))