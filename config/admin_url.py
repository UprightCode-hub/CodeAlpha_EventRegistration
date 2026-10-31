"""Normalize and validate the configured admin path."""

import re

from django.core.exceptions import ImproperlyConfigured


def normalize_admin_url(value):
    """Return a valid single admin path segment with one trailing slash."""
    segment = value.strip().strip("/")
    if (
        not segment
        or segment.lower() == "admin"
        or re.fullmatch(r"[A-Za-z0-9_-]+", segment, flags=re.ASCII) is None
    ):
        raise ImproperlyConfigured(
            "ADMIN_URL must be one valid segment and cannot be admin."
        )
    return segment + "/"
