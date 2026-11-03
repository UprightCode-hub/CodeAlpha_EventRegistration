"""Tests for the registration reads, the notification list and the organizer list."""

from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient

from registrations.models import Notification, Registration
from registrations.tests_helpers import (
    BAD_PAGES,
    NOTIFICATION_KEYS,
    ORGANIZER_READ_KEYS,
    READ_KEYS,
    ApiTestBase,
)

LIST_URL = "/api/registrations/"


class AttendeeListTests(ApiTestBase):
    """Checks GET /api/registrations/ for the signed-in attendee."""

    def setUp(self):
        super().setUp()
        self.client = self.as_user(self.attendee)
        self.other = self.make_attendees(1)[0]

    def ids(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        return [item["id"] for item in response.json()["results"]]

    def test_only_the_callers_registrations_are_listed(self):
        mine = self.make_registration(self.attendee, self.make_event(title="Mine"), "registered")
        self.make_registration(self.other, self.make_event(title="Theirs"), "registered")
        self.assertEqual(self.ids(self.client.get(LIST_URL)), [mine.id])

    def test_items_use_the_read_shape_and_the_page_shape(self):
        self.make_registration(self.attendee, self.make_event(), "registered")
        data = self.client.get(LIST_URL).json()
        self.assertEqual(set(data), {"count", "next", "previous", "results"})
        self.assertEqual(set(data["results"][0]), READ_KEYS)
        self.assertNotIn("attendee_username", data["results"][0])
        self.assertNotIn("attendee_email", data["results"][0])

    def test_newest_first_then_highest_id_first(self):
        now = timezone.now()
        old = self.make_registration(self.attendee, self.make_event(title="Old"), "registered")
        new = self.make_registration(self.attendee, self.make_event(title="New"), "registered")
        tie_a = self.make_registration(self.attendee, self.make_event(title="Tie A"), "registered")
        tie_b = self.make_registration(self.attendee, self.make_event(title="Tie B"), "registered")
        Registration.objects.filter(pk=old.pk).update(created_at=now - timedelta(hours=5))
        Registration.objects.filter(pk=new.pk).update(created_at=now)
        Registration.objects.filter(pk__in=[tie_a.pk, tie_b.pk]).update(
            created_at=now - timedelta(hours=1)
        )
        self.assertEqual(
            self.ids(self.client.get(LIST_URL)),
            [new.id, tie_b.id, tie_a.id, old.id],
        )

    def test_the_page_size_is_ten(self):
        for number in range(11):
            self.make_registration(
                self.attendee, self.make_event(title=f"Event {number}"), "registered"
            )
        first = self.client.get(LIST_URL).json()
        self.assertEqual(first["count"], 11)
        self.assertEqual(len(first["results"]), 10)
        self.assertIsNotNone(first["next"])
        self.assertEqual(len(self.client.get(LIST_URL, {"page": 2}).json()["results"]), 1)
        self.assertEqual(len(self.client.get(LIST_URL, {"page_size": 50}).json()["results"]), 10)

    def test_invalid_pages_are_404(self):
        self.make_registration(self.attendee, self.make_event(), "registered")
        for page in BAD_PAGES:
            with self.subTest(page=page):
                self.assert_error(self.client.get(LIST_URL, {"page": page}), 404, "not_found")

    def test_the_status_filter(self):
        held = self.make_registration(self.attendee, self.make_event(title="A"), "registered")
        waiting = self.make_registration(self.attendee, self.make_event(title="B"), "waitlisted")
        gone = self.make_registration(self.attendee, self.make_event(title="C"), "cancelled")
        called_off = self.make_registration(
            self.attendee, self.make_event(title="D"), "event_cancelled"
        )
        cases = {
            "registered": [held.id],
            "waitlisted": [waiting.id],
            "cancelled": [gone.id],
            "event_cancelled": [called_off.id],
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(self.ids(self.client.get(LIST_URL, {"status": value})), expected)
        self.assertEqual(len(self.ids(self.client.get(LIST_URL, {"status": ""}))), 4)
        self.assertEqual(len(self.ids(self.client.get(LIST_URL, {"colour": "red"}))), 4)

    def test_an_invalid_status_filter_is_a_validation_error(self):
        for value in ("pending", "REGISTERED", "bad\x00value"):
            with self.subTest(value=value):
                self.assert_field_error(self.client.get(LIST_URL, {"status": value}), "status")

    def test_waitlist_positions_are_ranks_within_each_event(self):
        event = self.make_event(title="Crowded", capacity=1)
        holder, w1, w2, w3 = self.make_attendees(4)
        self.make_registration(holder, event, "registered")
        gone = self.make_registration(self.other, event, "cancelled")
        r1 = self.make_registration(w1, event, "waitlisted")
        r2 = self.make_registration(w2, event, "waitlisted")
        r3 = self.make_registration(w3, event, "waitlisted")
        for registration, expected in ((r1, 1), (r2, 2), (r3, 3)):
            with self.subTest(expected=expected):
                data = self.as_user(registration.attendee).get(LIST_URL).json()["results"][0]
                self.assertEqual(data["waitlist_position"], expected)
        other_event = self.make_event(title="Elsewhere", capacity=1)
        self.make_registration(holder, other_event, "registered")
        mine = self.make_registration(self.attendee, other_event, "waitlisted")
        data = self.client.get(LIST_URL).json()["results"][0]
        self.assertEqual(data["id"], mine.id)
        self.assertEqual(data["waitlist_position"], 1)
        self.assertIsNone(
            self.as_user(gone.attendee).get(LIST_URL).json()["results"][0]["waitlist_position"]
        )

    def test_organizer_gets_403(self):
        self.assert_error(self.as_user(self.organizer).get(LIST_URL), 403, "permission_denied")

    def test_anonymous_user_gets_401(self):
        self.assert_error(APIClient().get(LIST_URL), 401, "not_authenticated")

    def test_the_query_count_does_not_grow_with_the_page_size(self):
        def make_pair(number):
            event = self.make_event(title=f"Pair {number}", capacity=1)
            holder = self.make_attendees(1)[0]
            self.make_registration(holder, event, "registered")
            self.make_registration(self.make_attendees(1)[0], event, "waitlisted")
            self.make_registration(self.attendee, event, "waitlisted")

        make_pair(0)
        make_pair(1)

        def grow():
            for number in range(2, 10):
                make_pair(number)

        self.assert_flat_queries(self.client, LIST_URL, grow, small=2, large=10)


class RegistrationDetailTests(ApiTestBase):
    """Checks GET /api/registrations/<id>/ for the owning attendee."""

    def setUp(self):
        super().setUp()
        self.event = self.make_event(capacity=1)
        holder = self.make_attendees(1)[0]
        self.make_registration(holder, self.event, "registered")
        self.registration = self.make_registration(self.attendee, self.event, "waitlisted")
        self.url = f"/api/registrations/{self.registration.id}/"
        self.client = self.as_user(self.attendee)

    def test_the_owner_sees_the_read_shape_with_the_waitlist_position(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), READ_KEYS)
        self.assertEqual(data["id"], self.registration.id)
        self.assertEqual(data["status"], "waitlisted")
        self.assertEqual(data["waitlist_position"], 1)
        self.assertEqual(data["event"], self.event.id)

    def test_someone_elses_registration_is_a_404(self):
        other = self.make_attendees(1)[0]
        self.assert_error(self.as_user(other).get(self.url), 404, "not_found")

    def test_an_unknown_registration_is_a_404(self):
        self.assert_error(self.client.get("/api/registrations/999999/"), 404, "not_found")

    def test_organizer_gets_403(self):
        self.assert_error(self.as_user(self.organizer).get(self.url), 403, "permission_denied")

    def test_anonymous_user_gets_401(self):
        self.assert_error(APIClient().get(self.url), 401, "not_authenticated")

    def test_other_methods_are_not_allowed(self):
        for method in ("put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.url, {}, format="json")
                self.assert_error(response, 405, "method_not_allowed")


class NotificationListTests(ApiTestBase):
    """Checks GET /api/notifications/ for any signed-in user."""

    URL = "/api/notifications/"

    def setUp(self):
        super().setUp()
        self.client = self.as_user(self.attendee)
        self.event = self.make_event()

    def make_note(
        self, recipient, kind="promoted", registration=None, event=None, message="Hello."
    ):
        return Notification.objects.create(
            recipient=recipient,
            event=event or self.event,
            registration=registration,
            kind=kind,
            message=message,
        )

    def ids(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        return [item["id"] for item in response.json()["results"]]

    def test_only_the_callers_notifications_are_listed(self):
        mine = self.make_note(self.attendee)
        self.make_note(self.make_attendees(1)[0])
        self.make_note(self.organizer)
        self.assertEqual(self.ids(self.client.get(self.URL)), [mine.id])

    def test_an_organizer_sees_their_own_list(self):
        mine = self.make_note(self.organizer, kind="event_updated")
        self.assertEqual(self.ids(self.as_user(self.organizer).get(self.URL)), [mine.id])

    def test_items_use_the_read_shape(self):
        registration = self.make_registration(self.attendee, self.event, "registered")
        self.make_note(self.attendee, registration=registration)
        self.make_note(self.attendee, kind="event_updated")
        data = self.client.get(self.URL).json()
        self.assertEqual(set(data), {"count", "next", "previous", "results"})
        for item in data["results"]:
            self.assertEqual(set(item), NOTIFICATION_KEYS)
            self.assertEqual(item["event"], self.event.id)
            self.assertEqual(item["event_title"], "Counts Meetup")
        by_kind = {item["kind"]: item for item in data["results"]}
        self.assertEqual(by_kind["promoted"]["registration"], registration.id)
        self.assertIsNone(by_kind["event_updated"]["registration"])

    def test_newest_first_then_highest_id_first(self):
        now = timezone.now()
        old = self.make_note(self.attendee)
        new = self.make_note(self.attendee)
        tie_a = self.make_note(self.attendee)
        tie_b = self.make_note(self.attendee)
        Notification.objects.filter(pk=old.pk).update(created_at=now - timedelta(hours=5))
        Notification.objects.filter(pk=new.pk).update(created_at=now)
        Notification.objects.filter(pk__in=[tie_a.pk, tie_b.pk]).update(
            created_at=now - timedelta(hours=1)
        )
        self.assertEqual(self.ids(self.client.get(self.URL)), [new.id, tie_b.id, tie_a.id, old.id])

    def test_the_page_size_is_ten_and_bad_pages_are_404(self):
        for _ in range(11):
            self.make_note(self.attendee)
        data = self.client.get(self.URL).json()
        self.assertEqual(data["count"], 11)
        self.assertEqual(len(data["results"]), 10)
        self.assertEqual(len(self.client.get(self.URL, {"page": 2}).json()["results"]), 1)
        for page in BAD_PAGES:
            with self.subTest(page=page):
                self.assert_error(self.client.get(self.URL, {"page": page}), 404, "not_found")

    def test_anonymous_user_gets_401(self):
        self.assert_error(APIClient().get(self.URL), 401, "not_authenticated")

    def test_other_methods_are_not_allowed(self):
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.URL, {}, format="json")
                self.assert_error(response, 405, "method_not_allowed")

    def test_the_query_count_does_not_grow_with_the_page_size(self):
        def make_one(number):
            event = self.make_event(title=f"Note event {number}")
            registration = self.make_registration(self.attendee, event, "registered")
            self.make_note(self.attendee, registration=registration, event=event)

        make_one(0)
        make_one(1)

        def grow():
            for number in range(2, 10):
                make_one(number)

        self.assert_flat_queries(self.client, self.URL, grow, small=2, large=10)


class OrganizerRegistrationListTests(ApiTestBase):
    """Checks GET /api/events/<id>/registrations/ for the owning organizer."""

    def setUp(self):
        super().setUp()
        self.client = self.as_user(self.organizer)
        self.event = self.make_event(capacity=1)
        self.url = f"/api/events/{self.event.id}/registrations/"

    def ids(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        return [item["id"] for item in response.json()["results"]]

    def test_every_status_is_listed_oldest_first_then_by_id(self):
        now = timezone.now()
        people = self.make_attendees(4)
        late = self.make_registration(people[0], self.event, "registered")
        early = self.make_registration(people[1], self.event, "cancelled")
        tie_a = self.make_registration(people[2], self.event, "waitlisted")
        tie_b = self.make_registration(people[3], self.event, "event_cancelled")
        Registration.objects.filter(pk=late.pk).update(created_at=now)
        Registration.objects.filter(pk=early.pk).update(created_at=now - timedelta(hours=5))
        Registration.objects.filter(pk__in=[tie_a.pk, tie_b.pk]).update(
            created_at=now - timedelta(hours=1)
        )
        self.assertEqual(
            self.ids(self.client.get(self.url)),
            [early.id, tie_a.id, tie_b.id, late.id],
        )

    def test_items_carry_the_attendee_username_and_email(self):
        person = self.make_attendees(1)[0]
        self.make_registration(person, self.event, "registered")
        data = self.client.get(self.url).json()
        self.assertEqual(set(data), {"count", "next", "previous", "results"})
        item = data["results"][0]
        self.assertEqual(set(item), ORGANIZER_READ_KEYS)
        self.assertEqual(item["attendee_username"], person.username)
        self.assertEqual(item["attendee_email"], person.email)

    def test_waitlist_positions_are_shown(self):
        first, second = self.make_attendees(2)
        self.make_registration(self.attendee, self.event, "registered")
        self.make_registration(first, self.event, "waitlisted")
        self.make_registration(second, self.event, "waitlisted")
        positions = [
            item["waitlist_position"] for item in self.client.get(self.url).json()["results"]
        ]
        self.assertEqual(positions, [None, 1, 2])

    def test_registrations_of_other_events_are_not_listed(self):
        self.make_registration(self.attendee, self.make_event(title="Elsewhere"), "registered")
        self.assertEqual(self.client.get(self.url).json()["results"], [])

    def test_a_cancelled_event_can_still_be_read(self):
        self.make_registration(self.attendee, self.event, "event_cancelled")
        self.event.status = "cancelled"
        self.event.cancel_reason = "Weather"
        self.event.save()
        self.assertEqual(len(self.ids(self.client.get(self.url))), 1)

    def test_the_page_size_is_ten_and_bad_pages_are_404(self):
        for person in self.make_attendees(11):
            self.make_registration(person, self.event, "waitlisted")
        data = self.client.get(self.url).json()
        self.assertEqual(data["count"], 11)
        self.assertEqual(len(data["results"]), 10)
        self.assertEqual(len(self.client.get(self.url, {"page": 2}).json()["results"]), 1)
        for page in BAD_PAGES:
            with self.subTest(page=page):
                self.assert_error(self.client.get(self.url, {"page": page}), 404, "not_found")

    def test_an_attendee_gets_403(self):
        self.assert_error(self.as_user(self.attendee).get(self.url), 403, "permission_denied")

    def test_an_organizer_who_does_not_own_the_event_gets_404(self):
        other = self.make_user("org_two", "organizer")
        self.assert_error(self.as_user(other).get(self.url), 404, "not_found")

    def test_an_unknown_event_is_a_404(self):
        self.assert_error(self.client.get("/api/events/999999/registrations/"), 404, "not_found")

    def test_anonymous_user_gets_401(self):
        self.assert_error(APIClient().get(self.url), 401, "not_authenticated")

    def test_other_methods_are_not_allowed(self):
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.url, {}, format="json")
                self.assert_error(response, 405, "method_not_allowed")

    def test_the_query_count_does_not_grow_with_the_page_size(self):
        def make_one():
            person = self.make_attendees(1)[0]
            self.make_registration(person, self.event, "waitlisted")

        make_one()
        make_one()

        def grow():
            for _ in range(8):
                make_one()

        self.assert_flat_queries(self.client, self.url, grow, small=2, large=10)
