import json
import threading
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from registrations.models import IdempotencyRecord, Notification, Registration

User = get_user_model()
FAST_HASHER = override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
PW = "Zx9!vQ-lamp-Tiger-42"
PHONE = "+2348012345678"
EVENTS = "/api/events/"
REGISTRATIONS = "/api/registrations/"
LOGIN = "/api/auth/login/"


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


def access_token(username):
    response = Client().post(
        LOGIN,
        json.dumps({"username": username, "password": PW}),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return response.json()["access"]


def post(path, body, token=None, key=None):
    extra = {}
    if token:
        extra["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    if key is not None:
        extra["HTTP_IDEMPOTENCY_KEY"] = key
    return Client().post(path, body, content_type="application/json", **extra)


def event_body(title="Idem Night", **overrides):
    start = timezone.now() + timedelta(days=10)
    body = {
        "title": title,
        "description": "Test event",
        "category": "meetup",
        "event_type": "online",
        "online_link": "https://example.com/live",
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(hours=2)).isoformat(),
        "capacity": 5,
        "contact_email": "org@example.com",
        "contact_phone": PHONE,
    }
    body.update(overrides)
    return json.dumps(body, sort_keys=True)


def registration_body(event):
    return json.dumps(
        {"event": event.pk, "full_name": "Ada Lovelace", "phone": PHONE}, sort_keys=True
    )


def make_event(organizer, capacity=5):
    start = timezone.now() + timedelta(days=10)
    return Event.objects.create(
        organizer=organizer,
        title="Idem Event",
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


def parallel(jobs):
    gate = threading.Barrier(len(jobs))
    out = [None] * len(jobs)

    def runner(index, job):
        try:
            gate.wait(timeout=20)
            response = job()
            out[index] = (
                response.status_code,
                response.headers.get("Idempotent-Replayed"),
                response.json(),
            )
        except Exception as exc:
            out[index] = ("error", None, repr(exc))
        finally:
            connection.close()

    threads = [threading.Thread(target=runner, args=(i, j)) for i, j in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return out


@FAST_HASHER
@override_settings(THROTTLING_ENABLED=False)
class IdempotencySequentialTests(TestCase):
    def setUp(self):
        make_user("org1", role="organizer")
        make_user("org2", role="organizer")
        make_user("a1")
        self.org_token = access_token("org1")
        self.org2_token = access_token("org2")
        self.attendee_token = access_token("a1")

    def test_same_key_and_body_replays_and_creates_one_event(self):
        body = event_body()
        first = post(EVENTS, body, self.org_token, key="k-replay")
        second = post(EVENTS, body, self.org_token, key="k-replay")
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertIsNone(first.headers.get("Idempotent-Replayed"))
        self.assertEqual(second.headers.get("Idempotent-Replayed"), "true")
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_same_key_with_a_different_body_is_a_422_and_creates_nothing(self):
        post(EVENTS, event_body(title="Original"), self.org_token, key="k-reuse")
        response = post(EVENTS, event_body(title="Changed"), self.org_token, key="k-reuse")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "idempotency_key_reused")
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(Event.objects.get().title, "Original")

    def test_failed_request_does_not_burn_the_key(self):
        bad = post(EVENTS, event_body(capacity=0), self.org_token, key="k-fail")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(IdempotencyRecord.objects.count(), 0)
        good = post(EVENTS, event_body(), self.org_token, key="k-fail")
        self.assertEqual(good.status_code, 201, good.content)
        self.assertIsNone(good.headers.get("Idempotent-Replayed"))

    def test_keys_are_scoped_to_the_user(self):
        a = post(EVENTS, event_body(title="Org one"), self.org_token, key="shared-key")
        b = post(EVENTS, event_body(title="Org two"), self.org2_token, key="shared-key")
        self.assertEqual((a.status_code, b.status_code), (201, 201))
        self.assertIsNone(b.headers.get("Idempotent-Replayed"))
        self.assertNotEqual(a.json()["id"], b.json()["id"])
        self.assertEqual(Event.objects.count(), 2)
        self.assertEqual(IdempotencyRecord.objects.count(), 2)

    def test_malformed_keys_are_refused_and_never_stored(self):
        bad_keys = [
            "",
            " ",
            "a b",
            "a" * 65,
            "ключ",
            "key\n",
            "key;drop",
            "a/b",
            "kéy",
        ]
        body = event_body()
        for key in bad_keys:
            with self.subTest(key=key):
                response = post(EVENTS, body, self.org_token, key=key)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.json()["error"]["code"], "validation_error")
        self.assertEqual(Event.objects.count(), 0)
        self.assertEqual(IdempotencyRecord.objects.count(), 0)

    def test_a_64_character_key_is_accepted(self):
        response = post(EVENTS, event_body(), self.org_token, key="a" * 64)
        self.assertEqual(response.status_code, 201, response.content)

    def test_auth_and_role_checks_run_before_any_key_is_stored(self):
        body = event_body()
        anonymous = post(EVENTS, body, token=None, key="k-anon")
        self.assertEqual(anonymous.status_code, 401)
        attendee = post(EVENTS, body, self.attendee_token, key="k-attendee")
        self.assertEqual(attendee.status_code, 403)
        self.assertEqual(IdempotencyRecord.objects.count(), 0)
        self.assertEqual(Event.objects.count(), 0)

    def test_expired_key_runs_the_request_again(self):
        body = event_body()
        post(EVENTS, body, self.org_token, key="k-old")
        IdempotencyRecord.objects.update(created_at=timezone.now() - timedelta(hours=25))
        again = post(EVENTS, body, self.org_token, key="k-old")
        self.assertEqual(again.status_code, 201, again.content)
        self.assertIsNone(again.headers.get("Idempotent-Replayed"))
        self.assertEqual(Event.objects.count(), 2)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_registration_replay_does_not_double_register_or_double_notify(self):
        event = make_event(User.objects.get(username="org1"))
        body = registration_body(event)
        first = post(REGISTRATIONS, body, self.attendee_token, key="k-reg")
        second = post(REGISTRATIONS, body, self.attendee_token, key="k-reg")
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(second.headers.get("Idempotent-Replayed"), "true")
        self.assertEqual(Registration.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(event=event).count(), 1)

    def test_a_refused_registration_with_a_new_key_stores_no_record(self):
        event = make_event(User.objects.get(username="org1"))
        body = registration_body(event)
        post(REGISTRATIONS, body, self.attendee_token, key="k-first")
        second = post(REGISTRATIONS, body, self.attendee_token, key="k-second")
        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.json()["error"]["code"], "already_registered")
        self.assertFalse(IdempotencyRecord.objects.filter(key="k-second").exists())
        self.assertEqual(Registration.objects.count(), 1)


@FAST_HASHER
@override_settings(THROTTLING_ENABLED=False)
class IdempotencyRaceTests(TransactionTestCase):
    def setUp(self):
        self.org = make_user("org1", role="organizer")
        make_user("a1")
        make_user("a2")
        self.org_token = access_token("org1")
        self.a1_token = access_token("a1")
        self.a2_token = access_token("a2")

    def test_same_key_same_body_in_parallel_creates_one_event(self):
        body = event_body()
        results = parallel(
            [lambda: post(EVENTS, body, self.org_token, key="race-1") for _ in range(8)]
        )
        self.assertEqual([r[0] for r in results], [201] * 8, results)
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)
        self.assertEqual(sum(1 for r in results if r[1] is None), 1)
        self.assertEqual(len({r[2]["id"] for r in results}), 1)

    def test_same_key_different_bodies_in_parallel_lets_exactly_one_win(self):
        results = parallel(
            [
                lambda n=n: post(
                    EVENTS, event_body(title=f"Title {n}"), self.org_token, key="race-2"
                )
                for n in range(8)
            ]
        )
        self.assertEqual(sorted(r[0] for r in results), [201] + [422] * 7, results)
        self.assertEqual(Event.objects.count(), 1)

    def test_a_failing_request_cannot_poison_a_key_used_by_a_good_one(self):
        good = event_body()
        jobs = [lambda: post(EVENTS, good, self.org_token, key="race-3") for _ in range(4)]
        jobs += [
            lambda: post(EVENTS, event_body(capacity=0), self.org_token, key="race-3")
            for _ in range(4)
        ]
        results = parallel(jobs)
        self.assertEqual([r[0] for r in results[:4]], [201] * 4, results)
        for status, _, _ in results[4:]:
            self.assertIn(status, (400, 422))
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_same_key_registration_race_registers_once(self):
        event = make_event(self.org, capacity=5)
        body = registration_body(event)
        results = parallel(
            [lambda: post(REGISTRATIONS, body, self.a1_token, key="race-4") for _ in range(8)]
        )
        self.assertEqual([r[0] for r in results], [201] * 8, results)
        self.assertEqual(Registration.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(event=event).count(), 1)

    def test_two_users_sharing_a_key_value_never_see_each_others_answers(self):
        event = make_event(self.org, capacity=5)
        body = registration_body(event)
        results = parallel(
            [
                lambda: post(REGISTRATIONS, body, self.a1_token, key="race-5"),
                lambda: post(REGISTRATIONS, body, self.a2_token, key="race-5"),
                lambda: post(REGISTRATIONS, body, self.a1_token, key="race-5"),
                lambda: post(REGISTRATIONS, body, self.a2_token, key="race-5"),
            ]
        )
        self.assertEqual([r[0] for r in results], [201] * 4, results)
        self.assertEqual(Registration.objects.count(), 2)
        self.assertEqual(sum(1 for r in results if r[1] is None), 2)
        self.assertEqual(len({r[2]["id"] for r in results}), 2)


@FAST_HASHER
class IdempotencyAdminTests(TestCase):
    def test_records_are_read_only_in_the_admin(self):
        boss = make_user("boss", superuser=True)
        make_user("org1", role="organizer")
        with override_settings(THROTTLING_ENABLED=False):
            post(EVENTS, event_body(), access_token("org1"), key="k-admin")
        record = IdempotencyRecord.objects.get()
        client = Client()
        client.force_login(boss)
        self.assertEqual(
            client.get(reverse("admin:registrations_idempotencyrecord_add")).status_code, 403
        )
        change = reverse("admin:registrations_idempotencyrecord_change", args=[record.pk])
        self.assertEqual(client.post(change, {"key": "tampered"}).status_code, 403)
        delete = reverse("admin:registrations_idempotencyrecord_delete", args=[record.pk])
        self.assertEqual(client.post(delete, {"post": "yes"}).status_code, 403)
        record.refresh_from_db()
        self.assertEqual(record.key, "k-admin")
