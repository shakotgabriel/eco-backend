from django.contrib import admin

from .models import Booking, BookingGroup, Reschedule


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("booking_reference", "payer_name", "payment_phone", "trip", "number_of_seats", "total_price", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("booking_reference", "payer_name", "payment_phone", "email")
    list_editable = ("status",)
    readonly_fields = ("booking_reference",)
    date_hierarchy = "created_at"


@admin.register(BookingGroup)
class BookingGroupAdmin(admin.ModelAdmin):
    list_display = ("group_reference", "trip_type", "discount_pct", "created_at")
    list_filter = ("trip_type",)
    search_fields = ("group_reference",)
    readonly_fields = ("group_reference",)


@admin.register(Reschedule)
class RescheduleAdmin(admin.ModelAdmin):
    list_display = (
        "id", "original_booking", "new_booking",
        "original_fare", "new_fare", "fare_difference",
        "reschedule_fee", "total_due", "status", "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = (
        "original_booking__booking_reference",
        "new_booking__booking_reference",
    )
    readonly_fields = (
        "original_booking", "new_booking", "original_fare", "new_fare",
        "fare_difference", "reschedule_fee", "total_due", "rescheduled_by",
    )
