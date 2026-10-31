from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import exceptions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsOrganizer
from config.exceptions import state_error_response
from events.filters import filter_events
from events.models import Event
from events.serializers import EventReadSerializer, EventWriteSerializer
from registrations.services import RegistrationRuleError, apply_event_edit, cancel_event


def event_error(code):
    """Builds a fixed response for event state errors."""
    return state_error_response(code)


def paginated_response(request, queryset, view):
    """Returns one page using the configured page size and response shape."""
    paginator = PageNumberPagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    return paginator.get_paginated_response(EventReadSerializer(page, many=True).data)


class EventListCreateView(APIView):
    """Reads public event pages and creates events for organizers."""

    http_method_names = ["get", "post", "options"]

    def initial(self, request, *args, **kwargs):
        if request.method.lower() not in self.http_method_names:
            raise exceptions.MethodNotAllowed(request.method)
        super().initial(request, *args, **kwargs)

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsOrganizer()]
        return [AllowAny()]

    def get(self, request):
        return paginated_response(request, filter_events(request.query_params), self)

    def post(self, request):
        serializer = EventWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = serializer.save(organizer=request.user)
        return Response(EventReadSerializer(event).data, status=status.HTTP_201_CREATED)


class EventDetailView(APIView):
    """Reads an event publicly and edits it for its organizer."""

    http_method_names = ["get", "patch", "options"]

    def initial(self, request, *args, **kwargs):
        if request.method.lower() not in self.http_method_names:
            raise exceptions.MethodNotAllowed(request.method)
        super().initial(request, *args, **kwargs)

    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsAuthenticated(), IsOrganizer()]
        return [AllowAny()]

    def get(self, request, pk):
        event = get_object_or_404(
            Event.objects.select_related("organizer"),
            pk=pk,
        )
        return Response(EventReadSerializer(event).data)

    def patch(self, request, pk):
        event = get_object_or_404(Event.objects.only("id", "organizer_id"), pk=pk)
        if event.organizer_id != request.user.pk:
            return Response(
                {"error": {"code": "not_found", "message": "The requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            updated_event = apply_event_edit(pk, request)
        except RegistrationRuleError as error:
            return event_error(error.code)
        return Response(EventReadSerializer(updated_event).data)


class CancelEventView(APIView):
    """Cancels an event for its owning organizer."""

    http_method_names = ["post", "options"]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsOrganizer()]
        return [IsAuthenticated()]

    def post(self, request, pk):
        event = get_object_or_404(Event.objects.only("id", "organizer_id"), pk=pk)
        if event.organizer_id != request.user.pk:
            return Response(
                {"error": {"code": "not_found", "message": "The requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            cancelled_event = cancel_event(pk, request)
        except RegistrationRuleError as error:
            return event_error(error.code)
        return Response(EventReadSerializer(cancelled_event).data)


class MyEventsView(APIView):
    """Lists all events owned by the authenticated organizer."""

    permission_classes = [IsAuthenticated, IsOrganizer]
    http_method_names = ["get", "options"]

    def initial(self, request, *args, **kwargs):
        if request.method.lower() not in self.http_method_names:
            raise exceptions.MethodNotAllowed(request.method)
        super().initial(request, *args, **kwargs)

    def get(self, request):
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
        return paginated_response(request, queryset, self)
