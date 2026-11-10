from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import Bus, MaintenanceRecord
from .seat_sync import parse_layout, sync_bus_seats


class BusSerializer(serializers.ModelSerializer):
    home_city_name = serializers.CharField(source="home_city.name", read_only=True)
    operator_name = serializers.SerializerMethodField()
    is_overdue_for_service = serializers.BooleanField(read_only=True)
    next_service_due = serializers.DateField(read_only=True)
    assigned_routes = serializers.SerializerMethodField()

    class Meta:
        model = Bus
        fields = [
            "id", "operator", "operator_name",
            "plate_number", "name", "home_city", "home_city_name",
            "capacity", "bus_type", "seat_layout", "total_rows",
            "vip_seats", "regular_seats", "has_cargo_section",
            "current_mileage_km",
            "insurance_expiry", "road_licence_expiry", "inspection_expiry",
            "status", "is_overdue_for_service", "next_service_due",
            "assigned_routes",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "is_overdue_for_service", "next_service_due", "created_at", "updated_at"]

    def get_operator_name(self, obj):
        return obj.operator.get_full_name() or obj.operator.username if obj.operator else None

    def validate_operator(self, value):
        if value and getattr(value, "role", None) != "OPERATOR":
            raise serializers.ValidationError("Only OPERATOR accounts can be assigned as bus operators.")
        return value

    def get_assigned_routes(self, obj):
        return [
            {
                "id": route.id,
                "origin_name": route.origin.name,
                "destination_name": route.destination.name,
            }
            for route in obj.assigned_routes.all()
        ]

    def validate(self, attrs):
        instance = self.instance
        vip = attrs.get("vip_seats", getattr(instance, "vip_seats", 0) or 0)
        regular = attrs.get("regular_seats", getattr(instance, "regular_seats", 0) or 0)
        if vip < 0 or regular < 0:
            raise serializers.ValidationError("VIP and regular seat counts cannot be negative.")
        if "vip_seats" in attrs or "regular_seats" in attrs or "capacity" not in attrs:
            attrs["capacity"] = vip + regular
        elif attrs.get("capacity") is not None and attrs["capacity"] != vip + regular:
            attrs["capacity"] = vip + regular

        layout = attrs.get("seat_layout", getattr(instance, "seat_layout", "2+2"))
        try:
            left, right = parse_layout(str(layout))
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"seat_layout": exc.messages if hasattr(exc, "messages") else [str(exc)]})
        cols_per_row = left + right
        total = vip + regular
        if total and cols_per_row:
            attrs["total_rows"] = max(1, (total + cols_per_row - 1) // cols_per_row)
        return attrs

    def validate_seat_layout(self, value):
        try:
            parse_layout(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages if hasattr(exc, "messages") else [str(exc)])
        return str(value).strip()

    def create(self, validated_data):
        bus = super().create(validated_data)
        self._sync_seats(bus)
        return bus

    def update(self, instance, validated_data):
        seat_fields = {"vip_seats", "regular_seats", "seat_layout", "capacity"}
        needs_sync = bool(seat_fields & set(validated_data.keys())) or instance.seats.count() == 0
        bus = super().update(instance, validated_data)
        if needs_sync:
            self._sync_seats(bus)
        return bus

    def _sync_seats(self, bus):
        try:
            sync_bus_seats(bus)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"seats": exc.messages if hasattr(exc, "messages") else [str(exc)]})


class BusSeatCellSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False, allow_null=True)
    seat_number = serializers.CharField(max_length=5)
    row = serializers.IntegerField(min_value=1)
    column = serializers.CharField(max_length=1)
    seat_type = serializers.ChoiceField(choices=["VIP", "REGULAR"])
    is_active = serializers.BooleanField(required=False, default=True)


class BusSeatMapWriteSerializer(serializers.Serializer):
    seat_layout = serializers.RegexField(regex=r"^[1-4]\+[1-4]$", max_length=5)
    seats = BusSeatCellSerializer(many=True)

    def validate_seats(self, seats):
        if not seats:
            raise serializers.ValidationError("Add at least one seat.")
        if len(seats) > 80:
            raise serializers.ValidationError("A bus cannot have more than 80 seats.")
        return seats


class MaintenanceRecordSerializer(serializers.ModelSerializer):
    bus_display = serializers.CharField(source="bus.__str__", read_only=True)
    maintenance_type_display = serializers.CharField(source="get_maintenance_type_display", read_only=True)
    approved_by_name = serializers.SerializerMethodField()
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = MaintenanceRecord
        fields = [
            "id", "bus", "bus_display",
            "maintenance_type", "maintenance_type_display", "status",
            "description",
            "scheduled_date", "completed_date",
            "cost", "currency", "vendor",
            "mileage_at_service",
            "next_service_date", "next_service_mileage",
            "performed_by", "approved_by", "approved_by_name",
            "notes", "is_overdue",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_approved_by_name(self, obj):
        if obj.approved_by:
            return obj.approved_by.get_full_name() or obj.approved_by.username
        return None

    def get_is_overdue(self, obj):
        if obj.next_service_date:
            from django.utils import timezone
            return obj.next_service_date < timezone.now().date()
        return False
