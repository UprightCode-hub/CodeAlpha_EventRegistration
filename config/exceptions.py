import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from django.http import JsonResponse
from rest_framework import exceptions as drf_exceptions, status
from rest_framework.views import exception_handler

logger = logging.getLogger("django.request")

ERRORS = {
    "validation_error": (400, "The submitted data is not valid."),
    "not_authenticated": (401, "Authentication is required or the token is invalid."),
    "permission_denied": (403, "You do not have permission to do this."),
    "not_found": (404, "The requested resource was not found."),
    "method_not_allowed": (405, "This method is not allowed for this endpoint."),
    "server_error": (500, "Something went wrong on the server."),
}

STATE_ERRORS = {
    "event_started": "This event has already started.",
    "event_cancelled": "This event has been cancelled.",
    "registration_closed": "Registration for this event is closed.",
    "already_registered": "You already have an open registration for this event.",
    "already_cancelled": "This registration is already cancelled.",
    "capacity_too_low": "Capacity cannot be lower than the number of registered attendees.",
}


def error_response(code, fields=None):
    """Builds a response using the public API error format."""
    http_status, message = ERRORS[code]
    error = {"code": code, "message": message}
    if code == "validation_error":
        error["fields"] = fields or {}
    return JsonResponse({"error": error}, status=http_status)


def state_error_response(code):
    """Builds a response for a fixed event or registration state error."""
    return JsonResponse(
        {"error": {"code": code, "message": STATE_ERRORS[code]}},
        status=400,
    )


def _field_messages(detail):
    if isinstance(detail, dict):
        return {key: _field_messages(value) for key, value in detail.items()}
    if isinstance(detail, (list, tuple)):
        return [str(value) for value in detail]
    return [str(detail)]


def api_exception_handler(exc, context):
    """Converts handled REST framework exceptions to the fixed error shape."""
    response = exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, drf_exceptions.ValidationError):
        fields = _field_messages(exc.detail)
        if not isinstance(fields, dict):
            fields = {"non_field_errors": fields}
        return error_response("validation_error", fields)
    if isinstance(exc, (drf_exceptions.NotAuthenticated, drf_exceptions.AuthenticationFailed)):
        return error_response("not_authenticated")
    if isinstance(exc, (drf_exceptions.PermissionDenied, DjangoPermissionDenied)):
        return error_response("permission_denied")
    if isinstance(exc, (drf_exceptions.NotFound, Http404)):
        return error_response("not_found")
    if isinstance(exc, drf_exceptions.MethodNotAllowed):
        return error_response("method_not_allowed")
    if response.status_code == status.HTTP_401_UNAUTHORIZED:
        return error_response("not_authenticated")
    return error_response("validation_error", {"non_field_errors": ["Invalid request."]})


def json_not_found(request, exception=None):
    """Returns the standard JSON response for an unknown URL."""
    return error_response("not_found")


def json_server_error(request):
    """Returns the generic JSON response for an unhandled server error."""
    return error_response("server_error")


class JsonExceptionMiddleware:
    """Logs uncaught view errors and returns the generic JSON server response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        logger.error(
            "Unhandled exception while processing request",
            exc_info=(type(exception), exception, exception.__traceback__),
        )
        return error_response("server_error")
