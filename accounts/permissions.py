from rest_framework.permissions import BasePermission


class IsUserRole(BasePermission):
    """Allows access only to authenticated accounts with the USER role."""

    message = "Only users with the USER role can perform this action."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_consumer)


class IsProviderRole(BasePermission):
    """Allows access only to authenticated accounts with the PROVIDER role."""

    message = "Only users with the PROVIDER role can perform this action."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_provider)
