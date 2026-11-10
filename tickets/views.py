import io
import json

import qrcode
from bebeto_backend.permissions import IsDriverOrAbove, IsStaffOrAbove
from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes as perm_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from bebeto_backend.throttles import BookingLookupThrottle

from .models import Ticket
from .serializers import TicketSerializer
from .signing import build_signed_qr_payload, is_valid_download_token, verify_ticket_signature


def _can_access_ticket(request, ticket) -> bool:
    """Staff and active agents can fetch any ticket; everyone else needs the ticket's download token."""
    if IsStaffOrAbove().has_permission(request, None):
        return True
    return is_valid_download_token(ticket.ticket_number, request.query_params.get("token", ""))


_TICKET_ACCESS_DENIED = {"detail": "This ticket link is missing its access token. Look the booking up again to download it."}


def _boarding_response(ticket, *, boarded: bool, already_used: bool, valid: bool = True, detail: str | None = None):
    payload = {
        "valid": valid,
        "boarded": boarded,
        "already_used": already_used,
        "ticket_number": ticket.ticket_number,
        "ticket_status": ticket.status,
        "booking_reference": ticket.booking.booking_reference,
        "booking_status": ticket.booking.status,
        "passenger": f"{ticket.first_name} {ticket.last_name}",
        "id_number": ticket.id_number,
        "seat": ticket.seat.seat_number if ticket.seat else "Open",
        "seat_class": ticket.seat_class,
        "route": f"{ticket.booking.trip.route.origin.name} → {ticket.booking.trip.route.destination.name}",
        "departure": ticket.booking.trip.departure_time.isoformat(),
        "bus": str(ticket.booking.trip.bus),
    }
    if detail:
        payload["detail"] = detail
    return Response(payload)


def _mark_boarded(ticket):
    already_used = ticket.status == "USED"
    is_valid_for_boarding = ticket.status == "VALID" and ticket.booking.status == "CONFIRMED"
    if is_valid_for_boarding:
        ticket.status = "USED"
        ticket.save(update_fields=["status", "updated_at"])
    return already_used, is_valid_for_boarding


class TicketViewSet(viewsets.ModelViewSet):
    queryset = Ticket.objects.select_related(
        "booking__trip__route__origin", "booking__trip__route__destination", "booking__trip__bus", "seat"
    ).all()
    serializer_class = TicketSerializer
    permission_classes = [IsStaffOrAbove]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["booking", "booking__trip", "seat_class", "status"]
    search_fields = ["ticket_number", "first_name", "last_name", "id_number"]
    ordering_fields = ["created_at", "seat_class"]
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

    @action(detail=True, methods=["get"], url_path="qr")
    def qr_code(self, request, pk=None):
        """Generate a QR code PNG for a specific ticket."""
        ticket = self.get_object()
        return _generate_ticket_qr(ticket)

    @action(detail=True, methods=["get"], url_path="pdf")
    def pdf(self, request, pk=None):
        """Generate a PDF boarding pass for a specific ticket."""
        ticket = self.get_object()
        return _generate_ticket_pdf_response(ticket)


@api_view(["GET"])
@perm_classes([AllowAny])
@throttle_classes([BookingLookupThrottle])
def ticket_qr_public(request, ticket_number):
    """Public QR code endpoint — look up ticket by ticket_number."""
    try:
        ticket = Ticket.objects.select_related(
            "booking__trip__route__origin", "booking__trip__route__destination"
        ).get(ticket_number=ticket_number)
    except Ticket.DoesNotExist:
        return Response({"detail": "Ticket not found."}, status=404)
    if not _can_access_ticket(request, ticket):
        return Response(_TICKET_ACCESS_DENIED, status=403)
    return _generate_ticket_qr(ticket)


@api_view(["GET"])
@perm_classes([AllowAny])
@throttle_classes([BookingLookupThrottle])
def ticket_pdf_public(request, ticket_number):
    """Public PDF endpoint — download boarding pass by ticket_number."""
    try:
        ticket = Ticket.objects.select_related(
            "booking__trip__route__origin", "booking__trip__route__destination",
            "booking__trip__bus", "seat",
        ).get(ticket_number=ticket_number)
    except Ticket.DoesNotExist:
        return Response({"detail": "Ticket not found."}, status=404)
    if not _can_access_ticket(request, ticket):
        return Response(_TICKET_ACCESS_DENIED, status=403)
    return _generate_ticket_pdf_response(ticket)


@api_view(["POST"])
@perm_classes([IsDriverOrAbove])
def verify_ticket_qr(request):
    """
    On-bus QR verification endpoint for drivers/staff.

    POST /api/v1/tickets/verify-qr/
    { "qr_data": "<JSON string from scanned QR code>" }

    Validates the HMAC signature and returns ticket + passenger details.
    """
    raw = request.data.get("qr_data", "")
    if not raw:
        return Response({"detail": "qr_data is required."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return Response({"detail": "Invalid QR data format."}, status=status.HTTP_400_BAD_REQUEST)

    sig = data.pop("sig", None)
    if not sig:
        return Response(
            {"valid": False, "detail": "QR code has no signature — may be forged."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not verify_ticket_signature(data, sig):
        return Response(
            {"valid": False, "detail": "INVALID SIGNATURE — ticket may be forged or tampered with."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Signature valid — fetch ticket details
    ticket_number = data.get("ticket", "")
    try:
        ticket = Ticket.objects.select_related(
            "booking__trip__route__origin", "booking__trip__route__destination",
            "booking__trip__bus", "seat",
        ).get(ticket_number=ticket_number)
    except Ticket.DoesNotExist:
        return Response(
            {"valid": True, "detail": "Signature valid but ticket not found in database."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Check if ticket is in a usable state
    already_used, is_valid_for_boarding = _mark_boarded(ticket)
    return _boarding_response(ticket, boarded=is_valid_for_boarding, already_used=already_used)


@api_view(["POST"])
@perm_classes([IsStaffOrAbove])
def board_ticket(request):
    """
    Desk boarding check-in by ticket number (no QR required).

    POST /api/v1/tickets/board/
    { "ticket_number": "TKT-..." }
    """
    ticket_number = str(request.data.get("ticket_number", "")).strip()
    if not ticket_number:
        return Response({"detail": "ticket_number is required."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        ticket = Ticket.objects.select_related(
            "booking__trip__route__origin", "booking__trip__route__destination",
            "booking__trip__bus", "seat",
        ).get(ticket_number=ticket_number)
    except Ticket.DoesNotExist:
        return Response({"detail": "Ticket not found."}, status=status.HTTP_404_NOT_FOUND)

    already_used, is_valid_for_boarding = _mark_boarded(ticket)
    if already_used:
        return _boarding_response(
            ticket,
            boarded=False,
            already_used=True,
            detail="Ticket already boarded.",
        )
    if not is_valid_for_boarding:
        return _boarding_response(
            ticket,
            boarded=False,
            already_used=False,
            valid=False,
            detail=f"Cannot board ticket in status {ticket.status} / booking {ticket.booking.status}.",
        )
    return _boarding_response(ticket, boarded=True, already_used=False)


def _generate_ticket_qr(ticket):
    """Build a signed QR code image from ticket data and return as PNG response."""
    qr_data = build_signed_qr_payload(ticket)

    img = qrcode.make(qr_data, box_size=8, border=2)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    response = HttpResponse(buffer, content_type="image/png")
    response["Content-Disposition"] = f'inline; filename="{ticket.ticket_number}.png"'
    return response


def _generate_ticket_pdf_response(ticket):
    """Build a PDF boarding pass and return as downloadable response."""
    from .pdf import generate_ticket_pdf

    buf = generate_ticket_pdf(ticket)
    response = HttpResponse(buf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{ticket.ticket_number}.pdf"'
    return response
