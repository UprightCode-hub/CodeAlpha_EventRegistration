from django.urls import path

from events.views import CancelEventView, EventDetailView, EventListCreateView

urlpatterns = [
    path("", EventListCreateView.as_view(), name="event-create"),
    path("<int:pk>/", EventDetailView.as_view(), name="event-edit"),
    path("<int:pk>/cancel/", CancelEventView.as_view(), name="event-cancel"),
]
