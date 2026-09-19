from rest_framework import serializers
from .models import Category, Product, Customer, Order, OrderItem


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'description', 'created_at']


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'description', 'category', 'category_name',
            'price', 'stock', 'is_active', 'created_at', 'updated_at'
        ]


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = [
            'id', 'first_name', 'last_name', 'email',
            'phone', 'address', 'created_at'
        ]


class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'product_name', 'quantity', 'price']


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, required=False)
    customer_name = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id', 'customer', 'customer_name', 'status', 'total_amount',
            'delivery_address', 'comment', 'items',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['total_amount']

    def get_customer_name(self, obj):
        return f'{obj.customer.first_name} {obj.customer.last_name}'

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        order = Order.objects.create(**validated_data)
        total = 0
        for item_data in items_data:
            product = item_data['product']
            price = item_data.get('price', product.price)
            quantity = item_data.get('quantity', 1)
            OrderItem.objects.create(
                order=order,
                product=product,
                quantity=quantity,
                price=price
            )
            total += price * quantity
        order.total_amount = total
        order.save(update_fields=['total_amount'])
        return order

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            total = 0
            for item_data in items_data:
                product = item_data['product']
                price = item_data.get('price', product.price)
                quantity = item_data.get('quantity', 1)
                OrderItem.objects.create(
                    order=instance,
                    product=product,
                    quantity=quantity,
                    price=price
                )
                total += price * quantity
            instance.total_amount = total
            instance.save(update_fields=['total_amount'])
        return instance


# --- Сериализаторы для сложных JOIN-запросов ---

class OrderWithDetailsSerializer(serializers.Serializer):
    """Результат JOIN-запроса: заказ + клиент + позиции + товары"""
    order_id = serializers.IntegerField()
    status = serializers.CharField()
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    created_at = serializers.DateTimeField()
    customer_id = serializers.IntegerField()
    customer_name = serializers.CharField()
    customer_email = serializers.EmailField()
    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    category_name = serializers.CharField()
    quantity = serializers.IntegerField()
    item_price = serializers.DecimalField(max_digits=10, decimal_places=2)


class SalesByCategorySerializer(serializers.Serializer):
    """Агрегирующий запрос: продажи по категориям"""
    category_id = serializers.IntegerField()
    category_name = serializers.CharField()
    orders_count = serializers.IntegerField()
    total_quantity = serializers.IntegerField()
    total_revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
