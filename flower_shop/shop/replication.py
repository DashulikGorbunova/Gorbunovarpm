"""
Streaming-replication helpers (lab 04: Primary + Replica).

Small, dependency-free helpers used by the API and the management command:

* ``db_identity(alias)``      — role of a connection (primary / replica, host, port)
* ``replication_status()``    — full status: Primary, Replica, pg_stat_replication, lag
* ``use_primary()``           — context manager to force reads on the Primary

All functions degrade gracefully: if the Replica is not configured or is
unreachable, they return what they can instead of raising.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from threading import local

from django.conf import settings
from django.db import connections

logger = logging.getLogger(__name__)

REPLICA_ALIAS = getattr(settings, 'READ_REPLICA_ALIAS', 'replica')

_state = local()


# --------------------------------------------------------------------------- #
# Force-primary switch (thread/request local)
# --------------------------------------------------------------------------- #
def force_primary() -> bool:
    """True when reads must ignore the Replica for the current thread."""
    return getattr(_state, 'force_primary', False)


@contextmanager
def use_primary():
    """
    Force reads onto the Primary inside this block.

    >>> with use_primary():
    ...     Order.objects.count()  # executed on 'default', not on 'replica'
    """
    previous = force_primary()
    _state.force_primary = True
    try:
        yield
    finally:
        _state.force_primary = previous


# --------------------------------------------------------------------------- #
# Configuration helpers
# --------------------------------------------------------------------------- #
def replica_configured() -> bool:
    return REPLICA_ALIAS in settings.DATABASES


def replica_alias(fallback: str = 'default') -> str:
    """Return the Replica alias, or ``fallback`` when it is not configured."""
    return REPLICA_ALIAS if replica_configured() else fallback


# --------------------------------------------------------------------------- #
# Raw inspection queries
# --------------------------------------------------------------------------- #
def _fetch_one(alias: str, sql: str, params=None):
    with connections[alias].cursor() as cursor:
        cursor.execute(sql, params or [])
        columns = [col[0] for col in cursor.description]
        row = cursor.fetchone()
        return dict(zip(columns, row)) if row else None


def _fetch_all(alias: str, sql: str, params=None):
    with connections[alias].cursor() as cursor:
        cursor.execute(sql, params or [])
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def db_identity(alias: str = 'default') -> dict:
    """Describe the server behind ``alias`` (role, host, port, recovery state)."""
    row = _fetch_one(
        alias,
        """
        SELECT current_database()       AS database,
               current_user             AS user_name,
               inet_server_addr()::text AS host,
               inet_server_port()       AS port,
               pg_is_in_recovery()      AS in_recovery
        """,
    )
    row['alias'] = alias
    row['role'] = 'replica' if row['in_recovery'] else 'primary'
    return row


def primary_lsn(alias: str = 'default') -> str | None:
    row = _fetch_one(alias, "SELECT pg_current_wal_lsn()::text AS lsn")
    return row['lsn'] if row else None


def replication_status() -> dict:
    """
    Aggregate the state of the replication pair.

    Returns a dict with:
      * ``enabled``   — is read scaling turned on and is a Replica configured;
      * ``primary``   — identity of the Primary;
      * ``replica``   — identity + replay position of the Replica;
      * ``senders``   — rows of ``pg_stat_replication`` on the Primary;
      * ``lag_seconds`` / ``lag_bytes`` — replication lag;
      * ``error``     — collected error messages, if any.
    """
    result: dict = {
        'enabled': replica_configured()
        and bool(getattr(settings, 'READ_REPLICA_ENABLED', False)),
        'replica_alias': REPLICA_ALIAS,
        'replica_configured': replica_configured(),
        'primary': None,
        'replica': None,
        'senders': [],
        'primary_lsn': None,
        'lag_seconds': None,
        'lag_bytes': None,
        'error': None,
    }
    errors: list[str] = []

    # ---- Primary side ----------------------------------------------------- #
    try:
        result['primary'] = db_identity('default')
        result['primary_lsn'] = primary_lsn('default')
        result['senders'] = _fetch_all(
            'default',
            """
            SELECT application_name,
                   client_addr::text AS client_addr,
                   state,
                   sync_state,
                   sent_lsn::text   AS sent_lsn,
                   write_lsn::text  AS write_lsn,
                   flush_lsn::text  AS flush_lsn,
                   replay_lsn::text AS replay_lsn,
                   EXTRACT(EPOCH FROM write_lag)::numeric(10, 3)  AS write_lag_seconds,
                   EXTRACT(EPOCH FROM flush_lag)::numeric(10, 3)  AS flush_lag_seconds,
                   EXTRACT(EPOCH FROM replay_lag)::numeric(10, 3) AS replay_lag_seconds,
                   EXTRACT(EPOCH FROM (now() - reply_time))::numeric(10, 3)
                       AS reply_lag_seconds
            FROM pg_stat_replication
            ORDER BY application_name
            """,
        )
    except Exception as exc:  # pragma: no cover - depends on live DB
        errors.append(f'primary: {exc}')

    # ---- Replica side ----------------------------------------------------- #
    if replica_configured():
        try:
            result['replica'] = db_identity(REPLICA_ALIAS)
            replay = _fetch_one(
                REPLICA_ALIAS,
                """
                SELECT pg_last_wal_replay_lsn()::text AS replay_lsn,
                       pg_last_xact_replay_timestamp() AS last_replay_ts,
                       EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp()))
                           ::numeric(10, 3) AS lag_seconds
                """,
            )
            result['replica'].update(replay)

            # How far the Replica is behind in bytes (Primary LSN - replay LSN)
            if result['primary_lsn'] and replay.get('replay_lsn'):
                lag = _fetch_one(
                    'default',
                    "SELECT (%s::pg_lsn - %s::pg_lsn) AS lag_bytes",
                    [result['primary_lsn'], replay['replay_lsn']],
                )
                result['lag_bytes'] = (
                    int(lag['lag_bytes'])
                    if lag and lag['lag_bytes'] is not None
                    else None
                )
        except Exception as exc:  # pragma: no cover - depends on live DB
            errors.append(f'replica: {exc}')

    # ---- Replication lag -------------------------------------------------- #
    # Priority:
    #   1. byte lag == 0  -> the Replica has replayed everything, lag is 0;
    #   2. canonical `replay_lag` from pg_stat_replication on the Primary;
    #   3. finally now() - pg_last_xact_replay_timestamp() on the Replica.
    # Step 1 handles idle pairs (where `replay_lag` is NULL) and stale
    # `replay_lag` right after a pause/resume.
    replay_lag = None
    if result.get('lag_bytes') == 0:
        replay_lag = 0.0
    elif result['senders']:
        value = result['senders'][0].get('replay_lag_seconds')
        if value is not None:
            replay_lag = float(value)
    if replay_lag is None and result.get('replica') and result['replica'].get('lag_seconds') is not None:
        replay_lag = float(result['replica']['lag_seconds'])
    result['lag_seconds'] = replay_lag

    if errors:
        result['error'] = '; '.join(errors)

    return result

