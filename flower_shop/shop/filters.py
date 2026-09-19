import django_filters
from .models import Order, Product


class OrderFilter(django_filters.FilterSet):
    status = django_filters.CharFilter(field_name='status')
    from_date = django_filters.DateTimeFilter(field_name='created_at', lookup_expr='gte')
    to_date = django_filters.DateTimeFilter(field_name='created_at', lookup_expr='lte')
    min_amount = django_filters.NumberFilter(field_name='total_amount', lookup_expr='gte')
    max_amount = django_filters.NumberFilter(field_name='total_amount', lookup_expr='lte')
    customer = django_filters.NumberFilter(field_name='customer_id')

    class Meta:
        model = Order
        fields = ['status', 'customer']


class ProductFilter(django_filters.FilterSet):
    category = django_filters.NumberFilter(field_name='category_id')
    is_active = django_filters.BooleanFilter()
    min_price = django_filters.NumberFilter(field_name='price', lookup_expr='gte')
    max_price = django_filters.NumberFilter(field_name='price', lookup_expr='lte')
    search = django_filters.CharFilter(field_name='name', lookup_expr='icontains')

    class Meta:
        model = Product
        fields = ['category', 'is_active']
