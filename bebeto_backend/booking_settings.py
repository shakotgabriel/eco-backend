"""
Add these settings to settings.py

# Booking expiry configuration
BOOKING_EXPIRY_CONFIG = {
    # Default: all payment methods get 5 minutes
    "default_minutes": 5,
    
    # Override per payment method
    "by_method": {
        "MOBILE_MONEY": 8,  # Slower callback from AT
        "MTN_MOMO": 5,      # USSD prompt typically fast
        "MPESA": 10,        # STK push may take longer on weak networks
        "CASH": 15,         # Staff has more time to process
    },
}

# Payment timeout before marking as FAILED
PAYMENT_TIMEOUT_MINUTES = 30  # Mark payments FAILED if no success after 30 min

# Payment retry configuration
PAYMENT_MAX_RETRIES = 3
PAYMENT_RETRY_DELAY_SECONDS = 60

# Idempotency key TTL (cleanup old keys after this long)
IDEMPOTENCY_KEY_TTL_HOURS = 24
