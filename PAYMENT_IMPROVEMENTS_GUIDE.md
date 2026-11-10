# Payment System Improvements Implementation Guide

This document describes the 9 payment system improvements that have been implemented to enhance reliability, prevent fraud, and improve observability.

## Summary of Improvements

All 9 improvements have been fully implemented and integrated into the payment system:

### ✅ #1: Idempotent Payment Callbacks
**Problem**: Duplicate webhook callbacks could process payments twice (double-charging).

**Solution**: 
- Created `CallbackIdempotencyKey` model to cache callback responses
- All three callback endpoints (MoMo, M-Pesa, Africa's Talking) now check for duplicates
- Duplicate callbacks return the cached response immediately
- Prevents double-processing even if provider retries callbacks

**Files Modified**:
- `payments/views.py` - Added idempotency checks to all callbacks
- `payments/payment_improvements.py` - Added `CallbackIdempotencyKey` model

---

### ✅ #2: Race Condition Prevention
**Problem**: Two concurrent requests could attempt to initiate payment for the same booking simultaneously, creating duplicate payments.

**Solution**:
- Added `prevent_concurrent_payment()` function using database-level locking (`select_for_update`)
- Prevents concurrent payment creation attempts for the same booking + method
- Checks in `momo_request_to_pay` and `mpesa_stk_push` endpoints

**Files Modified**:
- `payments/payment_utilities.py` - Added `prevent_concurrent_payment()` function
- `payments/views.py` - Added race condition checks to payment initiation endpoints

---

### ✅ #3: Payment Status Visibility Dashboard
**Problem**: No way for admins to see payment failures, timeouts, or retry history.

**Solution**:
- Created comprehensive `PaymentDashboardViewSet` with 8 endpoints
- Admins can view real-time payment statistics, failures, timeouts, retries
- Provider health status monitoring
- Webhook retry queue visibility
- Dead-letter queue inspection

**Dashboard Endpoints** (all under `/api/v1/payments/dashboard/`):
- `summary/` - Overall payment stats and health
- `failures/` - Failed payments with filtering
- `timeouts/` - Timed-out transactions
- `retries/` - Customer retry attempts
- `provider-health/` - Payment provider status
- `webhook-queue/` - Pending webhook retries
- `dlq/` - Dead-letter queue tasks
- `retry-webhook/` - Manually retry a webhook
- `retry-dlq-task/` - Manually retry a failed task

**Files Created**:
- `payments/payment_dashboard.py` - Dashboard viewset

---

### ✅ #4: Customer Self-Service Payment Retry
**Problem**: If payment fails, customer must create new booking. Wastes seats, creates duplicates.

**Solution**:
- Created `retry_payment()` endpoint allowing customers to retry failed payments
- New payment is created linked to original booking
- Can optionally change payment method on retry
- Tracks retry attempts in `PaymentRetryLog` model

**Endpoints**:
- `POST /api/v1/payments/retry/` - Retry a failed payment
- `GET /api/v1/payments/{payment_id}/retry-history/` - View retry history

**Files Created**:
- `payments/payment_retry_views.py` - Retry endpoints
- `payments/payment_improvements.py` - `PaymentRetryLog` model

---

### ✅ #5: Webhook Signature Verification
**Problem**: No verification that callbacks are from legitimate payment providers.

**Solution**:
- Enhanced `webhook_verification.py` with:
  - IP whitelist validation (configurable per provider)
  - HMAC-SHA256 signature verification (where supported)
  - Provider-specific verification strategies
- Already integrated into all callback endpoints

**Configuration Environment Variables**:
```bash
MTN_MOMO_ALLOWED_IPS="196.220.220.0/22"  # MTN infrastructure IPs
MPESA_ALLOWED_IPS="195.154.0.0/16"       # Safaricom infrastructure IPs
MTN_MOMO_NOTIFICATION_SECRET="..."       # For HMAC verification
```

**Files Modified**:
- `payments/webhook_verification.py` - Enhanced verification functions

---

### ✅ #6: Payment Retry Logic with Exponential Backoff
**Problem**: Failed API calls use fixed retries, could pile up on provider failures.

**Solution**:
- Created `calculate_exponential_backoff()` utility function
- Calculates delays with jitter: `delay = base * (factor ^ attempt) ± jitter`
- Used throughout the system for task retries and webhook retries
- Prevents thundering herd on provider recovery

**Configuration**:
```python
# In settings.py
PAYMENT_RETRY_DELAY_SECONDS = 60  # Base delay
WEBHOOK_RETRY_INITIAL_DELAY = 30  # Webhook base delay
```

**Backoff Formula**:
- Attempt 0: 30 sec
- Attempt 1: 60 sec (±10% jitter)
- Attempt 2: 120 sec (±10% jitter)
- Attempt 3: 240 sec (±10% jitter)
- ...capped at `max_delay_seconds` (300 sec default)

**Files Modified**:
- `payments/payment_utilities.py` - Added exponential backoff calculator

---

### ✅ #7: Dead-Letter Queue for Failed Tasks
**Problem**: Failed Celery tasks just disappear; no way to retry or debug.

**Solution**:
- Created `FailedCeleryTask` model to capture failed tasks
- Tasks that exhaust retries are stored in DLQ
- `retry_failed_celery_tasks()` periodic task retries DLQ items with exponential backoff
- Admins can view/retry stuck tasks via dashboard

**Celery Beat Task** (runs every 5 minutes):
```python
"retry-failed-celery-tasks-every-5-min": {
    "task": "payments.payment_tasks.retry_failed_celery_tasks",
    "schedule": 300.0,
}
```

**Files Created**:
- `payments/payment_improvements.py` - `FailedCeleryTask` model
- `payments/payment_tasks.py` - Retry tasks

---

### ✅ #8: Payment Provider Health Monitoring
**Problem**: No alerts when MTN/Safaricom APIs are down.

**Solution**:
- Created `PaymentProviderHealth` model to track provider status
- `monitor_payment_provider_health()` task checks providers periodically
- Marks providers as HEALTHY/DEGRADED/DOWN based on consecutive failures
- Admin dashboard shows real-time health status

**Health Check Logic**:
- MTN MoMo: Validates account holder (lightweight call)
- M-Pesa: Gets access token (lightweight call)
- Consecutive failures → DEGRADED (3+) → DOWN (5+)

**Celery Beat Task** (runs every 5 minutes):
```python
"monitor-payment-provider-health-every-5-min": {
    "task": "payments.payment_tasks.monitor_payment_provider_health",
    "schedule": 300.0,
}
```

**Configuration**:
```bash
MOMO_TEST_PHONE="256771234567"  # Test phone for health checks
```

**Files Modified**:
- `payments/payment_improvements.py` - `PaymentProviderHealth` model
- `payments/payment_tasks.py` - Health monitoring task

---

### ✅ #9: Webhook Retry for Failed Deliveries
**Problem**: If our backend is down when callback arrives, payment confirmation is lost.

**Solution**:
- Created `WebhookRetry` model to store failed webhook deliveries
- `process_pending_webhook_retries()` task retries pending webhooks
- Uses exponential backoff with configurable max attempts
- Failed webhooks automatically scheduled for retry instead of being dropped

**Workflow**:
1. Webhook delivery fails (provider receives non-2xx response)
2. Webhook stored in `WebhookRetry` with status=PENDING
3. Next retry scheduled with exponential backoff
4. Task processes retries every 2 minutes
5. After max attempts (default 5), marked as ABANDONED

**Celery Beat Task** (runs every 2 minutes):
```python
"process-pending-webhook-retries-every-2-min": {
    "task": "payments.payment_tasks.process_pending_webhook_retries",
    "schedule": 120.0,
}
```

**Configuration**:
```python
WEBHOOK_RETRY_MAX_ATTEMPTS = 5
WEBHOOK_RETRY_INITIAL_DELAY = 30  # seconds
```

**Files Created**:
- `payments/payment_improvements.py` - `WebhookRetry` model
- `payments/payment_tasks.py` - Webhook retry tasks

---

## Database Migration

All new models are included in migration `0008_payment_improvements.py`. Run:

```bash
python manage.py migrate
```

This creates:
- `payments_paymentproviderhealth`
- `payments_webhookretry`
- `payments_failedcelerytask`
- `payments_callbackidempotencykey`
- `payments_paymentretrylog`

---

## Celery Beat Configuration

The following tasks are automatically scheduled in `bebeto_backend/celery.py`:

```python
app.conf.beat_schedule = {
    # ... existing tasks ...
    
    # New payment improvement tasks
    "monitor-payment-provider-health-every-5-min": {
        "task": "payments.payment_tasks.monitor_payment_provider_health",
        "schedule": 300.0,  # 5 minutes
    },
    "process-pending-webhook-retries-every-2-min": {
        "task": "payments.payment_tasks.process_pending_webhook_retries",
        "schedule": 120.0,  # 2 minutes
    },
    "retry-failed-celery-tasks-every-5-min": {
        "task": "payments.payment_tasks.retry_failed_celery_tasks",
        "schedule": 300.0,  # 5 minutes
    },
}
```

---

## Environment Configuration

Add to `.env` file:

```bash
# Webhook signature verification
MTN_MOMO_ALLOWED_IPS="196.220.220.0/22"
MPESA_ALLOWED_IPS="195.154.0.0/16"
MTN_MOMO_NOTIFICATION_SECRET="your-secret-key"

# Payment retry configuration
PAYMENT_TIMEOUT_MINUTES=5
PAYMENT_MAX_RETRIES=3
PAYMENT_RETRY_DELAY_SECONDS=60

# Webhook retry configuration
WEBHOOK_RETRY_MAX_ATTEMPTS=5
WEBHOOK_RETRY_INITIAL_DELAY=30

# Provider health checks
MOMO_TEST_PHONE="256771234567"
```

---

## New API Endpoints

### Customer Payment Retry Endpoints
```
POST   /api/v1/payments/retry/
GET    /api/v1/payments/{payment_id}/retry-history/
```

### Admin Dashboard Endpoints
```
GET    /api/v1/payments/dashboard/summary/
GET    /api/v1/payments/dashboard/failures/
GET    /api/v1/payments/dashboard/timeouts/
GET    /api/v1/payments/dashboard/retries/
GET    /api/v1/payments/dashboard/provider-health/
GET    /api/v1/payments/dashboard/webhook-queue/
GET    /api/v1/payments/dashboard/dlq/
POST   /api/v1/payments/dashboard/retry-webhook/
POST   /api/v1/payments/dashboard/retry-dlq-task/
```

---

## Testing

A comprehensive test script is already in place:

```bash
python test_payment_cleanup.py
```

This script verifies:
- ✓ Booking expiry configuration
- ✓ Payment timeout settings
- ✓ Celery beat schedule
- ✓ Cleanup tasks callable
- ✓ Actual cleanup simulation

---

## Files Summary

### New Files Created
- `payments/payment_improvements.py` - New models
- `payments/payment_utilities.py` - Utility functions
- `payments/payment_tasks.py` - Celery tasks
- `payments/payment_retry_views.py` - Customer retry endpoints
- `payments/payment_dashboard.py` - Admin dashboard
- `payments/migrations/0008_payment_improvements.py` - Database migration
- `payments/idempotent_handlers.py` - Idempotent payment handlers (already existed, enhanced)

### Files Modified
- `payments/views.py` - Added idempotency, race condition prevention
- `payments/urls.py` - Added new endpoints
- `bebeto_backend/celery.py` - Added beat schedule tasks
- `bebeto_backend/settings.py` - Added configuration settings
- `payments/webhook_verification.py` - Enhanced verification

---

## Deployment Checklist

- [ ] Run `python manage.py migrate` to create new tables
- [ ] Set environment variables in `.env` (or platform config)
- [ ] Start Celery worker: `celery -A bebeto_backend worker -l info`
- [ ] Start Celery beat: `celery -A bebeto_backend beat -l info`
- [ ] Verify health check endpoints work
- [ ] Test payment retry endpoint with a failed payment
- [ ] Monitor admin dashboard for first 24 hours
- [ ] Verify no double-charges with idempotent callbacks

---

## Troubleshooting

### Webhook retries not processing?
- Check Celery beat is running: `celery -A bebeto_backend beat -l info`
- Check task is in beat schedule: `/api/v1/payments/dashboard/webhook-queue/`
- Check logs for `process_pending_webhook_retries` task

### Provider marked as DOWN?
- Check if provider APIs are actually down (test in provider dashboard)
- Manually trigger health check
- Check consecutive failures count in dashboard
- Wait for next scheduled health check (5 min)

### Duplicate callbacks still being processed?
- Verify `CallbackIdempotencyKey` table is populated
- Check if callback_id is being extracted correctly from provider payload
- Review callback handler logs

### Payment retries not available for customers?
- Verify payment status is FAILED
- Verify booking status is still PENDING
- Check user authentication (endpoint requires IsAuthenticated)
- Review error message in response

---

## Performance Considerations

- **Idempotency**: Minimal overhead, just DB lookup before processing
- **Race condition prevention**: Uses SELECT FOR UPDATE (rows locked for duration)
- **Health monitoring**: 2 lightweight API calls per provider every 5 min
- **Webhook retries**: Exponential backoff prevents provider overload
- **Dead-letter queue**: Runs every 5 min, processes max 50 tasks at a time

---

## Security Notes

1. **Callback Verification**: All callbacks verified by IP whitelist + optional HMAC
2. **Idempotent Keys**: Stored securely in DB, not in request params
3. **User Authorization**: Customer retry requires authenticated user
4. **Admin Dashboard**: Requires staff/admin permissions (IsStaffOrAbove)
5. **Rate Limiting**: Webhook callbacks throttled with `PaymentCallbackThrottle`

---

## Support

For questions or issues with these improvements:
1. Check the test script output for configuration validation
2. Review admin dashboard for detailed error messages
3. Check application logs for traceback details
4. Verify environment configuration is set correctly
