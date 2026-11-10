from django.contrib import admin

from .models import Seat


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = ("bus", "seat_number", "seat_type", "is_active")
    list_filter = ("seat_type", "is_active", "bus")
    search_fields = ("seat_number", "bus__plate_number", "bus__name")
    list_editable = ("is_active",)
