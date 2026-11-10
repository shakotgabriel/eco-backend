from datetime import timedelta
from decimal import Decimal

from bebeto_backend.permissions import IsManagerOrAbove, IsOperatorOwnerOrStaff, IsStaffOrAbove
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status as http_status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Bus, MaintenanceRecord
from .serializers import BusSerializer, MaintenanceRecordSerializer


class BusViewSet(viewsets.ModelViewSet):
    serializer_class = BusSerializer
    permission_classes = [IsOperatorOwnerOrStaff]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["bus_type", "status", "home_city", "operator"]
    search_fields = ["plate_number", "name"]
    ordering_fields = ["name", "capacity"]
    ordering = ["name"]

    def get_queryset(self):
        return (
            Bus.objects.select_related("home_city", "operator")
            .prefetch_related("assigned_routes__origin", "assigned_routes__destination")
            .all()
        )

    @action(detail=True, methods=["get"], url_path="profile")
    def profile(self, request, pk=None):
        """Full bus profile: details, trip history, revenue, parcel stats."""
        bus = self.get_object()
        days = int(request.query_params.get("days", 30))
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=days - 1)

        from trips.models import Trip
        trips = Trip.objects.filter(
            bus=bus,
            departure_time__date__gte=start_date,
            departure_time__date__lte=end_date,
        ).select_related("route__origin", "route__destination")

        trip_counts = trips.aggregate(
            total=Count("id"),
            completed=Count("id", filter=Q(status__in=["ARRIVED", "COMPLETED"])),
            cancelled=Count("id", filter=Q(status="CANCELLED")),
            scheduled=Count("id", filter=Q(status="SCHEDULED")),
        )

        # Revenue from bookings on this bus
        from bookings.models import Booking
        bookings = Booking.objects.filter(
            trip__bus=bus,
            trip__departure_time__date__gte=start_date,
            trip__departure_time__date__lte=end_date,
            status__in=["CONFIRMED", "COMPLETED", "RESCHEDULED"],
        )
        booking_stats = bookings.aggregate(
            total_bookings=Count("id"),
            total_seats=Sum("number_of_seats"),
            total_revenue=Sum("total_price"),
        )

        # Avg occupancy
        avg_occupancy = None
        completed_trips = trips.filter(status__in=["ARRIVED", "COMPLETED"])
        if completed_trips.exists() and bus.capacity > 0:
            occupancies = []
            for t in completed_trips:
                booked = bus.capacity - t.available_seats
                occupancies.append(round(booked / bus.capacity * 100, 1))
            if occupancies:
                avg_occupancy = round(sum(occupancies) / len(occupancies), 1)

        # Parcel stats
        from parcels.models import Parcel
        parcel_stats = Parcel.objects.filter(
            trip__bus=bus,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        ).aggregate(count=Count("id"), revenue=Sum("shipping_fee"))

        # Routes served
        routes_served = (
            trips.values("route__origin__name", "route__destination__name")
            .annotate(trip_count=Count("id"))
            .order_by("-trip_count")[:5]
        )

        # Recent trips (last 10)
        recent_trips = trips.order_by("-departure_time")[:10]

        # Maintenance summary
        now = timezone.now().date()
        maintenance_summary = {
            "total_records": bus.maintenance_records.count(),
            "overdue": bus.maintenance_records.filter(
                next_service_date__lt=now, next_service_date__isnull=False,
            ).count(),
            "scheduled": bus.maintenance_records.filter(status="SCHEDULED").count(),
            "is_overdue_for_service": bus.is_overdue_for_service,
            "next_service_due": bus.next_service_due,
            "total_maintenance_cost": str(
                bus.maintenance_records.filter(status="COMPLETED").aggregate(
                    t=Sum("cost")
                )["t"] or 0
            ),
        }

        return Response({
            "bus": BusSerializer(bus).data,
            "period": f"{days}d",
            "trips": trip_counts,
            "bookings": {
                "total": booking_stats["total_bookings"] or 0,
                "seats_sold": booking_stats["total_seats"] or 0,
                "revenue": str(booking_stats["total_revenue"] or 0),
            },
            "avg_occupancy_pct": avg_occupancy,
            "parcels": {
                "count": parcel_stats["count"] or 0,
                "revenue": str(parcel_stats["revenue"] or 0),
            },
            "maintenance": maintenance_summary,
            "routes_served": [
                {
                    "route": f"{r['route__origin__name']} → {r['route__destination__name']}",
                    "trip_count": r["trip_count"],
                }
                for r in routes_served
            ],
            "recent_trips": [
                {
                    "trip_id": t.id,
                    "route": str(t.route),
                    "departure": t.departure_time.isoformat(),
                    "status": t.status,
                    "available_seats": t.available_seats,
                }
                for t in recent_trips
            ],
        })

    @action(detail=True, methods=["get", "put"], url_path="seat-map", permission_classes=[IsOperatorOwnerOrStaff])
    def seat_map(self, request, pk=None):
        """Visual floorplan for a bus: VIP vs standard assignment."""
        bus = self.get_object()
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .seat_sync import apply_explicit_seat_plan
        from .serializers import BusSeatMapWriteSerializer

        if request.method == "PUT":
            serializer = BusSeatMapWriteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            try:
                result = apply_explicit_seat_plan(
                    bus,
                    serializer.validated_data["seat_layout"],
                    serializer.validated_data["seats"],
                )
            except DjangoValidationError as exc:
                messages = exc.messages if hasattr(exc, "messages") else [str(exc)]
                return Response({"detail": " ".join(messages)}, status=http_status.HTTP_400_BAD_REQUEST)
            bus.refresh_from_db()
            payload = self._seat_map_payload(bus)
            payload["applied"] = result
            return Response(payload)

        return Response(self._seat_map_payload(bus))

    def _seat_map_payload(self, bus):
        from seats.models import Seat
        from .seat_sync import column_letters, parse_layout, protected_seat_ids

        left, right = parse_layout(bus.seat_layout or "2+2")
        columns = column_letters(bus.seat_layout or "2+2")
        protected = protected_seat_ids(bus)
        seats = Seat.objects.filter(bus=bus, column__in=columns).order_by("row", "column")
        return {
            "bus_id": bus.id,
            "seat_layout": bus.seat_layout,
            "columns_left": left,
            "columns_right": right,
            "total_rows": bus.total_rows,
            "vip_seats": bus.vip_seats,
            "regular_seats": bus.regular_seats,
            "capacity": bus.capacity,
            "seats": [
                {
                    "id": seat.id,
                    "seat_number": seat.seat_number,
                    "row": seat.row,
                    "column": seat.column,
                    "position": seat.position,
                    "seat_type": seat.seat_type,
                    "is_active": seat.is_active,
                    "protected": seat.id in protected,
                }
                for seat in seats
            ],
        }

    @action(detail=True, methods=["get"], url_path="trip-history")
    def trip_history(self, request, pk=None):
        """Paginated trip history for a bus."""
        bus = self.get_object()
        from trips.models import Trip
        trips = (
            Trip.objects.filter(bus=bus)
            .select_related("route__origin", "route__destination", "driver")
            .order_by("-departure_time")[:50]
        )
        return Response([
            {
                "trip_id": t.id,
                "route": str(t.route),
                "driver": t.driver.get_full_name() if t.driver else None,
                "departure": t.departure_time.isoformat(),
                "arrival": t.arrival_time.isoformat() if t.arrival_time else None,
                "status": t.status,
                "available_seats": t.available_seats,
            }
            for t in trips
        ])

    @action(detail=True, methods=["get"], url_path="maintenance")
    def maintenance_history(self, request, pk=None):
        """Maintenance history for a bus with summary stats."""
        bus = self.get_object()
        records = bus.maintenance_records.select_related("approved_by").all()

        # Filter by status if provided
        record_status = request.query_params.get("status")
        if record_status:
            records = records.filter(status=record_status.upper())

        now = timezone.now().date()
        overdue = bus.maintenance_records.filter(
            next_service_date__lt=now, next_service_date__isnull=False,
        )

        total_cost = records.filter(status="COMPLETED").aggregate(
            total=Sum("cost")
        )["total"] or Decimal("0")

        return Response({
            "bus": str(bus),
            "current_mileage_km": bus.current_mileage_km,
            "is_overdue_for_service": bus.is_overdue_for_service,
            "next_service_due": bus.next_service_due,
            "summary": {
                "total_records": records.count(),
                "completed": records.filter(status="COMPLETED").count(),
                "scheduled": records.filter(status="SCHEDULED").count(),
                "overdue_count": overdue.count(),
                "total_cost": str(total_cost),
            },
            "overdue_items": MaintenanceRecordSerializer(overdue, many=True).data,
            "records": MaintenanceRecordSerializer(records[:30], many=True).data,
        })

    @action(detail=True, methods=["get"], url_path="overdue")
    def overdue_services(self, request, pk=None):
        """List overdue maintenance items for this bus."""
        bus = self.get_object()
        now = timezone.now().date()
        overdue_by_date = bus.maintenance_records.filter(
            next_service_date__lt=now, next_service_date__isnull=False,
        )
        overdue_by_mileage = bus.maintenance_records.filter(
            next_service_mileage__isnull=False,
            next_service_mileage__lte=bus.current_mileage_km,
        ) if bus.current_mileage_km else MaintenanceRecord.objects.none()

        combined = (overdue_by_date | overdue_by_mileage).distinct()
        return Response(MaintenanceRecordSerializer(combined, many=True).data)


class MaintenanceRecordViewSet(viewsets.ModelViewSet):
    """Full CRUD for maintenance records. Staff+ can view, Manager+ can create/edit/delete."""
    queryset = MaintenanceRecord.objects.select_related("bus", "approved_by").all()
    serializer_class = MaintenanceRecordSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["bus", "maintenance_type", "status"]
    search_fields = ["bus__plate_number", "bus__name", "vendor", "performed_by", "description"]
    ordering = ["-scheduled_date"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsStaffOrAbove()]
        return [IsManagerOrAbove()]

    def get_queryset(self):
        return super().get_queryset()

    def perform_update(self, serializer):
        """Auto-set completed_date when status changes to COMPLETED."""
        instance = serializer.save()
        if instance.status == "COMPLETED" and not instance.completed_date:
            instance.completed_date = timezone.now().date()
            instance.save(update_fields=["completed_date", "updated_at"])
        # Update bus mileage if mileage_at_service is provided and higher
        if instance.mileage_at_service and instance.mileage_at_service > instance.bus.current_mileage_km:
            instance.bus.current_mileage_km = instance.mileage_at_service
            instance.bus.save(update_fields=["current_mileage_km", "updated_at"])

    @action(detail=False, methods=["get"], url_path="overdue")
    def all_overdue(self, request):
        """List all overdue maintenance across all buses."""
        now = timezone.now().date()
        overdue_by_date = self.get_queryset().filter(
            next_service_date__lt=now, next_service_date__isnull=False,
        )
        return Response({
            "count": overdue_by_date.count(),
            "records": MaintenanceRecordSerializer(overdue_by_date, many=True).data,
        })

    @action(detail=False, methods=["get"], url_path="upcoming")
    def upcoming(self, request):
        """List maintenance scheduled in the next 7 days."""
        now = timezone.now().date()
        end = now + timedelta(days=7)
        upcoming = self.get_queryset().filter(
            scheduled_date__gte=now,
            scheduled_date__lte=end,
            status="SCHEDULED",
        )
        return Response(MaintenanceRecordSerializer(upcoming, many=True).data)

    @action(detail=False, methods=["get"], url_path="cost-summary")
    def cost_summary(self, request):
        """Total maintenance cost by bus and type for a period."""
        days = int(request.query_params.get("days", 90))
        start_date = timezone.now().date() - timedelta(days=days)

        by_bus = (
            self.get_queryset()
            .filter(status="COMPLETED", completed_date__gte=start_date)
            .values("bus__plate_number", "bus__name")
            .annotate(total_cost=Sum("cost"), record_count=Count("id"))
            .order_by("-total_cost")
        )

        by_type = (
            self.get_queryset()
            .filter(status="COMPLETED", completed_date__gte=start_date)
            .values("maintenance_type")
            .annotate(total_cost=Sum("cost"), record_count=Count("id"))
            .order_by("-total_cost")
        )

        total = self.get_queryset().filter(
            status="COMPLETED", completed_date__gte=start_date
        ).aggregate(total=Sum("cost"))["total"] or Decimal("0")

        return Response({
            "period": f"{days}d",
            "total_cost": str(total),
            "by_bus": [
                {
                    "bus": f"{r['bus__name'] or r['bus__plate_number']}",
                    "total_cost": str(r["total_cost"] or 0),
                    "record_count": r["record_count"],
                }
                for r in by_bus
            ],
            "by_type": [
                {
                    "type": r["maintenance_type"],
                    "total_cost": str(r["total_cost"] or 0),
                    "record_count": r["record_count"],
                }
                for r in by_type
            ],
        })
