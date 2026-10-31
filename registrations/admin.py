from django.contrib import admin

from registrations.models import Notification, Registration


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
        return False
