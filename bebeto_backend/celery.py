import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bebeto_backend.settings")

app = Celery("bebeto_backend")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
# Modules that are not named tasks.py (Celery only auto-loads *.tasks by default)
app.autodiscover_tasks(related_name="payment_tasks")

app.conf.beat_schedule = {
    "update-trip-statuses-every-5-min": {
        "task": "trips.tasks.update_trip_statuses",
        "schedule": 300.0,
    },
    "complete-arrived-trips-hourly": {
        "task": "trips.tasks.complete_arrived_trips",
        "schedule": crontab(minute=0),
    },
    "send-trip-reminders-every-30-min": {
        "task": "trips.tasks.send_trip_reminders",
        "schedule": 1800.0,
    },
    "expire-pending-bookings-every-minute": {
        "task": "trips.tasks.expire_pending_bookings",
        "schedule": 60.0,
    },
    "pre-expiry-booking-warnings-every-minute": {
        "task": "bookings.tasks.send_pre_expiry_warnings",
        "schedule": 60.0,
    },
    # Trip inventory is manager-created (admin UI / optional generate endpoint).
    # Do not auto-generate from beat — keeps inventory intentional.
    # Payment cleanup / provider health
    "cleanup-stale-payments-every-2-min": {
        "task": "payments.tasks.cleanup_stale_payments",
        "schedule": 120.0,
    },
    "expire-stale-momo-transactions-every-2-min": {
        "task": "payments.tasks.expire_stale_momo_transactions",
        "schedule": 120.0,
    },
    "expire-stale-mpesa-transactions-every-2-min": {
        "task": "payments.tasks.expire_stale_mpesa_transactions",
        "schedule": 120.0,
    },
    "monitor-payment-provider-health-every-5-min": {
        "task": "payments.payment_tasks.monitor_payment_provider_health",
        "schedule": 300.0,
    },
    "process-pending-webhook-retries-every-2-min": {
        "task": "payments.payment_tasks.process_pending_webhook_retries",
        "schedule": 120.0,
    },
    "retry-failed-celery-tasks-every-5-min": {
        "task": "payments.payment_tasks.retry_failed_celery_tasks",
        "schedule": 300.0,
    },
}
