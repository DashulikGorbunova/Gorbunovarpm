"""
CreatePartitionsJob — create missing RANGE partitions (monthly or daily).
"""
from django.core.management.base import BaseCommand, CommandError

from shop.partitions import (
    EVENTS_TABLE,
    HORIZON_DAYS,
    HORIZON_MONTHS,
    ORDER_TABLE,
    ensure_partitions,
    is_partitioned,
    required_day_partitions,
    required_month_partitions,
)


class Command(BaseCommand):
    help = (
        'CreatePartitionsJob: ensure missing partitions exist '
        '(shop_order monthly / lab_events daily). Idempotent.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--table',
            choices=['shop_order', 'lab_events'],
            default='shop_order',
            help='Parent partitioned table',
        )
        parser.add_argument(
            '--horizon',
            type=int,
            default=None,
            help='Horizon ahead (months for shop_order, days for lab_events)',
        )
        parser.add_argument(
            '--backfill',
            type=int,
            default=None,
            help='How far back to ensure partitions exist',
        )

    def handle(self, *args, **options):
        table = options['table']
        parent = ORDER_TABLE if table == 'shop_order' else EVENTS_TABLE
        self.stdout.write('Partition job started.')

        if not is_partitioned(parent):
            raise CommandError(
                f'{parent} is not partitioned. '
                f'Run the corresponding lab03 SQL setup script first.'
            )

        if table == 'shop_order':
            parts = required_month_partitions(
                horizon_months=options['horizon'],
                backfill_months=options['backfill'],
                parent=parent,
            )
            unit = 'months'
            horizon_show = options['horizon'] if options['horizon'] is not None else HORIZON_MONTHS
        else:
            parts = required_day_partitions(
                horizon_days=options['horizon'],
                backfill_days=options['backfill'],
                parent=parent,
            )
            unit = 'days'
            horizon_show = options['horizon'] if options['horizon'] is not None else HORIZON_DAYS

        result = ensure_partitions(parts)

        self.stdout.write(f'Table: {parent} (horizon={horizon_show} {unit})')
        self.stdout.write(f"Existing partitions: {len(result['existing'])}")
        self.stdout.write(f"Required partitions: {len(result['required'])}")
        self.stdout.write(f"Missing partitions: {len(result['missing_before'])}")

        if result['created']:
            self.stdout.write('Creating:')
            for name in result['created']:
                self.stdout.write(f'  {name}')
                self.stdout.write(
                    self.style.SUCCESS(f'Partition created successfully: {name}')
                )
        else:
            self.stdout.write('No missing partitions.')

        self.stdout.write(self.style.SUCCESS('Partition job finished.'))
