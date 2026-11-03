from django.contrib import admin
from django.db.models import Count, Q

from events.models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    """Provides event management with registration totals in the list."""

    list_display = (
        "title",
        "organizer",
        "category",
        "event_type",
        "status",
        "start_at",
        "capacity",
        "registered_count",
        "waitlist_count",
    )
    list_filter = ("status", "category", "event_type")
    search_fields = ("title", "city", "organizer__username")
    list_select_related = ("organizer",)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                registered_total=Count(
                    "registrations", filter=Q(registrations__status="registered")
                ),
                waitlist_total=Count("registrations", filter=Q(registrations__status="waitlisted")),
            )
        )
