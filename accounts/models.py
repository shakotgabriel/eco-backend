from django.contrib.auth.models import AbstractUser
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField


class User(AbstractUser):
    ROLE_CHOICES = (
        ("ADMIN", "Admin"),
        ("MANAGER", "Manager"),
        ("STAFF", "Staff"),
        ("DRIVER", "Driver"),
        ("OPERATOR", "Operator"),
    )

    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="STAFF")
    phone_number = PhoneNumberField(blank=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.username


class PortalSettings(models.Model):
    """Singleton-style portal configuration stored as JSON."""

    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="portal_settings_updates",
    )

    class Meta:
        verbose_name = "Portal settings"
        verbose_name_plural = "Portal settings"

    @classmethod
    def load(cls) -> "PortalSettings":
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"data": {}})
        return obj