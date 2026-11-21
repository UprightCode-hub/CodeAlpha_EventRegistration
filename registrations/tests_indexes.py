"""Tests that the declared indexes exist on the models and in the database."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from events.models import Event
from registrations.models import IdempotencyRecord, Notification, Registration

User = get_user_model()

EXPECTED = [
    (Event, ("status", "start_at")),
    (Event, ("organizer", "start_at")),
    (Registration, ("event", "status", "created_at")),
    (Registration, ("attendee", "created_at")),
    (Notification, ("recipient", "created_at")),
    (IdempotencyRecord, ("created_at",)),
]


def declared(model):
    """Returns the field tuples of every index declared in the model Meta."""
    return {tuple(index.fields) for index in model._meta.indexes}


def in_database(model):
    """Returns the column tuples of every index the database holds for the model."""
    with connection.cursor() as cursor:
        found = connection.introspection.get_constraints(cursor, model._meta.db_table)
    return {tuple(info["columns"]) for info in found.values() if info["index"]}


class IndexTests(TestCase):
    """Matches indexes by their fields, so the names the code chose do not matter."""

    def test_models_declare_the_expected_indexes(self):
        for model, fields in EXPECTED:
            with self.subTest(model=model.__name__, fields=fields):
                self.assertIn(fields, declared(model))

    def test_database_has_the_expected_indexes(self):
        for model, fields in EXPECTED:
            columns = tuple(model._meta.get_field(name).column for name in fields)
            with self.subTest(model=model.__name__, columns=columns):
                self.assertIn(columns, in_database(model))


class ConstraintTests(TestCase):
    """Checks that database constraints exist and reject invalid model writes."""

    def test_event_database_constraints_reject_invalid_values(self):
        organizer = User.objects.create_user(
            username="constraint_org",
            email="constraint-org@example.com",
            password="Str0ng-Pass-123",
            role="organizer",
        )
        start = timezone.now() + timedelta(days=1)
        values = {
            "organizer": organizer,
            "title": "Constraint Event",
            "description": "A valid description.",
            "category": "meetup",
            "event_type": "in_person",
            "venue_name": "Hall",
            "address": "1 Main Street",
            "city": "Lagos",
            "start_at": start,
            "end_at": start,
            "registration_deadline": start,
            "capacity": 1,
            "contact_email": "host@example.com",
            "contact_phone": "+2348012345678",
        }
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Event.objects.create(**values)

        values["end_at"] = start + timedelta(hours=1)
        values["capacity"] = 0
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Event.objects.create(**values)

        values["capacity"] = 1
        event = Event.objects.create(**values)
        attendee = User.objects.create_user(
            username="constraint_attendee",
            email="constraint-attendee@example.com",
            password="Str0ng-Pass-123",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Registration.objects.create(
                    attendee=attendee,
                    event=event,
                    status="invalid",
                    full_name="Invalid Status",
                    phone="+2348012345678",
                )
