# Bebeto Booking - Code Examples & Usage

## Configuration Examples

### Example 1: Basic Configuration
```python
# settings.py

# Simple default: all bookings expire in 5 minutes
BOOKING_EXPIRY_CONFIG = {
    "default_minutes": 5,
}

PAYMENT_TIMEOUT_MINUTES = 30
PAYMENT_MAX_RETRIES = 3
```

### Example 2: Advanced Configuration (Recommended)
```python
# settings.py

# Different timeouts per payment method
BOOKING_EXPIRY_CONFIG = {
    "default_minutes": 5,
    "by_method": {
        "MOBILE_MONEY": 8,      # AT callbacks are slower
        "MTN_MOMO": 5,          # USSD is quick
        "MPESA": 10,            # STK push on slower networks
        "CASH": 15,             # Staff needs time
    },
}

# Payment timeout before marking as FAILED and cancelling booking
PAYMENT_TIMEOUT_MINUTES = 30

# Max retry attempts per payment
PAYMENT_MAX_RETRIES = 3

# Retry delay (in case of transient failures)
PAYMENT_RETRY_DELAY_SECONDS = 60

# Idempotency key cleanup (old keys deleted after 24h)
IDEMPOTENCY_KEY_TTL_HOURS = 24
```

---

## API Examples

### Creating a Booking
```bash
POST /api/v1/bookings/

{
    "trip": 123,
    "payer_name": "John Doe",
    "payment_phone": "+256771234567",
    "email": "john@example.com",
    "payment_method": "MTN_MOMO",
    "tickets": [
        {
            "seat": 45,
            "seat_class": "REGULAR",
            "first_name": "John",
            "last_name": "Doe",
            "phone_number": "+256771234567",
            "id_number": "KG1234567",
            "nationality": "Ugandan"
        },
        {
            "seat": 46,
            "seat_class": "VIP",
            "first_name": "Jane",
            "last_name": "Doe",
            "phone_number": "+256772345678",
            "id_number": "KG7654321",
            "nationality": "Ugandan"
        }
    ]
}

Response:
{
    "id": "...",
    "booking_reference": "BEB-20260421-ABC123",
    "status": "PENDING",
    "expires_at": "2026-04-21T15:10:00Z",  # 5 min from now (MTN_MOMO)
    "total_price": 50000,
    "number_of_seats": 2
}
```

### Retry Failed Payment
```bash
POST /api/v1/payments/{payment_id}/retry/

Response:
{
    "detail": "Payment retry initiated",
    "new_payment": {
        "id": "new-payment-uuid",
        "status": "PENDING",
        "method": "MTN_MOMO"
    },
    "retry_count": 1,
    "max_retries": 3
}
```

### Check MoMo Payment Status
```bash
GET /api/v1/payments/momo/status/{reference_id}/

Response:
{
    "reference_id": "...",
    "momo_status": "SUCCESSFUL",
    "external_id": "BEB-20260421-ABC123",
    "financial_transaction_id": "MTN12345"
}
```

### Cancel Booking with Refund Calculation
```bash
POST /api/v1/bookings/{booking_id}/cancel/

{
    "reason": "Changed my mind"
}

Response:
{
    "detail": "Booking cancelled.",
    "booking_reference": "BEB-20260421-ABC123",
    "hours_until_departure": 48.5,
    "refund_percentage": 100,
    "refund_amount": "50000",
    "reason": "Changed my mind"
}
```

---

## Python Code Examples

### Manually Trigger Payment Success (Testing)
```python
from payments.idempotent_handlers import _on_payment_success_idempotent
from payments.models import Payment

payment = Payment.objects.get(pk="payment-uuid")
booking, was_newly_completed = _on_payment_success_idempotent(payment)

if was_newly_completed:
    print(f"✓ Booking {booking.booking_reference} confirmed!")
else:
    print(f"Payment already completed")

# Safe to call multiple times
_on_payment_success_idempotent(payment)  # No-op on 2nd call
```

### Mark Payment as Failed
```python
from payments.idempotent_handlers import _on_payment_failed_idempotent
from payments.models import Payment

payment = Payment.objects.get(pk="payment-uuid")
_on_payment_failed_idempotent(payment, reason="Network timeout")

# Safe to call multiple times
_on_payment_failed_idempotent(payment, reason="Network timeout")  # No-op
```

### Find Stale Bookings
```python
from bookings.models import Booking
from django.utils import timezone
from datetime import timedelta

# Bookings PENDING for >5 minutes
cutoff = timezone.now() - timedelta(minutes=5)
stale = Booking.objects.filter(
    status="PENDING",
    created_at__lt=cutoff,
    expires_at__lt=timezone.now()
)

print(f"Found {stale.count()} stale bookings")
for booking in stale:
    print(f"  - {booking.booking_reference} "
          f"(expired {booking.expires_at}, "
          f"trip departs {booking.trip.departure_time})")
```

### Get Payment Health Report
```python
from payments.tasks import generate_payment_health_report

report = generate_payment_health_report()
print(f"Total payments today: {report['payments_total']}")
print(f"Completed: {report['payments_completed']}")
print(f"Failed: {report['payments_failed']}")
print(f"Stale (>30min PENDING): {report['stale_pending']}")
print(f"MoMo stuck: {report['momo_pending_stuck']}")

# Check if warnings triggered
if report['stale_pending'] > 5:
    send_alert_email(f"⚠️ {report['stale_pending']} stale payments!")
```

### Extend Booking Expiry (Optional Feature)
```python
from bookings.models import Booking
from django.utils import timezone
from datetime import timedelta

booking = Booking.objects.get(booking_reference="BEB-...")

# Check if can extend (PENDING, not expired, <2 extensions)
if booking.status == "PENDING" and booking.expires_at > timezone.now():
    # Add 5 minutes
    booking.expires_at += timedelta(minutes=5)
    booking.save()
    print(f"✓ Extended to {booking.expires_at}")
else:
    print("❌ Cannot extend this booking")
```

---

## Celery Task Examples

### Run Cleanup Manually
```python
from payments.tasks import cleanup_stale_payments_task

# Cleanup payments PENDING for >30 min
cleanup_stale_payments_task(timeout_minutes=30)

# Output: ✓ cleanup_stale_payments: Processed 5 stale bookings
```

### Check Stuck MoMo Transactions
```python
from payments.tasks import check_stuck_momo_transactions

# Check transactions polling for >5 minutes
check_stuck_momo_transactions(polling_timeout_minutes=5)

# Output: ✓ check_stuck_momo_transactions: Marked 2 transactions as TIMEOUT
```

### Django Shell Commands
```bash
python manage.py shell

# View stale payments
from payments.models import Payment
from datetime import timedelta
from django.utils import timezone

cutoff = timezone.now() - timedelta(minutes=30)
stale = Payment.objects.filter(status="PENDING", created_at__lt=cutoff)
for p in stale:
    print(f"{p.booking.booking_reference}: {p.status} for {(timezone.now() - p.created_at).total_seconds() / 60:.0f} min")

# Exit
exit()
```

---

## Management Command Examples

### Dry Run (See What Would Be Cleaned)
```bash
python manage.py cleanup_stale_payments --timeout-minutes=30 --dry-run

# Output:
# [DRY RUN] Would fail payment 123 (booking BEB-20260421-ABC123, created 2026-04-21 14:05:00)
# [DRY RUN] Would fail payment 124 (booking BEB-20260421-XYZ789, created 2026-04-21 14:10:00)
# ✓ Cleanup complete. Processed 2 stale bookings.
```

### Actually Run Cleanup
```bash
python manage.py cleanup_stale_payments --timeout-minutes=30

# Output:
# ✓ Cancelled payment 123 and booking BEB-20260421-ABC123 (PENDING for 30+ min)
# ✓ Cancelled payment 124 and booking BEB-20260421-XYZ789 (PENDING for 30+ min)
# ✓ Cleanup complete. Processed 2 stale bookings.
```

### Custom Timeout
```bash
# Mark payments as failed if PENDING for >60 minutes
python manage.py cleanup_stale_payments --timeout-minutes=60
```

---

## Monitoring & Logging

### Check Celery Beat Schedule
```bash
# List all scheduled tasks
celery -A bebeto_backend inspect scheduled

# Output shows cleanup tasks scheduled for every 10 minutes
```

### View Celery Worker Logs
```bash
# Watch worker logs in real-time
docker logs -f celery_worker

# Or if using local celery
celery -A bebeto_backend worker -l info
```

### Check Payment Logs
```bash
# Find payment timeout logs
docker logs django_app | grep "Payment.*TIMEOUT"

# Find stale payment cleanup
docker logs celery_worker | grep "cleanup_stale_payments"
```

---

## Testing Scenarios

### Scenario 1: Payment Timeout
```python
# 1. Create booking with MTN_MOMO (5 min expiry)
booking = Booking.objects.create(
    trip=trip,
    payer_name="Test User",
    payment_phone="+256771234567",
    number_of_seats=1,
    total_price=50000,
    expires_at=now + timedelta(minutes=5)
)

# 2. Create payment
payment = Payment.objects.create(
    booking=booking,
    amount=50000,
    method="MTN_MOMO",
    status="PENDING"
)

# 3. Wait 5+ minutes (or manually set created_at in past)
payment.created_at = timezone.now() - timedelta(minutes=35)
payment.save()

# 4. Run cleanup
from payments.tasks import cleanup_stale_payments_task
cleanup_stale_payments_task(timeout_minutes=30)

# 5. Verify
assert booking.status == "CANCELLED"
assert payment.status == "FAILED"
print("✓ Test passed: payment timeout handled correctly")
```

### Scenario 2: Idempotent Payment Success
```python
# 1. Create payment
payment = Payment.objects.create(...)

# 2. Call success handler 3 times
from payments.idempotent_handlers import _on_payment_success_idempotent
for i in range(3):
    booking, was_newly_completed = _on_payment_success_idempotent(payment)
    print(f"Call {i+1}: was_newly_completed={was_newly_completed}")

# Output:
# Call 1: was_newly_completed=True
# Call 2: was_newly_completed=False
# Call 3: was_newly_completed=False
# ✓ Idempotent!
```

### Scenario 3: Payment Retry
```python
# 1. Payment fails
payment = Payment.objects.create(
    booking=booking,
    method="MTN_MOMO",
    status="FAILED"
)

# 2. User clicks "Retry Payment"
# This calls the retry endpoint:
# POST /api/v1/payments/{payment_id}/retry/

# 3. Backend creates new payment
new_payment = Payment.objects.create(
    booking=booking,
    method="MTN_MOMO",
    status="PENDING"
)

# 4. New payment flow initiated
# User gets new MoMo prompt on their phone
```

---

## Troubleshooting

### Problem: Cleanup task not running
```bash
# Check if beat scheduler is running
ps aux | grep celery.*beat

# If not, start it
celery -A bebeto_backend beat -l info

# Or in Docker
docker-compose up celery-beat
```

### Problem: Payment stuck PENDING after 30 minutes
```python
from payments.models import Payment
from django.utils import timezone
from datetime import timedelta

# Find stuck payments
cutoff = timezone.now() - timedelta(minutes=30)
stuck = Payment.objects.filter(
    status="PENDING",
    created_at__lt=cutoff
)

# Manually cleanup
for payment in stuck:
    payment.status = "FAILED"
    payment.save()
    payment.booking.status = "CANCELLED"
    payment.booking.save()
```

### Problem: Duplicate payment confirmations
```python
# This can't happen with idempotent handler
# But if it does, check logs:
# grep "already COMPLETED" logs/django.log
# grep "SELECT FOR UPDATE" logs/django.log
```

---

## Performance Tips

1. **Index on Payment.created_at**
   ```python
   # In Payment model Meta:
   indexes = [
       models.Index(fields=["status", "created_at"]),
   ]
   ```

2. **Batch Cleanup**
   - Cleanup task processes payments in batches
   - Configurable via `cleanup_stale_payments_task(batch_size=100)`

3. **Celery Task Routing**
   - Route cleanup to separate worker with higher priority
   - Prevents payment confirmations from being blocked

4. **Cache Booking Expiry Config**
   - BOOKING_EXPIRY_CONFIG loaded once at startup
   - Change requires server restart (or cache invalidation)

---

## Migration Path

If upgrading from old system:

1. **Week 1**: Deploy improvements, keep old cleanup task disabled
2. **Week 2**: Enable cleanup task with dry-run
3. **Week 3**: Monitor logs for false positives
4. **Week 4**: Enable cleanup task for real (should auto-cleanup ~2% of bookings)
5. **Week 5+**: Monitor payment health report daily
