import threading
from datetime import timedelta

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.models import F
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from registrations.admin import RegistrationAdmin
from registrations.models import Notification, Registration
from registrations.services import (
    RegistrationRuleError,
    apply_event_edit,
    cancel_registration,
    register_attendee,
)

User = get_user_model()
FAST_HASHER = override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
PW = "Zx9!vQ-lamp-Tiger-42"
PHONE = "+2348012345678"
R = Registration.Status


def make_user(username, role=None, superuser=False):
    user = User.objects.create_user(
        username=username, email=f"{username.lower()}@example.com", password=PW
    )
    if role:
        user.role = role
    if superuser:
        user.is_staff = True
        user.is_superuser = True
    user.save()
    return user


def make_event(organizer, capacity=1, **overrides):
    start = timezone.now() + timedelta(days=10)
    data = {
        "organizer": organizer,
        "title": "Race Night",
        "description": "Test event",
        "category": "meetup",
        "event_type": "online",
        "online_link": "https://example.com/live",
        "start_at": start,
        "end_at": start + timedelta(hours=2),
        "registration_deadline": start,
        "capacity": capacity,
        "contact_email": "org@example.com",
        "contact_phone": PHONE,
    }
    data.update(overrides)
    return Event.objects.create(**data)


def join(user, event):
    return register_attendee(user, event.pk, "Test Person", PHONE)


def run_parallel(jobs):
    gate = threading.Barrier(len(jobs))
    results = [None] * len(jobs)

    def runner(index, job):
        try:
            gate.wait(timeout=20)
            results[index] = ("ok", job())
        except Exception as exc:
            results[index] = ("err", exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=runner, args=(i, j)) for i, j in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


# ---------------------------------------------------------------- 1. admin bypass
@FAST_HASHER
class AdminSeatBypassTests(TestCase):
    def setUp(self):
        self.boss = make_user("boss", superuser=True)
        org = make_user("org1", role="organizer")
        self.event = make_event(org, capacity=1)
        self.first = join(make_user("a1"), self.event)
        self.second = join(make_user("a2"), self.event)
        self.client = Client(raise_request_exception=False)
        self.client.force_login(self.boss)

    def test_bulk_delete_action_does_nothing(self):
        response = self.client.post(
            reverse("admin:registrations_registration_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [self.first.pk, self.second.pk],
                "post": "yes",
            },
        )
        self.assertLess(response.status_code, 500)
        self.assertEqual(Registration.objects.count(), 2)

    def test_single_delete_is_forbidden(self):
        url = reverse("admin:registrations_registration_delete", args=[self.first.pk])
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
        self.assertEqual(Registration.objects.count(), 2)

    def test_status_cannot_be_changed_through_the_change_form(self):
        url = reverse("admin:registrations_registration_change", args=[self.first.pk])
        self.client.post(
            url,
            {"full_name": "Edited Name", "phone": PHONE, "note": "", "status": R.CANCELLED},
        )
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, R.REGISTERED)

    def test_admin_cannot_add_a_registration_that_ignores_capacity(self):
        # FINDING: has_add_permission is not overridden.
        request = RequestFactory().get("/")
        request.user = self.boss
        admin_class = RegistrationAdmin(Registration, django_admin.site)
        self.assertFalse(admin_class.has_add_permission(request))
        response = self.client.post(
            reverse("admin:registrations_registration_add"),
            {"full_name": "Ghost Person", "phone": PHONE, "note": ""},
        )
        self.assertLess(response.status_code, 500)
        self.assertEqual(Registration.objects.count(), 2)


# ---------------------------------------------------------------- 4. ORM-level constraints
@FAST_HASHER
class EventConstraintTests(TestCase):
    def setUp(self):
        self.org = make_user("org1", role="organizer")
        self.start = timezone.now() + timedelta(days=10)

    def refuse(self, **overrides):
        with transaction.atomic(), self.assertRaises(IntegrityError):
            make_event(self.org, **overrides)

    def test_end_must_be_after_start(self):
        for end in (self.start, self.start - timedelta(hours=1)):
            with self.subTest(end=end):
                self.refuse(start_at=self.start, end_at=end, registration_deadline=self.start)

    def test_deadline_cannot_be_after_start(self):
        self.refuse(
            start_at=self.start,
            end_at=self.start + timedelta(hours=1),
            registration_deadline=self.start + timedelta(minutes=1),
        )

    def test_capacity_zero_or_negative_is_refused(self):
        for capacity in (0, -1, -100000):
            with self.subTest(capacity=capacity):
                self.refuse(capacity=capacity)

    def test_queryset_update_cannot_bypass_the_checks(self):
        event = make_event(self.org, capacity=5)
        updates = (
            {"capacity": 0},
            {"end_at": F("start_at")},
            {"registration_deadline": F("start_at") + timedelta(minutes=1)},
        )
        for fields in updates:
            with (
                self.subTest(fields=fields),
                transaction.atomic(),
                self.assertRaises(IntegrityError),
            ):
                Event.objects.filter(pk=event.pk).update(**fields)

    def test_capacity_has_a_database_upper_bound(self):
        # FINDING: 100000 is only a validator, so a direct ORM write of 100001 succeeds.
        self.refuse(capacity=100001)

    def test_only_one_open_registration_per_attendee_and_event(self):
        event = make_event(self.org, capacity=5)
        user = make_user("a1")
        fields = {
            "attendee": user,
            "event": event,
            "full_name": "Test Person",
            "phone": PHONE,
            "note": "",
        }
        Registration.objects.create(status=R.REGISTERED, **fields)
        for status in (R.REGISTERED, R.WAITLISTED):
            with (
                self.subTest(status=status),
                transaction.atomic(),
                self.assertRaises(IntegrityError),
            ):
                Registration.objects.create(status=status, **fields)
        Registration.objects.update(status=R.CANCELLED)
        Registration.objects.create(status=R.WAITLISTED, **fields)  # allowed after cancelling
        self.assertEqual(Registration.objects.count(), 2)

    def test_registration_status_must_be_one_of_the_four_values(self):
        event = make_event(self.org, capacity=5)
        user = make_user("a1")
        fields = {
            "attendee": user,
            "event": event,
            "full_name": "Test Person",
            "phone": PHONE,
            "note": "",
        }
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Registration.objects.create(status="vip", **fields)
        reg = Registration.objects.create(status=R.REGISTERED, **fields)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Registration.objects.filter(pk=reg.pk).update(status="vip")


# ---------------------------------------------------------------- 4. seat races
@FAST_HASHER
class SeatRaceTests(TransactionTestCase):
    def setUp(self):
        self.org = make_user("org1", role="organizer")

    def counts(self, event):
        return (
            event.registrations.filter(status=R.REGISTERED).count(),
            event.registrations.filter(status=R.WAITLISTED).count(),
        )

    def assert_notifications_consistent(self, event):
        for registration in event.registrations.filter(status=R.REGISTERED):
            kinds = set(
                Notification.objects.filter(registration=registration).values_list(
                    "kind", flat=True
                )
            )
            direct = Notification.Kind.REGISTRATION_CONFIRMED in kinds
            promoted = Notification.Kind.PROMOTED in kinds
            self.assertEqual(int(direct) + int(promoted), 1, kinds)

    def test_last_seat_goes_to_exactly_one_person(self):
        event = make_event(self.org, capacity=1)
        users = [make_user(f"u{i}") for i in range(12)]
        results = run_parallel([lambda u=u: join(u, event) for u in users])
        self.assertEqual([r for r in results if r[0] == "err"], [])
        self.assertEqual(self.counts(event), (1, 11))
        self.assertEqual(Notification.objects.filter(event=event).count(), 12)
        self.assert_notifications_consistent(event)

    def test_double_submit_by_one_attendee_creates_one_open_registration(self):
        event = make_event(self.org, capacity=5)
        user = make_user("a1")
        results = run_parallel([lambda: join(user, event) for _ in range(8)])
        self.assertEqual(sum(1 for r in results if r[0] == "ok"), 1)
        for kind, value in results:
            if kind == "err":
                self.assertIsInstance(value, RegistrationRuleError)
                self.assertEqual(value.code, "already_registered")
        self.assertEqual(event.registrations.count(), 1)

    def test_cancellations_and_new_signups_never_jump_the_waitlist(self):
        event = make_event(self.org, capacity=2)
        users = [make_user(f"u{i}") for i in range(8)]
        regs = [join(u, event) for u in users[:4]]  # u0, u1 registered; u2, u3 waitlisted
        jobs = [
            lambda: cancel_registration(regs[0].pk, users[0].pk),
            lambda: cancel_registration(regs[1].pk, users[1].pk),
        ] + [lambda u=u: join(u, event) for u in users[4:]]
        results = run_parallel(jobs)
        self.assertEqual([r for r in results if r[0] == "err"], [])
        self.assertEqual(self.counts(event)[0], 2)
        status = {r.attendee_id: r.status for r in event.registrations.all()}
        self.assertEqual(status[users[2].pk], R.REGISTERED)
        self.assertEqual(status[users[3].pk], R.REGISTERED)
        for user in users[4:]:
            self.assertEqual(status[user.pk], R.WAITLISTED)
        self.assert_notifications_consistent(event)

    def test_double_cancel_promotes_exactly_one_person(self):
        event = make_event(self.org, capacity=1)
        users = [make_user(f"u{i}") for i in range(3)]
        regs = [join(u, event) for u in users]
        results = run_parallel(
            [lambda: cancel_registration(regs[0].pk, users[0].pk) for _ in range(6)]
        )
        self.assertEqual(sum(1 for r in results if r[0] == "ok"), 1)
        for kind, value in results:
            if kind == "err":
                self.assertEqual(getattr(value, "code", None), "already_cancelled")
        self.assertEqual(self.counts(event), (1, 1))
        promoted = Notification.objects.filter(event=event, kind=Notification.Kind.PROMOTED)
        self.assertEqual(promoted.count(), 1)

    def test_capacity_raise_racing_with_cancel_and_signups_fills_exactly_to_capacity(self):
        event = make_event(self.org, capacity=1)
        users = [make_user(f"u{i}") for i in range(8)]
        regs = [join(u, event) for u in users[:5]]  # u0 registered, u1..u4 waitlisted
        jobs = [
            lambda: apply_event_edit(event.pk, lambda current: {"capacity": 3}),
            lambda: cancel_registration(regs[0].pk, users[0].pk),
        ] + [lambda u=u: join(u, event) for u in users[5:]]
        results = run_parallel(jobs)
        self.assertEqual([r for r in results if r[0] == "err"], [])
        registered, waitlisted = self.counts(event)
        self.assertEqual(registered, 3)
        self.assertEqual(registered + waitlisted, 7)  # 8 people minus the one who cancelled
        self.assert_notifications_consistent(event)
