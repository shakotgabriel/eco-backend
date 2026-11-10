import django_filters

from .models import Trip


class TripFilter(django_filters.FilterSet):
    """Advanced filtering for trip search."""
    # Date range
    departure_date = django_filters.DateFilter(field_name="departure_time", lookup_expr="date")
    departure_from = django_filters.DateFilter(field_name="departure_time", lookup_expr="date__gte")
    departure_to = django_filters.DateFilter(field_name="departure_time", lookup_expr="date__lte")

    # Price range
    min_price = django_filters.NumberFilter(field_name="regular_price", lookup_expr="gte")
    max_price = django_filters.NumberFilter(field_name="regular_price", lookup_expr="lte")

    # Bus type
    bus_type = django_filters.CharFilter(field_name="bus__bus_type", lookup_expr="exact")

    # Seat availability
    min_seats = django_filters.NumberFilter(field_name="available_seats", lookup_expr="gte")

    # Route shortcuts
    origin = django_filters.NumberFilter(field_name="route__origin")
    destination = django_filters.NumberFilter(field_name="route__destination")
    origin_name = django_filters.CharFilter(field_name="route__origin__name", lookup_expr="icontains")
    destination_name = django_filters.CharFilter(field_name="route__destination__name", lookup_expr="icontains")

    class Meta:
        model = Trip
        fields = [
            "route", "bus", "status",
            "departure_date", "departure_from", "departure_to",
            "min_price", "max_price", "bus_type", "min_seats",
            "origin", "destination", "origin_name", "destination_name",
        ]
