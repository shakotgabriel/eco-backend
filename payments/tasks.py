import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def initiate_mobile_money_payment(self, payment_id):
    """
    Legacy task — previously used Africa's Talking mobile money checkout.
    Now replaced by dedicated MPESA (stk-push) and MTN MoMo (request-to-pay)
    endpoints. Kept for backward compatibility with MOBILE_MONEY payment method.
    """
    from .models import Payment

    try:
        payment = Payment.objects.select_related("booking").get(pk=payment_id)
    except Payment.DoesNotExist:
        logger.error(f"Payment {payment_id} not found.")
        return

    logger.warning(
        f"initiate_mobile_money_payment called for payment {payment_id} "
        f"(booking {payment.booking.booking_reference}). "
        f"Use MPESA or MTN_MOMO payment method instead."
    )


# ── MTN MoMo initiate task ───────────────────────────────────────────────


@shared_task(bind=True, max_retries=2, default_retry_delay=10)
def initiate_momo_payment(self, payment_id):
    """
    Called from booking creation when payment_method == 'MTN_MOMO'.
    Sends a Request to Pay to the customer's phone via MTN MoMo API,
    creates a MoMoTransaction record, and kicks off status polling.
    """
    from .models import MoMoTransaction, Payment
    from .mtn_momo import MoMoError, get_client_for_payment

    try:
        payment = Payment.objects.select_related("booking").get(pk=payment_id)
    except Payment.DoesNotExist:
        logger.error(f"MoMo initiate: Payment {payment_id} not found.")
        return

    if payment.status != "PENDING":
        logger.info(f"MoMo initiate: Payment {payment_id} is {payment.status}, skipping.")
        return

    booking = payment.booking
    # Use the booking's payment_phone (strip + prefix for MSISDN)
    phone = str(booking.payment_phone).lstrip("+")

    try:
        client = get_client_for_payment(payment)
        reference_id = client.request_to_pay(
            amount=payment.amount,
            phone_number=phone,
            external_id=booking.booking_reference,
            payer_message=f"EcoBus ticket {booking.booking_reference}",
            payee_note=f"Booking {booking.booking_reference}",
        )
    except MoMoError as exc:
        logger.error(f"MoMo initiate failed for payment {payment_id}: {exc}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        # Final failure — mark payment as failed
        payment.status = "FAILED"
        payment.save()
        return

    # Create the MoMo transaction record
    MoMoTransaction.objects.create(
        payment=payment,
        reference_id=reference_id,
        external_id=booking.booking_reference,
        payer_msisdn=phone,
    )

    logger.info(
        f"MoMo payment initiated: ref={reference_id}, "
        f"booking={booking.booking_reference}, phone={phone}"
    )

    # Start polling for the result after 15 seconds
    poll_momo_payment_status.apply_async(
        args=[str(reference_id)],
        countdown=15,
    )


# ── MTN MoMo polling task ────────────────────────────────────────────────

MAX_POLL_ATTEMPTS = 12  # 12 × 15s = 3 minutes of polling
POLL_INTERVAL = 15  # seconds between retries


@shared_task(bind=True, max_retries=MAX_POLL_ATTEMPTS, default_retry_delay=POLL_INTERVAL)
def poll_momo_payment_status(self, reference_id):
    """
    Poll MTN MoMo for the status of a Request to Pay.

    Called automatically after momo_request_to_pay initiates a payment.
    Retries every 15 seconds for up to ~3 minutes. If the payment is
    still PENDING after all retries, it marks the transaction as TIMEOUT.
    """
    from django.utils import timezone
    from .models import MoMoTransaction
    from .mtn_momo import MoMoError, get_client_for_payment

    try:
        momo_tx = MoMoTransaction.objects.select_related(
            "payment", "payment__booking"
        ).get(reference_id=reference_id)
    except MoMoTransaction.DoesNotExist:
        logger.error(f"MoMo poll: transaction {reference_id} not found.")
        return

    # Already resolved — nothing to do
    if momo_tx.momo_status != "PENDING":
        logger.info(f"MoMo poll: {reference_id} already {momo_tx.momo_status}.")
        return

    # Call MTN status endpoint
    try:
        client = get_client_for_payment(momo_tx.payment)
        mtn_data = client.get_payment_status(str(reference_id))
    except MoMoError as exc:
        logger.warning(f"MoMo poll failed for {reference_id}: {exc}")
        raise self.retry(exc=exc)

    mtn_status = mtn_data.get("status", "").upper()

    if mtn_status == "PENDING":
        # Still waiting — retry
        logger.info(
            f"MoMo poll: {reference_id} still PENDING "
            f"(attempt {self.request.retries + 1}/{MAX_POLL_ATTEMPTS})"
        )
        raise self.retry()

    # Terminal status reached — update records
    from .views import _update_momo_transaction
    _update_momo_transaction(momo_tx, mtn_data)
    logger.info(f"MoMo poll: {reference_id} resolved to {mtn_status}.")


@shared_task
def expire_stale_momo_transactions():
    """
    Mark stale MoMo polling attempts as timed out without cancelling the sale.
    Run this periodically via Celery Beat (e.g. every 2 minutes).
    """
    from datetime import timedelta
    from django.utils import timezone
    from .models import MoMoTransaction

    cutoff = timezone.now() - timedelta(minutes=5)
    stale = MoMoTransaction.objects.filter(
        momo_status="PENDING",
        created_at__lt=cutoff,
    ).select_related("payment")

    count = 0
    for momo_tx in stale:
        momo_tx.momo_status = "TIMEOUT"
        momo_tx.reason = "No response from MTN within timeout period."
        momo_tx.status_checked_at = timezone.now()
        momo_tx.save()
        count += 1

    if count:
        logger.info(f"Expired {count} stale MoMo transaction(s).")


# ── M-Pesa Daraja polling task ─────────────────────────────────────────

MPESA_MAX_POLL_ATTEMPTS = 8  # 8 × 10s = ~80 seconds of polling
MPESA_POLL_INTERVAL = 10  # seconds between retries


@shared_task(bind=True, max_retries=MPESA_MAX_POLL_ATTEMPTS, default_retry_delay=MPESA_POLL_INTERVAL)
def poll_mpesa_payment_status(self, checkout_request_id):
    """
    Poll Safaricom Daraja for the status of an STK Push transaction.

    Called automatically after mpesa_stk_push initiates a payment.
    Retries every 10 seconds for up to ~80 seconds. If the payment is
    still PENDING after all retries, it marks the transaction as TIMEOUT.
    """
    from django.utils import timezone
    from .models import MpesaTransaction
    from .mpesa import MpesaError, get_client

    try:
        mpesa_tx = MpesaTransaction.objects.select_related(
            "payment", "payment__booking"
        ).get(checkout_request_id=checkout_request_id)
    except MpesaTransaction.DoesNotExist:
        logger.error(f"M-Pesa poll: transaction {checkout_request_id} not found.")
        return

    # Already resolved — nothing to do
    if mpesa_tx.mpesa_status != "PENDING":
        logger.info(f"M-Pesa poll: {checkout_request_id} already {mpesa_tx.mpesa_status}.")
        return

    # Query Safaricom STK status endpoint
    try:
        client = get_client()
        query_data = client.query_stk_status(checkout_request_id)
    except MpesaError as exc:
        logger.warning(f"M-Pesa poll failed for {checkout_request_id}: {exc}")
        raise self.retry(exc=exc)

    result_code = query_data.get("ResultCode")

    if result_code is None:
        # Still processing — retry
        logger.info(
            f"M-Pesa poll: {checkout_request_id} still PENDING "
            f"(attempt {self.request.retries + 1}/{MPESA_MAX_POLL_ATTEMPTS})"
        )
        raise self.retry()

    # Terminal status reached — update records
    from .views import _update_mpesa_transaction
    _update_mpesa_transaction(mpesa_tx, query_data)
    logger.info(f"M-Pesa poll: {checkout_request_id} resolved to {mpesa_tx.mpesa_status}.")


@shared_task
def expire_stale_mpesa_transactions():
    """
    Mark stale M-Pesa polling attempts as timed out without cancelling the sale.
    Run this periodically via Celery Beat (e.g. every 2 minutes).
    """
    from datetime import timedelta
    from django.utils import timezone
    from .models import MpesaTransaction

    cutoff = timezone.now() - timedelta(minutes=5)
    stale = MpesaTransaction.objects.filter(
        mpesa_status="PENDING",
        created_at__lt=cutoff,
    ).select_related("payment")

    count = 0
    for mpesa_tx in stale:
        mpesa_tx.mpesa_status = "TIMEOUT"
        mpesa_tx.result_desc = "No response from Safaricom within timeout period."
        mpesa_tx.status_checked_at = timezone.now()
        mpesa_tx.save()
        count += 1

    if count:
        logger.info(f"Expired {count} stale M-Pesa transaction(s).")


# ── Payment cleanup task ───────────────────────────────────────────────────


@shared_task
def cleanup_stale_payments():
    """
    Mark non-provider pending payments as failed after their local timeout.
    Provider callbacks remain authoritative and may arrive after UI polling ends.
    
    Run this periodically via Celery Beat (e.g. every 2 minutes).
    Uses PAYMENT_TIMEOUT_MINUTES setting (default: 5 minutes).
    """
    from datetime import timedelta
    from django.utils import timezone
    from django.conf import settings
    from django.db import transaction
    from bookings.models import Booking
    from .models import Payment

    # Get timeout config from settings
    timeout_minutes = getattr(settings, "PAYMENT_TIMEOUT_MINUTES", 5)
    cutoff_time = timezone.now() - timedelta(minutes=timeout_minutes)

    # Find all PENDING payments older than timeout
    stale_payments = Payment.objects.filter(
        status="PENDING",
        created_at__lt=cutoff_time,
    ).exclude(method__in=["MPESA", "MTN_MOMO", "MOBILE_MONEY"]).select_related("booking")

    count = 0
    for payment in stale_payments:
        booking = payment.booking

        with transaction.atomic():
            # Mark payment as FAILED
            payment.status = "FAILED"
            payment.save(update_fields=["status", "updated_at"])

            # Cancel booking if still PENDING (triggers signal to release seats)
            if booking.status == "PENDING":
                booking.status = "CANCELLED"
                booking.save()

                logger.warning(
                    f"✓ Auto-cancelled payment {payment.id} and booking "
                    f"{booking.booking_reference} (PENDING for {timeout_minutes}+ minutes)"
                )
                count += 1

                # Send SMS notification to customer about timeout
                try:
                    from bookings.tasks import send_booking_cancellation_sms
                    send_booking_cancellation_sms.delay(booking.pk)
                except Exception as e:
                    logger.error(
                        f"Failed to send SMS notification for booking "
                        f"{booking.booking_reference}: {e}"
                    )

    if count:
        logger.info(f"✓ Cleanup complete: Processed {count} stale payments.")
