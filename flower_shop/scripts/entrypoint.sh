#!/bin/bash
set -e

echo "Waiting for PostgreSQL..."
while ! python -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('db', 5432)); s.close()" 2>/dev/null; do
  sleep 1
done
echo "PostgreSQL is ready."

echo "Running migrations..."
python manage.py migrate --noinput

echo "Creating superuser if not exists..."
python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@example.com', 'admin123')
    print('Superuser created: admin / admin123')
else:
    print('Superuser already exists')
"

echo "Loading initial seed data if empty..."
python manage.py shell -c "
from shop.models import Order
if Order.objects.count() == 0:
    print('Seeding small dataset...')
    import django.core.management
    django.core.management.call_command('seed_data', small=True)
else:
    print('Data already present, skip seeding')
" || true

echo "Starting application..."
exec "$@"
