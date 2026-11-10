from django.conf import settings
from django.db import models

from bebeto_backend.base import BaseModel


class ExchangeRate(BaseModel):
    """
    Stores exchange rates between currencies.
    Managers update these daily or weekly per city/country.
    The latest rate (by effective_date) is used for conversions.
    """

    CURRENCY_CHOICES = (
        ("SSP", "South Sudanese Pound"),
        ("KES", "Kenyan Shilling"),
        ("UGX", "Ugandan Shilling"),
        ("RWF", "Rwandan Franc"),
        ("USD", "US Dollar"),
    )

    from_currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES)
    to_currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES)
    rate = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        help_text="1 unit of from_currency = rate units of to_currency",
    )
    effective_date = models.DateField(
        help_text="Date from which this rate is effective",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exchange_rate_updates",
        help_text="Manager who set this rate",
    )
    notes = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional note, e.g. 'Black market rate', 'Central bank rate'",
    )

    class Meta:
        ordering = ["-effective_date", "from_currency", "to_currency"]
        constraints = [
            models.UniqueConstraint(
                fields=["from_currency", "to_currency", "effective_date"],
                name="unique_rate_per_day",
            ),
        ]
        indexes = [
            models.Index(
                fields=["from_currency", "to_currency", "-effective_date"],
                name="idx_rate_lookup",
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.from_currency == self.to_currency:
            raise ValidationError("From and To currency must be different.")
        if self.rate <= 0:
            raise ValidationError("Rate must be positive.")

    def __str__(self):
        return f"1 {self.from_currency} = {self.rate} {self.to_currency} ({self.effective_date})"
