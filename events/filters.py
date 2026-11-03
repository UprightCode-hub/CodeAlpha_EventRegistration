import re
from datetime import date

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers

from events.models import Event

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _date_parameter(params, name, errors):
    value = params.get(name)
    if not value:
        return None
    if not DATE_PATTERN.fullmatch(value):
        errors[name] = ["Use a date in YYYY-MM-DD format."]
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors[name] = ["Enter a valid date in YYYY-MM-DD format."]
        return None


def filter_events(params):
    """Applies the supported public event filters to an ordered queryset."""
    errors = {}
    status_value = params.get("status")
    if status_value and status_value not in Event.Status.values:
        errors["status"] = ["Select a valid status."]

    category = params.get("category")
    if category and category not in Event.Category.values:
        errors["category"] = ["Select a valid category."]

    event_type = params.get("event_type")
    if event_type and event_type not in Event.EventType.values:
        errors["event_type"] = ["Select a valid event type."]

    starts_after = _date_parameter(params, "starts_after", errors)
    starts_before = _date_parameter(params, "starts_before", errors)

    for name in ("q", "city"):
        value = params.get(name)
        if value and "\x00" in value:
            errors[name] = ["Null characters are not allowed."]

    if errors:
        raise serializers.ValidationError(errors)

    queryset = Event.objects.select_related("organizer").annotate(  # type: ignore[no-redef]  # QuerySet annotation.
        registered_total=Count("registrations", filter=Q(registrations__status="registered")),
        waitlist_total=Count("registrations", filter=Q(registrations__status="waitlisted")),
    )
    if status_value == Event.Status.CANCELLED:
        queryset = queryset.filter(status=Event.Status.CANCELLED)
    else:
        queryset = queryset.filter(
            status=Event.Status.SCHEDULED,
            start_at__gt=timezone.now(),
        )

    if category:
        queryset = queryset.filter(category=category)
    if event_type:
        queryset = queryset.filter(event_type=event_type)

    query = params.get("q")
    if query:
        queryset = queryset.filter(Q(title__icontains=query) | Q(description__icontains=query))
    city = params.get("city")
    if city:
        queryset = queryset.filter(city__icontains=city)
    if starts_after is not None:
        queryset = queryset.filter(start_at__date__gte=starts_after)
    if starts_before is not None:
        queryset = queryset.filter(start_at__date__lte=starts_before)

    return queryset.order_by("start_at", "id")
