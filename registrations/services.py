from django.db import IntegrityError, transaction
from django.utils import timezone

from config.exceptions import STATE_ERRORS
from events.models import Event
from registrations.models import Notification, Registration


class RegistrationRuleError(Exception):
    """Carries a fixed registration state error code."""

    def __init__(self, code):
        self.code = code
        super().__init__(STATE_ERRORS[code])


def notification_message(kind, title):
    """Builds a notification message from its event title."""
    templates = {
        "registration_confirmed": "You are registered for {title}.",
        "waitlisted": "You are on the waitlist for {title}.",
        "promoted": "A seat opened up and you are now registered for {title}.",
    }
    return templates[kind].format(title=title)[:255]


def _promote_waitlist_locked(event):
    while event.registrations.filter(status=Registration.Status.REGISTERED).count() < event.capacity:
        waiting = event.registrations.filter(
            status=Registration.Status.WAITLISTED
        ).order_by("created_at", "id").first()
        if waiting is None:
            break
        waiting.status = Registration.Status.REGISTERED
        waiting.save(update_fields=("status", "updated_at"))
        Notification.objects.create(
            recipient=waiting.attendee,
            event=event,
            registration=waiting,
            kind=Notification.Kind.PROMOTED,
            message=notification_message("promoted", event.title),
        )


def promote_waitlisted_registrations(event_id):
    """Promotes the oldest waitlisted people while seats remain."""
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=event_id)
        _promote_waitlist_locked(event)


def register_attendee(attendee, event_id, full_name, phone, note=""):
    """Creates a registration and its confirmation within one event lock."""
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=event_id)
        now = timezone.now()
        if event.status == Event.Status.CANCELLED:
            raise RegistrationRuleError("event_cancelled")
        if event.start_at <= now:
            raise RegistrationRuleError("event_started")
        if now > event.registration_deadline:
            raise RegistrationRuleError("registration_closed")
        if event.registrations.filter(
            attendee=attendee,
            status__in=(Registration.Status.REGISTERED, Registration.Status.WAITLISTED),
        ).exists():
            raise RegistrationRuleError("already_registered")
        registered_count = event.registrations.filter(
            status=Registration.Status.REGISTERED
        ).count()
        status_value = (
            Registration.Status.REGISTERED
            if registered_count < event.capacity
            else Registration.Status.WAITLISTED
        )
        try:
            with transaction.atomic():
                registration = Registration.objects.create(
                    attendee=attendee,
                    event=event,
                    status=status_value,
                    full_name=full_name,
                    phone=phone,
                    note=note,
                )
        except IntegrityError as exc:
            raise RegistrationRuleError("already_registered") from exc
        kind = (
            Notification.Kind.REGISTRATION_CONFIRMED
            if status_value == Registration.Status.REGISTERED
            else Notification.Kind.WAITLISTED
        )
        Notification.objects.create(
            recipient=attendee,
            event=event,
            registration=registration,
            kind=kind,
            message=notification_message(kind, event.title),
        )
        return registration


def cancel_registration(registration_id, attendee_id):
    """Cancels an attendee registration and fills any freed seat."""
    initial = Registration.objects.get(pk=registration_id, attendee_id=attendee_id)
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=initial.event_id)
        registration = Registration.objects.get(pk=registration_id, attendee_id=attendee_id)
        if registration.status in (
            Registration.Status.CANCELLED,
            Registration.Status.EVENT_CANCELLED,
        ):
            raise RegistrationRuleError("already_cancelled")
        if event.start_at <= timezone.now():
            raise RegistrationRuleError("event_started")
        held_seat = registration.status == Registration.Status.REGISTERED
        registration.status = Registration.Status.CANCELLED
        registration.save(update_fields=("status", "updated_at"))
        if held_seat:
            _promote_waitlist_locked(event)
        return registration
