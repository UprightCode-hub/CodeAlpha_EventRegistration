"""Tests for the Django admin pages of every model."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from events.models import Event
from registrations.models import Notification, Registration

User = get_user_model()

PASSWORD = "Str0ng-Pass-123"
USER_LIST = "/admin/accounts/user/"
EVENT_LIST = "/admin/events/event/"
REGISTRATION_LIST = "/admin/registrations/registration/"
NOTIFICATION_LIST = "/admin/registrations/notification/"


class AdminTestBase(TestCase):
    """Builds a superuser, an organizer and helpers that create rows directly."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin_one", email="admin1@example.com", password=PASSWORD
        )
        self.organizer = User.objects.create_user(
            username="org_one", email="org1@example.com", password=PASSWORD, role="organizer"
        )
        self.client.force_login(self.admin_user)
        self.counter = 0

    def make_attendee(self, username=None):
        self.counter += 1
        name = username or f"att_{self.counter}"
        return User.objects.create_user(
            username=name, email=f"{name}@example.com", password=PASSWORD, role="attendee"
        )

    def make_event(self, title="Admin Meetup", **overrides):
        start = timezone.now() + timedelta(days=10)
        values = dict(
            organizer=self.organizer,
            title=title,
            description="An event for the admin tests.",
            category="meetup",
            event_type="in_person",
            venue_name="Hall A",
            address="1 Main Street",
            city="Lagos",
            online_link="",
            start_at=start,
            end_at=start + timedelta(hours=2),
            registration_deadline=start,
            capacity=10,
            contact_email="host@example.com",
            contact_phone="+2348012345678",
        )
        values.update(overrides)
        return Event.objects.create(**values)

    def make_registration(self, person, event, status, full_name="Ada Okafor"):
        return Registration.objects.create(
            attendee=person,
            event=event,
            status=status,
            full_name=full_name,
            phone="+2348012345678",
        )

    def make_notification(self, person, event, registration=None, kind="registration_confirmed", message="Hello"):
        return Notification.objects.create(
            recipient=person,
            event=event,
            registration=registration,
            kind=kind,
            message=message,
        )

    def result_count(self, url, params=None):
        response = self.client.get(url, params or {})
        self.assertEqual(response.status_code, 200, f"{url} {params}")
        return response.context["cl"].result_count


class AdminPageTests(AdminTestBase):
    """Checks that every admin page of every model opens."""

    def setUp(self):
        super().setUp()
        self.person = self.make_attendee("att_page")
        self.event = self.make_event()
        self.registration = self.make_registration(self.person, self.event, "registered")
        self.note = self.make_notification(self.person, self.event, self.registration)

    def test_every_list_page_opens(self):
        for url in (USER_LIST, EVENT_LIST, REGISTRATION_LIST, NOTIFICATION_LIST):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_every_change_page_opens(self):
        pages = (
            f"{USER_LIST}{self.person.pk}/change/",
            f"{EVENT_LIST}{self.event.pk}/change/",
            f"{REGISTRATION_LIST}{self.registration.pk}/change/",
            f"{NOTIFICATION_LIST}{self.note.pk}/change/",
        )
        for url in pages:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_add_pages_open_for_users_events_and_registrations(self):
        for url in (f"{USER_LIST}add/", f"{EVENT_LIST}add/", f"{REGISTRATION_LIST}add/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_the_list_pages_show_the_rows(self):
        self.assertContains(self.client.get(EVENT_LIST), "Admin Meetup")
        self.assertContains(self.client.get(REGISTRATION_LIST), "Ada Okafor")
        self.assertContains(self.client.get(USER_LIST), "att_page")

    def test_the_event_list_shows_the_real_counts(self):
        self.make_registration(self.make_attendee(), self.event, "registered")
        self.make_registration(self.make_attendee(), self.event, "waitlisted")
        response = self.client.get(EVENT_LIST)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("registered_count", content)
        self.assertIn("waitlist_count", content)

    def test_notifications_cannot_be_added(self):
        self.assertEqual(self.client.get(f"{NOTIFICATION_LIST}add/").status_code, 403)
        before = Notification.objects.count()
        response = self.client.post(
            f"{NOTIFICATION_LIST}add/",
            {
                "recipient": self.person.pk,
                "event": self.event.pk,
                "kind": "promoted",
                "message": "Made by hand",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Notification.objects.count(), before)

    def test_an_event_with_registrations_cannot_be_deleted(self):
        url = f"{EVENT_LIST}{self.event.pk}/delete/"
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {"post": "yes"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())
        self.assertTrue(Registration.objects.filter(pk=self.registration.pk).exists())

    def test_anonymous_and_non_staff_users_are_sent_to_the_login_page(self):
        self.client.logout()
        response = self.client.get(EVENT_LIST)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/admin/login/"))
        self.client.force_login(self.person)
        response = self.client.get(EVENT_LIST)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/admin/login/"))


class AdminSearchAndFilterTests(AdminTestBase):
    """Checks the search boxes and filters on the list pages."""

    def setUp(self):
        super().setUp()
        self.other_organizer = User.objects.create_user(
            username="org_two", email="org2@example.com", password=PASSWORD, role="organizer"
        )
        self.alpha = self.make_event(title="Alpha Summit", city="Lagos")
        self.beta = self.make_event(
            title="Beta Workshop",
            city="Abuja",
            category="workshop",
            event_type="online",
            online_link="https://example.com/beta",
            venue_name="",
            address="",
            organizer=self.other_organizer,
        )
        Event.objects.filter(pk=self.beta.pk).update(status="cancelled", cancel_reason="Rain")
        self.ada = self.make_attendee("att_ada")
        self.bola = self.make_attendee("att_bola")
        self.reg_ada = self.make_registration(self.ada, self.alpha, "registered", "Ada Okafor")
        self.reg_bola = self.make_registration(self.bola, self.alpha, "waitlisted", "Bola Ade")
        self.note_ada = self.make_notification(
            self.ada, self.alpha, self.reg_ada, "registration_confirmed", "Seat confirmed marker"
        )
        self.note_bola = self.make_notification(
            self.bola, self.alpha, self.reg_bola, "waitlisted", "Waiting list entry"
        )

    def test_event_search_by_title_city_and_organizer(self):
        self.assertEqual(self.result_count(EVENT_LIST, {"q": "Alpha"}), 1)
        self.assertEqual(self.result_count(EVENT_LIST, {"q": "Abuja"}), 1)
        self.assertEqual(self.result_count(EVENT_LIST, {"q": "org_two"}), 1)
        self.assertEqual(self.result_count(EVENT_LIST, {"q": "nothing like this"}), 0)

    def test_event_filters(self):
        self.assertEqual(self.result_count(EVENT_LIST, {"status__exact": "cancelled"}), 1)
        self.assertEqual(self.result_count(EVENT_LIST, {"status__exact": "scheduled"}), 1)
        self.assertEqual(self.result_count(EVENT_LIST, {"category__exact": "workshop"}), 1)
        self.assertEqual(self.result_count(EVENT_LIST, {"event_type__exact": "in_person"}), 1)

    def test_registration_search(self):
        self.assertEqual(self.result_count(REGISTRATION_LIST, {"q": "Okafor"}), 1)
        self.assertEqual(self.result_count(REGISTRATION_LIST, {"q": "att_bola"}), 1)
        self.assertEqual(self.result_count(REGISTRATION_LIST, {"q": "att_ada@example.com"}), 1)
        self.assertEqual(self.result_count(REGISTRATION_LIST, {"q": "+2348012345678"}), 2)
        self.assertEqual(self.result_count(REGISTRATION_LIST, {"q": "Alpha"}), 2)

    def test_registration_status_filter(self):
        self.assertEqual(self.result_count(REGISTRATION_LIST, {"status__exact": "waitlisted"}), 1)
        self.assertEqual(self.result_count(REGISTRATION_LIST, {"status__exact": "registered"}), 1)
        self.assertEqual(self.result_count(REGISTRATION_LIST, {"status__exact": "cancelled"}), 0)

    def test_notification_search_and_kind_filter(self):
        self.assertEqual(self.result_count(NOTIFICATION_LIST, {"q": "marker"}), 1)
        self.assertEqual(self.result_count(NOTIFICATION_LIST, {"q": "att_bola"}), 1)
        self.assertEqual(self.result_count(NOTIFICATION_LIST, {"q": "Alpha"}), 2)
        self.assertEqual(self.result_count(NOTIFICATION_LIST, {"kind__exact": "waitlisted"}), 1)
        self.assertEqual(self.result_count(NOTIFICATION_LIST, {"kind__exact": "promoted"}), 0)

    def test_user_search_and_role_filter(self):
        self.assertEqual(self.result_count(USER_LIST, {"q": "att_ada"}), 1)
        self.assertEqual(self.result_count(USER_LIST, {"q": "att_bola@example.com"}), 1)
        attendees = User.objects.filter(role="attendee").count()
        organizers = User.objects.filter(role="organizer").count()
        self.assertEqual(self.result_count(USER_LIST, {"role__exact": "attendee"}), attendees)
        self.assertEqual(self.result_count(USER_LIST, {"role__exact": "organizer"}), organizers)


class AdminQueryCountTests(AdminTestBase):
    """Checks that a list page costs the same number of queries for 2 rows and for 12."""

    def add_rows(self, how_many):
        event = self.make_event(title=f"Bulk {self.counter}")
        for _ in range(how_many):
            person = self.make_attendee()
            registration = self.make_registration(person, event, "registered")
            self.make_notification(person, event, registration)
            self.make_event(title=f"Bulk event {self.counter}")

    def count_queries(self, url):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_list_pages_do_not_query_per_row(self):
        self.add_rows(2)
        lists = (EVENT_LIST, REGISTRATION_LIST, NOTIFICATION_LIST)
        small = {url: self.count_queries(url) for url in lists}
        self.add_rows(10)
        for url in lists:
            with self.subTest(url=url):
                self.assertEqual(self.count_queries(url), small[url])
