from django.contrib import admin

from .models import City


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "country", "is_active", "created_at")
    list_filter = ("country", "is_active")
    search_fields = ("name", "code", "country")
    list_editable = ("is_active",)
