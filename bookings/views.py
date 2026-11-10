from decimal import Decimal

from bebeto_backend.permissions import IsStaffOrAbove, PublicCreateOrStaff
from django.db import transaction
from django.db.models import Count, F, Sum
from bebeto_backend.throttles import BookingCreateThrottle, BookingLookupThrottle, PNRBurstThrottle
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import Booking, BookingGroup
from .phone import phones_match
from .serializers import (
    BookingCreateSerializer, BookingGroupSerializer, BookingSerializer,
    MultiLegBookingSerializer,
)


class BookingViewSet(viewsets.ModelViewSet):
    queryset = Booking.objects.select_related(
        "trip__route__origin", "trip__route__destination", "trip__bus", "user", "group"
    ).prefetch_related("tickets", "payments").all()
    permission_classes = [PublicCreateOrStaff]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "trip"]
    search_fields = ["booking_reference", "payer_name", "payment_phone"]
    ordering_fields = ["created_at", "total_price"]
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        # Agents and desk staff can only work with sales they created. Managers
        # and admins retain the operational overview.
        if user.is_authenticated and user.role == "STAFF":
            return qs.filter(user=user)
        agent = getattr(user, "agent_profile", None) if user.is_authenticated else None
        if agent is not None and agent.status == "ACTIVE":
            return qs.filter(user=user)
        return qs

    @staticmethod
    def _public_credentials_valid(request, booking):
        """Require the two customer-held booking credentials for public actions."""
        reference = str(request.data.get("booking_reference", "") or request.query_params.get("booking_reference", "")).strip()
        phone = str(request.data.get("payment_phone", "") or request.query_params.get("payment_phone", "")).strip()
        return (
            reference == booking.booking_reference
            and phones_match(booking.payment_phone, phone)
        )

    def initial(self, request, *args, **kwargs):
        """Auto-expire stale PENDING bookings on every request (no Celery needed)."""
        super().initial(request, *args, **kwargs)
        now = timezone.now()
        # Do not expire an in-flight provider collection simply because the UI
        # polling window ended. Provider callbacks are authoritative.
        stale = Booking.objects.filter(status="PENDING", expires_at__lte=now).exclude(
            payments__mpesa_transaction__mpesa_status="PENDING"
        ).exclude(
            payments__momo_transaction__momo_status="PENDING"
        ).distinct()
        for booking in stale:
            booking.status = "CANCELLED"
            booking.save(update_fields=["status", "updated_at"])

    def get_throttles(self):
        if self.action == "create":
            return [BookingCreateThrottle()]
        return super().get_throttles()

    def get_serializer_class(self):
        if self.action == "create":
            return BookingCreateSerializer
        return BookingSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = serializer.save()
        return Response(
            BookingSerializer(booking).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], permission_classes=[IsStaffOrAbove], url_path="confirm-cash")
    def confirm_cash(self, request, pk=None):
        """
        Staff/agent endpoint to confirm a cash payment booking.

        POST /api/v1/bookings/{id}/confirm-cash/
        {
          "collected_by": "Agent Name",          # optional
          "payment_currency": "UGX"              # optional — currency cash was collected in
        }

        Ticket fare stays in route currency. When payment_currency differs,
        Payment.amount is FX-converted and original_* / exchange_rate are stored.
        """
        booking = self.get_object()

        from accounts.portal_payments import is_payment_method_enabled, payment_disabled_response
        if not is_payment_method_enabled("cash"):
            return payment_disabled_response("cash")

        if booking.status != "PENDING":
            return Response(
                {"detail": f"Cannot confirm a {booking.status} booking. Only PENDING bookings can be confirmed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from payments.currency import convert
        from payments.models import Payment

        route_currency = booking.trip.route.currency
        allowed = {"SSP", "KES", "UGX", "RWF", "USD"}
        requested = str(request.data.get("payment_currency") or route_currency).upper().strip()
        if requested not in allowed:
            return Response(
                {"payment_currency": f"Unsupported currency. Use one of: {', '.join(sorted(allowed))}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        original_amount = booking.total_price
        try:
            collected_amount, rate_used = convert(original_amount, route_currency, requested)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        booking.status = "CONFIRMED"
        if not booking.user_id:
            booking.user = request.user
            booking.save(update_fields=["status", "user", "updated_at"])
        else:
            booking.save(update_fields=["status", "updated_at"])

        payment, created = Payment.objects.get_or_create(
            booking=booking,
            method="CASH",
            defaults={
                "amount": collected_amount,
                "currency": requested,
                "original_amount": original_amount,
                "original_currency": route_currency,
                "exchange_rate": rate_used,
                "status": "COMPLETED",
            },
        )
        if not created:
            payment.amount = collected_amount
            payment.currency = requested
            payment.original_amount = original_amount
            payment.original_currency = route_currency
            payment.exchange_rate = rate_used
            payment.status = "COMPLETED"
            payment.save()

        # Audit
        from reports.audit import log_action
        log_action("PAYMENT_COMPLETED", booking, request=request, details={
            "method": "CASH",
            "collected_by": request.data.get("collected_by", request.user.username),
            "ticket_amount": str(original_amount),
            "ticket_currency": route_currency,
            "collected_amount": str(collected_amount),
            "payment_currency": requested,
            "exchange_rate": str(rate_used),
        })

        # If a booking desk agent confirmed this sale, record it against their
        # commission ledger (idempotent — a booking can only ever have one AgentSale).
        agent = getattr(request.user, "agent_profile", None)
        if agent is not None and not hasattr(booking, "agent_sale"):
            from agents.models import AgentSale

            commission_pct = agent.effective_commission_pct
            commission_amount = (booking.total_price * commission_pct / Decimal("100")).quantize(Decimal("0.01"))
            AgentSale.objects.create(
                agent=agent,
                booking=booking,
                commission_pct=commission_pct,
                commission_amount=commission_amount,
            )
            agent.update_tier()

        # Trigger confirmation notifications
        try:
            from .tasks import send_booking_confirmation_email, send_booking_confirmation_sms
            send_booking_confirmation_email.delay(booking.pk)
            send_booking_confirmation_sms.delay(booking.pk)
        except Exception:
            pass

        return Response({
            "detail": "Cash payment confirmed. Booking is now CONFIRMED.",
            "booking_reference": booking.booking_reference,
            "status": booking.status,
            "ticket_amount": str(original_amount),
            "ticket_currency": route_currency,
            "collected_amount": str(collected_amount),
            "payment_currency": requested,
            "exchange_rate": str(rate_used),
        })

    @action(detail=True, methods=["get"], permission_classes=[AllowAny], url_path="payment-status")
    def payment_status(self, request, pk=None):
        """
        Get payment status for a booking (for polling).
        
        GET /api/v1/bookings/{id}/payment-status/?transaction_reference=XXX
        
        Returns payment status info for the given transaction reference.
        """
        booking = self.get_object()
        if not self._public_credentials_valid(request, booking):
            return Response({"detail": "booking_reference and payment_phone are required."}, status=status.HTTP_403_FORBIDDEN)
        transaction_ref = request.query_params.get("transaction_reference", "").strip()
        
        if not transaction_ref:
            return Response(
                {"detail": "transaction_reference query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Find payment by transaction reference
        from payments.models import Payment
        try:
            payment = Payment.objects.get(
                booking=booking,
                transaction_ref=transaction_ref,
            )
            return Response({
                "status": payment.status,
                "transaction_reference": payment.transaction_ref,
                "amount": str(payment.amount),
                "method": payment.method,
                "created_at": payment.created_at,
                "message": f"Payment is {payment.status}",
            })
        except Payment.DoesNotExist:
            return Response(
                {"detail": "Payment not found for this booking.", "status": "NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )

    @action(detail=True, methods=["post"], permission_classes=[AllowAny], url_path="cancel")
    def cancel_booking(self, request, pk=None):
        """
        Cancel a booking and release its seats. Tickets are non-refundable:
        no Refund records are created and completed payments stay COMPLETED.

        POST /api/v1/bookings/{id}/cancel/
        { "reason": "Customer requested" }  (optional)

        Passengers (booking_reference + payment_phone) can only cancel before
        departure; admins, managers and agents can cancel at any time.
        """
        booking = self.get_object()
        is_privileged = request.user.is_authenticated and (
            request.user.role in ("ADMIN", "MANAGER") or getattr(request.user, "agent_profile", None) is not None
        )
        if not is_privileged and not self._public_credentials_valid(request, booking):
            return Response(
                {"detail": "Provide the matching booking_reference and payment_phone to cancel this booking."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if booking.status in ("CANCELLED", "RESCHEDULED", "COMPLETED"):
            return Response(
                {"detail": f"Cannot cancel a {booking.status} booking."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        departure = booking.trip.departure_time
        hours_until = (departure - now).total_seconds() / 3600
        if not is_privileged and hours_until <= 0:
            return Response(
                {"detail": "This bus has already departed, so the booking can no longer be cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = request.data.get("reason", "")

        with transaction.atomic():
            booking = Booking.objects.select_for_update().get(pk=booking.pk)
            if booking.status in ("CANCELLED", "RESCHEDULED", "COMPLETED"):
                return Response({"detail": f"Cannot cancel a {booking.status} booking."}, status=status.HTTP_400_BAD_REQUEST)
            booking.status = "CANCELLED"
            booking.save()

            # A cancelled sale must not remain commissionable.
            try:
                sale = booking.agent_sale
                if sale.is_paid:
                    sale.commission_amount = Decimal("0.00")
                    sale.save(update_fields=["commission_amount", "updated_at"])
                else:
                    sale.delete()
                sale.agent.update_tier()
            except Exception:
                pass

        from reports.audit import log_action
        log_action("BOOKING_CANCELLED", booking, request=request, details={
            "reason": reason,
            "refund_amount": "0.00",
            "hours_until_departure": round(hours_until, 1),
        })

        return Response({
            "detail": "Booking cancelled. Your seats have been released. Tickets are non-refundable.",
            "booking_reference": booking.booking_reference,
            "hours_until_departure": round(hours_until, 1),
            "refund_percentage": 0,
            "refund_amount": "0.00",
            "reason": reason,
        })

    @action(detail=False, methods=["post"], permission_classes=[IsStaffOrAbove], url_path="recover-pending-cash")
    def recover_pending_cash(self, request):
        """Find a pending desk sale by reference and atomically cash-confirm it."""
        reference = str(request.data.get("booking_reference", "")).strip()
        if not reference:
            return Response({"booking_reference": "This field is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            booking = self.get_queryset().get(booking_reference=reference, status="PENDING")
        except Booking.DoesNotExist:
            return Response({"detail": "No pending booking found for that reference."}, status=status.HTTP_404_NOT_FOUND)
        # Reuse the normal cash confirmation flow, preserving the same FX and
        # commission behavior rather than creating a second recovery pathway.
        self.kwargs["pk"] = str(booking.pk)
        return self.confirm_cash(request, pk=str(booking.pk))

    @action(detail=False, methods=["get"], permission_classes=[IsStaffOrAbove], url_path="cash-drawer-summary")
    def cash_drawer_summary(self, request):
        """Daily cash totals grouped by collector and collection currency."""
        from payments.models import Payment
        day = request.query_params.get("date")
        payments = Payment.objects.filter(method="CASH", status="COMPLETED")
        if day:
            payments = payments.filter(paid_at__date=day)
        else:
            payments = payments.filter(paid_at__date=timezone.localdate())
        user = request.user
        if user.role == "STAFF" or getattr(user, "agent_profile", None) is not None:
            payments = payments.filter(booking__user=user)
        rows = payments.values("booking__user__username", "currency").annotate(
            count=Count("id"), total=Sum("amount")
        ).order_by("booking__user__username", "currency")
        return Response({"date": day or str(timezone.localdate()), "rows": [
            {"collector": row["booking__user__username"] or "Unassigned", "currency": row["currency"],
             "count": row["count"], "total": str(row["total"])} for row in rows
        ]})


@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([BookingLookupThrottle, PNRBurstThrottle])
def booking_lookup(request):
    """
    Public endpoint for guests to check their booking status.
    Query params: booking_reference, payment_phone (both required).
    """
    ref = request.query_params.get("booking_reference", "").strip().upper()
    phone = request.query_params.get("payment_phone", "").strip()

    if not ref or not phone:
        return Response(
            {"detail": "Both 'booking_reference' and 'payment_phone' are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    booking = (
        Booking.objects.select_related(
            "trip__route__origin", "trip__route__destination", "trip__bus"
        )
        .filter(booking_reference__iexact=ref)
        .first()
    )
    if booking is None or not phones_match(booking.payment_phone, phone):
        return Response(
            {"detail": "Booking not found. Check your reference and phone number."},
            status=status.HTTP_404_NOT_FOUND,
        )

    from tickets.serializers import TicketSerializer
    data = BookingSerializer(booking).data
    data["tickets"] = TicketSerializer(booking.tickets.all(), many=True).data
    trip = booking.trip
    route = trip.route
    data["trip_details"] = {
        "trip_id": trip.id,
        "route_id": route.id,
        "departure_time": trip.departure_time.isoformat(),
        "origin": route.origin.name,
        "destination": route.destination.name,
        "currency": route.currency,
    }
    return Response(data)


@api_view(["POST"])
@permission_classes([AllowAny])
def multi_leg_booking(request):
    """
    Book a round-trip or multi-leg journey.
    POST body includes trip_type, payer info, and legs[] with trip + tickets.
    """
    serializer = MultiLegBookingSerializer(data=request.data, context={"request": request})
    serializer.is_valid(raise_exception=True)
    group = serializer.save()
    return Response(BookingGroupSerializer(group).data, status=status.HTTP_201_CREATED)
