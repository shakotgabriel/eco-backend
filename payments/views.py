import hashlib
import hmac
import logging

from bebeto_backend.permissions import IsStaffOrAbove
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import api_view, permission_classes as perm_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated

from bebeto_backend.throttles import PaymentCallbackThrottle
from bookings.models import Booking
from rest_framework.response import Response

from .currency import convert
from .idempotency import require_idempotency_key
from .models import ExchangeRate, MoMoTransaction, MpesaTransaction, Payment
from .mpesa import MpesaError, get_client as get_mpesa_client
from .mtn_momo import MoMoError, get_client as get_momo_client, get_client_for_payment, market_for_msisdn
from .seat_validation import validate_seats_still_available
from .webhook_verification import verify_momo_callback_request, verify_mpesa_callback_request
from .serializers import (
    CurrencyConvertSerializer,
    ExchangeRateSerializer,
    MoMoRequestToPaySerializer,
    MoMoTransactionSerializer,
    MpesaSTKPushSerializer,
    MpesaTransactionSerializer,
    PaymentSerializer,
)

logger = logging.getLogger(__name__)


def _can_access_public_booking_payment(request, booking):
    """A booking id alone is never a public payment credential."""
    user = request.user
    if user.is_authenticated and user.role in ("ADMIN", "MANAGER", "STAFF"):
        return True
    agent = getattr(user, "agent_profile", None) if user.is_authenticated else None
    if agent is not None and agent.status == "ACTIVE":
        return booking.user_id == user.id
    return (
        str(request.data.get("booking_reference", "")).strip() == booking.booking_reference
        and str(request.data.get("payment_phone", "")).strip() == str(booking.payment_phone)
    )


def _frozen_collection_fields(booking, collection_currency):
    """Freeze the FX quote before the provider request leaves our process."""
    route_currency = booking.trip.route.currency
    amount, rate = convert(booking.total_price, route_currency, collection_currency)
    return {
        "amount": amount,
        "currency": collection_currency,
        "original_amount": booking.total_price,
        "original_currency": route_currency,
        "exchange_rate": rate,
    }


def _open_collection_payment(booking, method, collection_currency):
    """
    Create a PENDING provider payment while holding the booking row lock, so
    double taps and client retries can't start a second charge.

    Returns (payment, None) or (None, Response) when no new charge should start.
    """
    with transaction.atomic():
        locked = Booking.objects.select_for_update().select_related("trip__route").get(pk=booking.pk)
        if locked.payments.filter(status="COMPLETED").exists():
            return None, Response(
                {
                    "detail": "This booking is already paid.",
                    "already_paid": True,
                    "booking_reference": locked.booking_reference,
                    "booking_status": locked.status,
                },
                status=status.HTTP_200_OK,
            )
        pending = locked.payments.filter(status="PENDING", method__in=("MPESA", "MTN_MOMO")).first()
        if pending:
            return None, Response(
                {
                    "detail": "A payment request is already waiting on the customer's phone. Approve it or wait for it to expire before trying again.",
                    "payment_id": pending.id,
                    "method": pending.method,
                },
                status=status.HTTP_409_CONFLICT,
            )
        try:
            payment = Payment.objects.create(
                booking=locked, method=method, status="PENDING",
                **_frozen_collection_fields(locked, collection_currency),
            )
        except ValueError as exc:
            return None, Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return payment, None


class PaymentViewSet(viewsets.ModelViewSet):
    queryset = Payment.objects.select_related(
        "booking__trip__route__origin", "booking__trip__route__destination"
    ).all()
    serializer_class = PaymentSerializer
    permission_classes = [IsStaffOrAbove]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["booking", "method", "status"]
    search_fields = ["booking__booking_reference", "transaction_ref"]
    ordering_fields = ["created_at", "amount"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_authenticated and user.role == "STAFF":
            return qs.filter(booking__user=user)
        agent = getattr(user, "agent_profile", None) if user.is_authenticated else None
        if agent is not None and agent.status == "ACTIVE":
            return qs.filter(booking__user=user)
        return qs


@api_view(["POST"])
@perm_classes([AllowAny])


def _cache_callback_response(callback_id, webhook_type, response):
    """
    Cache a callback response for idempotency.
    If callback_id is already seen, the cached response will be returned.
    """
    if not callback_id:
        return
    
    from .payment_improvements import CallbackIdempotencyKey
    try:
        CallbackIdempotencyKey.objects.create(
            callback_id=str(callback_id),
            webhook_type=webhook_type,
            transaction_ref=str(callback_id),
            response_status=response.status_code,
            response_body=response.data if hasattr(response, "data") else {},
        )
    except Exception:
        # Ignore duplicates (race condition)
        pass


@throttle_classes([PaymentCallbackThrottle])
def mobile_money_callback(request):
    """
    Webhook called by Africa's Talking when a mobile money payment
    status changes (e.g. PendingConfirmation → Success / Failed).
    IDEMPOTENT: Duplicate callbacks are cached and returned immediately.

    AT sends JSON like:
    {
        "transactionId": "ATPid_...",
        "category": "MobileCheckout",
        "provider": "Mpesa",
        "providerRefId": "...",
        "providerChannel": "...",
        "productName": "...",
        "sourceType": "phoneNumber",
        "source": "+211...",
        "destinationType": "phoneNumber",
        "destination": "...",
        "value": "SSP 1500.00",
        "transactionFee": "...",
        "providerFee": "...",
        "status": "Success" | "Failed",
        "description": "...",
        "requestMetadata": {"booking_reference": "...", "payment_id": "..."},
        "transactionDate": "..."
    }
    """
    from .payment_improvements import CallbackIdempotencyKey

    data = request.data
    transaction_id = data.get("transactionId", "")

    # Check for duplicate callbacks (idempotency)
    if transaction_id:
        existing_callback = CallbackIdempotencyKey.objects.filter(
            callback_id=transaction_id,
            webhook_type="AT_CALLBACK"
        ).first()
        if existing_callback:
            logger.info(f"Duplicate AT callback: {transaction_id}, returning cached response")
            return Response(existing_callback.response_body, status=existing_callback.response_status)
    # ── Verify webhook signature ──────────────────────────────────────
    # Africa's Talking sends no built-in HMAC, but we use a shared secret
    # set in AT_WEBHOOK_SECRET env var. The caller must include it as
    # X-AT-Signature header or we reject the request.
    webhook_secret = getattr(settings, "AT_WEBHOOK_SECRET", "")
    if webhook_secret:
        sig_header = request.META.get("HTTP_X_AT_SIGNATURE", "")
        if not sig_header:
            logger.warning("AT callback missing X-AT-Signature header.")
            response = Response(
                {"detail": "Missing signature"},
                status=status.HTTP_403_FORBIDDEN,
            )
            _cache_callback_response(transaction_id, "AT_CALLBACK", response)
            return response
        # Compute expected HMAC-SHA256 of the raw body
        expected = hmac.new(
            webhook_secret.encode(),
            request.body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, sig_header):
            logger.warning("AT callback signature mismatch.")
            response = Response(
                {"detail": "Invalid signature"},
                status=status.HTTP_403_FORBIDDEN,
            )
            _cache_callback_response(transaction_id, "AT_CALLBACK", response)
            return response

    logger.info(f"AT mobile money callback received: {data}")

    at_status = data.get("status", "")
    metadata = data.get("requestMetadata", {})
    payment_id = metadata.get("payment_id")

    if not payment_id:
        logger.warning("Callback missing payment_id in metadata.")
        response = Response({"detail": "Missing payment_id"}, status=status.HTTP_400_BAD_REQUEST)
        _cache_callback_response(transaction_id, "AT_CALLBACK", response)
        return response

    try:
        payment = Payment.objects.get(pk=payment_id)
    except Payment.DoesNotExist:
        logger.error(f"Payment {payment_id} not found for callback.")
        response = Response({"detail": "Payment not found"}, status=status.HTTP_404_NOT_FOUND)
        _cache_callback_response(transaction_id, "AT_CALLBACK", response)
        return response

    if payment.status != "PENDING":
        logger.info(f"Payment {payment_id} already {payment.status}, ignoring callback.")
        response = Response({"detail": "Already processed"})
        _cache_callback_response(transaction_id, "AT_CALLBACK", response)
        return response

    payment.transaction_ref = transaction_id

    if at_status == "Success":
        # Validate seat ownership before confirming payment
        seats_ok, seat_error = validate_seats_still_available(payment.booking)
        if not seats_ok:
            logger.error(f"Payment {payment_id}: seat conflict — {seat_error}")
            payment.status = "FAILED"
            payment.save()
            response = Response({"detail": f"Seat conflict: {seat_error}"}, status=status.HTTP_409_CONFLICT)
            _cache_callback_response(transaction_id, "AT_CALLBACK", response)
            return response

        payment.status = "COMPLETED"
        payment.save()
        logger.info(f"Payment {payment_id} completed via AT callback.")
    elif at_status == "Failed":
        payment.status = "FAILED"
        payment.save()
        logger.warning(f"Payment {payment_id} failed via AT callback: {data.get('description', '')}")
    else:
        logger.info(f"Payment {payment_id} received status '{at_status}', no action.")

    response = Response({"detail": "OK"})
    _cache_callback_response(transaction_id, "AT_CALLBACK", response)
    return response


class ExchangeRateViewSet(viewsets.ModelViewSet):
    """Manage exchange rates (staff writes, public reads)."""
    queryset = ExchangeRate.objects.all()
    serializer_class = ExchangeRateSerializer

    def get_permissions(self):
        if self.action in ["list", "retrieve"]:
            return [AllowAny()]
        return [IsStaffOrAbove()]


@api_view(["POST"])
@perm_classes([AllowAny])
def currency_convert(request):
    """
    Public endpoint to convert an amount between currencies.
    POST { "amount": 1500, "from_currency": "SSP", "to_currency": "USD" }
    """
    serializer = CurrencyConvertSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    d = serializer.validated_data
    try:
        converted, rate = convert(d["amount"], d["from_currency"], d["to_currency"])
    except ValueError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    return Response({
        "original_amount": str(d["amount"]),
        "from_currency": d["from_currency"],
        "converted_amount": str(converted),
        "to_currency": d["to_currency"],
        "exchange_rate": str(rate),
    })


# ══════════════════════════════════════════════════════════════════════════
# MTN MoMo Uganda endpoints
# ══════════════════════════════════════════════════════════════════════════


@api_view(["POST"])
@perm_classes([AllowAny])
@require_idempotency_key
def momo_request_to_pay(request):
    """
    Initiate an MTN MoMo collection (Request to Pay) for a booking.
    PREVENTS RACE CONDITIONS: Uses database-level locking.

    POST /api/v1/payments/momo/request-to-pay/
    {
        "booking_id": "<id>",
        "phone_number": "256771234567"
    }

    Creates a Payment + MoMoTransaction, sends the request to MTN,
    and returns the transaction details. The payer receives a USSD
    prompt on their phone to approve the payment.

    Safe to retry: an already-paid booking returns 200 with already_paid=true,
    and an open prompt returns 409 instead of starting a second charge.
    Send an Idempotency-Key header to replay the original response.
    """
    serializer = MoMoRequestToPaySerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    # ── Validate booking ──────────────────────────────────────────────
    try:
        booking = Booking.objects.get(pk=data["booking_id"])
    except Booking.DoesNotExist:
        return Response(
            {"detail": "Booking not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if booking.status not in ("PENDING", "CONFIRMED"):
        return Response(
            {"detail": f"Booking is {booking.status}, cannot accept payment."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not _can_access_public_booking_payment(request, booking):
        return Response(
            {"detail": "booking_reference and payment_phone must match this booking."},
            status=status.HTTP_403_FORBIDDEN,
        )

    from accounts.portal_payments import is_payment_method_enabled, payment_disabled_response
    if not is_payment_method_enabled("momo"):
        return payment_disabled_response("momo")

    # Check for existing pending MoMo payment on this booking
    existing = MoMoTransaction.objects.filter(
        payment__booking=booking,
        momo_status="PENDING",
    ).first()
    if existing:
        return Response(
            {
                "detail": "A pending MoMo payment already exists for this booking.",
                "reference_id": str(existing.reference_id),
            },
            status=status.HTTP_409_CONFLICT,
        )

    phone = data["phone_number"]
    client = get_momo_client(market_for_msisdn(phone))
    payment, blocked = _open_collection_payment(booking, "MTN_MOMO", client.currency)
    if blocked is not None:
        return blocked

    # ── Call MTN MoMo API ─────────────────────────────────────────────
    try:
        reference_id = client.request_to_pay(
            amount=payment.amount,
            phone_number=phone,
            external_id=booking.booking_reference,
            payer_message=f"EcoBus ticket {booking.booking_reference}",
            payee_note=f"Booking {booking.booking_reference}",
        )
    except MoMoError as e:
        payment.status = "FAILED"
        payment.save()
        logger.error(f"MoMo request_to_pay failed for booking {booking.booking_reference}: {e}")
        return Response(
            {"detail": f"MTN MoMo API error: {str(e)}"},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    # ── Create MoMoTransaction record ─────────────────────────────────
    momo_tx = MoMoTransaction.objects.create(
        payment=payment,
        reference_id=reference_id,
        external_id=booking.booking_reference,
        payer_msisdn=phone,
    )

    # Kick off async status polling after a short delay
    from .tasks import poll_momo_payment_status
    poll_momo_payment_status.apply_async(
        args=[str(momo_tx.reference_id)],
        countdown=15,  # first check after 15 seconds
    )

    logger.info(
        f"MoMo payment initiated: ref={reference_id}, "
        f"booking={booking.booking_reference}, phone={phone}"
    )

    return Response(
        MoMoTransactionSerializer(momo_tx).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@perm_classes([AllowAny])
def momo_check_status(request, reference_id):
    """
    Check / refresh the status of a MoMo transaction.

    GET /api/v1/payments/momo/status/<reference_id>/

    Polls MTN for the latest status and updates our records.
    """
    try:
        momo_tx = MoMoTransaction.objects.select_related(
            "payment", "payment__booking"
        ).get(reference_id=reference_id)
    except MoMoTransaction.DoesNotExist:
        return Response(
            {"detail": "MoMo transaction not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # If still pending, poll MTN for fresh status
    if momo_tx.momo_status == "PENDING":
        try:
            client = get_client_for_payment(momo_tx.payment)
            mtn_data = client.get_payment_status(str(momo_tx.reference_id))
            _update_momo_transaction(momo_tx, mtn_data)
        except MoMoError as e:
            logger.warning(f"MoMo status check failed for {reference_id}: {e}")

    return Response(MoMoTransactionSerializer(momo_tx).data)


@api_view(["POST"])
@perm_classes([AllowAny])
@throttle_classes([PaymentCallbackThrottle])
def momo_callback(request):
    """
    Webhook endpoint called by MTN MoMo when a Request to Pay completes.

    POST /api/v1/payments/momo/callback/

    MTN sends a JSON body like:
    {
        "financialTransactionId": "123456789",
        "externalId": "BEB-20260421-ABC123",
        "amount": "50000",
        "currency": "UGX",
        "payer": {"partyIdType": "MSISDN", "partyId": "256771234567"},
        "payerMessage": "...",
        "payeeNote": "...",
        "status": "SUCCESSFUL" | "FAILED",
        "reason": { "code": "...", "message": "..." }
    }

    The X-Reference-Id from the original request is sent as
    `referenceId` in the callback body (or via the URL).
    """
    from .payment_improvements import CallbackIdempotencyKey

    data = request.data
    reference_id = data.get("referenceId") or data.get("externalId")

    # Check for duplicate callbacks (idempotency)
    if reference_id:
        existing_callback = CallbackIdempotencyKey.objects.filter(
            callback_id=str(reference_id),
            webhook_type="MOMO_CALLBACK",
        ).first()
        if existing_callback:
            logger.info(f"Duplicate MoMo callback: {reference_id}, returning cached response")
            return Response(existing_callback.response_body, status=existing_callback.response_status)

    # Verify callback authenticity (IP whitelist + optional signature)
    is_valid, error = verify_momo_callback_request(request)
    if not is_valid:
        response = Response({"detail": error}, status=status.HTTP_403_FORBIDDEN)
        _cache_callback_response(reference_id, "MOMO_CALLBACK", response)
        return response

    logger.info(f"MoMo callback received: {data}")

    if not reference_id:
        logger.warning("MoMo callback missing referenceId.")
        response = Response({"detail": "Missing referenceId"}, status=status.HTTP_400_BAD_REQUEST)
        _cache_callback_response(reference_id, "MOMO_CALLBACK", response)
        return response

    # Try to find by reference_id first, then by external_id
    momo_tx = MoMoTransaction.objects.filter(reference_id=reference_id).first()
    if not momo_tx:
        momo_tx = MoMoTransaction.objects.filter(external_id=reference_id).first()
    if not momo_tx:
        logger.error(f"MoMo callback: transaction not found for ref={reference_id}")
        return Response({"detail": "Transaction not found"}, status=status.HTTP_404_NOT_FOUND)

    if momo_tx.momo_status not in ("PENDING", "TIMEOUT"):
        logger.info(f"MoMo callback for {reference_id}: already {momo_tx.momo_status}, ignoring.")
        response = Response({"detail": "Already processed"})
        _cache_callback_response(reference_id, "MOMO_CALLBACK", response)
        return response

    momo_tx.callback_received_at = timezone.now()
    _update_momo_transaction(momo_tx, data)

    logger.info(f"MoMo callback processed: ref={reference_id}, status={momo_tx.momo_status}")
    response = Response({"detail": "OK"})
    _cache_callback_response(reference_id, "MOMO_CALLBACK", response)
    # metrics
    try:
        from .metrics import increment_counter
        increment_counter("momo_callback_processed")
    except Exception:
        pass
    return response


@api_view(["POST"])
@perm_classes([AllowAny])
def momo_validate_account(request):
    """
    Check if a phone number is registered on MTN MoMo.

    POST /api/v1/payments/momo/validate-account/
    { "phone_number": "256771234567" }
    """
    phone = request.data.get("phone_number", "").lstrip("+")
    if not phone:
        return Response(
            {"detail": "phone_number is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    market = market_for_msisdn(phone)
    if market is None:
        return Response(
            {"detail": "Use an MTN Uganda (+256) or MTN South Sudan (+211) number."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        client = get_momo_client(market)
        is_active = client.validate_account_holder(phone)
    except MoMoError as e:
        return Response(
            {"detail": f"Validation failed: {str(e)}"},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    return Response({"phone_number": phone, "is_momo_active": is_active})


# ══════════════════════════════════════════════════════════════════════════
# M-Pesa Daraja (Safaricom Kenya) endpoints
# ══════════════════════════════════════════════════════════════════════════

@api_view(["POST"])
@perm_classes([AllowAny])
@require_idempotency_key
def mpesa_stk_push(request):
    """
    Initiate an M-Pesa STK Push (Lipa Na M-Pesa Online) for a booking.

    POST /api/v1/payments/mpesa/stk-push/
    Creates a Payment + MpesaTransaction, sends the STK Push to Safaricom,
    and returns the transaction details. The payer receives a PIN prompt
    on their phone to approve the payment.

    Safe to retry: see momo_request_to_pay.
    """
    serializer = MpesaSTKPushSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    # ── Validate booking ──────────────────────────────────────────────
    try:
        booking = Booking.objects.get(pk=data["booking_id"])
    except Booking.DoesNotExist:
        return Response(
            {"detail": "Booking not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if booking.status not in ("PENDING", "CONFIRMED"):
        return Response(
            {"detail": f"Booking is {booking.status}, cannot accept payment."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not _can_access_public_booking_payment(request, booking):
        return Response(
            {"detail": "booking_reference and payment_phone must match this booking."},
            status=status.HTTP_403_FORBIDDEN,
        )

    from accounts.portal_payments import is_payment_method_enabled, payment_disabled_response
    if not is_payment_method_enabled("mpesa"):
        return payment_disabled_response("mpesa")

    # Check for existing pending M-Pesa payment on this booking
    existing = MpesaTransaction.objects.filter(
        payment__booking=booking,
        mpesa_status="PENDING",
    ).first()
    if existing:
        return Response(
            {
                "detail": "A pending M-Pesa payment already exists for this booking.",
                "checkout_request_id": existing.checkout_request_id,
            },
            status=status.HTTP_409_CONFLICT,
        )

    payment, blocked = _open_collection_payment(booking, "MPESA", "KES")
    if blocked is not None:
        return blocked

    # ── Call Safaricom Daraja STK Push ─────────────────────────────────
    phone = data["phone_number"]
    try:
        client = get_mpesa_client()
        stk_response = client.stk_push(
            amount=payment.amount,
            phone_number=phone,
            account_reference=booking.booking_reference,
            transaction_desc=f"EcoBus {booking.booking_reference}",
        )
    except MpesaError as e:
        payment.status = "FAILED"
        payment.save()
        logger.error(f"M-Pesa STK Push failed for booking {booking.booking_reference}: {e}")
        return Response(
            {"detail": f"M-Pesa API error: {str(e)}"},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    # ── Create MpesaTransaction record ─────────────────────────────────
    mpesa_tx = MpesaTransaction.objects.create(
        payment=payment,
        merchant_request_id=stk_response.get("MerchantRequestID", ""),
        checkout_request_id=stk_response.get("CheckoutRequestID", ""),
        phone_number=phone,
    )

    # Kick off async status polling after a short delay
    from .tasks import poll_mpesa_payment_status
    poll_mpesa_payment_status.apply_async(
        args=[mpesa_tx.checkout_request_id],
        countdown=10,
    )

    logger.info(
        f"M-Pesa STK Push initiated: checkout={mpesa_tx.checkout_request_id}, "
        f"booking={booking.booking_reference}, phone={phone}"
    )

    return Response(
        MpesaTransactionSerializer(mpesa_tx).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@perm_classes([AllowAny])
def mpesa_check_status(request, checkout_request_id):
    """
    Check / refresh the status of an M-Pesa STK Push transaction.

    GET /api/v1/payments/mpesa/status/<checkout_request_id>/

    Queries Safaricom for the latest status and updates our records.
    """
    try:
        mpesa_tx = MpesaTransaction.objects.select_related(
            "payment", "payment__booking"
        ).get(checkout_request_id=checkout_request_id)
    except MpesaTransaction.DoesNotExist:
        return Response(
            {"detail": "M-Pesa transaction not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # If still pending, query Safaricom for fresh status
    if mpesa_tx.mpesa_status == "PENDING":
        try:
            client = get_mpesa_client()
            query_data = client.query_stk_status(mpesa_tx.checkout_request_id)
            _update_mpesa_transaction(mpesa_tx, query_data)
        except MpesaError as e:
            logger.warning(f"M-Pesa status query failed for {checkout_request_id}: {e}")

    return Response(MpesaTransactionSerializer(mpesa_tx).data)


@api_view(["POST"])
@perm_classes([AllowAny])
@throttle_classes([PaymentCallbackThrottle])
def mpesa_callback(request):
    """
    Webhook endpoint called by Safaricom when an STK Push completes.
    IDEMPOTENT: Duplicate callbacks are cached and returned immediately.

    POST /api/v1/payments/mpesa/callback/

    Safaricom sends JSON like:

    Safaricom sends JSON like:
    {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "...",
                "CheckoutRequestID": "...",
                "ResultCode": 0,
                "ResultDesc": "The service request is processed successfully.",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": 1.00},
                        {"Name": "MpesaReceiptNumber", "Value": "ABC123DEF"},
                        {"Name": "Balance"},
                        {"Name": "TransactionDate", "Value": 20260424102115},
                        {"Name": "PhoneNumber", "Value": 254712345678}
                    ]
                    from .payment_improvements import CallbackIdempotencyKey
    
                    # Check for duplicate callbacks (idempotency)
                    data = request.data
                    stk_callback = data.get("Body", {}).get("stkCallback", {})
                    checkout_request_id = stk_callback.get("CheckoutRequestID")
    
                    if checkout_request_id:
                        existing_callback = CallbackIdempotencyKey.objects.filter(
                            callback_id=str(checkout_request_id),
                            webhook_type="MPESA_CALLBACK"
                        ).first()
                        if existing_callback:
                            logger.info(f"Duplicate M-Pesa callback: {checkout_request_id}, returning cached response")
                            return Response(existing_callback.response_body, status=existing_callback.response_status)
    
                }
            }
        }
    }
    """
    # Extract callback identifiers and check idempotency
    from .payment_improvements import CallbackIdempotencyKey

    data = request.data
    stk_callback = data.get("Body", {}).get("stkCallback", {})
    checkout_request_id = stk_callback.get("CheckoutRequestID")

    # Check for duplicate callbacks first
    if checkout_request_id:
        existing_callback = CallbackIdempotencyKey.objects.filter(
            callback_id=str(checkout_request_id),
            webhook_type="MPESA_CALLBACK",
        ).first()
        if existing_callback:
            logger.info(f"Duplicate M-Pesa callback: {checkout_request_id}, returning cached response")
            return Response(existing_callback.response_body, status=existing_callback.response_status)

    # Verify callback authenticity (IP whitelist)
    is_valid, error = verify_mpesa_callback_request(request)
    if not is_valid:
        response = Response({"detail": error}, status=status.HTTP_403_FORBIDDEN)
        _cache_callback_response(checkout_request_id, "MPESA_CALLBACK", response)
        return response

    logger.info(f"M-Pesa callback received: {data}")

    if not stk_callback:
        logger.warning("M-Pesa callback missing stkCallback body.")
        response = Response({"detail": "Missing stkCallback"}, status=status.HTTP_400_BAD_REQUEST)
        _cache_callback_response(checkout_request_id, "MPESA_CALLBACK", response)
        return response

    if not checkout_request_id:
        logger.warning("M-Pesa callback missing CheckoutRequestID.")
        response = Response({"detail": "Missing CheckoutRequestID"}, status=status.HTTP_400_BAD_REQUEST)
        _cache_callback_response(checkout_request_id, "MPESA_CALLBACK", response)
        return response

    try:
        mpesa_tx = MpesaTransaction.objects.select_related(
            "payment", "payment__booking"
        ).get(checkout_request_id=checkout_request_id)
    except MpesaTransaction.DoesNotExist:
        logger.error(f"M-Pesa callback: transaction not found for {checkout_request_id}")
        response = Response({"detail": "Transaction not found"}, status=status.HTTP_404_NOT_FOUND)
        _cache_callback_response(checkout_request_id, "MPESA_CALLBACK", response)
        return response

    if mpesa_tx.mpesa_status not in ("PENDING", "TIMEOUT"):
        logger.info(f"M-Pesa callback for {checkout_request_id}: already {mpesa_tx.mpesa_status}, ignoring.")
        response = Response({"detail": "Already processed"})
        _cache_callback_response(checkout_request_id, "MPESA_CALLBACK", response)
        return response

    mpesa_tx.callback_received_at = timezone.now()
    _update_mpesa_transaction_from_callback(mpesa_tx, stk_callback)
    response = Response({"detail": "OK"})

    logger.info(f"M-Pesa callback processed: checkout={checkout_request_id}, status={mpesa_tx.mpesa_status}")
    _cache_callback_response(checkout_request_id, "MPESA_CALLBACK", response)
    try:
        from .metrics import increment_counter
        increment_counter("mpesa_callback_processed")
    except Exception:
        pass
    return response


# ── Helpers ──────────────────────────────────────────────────────────────


def _update_mpesa_transaction_from_callback(mpesa_tx, stk_callback):
    """
    Update an MpesaTransaction and its linked Payment based on
    the stkCallback data from Safaricom.
    """
    result_code = stk_callback.get("ResultCode")
    result_desc = stk_callback.get("ResultDesc", "")

    mpesa_tx.result_code = result_code
    mpesa_tx.result_desc = result_desc

    if result_code == 0:
        # Success — extract metadata
        metadata = stk_callback.get("CallbackMetadata", {}).get("Item", [])
        meta_dict = {}
        for item in metadata:
            if "Value" in item:
                meta_dict[item["Name"]] = item["Value"]

        mpesa_tx.mpesa_receipt_number = str(meta_dict.get("MpesaReceiptNumber", ""))

        # Validate seat ownership before confirming payment
        payment = mpesa_tx.payment
        seats_ok, seat_error = validate_seats_still_available(payment.booking)
        if not seats_ok:
            logger.error(f"M-Pesa payment {payment.id}: seat conflict — {seat_error}")
            mpesa_tx.mpesa_status = "FAILED"
            mpesa_tx.result_desc = f"Seat conflict: {seat_error}"
            mpesa_tx.save()
            payment.status = "FAILED"
            payment.save()
            return

        mpesa_tx.mpesa_status = "SUCCESSFUL"
        mpesa_tx.save()

        payment.status = "COMPLETED"
        payment.transaction_ref = mpesa_tx.mpesa_receipt_number
        payment.save()
    else:
        # Failed or cancelled
        if result_code == 1032:
            mpesa_tx.mpesa_status = "CANCELLED"
        else:
            mpesa_tx.mpesa_status = "FAILED"
        mpesa_tx.save()

        payment = mpesa_tx.payment
        payment.status = "FAILED"
        payment.save()


def _update_mpesa_transaction(mpesa_tx, query_data):
    """
    Update an MpesaTransaction based on Safaricom STK Query response.
    The query endpoint returns ResultCode at the top level.
    """
    result_code = query_data.get("ResultCode")
    result_desc = query_data.get("ResultDesc", "")
    mpesa_tx.status_checked_at = timezone.now()

    if result_code is None:
        mpesa_tx.save()
        return

    mpesa_tx.result_code = result_code
    mpesa_tx.result_desc = result_desc

    if result_code == 0:
        # Validate seat ownership before confirming payment
        payment = mpesa_tx.payment
        seats_ok, seat_error = validate_seats_still_available(payment.booking)
        if not seats_ok:
            logger.error(f"M-Pesa query payment {payment.id}: seat conflict — {seat_error}")
            mpesa_tx.mpesa_status = "FAILED"
            mpesa_tx.result_desc = f"Seat conflict: {seat_error}"
            mpesa_tx.save()
            payment.status = "FAILED"
            payment.save()
        else:
            mpesa_tx.mpesa_status = "SUCCESSFUL"
            mpesa_tx.save()
            payment.status = "COMPLETED"
            payment.save()
    elif str(result_code) != "":
        if result_code == 1032:
            mpesa_tx.mpesa_status = "CANCELLED"
        else:
            mpesa_tx.mpesa_status = "FAILED"
        mpesa_tx.save()
        payment = mpesa_tx.payment
        payment.status = "FAILED"
        payment.save()


# ── MoMo Helper ──────────────────────────────────────────────────────────


def _update_momo_transaction(momo_tx, mtn_data):
    """
    Update a MoMoTransaction and its linked Payment based on
    the status data returned by MTN (from poll or callback).
    
    Idempotent: safe to call multiple times.
    """
    from payments.idempotent_handlers import _on_payment_success_idempotent, _on_payment_failed_idempotent
    
    mtn_status = mtn_data.get("status", "").upper()
    
    if mtn_status == "SUCCESSFUL":
        momo_tx.financial_transaction_id = mtn_data.get("financialTransactionId", "")
        
        # Validate seat ownership before confirming payment
        payment = momo_tx.payment
        seats_ok, seat_error = validate_seats_still_available(payment.booking)
        if not seats_ok:
            logger.error(f"MoMo payment {payment.id}: seat conflict — {seat_error}")
            momo_tx.momo_status = "FAILED"
            momo_tx.reason = f"Seat conflict: {seat_error}"
            momo_tx.status_checked_at = timezone.now()
            momo_tx.save(update_fields=["momo_status", "reason", "status_checked_at"])
            
            # Use idempotent handler to mark payment as failed
            _on_payment_failed_idempotent(payment, reason=f"Seat conflict: {seat_error}")
            return

        # Use idempotent handler for payment success
        momo_tx.momo_status = "SUCCESSFUL"
        momo_tx.status_checked_at = timezone.now()
        momo_tx.save(update_fields=["momo_status", "financial_transaction_id", "status_checked_at"])
        
        booking, was_newly_completed = _on_payment_success_idempotent(payment)
        if was_newly_completed:
            logger.info(f"MoMo transaction {momo_tx.reference_id} → SUCCESSFUL and payment confirmed")

    elif mtn_status in ("FAILED", "REJECTED", "TIMEOUT", "EXPIRED"):
        reason = mtn_data.get("reason", {})
        if isinstance(reason, dict):
            momo_tx.reason = reason.get("message", str(reason))
        else:
            momo_tx.reason = str(reason)
        
        momo_tx.momo_status = mtn_status
        momo_tx.status_checked_at = timezone.now()
        momo_tx.save(update_fields=["momo_status", "reason", "status_checked_at"])
        
        # Use idempotent handler to mark payment as failed
        payment = momo_tx.payment
        _on_payment_failed_idempotent(payment, reason=f"MoMo {mtn_status}: {momo_tx.reason}")

    else:
        # Still PENDING or unknown status
        momo_tx.momo_status = mtn_status
        momo_tx.status_checked_at = timezone.now()
        momo_tx.save(update_fields=["momo_status", "status_checked_at"])
