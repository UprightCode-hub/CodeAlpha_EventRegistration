"""Tests for cancelling a registration and for waitlist promotion after it."""

from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from events.models import Event
from registrations.models import Notification, Registration
from registrations.tests_helpers import READ_KEYS, ApiTestBase


class CancelTests(ApiTestBase):
    """Checks POST /api/registrations/<id>/cancel/ and the promotion it triggers."""

    def setUp(self):
        super().setUp()
        self.event = self.make_event(capacity=2)
        self.a1, self.a2, self.a3, self.a4 = self.make_attendees(4)
        self.r1 = self.make_registration(self.a1, self.event, "registered")
        self.r2 = self.make_registration(self.a2, self.event, "registered")
        self.r3 = self.make_registration(self.a3, self.event, "waitlisted")
        self.r4 = self.make_registration(self.a4, self.event, "waitlisted")

    def url(self, registration):
        return f"/api/registrations/{registration.id}/cancel/"

    def cancel(self, registration, user):
        return self.as_user(user).post(self.url(registration), {}, format="json")

    def status_of(self, registration):
        return Registration.objects.get(pk=registration.pk).status

    def test_cancelling_returns_200_with_the_read_shape_and_keeps_the_row(self):
        response = self.cancel(self.r1, self.a1)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(set(data), READ_KEYS)
        self.assertEqual(data["id"], self.r1.id)
        self.assertEqual(data["status"], "cancelled")
        self.assertIsNone(data["waitlist_position"])
        self.assertEqual(self.status_of(self.r1), "cancelled")

    def test_cancelling_a_seat_promotes_the_oldest_waitlisted_registration(self):
        self.cancel(self.r1, self.a1)
        self.assertEqual(self.status_of(self.r3), "registered")
        self.assertEqual(self.status_of(self.r4), "waitlisted")
        note = Notification.objects.get()
        self.assertEqual(note.recipient, self.a3)
        self.assertEqual(note.kind, "promoted")
        self.assertEqual(note.event, self.event)
        self.assertEqual(note.registration, self.r3)
        self.assertEqual(
            note.message,
            "A seat opened up and you are now registered for Counts Meetup.",
        )

    def test_the_person_who_cancels_gets_no_notification(self):
        self.cancel(self.r1, self.a1)
        self.assertEqual(Notification.objects.filter(recipient=self.a1).count(), 0)

    def test_the_next_person_moves_up_the_waitlist(self):
        self.cancel(self.r1, self.a1)
        response = self.as_user(self.a4).get(f"/api/registrations/{self.r4.id}/")
        self.assertEqual(response.json()["waitlist_position"], 1)

    def test_only_as_many_people_are_promoted_as_seats_were_freed(self):
        self.cancel(self.r2, self.a2)
        promoted = Registration.objects.filter(event=self.event, status="registered").count()
        self.assertEqual(promoted, 2)
        self.assertEqual(Notification.objects.filter(kind="promoted").count(), 1)

    def test_promotion_goes_by_created_at_then_id(self):
        earlier = self.r3.created_at - timedelta(hours=1)
        Registration.objects.filter(pk=self.r4.pk).update(created_at=earlier)
        self.cancel(self.r1, self.a1)
        self.assertEqual(self.status_of(self.r4), "registered")
        self.assertEqual(self.status_of(self.r3), "waitlisted")

    def test_cancelling_a_waitlisted_registration_promotes_nobody(self):
        response = self.cancel(self.r3, self.a3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.status_of(self.r3), "cancelled")
        self.assertEqual(self.status_of(self.r1), "registered")
        self.assertEqual(self.status_of(self.r2), "registered")
        self.assertEqual(self.status_of(self.r4), "waitlisted")
        self.assertEqual(Notification.objects.count(), 0)

    def test_cancelling_with_nobody_waiting_promotes_nobody(self):
        event = self.make_event(title="Quiet event")
        only = self.make_registration(self.a1, event, "registered")
        self.assertEqual(self.cancel(only, self.a1).status_code, 200)
        self.assertEqual(Notification.objects.count(), 0)

    def test_a_freed_seat_can_be_taken_by_a_new_registration(self):
        event = self.make_event(title="One seat", capacity=1)
        holder = self.make_registration(self.a1, event, "registered")
        self.cancel(holder, self.a1)
        body = {"event": event.id, "full_name": "New Person", "phone": "+2348012345678"}
        response = self.as_user(self.a2).post("/api/registrations/", body, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "registered")

    def test_cancelling_after_the_deadline_is_allowed_and_still_promotes(self):
        past = timezone.now() - timedelta(hours=1)
        Event.objects.filter(pk=self.event.pk).update(registration_deadline=past)
        response = self.cancel(self.r1, self.a1)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.status_of(self.r3), "registered")

    def test_cancelling_after_the_event_started_is_refused(self):
        event = self.started_event(title="Already running")
        registration = self.make_registration(self.a1, event, "registered")
        response = self.cancel(registration, self.a1)
        self.assert_error(response, 400, "event_started", "This event has already started.")
        self.assertEqual(self.status_of(registration), "registered")

    def test_cancelling_twice_gives_already_cancelled(self):
        self.cancel(self.r1, self.a1)
        response = self.cancel(self.r1, self.a1)
        self.assert_error(response, 400, "already_cancelled", "This registration is already cancelled.")

    def test_a_registration_ended_by_the_organizer_gives_already_cancelled(self):
        Registration.objects.filter(pk=self.r1.pk).update(status="event_cancelled")
        response = self.cancel(self.r1, self.a1)
        self.assert_error(response, 400, "already_cancelled")

    def test_already_cancelled_is_checked_before_event_started(self):
        event = self.started_event(title="Ran and cancelled")
        registration = self.make_registration(self.a1, event, "cancelled")
        self.assert_error(self.cancel(registration, self.a1), 400, "already_cancelled")

    def test_someone_elses_registration_is_a_404(self):
        response = self.cancel(self.r1, self.a2)
        self.assert_error(response, 404, "not_found")
        self.assertEqual(self.status_of(self.r1), "registered")

    def test_an_unknown_registration_is_a_404(self):
        response = self.as_user(self.a1).post("/api/registrations/999999/cancel/", {}, format="json")
        self.assert_error(response, 404, "not_found")

    def test_organizer_gets_403(self):
        response = self.cancel(self.r1, self.organizer)
        self.assert_error(response, 403, "permission_denied")
        self.assertEqual(self.status_of(self.r1), "registered")

    def test_anonymous_user_gets_401(self):
        response = APIClient().post(self.url(self.r1), {}, format="json")
        self.assert_error(response, 401, "not_authenticated")

    def test_get_is_not_allowed(self):
        response = self.as_user(self.a1).get(self.url(self.r1))
        self.assert_error(response, 405, "method_not_allowed")

    def test_the_body_is_ignored(self):
        response = self.as_user(self.a1).post(self.url(self.r1), {"status": "registered"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")

    def test_cancelling_takes_a_row_lock_on_the_event(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.cancel(self.r1, self.a1)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("FOR UPDATE" in q["sql"].upper() for q in queries.captured_queries))

    def test_a_failure_while_promoting_saves_nothing(self):
        client = self.as_user(self.a1)
        client.raise_request_exception = False
        with mock.patch.object(Notification, "save", side_effect=RuntimeError("boom")):
            with self.assertLogs("django.request", level="ERROR"):
                response = client.post(self.url(self.r1), {}, format="json")
        self.assert_error(response, 500, "server_error")
        self.assertEqual(self.status_of(self.r1), "registered")
        self.assertEqual(self.status_of(self.r3), "waitlisted")
