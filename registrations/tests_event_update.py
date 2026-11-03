"""Tests for editing an event: capacity changes, promotion and update notifications."""

from datetime import timedelta
from datetime import timezone as datetime_timezone
from unittest import mock

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from events.models import Event
from registrations.models import Notification, Registration
from registrations.tests_helpers import ApiTestBase

UPDATE_MESSAGE = "{title} has been updated. Please check the new details."
PROMOTED_MESSAGE = "A seat opened up and you are now registered for {title}"


class UpdateTestBase(ApiTestBase):
    """Builds an in person event with registrations in several states."""

    def setUp(self):
        super().setUp()
        self.client = self.as_user(self.organizer)
        self.event = self.place_event()
        self.p1, self.p2, self.p3, self.p4, self.p5 = self.make_attendees(5)
        self.r1 = self.make_registration(self.p1, self.event, "registered")
        self.r2 = self.make_registration(self.p2, self.event, "registered")
        self.r3 = self.make_registration(self.p3, self.event, "waitlisted")
        self.r4 = self.make_registration(self.p4, self.event, "cancelled")
        self.r5 = self.make_registration(self.p5, self.event, "event_cancelled")

    def place_event(self, **overrides):
        values = dict(
            title="Counts Meetup",
            event_type="in_person",
            venue_name="Hall A",
            address="1 Main Street",
            city="Lagos",
            online_link="",
            capacity=2,
        )
        values.update(overrides)
        return self.make_event(**values)

    def patch(self, body, event=None, client=None):
        target = event or self.event
        return (client or self.client).patch(f"/api/events/{target.id}/", body, format="json")

    def status_of(self, registration):
        return Registration.objects.get(pk=registration.pk).status

    def kinds_for(self, person):
        return sorted(Notification.objects.filter(recipient=person).values_list("kind", flat=True))


class CapacityTests(UpdateTestBase):
    """Checks capacity_too_low and promotion when capacity is raised."""

    def test_lowering_capacity_below_the_registered_count_is_refused(self):
        response = self.patch({"capacity": 1})
        self.assert_error(
            response,
            400,
            "capacity_too_low",
            "Capacity cannot be lower than the number of registered attendees.",
        )
        self.event.refresh_from_db()
        self.assertEqual(self.event.capacity, 2)
        self.assertEqual(Notification.objects.count(), 0)

    def test_waitlisted_registrations_do_not_count_toward_the_limit(self):
        event = self.place_event(title="Long waitlist", capacity=5)
        for person in self.make_attendees(2):
            self.make_registration(person, event, "registered")
        for person in self.make_attendees(3):
            self.make_registration(person, event, "waitlisted")
        response = self.patch({"capacity": 2}, event=event)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["capacity"], 2)

    def test_capacity_equal_to_the_registered_count_is_allowed(self):
        event = self.place_event(title="Exactly full", capacity=4)
        for person in self.make_attendees(3):
            self.make_registration(person, event, "registered")
        self.assertEqual(self.patch({"capacity": 3}, event=event).status_code, 200)
        self.assert_error(self.patch({"capacity": 2}, event=event), 400, "capacity_too_low")

    def test_field_errors_come_before_capacity_too_low(self):
        self.assert_field_error(self.patch({"capacity": 1, "title": "x"}), "title")
        for value in (0, 100001):
            with self.subTest(value=value):
                self.assert_field_error(self.patch({"capacity": value}), "capacity")

    def test_raising_capacity_promotes_the_oldest_waitlisted_registration(self):
        r6 = self.make_registration(self.make_attendees(1)[0], self.event, "waitlisted")
        response = self.patch({"capacity": 3})
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["registered_count"], 3)
        self.assertEqual(data["waitlist_count"], 1)
        self.assertEqual(data["seats_left"], 0)
        self.assertEqual(self.status_of(self.r3), "registered")
        self.assertEqual(self.status_of(r6), "waitlisted")
        note = Notification.objects.get(kind="promoted")
        self.assertEqual(note.recipient, self.p3)
        self.assertEqual(note.registration, self.r3)
        self.assertTrue(note.message.startswith(PROMOTED_MESSAGE.format(title="Counts Meetup")))

    def test_raising_capacity_by_more_than_the_waitlist_promotes_everyone_waiting(self):
        r6 = self.make_registration(self.make_attendees(1)[0], self.event, "waitlisted")
        response = self.patch({"capacity": 10})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["registered_count"], 4)
        self.assertEqual(data["waitlist_count"], 0)
        self.assertEqual(data["seats_left"], 6)
        self.assertEqual(self.status_of(r6), "registered")
        self.assertEqual(Notification.objects.filter(kind="promoted").count(), 2)

    def test_promotion_goes_by_created_at_then_id(self):
        r6 = self.make_registration(self.make_attendees(1)[0], self.event, "waitlisted")
        earlier = self.r3.created_at - timedelta(hours=1)
        Registration.objects.filter(pk=r6.pk).update(created_at=earlier)
        self.patch({"capacity": 3})
        self.assertEqual(self.status_of(r6), "registered")
        self.assertEqual(self.status_of(self.r3), "waitlisted")

    def test_raising_capacity_with_nobody_waiting_promotes_nobody(self):
        Registration.objects.filter(pk=self.r3.pk).update(status="cancelled")
        response = self.patch({"capacity": 5})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Notification.objects.count(), 0)

    def test_closed_registrations_are_never_promoted(self):
        self.patch({"capacity": 10})
        self.assertEqual(self.status_of(self.r4), "cancelled")
        self.assertEqual(self.status_of(self.r5), "event_cancelled")

    def test_promotion_still_happens_after_the_deadline(self):
        past = timezone.now() - timedelta(hours=1)
        Event.objects.filter(pk=self.event.pk).update(registration_deadline=past)
        response = self.patch({"capacity": 3})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.status_of(self.r3), "registered")


class UpdateNotificationTests(UpdateTestBase):
    """Checks when event_updated notifications are written and to whom."""

    def iso(self, moment):
        return moment.isoformat()

    def assert_attendees_told(self, event, expected):
        notes = Notification.objects.filter(event=event, kind="event_updated")
        self.assertEqual({note.recipient_id for note in notes}, {p.id for p in expected})
        self.assertEqual(notes.count(), len(expected))
        for note in notes:
            self.assertEqual(note.message, UPDATE_MESSAGE.format(title=event.title))
            self.assertIsNotNone(note.registration)

    def test_a_changed_place_or_time_field_notifies_open_registrations(self):
        base = self.event
        base.refresh_from_db()
        changes = {
            "city": {"city": "Abuja"},
            "address": {"address": "2 Side Road"},
            "venue_name": {"venue_name": "Hall B"},
            "start_at": {"start_at": self.iso(base.start_at + timedelta(hours=1))},
            "end_at": {"end_at": self.iso(base.end_at + timedelta(hours=1))},
            "event_type": {"event_type": "hybrid", "online_link": "https://example.com/live"},
        }
        for name, body in changes.items():
            with self.subTest(field=name):
                event = self.place_event(title=f"Change {name}")
                self.make_registration(self.p1, event, "registered")
                self.make_registration(self.p2, event, "waitlisted")
                self.make_registration(self.p3, event, "cancelled")
                self.make_registration(self.p4, event, "event_cancelled")
                response = self.patch(body, event=event)
                self.assertEqual(response.status_code, 200, response.content)
                self.assert_attendees_told(event, [self.p1, self.p2])

    def test_a_changed_online_link_notifies(self):
        event = self.make_event(
            title="Online talk", event_type="online", online_link="https://example.com/a"
        )
        self.make_registration(self.p1, event, "registered")
        response = self.patch({"online_link": "https://example.com/b"}, event=event)
        self.assertEqual(response.status_code, 200, response.content)
        self.assert_attendees_told(event, [self.p1])

    def test_changing_other_fields_sends_nothing(self):
        self.event.refresh_from_db()
        body = {
            "title": "Renamed Meetup",
            "description": "A new description.",
            "category": "workshop",
            "capacity": 3,
            "registration_deadline": self.iso(self.event.registration_deadline - timedelta(days=1)),
            "contact_email": "new@example.com",
            "contact_phone": "+2348099999999",
        }
        response = self.patch(body)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(Notification.objects.filter(kind="event_updated").exists())

    def test_sending_the_same_values_sends_nothing(self):
        self.event.refresh_from_db()
        offset = datetime_timezone(timedelta(hours=1))
        body = {
            "city": "Lagos",
            "address": "1 Main Street",
            "venue_name": "Hall A",
            "start_at": self.iso(self.event.start_at.astimezone(offset)),
            "end_at": self.iso(self.event.end_at.astimezone(offset)),
        }
        response = self.patch(body)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Notification.objects.count(), 0)

    def test_an_empty_edit_sends_nothing(self):
        self.assertEqual(self.patch({}).status_code, 200)
        self.assertEqual(Notification.objects.count(), 0)

    def test_the_message_uses_the_title_saved_by_the_same_edit(self):
        response = self.patch({"title": "Renamed Meetup", "city": "Abuja"})
        self.assertEqual(response.status_code, 200, response.content)
        message = Notification.objects.filter(recipient=self.p1, kind="event_updated").get().message
        self.assertEqual(message, UPDATE_MESSAGE.format(title="Renamed Meetup"))

    def test_the_organizer_and_closed_registrations_get_nothing(self):
        self.patch({"city": "Abuja"})
        quiet = {self.organizer.id, self.p4.id, self.p5.id}
        self.assertFalse(Notification.objects.filter(recipient_id__in=quiet).exists())

    def test_an_edit_that_promotes_and_moves_the_event_sends_both_notifications(self):
        response = self.patch({"capacity": 3, "city": "Abuja"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.kinds_for(self.p3), ["event_updated", "promoted"])
        self.assertEqual(self.kinds_for(self.p1), ["event_updated"])

    def test_a_failed_validation_sends_nothing_and_changes_nothing(self):
        self.event.refresh_from_db()
        body = {"city": "Abuja", "end_at": self.iso(self.event.start_at - timedelta(hours=1))}
        self.assert_field_error(self.patch(body), "end_at")
        self.event.refresh_from_db()
        self.assertEqual(self.event.city, "Lagos")
        self.assertEqual(Notification.objects.count(), 0)

    def test_an_event_with_nobody_registered_can_be_moved(self):
        event = self.place_event(title="Empty room")
        response = self.patch({"city": "Abuja"}, event=event)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Notification.objects.count(), 0)


class UpdateSafetyTests(UpdateTestBase):
    """Checks the lock, the single transaction and the state rules on edit."""

    def test_editing_takes_a_row_lock_on_the_event(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.patch({"capacity": 3})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("FOR UPDATE" in q["sql"].upper() for q in queries.captured_queries))

    def test_a_failure_while_saving_a_notification_saves_nothing(self):
        client = self.as_user(self.organizer)
        client.raise_request_exception = False
        with mock.patch.object(Notification, "save", side_effect=RuntimeError("boom")):
            with self.assertLogs("django.request", level="ERROR"):
                response = self.patch({"capacity": 3, "city": "Abuja"}, client=client)
        self.assert_error(response, 500, "server_error")
        self.event.refresh_from_db()
        self.assertEqual(self.event.capacity, 2)
        self.assertEqual(self.event.city, "Lagos")
        self.assertEqual(self.status_of(self.r3), "waitlisted")
        self.assertEqual(Notification.objects.count(), 0)

    def test_a_cancelled_event_cannot_be_edited_and_nobody_is_told(self):
        event = self.place_event(title="Called off", status="cancelled", cancel_reason="Rain")
        self.make_registration(self.p1, event, "event_cancelled")
        response = self.patch({"city": "Abuja"}, event=event)
        self.assert_error(response, 400, "event_cancelled")
        self.assertEqual(Notification.objects.count(), 0)

    def test_a_started_event_cannot_be_edited_and_nobody_is_told(self):
        event = self.started_event(
            title="Already running",
            event_type="in_person",
            venue_name="Hall",
            address="1 Main",
            city="Lagos",
        )
        self.make_registration(self.p1, event, "registered")
        response = self.patch({"city": "Abuja"}, event=event)
        self.assert_error(response, 400, "event_started")
        self.assertEqual(Notification.objects.count(), 0)
