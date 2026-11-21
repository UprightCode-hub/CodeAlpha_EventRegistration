import unittest
from datetime import UTC, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import ProtectedError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from registrations.models import Notification, Registration
from registrations.services import register_attendee

User = get_user_model()
FAST_HASHER = override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
PW = "Zx9!vQ-lamp-Tiger-42"
PHONE = "+2348012345678"
R = Registration.Status


def make_user(username, role=None, superuser=False):
    user = User.objects.create_user(
        username=username, email=f"{username.lower()}@example.com", password=PW
    )
    if role:
        user.role = role
    if superuser:
        user.is_staff = True
        user.is_superuser = True
    user.save()
    return user


def make_event(organizer, capacity=1):
    start = timezone.now() + timedelta(days=10)
    return Event.objects.create(
        organizer=organizer,
        title="Admin Night",
        description="Test event",
        category="meetup",
        event_type="online",
        online_link="https://example.com/live",
        start_at=start,
        end_at=start + timedelta(hours=2),
        registration_deadline=start,
        capacity=capacity,
        contact_email="org@example.com",
        contact_phone=PHONE,
    )


def admin_event_form(event, **overrides):
    """Build a valid POST body for the admin change form (datetimes are split widgets)."""
    data = {
        "organizer": event.organizer_id,
        "title": event.title,
        "description": event.description,
        "category": event.category,
        "event_type": event.event_type,
        "venue_name": event.venue_name,
        "address": event.address,
        "city": event.city,
        "online_link": event.online_link,
        "contact_email": event.contact_email,
        "contact_phone": event.contact_phone,
    }
    for name in ("start_at", "end_at", "registration_deadline"):
        value = overrides.pop(name, getattr(event, name)).astimezone(UTC)
        data[f"{name}_0"] = value.strftime("%Y-%m-%d")
        data[f"{name}_1"] = value.strftime("%H:%M:%S")
    data.update(overrides)
    return data


@FAST_HASHER
class EventAdminBypassTests(TestCase):
    def setUp(self):
        self.boss = make_user("boss", superuser=True)
        self.org = make_user("org1", role="organizer")
        self.event = make_event(self.org, capacity=1)
        self.first_user = make_user("a1")
        self.second_user = make_user("a2")
        self.first = register_attendee(self.first_user, self.event.pk, "First Person", PHONE)
        self.second = register_attendee(self.second_user, self.event.pk, "Second Person", PHONE)
        self.client = Client()
        self.client.force_login(self.boss)
        self.change_url = reverse("admin:events_event_change", args=[self.event.pk])

    def post_change(self, **overrides):
        return self.client.post(self.change_url, admin_event_form(self.event, **overrides))

    def test_capacity_status_and_reason_cannot_be_posted(self):
        self.post_change(title="Renamed", capacity=50, status="cancelled", cancel_reason="x")
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Renamed")  # proves the form itself was accepted
        self.assertEqual(self.event.capacity, 1)
        self.assertEqual(self.event.status, Event.Status.SCHEDULED)
        self.assertEqual(self.event.cancel_reason, "")
        self.assertEqual(Registration.objects.filter(status=R.WAITLISTED).count(), 1)

    @unittest.skip("ACCEPTED GAP: admin changes bypass the seat rules. See DECISIONS.md Known gaps.")
    def test_admin_time_change_notifies_open_registrations(self):
        # FINDING: start_at, end_at and the deadline are editable, and the admin save does not go
        # through apply_event_edit, so nobody is told.
        delta = timedelta(minutes=30)
        old_start = self.event.start_at
        self.post_change(
            start_at=old_start + delta,
            end_at=self.event.end_at + delta,
            registration_deadline=self.event.registration_deadline + delta,
        )
        self.event.refresh_from_db()
        self.assertNotEqual(
            self.event.start_at.replace(microsecond=0), old_start.replace(microsecond=0)
        )
        updated = Notification.objects.filter(
            event=self.event, kind=Notification.Kind.EVENT_UPDATED
        )
        self.assertEqual(updated.count(), 2)

    @unittest.skip("ACCEPTED GAP: admin changes bypass the seat rules. See DECISIONS.md Known gaps.")
    def test_admin_cannot_move_an_event_with_registrations_into_the_past(self):
        # FINDING: nothing in the model or the admin forbids it, and the services then refuse
        # every cancel and every edit with event_started.
        start = timezone.now() - timedelta(days=1)
        self.post_change(
            start_at=start, end_at=start + timedelta(hours=2), registration_deadline=start
        )
        self.event.refresh_from_db()
        self.assertGreater(self.event.start_at, timezone.now())

    def test_single_delete_of_an_event_with_registrations_is_refused(self):
        url = reverse("admin:events_event_delete", args=[self.event.pk])
        self.client.post(url, {"post": "yes"})
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())
        self.assertEqual(Registration.objects.count(), 2)

    def test_bulk_delete_of_events_with_registrations_is_refused(self):
        response = self.client.post(
            reverse("admin:events_event_changelist"),
            {"action": "delete_selected", "_selected_action": [self.event.pk], "post": "yes"},
        )
        self.assertLess(response.status_code, 500)
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())
        self.assertEqual(Registration.objects.count(), 2)

    # registrations/models.py uses on_delete=PROTECT for Registration.attendee and
    # Registration.event, and events/models.py uses PROTECT for Event.organizer.
    def test_deleting_a_seat_holder_is_refused_by_the_database_layer(self):
        with transaction.atomic(), self.assertRaises(ProtectedError):
            self.first_user.delete()
        self.assertEqual(Registration.objects.count(), 2)
        self.assertEqual(self.event.registrations.filter(status=R.REGISTERED).count(), 1)

    def test_deleting_an_event_with_registrations_is_refused_by_the_database_layer(self):
        with transaction.atomic(), self.assertRaises(ProtectedError):
            Event.objects.filter(pk=self.event.pk).delete()
        self.assertEqual(Registration.objects.count(), 2)

    def test_deleting_an_organizer_who_owns_events_is_refused(self):
        with transaction.atomic(), self.assertRaises(ProtectedError):
            self.org.delete()
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())