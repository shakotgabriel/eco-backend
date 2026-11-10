from django.db.models import Max, Subquery, OuterRef
from django.views.decorators.cache import cache_page
from django.utils.decorators import method_decorator
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from bebeto_backend.permissions import IsManagerOrAbove

from .models import ExchangeRate
from .serializers import ExchangeRateSerializer


class ExchangeRateViewSet(viewsets.ModelViewSet):
    queryset = ExchangeRate.objects.all()
    serializer_class = ExchangeRateSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["from_currency", "to_currency"]
    ordering_fields = ["effective_date", "from_currency"]
    ordering = ["-effective_date"]

    def get_permissions(self):
        if self.action in ("list", "retrieve", "latest"):
            return [AllowAny()]
        return [IsManagerOrAbove()]

    def perform_create(self, serializer):
        serializer.save(updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @method_decorator(cache_page(60 * 5))  # Cache for 5 minutes
    @action(detail=False, methods=["get"], permission_classes=[AllowAny])
    def latest(self, request):
        """
        Returns the most recent rate for each (from_currency, to_currency) pair.
        This is the endpoint the frontend should call.
        Results are cached for 5 minutes to reduce API load.
        """
        # Subquery to get the max effective_date per currency pair
        latest_dates = (
            ExchangeRate.objects
            .values("from_currency", "to_currency")
            .annotate(max_date=Max("effective_date"))
        )

        # Build filter for the latest rows
        from django.db.models import Q
        q = Q()
        for entry in latest_dates:
            q |= Q(
                from_currency=entry["from_currency"],
                to_currency=entry["to_currency"],
                effective_date=entry["max_date"],
            )

        if not q:
            return Response([])

        rates = ExchangeRate.objects.filter(q).select_related("updated_by")
        serializer = ExchangeRateSerializer(rates, many=True)
        return Response(serializer.data)
