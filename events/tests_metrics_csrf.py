"""Tests that the metrics route answers the same way when the CSRF check is on."""

from django.test import Client, TestCase, override_settings


@override_settings(METRICS_ENABLED=False)
class MetricsDisabledCsrfTests(TestCase):
    """Checks a disabled metrics route with a client that enforces the CSRF check."""

    def test_every_unsafe_method_gets_the_json_not_found(self):
        client = Client(enforce_csrf_checks=True)
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(client, method)("/metrics")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["error"]["code"], "not_found")


@override_settings(METRICS_ENABLED=True, METRICS_TOKEN="")
class MetricsEnabledCsrfTests(TestCase):
    """Checks an enabled metrics route with a client that enforces the CSRF check."""

    def test_post_is_refused_with_the_json_method_error(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post("/metrics")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()["error"]["code"], "method_not_allowed")
