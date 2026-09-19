# Performance indexes for lab 1 (EXPLAIN ANALYZE before/after).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0001_initial'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='order',
            index=models.Index(
                fields=['customer', '-created_at'],
                name='idx_order_cust_created_desc',
            ),
        ),
        migrations.AddIndex(
            model_name='order',
            index=models.Index(
                fields=['status', 'created_at'],
                name='idx_order_status_created',
            ),
        ),
        migrations.AddIndex(
            model_name='order',
            index=models.Index(
                condition=models.Q(status='NEW'),
                fields=['created_at'],
                name='idx_order_new_created',
            ),
        ),
    ]
