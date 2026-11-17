"""
DCI API Permission Classes

Permission classes for DCI API endpoints.
"""
import logging

from rest_framework.permissions import BasePermission

from .record_providers import search_target

logger = logging.getLogger(__name__)


class DCIPersonPermissions(BasePermission):
    """
    Permission class for DCI Person API endpoints.

    The required right is the DCI person right of the HTTP method's action, as configured in
    ApiDciConfig; POST is search in DCI and requires the query right. A superuser holds every right.
    """

    permission_map = {
        'GET': ('person', 'query'),
        'POST': ('person', 'query'),
        'PUT': ('person', 'update'),
        'PATCH': ('person', 'update'),
    }

    def has_permission(self, request, view):
        """Return True only when the user holds the right mapped to the method."""
        # Any error while evaluating the user denies access.
        try:
            return self._user_has_required_permission(request)
        except Exception:
            logger.exception("DCI permission check failed; access denied")
            return False

    def _user_has_required_permission(self, request):
        # Require authentication for all DCI Person endpoints
        user = getattr(request, 'user', None)
        if not user or getattr(user, 'is_authenticated', False) is not True:
            return False

        entity_action = self.permission_map.get(request.method)
        if not entity_action:
            return False

        # An undeclared action (None) or an empty right list denies.
        from .apps import configured_perms
        rights = configured_perms(*entity_action)
        if not rights:
            return False

        # A user object without has_perm raises AttributeError, which denies.
        return any(user.has_perm(right) is True for right in rights)


class DCIPersonCreatePermissions(DCIPersonPermissions):
    """Permission class for the DCI Person create endpoint: POST requires the create right."""

    permission_map = {
        'POST': ('person', 'create'),
    }


class RecordProviderPermission(BasePermission):
    """Gate of one record provider: POST requires the right named by its required_right()."""

    def __init__(self, provider):
        self.provider = provider

    def has_permission(self, request, view):
        """Return True only when the user holds the provider's right."""
        # Any error while evaluating the provider or the user denies access.
        try:
            return self._user_has_provider_right(request)
        except Exception:
            logger.exception("DCI record provider permission check failed; access denied")
            return False

    def _user_has_provider_right(self, request):
        if request.method != 'POST':
            return False
        user = getattr(request, 'user', None)
        if not user or getattr(user, 'is_authenticated', False) is not True:
            return False
        right = self.provider.required_right()
        # An empty right would be granted to everyone by has_perms.
        if not isinstance(right, str) or not right.strip():
            logger.error("Record provider %r declares no usable right; access denied",
                         self.provider)
            return False
        return user.has_perm(right) is True


class DCISearchPermissions(BasePermission):
    """
    Gate of sync and async search, chosen by reg_record_type before any other check.

    A registered provider type takes the provider's gate, any other type (or no type)
    takes DCIPersonPermissions, and a message mixing a provider type with another is
    denied.
    """

    def has_permission(self, request, view):
        try:
            provider = search_target(request.data)
        except Exception:
            logger.warning("DCI search denied: search target not resolved", exc_info=True)
            return False
        if provider is None:
            return DCIPersonPermissions().has_permission(request, view)
        return RecordProviderPermission(provider).has_permission(request, view)
