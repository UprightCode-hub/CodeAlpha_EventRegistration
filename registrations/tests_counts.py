"""Tests for the registered, waitlist and seats_left counts on event answers."""

from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from registrations.tests_helpers import ApiTestBase


class EventCountsTests(ApiTestBase):
    """Checks event answer counts, and that detail stays cheap as registrations grow."""

    def detail(self, event):
        return APIClient().get(f"/api/events/{event.id}/")

    def fill(self, event, registered, waitlisted):
        people = self.make_attendees(registered + waitlisted)
        for person in people[:registered]:
            self.make_registration(person, event, "registered")
        for person in people[registered:]:
            self.make_registration(person, event, "waitlisted")

    def test_detail_counts_open_registrations_only(self):
        event = self.make_event(capacity=5)
        self.fill(event, registered=2, waitlisted=1)
        extra = self.make_attendees(2)
        self.make_registration(extra[0], event, "cancelled")
        self.make_registration(extra[1], event, "event_cancelled")
        body = self.detail(event).json()
        self.assertEqual(body["registered_count"], 2)
        self.assertEqual(body["waitlist_count"], 1)
        self.assertEqual(body["seats_left"], 3)

    def test_detail_counts_do_not_leak_between_events(self):
        mine = self.make_event(capacity=5)
        other = self.make_event(capacity=5, title="Another Meetup")
        self.fill(other, registered=3, waitlisted=2)
        body = self.detail(mine).json()
        self.assertEqual(body["registered_count"], 0)
        self.assertEqual(body["waitlist_count"], 0)
        self.assertEqual(body["seats_left"], 5)

    def test_seats_left_never_goes_below_zero(self):
        event = self.make_event(capacity=1)
        self.fill(event, registered=2, waitlisted=0)
        self.assertEqual(self.detail(event).json()["seats_left"], 0)

    def test_detail_query_count_does_not_grow_with_registrations(self):
        event = self.make_event(capacity=50)
        self.detail(event)  # warm up so one-time work is not counted
        with CaptureQueriesContext(connection) as before:
            self.assertEqual(self.detail(event).status_code, 200)
        self.fill(event, registered=6, waitlisted=4)
        with CaptureQueriesContext(connection) as after:
            body = self.detail(event).json()
        self.assertEqual(body["registered_count"], 6)
        self.assertEqual(body["waitlist_count"], 4)
        self.assertEqual(len(before), len(after))

    def test_create_answer_starts_with_zero_counts(self):
        start = timezone.now() + timedelta(days=10)
        payload = {
            "title": "Counts Create Meetup",
            "description": "Created by the counts test.",
            "category": "meetup",
            "event_type": "online",
            "online_link": "https://example.com/live",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=3)).isoformat(),
            "capacity": 7,
            "contact_email": "host@example.com",
            "contact_phone": "+2348012345678",
        }
        response = self.as_user(self.organizer).post("/api/events/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["registered_count"], 0)
        self.assertEqual(body["waitlist_count"], 0)
        self.assertEqual(body["seats_left"], 7)

    def test_update_answer_carries_the_real_counts(self):
        event = self.make_event(capacity=5)
        self.fill(event, registered=2, waitlisted=1)
        response = self.as_user(self.organizer).patch(
            f"/api/events/{event.id}/", {"title": "Renamed Counts Meetup"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["registered_count"], 2)
        self.assertEqual(body["waitlist_count"], 1)
        self.assertEqual(body["seats_left"], 3)

    def test_update_answer_matches_detail_after_a_capacity_raise(self):
        event = self.make_event(capacity=2)
        self.fill(event, registered=2, waitlisted=1)
        response = self.as_user(self.organizer).patch(
            f"/api/events/{event.id}/", {"capacity": 5}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        patched = response.json()
        current = self.detail(event).json()
        for key in ("registered_count", "waitlist_count", "seats_left"):
            self.assertEqual(patched[key], current[key], key)
        self.assertEqual(current["registered_count"], 3)
        self.assertEqual(current["waitlist_count"], 0)
        self.assertEqual(current["seats_left"], 2)

    def test_detail_counts_follow_a_cancel_and_promotion(self):
        event = self.make_event(capacity=1)
        first, second = self.make_attendees(2)
        registration = self.make_registration(first, event, "registered")
        self.make_registration(second, event, "waitlisted")
        response = self.as_user(first).post(f"/api/registrations/{registration.id}/cancel/")
        self.assertEqual(response.status_code, 200, response.content)
        body = self.detail(event).json()
        self.assertEqual(body["registered_count"], 1)
        self.assertEqual(body["waitlist_count"], 0)
        self.assertEqual(body["seats_left"], 0)
