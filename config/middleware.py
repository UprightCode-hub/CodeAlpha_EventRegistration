"""Reject request bodies above the configured byte limit."""

from django.conf import settings

from config.exceptions import error_response


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
