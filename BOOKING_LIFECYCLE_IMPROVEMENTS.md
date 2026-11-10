# Bebeto Booking Lifecycle - Complete Improvements Guide

## Summary of Changes

This document provides a complete set of improvements to the booking lifecycle addressing the issues identified in the lifecycle trace.

---

## ✅ Issue 1: Payment Timeout Not Auto-resolved

### Problem
- MoMo polls for 3 min then stops, but Payment stays PENDING forever
- M-Pesa has no polling fallback if callback fails
- Seats remain reserved indefinitely
- Customer receives no notification

### Solution Implemented
1. **Created `cleanup_stale_payments_task()` (Celery beat task)**
   - Runs every 10 minutes
   - Marks payments FAILED if PENDING for >30 minutes
   - Auto-cancels associated bookings
   - Sends SMS notification: "Your payment timed out. Booking cancelled."
   - **File**: `payments/tasks_cleanup.py`

2. **Created `check_stuck_momo_transactions()` (Celery beat task)**
   - Monitors MoMo transactions stuck in polling state
   - Marks as TIMEOUT after 5 minutes of inactivity
   - Sends failure notification to customer
   - **File**: `payments/tasks_cleanup.py`

3. **Added Management Command**
   - Run manually: `python manage.py cleanup_stale_payments --timeout-minutes=30`
   - Dry-run mode: `--dry-run`
   - **File**: `payments/management/commands/cleanup_stale_payments.py`

---

## ✅ Issue 2: Hard-coded 2-Minute Booking Expiry

### Problem
- Timeout is hardcoded in serializer: `expires_at = now + timedelta(minutes=2)`
- No way to configure per payment method or context
- Too short for USSD/STK prompts on slow networks

### Solution Implemented
1. **Added Settings Configuration**
   ```python
   BOOKING_EXPIRY_CONFIG = {
       "default_minutes": 5,
       "by_method": {
           "MOBILE_MONEY": 8,
           "MTN_MOMO": 5,
           "MPESA": 10,
           "CASH": 15,
       },
   }
   ```
   - **File**: `bebeto_backend/booking_settings.py`

2. **Updated `BookingCreateSerializer.create()`**
   - Reads `BOOKING_EXPIRY_CONFIG` from settings
   - Selects timeout based on `payment_method`
   - Falls back to default if method not configured
   - **File**: `bookings/serializers.py` (lines ~197-206)

### Usage
```python
# In Django settings.py
from bebeto_backend.booking_settings import BOOKING_EXPIRY_CONFIG
```

---

## ✅ Issue 3: Race Condition on Payment Success

### Problem
- Seats validated AFTER callback received
- Window exists where seats could be released
- If seats now unavailable, payment marked FAILED but customer already paid

### Solution Implemented
1. **Pessimistic Locking in Idempotent Handler**
   - Uses `SELECT FOR UPDATE` on Payment row
   - Acquires lock before checking seat availability
   - Prevents concurrent updates
   - **File**: `payments/idempotent_handlers.py`

2. **Seat Availability Validation**
   - Still validates before confirming
   - If seats missing, marks payment FAILED + transaction FAILED
   - Allows customer to retry with different seats
   - **File**: `payments/views.py` line ~735-740

---

## ✅ Issue 4: No Idempotency on Payment Status Updates

### Problem
- Multiple callbacks could trigger duplicate confirmations
- Tickets/PDFs/emails sent multiple times
- No guard against race conditions

### Solution Implemented
1. **Created Idempotent Handler Functions**
   - `_on_payment_success_idempotent(payment)` - Uses SELECT FOR UPDATE + double-check
   - `_on_payment_failed_idempotent(payment, reason)` - Safe to call multiple times
   - Both are fully atomic and atomic
   - **File**: `payments/idempotent_handlers.py`

2. **Updated All Payment Callbacks**
   - MoMo callback now uses idempotent handler
   - M-Pesa callback updated (similar pattern)
   - Polling completion now uses idempotent handler
   - **File**: `payments/views.py` line ~721+

3. **How It Works**
   ```python
   # Safe to call 10x — only executes once
   _on_payment_success_idempotent(payment)
   
   # Returns tuple:
   # (booking, was_newly_completed)
   # was_newly_completed = True only if we just transitioned to COMPLETED
   ```

---

## ✅ Issue 5: No Payment Retry Mechanism

### Problem
- If callback never arrives, no way to retry
- Manual staff intervention required
- Customer has no self-service option

### Solution Implemented
1. **Created Payment Retry Endpoint**
   - `POST /api/v1/payments/{id}/retry/`
   - Validates: payment FAILED, booking still PENDING, within max retries
   - Creates new Payment record with same method
   - Re-initiates payment flow (MoMo/MPESA specific)
   - Limits retries to `PAYMENT_MAX_RETRIES` (default 3)
   - **File**: `payments/payment_retry_endpoint.py`

2. **Register Endpoint in URLs**
   Add to `payments/urls.py`:
   ```python
   path("payments/<uuid:id>/retry/", payment_retry, name="payment_retry"),
   ```

### Usage Example
```bash
curl -X POST http://localhost:8000/api/v1/payments/550e8400-e29b-41d4-a716-446655440000/retry/
# Response:
{
    "detail": "Payment retry initiated",
    "new_payment": { ... },
    "retry_count": 2,
    "max_retries": 3
}
```

---

## 🔧 Installation & Configuration

### Step 1: Update Settings
Add to `bebeto_backend/settings.py`:

```python
from bebeto_backend.booking_settings import BOOKING_EXPIRY_CONFIG

# Booking configuration
BOOKING_EXPIRY_CONFIG = {
    "default_minutes": 5,
    "by_method": {
        "MOBILE_MONEY": 8,
        "MTN_MOMO": 5,
        "MPESA": 10,
        "CASH": 15,
    },
}

# Payment timeout before marking as FAILED
PAYMENT_TIMEOUT_MINUTES = 30

# Payment retry configuration
PAYMENT_MAX_RETRIES = 3
PAYMENT_RETRY_DELAY_SECONDS = 60
```

### Step 2: Add Celery Beat Schedule
Update `bebeto_backend/celery.py`:

```python
from celery.schedules import crontab

app.conf.beat_schedule = {
    "cleanup-stale-payments": {
        "task": "payments.tasks.cleanup_stale_payments_task",
        "schedule": crontab(minute="*/10"),
        "kwargs": {"timeout_minutes": 30}
    },
    "check-stuck-momo-transactions": {
        "task": "payments.tasks.check_stuck_momo_transactions",
        "schedule": crontab(minute="*/15"),
        "kwargs": {"polling_timeout_minutes": 5}
    },
    "payment-health-report": {
        "task": "payments.tasks.generate_payment_health_report",
        "schedule": crontab(hour="0", minute="0"),  # Daily at midnight
    },
}
```

### Step 3: Create Management Directories
```bash
mkdir -p payments/management/commands
touch payments/management/__init__.py
touch payments/management/commands/__init__.py
```

### Step 4: Run Migrations (if any model changes)
```bash
python manage.py makemigrations
python manage.py migrate
```

### Step 5: Test the Cleanup Task
```bash
# Dry run to see what would be cleaned
python manage.py cleanup_stale_payments --timeout-minutes=30 --dry-run

# Actually run cleanup
python manage.py cleanup_stale_payments --timeout-minutes=30
```

### Step 6: Monitor Celery Beat
Ensure Celery beat scheduler is running:
```bash
celery -A bebeto_backend beat -l info
```

---

## 📊 Monitoring & Alerting

### Built-in Health Report
Run daily to get payment statistics:
```bash
python manage.py shell
from payments.tasks import generate_payment_health_report
report = generate_payment_health_report()
print(report)
```

### Manual Checks
```python
# Find stale PENDING payments
from payments.models import Payment
from django.utils import timezone
from datetime import timedelta

cutoff = timezone.now() - timedelta(minutes=30)
stale = Payment.objects.filter(status="PENDING", created_at__lt=cutoff)
print(f"Stale payments: {stale.count()}")

# Find stuck MoMo transactions
from payments.models import MoMoTransaction
stuck = MoMoTransaction.objects.filter(
    momo_status="PENDING",
    created_at__lt=cutoff
)
print(f"Stuck MoMo: {stuck.count()}")
```

---

## 🚀 Advanced Improvements (Optional)

### Option A: Optimistic Locking for Multi-leg Bookings
When one leg fails, refund all legs:

```python
# In BookingGroup.save():
@transaction.atomic
def ensure_all_legs_confirmed():
    """Rollback entire group if any leg payment fails"""
    for booking in self.bookings.all():
        if booking.status in ("PENDING", "FAILED"):
            # Mark entire group as FAILED
            self.status = "PARTIALLY_FAILED"
            self.save()
            return False
    return True
```

### Option B: Extend Expiry Endpoint
Allow customers to extend PENDING booking timeout:

```python
@api_view(["POST"])
@permission_classes([AllowAny])
def extend_booking_expiry(request, booking_ref):
    """
    POST /api/v1/bookings/{ref}/extend-expiry/
    {"payment_phone": "2567..."}
    
    Extends expiry by 5 minutes (max 2 extensions per booking)
    """
    # Validate phone matches payment_phone
    # Check extension count < 2
    # Update booking.expires_at += timedelta(minutes=5)
```

### Option C: Payment Method Auto-retry
If MoMo fails, offer fallback to M-Pesa:

```python
# In payment failed notification:
{
    "detail": "MoMo payment failed",
    "retry_methods": ["MPESA", "MOBILE_MONEY"],  # Alternative methods
    "retry_url": "/api/v1/payments/123/retry-with-method/",
}
```

---

## 📝 Testing Checklist

- [ ] Cleanup task finds stale payments (test with `--dry-run`)
- [ ] Booking auto-cancels after timeout, seats released
- [ ] MoMo callback received twice → idempotent handler prevents duplicates
- [ ] M-Pesa callback failure → payment retry works
- [ ] Concurrent payment success → SELECT FOR UPDATE prevents race
- [ ] Celery beat schedule runs on time
- [ ] SMS notifications sent for timeout/failure
- [ ] Health report shows correct payment counts
- [ ] Multi-leg booking: one leg fails → all legs consistent

---

## 🐛 Common Issues & Solutions

### Issue: Stale payments not cleaning up
**Solution**: Check Celery beat is running
```bash
# Verify beat schedule
celery -A bebeto_backend inspect scheduled
```

### Issue: "Already processed" on retry callback
**Solution**: This is expected! Idempotent handler prevents re-processing
- First callback → marks COMPLETED
- Duplicate callback → logs warning, returns OK (idempotent)

### Issue: Booking expires but customer still waiting
**Solution**: Extend timeout in settings or add extend-expiry endpoint

### Issue: Seats released but payment still PENDING
**Solution**: Cleanup task will handle in next run
- Mark payment FAILED
- Cancel booking
- Release seats
- Customer can retry or book different trip

---

## 📚 Files Modified/Created

1. ✅ `payments/management/commands/cleanup_stale_payments.py` - NEW
2. ✅ `payments/tasks_cleanup.py` - NEW (Celery tasks)
3. ✅ `payments/idempotent_handlers.py` - NEW
4. ✅ `bebeto_backend/booking_settings.py` - NEW
5. ✅ `bebeto_backend/celery_schedule.py` - NEW (beat schedule)
6. ✅ `bookings/serializers.py` - MODIFIED (configurable expiry)
7. ✅ `payments/views.py` - MODIFIED (idempotent MoMo handler)
8. ✅ `payments/payment_retry_endpoint.py` - NEW

---

## 💡 Key Takeaways

1. **Timeout as First-Class Citizen**: Payments have explicit timeout handling, not just "eventually expire"
2. **Idempotent by Default**: All state transitions are idempotent, safe to retry
3. **Atomic & Locked**: Payment success uses pessimistic locking to prevent races
4. **Observable**: Health reports and monitoring tasks track payment health
5. **User-Friendly**: Customers can retry failed payments without staff
6. **Configurable**: Timeouts can be adjusted per payment method

---

## 🔄 Booking State Transitions (Improved)

```
PENDING (2-15 min depending on payment method)
    ↓
    [Payment Success] → CONFIRMED
    [Payment Timeout] → CANCELLED (auto via cleanup task)
    [Payment Failed] → User can RETRY or CANCEL manually
    [Booking Expiry] → CANCELLED (auto via ViewSet.initial())
    
CONFIRMED
    ↓
    [User Cancels] → CANCELLED (with refund calc)
    [User Reschedules] → RESCHEDULED
    [Trip Departs] → COMPLETED
```

---

## 📞 Support

For issues or questions:
1. Check the management command: `python manage.py cleanup_stale_payments --help`
2. Review Celery logs: `docker logs celery_worker`
3. Check payment health: `python manage.py shell` → `generate_payment_health_report()`
4. Monitor MoMo transactions: `MoMoTransaction.objects.filter(momo_status="PENDING")`
