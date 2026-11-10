from rest_framework import filters, status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from bebeto_backend.permissions import IsStaffOrAbove

from .models import ContactMessage
from .serializers import (
    ContactMessageCreateSerializer,
    ContactMessageSerializer,
    ContactMessageUpdateSerializer,
)


@api_view(["POST"])
@permission_classes([AllowAny])
def submit_contact_form(request):
    """
    Submit a contact form message.

    POST /api/v1/contact/
    """
    serializer = ContactMessageCreateSerializer(data=request.data)
    if serializer.is_valid():
        contact = serializer.save()

        try:
            from django.core.mail import send_mail
            from django.conf import settings

            admin_email = getattr(settings, "CONTACT_FORM_ADMIN_EMAIL", "ecobus.co.ss@gmail.com")
            send_mail(
                subject=f"New Contact: {contact.subject}",
                message=f"From: {contact.name} ({contact.email})\nPhone: {contact.phone}\n\n{contact.message}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[admin_email],
                fail_silently=True,
            )
        except Exception:
            pass

        return Response(
            ContactMessageSerializer(contact).data,
            status=status.HTTP_201_CREATED,
        )

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ContactMessageViewSet(viewsets.ModelViewSet):
    """Staff inbox for contact form submissions."""

    queryset = ContactMessage.objects.all()
    permission_classes = [IsStaffOrAbove]
    http_method_names = ["get", "patch", "head", "options"]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status"]
    search_fields = ["name", "email", "subject", "message"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action in ("partial_update", "update"):
            return ContactMessageUpdateSerializer
        return ContactMessageSerializer
