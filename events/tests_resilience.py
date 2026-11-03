"""Tests for health, database failure answers, the body size limit and the connect timeout."""

import json
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import InterfaceError, OperationalError
from django.test import Client, TestCase
from rest_framework.test import APIClient

User = get_user_model()

PASSWORD = "Str0ng-Pass-77!"
EVENTS = "/api/events/"
HEALTH = "/api/health/"
LIMIT = 1048576
SECRET_HOST = "db-secret-host.internal"


def database_down(error=OperationalError):
    """Make every database statement fail as if the server went away."""
    return mock.patch(
        "django.db.backends.utils.CursorWrapper.execute",
        side_effect=error(f"connection to server at {SECRET_HOST} failed"),
    )


class ResilienceBase(TestCase):
    """Shared helpers for the failure tests."""

    def setUp(self):
        self.client = APIClient()

    def signed_in(self, role="organizer"):
        """Return a client with a valid token, made before any failure is simulated."""
        User.objects.create_user(
            username="ada", email="ada@example.com", password=PASSWORD, role=role
        )
        client = APIClient()
        token = client.post(
            "/api/auth/login/", {"username": "ada", "password": PASSWORD}, format="json"
        ).json()["access"]
        client.credentials(HTTP_AUTHORIZATION="Bearer " + token)
        return client

    def assert_unavailable(self, response):
        """Check the standard 503 answer."""
        self.assertEqual(response.status_code, 503)
        error = response.json()["error"]
        self.assertEqual(error["code"], "service_unavailable")
        self.assertEqual(error["message"], "The service is temporarily unavailable.")
        self.assertNotIn("fields", error)
        self.assertEqual(response.headers["Retry-After"], "5")
        self.assertNotIn(SECRET_HOST, response.content.decode())
        self.assertNotIn("Traceback", response.content.decode())


class HealthTests(ResilienceBase):
    """The health route."""

    def test_health_is_public_and_ok(self):
        response = self.client.get(HEALTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_health_ignores_a_bad_token(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not.a.token")
        self.assertEqual(self.client.get(HEALTH).status_code, 200)

    def test_other_methods_are_not_allowed(self):
        response = self.client.post(HEALTH, {}, format="json")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()["error"]["code"], "method_not_allowed")

    def test_health_reports_a_database_failure(self):
        with database_down():
            response = self.client.get(HEALTH)
        self.assert_unavailable(response)

    def test_health_recovers_without_a_restart(self):
        with database_down():
            self.assertEqual(self.client.get(HEALTH).status_code, 503)
        self.assertEqual(self.client.get(HEALTH).status_code, 200)

    def test_health_is_not_in_the_schema(self):
        schema = json.loads(self.client.get("/api/schema/?format=json").content)
        self.assertNotIn("/api/health/", schema["paths"])


class DatabaseFailureTests(ResilienceBase):
    """A database failure is a clean 503 everywhere."""

    def test_a_public_list_answers_503(self):
        with database_down():
            response = self.client.get(EVENTS)
        self.assert_unavailable(response)

    def test_a_signed_in_request_answers_503(self):
        client = self.signed_in()
        with database_down():
            response = client.get("/api/auth/me/")
        self.assert_unavailable(response)

    def test_a_write_answers_503(self):
        client = self.signed_in()
        with database_down():
            response = client.post(EVENTS, {"title": "A title"}, format="json")
        self.assert_unavailable(response)

    def test_login_answers_503(self):
        with database_down():
            response = self.client.post(
                "/api/auth/login/", {"username": "ada", "password": PASSWORD}, format="json"
            )
        self.assert_unavailable(response)

    def test_an_interface_error_is_also_503(self):
        with database_down(InterfaceError):
            response = self.client.get(EVENTS)
        self.assert_unavailable(response)

    def test_the_failure_is_logged_at_error_level_with_a_traceback(self):
        with self.assertLogs("django.request", level="ERROR") as captured:
            with database_down():
                self.client.get(EVENTS)
        self.assertTrue(any(record.exc_info for record in captured.records))

    def test_the_server_works_again_afterwards(self):
        with database_down():
            self.assertEqual(self.client.get(EVENTS).status_code, 503)
        self.assertEqual(self.client.get(EVENTS).status_code, 200)

    def test_another_unhandled_error_is_still_the_generic_500(self):
        with mock.patch(
            "django.db.backends.utils.CursorWrapper.execute", side_effect=RuntimeError(SECRET_HOST)
        ):
            with self.assertLogs("django.request", level="ERROR"):
                response = self.client.get(EVENTS)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "server_error")
        self.assertNotIn(SECRET_HOST, response.content.decode())

    def test_the_connection_has_a_connect_timeout(self):
        self.assertEqual(settings.DATABASES["default"]["OPTIONS"]["connect_timeout"], 5)


class BodySizeTests(ResilienceBase):
    """Very large bodies are refused early."""

    def padded(self, size):
        """A JSON body of exactly the given number of bytes."""
        overhead = len(json.dumps({"x": ""}))
        return json.dumps({"x": "a" * (size - overhead)})

    def post_raw(self, client, body, url=EVENTS):
        return client.generic("POST", url, body, content_type="application/json")

    def test_the_limit_is_a_setting(self):
        self.assertEqual(settings.MAX_REQUEST_BODY_BYTES, LIMIT)

    def test_a_body_over_the_limit_is_413(self):
        client = self.signed_in()
        body = self.padded(LIMIT + 1)
        self.assertEqual(len(body), LIMIT + 1)
        response = self.post_raw(client, body)
        self.assertEqual(response.status_code, 413)
        error = response.json()["error"]
        self.assertEqual(error["code"], "payload_too_large")
        self.assertEqual(error["message"], "The request body is too large.")

    def test_a_body_at_the_limit_is_accepted_by_the_size_check(self):
        client = self.signed_in()
        body = self.padded(LIMIT)
        self.assertEqual(len(body), LIMIT)
        response = self.post_raw(client, body)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_the_size_check_runs_before_authentication(self):
        response = self.post_raw(self.client, self.padded(LIMIT + 1))
        self.assertEqual(response.status_code, 413)

    def test_it_applies_to_login_too(self):
        response = self.post_raw(self.client, self.padded(LIMIT + 1), url="/api/auth/login/")
        self.assertEqual(response.status_code, 413)

    def test_normal_requests_are_not_affected(self):
        client = self.signed_in()
        self.assertEqual(client.get("/api/auth/me/").status_code, 200)
        self.assertEqual(self.client.get(EVENTS).status_code, 200)


class CsrfEnforcedTests(TestCase):
    """The JSON answers hold when the server really checks CSRF, as it does outside the tests."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def test_unknown_urls_answer_json_not_found_for_every_method(self):
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            response = self.client.generic(
                method, "/api/nothing-here/", "{}", content_type="application/json"
            )
            self.assertEqual(response.status_code, 404, method)
            self.assertEqual(response.json()["error"]["code"], "not_found", method)

    def test_api_writes_need_no_csrf_token(self):
        response = self.client.post(
            "/api/auth/login/",
            {"username": "nobody", "password": "x"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "not_authenticated")
        response = self.client.post("/api/events/", "{}", content_type="application/json")
        self.assertEqual(response.status_code, 401)

    def test_the_admin_still_checks_csrf(self):
        response = self.client.post(
            "/" + settings.ADMIN_URL + "login/", {"username": "a", "password": "b"}
        )
        self.assertEqual(response.status_code, 403)
