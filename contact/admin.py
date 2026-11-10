from django.contrib import admin
from .models import ContactMessage


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "subject", "status", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["name", "email", "subject", "message"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        ("Contact Info", {"fields": ("name", "email", "phone")}),
        ("Message", {"fields": ("subject", "message")}),
        ("Status", {"fields": ("status",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
