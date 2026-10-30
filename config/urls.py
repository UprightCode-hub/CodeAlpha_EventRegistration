from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import include, path, re_path

from config.exceptions import json_not_found

handler404 = "config.exceptions.json_not_found"
handler500 = "config.exceptions.json_server_error"


def admin_redirect(request):
    """Redirects the admin root path to its trailing-slash URL."""
    return HttpResponseRedirect("/admin/")


urlpatterns = [
    path("admin", admin_redirect),
    path("admin/", admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    re_path(r"^.*$", json_not_found),  # The catch-all would otherwise swallow the admin redirect.
]
