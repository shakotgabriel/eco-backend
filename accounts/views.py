from bebeto_backend.permissions import IsAdminUser, IsManagerOrAbove
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
import django_filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from bebeto_backend.throttles import PasswordResetThrottle
from rest_framework.response import Response

from .serializers import (
    ChangePasswordSerializer,
    EmailOrUsernameTokenObtainPairSerializer,
    PasswordResetRequestSerializer,
    PortalSettingsSerializer,
    UserCreateSerializer,
    UserProfileUpdateSerializer,
    UserSerializer,
)

from .models import PortalSettings

User = get_user_model()


class UserFilter(django_filters.FilterSet):
    """Exact role match without ChoiceFilter, so unknown values return empty instead of 400."""

    role = django_filters.CharFilter(method="filter_role")
    is_active = django_filters.BooleanFilter()

    class Meta:
        model = User
        fields = ["role", "is_active"]

    def filter_role(self, queryset, name, value):
        role = str(value or "").strip().upper()
        if not role:
            return queryset
        return queryset.filter(role=role)


class EmailOrUsernameTokenObtainPairView(TokenObtainPairView):
    """Login with either username or email, returning JWT + basic profile info."""
    serializer_class = EmailOrUsernameTokenObtainPairSerializer


@api_view(["POST"])
@permission_classes([AllowAny])
def logout_view(request):
    """
    Blacklist a refresh token so it can't mint new access tokens.

    POST /api/v1/auth/logout/  { "refresh": "<token>" }
    Always returns 205 so clients can clear their session even if the token
    was already expired or blacklisted.
    """
    raw = request.data.get("refresh") if hasattr(request, "data") else None
    if raw:
        try:
            RefreshToken(str(raw)).blacklist()
        except TokenError:
            pass
    return Response(status=status.HTTP_205_RESET_CONTENT)


class UserViewSet(viewsets.ModelViewSet):
    """
    User management.

    Role hierarchy:
      - ADMIN  → full access, can create/manage all roles including MANAGER.
      - MANAGER → can create and manage STAFF, DRIVER, and OPERATOR users.
    """
    permission_classes = [IsManagerOrAbove]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = UserFilter
    search_fields = ["username", "email", "first_name", "last_name", "phone_number"]
    ordering_fields = ["username", "date_joined"]
    ordering = ["username"]

    def get_queryset(self):
        user = self.request.user
        if user.role == "ADMIN":
            return User.objects.all()
        # Managers see only the roles they can manage
        return User.objects.filter(role__in=("STAFF", "DRIVER", "OPERATOR"))

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        return UserSerializer

    def perform_update(self, serializer):
        """Prevent managers from escalating a user's role beyond their authority."""
        new_role = serializer.validated_data.get("role")
        if new_role and self.request.user.role == "MANAGER":
            if new_role not in ("STAFF", "DRIVER", "OPERATOR"):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Managers can only assign STAFF, DRIVER, or OPERATOR.")
        serializer.save()


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    """Return the current authenticated user's profile with role-specific details."""
    user = request.user
    data = UserSerializer(user).data

    # Enrich with role-specific info
    if user.role in ("ADMIN", "MANAGER"):
        data["managed_users"] = User.objects.filter(
            role__in=("STAFF", "DRIVER", "OPERATOR") if user.role == "MANAGER"
            else ("ADMIN", "MANAGER", "STAFF", "DRIVER", "OPERATOR")
        ).count()

        from reports.audit import AuditLog
        data["recent_activity"] = list(
            AuditLog.objects.filter(actor=user)
            .order_by("-created_at")[:10]
            .values("action", "target_model", "target_id", "details", "created_at")
        )

    # Check if user is also an agent
    try:
        agent = user.agent_profile
        data["is_agent"] = True
        data["agent_code"] = agent.agent_code
        data["agent_status"] = agent.status
    except Exception:
        data["is_agent"] = False

    return Response(data)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_me(request):
    """Update the current user's profile fields."""
    serializer = UserProfileUpdateSerializer(
        request.user, data=request.data, partial=True
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(UserSerializer(request.user).data)


@api_view(["GET", "PATCH"])
@permission_classes([IsAdminUser])
def portal_settings(request):
    """Load or update portal-wide settings (admin only)."""
    settings_obj = PortalSettings.load()
    if request.method == "GET":
        return Response({"data": settings_obj.data, "updated_at": settings_obj.updated_at})
    serializer = PortalSettingsSerializer(
        settings_obj, data=request.data, partial=True, context={"request": request}
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response({"data": settings_obj.data, "updated_at": settings_obj.updated_at})


@api_view(["GET"])
@permission_classes([AllowAny])
def public_payment_methods(request):
    """Public checkout toggles from PortalSettings (safe subset only)."""
    from .portal_payments import public_payment_methods as methods

    return Response(methods())


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def change_password(request):
    """Change password for the currently authenticated user."""
    serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response({"detail": "Password changed successfully."})


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([PasswordResetThrottle])
def request_password_reset(request):
    """Send a password reset token to the user's email."""
    serializer = PasswordResetRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    email = serializer.validated_data["email"]

    # Always return the same message to prevent user enumeration
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response({"detail": "If that email exists, a reset link has been sent."})

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)

    from django.conf import settings
    from django.core.mail import send_mail

    send_mail(
        subject="EcoBus — Password Reset",
        message=(
            f"Hi {user.first_name or user.username},\n\n"
            f"Use the following credentials to reset your password:\n"
            f"  UID: {uid}\n"
            f"  Token: {token}\n\n"
            f"POST to /api/v1/accounts/password-reset-confirm/ with:\n"
            f"  uid, token, new_password\n\n"
            f"EcoBus"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
    )

    return Response({"detail": "If that email exists, a reset link has been sent."})


@api_view(["POST"])
@permission_classes([AllowAny])
def confirm_password_reset(request):
    """Confirm a password reset with uid, token, and new_password."""
    uid = request.data.get("uid")
    token = request.data.get("token")
    new_password = request.data.get("new_password")

    if not all([uid, token, new_password]):
        return Response(
            {"detail": "uid, token, and new_password are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        user_id = urlsafe_base64_decode(uid).decode()
        user = User.objects.get(pk=user_id)
    except (ValueError, User.DoesNotExist):
        return Response({"detail": "Invalid reset link."}, status=status.HTTP_400_BAD_REQUEST)

    if not default_token_generator.check_token(user, token):
        return Response({"detail": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST)

    user.set_password(new_password)
    user.save()
    return Response({"detail": "Password has been reset successfully."})
