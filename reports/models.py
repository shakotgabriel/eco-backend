from bebeto_backend.base import BaseModel
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Report(BaseModel):
    REPORT_TYPE_CHOICES = (
        ("DAILY_SALES", "Daily Sales"),
        ("WEEKLY_SALES", "Weekly Sales"),
        ("TRIP_SUMMARY", "Trip Summary"),
        ("ROUTE_PERFORMANCE", "Route Performance"),
        ("MONTHLY_REVENUE", "Monthly Revenue"),
    )

    title = models.CharField(max_length=200)
    report_type = models.CharField(max_length=30, choices=REPORT_TYPE_CHOICES)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="reports",
    )
    date_from = models.DateField()
    date_to = models.DateField()
    data = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValidationError("'Date to' must be on or after 'Date from'.")

    def __str__(self):
        return f"{self.title} ({self.report_type})"
