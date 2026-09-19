"""
ReplicationStatus — показать состояние Primary + Replica (lab 04).

Пример:
  python manage.py replication_status
"""
from django.core.management.base import BaseCommand

from shop.replication import replication_status


def _fmt(value):
    if value is None:
        return '—'
    if isinstance(value, float):
        return f'{value:.3f}'
    return str(value)


class Command(BaseCommand):
    help = 'Показать состояние streaming replication: Primary, Replica, pg_stat_replication, lag.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--json',
            action='store_true',
            help='Вывести результат в формате JSON',
        )

    def handle(self, *args, **options):
        data = replication_status()

        if options['json']:
            import json
            self.stdout.write(json.dumps(data, ensure_ascii=False, indent=2, default=str))
            return

        style_ok = self.style.SUCCESS
        style_err = self.style.ERROR

        self.stdout.write(style_ok('=== Replication status (lab 04) ==='))
        self.stdout.write(f"Read scaling enabled : {data['enabled']}")
        self.stdout.write(f"Replica alias        : {data['replica_alias']}")
        self.stdout.write(f"Replica configured    : {data['replica_configured']}")

        primary = data.get('primary')
        if primary:
            self.stdout.write('')
            self.stdout.write(style_ok('--- Primary (default) ---'))
            self.stdout.write(
                f"  role={primary['role']} host={_fmt(primary['host'])} "
                f"port={_fmt(primary['port'])} db={primary['database']}"
            )
            self.stdout.write(f"  current WAL LSN: {_fmt(data.get('primary_lsn'))}")
        else:
            self.stdout.write(style_err('Primary: не удалось получить информацию'))

        self.stdout.write('')
        self.stdout.write(style_ok('--- pg_stat_replication (on Primary) ---'))
        senders = data.get('senders') or []
        if not senders:
            self.stdout.write(style_err('  (пусто — нет подключённых реплик)'))
        for row in senders:
            self.stdout.write(
                f"  app={row['application_name']} client={row['client_addr']} "
                f"state={row['state']} sync={row['sync_state']}"
            )
            self.stdout.write(
                f"    sent={row['sent_lsn']} flush={row['flush_lsn']} "
                f"replay={row['replay_lsn']}"
            )
            self.stdout.write(
                f"    write_lag={_fmt(row.get('write_lag_seconds'))}s "
                f"flush_lag={_fmt(row.get('flush_lag_seconds'))}s "
                f"replay_lag={_fmt(row.get('replay_lag_seconds'))}s "
                f"reply_lag={_fmt(row.get('reply_lag_seconds'))}s"
            )

        replica = data.get('replica')
        self.stdout.write('')
        self.stdout.write(style_ok('--- Replica ---'))
        if replica:
            self.stdout.write(
                f"  role={replica['role']} host={_fmt(replica['host'])} "
                f"port={_fmt(replica['port'])} db={replica['database']}"
            )
            self.stdout.write(f"  in recovery     : {replica['in_recovery']}")
            self.stdout.write(f"  replay LSN      : {_fmt(replica.get('replay_lsn'))}")
            self.stdout.write(f"  last replay ts  : {_fmt(replica.get('last_replay_ts'))}")
            self.stdout.write(f"  lag (seconds)   : {_fmt(data.get('lag_seconds'))}")
            self.stdout.write(f"  lag (bytes)     : {_fmt(data.get('lag_bytes'))}")
        else:
            self.stdout.write(style_err('  Replica не сконфигурирована или недоступна'))

        if data.get('error'):
            self.stdout.write('')
            self.stdout.write(style_err(f"Errors: {data['error']}"))
