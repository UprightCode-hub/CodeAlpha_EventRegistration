from django.contrib import admin

from registrations.models import IdempotencyRecord, Notification, Registration


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    """Provides registration management with attendee and event details."""

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
