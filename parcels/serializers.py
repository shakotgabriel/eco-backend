from decimal import Decimal

from rest_framework import serializers

from .models import Parcel, ParcelPhoto, ParcelStatusLog


class ParcelStatusLogSerializer(serializers.ModelSerializer):
    changed_by_name = serializers.CharField(source="changed_by.__str__", read_only=True, default=None)
    location_name = serializers.CharField(source="location.name", read_only=True, default=None)

    class Meta:
        model = ParcelStatusLog
        fields = [
            "id", "old_status", "new_status", "changed_by", "changed_by_name",
            "location", "location_name", "note", "created_at",
        ]
        read_only_fields = ["id", "old_status", "changed_by", "created_at"]


class ParcelPhotoSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    uploaded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ParcelPhoto
        fields = ["id", "url", "caption", "uploaded_by", "uploaded_by_name", "created_at"]
        read_only_fields = fields

    def get_url(self, obj):
        request = self.context.get("request")
        path = f"/api/v1/parcels/{obj.parcel_id}/photos/{obj.id}/file/"
        if request:
            return request.build_absolute_uri(path)
        return path

    def get_uploaded_by_name(self, obj):
        if not obj.uploaded_by:
            return None
        return str(obj.uploaded_by)


class ParcelSerializer(serializers.ModelSerializer):
    trip_display = serializers.CharField(source="trip.__str__", read_only=True)
    status_logs = ParcelStatusLogSerializer(many=True, read_only=True)
    photos = ParcelPhotoSerializer(many=True, read_only=True)
    payment_status = serializers.CharField(source="payment.status", read_only=True, default=None)

    class Meta:
        model = Parcel
        fields = [
            "id", "tracking_number", "trip", "trip_display",
            "sender_name", "sender_phone",
            "receiver_name", "receiver_phone",
            "description", "weight_kg", "size", "declared_value",
            "shipping_fee", "currency", "status", "notes",
            "pickup_code", "picked_up_at",
            "payment", "payment_status",
            "booking", "status_logs", "photos",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "tracking_number", "pickup_code", "picked_up_at",
            "created_at", "updated_at",
        ]


class ParcelPublicCreateSerializer(serializers.ModelSerializer):
    """
    Customer-facing parcel creation. Unlike the staff `ParcelSerializer`, the
    shipping fee and currency are never trusted from the client — they're
    derived server-side from the selected trip's cargo rate.
    """
    class Meta:
        model = Parcel
        fields = [
            "id", "tracking_number", "trip",
            "sender_name", "sender_phone",
            "receiver_name", "receiver_phone",
            "description", "weight_kg", "size", "declared_value",
            "shipping_fee", "currency", "status",
            "created_at",
        ]
        read_only_fields = ["id", "tracking_number", "shipping_fee", "currency", "status", "created_at"]

    def validate_trip(self, trip):
        if trip.status not in ("SCHEDULED", "BOARDING"):
            raise serializers.ValidationError("This trip is no longer accepting parcels.")
        return trip

    def create(self, validated_data):
        trip = validated_data["trip"]
        weight_kg = validated_data.get("weight_kg") or Decimal("1")
        rate = trip.cargo_price_per_kg or Decimal("500")
        shipping_fee = (Decimal(rate) * Decimal(weight_kg)).quantize(Decimal("0.01"))

        return Parcel.objects.create(
            shipping_fee=shipping_fee,
            currency=trip.route.currency,
            **validated_data,
        )


class ParcelTrackSerializer(serializers.ModelSerializer):
    """Public-facing serializer for tracking — hides sensitive fields."""
    route = serializers.SerializerMethodField()
    departure = serializers.DateTimeField(source="trip.departure_time", read_only=True)

    class Meta:
        model = Parcel
        fields = [
            "id", "tracking_number", "sender_name", "receiver_name",
            "description", "size", "status",
            "route", "departure",
            "created_at", "updated_at",
        ]

    def get_route(self, obj):
        trip = obj.trip
        return f"{trip.route.origin.name} → {trip.route.destination.name}"


class UpdateParcelStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["PENDING", "IN_TRANSIT", "ARRIVED", "DELIVERED", "CANCELLED"])
    location = serializers.IntegerField(required=False, help_text="City ID where status change occurred")
    note = serializers.CharField(max_length=300, required=False, default="")


class ConfirmPickupSerializer(serializers.Serializer):
    pickup_code = serializers.CharField(max_length=6, help_text="6-digit pickup code from SMS")


class TransferParcelSerializer(serializers.Serializer):
    new_trip_id = serializers.IntegerField(help_text="ID of the new trip to transfer parcel to")
