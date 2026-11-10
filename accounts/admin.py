from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "email", "role", "phone_number", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("username", "email", "phone_number", "first_name", "last_name")
    ordering = ("username",)

    fieldsets = BaseUserAdmin.fieldsets + (
        ("EcoBus Info", {"fields": ("role", "phone_number")}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("EcoBus Info", {"fields": ("role", "phone_number")}),
    )
