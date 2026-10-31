from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import include, path, re_path

from config.exceptions import json_not_found
from events.views import MyEventsView
from registrations.views import OrganizerRegistrationListView, NotificationListView

handler404 = "config.exceptions.json_not_found"
handler500 = "config.exceptions.json_server_error"


def admin_redirect(request):
    """Redirects the admin root path to its trailing-slash URL."""
    return HttpResponseRedirect("/admin/")


urlpatterns = [
    path("admin", admin_redirect),
    path("admin/", admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/events/", include("events.urls")),
    path("api/registrations/", include("registrations.urls")),
    path("api/notifications/", NotificationListView.as_view(), name="notifications"),
    path(
        "api/events/<int:pk>/registrations/",
        OrganizerRegistrationListView.as_view(),
        name="event-registrations",
    ),
    path("api/my/events/", MyEventsView.as_view(), name="my-events"),
    re_path(r"^.*$", json_not_found),  # The catch-all would otherwise swallow the admin redirect.
]
