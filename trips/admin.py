from django.contrib import admin

from .models import Trip, TripSchedule


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ("route", "bus", "driver", "departure_time", "arrival_time", "regular_price", "vip_price", "cargo_price_per_kg", "status", "available_seats")
    list_filter = ("status", "departure_time", "route", "bus", "driver")
    search_fields = ("route__origin__name", "route__destination__name", "bus__plate_number", "bus__name", "driver__first_name", "driver__last_name")
    list_editable = ("status",)
    date_hierarchy = "departure_time"
    raw_id_fields = ("driver",)


@admin.register(TripSchedule)
class TripScheduleAdmin(admin.ModelAdmin):
    list_display = ("route", "bus", "driver", "departure_time", "days_of_week", "regular_price", "vip_price", "is_active")
    list_filter = ("is_active", "route", "bus")
    list_editable = ("is_active",)
    raw_id_fields = ("driver",)
