from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class PublicBurstThrottle(AnonRateThrottle):
    """Short burst limit for anonymous users."""
    scope = "public_burst"


class PublicSustainedThrottle(AnonRateThrottle):
    """Sustained rate limit for anonymous users."""
    scope = "public_sustained"


class BookingCreateThrottle(AnonRateThrottle):
    """Stricter limit on booking creation to prevent abuse."""
    scope = "booking_create"


class StaffRateThrottle(UserRateThrottle):
    """Higher limit for authenticated staff."""
    scope = "staff"


# ─── Per-endpoint throttles ───────────────────────────────────────────────

class TripSearchThrottle(AnonRateThrottle):
    """Limit trip search queries for anonymous users."""
    scope = "trip_search"


class PaymentCallbackThrottle(AnonRateThrottle):
    """Limit on payment callback webhook to prevent replay attacks."""
    scope = "payment_callback"


class PasswordResetThrottle(AnonRateThrottle):
    """Prevent brute-force password reset requests."""
    scope = "password_reset"


class ParcelTrackThrottle(AnonRateThrottle):
    """Limit public parcel tracking lookups."""
    scope = "parcel_track"


class BookingLookupThrottle(AnonRateThrottle):
    """Strict limit on PNR lookup to prevent booking reference enumeration."""
    scope = "booking_lookup"


class PNRBurstThrottle(AnonRateThrottle):
    """Very strict burst limit on PNR/booking lookup — 5 attempts per minute."""
    scope = "pnr_burst"
