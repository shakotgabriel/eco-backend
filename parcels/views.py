import logging

from bebeto_backend.permissions import IsStaffOrAbove, PublicCreateOrStaff
from cities.models import City
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from django.http import FileResponse, Http404
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes as perm_classes, throttle_classes
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from trips.models import Trip

from bebeto_backend.throttles import ParcelTrackThrottle
from rest_framework.response import Response

from .models import Parcel, ParcelPhoto, ParcelStatusLog
from .photos import (
    MAX_PHOTOS_PER_PARCEL,
    collect_uploaded_images,
    validate_parcel_image,
)
from .serializers import (
    ConfirmPickupSerializer,
    ParcelPhotoSerializer,
    ParcelPublicCreateSerializer,
    ParcelSerializer,
    ParcelTrackSerializer,
    TransferParcelSerializer,
    UpdateParcelStatusSerializer,
)
from .tasks import notify_parcel_status_change

logger = logging.getLogger(__name__)

STAFF_ROLES = ("ADMIN", "MANAGER", "STAFF")


class ParcelViewSet(viewsets.ModelViewSet):
    queryset = Parcel.objects.select_related(
        "trip__route__origin", "trip__route__destination", "trip__bus",
        "booking", "payment",
    ).prefetch_related("status_logs__location", "photos__uploaded_by").all()
    serializer_class = ParcelSerializer
    permission_classes = [PublicCreateOrStaff]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["trip", "status", "size", "booking"]
    search_fields = ["tracking_number", "sender_name", "receiver_name", "sender_phone", "receiver_phone", "description"]
    ordering_fields = ["created_at", "shipping_fee", "weight_kg"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "create":
            user = self.request.user
            is_staff = bool(user and user.is_authenticated and user.role in STAFF_ROLES)
            if not is_staff:
                return ParcelPublicCreateSerializer
        return ParcelSerializer

    def _is_staff_user(self, user):
        return bool(user and user.is_authenticated and user.role in STAFF_ROLES)

    def _attach_photos(self, parcel, request, caption=""):
        if not self._is_staff_user(request.user):
            return []

        uploads = collect_uploaded_images(request)
        if not uploads:
            return []

        existing = parcel.photos.count()
        remaining = MAX_PHOTOS_PER_PARCEL - existing
        if remaining <= 0:
            raise ValidationError(
                {"photos": f"A parcel can have at most {MAX_PHOTOS_PER_PARCEL} photos."}
            )
        if len(uploads) > remaining:
            raise ValidationError(
                {"photos": f"Only {remaining} more photo(s) can be added to this parcel."}
            )

        created = []
        for uploaded in uploads:
            validate_parcel_image(uploaded)
            created.append(
                ParcelPhoto.objects.create(
                    parcel=parcel,
                    image=uploaded,
                    caption=caption,
                    uploaded_by=request.user,
                )
            )
        return created

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        parcel = serializer.save()
        if self._is_staff_user(request.user):
            self._attach_photos(parcel, request)
        parcel = self.get_queryset().get(pk=parcel.pk)
        output = ParcelSerializer(parcel, context={"request": request})
        headers = self.get_success_headers(output.data)
        return Response(output.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=["post"], url_path="update-status", permission_classes=[IsStaffOrAbove])
    def update_status(self, request, pk=None):
        """Staff changes parcel status with an optional note and location. Sends SMS."""
        parcel = self.get_object()
        serializer = UpdateParcelStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_status = parcel.status
        new_status = serializer.validated_data["status"]

        if old_status == new_status:
            return Response({"detail": "Status unchanged."}, status=status.HTTP_400_BAD_REQUEST)

        # Generate pickup code when parcel arrives
        if new_status == "ARRIVED" and not parcel.pickup_code:
            parcel.generate_pickup_code()

        parcel.status = new_status
        parcel.save(update_fields=["status", "updated_at"])

        # Resolve location city
        location_id = serializer.validated_data.get("location")
        location = None
        if location_id:
            try:
                location = City.objects.get(id=location_id)
            except City.DoesNotExist:
                pass

        ParcelStatusLog.objects.create(
            parcel=parcel,
            old_status=old_status,
            new_status=new_status,
            changed_by=request.user if request.user.is_authenticated else None,
            location=location,
            note=serializer.validated_data.get("note", ""),
        )

        # Send SMS notification asynchronously
        notify_parcel_status_change.delay(parcel.id, new_status)

        parcel.refresh_from_db()
        return Response(ParcelSerializer(parcel, context={"request": request}).data)

    @action(
        detail=True, methods=["post"], url_path="confirm-pickup",
        permission_classes=[AllowAny], throttle_classes=[ParcelTrackThrottle],
    )
    def confirm_pickup(self, request, pk=None):
        """
        Receiver confirms delivery by providing the 6-digit pickup code.

        AllowAny by design: the pickup code (SMS'd to the receiver only once
        the parcel is ARRIVED) is the credential here, mirroring the booking
        cancel/lookup pattern elsewhere in the API. Rate-limited to make
        brute-forcing the 6-digit code impractical.
        """
        parcel = self.get_object()
        serializer = ConfirmPickupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if parcel.status != "ARRIVED":
            return Response(
                {"detail": "Parcel must be in ARRIVED status to confirm pickup."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not parcel.pickup_code:
            return Response(
                {"detail": "No pickup code generated for this parcel."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if serializer.validated_data["pickup_code"] != parcel.pickup_code:
            return Response(
                {"detail": "Invalid pickup code."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_status = parcel.status
        parcel.status = "DELIVERED"
        parcel.picked_up_at = timezone.now()
        parcel.save(update_fields=["status", "picked_up_at", "updated_at"])

        ParcelStatusLog.objects.create(
            parcel=parcel,
            old_status=old_status,
            new_status="DELIVERED",
            changed_by=request.user if request.user.is_authenticated else None,
            note="Pickup confirmed by receiver with code.",
        )

        notify_parcel_status_change.delay(parcel.id, "DELIVERED")

        return Response({
            "detail": "Pickup confirmed. Parcel marked as DELIVERED.",
            "parcel": ParcelSerializer(parcel).data,
        })

    @action(detail=True, methods=["post"], url_path="transfer", permission_classes=[IsStaffOrAbove])
    def transfer(self, request, pk=None):
        """Transfer a parcel to a different trip (e.g. when a trip is cancelled)."""
        parcel = self.get_object()
        serializer = TransferParcelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if parcel.status in ("DELIVERED", "CANCELLED"):
            return Response(
                {"detail": "Cannot transfer a delivered or cancelled parcel."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_trip_id = serializer.validated_data["new_trip_id"]
        try:
            new_trip = Trip.objects.select_related("route__origin", "route__destination").get(id=new_trip_id)
        except Trip.DoesNotExist:
            return Response({"detail": "Trip not found."}, status=status.HTTP_404_NOT_FOUND)

        if new_trip.status in ("CANCELLED", "COMPLETED", "ARRIVED"):
            return Response(
                {"detail": "Cannot transfer to a cancelled, completed, or arrived trip."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_trip = parcel.trip
        parcel.trip = new_trip
        parcel.save(update_fields=["trip", "updated_at"])

        ParcelStatusLog.objects.create(
            parcel=parcel,
            old_status=parcel.status,
            new_status=parcel.status,
            changed_by=request.user if request.user.is_authenticated else None,
            note=f"Transferred from trip {old_trip.id} ({old_trip}) to trip {new_trip.id} ({new_trip}).",
        )

        return Response({
            "detail": f"Parcel transferred to trip {new_trip.id}.",
            "parcel": ParcelSerializer(parcel, context={"request": request}).data,
        })

    @action(
        detail=True,
        methods=["post"],
        url_path="photos",
        permission_classes=[IsStaffOrAbove],
        parser_classes=[MultiPartParser, FormParser],
    )
    def upload_photos(self, request, pk=None):
        """Staff upload one or more photos of a parcel."""
        parcel = self.get_object()
        caption = (request.data.get("caption") or "").strip()[:200]
        created = self._attach_photos(parcel, request, caption=caption)
        if not created:
            raise ValidationError({"photos": "Choose at least one photo to upload."})
        parcel = self.get_queryset().get(pk=parcel.pk)
        return Response(
            ParcelSerializer(parcel, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True,
        methods=["delete"],
        url_path=r"photos/(?P<photo_id>[0-9]+)",
        permission_classes=[IsStaffOrAbove],
    )
    def delete_photo(self, request, pk=None, photo_id=None):
        parcel = self.get_object()
        try:
            photo = parcel.photos.get(pk=photo_id)
        except ParcelPhoto.DoesNotExist as exc:
            raise Http404("Photo not found.") from exc
        photo.delete()
        parcel = self.get_queryset().get(pk=parcel.pk)
        return Response(ParcelSerializer(parcel, context={"request": request}).data)

    @action(
        detail=True,
        methods=["get"],
        url_path=r"photos/(?P<photo_id>[0-9]+)/file",
        permission_classes=[IsStaffOrAbove],
    )
    def photo_file(self, request, pk=None, photo_id=None):
        """Stream a parcel photo to authenticated staff (not a public media URL)."""
        parcel = self.get_object()
        try:
            photo = parcel.photos.get(pk=photo_id)
        except ParcelPhoto.DoesNotExist as exc:
            raise Http404("Photo not found.") from exc
        if not photo.image:
            raise Http404("Photo file missing.")
        return FileResponse(photo.image.open("rb"), content_type="image/jpeg")

    @action(detail=True, methods=["get"], url_path="receipt")
    def receipt(self, request, pk=None):
        """Generate a PDF receipt/waybill for the parcel."""
        parcel = self.get_object()
        from .pdf import generate_parcel_receipt
        pdf_buf = generate_parcel_receipt(parcel)

        from django.http import HttpResponse
        response = HttpResponse(pdf_buf.read(), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="parcel_{parcel.tracking_number}.pdf"'
        return response


@api_view(["GET"])
@perm_classes([AllowAny])
@throttle_classes([ParcelTrackThrottle])
def parcel_track(request, tracking_number):
    """Public endpoint to track a parcel by tracking number."""
    try:
        parcel = Parcel.objects.select_related(
            "trip__route__origin", "trip__route__destination"
        ).get(tracking_number=tracking_number)
    except Parcel.DoesNotExist:
        return Response({"detail": "Parcel not found."}, status=status.HTTP_404_NOT_FOUND)

    data = ParcelTrackSerializer(parcel).data
    data["history"] = list(
        parcel.status_logs.select_related("location")
        .values("old_status", "new_status", "note", "location__name", "created_at")
        .order_by("created_at")
    )
    return Response(data)
