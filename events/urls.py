from django.urls import path

from events.views import EventDetailView, EventListCreateView

urlpatterns = [
    path("", EventListCreateView.as_view(), name="event-create"),
    path("<int:pk>/", EventDetailView.as_view(), name="event-edit"),
]
