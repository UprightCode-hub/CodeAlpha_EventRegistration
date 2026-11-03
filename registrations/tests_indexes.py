"""Tests that the declared indexes exist on the models and in the database."""

from django.db import connection
from django.test import TestCase

from events.models import Event
from registrations.models import IdempotencyRecord, Notification, Registration

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
