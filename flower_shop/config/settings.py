import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-dev-key-only-for-local')

DEBUG = os.environ.get('DJANGO_DEBUG', '1') == '1'

ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'django_filters',
    'drf_spectacular',
    'shop.apps.ShopConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ---------------------------------------------------------------------------
# Database connections
#   default -> Primary  (writes + migrations)
#   replica -> Replica  (read-only, streaming replication from Primary) — lab 04
# ---------------------------------------------------------------------------
def _db_config_from_url(url: str) -> dict:
    parsed = urlparse(url)
    return {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': parsed.path[1:],
        'USER': parsed.username,
        'PASSWORD': parsed.password,
        'HOST': parsed.hostname,
        'PORT': parsed.port or 5432,
    }


DATABASE_URL = os.environ.get('DATABASE_URL')
DATABASE_REPLICA_URL = os.environ.get('DATABASE_REPLICA_URL')

if DATABASE_URL:
    DATABASES = {'default': _db_config_from_url(DATABASE_URL)}
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('POSTGRES_DB', 'flower_shop'),
            'USER': os.environ.get('POSTGRES_USER', 'flower'),
            'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'flowerpass'),
            'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),
            'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        }
    }

# Replica connection (lab 04). Added only when DATABASE_REPLICA_URL is provided,
# so a single-database environment keeps working unchanged.
if DATABASE_REPLICA_URL:
    DATABASES['replica'] = _db_config_from_url(DATABASE_REPLICA_URL)

# --- Sharding (lab 05): independent PostgreSQL shards ---
# Each SHARD<i>_URL adds a Django alias shard<i>; SHARD_ALIASES lists what is
# actually configured, so a single-node environment keeps working.
SHARD_ALIASES: list[str] = []
_shard_index = 0
while os.environ.get(f'SHARD{_shard_index}_URL'):
    _alias = f'shard{_shard_index}'
    DATABASES[_alias] = _db_config_from_url(os.environ[f'SHARD{_shard_index}_URL'])
    SHARD_ALIASES.append(_alias)
    _shard_index += 1

SHARD_STRATEGY = os.environ.get('SHARD_STRATEGY', 'consistent')   # 'modulo' | 'consistent'
SHARD_VNODES = int(os.environ.get('SHARD_VNODES', '160'))
SHARD_TABLE = os.environ.get('SHARD_TABLE', 'shop_order_shard')
SHARD_KEY = 'customer_id'
# Optional Django-level router (off by default, see shop/shard_router.py).
SHARDING_ENABLED = os.environ.get('SHARDING_ENABLED', '0') == '1'

# --- Read scaling / Primary + Replica routing (lab 04) ---
READ_REPLICA_ALIAS = 'replica'
READ_REPLICA_ENABLED = (
    os.environ.get('READ_REPLICA_ENABLED', '1') == '1'
    and READ_REPLICA_ALIAS in DATABASES
)
# SELECTs for these models (app_label.model_name) go to the Replica.
# Writes, sessions, auth and admin stay on the Primary (read-after-write safety).
READ_REPLICA_MODELS = [
    'shop.order',
    'shop.orderitem',
    'shop.product',
    'shop.category',
]
DATABASE_ROUTERS = [
    'shop.shard_router.ShardAwareRouter',
    'shop.db_router.ReadReplicaRouter',
]


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = os.environ.get('DJANGO_TIME_ZONE', 'Europe/Moscow')
USE_I18N = True
USE_TZ = os.environ.get('DJANGO_USE_TZ', '1') == '1'

STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# DRF
REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_PAGINATION_CLASS': 'shop.pagination.StandardPagination',
    'PAGE_SIZE': 20,
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Flower Shop API',
    'DESCRIPTION': 'Backend сервиса цветочного магазина для модуля масштабирования БД',
    'VERSION': '1.0.0',
}

# Partition automation (lab 03)
ORDER_PARTITION_TABLE = 'shop_order'
ORDER_PARTITION_HORIZON_MONTHS = int(os.environ.get('ORDER_PARTITION_HORIZON_MONTHS', '3'))
ORDER_PARTITION_BACKFILL_MONTHS = int(os.environ.get('ORDER_PARTITION_BACKFILL_MONTHS', '24'))
EVENTS_PARTITION_TABLE = 'lab_events'
EVENTS_PARTITION_HORIZON_DAYS = int(os.environ.get('EVENTS_PARTITION_HORIZON_DAYS', '3'))
EVENTS_PARTITION_BACKFILL_DAYS = int(os.environ.get('EVENTS_PARTITION_BACKFILL_DAYS', '2'))

# Alerts: Telegram bot (primary for lab 03). Optional email/webhook.
TELEGRAM_BOT_TOKEN = os.environ.get(
    'TELEGRAM_BOT_TOKEN',
    '8549191648:AAGwYeJXQT59Iev6pjxuWug6n3nDXW_VFsU',
)
TELEGRAM_BOT_USERNAME = os.environ.get('TELEGRAM_BOT_USERNAME', 'Dahnfueshnr_bot')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '1188068476')

EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend',
)
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'partitions@flower-shop.local')
PARTITION_ALERT_EMAIL_ENABLED = os.environ.get('PARTITION_ALERT_EMAIL_ENABLED', '0') == '1'
PARTITION_ALERT_EMAILS = [
    e.strip()
    for e in os.environ.get('PARTITION_ALERT_EMAILS', 'ops@flower-shop.local').split(',')
    if e.strip()
]
PARTITION_ALERT_WEBHOOK_URL = os.environ.get('PARTITION_ALERT_WEBHOOK_URL', '')
PARTITION_ALERT_STATE_FILE = os.environ.get(
    'PARTITION_ALERT_STATE_FILE',
    str(BASE_DIR / '.partition_alert_state.json'),
)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'shop.partitions': {
            'handlers': ['console'],
            'level': 'INFO',
        },
        'shop.partitions.alert': {
            'handlers': ['console'],
            'level': 'WARNING',
        },
    },
}
