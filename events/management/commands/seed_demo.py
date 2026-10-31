from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from events.models import Event


class Command(BaseCommand):
    """Creates the two demo users and three scheduled demo events."""

    help = "Create demo users and events."

    def handle(self, *args, **options):
        user_model = get_user_model()
        organizer, created = self.get_or_create_user(
            user_model,
            username="demo_organizer",
            email="demo_organizer@example.com",
            role="organizer",
        )
        self.stdout.write(
            f"User demo_organizer {'created' if created else 'already existed'}."
        )
        attendee, created = self.get_or_create_user(
            user_model,
            username="demo_attendee",
            email="demo_attendee@example.com",
            role="attendee",
        )
        self.stdout.write(
            f"User demo_attendee {'created' if created else 'already existed'}."
        )

        now = timezone.now()
        definitions = (
            {
                "title": "Demo In Person Conference",
                "days": 7,
                "event_type": "in_person",
                "capacity": 25,
                "venue_name": "Demo Conference Hall",
                "address": "1 Example Road",
                "city": "Lagos",
                "online_link": "",
                "contact_email": "conference@example.com",
                "contact_phone": "+2348012345601",
            },
            {
                "title": "Demo Online Webinar",
                "days": 14,
                "event_type": "online",
                "capacity": 5,
                "venue_name": "",
                "address": "",
                "city": "",
                "online_link": "https://example.com/demo-webinar",
                "contact_email": "webinar@example.com",
                "contact_phone": "+2348012345602",
            },
            {
                "title": "Demo Hybrid Meetup",
                "days": 21,
                "event_type": "hybrid",
                "capacity": 50,
                "venue_name": "Demo Meetup Space",
                "address": "3 Sample Avenue",
                "city": "Abuja",
                "online_link": "https://example.com/demo-meetup",
                "contact_email": "meetup@example.com",
                "contact_phone": "+2348012345603",
            },
        )
        for definition in definitions:
            start_at = now + timedelta(days=definition.pop("days"))
            defaults = {
                **definition,
                "organizer": organizer,
                "description": f"A sample {definition['event_type']} event.",
                "category": "conference" if definition["event_type"] == "in_person" else (
                    "webinar" if definition["event_type"] == "online" else "meetup"
                ),
                "start_at": start_at,
                "end_at": start_at + timedelta(hours=2),
                "registration_deadline": start_at,
            }
            _, created = Event.objects.get_or_create(
                organizer=organizer,
                title=definition["title"],
                defaults=defaults,
            )
            self.stdout.write(
                f"Event {definition['title']} {'created' if created else 'already existed'}."
            )

    def get_or_create_user(self, user_model, username, email, role):
        try:
            return user_model.objects.get(username=username), False
        except user_model.DoesNotExist:
            return (
                user_model.objects.create_user(
                    username=username,
                    email=email,
                    password="EventDemo123!",
                    role=role,
                ),
                True,
            )
