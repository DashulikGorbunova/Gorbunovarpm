"""
Sharding integration with Django (lab 05).

The shard key is ``shop_order.customer_id``. Two access paths are provided:

1. **Explicit router** — ``alias_for_customer()`` / ``ShardRepository`` helpers:
   used by the shard commands and by the ``/api/shards/...`` endpoints. This is
   the recommended way, because Django routers cannot see ``WHERE`` clauses of
   an arbitrary queryset and therefore cannot route it reliably.
2. **Django DB router** — ``ShardAwareRouter`` (only active when
   ``SHARDING_ENABLED=1``): routes ``shop.order`` when Django passes an
   ``instance`` hint (``obj.save()``, ``Model.objects.get(pk=...)``).

The sharded data lives in the dedicated table ``shop_order_shard`` on every
shard instance (see ``shard_setup`` / ``shard_load`` commands).
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import connections

from .sharding import get_router, shards_configured

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Router helpers
# --------------------------------------------------------------------------- #
def shard_table() -> str:
    return getattr(settings, 'SHARD_TABLE', 'shop_order_shard')


def shard_key_field() -> str:
    return getattr(settings, 'SHARD_KEY', 'customer_id')


def alias_for_customer(customer_id) -> str:
    """Django alias of the shard that owns this customer."""
    return get_router().alias_for(customer_id)


def shard_index_for_customer(customer_id) -> int:
    return get_router().index_for(customer_id)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    id               bigint      NOT NULL,
    customer_id      bigint      NOT NULL,
    status           varchar(20) NOT NULL,
    total_amount     numeric(12, 2) NOT NULL,
    delivery_address text,
    comment          text,
    created_at       timestamptz NOT NULL,
    updated_at       timestamptz NOT NULL,
    PRIMARY KEY (id)
)
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_shard_order_cust_created "
    "ON {table} (customer_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_shard_order_status_created "
    "ON {table} (status, created_at)",
]

META_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS shard_meta (
    key   text PRIMARY KEY,
    value text
)
"""


def ensure_schema(alias: str) -> None:
    """Create the sharded orders table, indexes and the meta table."""
    table = shard_table()
    with connections[alias].cursor() as cursor:
        cursor.execute(CREATE_TABLE_SQL.format(table=table))
        for statement in INDEX_SQL:
            cursor.execute(statement.format(table=table))
        cursor.execute(META_TABLE_SQL)
    logger.info('Shard schema ensured on %s (%s)', alias, table)


def set_meta(alias: str, key: str, value: str) -> None:
    with connections[alias].cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO shard_meta (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            [key, str(value)],
        )


def get_meta(alias: str) -> dict:
    with connections[alias].cursor() as cursor:
        cursor.execute('SELECT key, value FROM shard_meta ORDER BY key')
        return {key: value for key, value in cursor.fetchall()}


# --------------------------------------------------------------------------- #
# Data access
# --------------------------------------------------------------------------- #
INSERT_SQL = """
INSERT INTO {table}
    (id, customer_id, status, total_amount, delivery_address,
     comment, created_at, updated_at)
VALUES %s
ON CONFLICT (id) DO NOTHING
"""


def insert_rows(alias: str, rows) -> int:
    """Bulk-insert order rows into a shard using psycopg2 execute_values."""
    from psycopg2.extras import execute_values

    rows = list(rows)
    if not rows:
        return 0
    sql = INSERT_SQL.format(table=shard_table())
    with connections[alias].cursor() as cursor:
        execute_values(cursor, sql, rows, page_size=2000)
    return len(rows)


def truncate_shard(alias: str) -> None:
    with connections[alias].cursor() as cursor:
        cursor.execute(f'TRUNCATE TABLE {shard_table()}')


def count_rows(alias: str) -> int:
    with connections[alias].cursor() as cursor:
        cursor.execute(f'SELECT count(*) FROM {shard_table()}')
        return cursor.fetchone()[0]


def count_by_customer(alias: str) -> int:
    """Number of distinct customers stored on this shard."""
    with connections[alias].cursor() as cursor:
        cursor.execute(f'SELECT count(DISTINCT customer_id) FROM {shard_table()}')
        return cursor.fetchone()[0]


def fetch_customer_orders(customer_id, limit: int = 20) -> list[dict]:
    """
    Real read scenario routed by the shard key: orders of one customer are read
    from the shard that owns this customer, not from the all-in-one Primary.
    """
    if not shards_configured():
        raise RuntimeError('shards are not configured')
    alias = alias_for_customer(customer_id)
    sql = f"""
        SELECT id, customer_id, status, total_amount, created_at
        FROM {shard_table()}
        WHERE customer_id = %s
        ORDER BY created_at DESC
        LIMIT %s
    """
    with connections[alias].cursor() as cursor:
        cursor.execute(sql, [customer_id, limit])
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def shard_report() -> dict:
    """Row/customer counts for every shard plus balance metrics."""
    from .sharding import configured_aliases

    aliases = configured_aliases()
    counts: dict[str, int] = {}
    customers: dict[str, int] = {}
    errors: dict[str, str] = {}
    for alias in aliases:
        try:
            counts[alias] = count_rows(alias)
            customers[alias] = count_by_customer(alias)
        except Exception as exc:  # pragma: no cover - depends on live DB
            errors[alias] = str(exc)

    total = sum(counts.values())
    balance = None
    if counts and total:
        ideal = total / len(counts)
        if ideal:
            worst = max(counts.values())
            balance = {
                'ideal_per_shard': round(ideal, 2),
                'max_shard_rows': worst,
                'deviation_percent': round((worst - ideal) * 100.0 / ideal, 2),
            }

    return {
        'table': shard_table(),
        'key': shard_key_field(),
        'strategy': getattr(settings, 'SHARD_STRATEGY', 'consistent'),
        'shards': aliases,
        'rows': counts,
        'distinct_customers': customers,
        'total_rows': total,
        'balance': balance,
        'errors': errors or None,
    }


# --------------------------------------------------------------------------- #
# Optional Django DB router
# --------------------------------------------------------------------------- #
class ShardAwareRouter:
    """
    Django-level router for ``shop.order`` (disabled by default).

    Django calls ``db_for_write(model, instance=...)`` on ``save()`` and
    ``db_for_read(model, instance=...)`` on ``get(pk=...)`` — only there is the
    shard key available. For arbitrary filtered querysets Django gives no key,
    so we return ``None`` and the call falls back to the default database.
    Use the explicit helpers above for reliable sharded access.
    """

    @staticmethod
    def _alias_from_hints(model, hints):
        if not shards_configured():
            return None
        if not getattr(settings, 'SHARDING_ENABLED', False):
            return None
        if f'{model._meta.app_label}.{model._meta.model_name}' != 'shop.order':
            return None
        instance = hints.get('instance')
        key = getattr(instance, shard_key_field(), None) if instance is not None else None
        if key is None:
            return None
        return alias_for_customer(key)

    def db_for_read(self, model, **hints):
        return self._alias_from_hints(model, hints)

    def db_for_write(self, model, **hints):
        return self._alias_from_hints(model, hints)

    def allow_relation(self, obj1, obj2, **hints):
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return None