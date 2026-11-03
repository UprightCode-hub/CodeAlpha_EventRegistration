"""Track failed sign-ins and enforce temporary lockouts."""

import logging
import math
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from accounts.models import LoginFailure

logger = logging.getLogger("accounts.security")


def normalize_username(username):
    """Strip and lowercase a username and keep its first 150 characters."""
    return username.strip().lower()[:150]


def blocking_seconds(username, address):
    """Return the remaining lockout time when either threshold is reached."""
    normalized = normalize_username(username)
    now = timezone.now()
    cutoff = now - timedelta(seconds=settings.LOGIN_LOCK_WINDOW_SECONDS)
    user_failures = (
        LoginFailure.objects.filter(username=normalized, ip_address=address, created_at__gte=cutoff)
        .order_by("created_at", "pk")
        .values_list("created_at", flat=True)
    )
    address_failures = (
        LoginFailure.objects.filter(ip_address=address, created_at__gte=cutoff)
        .order_by("created_at", "pk")
        .values_list("created_at", flat=True)
    )

    def retry_after(failures, limit):
        """Find when enough old failures expire to fall below a limit."""
        values = list(failures)
        if len(values) < limit:
            return None
        expires_at = values[len(values) - limit] + timedelta(
            seconds=settings.LOGIN_LOCK_WINDOW_SECONDS
        )
        seconds = math.ceil((expires_at - now).total_seconds())
        return max(1, min(settings.LOGIN_LOCK_WINDOW_SECONDS, seconds))

    waits = [
        wait
        for wait in (
            retry_after(user_failures, settings.LOGIN_MAX_FAILURES_PER_USER),
            retry_after(address_failures, settings.LOGIN_MAX_FAILURES_PER_IP),
        )
        if wait is not None
    ]
    return max(waits) if waits else None


def record_failure(username, address):
    """Delete expired rows, store one failure, and log it without credentials."""
    normalized = normalize_username(username)
    now = timezone.now()
    cutoff = now - timedelta(seconds=settings.LOGIN_LOCK_WINDOW_SECONDS)
    LoginFailure.objects.filter(created_at__lt=cutoff).delete()
    LoginFailure.objects.create(username=normalized, ip_address=address)
    logger.warning("login failed username=%r address=%s", normalized, address)


def clear_failures(username, address):
    """Clear recent failures for one normalized username and address."""
    LoginFailure.objects.filter(username=normalize_username(username), ip_address=address).delete()


def log_blocked(username, address):
    """Log a blocked sign-in without recording another failure."""
    logger.warning("login blocked username=%r address=%s", normalize_username(username), address)
