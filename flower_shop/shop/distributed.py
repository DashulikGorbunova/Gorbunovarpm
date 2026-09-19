"""
Distributed query patterns over the shards (lab 06).

After sharding, a query falls into one of two categories:

* **single-shard** — the shard key is in the WHERE clause, so the router can
  pick exactly one shard (e.g. ``customer_id = ?``);
* **distributed** — the result must be gathered from several shards and merged
  in the backend: COUNT/SUM/GROUP BY, ORDER BY … LIMIT, JOIN.

This module implements the distributed patterns on top of the existing shards
(``shop_order_shard``) and records per-shard timings so the cost is visible.

Only stdlib + Django ORM raw SQL is used. The merge helpers are pure functions
and are covered by unit tests without a database.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from django.db import connections

from .shard_router import (
    shard_table,
    alias_for_customer,
)
from .sharding import configured_aliases, get_router, shards_configured

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Per-shard execution helper
# --------------------------------------------------------------------------- #
@dataclass
class ShardResult:
    alias: str
    ok: bool
    elapsed_ms: float
    data: object = None
    error: str | None = None


@dataclass
class DistributedResult:
    """Container for a scatter-gather query over the shards."""

    query: str
    per_shard: list[ShardResult] = field(default_factory=list)
    merged: object = None
    distributed: bool = True
    failed_shards: list[str] = field(default_factory=list)

    @property
    def total_ms(self) -> float:
        return round(sum(item.elapsed_ms for item in self.per_shard), 3)

    @property
    def max_ms(self) -> float:
        return round(max((item.elapsed_ms for item in self.per_shard), default=0.0), 3)

    def as_dict(self) -> dict:
        return {
            'query': self.query,
            'distributed': self.distributed,
            'per_shard': [
                {
                    'alias': item.alias,
                    'ok': item.ok,
                    'elapsed_ms': round(item.elapsed_ms, 3),
                    'data': item.data,
                    'error': item.error,
                }
                for item in self.per_shard
            ],
            'failed_shards': self.failed_shards,
            'max_shard_ms': self.max_ms,
            'sum_shard_ms': self.total_ms,
            'merged': self.merged,
        }


def _run_on_shard(alias: str, sql: str, params=None):
    """Execute raw SQL on one shard, measuring the wall time."""
    started = time.perf_counter()
    try:
        with connections[alias].cursor() as cursor:
            cursor.execute(sql, params or [])
            columns = [col[0] for col in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        elapsed = (time.perf_counter() - started) * 1000.0
        return ShardResult(alias, True, elapsed, rows)
    except Exception as exc:  # pragma: no cover - depends on live DB
        elapsed = (time.perf_counter() - started) * 1000.0
        logger.warning('Shard %s failed: %s', alias, exc)
        return ShardResult(alias, False, elapsed, None, str(exc))


def _scatter(query: str, sql: str, params=None) -> DistributedResult:
    aliases = configured_aliases()
    result = DistributedResult(query=query)
    for alias in aliases:
        shard_result = _run_on_shard(alias, sql, params)
        result.per_shard.append(shard_result)
        if not shard_result.ok:
            result.failed_shards.append(alias)
    return result


# --------------------------------------------------------------------------- #
# Pure merge helpers (unit-tested without a database)
# --------------------------------------------------------------------------- #
def merge_count(values) -> int:
    """COUNT(*) over shards = sum of per-shard counts."""
    return sum(int(v or 0) for v in values)


def merge_sum(values):
    """SUM(x) over shards = sum of per-shard sums (NULL -> 0)."""
    return sum(v for v in values if v is not None)


def merge_group_by(per_shard_rows, key_field: str, value_field: str) -> dict:
    """SUM(value) GROUP BY key: add per-shard groups with the same key."""
    merged: dict = {}
    for rows in per_shard_rows:
        for row in rows or []:
            key = row.get(key_field)
            merged[key] = merged.get(key, 0) + (row.get(value_field) or 0)
    return dict(sorted(merged.items(), key=lambda kv: str(kv[0])))


def merge_top(per_shard_rows, limit: int, key_fields=('created_at', 'id')) -> list:
    """
    Global top-N: take each shard's top-N, then merge and keep the global top-N.

    This is why the first N rows of one shard are not enough — the global top
    may be spread across several shards.
    """
    combined: list = []
    for rows in per_shard_rows:
        combined.extend(rows or [])

    def sort_key(row):
        return tuple(row.get(name) for name in key_fields)

    combined.sort(key=sort_key, reverse=True)
    return combined[:limit]


# --------------------------------------------------------------------------- #
# Single-shard query
# --------------------------------------------------------------------------- #
def customer_orders_single_shard(customer_id, limit: int = 20) -> dict:
    """
    Single-shard query: ``WHERE customer_id = ?`` and customer_id is the shard
    key, so the router sends it to exactly one shard.
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
    started = time.perf_counter()
    result = _run_on_shard(alias, sql, [customer_id, limit])
    elapsed = (time.perf_counter() - started) * 1000.0
    return {
        'query': 'WHERE customer_id = %s (single-shard)',
        'distributed': False,
        'shard_alias': alias,
        'shards_touched': 1,
        'elapsed_ms': round(elapsed, 3),
        'ok': result.ok,
        'error': result.error,
        'rows': result.data,
    }


# --------------------------------------------------------------------------- #
# Distributed aggregation
# --------------------------------------------------------------------------- #
def distributed_count() -> DistributedResult:
    """COUNT(*) over all shards."""
    result = _scatter(
        'SELECT count(*) FROM ' + shard_table(),
        f'SELECT count(*) AS value FROM {shard_table()}',
    )
    result.merged = merge_count([item.data[0]['value'] for item in result.per_shard if item.ok])
    return result


def distributed_sum(column: str = 'total_amount') -> DistributedResult:
    """SUM(column) over all shards."""
    result = _scatter(
        f'SELECT sum({column}) FROM ' + shard_table(),
        f'SELECT sum({column}) AS value FROM {shard_table()}',
    )
    result.merged = merge_sum([item.data[0]['value'] for item in result.per_shard if item.ok])
    return result


def distributed_group_by_status() -> DistributedResult:
    """COUNT(*) GROUP BY status — each shard returns local groups, backend merges."""
    result = _scatter(
        'SELECT status, count(*) FROM ' + shard_table() + ' GROUP BY status',
        f'SELECT status, count(*) AS value FROM {shard_table()} GROUP BY status',
    )
    result.merged = merge_group_by(
        [item.data for item in result.per_shard if item.ok], 'status', 'value'
    )
    return result


# --------------------------------------------------------------------------- #
# Distributed ORDER BY ... LIMIT
# --------------------------------------------------------------------------- #
def distributed_top_orders(limit: int = 20) -> DistributedResult:
    """
    ORDER BY created_at DESC LIMIT :limit over shards = top-N per shard + merge.
    """
    sql = f"""
        SELECT id, customer_id, status, total_amount, created_at
        FROM {shard_table()}
        ORDER BY created_at DESC, id DESC
        LIMIT {int(limit)}
    """
    result = _scatter(
        f'ORDER BY created_at DESC LIMIT {limit} (per-shard top-{limit} + merge)',
        sql,
    )
    result.merged = merge_top(
        [item.data for item in result.per_shard if item.ok], limit
    )
    for item in result.per_shard:
        item.data = item.data[:3]  # keep the response small
    return result


# --------------------------------------------------------------------------- #
# Cross-shard JOIN
# --------------------------------------------------------------------------- #
def _fetch_one(alias: str, sql: str, params=None):
    with connections[alias].cursor() as cursor:
        cursor.execute(sql, params or [])
        columns = [col[0] for col in cursor.description]
        row = cursor.fetchone()
        return dict(zip(columns, row)) if row else None


def cross_shard_join_demo(customer_id, limit: int = 10) -> dict:
    """
    JOIN whose tables live on different nodes: ``shop_order_shard`` (shards) and
    ``shop_customer`` (Primary, not sharded).

    1. Probe: the customer table does not exist on the shards, so a plain SQL
       JOIN cannot be executed on any single shard.
    2. Workaround: read orders from the owning shard, read the customer from the
       Primary and join the two result sets in the backend.
    """
    if not shards_configured():
        raise RuntimeError('shards are not configured')
    aliases = configured_aliases()
    alias = alias_for_customer(customer_id)

    # 1. probe — is the joined table available on the shards?
    probe = {}
    for shard in aliases:
        try:
            _fetch_one(shard, 'SELECT 1 FROM shop_customer LIMIT 1')
            probe[shard] = 'available'
        except Exception as exc:  # pragma: no cover - depends on live DB
            probe[shard] = str(exc).splitlines()[0]

    # 2. orders from the owning shard
    started = time.perf_counter()
    with connections[alias].cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, customer_id, status, total_amount, created_at
            FROM {shard_table()}
            WHERE customer_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            [customer_id, limit],
        )
        columns = [col[0] for col in cursor.description]
        orders = [dict(zip(columns, row)) for row in cursor.fetchall()]
    orders_ms = (time.perf_counter() - started) * 1000.0

    # 3. customer from the Primary, then join in the backend
    started = time.perf_counter()
    customer = _fetch_one(
        'default',
        'SELECT id, first_name, last_name, email FROM shop_customer WHERE id = %s',
        [customer_id],
    )
    customer_ms = (time.perf_counter() - started) * 1000.0

    joined = [
        {
            **row,
            'customer_name': (
                f"{customer['first_name']} {customer['last_name']}" if customer else None
            ),
            'customer_email': customer['email'] if customer else None,
        }
        for row in orders
    ]

    return {
        'query': 'shop_order_shard (shards) JOIN shop_customer (Primary)',
        'distributed': True,
        'backend_join': True,
        'orders_shard': alias,
        'customer_source': 'default (Primary)',
        'joined_table_available_on_shards': probe,
        'orders_ms': round(orders_ms, 3),
        'customer_ms': round(customer_ms, 3),
        'rows': joined,
    }


# --------------------------------------------------------------------------- #
# Hot shard simulation
# --------------------------------------------------------------------------- #
def _fetch_group(alias: str, sql: str):
    with connections[alias].cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchall()


def top_customers_by_orders(top_n: int = 10) -> list[dict]:
    """Merge per-shard ``GROUP BY customer_id`` to find the busiest customers."""
    per_shard = []
    for alias in configured_aliases():
        try:
            per_shard.append(_fetch_group(
                alias,
                f'SELECT customer_id, count(*) AS value FROM {shard_table()} '
                f'GROUP BY customer_id',
            ))
        except Exception as exc:  # pragma: no cover - depends on live DB
            logger.warning('top_customers_by_orders: %s failed: %s', alias, exc)

    merged = merge_group_by(
        [[{'customer_id': cid, 'value': value} for cid, value in rows] for rows in per_shard],
        'customer_id',
        'value',
    )
    top = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return [
        {'customer_id': cid, 'orders': value, 'shard': alias_for_customer(cid)}
        for cid, value in top
    ]


def simulate_hot_shard(
    requests: int = 10000,
    hot_requests: int = 9000,
    hot_customers: int = 10,
) -> dict:
    """
    Show that equal row counts do not mean equal load.

    ``hot_requests`` of ``requests`` go to the busiest customers (a "popular
    user/tenant"); the rest are spread over all customers. The router sends a
    customer to a fixed shard, so the popular customer makes its shard hot.
    """
    import random

    aliases = configured_aliases()
    hot = top_customers_by_orders(hot_customers)
    hot_ids = [item['customer_id'] for item in hot]

    all_ids: list = []
    for alias in aliases:
        try:
            all_ids.extend(row[0] for row in _fetch_group(
                alias, f'SELECT DISTINCT customer_id FROM {shard_table()}'
            ))
        except Exception as exc:  # pragma: no cover - depends on live DB
            logger.warning('simulate_hot_shard: %s failed: %s', alias, exc)

    if not all_ids:
        raise RuntimeError('no sharded data — run shard_load first')

    # Resolve the shard once per customer instead of once per request.
    router = get_router()
    shard_of = {cid: router.alias_for(cid) for cid in set(all_ids)}

    counts = {alias: 0 for alias in aliases}
    for i in range(requests):
        if i < hot_requests and hot_ids:
            customer_id = random.choice(hot_ids)
        else:
            customer_id = random.choice(all_ids)
        counts[shard_of[customer_id]] += 1

    from .shard_router import count_rows

    rows = {}
    for alias in aliases:
        try:
            rows[alias] = count_rows(alias)
        except Exception as exc:  # pragma: no cover - depends on live DB
            logger.warning('simulate_hot_shard rows: %s failed: %s', alias, exc)

    total_rows = sum(rows.values()) or 1
    return {
        'requests': requests,
        'hot_requests': hot_requests,
        'hot_customers': hot,
        'requests_per_shard': counts,
        'request_share_percent': {
            alias: round(count * 100.0 / requests, 2) for alias, count in counts.items()
        },
        'row_share_percent': {
            alias: round(value * 100.0 / total_rows, 2) for alias, value in rows.items()
        },
        'rows_per_shard': rows,
        'verdict': (
            'hot shard'
            if counts and max(counts.values()) > 1.5 * min(counts.values())
            else 'balanced'
        ),
    }


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #
def routing_summary() -> dict:
    """Describe the router and classify the service queries."""
    router = get_router()
    return {
        'router': router.describe(),
        'where_merging_happens': 'backend (application layer)',
        'single_shard_queries': [
            'GET /api/shards/route/?customer_id=?  -> WHERE customer_id = ?',
            'GET /api/customers/{id}/orders/       -> WHERE customer_id = ?',
            'GET /api/orders/?customer=?           -> WHERE customer_id = ?',
        ],
        'distributed_queries': [
            'COUNT(*) / SUM() / GROUP BY по всем заказам',
            'ORDER BY created_at DESC LIMIT n (глобальный топ)',
            'JOIN заказов (шарды) с клиентами (Primary)',
            'GET /api/orders/ (список без фильтра по клиенту)',
        ],
    }