from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import exceptions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsOrganizer
from events.filters import filter_events
from events.models import Event
from events.serializers import EventReadSerializer, EventWriteSerializer


EVENT_ERRORS = {
    "event_started": "This event has already started.",
    "event_cancelled": "This event has been cancelled.",
}


def event_error(code):
    """Builds a fixed response for event state errors."""
    return Response(
        {"error": {"code": code, "message": EVENT_ERRORS[code]}},
        status=status.HTTP_400_BAD_REQUEST,
    )


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
        event = get_object_or_404(
            Event.objects.select_related("organizer"),
            pk=pk,
        )
        if event.organizer_id != request.user.pk:
            return Response(
                {"error": {"code": "not_found", "message": "The requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        if event.status == Event.Status.CANCELLED:
            return event_error("event_cancelled")
        if event.start_at <= timezone.now():
            return event_error("event_started")

        serializer = EventWriteSerializer(event, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated_event = serializer.save()
        return Response(EventReadSerializer(updated_event).data)


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
        )
        queryset = queryset.order_by("-start_at", "-id")
        return paginated_response(request, queryset, self)
