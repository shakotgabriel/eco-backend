"""
Report generation logic — aggregates real data from bookings, payments, trips.
All monetary totals are kept per currency (never mixed across KES/UGX/SSP/RWF).
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate, TruncWeek


def _money(value) -> str:
    return str(value or Decimal("0"))


def generate_daily_sales(date_from, date_to):
    """Daily breakdown of bookings and revenue, split by currency."""
    from bookings.models import Booking
    from payments.models import Payment

    bookings_by_day = (
        Booking.objects.filter(
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        )
        .annotate(day=TruncDate("created_at"))
        .values("day", "trip__route__currency")
        .annotate(
            total_bookings=Count("id"),
            confirmed=Count("id", filter=Q(status="CONFIRMED")),
            cancelled=Count("id", filter=Q(status="CANCELLED")),
            pending=Count("id", filter=Q(status="PENDING")),
            tickets=Sum("number_of_seats"),
            booking_revenue=Sum("total_price", filter=Q(status__in=["CONFIRMED", "COMPLETED", "RESCHEDULED"])),
        )
        .order_by("day", "trip__route__currency")
    )

    payments_by_day = (
        Payment.objects.filter(
            status="COMPLETED",
            paid_at__date__gte=date_from,
            paid_at__date__lte=date_to,
        )
        .annotate(day=TruncDate("paid_at"))
        .values("day", "currency")
        .annotate(
            revenue=Sum("amount"),
            transaction_count=Count("id"),
        )
        .order_by("day", "currency")
    )

    payments_map = {
        (str(p["day"]), p["currency"] or "UNK"): {
            "revenue": _money(p["revenue"]),
            "transaction_count": p["transaction_count"],
        }
        for p in payments_by_day
    }

    daily_data = []
    for row in bookings_by_day:
        day_str = str(row["day"])
        currency = row["trip__route__currency"] or "UNK"
        pay = payments_map.get((day_str, currency), {"revenue": "0", "transaction_count": 0})
        daily_data.append({
            "date": day_str,
            "currency": currency,
            "total_bookings": row["total_bookings"],
            "confirmed": row["confirmed"],
            "cancelled": row["cancelled"],
            "pending": row["pending"],
            "tickets": int(row["tickets"] or 0),
            "revenue": pay["revenue"] if pay["revenue"] != "0" else _money(row["booking_revenue"]),
            "transactions": pay["transaction_count"],
        })

    totals_by_currency = {}
    for d in daily_data:
        cur = d["currency"]
        bucket = totals_by_currency.setdefault(
            cur, {"currency": cur, "total_bookings": 0, "total_revenue": Decimal("0"), "total_confirmed": 0, "total_cancelled": 0, "tickets": 0}
        )
        bucket["total_bookings"] += d["total_bookings"]
        bucket["total_revenue"] += Decimal(d["revenue"])
        bucket["total_confirmed"] += d["confirmed"]
        bucket["total_cancelled"] += d["cancelled"]
        bucket["tickets"] += d["tickets"]

    return {
        "daily": daily_data,
        "totals_by_currency": [
            {**v, "total_revenue": _money(v["total_revenue"])} for v in totals_by_currency.values()
        ],
        "totals": {
            "note": "Use totals_by_currency — revenue is never mixed across currencies.",
            "total_bookings": sum(d["total_bookings"] for d in daily_data),
            "total_confirmed": sum(d["confirmed"] for d in daily_data),
            "total_cancelled": sum(d["cancelled"] for d in daily_data),
        },
    }


def generate_weekly_sales(date_from, date_to):
    """Weekly breakdown of bookings and revenue, split by currency."""
    from bookings.models import Booking

    rows = (
        Booking.objects.filter(
            status__in=["CONFIRMED", "COMPLETED", "RESCHEDULED"],
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        )
        .annotate(week=TruncWeek("created_at"))
        .values("week", "trip__route__currency", "trip__route__origin__country")
        .annotate(
            tickets=Sum("number_of_seats"),
            bookings=Count("id"),
            revenue=Sum("total_price"),
        )
        .order_by("week", "trip__route__currency")
    )

    weekly = []
    for row in rows:
        week = row["week"]
        label = week.date().isoformat() if hasattr(week, "date") else str(week)[:10]
        weekly.append({
            "week_start": label,
            "currency": row["trip__route__currency"] or "UNK",
            "country": row["trip__route__origin__country"] or "",
            "tickets": int(row["tickets"] or 0),
            "bookings": int(row["bookings"] or 0),
            "revenue": _money(row["revenue"]),
        })

    return {"weekly": weekly}


def generate_trip_summary(date_from, date_to):
    """Summary of trips in the date range with occupancy and revenue."""
    from trips.models import Trip

    trips = (
        Trip.objects.filter(
            departure_time__date__gte=date_from,
            departure_time__date__lte=date_to,
        )
        .select_related("route__origin", "route__destination", "bus")
        .annotate(
            booking_count=Count("bookings"),
            confirmed_bookings=Count("bookings", filter=Q(bookings__status="CONFIRMED")),
            revenue=Sum(
                "bookings__total_price",
                filter=Q(bookings__status__in=["CONFIRMED", "COMPLETED"]),
            ),
        )
        .order_by("departure_time")
    )

    trip_data = []
    for t in trips:
        capacity = t.bus.capacity if t.bus else 0
        booked = t.booking_count
        occupancy = round((booked / capacity * 100), 1) if capacity else 0

        trip_data.append({
            "trip_id": t.id,
            "route": f"{t.route.origin.name} -> {t.route.destination.name}",
            "currency": t.route.currency,
            "bus": str(t.bus),
            "departure": t.departure_time.isoformat(),
            "status": t.status,
            "capacity": capacity,
            "booked_seats": booked,
            "occupancy_pct": occupancy,
            "revenue": str(t.revenue or 0),
        })

    return {
        "trips": trip_data,
        "total_trips": len(trip_data),
        "avg_occupancy": (
            round(sum(t["occupancy_pct"] for t in trip_data) / len(trip_data), 1)
            if trip_data else 0
        ),
    }


def generate_route_performance(date_from, date_to):
    """Aggregate performance by route (native route currency)."""
    from routes.models import Route

    routes = (
        Route.objects.filter(is_active=True)
        .select_related("origin", "destination")
        .annotate(
            trip_count=Count(
                "trips",
                filter=Q(
                    trips__departure_time__date__gte=date_from,
                    trips__departure_time__date__lte=date_to,
                ),
            ),
            booking_count=Count(
                "trips__bookings",
                filter=Q(
                    trips__departure_time__date__gte=date_from,
                    trips__departure_time__date__lte=date_to,
                    trips__bookings__status__in=["CONFIRMED", "COMPLETED"],
                ),
            ),
            total_revenue=Sum(
                "trips__bookings__total_price",
                filter=Q(
                    trips__departure_time__date__gte=date_from,
                    trips__departure_time__date__lte=date_to,
                    trips__bookings__status__in=["CONFIRMED", "COMPLETED"],
                ),
            ),
        )
        .order_by("-total_revenue")
    )

    route_data = []
    for r in routes:
        route_data.append({
            "route_id": r.id,
            "route": f"{r.origin.name} -> {r.destination.name}",
            "country": r.origin.country,
            "currency": r.currency,
            "trips": r.trip_count,
            "bookings": r.booking_count,
            "revenue": str(r.total_revenue or 0),
            "avg_revenue_per_trip": (
                str(round(r.total_revenue / r.trip_count, 2))
                if r.trip_count and r.total_revenue else "0"
            ),
        })

    return {"routes": route_data}


def generate_monthly_revenue(date_from, date_to):
    """Month-by-month revenue breakdown per currency."""
    from django.db.models.functions import TruncMonth
    from payments.models import Payment

    monthly = (
        Payment.objects.filter(
            status="COMPLETED",
            paid_at__date__gte=date_from,
            paid_at__date__lte=date_to,
        )
        .annotate(month=TruncMonth("paid_at"))
        .values("month", "currency")
        .annotate(
            revenue=Sum("amount"),
            transactions=Count("id"),
        )
        .order_by("month", "currency")
    )

    monthly_data = [
        {
            "month": str(m["month"].date()),
            "currency": m["currency"] or "UNK",
            "revenue": str(m["revenue"] or 0),
            "transactions": m["transactions"],
        }
        for m in monthly
    ]

    by_currency = {}
    for m in monthly_data:
        by_currency[m["currency"]] = by_currency.get(m["currency"], Decimal("0")) + Decimal(m["revenue"])

    return {
        "monthly": monthly_data,
        "totals_by_currency": [
            {"currency": cur, "total_revenue": _money(total)} for cur, total in by_currency.items()
        ],
    }


GENERATORS = {
    "DAILY_SALES": generate_daily_sales,
    "WEEKLY_SALES": generate_weekly_sales,
    "TRIP_SUMMARY": generate_trip_summary,
    "ROUTE_PERFORMANCE": generate_route_performance,
    "MONTHLY_REVENUE": generate_monthly_revenue,
}
