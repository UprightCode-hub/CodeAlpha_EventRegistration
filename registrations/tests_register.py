"""Tests for registering for an event and for the seat and waitlist rules."""

from unittest import mock

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from events.models import Event
from registrations.models import Notification, Registration
from registrations.tests_helpers import READ_KEYS, ApiTestBase

URL = "/api/registrations/"


class RegisterTests(ApiTestBase):
    """Checks POST /api/registrations/ for attendees."""

    def setUp(self):
        super().setUp()
        self.event = self.make_event(capacity=2)
        self.client = self.as_user(self.attendee)

    def body(self, **overrides):
        values = {
            "event": self.event.id,
            "full_name": "Ada Obi",
            "phone": "+2348012345678",
            "note": "Window seat please",
        }
        values.update(overrides)
        return values

    def register(self, client=None, **overrides):
        return (client or self.client).post(URL, self.body(**overrides), format="json")

    def test_registering_returns_201_with_the_read_shape(self):
        response = self.register()
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual(set(data), READ_KEYS)
        self.assertEqual(data["event"], self.event.id)
        self.assertEqual(data["event_title"], "Counts Meetup")
        self.assertEqual(data["full_name"], "Ada Obi")
        self.assertEqual(data["phone"], "+2348012345678")
        self.assertEqual(data["note"], "Window seat please")
        self.assertEqual(data["status"], "registered")
        self.assertIsNone(data["waitlist_position"])

    def test_registering_saves_the_row_and_one_confirmation(self):
        response = self.register()
        self.assertEqual(response.status_code, 201)
        registration = Registration.objects.get()
        self.assertEqual(registration.attendee, self.attendee)
        self.assertEqual(registration.event, self.event)
        note = Notification.objects.get()
        self.assertEqual(note.recipient, self.attendee)
        self.assertEqual(note.event, self.event)
        self.assertEqual(note.registration, registration)
        self.assertEqual(note.kind, "registration_confirmed")
        self.assertEqual(note.message, "You are registered for Counts Meetup.")

    def test_the_note_is_optional(self):
        body = self.body()
        del body["note"]
        response = self.client.post(URL, body, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["note"], "")

    def test_the_full_name_is_stored_without_outer_spaces(self):
        response = self.register(full_name="  Ada Obi  ")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["full_name"], "Ada Obi")

    def test_fields_in_the_body_that_are_not_inputs_are_ignored(self):
        other = self.make_attendees(1)[0]
        response = self.register(status="waitlisted", attendee=other.id, id=9999)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "registered")
        self.assertEqual(Registration.objects.get().attendee, self.attendee)

    def test_anonymous_user_gets_401(self):
        response = APIClient().post(URL, self.body(), format="json")
        self.assert_error(response, 401, "not_authenticated")

    def test_organizer_gets_403(self):
        response = self.register(client=self.as_user(self.organizer))
        self.assert_error(response, 403, "permission_denied")
        self.assertEqual(Registration.objects.count(), 0)

    def test_missing_required_fields_are_validation_errors(self):
        for name in ("event", "full_name", "phone"):
            with self.subTest(name=name):
                body = self.body()
                del body[name]
                response = self.client.post(URL, body, format="json")
                self.assert_field_error(response, name)

    def test_unknown_or_unusable_event_values_are_validation_errors(self):
        for value in (999999999, "abc", None, 2147483648, 10**20):
            with self.subTest(value=value):
                self.assert_field_error(self.register(event=value), "event")
        self.assertEqual(Registration.objects.count(), 0)

    def test_full_name_rules(self):
        for value in ("A", "x" * 121, "   ", "Ada\x00Obi"):
            with self.subTest(value=value):
                self.assert_field_error(self.register(full_name=value), "full_name")
        self.assertEqual(self.register(full_name="x" * 120).status_code, 201)

    def test_bad_phone_numbers_are_validation_errors(self):
        bad = (
            "08012345678",
            "+0123456789",
            "+234 801 234 5678",
            "+2348012",
            "+1234567890123456",
            "abc",
        )
        for value in bad:
            with self.subTest(value=value):
                self.assert_field_error(self.register(phone=value), "phone")

    def test_phone_numbers_at_the_length_limits_are_accepted(self):
        for value in ("+12345678", "+123456789012345"):
            with self.subTest(value=value):
                event = self.make_event(title=f"Phone {value}")
                self.assertEqual(self.register(event=event.id, phone=value).status_code, 201)

    def test_note_rules(self):
        self.assert_field_error(self.register(note="x" * 501), "note")
        self.assert_field_error(self.register(note="bad\x00note"), "note")
        self.assertEqual(self.register(note="x" * 500).status_code, 201)

    def test_malformed_json_is_a_validation_error(self):
        response = self.client.post(URL, "{bad json", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error_of(response)["code"], "validation_error")

    def test_the_first_registrations_take_seats_and_later_ones_wait(self):
        people = self.make_attendees(4)
        results = [self.register(client=self.as_user(person)) for person in people]
        self.assertEqual([r.status_code for r in results], [201, 201, 201, 201])
        self.assertEqual([r.json()["status"] for r in results], ["registered", "registered", "waitlisted", "waitlisted"])
        self.assertEqual([r.json()["waitlist_position"] for r in results], [None, None, 1, 2])
        detail = self.client.get(f"/api/events/{self.event.id}/").json()
        self.assertEqual(detail["registered_count"], 2)
        self.assertEqual(detail["waitlist_count"], 2)
        self.assertEqual(detail["seats_left"], 0)

    def test_a_waitlisted_registration_gets_the_waitlist_notification(self):
        first, second, third = self.make_attendees(3)
        for person in (first, second, third):
            self.register(client=self.as_user(person))
        note = Notification.objects.get(recipient=third)
        self.assertEqual(note.kind, "waitlisted")
        self.assertEqual(note.message, "You are on the waitlist for Counts Meetup.")
        self.assertEqual(note.registration.attendee, third)

    def test_a_cancelled_event_is_refused(self):
        self.event.status = Event.Status.CANCELLED
        self.event.cancel_reason = "Weather"
        self.event.save()
        response = self.register()
        self.assert_error(response, 400, "event_cancelled", "This event has been cancelled.")
        self.assertEqual(Registration.objects.count(), 0)

    def test_a_started_event_is_refused(self):
        event = self.started_event(title="Already running")
        response = self.register(event=event.id)
        self.assert_error(response, 400, "event_started", "This event has already started.")
        self.assertEqual(Registration.objects.count(), 0)

    def test_a_passed_deadline_is_refused(self):
        event = self.closed_event(title="Deadline gone")
        response = self.register(event=event.id)
        self.assert_error(response, 400, "registration_closed", "Registration for this event is closed.")
        self.assertEqual(Registration.objects.count(), 0)

    def test_a_passed_deadline_also_blocks_the_waitlist(self):
        event = self.closed_event(title="Full and closed", capacity=1)
        self.make_registration(self.make_attendees(1)[0], event, "registered")
        response = self.register(event=event.id)
        self.assert_error(response, 400, "registration_closed")
        self.assertEqual(event.registrations.count(), 1)

    def test_state_errors_are_checked_in_the_agreed_order(self):
        both = self.started_event(title="Cancelled and started", status="cancelled", cancel_reason="Rain")
        self.assert_error(self.register(event=both.id), 400, "event_cancelled")
        late = self.started_event(title="Started and closed")
        self.assert_error(self.register(event=late.id), 400, "event_started")

    def test_an_open_registration_blocks_a_second_one(self):
        for existing in ("registered", "waitlisted"):
            with self.subTest(existing=existing):
                event = self.make_event(title=f"Held as {existing}")
                self.make_registration(self.attendee, event, existing)
                before = Notification.objects.count()
                response = self.register(event=event.id)
                self.assert_error(
                    response,
                    400,
                    "already_registered",
                    "You already have an open registration for this event.",
                )
                self.assertEqual(event.registrations.count(), 1)
                self.assertEqual(Notification.objects.count(), before)

    def test_registering_again_after_cancelling_makes_a_new_row(self):
        old = self.make_registration(self.attendee, self.event, "cancelled")
        response = self.register()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "registered")
        self.assertNotEqual(response.json()["id"], old.id)
        self.assertEqual(self.event.registrations.count(), 2)

    def test_registering_takes_a_row_lock_on_the_event(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.register()
        self.assertEqual(response.status_code, 201)
        self.assertTrue(any("FOR UPDATE" in q["sql"].upper() for q in queries.captured_queries))

    def test_a_failure_while_saving_the_notification_saves_nothing(self):
        client = self.as_user(self.attendee)
        client.raise_request_exception = False
        with mock.patch.object(Notification, "save", side_effect=RuntimeError("boom")):
            with self.assertLogs("django.request", level="ERROR"):
                response = client.post(URL, self.body(), format="json")
        self.assert_error(response, 500, "server_error")
        self.assertEqual(Registration.objects.count(), 0)
