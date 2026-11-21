from django.contrib import admin
from django.contrib.admin import ActionLocation
from django.http import HttpRequest

from registrations.models import IdempotencyRecord, Notification, Registration


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    """Provides registration management with attendee and event details."""

    readonly_fields = ("attendee", "event", "status", "created_at", "updated_at")

    list_display = ("id", "full_name", "attendee", "event", "status", "created_at")
    list_filter = ("status",)
    search_fields = (
        "full_name",
        "phone",
        "attendee__username",
        "attendee__email",
        "event__title",
    )
    list_select_related = ("attendee", "event")

    def has_add_permission(self, request):
        """Keep registration creation in the transactional seat service."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Preserve registration history and prevent bulk seat bypasses."""
        return False

    def get_actions(
        self,
        request: HttpRequest,
        action_location: ActionLocation = ActionLocation.CHANGE_LIST,
    ):
        """Remove Django's bulk delete action for registrations."""
        actions = super().get_actions(request, action_location)
        actions.pop("delete_selected", None)
        return actions


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """Provides read-only creation controls and searchable notification lists."""

    list_display = ("id", "recipient", "event", "kind", "created_at")
    list_filter = ("kind",)
    search_fields = ("recipient__username", "event__title", "message")
    list_select_related = ("recipient", "event")

    def has_add_permission(self, request):
        """Prevent adding notifications in the admin."""
        return False


@admin.register(IdempotencyRecord)
class IdempotencyRecordAdmin(admin.ModelAdmin):
    """Shows saved idempotency responses without allowing edits."""

    list_display = ("id", "user", "endpoint", "key", "status_code", "created_at")
    list_filter = ("endpoint", "created_at")
    search_fields = ("key", "user__username", "endpoint")

    def has_add_permission(self, request):
        """Prevent adding idempotency records in the admin."""
        return False

    def has_change_permission(self, request, obj=None):
        """Prevent editing idempotency records in the admin."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Prevent deleting idempotency records in the admin."""
        return False
