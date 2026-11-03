from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone
from rest_framework import serializers

from config.fields import OffsetDateTimeField
from config.validators import http_https_link_validator, phone_validator
from events.models import Event


class SafeCharField(serializers.CharField):
    """Rejects null characters before they reach text database fields."""

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if "\x00" in value:
            raise serializers.ValidationError("Null characters are not allowed.")
        return value


class EventWriteSerializer(serializers.ModelSerializer):
    """Validates event creation and partial updates."""

    title = SafeCharField(min_length=3, max_length=200)
    description = SafeCharField(min_length=1, max_length=5000)
    venue_name = SafeCharField(max_length=200, required=False, allow_blank=True)
    address = SafeCharField(max_length=255, required=False, allow_blank=True)
    city = SafeCharField(max_length=100, required=False, allow_blank=True)
    online_link = serializers.URLField(
        max_length=2000,
        required=False,
        allow_blank=True,
        validators=(http_https_link_validator,),
    )
    start_at = OffsetDateTimeField()
    end_at = OffsetDateTimeField()
    registration_deadline = OffsetDateTimeField(required=False)
    capacity = serializers.IntegerField(
        min_value=1,
        max_value=100000,
        validators=(MinValueValidator(1), MaxValueValidator(100000)),
    )
    contact_email = serializers.EmailField(max_length=254)
    contact_phone = SafeCharField(max_length=16, validators=(phone_validator,))

    class Meta:
        """Defines the fields clients can provide when writing an event."""

        model = Event
        fields = (
            "title",
            "description",
            "category",
            "event_type",
            "venue_name",
            "address",
            "city",
            "online_link",
            "start_at",
            "end_at",
            "registration_deadline",
            "capacity",
            "contact_email",
            "contact_phone",
        )

    def validate(self, attrs):
        errors = {}

        def current(name, default=""):
            if name in attrs:
                return attrs[name]
            if self.instance is not None:
                return getattr(self.instance, name)
            return default

        start_at = current("start_at")
        end_at = current("end_at")
        deadline = current("registration_deadline")
        if self.instance is None and "registration_deadline" not in attrs:
            deadline = start_at
            attrs["registration_deadline"] = start_at

        now = timezone.now()
        if self.instance is None and start_at <= now:
            errors["start_at"] = ["The event start must be in the future."]
        elif self.instance is not None and "start_at" in attrs and start_at <= now:
            errors["start_at"] = ["A changed event start must be in the future."]
        if end_at <= start_at:
            errors["end_at"] = ["The event end must be after its start."]
        if deadline > start_at:
            errors["registration_deadline"] = [
                "The registration deadline must be on or before the event start."
            ]
        if self.instance is None and deadline <= now:
            errors["registration_deadline"] = ["The registration deadline must be in the future."]

        event_type = current("event_type")
        required_by_type = {
            "in_person": ("venue_name", "address", "city"),
            "online": ("online_link",),
            "hybrid": ("venue_name", "address", "city", "online_link"),
        }
        for field_name in required_by_type.get(event_type, ()):
            if not current(field_name):
                errors[field_name] = ["This field is required for the selected event type."]

        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class CancelEventSerializer(serializers.Serializer):
    """Validates and trims an event cancellation reason."""

    reason = SafeCharField(min_length=1, max_length=500, trim_whitespace=True)


class EventReadSerializer(serializers.ModelSerializer):
    """Returns the public event representation."""

    start_at = OffsetDateTimeField(read_only=True)
    end_at = OffsetDateTimeField(read_only=True)
    registration_deadline = OffsetDateTimeField(read_only=True)
    created_at = OffsetDateTimeField(read_only=True)
    registered_count = serializers.IntegerField(read_only=True)
    waitlist_count = serializers.IntegerField(read_only=True)
    seats_left = serializers.SerializerMethodField()
    organizer_username = serializers.CharField(source="organizer.username", read_only=True)

    class Meta:
        """Defines the event response fields."""

        model = Event
        fields = (
            "id",
            "title",
            "description",
            "category",
            "event_type",
            "venue_name",
            "address",
            "city",
            "online_link",
            "start_at",
            "end_at",
            "registration_deadline",
            "capacity",
            "registered_count",
            "seats_left",
            "waitlist_count",
            "contact_email",
            "contact_phone",
            "organizer_username",
            "status",
            "cancel_reason",
            "created_at",
        )
        read_only_fields = fields

    def get_seats_left(self, event) -> int:
        return max(event.capacity - event.registered_count, 0)
