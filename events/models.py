from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Event(models.Model):
    """An event created and managed by an organizer."""

    class Category(models.TextChoices):
        """Lists the categories available for an event."""

        CONFERENCE = "conference", "Conference"
        WORKSHOP = "workshop", "Workshop"
        MEETUP = "meetup", "Meetup"
        WEBINAR = "webinar", "Webinar"
        NETWORKING = "networking", "Networking"
        OTHER = "other", "Other"

    class EventType(models.TextChoices):
        """Lists the supported event delivery formats."""

        IN_PERSON = "in_person", "In person"
        ONLINE = "online", "Online"
        HYBRID = "hybrid", "Hybrid"

    class Status(models.TextChoices):
        """Lists the event lifecycle states."""

        SCHEDULED = "scheduled", "Scheduled"
        CANCELLED = "cancelled", "Cancelled"

    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="events",
    )
    title = models.CharField(max_length=200)
    description = models.TextField(max_length=5000)
    category = models.CharField(max_length=20, choices=Category.choices)
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    venue_name = models.CharField(max_length=200, blank=True, default="")
    address = models.CharField(max_length=255, blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    online_link = models.URLField(max_length=2000, blank=True, default="")
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    registration_deadline = models.DateTimeField()
    capacity = models.PositiveIntegerField(
        validators=(MinValueValidator(1), MaxValueValidator(100000))
    )
    contact_email = models.EmailField(max_length=254)
    contact_phone = models.CharField(max_length=16)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )
    cancel_reason = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=("status", "start_at"), name="event_status_start_idx"),
            models.Index(fields=("organizer", "start_at"), name="event_organizer_start_idx"),
        ]

    registered_total: int
    waitlist_total: int

    @property
    def registered_count(self):
        if "registered_total" in self.__dict__:
            return self.registered_total
        return self.registrations.filter(status="registered").count()

    @property
    def waitlist_count(self):
        if "waitlist_total" in self.__dict__:
            return self.waitlist_total
        return self.registrations.filter(status="waitlisted").count()
