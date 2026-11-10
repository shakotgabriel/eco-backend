from django.contrib import admin

from .models import Ticket


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("ticket_number", "booking", "first_name", "last_name", "phone_number", "seat", "seat_class", "price", "status", "created_at")
    list_filter = ("status", "seat_class", "created_at")
    search_fields = ("ticket_number", "first_name", "last_name", "phone_number", "id_number", "booking__booking_reference")
    list_editable = ("status",)
    readonly_fields = ("ticket_number",)
