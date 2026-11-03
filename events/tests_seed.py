"""Tests for the demo data command."""

import re
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from events.models import Event
from registrations.models import Notification, Registration

User = get_user_model()

DEMO_PASSWORD = "EventDemo123!"
PHONE_FORMAT = re.compile(r"^\+[1-9][0-9]{7,14}$")


class SeedDemoTests(TestCase):
    """Checks what seed_demo creates and that running it twice changes nothing."""

    def seed(self):
        out = StringIO()
        call_command("seed_demo", stdout=out)
        return out.getvalue()

    def organizer(self):
        return User.objects.get(username="demo_organizer")

    def events(self):
        return list(Event.objects.filter(organizer=self.organizer()).order_by("start_at"))

    def login(self, username, password=DEMO_PASSWORD):
        client = APIClient()
        response = client.post(
            "/api/auth/login/", {"username": username, "password": password}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        client.credentials(HTTP_AUTHORIZATION="Bearer " + response.json()["access"])
        return client

    def test_it_creates_the_two_demo_users_with_the_right_roles(self):
        self.seed()
        organizer = self.organizer()
        attendee = User.objects.get(username="demo_attendee")
        self.assertEqual(organizer.role, "organizer")
        self.assertEqual(attendee.role, "attendee")
        self.assertEqual(organizer.email, "demo_organizer@example.com")
        self.assertEqual(attendee.email, "demo_attendee@example.com")
        self.assertTrue(organizer.check_password(DEMO_PASSWORD))
        self.assertTrue(attendee.check_password(DEMO_PASSWORD))
        self.assertFalse(organizer.is_staff)
        self.assertFalse(attendee.is_staff)

    def test_it_prints_what_it_did(self):
        self.assertTrue(self.seed().strip())

    def test_it_creates_three_future_scheduled_events_for_the_organizer(self):
        self.seed()
        events = self.events()
        self.assertEqual(len(events), 3)
        self.assertEqual({event.status for event in events}, {"scheduled"})
        self.assertEqual(Event.objects.count(), 3)
        now = timezone.now()
        for event, (low, high) in zip(events, ((6, 8), (13, 15), (20, 22)), strict=True):
            with self.subTest(title=event.title):
                self.assertGreater(event.start_at, now + timedelta(days=low))
                self.assertLess(event.start_at, now + timedelta(days=high))

    def test_the_events_use_each_type_in_order_and_different_capacities(self):
        self.seed()
        events = self.events()
        self.assertEqual([event.event_type for event in events], ["in_person", "online", "hybrid"])
        capacities = [event.capacity for event in events]
        self.assertEqual(len(set(capacities)), 3)
        self.assertIn(5, capacities)
        self.assertEqual(len({event.title for event in events}), 3)

    def test_every_event_follows_the_event_rules(self):
        self.seed()
        for event in self.events():
            with self.subTest(title=event.title):
                self.assertTrue(3 <= len(event.title) <= 200)
                self.assertTrue(1 <= len(event.description) <= 5000)
                self.assertEqual(event.end_at - event.start_at, timedelta(hours=2))
                self.assertEqual(event.registration_deadline, event.start_at)
                self.assertTrue(1 <= event.capacity <= 100000)
                self.assertIn("@", event.contact_email)
                self.assertRegex(event.contact_phone, PHONE_FORMAT)
                if event.event_type in ("in_person", "hybrid"):
                    self.assertTrue(event.venue_name)
                    self.assertTrue(event.address)
                    self.assertTrue(event.city)
                if event.event_type in ("online", "hybrid"):
                    self.assertTrue(event.online_link.startswith("https://"))

    def test_it_creates_no_registrations_and_no_notifications(self):
        self.seed()
        self.assertEqual(Registration.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_running_it_twice_changes_nothing(self):
        self.seed()
        user_ids = sorted(User.objects.values_list("id", flat=True))
        event_ids = sorted(Event.objects.values_list("id", flat=True))
        self.seed()
        self.assertEqual(sorted(User.objects.values_list("id", flat=True)), user_ids)
        self.assertEqual(sorted(Event.objects.values_list("id", flat=True)), event_ids)
        self.assertEqual(Event.objects.count(), 3)
        self.assertEqual(
            User.objects.filter(username__in=["demo_organizer", "demo_attendee"]).count(), 2
        )

    def test_a_second_run_does_not_reset_a_password_or_overwrite_an_edit(self):
        self.seed()
        organizer = self.organizer()
        organizer.set_password("Changed-Pass-9")
        organizer.save()
        edited = self.events()[0]
        Event.objects.filter(pk=edited.pk).update(capacity=77)
        self.seed()
        organizer.refresh_from_db()
        self.assertTrue(organizer.check_password("Changed-Pass-9"))
        self.assertFalse(organizer.check_password(DEMO_PASSWORD))
        edited.refresh_from_db()
        self.assertEqual(edited.capacity, 77)
        self.assertEqual(Event.objects.count(), 3)

    def test_it_works_when_the_organizer_already_exists(self):
        User.objects.create_user(
            username="demo_organizer",
            email="earlier@example.com",
            password="Earlier-Pass-1",
            role="organizer",
        )
        self.seed()
        self.assertEqual(User.objects.filter(username="demo_organizer").count(), 1)
        organizer = self.organizer()
        self.assertTrue(organizer.check_password("Earlier-Pass-1"))
        self.assertEqual(Event.objects.filter(organizer=organizer).count(), 3)
        self.assertTrue(User.objects.filter(username="demo_attendee").exists())

    def test_both_demo_users_can_sign_in(self):
        self.seed()
        self.login("demo_organizer")
        self.login("demo_attendee")

    def test_the_seeded_events_show_in_the_public_list_with_empty_counts(self):
        self.seed()
        data = APIClient().get("/api/events/").json()
        self.assertEqual(data["count"], 3)
        for item in data["results"]:
            self.assertEqual(item["registered_count"], 0)
            self.assertEqual(item["waitlist_count"], 0)
            self.assertEqual(item["seats_left"], item["capacity"])
            self.assertEqual(item["organizer_username"], "demo_organizer")

    def test_the_organizer_sees_the_three_events_in_my_events(self):
        self.seed()
        data = self.login("demo_organizer").get("/api/my/events/").json()
        self.assertEqual(data["count"], 3)

    def test_the_demo_attendee_can_register_for_a_seeded_event(self):
        self.seed()
        event = self.events()[0]
        client = self.login("demo_attendee")
        response = client.post(
            "/api/registrations/",
            {"event": event.id, "full_name": "Demo Attendee", "phone": "+2348012345678"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["status"], "registered")
