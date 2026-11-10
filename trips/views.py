import hashlib
from datetime import timedelta

from bebeto_backend.permissions import IsStaffOrAbove, IsStaffOrReadOnly
from django.core.cache import cache
from django.db.models import Count, Q, Sum
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from seats.models import Seat, SeatLock
from seats.serializers import SeatLockSerializer, SeatMapSerializer

from .filters import TripFilter
from bebeto_backend.permissions import IsManagerOrAbove
from .models import Trip, TripSchedule
from .serializers import TripListSerializer, TripScheduleSerializer, TripSerializer


class TripViewSet(viewsets.ModelViewSet):
    permission_classes = [IsStaffOrReadOnly]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = TripFilter
    search_fields = ["route__origin__name", "route__destination__name", "bus__name", "bus__plate_number"]
    ordering_fields = ["departure_time", "arrival_time", "regular_price", "available_seats"]
    ordering = ["departure_time"]

    def get_queryset(self):
        qs = Trip.objects.select_related(
            "route__origin", "route__destination", "bus__operator", "driver"
        ).all()
        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return TripListSerializer
        return TripSerializer

    def list(self, request, *args, **kwargs):
        """Trip search with 30-second cache keyed on query params."""
        query_string = request.META.get("QUERY_STRING", "")
        cache_key = f"trip_search:{hashlib.md5(query_string.encode()).hexdigest()}"
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)
        response = super().list(request, *args, **kwargs)
        cache.set(cache_key, response.data, timeout=30)
        return response

    @action(detail=False, methods=["get"], permission_classes=[IsStaffOrAbove], url_path="manifests")
    def manifests(self, request):
        """Server-side passenger manifests; ticket rows are the inventory source."""
        from tickets.models import Ticket

        date = request.query_params.get("departure_date")
        trips = self.get_queryset()
        if date:
            trips = trips.filter(departure_time__date=date)
        trips = trips.select_related("route__origin", "route__destination", "bus", "driver").order_by("departure_time")
        ticket_rows = Ticket.objects.filter(
            booking__trip__in=trips,
            booking__status__in=["PENDING", "CONFIRMED", "COMPLETED"],
        ).select_related("booking", "seat").order_by("seat__row", "seat__column")
        by_trip = {}
        for ticket in ticket_rows:
            by_trip.setdefault(ticket.booking.trip_id, []).append({
                "ticket_number": ticket.ticket_number,
                "seat": ticket.seat.seat_number if ticket.seat else "Open",
                "name": f"{ticket.first_name} {ticket.last_name}".strip(),
                "phone": str(ticket.phone_number),
                "ticket_status": ticket.status,
                "booking_status": ticket.booking.status,
            })
        return Response({"results": [{
            "id": trip.id,
            "route": str(trip.route),
            "departure_time": trip.departure_time,
            "bus": str(trip.bus),
            "plate_number": trip.bus.plate_number,
            "driver": str(trip.driver) if trip.driver else "Unassigned",
            "capacity": trip.bus.capacity,
            "status": trip.status,
            "passengers": by_trip.get(trip.id, []),
        } for trip in trips]})

    @action(detail=True, methods=["get"], permission_classes=[AllowAny], url_path="seat-map")
    def seat_map(self, request, pk=None):
        """Return the seat map for this trip with availability statuses."""
        trip = self.get_object()
        seats = Seat.objects.filter(bus=trip.bus, is_active=True).order_by("row", "column")

        # IDs of seats already booked on this trip
        booked_seat_ids = set(
            trip.bookings.exclude(status="CANCELLED")
            .values_list("tickets__seat_id", flat=True)
            .exclude(tickets__seat_id=None)
        )

        # IDs of seats currently locked (not expired)
        now = timezone.now()
        SeatLock.objects.filter(trip=trip, expires_at__lte=now).delete()
        locked_seat_ids = set(
            SeatLock.objects.filter(trip=trip).values_list("seat_id", flat=True)
        )

        serializer = SeatMapSerializer(
            seats, many=True,
            context={"booked_seat_ids": booked_seat_ids, "locked_seat_ids": locked_seat_ids}
        )

        layout = trip.bus.seat_layout
        left, right = layout.split("+")
        return Response({
            "trip_id": trip.id,
            "bus": str(trip.bus),
            "layout": layout,
            "columns_left": int(left),
            "columns_right": int(right),
            "total_rows": trip.bus.total_rows,
            "seats": serializer.data,
        })

    @action(detail=True, methods=["post"], permission_classes=[AllowAny], url_path="lock-seats")
    def lock_seats(self, request, pk=None):
        """Temporarily lock seats for 5 minutes while the customer completes booking."""
        import uuid as _uuid

        trip = self.get_object()
        serializer = SeatLockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        seat_ids = serializer.validated_data["seat_ids"]
        # Generate secure session key: prefer authenticated user ID, else generate UUID
        provided_key = serializer.validated_data.get("session_key", "")
        if request.user and request.user.is_authenticated:
            session_key = f"user-{request.user.id}"
        elif provided_key:
            session_key = provided_key
        else:
            session_key = f"anon-{_uuid.uuid4().hex}"
        now = timezone.now()
        expires_at = now + timedelta(minutes=5)

        # Clean expired locks
        SeatLock.objects.filter(trip=trip, expires_at__lte=now).delete()

        # Check seats belong to this bus
        bus_seat_ids = set(Seat.objects.filter(bus=trip.bus, id__in=seat_ids).values_list("id", flat=True))
        invalid = set(seat_ids) - bus_seat_ids
        if invalid:
            try:
                from payments.metrics import increment_counter
                increment_counter("seat_lock_invalid_request")
            except Exception:
                pass
            return Response({"detail": f"Seats {invalid} do not belong to this bus."}, status=status.HTTP_400_BAD_REQUEST)

        # Check not already booked
        booked = set(
            trip.bookings.exclude(status="CANCELLED")
            .values_list("tickets__seat_id", flat=True)
        ) & set(seat_ids)
        if booked:
            try:
                from payments.metrics import increment_counter
                increment_counter("seat_lock_conflicts")
            except Exception:
                pass
            return Response({"detail": f"Seats already booked: {booked}"}, status=status.HTTP_409_CONFLICT)

        # Check not locked by someone else
        existing_locks = SeatLock.objects.filter(trip=trip, seat_id__in=seat_ids).exclude(session_key=session_key)
        if existing_locks.exists():
            locked = set(existing_locks.values_list("seat_id", flat=True))
            try:
                from payments.metrics import increment_counter
                increment_counter("seat_lock_conflicts")
            except Exception:
                pass
            return Response({"detail": f"Seats currently held by another user: {locked}"}, status=status.HTTP_409_CONFLICT)

        # Upsert locks
        created = []
        for sid in seat_ids:
            SeatLock.objects.update_or_create(
                seat_id=sid, trip=trip,
                defaults={"session_key": session_key, "expires_at": expires_at},
            )
            created.append(sid)

        return Response({"locked_seats": created, "session_key": session_key, "expires_at": expires_at.isoformat()}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], permission_classes=[AllowAny], url_path="unlock-seats")
    def unlock_seats(self, request, pk=None):
        """Release seat locks for a session."""
        trip = self.get_object()
        session_key = request.data.get("session_key", "")
        if not session_key:
            return Response({"detail": "session_key is required."}, status=status.HTTP_400_BAD_REQUEST)
        count, _ = SeatLock.objects.filter(trip=trip, session_key=session_key).delete()
        return Response({"released": count})


    @action(detail=True, methods=["post"], url_path="update-prices", permission_classes=[IsManagerOrAbove])
    def update_prices(self, request, pk=None):
        """Update trip prices with audit logging. Manager+ only."""
        trip = self.get_object()
        changed = []

        for field in ("regular_price", "vip_price", "cargo_price_per_kg"):
            if field in request.data:
                from decimal import Decimal
                old_val = getattr(trip, field)
                new_val = Decimal(str(request.data[field]))
                if old_val != new_val:
                    changed.append(f"{field}: {old_val} → {new_val}")
                    setattr(trip, field, new_val)

        if not changed:
            return Response({"detail": "No price changes."}, status=status.HTTP_400_BAD_REQUEST)

        trip.save(update_fields=[f for f in ("regular_price", "vip_price", "cargo_price_per_kg", "updated_at") if f in request.data or f == "updated_at"])

        # Audit log
        try:
            from reports.audit import log_action
            log_action(
                actor=request.user,
                action="TRIP_PRICE_UPDATED",
                target_model="Trip",
                target_id=trip.id,
                details=f"Price updated: {'; '.join(changed)}. Reason: {request.data.get('reason', '')}",
            )
        except Exception:
            pass

        return Response({
            "detail": f"Prices updated: {'; '.join(changed)}",
            "trip": TripSerializer(trip).data,
        })

    @action(detail=True, methods=["get"], url_path="stats")
    def trip_stats(self, request, pk=None):
        """Stats for a single trip: bookings, revenue, occupancy, parcels."""
        trip = self.get_object()

        from bookings.models import Booking
        bookings = Booking.objects.filter(
            trip=trip,
            status__in=["CONFIRMED", "COMPLETED", "RESCHEDULED"],
        )
        booking_stats = bookings.aggregate(
            total=Count("id"),
            seats=Sum("number_of_seats"),
            revenue=Sum("total_price"),
        )

        occupancy = None
        if trip.bus and trip.bus.capacity > 0:
            booked = trip.bus.capacity - trip.available_seats
            occupancy = round(booked / trip.bus.capacity * 100, 1)

        from parcels.models import Parcel
        parcel_stats = Parcel.objects.filter(trip=trip).aggregate(
            count=Count("id"), revenue=Sum("shipping_fee")
        )

        return Response({
            "trip_id": trip.id,
            "route": str(trip.route),
            "bus": str(trip.bus),
            "status": trip.status,
            "bookings": {
                "total": booking_stats["total"] or 0,
                "seats_sold": booking_stats["seats"] or 0,
                "revenue": str(booking_stats["revenue"] or 0),
            },
            "occupancy_pct": occupancy,
            "available_seats": trip.available_seats,
            "parcels": {
                "count": parcel_stats["count"] or 0,
                "revenue": str(parcel_stats["revenue"] or 0),
            },
        })


class TripScheduleViewSet(viewsets.ModelViewSet):
    """Manage recurring trip timetable entries. Managers+ only."""
    queryset = TripSchedule.objects.select_related(
        "route__origin", "route__destination", "bus"
    ).all()
    serializer_class = TripScheduleSerializer
    permission_classes = [IsManagerOrAbove]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["route", "bus", "is_active"]
    search_fields = ["route__origin__name", "route__destination__name", "bus__name"]
    ordering = ["route", "departure_time"]

    @action(detail=False, methods=["post"], url_path="generate-now")
    def generate_now(self, request):
        """Generate or preview trips from active schedules (manager action)."""
        from .tasks import generate_scheduled_trips

        raw = request.data.get("days_ahead", 7)
        try:
            days_ahead = int(raw)
        except (TypeError, ValueError):
            return Response(
                {"detail": "days_ahead must be an integer between 1 and 60."},
                status=400,
            )
        if days_ahead < 1 or days_ahead > 60:
            return Response(
                {"detail": "days_ahead must be an integer between 1 and 60."},
                status=400,
            )

        dry_run = str(request.data.get("dry_run", "")).lower() in ("1", "true", "yes")

        def _optional_int(key):
            val = request.data.get(key)
            if val in (None, ""):
                return None
            try:
                return int(val)
            except (TypeError, ValueError):
                raise ValueError(key)

        try:
            route_id = _optional_int("route_id")
            origin_city_id = _optional_int("origin_city_id")
            destination_city_id = _optional_int("destination_city_id")
        except ValueError as exc:
            return Response(
                {"detail": f"{exc.args[0]} must be an integer."},
                status=400,
            )

        schedule_ids = request.data.get("schedule_ids")
        if schedule_ids is not None and not isinstance(schedule_ids, list):
            return Response({"detail": "schedule_ids must be a list of ids."}, status=400)

        result = generate_scheduled_trips.run(
            days_ahead=days_ahead,
            dry_run=dry_run,
            route_id=route_id,
            origin_city_id=origin_city_id,
            destination_city_id=destination_city_id,
            schedule_ids=schedule_ids,
        )
        detail = (
            "Preview only — no trips created."
            if dry_run
            else "Trip generation complete."
        )
        if result.get("schedules_considered", 0) == 0:
            detail = "No active schedules match the selected filters."
        return Response({"detail": detail, **result})
