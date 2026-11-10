from bebeto_backend.permissions import IsStaffOrAbove
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, viewsets

from .models import Seat
from .serializers import SeatSerializer


class SeatViewSet(viewsets.ModelViewSet):
    queryset = Seat.objects.select_related("bus").all()
    serializer_class = SeatSerializer
    permission_classes = [IsStaffOrAbove]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["bus", "seat_type", "is_active"]
    search_fields = ["seat_number"]
    ordering_fields = ["seat_number", "seat_type"]
    ordering = ["bus", "seat_number"]
