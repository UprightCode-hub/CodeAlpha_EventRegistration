from datetime import UTC

from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware
from rest_framework import serializers


class OffsetDateTimeField(serializers.DateTimeField):
    """Parses offset-aware timestamps and represents them in UTC."""

    default_error_messages = {
        **serializers.DateTimeField.default_error_messages,
        "offset_required": "A timezone offset is required.",
    }

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("default_timezone", UTC)
        kwargs.setdefault("format", "iso-8601")
        super().__init__(*args, **kwargs)

    def to_internal_value(self, value):
        if isinstance(value, str):
            parsed = parse_datetime(value)
            if parsed is not None and not is_aware(parsed):
                self.fail("offset_required")
        elif hasattr(value, "tzinfo") and not is_aware(value):
            self.fail("offset_required")
        return super().to_internal_value(value)
