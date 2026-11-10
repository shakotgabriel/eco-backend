from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsAdminUser(BasePermission):
    """Only users with ADMIN role."""

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == "ADMIN"
        )


class IsManagerOrAbove(BasePermission):
    """Users with MANAGER or ADMIN role."""

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ("ADMIN", "MANAGER")
        )


class IsStaffOrAbove(BasePermission):
    """Staff, managers/admins, and active selling agents."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.role in ("ADMIN", "MANAGER", "STAFF"):
            return True
        agent = getattr(user, "agent_profile", None)
        return agent is not None and agent.status == "ACTIVE"


class ReadOnly(BasePermission):
    """Allow unauthenticated read-only access."""

    def has_permission(self, request, view):
        return request.method in SAFE_METHODS


class IsStaffOrReadOnly(BasePermission):
    """Authenticated staff can write; anyone can read."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ("ADMIN", "MANAGER", "STAFF")
        )


class PublicCreateOrStaff(BasePermission):
    """Anyone can create (POST); staff required for everything else."""

    def has_permission(self, request, view):
        if request.method == "POST":
            return True
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ("ADMIN", "MANAGER", "STAFF")
        )


class IsOperatorOwnerOrStaff(BasePermission):
    """
    Fleet access:
    - ADMIN / MANAGER: full access to all objects.
    - STAFF: read-only at object level.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return request.method in SAFE_METHODS
        return request.user.role in ("ADMIN", "MANAGER", "STAFF")

    def has_object_permission(self, request, view, obj):
        if request.user.role in ("ADMIN", "MANAGER"):
            return True
        if request.user.role == "STAFF":
            return request.method in SAFE_METHODS
        return False


class IsDriverOrAbove(BasePermission):
    """Boarding / ticket scan: ADMIN, MANAGER, STAFF, or DRIVER."""

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role in ("ADMIN", "MANAGER", "STAFF", "DRIVER")
        )
