"""Tests for creating and editing events."""

from datetime import timedelta
from datetime import timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APITestCase

from events.models import Event

User = get_user_model()

PASSWORD = "Tr4vel-Mango-Quartz!"
EVENTS_URL = "/api/events/"

READ_FIELDS = {
    "id", "title", "description", "category", "event_type", "venue_name",
    "address", "city", "online_link", "start_at", "end_at",
    "registration_deadline", "capacity", "registered_count", "seats_left",
    "waitlist_count", "contact_email", "contact_phone", "organizer_username",
    "status", "cancel_reason", "created_at",
}


def error_of(response):
    return response.json()["error"]


def detail_url(event):
    return f"/api/events/{event.pk}/"


class EventTestBase(APITestCase):
    def setUp(self):
        self.organizer = self.make_user("olu", "organizer")
        self.other_organizer = self.make_user("ade", "organizer")
        self.attendee = self.make_user("tobi", "attendee")
        self.client.force_authenticate(user=self.organizer)

    def make_user(self, username, role):
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password=PASSWORD,
            role=role,
        )

    def payload(self, **overrides):
        start = timezone.now() + timedelta(days=30)
        data = {
            "title": "Lagos Python Meetup",
            "description": "Talks and networking.",
            "category": "meetup",
            "event_type": "in_person",
            "venue_name": "Tech Hub",
            "address": "12 Marina Road",
            "city": "Lagos",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=3)).isoformat(),
            "registration_deadline": (start - timedelta(days=1)).isoformat(),
            "capacity": 50,
            "contact_email": "events@example.com",
            "contact_phone": "+2348012345678",
        }
        data.update(overrides)
        return data

    def make_event(self, **overrides):
        start = timezone.now() + timedelta(days=30)
        values = {
            "organizer": self.organizer,
            "title": "Existing Meetup",
            "description": "Talks.",
            "category": "meetup",
            "event_type": "in_person",
            "venue_name": "Tech Hub",
            "address": "12 Marina Road",
            "city": "Lagos",
            "online_link": "",
            "start_at": start,
            "end_at": start + timedelta(hours=3),
            "registration_deadline": start - timedelta(days=1),
            "capacity": 50,
            "contact_email": "events@example.com",
            "contact_phone": "+2348012345678",
        }
        values.update(overrides)
        return Event.objects.create(**values)

    def create(self, **overrides):
        return self.client.post(EVENTS_URL, self.payload(**overrides), format="json")

    def create_without(self, *names, **overrides):
        data = self.payload(**overrides)
        for name in names:
            data.pop(name, None)
        return self.client.post(EVENTS_URL, data, format="json")

    def edit(self, event, **body):
        return self.client.patch(detail_url(event), body, format="json")

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(error_of(response)["code"], code)

    def assert_field_error(self, response, field):
        self.assert_error(response, 400, "validation_error")
        self.assertIn(field, error_of(response)["fields"])


class CreateEventTests(EventTestBase):
    def test_organizer_creates_an_event(self):
        response = self.create()
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(set(body), READ_FIELDS)
        self.assertEqual(body["organizer_username"], "olu")
        self.assertEqual(body["status"], "scheduled")
        self.assertEqual(body["cancel_reason"], "")
        self.assertEqual(body["registered_count"], 0)
        self.assertEqual(body["waitlist_count"], 0)
        self.assertEqual(body["seats_left"], 50)
        self.assertEqual(Event.objects.count(), 1)

    def test_organizer_comes_from_the_token_not_the_body(self):
        response = self.create(organizer=self.other_organizer.pk, organizer_username="ade")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Event.objects.get().organizer, self.organizer)

    def test_status_and_cancel_reason_in_the_body_are_ignored(self):
        response = self.create(status="cancelled", cancel_reason="nope")
        self.assertEqual(response.status_code, 201)
        event = Event.objects.get()
        self.assertEqual(event.status, "scheduled")
        self.assertEqual(event.cancel_reason, "")

    def test_anonymous_user_gets_401(self):
        self.client.force_authenticate(user=None)
        self.assert_error(self.create(), 401, "not_authenticated")

    def test_attendee_gets_403(self):
        self.client.force_authenticate(user=self.attendee)
        self.assert_error(self.create(), 403, "permission_denied")
        self.assertEqual(Event.objects.count(), 0)

    def test_every_required_field_is_reported(self):
        response = self.client.post(EVENTS_URL, {}, format="json")
        self.assert_error(response, 400, "validation_error")
        fields = error_of(response)["fields"]
        for name in (
            "title", "description", "category", "event_type", "start_at",
            "end_at", "capacity", "contact_email", "contact_phone",
        ):
            self.assertIn(name, fields)

    def test_title_length_limits(self):
        self.assert_field_error(self.create(title="ab"), "title")
        self.assert_field_error(self.create(title="x" * 201), "title")
        self.assertEqual(self.create(title="x" * 200).status_code, 201)
        self.assertEqual(self.create(title="abc").status_code, 201)

    def test_description_limits(self):
        self.assert_field_error(self.create(description=""), "description")
        self.assert_field_error(self.create(description="x" * 5001), "description")
        self.assertEqual(self.create(description="x" * 5000).status_code, 201)

    def test_venue_fields_link_and_contact_email_have_length_limits(self):
        for name, limit in (("venue_name", 200), ("address", 255), ("city", 100)):
            with self.subTest(field=name):
                self.assert_field_error(self.create(**{name: "x" * (limit + 1)}), name)
                self.assertEqual(self.create(**{name: "x" * limit}).status_code, 201)
        self.assert_field_error(
            self.create(contact_email="a" * 250 + "@example.com"), "contact_email"
        )
        self.assert_field_error(
            self.create(event_type="online", online_link="https://example.com/" + "a" * 2000),
            "online_link",
        )

    def test_null_character_is_a_validation_error_not_a_server_error(self):
        self.assert_field_error(self.create(title="bad\x00title"), "title")
        self.assert_field_error(self.create(city="La\x00gos"), "city")

    def test_unknown_category_and_event_type_are_rejected(self):
        self.assert_field_error(self.create(category="party"), "category")
        self.assert_field_error(self.create(event_type="telepathy"), "event_type")

    def test_every_listed_category_and_event_type_is_accepted(self):
        for category in ("conference", "workshop", "meetup", "webinar", "networking", "other"):
            self.assertEqual(self.create(category=category).status_code, 201)

    def test_capacity_limits(self):
        for bad in (0, -5, 100001, 2147483648, "many", 10.5, None):
            with self.subTest(capacity=bad):
                self.assert_field_error(self.create(capacity=bad), "capacity")
        for good in (1, 100000):
            with self.subTest(capacity=good):
                self.assertEqual(self.create(capacity=good).status_code, 201)

    def test_in_person_event_needs_venue_address_and_city(self):
        for name in ("venue_name", "address", "city"):
            with self.subTest(missing=name):
                self.assert_field_error(self.create_without(name), name)

    def test_online_event_needs_no_venue_but_needs_a_link(self):
        online = {"event_type": "online"}
        response = self.create_without(
            "venue_name", "address", "city", online_link="https://meet.example.com/abc", **online
        )
        self.assertEqual(response.status_code, 201)
        self.assert_field_error(
            self.create_without("venue_name", "address", "city", **online), "online_link"
        )

    def test_hybrid_event_needs_a_venue_and_a_link(self):
        link = "https://meet.example.com/abc"
        self.assertEqual(
            self.create(event_type="hybrid", online_link=link).status_code, 201
        )
        self.assert_field_error(self.create(event_type="hybrid"), "online_link")
        self.assert_field_error(
            self.create_without("address", event_type="hybrid", online_link=link), "address"
        )

    def test_link_must_be_http_or_https(self):
        for bad in ("ftp://example.com/x", "meet", "javascript:alert(1)", "http://"):
            with self.subTest(link=bad):
                self.assert_field_error(
                    self.create(event_type="online", online_link=bad), "online_link"
                )
        self.assertEqual(
            self.create(event_type="online", online_link="http://example.com/x").status_code,
            201,
        )

    def test_phone_must_be_international_format(self):
        for bad in ("08012345678", "+0123456789", "+123", "+23480123456789012",
                    "phone", "+234 801 234 5678", "+234-801-234-5678", ""):
            with self.subTest(phone=bad):
                self.assert_field_error(self.create(contact_phone=bad), "contact_phone")
        self.assertEqual(self.create(contact_phone="+442071234567").status_code, 201)

    def test_contact_email_must_be_valid(self):
        self.assert_field_error(self.create(contact_email="not-an-email"), "contact_email")
        self.assert_field_error(self.create(contact_email=""), "contact_email")

    def test_start_in_the_past_is_rejected(self):
        past = timezone.now() - timedelta(days=1)
        response = self.create(
            start_at=past.isoformat(),
            end_at=(past + timedelta(hours=3)).isoformat(),
            registration_deadline=(past - timedelta(days=1)).isoformat(),
        )
        self.assert_field_error(response, "start_at")

    def test_end_must_be_after_start(self):
        start = timezone.now() + timedelta(days=30)
        self.assert_field_error(
            self.create(start_at=start.isoformat(), end_at=start.isoformat()), "end_at"
        )
        self.assert_field_error(
            self.create(
                start_at=start.isoformat(),
                end_at=(start - timedelta(hours=1)).isoformat(),
            ),
            "end_at",
        )

    def test_deadline_cannot_be_after_the_start(self):
        start = timezone.now() + timedelta(days=30)
        response = self.create(
            start_at=start.isoformat(),
            end_at=(start + timedelta(hours=3)).isoformat(),
            registration_deadline=(start + timedelta(hours=1)).isoformat(),
        )
        self.assert_field_error(response, "registration_deadline")

    def test_deadline_in_the_past_is_rejected(self):
        past = timezone.now() - timedelta(hours=1)
        response = self.create(registration_deadline=past.isoformat())
        self.assert_field_error(response, "registration_deadline")

    def test_deadline_defaults_to_the_start(self):
        start = timezone.now() + timedelta(days=30)
        response = self.create_without(
            "registration_deadline",
            start_at=start.isoformat(),
            end_at=(start + timedelta(hours=3)).isoformat(),
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(parse_datetime(body["registration_deadline"]), parse_datetime(body["start_at"]))

    def test_deadline_equal_to_the_start_is_accepted(self):
        start = timezone.now() + timedelta(days=30)
        response = self.create(
            start_at=start.isoformat(),
            end_at=(start + timedelta(hours=3)).isoformat(),
            registration_deadline=start.isoformat(),
        )
        self.assertEqual(response.status_code, 201)

    def test_time_without_an_offset_is_rejected(self):
        start = (timezone.now() + timedelta(days=30)).replace(tzinfo=None)
        end = start + timedelta(hours=3)
        self.assert_field_error(
            self.create(start_at=start.isoformat(), end_at=(end.replace(tzinfo=dt_timezone.utc)).isoformat()),
            "start_at",
        )
        later = timezone.now() + timedelta(days=30)
        self.assert_field_error(
            self.create(start_at=later.isoformat(), end_at=end.isoformat()), "end_at"
        )

    def test_times_with_an_offset_are_stored_and_returned_in_utc(self):
        lagos = dt_timezone(timedelta(hours=1))
        start = (timezone.now() + timedelta(days=30)).astimezone(lagos)
        response = self.create(
            start_at=start.isoformat(),
            end_at=(start + timedelta(hours=3)).isoformat(),
            registration_deadline=(start - timedelta(days=1)).isoformat(),
        )
        self.assertEqual(response.status_code, 201)
        returned = parse_datetime(response.json()["start_at"])
        self.assertEqual(returned, start)
        self.assertEqual(returned.utcoffset(), timedelta(0))
        self.assertEqual(Event.objects.get().start_at, start)


class EditEventTests(EventTestBase):
    def test_owner_can_edit_an_event(self):
        event = self.make_event()
        response = self.edit(event, title="New title")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), READ_FIELDS)
        self.assertEqual(response.json()["title"], "New title")
        event.refresh_from_db()
        self.assertEqual(event.title, "New title")

    def test_unchanged_fields_stay_as_they_were(self):
        event = self.make_event()
        self.edit(event, title="New title")
        event.refresh_from_db()
        self.assertEqual(event.city, "Lagos")
        self.assertEqual(event.capacity, 50)

    def test_status_cancel_reason_and_organizer_cannot_be_changed_by_edit(self):
        event = self.make_event()
        response = self.edit(
            event,
            status="cancelled",
            cancel_reason="nope",
            organizer=self.other_organizer.pk,
            title="Still allowed",
        )
        self.assertEqual(response.status_code, 200)
        event.refresh_from_db()
        self.assertEqual(event.status, "scheduled")
        self.assertEqual(event.cancel_reason, "")
        self.assertEqual(event.organizer, self.organizer)
        self.assertEqual(event.title, "Still allowed")

    def test_organizer_who_does_not_own_the_event_gets_404(self):
        event = self.make_event()
        self.client.force_authenticate(user=self.other_organizer)
        self.assert_error(self.edit(event, title="Hijack"), 404, "not_found")
        event.refresh_from_db()
        self.assertEqual(event.title, "Existing Meetup")

    def test_attendee_gets_403(self):
        event = self.make_event()
        self.client.force_authenticate(user=self.attendee)
        self.assert_error(self.edit(event, title="Hijack"), 403, "permission_denied")

    def test_anonymous_user_gets_401(self):
        event = self.make_event()
        self.client.force_authenticate(user=None)
        self.assert_error(self.edit(event, title="Hijack"), 401, "not_authenticated")

    def test_unknown_event_gets_404(self):
        response = self.client.patch("/api/events/999999/", {"title": "x"}, format="json")
        self.assert_error(response, 404, "not_found")

    def test_started_event_cannot_be_edited(self):
        start = timezone.now() - timedelta(hours=1)
        event = self.make_event(
            start_at=start,
            end_at=start + timedelta(hours=3),
            registration_deadline=start - timedelta(days=1),
        )
        self.assert_error(self.edit(event, title="Late"), 400, "event_started")
        event.refresh_from_db()
        self.assertEqual(event.title, "Existing Meetup")

    def test_cancelled_event_cannot_be_edited(self):
        event = self.make_event(status="cancelled", cancel_reason="Venue closed")
        self.assert_error(self.edit(event, title="Late"), 400, "event_cancelled")

    def test_put_and_delete_are_not_available(self):
        event = self.make_event()
        put = self.client.put(detail_url(event), self.payload(), format="json")
        self.assert_error(put, 405, "method_not_allowed")
        delete = self.client.delete(detail_url(event))
        self.assert_error(delete, 405, "method_not_allowed")
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_end_before_the_saved_start_is_rejected(self):
        event = self.make_event()
        end = event.start_at - timedelta(hours=1)
        self.assert_field_error(self.edit(event, end_at=end.isoformat()), "end_at")

    def test_start_after_the_saved_end_is_rejected(self):
        event = self.make_event()
        start = event.start_at + timedelta(days=10)
        response = self.edit(event, start_at=start.isoformat())
        self.assert_error(response, 400, "validation_error")

    def test_deadline_after_the_saved_start_is_rejected(self):
        event = self.make_event()
        deadline = event.start_at + timedelta(hours=1)
        self.assert_field_error(
            self.edit(event, registration_deadline=deadline.isoformat()),
            "registration_deadline",
        )

    def test_start_before_the_saved_deadline_is_rejected(self):
        event = self.make_event()
        start = event.start_at - timedelta(days=2)
        response = self.edit(event, start_at=start.isoformat())
        self.assert_error(response, 400, "validation_error")

    def test_new_start_in_the_past_is_rejected(self):
        event = self.make_event()
        past = timezone.now() - timedelta(days=1)
        response = self.edit(
            event,
            start_at=past.isoformat(),
            end_at=(past + timedelta(hours=3)).isoformat(),
            registration_deadline=(past - timedelta(days=1)).isoformat(),
        )
        self.assert_error(response, 400, "validation_error")
        event.refresh_from_db()
        self.assertGreater(event.start_at, timezone.now())

    def test_moving_the_whole_schedule_together_is_accepted(self):
        event = self.make_event()
        start = timezone.now() + timedelta(days=60)
        response = self.edit(
            event,
            start_at=start.isoformat(),
            end_at=(start + timedelta(hours=2)).isoformat(),
            registration_deadline=(start - timedelta(days=2)).isoformat(),
        )
        self.assertEqual(response.status_code, 200)
        event.refresh_from_db()
        self.assertEqual(event.start_at, start)

    def test_switching_to_online_needs_a_link(self):
        event = self.make_event()
        self.assert_field_error(self.edit(event, event_type="online"), "online_link")
        event.refresh_from_db()
        self.assertEqual(event.event_type, "in_person")

    def test_switching_to_online_with_a_link_is_accepted(self):
        event = self.make_event()
        response = self.edit(
            event, event_type="online", online_link="https://meet.example.com/x"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["event_type"], "online")

    def test_clearing_a_required_venue_field_is_rejected(self):
        event = self.make_event()
        self.assert_field_error(self.edit(event, address=""), "address")

    def test_naive_time_is_rejected_on_edit(self):
        event = self.make_event()
        naive = (event.start_at + timedelta(days=1)).replace(tzinfo=None)
        self.assert_field_error(self.edit(event, start_at=naive.isoformat()), "start_at")

    def test_capacity_can_be_raised_and_lowered(self):
        event = self.make_event()
        self.assertEqual(self.edit(event, capacity=200).json()["capacity"], 200)
        response = self.edit(event, capacity=1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["seats_left"], 1)

    def test_capacity_limits_on_edit(self):
        event = self.make_event()
        for bad in (0, 100001, 2147483648, "many"):
            with self.subTest(capacity=bad):
                self.assert_field_error(self.edit(event, capacity=bad), "capacity")

    def test_edit_with_bad_phone_or_null_character_is_a_validation_error(self):
        event = self.make_event()
        self.assert_field_error(self.edit(event, contact_phone="0801"), "contact_phone")
        self.assert_field_error(self.edit(event, title="bad\x00title"), "title")

    def test_empty_edit_changes_nothing(self):
        event = self.make_event()
        response = self.edit(event)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "Existing Meetup")


class EventModelTests(EventTestBase):
    def test_new_events_default_to_scheduled_with_no_reason(self):
        event = self.make_event()
        self.assertEqual(event.status, "scheduled")
        self.assertEqual(event.cancel_reason, "")

    def test_an_organizer_with_events_cannot_be_deleted(self):
        self.make_event()
        with self.assertRaises(ProtectedError):
            self.organizer.delete()
        self.assertEqual(Event.objects.count(), 1)