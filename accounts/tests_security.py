"""Tests for the sign-in lockout, the password length cap and the security log."""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import LoginFailure

User = get_user_model()

PASSWORD = "Str0ng-Pass-77!"
WRONG = "wrong-Pass-1"
LOGIN = "/api/auth/login/"
REGISTER = "/api/auth/register/"
IP_A = "10.0.0.5"
IP_B = "10.0.0.6"


def make_user(username, role="attendee", password=PASSWORD):
    """Create a user directly in the database."""
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password=password, role=role
    )


class SecurityBase(TestCase):
    """Shared helpers for the sign-in tests."""

    def setUp(self):
        self.client = APIClient()

    def login(self, username, password=PASSWORD, ip=IP_A):
        """Post a login from a chosen address."""
        return self.client.post(
            LOGIN, {"username": username, "password": password}, format="json", REMOTE_ADDR=ip
        )

    def fail(self, username, times, ip=IP_A):
        """Make wrong attempts and check each one is a plain 401."""
        for _ in range(times):
            response = self.login(username, WRONG, ip)
            self.assertEqual(response.status_code, 401)

    def later(self, seconds):
        """Patch the clock forward for the code under test."""
        return mock.patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(seconds=seconds))


class PasswordLengthTests(SecurityBase):
    """The password has an upper length."""

    def register(self, password):
        return self.client.post(
            REGISTER,
            {"username": "longpw", "email": "longpw@example.com", "password": password, "role": "attendee"},
            format="json",
        )

    def test_register_accepts_128_characters(self):
        response = self.register("Xq7!" * 32)
        self.assertEqual(response.status_code, 201)

    def test_register_rejects_129_characters(self):
        response = self.register("Xq7!" * 32 + "a")
        self.assertEqual(response.status_code, 400)
        error = response.json()["error"]
        self.assertEqual(error["code"], "validation_error")
        self.assertIn("password", error["fields"])

    def test_login_with_a_128_character_password_works(self):
        password = "Xq7!" * 32
        make_user("longok", password=password)
        self.assertEqual(self.login("longok", password).status_code, 200)

    def test_login_with_a_129_character_password_is_refused_as_a_failure(self):
        make_user("ada")
        response = self.login("ada", "a" * 129)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "not_authenticated")
        self.assertEqual(LoginFailure.objects.count(), 1)

    def test_login_with_a_151_character_username_is_refused_as_a_failure(self):
        response = self.login("u" * 151, PASSWORD)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "not_authenticated")
        self.assertEqual(LoginFailure.objects.count(), 1)

    def test_over_length_answer_matches_a_wrong_password_answer(self):
        make_user("ada")
        wrong = self.login("ada", WRONG)
        long_password = self.login("ada", "a" * 129)
        self.assertEqual(wrong.json(), long_password.json())


class LockoutTests(SecurityBase):
    """Repeated failed sign-ins are blocked."""

    def test_the_sixth_attempt_is_blocked_even_with_the_right_password(self):
        make_user("ada")
        self.fail("ada", 5)
        response = self.login("ada", PASSWORD)
        self.assertEqual(response.status_code, 429)
        error = response.json()["error"]
        self.assertEqual(error["code"], "too_many_attempts")
        self.assertEqual(error["message"], "Too many failed sign-in attempts. Try again later.")
        self.assertNotIn("access", response.json())
        retry = int(response.headers["Retry-After"])
        self.assertTrue(1 <= retry <= 900)

    def test_four_failures_do_not_block(self):
        make_user("ada")
        self.fail("ada", 4)
        self.assertEqual(self.login("ada", PASSWORD).status_code, 200)

    def test_a_success_resets_the_counter(self):
        make_user("ada")
        self.fail("ada", 4)
        self.assertEqual(self.login("ada", PASSWORD).status_code, 200)
        self.fail("ada", 4)
        self.assertEqual(self.login("ada", PASSWORD).status_code, 200)

    def test_a_blocked_attempt_is_not_recorded(self):
        make_user("ada")
        self.fail("ada", 5)
        self.login("ada", PASSWORD)
        self.login("ada", WRONG)
        self.assertEqual(LoginFailure.objects.count(), 5)

    def test_another_username_from_the_same_address_is_not_blocked(self):
        make_user("ada")
        make_user("bob")
        self.fail("ada", 5)
        self.assertEqual(self.login("ada", PASSWORD).status_code, 429)
        self.assertEqual(self.login("bob", PASSWORD).status_code, 200)

    def test_the_same_username_from_another_address_is_not_blocked(self):
        make_user("ada")
        self.fail("ada", 5, ip=IP_A)
        self.assertEqual(self.login("ada", PASSWORD, ip=IP_A).status_code, 429)
        self.assertEqual(self.login("ada", PASSWORD, ip=IP_B).status_code, 200)

    def test_an_unknown_username_locks_the_same_way(self):
        make_user("ada")
        self.fail("ghost", 5)
        blocked_unknown = self.login("ghost", PASSWORD)
        self.fail("ada", 5)
        blocked_known = self.login("ada", PASSWORD)
        self.assertEqual(blocked_unknown.status_code, 429)
        self.assertEqual(blocked_known.status_code, 429)
        self.assertEqual(blocked_unknown.json(), blocked_known.json())

    def test_usernames_count_together_ignoring_case_and_outer_spaces(self):
        make_user("ada")
        for name in ("ADA", "Ada", "ada ", " aDa", "ada"):
            self.assertEqual(self.login(name, WRONG).status_code, 401)
        self.assertEqual(self.login("ada", PASSWORD).status_code, 429)

    def test_the_address_limit_blocks_every_username(self):
        make_user("ada")
        for number in range(19):
            self.assertEqual(self.login(f"ghost{number}", WRONG).status_code, 401)
        self.assertEqual(self.login("ada", PASSWORD).status_code, 200)
        self.assertEqual(self.login("ghost19", WRONG).status_code, 401)
        response = self.login("ada", PASSWORD)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "too_many_attempts")

    def test_the_address_limit_does_not_reach_another_address(self):
        make_user("ada")
        for number in range(20):
            self.login(f"ghost{number}", WRONG)
        self.assertEqual(self.login("ada", PASSWORD, ip=IP_A).status_code, 429)
        self.assertEqual(self.login("ada", PASSWORD, ip=IP_B).status_code, 200)

    def test_the_block_ends_when_the_window_passes(self):
        make_user("ada")
        self.fail("ada", 5)
        self.assertEqual(self.login("ada", PASSWORD).status_code, 429)
        with self.later(901):
            self.assertEqual(self.login("ada", PASSWORD).status_code, 200)

    def test_the_block_holds_just_inside_the_window(self):
        make_user("ada")
        self.fail("ada", 5)
        with self.later(800):
            self.assertEqual(self.login("ada", PASSWORD).status_code, 429)

    def test_old_failures_are_deleted_when_a_new_one_is_recorded(self):
        make_user("ada")
        self.fail("ada", 5)
        with self.later(901):
            self.assertEqual(self.login("ada", WRONG).status_code, 401)
        self.assertEqual(LoginFailure.objects.count(), 1)

    def test_a_malformed_body_is_not_a_failure(self):
        response = self.client.post(LOGIN, "{bad", content_type="application/json", REMOTE_ADDR=IP_A)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "validation_error")
        self.assertEqual(LoginFailure.objects.count(), 0)

    def test_a_missing_field_is_not_a_failure(self):
        response = self.client.post(LOGIN, {"username": "ada"}, format="json", REMOTE_ADDR=IP_A)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(LoginFailure.objects.count(), 0)

    def test_register_is_not_limited_by_the_lockout(self):
        self.fail("ghost", 5)
        response = self.client.post(
            REGISTER,
            {"username": "newbie", "email": "newbie@example.com", "password": PASSWORD, "role": "attendee"},
            format="json",
            REMOTE_ADDR=IP_A,
        )
        self.assertEqual(response.status_code, 201)

    def test_a_token_from_before_the_lockout_still_works(self):
        make_user("ada")
        token = self.login("ada", PASSWORD).json()["access"]
        self.fail("ada", 5)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)
        self.assertEqual(self.client.get("/api/auth/me/", REMOTE_ADDR=IP_A).status_code, 200)


class SecurityLogTests(SecurityBase):
    """Failed and blocked sign-ins are logged safely."""

    def test_a_failed_login_is_logged_without_the_password(self):
        make_user("ada")
        with self.assertLogs("accounts.security", level="WARNING") as captured:
            self.login("ada", WRONG)
        text = "\n".join(captured.output)
        self.assertIn("login failed", text)
        self.assertIn("ada", text)
        self.assertIn(IP_A, text)
        self.assertNotIn(WRONG, text)

    def test_a_control_character_in_the_username_cannot_forge_a_line(self):
        with self.assertLogs("accounts.security", level="WARNING") as captured:
            self.login("bad\nlogin failed username='root'", WRONG)
        for record in captured.records:
            self.assertNotIn("\n", record.getMessage())

    def test_a_blocked_login_is_logged(self):
        make_user("ada")
        self.fail("ada", 5)
        with self.assertLogs("accounts.security", level="WARNING") as captured:
            self.login("ada", PASSWORD)
        self.assertIn("login blocked", "\n".join(captured.output))

    def test_a_successful_login_writes_no_warning(self):
        make_user("ada")
        with self.assertNoLogs("accounts.security", level="WARNING"):
            self.assertEqual(self.login("ada", PASSWORD).status_code, 200)
