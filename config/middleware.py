"""Provide request metadata and reject oversized request bodies."""

import logging
import re
import time
import uuid

import structlog
from django.conf import settings

from config.exceptions import error_response

REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
access_logger = logging.getLogger("api.access")


class RequestIdMiddleware:
    """Bind a request id, add it to responses, and write access records."""

    def __init__(self, get_response):
        """Save the next middleware or view in the request chain."""
        self.get_response = get_response

    def __call__(self, request):
        """Process one request with a bound id and one access record."""
        request_id = request.headers.get("X-Request-ID", "")
        if REQUEST_ID_PATTERN.fullmatch(request_id) is None:
            request_id = uuid.uuid4().hex
        started_at = time.perf_counter()
        status_code = 500
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = self.get_response(request)
            status_code = response.status_code
            response["X-Request-ID"] = request_id
            return response
        finally:
            user = getattr(request, "user", None)
            user_id = (
                getattr(user, "pk", None) if getattr(user, "is_authenticated", False) else None
            )
            access_logger.info(
                "request finished",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.path,
                    "status": status_code,
                    "duration_ms": (time.perf_counter() - started_at) * 1000,
                    "user_id": user_id,
                },
            )
            structlog.contextvars.clear_contextvars()


class BodySizeLimitMiddleware:
    """Rejects oversized request bodies before authentication or parsing."""

    def __init__(self, get_response):
        """Save the next middleware or view in the request chain."""
        self.get_response = get_response

    def __call__(self, request):
        """Check the declared body size before passing the request onward."""
        # Django does not limit the JSON request bodies parsed by the API.
        content_length = request.META.get("CONTENT_LENGTH", "")
        try:
            body_size = int(content_length)
        except (TypeError, ValueError):
            body_size = 0
        if body_size > settings.MAX_REQUEST_BODY_BYTES:
            return error_response("payload_too_large")
        return self.get_response(request)
