"""Apply scoped rate limits using Django's default cache."""

import time

from django.conf import settings
from django.core.cache import cache
from rest_framework.throttling import BaseThrottle


class ScopedRateThrottle(BaseThrottle):
    """Throttle requests using the scope configured on each API view."""

    cache = cache
    timer = time.time

    def allow_request(self, request, view):
        """Allow the request when its active scope remains below its rate."""
        if not settings.THROTTLING_ENABLED:
            return True
        self.scope = self.get_scope(request, view)
        if self.scope is None:
            return True
        self.rate = self.get_rate(request)
        if self.rate is None:
            return True
        self.num_requests, self.duration = self.parse_rate(self.rate)
        self.key = self.get_cache_key(request, view)
        self.history = self.cache.get(self.key, [])
        self.now = self.timer()
        while self.history and self.history[-1] <= self.now - self.duration:
            self.history.pop()
        if len(self.history) >= self.num_requests:
            return False
        self.history.insert(0, self.now)
        self.cache.set(self.key, self.history, self.duration)
        return True

    def get_scope(self, request, view):
        """Return the scope assigned to the request method on its view."""
        scope = getattr(view, "throttle_scope", None)
        if isinstance(scope, dict):
            return scope.get(request.method)
        return scope

    def get_rate(self, request):
        """Return the configured user or anonymous rate for the active scope."""
        identity_type = "user" if request.user.is_authenticated else "anon"
        return settings.THROTTLE_RATES.get(self.scope, {}).get(identity_type)

    def parse_rate(self, rate: str) -> tuple[int, int]:
        """Return the number of requests and the period in seconds for a rate like 5/min."""
        number, period = rate.split("/")
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[period[0]]
        return int(number), seconds

    def get_cache_key(self, request, view):
        """Return a cache key scoped to the request identity and operation."""
        if request.user.is_authenticated:
            identity = f"user:{request.user.pk}"
        else:
            identity = f"anon:{request.META.get('REMOTE_ADDR', '')}"
        return f"throttle:{self.scope}:{identity}"

    def wait(self):
        """Return the number of seconds until the next request is allowed."""
        if not self.history:
            return None
        return self.duration - (self.now - self.history[-1])
