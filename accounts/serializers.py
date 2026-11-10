from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

User = get_user_model()


class EmailOrUsernameTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Accepts either a username or an email address in the `username` field
    (frontends commonly log in with email), and embeds role/name claims in
    the token so edge middleware can make RBAC decisions without an extra
    round-trip to `/accounts/me/`.
    """

    default_error_messages = {
        "no_active_account": "No active account found with the given credentials.",
    }

    def validate(self, attrs):
        username_or_email = attrs.get(self.username_field, "")
        if "@" in username_or_email:
            try:
                matched_user = User.objects.get(email__iexact=username_or_email)
                attrs[self.username_field] = matched_user.get_username()
            except User.DoesNotExist:
                pass
            except User.MultipleObjectsReturned:
                pass

        data = super().validate(attrs)

        data["user"] = {
            "id": self.user.id,
            "username": self.user.username,
            "email": self.user.email,
            "name": self.user.get_full_name() or self.user.username,
            "role": self.user.role,
        }
        return data

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["role"] = user.role
        token["name"] = user.get_full_name() or user.username
        token["email"] = user.email
        return token


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id", "username", "email", "first_name", "last_name",
            "role", "phone_number", "is_active", "date_joined", "updated_at",
        ]
        read_only_fields = ["id", "date_joined", "updated_at"]

    def to_internal_value(self, data):
        if isinstance(data, dict) and data.get("role"):
            data = data.copy()
            data["role"] = str(data["role"]).strip().upper()
        return super().to_internal_value(data)


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    # Roles that each creator level is allowed to assign
    MANAGER_ALLOWED_ROLES = ("STAFF", "DRIVER", "OPERATOR")

    class Meta:
        model = User
        fields = [
            "username", "email", "first_name", "last_name",
            "role", "phone_number", "password",
        ]

    def to_internal_value(self, data):
        if isinstance(data, dict) and data.get("role"):
            data = data.copy()
            data["role"] = str(data["role"]).strip().upper()
        return super().to_internal_value(data)

    def validate_role(self, value):
        request = self.context.get("request")
        if not request or not request.user:
            return value
        creator = request.user
        if creator.role == "ADMIN":
            return value  # admin can create any role
        if creator.role == "MANAGER":
            if value not in self.MANAGER_ALLOWED_ROLES:
                raise serializers.ValidationError(
                    f"Managers can only create {', '.join(self.MANAGER_ALLOWED_ROLES)} accounts."
                )
        return value

    def create(self, validated_data):
        password = validated_data.pop("password")
        role = validated_data.get("role", "STAFF")
        if role in ("ADMIN", "MANAGER"):
            validated_data.setdefault("is_staff", True)
        if role == "ADMIN":
            validated_data.setdefault("is_superuser", True)
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class UserProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "phone_number"]


class PortalSettingsSerializer(serializers.Serializer):
    data = serializers.JSONField()

    def update(self, instance, validated_data):
        incoming = validated_data.get("data") or {}
        merged = {**instance.data, **incoming}
        instance.data = merged
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            instance.updated_by = request.user
        instance.save()
        return instance


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save()
        return user


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        # Do NOT reveal whether the email exists — always accept silently.
        # The view handles the actual lookup and only sends if the user exists.
        return value
