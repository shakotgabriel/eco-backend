from bebeto_backend.permissions import IsStaffOrReadOnly
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, viewsets

from .models import City
from .serializers import CitySerializer


class CityViewSet(viewsets.ModelViewSet):
    queryset = City.objects.all()
    serializer_class = CitySerializer
    permission_classes = [IsStaffOrReadOnly]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["country", "is_active"]
    search_fields = ["name", "code", "country"]
    ordering_fields = ["name", "code"]
    ordering = ["name"]
