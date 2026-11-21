"""Tests for trusted reverse-proxy client address extraction."""

from django.test import RequestFactory, SimpleTestCase, override_settings

from config.net import client_ip


class ClientIpTests(SimpleTestCase):
    """Checks that forwarded addresses are opt-in and bounded by proxy count."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_forwarded_header_is_ignored_by_default(self):
        request = self.factory.get("/", REMOTE_ADDR="10.0.0.3", HTTP_X_FORWARDED_FOR="1.1.1.1")
        self.assertEqual(client_ip(request), "10.0.0.3")

    @override_settings(USE_X_FORWARDED_FOR=True, TRUSTED_PROXY_COUNT=1)
    def test_one_trusted_proxy_selects_the_original_client(self):
        request = self.factory.get(
            "/", REMOTE_ADDR="10.0.0.3", HTTP_X_FORWARDED_FOR="1.1.1.1, 10.0.0.2"
        )
        self.assertEqual(client_ip(request), "1.1.1.1")

    @override_settings(USE_X_FORWARDED_FOR=True, TRUSTED_PROXY_COUNT=2)
    def test_insufficient_forwarded_addresses_fall_back_to_remote(self):
        request = self.factory.get("/", REMOTE_ADDR="10.0.0.3", HTTP_X_FORWARDED_FOR="1.1.1.1")
        self.assertEqual(client_ip(request), "10.0.0.3")

    @override_settings(USE_X_FORWARDED_FOR=True, TRUSTED_PROXY_COUNT=1)
    def test_invalid_forwarded_values_are_ignored(self):
        request = self.factory.get(
            "/", REMOTE_ADDR="10.0.0.3", HTTP_X_FORWARDED_FOR="not-an-ip, 1.1.1.1"
        )
        self.assertEqual(client_ip(request), "10.0.0.3")
