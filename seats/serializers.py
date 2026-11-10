from rest_framework import serializers

from .models import Seat, SeatLock


class SeatSerializer(serializers.ModelSerializer):
    bus_name = serializers.CharField(source="bus.__str__", read_only=True)

    class Meta:
        model = Seat
        fields = [
            "id", "bus", "bus_name", "seat_number", "row", "column",
            "position", "seat_type", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class SeatMapSerializer(serializers.ModelSerializer):
    """Used in trip seat-map: includes availability status."""
    status = serializers.SerializerMethodField()

    class Meta:
        model = Seat
        fields = ["id", "seat_number", "row", "column", "position", "seat_type", "status"]

    def get_status(self, obj):
        booked_ids = self.context.get("booked_seat_ids", set())
        locked_ids = self.context.get("locked_seat_ids", set())
        if not obj.is_active:
            return "UNAVAILABLE"
        if obj.id in booked_ids:
            return "BOOKED"
        if obj.id in locked_ids:
            return "LOCKED"
        return "AVAILABLE"


class SeatLockSerializer(serializers.Serializer):
    seat_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1, max_length=10)
    session_key = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        help_text="Optional. Auto-generated from user ID if authenticated.",
    )
