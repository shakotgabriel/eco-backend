from decimal import Decimal

from bebeto_backend.base import BaseModel
from django.conf import settings
from django.db import models
from django.db.models import Sum
from phonenumber_field.modelfields import PhoneNumberField


class CommissionTier(models.Model):
    """
    Tiered commission rates based on monthly sales volume.
    E.g. 0-50 sales = 5%, 51-100 = 7%, 101+ = 10%.
    """
    name = models.CharField(max_length=50, help_text="Tier name, e.g. Bronze, Silver, Gold")
    min_monthly_sales = models.PositiveIntegerField(help_text="Minimum sales in a month to qualify")
    commission_pct = models.DecimalField(
        max_digits=5, decimal_places=2, help_text="Commission % for this tier"
    )

    class Meta:
        ordering = ["min_monthly_sales"]

    def __str__(self):
        return f"{self.name} ({self.min_monthly_sales}+ sales → {self.commission_pct}%)"


class Agent(BaseModel):
    """
    A ticket-selling agent or sales point (bus station counter, town agent, etc.).
    Linked to a User account that can log in and sell tickets.
    """
    STATUS_CHOICES = (
        ("ACTIVE", "Active"),
        ("SUSPENDED", "Suspended"),
        ("TERMINATED", "Terminated"),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_profile"
    )
    agent_code = models.CharField(max_length=20, unique=True, help_text="Unique agent identifier, e.g. AGT-NBO-001")
    business_name = models.CharField(max_length=150, blank=True, help_text="Business or station name")
    city = models.ForeignKey(
        "cities.City", on_delete=models.SET_NULL, null=True, blank=True, related_name="agents"
    )
    phone = PhoneNumberField(help_text="Agent's contact phone")
    commission_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=5.00,
        help_text="Base commission percentage (overridden by tier if applicable)"
    )
    current_tier = models.ForeignKey(
        CommissionTier, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="agents", help_text="Current commission tier based on performance",
    )
    referred_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="referrals", help_text="Agent who referred this agent",
    )
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="ACTIVE")
    monthly_sales_target = models.PositiveIntegerField(
        default=0,
        help_text="Monthly ticket sales target set by management",
    )

    class Meta:
        ordering = ["agent_code"]

    def __str__(self):
        return f"{self.agent_code} — {self.business_name or self.user.get_full_name()}"

    @property
    def effective_commission_pct(self):
        """Return tier commission if set, otherwise base commission."""
        if self.current_tier:
            return self.current_tier.commission_pct
        return self.commission_pct

    def get_monthly_sales_count(self):
        """Count of sales this calendar month."""
        from django.utils import timezone
        now = timezone.now()
        return self.sales.filter(
            created_at__year=now.year,
            created_at__month=now.month,
        ).count()

    def update_tier(self):
        """Recalculate and update the agent's commission tier based on monthly sales."""
        monthly_count = self.get_monthly_sales_count()
        tier = (
            CommissionTier.objects
            .filter(min_monthly_sales__lte=monthly_count)
            .order_by("-min_monthly_sales")
            .first()
        )
        if tier != self.current_tier:
            self.current_tier = tier
            self.save(update_fields=["current_tier", "updated_at"])
        return tier


class AgentSale(BaseModel):
    """
    Records each booking made by an agent. Used for commission calculation.
    """
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="sales")
    booking = models.OneToOneField(
        "bookings.Booking", on_delete=models.CASCADE, related_name="agent_sale"
    )
    commission_pct = models.DecimalField(max_digits=5, decimal_places=2, help_text="Commission % at time of sale")
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="Calculated commission")
    is_paid = models.BooleanField(default=False, help_text="Whether commission has been paid out")
    paid_at = models.DateTimeField(null=True, blank=True)
    payout = models.ForeignKey(
        "AgentPayout", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="sales", help_text="Payout batch this sale was paid in",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.agent.agent_code} — {self.booking.booking_reference} ({self.commission_amount})"


class AgentPayout(BaseModel):
    """
    Records a commission payout to an agent. Groups multiple sales into one payment.
    """
    PAYOUT_METHOD_CHOICES = (
        ("CASH", "Cash"),
        ("BANK_TRANSFER", "Bank Transfer"),
        ("MTN_MOMO", "MTN Mobile Money"),
        ("MPESA", "M-Pesa"),
    )
    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("COMPLETED", "Completed"),
        ("FAILED", "Failed"),
    )

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="payouts")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="SSP")
    method = models.CharField(max_length=15, choices=PAYOUT_METHOD_CHOICES, default="CASH")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="PENDING")
    reference = models.CharField(max_length=100, blank=True, help_text="Transaction reference / receipt number")
    note = models.TextField(blank=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="agent_payouts_processed",
    )
    sales_count = models.PositiveIntegerField(default=0, help_text="Number of sales in this payout")
    period_from = models.DateField(null=True, blank=True, help_text="Earliest sale date in this payout")
    period_to = models.DateField(null=True, blank=True, help_text="Latest sale date in this payout")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Payout {self.id} — {self.agent.agent_code} — {self.currency} {self.amount}"
