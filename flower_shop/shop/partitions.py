"""
Partition helpers for Flower Shop (lab 03).

- shop_order: RANGE by created_at, monthly
- lab_events: RANGE by created_at, daily (job / alert demo)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from django.conf import settings
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)

_IDENT = re.compile(r'^[a-z_][a-z0-9_]*$')

ORDER_TABLE = getattr(settings, 'ORDER_PARTITION_TABLE', 'shop_order')
EVENTS_TABLE = getattr(settings, 'EVENTS_PARTITION_TABLE', 'lab_events')
HORIZON_MONTHS = int(getattr(settings, 'ORDER_PARTITION_HORIZON_MONTHS', 3))
BACKFILL_MONTHS = int(getattr(settings, 'ORDER_PARTITION_BACKFILL_MONTHS', 24))
HORIZON_DAYS = int(getattr(settings, 'EVENTS_PARTITION_HORIZON_DAYS', 3))
BACKFILL_DAYS = int(getattr(settings, 'EVENTS_PARTITION_BACKFILL_DAYS', 2))


def _quote_ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f'Unsafe SQL identifier: {name!r}')
    return name


@dataclass(frozen=True)
class MonthPartition:
    year: int
    month: int
    parent: str = ORDER_TABLE

    @property
    def name(self) -> str:
        return f'{self.parent}_{self.year:04d}_{self.month:02d}'

    @property
    def start(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def end(self) -> date:
        if self.month == 12:
            return date(self.year + 1, 1, 1)
        return date(self.year, self.month + 1, 1)

    def shift(self, delta_months: int) -> 'MonthPartition':
        idx = self.year * 12 + (self.month - 1) + delta_months
        return MonthPartition(
            year=idx // 12,
            month=idx % 12 + 1,
            parent=self.parent,
        )


@dataclass(frozen=True)
class DayPartition:
    day: date
    parent: str = EVENTS_TABLE

    @property
    def name(self) -> str:
        return f'{self.parent}_{self.day.strftime("%Y_%m_%d")}'

    @property
    def start(self) -> date:
        return self.day

    @property
    def end(self) -> date:
        return self.day + timedelta(days=1)

    def shift(self, delta_days: int) -> 'DayPartition':
        return DayPartition(day=self.day + timedelta(days=delta_days), parent=self.parent)


def month_from_date(d: date, parent: str = ORDER_TABLE) -> MonthPartition:
    return MonthPartition(year=d.year, month=d.month, parent=parent)


def required_month_partitions(
    today: date | None = None,
    *,
    horizon_months: int | None = None,
    backfill_months: int | None = None,
    parent: str = ORDER_TABLE,
) -> list[MonthPartition]:
    today = today or timezone.localdate()
    horizon = HORIZON_MONTHS if horizon_months is None else horizon_months
    backfill = BACKFILL_MONTHS if backfill_months is None else backfill_months
    base = month_from_date(today, parent=parent)
    start = base.shift(-backfill)
    end = base.shift(horizon)
    result: list[MonthPartition] = []
    cur = start
    while True:
        result.append(cur)
        if cur == end:
            break
        cur = cur.shift(1)
    return result


def health_month_partitions(
    today: date | None = None,
    *,
    horizon_months: int | None = None,
    parent: str = ORDER_TABLE,
) -> list[MonthPartition]:
    today = today or timezone.localdate()
    horizon = HORIZON_MONTHS if horizon_months is None else horizon_months
    base = month_from_date(today, parent=parent)
    return [base.shift(i) for i in range(0, horizon + 1)]


def required_day_partitions(
    today: date | None = None,
    *,
    horizon_days: int | None = None,
    backfill_days: int | None = None,
    parent: str = EVENTS_TABLE,
) -> list[DayPartition]:
    today = today or timezone.localdate()
    horizon = HORIZON_DAYS if horizon_days is None else horizon_days
    backfill = BACKFILL_DAYS if backfill_days is None else backfill_days
    base = DayPartition(day=today, parent=parent)
    return [base.shift(i) for i in range(-backfill, horizon + 1)]


def health_day_partitions(
    today: date | None = None,
    *,
    horizon_days: int | None = None,
    parent: str = EVENTS_TABLE,
) -> list[DayPartition]:
    today = today or timezone.localdate()
    horizon = HORIZON_DAYS if horizon_days is None else horizon_days
    base = DayPartition(day=today, parent=parent)
    return [base.shift(i) for i in range(0, horizon + 1)]


def list_existing_partitions(parent: str) -> set[str]:
    parent = _quote_ident(parent)
    sql = """
        SELECT c.relname
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname = %s
    """
    with connection.cursor() as cur:
        cur.execute(sql, [parent])
        return {row[0] for row in cur.fetchall()}


def is_partitioned(parent: str) -> bool:
    parent = _quote_ident(parent)
    sql = """
        SELECT 1
        FROM pg_partitioned_table pt
        JOIN pg_class c ON c.oid = pt.partrelid
        WHERE c.relname = %s
    """
    with connection.cursor() as cur:
        cur.execute(sql, [parent])
        return cur.fetchone() is not None


def create_range_partition(name: str, parent: str, start: date, end: date) -> bool:
    name = _quote_ident(name)
    parent = _quote_ident(parent)
    existing = list_existing_partitions(parent)
    if name in existing:
        return False
    ddl = (
        f'CREATE TABLE IF NOT EXISTS {name} '
        f'PARTITION OF {parent} '
        f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
    )
    with connection.cursor() as cur:
        cur.execute(ddl)
    logger.info('Partition created: %s [%s, %s)', name, start, end)
    return True


def ensure_partitions(parts: Iterable[MonthPartition | DayPartition]) -> dict:
    parts = list(parts)
    if not parts:
        return {'existing': [], 'required': [], 'missing_before': [], 'created': []}
    parent = parts[0].parent
    existing_before = list_existing_partitions(parent)
    missing = [p for p in parts if p.name not in existing_before]
    created: list[str] = []
    for p in missing:
        if create_range_partition(p.name, parent, p.start, p.end):
            created.append(p.name)
    return {
        'existing': sorted(existing_before),
        'required': [p.name for p in parts],
        'missing_before': [p.name for p in missing],
        'created': created,
        'parent': parent,
    }


def check_partition_health(
    required: list[MonthPartition | DayPartition],
) -> dict:
    if not required:
        raise ValueError('required partitions list is empty')
    parent = required[0].parent
    existing = list_existing_partitions(parent)
    missing = [p.name for p in required if p.name not in existing]
    status = 'OK' if not missing else 'CRITICAL'
    return {
        'status': status,
        'table': parent,
        'missing': missing,
        'required': [p.name for p in required],
        'existing': sorted(existing),
        'horizon_months': HORIZON_MONTHS if isinstance(required[0], MonthPartition) else None,
        'horizon_days': HORIZON_DAYS if isinstance(required[0], DayPartition) else None,
        'checked_at': timezone.now(),
    }


def drop_partition_if_exists(name: str) -> bool:
    name = _quote_ident(name)
    with connection.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_class WHERE relname = %s AND relkind IN ('r', 'p')",
            [name],
        )
        if not cur.fetchone():
            return False
        cur.execute(f'DROP TABLE IF EXISTS {name}')
    return True


# Backwards-compatible aliases used by early command drafts
required_partitions = required_month_partitions
health_required_partitions = health_month_partitions
PARENT_TABLE = ORDER_TABLE
