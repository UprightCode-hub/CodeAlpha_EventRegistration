"""Tests for the Idempotency-Key header on event create and registration create."""

import json
import threading
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from events.models import Event
from registrations.models import IdempotencyRecord, Notification, Registration

User = get_user_model()

PASSWORD = "Str0ng-Pass-77!"
PHONE = "+2348012345678"
EVENTS = "/api/events/"
REGISTRATIONS = "/api/registrations/"
REPLAY = "Idempotent-Replayed"


class Helpers:
    """Small builders shared by the test classes."""

    def make_user(self, username, role):
        """Create a user directly in the database."""
        return User.objects.create_user(
            username=username, email=f"{username}@example.com", password=PASSWORD, role=role
        )

    def token(self, username):
        """Log in through the API and return the access token."""
        response = APIClient().post(
            "/api/auth/login/", {"username": username, "password": PASSWORD}, format="json"
        )
        return response.json()["access"]

    def api(self, username):
        """Return a client signed in as the user."""
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer " + self.token(username))
        return client

    def event_body(self, **over):
        """A valid in person event body."""
        start = timezone.now() + timedelta(days=7)
        body = {
            "title": "Idempotency meetup",
            "description": "A test event.",
            "category": "meetup",
            "event_type": "in_person",
            "venue_name": "Hall",
            "address": "1 Test Street",
            "city": "Lagos",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=2)).isoformat(),
            "capacity": 5,
            "contact_email": "host@example.com",
            "contact_phone": PHONE,
        }
        body.update(over)
        return body

    def reg_body(self, event_id, **over):
        """A valid registration body."""
        body = {"event": event_id, "full_name": "Ada Obi", "phone": PHONE}
        body.update(over)
        return body

    def post(self, client, url, body, key=None):
        """Post JSON with an optional Idempotency-Key."""
        extra = {"HTTP_IDEMPOTENCY_KEY": key} if key is not None else {}
        return client.post(url, body, format="json", **extra)

    def replayed(self, response):
        """True when the answer is marked as a replay."""
        return response.headers.get(REPLAY) == "true"


class RegistrationIdempotencyTests(Helpers, TestCase):
    """POST /api/registrations/ with a key."""

    def setUp(self):
        self.make_user("org", "organizer")
        self.make_user("att", "attendee")
        self.make_user("att2", "attendee")
        self.org = self.api("org")
        self.att = self.api("att")
        self.att2 = self.api("att2")
        self.event = self.org.post(EVENTS, self.event_body(), format="json").json()

    def register(self, client=None, key=None, **over):
        return self.post(
            client or self.att, REGISTRATIONS, self.reg_body(self.event["id"], **over), key
        )

    def test_the_same_key_replays_the_first_answer(self):
        first = self.register(key="k-1")
        second = self.register(key="k-1")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json(), second.json())
        self.assertTrue(self.replayed(second))
        self.assertFalse(self.replayed(first))
        self.assertEqual(Registration.objects.count(), 1)
        self.assertEqual(Notification.objects.count(), 1)

    def test_without_a_key_a_retry_is_still_already_registered(self):
        self.assertEqual(self.register().status_code, 201)
        second = self.register()
        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.json()["error"]["code"], "already_registered")

    def test_the_same_key_with_another_body_is_refused(self):
        self.assertEqual(self.register(key="k-1").status_code, 201)
        second = self.register(key="k-1", full_name="Someone Else")
        self.assertEqual(second.status_code, 422)
        error = second.json()["error"]
        self.assertEqual(error["code"], "idempotency_key_reused")
        self.assertEqual(
            error["message"], "This Idempotency-Key was already used with a different request."
        )
        self.assertEqual(Registration.objects.count(), 1)
        self.assertEqual(Registration.objects.get().full_name, "Ada Obi")

    def test_keys_belong_to_one_user(self):
        first = self.register(key="shared")
        other = self.register(client=self.att2, key="shared")
        self.assertEqual(other.status_code, 201)
        self.assertFalse(self.replayed(other))
        self.assertNotEqual(first.json()["id"], other.json()["id"])
        self.assertEqual(Registration.objects.count(), 2)

    def test_bad_key_formats_are_refused(self):
        for key in ("a" * 65, "has space", "semi;colon", "caf\u00e9", "dot.dot"):
            response = self.register(key=key)
            self.assertEqual(response.status_code, 400, key)
            error = response.json()["error"]
            self.assertEqual(error["code"], "validation_error", key)
            self.assertIn("Idempotency-Key", error["fields"], key)
        self.assertEqual(Registration.objects.count(), 0)

    def test_an_empty_key_is_refused(self):
        response = self.register(key="")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Idempotency-Key", response.json()["error"]["fields"])

    def test_good_key_formats_are_accepted(self):
        self.assertEqual(self.register(key="a" * 64).status_code, 201)
        self.assertEqual(self.register(client=self.att2, key="A-b_9").status_code, 201)

    def test_a_failed_answer_is_not_stored(self):
        bad = self.register(key="k-1", full_name="A")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(IdempotencyRecord.objects.count(), 0)
        good = self.register(key="k-1")
        self.assertEqual(good.status_code, 201)
        self.assertFalse(self.replayed(good))
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_a_state_error_is_not_stored(self):
        self.assertEqual(self.register().status_code, 201)
        refused = self.register(key="k-2")
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()["error"]["code"], "already_registered")
        self.assertEqual(IdempotencyRecord.objects.count(), 0)

    def test_the_replay_shows_the_original_answer_not_the_current_state(self):
        first = self.register(key="k-1")
        reg_id = first.json()["id"]
        cancelled = self.att.post(f"{REGISTRATIONS}{reg_id}/cancel/", format="json")
        self.assertEqual(cancelled.status_code, 200)
        replay = self.register(key="k-1")
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.json()["status"], "registered")
        self.assertEqual(replay.json()["id"], reg_id)
        self.assertTrue(self.replayed(replay))
        self.assertEqual(Registration.objects.count(), 1)
        self.assertEqual(Registration.objects.get().status, "cancelled")

    def test_permission_errors_come_before_the_key_and_are_not_stored(self):
        as_organizer = self.post(self.org, REGISTRATIONS, self.reg_body(self.event["id"]), "k-1")
        self.assertEqual(as_organizer.status_code, 403)
        anonymous = self.post(APIClient(), REGISTRATIONS, self.reg_body(self.event["id"]), "k-1")
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(IdempotencyRecord.objects.count(), 0)

    def test_a_bad_key_does_not_hide_a_permission_error(self):
        response = self.post(self.org, REGISTRATIONS, self.reg_body(self.event["id"]), "bad key")
        self.assertEqual(response.status_code, 403)

    def test_a_record_is_kept_for_a_day(self):
        self.assertEqual(self.register(key="k-1").status_code, 201)
        with mock.patch(
            "django.utils.timezone.now", return_value=timezone.now() + timedelta(hours=23)
        ):
            replay = self.register(key="k-1")
        self.assertEqual(replay.status_code, 201)
        self.assertTrue(self.replayed(replay))

    def test_a_record_expires_after_a_day(self):
        self.assertEqual(self.register(key="k-1").status_code, 201)
        with mock.patch(
            "django.utils.timezone.now", return_value=timezone.now() + timedelta(hours=25)
        ):
            again = self.register(key="k-1")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.json()["error"]["code"], "already_registered")
        self.assertFalse(self.replayed(again))

    def test_the_header_is_ignored_on_other_endpoints(self):
        response = self.org.patch(
            f"{EVENTS}{self.event['id']}/",
            {"title": "A new title"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="bad key!",
        )
        self.assertEqual(response.status_code, 200)

    def test_waitlisted_answers_replay_too(self):
        small = self.org.post(
            EVENTS, self.event_body(title="Small one", capacity=1), format="json"
        ).json()
        self.assertEqual(
            self.post(self.att, REGISTRATIONS, self.reg_body(small["id"]), "k-a").status_code, 201
        )
        first = self.post(self.att2, REGISTRATIONS, self.reg_body(small["id"]), "k-b")
        self.assertEqual(first.json()["status"], "waitlisted")
        replay = self.post(self.att2, REGISTRATIONS, self.reg_body(small["id"]), "k-b")
        self.assertEqual(replay.json(), first.json())
        self.assertEqual(Registration.objects.filter(event_id=small["id"]).count(), 2)

    def test_the_schema_documents_the_header(self):
        schema = json.loads(self.client.get("/api/schema/?format=json").content)
        for path in ("/api/events/", "/api/registrations/"):
            parameters = schema["paths"][path]["post"].get("parameters", [])
            names = [(p["name"], p["in"]) for p in parameters]
            self.assertIn(("Idempotency-Key", "header"), names, path)
            self.assertIn("422", schema["paths"][path]["post"]["responses"], path)


class EventIdempotencyTests(Helpers, TestCase):
    """POST /api/events/ with a key."""

    def setUp(self):
        self.make_user("org", "organizer")
        self.make_user("org2", "organizer")
        self.make_user("att", "attendee")
        self.org = self.api("org")
        self.org2 = self.api("org2")

    def create(self, client=None, key=None, **over):
        return self.post(client or self.org, EVENTS, self.event_body(**over), key)

    def test_the_same_key_creates_one_event(self):
        body = self.event_body()
        first = self.post(self.org, EVENTS, body, "k-1")
        second = self.post(self.org, EVENTS, body, "k-1")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json(), second.json())
        self.assertTrue(self.replayed(second))
        self.assertEqual(Event.objects.count(), 1)

    def test_without_a_key_two_posts_make_two_events(self):
        body = self.event_body()
        self.post(self.org, EVENTS, body)
        self.post(self.org, EVENTS, body)
        self.assertEqual(Event.objects.count(), 2)

    def test_the_same_key_with_another_body_is_refused(self):
        self.assertEqual(self.create(key="k-1").status_code, 201)
        second = self.create(key="k-1", title="A different title")
        self.assertEqual(second.status_code, 422)
        self.assertEqual(second.json()["error"]["code"], "idempotency_key_reused")
        self.assertEqual(Event.objects.count(), 1)

    def test_a_failed_create_is_not_stored(self):
        bad = self.create(key="k-1", capacity=0)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(IdempotencyRecord.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)
        good = self.create(key="k-1")
        self.assertEqual(good.status_code, 201)
        self.assertFalse(self.replayed(good))

    def test_keys_belong_to_one_organizer(self):
        first = self.create(key="shared")
        other = self.create(client=self.org2, key="shared")
        self.assertEqual(other.status_code, 201)
        self.assertNotEqual(first.json()["id"], other.json()["id"])
        self.assertEqual(Event.objects.count(), 2)

    def test_a_bad_key_is_a_validation_error(self):
        response = self.create(key="no good!")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Idempotency-Key", response.json()["error"]["fields"])
        self.assertEqual(Event.objects.count(), 0)

    def test_an_attendee_gets_403_before_the_key_is_looked_at(self):
        self.make_user("att2", "attendee")
        response = self.create(client=self.api("att"), key="bad key")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(IdempotencyRecord.objects.count(), 0)


class Racers:
    """Runs the same request from several threads at the same moment."""

    def run_threads(self, token, url, body, key, count=4):
        results = []
        barrier = threading.Barrier(count)

        def fire():
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION="Bearer " + token)
                barrier.wait()
                response = client.post(url, body, format="json", HTTP_IDEMPOTENCY_KEY=key)
                results.append(
                    (response.status_code, response.json(), response.headers.get(REPLAY))
                )
            except Exception as exc:  # a thread must never die silently
                results.append(("error", repr(exc), None))
            finally:
                connection.close()

        threads = [threading.Thread(target=fire) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        return results


class RegistrationRaceTests(Helpers, Racers, TransactionTestCase):
    """Four simultaneous retries of one registration."""

    def test_four_requests_with_one_key_make_one_registration(self):
        self.make_user("org", "organizer")
        self.make_user("att", "attendee")
        event = self.api("org").post(EVENTS, self.event_body(), format="json").json()
        token = self.token("att")
        results = self.run_threads(token, REGISTRATIONS, self.reg_body(event["id"]), "race-1")
        self.assertEqual(len(results), 4)
        self.assertEqual([r[0] for r in results], [201] * 4, results)
        self.assertEqual(len({json.dumps(r[1], sort_keys=True) for r in results}), 1)
        self.assertEqual(sum(1 for r in results if r[2] == "true"), 3)
        self.assertEqual(Registration.objects.count(), 1)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)


class EventRaceTests(Helpers, Racers, TransactionTestCase):
    """Four simultaneous retries of one event create."""

    def test_four_requests_with_one_key_make_one_event(self):
        self.make_user("org", "organizer")
        token = self.token("org")
        results = self.run_threads(token, EVENTS, self.event_body(), "race-2")
        self.assertEqual(len(results), 4)
        self.assertEqual([r[0] for r in results], [201] * 4, results)
        self.assertEqual(len({r[1]["id"] for r in results}), 1)
        self.assertEqual(sum(1 for r in results if r[2] == "true"), 3)
        self.assertEqual(Event.objects.count(), 1)
