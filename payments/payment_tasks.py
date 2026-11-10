"""
Extended Celery tasks for payment improvements:
- Webhook retry processing
- Provider health monitoring
- Dead-letter queue retry
- Exponential backoff implementation
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# #8: PROVIDER HEALTH MONITORING
# ══════════════════════════════════════════════════════════════════════════


@shared_task
def monitor_payment_provider_health():
    """
    Periodically check health of all payment providers.
    Run via Celery Beat every 5 minutes.
    """
    from .payment_utilities import check_provider_health
    from .payment_improvements import PaymentProviderHealth

    providers = ["MTN_MOMO", "MPESA"]
    
    for provider in providers:
        try:
            is_healthy, error_msg, response_time = check_provider_health(provider)
            status = "HEALTHY" if is_healthy else "DEGRADED"
            logger.info(f"Provider {provider}: {status} ({response_time}ms)")
        except Exception as e:
            logger.error(f"Health check error for {provider}: {e}")


# ══════════════════════════════════════════════════════════════════════════
# #9: WEBHOOK RETRY PROCESSING
# ══════════════════════════════════════════════════════════════════════════


@shared_task
def process_pending_webhook_retries():
    """
    Process WebhookRetry records that are ready for retry.
    Run via Celery Beat every 2 minutes.
    """
    from .payment_improvements import WebhookRetry

    # Find webhooks ready for retry
    now = timezone.now()
    pending_retries = WebhookRetry.objects.filter(
        status="PENDING",
        next_retry_at__lte=now
    ).select_related("payment")

    for webhook in pending_retries:
        try:
            _retry_webhook_delivery(webhook)
        except Exception as e:
            logger.error(f"Failed to retry webhook {webhook.id}: {e}")


@shared_task
def retry_webhook_delivery(webhook_retry_id):
    """
    Retry delivering a specific webhook. Called by process_pending_webhook_retries.
    """
    from .payment_improvements import WebhookRetry

    try:
        webhook = WebhookRetry.objects.get(pk=webhook_retry_id)
    except WebhookRetry.DoesNotExist:
        logger.error(f"WebhookRetry {webhook_retry_id} not found")
        return

    _retry_webhook_delivery(webhook)


def _retry_webhook_delivery(webhook):
    """Helper to actually retry webhook delivery."""
    from .payment_improvements import WebhookRetry
    from .payment_utilities import schedule_webhook_retry

    if webhook.status != "PENDING":
        return

    try:
        # Determine handler based on webhook type
        if webhook.webhook_type == "MOMO_CALLBACK":
            response = _process_momo_webhook_locally(webhook.payload)
        elif webhook.webhook_type == "MPESA_CALLBACK":
            response = _process_mpesa_webhook_locally(webhook.payload)
        elif webhook.webhook_type == "AT_CALLBACK":
            response = _process_at_webhook_locally(webhook.payload)
        else:
            logger.error(f"Unknown webhook type: {webhook.webhook_type}")
            webhook.status = "ABANDONED"
            webhook.save()
            return

        # Success
        webhook.status = "DELIVERED"
        webhook.last_attempt_at = timezone.now()
        webhook.save()
        logger.info(f"✓ Webhook {webhook.id} delivered successfully")

    except Exception as e:
        webhook.attempt_count += 1
        webhook.last_error = str(e)
        webhook.last_attempt_at = timezone.now()

        if webhook.attempt_count >= webhook.max_attempts:
            webhook.status = "ABANDONED"
            logger.error(f"✗ Webhook {webhook.id} abandoned after {webhook.attempt_count} attempts")
        else:
            # Schedule next retry
            from .payment_utilities import calculate_exponential_backoff
            next_delay = calculate_exponential_backoff(webhook.attempt_count - 1, base_delay_seconds=60)
            webhook.next_retry_at = timezone.now() + timedelta(seconds=next_delay)
            logger.warning(f"Webhook {webhook.id} retry #{webhook.attempt_count} scheduled in {next_delay}s")

        webhook.save()


def _process_momo_webhook_locally(payload):
    """Process a MoMo callback payload locally (for retry)."""
    from .views import _update_momo_transaction
    from .models import MoMoTransaction

    reference_id = payload.get("referenceId")
    if not reference_id:
        raise ValueError("Missing referenceId in MoMo payload")

    momo_tx = MoMoTransaction.objects.get(reference_id=reference_id)
    _update_momo_transaction(momo_tx, payload)


def _process_mpesa_webhook_locally(payload):
    """Process an M-Pesa callback payload locally (for retry)."""
    from .views import _update_mpesa_transaction_from_callback
    from .models import MpesaTransaction

    stk_callback = payload.get("Body", {}).get("stkCallback", {})
    checkout_request_id = stk_callback.get("CheckoutRequestID")
    if not checkout_request_id:
        raise ValueError("Missing CheckoutRequestID in M-Pesa payload")

    mpesa_tx = MpesaTransaction.objects.get(checkout_request_id=checkout_request_id)
    _update_mpesa_transaction_from_callback(mpesa_tx, stk_callback)


def _process_at_webhook_locally(payload):
    """Process an Africa's Talking callback payload locally (for retry)."""
    from .models import Payment

    payment_id = payload.get("requestMetadata", {}).get("payment_id")
    if not payment_id:
        raise ValueError("Missing payment_id in AT payload")

    payment = Payment.objects.get(pk=payment_id)
    # Re-process payment status
    # (This is simplified; full logic would be in a helper function)


# ══════════════════════════════════════════════════════════════════════════
# #7: DEAD-LETTER QUEUE RETRY
# ══════════════════════════════════════════════════════════════════════════


@shared_task
def retry_failed_celery_tasks():
    """
    Retry FailedCeleryTask records that are ready for retry.
    Run via Celery Beat every 5 minutes.
    """
    from .payment_improvements import FailedCeleryTask
    from celery import current_app

    now = timezone.now()
    failed_tasks = FailedCeleryTask.objects.filter(
        status="PENDING",
        next_retry_at__lte=now
    )

    for task_record in failed_tasks:
        try:
            # Re-register and execute the task
            task_func = current_app.tasks.get(task_record.task_name)
            if not task_func:
                logger.error(f"Task {task_record.task_name} not found")
                task_record.status = "ABANDONED"
                task_record.save()
                continue

            # Re-execute with original args/kwargs
            task_func.apply_async(args=task_record.args, kwargs=task_record.kwargs)
            task_record.status = "RESOLVED"
            task_record.save()
            logger.info(f"✓ Retried task {task_record.task_name} (attempt {task_record.attempt_count})")

        except Exception as e:
            task_record.attempt_count += 1
            task_record.error_message = str(e)

            if task_record.attempt_count >= task_record.max_retries:
                task_record.status = "ABANDONED"
                logger.error(f"✗ Task {task_record.task_name} abandoned")
            else:
                from .payment_utilities import calculate_exponential_backoff
                next_delay = calculate_exponential_backoff(task_record.attempt_count - 1)
                task_record.next_retry_at = timezone.now() + timedelta(seconds=next_delay)

            task_record.save()


@shared_task(bind=True)
def handle_failed_task_with_dlq(self, task_name, args, kwargs, exc, traceback_str):
    """
    Store a failed task in the dead-letter queue for later retry.
    Called when a task exhausts its retries.
    """
    from .payment_improvements import FailedCeleryTask
    from .payment_utilities import calculate_exponential_backoff

    next_delay = calculate_exponential_backoff(0, base_delay_seconds=60)

    FailedCeleryTask.objects.create(
        task_name=task_name,
        args=args or [],
        kwargs=kwargs or {},
        error_message=str(exc),
        traceback=traceback_str,
        attempt_count=1,
        max_retries=3,
        next_retry_at=timezone.now() + timedelta(seconds=next_delay),
        status="PENDING"
    )

    logger.error(f"Task {task_name} stored in DLQ: {exc}")
