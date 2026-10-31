"""Tests for the registration and notification models and the live event counts."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from events.models import Event
from registrations.models import Notification, Registration

User = get_user_model()

OPEN_STATUSES = ("registered", "waitlisted")
CLOSED_STATUSES = ("cancelled", "event_cancelled")


class RegistrationTestBase(TestCase):
    """Builds users, events and registrations straight through the database layer."""

    def setUp(self):
        self.user_counter = 0
        self.organizer = self.make_user("org_one", "organizer")
        self.attendee = self.make_user("att_one", "attendee")

    def make_user(self, username, role="attendee"):
        user = User(username=username, email=f"{username}@example.com", role=role)
        user.set_password("Str0ng-pass-123")
        user.save()
        return user

    def make_attendees(self, count):
        people = []
        for _ in range(count):
            self.user_counter += 1
            people.append(self.make_user(f"person_{self.user_counter}"))
        return people

    def make_event(self, organizer=None, **overrides):
        start = timezone.now() + timedelta(days=10)
        values = dict(
            organizer=organizer or self.organizer,
            title="Counts Meetup",
            description="A meetup used by the tests.",
            category="meetup",
            event_type="online",
            online_link="https://example.com/live",
            start_at=start,
            end_at=start + timedelta(hours=3),
            registration_deadline=start - timedelta(days=1),
            capacity=3,
            contact_email="host@example.com",
            contact_phone="+2348012345678",
        )
        values.update(overrides)
        return Event.objects.create(**values)

    def make_registration(self, attendee, event, status, **overrides):
        values = dict(
            attendee=attendee,
            event=event,
            status=status,
            full_name="Test Person",
            phone="+2348012345678",
        )
        values.update(overrides)
        return Registration.objects.create(**values)


class RegistrationModelTests(RegistrationTestBase):
    """Checks the Registration fields, status values and defaults."""

    def setUp(self):
        super().setUp()
        self.event = self.make_event()

    def test_a_registration_is_saved_with_its_fields(self):
        registration = self.make_registration(self.attendee, self.event, "registered")
        registration.refresh_from_db()
        self.assertEqual(registration.attendee, self.attendee)
        self.assertEqual(registration.event, self.event)
        self.assertEqual(registration.status, "registered")
        self.assertEqual(registration.full_name, "Test Person")
        self.assertEqual(registration.phone, "+2348012345678")
        self.assertIsNotNone(registration.created_at)
        self.assertIsNotNone(registration.updated_at)

    def test_note_is_blank_by_default(self):
        registration = self.make_registration(self.attendee, self.event, "registered")
        registration.refresh_from_db()
        self.assertEqual(registration.note, "")

    def test_status_has_exactly_the_four_values(self):
        field = Registration._meta.get_field("status")
        self.assertEqual(
            {value for value, _ in field.choices},
            {"registered", "waitlisted", "cancelled", "event_cancelled"},
        )

    def test_status_has_no_default(self):
        self.assertFalse(Registration._meta.get_field("status").has_default())

    def test_text_columns_have_the_contract_lengths(self):
        self.assertEqual(Registration._meta.get_field("full_name").max_length, 120)
        self.assertEqual(Registration._meta.get_field("phone").max_length, 16)
        self.assertEqual(Registration._meta.get_field("note").max_length, 500)

    def test_related_names_reach_the_registrations(self):
        self.make_registration(self.attendee, self.event, "registered")
        self.assertEqual(self.attendee.registrations.count(), 1)
        self.assertEqual(self.event.registrations.count(), 1)

    def test_deleting_an_attendee_with_a_registration_is_refused(self):
        self.make_registration(self.attendee, self.event, "registered")
        with self.assertRaises(ProtectedError):
            self.attendee.delete()

    def test_deleting_an_event_with_a_registration_is_refused(self):
        self.make_registration(self.attendee, self.event, "waitlisted")
        with self.assertRaises(ProtectedError):
            self.event.delete()


class OpenRegistrationConstraintTests(RegistrationTestBase):
    """Checks the conditional unique rule on attendee and event."""

    def setUp(self):
        super().setUp()
        self.event = self.make_event()

    def test_the_constraint_has_the_agreed_name(self):
        names = [constraint.name for constraint in Registration._meta.constraints]
        self.assertIn("unique_open_registration_per_attendee_event", names)

    def test_a_second_open_registration_for_the_same_pair_is_refused(self):
        for first in OPEN_STATUSES:
            for second in OPEN_STATUSES:
                with self.subTest(first=first, second=second):
                    event = self.make_event(title=f"{first} then {second}")
                    self.make_registration(self.attendee, event, first)
                    with self.assertRaises(IntegrityError):
                        with transaction.atomic():
                            self.make_registration(self.attendee, event, second)

    def test_closed_rows_do_not_block_a_new_registration(self):
        for closed in CLOSED_STATUSES:
            with self.subTest(closed=closed):
                event = self.make_event(title=f"after {closed}")
                self.make_registration(self.attendee, event, closed)
                self.make_registration(self.attendee, event, "registered")
                self.assertEqual(event.registrations.count(), 2)

    def test_many_closed_rows_can_exist_for_the_same_pair(self):
        self.make_registration(self.attendee, self.event, "cancelled")
        self.make_registration(self.attendee, self.event, "cancelled")
        self.make_registration(self.attendee, self.event, "event_cancelled")
        self.assertEqual(self.event.registrations.count(), 3)

    def test_a_closed_row_cannot_be_reopened_while_another_is_open(self):
        old = self.make_registration(self.attendee, self.event, "cancelled")
        self.make_registration(self.attendee, self.event, "registered")
        old.status = "waitlisted"
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                old.save()

    def test_other_attendees_and_other_events_are_not_blocked(self):
        other_person = self.make_attendees(1)[0]
        other_event = self.make_event(title="Another event")
        self.make_registration(self.attendee, self.event, "registered")
        self.make_registration(other_person, self.event, "registered")
        self.make_registration(self.attendee, other_event, "registered")
        self.assertEqual(Registration.objects.count(), 3)


class NotificationModelTests(RegistrationTestBase):
    """Checks the Notification fields, kinds and relations."""

    def setUp(self):
        super().setUp()
        self.event = self.make_event()
        self.registration = self.make_registration(self.attendee, self.event, "registered")

    def test_a_notification_is_saved_with_its_fields(self):
        note = Notification.objects.create(
            recipient=self.attendee,
            event=self.event,
            registration=self.registration,
            kind="registration_confirmed",
            message="You are registered for Counts Meetup.",
        )
        note.refresh_from_db()
        self.assertEqual(note.recipient, self.attendee)
        self.assertEqual(note.event, self.event)
        self.assertEqual(note.registration, self.registration)
        self.assertEqual(note.kind, "registration_confirmed")
        self.assertIsNotNone(note.created_at)

    def test_the_registration_is_optional(self):
        note = Notification.objects.create(
            recipient=self.attendee,
            event=self.event,
            kind="event_updated",
            message="Counts Meetup has been updated.",
        )
        note.refresh_from_db()
        self.assertIsNone(note.registration)

    def test_kind_has_exactly_the_five_values(self):
        field = Notification._meta.get_field("kind")
        self.assertEqual(
            {value for value, _ in field.choices},
            {
                "registration_confirmed",
                "waitlisted",
                "promoted",
                "event_cancelled",
                "event_updated",
            },
        )

    def test_message_is_a_character_column_of_255(self):
        self.assertEqual(Notification._meta.get_field("message").max_length, 255)

    def test_related_names_reach_the_notifications(self):
        Notification.objects.create(
            recipient=self.attendee,
            event=self.event,
            registration=self.registration,
            kind="promoted",
            message="A seat opened up.",
        )
        self.assertEqual(self.attendee.notifications.count(), 1)
        self.assertEqual(self.event.notifications.count(), 1)
        self.assertEqual(self.registration.notifications.count(), 1)

    def test_deleting_an_event_that_has_notifications_is_refused(self):
        event = self.make_event(title="Only notified")
        Notification.objects.create(
            recipient=self.attendee,
            event=event,
            kind="event_updated",
            message="Updated.",
        )
        with self.assertRaises(ProtectedError):
            event.delete()


class EventCountTests(RegistrationTestBase):
    """Checks that event responses carry real registered and waitlist counts."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.organizer)
        self.event = self.make_event(capacity=3)
        people = self.make_attendees(5)
        statuses = ["registered", "registered", "waitlisted", "cancelled", "event_cancelled"]
        for person, status in zip(people, statuses):
            self.make_registration(person, self.event, status)

    def assert_counts(self, item, registered, waitlist, seats_left):
        self.assertEqual(item["registered_count"], registered)
        self.assertEqual(item["waitlist_count"], waitlist)
        self.assertEqual(item["seats_left"], seats_left)

    def find(self, response, event):
        self.assertEqual(response.status_code, 200)
        matches = [item for item in response.data["results"] if item["id"] == event.id]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_detail_counts_only_registered_and_waitlisted_rows(self):
        response = self.client.get(f"/api/events/{self.event.id}/")
        self.assertEqual(response.status_code, 200)
        self.assert_counts(response.data, registered=2, waitlist=1, seats_left=1)

    def test_list_shows_the_real_counts(self):
        item = self.find(self.client.get("/api/events/"), self.event)
        self.assert_counts(item, registered=2, waitlist=1, seats_left=1)

    def test_my_events_shows_the_real_counts(self):
        item = self.find(self.client.get("/api/my/events/"), self.event)
        self.assert_counts(item, registered=2, waitlist=1, seats_left=1)

    def test_update_response_shows_the_real_counts(self):
        response = self.client.patch(
            f"/api/events/{self.event.id}/", {"title": "Renamed Meetup"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assert_counts(response.data, registered=2, waitlist=1, seats_left=1)

    def test_registrations_of_another_event_are_not_counted(self):
        empty = self.make_event(title="Nobody yet")
        response = self.client.get("/api/events/")
        self.assert_counts(self.find(response, empty), registered=0, waitlist=0, seats_left=3)
        self.assert_counts(self.find(response, self.event), registered=2, waitlist=1, seats_left=1)

    def test_seats_left_never_goes_below_zero(self):
        crowded = self.make_event(title="Overfull", capacity=1)
        for person in self.make_attendees(2):
            self.make_registration(person, crowded, "registered")
        item = self.find(self.client.get("/api/events/"), crowded)
        self.assert_counts(item, registered=2, waitlist=0, seats_left=0)

    def test_a_cancelled_event_counts_no_seat_holders(self):
        called_off = self.make_event(title="Called off", status="cancelled", cancel_reason="Weather")
        for person in self.make_attendees(2):
            self.make_registration(person, called_off, "event_cancelled")
        response = self.client.get(f"/api/events/{called_off.id}/")
        self.assertEqual(response.status_code, 200)
        self.assert_counts(response.data, registered=0, waitlist=0, seats_left=3)

    def assert_query_count_is_flat(self, url):
        self.make_event(title="Second event")
        self.client.get(url)  # warm up so one-time work is not counted
        with CaptureQueriesContext(connection) as small:
            first = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.data["results"]), 2)
        for number in range(8):
            extra = self.make_event(title=f"Extra event {number}")
            self.make_registration(self.attendee, extra, "registered")
            self.make_registration(self.make_attendees(1)[0], extra, "waitlisted")
        with CaptureQueriesContext(connection) as large:
            second = self.client.get(url)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(second.data["results"]), 10)
        self.assertEqual(len(small), len(large))

    def test_list_query_count_does_not_grow_with_the_page_size(self):
        self.assert_query_count_is_flat("/api/events/")

    def test_my_events_query_count_does_not_grow_with_the_page_size(self):
        self.assert_query_count_is_flat("/api/my/events/")