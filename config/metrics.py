"""Expose Prometheus metrics and record completed requests."""

import time

from django.conf import settings
from django.http import HttpResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from config.exceptions import error_response

REQUEST_COUNTER = Counter(
    "http_requests_total",
    "Number of HTTP requests handled.",
    ("method", "route", "status"),
)
REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Time spent handling HTTP requests.",
    ("method", "route"),
)


def route_label(request):
    """Return the bounded route label for a request."""
    resolver_match = getattr(request, "resolver_match", None)
    route = getattr(resolver_match, "route", None)
    if not route or route == "^.*$":
        return "unmatched"
    return route.lstrip("/")


def metrics_view(request):
    """Return Prometheus metrics when collection is enabled and authorized."""
    if not settings.METRICS_ENABLED:
        return error_response("not_found")
    token = settings.METRICS_TOKEN
    if token and request.headers.get("Authorization") != f"Bearer {token}":
        return error_response("not_authenticated")
    if request.method != "GET":
        return error_response("method_not_allowed")
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)


class MetricsMiddleware:
    """Record request counters and durations after each response."""

    def __init__(self, get_response):
        """Save the next middleware or view in the request chain."""
        self.get_response = get_response

    def __call__(self, request):
        """Measure the request and record its final status."""
        started_at = time.perf_counter()
        status_code = 500
        try:
            response = self.get_response(request)
            status_code = response.status_code
            return response
        finally:
            if request.path != "/metrics":
                route = route_label(request)
                REQUEST_COUNTER.labels(request.method, route, str(status_code)).inc()
                REQUEST_DURATION.labels(request.method, route).observe(
                    time.perf_counter() - started_at
                )
