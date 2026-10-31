"""Shared helpers for the register, cancel and list tests."""

from datetime import timedelta

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from registrations.tests import RegistrationTestBase

READ_KEYS = {
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
}
ORGANIZER_READ_KEYS = READ_KEYS | {"attendee_username", "attendee_email"}
NOTIFICATION_KEYS = {"id", "kind", "message", "event", "event_title", "registration", "created_at"}
BAD_PAGES = ("abc", "0", "-1", "99", "99999999999999999999")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ApiTestBase(RegistrationTestBase):
    """Adds signed-in clients, error checks and event builders for the API tests."""

    def as_user(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def error_of(self, response):
        return response.json()["error"]

    def assert_error(self, response, status, code, message=None):
        self.assertEqual(response.status_code, status, response.content)
        error = self.error_of(response)
        self.assertEqual(error["code"], code)
        if message is not None:
            self.assertEqual(error["message"], message)

    def assert_field_error(self, response, field):
        self.assertEqual(response.status_code, 400, response.content)
        error = self.error_of(response)
        self.assertEqual(error["code"], "validation_error")
        self.assertIn(field, error["fields"])

    def started_event(self, **overrides):
        """Builds an event whose start time has already passed."""
        start = timezone.now() - timedelta(hours=1)
        values = dict(
            start_at=start,
            end_at=start + timedelta(hours=3),
            registration_deadline=start - timedelta(days=1),
        )
        values.update(overrides)
        return self.make_event(**values)

    def closed_event(self, **overrides):
        """Builds an upcoming event whose registration deadline has passed."""
        start = timezone.now() + timedelta(hours=2)
        values = dict(
            start_at=start,
            end_at=start + timedelta(hours=3),
            registration_deadline=timezone.now() - timedelta(hours=1),
        )
        values.update(overrides)
        return self.make_event(**values)

    def assert_flat_queries(self, client, url, grow, small, large):
        """Checks that a page of `large` items costs as many queries as a page of `small`."""
        client.get(url)  # warm up so one-time work is not counted
        with CaptureQueriesContext(connection) as before:
            first = client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.json()["results"]), small)
        grow()
        with CaptureQueriesContext(connection) as after:
            second = client.get(url)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(second.json()["results"]), large)
        self.assertEqual(len(before), len(after))
