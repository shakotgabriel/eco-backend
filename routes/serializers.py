from rest_framework import serializers

from buses.models import Bus

from .models import Route, RoutePriceHistory


class RoutePriceHistorySerializer(serializers.ModelSerializer):
    changed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = RoutePriceHistory
        fields = [
            "id", "old_price", "new_price",
            "old_vip_price", "new_vip_price", "currency",
            "changed_by", "changed_by_name", "reason", "created_at",
        ]

    def get_changed_by_name(self, obj):
        if obj.changed_by:
            return obj.changed_by.get_full_name() or obj.changed_by.username
        return None


class RouteSerializer(serializers.ModelSerializer):
    origin_name = serializers.CharField(source="origin.name", read_only=True)
    destination_name = serializers.CharField(source="destination.name", read_only=True)
    assigned_bus_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Bus.objects.exclude(status="RETIRED"),
        source="assigned_buses",
        required=False,
    )
    assigned_buses = serializers.SerializerMethodField()

    class Meta:
        model = Route
        fields = [
            "id", "origin", "origin_name", "destination", "destination_name",
            "distance_km", "estimated_duration", "base_price", "vip_price", "currency",
            "allowed_bus_types", "assigned_bus_ids", "assigned_buses",
            "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_assigned_buses(self, obj):
        return [
            {
                "id": bus.id,
                "name": bus.name,
                "plate_number": bus.plate_number,
                "bus_type": bus.bus_type,
                "bus_type_display": bus.get_bus_type_display(),
                "status": bus.status,
                "seat_layout": bus.seat_layout,
                "vip_seats": bus.vip_seats,
                "regular_seats": bus.regular_seats,
                "capacity": bus.capacity,
            }
            for bus in obj.assigned_buses.all()
        ]

    def validate(self, attrs):
        allowed = attrs.get(
            "allowed_bus_types",
            getattr(self.instance, "allowed_bus_types", None) or [],
        )
        buses = attrs.get("assigned_buses")
        if allowed and buses is not None:
            mismatched = [bus for bus in buses if bus.bus_type not in allowed]
            if mismatched:
                labels = ", ".join(bus.plate_number for bus in mismatched)
                raise serializers.ValidationError({
                    "assigned_bus_ids": (
                        f"These buses are not an allowed type on this route: {labels}. "
                        "Add their type under allowed bus types, or pick a matching bus."
                    )
                })
        return attrs


class UpdateRoutePriceSerializer(serializers.Serializer):
    base_price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    vip_price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    apply_to_future = serializers.BooleanField(required=False, default=True)
    reason = serializers.CharField(max_length=300, required=False, default="")

    def validate(self, attrs):
        if "base_price" not in attrs and "vip_price" not in attrs:
            raise serializers.ValidationError("Provide a standard price, a VIP price, or both.")
        return attrs
