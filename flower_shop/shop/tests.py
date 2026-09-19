"""
Tests for the read-scaling router and replication helpers (lab 04).

These are pure ``SimpleTestCase`` tests: they exercise the routing decisions
without touching a database, so they run even in an environment without the
Primary/Replica pair.
"""
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.test import SimpleTestCase, override_settings

from shop.db_router import ReadReplicaRouter
from shop.models import Category, Order, Product
from shop.replication import use_primary
from shop.sharding import (
    STRATEGY_CONSISTENT,
    STRATEGY_MODULO,
    ConsistentHashing,
    ModuloSharding,
    ShardRouter,
    key_hash,
    plan_migration,
    shard_aliases,
)
from shop.distributed import (
    merge_count,
    merge_group_by,
    merge_sum,
    merge_top,
)


class ReadReplicaRouterTests(SimpleTestCase):
    def setUp(self):
        self.router = ReadReplicaRouter()

    # --- writes ---------------------------------------------------------- #
    def test_writes_always_go_to_primary(self):
        self.assertEqual(self.router.db_for_write(Order), 'default')
        self.assertEqual(self.router.db_for_write(Product), 'default')
        self.assertEqual(self.router.db_for_write(Category), 'default')

    # --- reads ----------------------------------------------------------- #
    @override_settings(READ_REPLICA_MODELS=['shop.order', 'shop.product'])
    def test_reads_of_routed_models_go_to_replica(self):
        with mock.patch.object(ReadReplicaRouter, '_replica_ready', return_value=True):
            self.assertEqual(self.router.db_for_read(Order), 'replica')
            self.assertEqual(self.router.db_for_read(Product), 'replica')

    @override_settings(READ_REPLICA_MODELS=['shop.order', 'shop.product'])
    def test_reads_of_other_models_stay_on_primary(self):
        with mock.patch.object(ReadReplicaRouter, '_replica_ready', return_value=True):
            # Category is intentionally not routed in this override
            self.assertIsNone(self.router.db_for_read(Category))
            # Framework models (auth) must never go to the Replica
            self.assertIsNone(self.router.db_for_read(User))

    @override_settings(READ_REPLICA_MODELS=['shop.order'])
    def test_reads_fall_back_to_primary_when_replica_disabled(self):
        with mock.patch.object(ReadReplicaRouter, '_replica_ready', return_value=False):
            self.assertIsNone(self.router.db_for_read(Order))

    @override_settings(READ_REPLICA_MODELS=['shop.order'])
    def test_use_primary_forces_primary_reads(self):
        with mock.patch.object(ReadReplicaRouter, '_replica_ready', return_value=True):
            self.assertEqual(self.router.db_for_read(Order), 'replica')
            with use_primary():
                self.assertIsNone(self.router.db_for_read(Order))
            self.assertEqual(self.router.db_for_read(Order), 'replica')

    @override_settings(READ_REPLICA_MODELS=['shop.order'])
    def test_explicit_hint_wins(self):
        with mock.patch.object(ReadReplicaRouter, '_replica_ready', return_value=True):
            self.assertEqual(
                self.router.db_for_read(Order, db_alias='default'),
                'default',
            )

    # --- migrations / relations ------------------------------------------ #
    def test_migrations_only_on_primary(self):
        self.assertTrue(self.router.allow_migrate('default', 'shop'))
        self.assertFalse(self.router.allow_migrate('replica', 'shop'))

    def test_relation_allowed_within_replication_pair(self):
        order = Order()
        product = Product()
        order._state.db = 'replica'
        product._state.db = 'default'
        self.assertTrue(self.router.allow_relation(order, product))

    def test_replica_ready_detects_configuration(self):
        with mock.patch.dict(
            settings.DATABASES,
            {'replica': {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'x'}},
        ), mock.patch.object(settings, 'READ_REPLICA_ENABLED', True):
            self.assertTrue(self.router._replica_ready())

        with mock.patch.dict(
            settings.DATABASES, {}, clear=True,
        ), mock.patch.object(settings, 'READ_REPLICA_ENABLED', True):
            self.assertFalse(self.router._replica_ready())


class ShardingKeyHashTests(SimpleTestCase):
    def test_key_hash_is_deterministic(self):
        # Must not depend on PYTHONHASHSEED (unlike the built-in hash()).
        self.assertEqual(key_hash('101'), key_hash('101'))
        self.assertEqual(key_hash(101), key_hash('101'))

    def test_key_hash_fits_in_32_bits(self):
        for key in ('a', 1, 999999999, 'customer:42'):
            value = key_hash(key)
            self.assertGreaterEqual(value, 0)
            self.assertLess(value, 2 ** 32)


class ModuloShardingTests(SimpleTestCase):
    def test_index_matches_formula(self):
        sharding = ModuloSharding(3)
        for key in range(1, 100):
            self.assertEqual(sharding.index_for(key), key_hash(key) % 3)

    def test_index_is_always_inside_range(self):
        sharding = ModuloSharding(4)
        for key in range(1, 500):
            self.assertIn(sharding.index_for(key), range(4))

    def test_distribution_is_roughly_uniform(self):
        sharding = ModuloSharding(3)
        counts = sharding.distribution(range(1, 30001), shard_aliases(3))
        total = sum(counts.values())
        for count in counts.values():
            share = count * 100.0 / total
            self.assertLess(abs(share - 33.33), 3.0, counts)

    def test_rejects_zero_shards(self):
        with self.assertRaises(ValueError):
            ModuloSharding(0)


class ConsistentHashingTests(SimpleTestCase):
    def setUp(self):
        self.ring = ConsistentHashing(vnodes=160)
        for alias in shard_aliases(3):
            self.ring.add_node(alias)

    def test_every_key_lands_on_a_known_node(self):
        for key in range(1, 1000):
            self.assertIn(self.ring.node_for(key), self.ring.nodes)

    def test_mapping_is_stable_for_same_ring(self):
        other = ConsistentHashing(vnodes=160)
        for alias in shard_aliases(3):
            other.add_node(alias)
        for key in range(1, 500):
            self.assertEqual(self.ring.node_for(key), other.node_for(key))

    def test_empty_ring_returns_none(self):
        self.assertIsNone(ConsistentHashing().node_for(1))

    def test_removing_node_returns_its_keys(self):
        sharding = ConsistentHashing(vnodes=160)
        sharding.add_node('shard0')
        sharding.add_node('shard1')
        moved = sum(1 for k in range(1, 2000) if sharding.node_for(k) == 'shard1')
        sharding.remove_node('shard1')
        self.assertEqual(sharding.nodes, ['shard0'])
        for key in range(1, 2000):
            self.assertEqual(sharding.node_for(key), 'shard0')
        self.assertGreater(moved, 0)

    def test_virtual_nodes_improve_balance(self):
        keys = list(range(1, 20001))

        one_point = ConsistentHashing(vnodes=1)
        for alias in shard_aliases(3):
            one_point.add_node(alias)
        coarse = one_point.distribution(keys)
        coarse_spread = max(coarse.values()) - min(coarse.values())

        many_points = ConsistentHashing(vnodes=160)
        for alias in shard_aliases(3):
            many_points.add_node(alias)
        fine = many_points.distribution(keys)
        fine_spread = max(fine.values()) - min(fine.values())

        self.assertLess(fine_spread, coarse_spread)
        total = sum(fine.values())
        for count in fine.values():
            self.assertLess(abs(count * 100.0 / total - 33.33), 6.0)


class ShardRouterTests(SimpleTestCase):
    def test_alias_and_index_are_consistent(self):
        router = ShardRouter(shard_aliases(3), STRATEGY_CONSISTENT, vnodes=160)
        for key in range(1, 200):
            alias = router.alias_for(key)
            self.assertIn(alias, router.aliases)
            self.assertEqual(router.aliases[router.index_for(key)], alias)

    def test_modulo_and_consistent_may_differ(self):
        keys = range(1, 500)
        modulo = ShardRouter(shard_aliases(3), STRATEGY_MODULO)
        consistent = ShardRouter(shard_aliases(3), STRATEGY_CONSISTENT, vnodes=160)
        differences = sum(1 for k in keys if modulo.alias_for(k) != consistent.alias_for(k))
        self.assertGreater(differences, 0)

    def test_describe_lists_ring_points(self):
        info = ShardRouter(shard_aliases(3), STRATEGY_CONSISTENT, vnodes=10).describe()
        self.assertEqual(info['shards'], 3)
        self.assertEqual(info['ring_points'], 30)
        self.assertEqual(info['vnodes'], 10)

    def test_requires_aliases(self):
        with self.assertRaises(ValueError):
            ShardRouter([], STRATEGY_CONSISTENT)


class MigrationPlanTests(SimpleTestCase):
    KEYS = list(range(1, 20001))

    def test_modulo_moves_most_keys_when_shards_change(self):
        plan = plan_migration(
            self.KEYS, shard_aliases(3), shard_aliases(4), STRATEGY_MODULO
        )
        # Only keys with hash%3 == hash%4 keep their place (~1/N).
        self.assertGreater(plan.moved_percent, 70.0)
        self.assertEqual(plan.moved_keys + plan.kept_keys, plan.total_keys)

    def test_consistent_moves_about_one_over_new_shards(self):
        plan = plan_migration(
            self.KEYS, shard_aliases(3), shard_aliases(4), STRATEGY_CONSISTENT, vnodes=160
        )
        self.assertLess(abs(plan.moved_percent - 25.0), 5.0)

    def test_consistent_moves_less_than_modulo(self):
        modulo = plan_migration(
            self.KEYS, shard_aliases(3), shard_aliases(4), STRATEGY_MODULO
        )
        consistent = plan_migration(
            self.KEYS, shard_aliases(3), shard_aliases(4), STRATEGY_CONSISTENT, vnodes=160
        )
        self.assertLess(consistent.moved_keys, modulo.moved_keys)

    def test_new_shard_only_receives_data_under_consistent_hashing(self):
        ring_before = ShardRouter(shard_aliases(3), STRATEGY_CONSISTENT, 160)
        ring_after = ShardRouter(shard_aliases(4), STRATEGY_CONSISTENT, 160)
        for key in self.KEYS:
            before = ring_before.alias_for(key)
            after = ring_after.alias_for(key)
            if before != after:
                # Old shards never exchange keys with each other.
                self.assertEqual(after, 'shard3')

    def test_plan_dict_is_json_ready(self):
        plan = plan_migration(
            self.KEYS, shard_aliases(3), shard_aliases(4), STRATEGY_CONSISTENT
        )
        data = plan.as_dict()
        self.assertEqual(data['strategy'], STRATEGY_CONSISTENT)
        self.assertEqual(data['shards_before'], 3)
        self.assertEqual(data['shards_after'], 4)
        self.assertIn('shard2', data['distribution_before'])


class DistributedMergeTests(SimpleTestCase):
    """Merge logic that runs in the backend after a scatter-gather query."""

    def test_merge_count_is_sum_of_shards(self):
        self.assertEqual(merge_count([120, 150, 130]), 400)
        self.assertEqual(merge_count([None, 5, None]), 5)
        self.assertEqual(merge_count([]), 0)

    def test_merge_sum_ignores_nulls(self):
        self.assertEqual(merge_sum([10, None, 32, 8]), 50)
        self.assertEqual(merge_sum([None, None]), 0)

    def test_merge_group_by_adds_same_keys(self):
        shard0 = [{'status': 'NEW', 'value': 10}, {'status': 'PAID', 'value': 5}]
        shard1 = [{'status': 'NEW', 'value': 7}, {'status': 'DELIVERED', 'value': 3}]
        shard2 = [{'status': 'NEW', 'value': 1}, {'status': 'PAID', 'value': 2}]
        merged = merge_group_by([shard0, shard1, shard2], 'status', 'value')
        self.assertEqual(merged, {'DELIVERED': 3, 'NEW': 18, 'PAID': 7})

    def test_merge_group_by_handles_empty_shards(self):
        merged = merge_group_by([[], None, [{'status': 'NEW', 'value': 4}]], 'status', 'value')
        self.assertEqual(merged, {'NEW': 4})

    def test_merge_top_returns_global_top_n(self):
        # Each shard has its own top rows; the global top must interleave them.
        shard0 = [{'id': 10, 'created_at': 100}, {'id': 7, 'created_at': 70}]
        shard1 = [{'id': 9, 'created_at': 90}, {'id': 5, 'created_at': 50}]
        shard2 = [{'id': 8, 'created_at': 80}, {'id': 6, 'created_at': 60}]
        merged = merge_top([shard0, shard1, shard2], 3)
        self.assertEqual([row['id'] for row in merged], [10, 9, 8])

    def test_single_shard_top_n_is_not_the_global_top_n(self):
        # Per-shard tops...
        shard0 = [{'id': 10, 'created_at': 100}]
        shard1 = [{'id': 9, 'created_at': 90}]
        shard2 = [{'id': 8, 'created_at': 80}]

        # ...the global top-1 is the maximum across ALL shards, not one of them.
        self.assertEqual([row['id'] for row in merge_top([shard0], 1)], [10])
        self.assertEqual([row['id'] for row in merge_top([shard2], 1)], [8])
        self.assertEqual(
            [row['id'] for row in merge_top([shard0, shard1, shard2], 1)],
            [10],
        )
