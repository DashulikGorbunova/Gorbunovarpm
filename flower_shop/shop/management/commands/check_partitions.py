"""
PartitionHealthCheck — verify partition horizon and send alerts (with dedup).
"""
from django.core.management.base import BaseCommand, CommandError

from shop.alerts import process_health_result
from shop.partitions import (
    EVENTS_TABLE,
    HORIZON_DAYS,
    HORIZON_MONTHS,
    ORDER_TABLE,
    check_partition_health,
    health_day_partitions,
    health_month_partitions,
    is_partitioned,
)


class Command(BaseCommand):
    help = 'PartitionHealthCheck: OK / CRITICAL + alert / recovery.'

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
            help='Required horizon ahead (months or days depending on table)',
        )
        parser.add_argument(
            '--force-alert',
            action='store_true',
            help='Bypass deduplication',
        )
        parser.add_argument(
            '--no-alert',
            action='store_true',
            help='Print status only',
        )

    def handle(self, *args, **options):
        table = options['table']
        parent = ORDER_TABLE if table == 'shop_order' else EVENTS_TABLE

        if not is_partitioned(parent):
            raise CommandError(
                f'{parent} is not partitioned. '
                f'Run the corresponding lab03 SQL setup script first.'
            )

        if table == 'shop_order':
            required = health_month_partitions(
                horizon_months=options['horizon'],
                parent=parent,
            )
            horizon_label = (
                f"{options['horizon'] if options['horizon'] is not None else HORIZON_MONTHS} months"
            )
        else:
            required = health_day_partitions(
                horizon_days=options['horizon'],
                parent=parent,
            )
            horizon_label = (
                f"{options['horizon'] if options['horizon'] is not None else HORIZON_DAYS} days"
            )

        result = check_partition_health(required)
        # unify key used in alert text
        if result.get('horizon_days') is not None:
            result['horizon_months'] = result['horizon_days']  # reuse formatter label below
        result['horizon_label'] = horizon_label

        status = result['status']
        style = self.style.SUCCESS if status == 'OK' else self.style.ERROR
        self.stdout.write(style(f'Partition health: {status}'))
        self.stdout.write(f"Table: {result['table']}")
        self.stdout.write(f'Expected horizon: {horizon_label}')
        self.stdout.write(f"Required: {', '.join(result['required'])}")
        if result['missing']:
            self.stdout.write(self.style.ERROR(f"Missing: {', '.join(result['missing'])}"))
        else:
            self.stdout.write('All required partitions exist.')
        self.stdout.write(f"Checked at: {result['checked_at']}")

        if options['no_alert']:
            return

        action = process_health_result(result, force=options['force_alert'])
        self.stdout.write(f'Alert action: {action}')
