"""Admin dashboard data providers for staff/manager APIs."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth, TruncWeek, TruncYear
from django.utils import timezone


SOLD_BOOKING_STATUSES = ["CONFIRMED", "COMPLETED", "RESCHEDULED"]

# Country → local operating currency (never mix these in rollups).
COUNTRY_CURRENCY = {
    "Kenya": "KES",
    "Uganda": "UGX",
    "South Sudan": "SSP",
    "Rwanda": "RWF",
}


def _parse_period(period: str | None) -> tuple[str, int]:
    """Legacy day-window parser used by older dashboard endpoints."""
    normalized = (period or "7d").lower().strip()
    if normalized == "today":
        return normalized, 1
    if normalized in {"7d", "30d", "90d", "365d"}:
        return normalized, int(normalized[:-1])
    if normalized in {"this-year", "ytd", "1y"}:
        return "365d", 365
    if normalized in {"this-month", "mtd"}:
        return "30d", 30
    if normalized in {"this-quarter", "qtd"}:
        return "90d", 90
    return "7d", 7


def _resolve_sales_window(
    *,
    period: str | None = None,
    year: int | None = None,
    month: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[date, date, str]:
    """
    Resolve an inclusive sales date window.
    Priority: date_from/date_to → year(+month) → period aliases.
    """
    today = timezone.now().date()

    if date_from and date_to:
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
        if start > end:
            start, end = end, start
        return start, end, "custom"

    if year:
        if month and 1 <= month <= 12:
            start = date(year, month, 1)
            end = date(year, month, monthrange(year, month)[1])
            label = f"{year}-{month:02d}"
        else:
            start = date(year, 1, 1)
            end = date(year, 12, 31)
            label = str(year)
        if end > today:
            end = today
        if start > today:
            start = today
        return start, end, label

    normalized = (period or "7d").lower().strip()
    end = today
    if normalized == "today":
        return today, today, "today"
    if normalized in {"this-week", "7d"}:
        return today - timedelta(days=6), today, "7d"
    if normalized in {"this-month", "mtd", "30d"}:
        return date(today.year, today.month, 1), today, "mtd"
    if normalized in {"this-quarter", "qtd", "90d"}:
        q_month = ((today.month - 1) // 3) * 3 + 1
        return date(today.year, q_month, 1), today, "qtd"
    if normalized in {"this-year", "ytd", "365d", "1y"}:
        return date(today.year, 1, 1), today, "ytd"
    if normalized.endswith("d") and normalized[:-1].isdigit():
        days = int(normalized[:-1])
        return today - timedelta(days=days - 1), today, f"{days}d"
    return today - timedelta(days=6), today, "7d"


def _money(value) -> str:
    return str(value or Decimal("0"))


def _percent_change(current, previous) -> float:
    current_dec = Decimal(str(current or 0))
    previous_dec = Decimal(str(previous or 0))
    if previous_dec == 0:
        return float(100 if current_dec > 0 else 0)
    return round(float(((current_dec - previous_dec) / previous_dec) * 100), 2)


def _currency_for_country(country: str | None) -> str:
    if not country:
        return ""
    return COUNTRY_CURRENCY.get(country, "")


def _apply_sales_filters(qs, *, country=None, city_id=None, route_id=None, currency=None):
    """Apply optional geography filters to a Booking queryset."""
    if country:
        qs = qs.filter(trip__route__origin__country__iexact=country)
    if city_id:
        qs = qs.filter(trip__route__origin_id=city_id)
    if route_id:
        qs = qs.filter(trip__route_id=route_id)
    if currency:
        qs = qs.filter(trip__route__currency__iexact=currency)
    return qs


def get_revenue_trend(period: str | None = "7d"):
    """
    Revenue trend with per-currency series.
    `points` keep ticket counts only for compatibility; money lives in series_by_currency.
    """
    from bookings.models import Booking
    from payments.models import Payment

    _, days = _parse_period(period)
    now = timezone.now()
    end_date = now.date()
    start_date = end_date - timedelta(days=days - 1)

    payments_by_day_currency = (
        Payment.objects.filter(
            status="COMPLETED",
            paid_at__date__gte=start_date,
            paid_at__date__lte=end_date,
        )
        .annotate(day=TruncDate("paid_at"))
        .values("day", "currency")
        .annotate(revenue=Sum("amount"), tickets=Count("id"))
        .order_by("day", "currency")
    )

    bookings_by_day = (
        Booking.objects.filter(
            status__in=SOLD_BOOKING_STATUSES,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(tickets=Sum("number_of_seats"))
        .order_by("day")
    )

    ticket_map = {str(item["day"]): item["tickets"] or 0 for item in bookings_by_day}
    currencies = sorted({(item["currency"] or "UNK") for item in payments_by_day_currency})
    revenue_maps = {cur: {} for cur in currencies}
    for item in payments_by_day_currency:
        cur = item["currency"] or "UNK"
        revenue_maps[cur][str(item["day"])] = item["revenue"] or Decimal("0")

    points = []
    for offset in range(days):
        point_date = start_date + timedelta(days=offset)
        date_key = point_date.isoformat()
        points.append(
            {
                "date": date_key,
                "tickets": int(ticket_map.get(date_key, 0)),
                # legacy single revenue field intentionally omitted from mixed currencies
                "revenue": "0",
            }
        )

    series_by_currency = []
    totals_by_currency = []
    for cur in currencies:
        cur_points = []
        total_rev = Decimal("0")
        for offset in range(days):
            point_date = start_date + timedelta(days=offset)
            date_key = point_date.isoformat()
            rev = revenue_maps[cur].get(date_key, Decimal("0"))
            total_rev += rev
            cur_points.append({"date": date_key, "revenue": _money(rev), "tickets": 0})
        series_by_currency.append({"currency": cur, "points": cur_points})
        totals_by_currency.append({"currency": cur, "revenue": _money(total_rev), "tickets": 0})

    # Attach seat tickets to currency totals via booking route currency
    booking_currency_totals = (
        Booking.objects.filter(
            status__in=SOLD_BOOKING_STATUSES,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        .values("trip__route__currency")
        .annotate(tickets=Sum("number_of_seats"), revenue=Sum("total_price"))
    )
    ticket_by_cur = {row["trip__route__currency"] or "UNK": int(row["tickets"] or 0) for row in booking_currency_totals}
    rev_by_cur = {row["trip__route__currency"] or "UNK": row["revenue"] or Decimal("0") for row in booking_currency_totals}
    for item in totals_by_currency:
        item["tickets"] = ticket_by_cur.get(item["currency"], 0)
    # Ensure booking-only currencies appear
    known = {t["currency"] for t in totals_by_currency}
    for cur, tickets in ticket_by_cur.items():
        if cur not in known:
            totals_by_currency.append(
                {"currency": cur, "revenue": _money(rev_by_cur.get(cur)), "tickets": tickets}
            )

    return {
        "period": f"{days}d",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "points": points,
        "series_by_currency": series_by_currency,
        "totals_by_currency": totals_by_currency,
        "totals": {
            "revenue": "0",
            "tickets": sum(int(p["tickets"]) for p in points),
            "note": "Revenue is never mixed across currencies; see totals_by_currency.",
        },
    }


def get_sales_report(
    period: str | None = "7d",
    granularity: str | None = "daily",
    country: str | None = None,
    city_id: int | None = None,
    route_id: int | None = None,
    currency: str | None = None,
    year: int | None = None,
    month: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    compare: str | None = None,
):
    """
    Sales with geography filters and daily/weekly/monthly/yearly grains.
    All money amounts stay in their native route currency.
    Optional compare=yoy adds prior-year window totals + series.
    """
    from bookings.models import Booking
    from routes.models import Route

    start_date, end_date, window_label = _resolve_sales_window(
        period=period,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
    )
    grain = (granularity or "daily").lower().strip()
    if grain not in {"daily", "weekly", "monthly", "yearly"}:
        grain = "daily"

    def _build_for_window(win_start: date, win_end: date):
        base_qs = Booking.objects.filter(
            status__in=SOLD_BOOKING_STATUSES,
            created_at__date__gte=win_start,
            created_at__date__lte=win_end,
        )
        base_qs = _apply_sales_filters(
            base_qs, country=country, city_id=city_id, route_id=route_id, currency=currency
        )

        if grain == "weekly":
            trunc = TruncWeek("created_at")
        elif grain == "monthly":
            trunc = TruncMonth("created_at")
        elif grain == "yearly":
            trunc = TruncYear("created_at")
        else:
            trunc = TruncDate("created_at")

        timeline = (
            base_qs.annotate(bucket=trunc)
            .values("bucket", "trip__route__currency")
            .annotate(
                tickets=Sum("number_of_seats"),
                bookings=Count("id"),
                revenue=Sum("total_price"),
            )
            .order_by("bucket", "trip__route__currency")
        )

        def _bucket_label(value) -> str:
            if value is None:
                return ""
            if grain == "yearly":
                if hasattr(value, "year"):
                    return str(value.year)
                return str(value)[:4]
            if grain == "monthly":
                if hasattr(value, "strftime"):
                    return value.strftime("%Y-%m")
                return str(value)[:7]
            if hasattr(value, "date") and not isinstance(value, type(win_end)):
                try:
                    return value.date().isoformat()
                except Exception:
                    pass
            if hasattr(value, "isoformat"):
                return value.isoformat()[:10]
            return str(value)[:10]

        def _align_key(label: str) -> str:
            """Year-agnostic key for YoY alignment."""
            if grain == "yearly":
                return "year"
            if grain == "monthly" and len(label) >= 7:
                return label[5:7]  # MM
            if grain in {"daily", "weekly"} and len(label) >= 10:
                return label[5:10]  # MM-DD
            return label

        series_rows = []
        for row in timeline:
            label = _bucket_label(row["bucket"])
            series_rows.append(
                {
                    "period_start": label,
                    "align_key": _align_key(label),
                    "granularity": grain,
                    "currency": row["trip__route__currency"] or "UNK",
                    "tickets": int(row["tickets"] or 0),
                    "bookings": int(row["bookings"] or 0),
                    "revenue": _money(row["revenue"]),
                }
            )

        by_currency = (
            base_qs.values("trip__route__currency")
            .annotate(
                tickets=Sum("number_of_seats"),
                bookings=Count("id"),
                revenue=Sum("total_price"),
                trips=Count("trip_id", distinct=True),
            )
            .order_by("trip__route__currency")
        )
        currency_rows = [
            {
                "currency": row["trip__route__currency"] or "UNK",
                "country": next(
                    (c for c, cur in COUNTRY_CURRENCY.items() if cur == (row["trip__route__currency"] or "")),
                    "",
                ),
                "tickets": int(row["tickets"] or 0),
                "bookings": int(row["bookings"] or 0),
                "trips": int(row["trips"] or 0),
                "revenue": _money(row["revenue"]),
            }
            for row in by_currency
        ]

        by_city = (
            base_qs.values(
                "trip__route__origin__id",
                "trip__route__origin__name",
                "trip__route__origin__country",
                "trip__route__currency",
            )
            .annotate(
                tickets=Sum("number_of_seats"),
                bookings=Count("id"),
                trips=Count("trip_id", distinct=True),
                revenue=Sum("total_price"),
            )
            .order_by("-revenue")
        )
        city_rows = [
            {
                "city_id": row["trip__route__origin__id"],
                "city": row["trip__route__origin__name"] or "Unknown",
                "country": row["trip__route__origin__country"] or "",
                "currency": row["trip__route__currency"] or "UNK",
                "tickets": int(row["tickets"] or 0),
                "bookings": int(row["bookings"] or 0),
                "trips": int(row["trips"] or 0),
                "revenue": _money(row["revenue"]),
            }
            for row in by_city
        ]

        by_route = (
            base_qs.values(
                "trip__route__id",
                "trip__route__origin__name",
                "trip__route__destination__name",
                "trip__route__origin__country",
                "trip__route__currency",
            )
            .annotate(
                tickets=Sum("number_of_seats"),
                bookings=Count("id"),
                trips=Count("trip_id", distinct=True),
                revenue=Sum("total_price"),
            )
            .order_by("-revenue")
        )
        route_rows = [
            {
                "route_id": row["trip__route__id"],
                "route": f"{row['trip__route__origin__name']} -> {row['trip__route__destination__name']}",
                "country": row["trip__route__origin__country"] or "",
                "currency": row["trip__route__currency"] or "UNK",
                "tickets": int(row["tickets"] or 0),
                "bookings": int(row["bookings"] or 0),
                "trips": int(row["trips"] or 0),
                "revenue": _money(row["revenue"]),
            }
            for row in by_route
        ]

        from tickets.models import Ticket

        ticket_qs = (
            Ticket.objects.filter(
                booking__status__in=SOLD_BOOKING_STATUSES,
                booking__created_at__date__gte=win_start,
                booking__created_at__date__lte=win_end,
            )
            .select_related(
                "booking",
                "booking__user",
                "booking__trip__route__origin",
                "booking__trip__route__destination",
                "booking__agent_sale__agent__user",
            )
            .prefetch_related("booking__payments")
            .order_by("-booking__created_at", "ticket_number")
        )
        if country:
            ticket_qs = ticket_qs.filter(booking__trip__route__origin__country__iexact=country)
        if city_id:
            ticket_qs = ticket_qs.filter(booking__trip__route__origin_id=city_id)
        if route_id:
            ticket_qs = ticket_qs.filter(booking__trip__route_id=route_id)
        if currency:
            ticket_qs = ticket_qs.filter(booking__trip__route__currency__iexact=currency)

        PAYMENT_LABELS = {
            "CASH": "Cash",
            "MOBILE_MONEY": "Mobile Money",
            "MTN_MOMO": "MTN MoMo",
            "MPESA": "M-Pesa",
            "BANK_TRANSFER": "Bank",
        }

        ticket_rows = []
        for t in ticket_qs[:5000]:
            booking = t.booking
            route = booking.trip.route
            origin = route.origin
            destination = route.destination

            agent_name = "Online"
            try:
                agent_user = booking.agent_sale.agent.user
                agent_name = (agent_user.get_full_name() or agent_user.username or "").strip() or "Agent"
            except Exception:
                if booking.user_id and booking.user:
                    agent_name = (booking.user.get_full_name() or booking.user.username or "").strip() or "Staff"

            payments = list(booking.payments.all())
            completed = next((p for p in payments if p.status == "COMPLETED"), None)
            payment = completed or (payments[0] if payments else None)
            payment_method = PAYMENT_LABELS.get(payment.method, payment.method) if payment else "—"
            payment_status = "Paid" if payment and payment.status == "COMPLETED" else (
                payment.status.replace("_", " ").title() if payment else booking.status.replace("_", " ").title()
            )

            ticket_rows.append(
                {
                    "ticket_number": t.ticket_number,
                    "sale_date": booking.created_at.date().isoformat(),
                    "booking_reference": booking.booking_reference,
                    "origin": origin.name if origin else "",
                    "destination": destination.name if destination else "",
                    "route": f"{origin.name if origin else ''} → {destination.name if destination else ''}".strip(" →"),
                    "city": origin.name if origin else "",
                    "country": origin.country if origin else "",
                    "currency": route.currency or "UNK",
                    "passenger": f"{t.first_name} {t.last_name}".strip(),
                    "agent": agent_name,
                    "payment_method": payment_method,
                    "status": payment_status,
                    "trips": 1,
                    "amount": _money(t.price),
                }
            )

        return {
            "series": series_rows,
            "currency_totals": currency_rows,
            "cities": city_rows,
            "routes": route_rows,
            "ticket_rows": ticket_rows,
            "tickets": sum(c["tickets"] for c in currency_rows),
            "bookings": sum(c["bookings"] for c in currency_rows),
        }

    current = _build_for_window(start_date, end_date)

    countries = sorted(
        {
            name
            for name in Route.objects.filter(is_active=True)
            .values_list("origin__country", flat=True)
            .distinct()
            if name
        }
    )

    available_years = list(
        range(2023, timezone.now().year + 1)
    )

    result = {
        "period": window_label,
        "granularity": grain,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "filters": {
            "country": country or "",
            "city_id": city_id,
            "route_id": route_id,
            "currency": currency or "",
            "year": year,
            "month": month,
            "date_from": date_from or "",
            "date_to": date_to or "",
            "compare": (compare or "").lower().strip(),
        },
        "available_countries": [
            {"country": c, "currency": _currency_for_country(c)} for c in countries
        ],
        "available_years": available_years,
        "series": current["series"],
        "currency_totals": current["currency_totals"],
        "cities": current["cities"],
        "routes": current["routes"],
        "ticket_rows": current.get("ticket_rows", []),
        "tickets": current["tickets"],
        "bookings": current["bookings"],
        "comparison": None,
    }

    compare_mode = (compare or "").lower().strip()
    if compare_mode in {"yoy", "year_over_year", "prior_year"}:
        try:
            prior_start = date(start_date.year - 1, start_date.month, start_date.day)
        except ValueError:
            # Feb 29 → Feb 28
            prior_start = date(start_date.year - 1, start_date.month, 28)
        try:
            prior_end = date(end_date.year - 1, end_date.month, end_date.day)
        except ValueError:
            prior_end = date(end_date.year - 1, end_date.month, 28)

        prior = _build_for_window(prior_start, prior_end)
        prior_map = {row["currency"]: row for row in prior["currency_totals"]}
        deltas = []
        for row in current["currency_totals"]:
            prev = prior_map.get(row["currency"])
            cur_rev = Decimal(str(row["revenue"] or 0))
            prev_rev = Decimal(str(prev["revenue"] if prev else 0))
            cur_tickets = int(row["tickets"] or 0)
            prev_tickets = int(prev["tickets"] if prev else 0)
            deltas.append(
                {
                    "currency": row["currency"],
                    "country": row.get("country") or "",
                    "revenue": row["revenue"],
                    "prior_revenue": _money(prev_rev),
                    "revenue_change_pct": _percent_change(cur_rev, prev_rev),
                    "tickets": cur_tickets,
                    "prior_tickets": prev_tickets,
                    "tickets_change_pct": _percent_change(cur_tickets, prev_tickets),
                }
            )
        # Include prior-only currencies
        for cur, prev in prior_map.items():
            if cur not in {d["currency"] for d in deltas}:
                deltas.append(
                    {
                        "currency": cur,
                        "country": prev.get("country") or "",
                        "revenue": "0",
                        "prior_revenue": prev["revenue"],
                        "revenue_change_pct": _percent_change(0, prev["revenue"]),
                        "tickets": 0,
                        "prior_tickets": int(prev["tickets"] or 0),
                        "tickets_change_pct": _percent_change(0, prev["tickets"]),
                    }
                )

        result["comparison"] = {
            "mode": "yoy",
            "label": f"{prior_start.year} vs {start_date.year}",
            "start_date": prior_start.isoformat(),
            "end_date": prior_end.isoformat(),
            "series": prior["series"],
            "currency_totals": prior["currency_totals"],
            "deltas": deltas,
            "tickets": prior["tickets"],
            "bookings": prior["bookings"],
            "tickets_change_pct": _percent_change(current["tickets"], prior["tickets"]),
            "bookings_change_pct": _percent_change(current["bookings"], prior["bookings"]),
        }

    return result


def get_route_performance(period: str | None = "30d", limit: int = 10):
    from routes.models import Route

    _, days = _parse_period(period)
    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days - 1)

    routes = (
        Route.objects.filter(is_active=True)
        .select_related("origin", "destination")
        .annotate(
            tickets=Sum(
                "trips__bookings__number_of_seats",
                filter=Q(
                    trips__bookings__status__in=SOLD_BOOKING_STATUSES,
                    trips__departure_time__date__gte=start_date,
                    trips__departure_time__date__lte=end_date,
                ),
            ),
            revenue=Sum(
                "trips__bookings__total_price",
                filter=Q(
                    trips__bookings__status__in=SOLD_BOOKING_STATUSES,
                    trips__departure_time__date__gte=start_date,
                    trips__departure_time__date__lte=end_date,
                ),
            ),
            trip_count=Count(
                "trips",
                filter=Q(
                    trips__departure_time__date__gte=start_date,
                    trips__departure_time__date__lte=end_date,
                ),
                distinct=True,
            ),
        )
        .order_by("-revenue")[:limit]
    )

    data = []
    for route in routes:
        data.append(
            {
                "route_id": route.id,
                "route": f"{route.origin.name} -> {route.destination.name}",
                "currency": route.currency,
                "tickets": int(route.tickets or 0),
                "revenue": _money(route.revenue),
                "trips": int(route.trip_count or 0),
            }
        )

    return {
        "period": f"{days}d",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "routes": data,
    }


def get_agent_leaderboard(period: str | None = "7d", limit: int = 5):
    from agents.models import AgentSale

    _, days = _parse_period(period)
    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days - 1)

    top_agents = (
        AgentSale.objects.filter(created_at__date__gte=start_date, created_at__date__lte=end_date)
        .select_related("agent__user", "agent__city")
        .values(
            "agent_id",
            "agent__agent_code",
            "agent__user__username",
            "agent__user__first_name",
            "agent__user__last_name",
            "agent__city__name",
            "agent__city__country",
            "booking__trip__route__currency",
        )
        .annotate(
            tickets=Sum("booking__number_of_seats"),
            revenue=Sum("booking__total_price"),
        )
        .order_by("-tickets", "-revenue")[:limit]
    )

    results = []
    for item in top_agents:
        full_name = f"{(item['agent__user__first_name'] or '').strip()} {(item['agent__user__last_name'] or '').strip()}".strip()
        country = item["agent__city__country"] or ""
        currency = item["booking__trip__route__currency"] or _currency_for_country(country) or "UNK"
        results.append(
            {
                "agent_id": item["agent_id"],
                "agent_code": item["agent__agent_code"],
                "name": full_name or item["agent__user__username"],
                "location": item["agent__city__name"] or "-",
                "country": country,
                "currency": currency,
                "tickets": int(item["tickets"] or 0),
                "revenue": _money(item["revenue"]),
            }
        )

    return {
        "period": f"{days}d",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "agents": results,
    }


def get_city_performance(period: str | None = "30d", limit: int = 20):
    """Revenue and tickets aggregated by origin city (international ops view)."""
    from bookings.models import Booking

    _, days = _parse_period(period)
    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days - 1)

    rows = (
        Booking.objects.filter(
            status__in=SOLD_BOOKING_STATUSES,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        .values(
            "trip__route__origin__id",
            "trip__route__origin__name",
            "trip__route__origin__country",
            "trip__route__currency",
        )
        .annotate(
            tickets=Sum("number_of_seats"),
            revenue=Sum("total_price"),
            bookings=Count("id"),
            trips=Count("trip_id", distinct=True),
        )
        .order_by("-revenue")[:limit]
    )

    cities = []
    for row in rows:
        cities.append(
            {
                "city_id": row["trip__route__origin__id"],
                "city": row["trip__route__origin__name"] or "Unknown",
                "country": row["trip__route__origin__country"] or "",
                "currency": row["trip__route__currency"] or "KES",
                "tickets": int(row["tickets"] or 0),
                "bookings": int(row["bookings"] or 0),
                "trips": int(row["trips"] or 0),
                "revenue": _money(row["revenue"]),
            }
        )

    return {
        "period": f"{days}d",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "cities": cities,
    }


def get_manager_performance(period: str | None = "30d", limit: int = 20):
    """
    Operational performance for MANAGER users.
    Matches managers to cities via username/email (e.g. manager.nairobi)
    and attributes agent sales + audit activity for the period.
    """
    from accounts.models import User
    from agents.models import AgentSale
    from cities.models import City
    from reports.audit import AuditLog

    _, days = _parse_period(period)
    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days - 1)

    managers = list(
        User.objects.filter(role="MANAGER", is_active=True).order_by("first_name", "username")[:limit]
    )
    cities = list(City.objects.filter(is_active=True).values("id", "name", "country", "code"))

    def match_city(manager: User):
        haystack = f"{manager.username} {manager.email} {manager.first_name} {manager.last_name}".lower()
        for city in cities:
            name = (city["name"] or "").lower()
            code = (city["code"] or "").lower()
            if name and name in haystack:
                return city
            if code and len(code) >= 3 and code in haystack:
                return city
        return None

    results = []
    for manager in managers:
        city = match_city(manager)
        sales_qs = AgentSale.objects.filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        if city:
            sales_qs = sales_qs.filter(agent__city_id=city["id"])
        else:
            sales_qs = sales_qs.none()

        sales = sales_qs.aggregate(
            tickets=Sum("booking__number_of_seats"),
            revenue=Sum("booking__total_price"),
            sales_count=Count("id"),
        )
        audit_count = AuditLog.objects.filter(
            actor=manager,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        ).count()
        agent_count = 0
        if city:
            from agents.models import Agent

            agent_count = Agent.objects.filter(city_id=city["id"], status="ACTIVE").count()

        country = city["country"] if city else ""
        currency = _currency_for_country(country) or "UNK"
        # Prefer currency actually used on attributed sales when available
        sale_currency = (
            sales_qs.values_list("booking__trip__route__currency", flat=True).first()
            if city
            else None
        )
        if sale_currency:
            currency = sale_currency

        full_name = manager.get_full_name() or manager.username
        results.append(
            {
                "manager_id": manager.id,
                "name": full_name,
                "email": manager.email,
                "username": manager.username,
                "region": city["name"] if city else "Unassigned",
                "country": country,
                "currency": currency,
                "agents": agent_count,
                "tickets": int(sales["tickets"] or 0),
                "sales_count": int(sales["sales_count"] or 0),
                "revenue": _money(sales["revenue"]),
                "audit_actions": audit_count,
            }
        )

    results.sort(key=lambda item: Decimal(item["revenue"]), reverse=True)
    return {
        "period": f"{days}d",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "managers": results,
    }


def get_fleet_status(limit: int = 6):
    from buses.models import Bus
    from trips.models import Trip

    now = timezone.now()
    active_trips = (
        Trip.objects.filter(
            departure_time__lte=now,
            arrival_time__gte=now,
            status__in=["BOARDING", "DEPARTED"],
        )
        .select_related("route__origin", "route__destination", "bus", "driver")
        .order_by("departure_time")
    )

    on_route_bus_ids = {trip.bus_id for trip in active_trips}

    total_buses = Bus.objects.count()
    maintenance_count = Bus.objects.filter(status="MAINTENANCE").count()
    retired_count = Bus.objects.filter(status="RETIRED").count()
    on_route_count = len(on_route_bus_ids)
    available_count = max(total_buses - maintenance_count - retired_count - on_route_count, 0)

    active_buses = []
    for trip in active_trips[:limit]:
        total_seconds = (trip.arrival_time - trip.departure_time).total_seconds()
        elapsed_seconds = (now - trip.departure_time).total_seconds()
        progress = int(max(0, min(100, (elapsed_seconds / total_seconds) * 100))) if total_seconds > 0 else 0
        driver_name = "-"
        if trip.driver:
            driver_name = trip.driver.get_full_name() or trip.driver.username

        active_buses.append(
            {
                "trip_id": trip.id,
                "bus_id": trip.bus_id,
                "plate_number": trip.bus.plate_number,
                "bus_type": trip.bus.get_bus_type_display(),
                "route": f"{trip.route.origin.name} -> {trip.route.destination.name}",
                "driver": driver_name,
                "status": trip.status,
                "progress": progress,
            }
        )

    return {
        "overview": {
            "total": total_buses,
            "on_route": on_route_count,
            "available": available_count,
            "maintenance": maintenance_count,
            "retired": retired_count,
        },
        "active_buses": active_buses,
    }


def get_recent_transactions(
    limit: int = 15,
    for_user=None,
    *,
    page: int = 1,
    search: str = "",
    status: str = "",
    method: str = "",
    date_from=None,
    date_to=None,
):
    from django.db.models import Q

    from payments.models import Payment
    from reports.audit import AuditLog

    payments = Payment.objects.select_related(
        "booking__trip__route__origin",
        "booking__trip__route__destination",
        "booking__trip__bus",
        "booking__user",
        "booking__agent_sale__agent__city",
        "booking__agent_sale__agent__user",
    ).order_by("-created_at")

    # Staff / agents only see sales they made (desk attribution, agent ledger, or cash confirm audit).
    if for_user is not None:
        audited_ids = []
        for tid in AuditLog.objects.filter(
            actor=for_user,
            action="PAYMENT_COMPLETED",
        ).values_list("target_id", flat=True)[:1000]:
            try:
                audited_ids.append(int(tid))
            except (TypeError, ValueError):
                continue

        payments = payments.filter(
            Q(booking__user=for_user)
            | Q(booking__agent_sale__agent__user=for_user)
            | Q(booking_id__in=audited_ids)
        ).distinct()

    search = (search or "").strip()
    if search:
        payments = payments.filter(
            Q(booking__booking_reference__icontains=search)
            | Q(booking__payer_name__icontains=search)
            | Q(booking__payment_phone__icontains=search.lstrip("0+"))
            | Q(booking__trip__route__origin__name__icontains=search)
            | Q(booking__trip__route__destination__name__icontains=search)
        )
    if status:
        payments = payments.filter(status=status.upper())
    if method:
        payments = payments.filter(method=method.upper())
    if date_from:
        payments = payments.filter(booking__trip__departure_time__date__gte=date_from)
    if date_to:
        payments = payments.filter(booking__trip__departure_time__date__lte=date_to)

    total = payments.count()
    page = max(1, page)
    offset = (page - 1) * limit
    payments = payments[offset:offset + limit]

    results = []
    for payment in payments:
        booking = payment.booking
        trip = getattr(booking, "trip", None)
        route_label = "-"
        travel_class = "-"
        departure_time = None

        if trip and trip.route:
            route_label = f"{trip.route.origin.name} -> {trip.route.destination.name}"
            departure_time = trip.departure_time.isoformat()

        if trip and trip.bus:
            travel_class = trip.bus.get_bus_type_display()

        agent_name = "-"
        if hasattr(booking, "agent_sale") and booking.agent_sale and booking.agent_sale.agent:
            agent_user = booking.agent_sale.agent.user
            agent_name = agent_user.get_full_name() or agent_user.username
        elif booking.user_id:
            agent_name = booking.user.get_full_name() or booking.user.username

        results.append(
            {
                "payment_id": payment.id,
                "booking_id": booking.id,
                "booking_reference": booking.booking_reference,
                "customer": booking.payer_name,
                "phone": str(booking.payment_phone),
                "route": route_label,
                "travel_class": travel_class,
                "departure_time": departure_time,
                "amount": _money(payment.amount),
                "currency": payment.currency,
                "payment_method": payment.method,
                "status": payment.status,
                "seats": booking.number_of_seats,
                "agent": agent_name,
                "created_at": payment.created_at.isoformat(),
            }
        )

    return {
        "limit": limit,
        "page": page,
        "count": len(results),
        "total": total,
        "has_more": offset + len(results) < total,
        "results": results,
        "scoped_to_self": for_user is not None,
    }


def _get_parcel_stats(start_date, end_date):
    """Parcel revenue, volume by route, and avg delivery time."""
    from parcels.models import Parcel

    parcels_period = Parcel.objects.filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    )

    totals = parcels_period.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status="PENDING")),
        in_transit=Count("id", filter=Q(status="IN_TRANSIT")),
        arrived=Count("id", filter=Q(status="ARRIVED")),
        delivered=Count("id", filter=Q(status="DELIVERED")),
        cancelled=Count("id", filter=Q(status="CANCELLED")),
        revenue=Sum("shipping_fee"),
    )

    # Volume by route (top 5)
    by_route = (
        parcels_period
        .values(
            "trip__route__id",
            "trip__route__origin__name",
            "trip__route__destination__name",
        )
        .annotate(
            count=Count("id"),
            revenue=Sum("shipping_fee"),
        )
        .order_by("-count")[:5]
    )
    route_data = [
        {
            "route": f"{r['trip__route__origin__name']} → {r['trip__route__destination__name']}",
            "count": r["count"],
            "revenue": _money(r["revenue"]),
        }
        for r in by_route
    ]

    # Avg delivery time (created_at → picked_up_at for DELIVERED parcels)
    from django.db.models import Avg, F
    avg_delivery = (
        Parcel.objects.filter(
            status="DELIVERED",
            picked_up_at__isnull=False,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        .annotate(delivery_time=F("picked_up_at") - F("created_at"))
        .aggregate(avg=Avg("delivery_time"))
    )
    avg_hours = None
    if avg_delivery["avg"]:
        avg_hours = round(avg_delivery["avg"].total_seconds() / 3600, 1)

    return {
        "totals": {
            "total": totals["total"] or 0,
            "pending": totals["pending"] or 0,
            "in_transit": totals["in_transit"] or 0,
            "arrived": totals["arrived"] or 0,
            "delivered": totals["delivered"] or 0,
            "cancelled": totals["cancelled"] or 0,
            "revenue": _money(totals["revenue"]),
        },
        "by_route": route_data,
        "avg_delivery_hours": avg_hours,
    }


def get_dashboard_stats(period: str | None = "7d"):
    """
    Backward-compatible dashboard payload used by /api/v1/reports/dashboard/.
    Now includes richer sections for admin UI integration.
    """
    from agents.models import Agent
    from bookings.models import Booking
    from buses.models import Bus
    from payments.models import Payment
    from routes.models import Route
    from trips.models import Trip

    normalized_period, days = _parse_period(period)
    now = timezone.now()
    today = now.date()
    yesterday = today - timedelta(days=1)
    start_date = today - timedelta(days=days - 1)

    today_bookings = Booking.objects.filter(created_at__date=today)
    today_counts = today_bookings.aggregate(
        total=Count("id"),
        confirmed=Count("id", filter=Q(status="CONFIRMED")),
        pending=Count("id", filter=Q(status="PENDING")),
        cancelled=Count("id", filter=Q(status="CANCELLED")),
    )

    # Never mix currencies into a single revenue number.
    today_revenue_by_currency = list(
        Payment.objects.filter(status="COMPLETED", paid_at__date=today)
        .values("currency")
        .annotate(total=Sum("amount"))
        .order_by("currency")
    )
    yesterday_revenue_by_currency = {
        row["currency"]: row["total"] or Decimal("0")
        for row in Payment.objects.filter(status="COMPLETED", paid_at__date=yesterday)
        .values("currency")
        .annotate(total=Sum("amount"))
    }
    week_revenue_by_currency = list(
        Payment.objects.filter(status="COMPLETED", paid_at__date__gte=start_date, paid_at__date__lte=today)
        .values("currency")
        .annotate(total=Sum("amount"))
        .order_by("currency")
    )
    # Legacy scalar fields kept as first currency for older clients (prefer empty over mixed sum).
    today_revenue = today_revenue_by_currency[0]["total"] if today_revenue_by_currency else Decimal("0")
    yesterday_revenue = (
        yesterday_revenue_by_currency.get(today_revenue_by_currency[0]["currency"], Decimal("0"))
        if today_revenue_by_currency
        else Decimal("0")
    )
    week_revenue = week_revenue_by_currency[0]["total"] if week_revenue_by_currency else Decimal("0")

    tickets_today = (
        Booking.objects.filter(status__in=SOLD_BOOKING_STATUSES, created_at__date=today)
        .aggregate(total=Sum("number_of_seats"))["total"]
        or 0
    )
    tickets_yesterday = (
        Booking.objects.filter(status__in=SOLD_BOOKING_STATUSES, created_at__date=yesterday)
        .aggregate(total=Sum("number_of_seats"))["total"]
        or 0
    )

    today_trips = Trip.objects.filter(departure_time__date=today)
    trip_counts = today_trips.aggregate(
        total=Count("id"),
        scheduled=Count("id", filter=Q(status="SCHEDULED")),
        boarding=Count("id", filter=Q(status="BOARDING")),
        departed=Count("id", filter=Q(status="DEPARTED")),
        arrived=Count("id", filter=Q(status__in=["ARRIVED", "COMPLETED"])),
        cancelled=Count("id", filter=Q(status="CANCELLED")),
    )

    upcoming = (
        Trip.objects.filter(
            departure_time__gte=now,
            departure_time__lte=now + timedelta(hours=6),
            status__in=["SCHEDULED", "BOARDING"],
        )
        .select_related("route__origin", "route__destination", "bus")
        .order_by("departure_time")[:10]
    )

    upcoming_data = [
        {
            "trip_id": trip.id,
            "route": f"{trip.route.origin.name} -> {trip.route.destination.name}",
            "bus": str(trip.bus),
            "departure": trip.departure_time.isoformat(),
            "status": trip.status,
            "available_seats": trip.available_seats,
        }
        for trip in upcoming
    ]

    pending_payments = Payment.objects.filter(status="PENDING").count()
    active_agents_count = Agent.objects.filter(status="ACTIVE").count()
    total_agents_count = Agent.objects.count()
    on_route_buses_count = Trip.objects.filter(
        departure_time__lte=now,
        arrival_time__gte=now,
        status__in=["BOARDING", "DEPARTED"],
    ).values("bus_id").distinct().count()
    total_buses_count = Bus.objects.count()

    payment_distribution_qs = (
        Payment.objects.filter(
            status="COMPLETED",
            paid_at__date__gte=start_date,
            paid_at__date__lte=today,
        )
        .values("method")
        .annotate(total=Count("id"))
        .order_by("-total")
    )
    payment_total = sum(item["total"] for item in payment_distribution_qs) or 1
    payment_distribution = [
        {
            "method": item["method"],
            "count": item["total"],
            "percent": round((item["total"] / payment_total) * 100, 2),
        }
        for item in payment_distribution_qs
    ]

    inactive_routes = (
        Route.objects.filter(is_active=False)
        .select_related("origin", "destination")
        .order_by("origin__name", "destination__name")[:5]
    )
    upcoming_routes = [
        {
            "route_id": route.id,
            "from": route.origin.name,
            "to": route.destination.name,
            "distance_km": str(route.distance_km) if route.distance_km is not None else None,
            "estimated_duration": str(route.estimated_duration),
            "currency": route.currency,
            "is_active": route.is_active,
        }
        for route in inactive_routes
    ]

    # Parcel stats
    parcel_stats = _get_parcel_stats(start_date, today)

    return {
        "period": normalized_period,
        "today": {
            "bookings": today_counts,
            "revenue": _money(today_revenue),
            "revenue_by_currency": [
                {"currency": row["currency"] or "UNK", "revenue": _money(row["total"])}
                for row in today_revenue_by_currency
            ],
            "trips": trip_counts,
        },
        "week_revenue": _money(week_revenue),
        "week_revenue_by_currency": [
            {"currency": row["currency"] or "UNK", "revenue": _money(row["total"])}
            for row in week_revenue_by_currency
        ],
        "pending_payments": pending_payments,
        "upcoming_departures": upcoming_data,
        "parcels": parcel_stats,
        "kpis": {
            "today_revenue": {
                "value": _money(today_revenue),
                "change_pct": _percent_change(today_revenue, yesterday_revenue),
                "previous": _money(yesterday_revenue),
                "by_currency": [
                    {
                        "currency": row["currency"] or "UNK",
                        "value": _money(row["total"]),
                        "change_pct": _percent_change(
                            row["total"],
                            yesterday_revenue_by_currency.get(row["currency"], Decimal("0")),
                        ),
                        "previous": _money(yesterday_revenue_by_currency.get(row["currency"], Decimal("0"))),
                    }
                    for row in today_revenue_by_currency
                ],
            },
            "tickets_sold_today": {
                "value": int(tickets_today),
                "change_pct": _percent_change(tickets_today, tickets_yesterday),
                "previous": int(tickets_yesterday),
            },
            "active_agents": {
                "value": active_agents_count,
                "total": total_agents_count,
            },
            "active_buses": {
                "value": on_route_buses_count,
                "total": total_buses_count,
            },
        },
        "payment_distribution": payment_distribution,
        "revenue_trend": get_revenue_trend(normalized_period),
        "route_performance": get_route_performance(normalized_period, limit=15),
        "agent_leaderboard": get_agent_leaderboard(normalized_period, limit=15),
        "city_performance": get_city_performance(normalized_period, limit=20),
        "manager_performance": get_manager_performance(normalized_period, limit=20),
        "sales_report": get_sales_report(normalized_period, granularity="daily"),
        "fleet": get_fleet_status(limit=6),
        "recent_transactions": get_recent_transactions(limit=15),
        "upcoming_routes": upcoming_routes,
    }
