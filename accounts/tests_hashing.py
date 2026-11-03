"""Tests for the password hasher setup: new hashes use Argon2 and old PBKDF2 hashes still work."""

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from rest_framework.test import APITestCase

User = get_user_model()
PASSWORD = "Str0ng-Pass-9271!x"


class HashingTests(APITestCase):
    """Uses the real hasher list from the settings, with no override."""

    def register(self, username):
        return self.client.post(
            "/api/auth/register/",
            {
                "username": username,
                "email": f"{username}@example.com",
                "password": PASSWORD,
                "role": "attendee",
            },
            format="json",
        )

    def legacy_user(self, username):
        user = User.objects.create(
            username=username, email=f"{username}@example.com", role="attendee"
        )
        user.password = make_password(PASSWORD, hasher="pbkdf2_sha256")
        user.save()
        return user

    def test_new_account_is_stored_with_argon2(self):
        self.assertEqual(self.register("hash_new").status_code, 201)
        self.assertTrue(User.objects.get(username="hash_new").password.startswith("argon2$"))

    def test_old_pbkdf2_hash_still_logs_in(self):
        user = self.legacy_user("hash_old")
        self.assertTrue(user.password.startswith("pbkdf2_sha256$"))
        response = self.client.post(
            "/api/auth/login/", {"username": "hash_old", "password": PASSWORD}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("access", response.json())

    def test_wrong_password_does_not_upgrade_the_hash(self):
        user = self.legacy_user("hash_wrong")
        response = self.client.post(
            "/api/auth/login/",
            {"username": "hash_wrong", "password": "not-the-password"},
            format="json",
        )
        self.assertEqual(response.status_code, 401)
        user.refresh_from_db()
        self.assertTrue(user.password.startswith("pbkdf2_sha256$"))

    def test_login_upgrades_an_old_hash_to_argon2(self):
        user = self.legacy_user("hash_upgrade")
        self.client.post(
            "/api/auth/login/", {"username": "hash_upgrade", "password": PASSWORD}, format="json"
        )
        user.refresh_from_db()
        self.assertTrue(user.password.startswith("argon2$"))
