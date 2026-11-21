import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings as project_settings
from django.test import SimpleTestCase

BASE_DIR = Path(project_settings.BASE_DIR)
GOOD_KEY = "k8Qz-Vn3pLw9XeRt7YuAo2BcDf5GhJm1-extra"


def load_settings(**env):
    """Import config.settings in a clean child process and return the result."""
    child = dict(os.environ)
    child.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "SECRET_KEY": GOOD_KEY,
            "DEBUG": "False",
            "TRUSTED_PROXY_COUNT": "0",
            "USE_X_FORWARDED_FOR": "False",
            "METRICS_ENABLED": "False",
            "METRICS_TOKEN": "",
            "ADMIN_URL": "eventreg-manage/",
        }
    )
    child.update(env)
    return subprocess.run(
        [sys.executable, "-c", "import config.settings"],
        cwd=BASE_DIR,
        env=child,
        capture_output=True,
        text=True,
        timeout=90,
    )


class StartupValidationTests(SimpleTestCase):
    def assert_refused(self, result, needle=""):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(needle, result.stderr)

    def test_baseline_good_config_starts(self):
        result = load_settings()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_weak_secret_keys_are_refused_when_debug_is_off(self):
        weak = [
            "",
            "short",
            "a" * 31,
            "django-insecure-" + "x" * 40,
            "Django-Insecure-" + "x" * 40,
            "please-change-me-" + "x" * 40,
            "PLEASE-CHANGE-ME-" + "x" * 40,
        ]
        for key in weak:
            with self.subTest(key=key[:20]):
                self.assert_refused(load_settings(SECRET_KEY=key), "SECRET_KEY")

    def test_only_the_exact_value_true_enables_debug(self):
        for value in ("false", "", "1", "true", "TRUE", "yes", "False"):
            with self.subTest(debug=value):
                self.assert_refused(load_settings(DEBUG=value, SECRET_KEY="weak"), "SECRET_KEY")
        self.assertEqual(load_settings(DEBUG="True", SECRET_KEY="weak").returncode, 0)

    def test_bad_trusted_proxy_count_is_refused(self):
        for value in ("-1", "abc", "1.5", ""):
            with self.subTest(value=value):
                self.assert_refused(load_settings(TRUSTED_PROXY_COUNT=value), "TRUSTED_PROXY_COUNT")
        self.assertEqual(load_settings(TRUSTED_PROXY_COUNT="2").returncode, 0)

    def test_metrics_cannot_be_enabled_without_a_token(self):
        for token in ("", "   "):
            with self.subTest(token=repr(token)):
                result = load_settings(METRICS_ENABLED="True", METRICS_TOKEN=token)
                self.assert_refused(result, "METRICS_TOKEN")
        self.assertEqual(load_settings(METRICS_ENABLED="True", METRICS_TOKEN="s3cret").returncode, 0)

    def test_admin_path_cannot_be_the_default_scanned_path(self):
        for value in ("admin", "ADMIN", "Admin", "", "a/b", "bad path", "x?y"):
            with self.subTest(value=value):
                self.assertNotEqual(load_settings(ADMIN_URL=value).returncode, 0)
