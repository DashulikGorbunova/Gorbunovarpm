from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CategoryViewSet, ProductViewSet, CustomerViewSet, OrderViewSet,
    ReplicationStatusView, ShardReportView, ShardRouteView, ShardPlanView,
    ShardDistributedView,
)

router = DefaultRouter()
router.register(r'categories', CategoryViewSet, basename='category')
router.register(r'products', ProductViewSet, basename='product')
router.register(r'customers', CustomerViewSet, basename='customer')
router.register(r'orders', OrderViewSet, basename='order')

urlpatterns = [
    # Lab 04: состояние Primary/Replica и replication lag
    path('replication/status/', ReplicationStatusView.as_view(), name='replication-status'),
    # Lab 05: шардирование
    path('shards/report/', ShardReportView.as_view(), name='shard-report'),
    path('shards/route/', ShardRouteView.as_view(), name='shard-route'),
    path('shards/plan/', ShardPlanView.as_view(), name='shard-plan'),
    # Lab 06: single-shard vs distributed queries
    path('shards/distributed/', ShardDistributedView.as_view(), name='shard-distributed'),
    path('', include(router.urls)),
]
