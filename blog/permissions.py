from rest_framework import permissions
from rest_framework.exceptions import APIException


class SiteUnderMaintenance(APIException):
    status_code = 503
    default_detail = "Сайтът временно е в режим на поддръжка."
    default_code = 'site_maintenance'


class SiteIsAvailable(permissions.BasePermission):
    """Block non-staff API traffic while the global maintenance flag is active."""

    message = "Сайтът временно е в режим на поддръжка."

    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated and request.user.is_staff:
            return True

        # Import lazily so permission module loading cannot create an app-registry
        # cycle during Django startup and migrations.
        from .models import SiteSettings

        site_settings = (
            SiteSettings.objects.filter(pk=1)
            .only('maintenance_mode')
            .first()
        )
        if site_settings and site_settings.maintenance_mode:
            raise SiteUnderMaintenance()
        return True


class IsOwner(permissions.BasePermission):
    """
    Custom permission to only allow owners of an object to edit or delete it.
    """
    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed to any request,
        # so we'll always allow GET, HEAD or OPTIONS requests.
        if request.method in permissions.SAFE_METHODS:
            return True

        # Write permissions are only allowed to the owner of the content.
        return bool(obj.user_id and obj.user_id == request.user.pk)
