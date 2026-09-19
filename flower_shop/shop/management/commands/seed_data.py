"""
Генерация тестовых данных для цветочного магазина.

Примеры использования:
  python manage.py seed_data --small          # небольшой набор для проверки API
  python manage.py seed_data --orders 100000  # 100 тыс. заказов
  python manage.py seed_data --clear          # очистить данные перед генерацией
"""

import random
from decimal import Decimal
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from faker import Faker

from shop.models import Category, Product, Customer, Order, OrderItem


fake = Faker('ru_RU')

CATEGORIES = [
    ('Розы', 'Классические и кустовые розы'),
    ('Тюльпаны', 'Весенние тюльпаны разных сортов'),
    ('Пионы', 'Пышные пионы'),
    ('Хризантемы', 'Хризантемы и кустовые'),
    ('Орхидеи', 'Фаленопсисы и другие орхидеи'),
    ('Букеты', 'Готовые сборные букеты'),
    ('Композиции', 'Композиции в коробках и корзинах'),
    ('Сухоцветы', 'Стабилизированные и сухоцветы'),
]

PRODUCT_NAMES = {
    'Розы': ['Роза красная', 'Роза белая', 'Роза розовая', 'Роза пионовидная', 'Кустовая роза'],
    'Тюльпаны': ['Тюльпан красный', 'Тюльпан жёлтый', 'Тюльпан белый', 'Тюльпан пионовидный'],
    'Пионы': ['Пион Сара Бернар', 'Пион белый', 'Пион розовый'],
    'Хризантемы': ['Хризантема кустовая', 'Хризантема одноголовая', 'Хризантема баллы'],
    'Орхидеи': ['Фаленопсис белый', 'Фаленопсис розовый', 'Фаленопсис микс'],
    'Букеты': ['Букет "Нежность"', 'Букет "Страсть"', 'Букет "Весна"', 'Букет "Премиум"'],
    'Композиции': ['Композиция в коробке', 'Корзина с цветами', 'Шляпная коробка'],
    'Сухоцветы': ['Лагурус', 'Хлопок', 'Эвкалипт стабилизированный'],
}

STATUSES = ['NEW', 'PAID', 'PROCESSING', 'DELIVERED', 'CANCELLED']


class Command(BaseCommand):
    help = 'Генерация тестовых данных (клиенты, товары, заказы)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--small',
            action='store_true',
            help='Небольшой набор данных для проверки API (~20 клиентов, ~50 заказов)',
        )
        parser.add_argument(
            '--customers',
            type=int,
            default=None,
            help='Количество клиентов',
        )
        parser.add_argument(
            '--orders',
            type=int,
            default=None,
            help='Количество заказов (основная сущность для масштабирования)',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Удалить существующие данные перед генерацией',
        )

    def handle(self, *args, **options):
        if options['clear']:
            self.stdout.write('Очистка данных...')
            # TRUNCATE CASCADE обходит PROTECT на FK (OrderItem→Product, Order→Customer)
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute(
                    'TRUNCATE TABLE shop_orderitem, shop_order, shop_product, '
                    'shop_customer, shop_category RESTART IDENTITY CASCADE'
                )
            self.stdout.write(self.style.SUCCESS('Данные очищены'))

        if options['small']:
            n_customers = 20
            n_orders = 50
        else:
            n_customers = options['customers'] or 100
            n_orders = options['orders'] or 500

        self.stdout.write(f'Генерация: {n_customers} клиентов, {n_orders} заказов...')

        categories = self._create_categories()
        products = self._create_products(categories)
        customers = self._create_customers(n_customers)
        self._create_orders(customers, products, n_orders)

        self.stdout.write(self.style.SUCCESS(
            f'Готово!\n'
            f'  Категории: {Category.objects.count()}\n'
            f'  Товары:    {Product.objects.count()}\n'
            f'  Клиенты:   {Customer.objects.count()}\n'
            f'  Заказы:    {Order.objects.count()}\n'
            f'  Позиции:   {OrderItem.objects.count()}'
        ))

    def _create_categories(self):
        categories = []
        for name, desc in CATEGORIES:
            cat, _ = Category.objects.get_or_create(
                name=name,
                defaults={'description': desc}
            )
            categories.append(cat)
        self.stdout.write(f'  Категории: {len(categories)}')
        return categories

    def _create_products(self, categories):
        products = []
        for cat in categories:
            names = PRODUCT_NAMES.get(cat.name, [f'{cat.name} стандарт'])
            for name in names:
                product, _ = Product.objects.get_or_create(
                    name=name,
                    category=cat,
                    defaults={
                        'description': fake.sentence(),
                        'price': Decimal(str(round(random.uniform(500, 15000), 2))),
                        'stock': random.randint(10, 200),
                        'is_active': True,
                    }
                )
                products.append(product)
        extra_needed = max(0, 40 - Product.objects.count())
        extras = []
        for _ in range(extra_needed):
            cat = random.choice(categories)
            extras.append(Product(
                name=f'{cat.name} {fake.word().title()}',
                description=fake.text(max_nb_chars=120),
                category=cat,
                price=Decimal(str(round(random.uniform(300, 20000), 2))),
                stock=random.randint(5, 150),
                is_active=random.random() > 0.1,
            ))
        if extras:
            extras = Product.objects.bulk_create(extras)
            products.extend(extras)
        self.stdout.write(f'  Товары: {len(products)}')
        return products

    def _create_customers(self, count):
        batch_size = 1000
        created = []
        existing = Customer.objects.count()
        for start in range(0, count, batch_size):
            chunk = min(batch_size, count - start)
            batch = []
            for i in range(chunk):
                n = existing + start + i
                batch.append(Customer(
                    first_name=fake.first_name(),
                    last_name=fake.last_name(),
                    email=f'customer{n}_{fake.user_name()}@example.com',
                    phone=fake.phone_number()[:20],
                    address=fake.address(),
                ))
            created.extend(Customer.objects.bulk_create(batch, batch_size=chunk))
            self.stdout.write(f'  Клиенты: {len(created)}/{count}')
        return created

    def _create_orders(self, customers, products, count):
        now = timezone.now()
        batch_size = 1000
        orders_created = 0

        for i in range(0, count, batch_size):
            chunk = min(batch_size, count - i)
            orders = []
            for _ in range(chunk):
                created_at = now - timedelta(
                    days=random.randint(0, 730),
                    hours=random.randint(0, 23),
                    minutes=random.randint(0, 59),
                )
                orders.append(Order(
                    customer=random.choice(customers),
                    status=random.choice(STATUSES),
                    total_amount=Decimal('0'),
                    delivery_address=fake.address(),
                    comment=fake.sentence() if random.random() > 0.7 else '',
                    created_at=created_at,
                ))

            with transaction.atomic():
                created_orders = Order.objects.bulk_create(orders)
                items = []
                for order in created_orders:
                    n_items = random.randint(1, 5)
                    chosen = random.sample(products, min(n_items, len(products)))
                    total = Decimal('0')
                    for product in chosen:
                        qty = random.randint(1, 7)
                        price = product.price
                        items.append(OrderItem(
                            order=order,
                            product=product,
                            quantity=qty,
                            price=price,
                        ))
                        total += price * qty
                    order.total_amount = total

                OrderItem.objects.bulk_create(items)
                Order.objects.bulk_update(created_orders, ['total_amount'])

            orders_created += chunk
            self.stdout.write(f'  Заказы: {orders_created}/{count}')

        self.stdout.write(f'  Заказы созданы: {orders_created}')
