from django.db.models import Case, Count, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from rest_framework import serializers

from config.validators import phone_validator
from events.models import Event
from registrations.models import Notification, Registration


class SafeCharField(serializers.CharField):
    """Rejects null characters before they reach text fields."""

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if "\x00" in value:
            raise serializers.ValidationError("Null characters are not allowed.")
        return value


def with_waitlist_position(queryset):
    """Annotates each registration with its rank among event waitlist rows."""
    # Counting through the current row gives its 1-based rank in the same SQL query.
    prior = (
        Registration.objects.filter(
            event_id=OuterRef("event_id"),
            status=Registration.Status.WAITLISTED,
        )
        .filter(
            Q(created_at__lt=OuterRef("created_at"))
            | Q(created_at=OuterRef("created_at"), id__lte=OuterRef("id"))
        )
        .order_by()
        .values("event_id")
        .annotate(total=Count("id"))
        .values("total")[:1]
    )
    return queryset.annotate(
        waitlist_position_value=Case(
            When(
                status=Registration.Status.WAITLISTED,
                then=Coalesce(Subquery(prior), Value(0)),
            ),
            default=Value(None),
            output_field=IntegerField(null=True),
        )
    )


class RegistrationRequestSerializer(serializers.Serializer):
    """Validates attendee registration input."""

    event = serializers.IntegerField(
        min_value=1,
        max_value=2147483647,
    )
    full_name = SafeCharField(min_length=2, max_length=120, trim_whitespace=True)
    phone = SafeCharField(max_length=16, validators=(phone_validator,))
    note = SafeCharField(max_length=500, required=False, allow_blank=True, default="")

    def validate_event(self, value):
        if not Event.objects.filter(pk=value).exists():
            raise serializers.ValidationError("Select a valid event.")
        return value


class RegistrationReadSerializer(serializers.ModelSerializer):
    """Returns the attendee registration representation."""

    event = serializers.IntegerField(source="event_id", read_only=True)
    event_title = serializers.CharField(source="event.title", read_only=True)
    event_start_at = serializers.DateTimeField(source="event.start_at", read_only=True)
    waitlist_position = serializers.IntegerField(
        source="waitlist_position_value", read_only=True, allow_null=True
    )

    class Meta:
        """Defines the registration response fields."""

        model = Registration
        fields: tuple[str, ...] = (
            "id",
            "event",
            "event_title",
            "event_start_at",
            "full_name",
            "phone",
            "note",
            "status",
            "waitlist_position",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class OrganizerRegistrationReadSerializer(RegistrationReadSerializer):
    """Returns a registration with attendee account details."""

    attendee_username = serializers.CharField(source="attendee.username", read_only=True)
    attendee_email = serializers.EmailField(source="attendee.email", read_only=True)

    class Meta(RegistrationReadSerializer.Meta):
        """Adds attendee account fields to the registration response."""

        fields = RegistrationReadSerializer.Meta.fields + ("attendee_username", "attendee_email")


class NotificationReadSerializer(serializers.ModelSerializer):
    """Returns the notification representation."""

    event_title = serializers.CharField(source="event.title", read_only=True)
    event = serializers.IntegerField(source="event_id", read_only=True)
    registration = serializers.IntegerField(
        source="registration_id", read_only=True, allow_null=True
    )

    class Meta:
        """Defines the notification response fields."""

        model = Notification
        fields = ("id", "kind", "message", "event", "event_title", "registration", "created_at")
        read_only_fields = fields
