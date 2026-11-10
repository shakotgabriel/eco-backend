from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers

from .models import Ticket
from .signing import ticket_download_token


class TicketSerializer(serializers.ModelSerializer):
    booking_reference = serializers.CharField(source="booking.booking_reference", read_only=True)
    booking_status = serializers.CharField(source="booking.status", read_only=True)
    trip = serializers.IntegerField(source="booking.trip_id", read_only=True)
    seat_number = serializers.SerializerMethodField()
    full_name = serializers.SerializerMethodField()
    download_token = serializers.SerializerMethodField()

    class Meta:
        model = Ticket
        fields = [
            "id", "ticket_number", "booking", "booking_reference", "booking_status", "trip",
            "seat", "seat_number", "first_name", "last_name", "full_name",
            "phone_number", "id_number", "nationality",
            "seat_class", "price", "status", "download_token",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "ticket_number", "download_token", "created_at", "updated_at"]

    def get_download_token(self, obj):
        return ticket_download_token(obj.ticket_number) if obj.ticket_number else ""

    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}"

    def get_seat_number(self, obj):
        return obj.seat.seat_number if obj.seat_id else ""


class TicketCreateSerializer(serializers.Serializer):
    """Used inside BookingCreateSerializer for nested ticket creation."""
    seat = serializers.IntegerField(required=True, help_text="Seat ID from the seat map")
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    phone_number = PhoneNumberField()
    id_number = serializers.CharField(max_length=50)
    nationality = serializers.CharField(max_length=100, required=False, default="")
    seat_class = serializers.ChoiceField(
        choices=["VIP", "REGULAR"],
        default="REGULAR",
    )
