from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import exceptions, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema

from accounts.permissions import IsOrganizer
from config.exceptions import error_response, state_error_response
from config.idempotency import run_idempotent_create
from config.pagination import paginated_response
from config.schema_helpers import (
    idempotency_key_parameter,
    page_parameter,
    page_schema,
)
from events.filters import filter_events
from events.models import Event
from events.serializers import CancelEventSerializer, EventReadSerializer, EventWriteSerializer
from registrations.models import IdempotencyRecord
from registrations.services import RegistrationRuleError, apply_event_edit, cancel_event


def event_error(code):
    """Builds a fixed response for event state errors."""
    return state_error_response(code)


class EventListCreateView(APIView):
    """Reads public event pages and creates events for organizers."""

    http_method_names = ["get", "post", "options"]

    def initial(self, request, *args, **kwargs):
        """Reject unsupported methods before normal request checks."""
        if request.method.lower() not in self.http_method_names:
            raise exceptions.MethodNotAllowed(request.method)
        super().initial(request, *args, **kwargs)

    def get_permissions(self):
        """Return permissions for the current request method."""
        if self.request.method == "POST":
            return [IsAuthenticated(), IsOrganizer()]
        return [AllowAny()]

    @extend_schema(
        summary="List and filter upcoming events",
        operation_id="events_list",
        parameters=[
            page_parameter(),
            OpenApiParameter("q", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("city", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("category", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("event_type", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("status", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("starts_after", OpenApiTypes.DATE, OpenApiParameter.QUERY),
            OpenApiParameter("starts_before", OpenApiTypes.DATE, OpenApiParameter.QUERY),
        ],
        responses={200: page_schema("EventListPageResponse", EventReadSerializer)},
        auth=[],
    )
    def get(self, request):
        """Return the requested data."""
        return paginated_response(
            request, filter_events(request.query_params), self, EventReadSerializer
        )

    @extend_schema(
        summary="Create an event",
        request=EventWriteSerializer,
        parameters=[
            idempotency_key_parameter(),
        ],
        responses={201: EventReadSerializer, 422: OpenApiTypes.OBJECT},
        auth=[{"BearerAuth": []}],
    )
    def post(self, request):
        """Create an event or replay its saved answer for the supplied key."""
        def create_event():
            """Run the original event creation and return its response."""
            serializer = EventWriteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            event = serializer.save(organizer=request.user)
            return Response(
                EventReadSerializer(event).data, status=status.HTTP_201_CREATED
            )

        return run_idempotent_create(
            request, "events.create", create_event, IdempotencyRecord
        )


class EventDetailView(APIView):
    """Reads an event publicly and edits it for its organizer."""

    http_method_names = ["get", "patch", "options"]

    def initial(self, request, *args, **kwargs):
        """Reject unsupported methods before normal request checks."""
        if request.method.lower() not in self.http_method_names:
            raise exceptions.MethodNotAllowed(request.method)
        super().initial(request, *args, **kwargs)

    def get_permissions(self):
        """Return permissions for the current request method."""
        if self.request.method == "PATCH":
            return [IsAuthenticated(), IsOrganizer()]
        return [AllowAny()]

    @extend_schema(
        summary="Get event details",
        operation_id="events_retrieve",
        responses={200: EventReadSerializer},
        auth=[],
    )
    def get(self, request, pk):
        """Return the requested data."""
        event = get_object_or_404(
            Event.objects.select_related("organizer"),
            pk=pk,
        )
        return Response(EventReadSerializer(event).data)

    @extend_schema(
        summary="Edit an event",
        request=EventWriteSerializer,
        responses={200: EventReadSerializer},
        auth=[{"BearerAuth": []}],
    )
    def patch(self, request, pk):
        """Apply the requested partial update."""
        event = get_object_or_404(Event.objects.only("id", "organizer_id"), pk=pk)
        if event.organizer_id != request.user.pk:
            return error_response("not_found")

        def validate_edit(locked_event):
            """Validate the edit against the locked event values."""
            serializer = EventWriteSerializer(locked_event, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            return serializer

        try:
            updated_event = apply_event_edit(pk, validate_edit)
        except RegistrationRuleError as error:
            return event_error(error.code)
        return Response(EventReadSerializer(updated_event).data)


class CancelEventView(APIView):
    """Cancels an event for its owning organizer."""

    http_method_names = ["post", "options"]

    def get_permissions(self):
        """Return permissions for the current request method."""
        if self.request.method == "POST":
            return [IsAuthenticated(), IsOrganizer()]
        return [IsAuthenticated()]

    @extend_schema(
        summary="Cancel an event",
        request=CancelEventSerializer,
        responses={200: EventReadSerializer},
        auth=[{"BearerAuth": []}],
    )
    def post(self, request, pk):
        """Handle the submitted request."""
        event = get_object_or_404(Event.objects.only("id", "organizer_id"), pk=pk)
        if event.organizer_id != request.user.pk:
            return error_response("not_found")

        def validate_reason():
            """Validate and return the submitted cancellation reason."""
            serializer = CancelEventSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            return serializer.validated_data["reason"]

        try:
            cancelled_event = cancel_event(pk, validate_reason)
        except RegistrationRuleError as error:
            return event_error(error.code)
        return Response(EventReadSerializer(cancelled_event).data)


class MyEventsView(APIView):
    """Lists all events owned by the authenticated organizer."""

    permission_classes = [IsAuthenticated, IsOrganizer]
    http_method_names = ["get", "options"]

    def initial(self, request, *args, **kwargs):
        """Reject unsupported methods before normal request checks."""
        if request.method.lower() not in self.http_method_names:
            raise exceptions.MethodNotAllowed(request.method)
        super().initial(request, *args, **kwargs)

    @extend_schema(
        summary="List events owned by the organizer",
        operation_id="my_events_list",
        parameters=[page_parameter()],
        responses={200: page_schema("MyEventsPageResponse", EventReadSerializer)},
        auth=[{"BearerAuth": []}],
    )
    def get(self, request):
        """Return the requested data."""
        queryset = Event.objects.filter(organizer=request.user).select_related(
            "organizer"
        ).annotate(
            registered_total=Count(
                "registrations", filter=Q(registrations__status="registered")
            ),
            waitlist_total=Count(
                "registrations", filter=Q(registrations__status="waitlisted")
            ),
        )
        queryset = queryset.order_by("-start_at", "-id")
        return paginated_response(request, queryset, self, EventReadSerializer)
