"""
Add this endpoint to payments/views.py
"""
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings

logger = logging.getLogger(__name__)


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([PaymentCallbackThrottle])
def payment_retry(request):
    """
    Retry a failed or timed-out payment.
    
    POST /api/v1/payments/{id}/retry/
    {
        "booking_id": "<uuid>"
    }
    
    Only allows retry of FAILED/TIMEOUT payments within X attempts.
    Creates a new Payment record and re-initiates the payment flow.
    """
    from payments.models import Payment
    from bookings.models import Booking
    
    payment_id = request.parser_context.get("kwargs", {}).get("id")
    if not payment_id:
        return Response(
            {"detail": "Payment ID required in URL"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    try:
        old_payment = Payment.objects.select_related("booking").get(pk=payment_id)
    except Payment.DoesNotExist:
        return Response(
            {"detail": "Payment not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    
    booking = old_payment.booking
    
    # Only allow retry if payment failed or timed out
    if old_payment.status not in ("FAILED",):
        return Response(
            {"detail": f"Cannot retry a {old_payment.status} payment. "
                      f"Only FAILED payments can be retried."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    # Check retry count (count previous failed attempts for this booking + method)
    retry_count = Payment.objects.filter(
        booking=booking,
        method=old_payment.method,
        status__in=("FAILED",)
    ).count()
    
    max_retries = getattr(settings, "PAYMENT_MAX_RETRIES", 3)
    if retry_count >= max_retries:
        return Response(
            {"detail": f"Maximum retry attempts ({max_retries}) exceeded. "
                      f"Please contact support."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    
    # Booking must still be PENDING (not expired or cancelled)
    if booking.status != "PENDING":
        return Response(
            {"detail": f"Booking is {booking.status}. "
                      f"Only PENDING bookings can retry payment."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    # Check if booking has expired
    from django.utils import timezone
    if booking.expires_at and booking.expires_at <= timezone.now():
        return Response(
            {"detail": "Booking has expired. Please create a new booking."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    # Create new Payment record
    new_payment = Payment.objects.create(
        booking=booking,
        amount=booking.total_price,
        currency=old_payment.currency,
        method=old_payment.method,
        status="PENDING",
    )
    
    # Re-initiate payment flow based on method
    if old_payment.method == "MTN_MOMO":
        from payments.tasks import initiate_momo_payment
        initiate_momo_payment.delay(new_payment.pk)
    elif old_payment.method == "MPESA":
        logger.info(f"Payment retry for M-Pesa: user must call STK push endpoint again")
    elif old_payment.method == "MOBILE_MONEY":
        from payments.tasks import initiate_mobile_money_payment
        initiate_mobile_money_payment.delay(new_payment.pk)
    
    logger.info(
        f"✓ Payment retry initiated: old={old_payment.id}, new={new_payment.id}, "
        f"booking={booking.booking_reference}, method={old_payment.method}, "
        f"retry_count={retry_count + 1}"
    )
    
    from payments.serializers import PaymentSerializer
    return Response(
        {
            "detail": "Payment retry initiated",
            "new_payment": PaymentSerializer(new_payment).data,
            "retry_count": retry_count + 1,
            "max_retries": max_retries,
        },
        status=status.HTTP_201_CREATED,
    )
