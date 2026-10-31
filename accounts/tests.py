"""Tests for accounts: register, login, refresh, me, the error format, logging and the Django admin."""

import os
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APITestCase

from accounts.views import RegisterView

User = get_user_model()

REGISTER_URL = "/api/auth/register/"
LOGIN_URL = "/api/auth/login/"
REFRESH_URL = "/api/auth/refresh/"
ME_URL = "/api/auth/me/"
PASSWORD = "Tr4vel-Mango-Quartz!"


def error_of(response):
    return response.json()["error"]


class AccountTestBase(APITestCase):
    def register(self, **overrides):
        data = {
            "username": "alice",
            "email": "alice@example.com",
            "password": PASSWORD,
            "role": "attendee",
        }
        data.update(overrides)
        return self.client.post(REGISTER_URL, data, format="json")

    def make_user(self, username="alice", role="attendee"):
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password=PASSWORD,
            role=role,
        )

    def tokens(self, username="alice"):
        response = self.client.post(
            LOGIN_URL, {"username": username, "password": PASSWORD}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(error_of(response)["code"], code)


class RegisterTests(AccountTestBase):
    def test_register_attendee(self):
        response = self.register()
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(set(body), {"id", "username", "email", "role"})
        self.assertEqual(body["username"], "alice")
        self.assertEqual(body["role"], "attendee")

    def test_register_organizer(self):
        response = self.register(username="olu", email="olu@example.com", role="organizer")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["role"], "organizer")

    def test_password_is_hashed_and_never_returned(self):
        response = self.register()
        self.assertNotIn("password", response.json())
        user = User.objects.get(username="alice")
        self.assertNotEqual(user.password, PASSWORD)
        self.assertTrue(user.check_password(PASSWORD))

    def test_missing_role_is_rejected(self):
        response = self.client.post(
            REGISTER_URL,
            {"username": "alice", "email": "alice@example.com", "password": PASSWORD},
            format="json",
        )
        self.assert_error(response, 400, "validation_error")
        self.assertIn("role", error_of(response)["fields"])

    def test_unknown_role_is_rejected(self):
        response = self.register(role="admin")
        self.assert_error(response, 400, "validation_error")
        self.assertIn("role", error_of(response)["fields"])

    def test_every_missing_field_is_reported(self):
        response = self.client.post(REGISTER_URL, {}, format="json")
        self.assert_error(response, 400, "validation_error")
        fields = error_of(response)["fields"]
        for name in ("username", "email", "password", "role"):
            self.assertIn(name, fields)

    def test_duplicate_username_is_rejected(self):
        self.register()
        response = self.register(email="other@example.com")
        self.assert_error(response, 400, "validation_error")
        self.assertIn("username", error_of(response)["fields"])

    def test_duplicate_email_is_rejected_ignoring_case(self):
        self.register()
        response = self.register(username="bob", email="ALICE@Example.com")
        self.assert_error(response, 400, "validation_error")
        self.assertIn("email", error_of(response)["fields"])

    def test_email_is_stored_in_lowercase(self):
        response = self.register(email="Alice@Example.COM")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["email"], "alice@example.com")
        self.assertEqual(User.objects.get(username="alice").email, "alice@example.com")

    def test_weak_password_is_rejected(self):
        response = self.register(password="123")
        self.assert_error(response, 400, "validation_error")
        self.assertIn("password", error_of(response)["fields"])

    def test_staff_rights_cannot_be_granted_through_register(self):
        response = self.register(is_staff=True, is_superuser=True)
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(username="alice")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_malformed_json_is_a_validation_error(self):
        response = self.client.post(
            REGISTER_URL, data="{not json", content_type="application/json"
        )
        self.assert_error(response, 400, "validation_error")


class LoginTests(AccountTestBase):
    def test_login_returns_both_tokens(self):
        self.make_user()
        response = self.client.post(
            LOGIN_URL, {"username": "alice", "password": PASSWORD}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"access", "refresh"})

    def test_wrong_password_is_401(self):
        self.make_user()
        response = self.client.post(
            LOGIN_URL, {"username": "alice", "password": "wrong-password"}, format="json"
        )
        self.assert_error(response, 401, "not_authenticated")

    def test_unknown_user_gets_the_same_answer_as_a_wrong_password(self):
        self.make_user()
        wrong_password = self.client.post(
            LOGIN_URL, {"username": "alice", "password": "wrong-password"}, format="json"
        )
        unknown_user = self.client.post(
            LOGIN_URL, {"username": "nobody", "password": "wrong-password"}, format="json"
        )
        self.assertEqual(wrong_password.status_code, unknown_user.status_code)
        self.assertEqual(wrong_password.json(), unknown_user.json())

    def test_missing_credentials_are_a_validation_error(self):
        response = self.client.post(LOGIN_URL, {}, format="json")
        self.assert_error(response, 400, "validation_error")


class RefreshTests(AccountTestBase):
    def test_refresh_returns_a_new_access_token(self):
        self.make_user()
        tokens = self.tokens()
        response = self.client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.json())

    def test_garbage_refresh_token_is_401(self):
        response = self.client.post(REFRESH_URL, {"refresh": "not-a-token"}, format="json")
        self.assert_error(response, 401, "not_authenticated")

    def test_access_token_cannot_be_used_to_refresh(self):
        self.make_user()
        tokens = self.tokens()
        response = self.client.post(REFRESH_URL, {"refresh": tokens["access"]}, format="json")
        self.assert_error(response, 401, "not_authenticated")


class MeTests(AccountTestBase):
    def test_me_returns_the_current_user(self):
        self.make_user(role="organizer")
        tokens = self.tokens()
        response = self.client.get(ME_URL, HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"id", "username", "email", "role"})
        self.assertEqual(body["username"], "alice")
        self.assertEqual(body["role"], "organizer")

    def test_me_requires_a_token(self):
        response = self.client.get(ME_URL)
        self.assert_error(response, 401, "not_authenticated")

    def test_me_rejects_a_garbage_token(self):
        response = self.client.get(ME_URL, HTTP_AUTHORIZATION="Bearer not-a-token")
        self.assert_error(response, 401, "not_authenticated")

    def test_me_rejects_a_refresh_token(self):
        self.make_user()
        tokens = self.tokens()
        response = self.client.get(ME_URL, HTTP_AUTHORIZATION=f"Bearer {tokens['refresh']}")
        self.assert_error(response, 401, "not_authenticated")


class ErrorFormatTests(AccountTestBase):
    def test_unknown_url_is_a_json_404(self):
        response = self.client.get("/api/nope/")
        self.assert_error(response, 404, "not_found")
        self.assertEqual(set(error_of(response)), {"code", "message"})

    @override_settings(DEBUG=True)
    def test_unknown_url_is_json_even_when_debug_is_on(self):
        response = self.client.get("/api/nope/")
        self.assert_error(response, 404, "not_found")
        self.assertTrue(response["Content-Type"].startswith("application/json"))

    def test_root_url_is_a_json_404(self):
        response = self.client.get("/")
        self.assert_error(response, 404, "not_found")

    def test_wrong_method_is_405(self):
        response = self.client.get(REGISTER_URL)
        self.assert_error(response, 405, "method_not_allowed")

    def test_unhandled_exception_returns_a_generic_500(self):
        self.client.raise_request_exception = False
        with patch.object(RegisterView, "post", side_effect=RuntimeError("secret detail")):
            response = self.client.post(REGISTER_URL, {}, format="json")
        self.assert_error(response, 500, "server_error")
        self.assertNotIn("secret detail", response.content.decode())
        self.assertNotIn("RuntimeError", response.content.decode())

    def test_unhandled_exception_is_logged_with_its_traceback(self):
        self.client.raise_request_exception = False
        with patch.object(RegisterView, "post", side_effect=RuntimeError("boom")):
            with self.assertLogs("django.request", level="ERROR") as captured:
                self.client.post(REGISTER_URL, {}, format="json")
        self.assertTrue(any(record.exc_info for record in captured.records))


class AdminTests(TestCase):
    def make_superuser(self):
        return User.objects.create_superuser(
            username="boss", email="boss@example.com", password=PASSWORD
        )

    def test_admin_without_a_trailing_slash_redirects(self):
        response = self.client.get("/" + settings.ADMIN_URL.rstrip("/"))
        self.assertIn(response.status_code, (301, 302))
        self.assertTrue(response["Location"].endswith("/" + settings.ADMIN_URL))

    def test_superuser_can_open_the_admin_and_the_user_list(self):
        self.make_superuser()
        self.assertTrue(self.client.login(username="boss", password=PASSWORD))
        self.assertEqual(self.client.get("/" + settings.ADMIN_URL).status_code, 200)
        response = self.client.get("/" + settings.ADMIN_URL + "accounts/user/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "boss")

    def test_regular_user_cannot_open_the_admin(self):
        User.objects.create_user(
            username="alice", email="alice@example.com", password=PASSWORD
        )
        self.assertTrue(self.client.login(username="alice", password=PASSWORD))
        response = self.client.get("/" + settings.ADMIN_URL)
        self.assertEqual(response.status_code, 302)

    def test_create_superuser_defaults_the_role_to_organizer(self):
        user = self.make_superuser()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.role, "organizer")

    def test_createsuperuser_command_asks_for_no_role(self):
        with patch.dict(os.environ, {"DJANGO_SUPERUSER_PASSWORD": PASSWORD}):
            call_command(
                "createsuperuser",
                interactive=False,
                username="cli",
                email="cli@example.com",
                verbosity=0,
            )
        user = User.objects.get(username="cli")
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.role, "organizer")


class UserModelTests(TestCase):
    def test_roles_are_exactly_organizer_and_attendee(self):
        choices = {value for value, _label in User._meta.get_field("role").choices}
        self.assertEqual(choices, {"organizer", "attendee"})

    def test_new_users_default_to_attendee(self):
        user = User.objects.create_user("zed", "zed@example.com", PASSWORD)
        self.assertEqual(user.role, "attendee")

    def test_email_is_unique_in_the_database(self):
        User.objects.create_user("one", "same@example.com", PASSWORD)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user("two", "same@example.com", PASSWORD)


class SettingsTests(SimpleTestCase):
    def test_user_model_is_the_custom_user(self):
        self.assertEqual(settings.AUTH_USER_MODEL, "accounts.User")

    def test_times_are_stored_in_utc(self):
        self.assertEqual(settings.TIME_ZONE, "UTC")
        self.assertTrue(settings.USE_TZ)

    def test_responses_are_json_only(self):
        self.assertEqual(
            settings.REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"],
            ["rest_framework.renderers.JSONRenderer"],
        )

    def test_logging_has_a_handler(self):
        self.assertTrue(settings.LOGGING.get("handlers"))