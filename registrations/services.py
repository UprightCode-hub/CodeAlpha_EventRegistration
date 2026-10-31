from datetime import timezone as datetime_timezone

from django.db import IntegrityError, transaction
from django.utils import timezone

from config.exceptions import STATE_ERRORS
from events.models import Event
from events.serializers import CancelEventSerializer, EventWriteSerializer
from registrations.models import Notification, Registration


class RegistrationRuleError(Exception):
    """Carries a fixed registration state error code."""

    def __init__(self, code):
        self.code = code
        super().__init__(STATE_ERRORS[code])


def notification_message(kind, title, reason=None):
    """Builds a notification message from its event title."""
    templates = {
        "registration_confirmed": "You are registered for {title}.",
        "waitlisted": "You are on the waitlist for {title}.",
        "promoted": "A seat opened up and you are now registered for {title}.",
        "event_cancelled": "{title} has been cancelled. Reason: {reason}",
        "event_updated": "{title} has been updated. Please check the new details.",
    }
    return templates[kind].format(title=title, reason=reason or "")[:255]


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


def cancel_event(event_id, request):
    """Cancels an event and closes its open registrations atomically."""
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=event_id)
        if event.status == Event.Status.CANCELLED:
            raise RegistrationRuleError("event_cancelled")
        if event.start_at <= timezone.now():
            raise RegistrationRuleError("event_started")
        serializer = CancelEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["reason"]
        event.status = Event.Status.CANCELLED
        event.cancel_reason = reason
        event.save(update_fields=("status", "cancel_reason", "updated_at"))
        open_registrations = event.registrations.filter(
            status__in=(Registration.Status.REGISTERED, Registration.Status.WAITLISTED)
        ).select_related("attendee")
        for registration in open_registrations:
            registration.status = Registration.Status.EVENT_CANCELLED
            registration.save(update_fields=("status", "updated_at"))
            Notification.objects.create(
                recipient=registration.attendee,
                event=event,
                registration=registration,
                kind=Notification.Kind.EVENT_CANCELLED,
                message=notification_message("event_cancelled", event.title, reason),
            )
        return event


def apply_event_edit(event_id, request):
    """Validates and applies an event edit under the event seat lock."""
    changed_fields = (
        "start_at",
        "end_at",
        "event_type",
        "venue_name",
        "address",
        "city",
        "online_link",
    )
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=event_id)
        if event.status == Event.Status.CANCELLED:
            raise RegistrationRuleError("event_cancelled")
        if event.start_at <= timezone.now():
            raise RegistrationRuleError("event_started")

        serializer = EventWriteSerializer(event, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        new_capacity = values.get("capacity", event.capacity)
        registered_count = event.registrations.filter(
            status=Registration.Status.REGISTERED
        ).count()
        if new_capacity < registered_count:
            raise RegistrationRuleError("capacity_too_low")

        def same_value(field_name, new_value):
            old_value = getattr(event, field_name)
            if field_name in ("start_at", "end_at"):
                return old_value.astimezone(datetime_timezone.utc) == new_value.astimezone(
                    datetime_timezone.utc
                )
            return old_value == new_value

        changed_location_or_time = any(
            field_name in values and not same_value(field_name, values[field_name])
            for field_name in changed_fields
        )
        old_capacity = event.capacity
        updated_event = serializer.save()

        if new_capacity > old_capacity:
            promote_waitlisted_registrations(event.pk)
        if changed_location_or_time:
            open_registrations = updated_event.registrations.filter(
                status__in=(Registration.Status.REGISTERED, Registration.Status.WAITLISTED)
            ).select_related("attendee")
            for registration in open_registrations:
                Notification.objects.create(
                    recipient=registration.attendee,
                    event=updated_event,
                    registration=registration,
                    kind=Notification.Kind.EVENT_UPDATED,
                    message=notification_message("event_updated", updated_event.title),
                )
        return updated_event
