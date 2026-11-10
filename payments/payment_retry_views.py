"""
Customer payment retry endpoint.
Allows customers to retry failed payments without creating a new booking.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes as perm_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
import logging

from .models import Payment
from .payment_utilities import create_payment_retry
from .serializers import PaymentSerializer

logger = logging.getLogger(__name__)


@api_view(["POST"])
@perm_classes([AllowAny])
def retry_payment(request):
    """
    Retry a failed payment for a booking.

    POST /api/v1/payments/retry/
    {
        "payment_id": "<id>",
        "new_method": "MTN_MOMO"  # Optional: change payment method
    }

    Guest bookings must also send booking_reference and payment_phone (the
    same credentials as booking lookup/cancel). Bookings with an owning user
    can be retried by that user or by staff.

    Idempotent: a booking that is already paid, or that already has a pending
    attempt, never gets a second payment record.

    Returns:
        201: New payment created successfully
        400: Invalid request or payment cannot be retried
        404: Payment not found
    """
    payment_id = request.data.get("payment_id")
    new_method = request.data.get("new_method")

    if not payment_id:
        return Response(
            {"detail": "payment_id is required."},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        original_payment = Payment.objects.select_related("booking").get(pk=payment_id)
    except Payment.DoesNotExist:
        return Response(
            {"detail": "Payment not found."},
            status=status.HTTP_404_NOT_FOUND
        )

    from .views import _can_access_public_booking_payment

    booking = original_payment.booking
    is_owner = booking.user_id is not None and booking.user_id == getattr(request.user, "id", None)
    if not is_owner and not _can_access_public_booking_payment(request, booking):
        return Response(
            {"detail": "booking_reference and payment_phone must match this booking."},
            status=status.HTTP_403_FORBIDDEN
        )

    # Create retry payment
    new_payment, error = create_payment_retry(payment_id, new_method)

    if error:
        return Response(
            {"detail": error},
            status=status.HTTP_400_BAD_REQUEST
        )

    logger.info(f"Payment retry created: user={request.user.id}, original={payment_id}, new={new_payment.id}")

    return Response(
        PaymentSerializer(new_payment).data,
        status=status.HTTP_201_CREATED
    )


@api_view(["GET"])
@perm_classes([IsAuthenticated])
def get_payment_retry_history(request, payment_id):
    """
    Get retry history for a payment.

    GET /api/v1/payments/{payment_id}/retry-history/

    Returns list of all retry attempts for this payment.
    """
    try:
        payment = Payment.objects.select_related("booking").get(pk=payment_id)
    except Payment.DoesNotExist:
        return Response(
            {"detail": "Payment not found."},
            status=status.HTTP_404_NOT_FOUND
        )

    # Verify user owns this booking
    if payment.booking.user != request.user:
        return Response(
            {"detail": "Unauthorized."},
            status=status.HTTP_403_FORBIDDEN
        )

    retry_logs = payment.retry_logs.all().order_by("created_at")
    
    retry_data = []
    for log in retry_logs:
        retry_data.append({
            "attempt_number": log.retry_attempt_number,
            "attempted_at": log.attempted_at,
            "status": log.status,
            "new_payment_id": log.new_payment_id,
        })

    return Response({
        "payment_id": payment.id,
        "original_status": payment.status,
        "total_retries": retry_logs.count(),
        "retries": retry_data
    })
