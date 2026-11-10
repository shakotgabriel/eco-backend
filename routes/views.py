from datetime import timedelta

from bebeto_backend.permissions import IsManagerOrAbove, IsStaffOrReadOnly
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Route, RoutePriceHistory
from .serializers import (
    RoutePriceHistorySerializer,
    RouteSerializer,
    UpdateRoutePriceSerializer,
)


def _record_route_price_history(route, old_base, old_vip, user, reason=""):
    if route.base_price == old_base and route.vip_price == old_vip:
        return False
    RoutePriceHistory.objects.create(
        route=route,
        old_price=old_base,
        new_price=route.base_price,
        old_vip_price=old_vip,
        new_vip_price=route.vip_price,
        currency=route.currency,
        changed_by=user if getattr(user, "is_authenticated", False) else None,
        reason=reason,
    )
    return True


def _apply_route_prices_to_future(route):
    from trips.models import Trip, TripSchedule

    return {
        "schedules": TripSchedule.objects.filter(route=route, is_active=True).update(
            regular_price=route.base_price,
            vip_price=route.vip_price,
        ),
        "trips": Trip.objects.filter(route=route, status="SCHEDULED").update(
            regular_price=route.base_price,
            vip_price=route.vip_price,
        ),
    }


class RouteViewSet(viewsets.ModelViewSet):
    queryset = Route.objects.select_related("origin", "destination").prefetch_related("assigned_buses").all()
    serializer_class = RouteSerializer
    permission_classes = [IsStaffOrReadOnly]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["origin", "destination", "currency", "is_active"]
    search_fields = ["origin__name", "destination__name"]
    ordering_fields = ["base_price", "distance_km"]
    ordering = ["origin__name", "destination__name"]

    def perform_update(self, serializer):
        """Auto-track price changes when route is updated via PATCH/PUT."""
        route = self.get_object()
        old_price = route.base_price
        old_vip = route.vip_price
        instance = serializer.save()
        _record_route_price_history(
            instance,
            old_price,
            old_vip,
            self.request.user,
            reason="Updated via API",
        )

    @action(detail=True, methods=["post"], url_path="update-price", permission_classes=[IsManagerOrAbove])
    def update_price(self, request, pk=None):
        """Dedicated endpoint to update standard and VIP fares. Manager+ only."""
        route = self.get_object()
        serializer = UpdateRoutePriceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_price = route.base_price
        old_vip = route.vip_price
        update_fields = ["updated_at"]

        if "base_price" in serializer.validated_data:
            route.base_price = serializer.validated_data["base_price"]
            update_fields.append("base_price")
        if "vip_price" in serializer.validated_data:
            route.vip_price = serializer.validated_data["vip_price"]
            update_fields.append("vip_price")

        if route.base_price == old_price and route.vip_price == old_vip:
            return Response({"detail": "Price unchanged."}, status=status.HTTP_400_BAD_REQUEST)

        route.save(update_fields=update_fields)
        _record_route_price_history(
            route,
            old_price,
            old_vip,
            request.user,
            reason=serializer.validated_data.get("reason", ""),
        )

        applied = {"schedules": 0, "trips": 0}
        if serializer.validated_data.get("apply_to_future", True):
            applied = _apply_route_prices_to_future(route)

        return Response({
            "detail": (
                f"Standard fare {old_price} → {route.base_price}. "
                f"VIP fare {old_vip} → {route.vip_price}."
            ),
            "applied_to": applied,
            "route": RouteSerializer(route).data,
        })

    @action(detail=True, methods=["get"], url_path="price-history")
    def price_history(self, request, pk=None):
        """View price change history for a route."""
        route = self.get_object()
        history = route.price_history.select_related("changed_by").all()
        return Response(RoutePriceHistorySerializer(history, many=True).data)

    @action(detail=True, methods=["get"], url_path="stats")
    def stats(self, request, pk=None):
        """Route performance stats: trips, revenue, occupancy, parcels."""
        route = self.get_object()
        days = int(request.query_params.get("days", 30))
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=days - 1)

        from trips.models import Trip
        trips = Trip.objects.filter(
            route=route,
            departure_time__date__gte=start_date,
            departure_time__date__lte=end_date,
        )

        trip_counts = trips.aggregate(
            total=Count("id"),
            completed=Count("id", filter=Q(status__in=["ARRIVED", "COMPLETED"])),
            cancelled=Count("id", filter=Q(status="CANCELLED")),
        )

        from bookings.models import Booking
        bookings = Booking.objects.filter(
            trip__route=route,
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
        completed_trips = trips.filter(status__in=["ARRIVED", "COMPLETED"]).select_related("bus")
        if completed_trips.exists():
            occupancies = []
            for t in completed_trips:
                if t.bus and t.bus.capacity > 0:
                    booked = t.bus.capacity - t.available_seats
                    occupancies.append(round(booked / t.bus.capacity * 100, 1))
            if occupancies:
                avg_occupancy = round(sum(occupancies) / len(occupancies), 1)

        # Parcel stats
        from parcels.models import Parcel
        parcel_stats = Parcel.objects.filter(
            trip__route=route,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        ).aggregate(
            count=Count("id"),
            revenue=Sum("shipping_fee"),
        )

        return Response({
            "route": str(route),
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
            "currency": route.currency,
        })
