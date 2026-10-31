from django.conf import settings
from django.core.validators import MinLengthValidator
from django.db import models
from django.db.models import Q

from config.validators import phone_validator


class Registration(models.Model):
    """Stores an attendee's registration for an event."""

    class Status(models.TextChoices):
        """Lists the registration lifecycle states."""

        REGISTERED = "registered", "Registered"
        WAITLISTED = "waitlisted", "Waitlisted"
        CANCELLED = "cancelled", "Cancelled"
        EVENT_CANCELLED = "event_cancelled", "Event cancelled"

    attendee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="registrations",
    )
    event = models.ForeignKey(
        "events.Event",
        on_delete=models.PROTECT,
        related_name="registrations",
    )
    status = models.CharField(max_length=20, choices=Status.choices)
    full_name = models.CharField(max_length=120, validators=(MinLengthValidator(2),))
    phone = models.CharField(max_length=16, validators=(phone_validator,))
    note = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Defines uniqueness for registrations that still hold a place."""

        constraints = (
            models.UniqueConstraint(
                fields=("attendee", "event"),
                condition=Q(status__in=("registered", "waitlisted")),
                name="unique_open_registration_per_attendee_event",
            ),
        )


class Notification(models.Model):
    """Stores an event notification for a user."""

    class Kind(models.TextChoices):
        """Lists the notification types available to the application."""

        REGISTRATION_CONFIRMED = "registration_confirmed", "Registration confirmed"
        WAITLISTED = "waitlisted", "Waitlisted"
        PROMOTED = "promoted", "Promoted"
        EVENT_CANCELLED = "event_cancelled", "Event cancelled"
        EVENT_UPDATED = "event_updated", "Event updated"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    event = models.ForeignKey(
        "events.Event",
        on_delete=models.PROTECT,
        related_name="notifications",
    )
    registration = models.ForeignKey(
        Registration,
        on_delete=models.SET_NULL,
        related_name="notifications",
        blank=True,
        null=True,
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    message = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
