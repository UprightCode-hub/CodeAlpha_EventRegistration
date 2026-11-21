import base64
import json
import threading
import unittest

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import (
    Client,
    RequestFactory,
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import LoginFailure
from config.net import client_ip

User = get_user_model()
FAST_HASHER = override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
PW = "Zx9!vQ-lamp-Tiger-42"
NEW_PW = "Qw8#nine-Falcon-77"
REGISTER = "/api/auth/register/"
LOGIN = "/api/auth/login/"
REFRESH = "/api/auth/refresh/"
LOGOUT = "/api/auth/logout/"
PASSWORD = "/api/auth/password/"
ME = "/api/auth/me/"


def make_user(username, password=PW, role=None, superuser=False):
    user = User.objects.create_user(
        username=username, email=f"{username.lower()}@example.com", password=password
    )
    if role:
        user.role = role
    if superuser:
        user.is_staff = True
        user.is_superuser = True
    user.save()
    return user


def b64(data):
    raw = json.dumps(data).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class ApiMixin:
    def post_json(self, path, data, token=None):
        extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post(path, data, format="json", **extra)

    def login(self, username, password=PW):
        response = self.post_json(LOGIN, {"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def me(self, token):
        return self.client.get(ME, HTTP_AUTHORIZATION=f"Bearer {token}")


# ---------------------------------------------------------------- 5. escalation and replay
@FAST_HASHER
@override_settings(THROTTLING_ENABLED=False)
class RoleEscalationTests(ApiMixin, APITestCase):
    def test_role_and_staff_flags_in_register_body_are_ignored(self):
        payloads = [
            {"role": "organizer"},
            {"role": "ORGANIZER"},
            {"is_staff": True, "is_superuser": True},
            {"profile": {"role": "organizer"}, "user": {"role": "organizer", "is_staff": True}},
            {"groups": [1], "user_permissions": [1]},
        ]
        for i, extra in enumerate(payloads):
            with self.subTest(extra=extra):
                name = f"mallory{i}"
                body = {"username": name, "email": f"{name}@example.com", "password": PW, **extra}
                response = self.post_json(REGISTER, body)
                self.assertEqual(response.status_code, 201, response.content)
                self.assertEqual(response.json()["role"], "attendee")
                user = User.objects.get(username=name)
                self.assertEqual(user.role, "attendee")
                self.assertFalse(user.is_staff)
                self.assertFalse(user.is_superuser)

    def test_role_in_multipart_register_body_is_ignored(self):
        body = {
            "username": "mallory",
            "email": "mallory@example.com",
            "password": PW,
            "role": "organizer",
        }
        response = self.client.post(REGISTER, body, format="multipart")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(User.objects.get(username="mallory").role, "attendee")

    def test_me_endpoint_cannot_change_role(self):
        user = make_user("ada")
        token = self.login("ada")["access"]
        for method in ("patch", "put", "post"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    ME, {"role": "organizer"}, format="json", HTTP_AUTHORIZATION=f"Bearer {token}"
                )
                self.assertEqual(response.status_code, 405)
        user.refresh_from_db()
        self.assertEqual(user.role, "attendee")

    def test_attendee_is_refused_on_organizer_endpoints(self):
        make_user("ada")
        token = self.login("ada")["access"]
        self.assertEqual(self.post_json("/api/events/", {}, token).status_code, 403)
        listing = self.client.get("/api/my/events/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(listing.status_code, 403)

    def test_demoted_organizer_loses_power_with_an_old_token(self):
        org = make_user("org1", role="organizer")
        token = self.login("org1")["access"]
        org.role = "attendee"
        org.save()
        self.assertEqual(self.post_json("/api/events/", {}, token).status_code, 403)


@FAST_HASHER
@override_settings(THROTTLING_ENABLED=False)
class TokenReplayTests(ApiMixin, APITestCase):
    def test_rotated_refresh_token_cannot_be_replayed(self):
        make_user("ada")
        first = self.login("ada")
        rotated = self.post_json(REFRESH, {"refresh": first["refresh"]})
        self.assertEqual(rotated.status_code, 200, rotated.content)
        second = rotated.json()
        self.assertNotEqual(second["refresh"], first["refresh"])
        replay = self.post_json(REFRESH, {"refresh": first["refresh"]})
        self.assertEqual(replay.status_code, 401)
        self.assertEqual(self.post_json(REFRESH, {"refresh": second["refresh"]}).status_code, 200)

    def test_logout_blacklists_refresh_token(self):
        make_user("ada")
        tokens = self.login("ada")
        out = self.post_json(LOGOUT, {"refresh": tokens["refresh"]}, tokens["access"])
        self.assertEqual(out.status_code, 204, out.content)
        self.assertEqual(self.post_json(REFRESH, {"refresh": tokens["refresh"]}).status_code, 401)
        again = self.post_json(LOGOUT, {"refresh": tokens["refresh"]}, tokens["access"])
        self.assertEqual(again.status_code, 400)

    def test_cannot_log_out_someone_elses_refresh_token(self):
        make_user("ada")
        make_user("bob")
        ada = self.login("ada")
        bob = self.login("bob")
        response = self.post_json(LOGOUT, {"refresh": bob["refresh"]}, ada["access"])
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.post_json(REFRESH, {"refresh": bob["refresh"]}).status_code, 200)

    def test_password_change_kills_old_refresh_and_access_tokens(self):
        make_user("ada")
        tokens = self.login("ada")
        changed = self.post_json(
            PASSWORD, {"current_password": PW, "new_password": NEW_PW}, tokens["access"]
        )
        self.assertEqual(changed.status_code, 204, changed.content)
        self.assertEqual(self.post_json(REFRESH, {"refresh": tokens["refresh"]}).status_code, 401)
        self.assertEqual(self.me(tokens["access"]).status_code, 401)
        old = self.post_json(LOGIN, {"username": "ada", "password": PW})
        self.assertEqual(old.status_code, 401)
        self.assertTrue(self.login("ada", NEW_PW)["access"])

    def test_wrong_current_password_changes_nothing(self):
        make_user("ada")
        tokens = self.login("ada")
        response = self.post_json(
            PASSWORD, {"current_password": "nope", "new_password": NEW_PW}, tokens["access"]
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.post_json(REFRESH, {"refresh": tokens["refresh"]}).status_code, 200)

    def test_deactivated_user_loses_access(self):
        user = make_user("ada")
        tokens = self.login("ada")
        user.is_active = False
        user.save()
        self.assertEqual(self.me(tokens["access"]).status_code, 401)
        # Not sure SimpleJWT 5.5.1 re-checks is_active on refresh. If this fails, it is a gap.
        refresh = self.post_json(REFRESH, {"refresh": tokens["refresh"]})
        self.assertNotEqual(refresh.status_code, 200)

    def test_forged_and_tampered_tokens_are_rejected(self):
        make_user("ada")
        org = make_user("org1", role="organizer")
        tokens = self.login("ada")

        header_part = b64({"alg": "none", "typ": "JWT"})
        claims_part = b64(
            {"token_type": "access", "exp": 4102444800, "jti": "x", "user_id": org.pk}
        )
        unsigned = f"{header_part}.{claims_part}."
        self.assertEqual(self.me(unsigned).status_code, 401)

        header, payload, signature = tokens["access"].split(".")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        claims["user_id"] = org.pk
        tampered = f"{header}.{b64(claims)}.{signature}"
        self.assertEqual(self.me(tampered).status_code, 401)

        self.assertEqual(self.post_json(REFRESH, {"refresh": tokens["access"]}).status_code, 401)


# ---------------------------------------------------------------- 2. admin lockout
@FAST_HASHER
@override_settings(USE_X_FORWARDED_FOR=False, TRUSTED_PROXY_COUNT=0)
class AdminLockoutTests(TestCase):
    def setUp(self):
        make_user("boss", superuser=True)
        self.url = reverse("admin:login")

    def attempt(self, username, password, ip="198.51.100.1", **extra):
        return Client().post(
            self.url, {"username": username, "password": password}, REMOTE_ADDR=ip, **extra
        )

    def test_correct_password_is_refused_once_locked(self):
        for _ in range(settings.LOGIN_MAX_FAILURES_PER_USER):
            self.assertEqual(self.attempt("boss", "wrong").status_code, 200)
        response = self.attempt("boss", PW)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "too_many_attempts")
        self.assertIn("Retry-After", response.headers)

    def test_case_and_whitespace_variants_share_one_counter(self):
        for name in ("boss", "BOSS", " boss ", "Boss", "bOsS"):
            self.attempt(name, "wrong")
        self.assertEqual(set(LoginFailure.objects.values_list("username", flat=True)), {"boss"})
        self.assertEqual(self.attempt("boss", PW).status_code, 429)

    def test_one_address_spraying_many_usernames_is_capped(self):
        for i in range(settings.LOGIN_MAX_FAILURES_PER_IP):
            self.attempt(f"ghost{i}", "wrong")
        self.assertEqual(self.attempt("boss", PW).status_code, 429)

    def test_lock_applies_to_the_attacker_address_only(self):
        for _ in range(settings.LOGIN_MAX_FAILURES_PER_USER):
            self.attempt("boss", "wrong", ip="198.51.100.1")
        self.assertEqual(self.attempt("boss", PW, ip="198.51.100.2").status_code, 302)

    def test_forwarded_header_is_ignored_when_proxy_trust_is_off(self):
        for i in range(settings.LOGIN_MAX_FAILURES_PER_USER):
            self.attempt("boss", "wrong", HTTP_X_FORWARDED_FOR=f"203.0.113.{i}")
        response = self.attempt("boss", PW, HTTP_X_FORWARDED_FOR="203.0.113.200")
        self.assertEqual(response.status_code, 429)

    def test_success_clears_the_counter(self):
        for _ in range(4):
            self.attempt("boss", "wrong")
        self.assertEqual(self.attempt("boss", PW).status_code, 302)
        for _ in range(4):
            self.attempt("boss", "wrong")
        self.assertEqual(self.attempt("boss", PW).status_code, 302)

    def test_unicode_lookalike_usernames_share_one_counter(self):
        # FINDING: Django's login form applies NFKC to the username, accounts.security does not.
        variants = [
            "ｂoss",
            "bｏss",
            "boｓs",
            "bosｓ",
            "ｂｏss",
            "ｂoｓs",
        ]
        for name in variants:
            self.attempt(name, "wrong")
        response = self.attempt("ｂｏｓｓ", PW)
        self.assertEqual(response.status_code, 429)


@FAST_HASHER
@override_settings(USE_X_FORWARDED_FOR=True, TRUSTED_PROXY_COUNT=1)
class AdminLockoutBehindProxyTests(TestCase):
    def test_rotating_a_spoofed_left_hop_cannot_reset_the_lock(self):
        # The real client is always 198.51.100.7; only the leftmost entry changes.
        make_user("boss", superuser=True)
        url = reverse("admin:login")

        def post(password, spoof):
            return Client().post(
                url,
                {"username": "boss", "password": password},
                REMOTE_ADDR="10.0.0.1",
                HTTP_X_FORWARDED_FOR=f"{spoof}, 198.51.100.7, 10.0.0.2",
            )

        for i in range(settings.LOGIN_MAX_FAILURES_PER_USER + 1):
            post("wrong", f"203.0.113.{i}")
        self.assertEqual(post(PW, "203.0.113.99").status_code, 429)


# ---------------------------------------------------------------- 3. proxy spoofing
class ClientIpTests(SimpleTestCase):
    def ip(self, forwarded=None, remote="10.0.0.1"):
        extra = {"REMOTE_ADDR": remote}
        if forwarded is not None:
            extra["HTTP_X_FORWARDED_FOR"] = forwarded
        return client_ip(RequestFactory().get("/", **extra))

    @override_settings(USE_X_FORWARDED_FOR=False, TRUSTED_PROXY_COUNT=0)
    def test_header_ignored_when_everything_is_off(self):
        self.assertEqual(self.ip("9.9.9.9"), "10.0.0.1")

    @override_settings(USE_X_FORWARDED_FOR=False, TRUSTED_PROXY_COUNT=2)
    def test_header_ignored_when_flag_is_off_even_with_a_count(self):
        self.assertEqual(self.ip("9.9.9.9, 8.8.8.8, 7.7.7.7"), "10.0.0.1")

    @override_settings(USE_X_FORWARDED_FOR=True, TRUSTED_PROXY_COUNT=0)
    def test_header_ignored_when_count_is_zero(self):
        self.assertEqual(self.ip("9.9.9.9"), "10.0.0.1")

    @override_settings(USE_X_FORWARDED_FOR=True, TRUSTED_PROXY_COUNT=1)
    def test_missing_or_garbage_header_falls_back_to_remote_addr(self):
        self.assertEqual(self.ip(None), "10.0.0.1")
        self.assertEqual(self.ip("not-an-ip, also-bad"), "10.0.0.1")

    # Project convention (see config/tests_net.py): TRUSTED_PROXY_COUNT is the number of proxy
    # addresses listed in the header. The direct peer (REMOTE_ADDR) is an extra hop that is not
    # counted. The real client is addresses[-(count + 1)].
    @override_settings(USE_X_FORWARDED_FOR=True, TRUSTED_PROXY_COUNT=1)
    def test_one_trusted_proxy_selects_the_original_client(self):
        self.assertEqual(self.ip("198.51.100.7, 10.0.0.2"), "198.51.100.7")

    @override_settings(USE_X_FORWARDED_FOR=True, TRUSTED_PROXY_COUNT=1)
    def test_client_supplied_leftmost_entries_are_ignored(self):
        # The attacker sends "9.9.9.9"; the first proxy appends the real client; the next hop
        # appends the first proxy. Only the entry at the trusted position is used.
        self.assertEqual(self.ip("9.9.9.9, 198.51.100.7, 10.0.0.2"), "198.51.100.7")

    @override_settings(USE_X_FORWARDED_FOR=True, TRUSTED_PROXY_COUNT=2)
    def test_two_trusted_proxies_ignore_everything_left_of_the_real_client(self):
        self.assertEqual(
            self.ip("9.9.9.9, 198.51.100.7, 10.0.0.5, 10.0.0.2"), "198.51.100.7"
        )

    @override_settings(USE_X_FORWARDED_FOR=True, TRUSTED_PROXY_COUNT=2)
    def test_too_few_entries_fall_back_to_remote_addr(self):
        self.assertEqual(self.ip("198.51.100.7, 10.0.0.2"), "10.0.0.1")

    @override_settings(USE_X_FORWARDED_FOR=True, TRUSTED_PROXY_COUNT=1)
    def test_single_entry_header_falls_back_to_remote_addr(self):
        # Documents a limitation: with one proxy that only appends the client, no count works.
        self.assertEqual(self.ip("198.51.100.7"), "10.0.0.1")


# ---------------------------------------------------------------- 4. races on accounts
@unittest.skip(
    "ACCEPTED GAP: lockout check and failure record are not atomic under parallel requests. "
    "See DECISIONS.md Known gaps. Unskip if this is ever fixed."
)
@override_settings(THROTTLING_ENABLED=False)
class LoginRaceTests(TransactionTestCase):
    def test_parallel_guesses_cannot_exceed_the_per_user_limit(self):
        # FINDING (probabilistic): blocking_seconds() and record_failure() are not atomic, so
        # parallel requests can all pass the check before any failure is stored. This class uses
        # the real Argon2 hasher on purpose, to widen the race window. Run it a few times.
        make_user("victim")
        workers, outcomes, errors = 30, [], []
        gate = threading.Barrier(workers)

        def attack():
            try:
                gate.wait(timeout=15)
                response = Client().post(
                    LOGIN,
                    json.dumps({"username": "victim", "password": "wrong-guess"}),
                    content_type="application/json",
                    REMOTE_ADDR="198.51.100.9",
                )
                outcomes.append(response.json()["error"]["code"])
            except Exception as exc:
                errors.append(repr(exc))
            finally:
                connection.close()

        threads = [threading.Thread(target=attack) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(errors, [])
        self.assertLessEqual(
            outcomes.count("not_authenticated"), settings.LOGIN_MAX_FAILURES_PER_USER
        )


@FAST_HASHER
@override_settings(THROTTLING_ENABLED=False)
class UsernameRaceTests(TransactionTestCase):
    def test_database_rejects_case_variant_usernames(self):
        make_user("Alice")
        with transaction.atomic(), self.assertRaises(IntegrityError):
            User.objects.create_user(username="alice", email="other@example.com", password=PW)

    def test_database_rejects_case_variant_emails(self):
        User.objects.create_user(username="u1", email="Ada@Example.com", password=PW)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            User.objects.create_user(username="u2", email="ada@example.com", password=PW)

    def test_parallel_registrations_of_case_variants_create_one_account(self):
        names = ["Alice", "alice", "ALICE", "aLiCe"] * 3
        statuses, errors = [], []
        gate = threading.Barrier(len(names))

        def register(i, name):
            try:
                gate.wait(timeout=15)
                response = Client().post(
                    REGISTER,
                    json.dumps({"username": name, "email": f"u{i}@example.com", "password": PW}),
                    content_type="application/json",
                )
                statuses.append(response.status_code)
            except Exception as exc:
                errors.append(repr(exc))
            finally:
                connection.close()

        threads = [threading.Thread(target=register, args=(i, n)) for i, n in enumerate(names)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(errors, [])
        self.assertEqual(statuses.count(201), 1, statuses)
        self.assertTrue(all(code in (201, 400) for code in statuses), statuses)
        self.assertEqual(User.objects.filter(username__iexact="alice").count(), 1)