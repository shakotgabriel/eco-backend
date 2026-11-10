from django.contrib import admin

from .audit import AuditLog
from .models import Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("title", "report_type", "generated_by", "date_from", "date_to", "created_at")
    list_filter = ("report_type", "created_at")
    search_fields = ("title",)
    date_hierarchy = "created_at"


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "actor", "target_model", "target_id", "ip_address", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("target_id", "actor__username")
    readonly_fields = ("action", "actor", "target_model", "target_id", "details", "ip_address", "created_at")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
