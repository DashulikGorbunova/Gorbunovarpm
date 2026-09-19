from django.db import connection, connections
from django.db.models import Count, Sum, F
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from .models import Category, Product, Customer, Order, OrderItem
from .serializers import (
    CategorySerializer, ProductSerializer, CustomerSerializer,
    OrderSerializer, OrderWithDetailsSerializer, SalesByCategorySerializer
)
from .filters import OrderFilter, ProductFilter
from .replication import (
    db_identity,
    replica_alias,
    replica_configured,
    replication_status,
)


def _db_ping(alias):
    """Простой SELECT 1 на указанном подключении."""
    with connections[alias].cursor() as cursor:
        cursor.execute('SELECT 1')
        cursor.fetchone()


class HealthCheckView(APIView):
    """GET /health — проверка процесса и подключений Primary + Replica (lab 04)"""
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        try:
            _db_ping('default')
        except Exception as e:
            return Response({
                'status': 'error',
                'database': 'disconnected',
                'primary': 'disconnected',
                'detail': str(e)
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        payload = {
            'status': 'ok',
            'database': 'connected',
            'primary': 'connected',
            'service': 'flower_shop',
        }
        if replica_configured():
            try:
                _db_ping(replica_alias())
                payload['replica'] = 'connected'
            except Exception as e:
                payload['replica'] = 'disconnected'
                payload['replica_detail'] = str(e)
        else:
            payload['replica'] = 'not configured'
        return Response(payload)


class ReplicationStatusView(APIView):
    """GET /api/replication/status/ — состояние Primary/Replica и replication lag (lab 04)"""
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response(replication_status())


class ShardReportView(APIView):
    """GET /api/shards/report/ — распределение заказов по шардам (lab 05)"""
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        from .shard_router import shard_report
        from .sharding import shards_configured

        if not shards_configured():
            return Response(
                {'detail': 'shards are not configured'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(shard_report())


class ShardRouteView(APIView):
    """GET /api/shards/route/?customer_id=N — какой шард обслуживает клиента (lab 05)"""
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        from .shard_router import alias_for_customer, fetch_customer_orders
        from .sharding import get_router, shards_configured

        if not shards_configured():
            return Response(
                {'detail': 'shards are not configured'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        raw = request.query_params.get('customer_id')
        if raw is None:
            return Response(
                {'detail': 'Параметр customer_id обязателен'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            customer_id = int(raw)
        except ValueError:
            return Response(
                {'detail': 'customer_id должен быть числом'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        router = get_router()
        alias = alias_for_customer(customer_id)
        try:
            orders = fetch_customer_orders(customer_id, limit=20)
            error = None
        except Exception as exc:  # pragma: no cover - depends on live DB
            orders, error = [], str(exc)

        return Response({
            'customer_id': customer_id,
            'strategy': router.strategy,
            'shard_index': router.index_for(customer_id),
            'shard_alias': alias,
            'orders_count': len(orders),
            'orders_on_shard': orders,
            'error': error,
        })


class ShardDistributedView(APIView):
    """GET /api/shards/distributed/?mode=count|group|top|join|hot — распределённые запросы (lab 06)"""
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        from .distributed import (
            cross_shard_join_demo,
            distributed_count,
            distributed_group_by_status,
            distributed_sum,
            distributed_top_orders,
            routing_summary,
            simulate_hot_shard,
        )
        from .sharding import shards_configured

        if not shards_configured():
            return Response(
                {'detail': 'shards are not configured'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        mode = request.query_params.get('mode', 'count')
        try:
            limit = int(request.query_params.get('limit', 20))
            customer_id = int(request.query_params.get('customer_id', 1))
            requests = int(request.query_params.get('requests', 10000))
        except ValueError:
            return Response(
                {'detail': 'limit/customer_id/requests должны быть числами'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            if mode == 'count':
                payload = distributed_count().as_dict()
            elif mode == 'sum':
                payload = distributed_sum().as_dict()
            elif mode == 'group':
                payload = distributed_group_by_status().as_dict()
            elif mode == 'top':
                payload = distributed_top_orders(limit=limit).as_dict()
            elif mode == 'join':
                payload = cross_shard_join_demo(customer_id, limit=limit)
            elif mode == 'hot':
                payload = simulate_hot_shard(requests=requests)
            elif mode == 'summary':
                payload = routing_summary()
            else:
                return Response(
                    {'detail': 'mode должен быть count|sum|group|top|join|hot|summary'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except Exception as exc:  # pragma: no cover - depends on live DB
            return Response(
                {'detail': str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        payload['mode'] = mode
        return Response(payload)


class ShardPlanView(APIView):
    """GET /api/shards/plan/ — сравнение modulo и consistent при N→N+1 (lab 05)"""
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        from django.conf import settings
        from django.db import connections

        from .sharding import (
            STRATEGY_CONSISTENT,
            STRATEGY_MODULO,
            plan_migration,
            shard_aliases,
        )

        try:
            n_from = int(request.query_params.get('from', 3))
            n_to = int(request.query_params.get('to', 4))
            samples = int(request.query_params.get('samples', 100000))
        except ValueError:
            return Response(
                {'detail': 'from/to/samples должны быть числами'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if n_to <= n_from:
            return Response(
                {'detail': 'to должен быть больше from'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with connections['default'].cursor() as cursor:
            cursor.execute(
                'SELECT customer_id FROM shop_order ORDER BY created_at DESC LIMIT %s',
                [samples],
            )
            keys = [row[0] for row in cursor.fetchall() if row[0] is not None]

        vnodes = int(getattr(settings, 'SHARD_VNODES', 160))
        aliases_from = shard_aliases(n_from)
        aliases_to = shard_aliases(n_to)
        return Response({
            'keys': len(keys),
            'shards_from': n_from,
            'shards_to': n_to,
            'vnodes': vnodes,
            'plans': {
                strategy: plan_migration(
                    keys, aliases_from, aliases_to, strategy, vnodes
                ).as_dict()
                for strategy in (STRATEGY_MODULO, STRATEGY_CONSISTENT)
            },
        })


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ['name']
    ordering_fields = ['name', 'created_at']


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.select_related('category').all()
    serializer_class = ProductSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ProductFilter
    search_fields = ['name', 'description']
    ordering_fields = ['price', 'created_at', 'name']


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ['first_name', 'last_name', 'email', 'phone']
    ordering_fields = ['created_at', 'last_name']

    @action(detail=True, methods=['get'])
    def orders(self, request, pk=None):
        """GET /api/customers/{id}/orders — заказы клиента"""
        customer = self.get_object()
        orders = customer.orders.prefetch_related('items__product').all()
        page = self.paginate_queryset(orders)
        serializer = OrderSerializer(page if page is not None else orders, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.select_related('customer').prefetch_related(
        'items__product__category'
    ).all()
    serializer_class = OrderSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = OrderFilter
    ordering_fields = ['created_at', 'total_amount', 'status']
    ordering = ['-created_at']

    @action(detail=True, methods=['get'])
    def items(self, request, pk=None):
        """GET /api/orders/{id}/items — позиции заказа"""
        order = self.get_object()
        items = order.items.select_related('product').all()
        from .serializers import OrderItemSerializer
        return Response(OrderItemSerializer(items, many=True).data)

    @action(detail=False, methods=['get'], url_path='read-demo')
    def read_demo(self, request):
        """
        Read-scaling demo (lab 04).

        Выполняет тот же SELECT, что и обычный список заказов
        (``GET /api/orders/``), но явно через Replica, и показывает:
        * какой сервер обслужил запрос (``pg_is_in_recovery``, host, port);
        * расхождение количества/последних заказов Primary ↔ Replica;
        * replication lag в секундах и байтах.

        Обычный ``GET /api/orders/`` тоже идёт на Replica — это задаётся
        роутером ``shop.db_router.ReadReplicaRouter`` и ``READ_REPLICA_MODELS``.
        """
        alias = replica_alias()
        replica_qs = Order.objects.using(alias)

        payload = {
            'replica_alias': alias,
            'replica_used': alias != 'default',
            'served_by': None,
            'count_primary': Order.objects.using('default').count(),
            'count_replica': replica_qs.count(),
            'latest_primary': list(
                Order.objects.using('default')
                .order_by('-created_at')
                .values('id', 'status', 'created_at')[:3]
            ),
            'latest_replica': list(
                replica_qs.order_by('-created_at')
                .values('id', 'status', 'created_at')[:3]
            ),
            'sample_replica': list(
                replica_qs.order_by('-created_at')
                .values('id', 'status', 'created_at')[:5]
            ),
            'lag_seconds': None,
            'lag_bytes': None,
        }
        try:
            payload['served_by'] = db_identity(alias)
        except Exception as exc:  # pragma: no cover - зависит от живой БД
            payload['served_by_error'] = str(exc)

        status_info = replication_status()
        payload['lag_seconds'] = status_info.get('lag_seconds')
        payload['lag_bytes'] = status_info.get('lag_bytes')
        payload['replication'] = {
            'sender': (status_info.get('senders') or [None])[0],
            'error': status_info.get('error'),
        }
        return Response(payload)


    # ========== JOIN-запросы (минимум 2) ==========

    @action(detail=False, methods=['get'], url_path='with-details')
    def with_details(self, request):
        """
        JOIN-запрос №1:
        Заказы + клиенты + позиции + товары + категории
        Используется raw SQL для явного JOIN (удобно анализировать в EXPLAIN).
        """
        status_filter = request.query_params.get('status')
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')

        sql = """
            SELECT
                o.id AS order_id,
                o.status,
                o.total_amount,
                o.created_at,
                c.id AS customer_id,
                (c.first_name || ' ' || c.last_name) AS customer_name,
                c.email AS customer_email,
                p.id AS product_id,
                p.name AS product_name,
                cat.name AS category_name,
                oi.quantity,
                oi.price AS item_price
            FROM shop_order o
            JOIN shop_customer c ON c.id = o.customer_id
            JOIN shop_orderitem oi ON oi.order_id = o.id
            JOIN shop_product p ON p.id = oi.product_id
            JOIN shop_category cat ON cat.id = p.category_id
            WHERE 1=1
        """
        params = []
        if status_filter:
            sql += " AND o.status = %s"
            params.append(status_filter)
        if from_date:
            sql += " AND o.created_at >= %s"
            params.append(from_date)
        if to_date:
            sql += " AND o.created_at <= %s"
            params.append(to_date)
        sql += " ORDER BY o.created_at DESC LIMIT 500"

        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            columns = [col[0] for col in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

        serializer = OrderWithDetailsSerializer(rows, many=True)
        return Response({
            'count': len(rows),
            'results': serializer.data
        })

    @action(detail=False, methods=['get'], url_path='customer-products')
    def customer_products(self, request):
        """
        JOIN-запрос №2:
        Какие товары покупал конкретный клиент (через заказы).
        """
        customer_id = request.query_params.get('customer_id')
        if not customer_id:
            return Response(
                {'detail': 'Параметр customer_id обязателен'},
                status=status.HTTP_400_BAD_REQUEST
            )

        sql = """
            SELECT DISTINCT
                p.id AS product_id,
                p.name AS product_name,
                cat.name AS category_name,
                p.price,
                COUNT(oi.id) AS times_ordered,
                SUM(oi.quantity) AS total_quantity
            FROM shop_order o
            JOIN shop_orderitem oi ON oi.order_id = o.id
            JOIN shop_product p ON p.id = oi.product_id
            JOIN shop_category cat ON cat.id = p.category_id
            WHERE o.customer_id = %s
            GROUP BY p.id, p.name, cat.name, p.price
            ORDER BY total_quantity DESC
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, [customer_id])
            columns = [col[0] for col in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

        return Response({
            'customer_id': int(customer_id),
            'products': rows
        })

    # ========== Агрегирующий запрос ==========

    @action(detail=False, methods=['get'], url_path='sales-by-category')
    def sales_by_category(self, request):
        """
        Агрегирующий запрос:
        Продажи (количество заказов, сумма, кол-во товаров) по категориям.
        """
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')

        qs = (
            OrderItem.objects
            .select_related('product__category', 'order')
            .values(
                category_id=F('product__category_id'),
                category_name=F('product__category__name')
            )
            .annotate(
                orders_count=Count('order_id', distinct=True),
                total_quantity=Sum('quantity'),
                total_revenue=Sum(F('quantity') * F('price'))
            )
            .order_by('-total_revenue')
        )

        if from_date:
            qs = qs.filter(order__created_at__gte=from_date)
        if to_date:
            qs = qs.filter(order__created_at__lte=to_date)

        serializer = SalesByCategorySerializer(list(qs), many=True)
        return Response(serializer.data)
