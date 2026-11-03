from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic.base import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.permissions import AllowAny

from config.exceptions import json_not_found
from config.health import HealthView
from events.views import MyEventsView
from registrations.views import NotificationListView, OrganizerRegistrationListView

handler404 = "config.exceptions.json_not_found"
handler500 = "config.exceptions.json_server_error"


urlpatterns = [
    path(
        settings.ADMIN_URL.rstrip("/"),
        RedirectView.as_view(url="/" + settings.ADMIN_URL, permanent=False),
    ),
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/events/", include("events.urls")),
    path("api/registrations/", include("registrations.urls")),
    path("api/notifications/", NotificationListView.as_view(), name="notifications"),
    path("api/health/", HealthView.as_view(), name="health"),
    path(
        "api/events/<int:pk>/registrations/",
        OrganizerRegistrationListView.as_view(),
        name="event-registrations",
    ),
    path("api/my/events/", MyEventsView.as_view(), name="my-events"),
    path(
        "api/schema/",
        SpectacularAPIView.as_view(permission_classes=[AllowAny]),
        name="schema",
    ),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema", permission_classes=[AllowAny]),
        name="swagger-ui",
    ),
    # The catch-all would otherwise swallow the configured admin redirect.
    re_path(r"^.*$", json_not_found),
]
