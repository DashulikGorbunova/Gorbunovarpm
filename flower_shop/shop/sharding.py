"""
Sharding core for Flower Shop (lab 05).

Two placement strategies are implemented and compared:

* ``ModuloSharding``    — ``shard = hash(key) % N``;
* ``ConsistentHashing`` — hash ring with virtual nodes.

Why our own ``key_hash`` instead of the built-in ``hash()``: Python randomises
``hash()`` for strings per process (PYTHONHASHSEED), so the same key would land
on different shards in different workers. We use MD5 truncated to 32 bits —
stable, deterministic and spread well enough for sharding.

Shard key of the project is ``shop_order.customer_id`` (see docs/lab-05.md).
"""
from __future__ import annotations

import bisect
import hashlib
from dataclasses import dataclass, field

RING_SIZE = 2 ** 32

STRATEGY_MODULO = 'modulo'
STRATEGY_CONSISTENT = 'consistent'


def key_hash(key) -> int:
    """Deterministic 32-bit hash of a shard key (process-independent)."""
    digest = hashlib.md5(str(key).encode('utf-8')).digest()
    return int.from_bytes(digest[:4], 'big')


@dataclass(frozen=True)
class ModuloSharding:
    """``shard = hash(key) % N`` — the naive strategy from task 3."""

    shards: int

    def __post_init__(self):
        if self.shards < 1:
            raise ValueError('shards must be >= 1')

    def index_for(self, key) -> int:
        return key_hash(key) % self.shards

    def alias_for(self, key, aliases: list[str]) -> str:
        return aliases[self.index_for(key) % len(aliases)]

    def distribution(self, keys, aliases: list[str]) -> dict[str, int]:
        counts = {alias: 0 for alias in aliases}
        for key in keys:
            counts[self.alias_for(key, aliases)] += 1
        return counts


@dataclass
class ConsistentHashing:
    """
    Simplified consistent hash ring.

    Every node is placed on the ring ``vnodes`` times (virtual nodes), which
    smooths the distribution: with few physical nodes the plain ring is very
    uneven, virtual nodes make it close to uniform.
    """

    vnodes: int = 160
    _ring: list[tuple[int, str]] = field(default_factory=list, repr=False)
    _positions: list[int] = field(default_factory=list, repr=False)
    _nodes: set[str] = field(default_factory=set, repr=False)

    # -- ring construction ------------------------------------------------- #
    def _rebuild(self):
        ring: list[tuple[int, str]] = []
        for node in sorted(self._nodes):
            for i in range(self.vnodes):
                ring.append((key_hash(f'{node}#{i}'), node))
        ring.sort(key=lambda point: (point[0], point[1]))
        self._ring = ring
        self._positions = [point[0] for point in ring]

    @property
    def nodes(self) -> list[str]:
        return sorted(self._nodes)

    def add_node(self, node: str) -> None:
        self._nodes.add(node)
        self._rebuild()

    def remove_node(self, node: str) -> None:
        self._nodes.discard(node)
        self._rebuild()

    def node_for(self, key) -> str | None:
        """Closest node clockwise from ``hash(key)`` (with wraparound)."""
        if not self._ring:
            return None
        position = key_hash(key)
        idx = bisect.bisect_left(self._positions, position)
        if idx == len(self._positions):
            idx = 0  # wraparound to the first point on the ring
        return self._ring[idx][1]

    def distribution(self, keys) -> dict[str, int]:
        counts = {node: 0 for node in self._nodes}
        for key in keys:
            node = self.node_for(key)
            if node is not None:
                counts[node] += 1
        return counts


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
class ShardRouter:
    """
    Maps a shard key to one of the configured shard aliases.

    ``strategy='modulo'``     -> hash(key) % N
    ``strategy='consistent'`` -> closest point on the hash ring (virtual nodes)
    """

    def __init__(self, aliases, strategy: str = STRATEGY_CONSISTENT, vnodes: int = 160):
        self.aliases = list(aliases)
        if not self.aliases:
            raise ValueError('no shard aliases configured')
        self.strategy = strategy
        self.vnodes = vnodes
        self._modulo = ModuloSharding(len(self.aliases))
        self._ring = None
        if strategy == STRATEGY_CONSISTENT:
            self._ring = ConsistentHashing(vnodes=vnodes)
            for alias in self.aliases:
                self._ring.add_node(alias)

    @property
    def shards(self) -> int:
        return len(self.aliases)

    def alias_for(self, key) -> str:
        """Django DB alias of the shard that owns ``key``."""
        if self._ring is not None:
            node = self._ring.node_for(key)
            return node if node is not None else self.aliases[0]
        return self._modulo.alias_for(key, self.aliases)

    def index_for(self, key) -> int:
        alias = self.alias_for(key)
        return self.aliases.index(alias) if alias in self.aliases else 0

    def distribution(self, keys) -> dict[str, int]:
        if self._ring is not None:
            return self._ring.distribution(keys)
        return self._modulo.distribution(keys, self.aliases)

    def describe(self) -> dict:
        return {
            'strategy': self.strategy,
            'shards': self.shards,
            'aliases': list(self.aliases),
            'vnodes': self.vnodes if self.strategy == STRATEGY_CONSISTENT else None,
            'ring_points': len(self._ring._ring) if self._ring is not None else None,
        }


# --------------------------------------------------------------------------- #
# Migration (rebalancing) planning
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MigrationPlan:
    """How many keys change their shard when the number of shards changes."""

    strategy: str
    shards_before: int
    shards_after: int
    total_keys: int
    moved_keys: int
    kept_keys: int
    moved_percent: float
    ideal_percent: float
    distribution_before: dict[str, int]
    distribution_after: dict[str, int]

    def as_dict(self) -> dict:
        return {
            'strategy': self.strategy,
            'shards_before': self.shards_before,
            'shards_after': self.shards_after,
            'total_keys': self.total_keys,
            'moved_keys': self.moved_keys,
            'kept_keys': self.kept_keys,
            'moved_percent': round(self.moved_percent, 2),
            'ideal_percent': round(self.ideal_percent, 2),
            'distribution_before': self.distribution_before,
            'distribution_after': self.distribution_after,
        }


def plan_migration(
    keys,
    aliases_before: list[str],
    aliases_after: list[str],
    strategy: str = STRATEGY_CONSISTENT,
    vnodes: int = 160,
) -> MigrationPlan:
    """
    Compare shard placement before and after ``N -> N+k`` shards.

    ``moved_percent`` is the share of keys whose shard assignment changes —
    exactly what has to be physically relocated when a new shard is added.
    """
    keys = list(keys)
    before = ShardRouter(aliases_before, strategy, vnodes)
    after = ShardRouter(aliases_after, strategy, vnodes)

    moved = 0
    for key in keys:
        if before.alias_for(key) != after.alias_for(key):
            moved += 1

    total = len(keys) or 1
    return MigrationPlan(
        strategy=strategy,
        shards_before=len(aliases_before),
        shards_after=len(aliases_after),
        total_keys=len(keys),
        moved_keys=moved,
        kept_keys=len(keys) - moved,
        moved_percent=moved * 100.0 / total,
        ideal_percent=100.0 / len(aliases_after),
        distribution_before=before.distribution(keys),
        distribution_after=after.distribution(keys),
    )


def shard_aliases(count: int, prefix: str = 'shard') -> list[str]:
    """['shard0', 'shard1', ...] for simulation experiments."""
    return [f'{prefix}{i}' for i in range(count)]


# --------------------------------------------------------------------------- #
# Settings-backed factory
# --------------------------------------------------------------------------- #
def configured_aliases() -> list[str]:
    from django.conf import settings
    return list(getattr(settings, 'SHARD_ALIASES', []) or [])


_ROUTER_CACHE: dict = {}


def get_router(strategy: str | None = None, aliases: list[str] | None = None) -> ShardRouter:
    """
    Router built from settings (or explicit arguments), cached per
    (aliases, strategy, vnodes) so repeated lookups don't rebuild the ring.
    """
    from django.conf import settings

    aliases = aliases if aliases is not None else configured_aliases()
    if not aliases:
        raise ValueError('no shards configured (set SHARD0_URL, SHARD1_URL, ...)')
    strategy = strategy or getattr(settings, 'SHARD_STRATEGY', STRATEGY_CONSISTENT)
    vnodes = int(getattr(settings, 'SHARD_VNODES', 160))

    key = (tuple(aliases), strategy, vnodes)
    router = _ROUTER_CACHE.get(key)
    if router is None:
        router = ShardRouter(aliases, strategy, vnodes)
        _ROUTER_CACHE[key] = router
    return router


def shards_configured() -> bool:
    return len(configured_aliases()) > 0