"""
Read-scaling database router (lab 04: Primary + Replica).

Rules
-----
* Every write and every migration goes to the **Primary** (``default``).
* SELECTs for the models listed in ``settings.READ_REPLICA_MODELS`` go to the
  **Replica** (``replica``) when it is configured and enabled.
* Everything else (auth, sessions, admin, migrations bookkeeping) stays on the
  Primary, which keeps the framework's read-after-write paths consistent.

The Replica is asynchronous: an INSERT on the Primary may not be visible on the
Replica for a short moment (replication lag). Code that needs read-after-write
consistency must either call ``.using("default")`` explicitly or wrap the read
in ``shop.replication.use_primary()``.
"""
from django.conf import settings

from .replication import REPLICA_ALIAS, force_primary


class ReadReplicaRouter:
    """Routes reads to the Replica and writes to the Primary."""

    @staticmethod
    def _replica_ready() -> bool:
        return (
            getattr(settings, 'READ_REPLICA_ENABLED', False)
            and REPLICA_ALIAS in settings.DATABASES
        )

    def db_for_read(self, model, **hints):
        # Explicit alias via hints wins.
        hinted = hints.get('db_alias')
        if hinted:
            return hinted

        if not self._replica_ready() or force_primary():
            return None  # fall back to 'default'

        routed = getattr(settings, 'READ_REPLICA_MODELS', None)
        if routed is None:
            return REPLICA_ALIAS

        key = f'{model._meta.app_label}.{model._meta.model_name}'
        return REPLICA_ALIAS if key in routed else None

    def db_for_write(self, model, **hints):
        return 'default'

    def allow_relation(self, obj1, obj2, **hints):
        aliases = {obj1._state.db, obj2._state.db}
        if aliases <= {'default', REPLICA_ALIAS}:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # Schema changes only on the Primary.
        return db == 'default'
