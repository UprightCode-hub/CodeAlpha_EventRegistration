from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema

from accounts.permissions import IsAttendee, IsOrganizer
from config.exceptions import error_response, state_error_response
from config.idempotency import run_idempotent_create
from config.pagination import paginated_response
from config.schema_helpers import (
    idempotency_key_parameter,
    page_parameter,
    page_schema,
)
from events.models import Event
from registrations.models import IdempotencyRecord, Notification, Registration
from registrations.serializers import (
    NotificationReadSerializer,
    OrganizerRegistrationReadSerializer,
    RegistrationReadSerializer,
    RegistrationRequestSerializer,
    with_waitlist_position,
)
from registrations.services import (
    RegistrationRuleError,
    cancel_registration,
    register_attendee,
)


def registration_state_response(error):
    """Converts a registration state error to the shared response shape."""
    return state_error_response(error.code)


class RegistrationListCreateView(APIView):
    """Creates registrations and lists the signed-in attendee's registrations."""

    http_method_names = ["get", "post", "options"]

    def get_permissions(self):
        """Return permissions for the current request method."""
        if self.request.method in ("GET", "POST"):
            return [IsAuthenticated(), IsAttendee()]
        return [IsAuthenticated()]

    @extend_schema(
        summary="Create an attendee registration",
        request=RegistrationRequestSerializer,
        parameters=[
            idempotency_key_parameter(),
        ],
        responses={201: RegistrationReadSerializer, 422: OpenApiTypes.OBJECT},
        auth=[{"BearerAuth": []}],
    )
    def post(self, request):
        """Create an attendee registration or replay its saved answer."""
        def create_registration():
            """Run the original registration creation and return its response."""
            serializer = RegistrationRequestSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            try:
                registration = register_attendee(
                    request.user,
                    event_id=serializer.validated_data["event"],
                    full_name=serializer.validated_data["full_name"],
                    phone=serializer.validated_data["phone"],
                    note=serializer.validated_data["note"],
                )
            except RegistrationRuleError as error:
                return registration_state_response(error)
            item = get_object_or_404(
                with_waitlist_position(
                    Registration.objects.select_related("event", "attendee")
                ),
                pk=registration.pk,
            )
            return Response(RegistrationReadSerializer(item).data, status=201)

        return run_idempotent_create(
            request, "registrations.create", create_registration, IdempotencyRecord
        )

    @extend_schema(
        summary="List the attendee's registrations",
        operation_id="registrations_list",
        parameters=[
            page_parameter(),
            OpenApiParameter("status", OpenApiTypes.STR, OpenApiParameter.QUERY),
        ],
        responses={
            200: page_schema("RegistrationPageResponse", RegistrationReadSerializer)
        },
        auth=[{"BearerAuth": []}],
    )
    def get(self, request):
        """Return the requested data."""
        status_value = request.query_params.get("status")
        valid_statuses = Registration.Status.values
        if status_value and status_value not in valid_statuses:
            raise serializers.ValidationError({"status": ["Select a valid status."]})
        queryset = Registration.objects.filter(attendee=request.user).select_related(
            "event", "attendee"
        )
        if status_value:
            queryset = queryset.filter(status=status_value)
        queryset = with_waitlist_position(queryset).order_by("-created_at", "-id")
        return paginated_response(request, queryset, self, RegistrationReadSerializer)


class RegistrationDetailView(APIView):
    """Reads one registration for its owning attendee."""

    http_method_names = ["get", "options"]

    def get_permissions(self):
        """Return permissions for the current request method."""
        if self.request.method == "GET":
            return [IsAuthenticated(), IsAttendee()]
        return [IsAuthenticated()]

    @extend_schema(
        summary="Get an attendee registration",
        operation_id="registrations_retrieve",
        responses={200: RegistrationReadSerializer},
        auth=[{"BearerAuth": []}],
    )
    def get(self, request, pk):
        """Return the requested data."""
        queryset = with_waitlist_position(
            Registration.objects.filter(attendee=request.user).select_related(
                "event", "attendee"
            )
        )
        registration = get_object_or_404(queryset, pk=pk)
        return Response(RegistrationReadSerializer(registration).data)


class RegistrationCancelView(APIView):
    """Cancels one registration for its owning attendee."""

    http_method_names = ["post", "options"]

    def get_permissions(self):
        """Return permissions for the current request method."""
        if self.request.method == "POST":
            return [IsAuthenticated(), IsAttendee()]
        return [IsAuthenticated()]

    @extend_schema(
        summary="Cancel an attendee registration",
        request=None,
        responses={200: RegistrationReadSerializer},
        auth=[{"BearerAuth": []}],
    )
    def post(self, request, pk):
        """Handle the submitted request."""
        try:
            registration = Registration.objects.get(pk=pk, attendee=request.user)
        except Registration.DoesNotExist:
            return error_response("not_found")
        try:
            updated = cancel_registration(registration.pk, request.user.pk)
        except RegistrationRuleError as error:
            return registration_state_response(error)
        item = get_object_or_404(
            with_waitlist_position(
                Registration.objects.select_related("event", "attendee")
            ),
            pk=updated.pk,
        )
        return Response(RegistrationReadSerializer(item).data)


class NotificationListView(APIView):
    """Lists notifications addressed to the signed-in user."""

    http_method_names = ["get", "options"]

    def get_permissions(self):
        """Return permissions for the current request method."""
        return [IsAuthenticated()]

    @extend_schema(
        summary="List the signed-in user's notifications",
        parameters=[page_parameter()],
        responses={
            200: page_schema("NotificationPageResponse", NotificationReadSerializer)
        },
        auth=[{"BearerAuth": []}],
    )
    def get(self, request):
        """Return the requested data."""
        queryset = Notification.objects.filter(recipient=request.user).select_related(
            "event", "registration"
        ).order_by("-created_at", "-id")
        return paginated_response(request, queryset, self, NotificationReadSerializer)


class OrganizerRegistrationListView(APIView):
    """Lists registrations for an event owned by the organizer."""

    http_method_names = ["get", "options"]

    def get_permissions(self):
        """Return permissions for the current request method."""
        if self.request.method == "GET":
            return [IsAuthenticated(), IsOrganizer()]
        return [IsAuthenticated()]

    @extend_schema(
        summary="List registrations for an owned event",
        parameters=[page_parameter()],
        responses={
            200: page_schema(
                "OrganizerRegistrationPageResponse", OrganizerRegistrationReadSerializer
            )
        },
        auth=[{"BearerAuth": []}],
    )
    def get(self, request, pk):
        """Return the requested data."""
        event = get_object_or_404(Event.objects.only("id", "organizer_id"), pk=pk)
        if event.organizer_id != request.user.pk:
            return error_response("not_found")
        queryset = with_waitlist_position(
            Registration.objects.filter(event_id=event.pk).select_related(
                "event", "attendee"
            )
        ).order_by("created_at", "id")
        return paginated_response(request, queryset, self, OrganizerRegistrationReadSerializer)
