from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import Trip, TripSchedule


def _ensure_route_allows_bus(route, bus):
    if not route or not bus:
        return
    try:
        route.validate_bus(bus)
    except DjangoValidationError as exc:
        message = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
        raise serializers.ValidationError({"bus": message})


def _ensure_driver(value):
    if value and getattr(value, "role", None) != "DRIVER":
        raise serializers.ValidationError("Only DRIVER accounts can be assigned as trip drivers.")
    return value


class TripSerializer(serializers.ModelSerializer):
    route_display = serializers.CharField(source="route.__str__", read_only=True)
    bus_display = serializers.CharField(source="bus.__str__", read_only=True)
    driver_name = serializers.SerializerMethodField()

    class Meta:
        model = Trip
        fields = [
            "id", "route", "route_display", "bus", "bus_display",
            "driver", "driver_name",
            "departure_time", "arrival_time",
            "regular_price", "vip_price", "cargo_price_per_kg",
            "status", "available_seats", "created_at", "updated_at",
        ]

        read_only_fields = ["id", "created_at", "updated_at"]

    def get_driver_name(self, obj):
        return obj.driver.get_full_name() if obj.driver else None

    def validate_driver(self, value):
        return _ensure_driver(value)

    def validate(self, attrs):
        # Status-only updates must not re-validate bus/route assignment.
        # Otherwise a trip cannot be marked arrived if the route's allowed
        # bus types changed after the trip was created.
        if self.instance is not None and set(attrs.keys()) <= {"status"}:
            return attrs
        route = attrs.get("route", getattr(self.instance, "route", None))
        bus = attrs.get("bus", getattr(self.instance, "bus", None))
        _ensure_route_allows_bus(route, bus)
        return attrs


class TripListSerializer(serializers.ModelSerializer):
    """Lighter serializer for public trip search results."""
    origin = serializers.CharField(source="route.origin.name", read_only=True)
    destination = serializers.CharField(source="route.destination.name", read_only=True)
    route_display = serializers.CharField(source="route.__str__", read_only=True)
    bus_name = serializers.CharField(source="bus.__str__", read_only=True)
    bus_type = serializers.CharField(source="bus.bus_type", read_only=True)
    bus_type_display = serializers.CharField(source="bus.get_bus_type_display", read_only=True)
    bus_plate = serializers.CharField(source="bus.plate_number", read_only=True)
    bus_capacity = serializers.IntegerField(source="bus.capacity", read_only=True)
    seat_layout = serializers.CharField(source="bus.seat_layout", read_only=True)
    driver_name = serializers.SerializerMethodField()
    currency = serializers.CharField(source="route.currency", read_only=True)
    duration = serializers.SerializerMethodField()

    class Meta:
        model = Trip
        fields = [
            "id", "route", "bus", "driver",
            "origin", "destination", "route_display",
            "bus_name", "bus_type", "bus_type_display", "bus_plate", "bus_capacity", "seat_layout",
            "driver_name",
            "departure_time", "arrival_time", "duration",
            "regular_price", "vip_price", "cargo_price_per_kg",
            "currency", "status", "available_seats",
        ]

    def get_driver_name(self, obj):
        return obj.driver.get_full_name() if obj.driver else None

    def get_duration(self, obj):
        if obj.arrival_time and obj.departure_time:
            delta = obj.arrival_time - obj.departure_time
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes = remainder // 60
            return f"{hours}h {minutes}m"
        return None


class TripScheduleSerializer(serializers.ModelSerializer):
    route_display = serializers.CharField(source="route.__str__", read_only=True)
    bus_display = serializers.CharField(source="bus.__str__", read_only=True)
    driver_name = serializers.SerializerMethodField()
    days_display = serializers.SerializerMethodField()

    class Meta:
        model = TripSchedule
        fields = [
            "id", "route", "route_display", "bus", "bus_display",
            "driver", "driver_name",
            "departure_time", "days_of_week", "days_display",
            "regular_price", "vip_price", "cargo_price_per_kg",
            "is_active", "created_at", "updated_at",
        ]

        read_only_fields = ["id", "created_at", "updated_at"]

    def get_driver_name(self, obj):
        return obj.driver.get_full_name() if obj.driver else None

    def validate_driver(self, value):
        return _ensure_driver(value)

    def get_days_display(self, obj):
        day_names = dict(TripSchedule.DAY_CHOICES)
        return [day_names.get(d, "?") for d in (obj.days_of_week or [])]

    def validate(self, attrs):
        route = attrs.get("route", getattr(self.instance, "route", None))
        bus = attrs.get("bus", getattr(self.instance, "bus", None))
        _ensure_route_allows_bus(route, bus)
        return attrs
