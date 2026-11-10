from rest_framework import serializers

from .models import ExchangeRate


class ExchangeRateSerializer(serializers.ModelSerializer):
    updated_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ExchangeRate
        fields = [
            "id", "from_currency", "to_currency", "rate",
            "effective_date", "updated_by", "updated_by_name",
            "notes", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "updated_by", "updated_by_name", "created_at", "updated_at"]

    def get_updated_by_name(self, obj):
        if obj.updated_by:
            return obj.updated_by.get_full_name() or obj.updated_by.email
        return None

    def validate(self, attrs):
        from_cur = attrs.get("from_currency")
        to_cur = attrs.get("to_currency")
        if from_cur and to_cur and from_cur == to_cur:
            raise serializers.ValidationError("From and To currency must be different.")
        rate = attrs.get("rate")
        if rate is not None and rate <= 0:
            raise serializers.ValidationError({"rate": "Rate must be positive."})
        return attrs


class LatestRatesSerializer(serializers.Serializer):
    """Read-only serializer for the /latest/ endpoint."""
    from_currency = serializers.CharField()
    to_currency = serializers.CharField()
    rate = serializers.DecimalField(max_digits=12, decimal_places=6)
    effective_date = serializers.DateField()
    updated_by_name = serializers.CharField(allow_null=True)
