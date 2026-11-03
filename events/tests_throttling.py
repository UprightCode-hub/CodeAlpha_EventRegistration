"""Tests for the rate limits on the chosen endpoints."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

User = get_user_model()

RATES = {
    "login": {"anon": "3/min", "user": "6/min"},
    "register": {"anon": "3/min", "user": "6/min"},
    "public_read": {"anon": "3/min", "user": "6/min"},
    "write": {"anon": "3/min", "user": "5/min"},
}
EVENTS = "/api/events/"
REGISTRATIONS = "/api/registrations/"
LOGIN = "/api/auth/login/"
REGISTER = "/api/auth/register/"


@override_settings(THROTTLING_ENABLED=True, THROTTLE_RATES=RATES)
class ThrottleTestBase(TestCase):
    """Starts every test with an empty cache and two users who need no password."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.organizer = User.objects.create(
            username="thr_org", email="thr_org@example.com", role="organizer"
        )
        self.attendee = User.objects.create(
            username="thr_att", email="thr_att@example.com", role="attendee"
        )
        self.other = User.objects.create(
            username="thr_other", email="thr_other@example.com", role="attendee"
        )

    def as_user(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def assert_throttled(self, response):
        self.assertEqual(response.status_code, 429, response.content)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "too_many_requests",
                    "message": "Too many requests. Try again later.",
                }
            },
        )
        retry_after = int(response["Retry-After"])
        self.assertGreaterEqual(retry_after, 1)
        self.assertLessEqual(retry_after, 60)

    def call_until_limit(self, send, allowed, expected_status):
        for number in range(allowed):
            response = send()
            self.assertEqual(
                response.status_code, expected_status, f"call {number + 1}: {response.content}"
            )
        self.assert_throttled(send())


class AnonymousLimitTests(ThrottleTestBase):
    """Checks the limits for clients who are not signed in."""

    def test_login_is_limited(self):
        count = iter(range(100))

        def attempt():
            body = {"username": f"nobody{next(count)}", "password": "wrong-password"}
            return APIClient().post(LOGIN, body, format="json")

        self.call_until_limit(attempt, 3, 401)

    def test_register_is_limited(self):
        self.call_until_limit(lambda: APIClient().post(REGISTER, {}, format="json"), 3, 400)

    def test_the_public_event_list_is_limited(self):
        client = APIClient()
        self.call_until_limit(lambda: client.get(EVENTS), 3, 200)

    def test_list_and_detail_share_one_count(self):
        client = APIClient()
        self.assertEqual(client.get(EVENTS).status_code, 200)
        self.assertEqual(client.get(EVENTS).status_code, 200)
        self.assertEqual(client.get(f"{EVENTS}999999/").status_code, 404)
        self.assert_throttled(client.get(EVENTS))

    def test_scopes_are_counted_separately(self):
        client = APIClient()
        self.call_until_limit(lambda: client.get(EVENTS), 3, 200)
        response = client.post(
            LOGIN, {"username": "nobody", "password": "wrong-password"}, format="json"
        )
        self.assertEqual(response.status_code, 401)

    def test_a_forged_forwarded_address_does_not_help(self):
        client = APIClient()
        for address in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
            self.assertEqual(client.get(EVENTS, HTTP_X_FORWARDED_FOR=address).status_code, 200)
        self.assert_throttled(client.get(EVENTS, HTTP_X_FORWARDED_FOR="4.4.4.4"))

    def test_unauthenticated_writes_are_401_and_never_counted(self):
        client = APIClient()
        for _ in range(10):
            self.assertEqual(client.post(EVENTS, {}, format="json").status_code, 401)


class SignedInLimitTests(ThrottleTestBase):
    """Checks that signed-in users get a higher limit and their own count."""

    def test_a_signed_in_user_gets_the_higher_limit(self):
        anonymous = APIClient()
        self.call_until_limit(lambda: anonymous.get(EVENTS), 3, 200)
        user = self.as_user(self.attendee)
        self.call_until_limit(lambda: user.get(EVENTS), 6, 200)
        self.assert_throttled(anonymous.get(EVENTS))

    def test_each_user_has_their_own_count(self):
        first = self.as_user(self.attendee)
        self.call_until_limit(lambda: first.get(EVENTS), 6, 200)
        self.assertEqual(self.as_user(self.other).get(EVENTS).status_code, 200)

    def test_event_create_is_limited(self):
        organizer = self.as_user(self.organizer)
        self.call_until_limit(lambda: organizer.post(EVENTS, {}, format="json"), 5, 400)

    def test_registration_create_is_limited(self):
        attendee = self.as_user(self.attendee)
        self.call_until_limit(lambda: attendee.post(REGISTRATIONS, {}, format="json"), 5, 400)

    def test_writes_share_one_count_across_operations(self):
        organizer = self.as_user(self.organizer)
        for _ in range(3):
            self.assertEqual(organizer.post(EVENTS, {}, format="json").status_code, 400)
        for _ in range(2):
            self.assertEqual(
                organizer.post(f"{EVENTS}999999/cancel/", {}, format="json").status_code, 404
            )
        self.assert_throttled(organizer.post(EVENTS, {}, format="json"))


class UnlimitedEndpointTests(ThrottleTestBase):
    """Checks the endpoints that are not limited."""

    def test_health_is_never_limited(self):
        client = APIClient()
        for _ in range(10):
            self.assertEqual(client.get("/api/health/").status_code, 200)

    def test_me_is_never_limited(self):
        user = self.as_user(self.attendee)
        for _ in range(10):
            self.assertEqual(user.get("/api/auth/me/").status_code, 200)

    def test_my_registrations_and_notifications_are_never_limited(self):
        user = self.as_user(self.attendee)
        for _ in range(10):
            self.assertEqual(user.get(REGISTRATIONS).status_code, 200)
            self.assertEqual(user.get("/api/notifications/").status_code, 200)

    def test_switching_the_limit_off_lets_everything_through(self):
        client = APIClient()
        with override_settings(THROTTLING_ENABLED=False):
            for _ in range(10):
                self.assertEqual(client.get(EVENTS).status_code, 200)


class DefaultRateTests(SimpleTestCase):
    """Checks the shape of the default rates in the settings."""

    PER_MINUTE = {"s": 60, "m": 1, "h": 1 / 60, "d": 1 / 1440}

    def per_minute(self, rate):
        number, period = rate.split("/")
        return int(number) * self.PER_MINUTE[period[0]]

    def test_every_scope_has_both_rates_and_users_get_more(self):
        for scope in ("login", "register", "public_read", "write"):
            with self.subTest(scope=scope):
                rates = settings.THROTTLE_RATES[scope]
                self.assertGreater(self.per_minute(rates["user"]), self.per_minute(rates["anon"]))
