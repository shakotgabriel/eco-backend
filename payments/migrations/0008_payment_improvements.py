"""
Django migration: Add payment improvement models
- PaymentProviderHealth
- WebhookRetry
- FailedCeleryTask
- CallbackIdempotencyKey
- PaymentRetryLog

Run with: python manage.py migrate
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0007_payment_idx_payment_txn_ref_and_more'),
        ('bookings', '0001_initial'),  # Adjust this to your actual latest bookings migration
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentProviderHealth',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('provider', models.CharField(choices=[('MTN_MOMO', 'MTN Mobile Money'), ('MPESA', 'M-Pesa Daraja'), ('AT', "Africa's Talking")], max_length=20, unique=True)),
                ('status', models.CharField(choices=[('HEALTHY', 'Healthy'), ('DEGRADED', 'Degraded'), ('DOWN', 'Down')], default='HEALTHY', max_length=10)),
                ('last_check_at', models.DateTimeField(blank=True, null=True)),
                ('last_success_at', models.DateTimeField(blank=True, null=True)),
                ('last_error_at', models.DateTimeField(blank=True, null=True)),
                ('error_message', models.TextField(blank=True, help_text='Last error encountered')),
                ('consecutive_failures', models.IntegerField(default=0)),
                ('response_time_ms', models.IntegerField(blank=True, help_text='Last response time in milliseconds', null=True)),
            ],
            options={
                'verbose_name': 'Payment Provider Health',
                'ordering': ['provider'],
            },
        ),
        migrations.CreateModel(
            name='WebhookRetry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('webhook_type', models.CharField(choices=[('MOMO_CALLBACK', 'MTN MoMo Callback'), ('MPESA_CALLBACK', 'M-Pesa Callback'), ('AT_CALLBACK', "Africa's Talking Callback")], max_length=20)),
                ('transaction_ref', models.CharField(help_text='Payment/Transaction reference', max_length=200)),
                ('payload', models.JSONField(help_text='Original webhook payload')),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('DELIVERED', 'Delivered'), ('FAILED', 'Failed'), ('ABANDONED', 'Abandoned')], default='PENDING', max_length=15)),
                ('attempt_count', models.IntegerField(default=0)),
                ('max_attempts', models.IntegerField(default=5)),
                ('last_attempt_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.TextField(blank=True)),
                ('next_retry_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='FailedCeleryTask',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('task_name', models.CharField(max_length=255)),
                ('args', models.JSONField(default=list)),
                ('kwargs', models.JSONField(default=dict)),
                ('error_message', models.TextField()),
                ('traceback', models.TextField(blank=True)),
                ('attempt_count', models.IntegerField(default=1)),
                ('max_retries', models.IntegerField(default=3)),
                ('next_retry_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('RESOLVED', 'Resolved'), ('ABANDONED', 'Abandoned')], default='PENDING', max_length=15)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='CallbackIdempotencyKey',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('callback_id', models.CharField(db_index=True, help_text='Callback ID from provider', max_length=255, unique=True)),
                ('webhook_type', models.CharField(help_text='Type of callback (MOMO, MPESA, etc.)', max_length=20)),
                ('transaction_ref', models.CharField(help_text='Payment/Transaction reference', max_length=200)),
                ('processed_at', models.DateTimeField(auto_now_add=True)),
                ('response_status', models.IntegerField()),
                ('response_body', models.JSONField()),
            ],
            options={
                'ordering': ['-processed_at'],
            },
        ),
        migrations.CreateModel(
            name='PaymentRetryLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('original_attempt_at', models.DateTimeField(help_text='When the original payment was attempted')),
                ('retry_attempt_number', models.IntegerField(default=1)),
                ('attempted_at', models.DateTimeField(auto_now_add=True)),
                ('new_payment_id', models.IntegerField(blank=True, help_text='ID of new Payment created on retry', null=True)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('SUCCEEDED', 'Succeeded'), ('FAILED', 'Failed')], default='PENDING', max_length=15)),
                ('payment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='retry_logs', to='payments.payment')),
            ],
            options={
                'ordering': ['-attempted_at'],
            },
        ),
        # Add indexes
        migrations.AddIndex(
            model_name='webhookretry',
            index=models.Index(fields=['webhook_type', 'status'], name='idx_webhook_retry_type_status'),
        ),
        migrations.AddIndex(
            model_name='webhookretry',
            index=models.Index(fields=['transaction_ref'], name='idx_webhook_retry_txn_ref'),
        ),
        migrations.AddIndex(
            model_name='webhookretry',
            index=models.Index(fields=['next_retry_at'], name='idx_webhook_retry_next_retry'),
        ),
        migrations.AddIndex(
            model_name='failedcelerytask',
            index=models.Index(fields=['task_name', 'status'], name='idx_failed_task_name_status'),
        ),
        migrations.AddIndex(
            model_name='failedcelerytask',
            index=models.Index(fields=['next_retry_at'], name='idx_failed_task_retry_at'),
        ),
        migrations.AddIndex(
            model_name='callbackidempotencykey',
            index=models.Index(fields=['webhook_type', 'transaction_ref'], name='idx_callback_idem_type_txn'),
        ),
        migrations.AddIndex(
            model_name='paymentretrylog',
            index=models.Index(fields=['payment', 'retry_attempt_number'], name='idx_retry_log_payment_attempt'),
        ),
    ]
