from django.db import models
from django.utils import timezone


class Category(models.Model):
    """Категории цветов / букетов"""
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Категория'
        verbose_name_plural = 'Категории'
        ordering = ['name']

    def __str__(self):
        return self.name


class Product(models.Model):
    """Товары (цветы, букеты, композиции)"""
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name='products'
    )
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Товар'
        verbose_name_plural = 'Товары'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['name'], name='idx_product_name'),
            models.Index(fields=['category', 'is_active'], name='idx_product_cat_active'),
        ]

    def __str__(self):
        return f'{self.name} ({self.price} ₽)'


class Customer(models.Model):
    """Клиенты магазина"""
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Клиент'
        verbose_name_plural = 'Клиенты'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.first_name} {self.last_name} <{self.email}>'


class Order(models.Model):
    """
    Основная сущность для масштабирования.
    Количество заказов естественно растёт со временем.
    """
    STATUS_CHOICES = [
        ('NEW', 'Новый'),
        ('PAID', 'Оплачен'),
        ('PROCESSING', 'В сборке'),
        ('DELIVERED', 'Доставлен'),
        ('CANCELLED', 'Отменён'),
    ]

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name='orders'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='NEW')
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    delivery_address = models.TextField(blank=True)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'
        ordering = ['-created_at']
        indexes = [
            # Диапазоны только по дате: GET /api/orders/?from_date=&to_date=
            models.Index(fields=['created_at'], name='idx_order_created_at'),
            # Фильтр клиента + статус: GET /api/orders/?customer=&status=
            models.Index(fields=['customer', 'status'], name='idx_order_customer_status'),
            # Query 1: GET /api/customers/{id}/orders?ordering=-created_at
            models.Index(
                fields=['customer', '-created_at'],
                name='idx_order_cust_created_desc',
            ),
            # Query 2: GET /api/orders/?status=PAID&from_date=...
            models.Index(
                fields=['status', 'created_at'],
                name='idx_order_status_created',
            ),
            # Partial index для «горячих» новых заказов
            models.Index(
                fields=['created_at'],
                name='idx_order_new_created',
                condition=models.Q(status='NEW'),
            ),
        ]

    def __str__(self):
        return f'Order #{self.id} — {self.customer} ({self.status})'


class OrderItem(models.Model):
    """
    Позиции заказа.
    Связь many-to-many между Order и Product через промежуточную таблицу.
    """
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='items'
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='order_items'
    )
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2)  # цена на момент заказа

    class Meta:
        verbose_name = 'Позиция заказа'
        verbose_name_plural = 'Позиции заказа'
        constraints = [
            models.UniqueConstraint(
                fields=['order', 'product'],
                name='uniq_orderitem_order_product',
            ),
        ]

    def __str__(self):
        return f'{self.product.name} x {self.quantity}'

    def save(self, *args, **kwargs):
        if not self.price:
            self.price = self.product.price
        super().save(*args, **kwargs)
