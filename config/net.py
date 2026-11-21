"""Network request helpers shared by authentication and throttling."""

import ipaddress
from typing import Any

from django.conf import settings


def client_ip(request: Any) -> str:
    """Return the client address, trusting forwarded headers only when enabled."""
    remote_addr = request.META.get("REMOTE_ADDR", "")
    if not settings.USE_X_FORWARDED_FOR or settings.TRUSTED_PROXY_COUNT < 1:
        return remote_addr

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    addresses = []
    for value in forwarded.split(","):
        candidate = value.strip()
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        addresses.append(candidate)

    trusted_count = settings.TRUSTED_PROXY_COUNT
    if len(addresses) <= trusted_count:
        return remote_addr
    return addresses[-(trusted_count + 1)]
