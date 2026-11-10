from rest_framework import serializers

from .audit import AuditLog
from .models import Report


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id", "action", "actor", "actor_name",
            "target_model", "target_id", "details",
            "ip_address", "created_at",
        ]
        read_only_fields = fields

    def get_actor_name(self, obj):
        return obj.actor.username if obj.actor else "system"


class ReportSerializer(serializers.ModelSerializer):
    generated_by_name = serializers.CharField(source="generated_by.username", read_only=True)

    class Meta:
        model = Report
        fields = [
            "id", "title", "report_type", "generated_by", "generated_by_name",
            "date_from", "date_to", "data",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
