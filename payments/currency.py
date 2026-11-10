"""
Currency conversion utilities.

Exchange rates are stored in a model and cached.
Conversions are always done through USD as the base.
"""
from decimal import Decimal, ROUND_HALF_UP

from django.core.cache import cache

CACHE_KEY = "currency_rates"
CACHE_TTL = 3600  # 1 hour


def get_rates():
    """Return dict of {currency_code: rate_to_usd}. Cached for 1 hour."""
    rates = cache.get(CACHE_KEY)
    if rates is None:
        from .models import ExchangeRate
        rates = {r.currency: r.rate_to_usd for r in ExchangeRate.objects.all()}
        rates["USD"] = Decimal("1")
        cache.set(CACHE_KEY, rates, CACHE_TTL)
    return rates


def convert(amount, from_currency, to_currency):
    """
    Convert amount from one currency to another.
    Returns (converted_amount, rate_used).
    """
    if from_currency == to_currency:
        return amount, Decimal("1")

    rates = get_rates()
    from_rate = rates.get(from_currency)
    to_rate = rates.get(to_currency)

    if from_rate is None:
        raise ValueError(f"Unknown currency: {from_currency}")
    if to_rate is None:
        raise ValueError(f"Unknown currency: {to_currency}")

    # amount_in_usd = amount / from_rate
    # result = amount_in_usd * to_rate
    usd_amount = amount / from_rate
    result = (usd_amount * to_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rate_used = (to_rate / from_rate).quantize(Decimal("0.000001"))
    return result, rate_used


def invalidate_cache():
    """Call after updating ExchangeRate records."""
    cache.delete(CACHE_KEY)
