"""Tests for reading events: the public list, filters, detail and an organizer's own events."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from events.models import Event

User = get_user_model()

PASSWORD = "Tr4vel-Mango-Quartz!"
EVENTS_URL = "/api/events/"
MY_EVENTS_URL = "/api/my/events/"

READ_FIELDS = {
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


def error_of(response):
    return response.json()["error"]


def titles(response):
    return [item["title"] for item in response.json()["results"]]


class ReadTestBase(APITestCase):
    def setUp(self):
        self.organizer = self.make_user("olu", "organizer")
        self.other_organizer = self.make_user("ade", "organizer")
        self.attendee = self.make_user("tobi", "attendee")

    def make_user(self, username, role):
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password=PASSWORD,
            role=role,
        )

    def make_event(self, owner=None, days=30, start=None, **overrides):
        start = start or timezone.now() + timedelta(days=days)
        values = {
            "organizer": owner or self.organizer,
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

    def list(self, **params):
        return self.client.get(EVENTS_URL, params)

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(error_of(response)["code"], code)

    def assert_field_error(self, response, field):
        self.assert_error(response, 400, "validation_error")
        self.assertIn(field, error_of(response)["fields"])


class EventListTests(ReadTestBase):
    def test_list_is_public_and_paged(self):
        self.make_event()
        response = self.list()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"count", "next", "previous", "results"})
        self.assertEqual(body["count"], 1)

    def test_list_works_for_a_signed_in_user_too(self):
        self.make_event()
        self.client.force_authenticate(user=self.attendee)
        self.assertEqual(self.list().status_code, 200)

    def test_list_items_use_the_read_fields(self):
        self.make_event()
        item = self.list().json()["results"][0]
        self.assertEqual(set(item), READ_FIELDS)
        self.assertEqual(item["organizer_username"], "olu")

    def test_counts_are_zero_and_every_seat_is_free_when_nobody_registered(self):
        self.make_event(capacity=40)
        item = self.list().json()["results"][0]
        self.assertEqual(item["registered_count"], 0)
        self.assertEqual(item["waitlist_count"], 0)
        self.assertEqual(item["seats_left"], 40)

    def test_default_list_shows_only_upcoming_scheduled_events(self):
        self.make_event(title="Upcoming")
        self.make_event(title="Already started", days=-1)
        self.make_event(title="Called off", status="cancelled", cancel_reason="Venue closed")
        self.assertEqual(titles(self.list()), ["Upcoming"])

    def test_list_is_ordered_by_start_ascending(self):
        self.make_event(title="Middle", days=10)
        self.make_event(title="Last", days=20)
        self.make_event(title="First", days=5)
        self.assertEqual(titles(self.list()), ["First", "Middle", "Last"])

    def test_page_size_is_ten(self):
        for index in range(12):
            self.make_event(title=f"Event {index:02d}", days=index + 1)
        first = self.list().json()
        self.assertEqual(first["count"], 12)
        self.assertEqual(len(first["results"]), 10)
        self.assertIsNotNone(first["next"])
        second = self.list(page=2).json()
        self.assertEqual(len(second["results"]), 2)
        self.assertIsNone(second["next"])

    def test_client_cannot_change_the_page_size(self):
        for index in range(12):
            self.make_event(title=f"Event {index:02d}", days=index + 1)
        self.assertEqual(len(self.list(page_size=50).json()["results"]), 10)

    def test_invalid_page_is_a_404(self):
        self.make_event()
        for bad in ("99", "abc", "0", "-1", "99999999999999999999"):
            with self.subTest(page=bad):
                self.assert_error(self.list(page=bad), 404, "not_found")

    def test_unknown_query_parameters_are_ignored(self):
        self.make_event()
        self.assertEqual(self.list(colour="blue").json()["count"], 1)

    def test_query_count_does_not_grow_with_the_page_size(self):
        for index in range(2):
            self.make_event(title=f"Event {index}", days=index + 1)
        with CaptureQueriesContext(connection) as small:
            self.list()
        for index in range(2, 10):
            self.make_event(title=f"Event {index}", days=index + 1)
        with CaptureQueriesContext(connection) as large:
            response = self.list()
        self.assertEqual(len(response.json()["results"]), 10)
        self.assertEqual(len(small), len(large))


class EventFilterTests(ReadTestBase):
    def test_q_matches_title_ignoring_case(self):
        self.make_event(title="Python Night")
        self.make_event(title="Cooking Class")
        self.assertEqual(titles(self.list(q="python")), ["Python Night"])

    def test_q_matches_description(self):
        self.make_event(title="One", description="Learn about Rust here")
        self.make_event(title="Two", description="Something else")
        self.assertEqual(titles(self.list(q="RUST")), ["One"])

    def test_q_with_no_match_returns_an_empty_page(self):
        self.make_event()
        body = self.list(q="zzzz").json()
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["results"], [])

    def test_q_with_sql_text_is_treated_as_plain_text(self):
        self.make_event()
        response = self.list(q="'; DROP TABLE events_event; --")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])
        self.assertEqual(Event.objects.count(), 1)

    def test_null_character_in_a_text_filter_is_a_validation_error(self):
        self.assert_field_error(self.list(q="a\x00b"), "q")
        self.assert_field_error(self.list(city="La\x00gos"), "city")

    def test_very_long_q_does_not_cause_a_server_error(self):
        self.make_event()
        self.assertEqual(self.list(q="x" * 10000).status_code, 200)

    def test_city_matches_part_of_the_name_ignoring_case(self):
        self.make_event(title="In Lagos", city="Lagos")
        self.make_event(title="In Abuja", city="Abuja")
        self.assertEqual(titles(self.list(city="lag")), ["In Lagos"])

    def test_category_filter_is_exact(self):
        self.make_event(title="A workshop", category="workshop")
        self.make_event(title="A meetup", category="meetup")
        self.assertEqual(titles(self.list(category="workshop")), ["A workshop"])

    def test_event_type_filter_is_exact(self):
        self.make_event(
            title="Online one", event_type="online", online_link="https://meet.example.com/x"
        )
        self.make_event(title="In person one", event_type="in_person")
        self.assertEqual(titles(self.list(event_type="online")), ["Online one"])

    def test_unknown_category_event_type_and_status_are_validation_errors(self):
        self.assert_field_error(self.list(category="party"), "category")
        self.assert_field_error(self.list(event_type="telepathy"), "event_type")
        self.assert_field_error(self.list(status="deleted"), "status")

    def test_status_cancelled_lists_cancelled_events_including_past_ones(self):
        self.make_event(title="Called off soon", status="cancelled", cancel_reason="Rain")
        self.make_event(
            title="Called off long ago", days=-20, status="cancelled", cancel_reason="Rain"
        )
        self.make_event(title="Running")
        self.assertEqual(
            sorted(titles(self.list(status="cancelled"))),
            ["Called off long ago", "Called off soon"],
        )

    def test_status_scheduled_behaves_like_the_default(self):
        self.make_event(title="Upcoming")
        self.make_event(title="Already started", days=-1)
        self.make_event(title="Called off", status="cancelled", cancel_reason="Rain")
        self.assertEqual(titles(self.list(status="scheduled")), ["Upcoming"])

    def test_date_filters_include_the_given_day(self):
        base = (timezone.now() + timedelta(days=100)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        self.make_event(title="Day 0", start=base)
        self.make_event(title="Day 10", start=base + timedelta(days=10))
        self.make_event(title="Day 20", start=base + timedelta(days=20))
        day_10 = (base + timedelta(days=10)).date().isoformat()
        self.assertEqual(titles(self.list(starts_after=day_10)), ["Day 10", "Day 20"])
        self.assertEqual(titles(self.list(starts_before=day_10)), ["Day 0", "Day 10"])
        self.assertEqual(titles(self.list(starts_after=day_10, starts_before=day_10)), ["Day 10"])

    def test_bad_dates_are_validation_errors(self):
        self.assert_field_error(self.list(starts_after="not-a-date"), "starts_after")
        self.assert_field_error(self.list(starts_before="2030-13-45"), "starts_before")
        self.assert_field_error(self.list(starts_after="10/10/2030"), "starts_after")

    def test_extreme_dates_do_not_cause_a_server_error(self):
        self.make_event(title="Any")
        self.assertEqual(titles(self.list(starts_before="9999-12-31")), ["Any"])
        self.assertEqual(self.list(starts_after="9999-12-31").json()["results"], [])
        self.assertEqual(self.list(starts_before="0001-01-01").json()["results"], [])
        self.assertEqual(titles(self.list(starts_after="0001-01-01")), ["Any"])

    def test_filters_combine(self):
        self.make_event(title="Lagos workshop", city="Lagos", category="workshop")
        self.make_event(title="Lagos meetup", city="Lagos", category="meetup")
        self.make_event(title="Abuja workshop", city="Abuja", category="workshop")
        self.assertEqual(titles(self.list(city="lagos", category="workshop")), ["Lagos workshop"])

    def test_empty_filter_values_are_ignored(self):
        self.make_event()
        self.assertEqual(self.list(q="", city="").json()["count"], 1)


class EventDetailTests(ReadTestBase):
    def url(self, event):
        return f"/api/events/{event.pk}/"

    def test_detail_is_public(self):
        event = self.make_event()
        response = self.client.get(self.url(event))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), READ_FIELDS)
        self.assertEqual(body["id"], event.pk)
        self.assertEqual(body["contact_email"], "events@example.com")

    def test_detail_shows_a_cancelled_event_with_its_reason(self):
        event = self.make_event(status="cancelled", cancel_reason="Venue closed")
        body = self.client.get(self.url(event)).json()
        self.assertEqual(body["status"], "cancelled")
        self.assertEqual(body["cancel_reason"], "Venue closed")

    def test_detail_shows_an_event_that_already_started(self):
        event = self.make_event(days=-3)
        self.assertEqual(self.client.get(self.url(event)).status_code, 200)

    def test_unknown_event_is_a_json_404(self):
        self.assert_error(self.client.get("/api/events/999999/"), 404, "not_found")

    def test_non_numeric_id_is_a_json_404(self):
        self.assert_error(self.client.get("/api/events/abc/"), 404, "not_found")

    def test_detail_exposes_no_owner_id(self):
        event = self.make_event()
        body = self.client.get(self.url(event)).json()
        self.assertNotIn("organizer", body)


class MyEventsTests(ReadTestBase):
    def test_anonymous_user_gets_401(self):
        self.assert_error(self.client.get(MY_EVENTS_URL), 401, "not_authenticated")

    def test_attendee_gets_403(self):
        self.client.force_authenticate(user=self.attendee)
        self.assert_error(self.client.get(MY_EVENTS_URL), 403, "permission_denied")

    def test_lists_only_the_callers_events(self):
        self.make_event(title="Mine one")
        self.make_event(title="Mine two", days=40)
        self.make_event(title="Someone else", owner=self.other_organizer)
        self.client.force_authenticate(user=self.organizer)
        response = self.client.get(MY_EVENTS_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(titles(response)), ["Mine one", "Mine two"])

    def test_another_organizer_sees_only_their_own(self):
        self.make_event(title="Mine")
        self.make_event(title="Theirs", owner=self.other_organizer)
        self.client.force_authenticate(user=self.other_organizer)
        self.assertEqual(titles(self.client.get(MY_EVENTS_URL)), ["Theirs"])

    def test_includes_every_status_and_past_events(self):
        self.make_event(title="Upcoming")
        self.make_event(title="Past", days=-10)
        self.make_event(title="Called off", status="cancelled", cancel_reason="Rain")
        self.client.force_authenticate(user=self.organizer)
        self.assertEqual(
            sorted(titles(self.client.get(MY_EVENTS_URL))),
            ["Called off", "Past", "Upcoming"],
        )

    def test_is_ordered_by_start_newest_first(self):
        self.make_event(title="Middle", days=10)
        self.make_event(title="Latest", days=20)
        self.make_event(title="Earliest", days=-5)
        self.client.force_authenticate(user=self.organizer)
        self.assertEqual(titles(self.client.get(MY_EVENTS_URL)), ["Latest", "Middle", "Earliest"])

    def test_items_use_the_read_fields_and_the_list_is_paged(self):
        for index in range(12):
            self.make_event(title=f"Event {index:02d}", days=index + 1)
        self.client.force_authenticate(user=self.organizer)
        body = self.client.get(MY_EVENTS_URL).json()
        self.assertEqual(set(body), {"count", "next", "previous", "results"})
        self.assertEqual(body["count"], 12)
        self.assertEqual(len(body["results"]), 10)
        self.assertEqual(set(body["results"][0]), READ_FIELDS)

    def test_organizer_with_no_events_gets_an_empty_page(self):
        self.client.force_authenticate(user=self.other_organizer)
        body = self.client.get(MY_EVENTS_URL).json()
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["results"], [])

    def test_invalid_page_is_a_404(self):
        self.client.force_authenticate(user=self.organizer)
        self.assert_error(self.client.get(MY_EVENTS_URL, {"page": "5"}), 404, "not_found")
