from rest_framework import serializers

from .models import City


class CitySerializer(serializers.ModelSerializer):
    class Meta:
        model = City
        fields = ["id", "name", "code", "country", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]
