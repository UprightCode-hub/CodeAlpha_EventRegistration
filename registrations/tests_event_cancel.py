"""Tests for calling an event off through the cancel endpoint."""

from unittest import mock

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from registrations.models import Notification, Registration
from registrations.tests_helpers import ApiTestBase

EVENT_READ_KEYS = {
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
}


class CancelEventTests(ApiTestBase):
    """Checks POST /api/events/<id>/cancel/ for the owning organizer."""

    def setUp(self):
        super().setUp()
        self.event = self.make_event(capacity=2)
        self.client = self.as_user(self.organizer)
        self.url = f"/api/events/{self.event.id}/cancel/"
        self.p1, self.p2, self.p3, self.p4, self.p5 = self.make_attendees(5)
        self.r1 = self.make_registration(self.p1, self.event, "registered")
        self.r2 = self.make_registration(self.p2, self.event, "registered")
        self.r3 = self.make_registration(self.p3, self.event, "waitlisted")
        self.r4 = self.make_registration(self.p4, self.event, "cancelled")
        self.r5 = self.make_registration(self.p5, self.event, "event_cancelled")

    def cancel(self, body=None, client=None, url=None):
        if body is None:
            body = {"reason": "Venue flooded"}
        return (client or self.client).post(url or self.url, body, format="json")

    def status_of(self, registration):
        return Registration.objects.get(pk=registration.pk).status

    def test_cancelling_returns_200_with_the_event_read_shape(self):
        response = self.cancel()
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(set(data), EVENT_READ_KEYS)
        self.assertEqual(data["id"], self.event.id)
        self.assertEqual(data["status"], "cancelled")
        self.assertEqual(data["cancel_reason"], "Venue flooded")
        self.assertEqual(data["registered_count"], 0)
        self.assertEqual(data["waitlist_count"], 0)
        self.assertEqual(data["seats_left"], 2)

    def test_the_event_is_saved_as_cancelled_with_its_reason(self):
        self.cancel()
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, "cancelled")
        self.assertEqual(self.event.cancel_reason, "Venue flooded")

    def test_open_registrations_end_and_closed_ones_stay_as_they_were(self):
        self.cancel()
        self.assertEqual(self.status_of(self.r1), "event_cancelled")
        self.assertEqual(self.status_of(self.r2), "event_cancelled")
        self.assertEqual(self.status_of(self.r3), "event_cancelled")
        self.assertEqual(self.status_of(self.r4), "cancelled")
        self.assertEqual(self.status_of(self.r5), "event_cancelled")

    def test_each_affected_attendee_gets_one_notification_with_the_reason(self):
        self.cancel()
        notes = Notification.objects.all()
        self.assertEqual(notes.count(), 3)
        self.assertEqual({note.kind for note in notes}, {"event_cancelled"})
        self.assertEqual(
            {note.recipient_id for note in notes}, {self.p1.id, self.p2.id, self.p3.id}
        )
        by_person = {note.recipient_id: note for note in notes}
        self.assertEqual(by_person[self.p1.id].registration, self.r1)
        self.assertEqual(by_person[self.p3.id].registration, self.r3)
        for note in notes:
            self.assertEqual(note.event, self.event)
            self.assertEqual(
                note.message,
                "Counts Meetup has been cancelled. Reason: Venue flooded",
            )

    def test_the_organizer_and_closed_registrations_get_nothing(self):
        self.cancel()
        quiet = {self.organizer.id, self.p4.id, self.p5.id}
        self.assertFalse(Notification.objects.filter(recipient_id__in=quiet).exists())

    def test_nobody_is_promoted(self):
        self.cancel()
        self.assertFalse(Notification.objects.filter(kind="promoted").exists())

    def test_the_reason_is_stored_without_outer_spaces(self):
        response = self.cancel({"reason": "  Rain  "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cancel_reason"], "Rain")
        note = Notification.objects.filter(recipient=self.p1).get()
        self.assertEqual(note.message, "Counts Meetup has been cancelled. Reason: Rain")

    def test_a_long_message_is_cut_to_255_characters(self):
        title = "T" * 200
        reason = "r" * 500
        event = self.make_event(title=title)
        person = self.make_attendees(1)[0]
        self.make_registration(person, event, "registered")
        response = self.cancel({"reason": reason}, url=f"/api/events/{event.id}/cancel/")
        self.assertEqual(response.status_code, 200, response.content)
        message = Notification.objects.get(recipient=person).message
        self.assertEqual(len(message), 255)
        self.assertEqual(message, f"{title} has been cancelled. Reason: {reason}"[:255])

    def test_a_reason_of_500_characters_is_accepted(self):
        self.assertEqual(self.cancel({"reason": "x" * 500}).status_code, 200)

    def test_bad_reasons_are_validation_errors_and_change_nothing(self):
        for body in (
            {},
            {"reason": None},
            {"reason": ""},
            {"reason": "   "},
            {"reason": "x" * 501},
            {"reason": "bad\x00reason"},
        ):
            with self.subTest(body=body):
                self.assert_field_error(self.cancel(body), "reason")
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, "scheduled")
        self.assertEqual(self.status_of(self.r1), "registered")
        self.assertEqual(Notification.objects.count(), 0)

    def test_malformed_json_is_a_validation_error(self):
        response = self.client.post(self.url, "{bad json", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.error_of(response)["code"], "validation_error")

    def test_other_fields_in_the_body_are_ignored(self):
        response = self.cancel({"reason": "Rain", "status": "scheduled", "capacity": 99})
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, "cancelled")
        self.assertEqual(self.event.capacity, 2)

    def test_cancelling_an_event_with_no_registrations_works(self):
        empty = self.make_event(title="Nobody came")
        response = self.cancel(url=f"/api/events/{empty.id}/cancel/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Notification.objects.count(), 0)

    def test_cancelling_twice_gives_event_cancelled(self):
        self.assertEqual(self.cancel().status_code, 200)
        response = self.cancel()
        self.assert_error(response, 400, "event_cancelled", "This event has been cancelled.")

    def test_a_started_event_cannot_be_cancelled(self):
        event = self.started_event(title="Already running")
        response = self.cancel(url=f"/api/events/{event.id}/cancel/")
        self.assert_error(response, 400, "event_started", "This event has already started.")
        event.refresh_from_db()
        self.assertEqual(event.status, "scheduled")

    def test_state_errors_come_before_body_validation_and_cancelled_before_started(self):
        both = self.started_event(
            title="Cancelled and started", status="cancelled", cancel_reason="Rain"
        )
        url = f"/api/events/{both.id}/cancel/"
        self.assert_error(self.cancel({}, url=url), 400, "event_cancelled")
        late = self.started_event(title="Started only")
        self.assert_error(
            self.cancel({}, url=f"/api/events/{late.id}/cancel/"), 400, "event_started"
        )

    def test_anonymous_user_gets_401(self):
        self.assert_error(self.cancel(client=APIClient()), 401, "not_authenticated")

    def test_an_attendee_gets_403(self):
        self.assert_error(self.cancel(client=self.as_user(self.p1)), 403, "permission_denied")
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, "scheduled")

    def test_an_organizer_who_does_not_own_the_event_gets_404(self):
        other = self.make_user("org_two", "organizer")
        self.assert_error(self.cancel(client=self.as_user(other)), 404, "not_found")
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, "scheduled")

    def test_an_unknown_event_is_a_404(self):
        self.assert_error(self.cancel(url="/api/events/999999/cancel/"), 404, "not_found")

    def test_other_methods_are_not_allowed(self):
        for method in ("get", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.url)
                self.assert_error(response, 405, "method_not_allowed")

    def test_a_cancelled_event_stays_visible_but_leaves_the_default_list(self):
        self.cancel()
        detail = self.client.get(self.url.replace("cancel/", ""))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["status"], "cancelled")
        default_ids = [item["id"] for item in self.client.get("/api/events/").json()["results"]]
        self.assertNotIn(self.event.id, default_ids)
        cancelled_ids = [
            item["id"]
            for item in self.client.get("/api/events/", {"status": "cancelled"}).json()["results"]
        ]
        self.assertIn(self.event.id, cancelled_ids)

    def test_attendees_see_their_registration_as_event_cancelled(self):
        self.cancel()
        data = self.as_user(self.p3).get(f"/api/registrations/{self.r3.id}/").json()
        self.assertEqual(data["status"], "event_cancelled")
        self.assertIsNone(data["waitlist_position"])

    def test_cancelling_takes_a_row_lock_on_the_event(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.cancel()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("FOR UPDATE" in q["sql"].upper() for q in queries.captured_queries))

    def test_a_failure_while_saving_a_notification_saves_nothing(self):
        client = self.as_user(self.organizer)
        client.raise_request_exception = False
        with mock.patch.object(Notification, "save", side_effect=RuntimeError("boom")):
            with self.assertLogs("django.request", level="ERROR"):
                response = self.cancel(client=client)
        self.assert_error(response, 500, "server_error")
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, "scheduled")
        self.assertEqual(self.event.cancel_reason, "")
        self.assertEqual(self.status_of(self.r1), "registered")
        self.assertEqual(self.status_of(self.r3), "waitlisted")
