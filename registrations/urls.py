from django.urls import path

from registrations.views import (
    RegistrationCancelView,
    RegistrationDetailView,
    RegistrationListCreateView,
)

app_name = "registrations"

urlpatterns = [
    path("", RegistrationListCreateView.as_view(), name="registrations"),
    path("<int:pk>/", RegistrationDetailView.as_view(), name="registration-detail"),
    path("<int:pk>/cancel/", RegistrationCancelView.as_view(), name="registration-cancel"),
]
