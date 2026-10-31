from rest_framework.permissions import BasePermission


class IsOrganizer(BasePermission):
    """Allows authenticated users with the organizer role."""

    def has_permission(self, request, view):
        return getattr(request.user, "role", None) == "organizer"


class IsAttendee(BasePermission):
    """Allows authenticated users with the attendee role."""

    def has_permission(self, request, view):
        return getattr(request.user, "role", None) == "attendee"
