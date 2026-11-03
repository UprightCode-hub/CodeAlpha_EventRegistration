"""Tests for the request id, the JSON log format, the access log and the metrics route."""

import json
import logging
import re
import sys
from datetime import datetime

import structlog
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

User = get_user_model()
PASSWORD = "Str0ng-Pass-9271!x"
HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEALTH = "/api/health/"


def render(record):
    """Return the text the console handler would print for a record."""
    handler = logging.getHandlerByName("console")
    assert handler is not None, "the console handler must be named console"
    return handler.format(record)


def make_record(msg, args=(), level=logging.INFO, name="tests.logger", exc_info=None, **extra):
    """Build a log record the way the logging module does."""
    values = {
        "name": name,
        "levelno": level,
        "levelname": logging.getLevelName(level),
        "msg": msg,
        "args": args,
        "exc_info": exc_info,
    }
    values.update(extra)
    return logging.makeLogRecord(values)


def metric_value(text, name, **labels):
    """Return the value of one sample in the exposition text, or 0.0 when it is not there."""
    for line in text.splitlines():
        if not line.startswith(name + "{"):
            continue
        if all(f'{key}="{value}"' in line for key, value in labels.items()):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


class RequestIdTests(TestCase):
    """Checks the X-Request-ID header on every kind of answer."""

    def test_a_made_id_is_32_lowercase_hex_characters(self):
        response = self.client.get(HEALTH)
        self.assertRegex(response["X-Request-ID"], HEX32)

    def test_two_requests_get_different_ids(self):
        first = self.client.get(HEALTH)["X-Request-ID"]
        second = self.client.get(HEALTH)["X-Request-ID"]
        self.assertNotEqual(first, second)

    def test_error_answers_carry_the_header(self):
        response = self.client.get("/api/nothing-here-xyz/")
        self.assertEqual(response.status_code, 404)
        self.assertRegex(response["X-Request-ID"], HEX32)

    def test_an_oversized_body_answer_carries_the_header(self):
        response = self.client.post(
            "/api/auth/login/", data=b"{}", content_type="application/json", CONTENT_LENGTH="1048577"
        )
        self.assertEqual(response.status_code, 413)
        self.assertRegex(response["X-Request-ID"], HEX32)

    def test_a_valid_id_from_the_client_is_kept(self):
        response = self.client.get(HEALTH, HTTP_X_REQUEST_ID="abc-123_XYZ")
        self.assertEqual(response["X-Request-ID"], "abc-123_XYZ")

    def test_an_id_of_exactly_64_characters_is_kept(self):
        value = "a" * 64
        self.assertEqual(self.client.get(HEALTH, HTTP_X_REQUEST_ID=value)["X-Request-ID"], value)

    def test_bad_ids_are_replaced(self):
        for bad in ("has space", "x" * 65, "bad;chars", "<script>", "a/b", ""):
            with self.subTest(value=bad):
                response = self.client.get(HEALTH, HTTP_X_REQUEST_ID=bad)
                self.assertNotEqual(response["X-Request-ID"], bad)
                self.assertRegex(response["X-Request-ID"], HEX32)


class AccessLogTests(TestCase):
    """Checks the one access record written for every request."""

    def test_one_record_per_request_with_the_agreed_attributes(self):
        with self.assertLogs("api.access", level="INFO") as captured:
            response = self.client.get(HEALTH)
        self.assertEqual(len(captured.records), 1)
        record = captured.records[0]
        self.assertEqual(record.getMessage(), "request finished")
        self.assertEqual(record.levelno, logging.INFO)
        self.assertEqual(record.method, "GET")
        self.assertEqual(record.path, HEALTH)
        self.assertEqual(record.status, 200)
        self.assertIsInstance(record.duration_ms, (int, float))
        self.assertGreaterEqual(record.duration_ms, 0)
        self.assertEqual(record.request_id, response["X-Request-ID"])
        self.assertIsNone(record.user_id)

    def test_error_answers_are_logged_with_their_status(self):
        with self.assertLogs("api.access", level="INFO") as captured:
            self.client.get("/api/nothing-here-xyz/")
        self.assertEqual(captured.records[0].status, 404)

    def test_the_query_string_is_never_logged(self):
        with self.assertLogs("api.access", level="INFO") as captured:
            self.client.get("/api/events/", {"q": "hunter2secret"})
        record = captured.records[0]
        self.assertEqual(record.path, "/api/events/")
        self.assertNotIn("hunter2secret", render(record))

    def test_a_signed_in_user_is_recorded_by_id(self):
        user = User.objects.create_user(
            username="log_user", email="log_user@example.com", password=PASSWORD, role="attendee"
        )
        login = self.client.post(
            "/api/auth/login/", {"username": "log_user", "password": PASSWORD}, content_type="application/json"
        )
        token = login.json()["access"]
        with self.assertLogs("api.access", level="INFO") as captured:
            response = self.client.get("/api/auth/me/", HTTP_AUTHORIZATION="Bearer " + token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured.records[0].user_id, user.id)

    def test_the_json_line_holds_the_same_values(self):
        with self.assertLogs("api.access", level="INFO") as captured:
            self.client.get(HEALTH)
        record = captured.records[0]
        data = json.loads(render(record))
        self.assertEqual(data["event"], "request finished")
        self.assertEqual(data["logger"], "api.access")
        self.assertEqual(data["level"], "info")
        self.assertEqual(data["method"], "GET")
        self.assertEqual(data["status"], 200)
        self.assertEqual(data["request_id"], record.request_id)

    def test_passwords_and_tokens_never_reach_any_log_line(self):
        secret_password = "NeverLogThisPw-1"
        secret_token = "secrettoken123abc"
        with self.assertLogs(level="DEBUG") as captured:
            self.client.post(
                "/api/auth/login/",
                {"username": "nobody", "password": secret_password},
                content_type="application/json",
            )
            self.client.get("/api/auth/me/", HTTP_AUTHORIZATION="Bearer " + secret_token)
        self.assertTrue(captured.records)
        for record in captured.records:
            text = render(record) + record.getMessage()
            self.assertNotIn(secret_password, text)
            self.assertNotIn(secret_token, text)


class JsonFormatTests(TestCase):
    """Checks the format of the lines the console handler prints."""

    def setUp(self):
        structlog.contextvars.clear_contextvars()
        self.addCleanup(structlog.contextvars.clear_contextvars)

    def test_a_line_is_one_json_object_with_the_agreed_keys(self):
        record = make_record(
            "login failed username=%r address=%s",
            ("alice", "127.0.0.1"),
            level=logging.WARNING,
            name="accounts.security",
        )
        line = render(record)
        self.assertEqual(len(line.splitlines()), 1)
        data = json.loads(line)
        self.assertEqual(data["level"], "warning")
        self.assertEqual(data["logger"], "accounts.security")
        self.assertEqual(data["event"], "login failed username='alice' address=127.0.0.1")
        stamp = datetime.fromisoformat(data["timestamp"])
        self.assertIsNotNone(stamp.utcoffset())
        self.assertEqual(stamp.utcoffset().total_seconds(), 0)

    def test_levels_are_lowercase(self):
        for level, name in ((logging.INFO, "info"), (logging.ERROR, "error"), (logging.DEBUG, "debug")):
            with self.subTest(level=name):
                self.assertEqual(json.loads(render(make_record("x", level=level)))["level"], name)

    def test_extra_fields_are_included(self):
        data = json.loads(render(make_record("x", order_id=7, shop="north")))
        self.assertEqual(data["order_id"], 7)
        self.assertEqual(data["shop"], "north")

    def test_an_exception_goes_into_the_exception_key(self):
        try:
            raise RuntimeError("boom-detail")
        except RuntimeError:
            record = make_record("it failed", level=logging.ERROR, exc_info=sys.exc_info())
        line = render(record)
        self.assertEqual(len(line.splitlines()), 1)
        data = json.loads(line)
        self.assertIn("RuntimeError", data["exception"])
        self.assertIn("boom-detail", data["exception"])

    def test_the_request_id_is_added_when_one_is_bound(self):
        self.assertNotIn("request_id", json.loads(render(make_record("x"))))
        structlog.contextvars.bind_contextvars(request_id="ctx-123")
        self.assertEqual(json.loads(render(make_record("x")))["request_id"], "ctx-123")

    def test_structlog_loggers_come_out_in_the_same_format(self):
        with self.assertLogs("tests.structlog", level="INFO") as captured:
            structlog.get_logger("tests.structlog").info("hello from structlog", shop="north")
        data = json.loads(render(captured.records[0]))
        self.assertEqual(data["event"], "hello from structlog")
        self.assertEqual(data["level"], "info")
        self.assertEqual(data["shop"], "north")


@override_settings(METRICS_ENABLED=True, METRICS_TOKEN="")
class MetricsTests(TestCase):
    """Checks the /metrics route and the numbers behind it."""

    def scrape(self, **extra):
        return self.client.get("/metrics", **extra)

    def test_the_text_format_names_both_metrics(self):
        response = self.scrape()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/plain"))
        text = response.content.decode()
        self.assertIn("# TYPE http_requests_total counter", text)
        self.assertIn("# TYPE http_request_duration_seconds histogram", text)

    def test_requests_are_counted_by_method_route_and_status(self):
        labels = {"method": "GET", "route": "api/health/", "status": "200"}
        before = metric_value(self.scrape().content.decode(), "http_requests_total", **labels)
        self.client.get(HEALTH)
        self.client.get(HEALTH)
        after = metric_value(self.scrape().content.decode(), "http_requests_total", **labels)
        self.assertEqual(after - before, 2)

    def test_durations_are_counted_in_the_histogram(self):
        labels = {"method": "GET", "route": "api/health/"}
        name = "http_request_duration_seconds_count"
        before = metric_value(self.scrape().content.decode(), name, **labels)
        self.client.get(HEALTH)
        after = metric_value(self.scrape().content.decode(), name, **labels)
        self.assertEqual(after - before, 1)

    def test_unknown_urls_share_one_label(self):
        labels = {"method": "GET", "route": "unmatched", "status": "404"}
        before = metric_value(self.scrape().content.decode(), "http_requests_total", **labels)
        self.client.get("/api/nothing-here-aaa/")
        self.client.get("/api/nothing-here-bbb/")
        text = self.scrape().content.decode()
        self.assertEqual(metric_value(text, "http_requests_total", **labels) - before, 2)
        self.assertNotIn("nothing-here", text)

    def test_the_metrics_route_does_not_count_itself(self):
        self.scrape()
        text = self.scrape().content.decode()
        self.assertNotIn('route="metrics"', text)

    def test_other_methods_are_refused(self):
        self.assertEqual(self.client.post("/metrics").status_code, 405)


@override_settings(METRICS_ENABLED=False)
class MetricsDisabledTests(TestCase):
    """Checks that a disabled /metrics route looks like any unknown URL."""

    def test_every_method_gets_the_json_not_found(self):
        for method in ("get", "post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)("/metrics")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["error"]["code"], "not_found")

    @override_settings(METRICS_TOKEN="scrape-token-1")
    def test_disabled_wins_over_the_token_check(self):
        self.assertEqual(self.client.get("/metrics").status_code, 404)


@override_settings(METRICS_ENABLED=True, METRICS_TOKEN="scrape-token-1")
class MetricsTokenTests(TestCase):
    """Checks the optional bearer token on /metrics."""

    def test_no_token_is_a_401(self):
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "not_authenticated")

    def test_a_wrong_token_is_a_401(self):
        response = self.client.get("/metrics", HTTP_AUTHORIZATION="Bearer nope")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "not_authenticated")

    def test_the_right_token_is_accepted(self):
        response = self.client.get("/metrics", HTTP_AUTHORIZATION="Bearer scrape-token-1")
        self.assertEqual(response.status_code, 200)
