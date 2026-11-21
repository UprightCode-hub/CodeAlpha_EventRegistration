"""Tests for environment-specific Django security settings."""

import json
import os
import subprocess
import sys

from django.test import SimpleTestCase


class ProductionSecuritySettingsTests(SimpleTestCase):
    """Checks production settings without changing the active test configuration."""

    def settings_from_environment(self, **overrides):
        environment = os.environ | overrides
        command = """
import json
from config import settings

names = (
    'SECURE_SSL_REDIRECT',
    'SESSION_COOKIE_SECURE',
    'CSRF_COOKIE_SECURE',
    'SECURE_HSTS_SECONDS',
    'SECURE_HSTS_INCLUDE_SUBDOMAINS',
    'SECURE_HSTS_PRELOAD',
    'SECURE_PROXY_SSL_HEADER',
    'CSRF_TRUSTED_ORIGINS',
    'USE_X_FORWARDED_FOR',
    'TRUSTED_PROXY_COUNT',
)
print(json.dumps({name: getattr(settings, name, None) for name in names}))
"""
        result = subprocess.run(
            [sys.executable, "-c", command],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_forwarded_for_is_disabled_by_default(self):
        values = self.settings_from_environment()
        self.assertFalse(values["USE_X_FORWARDED_FOR"])
        self.assertEqual(values["TRUSTED_PROXY_COUNT"], 0)

    def test_production_defaults_enable_transport_and_cookie_protection(self):
        values = self.settings_from_environment(DEBUG="False")
        self.assertTrue(values["SECURE_SSL_REDIRECT"])
        self.assertTrue(values["SESSION_COOKIE_SECURE"])
        self.assertTrue(values["CSRF_COOKIE_SECURE"])
        self.assertEqual(values["SECURE_HSTS_SECONDS"], 31536000)
        self.assertTrue(values["SECURE_HSTS_INCLUDE_SUBDOMAINS"])
        self.assertTrue(values["SECURE_HSTS_PRELOAD"])
        self.assertEqual(values["SECURE_PROXY_SSL_HEADER"], ["HTTP_X_FORWARDED_PROTO", "https"])
        self.assertEqual(values["CSRF_TRUSTED_ORIGINS"], [])

    def test_production_security_settings_can_be_overridden_from_the_environment(self):
        values = self.settings_from_environment(
            DEBUG="False",
            SECURE_SSL_REDIRECT="False",
            SESSION_COOKIE_SECURE="False",
            CSRF_COOKIE_SECURE="False",
            SECURE_HSTS_SECONDS="3600",
            SECURE_HSTS_INCLUDE_SUBDOMAINS="False",
            SECURE_HSTS_PRELOAD="False",
            SECURE_PROXY_SSL_HEADER_ENABLED="False",
            CSRF_TRUSTED_ORIGINS="https://app.example.com, https://admin.example.com ",
        )
        self.assertFalse(values["SECURE_SSL_REDIRECT"])
        self.assertFalse(values["SESSION_COOKIE_SECURE"])
        self.assertFalse(values["CSRF_COOKIE_SECURE"])
        self.assertEqual(values["SECURE_HSTS_SECONDS"], 3600)
        self.assertFalse(values["SECURE_HSTS_INCLUDE_SUBDOMAINS"])
        self.assertFalse(values["SECURE_HSTS_PRELOAD"])
        self.assertIsNone(values["SECURE_PROXY_SSL_HEADER"])
        self.assertEqual(
            values["CSRF_TRUSTED_ORIGINS"],
            ["https://app.example.com", "https://admin.example.com"],
        )

    def test_debug_mode_does_not_enable_production_security_settings(self):
        values = self.settings_from_environment(DEBUG="True")
        self.assertIsNone(values["SECURE_SSL_REDIRECT"])

    def test_production_rejects_a_weak_secret_key(self):
        environment = os.environ | {"DEBUG": "False", "SECRET_KEY": "change-me"}
        result = subprocess.run(
            [sys.executable, "-c", "from config import settings"],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SECRET_KEY", result.stderr)
