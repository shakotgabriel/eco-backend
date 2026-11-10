from django.contrib import admin

from .models import Bus, MaintenanceRecord


class MaintenanceRecordInline(admin.TabularInline):
    model = MaintenanceRecord
    extra = 0
    fields = ("maintenance_type", "status", "scheduled_date", "completed_date", "cost", "currency", "vendor", "next_service_date")
    readonly_fields = ("completed_date",)


@admin.register(Bus)
class BusAdmin(admin.ModelAdmin):
    list_display = ("plate_number", "name", "operator", "home_city", "capacity", "bus_type", "current_mileage_km", "status")
    list_filter = ("bus_type", "status", "home_city", "operator")
    search_fields = ("plate_number", "name", "operator__username", "operator__first_name")
    list_editable = ("status",)
    raw_id_fields = ("operator",)
    inlines = [MaintenanceRecordInline]


@admin.register(MaintenanceRecord)
class MaintenanceRecordAdmin(admin.ModelAdmin):
    list_display = ("bus", "maintenance_type", "status", "scheduled_date", "completed_date", "cost", "currency", "vendor", "next_service_date")
    list_filter = ("maintenance_type", "status", "bus")
    search_fields = ("bus__plate_number", "bus__name", "vendor", "performed_by", "description")
    list_editable = ("status",)
    raw_id_fields = ("bus", "approved_by")
    date_hierarchy = "scheduled_date"
