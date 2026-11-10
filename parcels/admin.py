from django.contrib import admin

from .models import Parcel, ParcelPhoto, ParcelStatusLog


class ParcelPhotoInline(admin.TabularInline):
    model = ParcelPhoto
    extra = 0
    readonly_fields = ["uploaded_by", "created_at"]


class ParcelStatusLogInline(admin.TabularInline):
    model = ParcelStatusLog
    extra = 0
    readonly_fields = ["old_status", "new_status", "changed_by", "location", "note", "created_at"]


@admin.register(Parcel)
class ParcelAdmin(admin.ModelAdmin):
    list_display = ["tracking_number", "sender_name", "receiver_name", "trip", "size", "status", "pickup_code", "picked_up_at", "created_at"]
    list_filter = ["status", "size"]
    search_fields = ["tracking_number", "sender_name", "receiver_name", "sender_phone", "receiver_phone"]
    readonly_fields = ["tracking_number", "pickup_code", "picked_up_at"]
    raw_id_fields = ["trip", "booking", "payment"]
    inlines = [ParcelPhotoInline, ParcelStatusLogInline]
