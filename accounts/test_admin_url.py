"""Tests for the admin served at a configurable path instead of /admin/."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, SimpleTestCase, TestCase, override_settings

from accounts.models import LoginFailure, User
from config.admin_url import normalize_admin_url

PASSWORD = "Zq7!rvTn#4Lw9dKp"


def admin_root():
    """The admin address as a request path, for example /eventreg-manage/."""
    return "/" + settings.ADMIN_URL


class NormalizeAdminUrlTests(SimpleTestCase):
    """Checks the cleaning and the refusal rules for the admin path value."""

    def test_a_trailing_slash_is_added(self):
        self.assertEqual(normalize_admin_url("control-room"), "control-room/")

    def test_one_trailing_slash_is_kept(self):
        self.assertEqual(normalize_admin_url("control-room/"), "control-room/")

    def test_spaces_and_outer_slashes_are_removed(self):
        self.assertEqual(normalize_admin_url("  /control_room/  "), "control_room/")
        self.assertEqual(normalize_admin_url("//control_room//"), "control_room/")

    def test_letters_digits_hyphen_and_underscore_are_allowed(self):
        self.assertEqual(normalize_admin_url("Ab-9_x"), "Ab-9_x/")

    def test_the_default_admin_path_is_refused_in_any_letter_case(self):
        for value in ("admin", "admin/", "/admin/", "ADMIN", "Admin/", " admin "):
            with self.subTest(value=value):
                with self.assertRaises(ImproperlyConfigured):
                    normalize_admin_url(value)

    def test_an_empty_value_is_refused(self):
        for value in ("", "   ", "/", "//"):
            with self.subTest(value=value):
                with self.assertRaises(ImproperlyConfigured):
                    normalize_admin_url(value)

    def test_more_than_one_segment_is_refused(self):
        for value in ("a/b", "a/b/", "/a/b/c"):
            with self.subTest(value=value):
                with self.assertRaises(ImproperlyConfigured):
                    normalize_admin_url(value)

    def test_other_characters_are_refused(self):
        for value in ("a b", "a.b", "a?b", "a#b", "a%20b", "a\\b", "caf\u00e9", "a\u0000b"):
            with self.subTest(value=value):
                with self.assertRaises(ImproperlyConfigured):
                    normalize_admin_url(value)


class AdminUrlSettingTests(SimpleTestCase):
    """Checks the value the project actually runs with."""

    def test_the_setting_ends_with_one_slash(self):
        self.assertTrue(settings.ADMIN_URL.endswith("/"))
        self.assertFalse(settings.ADMIN_URL.endswith("//"))

    def test_the_setting_is_not_the_default_admin_path(self):
        self.assertNotEqual(settings.ADMIN_URL.lower(), "admin/")

    def test_the_setting_is_already_cleaned(self):
        self.assertEqual(normalize_admin_url(settings.ADMIN_URL), settings.ADMIN_URL)


class OldAdminPathTests(TestCase):
    """Checks that /admin/ no longer opens anything and answers like an unknown URL."""

    def assert_json_not_found(self, response):
        self.assertEqual(response.status_code, 404)
        self.assertIn("application/json", response["Content-Type"])
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_admin_without_a_slash_is_not_found(self):
        self.assert_json_not_found(Client().get("/admin"))

    def test_admin_with_a_slash_is_not_found(self):
        self.assert_json_not_found(Client().get("/admin/"))

    def test_pages_under_the_old_path_are_not_found(self):
        for path in ("/admin/login/", "/admin/accounts/user/", "/admin/events/event/"):
            with self.subTest(path=path):
                self.assert_json_not_found(Client().get(path))

    def test_a_post_to_the_old_path_is_not_found_and_not_a_csrf_refusal(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post("/admin/login/", {"username": "a", "password": "b"})
        self.assert_json_not_found(response)

    def test_a_signed_in_superuser_does_not_get_in_at_the_old_path(self):
        user = User.objects.create_superuser(
            username="old_path_admin", email="oldpath@example.com", password=PASSWORD
        )
        client = Client()
        client.force_login(user)
        self.assert_json_not_found(client.get("/admin/"))


class AdminAtTheConfiguredPathTests(TestCase):
    """Checks that the admin works as before at the configured path."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="path_admin", email="pathadmin@example.com", password=PASSWORD
        )

    def test_the_path_without_a_slash_redirects_to_the_path_with_one(self):
        response = Client().get(admin_root().rstrip("/"))
        self.assertIn(response.status_code, (301, 302))
        self.assertEqual(response["Location"], admin_root())

    def test_an_anonymous_visitor_is_sent_to_the_login_page(self):
        response = Client().get(admin_root())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(admin_root() + "login/"))

    def test_the_login_page_opens(self):
        self.assertEqual(Client().get(admin_root() + "login/").status_code, 200)

    def test_a_superuser_can_open_the_admin_and_the_user_list(self):
        client = Client()
        client.force_login(self.superuser)
        self.assertEqual(client.get(admin_root()).status_code, 200)
        self.assertEqual(client.get(admin_root() + "accounts/user/").status_code, 200)

    def test_a_regular_user_cannot_open_the_admin(self):
        regular = User.objects.create_user(
            username="path_regular", email="pathregular@example.com", password=PASSWORD
        )
        client = Client()
        client.force_login(regular)
        response = client.get(admin_root())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(admin_root() + "login/"))

    def test_the_admin_login_still_checks_csrf(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(admin_root() + "login/", {"username": "a", "password": "b"})
        self.assertEqual(response.status_code, 403)


class AdminLoginSecurityTests(TestCase):
    """Checks lockout and failure accounting on the Django admin login form."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="protected_admin", email="protected@example.com", password=PASSWORD
        )
        self.login_url = admin_root() + "login/"

    @override_settings(LOGIN_MAX_FAILURES_PER_USER=2, LOGIN_MAX_FAILURES_PER_IP=20)
    def test_repeated_failed_admin_logins_are_locked(self):
        client = Client()
        with self.assertLogs("accounts.security", level="WARNING") as logs:
            for _ in range(2):
                response = client.post(
                    self.login_url, {"username": self.user.username, "password": "wrong"}
                )
                self.assertEqual(response.status_code, 200)
            response = client.post(
                self.login_url, {"username": self.user.username, "password": "wrong"}
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "900")
        self.assertEqual(LoginFailure.objects.filter(username=self.user.username).count(), 2)
        self.assertTrue(any("login blocked" in message for message in logs.output))

    @override_settings(LOGIN_MAX_FAILURES_PER_USER=2, LOGIN_MAX_FAILURES_PER_IP=20)
    def test_successful_admin_login_clears_failures(self):
        client = Client()
        failed = client.post(self.login_url, {"username": self.user.username, "password": "wrong"})
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(LoginFailure.objects.count(), 1)

        successful = client.post(
            self.login_url, {"username": self.user.username, "password": PASSWORD}
        )
        self.assertEqual(successful.status_code, 302)
        self.assertEqual(LoginFailure.objects.count(), 0)
